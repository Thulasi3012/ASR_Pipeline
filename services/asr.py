"""
asr.py — Sarvam.ai Speech-to-Text  (transcript only)

Flow
----
Short audio (WAV ≤ 30 s, file ≤ 25 MB) → Real-time API  (instant)
Long  audio (WAV  > 30 s, file  > 25 MB) → Batch SDK     (poll until done)

Batch SDK steps  (sarvamai package)
------------------------------------
1. create_job()          → get job_id
2. upload_files()        → PUT audio to Sarvam's blob storage
3. start()               → kick off processing
4. wait_until_complete() → poll every 5 s
5. download_outputs()    → save result JSON locally
6. read JSON             → return transcript string

KEY FIX: The temp file MUST be written inside a named temp directory
         with the ORIGINAL filename (e.g. "audio.wav"), NOT as a
         NamedTemporaryFile (which gives tmpXXXXXX.wav).
         The SDK sends os.path.basename(path) as the "files" list to
         the API — if that name looks random/empty the API returns 400.

Install
-------
    pip install sarvamai httpx pydub
"""

import asyncio
import io
import json
import shutil
import tempfile
import time
import wave
from contextlib import closing
from pathlib import Path

import httpx
from core.config import settings

# ─────────────────────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────────────────────

REALTIME_MAX_SECONDS: float = 30.0
REALTIME_MAX_MB:      float = 25.0

CONTENT_TYPE_MAP = {
    "wav":  "audio/wav",
    "mp3":  "audio/mpeg",
    "ogg":  "audio/ogg",
    "flac": "audio/flac",
    "m4a":  "audio/mp4",
    "mp4":  "audio/mp4",
    "webm": "audio/webm",
    "aac":  "audio/aac",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _wav_duration(wav_bytes: bytes) -> float | None:
    """Return WAV duration in seconds, or None on failure."""
    try:
        with closing(wave.open(io.BytesIO(wav_bytes), "rb")) as wf:
            frames    = wf.getnframes()
            rate      = wf.getframerate()
            channels  = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            if frames >= 2**31 - 1:
                return max(0, len(wav_bytes) - 44) / (rate * channels * sampwidth)
            return frames / float(rate)
    except Exception:
        return None


def _resample_wav(audio_bytes: bytes) -> bytes:
    """Normalise WAV → mono / 16 kHz / 16-bit via pydub."""
    try:
        from pydub import AudioSegment
        seg = (
            AudioSegment.from_file(io.BytesIO(audio_bytes))
            .set_channels(1)
            .set_frame_rate(16_000)
            .set_sample_width(2)
        )
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        out = buf.getvalue()
        print(f"[ASR] Resampled WAV: {len(audio_bytes)//1024} KB → {len(out)//1024} KB")
        return out
    except ImportError:
        print("[ASR] pydub not installed — skipping resample")
        return audio_bytes
    except Exception as exc:
        print(f"[ASR] Resample failed ({exc}) — using original")
        return audio_bytes


def _extract_transcript(output_dir: str, uploaded_filename: str) -> str:
    """Read transcript from the JSON file Sarvam writes after download_outputs().

    Sarvam names the output file:  <uploaded_basename>.json
    e.g. uploaded "audio.wav" → output "audio.wav.json"
    """
    base       = Path(uploaded_filename).name          # "audio.wav"
    candidates = [
        Path(output_dir) / f"{base}.json",             # "audio.wav.json"
        Path(output_dir) / f"{Path(base).stem}.json",  # "audio.json"
    ]
    json_path = next((p for p in candidates if p.exists()), None)

    if json_path is None:
        all_jsons = list(Path(output_dir).glob("*.json"))
        if not all_jsons:
            raise FileNotFoundError(f"[ASR] No output JSON in {output_dir}")
        json_path = all_jsons[0]
        print(f"[ASR] Using fallback output: {json_path}")

    print(f"[ASR][Batch] Reading transcript from: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    if data.get("transcript"):
        return data["transcript"]

    entries = (data.get("diarized_transcript") or {}).get("entries") or []
    if entries:
        return " ".join(e["transcript"] for e in entries if e.get("transcript"))

    raise ValueError(f"[ASR] No transcript in output JSON: {data}")


# ─────────────────────────────────────────────────────────────────────────────
# Batch path — sarvamai SDK
# ─────────────────────────────────────────────────────────────────────────────

def _batch_transcribe_sync(audio_bytes: bytes, original_filename: str) -> str:
    """Full Sarvam batch job — runs synchronously inside asyncio.to_thread()."""
    try:
        from sarvamai import SarvamAI
    except ImportError:
        raise ImportError("Run: pip install sarvamai")

    client = SarvamAI(api_subscription_key=settings.sarvam_api_key)

    lang = getattr(settings, "sarvam_language_code", "en-IN")
    job_kwargs: dict = {"model": "saaras:v3", "mode": "transcribe"}
    if lang and lang != "unknown":
        job_kwargs["language_code"] = lang

    print(f"[ASR][Batch] Creating job — model=saaras:v3  lang={job_kwargs.get('language_code', 'auto')}  file={original_filename}")

    # Step 1 — create job
    job = client.speech_to_text_job.create_job(**job_kwargs)
    print(f"[ASR][Batch] Job created — job_id={job.job_id}")

    # Write audio into a temp DIRECTORY using the ORIGINAL filename.
    # This is critical: the SDK sends os.path.basename(path) as the
    # "files" parameter. A random tmpXXXXXX name causes a 400 error.
    tmp_dir    = tempfile.mkdtemp()
    audio_path = str(Path(tmp_dir) / original_filename)  # e.g. /tmp/abc123/audio.wav
    output_dir = tempfile.mkdtemp()

    try:
        Path(audio_path).write_bytes(audio_bytes)
        print(f"[ASR][Batch] Wrote audio → {audio_path}")

        # Step 2 — upload (SDK PUTs to Azure presigned URL)
        job.upload_files(file_paths=[audio_path])
        print(f"[ASR][Batch] Uploaded ✓  ({len(audio_bytes)//1024} KB)")

        # Step 3 — start
        job.start()
        print(f"[ASR][Batch] Job started — polling…")

        # Step 4 — wait
        t0     = time.monotonic()
        status = job.wait_until_complete(poll_interval=5, timeout=600)
        print(f"[ASR][Batch] Finished in {time.monotonic()-t0:.0f}s — state={status.job_state}")

        if status.job_state.lower() == "failed":
            raise RuntimeError(f"[ASR][Batch] Job failed: {status}")

        # Step 5 — download result JSON
        job.download_outputs(output_dir=output_dir)
        print(f"[ASR][Batch] Output downloaded → {output_dir}")

        # Step 6 — read transcript from JSON
        return _extract_transcript(output_dir, original_filename)

    finally:
        shutil.rmtree(tmp_dir,    ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)


async def _transcribe_batch(audio_bytes: bytes, filename: str) -> str:
    transcript = await asyncio.to_thread(_batch_transcribe_sync, audio_bytes, filename)
    print(f"[ASR][Batch] Transcript ready — {len(transcript)} chars")
    return transcript


# ─────────────────────────────────────────────────────────────────────────────
# Real-time path — direct httpx POST
# ─────────────────────────────────────────────────────────────────────────────

async def _transcribe_realtime(audio_bytes: bytes, filename: str, content_type: str) -> str:
    lang  = getattr(settings, "sarvam_language_code", "en-IN")
    model = getattr(settings, "sarvam_model", "saarika:v2.5")

    headers = {"api-subscription-key": settings.sarvam_api_key}
    files   = {"file": (filename, audio_bytes, content_type)}
    data: dict = {"model": model}
    if lang and lang != "unknown":
        data["language_code"] = lang

    print(f"[ASR][Realtime] POST → model={model}  lang={lang}  file={filename}  size={len(audio_bytes)//1024} KB")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            settings.sarvam_api_url,
            headers=headers,
            files=files,
            data=data,
        )

    if resp.status_code != 200:
        print(f"[ASR][Realtime] Error {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    result     = resp.json()
    transcript = result.get("transcript")
    if not transcript:
        raise ValueError(f"[ASR][Realtime] No transcript in response: {result}")

    print(f"[ASR][Realtime] Transcript ready — {len(transcript)} chars")
    return transcript


# ─────────────────────────────────────────────────────────────────────────────
# Public entry-point
# ─────────────────────────────────────────────────────────────────────────────

async def transcribe(audio_url: str) -> str:
    """Download audio from *audio_url* and return the transcript string.

    Routing
    -------
    WAV > 30 s   → Batch SDK   (sarvamai)
    File > 25 MB → Batch SDK
    Otherwise    → Real-time REST
    """
    # ── Download ──────────────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=60, verify=False, follow_redirects=True) as client:
        resp = await client.get(audio_url)
        resp.raise_for_status()
        audio_bytes = resp.content

    size_mb = len(audio_bytes) / (1024 * 1024)
    print(f"[ASR] Downloaded {size_mb:.2f} MB from {audio_url}")

    # ── File metadata ─────────────────────────────────────────────────────────
    filename     = audio_url.split("/")[-1].split("?")[0] or "audio.wav"
    ext          = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
    content_type = CONTENT_TYPE_MAP.get(ext, "audio/wav")

    # ── Route: long WAV → Batch ───────────────────────────────────────────────
    if ext == "wav":
        duration = _wav_duration(audio_bytes)
        if duration is not None:
            print(f"[ASR] WAV duration: {duration:.1f}s")
            if duration > REALTIME_MAX_SECONDS:
                print(f"[ASR] Long audio → Batch SDK")
                return await _transcribe_batch(audio_bytes, filename)

    # ── Route: large file → Batch ─────────────────────────────────────────────
    if size_mb > REALTIME_MAX_MB:
        print(f"[ASR] Large file ({size_mb:.1f} MB) → Batch SDK")
        return await _transcribe_batch(audio_bytes, filename)

    # ── Real-time: normalise WAV + POST ───────────────────────────────────────
    if ext == "wav":
        audio_bytes = _resample_wav(audio_bytes)
    return await _transcribe_realtime(audio_bytes, filename, content_type)
# ASR Pipeline (Sarvam.ai)

A simple, queue-driven Automatic Speech Recognition (ASR) pipeline built with **FastAPI**, **RabbitMQ**, and **Sarvam.ai** (via `sarvamai` SDK / REST).

It is designed to:
- Accept audio URLs via an HTTP API
- Enqueue jobs to **RabbitMQ** (`ASR-Inference-Queue`)
- Consume jobs in a worker, call Sarvam.ai, update a PostgreSQL database, and publish results to a **result queue** (`result-queue`)
- Provide an API to query transcription results by job ID

---

## ✅ High-Level Flow (How it works)

1. Client POSTs `{ "audio_url": "https://..." }` to `/process-queue`
2. API creates a DB job record (status: `queued`) and publishes a message to RabbitMQ `ASR-Inference-Queue`
3. One or more workers consume messages from `ASR-Inference-Queue`
4. Worker downloads the audio, chooses a Sarvam.ai path (Realtime vs Batch) based on file size/duration, then transcribes
5. Worker updates DB record (status: `processing` → `completed` / `failed`) and sends a result message to `result-queue`
6. Client can poll `/asr-result/{job_id}` to read the final transcript and status (or subscribe to `result-queue` externally)

---

## 🗂️ Repo Structure

```
main.py                # FastAPI app entrypoint
worker.py              # Worker process that consumes ASR-Inference-Queue

core/                  # configuration
  config.py            # env-backed settings

database/              # persistence
  database.py          # async DB init + session
  models.py            # Job model + status enum

routes/                # API endpoints
  routes.py            # /process-queue, /asr-result/{job_id}

services/              # business logic
  asr_inference_queue.py  # push job into RabbitMQ
  result_queue.py         # publish results to RabbitMQ
  asr.py                  # Sarvam.ai integration (realtime & batch)
```

---

## 🧩 Environment & Configuration

This project uses **environment variables** via a `.env` file (loaded by `pydantic-settings`).

Create a `.env` file in the project root with values similar to:

```ini
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=asr_pipeline
DB_USER=asr_user
DB_PASSWORD=supersecure

# RabbitMQ
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# Sarvam.ai (required for transcription)
SARVAM_API_KEY=your_sarvam_api_key_here
# optional overrides
# SARVAM_API_URL=https://api.sarvam.ai/speech-to-text
# SARVAM_LANGUAGE_CODE=en-IN
# SARVAM_MODEL=saarika:v2.5
```

> ✅ NOTE: `SARVAM_API_KEY` is required for transcription to work.

---

## 🧰 Prerequisites

### 1) Python (3.11+ recommended)

Install Python and create a venv:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install sarvamai pydub
```

### 2) PostgreSQL

This service uses PostgreSQL via `asyncpg`.

- Create the database and user matching your `.env` values.
- The app will create the required table automatically on startup.

### 3) Erlang & RabbitMQ (Windows)

RabbitMQ requires Erlang. On Windows, install both using Chocolatey or the installers.

#### Option A: Chocolatey (recommended)

```powershell
choco install erlang
choco install rabbitmq
```

After install, enable management plugin:

```powershell
rabbitmq-plugins enable rabbitmq_management
```

Start RabbitMQ service:

```powershell
net start RabbitMQ
```

#### Option B: Official installers

- Erlang: https://www.erlang.org/downloads
- RabbitMQ: https://www.rabbitmq.com/install-windows.html

Then run:

```powershell
rabbitmq-plugins enable rabbitmq_management
net start RabbitMQ
```

---

## 🐇 RabbitMQ Basics (commands)

### Check status & running nodes

```powershell
rabbitmqctl status
```

### List queues

```powershell
rabbitmqctl list_queues
```

### Purge a queue

```powershell
rabbitmqctl purge_queue name=ASR-Inference-Queue
rabbitmqctl purge_queue name=result-queue
```

### Delete a queue

```powershell
rabbitmqctl delete_queue ASR-Inference-Queue
rabbitmqctl delete_queue result-queue
```

### Access the UI (once management plugin enabled)

Open in browser:

```
http://localhost:15672
```

Default credentials (if unchanged): `guest` / `guest`

---

##  Run the API Server (FastAPI)

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### API endpoints

#### POST /process-queue
Enqueues an audio transcription job.

Request body:

```json
{ "audio_url": "https://example.com/speech.wav" }
```

Response:

```json
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

#### GET /asr-result/{job_id}
Polls the database for the job result.

Response:

```json
{
  "job_id": "<uuid>",
  "status": "completed", // queued | processing | completed | failed
  "audio_url": "https://...",
  "transcript_text": "...",
  "error_message": null
}
```

---

## Run a Worker (consumer)

The worker listens to `ASR-Inference-Queue`, runs Sarvam.ai transcription, updates the DB, and posts results to `result-queue`.

```powershell
python worker.py
```

You can optionally pass a worker ID for logging:

```powershell
python worker.py 2
```

---

##  Queue Names (defaults)

- **Inference queue**: `ASR-Inference-Queue` (jobs created by API)
- **Result queue**: `result-queue` (results published by worker)

---

## 📌 Sarvam.ai / ASR Model Behavior ( Temp ASR )

### Realtime vs Batch routing

The code chooses the best Sarvam.ai path based on the audio file size and duration:

- **Realtime REST API** → used for short audio (< 30s) and small files (< 25 MB)
- **Batch SDK** (`sarvamai` package) → used for long audio (> 30s) or large files (> 25 MB)

### Model configuration

By default, the app uses:
- Realtime model: `saarika:v2.5`
- Batch model: `saaras:v3`

You can override via environment variables:

- `SARVAM_MODEL` (realtime; default `saarika:v2.5`)
- `SARVAM_LANGUAGE_CODE` (default `en-IN`)

---

## ✅ Example: Run End-to-End

1. Start RabbitMQ + PostgreSQL
2. Set `.env` with credentials
3. Start API: `uvicorn main:app --reload`
4. Start worker: `python worker.py`
5. Make a request:

```powershell
curl -X POST "http://localhost:8000/process-queue" -H "Content-Type: application/json" -d "{\"audio_url\":\"https://www.example.com/your-audio.wav\"}"
```

6. Poll result:

```powershell
curl "http://localhost:8000/asr-result/<job_id>"
```

---

##  Troubleshooting

- **No transcription / error**: check `worker.py` logs; it prints errors from Sarvam.ai.
- **RabbitMQ connection refused**: ensure RabbitMQ is running and credentials match `.env`.
- **Database connection errors**: ensure Postgres is running and the database/user exist.

---

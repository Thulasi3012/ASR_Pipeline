import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, HttpUrl

from database.database import get_db
from database.models import Job, JobStatus
from services.asr_inference_queue import push_to_asr_queue

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────

class ProcessQueueRequest(BaseModel):
    audio_url: HttpUrl


class ProcessQueueResponse(BaseModel):
    job_id: str
    status: str


class ASRResultResponse(BaseModel):
    job_id: str
    status: str
    audio_url: str
    transcript_text: str | None = None
    error_message: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/process-queue", response_model=ProcessQueueResponse, status_code=202)
async def process_queue(request: ProcessQueueRequest, db: AsyncSession = Depends(get_db)):
    job_id = str(uuid.uuid4())
    audio_url = str(request.audio_url)

    # Save job to DB
    job = Job(
        job_id=job_id,
        audio_url=audio_url,
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    # Push to RabbitMQ
    push_to_asr_queue(job_id=job_id, audio_url=audio_url)

    return ProcessQueueResponse(job_id=job_id, status="queued")


@router.get("/asr-result/{job_id}", response_model=ASRResultResponse)
async def get_asr_result(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    return ASRResultResponse(
        job_id=job.job_id,
        status=job.status.value,
        audio_url=job.audio_url,
        transcript_text=job.transcript_text,
        error_message=job.error_message,
    )

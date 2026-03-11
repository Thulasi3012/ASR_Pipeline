"""
worker.py — Run this separately: python worker.py
Consumes from ASR-Inference-Queue → Sarvam.ai → DB update → result-queue
"""

import asyncio
import json
import pika
from datetime import datetime, timezone

from sqlalchemy import select

from core.config import settings
from database.database import AsyncSessionLocal, init_db
from database.models import Job, JobStatus
from services.asr import transcribe
from services.result_queue import push_to_result_queue
from services.asr_inference_queue import get_connection

#setting up of worker id 
# import os
# WORKER_ID = os.environ.get("WORKER_ID", "unknown")
# print(f"[Worker {WORKER_ID}] Listening on queue: {settings.asr_inference_queue}")
import sys
# Default to 1 if not passed
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "1"
print(f"[Worker {WORKER_ID}] Starting...")
# -------------------------------
# Create ONE global event loop
# -------------------------------
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


# -------------------------------
# Process Job (Async)
# -------------------------------
async def process_job(payload: dict):
    job_id = payload["job_id"]
    audio_url = payload["audio_url"]

    print(f"[Worker {WORKER_ID}] Processing job_id={job_id}")

    # Fetch job
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            print(f"[Worker] Job {job_id} not found in DB")
            return

        # Mark as PROCESSING
        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()

    transcript_text = None
    error_message = None
    final_status = JobStatus.FAILED

    # -------------------------------
    # Call ASR Model (Sarvam.ai)
    # -------------------------------
    try:
        transcript_text = await transcribe(audio_url)
        final_status = JobStatus.COMPLETED
        print(f"[Worker {WORKER_ID}] Transcription completed for job_id={job_id}")

    except Exception as e:
        error_message = str(e)
        print(f"[Worker {WORKER_ID}] Transcription FAILED for job_id={job_id}: {e}")

    # -------------------------------
    # Update Database
    # -------------------------------
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one_or_none()

        if job:
            job.status = final_status
            job.worker_id = WORKER_ID
            job.transcript_text = transcript_text
            job.error_message = error_message
            job.updated_at = datetime.now(timezone.utc)

            await session.commit()

    # -------------------------------
    # Push Result to Result Queue
    # -------------------------------
    push_to_result_queue(
        job_id=job_id,
        status=final_status.value,
        transcript_text=transcript_text,
        error_message=error_message,
    )

    print(f"[Worker {WORKER_ID}] Job finished job_id={job_id} status={final_status.value}")

# -------------------------------
# RabbitMQ Consumer Callback
# -------------------------------
def on_message(ch, method, properties, body):
    try:
        payload = json.loads(body)

        print(f"[Worker {WORKER_ID}] Received job_id={payload.get('job_id')}")

        # Run async job in the global event loop
        loop.run_until_complete(process_job(payload))

        # Acknowledge message
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        print(f"[Worker {WORKER_ID}] ERROR processing message: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)


# -------------------------------
# Startup Tasks
# -------------------------------
async def startup():
    await init_db()
    print(f"[Worker {WORKER_ID}] DB ready")


# -------------------------------
# Main Worker
# -------------------------------
def main():

    # Initialize DB
    loop.run_until_complete(startup())

    # Debug RabbitMQ config
    print("----- RabbitMQ Config -----")
    print("Host:", settings.rabbitmq_host)
    print("Port:", settings.rabbitmq_port)
    print("User:", settings.rabbitmq_user)
    print("Password:", settings.rabbitmq_password)
    print("---------------------------")

    # Connect RabbitMQ
    connection = get_connection()
    channel = connection.channel()

    # Declare queue
    channel.queue_declare(queue=settings.asr_inference_queue, durable=True)

    # One job per worker
    channel.basic_qos(prefetch_count=1)

    # Start consumer
    channel.basic_consume(
        queue=settings.asr_inference_queue,
        on_message_callback=on_message
    )

    print(f"[Worker {WORKER_ID}] Listening on queue: {settings.asr_inference_queue}")

    channel.start_consuming()


# -------------------------------
# Entry
# -------------------------------
if __name__ == "__main__":
    main()
import pika
import json
from core.config import settings


def get_connection():
    credentials = pika.PlainCredentials(settings.rabbitmq_user, settings.rabbitmq_password)
    params = pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        credentials=credentials,
        heartbeat=60,
    )
    return pika.BlockingConnection(params)


def push_to_asr_queue(job_id: str, audio_url: str):
    """Push a job message onto ASR-Inference-Queue."""
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=settings.asr_inference_queue, durable=True)

    message = {"job_id": job_id, "audio_url": audio_url}

    channel.basic_publish(
        exchange="",
        routing_key=settings.asr_inference_queue,
        body=json.dumps(message),
        properties=pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent,
            content_type="application/json",
        ),
    )

    connection.close()

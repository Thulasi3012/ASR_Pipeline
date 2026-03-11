import pika
import json
from core.config import settings
from services.asr_inference_queue import get_connection


def push_to_result_queue(job_id: str, status: str, transcript_text: str = None, error_message: str = None):
    """Push completed ASR result onto result-queue."""
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=settings.result_queue, durable=True)

    message = {
        "job_id": job_id,
        "status": status,
        "transcript_text": transcript_text,
        "error_message": error_message,
    }

    channel.basic_publish(
        exchange="",
        routing_key=settings.result_queue,
        body=json.dumps(message),
        properties=pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent,
            content_type="application/json",
        ),
    )

    connection.close()


def start_result_consumer(callback):
    """
    Start consuming from result-queue.
    callback(payload: dict) is called for each message.
    """
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=settings.result_queue, durable=True)
    channel.basic_qos(prefetch_count=1)

    def on_message(ch, method, properties, body):
        payload = json.loads(body)
        callback(payload)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=settings.result_queue, on_message_callback=on_message)
    channel.start_consuming()

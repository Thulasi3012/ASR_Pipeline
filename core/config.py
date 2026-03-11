from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    # Database
    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    # RabbitMQ
    rabbitmq_host: str
    rabbitmq_port: int = 5672
    rabbitmq_user: str
    rabbitmq_password: str
    asr_inference_queue: str = "ASR-Inference-Queue"
    result_queue: str = "result-queue"

    # Sarvam.ai
    sarvam_api_key: str
    sarvam_api_url: str = "https://api.sarvam.ai/speech-to-text"
    sarvam_language_code: str = "en-IN"
    # Use a supported Sarvam.ai model (see docs for latest versions).
    # 'saarika:v2.5' is currently the recommended stable model.
    # For large/long audio (batch), 'saaras:v3' is the recommended model.
    sarvam_model: str = "saarika:v2.5"

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    class Config:
        env_file = ".env"


settings = Settings()

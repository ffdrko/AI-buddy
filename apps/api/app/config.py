from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://study:study@db:5432/study"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint_url: str = "http://minio:9000"
    s3_bucket: str = "study-raw"
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()

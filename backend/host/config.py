from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str
    google_client_secret_json: str
    postgres_url: str = "sqlite+aiosqlite:///assistant.db"
    nas_host: str | None = None
    nas_user: str | None = None
    nas_pass: str | None = None

    # derived paths
    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

settings = Settings()          # importable singleton

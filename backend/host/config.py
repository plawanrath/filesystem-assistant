import sys, os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


def _bundle_resources() -> Path | None:
    """
    Return Path to MyApp.app/Contents/Resources when running frozen,
    else None when running from source.
    """
    if getattr(sys, "frozen", False):           # True in py2app/pyinstaller
        # argv[0] == ".../Filesystem Assistant.app/Contents/MacOS/main"
        return Path(sys.argv[0]).resolve().parent.parent / "Resources"
    return None

# -------- locate the .env file --------------------------------------
bundle_res = _bundle_resources()
if bundle_res and (bundle_res / ".env").exists():
    load_dotenv(bundle_res / ".env", override=True)
else:
    # fallback to project root / current dir
    load_dotenv(".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str
    google_client_secret_json: str
    postgres_url: str = "sqlite+aiosqlite:///assistant.db"
    nas_host: str | None = None
    nas_user: str | None = None
    nas_pass: str | None = None
    nas_port: str | None = None

    # derived paths
    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

settings = Settings()          # importable singleton

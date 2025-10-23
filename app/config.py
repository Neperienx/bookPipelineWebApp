import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _default_sqlite_uri() -> str:
    instance_path = BASE_DIR / "instance"
    instance_path.mkdir(exist_ok=True)
    return f"sqlite:///{instance_path / 'book_pipeline.db'}"


class Config:
    """Base configuration shared across environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", _default_sqlite_uri())
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = None


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from dotenv import load_dotenv

from .config import Config
from .extensions import csrf, db, login_manager, migrate
from .db_utils import ensure_database_schema


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )
    app.config.from_object(config_class)
    app.config.setdefault("PROMPT_CONFIG_PATH", str(BASE_DIR / "prompt_config.json"))
    if "TEXT_GENERATOR_MODEL_PATH" not in app.config:
        env_model_path = os.environ.get("TEXT_GENERATOR_MODEL_PATH")
        if env_model_path:
            app.config["TEXT_GENERATOR_MODEL_PATH"] = env_model_path

    register_extensions(app)
    register_blueprints(app)

    with app.app_context():
        ensure_database_schema()

    return app


def register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"
    csrf.init_app(app)


def register_blueprints(app: Flask) -> None:
    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .projects import bp as projects_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(projects_bp)

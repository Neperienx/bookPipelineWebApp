"""Utility script to configure development environment variables and initialize the database."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import create_app, db
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
BACKUP_SUFFIX = ".bak"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update a .env file with the Flask settings required for local development "
            "and initialize the SQLite database."
        )
    )
    parser.add_argument(
        "--flask-app",
        default="wsgi.py",
        help="Entry point used by Flask (default: wsgi.py)",
    )
    parser.add_argument(
        "--flask-env",
        default="development",
        help="Environment name set for FLASK_ENV (default: development)",
    )
    parser.add_argument(
        "--secret-key",
        required=False,
        help=(
            "Secret key for Flask sessions. If omitted, the current value in .env is preserved or "
            "fallback defaults are used."
        ),
    )
    parser.add_argument(
        "--llm-api-base",
        help="Base URL for your local LLM API (optional).",
    )
    parser.add_argument(
        "--database-url",
        help="Override SQLALCHEMY_DATABASE_URI / DATABASE_URL (optional).",
    )
    parser.add_argument(
        "--env-path",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Path to the .env file that should be created/updated.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Only update the .env file without touching the database.",
    )
    return parser.parse_args()


def read_env(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        data[key.strip()] = value.strip()
    return data


def write_env(path: Path, values: Dict[str, str]) -> None:
    if path.exists():
        backup_path = path.with_suffix(path.suffix + BACKUP_SUFFIX)
        shutil.copy(path, backup_path)
        print(f"Existing {path.name} backed up to {backup_path.name}.")
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n")
    print(f"Updated environment variables written to {path}.")


def update_env_file(args: argparse.Namespace) -> Dict[str, str]:
    env_data = read_env(args.env_path)
    env_updates = {
        "FLASK_APP": args.flask_app,
        "FLASK_ENV": args.flask_env,
    }
    if args.secret_key:
        env_updates["SECRET_KEY"] = args.secret_key
    if args.llm_api_base:
        env_updates["LLM_API_BASE"] = args.llm_api_base
    if args.database_url:
        env_updates["DATABASE_URL"] = args.database_url

    env_data.update(env_updates)
    write_env(args.env_path, env_data)
    return env_data


def initialize_database() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
    print("Database initialized (instance/book_pipeline.db).")


def main() -> None:
    args = parse_args()
    env_values = update_env_file(args)

    if not args.skip_db:
        initialize_database()
    else:
        print("Database initialization skipped.")

    print("\nSetup complete! Summary:")
    for key in sorted(env_values):
        print(f"  {key}={env_values[key]}")


if __name__ == "__main__":
    main()

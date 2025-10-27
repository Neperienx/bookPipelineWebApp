"""Minimal web chat interface for the local TextGenerator model.

This module exposes a small Flask application that mimics the
single-session conversational workflow of ChatGPT.  Messages are stored in
the user's session so refreshing the page keeps the conversation intact until
it is cleared.  The underlying responses are produced by the local
``TextGenerator`` class defined in :mod:`text_generator`.

Run the application with ``flask --app chat_interface run`` after exporting
``LOCAL_GPT_MODEL_PATH`` (and an optional ``FLASK_SECRET_KEY``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List

from datetime import datetime

from flask import Flask, abort, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy

from text_generator import TextGenerator
from system_prompts import SYSTEM_PROMPTS

# Flask session requires a secret key.  Use an environment variable so the
# application can be run without editing source code.
DEFAULT_SECRET = "dev-secret-key-change-me"

# ``TextGenerator`` is expensive to initialise, so cache a single instance per
# process.  It loads lazily on the first request that needs it.
_generator: TextGenerator | None = None

# Database handle is created globally so unit tests can import the ``db`` object
# without instantiating the Flask application first.
db = SQLAlchemy()


class Project(db.Model):
    """Story project persisted in the local SQLite database."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    outline = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Project {self.id} {self.name!r}>"

# The Windows desktop deployment expects a specific local GGUF/Transformers
# directory.  Use it as a sensible default when the app is launched on that
# machine so users are not required to export an environment variable first.
DEFAULT_WINDOWS_MODEL_PATH = Path(
    r"C:\Users\nicol\Documents\01_Code\models\dolphin-2.6-mistral-7b"
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", DEFAULT_SECRET)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        database_url = "sqlite:///book_pipeline.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()

    @app.route("/", methods=["GET", "POST"])
    def dashboard() -> str:
        error = None

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            outline = request.form.get("outline", "").strip() or None

            if not name:
                error = "Please provide a project name."
            else:
                project = Project(name=name, outline=outline)
                db.session.add(project)
                db.session.commit()
                return redirect(url_for("project_detail", project_id=project.id))

        projects = Project.query.order_by(Project.created_at.desc()).all()
        return render_template("dashboard.html", projects=projects, error=error)

    @app.route("/projects/<int:project_id>", methods=["GET", "POST"])
    def project_detail(project_id: int) -> str:
        project = Project.query.get(project_id)
        if project is None:
            abort(404)

        session_key = _session_key(project_id)
        history = session.setdefault(session_key, [])
        error = None
        success = None

        if request.method == "POST":
            if "reset" in request.form:
                session.pop(session_key, None)
                return redirect(url_for("project_detail", project_id=project_id))

            user_message = request.form.get("message", "").strip()
            if user_message:
                history.append({"role": "user", "content": user_message})
                try:
                    generator = _get_generator()
                    assistant_reply = generator.generate_response(
                        _build_prompt(history)
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    error = f"The local model could not generate a reply: {exc}"
                    history.pop()
                else:
                    assistant_reply = assistant_reply or "(no reply)"
                    history.append(
                        {
                            "role": "assistant",
                            "content": assistant_reply,
                        }
                    )
                    clean_outline = assistant_reply.strip()
                    if clean_outline and clean_outline != "(no reply)":
                        project.outline = clean_outline
                        db.session.commit()
                        success = "Outline updated from assistant."
                session.modified = True
            else:
                error = "Please enter a message before sending."

        return render_template(
            "project.html",
            project=project,
            history=history,
            error=error,
            success=success,
        )

    return app


def _get_generator() -> TextGenerator:
    """Return a cached ``TextGenerator`` instance."""

    global _generator
    if _generator is None:
        model_path = os.environ.get("LOCAL_GPT_MODEL_PATH")

        if not model_path and os.name == "nt":
            if DEFAULT_WINDOWS_MODEL_PATH.exists():
                model_path = str(DEFAULT_WINDOWS_MODEL_PATH)

        if not model_path:
            raise RuntimeError(
                "Set the LOCAL_GPT_MODEL_PATH environment variable to the directory "
                "containing your local Hugging Face model."
            )

        _generator = TextGenerator(model_path)
    return _generator


def _build_prompt(history: Iterable[Dict[str, str]]) -> str:
    """Construct a conversation prompt from the stored history."""

    prompt_lines: List[str] = []
    system_prompt = SYSTEM_PROMPTS.get("outline_assistant")
    if system_prompt:
        prompt_lines.append(f"System: {system_prompt}")
    for message in history:
        if message["role"] == "user":
            prefix = "User"
        else:
            prefix = "Assistant"
        prompt_lines.append(f"{prefix}: {message['content']}")
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)


def _session_key(project_id: int) -> str:
    return f"chat_history_{project_id}"


# Allow ``python chat_interface.py`` to run the development server directly.
if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)

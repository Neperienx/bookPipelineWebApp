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

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy

from text_generator import TextGenerator
from system_prompts import SYSTEM_PROMPTS, get_character_fields

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

    characters = db.relationship(
        "Character",
        back_populates="project",
        order_by="Character.created_at.desc()",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Project {self.id} {self.name!r}>"


class Character(db.Model):
    """Character profile associated with a project."""

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    name = db.Column(db.String(160), nullable=True)
    age = db.Column(db.String(60), nullable=True)
    gender_pronouns = db.Column(db.String(120), nullable=True)
    basic_information = db.Column(db.Text, nullable=True)
    physical_appearance = db.Column(db.Text, nullable=True)
    personality = db.Column(db.Text, nullable=True)
    background = db.Column(db.Text, nullable=True)
    psychology = db.Column(db.Text, nullable=True)
    in_story = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    project = db.relationship("Project", back_populates="characters")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Character {self.id} project={self.project_id}>"

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

    @app.route(
        "/projects/<int:project_id>/characters",
        methods=["POST"],
    )
    def character_create(project_id: int) -> str:
        project = Project.query.get(project_id)
        if project is None:
            abort(404)

        character = Character(project=project)
        db.session.add(character)
        db.session.commit()

        return redirect(
            url_for(
                "character_detail",
                project_id=project_id,
                character_id=character.id,
            )
        )

    @app.route(
        "/projects/<int:project_id>/characters/<int:character_id>",
        methods=["GET", "POST"],
    )
    def character_detail(project_id: int, character_id: int) -> str:
        project = Project.query.get(project_id)
        if project is None:
            abort(404)

        character = Character.query.filter_by(
            id=character_id, project_id=project_id
        ).first()
        if character is None:
            abort(404)

        character_fields = get_character_fields()
        history_key = _character_session_key(project_id, character_id)
        history = session.setdefault(history_key, [])

        error = None
        success = None

        if request.method == "POST":
            if "reset" in request.form:
                session.pop(history_key, None)
                return redirect(
                    url_for(
                        "character_detail",
                        project_id=project_id,
                        character_id=character_id,
                    )
                )

            user_message = request.form.get("message", "").strip()
            if not user_message:
                error = "Please enter a message before sending."
            else:
                history.append({"role": "user", "content": user_message})
                try:
                    generator = _get_generator()
                    outline_text_raw = project.outline or "no outline has been provided yet"
                    outline_text = " ".join(outline_text_raw.split())
                    base_prompt = SYSTEM_PROMPTS.get("character_creation", {}).get(
                        "base",
                        "You are a writing assistant and we want to create a character.",
                    )
                    assistant_sections: List[str] = []

                    for field in character_fields:
                        prompt = _build_character_prompt(
                            base_prompt,
                            outline_text,
                            field,
                            history,
                        )
                        response = generator.generate_response(prompt) or ""
                        clean_response = response.strip()
                        setattr(character, field["key"], clean_response or None)
                        assistant_sections.append(
                            f"{field['label']}:\n{clean_response or '(no response)'}"
                        )

                    character.updated_at = datetime.utcnow()
                    db.session.commit()
                    success = "Character profile updated from assistant."

                    assistant_reply = "\n\n".join(assistant_sections).strip()
                    if assistant_reply:
                        history.append(
                            {
                                "role": "assistant",
                                "content": assistant_reply,
                            }
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    error = f"The local model could not generate a reply: {exc}"
                    history.pop()

                session.modified = True

        character_data = {
            field["key"]: getattr(character, field["key"])
            for field in character_fields
        }

        return render_template(
            "character.html",
            project=project,
            character=character,
            character_fields=character_fields,
            character_data=character_data,
            history=history,
            error=error,
            success=success,
        )

    @app.route(
        "/projects/<int:project_id>/characters/<int:character_id>/generate",
        methods=["POST"],
    )
    def character_generate(project_id: int, character_id: int):
        project = Project.query.get(project_id)
        if project is None:
            return jsonify({"error": "Project not found."}), 404

        character = Character.query.filter_by(
            id=character_id, project_id=project_id
        ).first()
        if character is None:
            return jsonify({"error": "Character not found."}), 404

        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        field_key = payload.get("field_key")
        is_first = bool(payload.get("is_first"))

        character_fields = get_character_fields()
        field = next((f for f in character_fields if f["key"] == field_key), None)
        if field is None:
            return jsonify({"error": "Unknown character field."}), 400

        if is_first and not message:
            return jsonify({"error": "Message content is required."}), 400

        history_key = _character_session_key(project_id, character_id)
        history = session.setdefault(history_key, [])

        try:
            generator = _get_generator()
        except Exception as exc:  # pragma: no cover - defensive
            return (
                jsonify(
                    {
                        "error": "The local model could not be initialised.",
                        "detail": str(exc),
                    }
                ),
                500,
            )

        if is_first:
            history.append({"role": "user", "content": message})
            session.modified = True

        outline_text_raw = project.outline or "no outline has been provided yet"
        outline_text = " ".join(outline_text_raw.split())
        base_prompt = SYSTEM_PROMPTS.get("character_creation", {}).get(
            "base",
            "You are a writing assistant and we want to create a character.",
        )

        try:
            prompt = _build_character_prompt(
                base_prompt,
                outline_text,
                field,
                history,
            )
            response = generator.generate_response(prompt) or ""
            clean_response = response.strip()
        except Exception as exc:  # pragma: no cover - defensive
            if is_first:
                history.pop()
                session.modified = True
            return (
                jsonify(
                    {
                        "error": "The local model could not generate a reply.",
                        "detail": str(exc),
                    }
                ),
                500,
            )

        setattr(character, field_key, clean_response or None)
        character.updated_at = datetime.utcnow()
        db.session.commit()

        assistant_content = (
            f"{field['label']}:\n{clean_response or '(no response)'}"
        )
        history.append({"role": "assistant", "content": assistant_content})
        session.modified = True

        return jsonify(
            {
                "field_key": field_key,
                "field_label": field["label"],
                "content": clean_response,
            }
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


def _build_character_prompt(
    base_prompt: str,
    outline_text: str,
    field: Dict[str, str],
    history: Iterable[Dict[str, str]],
) -> str:
    """Construct a prompt for the character creation flow."""

    system_line = (
        f"System: {base_prompt} "
        f"The story outline so far will be about {outline_text}. "
        f"In this step of the process we want to define a specific aspect of the character "
        f"{field['label']}: {field['description']}, please do so in {field['word_count']} words."
    )

    prompt_lines: List[str] = [system_line]
    for message in history:
        prefix = "User" if message["role"] == "user" else "Assistant"
        prompt_lines.append(f"{prefix}: {message['content']}")
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)


def _session_key(project_id: int) -> str:
    return f"chat_history_{project_id}"


def _character_session_key(project_id: int, character_id: int) -> str:
    return f"character_chat_{project_id}_{character_id}"


# Allow ``python chat_interface.py`` to run the development server directly.
if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)

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
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

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
                    config = SYSTEM_PROMPTS.get("character_creation", {})
                    base_prompt = config.get(
                        "base",
                        "You are a writing assistant and we want to create a character.",
                    )
                    json_rules = config.get("json_format_rules", "")
                    profile_data, _sections, assistant_reply = _run_character_profile_generation(
                        generator,
                        base_prompt,
                        json_rules,
                        outline_text,
                        character_fields,
                        history,
                    )
                except ValueError as exc:
                    error = str(exc)
                    history.pop()
                except Exception as exc:  # pragma: no cover - defensive
                    error = f"The local model could not generate a reply: {exc}"
                    history.pop()
                else:
                    _apply_character_profile(character, character_fields, profile_data)
                    character.updated_at = datetime.utcnow()
                    db.session.commit()
                    success = "Character profile updated from assistant."

                    if assistant_reply:
                        history.append(
                            {
                                "role": "assistant",
                                "content": assistant_reply,
                            }
                        )

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

        if not message:
            return jsonify({"error": "Message content is required."}), 400

        character_fields = get_character_fields()
        history_key = _character_session_key(project_id, character_id)
        history = session.setdefault(history_key, [])

        history.append({"role": "user", "content": message})
        session.modified = True

        try:
            generator = _get_generator()
        except Exception as exc:  # pragma: no cover - defensive
            history.pop()
            session.modified = True
            return (
                jsonify(
                    {
                        "error": "The local model could not be initialised.",
                        "detail": str(exc),
                    }
                ),
                500,
            )

        outline_text_raw = project.outline or "no outline has been provided yet"
        outline_text = " ".join(outline_text_raw.split())
        config = SYSTEM_PROMPTS.get("character_creation", {})
        base_prompt = config.get(
            "base",
            "You are a writing assistant and we want to create a character.",
        )
        json_rules = config.get("json_format_rules", "")

        try:
            profile_data, sections, assistant_reply = _run_character_profile_generation(
                generator,
                base_prompt,
                json_rules,
                outline_text,
                character_fields,
                history,
            )
        except ValueError as exc:
            history.pop()
            session.modified = True
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pragma: no cover - defensive
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

        _apply_character_profile(character, character_fields, profile_data)
        character.updated_at = datetime.utcnow()
        db.session.commit()

        if assistant_reply:
            history.append({"role": "assistant", "content": assistant_reply})
            session.modified = True

        character_data = {
            field["key"]: getattr(character, field["key"])
            for field in character_fields
        }

        return jsonify(
            {
                "character_data": character_data,
                "sections": sections,
                "assistant_reply": assistant_reply,
                "message": "Character profile updated from assistant.",
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


def _run_character_profile_generation(
    generator: TextGenerator,
    base_prompt: str,
    json_rules: str,
    outline_text: str,
    character_fields: Iterable[Dict[str, Any]],
    history: Iterable[Dict[str, str]],
) -> tuple[Dict[str, str], List[Dict[str, str]], str]:
    """Generate a complete character profile and return structured results."""

    fields = list(character_fields)
    prompt = _build_character_json_prompt(
        base_prompt,
        json_rules,
        outline_text,
        fields,
        history,
    )
    response = generator.generate_response(prompt) or ""
    profile_data = _parse_character_json(response, fields)

    sections: List[Dict[str, str]] = []
    for field in fields:
        key = field["key"]
        content = profile_data.get(key, "").strip()
        sections.append(
            {
                "key": key,
                "label": field.get("label", key),
                "content": content,
            }
        )

    assistant_reply = "\n\n".join(
        f"{section['label']}:\n{section['content'] or '(no response)'}"
        for section in sections
    ).strip()

    return profile_data, sections, assistant_reply


def _build_character_json_prompt(
    base_prompt: str,
    json_rules: str,
    outline_text: str,
    character_fields: Iterable[Dict[str, Any]],
    history: Iterable[Dict[str, str]],
) -> str:
    """Construct a single prompt that enforces JSON output for character fields."""

    fields = list(character_fields)
    expected_keys = ", ".join(
        f'"{field["key"]}"' for field in fields
    )
    template_lines = [
        f'  "{field["key"]}": ""' for field in fields
    ]
    json_template = "{\n" + ",\n".join(template_lines) + "\n}"

    field_guidance_lines = [
        f'- "{field["key"]}" ({field.get("label", field["key"])}): {field.get("description", "")}'
        for field in fields
    ]

    system_parts = [
        base_prompt.strip(),
        "Follow these rules exactly:",
        (
            "1. Respond exclusively with a single valid JSON object using double quotes and no "
            "trailing commas."
        ),
        (
            "2. Include exactly these keys in the root object and populate each one: "
            f"{expected_keys}."
        ),
        "3. Do not include Markdown fences, code blocks, or commentary outside the JSON.",
    ]

    if json_rules.strip():
        system_parts.append(json_rules.strip())

    system_parts.extend(
        [
            f"The story outline so far is: {outline_text}.",
            "Return JSON that matches this template:",
            json_template,
        ]
    )

    if field_guidance_lines:
        system_parts.append("Field guidance:")
        system_parts.extend(field_guidance_lines)

    system_line = "System: " + "\n".join(part for part in system_parts if part)

    prompt_lines: List[str] = [system_line]
    for message in history:
        prefix = "User" if message["role"] == "user" else "Assistant"
        prompt_lines.append(f"{prefix}: {message['content']}")
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)


def _parse_character_json(
    raw_response: str,
    character_fields: Iterable[Dict[str, Any]],
) -> Dict[str, str]:
    """Parse and validate the JSON response from the assistant."""

    fields = list(character_fields)
    cleaned = _strip_json_code_fences(raw_response)
    json_block = _extract_json_object(cleaned)
    if not json_block:
        raise ValueError(
            "The assistant response did not contain the expected JSON object. Please try again."
        )

    try:
        payload = json.loads(json_block)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "The assistant returned invalid JSON. Please try again."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "The assistant response was not a JSON object. Please try again."
        )

    parsed: Dict[str, str] = {}
    expected_keys = [field["key"] for field in fields]
    missing = [key for key in expected_keys if key not in payload]
    if missing:
        raise ValueError(
            "The assistant response was missing required fields. Please try again."
        )

    for key in expected_keys:
        value = payload.get(key, "")
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False)
        elif value is None:
            value_text = ""
        else:
            value_text = str(value)
        parsed[key] = value_text.strip()

    return parsed


def _strip_json_code_fences(text: str) -> str:
    """Remove Markdown code fences from ``text`` if they are present."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
    return cleaned


def _extract_json_object(text: str) -> str | None:
    """Return the first JSON object found in ``text`` or ``None``."""

    start = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : index + 1]

    return None


def _apply_character_profile(
    character: Character,
    character_fields: Iterable[Dict[str, Any]],
    profile_data: Dict[str, str],
) -> None:
    """Persist the generated character profile to the database model."""

    fields = list(character_fields)
    for field in fields:
        key = field["key"]
        value = profile_data.get(key, "").strip()
        setattr(character, key, value or None)


def _session_key(project_id: int) -> str:
    return f"chat_history_{project_id}"


def _character_session_key(project_id: int, character_id: int) -> str:
    return f"character_chat_{project_id}_{character_id}"


# Allow ``python chat_interface.py`` to run the development server directly.
if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)

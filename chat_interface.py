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

import logging
import os
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

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

from sqlalchemy import inspect, text

import torch

LOGGER = logging.getLogger(__name__)

from text_generator import TextGenerator
from system_prompts import (
    SYSTEM_PROMPTS,
    get_character_fields,
    get_character_input_fields,
)

# Flask session requires a secret key.  Use an environment variable so the
# application can be run without editing source code.
DEFAULT_SECRET = "dev-secret-key-change-me"

# ``TextGenerator`` is expensive to initialise, so cache a single instance per
# process.  It loads lazily on the first request that needs it.
_generator: TextGenerator | None = None

# Database handle is created globally so unit tests can import the ``db`` object
# without instantiating the Flask application first.
db = SQLAlchemy()


_CHAPTER_HEADING_PATTERN = re.compile(r"^\s*Chapter\s+(\d+)\s*:\s*(.*)$", re.IGNORECASE)
_TITLE_SPLIT_PATTERN = re.compile(r"\s*[—–-]\s*")


class Project(db.Model):
    """Story project persisted in the local SQLite database."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    outline = db.Column(db.Text, nullable=True)
    act1_outline = db.Column(db.Text, nullable=True)
    act2_outline = db.Column(db.Text, nullable=True)
    act3_outline = db.Column(db.Text, nullable=True)
    act_final_notes = db.Column(db.Text, nullable=True)
    act1_chapters = db.Column(db.Text, nullable=True)
    act2_chapters = db.Column(db.Text, nullable=True)
    act3_chapters = db.Column(db.Text, nullable=True)
    chapters_final_notes = db.Column(db.Text, nullable=True)
    act1_chapter_list = db.Column(db.Text, nullable=True)
    act2_chapter_list = db.Column(db.Text, nullable=True)
    act3_chapter_list = db.Column(db.Text, nullable=True)
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
    role_in_story = db.Column(db.String(160), nullable=True)
    character_outline = db.Column(db.Text, nullable=True)
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


def _ensure_character_columns() -> None:
    """Add missing columns required by the updated character schema."""

    inspector = inspect(db.engine)
    try:
        existing_columns = {
            column["name"] for column in inspector.get_columns("character")
        }
    except Exception:  # pragma: no cover - defensive fallback
        return

    alterations: List[str] = []
    if "role_in_story" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN role_in_story VARCHAR(160)")
    if "character_outline" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN character_outline TEXT")

    if not alterations:
        return

    with db.engine.connect() as connection:
        for statement in alterations:
            connection.execute(text(statement))
        connection.commit()


def _ensure_project_columns() -> None:
    """Add newly introduced project columns when they are missing."""

    inspector = inspect(db.engine)
    try:
        existing_columns = {
            column["name"] for column in inspector.get_columns("project")
        }
    except Exception:  # pragma: no cover - defensive fallback
        return

    alterations: List[str] = []
    column_specs = {
        "act1_outline": "ALTER TABLE project ADD COLUMN act1_outline TEXT",
        "act2_outline": "ALTER TABLE project ADD COLUMN act2_outline TEXT",
        "act3_outline": "ALTER TABLE project ADD COLUMN act3_outline TEXT",
        "act_final_notes": "ALTER TABLE project ADD COLUMN act_final_notes TEXT",
        "act1_chapters": "ALTER TABLE project ADD COLUMN act1_chapters TEXT",
        "act2_chapters": "ALTER TABLE project ADD COLUMN act2_chapters TEXT",
        "act3_chapters": "ALTER TABLE project ADD COLUMN act3_chapters TEXT",
        "chapters_final_notes": "ALTER TABLE project ADD COLUMN chapters_final_notes TEXT",
        "act1_chapter_list": "ALTER TABLE project ADD COLUMN act1_chapter_list TEXT",
        "act2_chapter_list": "ALTER TABLE project ADD COLUMN act2_chapter_list TEXT",
        "act3_chapter_list": "ALTER TABLE project ADD COLUMN act3_chapter_list TEXT",
    }

    for column_name, statement in column_specs.items():
        if column_name not in existing_columns:
            alterations.append(statement)

    if not alterations:
        return

    with db.engine.connect() as connection:
        for statement in alterations:
            connection.execute(text(statement))
        connection.commit()

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
        _ensure_character_columns()
        _ensure_project_columns()

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
        act_session_key = _act_session_key(project_id)
        act_history = session.setdefault(act_session_key, [])
        chapter_session_key = _chapter_session_key(project_id)
        chapter_history = session.setdefault(chapter_session_key, [])
        error = None
        success = None
        act_error = None
        act_success = None
        chapter_error = None
        chapter_warning = None
        chapter_success = None
        chapter_count_default = 10
        chapter_count_value = chapter_count_default
        chapter_debug_details: List[str] = []

        if request.method == "POST":
            chat_type = request.form.get("chat_type", "outline")

            if "reset" in request.form:
                if chat_type == "acts":
                    session.pop(act_session_key, None)
                elif chat_type == "chapters":
                    session.pop(chapter_session_key, None)
                else:
                    session.pop(session_key, None)
                return redirect(url_for("project_detail", project_id=project_id))

            user_message = request.form.get("message", "").strip()
            if chat_type == "acts":
                if user_message:
                    act_history.append({"role": "user", "content": user_message})
                    try:
                        generator = _get_generator()
                        act_results = _generate_three_act_outline(
                            generator,
                            project,
                            user_message,
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        act_error = (
                            "The local model could not generate the act outline: "
                            f"{exc}"
                        )
                        act_history.pop()
                    else:
                        device_type = generator.get_compute_device()
                        device_label = _normalise_device_label(device_type)
                        device_sentence = _device_usage_sentence(device_type)
                        acts = [result.strip() for result in act_results]
                        labels = ["Act I", "Act II", "Act III"]
                        for label, content in zip(labels, acts):
                            response_text = (
                                f"{label} outline:\n{content or '(no reply)'}"
                            )
                            act_history.append(
                                {
                                    "role": "assistant",
                                    "content": response_text,
                                    "device_type": device_label,
                                }
                            )
                        project.act_final_notes = user_message
                        project.act1_outline = acts[0] if acts else ""
                        project.act2_outline = acts[1] if len(acts) > 1 else ""
                        project.act3_outline = acts[2] if len(acts) > 2 else ""
                        db.session.commit()
                        act_success = (
                            "Act-by-act outline updated from assistant."
                            f"{device_sentence}"
                        )
                    session.modified = True
                else:
                    act_error = "Please enter a message before sending."
            elif chat_type == "chapters":
                chapters_count_raw = request.form.get("chapters_count", "").strip()
                try:
                    chapters_per_act = int(
                        chapters_count_raw or chapter_count_default
                    )
                except ValueError:
                    chapter_error = "Please enter a valid positive number of chapters."
                    chapters_per_act = chapter_count_default
                else:
                    chapter_count_value = chapters_per_act

                if chapter_error is None:
                    if chapters_per_act <= 0:
                        chapter_error = "Please enter a valid positive number of chapters."
                    elif not user_message:
                        chapter_error = "Please enter a message before sending."
                    else:
                        chapter_history.append(
                            {"role": "user", "content": user_message}
                        )
                        try:
                            generator = _get_generator()
                            (
                                chapter_texts,
                                chapter_structures,
                                chapter_debug_details,
                                chapter_all_valid,
                            ) = _generate_chapter_outlines(
                                generator,
                                project,
                                user_message,
                                chapters_per_act,
                            )
                        except Exception as exc:  # pragma: no cover - defensive
                            chapter_error = (
                                "The local model could not generate the chapter outline: "
                                f"{exc}"
                            )
                            chapter_history.pop()
                        else:
                            if chapter_debug_details:
                                for entry in chapter_debug_details:
                                    LOGGER.info("Chapter generation debug: %s", entry)
                            device_type = generator.get_compute_device()
                            device_label = _normalise_device_label(device_type)
                            device_sentence = _device_usage_sentence(device_type)
                            chapters = [result.strip() for result in chapter_texts]
                            labels = ["Act I", "Act II", "Act III"]
                            for label, content in zip(labels, chapters):
                                response_text = (
                                    f"{label} chapters:\n{content or '(no reply)'}"
                                )
                                chapter_history.append(
                                    {
                                        "role": "assistant",
                                        "content": response_text,
                                        "device_type": device_label,
                                    }
                                )
                            project.chapters_final_notes = user_message
                            project.act1_chapters = chapters[0] if chapters else ""
                            project.act2_chapters = (
                                chapters[1] if len(chapters) > 1 else ""
                            )
                            project.act3_chapters = (
                                chapters[2] if len(chapters) > 2 else ""
                            )
                            project.act1_chapter_list = (
                                json.dumps(chapter_structures[0], ensure_ascii=False)
                                if chapter_structures and len(chapter_structures) > 0
                                else None
                            )
                            project.act2_chapter_list = (
                                json.dumps(chapter_structures[1], ensure_ascii=False)
                                if chapter_structures and len(chapter_structures) > 1
                                else None
                            )
                            project.act3_chapter_list = (
                                json.dumps(chapter_structures[2], ensure_ascii=False)
                                if chapter_structures and len(chapter_structures) > 2
                                else None
                            )
                            db.session.commit()
                            if not chapter_all_valid:
                                chapter_warning = (
                                    "Chapter outline generation completed with validation warnings. "
                                    "Review the debug log below for specifics."
                                )
                            chapter_success = (
                                "Chapter-by-chapter outline updated from assistant."
                                f"{device_sentence}"
                            )
                        session.modified = True
            else:
                if user_message:
                    history.append({"role": "user", "content": user_message})
                    try:
                        generator = _get_generator()
                        assistant_reply_raw = generator.generate_response(
                            _build_outline_prompt(project, history)
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        error = (
                            "The local model could not generate a reply: " f"{exc}"
                        )
                        history.pop()
                    else:
                        device_type = generator.get_compute_device()
                        assistant_reply = assistant_reply_raw or "(no reply)"
                        history.append(
                            {
                                "role": "assistant",
                                "content": assistant_reply,
                                "device_type": _normalise_device_label(device_type),
                            }
                        )
                        clean_outline = assistant_reply.strip()
                        if clean_outline and clean_outline != "(no reply)":
                            project.outline = clean_outline
                            db.session.commit()
                            success_suffix = _device_usage_sentence(device_type)
                            success = (
                                "Outline updated from assistant."
                                f"{success_suffix}"
                            )
                    session.modified = True
                else:
                    error = "Please enter a message before sending."

        device_hint = _compute_device_hint()

        return render_template(
            "project.html",
            project=project,
            history=history,
            error=error,
            success=success,
            act_history=act_history,
            act_error=act_error,
            act_success=act_success,
            chapter_history=chapter_history,
            chapter_error=chapter_error,
            chapter_warning=chapter_warning,
            chapter_success=chapter_success,
            chapter_count_value=chapter_count_value,
            chapter_debug_details=chapter_debug_details,
            device_hint=device_hint,
            act_chapter_lists=_collect_project_chapter_lists(project),
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
        input_fields = get_character_input_fields()
        form_key = _character_form_state_key(project_id, character_id)

        if request.method == "POST" and "reset_form" in request.form:
            session.pop(form_key, None)
            session.modified = True
            return redirect(
                url_for(
                    "character_detail",
                    project_id=project_id,
                    character_id=character_id,
                )
            )

        stored_form = session.get(form_key, {})
        form_data: Dict[str, str] = {}
        for field in input_fields:
            key = field["key"]
            if key == "name":
                default_value = character.name or stored_form.get(key, "")
            elif key == "role_in_story":
                default_value = character.role_in_story or stored_form.get(key, "")
            else:
                default_value = stored_form.get(key, "")
            form_data[key] = default_value or ""

        device_hint = _compute_device_hint()

        return render_template(
            "character.html",
            project=project,
            character=character,
            character_fields=character_fields,
            input_fields=input_fields,
            form_data=form_data,
            device_hint=device_hint,
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
        inputs_payload = payload.get("inputs")
        if not isinstance(inputs_payload, dict):
            return jsonify({"error": "Invalid request payload."}), 400

        input_fields = get_character_input_fields()
        trimmed_inputs: Dict[str, str] = {}
        for field in input_fields:
            key = field["key"]
            raw_value = inputs_payload.get(key, "")
            if raw_value is None:
                value_text = ""
            else:
                value_text = str(raw_value).strip()
            trimmed_inputs[key] = value_text

        name = trimmed_inputs.get("name", "")
        role = trimmed_inputs.get("role_in_story", "")
        if not name or not role:
            return (
                jsonify({"error": "Name and role in the story are required."}),
                422,
            )

        prompt_inputs = {key: value for key, value in trimmed_inputs.items() if value}

        form_key = _character_form_state_key(project_id, character_id)
        session[form_key] = trimmed_inputs
        session.modified = True

        character_fields = get_character_fields()

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
                character_fields,
                prompt_inputs,
                input_fields,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pragma: no cover - defensive
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
        character.name = name
        character.role_in_story = role
        character.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify(
            {
                "character": {
                    "name": character.name,
                    "role_in_story": character.role_in_story,
                },
                "character_outline": profile_data.get("character_outline", ""),
                "sections": sections,
                "assistant_reply": assistant_reply,
                "device_type": generator.get_compute_device(),
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


def _act_session_key(project_id: int) -> str:
    """Return the session key used for act outline conversations."""

    return f"act_chat_history_{project_id}"


def _chapter_session_key(project_id: int) -> str:
    """Return the session key used for chapter outline conversations."""

    return f"chapter_chat_history_{project_id}"


def _normalise_whitespace(value: str) -> str:
    """Collapse excessive whitespace in generated text."""

    return re.sub(r"\s+", " ", value or "").strip()


def _extract_title_summary(raw_content: str) -> Tuple[str, str]:
    """Split a chapter line into title and summary parts."""

    if not raw_content:
        return "", ""

    parts = _TITLE_SPLIT_PATTERN.split(raw_content, maxsplit=1)
    if len(parts) == 2:
        title, summary = parts
        return title.strip(), summary.strip()

    cleaned = raw_content.strip()
    return cleaned, ""


def _parse_chapter_entries(text: str) -> List[Dict[str, Any]]:
    """Return structured chapter entries parsed from ``text``."""

    entries: List[Dict[str, Any]] = []
    if not text:
        return entries

    current: Dict[str, Any] | None = None
    for line in text.splitlines():
        match = _CHAPTER_HEADING_PATTERN.match(line)
        if match:
            if current is not None:
                current["raw"] = _normalise_whitespace(current.get("raw", ""))
                title, summary = _extract_title_summary(current.get("raw", ""))
                current["title"] = title
                current["summary"] = summary
                entries.append(current)

            number = int(match.group(1))
            remainder = match.group(2).strip()
            current = {
                "number": number,
                "raw": remainder,
            }
            continue

        if current is None:
            continue

        stripped = line.strip()
        if not stripped:
            continue

        raw_value = current.get("raw", "")
        if raw_value:
            raw_value = f"{raw_value} {stripped}"
        else:
            raw_value = stripped
        current["raw"] = raw_value

    if current is not None:
        current["raw"] = _normalise_whitespace(current.get("raw", ""))
        title, summary = _extract_title_summary(current.get("raw", ""))
        current["title"] = title
        current["summary"] = summary
        entries.append(current)

    return entries


def _serialise_chapter_entries(entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reduce parsed entries to JSON serialisable dictionaries."""

    serialised: List[Dict[str, Any]] = []
    for entry in entries:
        number = int(entry.get("number", 0) or 0)
        title = _normalise_whitespace(str(entry.get("title", "")))
        summary = _normalise_whitespace(str(entry.get("summary", "")))
        serialised.append(
            {
                "number": number,
                "title": title,
                "summary": summary,
            }
        )
    return serialised


def _render_chapter_entries(entries: Sequence[Dict[str, Any]]) -> str:
    """Format structured entries back into canonical chapter text."""

    lines: List[str] = []
    for entry in entries:
        number = entry.get("number")
        try:
            number_int = int(number)
        except (TypeError, ValueError):
            continue

        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip()
        if summary:
            line = f"Chapter {number_int}: {title} — {summary}".strip()
        else:
            line = f"Chapter {number_int}: {title}".strip()
        lines.append(line)

    return "\n".join(lines).strip()


def _validate_chapter_outline(
    response: str, expected_count: int
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Return whether ``response`` matches the required chapter format."""

    entries = _parse_chapter_entries(response)
    if len(entries) != expected_count:
        return (
            False,
            entries,
            f"expected {expected_count} chapters but found {len(entries)}",
        )

    seen_numbers: set[int] = set()
    for index, entry in enumerate(entries, start=1):
        number = int(entry.get("number", 0) or 0)
        if number in seen_numbers:
            return False, entries, f"chapter number {number} is duplicated"
        seen_numbers.add(number)
        if number != index:
            return (
                False,
                entries,
                f"chapter numbers must increase sequentially starting at 1 (found {number} at position {index})",
            )
        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip()
        if not title or not summary:
            return (
                False,
                entries,
                "each chapter needs a title and a 2-3 sentence summary separated by a dash",
            )

    return True, entries, ""


def _load_chapter_list(
    serialised: str | None, fallback_text: str | None
) -> List[Dict[str, Any]]:
    """Deserialize stored chapter entries with parsing fallback."""

    if serialised:
        try:
            data = json.loads(serialised)
        except (TypeError, json.JSONDecodeError):
            data = None
        if isinstance(data, list):
            cleaned: List[Dict[str, Any]] = []
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                number = entry.get("number")
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                try:
                    number_int = int(number)
                except (TypeError, ValueError):
                    continue
                cleaned.append(
                    {
                        "number": number_int,
                        "title": str(title).strip(),
                        "summary": str(summary).strip(),
                    }
                )
            if cleaned:
                return cleaned

    if fallback_text:
        parsed = _parse_chapter_entries(fallback_text)
        if parsed:
            return _serialise_chapter_entries(parsed)

    return []


def _collect_project_chapter_lists(project: Project) -> Dict[int, List[Dict[str, Any]]]:
    """Return per-act chapter lists for template rendering."""

    return {
        1: _load_chapter_list(project.act1_chapter_list, project.act1_chapters),
        2: _load_chapter_list(project.act2_chapter_list, project.act2_chapters),
        3: _load_chapter_list(project.act3_chapter_list, project.act3_chapters),
    }


def _generate_single_act_chapters(
    generator: TextGenerator,
    act_number: int,
    outline_text: str,
    act_outlines: Sequence[Tuple[int, str]],
    character_context: str,
    final_notes: str,
    previous_chapters: Sequence[Tuple[int, str]],
    chapters_per_act: int,
    *,
    max_attempts: int = 3,
) -> Tuple[str, List[Dict[str, Any]], List[str], bool]:
    """Run chapter generation with validation and optional retries."""

    attempt = 0
    prompt = _build_chapter_prompt(
        act_number,
        outline_text,
        act_outlines,
        character_context,
        final_notes,
        previous_chapters,
        chapters_per_act,
    )
    last_response = ""
    last_entries: List[Dict[str, Any]] = []
    debug_messages: List[str] = []
    attempt_start_overall = time.perf_counter()

    while attempt < max_attempts:
        attempt += 1
        attempt_start = time.perf_counter()
        response = generator.generate_response(prompt) or ""
        duration = time.perf_counter() - attempt_start
        response_clean = response.strip()
        is_valid, entries, error_message = _validate_chapter_outline(
            response_clean, chapters_per_act
        )
        chapter_count = len(entries)
        character_count = len(response_clean)
        if is_valid:
            formatted_text = _render_chapter_entries(entries)
            success_message = (
                f"Act {act_number} attempt {attempt} succeeded in {duration:.2f}s "
                f"with {chapter_count} chapters (characters={character_count})."
            )
            LOGGER.info(success_message)
            debug_messages.append(success_message)
            return formatted_text, entries, debug_messages, True

        error_detail = error_message or "unknown validation error"
        failure_message = (
            f"Act {act_number} attempt {attempt} failed validation in {duration:.2f}s: "
            f"{error_detail} (chapters={chapter_count}, characters={character_count})."
        )
        LOGGER.warning(failure_message)
        debug_messages.append(failure_message)

        last_response = response_clean
        last_entries = entries
        prompt = _build_chapter_prompt(
            act_number,
            outline_text,
            act_outlines,
            character_context,
            final_notes,
            previous_chapters,
            chapters_per_act,
            feedback=(
                "The format validator rejected the last draft: "
                f"{error_detail}. Produce a fresh list that follows every instruction."
            ),
            previous_response=last_response,
        )

    elapsed = time.perf_counter() - attempt_start_overall
    if last_entries:
        formatted_text = _render_chapter_entries(last_entries)
    else:
        formatted_text = last_response
    fallback_message = (
        f"Act {act_number} exhausted {max_attempts} attempts in {elapsed:.2f}s. "
        "Returning the last draft without a successful validation pass."
    )
    LOGGER.error(fallback_message)
    debug_messages.append(fallback_message)
    return formatted_text, last_entries, debug_messages, False


def _build_outline_prompt(project: Project, history: Iterable[Dict[str, str]]) -> str:
    """Construct a prompt for the outline assistant that includes characters."""

    prompt_lines: List[str] = []
    system_prompt = SYSTEM_PROMPTS.get("outline_assistant")
    if system_prompt:
        prompt_lines.append(f"System: {system_prompt}")

    character_context = _collect_character_context(project.characters)
    if character_context and not character_context.strip().startswith(
        "No character descriptions available."
    ):
        prompt_lines.append(
            "System: Reference the following character roster when crafting the outline.\n"
            f"{character_context}"
        )

    for message in history:
        if message["role"] == "user":
            prefix = "User"
        else:
            prefix = "Assistant"
        prompt_lines.append(f"{prefix}: {message['content']}")

    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)


def _generate_three_act_outline(
    generator: TextGenerator,
    project: Project,
    final_notes: str,
) -> Tuple[str, str, str]:
    """Generate a three-act outline informed by project context."""

    outline_text = (project.outline or "No outline has been provided yet.").strip()
    character_context = _collect_character_context(project.characters)
    notes_text = final_notes.strip() or "No final notes provided."

    results: List[str] = []
    previous_acts: List[Tuple[int, str]] = []

    for act_number in (1, 2, 3):
        prompt = _build_act_prompt(
            act_number,
            outline_text,
            character_context,
            notes_text,
            previous_acts,
        )
        response = generator.generate_response(prompt) or ""
        response_clean = response.strip()
        results.append(response_clean)
        previous_acts.append((act_number, response_clean))

    while len(results) < 3:
        results.append("")

    return results[0], results[1], results[2]


def _generate_chapter_outlines(
    generator: TextGenerator,
    project: Project,
    final_notes: str,
    chapters_per_act: int,
) -> Tuple[List[str], List[List[Dict[str, Any]]], List[str], bool]:
    """Generate chapter-by-chapter outlines for each act."""

    if chapters_per_act <= 0:
        raise ValueError("chapters_per_act must be a positive integer")

    outline_text = (project.outline or "No outline has been provided yet.").strip()
    character_context = _collect_character_context(project.characters)
    notes_text = final_notes.strip() or "No additional notes provided."

    act_outlines = [
        (1, (project.act1_outline or "No outline generated yet.").strip()),
        (2, (project.act2_outline or "No outline generated yet.").strip()),
        (3, (project.act3_outline or "No outline generated yet.").strip()),
    ]

    results: List[str] = []
    structured_results: List[List[Dict[str, Any]]] = []
    previous_chapters: List[Tuple[int, str]] = []
    debug_entries: List[str] = []
    all_valid = True

    for act_number in (1, 2, 3):
        (
            formatted_text,
            entries,
            act_debug_entries,
            act_valid,
        ) = _generate_single_act_chapters(
            generator,
            act_number,
            outline_text,
            act_outlines,
            character_context,
            notes_text,
            previous_chapters,
            chapters_per_act,
        )
        results.append(formatted_text.strip())
        serialised_entries = _serialise_chapter_entries(entries)
        structured_results.append(serialised_entries)
        previous_chapters.append((act_number, formatted_text.strip()))
        debug_entries.extend(act_debug_entries)
        if not act_valid:
            all_valid = False

    while len(results) < 3:
        results.append("")
        structured_results.append([])

    if all_valid:
        summary = "All acts passed validation without issues."
        LOGGER.info("Chapter generation summary: %s", summary)
    else:
        summary = (
            "One or more acts failed validation; the latest draft was returned for review."
        )
        LOGGER.warning("Chapter generation summary: %s", summary)
    debug_entries.append(summary)

    return results, structured_results, debug_entries, all_valid


def _collect_character_context(characters: Iterable["Character"]) -> str:
    """Build a readable summary of all available character descriptions."""

    entries: List[str] = []
    for character in characters:
        sections: List[str] = []
        if character.name:
            sections.append(f"Name: {character.name}")
        if character.role_in_story:
            sections.append(f"Role in story: {character.role_in_story}")
        if character.character_outline:
            sections.append(f"Character outline: {character.character_outline}")
        if sections:
            entries.append("\n".join(sections))

    if not entries:
        return "No character descriptions available."

    return "\n\n".join(entries)


def _build_act_prompt(
    act_number: int,
    outline_text: str,
    character_context: str,
    final_notes: str,
    previous_acts: Sequence[Tuple[int, str]],
) -> str:
    """Construct a tailored prompt for the requested act."""

    act_labels = {1: "Act I", 2: "Act II", 3: "Act III"}
    label = act_labels.get(act_number, f"Act {act_number}")

    config = SYSTEM_PROMPTS.get("act_outline", {})
    base_prompt = config.get(
        "base",
        (
            "You are a collaborative narrative designer tasked with producing a "
            "beat-by-beat act outline that is vivid, coherent, and firmly rooted "
            "in the provided story materials."
        ),
    )
    act_guidance_map = config.get("acts", {})
    act_guidance = act_guidance_map.get(
        act_number,
        "Ensure the act fulfils its role in classic three-act structure.",
    )

    user_sections: List[str] = [
        f"Create {label} of a three-act outline while respecting the context below.",
        "",
        "Project outline:",
        outline_text or "No outline has been provided yet.",
        "",
        "Character roster:",
        character_context or "No character descriptions available.",
        "",
        "Author final notes:",
        final_notes or "No final notes provided.",
    ]

    if previous_acts:
        user_sections.append("")
        user_sections.append("Previous acts for continuity:")
        for previous_act_number, summary in previous_acts:
            previous_label = act_labels.get(
                previous_act_number,
                f"Act {previous_act_number}",
            )
            cleaned_summary = summary.strip() or "(no summary available)"
            user_sections.append(f"{previous_label} summary:\n{cleaned_summary}")

    user_sections.extend(
        [
            "",
            f"Guidance for {label}:",
            act_guidance.strip(),
            "",
            (
                "Deliver the response as 4-6 numbered beats. Each beat should be "
                "one or two sentences that emphasise goals, conflicts, and "
                "reversals relevant to this act."
            ),
        ]
    )

    user_message = "\n".join(user_sections).strip()

    return "\n".join(
        [
            f"System: {base_prompt}",
            "User:",
            user_message,
            "Assistant:",
        ]
    )


def _build_chapter_prompt(
    act_number: int,
    outline_text: str,
    act_outlines: Sequence[Tuple[int, str]],
    character_context: str,
    final_notes: str,
    previous_chapters: Sequence[Tuple[int, str]],
    chapters_per_act: int,
    feedback: str | None = None,
    previous_response: str | None = None,
) -> str:
    """Construct a prompt for the requested chapter-by-chapter outline."""

    act_labels = {1: "Act I", 2: "Act II", 3: "Act III"}
    label = act_labels.get(act_number, f"Act {act_number}")

    config = SYSTEM_PROMPTS.get("chapter_outline", {})
    base_prompt = config.get(
        "base",
        (
            "You are a creative writing assistant who expands beat outlines into "
            "detailed, chapter-by-chapter plans while preserving continuity and "
            "dramatic momentum."
        ),
    )
    format_instructions = config.get(
        "format",
        (
            "Present each chapter as a numbered list entry in the form "
            "'Chapter <number>: <evocative title> — <2-3 sentence summary>'."
        ),
    )
    focus_instructions = config.get(
        "act_focus",
        "Focus exclusively on {act_label}. Reference earlier acts only for continuity and do not plan future acts.",
    )
    count_instructions = config.get(
        "chapter_count",
        "Outline this act in exactly {chapter_count} chapters, ensuring each advances tension and character arcs.",
    )

    try:
        focus_line = focus_instructions.format(act_label=label)
    except KeyError:
        focus_line = focus_instructions

    try:
        count_line = count_instructions.format(
            chapter_count=chapters_per_act, act_label=label
        )
    except KeyError:
        count_line = count_instructions

    try:
        format_line = format_instructions.format(
            chapter_count=chapters_per_act, act_label=label
        )
    except KeyError:
        format_line = format_instructions

    outline_sections: List[str] = []
    for outline_act_number, outline_text_value in act_outlines:
        outline_label = act_labels.get(
            outline_act_number, f"Act {outline_act_number}"
        )
        cleaned_outline = outline_text_value.strip() or "(no outline provided)"
        outline_sections.append(
            f"{outline_label} outline:\n{cleaned_outline}"
        )

    if not outline_sections:
        outline_sections.append("(no act outlines provided)")

    chapter_sections: List[str] = []
    for chapter_act_number, chapters_text in previous_chapters:
        chapter_label = act_labels.get(
            chapter_act_number, f"Act {chapter_act_number}"
        )
        cleaned_chapters = chapters_text.strip() or "(no chapters available)"
        chapter_sections.append(
            f"{chapter_label} chapters so far:\n{cleaned_chapters}"
        )

    user_sections: List[str] = [
        "You are a creative writing assistant working from a complete three-act outline.",
        f"Your role now is to write a chapter-by-chapter outline of this act: {label}.",
        f"You will outline this act in {chapters_per_act} chapters.",
        "",
        "Story overview:",
        outline_text or "No broad outline has been provided yet.",
        "",
        "Full three-act outline for context:",
        "\n\n".join(outline_sections),
        "",
        f"Focus on {label}. To reinforce the target, the act outline is repeated below:",
    ]

    current_outline = next(
        (
            outline_text_value.strip()
            for outline_act_number, outline_text_value in act_outlines
            if outline_act_number == act_number
        ),
        "(no outline provided)",
    )
    user_sections.append(current_outline or "(no outline provided)")

    user_sections.extend(
        [
            "",
            "Character roster:",
            character_context or "No character descriptions available.",
        ]
    )

    if chapter_sections:
        user_sections.extend(["", "Previous chapters for continuity:", "\n\n".join(chapter_sections)])

    user_sections.extend(
        [
            "",
            "Author notes for this pass:",
            final_notes or "No additional notes provided.",
            "",
            focus_line,
            count_line,
            format_line,
            "Ensure chapter arcs build naturally from prior acts and prepare the next act where appropriate without jumping ahead.",
            "",
            "Formatting requirements:",
            "- Each chapter must appear on its own line with no leading bullets or numbering other than the required label.",
            "- Begin every line exactly with 'Chapter <number>:' (for example, 'Chapter 1:').",
            "- After the colon, include an evocative chapter title, then an em dash (—) or hyphen, followed by a 2-3 sentence summary.",
            "- Do not add commentary or sections before or after the chapter list.",
        ]
    )

    if feedback:
        user_sections.extend(
            [
                "",
                "Format validator feedback:",
                feedback.strip(),
            ]
        )
        if previous_response:
            user_sections.extend(
                [
                    "",
                    "Previous invalid response (for reference only):",
                    previous_response.strip(),
                ]
            )
        user_sections.extend(
            [
                "",
                "Regenerate the complete chapter list now, strictly following every rule above without mentioning this instruction.",
            ]
        )

    user_message = "\n".join(user_sections).strip()

    return "\n".join(
        [
            f"System: {base_prompt}",
            "User:",
            user_message,
            "Assistant:",
        ]
    )

def _run_character_profile_generation(
    generator: TextGenerator,
    base_prompt: str,
    json_rules: str,
    character_fields: Iterable[Dict[str, Any]],
    user_inputs: Dict[str, str],
    input_fields: Iterable[Dict[str, Any]],
) -> tuple[Dict[str, str], List[Dict[str, str]], str]:
    """Generate a complete character profile and return structured results."""

    fields = list(character_fields)
    prompt = _build_character_json_prompt(
        base_prompt,
        json_rules,
        fields,
        user_inputs,
        list(input_fields),
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
    character_fields: Iterable[Dict[str, Any]],
    user_inputs: Dict[str, str],
    input_fields: Iterable[Dict[str, Any]],
) -> str:
    """Construct a prompt that enforces JSON output for the character outline."""

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

    system_parts: List[str] = []
    if base_prompt.strip():
        system_parts.append(base_prompt.strip())
    if json_rules.strip():
        system_parts.append(json_rules.strip())

    system_parts.extend(
        [
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
            "4. Limit \"character_outline\" to 100 words or fewer, writing in a vivid third-person voice.",
            "\nJSON schema template:",
            json_template,
            "\nGuidance for each field:",
            *field_guidance_lines,
        ]
    )

    user_lines: List[str] = [
        (
            "Focus on developing the character profile independently. "
            "Do not assume any existing story outline—use only the supplied details "
            "and your own fitting inventions."
        )
    ]
    provided_details: List[str] = []
    for field in input_fields:
        key = field.get("key")
        value = user_inputs.get(key, "")
        if not value:
            continue
        label = field.get("label", key)
        provided_details.append(f"- {label}: {value}")

    if provided_details:
        user_lines.append("Character details supplied by the author:")
        user_lines.extend(provided_details)
        user_lines.append(
            "Incorporate every provided detail. Invent any missing aspects so the outline feels cohesive."
        )
    else:
        user_lines.append(
            "No specific character details were supplied. Invent fitting information that supports the story."
        )

    user_lines.append("Return only the JSON object and nothing else.")

    prompt_lines: List[str] = ["System: " + "\n".join(system_parts)]
    prompt_lines.append("User: " + "\n".join(user_lines))
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


def _normalise_device_label(device_type: str | None) -> str:
    """Return an uppercase device label for UI display."""

    if not device_type:
        return ""
    label = str(device_type).strip()
    if not label:
        return ""
    return label.upper()


def _device_usage_sentence(device_type: str | None) -> str:
    """Return a sentence fragment describing the compute backend used."""

    label = _normalise_device_label(device_type)
    if not label:
        return ""
    return f" Generated using the {label} backend."


def _compute_device_hint() -> str:
    """Return a best-effort guess at the compute device available to the model."""

    if torch.cuda.is_available():
        return "GPU"
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and getattr(mps_backend, "is_available", lambda: False)():
        return "GPU"
    return "CPU"


def _session_key(project_id: int) -> str:
    return f"chat_history_{project_id}"


def _character_form_state_key(project_id: int, character_id: int) -> str:
    return f"character_form_{project_id}_{character_id}"


# Allow ``python chat_interface.py`` to run the development server directly.
if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)

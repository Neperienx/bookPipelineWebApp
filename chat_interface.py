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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from api_handler import OpenAIUnifiedGenerator
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

from sqlalchemy import and_, inspect, or_, text

import torch

LOGGER = logging.getLogger(__name__)

from text_generator import TextGenerator
from pdf_handler import PDFExportError, export_chapter_drafts_to_pdf
from text_exporter import TextExportError, export_chapter_drafts_to_txt
from system_prompts import (
    SYSTEM_PROMPTS,
    get_character_fields,
    get_character_input_fields,
    get_prompt_max_new_tokens,
)

class OpenAIAPIRateLimitError(RuntimeError):
    """Raised when the OpenAI API reports a rate limit condition."""

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(
            message
            or "The OpenAI API rate limit has been exceeded. Please try again shortly."
        )






def _raise_for_openai_api_error(exc: Exception) -> None:
    """Raise a specialised error when an API call reports rate limiting."""
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        raise OpenAIAPIRateLimitError() from exc

    # Some SDK exceptions carry .code or .error with metadata
    code = getattr(exc, "code", None)
    if isinstance(code, str) and "rate" in code.lower():
        raise OpenAIAPIRateLimitError() from exc

    message = str(exc).lower()
    if "rate limit" in message or "too many requests" in message:
        raise OpenAIAPIRateLimitError() from exc


# Flask session requires a secret key.  Use an environment variable so the
# application can be run without editing source code.
DEFAULT_SECRET = "dev-secret-key-change-me"

# ``TextGenerator`` is expensive to initialise, so cache a single instance per
# process.  It loads lazily on the first request that needs it.
_generator: TextGenerator | None = None

# Cache for the optional OpenAI API backend.  The configuration is loaded from
# ``openai_config.json`` when the user explicitly opts-in via the UI.
_openai_generator: OpenAIUnifiedGenerator | None = None
_openai_signature: Tuple[str, str] | None = None
_OPENAI_CONFIG_PATH = Path(__file__).resolve().parent / "openai_config.json"

# Default number of previous chapters to reference when drafting new prose.
_DEFAULT_DRAFT_CONTEXT_COUNT = 2

# Database handle is created globally so unit tests can import the ``db`` object
# without instantiating the Flask application first.
db = SQLAlchemy()



def _load_openai_config() -> Optional[Dict[str, str]]:
    """Read API credentials from ``openai_config.json`` if available."""

    try:
        raw_text = _OPENAI_CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:  # pragma: no cover - IO failure
        LOGGER.warning("Could not read OpenAI configuration: %s", exc)
        return None

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        LOGGER.warning("Invalid JSON in OpenAI configuration: %s", exc)
        return None

    model = str(data.get("model", "")).strip()
    api_key = str(data.get("api_key", "")).strip()
    if not model or not api_key:
        LOGGER.warning(
            "OpenAI configuration is missing the 'model' or 'api_key' field."
        )
        return None

    return {"model": model, "api_key": api_key}


def _get_openai_generator() -> OpenAIUnifiedGenerator:
    """Return a cached OpenAI API adapter configured from disk."""
    global _openai_generator, _openai_signature
    config = _load_openai_config()
    if not config:
        raise RuntimeError(
            "OpenAI API configuration not found. Update openai_config.json with your model and API key."
        )

    signature = (config["model"], config["api_key"])
    if _openai_generator is None or _openai_signature != signature:
        _openai_generator = OpenAIUnifiedGenerator(*signature)
        _openai_signature = signature

    return _openai_generator



def _resolve_text_generator(use_api: bool) -> TextGenerator | OpenAIUnifiedGenerator:
    """Return the requested text generation backend."""

    if use_api:
        return _get_openai_generator()
    return _get_generator()


def _is_api_requested(data: Mapping[str, Any]) -> bool:
    """Return True when the submitted form asks to use the API backend."""

    raw_value = data.get("use_api")
    if raw_value is None:
        return False
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    if isinstance(raw_value, (list, tuple)):
        return any(_is_api_requested({"use_api": item}) for item in raw_value)
    return bool(raw_value)


_CHAPTER_HEADER_PATTERN = re.compile(
    r"^\s*Chapter\s*:\s*Chapter\s+(\d+)\s*[—–-]\s*(.*)$",
    re.IGNORECASE,
)
_LEGACY_CHAPTER_HEADING_PATTERN = re.compile(
    r"^\s*Chapter\s+(\d+)\s*:\s*(.*)$",
    re.IGNORECASE,
)
_TITLE_SPLIT_PATTERN = re.compile(r"\s*[—–-]\s*")
_ACT_SECTION_PATTERN = re.compile(
    r"(Act:\s.*?)(?=(?:\r?\nAct:\s)|\Z)", re.DOTALL
)
_SUPPORTING_HEADER_PATTERN = re.compile(
    r"^\s*Character\s*:\s*(.+)$",
    re.IGNORECASE,
)

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
    concepts = db.relationship(
        "Concept",
        back_populates="project",
        order_by="Concept.created_at.desc()",
        cascade="all, delete-orphan",
    )
    chapters = db.relationship(
        "ChapterDraft",
        back_populates="project",
        order_by="(ChapterDraft.act_number, ChapterDraft.chapter_number)",
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
    physical_description = db.Column(db.Text, nullable=True)
    character_description = db.Column(db.Text, nullable=True)
    background = db.Column(db.Text, nullable=True)
    personality_frictions = db.Column(db.Text, nullable=True)
    secret = db.Column(db.Text, nullable=True)
    is_supporting = db.Column(db.Boolean, nullable=False, default=False)
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


class Concept(db.Model):
    """Core concept definition extracted from a project outline."""

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    issue = db.Column(db.Text, nullable=True)
    definition = db.Column(db.Text, nullable=False)
    examples = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    project = db.relationship("Project", back_populates="concepts")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Concept {self.id} project={self.project_id} {self.name!r}>"


class ChapterDraft(db.Model):
    """Full chapter draft generated from an outline."""

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    act_number = db.Column(db.Integer, nullable=False)
    chapter_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(255), nullable=True)
    outline_summary = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    project = db.relationship("Project", back_populates="chapters")

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "act_number",
            "chapter_number",
            name="uq_project_act_chapter",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<ChapterDraft project={self.project_id} act={self.act_number} "
            f"chapter={self.chapter_number}>"
        )


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
    if "physical_description" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN physical_description TEXT")
    if "character_description" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN character_description TEXT")
    if "background" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN background TEXT")
    if "personality_frictions" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN personality_frictions TEXT")
    if "secret" not in existing_columns:
        alterations.append("ALTER TABLE character ADD COLUMN secret TEXT")
    if "is_supporting" not in existing_columns:
        alterations.append(
            "ALTER TABLE character ADD COLUMN is_supporting BOOLEAN NOT NULL DEFAULT 0"
        )

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
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        session_key = _session_key(project_id)
        history = session.setdefault(session_key, [])
        act_session_key = _act_session_key(project_id)
        act_history = session.setdefault(act_session_key, [])
        chapter_session_key = _chapter_session_key(project_id)
        chapter_history = session.setdefault(chapter_session_key, [])
        concept_session_key = _concept_session_key(project_id)
        concept_history = session.setdefault(concept_session_key, [])
        supporting_session_key = _supporting_session_key(project_id)
        supporting_history = session.setdefault(supporting_session_key, [])
        draft_session_key = _draft_session_key(project_id)
        draft_history = session.setdefault(draft_session_key, [])
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
        concept_error = None
        concept_success = None
        supporting_error = None
        supporting_success = None
        draft_error = None
        draft_success = None
        draft_context_default = _DEFAULT_DRAFT_CONTEXT_COUNT
        draft_context_value = draft_context_default
        draft_selected_act: Optional[int] = None
        draft_selected_chapter: Optional[int] = None

        if request.method == "POST":
            chat_type = request.form.get("chat_type", "outline")

            if "reset" in request.form:
                if chat_type == "acts":
                    session.pop(act_session_key, None)
                elif chat_type == "chapters":
                    session.pop(chapter_session_key, None)
                elif chat_type == "concepts":
                    session.pop(concept_session_key, None)
                elif chat_type == "supporting":
                    session.pop(supporting_session_key, None)
                elif chat_type == "drafts":
                    session.pop(draft_session_key, None)
                else:
                    session.pop(session_key, None)
                return redirect(url_for("project_detail", project_id=project_id))

            use_api_requested = _is_api_requested(request.form)
            user_message = request.form.get("message", "").strip()
            if chat_type == "acts":
                if user_message:
                    act_history.append({"role": "user", "content": user_message})
                    generator = None
                    try:
                        generator = _resolve_text_generator(use_api_requested)
                        (
                            act1_result,
                            act2_result,
                            act3_result,
                            acts_detected,
                        ) = _generate_three_act_outline(
                            generator,
                            project,
                            user_message,
                        )
                    except OpenAIAPIRateLimitError as exc:
                        act_error = str(exc)
                        act_history.pop()
                    except RuntimeError as exc:
                        act_error = str(exc)
                        act_history.pop()
                    except Exception as exc:  # pragma: no cover - defensive
                        act_error = (
                            "The text generation backend could not generate the act outline: "
                            f"{exc}"
                        )
                        act_history.pop()
                    else:
                        device_type = generator.get_compute_device()
                        device_label = _normalise_device_label(device_type)
                        device_sentence = _device_usage_sentence(device_type)
                        acts = [
                            act1_result.strip(),
                            act2_result.strip(),
                            act3_result.strip(),
                        ]
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
                        if acts_detected >= 3:
                            act_success = (
                                "Act-by-act outline updated from assistant."
                                f"{device_sentence}"
                            )
                        else:
                            act_success = (
                                "Act outline updated from assistant, but only "
                                f"{acts_detected} act section"
                                f"{'s' if acts_detected != 1 else ''} were detected. "
                                "Detected sections were saved; regenerate to fill the remaining acts. "
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
                        generator = None
                        try:
                            generator = _resolve_text_generator(use_api_requested)
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
                        except OpenAIAPIRateLimitError as exc:
                            chapter_error = str(exc)
                            chapter_history.pop()
                        except RuntimeError as exc:
                            chapter_error = str(exc)
                            chapter_history.pop()
                        except Exception as exc:  # pragma: no cover - defensive
                            chapter_error = (
                                "The text generation backend could not generate the chapter outline: "
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
            elif chat_type == "concepts":
                additional_guidance = user_message
                outline_text = (project.outline or "").strip()
                if not outline_text:
                    concept_error = (
                        "Please generate or save a project outline before refining concepts."
                    )
                else:
                    if additional_guidance:
                        concept_history.append(
                            {"role": "user", "content": additional_guidance}
                        )
                    else:
                        concept_history.append(
                            {
                                "role": "user",
                                "content": (
                                    "Analyse the outline and clarify any core concepts that seem vague."
                                ),
                            }
                        )
                    generator = None
                    try:
                        generator = _resolve_text_generator(use_api_requested)
                        analysis_results = _identify_unclear_concepts(
                            generator,
                            outline_text,
                            additional_guidance,
                        )
                        definitions: List[Dict[str, Any]] = []
                        if analysis_results:
                            definitions = _define_core_concepts(
                                generator,
                                outline_text,
                                analysis_results,
                                additional_guidance,
                            )
                    except OpenAIAPIRateLimitError as exc:
                        concept_error = str(exc)
                        concept_history.pop()
                    except RuntimeError as exc:
                        concept_error = str(exc)
                        concept_history.pop()
                    except ValueError as exc:
                        concept_error = str(exc)
                        concept_history.pop()
                    except Exception as exc:  # pragma: no cover - defensive
                        concept_error = (
                            "The text generation backend could not analyse the concepts: "
                            f"{exc}"
                        )
                        concept_history.pop()
                    else:
                        device_type = generator.get_compute_device()
                        device_label = _normalise_device_label(device_type)
                        device_sentence = _device_usage_sentence(device_type)
                        analysis_message = _format_concept_analysis_summary(
                            analysis_results
                        )
                        concept_history.append(
                            {
                                "role": "assistant",
                                "content": analysis_message,
                                "device_type": device_label,
                            }
                        )
                        if analysis_results and definitions:
                            definition_message = (
                                _format_concept_definition_summary(definitions)
                            )
                            concept_history.append(
                                {
                                    "role": "assistant",
                                    "content": definition_message,
                                    "device_type": device_label,
                                }
                            )
                            _apply_concept_definitions(
                                project,
                                analysis_results,
                                definitions,
                            )
                            db.session.commit()
                            concept_success = (
                                "Concept definitions updated from assistant."
                                f"{device_sentence}"
                            )
                        else:
                            concept_success = (
                                "No unclear concepts were identified in the outline."
                                f"{device_sentence}"
                            )
                        session.modified = True
                session.modified = True
            elif chat_type == "drafts":
                act_raw = request.form.get("act_number", "").strip()
                chapter_raw = request.form.get("chapter_number", "").strip()
                context_raw = request.form.get("context_count", "").strip()
                additional_guidance = user_message

                try:
                    act_number = int(act_raw)
                    if act_number <= 0:
                        raise ValueError
                except ValueError:
                    draft_error = "Please choose a valid act before drafting."
                else:
                    draft_selected_act = act_number

                try:
                    chapter_number = int(chapter_raw)
                    if chapter_number <= 0:
                        raise ValueError
                except ValueError:
                    draft_error = (
                        draft_error
                        or "Please choose a valid chapter from the outline to draft."
                    )
                else:
                    draft_selected_chapter = chapter_number

                try:
                    context_count = int(context_raw or draft_context_default)
                except ValueError:
                    draft_error = draft_error or (
                        "Please enter a whole number for the continuity chapters."
                    )
                    context_count = draft_context_default
                else:
                    if context_count < 0:
                        draft_error = draft_error or (
                            "Continuity chapters cannot be negative."
                        )
                        context_count = draft_context_default
                draft_context_value = context_count

                if draft_error is None:
                    draft_result = _execute_chapter_draft_generation(
                        project,
                        draft_history,
                        draft_selected_act,
                        draft_selected_chapter,
                        context_count,
                        additional_guidance,
                        use_api_requested,
                    )
                    if draft_result.get("error"):
                        draft_error = draft_result["error"]
                    else:
                        draft_success = draft_result.get("success")
                session.modified = True
            elif chat_type == "supporting":
                additional_guidance = user_message
                act_outline_segments = [
                    segment.strip()
                    for segment in [
                        project.act1_outline or "",
                        project.act2_outline or "",
                        project.act3_outline or "",
                    ]
                    if segment and segment.strip()
                ]
                if not act_outline_segments:
                    supporting_error = (
                        "Please generate the act-by-act outline before creating supporting characters."
                    )
                else:
                    user_display_message = additional_guidance or (
                        "Review the acts and suggest any supporting characters who need quick reference profiles."
                    )
                    supporting_history.append(
                        {"role": "user", "content": user_display_message}
                    )
                    generator = None
                    try:
                        generator = _resolve_text_generator(use_api_requested)
                        (
                            assistant_reply,
                            parsed_characters,
                        ) = _generate_supporting_characters(
                            generator,
                            project,
                            additional_guidance,
                        )
                    except OpenAIAPIRateLimitError as exc:
                        supporting_error = str(exc)
                        supporting_history.pop()
                    except RuntimeError as exc:
                        supporting_error = str(exc)
                        supporting_history.pop()
                    except ValueError as exc:
                        supporting_error = str(exc)
                        supporting_history.pop()
                    except Exception as exc:  # pragma: no cover - defensive
                        supporting_error = (
                            "The text generation backend could not identify supporting characters: "
                            f"{exc}"
                        )
                        supporting_history.pop()
                    else:
                        device_type = generator.get_compute_device()
                        device_label = _normalise_device_label(device_type)
                        device_sentence = _device_usage_sentence(device_type)
                        clean_reply = assistant_reply.strip() or "(no reply)"
                        supporting_history.append(
                            {
                                "role": "assistant",
                                "content": clean_reply,
                                "device_type": device_label,
                            }
                        )
                        added_count, updated_count = _apply_supporting_character_updates(
                            project,
                            parsed_characters,
                        )
                        db.session.commit()
                        changes: List[str] = []
                        if added_count:
                            plural = "s" if added_count != 1 else ""
                            changes.append(
                                f"created {added_count} new supporting character{plural}"
                            )
                        if updated_count:
                            plural = "s" if updated_count != 1 else ""
                            changes.append(
                                f"updated {updated_count} existing profile{plural}"
                            )
                        if changes:
                            change_sentence = ", ".join(changes)
                            supporting_success = (
                                f"Supporting cast saved: {change_sentence}."
                                f"{device_sentence}"
                            )
                        else:
                            supporting_success = (
                                "No new supporting characters were required; the roster is already up to date."
                                f"{device_sentence}"
                            )
                        session.modified = True
                session.modified = True
            else:
                if user_message:
                    history.append({"role": "user", "content": user_message})
                    generator = None
                    try:
                        generator = _resolve_text_generator(use_api_requested)
                        prompt = _build_outline_prompt(project, history)
                        max_tokens = get_prompt_max_new_tokens("outline_assistant")
                        assistant_reply_raw = generator.generate_response(
                            prompt,
                            max_new_tokens=max_tokens,
                        )
                    except OpenAIAPIRateLimitError as exc:
                        error = str(exc)
                        history.pop()
                    except RuntimeError as exc:
                        error = str(exc)
                        history.pop()
                    except Exception as exc:  # pragma: no cover - defensive
                        error = (
                            "The text generation backend could not generate a reply: "
                            f"{exc}"
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

        main_characters = [
            character for character in project.characters if not character.is_supporting
        ]
        supporting_characters_list = [
            character for character in project.characters if character.is_supporting
        ]
        device_hint = _compute_device_hint()

        act_chapter_lists = _collect_project_chapter_lists(project)
        (
            chapter_draft_groups,
            chapter_draft_lookup,
        ) = _collect_chapter_draft_payload(project)
        saved_chapter_count = sum(
            len(entries) for entries in chapter_draft_groups.values()
        )
        chapter_outline_lookup = _build_chapter_outline_lookup(act_chapter_lists)

        last_unfilled = _find_last_unfilled_chapter(
            act_chapter_lists, chapter_draft_lookup
        )
        last_planned = _find_last_planned_chapter(act_chapter_lists)
        next_after_last_draft = _suggest_next_chapter_after_last_draft(
            act_chapter_lists, chapter_draft_lookup
        )

        if draft_selected_act is None:
            if next_after_last_draft is not None:
                draft_selected_act = next_after_last_draft[0]
            elif last_unfilled is not None:
                draft_selected_act = last_unfilled[0]
            elif last_planned is not None:
                draft_selected_act = last_planned[0]
            else:
                draft_selected_act = 1

        if draft_selected_chapter is None:
            chapters_for_act = act_chapter_lists.get(draft_selected_act, [])
            if chapters_for_act:
                chapter_default: Optional[int] = None
                if (
                    next_after_last_draft is not None
                    and next_after_last_draft[0] == draft_selected_act
                ):
                    chapter_default = next_after_last_draft[1]
                if chapter_default is None:
                    chapter_default = _find_last_unfilled_chapter_in_act(
                        draft_selected_act,
                        act_chapter_lists,
                        chapter_draft_lookup,
                    )
                if chapter_default is None:
                    chapter_default = _find_last_planned_chapter_in_act(
                        draft_selected_act,
                        act_chapter_lists,
                    )
                if chapter_default is None:
                    first_entry = chapters_for_act[0]
                    chapter_default = int(first_entry.get("number") or 1)
                draft_selected_chapter = chapter_default
            else:
                draft_selected_chapter = 1

        export_message_key = _draft_export_message_key(project_id)
        export_error_key = _draft_export_error_key(project_id)
        draft_export_message = session.pop(export_message_key, None)
        draft_export_error = session.pop(export_error_key, None)
        if draft_export_message is not None or draft_export_error is not None:
            session.modified = True

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
            concept_history=concept_history,
            concept_error=concept_error,
            concept_success=concept_success,
            supporting_history=supporting_history,
            supporting_error=supporting_error,
            supporting_success=supporting_success,
            draft_history=draft_history,
            draft_error=draft_error,
            draft_success=draft_success,
            draft_context_value=draft_context_value,
            draft_context_default=draft_context_default,
            draft_selected_act=draft_selected_act,
            draft_selected_chapter=draft_selected_chapter,
            draft_export_message=draft_export_message,
            draft_export_error=draft_export_error,
            saved_chapter_count=saved_chapter_count,
            main_characters=main_characters,
            supporting_characters=supporting_characters_list,
            device_hint=device_hint,
            act_chapter_lists=act_chapter_lists,
            chapter_draft_groups=chapter_draft_groups,
            chapter_draft_lookup=chapter_draft_lookup,
            chapter_outline_lookup=chapter_outline_lookup,
        )

    @app.route(
        "/projects/<int:project_id>/draft_chapter",
        methods=["POST"],
    )
    def project_draft_chapter(project_id: int):
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        payload = request.get_json(silent=True) or {}
        use_api_requested = _is_api_requested(payload)

        act_value = payload.get("act_number")
        chapter_value = payload.get("chapter_number")
        context_value = payload.get("context_count")
        additional_guidance = str(payload.get("message", "") or "").strip()

        try:
            act_number = int(act_value)
            if act_number <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return (
                jsonify({"ok": False, "error": "Please choose a valid act before drafting."}),
                422,
            )

        try:
            chapter_number = int(chapter_value)
            if chapter_number <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Please choose a valid chapter from the outline to draft.",
                    }
                ),
                422,
            )

        if context_value is None:
            context_count = _DEFAULT_DRAFT_CONTEXT_COUNT
        else:
            try:
                context_count = int(context_value)
            except (TypeError, ValueError):
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "Please enter a whole number for the continuity chapters.",
                        }
                    ),
                    422,
                )
            if context_count < 0:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "Continuity chapters cannot be negative.",
                        }
                    ),
                    422,
                )

        draft_session_key = _draft_session_key(project_id)
        draft_history = session.setdefault(draft_session_key, [])

        draft_result = _execute_chapter_draft_generation(
            project,
            draft_history,
            act_number,
            chapter_number,
            context_count,
            additional_guidance,
            use_api_requested,
        )

        session.modified = True

        error_message = draft_result.get("error")
        if error_message:
            return jsonify({"ok": False, "error": error_message}), 400

        saved_draft = draft_result.get("saved_draft")
        outline_entry = draft_result.get("outline_entry") or {}

        response_payload = {
            "ok": True,
            "draft": _serialise_chapter_draft(saved_draft) if saved_draft else None,
            "assistant_reply": draft_result.get("assistant_reply"),
            "device_label": draft_result.get("device_label"),
            "user_message": draft_result.get("user_display_message"),
            "outline_entry": {
                "title": str(outline_entry.get("title", "")).strip(),
                "summary": str(outline_entry.get("summary", "")).strip(),
            },
        }

        return jsonify(response_payload)

    @app.route(
        "/projects/<int:project_id>/update_text",
        methods=["POST"],
    )
    def project_update_text(project_id: int):
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        payload = request.get_json(silent=True) or {}
        field_name = str(payload.get("field", "")).strip()
        allowed_fields = {
            "outline",
            "act1_outline",
            "act2_outline",
            "act3_outline",
        }
        if field_name not in allowed_fields:
            return (
                jsonify({"ok": False, "error": "Unknown or unsupported project field."}),
                400,
            )

        raw_value = payload.get("value", "")
        if raw_value is None:
            new_value = ""
        else:
            new_value = str(raw_value).replace("\r\n", "\n")

        cleaned_value = new_value.strip()
        setattr(project, field_name, cleaned_value or None)
        db.session.commit()

        return jsonify({"ok": True, "value": cleaned_value})

    @app.route(
        "/projects/<int:project_id>/concepts/<int:concept_id>",
        methods=["PATCH"],
    )
    def project_update_concept(project_id: int, concept_id: int):
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        concept = Concept.query.filter_by(id=concept_id, project_id=project_id).first()
        if concept is None:
            return jsonify({"ok": False, "error": "Concept not found."}), 404

        payload = request.get_json(silent=True) or {}

        def _clean(value: Any) -> str:
            if value is None:
                return ""
            return str(value).replace("\r\n", "\n").strip()

        name = _clean(payload.get("name")) or concept.name or ""
        definition = _clean(payload.get("definition")) or concept.definition or ""
        if not name or not definition:
            return (
                jsonify({"ok": False, "error": "Name and definition are required."}),
                422,
            )

        concept.name = name
        concept.definition = definition

        issue = _clean(payload.get("issue"))
        concept.issue = issue or None

        examples_text = _clean(payload.get("examples"))
        concept.examples = examples_text or None

        db.session.commit()

        examples_list = []
        if concept.examples:
            examples_list = [
                line.strip()
                for line in concept.examples.split("\n")
                if line.strip()
            ]

        return jsonify(
            {
                "ok": True,
                "concept": {
                    "id": concept.id,
                    "name": concept.name,
                    "issue": concept.issue or "",
                    "definition": concept.definition,
                    "examples": examples_list,
                },
            }
        )

    @app.route(
        "/projects/<int:project_id>/characters/<int:character_id>/update",
        methods=["PATCH"],
    )
    def project_update_character(project_id: int, character_id: int):
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        character = Character.query.filter_by(
            id=character_id, project_id=project_id
        ).first()
        if character is None:
            return jsonify({"ok": False, "error": "Character not found."}), 404

        payload = request.get_json(silent=True) or {}

        def _clean(value: Any) -> str:
            if value is None:
                return ""
            return str(value).replace("\r\n", "\n").strip()

        if "name" in payload:
            character.name = _clean(payload.get("name")) or None

        if "role_in_story" in payload:
            character.role_in_story = _clean(payload.get("role_in_story")) or None

        if "character_description" in payload:
            character.character_description = (
                _clean(payload.get("character_description")) or None
            )

        if "physical_description" in payload:
            character.physical_description = (
                _clean(payload.get("physical_description")) or None
            )

        if "background" in payload:
            character.background = _clean(payload.get("background")) or None

        db.session.commit()

        return jsonify(
            {
                "ok": True,
                "character": {
                    "id": character.id,
                    "name": character.name or "",
                    "role_in_story": character.role_in_story or "",
                    "character_description": character.character_description or "",
                    "is_supporting": bool(character.is_supporting),
                },
            }
        )

    @app.route(
        "/projects/<int:project_id>/chapters/plan",
        methods=["PATCH"],
    )
    def project_update_chapter_plan(project_id: int):
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        payload = request.get_json(silent=True) or {}

        try:
            act_number = int(payload.get("act_number"))
            chapter_number = int(payload.get("chapter_number"))
        except (TypeError, ValueError):
            return (
                jsonify({"ok": False, "error": "Act and chapter numbers are required."}),
                422,
            )

        if act_number not in {1, 2, 3} or chapter_number <= 0:
            return (
                jsonify({"ok": False, "error": "Invalid act or chapter number."}),
                422,
            )

        title_raw = payload.get("title", "")
        summary_raw = payload.get("summary", "")

        title = str(title_raw or "").replace("\r\n", "\n").strip()
        summary = str(summary_raw or "").replace("\r\n", "\n").strip()

        if not title or not summary:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Both the chapter title and summary are required.",
                    }
                ),
                422,
            )

        list_attr = {
            1: "act1_chapter_list",
            2: "act2_chapter_list",
            3: "act3_chapter_list",
        }.get(act_number)
        text_attr = {
            1: "act1_chapters",
            2: "act2_chapters",
            3: "act3_chapters",
        }.get(act_number)

        serialised_text = getattr(project, list_attr)
        fallback_text = getattr(project, text_attr)
        entries = _load_chapter_list(serialised_text, fallback_text)
        if not entries:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Chapter outline entries are not available for editing.",
                    }
                ),
                404,
            )

        updated = False
        for entry in entries:
            number = entry.get("number")
            try:
                number_int = int(number)
            except (TypeError, ValueError):
                continue
            if number_int == chapter_number:
                entry["title"] = title
                entry["summary"] = summary
                updated = True
                break

        if not updated:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "The requested chapter could not be found.",
                    }
                ),
                404,
            )

        serialised_entries = _serialise_chapter_entries(entries)
        rendered_text = _render_chapter_entries(serialised_entries)

        setattr(project, list_attr, json.dumps(serialised_entries, ensure_ascii=False))
        setattr(project, text_attr, rendered_text)

        db.session.commit()

        return jsonify(
            {
                "ok": True,
                "entry": {
                    "act_number": act_number,
                    "chapter_number": chapter_number,
                    "title": title,
                    "summary": summary,
                },
            }
        )

    @app.route(
        "/projects/<int:project_id>/chapters/export",
        methods=["POST"],
    )
    def chapter_export_pdf(project_id: int) -> str:
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        message_key = _draft_export_message_key(project_id)
        error_key = _draft_export_error_key(project_id)

        drafts = (
            ChapterDraft.query.filter_by(project_id=project_id)
            .order_by(
                ChapterDraft.act_number.asc(),
                ChapterDraft.chapter_number.asc(),
            )
            .all()
        )

        if not drafts:
            session[error_key] = "No drafted chapters are available to export yet."
            session.modified = True
            return redirect(url_for("project_detail", project_id=project_id))

        output_path = Path(__file__).resolve().parent / "temp.pdf"
        try:
            pdf_path = export_chapter_drafts_to_pdf(
                project,
                drafts,
                output_path=output_path,
            )
        except PDFExportError as exc:
            session[error_key] = str(exc)
            session.modified = True
            return redirect(url_for("project_detail", project_id=project_id))

        session[message_key] = (
            f"Exported {len(drafts)} chapter{'s' if len(drafts) != 1 else ''} to {pdf_path.name}."
        )
        session.modified = True
        return redirect(url_for("project_detail", project_id=project_id))

    @app.route(
        "/projects/<int:project_id>/chapters/export/txt",
        methods=["POST"],
    )
    def chapter_export_txt(project_id: int) -> str:
        project = db.session.get(Project, project_id)
        if project is None:
            abort(404)

        message_key = _draft_export_message_key(project_id)
        error_key = _draft_export_error_key(project_id)

        drafts = (
            ChapterDraft.query.filter_by(project_id=project_id)
            .order_by(
                ChapterDraft.act_number.asc(),
                ChapterDraft.chapter_number.asc(),
            )
            .all()
        )

        if not drafts:
            session[error_key] = "No drafted chapters are available to export yet."
            session.modified = True
            return redirect(url_for("project_detail", project_id=project_id))

        output_path = Path(__file__).resolve().parent / "temp.txt"
        try:
            txt_path = export_chapter_drafts_to_txt(
                project,
                drafts,
                output_path=output_path,
            )
        except TextExportError as exc:
            session[error_key] = str(exc)
            session.modified = True
            return redirect(url_for("project_detail", project_id=project_id))

        session[message_key] = (
            f"Exported {len(drafts)} chapter{'s' if len(drafts) != 1 else ''} to {txt_path.name}."
        )
        session.modified = True
        return redirect(url_for("project_detail", project_id=project_id))

    @app.route(
        "/projects/<int:project_id>/characters",
        methods=["POST"],
    )
    def character_create(project_id: int) -> str:
        project = db.session.get(Project, project_id)
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
        project = db.session.get(Project, project_id)
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
        project = db.session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "Project not found."}), 404

        character = Character.query.filter_by(
            id=character_id, project_id=project_id
        ).first()
        if character is None:
            return jsonify({"error": "Character not found."}), 404

        payload = request.get_json(silent=True) or {}
        inputs_payload = payload.get("inputs")
        use_api_requested = _is_api_requested({"use_api": payload.get("use_api")})
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
            generator = _resolve_text_generator(use_api_requested)
        except RuntimeError as exc:
            LOGGER.error("Failed to initialise text generator: %s", exc)
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Unexpected error initialising text generator")
            return (
                jsonify(
                    {
                        "error": "The text generation backend could not be initialised.",
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
        except OpenAIAPIRateLimitError as exc:
            LOGGER.info(
                "Character profile generation rate limited for project %s character %s",
                project_id,
                character_id,
            )
            return jsonify({"error": str(exc)}), 429
        except ValueError as exc:
            LOGGER.warning(
                "Character profile generation validation failed for project %s character %s: %s",
                project_id,
                character_id,
                exc,
            )
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception(
                "Character profile generation failed for project %s character %s",
                project_id,
                character_id,
            )
            return (
                jsonify(
                    {
                        "error": "The text generation backend could not generate a reply.",
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
                "profile": profile_data,
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


def _concept_session_key(project_id: int) -> str:
    """Return the session key used for concept development conversations."""

    return f"concept_chat_history_{project_id}"


def _supporting_session_key(project_id: int) -> str:
    """Return the session key used for supporting character conversations."""

    return f"supporting_chat_history_{project_id}"


def _draft_session_key(project_id: int) -> str:
    """Return the session key used for chapter drafting conversations."""

    return f"draft_chat_history_{project_id}"


def _draft_export_message_key(project_id: int) -> str:
    """Return the session key storing PDF export success messages."""

    return f"draft_export_message_{project_id}"


def _draft_export_error_key(project_id: int) -> str:
    """Return the session key storing PDF export error messages."""

    return f"draft_export_error_{project_id}"


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


def _parse_structured_chapter_entries(text: str) -> List[Dict[str, Any]]:
    """Parse chapter entries that follow the new 'Chapter:' section format."""

    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    found_header = False

    for line in text.splitlines():
        match = _CHAPTER_HEADER_PATTERN.match(line)
        if match:
            found_header = True
            if current is not None:
                summary = _normalise_whitespace(
                    " ".join(current.get("summary_lines", []))
                )
                entries.append(
                    {
                        "number": current["number"],
                        "title": current["title"],
                        "summary": summary,
                    }
                )

            number = int(match.group(1))
            title = _normalise_whitespace(match.group(2))
            current = {
                "number": number,
                "title": title,
                "summary_lines": [],
            }
            continue

        if current is None:
            continue

        stripped = line.strip()
        if not stripped:
            continue

        current.setdefault("summary_lines", []).append(stripped)

    if current is not None:
        summary = _normalise_whitespace(" ".join(current.get("summary_lines", [])))
        entries.append(
            {
                "number": current["number"],
                "title": current["title"],
                "summary": summary,
            }
        )

    if not found_header:
        return []

    return entries


def _parse_legacy_chapter_entries(text: str) -> List[Dict[str, Any]]:
    """Parse chapter entries that follow the legacy single-line format."""

    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None

    for line in text.splitlines():
        match = _LEGACY_CHAPTER_HEADING_PATTERN.match(line)
        if match:
            if current is not None:
                raw_value = _normalise_whitespace(current.get("raw", ""))
                title, summary = _extract_title_summary(raw_value)
                entries.append(
                    {
                        "number": current["number"],
                        "title": title,
                        "summary": summary,
                    }
                )

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
        raw_value = _normalise_whitespace(current.get("raw", ""))
        title, summary = _extract_title_summary(raw_value)
        entries.append(
            {
                "number": current["number"],
                "title": title,
                "summary": summary,
            }
        )

    return entries


def _parse_chapter_entries(text: str) -> List[Dict[str, Any]]:
    """Return structured chapter entries parsed from ``text``."""

    if not text:
        return []

    structured = _parse_structured_chapter_entries(text)
    if structured:
        return structured

    return _parse_legacy_chapter_entries(text)


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

    sections: List[str] = []
    for entry in entries:
        number = entry.get("number")
        try:
            number_int = int(number)
        except (TypeError, ValueError):
            continue

        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip()
        header_title = title if title else "Untitled Chapter"
        header = f"Chapter: Chapter {number_int} — {header_title}".strip()

        section_lines = [header]
        if summary:
            section_lines.append(summary)

        sections.append("\n".join(section_lines).strip())

    return "\n\n".join(sections).strip()


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


def _find_last_drafted_chapter(
    act_chapter_lists: Dict[int, List[Dict[str, Any]]],
    chapter_draft_lookup: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[int, int]]:
    """Return the act/chapter tuple for the most recently drafted chapter."""

    last_drafted: Optional[Tuple[int, int]] = None
    for act_number in sorted(act_chapter_lists):
        entries = act_chapter_lists.get(act_number, [])
        for entry in entries:
            number_raw = entry.get("number")
            try:
                number = int(number_raw)
            except (TypeError, ValueError):
                continue
            key = f"{act_number}-{number}"
            draft_entry = chapter_draft_lookup.get(key)
            content = (draft_entry or {}).get("content", "")
            if str(content).strip():
                last_drafted = (int(act_number), number)
    return last_drafted


def _suggest_next_chapter_after_last_draft(
    act_chapter_lists: Dict[int, List[Dict[str, Any]]],
    chapter_draft_lookup: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[int, int]]:
    """Return the next planned chapter following the most recent draft."""

    last_drafted = _find_last_drafted_chapter(
        act_chapter_lists, chapter_draft_lookup
    )
    if last_drafted is None:
        return None

    act_number, chapter_number = last_drafted
    entries = act_chapter_lists.get(act_number, [])
    for index, entry in enumerate(entries):
        number_raw = entry.get("number")
        try:
            number = int(number_raw)
        except (TypeError, ValueError):
            continue
        if number != chapter_number:
            continue

        for next_entry in entries[index + 1 :]:
            next_number_raw = next_entry.get("number")
            try:
                next_number = int(next_number_raw)
            except (TypeError, ValueError):
                continue
            key = f"{act_number}-{next_number}"
            draft_entry = chapter_draft_lookup.get(key)
            content = (draft_entry or {}).get("content", "")
            if not str(content).strip():
                return act_number, next_number
        break

    return None


def _find_last_unfilled_chapter(
    act_chapter_lists: Dict[int, List[Dict[str, Any]]],
    chapter_draft_lookup: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[int, int]]:
    """Return the act/chapter tuple for the most recent chapter without saved prose."""

    last_missing: Optional[Tuple[int, int]] = None
    for act_number, entries in act_chapter_lists.items():
        for entry in entries:
            number_raw = entry.get("number")
            try:
                number = int(number_raw)
            except (TypeError, ValueError):
                continue
            key = f"{act_number}-{number}"
            draft_entry = chapter_draft_lookup.get(key)
            content = (draft_entry or {}).get("content", "")
            if not str(content).strip():
                last_missing = (int(act_number), number)
    return last_missing


def _find_last_planned_chapter(
    act_chapter_lists: Dict[int, List[Dict[str, Any]]]
) -> Optional[Tuple[int, int]]:
    """Return the act/chapter tuple for the most recent planned chapter."""

    last_planned: Optional[Tuple[int, int]] = None
    for act_number, entries in act_chapter_lists.items():
        for entry in entries:
            number_raw = entry.get("number")
            try:
                number = int(number_raw)
            except (TypeError, ValueError):
                continue
            last_planned = (int(act_number), number)
    return last_planned


def _find_last_unfilled_chapter_in_act(
    act_number: int,
    act_chapter_lists: Dict[int, List[Dict[str, Any]]],
    chapter_draft_lookup: Dict[str, Dict[str, Any]],
) -> Optional[int]:
    """Return the chapter number of the last unfilled chapter in ``act_number``."""

    entries = act_chapter_lists.get(act_number, [])
    last_missing: Optional[int] = None
    for entry in entries:
        number_raw = entry.get("number")
        try:
            number = int(number_raw)
        except (TypeError, ValueError):
            continue
        key = f"{act_number}-{number}"
        draft_entry = chapter_draft_lookup.get(key)
        content = (draft_entry or {}).get("content", "")
        if not str(content).strip():
            last_missing = number
    return last_missing


def _find_last_planned_chapter_in_act(
    act_number: int, act_chapter_lists: Dict[int, List[Dict[str, Any]]]
) -> Optional[int]:
    """Return the chapter number of the final planned chapter in ``act_number``."""

    entries = act_chapter_lists.get(act_number, [])
    last_planned: Optional[int] = None
    for entry in entries:
        number_raw = entry.get("number")
        try:
            number = int(number_raw)
        except (TypeError, ValueError):
            continue
        last_planned = number
    return last_planned


def _find_chapter_outline_entry(
    project: Project, act_number: int, chapter_number: int
) -> Optional[Dict[str, Any]]:
    """Return the outline entry for ``act_number``/``chapter_number``."""

    chapter_lists = _collect_project_chapter_lists(project)
    entries = chapter_lists.get(act_number, [])
    for entry in entries:
        number = entry.get("number")
        try:
            number_int = int(number)
        except (TypeError, ValueError):
            continue
        if number_int == chapter_number:
            return entry
    return None


def _fetch_previous_chapter_drafts(
    project_id: int,
    act_number: int,
    chapter_number: int,
    limit: int,
) -> List[ChapterDraft]:
    """Return up to ``limit`` drafts that precede the target chapter."""

    if limit <= 0:
        return []

    query = (
        ChapterDraft.query.filter(ChapterDraft.project_id == project_id)
        .filter(
            or_(
                ChapterDraft.act_number < act_number,
                and_(
                    ChapterDraft.act_number == act_number,
                    ChapterDraft.chapter_number < chapter_number,
                ),
            )
        )
        .order_by(
            ChapterDraft.act_number.desc(),
            ChapterDraft.chapter_number.desc(),
        )
    )

    drafts = query.limit(limit).all()
    return list(reversed(drafts))


def _collect_chapter_draft_payload(
    project: Project,
) -> Tuple[Dict[int, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    """Return grouped and lookup views of saved chapter drafts."""

    grouped: Dict[int, List[Dict[str, Any]]] = {1: [], 2: [], 3: []}
    lookup: Dict[str, Dict[str, Any]] = {}

    drafts = sorted(
        project.chapters,
        key=lambda draft: (draft.act_number or 0, draft.chapter_number or 0),
    )

    for draft in drafts:
        act_number = int(draft.act_number or 0)
        chapter_num = int(draft.chapter_number or 0)
        entry = {
            "act_number": act_number,
            "chapter_number": chapter_num,
            "title": (draft.title or "").strip(),
            "outline_summary": (draft.outline_summary or "").strip(),
            "content": (draft.content or "").strip(),
            "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
            "updated_display": (
                draft.updated_at.strftime("%b %d, %Y %H:%M")
                if draft.updated_at
                else ""
            ),
        }
        grouped.setdefault(act_number, []).append(entry)
        lookup[f"{act_number}-{chapter_num}"] = entry

    return grouped, lookup


def _serialise_chapter_draft(draft: ChapterDraft) -> Dict[str, Any]:
    """Return a JSON-friendly representation of a ``ChapterDraft``."""

    return {
        "act_number": draft.act_number,
        "chapter_number": draft.chapter_number,
        "title": (draft.title or "").strip(),
        "outline_summary": (draft.outline_summary or "").strip(),
        "content": (draft.content or "").strip(),
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def _execute_chapter_draft_generation(
    project: Project,
    draft_history: List[Dict[str, Any]],
    act_number: int,
    chapter_number: int,
    context_count: int,
    additional_guidance: str,
    use_api_requested: bool,
) -> Dict[str, Any]:
    """Generate and persist a chapter draft, returning status metadata."""

    result: Dict[str, Any] = {
        "error": None,
        "success": None,
        "assistant_reply": None,
        "device_label": None,
        "device_sentence": None,
        "saved_draft": None,
        "outline_entry": None,
        "user_display_message": None,
    }

    outline_entry = _find_chapter_outline_entry(
        project, act_number, chapter_number
    )
    if outline_entry is None:
        result[
            "error"
        ] = (
            "No saved chapter outline was found for the selected act and chapter. "
            "Generate the chapter-by-chapter plan first."
        )
        return result

    display_lines = [
        f"Draft Act {act_number}, Chapter {chapter_number}",
        (
            "Outline: "
            f"{outline_entry.get('title', '') or 'Untitled'} — "
            f"{outline_entry.get('summary', '') or '(no summary)'}"
        ).strip(),
    ]
    if additional_guidance:
        display_lines.extend(["", "Author notes:", additional_guidance])
    if context_count > 0:
        display_lines.extend(
            [
                "",
                (
                    "Include continuity from the most recent "
                    f"{context_count} chapter(s)."
                ),
            ]
        )

    user_display_message = "\n".join(
        line for line in display_lines if line is not None
    ).strip()
    result["user_display_message"] = user_display_message

    draft_history.append({"role": "user", "content": user_display_message})

    generator: TextGenerator | OpenAIUnifiedGenerator | None = None
    try:
        generator = _resolve_text_generator(use_api_requested)
        previous_chapters = _fetch_previous_chapter_drafts(
            project.id,
            act_number,
            chapter_number,
            context_count,
        )
        chapter_plan = _collect_project_chapter_lists(project).get(act_number, [])
        assistant_reply = _generate_chapter_draft(
            generator,
            project,
            act_number,
            chapter_number,
            outline_entry,
            chapter_plan,
            previous_chapters,
            additional_guidance,
            context_count,
        )
    except OpenAIAPIRateLimitError as exc:
        result["error"] = str(exc)
        draft_history.pop()
        return result
    except RuntimeError as exc:
        result["error"] = str(exc)
        draft_history.pop()
        return result
    except ValueError as exc:
        result["error"] = str(exc)
        draft_history.pop()
        return result
    except Exception as exc:  # pragma: no cover - defensive
        result[
            "error"
        ] = (
            "The text generation backend could not create the chapter draft: "
            f"{exc}"
        )
        draft_history.pop()
        return result

    device_type = generator.get_compute_device() if generator else None
    device_label = _normalise_device_label(device_type)
    device_sentence = _device_usage_sentence(device_type)
    clean_reply = assistant_reply.strip() if assistant_reply else "(no reply)"

    draft_history.append(
        {
            "role": "assistant",
            "content": clean_reply,
            "device_type": device_label,
        }
    )

    saved_draft = _save_chapter_draft(
        project,
        act_number,
        chapter_number,
        outline_entry,
        clean_reply,
    )
    db.session.commit()

    result.update(
        {
            "success": "Chapter draft saved to the project." f"{device_sentence}",
            "assistant_reply": clean_reply,
            "device_label": device_label,
            "device_sentence": device_sentence,
            "saved_draft": saved_draft,
            "outline_entry": outline_entry,
        }
    )

    return result


def _build_chapter_outline_lookup(
    act_chapter_lists: Dict[int, List[Dict[str, Any]]]
) -> Dict[str, Dict[str, str]]:
    """Return a lookup table for chapter outline entries."""

    lookup: Dict[str, Dict[str, str]] = {}
    for act_number, entries in act_chapter_lists.items():
        for entry in entries:
            number = entry.get("number")
            try:
                number_int = int(number)
            except (TypeError, ValueError):
                continue
            key = f"{act_number}-{number_int}"
            lookup[key] = {
                "title": str(entry.get("title", "")).strip(),
                "summary": str(entry.get("summary", "")).strip(),
            }
    return lookup


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
    max_tokens = get_prompt_max_new_tokens("chapter_outline")

    while attempt < max_attempts:
        attempt += 1
        attempt_start = time.perf_counter()
        response = generator.generate_response(
            prompt,
            max_new_tokens=max_tokens,
        ) or ""
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
    prompt_config = SYSTEM_PROMPTS.get("outline_assistant")
    if isinstance(prompt_config, dict):
        system_prompt = prompt_config.get("prompt")
    else:
        system_prompt = prompt_config
    if system_prompt:
        prompt_lines.append(f"System: {system_prompt}")

    character_context = _build_character_roster(project)
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
) -> Tuple[str, str, str, int]:
    """Generate a three-act outline informed by project context."""

    outline_text = (project.outline or "No outline has been provided yet.").strip()
    character_context = _build_character_roster(project)
    notes_text = final_notes.strip() or "No final notes provided."

    prompt = _build_full_act_prompt(
        outline_text,
        character_context,
        notes_text,
    )
    max_tokens = get_prompt_max_new_tokens("act_outline")
    response = generator.generate_response(
        prompt,
        max_new_tokens=max_tokens,
    ) or ""
    response_clean = response.strip()

    act_sections = _split_act_sections(response_clean)
    acts_detected = len(act_sections)

    if acts_detected >= 3:
        results = act_sections[:3]
    elif response_clean:
        results = [response_clean, "", ""]
    else:
        results = ["", "", ""]

    while len(results) < 3:
        results.append("")

    return results[0], results[1], results[2], acts_detected


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
    character_context = _build_character_roster(project)
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


def _identify_unclear_concepts(
    generator: TextGenerator,
    outline_text: str,
    additional_guidance: str,
) -> List[Dict[str, str]]:
    """Return concepts mentioned in the outline that need clarification."""

    prompt = _build_concept_analysis_prompt(outline_text, additional_guidance)
    max_tokens = get_prompt_max_new_tokens("concept_development")
    response = generator.generate_response(
        prompt,
        max_new_tokens=max_tokens,
    ) or ""
    return _parse_concept_analysis(response)


def _define_core_concepts(
    generator: TextGenerator,
    outline_text: str,
    concepts: List[Dict[str, str]],
    additional_guidance: str,
) -> List[Dict[str, Any]]:
    """Return refined definitions for the provided ``concepts``."""

    if not concepts:
        return []

    prompt = _build_concept_definition_prompt(
        outline_text,
        concepts,
        additional_guidance,
    )
    max_tokens = get_prompt_max_new_tokens("concept_development")
    response = generator.generate_response(
        prompt,
        max_new_tokens=max_tokens,
    ) or ""
    return _parse_concept_definitions(response)


def _collect_character_context(characters: Iterable["Character"]) -> str:
    """Build a readable summary of all available character descriptions."""

    entries: List[str] = []
    for character in characters:
        sections: List[str] = []
        if character.name:
            sections.append(f"Name: {character.name}")
        if character.role_in_story:
            sections.append(f"Role in story: {character.role_in_story}")
        if character.physical_description:
            sections.append(f"Physical description: {character.physical_description}")
        if character.character_description:
            sections.append(f"Character description: {character.character_description}")
        if character.background:
            sections.append(f"Background: {character.background}")
        if character.personality_frictions:
            sections.append(
                f"Potential frictions & hidden motivations: {character.personality_frictions}"
            )
        if character.secret:
            sections.append(f"Secret: {character.secret}")
        if sections:
            entries.append("\n".join(sections))

    if not entries:
        return "No character descriptions available."

    return "\n\n".join(entries)


def _build_character_roster(project: "Project") -> str:
    """Return main and supporting character context for prompt construction."""

    main_characters = [
        character for character in project.characters if not character.is_supporting
    ]
    supporting_characters = [
        character for character in project.characters if character.is_supporting
    ]

    sections: List[str] = []

    main_roster = _collect_character_context(main_characters)
    if main_roster.strip() and not main_roster.startswith("No character"):
        sections.append("Main characters:\n" + main_roster)

    supporting_roster = _collect_character_context(supporting_characters)
    if supporting_roster.strip() and not supporting_roster.startswith("No character"):
        sections.append("Supporting characters:\n" + supporting_roster)

    if sections:
        return "\n\n".join(sections)

    return "No character descriptions available."


def _collect_act_outline_text(project: Project) -> str:
    """Return the combined act outline text for the project."""

    sections: List[str] = []
    for label, text in [
        ("Act I", project.act1_outline),
        ("Act II", project.act2_outline),
        ("Act III", project.act3_outline),
    ]:
        cleaned = (text or "").strip()
        if cleaned:
            sections.append(f"{label}:\n{cleaned}")

    return "\n\n".join(sections).strip()


def _build_supporting_characters_prompt(
    project: Project,
    outline_text: str,
    additional_guidance: str,
) -> str:
    """Construct the prompt sent to the supporting character assistant."""

    config = SYSTEM_PROMPTS.get("supporting_characters", {})
    lines: List[str] = []

    base = config.get("base")
    if base:
        lines.append(f"System: {base}")

    task = config.get("task")
    if task:
        lines.append(f"System: {task}")

    format_instructions = config.get("format")
    if format_instructions:
        lines.append(f"System: {format_instructions}")

    if outline_text:
        lines.append("User: Here is the current three-act outline.\n" + outline_text)

    main_roster = _collect_character_context(
        character for character in project.characters if not character.is_supporting
    )
    if main_roster and not main_roster.startswith("No character"):
        lines.append("User: Fully developed characters.\n" + main_roster)

    supporting_roster = _collect_character_context(
        character for character in project.characters if character.is_supporting
    )
    if supporting_roster and not supporting_roster.startswith("No character"):
        lines.append(
            "User: Supporting characters already documented.\n" + supporting_roster
        )

    existing_names = sorted(
        {
            character.name.strip()
            for character in project.characters
            if character.name
        }
    )
    if existing_names:
        lines.append(
            "User: Avoid duplicating these character names.\n"
            + ", ".join(existing_names)
        )

    if additional_guidance:
        lines.append("User: Additional guidance.\n" + additional_guidance.strip())

    lines.append("Assistant:")
    return "\n\n".join(lines)


def _generate_supporting_characters(
    generator: TextGenerator | OpenAIUnifiedGenerator,
    project: Project,
    additional_guidance: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """Generate supporting character summaries based on the act outline."""

    outline_text = _collect_act_outline_text(project)
    if not outline_text:
        raise ValueError("No act outline is available to analyse.")

    prompt = _build_supporting_characters_prompt(
        project,
        outline_text,
        additional_guidance,
    )
    max_tokens = get_prompt_max_new_tokens("supporting_characters")
    response = generator.generate_response(
        prompt,
        max_new_tokens=max_tokens,
    ) or ""
    parsed = _parse_supporting_characters(response)
    return response, parsed


def _parse_supporting_characters(text: str) -> List[Dict[str, str]]:
    """Parse assistant output into supporting character entries."""

    entries: List[Dict[str, str]] = []
    current_name: str | None = None
    description_lines: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = _SUPPORTING_HEADER_PATTERN.match(line)
        if match:
            if current_name:
                description = _normalise_whitespace(" ".join(description_lines))
                if description:
                    entries.append(
                        {"name": current_name, "description": description}
                    )
            current_name = match.group(1).strip()
            description_lines = []
        else:
            if current_name is not None:
                description_lines.append(line.strip())

    if current_name:
        description = _normalise_whitespace(" ".join(description_lines))
        if description:
            entries.append({"name": current_name, "description": description})

    return entries


def _apply_supporting_character_updates(
    project: Project,
    characters_data: Sequence[Dict[str, str]],
) -> Tuple[int, int]:
    """Persist supporting character summaries to the database."""

    if not characters_data:
        return 0, 0

    existing: Dict[str, Character] = {}
    for character in project.characters:
        if not character.name:
            continue
        key = character.name.strip().lower()
        if key not in existing or character.is_supporting:
            existing[key] = character

    added = 0
    updated = 0
    for entry in characters_data:
        name = _normalise_whitespace(entry.get("name", ""))
        description = entry.get("description", "").strip()
        if not name or not description:
            continue

        key = name.lower()
        character = existing.get(key)
        if character is not None:
            if not character.is_supporting:
                # Skip fully developed primary characters.
                continue
            changes = False
            if not character.role_in_story:
                character.role_in_story = "Supporting character"
                changes = True
            if (character.character_description or "").strip() != description:
                character.character_description = description
                changes = True
            if changes:
                updated += 1
        else:
            character = Character(
                project=project,
                name=name,
                role_in_story="Supporting character",
                character_description=description,
                is_supporting=True,
            )
            db.session.add(character)
            existing[key] = character
            added += 1

    return added, updated


def _split_act_sections(response_text: str) -> List[str]:
    """Return individual act sections from a formatted assistant response."""

    if not response_text:
        return []

    matches = [
        match.group(1).strip()
        for match in _ACT_SECTION_PATTERN.finditer(response_text)
        if match.group(1).strip()
    ]
    return matches


def _build_full_act_prompt(
    outline_text: str,
    character_context: str,
    final_notes: str,
) -> str:
    """Construct a prompt requesting the complete three-act outline."""

    act_labels = {1: "Act I", 2: "Act II", 3: "Act III"}

    config = SYSTEM_PROMPTS.get("act_outline", {})
    base_prompt = config.get(
        "base",
        (
            "You are a collaborative narrative designer tasked with producing a "
            "beat-by-beat act outline that is vivid, coherent, and firmly rooted "
            "in the provided story materials."
        ),
    )
    format_prompt = config.get(
        "format",
        (
            "Respond in plain text. Begin each act with 'Act:' followed by the act "
            "label and provide 4-6 numbered beats before moving on to the next act."
        ),
    )
    act_guidance_map = config.get("acts", {})

    user_sections: List[str] = [
        "Craft a complete three-act outline using the context below.",
        "",
        "Project outline:",
        outline_text or "No outline has been provided yet.",
        "",
        "Character roster:",
        character_context or "No character descriptions available.",
        "",
        "Author final notes:",
        final_notes or "No final notes provided.",
        "",
        "Act-specific guidance:",
    ]

    for act_number in (1, 2, 3):
        label = act_labels.get(act_number, f"Act {act_number}")
        guidance = act_guidance_map.get(
            act_number,
            "Ensure the act fulfils its role in classic three-act structure.",
        )
        user_sections.append(f"{label}: {guidance.strip()}")

    user_sections.extend(
        [
            "",
            "Formatting requirements:",
            format_prompt.strip(),
            "- Begin each act section on a new line with the exact prefix 'Act:'.",
            "- Under each header, list 4-6 numbered beats (e.g., '1. ...').",
            "- Keep the response as plain text with blank lines between acts and no JSON or bullet lists.",
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
            "- Begin each chapter section on a new line with the exact prefix 'Chapter:' followed by 'Chapter <number> — <Title>'.",
            "- Place the 2-3 sentence summary immediately underneath the header as a single paragraph (no bullet points).",
            "- Leave a blank line between chapter sections and do not add commentary before or after the list.",
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


def _save_chapter_draft(
    project: Project,
    act_number: int,
    chapter_number: int,
    outline_entry: Mapping[str, Any],
    content: str,
) -> ChapterDraft:
    """Create or update the saved draft for the specified chapter."""

    draft = ChapterDraft.query.filter_by(
        project_id=project.id,
        act_number=act_number,
        chapter_number=chapter_number,
    ).first()
    if draft is None:
        draft = ChapterDraft(
            project=project,
            act_number=act_number,
            chapter_number=chapter_number,
        )

    title = str(outline_entry.get("title", "")).strip()
    summary = str(outline_entry.get("summary", "")).strip()
    cleaned_content = content.strip()

    draft.title = title or None
    draft.outline_summary = summary or None
    draft.content = cleaned_content or None

    db.session.add(draft)
    return draft


def _generate_chapter_draft(
    generator: TextGenerator | OpenAIUnifiedGenerator,
    project: Project,
    act_number: int,
    chapter_number: int,
    outline_entry: Mapping[str, Any],
    chapter_plan: Sequence[Mapping[str, Any]],
    previous_chapters: Sequence[ChapterDraft],
    additional_guidance: str,
    requested_context: int,
) -> str:
    """Generate a prose draft for the requested chapter."""

    prompt = _build_chapter_draft_prompt(
        project,
        act_number,
        chapter_number,
        outline_entry,
        chapter_plan,
        previous_chapters,
        additional_guidance,
        requested_context,
    )
    max_tokens = get_prompt_max_new_tokens("chapter_drafting", fallback=2048)
    response = generator.generate_response(
        prompt,
        max_new_tokens=max_tokens,
    ) or ""
    return response.strip()


def _build_chapter_draft_prompt(
    project: Project,
    act_number: int,
    chapter_number: int,
    outline_entry: Mapping[str, Any],
    chapter_plan: Sequence[Mapping[str, Any]],
    previous_chapters: Sequence[ChapterDraft],
    additional_guidance: str,
    requested_context: int,
) -> str:
    """Construct a rich prompt for drafting chapter prose."""

    config = SYSTEM_PROMPTS.get("chapter_drafting", {})
    base_prompt = config.get(
        "base",
        (
            "You are a collaborative novelist drafting polished manuscript pages. "
            "Honour the provided outline, tone, and character continuity."
        ),
    )
    continuity_prompt = config.get(
        "continuity",
        (
            "Maintain continuity with the existing chapters and planned outline. Track character motivations, subplots, and scene geography."
        ),
    )
    style_prompt = config.get(
        "style",
        (
            "Write immersive, publication-ready prose with concrete sensory detail, distinct character voices, and clear scene transitions."
        ),
    )
    format_prompt = config.get(
        "format",
        (
            "Respond in plain text paragraphs suitable for a novel manuscript. Avoid bullet points, lists, or markdown headings."
        ),
    )
    length_prompt = config.get(
        "length",
        "Aim for roughly 900-1200 words unless the outline strongly suggests otherwise.",
    )

    story_outline = (project.outline or "No project outline has been saved yet.").strip()
    full_act_outline = _collect_act_outline_text(project)
    act_outline_text = _get_single_act_outline_text(project, act_number)
    character_context = _build_character_roster(project)
    plan_lines: List[str] = []
    for entry in chapter_plan:
        number = entry.get("number")
        title = str(entry.get("title", "")).strip()
        summary = str(entry.get("summary", "")).strip()
        if not number:
            continue
        plan_lines.append(
            f"Chapter {number}: {title or 'Untitled'} — {summary or '(no summary)'}"
        )
    plan_text = "\n".join(plan_lines) if plan_lines else "(No chapter breakdown saved yet.)"

    target_title = str(outline_entry.get("title", "")).strip()
    target_summary = str(outline_entry.get("summary", "")).strip()

    previous_context = _format_previous_chapter_context(previous_chapters)
    general_notes = (project.chapters_final_notes or "").strip()
    author_notes = additional_guidance.strip()

    user_sections: List[str] = [
        "Use the materials below to write the next chapter of the novel in polished prose.",
        "",
        "Project overview:",
        story_outline or "(no project outline provided)",
        "",
        "Three-act outline reference:",
        full_act_outline or "(no act outline available)",
        "",
        f"Act {act_number} outline:",
        act_outline_text or "(no act outline available)",
        "",
        f"Chapter plan for Act {act_number}:",
        plan_text,
        "",
        f"Target chapter: Act {act_number}, Chapter {chapter_number}",
        f"Planned title: {target_title or 'Untitled Chapter'}",
        "Outline summary for this chapter:",
        target_summary or "(no summary provided)",
    ]

    if previous_context:
        user_sections.extend(
            [
                "",
                "Most recent drafted chapters for continuity:",
                previous_context,
            ]
        )
    elif requested_context > 0:
        user_sections.extend(
            [
                "",
                "No previous chapter drafts are available yet for continuity reference.",
            ]
        )

    if general_notes:
        user_sections.extend(
            [
                "",
                "General chapter development notes:",
                general_notes,
            ]
        )

    if author_notes:
        user_sections.extend(
            [
                "",
                "Author notes for this chapter:",
                author_notes,
            ]
        )

    user_sections.extend(
        [
            "",
            "Character roster:",
            character_context or "No character descriptions available.",
        ]
    )

    instruction_lines = ["", "Writing instructions:"]
    for directive in (continuity_prompt, style_prompt, format_prompt, length_prompt):
        directive_text = (directive or "").strip()
        if directive_text:
            instruction_lines.append(directive_text)

    user_sections.extend(instruction_lines)

    prompt_lines = [f"System: {base_prompt.strip()}", "User:"]
    prompt_lines.extend(user_sections)
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)


def _format_previous_chapter_context(
    chapters: Sequence[ChapterDraft],
) -> str:
    """Return a formatted context block describing previous drafts."""

    if not chapters:
        return ""

    lines: List[str] = []
    for draft in chapters:
        header = (
            f"Act {draft.act_number}, Chapter {draft.chapter_number}: "
            f"{draft.title or 'Untitled Chapter'}"
        )
        lines.append(header)
        content = (draft.content or "").strip()
        if content:
            lines.append(content)
        lines.append("")

    return "\n".join(lines).strip()


def _get_single_act_outline_text(project: Project, act_number: int) -> str:
    """Return the stored outline text for the requested act."""

    mapping = {
        1: project.act1_outline,
        2: project.act2_outline,
        3: project.act3_outline,
    }
    text = mapping.get(act_number) or ""
    return text.strip()


def _build_concept_analysis_prompt(
    outline_text: str,
    additional_guidance: str,
) -> str:
    """Construct a prompt that identifies vague concepts in an outline."""

    config = SYSTEM_PROMPTS.get("concept_development", {})
    base_prompt = config.get(
        "analysis_prompt",
        (
            "You are a developmental editor who specialises in spotting vague or underspecified "
            "story concepts. Carefully review the outline and list any notions that the author "
            "mentions but does not clearly define."
        ),
    )
    response_instructions = config.get(
        "analysis_response_instructions",
        (
            "List each unclear concept on its own line as 'Concept Name — short note about what needs clarity.' "
            "If nothing requires revision, reply with 'No unclear concepts found.'"
        ),
    )

    user_sections: List[str] = [
        (
            "Evaluate the outline below. Identify only the concepts, organisations, technologies, "
            "locations, or other terms that are explicitly mentioned but feel ambiguous, contradictory, "
            "or underspecified."
        ),
        "Outline:",
        outline_text.strip() or "(no outline provided)",
    ]
    if additional_guidance.strip():
        user_sections.extend(
            [
                "Author guidance to consider while evaluating the outline:",
                additional_guidance.strip(),
            ]
        )
    user_sections.extend(
        [
            "Response instructions:",
            response_instructions.strip(),
        ]
    )

    prompt_lines = ["System: " + base_prompt.strip(), "User:"]
    prompt_lines.extend(user_sections)
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)



def _build_concept_definition_prompt(
    outline_text: str,
    concepts: List[Dict[str, str]],
    additional_guidance: str,
) -> str:
    """Return a prompt that requests clear definitions for each concept."""

    config = SYSTEM_PROMPTS.get("concept_development", {})
    base_prompt = config.get(
        "definition_prompt",
        (
            "You are a worldbuilding specialist tasked with clarifying story concepts. For each concept, "
            "write a concise but concrete definition that resolves ambiguities and fits the outline. Also "
            "provide two or three illustrative examples when feasible."
        ),
    )
    response_instructions = config.get(
        "definition_response_instructions",
        (
            "For each concept, start a new paragraph with 'Concept Name:' followed by a precise definition. "
            "Add a sentence beginning with 'Examples:' that shows one or two ways the concept could manifest in the story."
        ),
    )

    concept_lines: List[str] = []
    for entry in concepts:
        name = str(entry.get("name", "")).strip() or "Unnamed concept"
        issue = str(entry.get("issue", "")).strip()
        if issue:
            concept_lines.append(f"- {name}: {issue}")
        else:
            concept_lines.append(f"- {name}")
    concept_overview = "\n".join(concept_lines) if concept_lines else "- (No concept issues were provided.)"

    user_sections: List[str] = [
        "Use the outline and the concept notes below to craft plain-text definitions the author can apply immediately.",
        "Outline:",
        outline_text.strip() or "(no outline provided)",
        "Concepts requiring clarification:",
        concept_overview,
    ]
    if additional_guidance.strip():
        user_sections.extend(
            [
                "Additional author guidance to incorporate:",
                additional_guidance.strip(),
            ]
        )
    user_sections.extend(
        [
            "Response instructions:",
            response_instructions.strip(),
        ]
    )

    prompt_lines = ["System: " + base_prompt.strip(), "User:"]
    prompt_lines.extend(user_sections)
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)



def _parse_concept_analysis(raw_response: str) -> List[Dict[str, str]]:
    """Parse the analysis response into a list of concept issues."""

    cleaned = _strip_json_code_fences(raw_response)
    json_block = _extract_json_object(cleaned)
    if json_block:
        try:
            payload = json.loads(json_block)
        except json.JSONDecodeError:
            payload = None
        else:
            if isinstance(payload, dict):
                items = payload.get("concepts", [])
                if isinstance(items, list):
                    results: List[Dict[str, str]] = []
                    for entry in items:
                        if not isinstance(entry, dict):
                            continue
                        name = str(entry.get("name", "")).strip()
                        issue = str(entry.get("issue", "")).strip()
                        if not name:
                            continue
                        results.append({"name": name, "issue": issue})
                    return results

    return _parse_plain_concept_analysis(cleaned)



def _parse_concept_definitions(raw_response: str) -> List[Dict[str, Any]]:
    """Parse the definition response from the assistant."""

    cleaned = _strip_json_code_fences(raw_response)
    json_block = _extract_json_object(cleaned)
    if json_block:
        try:
            payload = json.loads(json_block)
        except json.JSONDecodeError:
            payload = None
        else:
            if isinstance(payload, dict):
                items = payload.get("concepts", [])
                if isinstance(items, list):
                    results: List[Dict[str, Any]] = []
                    for entry in items:
                        if not isinstance(entry, dict):
                            continue
                        name = str(entry.get("name", "")).strip()
                        definition = str(entry.get("definition", "")).strip()
                        examples_raw = entry.get("examples", [])
                        examples: List[str] = []
                        if isinstance(examples_raw, list):
                            for example in examples_raw:
                                if example is None:
                                    continue
                                examples.append(str(example).strip())
                        elif isinstance(examples_raw, (str, int, float)):
                            text_value = str(examples_raw).strip()
                            if text_value:
                                examples.append(text_value)
                        if not name:
                            continue
                        results.append(
                            {
                                "name": name,
                                "definition": definition,
                                "examples": [ex for ex in examples if ex],
                            }
                        )
                    return results

    return _parse_plain_concept_definitions(cleaned)



def _format_concept_analysis_summary(concepts: List[Dict[str, str]]) -> str:
    """Return a human-readable summary of concept analysis."""

    if not concepts:
        return "No ambiguous concepts were detected in the outline."

    lines = ["Potentially unclear concepts:"]
    for entry in concepts:
        name = entry.get("name", "").strip() or "Unnamed concept"
        issue = entry.get("issue", "").strip()
        if issue:
            lines.append(f"- {name}: {issue}")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def _format_concept_definition_summary(concepts: List[Dict[str, Any]]) -> str:
    """Return a summary message containing concept definitions."""

    if not concepts:
        return "No concept definitions were generated."

    lines = ["Concept definitions:"]
    for entry in concepts:
        name = entry.get("name", "").strip() or "Unnamed concept"
        definition = entry.get("definition", "").strip()
        lines.append(f"\n{name}")
        if definition:
            lines.append(f"Definition: {definition}")
        examples = entry.get("examples", [])
        if examples:
            lines.append("Examples:")
            for example in examples:
                if not example:
                    continue
                lines.append(f"- {example}")
    return "\n".join(lines).strip()


def _apply_concept_definitions(
    project: Project,
    issues: List[Dict[str, str]],
    concepts: List[Dict[str, Any]],
) -> None:
    """Persist the refined concept definitions to the database."""

    issue_lookup = {
        entry["name"].strip().lower(): entry.get("issue", "").strip()
        for entry in issues
        if entry.get("name")
    }
    for existing in list(project.concepts):
        db.session.delete(existing)

    for entry in concepts:
        name = entry.get("name", "").strip()
        if not name:
            continue
        definition = entry.get("definition", "").strip()
        if not definition:
            continue
        examples_list = entry.get("examples", [])
        if isinstance(examples_list, list):
            examples_text = "\n".join(ex for ex in examples_list if ex)
        elif isinstance(examples_list, str):
            examples_text = examples_list.strip()
        else:
            examples_text = ""
        issue_text = issue_lookup.get(name.lower(), "")
        concept = Concept(
            project=project,
            name=name,
            issue=issue_text or None,
            definition=definition,
            examples=examples_text or None,
        )
        db.session.add(concept)


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
    max_tokens = get_prompt_max_new_tokens("character_creation")
    response = generator.generate_response(
        prompt,
        max_new_tokens=max_tokens,
    ) or ""
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
    """Construct a prompt that enforces JSON output for the character profile."""

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
            (
                "4. Keep each field within its suggested word count, using vivid but concise prose that avoids "
                "story scenes or plot advancement."
            ),
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
        ),
        (
            "Deliver exactly five sections: physical description, character description, background, "
            "potential frictions & hidden motivations, and secret."
        ),
        "Do not draft story beats, scenes, or plot progression.",
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


def _parse_plain_concept_analysis(text: str) -> List[Dict[str, str]]:
    """Best-effort fallback parser for non-JSON concept analysis replies."""

    results: List[Dict[str, str]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return results

    for raw_line in lines:
        line = re.sub(r"^[\-\*\u2022]+\s*", "", raw_line)
        line = re.sub(r"^\d+(?:[.)]|\s+)\s*", "", line)
        if not line:
            continue

        name = ""
        issue = ""

        separator_match = re.match(
            r"^(?P<name>.+?)\s*(?:[:\-\u2013\u2014]\s+)(?P<issue>.+)$",
            line,
        )
        if separator_match:
            name = separator_match.group("name").strip(' "')
            issue = separator_match.group("issue").strip()
        else:
            keyword_match = re.search(
                r"\b(is|are|needs|need|lacks|lack|requires|require|remains|seems)\b",
                line,
                flags=re.IGNORECASE,
            )
            if keyword_match:
                name = line[: keyword_match.start()].strip(" -\u2013\u2014:.,")
                issue = line[keyword_match.start() :].strip()

        if not name:
            continue
        results.append({"name": name, "issue": issue})

    return results


def _parse_plain_concept_definitions(text: str) -> List[Dict[str, Any]]:
    """Best-effort fallback parser for non-JSON concept definition replies."""

    if not text.strip():
        return []

    normalised_lines: List[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            normalised_lines.append("")
            continue
        stripped = re.sub(r"^[\-\*\u2022]+\s*", "", stripped)
        stripped = re.sub(r"^\d+(?:[.)]|\s+)\s*", "", stripped)
        normalised_lines.append(stripped)

    heading_pattern = re.compile(r"^[A-Z0-9][^:]{0,80}[:\-\u2013\u2014]\s+.+$")

    blocks: List[List[str]] = []
    current_block: List[str] = []
    for line in normalised_lines:
        if not line:
            if current_block:
                blocks.append(current_block)
                current_block = []
            continue
        if current_block and heading_pattern.match(line):
            blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)

    if current_block:
        blocks.append(current_block)

    results: List[Dict[str, Any]] = []

    for block in blocks:
        if not block:
            continue

        first_line = block[0]
        name = ""
        definition_parts: List[str] = []
        examples: List[str] = []

        separator_match = re.match(
            r"^(?P<name>.+?)\s*(?:[:\-\u2013\u2014]\s+)(?P<definition>.+)$",
            first_line,
        )
        if separator_match:
            name = separator_match.group("name").strip(' "')
            initial_definition = separator_match.group("definition").strip()
            if initial_definition:
                definition_parts.append(initial_definition)
            remaining_lines = block[1:]
        else:
            name = first_line.rstrip(":")
            remaining_lines = block[1:]

        collecting_examples = False
        for line in remaining_lines:
            header_match = re.match(r"examples?\s*[:\-]\s*(.*)", line, flags=re.IGNORECASE)
            if header_match:
                collecting_examples = True
                inline = header_match.group(1).strip()
                if inline:
                    examples.extend(_split_inline_examples(inline))
                continue

            if collecting_examples:
                examples.extend(_split_inline_examples(line))
                continue

            definition_parts.append(line)

        definition = " ".join(definition_parts).strip()
        clean_examples = [example for example in (ex.strip() for ex in examples) if example]

        if not name:
            continue
        entry: Dict[str, Any] = {"name": name}
        if definition:
            entry["definition"] = definition
        if clean_examples:
            entry["examples"] = clean_examples
        if len(entry) > 1:
            results.append(entry)

    return results


def _split_inline_examples(text: str) -> List[str]:
    """Split inline example strings into individual entries."""

    if not text:
        return []
    parts = re.split(r"[;\u2022\|]\s*", text)
    if len(parts) == 1:
        parts = re.split(r",\s*(?=[A-Z0-9])", text)
    return [part.strip() for part in parts if part.strip()]


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

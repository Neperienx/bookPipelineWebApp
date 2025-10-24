from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from flask import current_app

from ..extensions import db
from ..models import CharacterProfile, OutlineDraft, Project
from .story_outline import (
    OutlineGenerationError,
    _get_text_generator,
    _load_prompt_entry,
    generate_story_outline,
)


class OutlineAutofillError(RuntimeError):
    """Raised when the outline autofill cannot persist a draft."""


class CharacterAutofillError(RuntimeError):
    """Raised when the character autofill cannot update the roster."""


@dataclass
class OutlineAutofillResult:
    draft: OutlineDraft
    used_fallback: bool
    prompt: str
    word_count: int


@dataclass
class CharacterAutofillResult:
    created: List[CharacterProfile]
    updated: List[CharacterProfile]
    used_fallback: bool
    prompt: str


_CHARACTER_AUTOFILL_PROMPT_KEY = "character_autofill"


def autofill_outline_for_project(project: Project, user_prompt: str) -> OutlineAutofillResult:
    """Generate and persist an outline draft for ``project`` using ``user_prompt``."""

    prompt_text = (user_prompt or "").strip()
    if not prompt_text:
        raise OutlineAutofillError("An outline prompt is required for autofill.")

    try:
        outline_result = generate_story_outline(prompt_text, project_title=project.title)
    except OutlineGenerationError as exc:
        raise OutlineAutofillError(str(exc)) from exc

    existing_count = OutlineDraft.query.filter_by(project_id=project.id).count()
    draft = OutlineDraft(
        project=project,
        title=f"Outline draft {existing_count + 1}",
        content=outline_result.outline,
        prompt=outline_result.prompt,
        word_count=outline_result.word_count,
        used_fallback=outline_result.used_fallback,
    )
    db.session.add(draft)

    normalized_prompt = prompt_text or None
    if project.last_outline_prompt != normalized_prompt:
        project.last_outline_prompt = normalized_prompt

    db.session.flush()
    return OutlineAutofillResult(
        draft=draft,
        used_fallback=outline_result.used_fallback,
        prompt=outline_result.prompt,
        word_count=outline_result.word_count,
    )


def autofill_characters_for_project(project: Project, user_prompt: str) -> CharacterAutofillResult:
    """Generate or update character profiles for ``project`` based on ``user_prompt``."""

    result = _generate_character_summaries(user_prompt, project_title=project.title)

    created: List[CharacterProfile] = []
    updated: List[CharacterProfile] = []

    for character_data in result["characters"]:
        name = character_data.get("name")
        if not name:
            continue

        existing = CharacterProfile.query.filter_by(project_id=project.id, name=name).first()
        if existing:
            existing.role = character_data.get("role")
            existing.background = character_data.get("background")
            existing.goals = character_data.get("goals")
            existing.conflict = character_data.get("conflict")
            existing.notes = character_data.get("notes")
            updated.append(existing)
        else:
            character = CharacterProfile(
                project=project,
                name=name,
                role=character_data.get("role"),
                background=character_data.get("background"),
                goals=character_data.get("goals"),
                conflict=character_data.get("conflict"),
                notes=character_data.get("notes"),
            )
            db.session.add(character)
            created.append(character)

    db.session.flush()

    return CharacterAutofillResult(
        created=created,
        updated=updated,
        used_fallback=result["used_fallback"],
        prompt=result["prompt"],
    )


def _generate_character_summaries(user_prompt: str, *, project_title: Optional[str] = None) -> Dict[str, object]:
    prompt_text = (user_prompt or "").strip()
    if not prompt_text:
        raise CharacterAutofillError("Provide context so the assistant can draft characters.")

    try:
        config_entry = _load_prompt_entry(_CHARACTER_AUTOFILL_PROMPT_KEY)
    except OutlineGenerationError as exc:
        raise CharacterAutofillError(str(exc)) from exc

    prompt_template = config_entry.get("prompt_template")
    if not prompt_template:
        raise CharacterAutofillError("Character autofill prompt template is missing.")

    project_fragment = (project_title or "the story").strip() or "the story"
    final_prompt = prompt_template.format(project_title=project_fragment, user_prompt=prompt_text)

    generator = _get_text_generator()
    parameters = config_entry.get("parameters") if isinstance(config_entry, dict) else None
    max_new_tokens = None
    if isinstance(parameters, dict):
        max_new_tokens = parameters.get("max_new_tokens")

    response_text: Optional[str] = None
    used_fallback = False

    if generator is not None:
        try:
            response_text = generator.generate_response(final_prompt, max_new_tokens=max_new_tokens)
        except Exception as exc:  # pragma: no cover - defensive logging for integrations
            current_app.logger.warning(
                "LLM character autofill failed; falling back to heuristic profiles. Error: %s",
                exc,
            )

    if not response_text:
        response_text = json.dumps({"characters": _fallback_characters(project_fragment, prompt_text)})
        used_fallback = True

    characters = _parse_character_payload(response_text)
    if not characters:
        raise CharacterAutofillError("The character generator returned an empty response.")

    return {
        "characters": characters,
        "used_fallback": used_fallback,
        "prompt": final_prompt,
    }


def _parse_character_payload(raw_text: str) -> List[Dict[str, Optional[str]]]:
    text = (raw_text or "").strip()
    if not text:
        return []

    fence_match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    if not text.startswith("{") and not text.startswith("["):
        brace_index = text.find("{")
        bracket_index = text.find("[")
        candidates = [index for index in (brace_index, bracket_index) if index != -1]
        if candidates:
            text = text[min(candidates):]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        current_app.logger.warning("Unable to parse character autofill output as JSON: %s", text)
        return []

    if isinstance(parsed, dict):
        items = parsed.get("characters")
        if not isinstance(items, list):
            current_app.logger.warning("Character autofill JSON missing 'characters' list: %s", parsed)
            return []
    elif isinstance(parsed, list):
        items = parsed
    else:
        return []

    characters: List[Dict[str, Optional[str]]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = _clean_field(entry.get("name"))
        if not name:
            continue
        character = {
            "name": name,
            "role": _clean_field(entry.get("role") or entry.get("role_in_story")),
            "background": _clean_field(entry.get("background")),
            "goals": _clean_field(entry.get("goals") or entry.get("core_drive")),
            "conflict": _clean_field(entry.get("conflict") or entry.get("hidden_vulnerability")),
            "notes": _clean_field(entry.get("notes") or entry.get("relationship_web")),
        }
        characters.append(character)
    return characters


def _clean_field(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _fallback_characters(project_title: str, prompt_text: str) -> List[Dict[str, Optional[str]]]:
    concept_excerpt = prompt_text[:140].rstrip()
    if len(prompt_text) > 140:
        concept_excerpt += "…"

    return [
        {
            "name": "Protagonist",
            "role": "Central protagonist",
            "background": f"Anchors the narrative of {project_title}, shaped by {concept_excerpt or 'an emerging concept'}.",
            "goals": "Drives the plot forward by pursuing a deeply personal desire tied to the core premise.",
            "conflict": "Must confront an escalating internal vulnerability mirrored by external pressures.",
            "notes": "Track how their relationships shift as stakes rise; ensure their voice remains distinct.",
        },
        {
            "name": "Opposition",
            "role": "Primary antagonistic force",
            "background": "Embodies the counter-argument to the protagonist's worldview with resources the hero lacks.",
            "goals": "Seeks to maintain control, believing the protagonist's success would upend the existing order.",
            "conflict": "Applies pressure through moral compromises, forcing the protagonist toward decisive action.",
            "notes": "Highlight parallels between the opposition and protagonist to heighten dramatic tension.",
        },
        {
            "name": "Key Ally",
            "role": "Trusted ally",
            "background": "Understands the protagonist's blind spots and has history that keeps them invested in the journey.",
            "goals": "Wants the protagonist to succeed but harbours a secondary agenda that could complicate loyalty.",
            "conflict": "Their support is tested when the cost of the mission threatens something they protect.",
            "notes": "Use them to surface exposition organically and introduce unexpected strategy shifts.",
        },
    ]

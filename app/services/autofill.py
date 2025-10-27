from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from flask import current_app

from ..extensions import db
from ..models import CharacterProfile, OutlineDraft, Project
from .story_outline import (
    OutlineGenerationError,
    _extract_generation_parameters,
    _get_text_generator,
    _load_prompt_entry,
    generate_story_outline,
)


class OutlineAutofillError(RuntimeError):
    """Raised when the outline autofill cannot persist a draft."""


class CharacterAutofillError(RuntimeError):
    """Raised when the character autofill cannot update the roster."""


class CharacterProfileSuggestionError(RuntimeError):
    """Raised when a single-character suggestion cannot be generated."""


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


@dataclass
class CharacterProfileSuggestion:
    profile: Dict[str, Optional[str]]
    used_fallback: bool
    prompt: str


_CHARACTER_AUTOFILL_PROMPT_KEY = "character_autofill"
_SINGLE_CHARACTER_AUTOFILL_PROMPT_KEY = "single_character_autofill"


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


def draft_character_profile(
    user_prompt: str,
    *,
    project_title: Optional[str] = None,
) -> CharacterProfileSuggestion:
    """Generate a single character profile from ``user_prompt``."""

    prompt_text = (user_prompt or "").strip()
    if not prompt_text:
        raise CharacterProfileSuggestionError(
            "Share a short description so the assistant can draft the profile."
        )

    try:
        config_entry = _load_prompt_entry(_SINGLE_CHARACTER_AUTOFILL_PROMPT_KEY)
    except OutlineGenerationError as exc:
        raise CharacterProfileSuggestionError(str(exc)) from exc

    prompt_template = config_entry.get("prompt_template")
    if not prompt_template:
        raise CharacterProfileSuggestionError("Character profile prompt template is missing.")

    project_fragment = (project_title or "the story").strip() or "the story"

    field_templates_raw = config_entry.get("field_templates")
    ordered_fields = ("name", "role", "background", "goals", "conflict", "notes")
    if isinstance(field_templates_raw, dict) and any(
        field_templates_raw.get(field) for field in ordered_fields
    ):
        generator = _get_text_generator()
        generation_kwargs = _extract_generation_parameters(config_entry.get("parameters"))
        fallback_profile = _fallback_single_character(project_fragment, prompt_text)
        profile: Dict[str, Optional[str]] = {}
        prompts: List[str] = []
        used_fallback = False

        for field in ordered_fields:
            template = field_templates_raw.get(field)
            if not isinstance(template, str):
                continue

            field_prompt = template.format(
                project_title=project_fragment,
                user_prompt=prompt_text,
            )
            prompts.append(field_prompt)

            response_text: Optional[str] = None
            if generator is not None:
                try:
                    response_text = generator.generate_response(field_prompt, **generation_kwargs)
                except Exception as exc:  # pragma: no cover - defensive logging for integrations
                    current_app.logger.warning(
                        "LLM character field generation failed for %s; using fallback. Error: %s",
                        field,
                        exc,
                    )

            value = _parse_single_field_response(response_text, field)
            if value is None:
                fallback_value = fallback_profile.get(field)
                if fallback_value:
                    profile[field] = fallback_value
                    used_fallback = True
                else:
                    used_fallback = True
                continue

            profile[field] = value

        if not profile:
            raise CharacterProfileSuggestionError("The character generator returned an empty profile.")

        prompt_audit = "\n\n".join(prompts)
        return CharacterProfileSuggestion(
            profile=profile,
            used_fallback=used_fallback,
            prompt=prompt_audit,
        )

    final_prompt = prompt_template.format(project_title=project_fragment, user_prompt=prompt_text)

    generator = _get_text_generator()
    generation_kwargs = _extract_generation_parameters(config_entry.get("parameters"))

    response_text: Optional[str] = None
    used_fallback = False

    if generator is not None:
        try:
            response_text = generator.generate_response(final_prompt, **generation_kwargs)
        except Exception as exc:  # pragma: no cover - defensive logging for integrations
            current_app.logger.warning(
                "LLM character profile generation failed; using fallback profile. Error: %s",
                exc,
            )

    if not response_text:
        response_text = json.dumps(_fallback_single_character(project_fragment, prompt_text))
        used_fallback = True

    profile = _parse_character_profile_response(response_text)
    if not profile:
        raise CharacterProfileSuggestionError("The character generator returned an empty profile.")

    return CharacterProfileSuggestion(
        profile=profile,
        used_fallback=used_fallback,
        prompt=final_prompt,
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
    generation_kwargs = _extract_generation_parameters(config_entry.get("parameters"))

    response_text: Optional[str] = None
    used_fallback = False

    if generator is not None:
        try:
            response_text = generator.generate_response(final_prompt, **generation_kwargs)
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


def _parse_single_field_response(raw_text: Optional[str], field: str) -> Optional[str]:
    if not raw_text:
        return None

    text = raw_text.strip()
    if not text:
        return None

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
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        cleaned = _clean_field(text.strip('"'))
        if cleaned:
            return cleaned
        current_app.logger.warning(
            "Unable to parse character field output for %s as JSON: %s",
            field,
            text,
        )
        return None

    candidate: Optional[object] = None
    if isinstance(parsed, dict):
        if field in parsed:
            candidate = parsed[field]
        elif "character" in parsed and isinstance(parsed["character"], dict):
            candidate = parsed["character"].get(field)
        elif "value" in parsed and field == "notes":
            candidate = parsed["value"]
    elif isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, dict) and field in entry:
                candidate = entry[field]
                break

    return _clean_field(candidate)


def _parse_character_profile_response(raw_text: str) -> Dict[str, Optional[str]]:
    text = (raw_text or "").strip()
    if not text:
        return {}

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
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        current_app.logger.warning("Unable to parse character profile output as JSON: %s", text)
        return {}

    candidate: Optional[Dict[str, Optional[str]]] = None

    if isinstance(parsed, dict):
        if "character" in parsed and isinstance(parsed["character"], dict):
            parsed = parsed["character"]
        elif "characters" in parsed and isinstance(parsed["characters"], list) and parsed["characters"]:
            first = parsed["characters"][0]
            if isinstance(first, dict):
                parsed = first
        if isinstance(parsed, dict):
            candidate = parsed  # type: ignore[assignment]
    elif isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict):
            candidate = first

    if not isinstance(candidate, dict):
        current_app.logger.warning("Character profile generator returned an unexpected payload: %s", parsed)
        return {}

    profile = {
        "name": _clean_field(candidate.get("name")),
        "role": _clean_field(candidate.get("role") or candidate.get("role_in_story")),
        "background": _clean_field(candidate.get("background")),
        "goals": _clean_field(candidate.get("goals") or candidate.get("core_drive")),
        "conflict": _clean_field(candidate.get("conflict") or candidate.get("primary_conflict")),
        "notes": _clean_field(candidate.get("notes") or candidate.get("relationship_web")),
    }

    return {key: value for key, value in profile.items() if value}


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


def _fallback_single_character(project_title: str, prompt_text: str) -> Dict[str, Optional[str]]:
    concept_excerpt = prompt_text[:160].rstrip()
    if len(prompt_text) > 160:
        concept_excerpt += "…"

    title_fragment = project_title or "the story"

    return {
        "name": "Provisional Lead",
        "role": f"Key figure shaping {title_fragment}",
        "background": _clean_field(
            f"Known for navigating {concept_excerpt or 'uncertain terrain'} with quiet resilience."
        ),
        "goals": "Determined to push the story's central promise into action without wasting words.",
        "conflict": "Faces escalating pressure that exposes their sharpest vulnerability.",
        "notes": "Track how alliances shift around them as the plot accelerates.",
    }

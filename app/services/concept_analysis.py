"""Concept clarification pipeline built on top of the outline stage."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Optional

from flask import current_app

from .story_outline import (
    OutlineGenerationError,
    _extract_generation_parameters,
    _get_text_generator,
    _load_prompt_entry,
)


class ConceptClarificationError(RuntimeError):
    """Raised when concept clarification cannot be completed."""


@dataclass
class ConceptCandidate:
    name: str
    issue: Optional[str]


@dataclass
class ConceptDefinitionPayload:
    name: str
    definition: str
    examples: List[str]
    issue: Optional[str] = None


@dataclass
class ConceptClarificationResult:
    concepts: List[ConceptDefinitionPayload]
    used_fallback: bool


PROMPT_KEY_IDENTIFY = "concept_outline_review"
PROMPT_KEY_DEFINE = "concept_definition_pass"

_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z'\-]+")
_STOPWORDS = {
    "about",
    "after",
    "again",
    "among",
    "being",
    "first",
    "other",
    "there",
    "their",
    "through",
    "under",
    "while",
    "world",
}


def clarify_outline_concepts(
    outline_text: str,
    *,
    project_title: Optional[str] = None,
) -> ConceptClarificationResult:
    """Run the two-step concept clarification workflow."""

    cleaned_outline = (outline_text or "").strip()
    if not cleaned_outline:
        raise ConceptClarificationError("An outline with content is required to analyse concepts.")

    project_fragment = (project_title or "the project").strip() or "the project"

    candidates, identify_fallback = _identify_unclear_concepts(cleaned_outline, project_fragment)
    if not candidates:
        return ConceptClarificationResult(concepts=[], used_fallback=identify_fallback)

    definitions, define_fallback = _define_concepts(
        cleaned_outline,
        candidates,
        project_fragment,
    )

    combined = []
    candidate_map = {candidate.name.casefold(): candidate for candidate in candidates}
    seen: set[str] = set()
    for payload in definitions:
        name_key = payload.name.casefold()
        if not payload.name or not payload.definition or name_key in seen:
            continue
        issue = None
        if name_key in candidate_map:
            issue = candidate_map[name_key].issue
        combined.append(
            ConceptDefinitionPayload(
                name=payload.name,
                definition=payload.definition,
                examples=payload.examples,
                issue=issue,
            )
        )
        seen.add(name_key)

    used_fallback = identify_fallback or define_fallback
    return ConceptClarificationResult(concepts=combined, used_fallback=used_fallback)


def _identify_unclear_concepts(
    outline: str,
    project_fragment: str,
) -> tuple[List[ConceptCandidate], bool]:
    config = _load_prompt_entry_safe(PROMPT_KEY_IDENTIFY)
    prompt_template = config.get("prompt_template")
    if not prompt_template:
        raise ConceptClarificationError("Prompt template for concept review is missing.")

    final_prompt = _apply_template(
        prompt_template,
        project_title=project_fragment.strip(),
        outline=outline.strip(),
    )

    generator = _get_text_generator()
    generation_kwargs = _extract_generation_parameters(config.get("parameters"))

    raw_response: Optional[str] = None
    used_fallback = False

    if generator is not None:
        try:
            raw_response = generator.generate_response(final_prompt, **generation_kwargs)
        except Exception as exc:  # pragma: no cover - defensive logging for integrations
            current_app.logger.warning(
                "Concept identification generation failed; using fallback. Error: %s",
                exc,
            )

    candidates = _parse_candidates(raw_response)
    if not candidates:
        candidates = _fallback_candidates(outline, project_fragment)
        used_fallback = True

    return candidates, used_fallback


def _define_concepts(
    outline: str,
    candidates: Iterable[ConceptCandidate],
    project_fragment: str,
) -> tuple[List[ConceptDefinitionPayload], bool]:
    candidate_list = list(candidates)
    config = _load_prompt_entry_safe(PROMPT_KEY_DEFINE)
    prompt_template = config.get("prompt_template")
    if not prompt_template:
        raise ConceptClarificationError("Prompt template for concept definitions is missing.")

    concepts_payload = [
        {
            "name": candidate.name,
            "issue": candidate.issue,
        }
        for candidate in candidate_list
    ]
    payload_text = json.dumps({"concepts": concepts_payload}, ensure_ascii=False, indent=2)

    final_prompt = _apply_template(
        prompt_template,
        project_title=project_fragment.strip(),
        outline=outline.strip(),
        concept_list=payload_text.strip(),
    )

    generator = _get_text_generator()
    generation_kwargs = _extract_generation_parameters(config.get("parameters"))

    raw_response: Optional[str] = None
    used_fallback = False

    if generator is not None:
        try:
            raw_response = generator.generate_response(final_prompt, **generation_kwargs)
        except Exception as exc:  # pragma: no cover - integration guard
            current_app.logger.warning(
                "Concept definition generation failed; using fallback. Error: %s",
                exc,
            )

    definitions = _parse_definition_payload(raw_response)
    if not definitions:
        definitions = _fallback_concept_definitions(outline, candidate_list, project_fragment)
        used_fallback = True

    return definitions, used_fallback


def _parse_candidates(raw_response: Optional[str]) -> List[ConceptCandidate]:
    if not raw_response:
        return []

    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError:
        return []

    concepts = data.get("concepts") if isinstance(data, dict) else data
    if not isinstance(concepts, list):
        return []

    candidates: List[ConceptCandidate] = []
    for item in concepts:
        if not isinstance(item, dict):
            continue
        name_raw = item.get("name")
        issue_raw = item.get("issue")
        if not isinstance(name_raw, str):
            continue
        name = name_raw.strip()
        if not name:
            continue
        issue = issue_raw.strip() if isinstance(issue_raw, str) else None
        candidates.append(ConceptCandidate(name=name, issue=issue))

    return candidates


def _parse_definition_payload(raw_response: Optional[str]) -> List[ConceptDefinitionPayload]:
    if not raw_response:
        return []

    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError:
        return []

    concepts = data.get("concepts") if isinstance(data, dict) else data
    if not isinstance(concepts, list):
        return []

    definitions: List[ConceptDefinitionPayload] = []
    for item in concepts:
        if not isinstance(item, dict):
            continue
        name_raw = item.get("name")
        definition_raw = item.get("definition")
        examples_raw = item.get("examples")
        if not isinstance(name_raw, str) or not isinstance(definition_raw, str):
            continue
        name = name_raw.strip()
        definition = definition_raw.strip()
        if not name or not definition:
            continue
        if isinstance(examples_raw, list):
            examples = [str(example).strip() for example in examples_raw if str(example).strip()]
        elif isinstance(examples_raw, str):
            examples = [examples_raw.strip()] if examples_raw.strip() else []
        else:
            examples = []
        definitions.append(
            ConceptDefinitionPayload(
                name=name,
                definition=definition,
                examples=examples,
            )
        )

    return definitions


def _fallback_candidates(outline: str, project_fragment: str, limit: int = 5) -> List[ConceptCandidate]:
    words = [word for word in _WORD_PATTERN.findall(outline) if len(word) > 4]
    counts = Counter(word.casefold() for word in words)

    candidates: List[ConceptCandidate] = []
    for word, count in counts.most_common():
        if count < 2:
            continue
        if word in _STOPWORDS:
            continue
        name = word.replace("'", " ").strip().title()
        if not name:
            continue
        candidates.append(
            ConceptCandidate(
                name=name,
                issue=f"Clarify how {name} functions within the story.",
            )
        )
        if len(candidates) >= limit:
            break

    if not candidates:
        candidates.append(
            ConceptCandidate(
                name=project_fragment,
                issue="Clarify the central concept and the stakes driving the narrative.",
            )
        )

    return candidates


def _fallback_concept_definitions(
    outline: str,
    candidates: Iterable[ConceptCandidate],
    project_fragment: str,
) -> List[ConceptDefinitionPayload]:
    candidate_list = list(candidates)
    if not candidate_list:
        candidate_list = _fallback_candidates(outline, project_fragment)

    definitions: List[ConceptDefinitionPayload] = []
    for candidate in candidate_list:
        context = _extract_context(outline, candidate.name)
        if context:
            definition = (
                f"{candidate.name} represents {context}. Expand the outline with sensory detail, clear stakes, "
                "and how characters experience this concept moment to moment."
            )
        else:
            definition = (
                f"{candidate.name} is a pivotal idea in {project_fragment}. Describe what it looks like, how it changes "
                "the world, and why the characters cannot ignore it."
            )
        examples = [
            f"A scene where {candidate.name} forces a difficult choice.",
            f"An image or sensation that shows {candidate.name} in action.",
        ]
        definitions.append(
            ConceptDefinitionPayload(
                name=candidate.name,
                definition=definition,
                examples=examples,
                issue=candidate.issue,
            )
        )

    return definitions


def _extract_context(outline: str, term: str) -> str:
    if not term:
        return ""
    pattern = re.compile(rf"(.{{0,140}}{re.escape(term)}.{{0,140}})", re.IGNORECASE | re.DOTALL)
    match = pattern.search(outline)
    if not match:
        return ""
    snippet = " ".join(match.group(1).split())
    return snippet.strip()


def _apply_template(template: str, **values: str) -> str:
    result = template
    for key, raw in values.items():
        replacement = raw if isinstance(raw, str) else str(raw)
        result = result.replace(f"{{{key}}}", replacement)
    return result


def _load_prompt_entry_safe(key: str) -> dict:
    try:
        return _load_prompt_entry(key)
    except OutlineGenerationError as exc:
        raise ConceptClarificationError(str(exc)) from exc


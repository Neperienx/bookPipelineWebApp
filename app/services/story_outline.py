from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from flask import current_app

PROMPT_KEY = "outline_from_prompt"
PROMPT_CACHE_KEY = "_PROMPT_CONFIG_CACHE"


class OutlineGenerationError(RuntimeError):
    """Raised when an outline cannot be generated."""


@dataclass
class OutlineGenerationResult:
    outline: str
    prompt: str
    word_count: int
    used_fallback: bool


def generate_story_outline(user_prompt: str, *, project_title: Optional[str] = None) -> OutlineGenerationResult:
    """Create a structured outline from a short story idea.

    Parameters
    ----------
    user_prompt:
        The raw idea supplied by the user.
    project_title:
        Optional project title used to contextualise the generated outline.
    """

    prompt_text = (user_prompt or "").strip()
    if not prompt_text:
        raise OutlineGenerationError("A prompt is required to draft an outline.")

    config_entry = _load_prompt_entry(PROMPT_KEY)
    prompt_template = config_entry.get("prompt_template")
    if not prompt_template:
        raise OutlineGenerationError("Prompt configuration is missing the template text.")

    final_prompt = prompt_template.format(
        project_title=(project_title or "the story").strip(),
        user_prompt=prompt_text,
    )

    outline_text: Optional[str] = None
    used_fallback = False

    generator = _get_text_generator()
    generation_kwargs = _extract_generation_parameters(config_entry.get("parameters"))

    if generator is not None:
        try:
            outline_text = generator.generate_response(final_prompt, **generation_kwargs)
        except Exception as exc:  # pragma: no cover - defensive logging for external integrations
            current_app.logger.warning("LLM outline generation failed; falling back to heuristic outline. Error: %s", exc)

    if not outline_text:
        outline_text = _build_fallback_outline(prompt_text, project_title=project_title)
        used_fallback = True

    outline_text = outline_text.strip()
    if not outline_text:
        raise OutlineGenerationError("The outline generator returned an empty response.")

    word_count = len(_WORD_PATTERN.findall(outline_text))
    return OutlineGenerationResult(
        outline=outline_text,
        prompt=final_prompt,
        word_count=word_count,
        used_fallback=used_fallback,
    )


def _load_prompt_entry(key: str) -> Dict[str, Any]:
    config = _load_prompt_config()
    try:
        entry = config[key]
    except KeyError as exc:  # pragma: no cover - configuration issues are caught at runtime
        raise OutlineGenerationError(f"Prompt configuration is missing the '{key}' entry.") from exc
    if not isinstance(entry, dict):
        raise OutlineGenerationError(f"Prompt configuration entry '{key}' must be a dictionary.")
    return entry


def _load_prompt_config() -> Dict[str, Any]:
    app = current_app
    cached = app.config.get(PROMPT_CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    config_path = app.config.get("PROMPT_CONFIG_PATH")
    if not config_path:
        raise OutlineGenerationError("PROMPT_CONFIG_PATH is not configured.")

    path = Path(config_path)
    if not path.exists():
        raise OutlineGenerationError(f"Prompt configuration file not found at: {path}")

    with path.open("r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:  # pragma: no cover - malformed file should be obvious at runtime
            raise OutlineGenerationError(f"Unable to parse prompt configuration: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise OutlineGenerationError("Prompt configuration must be a JSON object.")

    app.config[PROMPT_CACHE_KEY] = data
    return data


_GENERATION_PARAMETER_KEYS = {
    "max_new_tokens",
    "temperature",
    "top_p",
    "repetition_penalty",
    "presence_penalty",
    "frequency_penalty",
    "top_k",
}


def _extract_generation_parameters(parameters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Filter a raw parameters dictionary to generation kwargs supported by the LLM."""

    if not isinstance(parameters, dict):
        return {}

    kwargs: Dict[str, Any] = {}
    for key in _GENERATION_PARAMETER_KEYS:
        if key in parameters and parameters[key] is not None:
            kwargs[key] = parameters[key]
    return kwargs


def _get_text_generator() -> Optional[Any]:  # pragma: no cover - integration point
    app = current_app
    if "_TEXT_GENERATOR_INSTANCE" in app.config:
        return app.config["_TEXT_GENERATOR_INSTANCE"]

    model_path = app.config.get("TEXT_GENERATOR_MODEL_PATH")
    if not model_path:
        app.logger.info("TEXT_GENERATOR_MODEL_PATH not configured; using fallback outline generator.")
        app.config["_TEXT_GENERATOR_INSTANCE"] = None
        return None

    try:
        from text_generator import TextGenerator

        app.logger.info("Initialising text generator with model path: %s", model_path)
        generator = TextGenerator(model_path=model_path)
    except Exception as exc:
        app.logger.warning("Failed to initialise text generator at '%s': %s", model_path, exc)
        generator = None
    app.config["_TEXT_GENERATOR_INSTANCE"] = generator
    return generator


def _build_fallback_outline(prompt_text: str, *, project_title: Optional[str]) -> str:
    concept = (prompt_text or "").strip()
    concept_excerpt = _concept_excerpt(concept)
    title_fragment = (project_title or "the story").strip() or "the story"

    sections = [
        (
            "Premise & Hook",
            [
                _pad_sentence(
                    f"Clarify the core concept driving {title_fragment}: {concept_excerpt}. Outline the protagonist, the central conflict, and the hook that signals why the narrative matters now.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Describe the tonal palette and genre markers that align with {title_fragment}, pointing to emotions or comparable works that hint at how the journey should feel.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Highlight the protagonist's current status quo and the promise of change implied by {concept_excerpt}, including the personal stake that will anchor reader investment.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Frame the guiding dramatic question—what must ultimately be resolved for {title_fragment} to feel complete, and what force immediately stands in the way?",
                    concept_excerpt,
                ),
            ],
        ),
        (
            "Act I — Setup & Disruption",
            [
                _pad_sentence(
                    f"Showcase daily life before the disruption, letting readers experience the textures, relationships, and expectations that {concept_excerpt} will inevitably overturn.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Introduce key supporting characters whose motivations either reinforce the protagonist's inertia or foreshadow the rupture to come.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Deliver the inciting incident that collides with the protagonist's goal, forcing a choice that aligns the story's direction with {title_fragment}.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Close the act with a point-of-no-return decision that propels the protagonist into unfamiliar territory, crystallising the central stakes of {concept_excerpt}.",
                    concept_excerpt,
                ),
            ],
        ),
        (
            "Act II — Rising Challenges",
            [
                _pad_sentence(
                    f"Lay out escalating obstacles that complicate the protagonist's pursuit, each revealing a deeper layer of theme or worldbuilding tied to {concept_excerpt}.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Show how allies and antagonists evolve in response to the central conflict, clarifying shifting loyalties and sharpening the cost of failure.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Include a midpoint revelation or reversal that reframes the protagonist's understanding of what is truly at stake within {title_fragment}.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Chart the spiral toward a low point where internal doubts and external pressure converge, forcing the protagonist to confront their core flaw.",
                    concept_excerpt,
                ),
            ],
        ),
        (
            "Act III — Climax & Resolution",
            [
                _pad_sentence(
                    f"Stage the final confrontation that demands the protagonist apply lessons learned, resolving the central tension seeded by {concept_excerpt}.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Illustrate how supporting characters influence the climax—either by offering pivotal aid or intensifying the stakes with conflicting agendas.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Deliver a decisive moment that answers the dramatic question posed earlier, showing the tangible cost of success or failure.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Conclude with an emotional denouement that signals the new normal, demonstrating how {title_fragment} has permanently shifted as a result of the journey.",
                    concept_excerpt,
                ),
            ],
        ),
        (
            "Character, Theme & World Threads",
            [
                _pad_sentence(
                    f"Track the protagonist's internal arc so it mirrors the external plot beats, tying each shift back to the promise of {concept_excerpt}.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Note secondary character turns that either challenge or reinforce the protagonist's transformation, ensuring every subplot contributes to the central theme.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Document recurring motifs, settings, or props that can symbolise the story's stakes and provide continuity throughout the outline.",
                    concept_excerpt,
                ),
                _pad_sentence(
                    f"Suggest closing imagery or final lines that echo the opening, giving {title_fragment} a resonant, cyclical sense of completion.",
                    concept_excerpt,
                ),
            ],
        ),
    ]

    lines: list[str] = []
    for heading, bullet_points in sections:
        lines.append(heading)
        for bullet in bullet_points:
            lines.append(f"- {bullet}")
        lines.append("")

    return "\n".join(lines).strip()


def _pad_sentence(sentence: str, concept_excerpt: str, *, target_words: int = 26) -> str:
    words = sentence.split()
    if len(words) >= target_words:
        return sentence
    filler = (
        f" Layer in sensory detail, concrete stakes, and the protagonist's emotional filter so the moment feels alive and specific to {concept_excerpt}."
    )
    return sentence + filler


def _concept_excerpt(concept: str, max_words: int = 22) -> str:
    words = concept.split()
    if not words:
        return "your story concept"
    if len(words) <= max_words:
        return concept
    return " ".join(words[:max_words]) + "…"


_WORD_PATTERN = re.compile(r"\b\w+[\w'-]*\b")

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from flask import current_app

from .story_outline import OutlineGenerationError, _get_text_generator, _load_prompt_entry


class StageGenerationError(RuntimeError):
    """Raised when a stage suggestion cannot be generated."""


@dataclass
class StageContentResult:
    text: str
    prompt: str
    used_fallback: bool


STAGE_PROMPT_KEYS: Dict[str, str] = {
    "prompt": "stage_initial_story_prompt",
    "characters": "stage_character_development",
}


def generate_stage_content(stage: str, user_prompt: str, *, project_title: Optional[str] = None) -> StageContentResult:
    stage_key = STAGE_PROMPT_KEYS.get(stage)
    if not stage_key:
        raise StageGenerationError("Unsupported stage requested.")

    cleaned_prompt = (user_prompt or "").strip()
    if not cleaned_prompt:
        raise StageGenerationError("Provide a short brief so the assistant can help.")

    project_fragment = (project_title or "the story").strip() or "the story"

    try:
        config_entry = _load_prompt_entry(stage_key)
    except OutlineGenerationError as exc:
        raise StageGenerationError(str(exc)) from exc
    prompt_template = config_entry.get("prompt_template")
    if not prompt_template:
        raise StageGenerationError("Prompt template is missing for this stage.")

    final_prompt = prompt_template.format(
        project_title=project_fragment,
        user_prompt=cleaned_prompt,
    )

    generator = _get_text_generator()
    parameters = config_entry.get("parameters") if isinstance(config_entry, dict) else None
    max_new_tokens = None
    if isinstance(parameters, dict):
        max_new_tokens = parameters.get("max_new_tokens")

    result_text: Optional[str] = None
    used_fallback = False

    if generator is not None:
        try:
            result_text = generator.generate_response(final_prompt, max_new_tokens=max_new_tokens)
        except Exception as exc:  # pragma: no cover - defensive log for integrations
            current_app.logger.warning(
                "LLM stage generation failed; using fallback response for '%s'. Error: %s",
                stage,
                exc,
            )

    if not result_text:
        result_text = _fallback_for_stage(stage, cleaned_prompt, project_fragment)
        used_fallback = True

    result_text = result_text.strip()
    if not result_text:
        raise StageGenerationError("The assistant returned an empty response.")

    return StageContentResult(text=result_text, prompt=final_prompt, used_fallback=used_fallback)


def _fallback_for_stage(stage: str, user_prompt: str, project_title: str) -> str:
    if stage == "prompt":
        return (
            f"Working title: {project_title}\n"
            "Premise summary:\n"
            f"- Clarify the inciting incident suggested by: {user_prompt}.\n"
            "- Outline the protagonist's central desire and immediate obstacle.\n"
            "- Note the tone, genre, or comparable stories that inspired this concept.\n\n"
            "Key questions to explore next:\n"
            "1. What personal stakes make the protagonist risk everything?\n"
            "2. Which relationship will transform the most because of these events?\n"
            "3. Where does the story leave the reader emotionally?"
        )
    if stage == "characters":
        return (
            f"Character seeds for {project_title}:\n"
            "1. Protagonist — capture their public persona versus private fear, the choice they avoid, and one sensory detail that defines them.\n"
            "2. Antagonistic Force — describe the pressure it exerts, how it mirrors the protagonist, and why it believes it must win.\n"
            "3. Key Ally — note the skill that makes them indispensable, the boundary they won't cross, and the moment they may waver.\n"
            "4. Wildcard — imagine an unexpected character or subplot that complicates loyalties."
        )

    raise StageGenerationError("No fallback is defined for this stage.")

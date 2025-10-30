"""Central configuration for system prompts used by the text generator."""

from __future__ import annotations

SYSTEM_PROMPTS = {
    "outline_assistant": (
        "You are a creative writing assistant. The author story outline is as follows: "
        "(Make a story outline in 200 words or less)"
    ),
    "character_creation": {
        "base": "You are a writing assistant and we want to create a character.",
        "json_format_rules": (
            "Respond exclusively with a single valid JSON object that follows the provided schema. "
            "Use double quotes for all keys and string values, avoid trailing commas, and do not wrap the JSON "
            "in Markdown code fences or add any explanatory prose before or after it."
        ),
        "fields": [
            {
                "key": "character_outline",
                "label": "Character outline",
                "description": (
                    "Summarise the character in no more than 100 words. Combine all known details with evocative "
                    "story hooks that highlight motivation, conflict, and personality."
                ),
                "word_count": 100,
            }
        ],
        "input_fields": [
            {
                "key": "name",
                "label": "Name",
                "description": (
                    "Provide the character’s full name and any commonly used nickname(s). The name should reflect the "
                    "character’s cultural background, era, and tone of the story."
                ),
                "input_type": "text",
                "required": True,
            },
            {
                "key": "role_in_story",
                "label": "Role in the story",
                "description": (
                    "Explain how the character functions within the narrative—hero, antagonist, mentor, comic relief, etc."
                ),
                "input_type": "text",
                "required": True,
            },
            {
                "key": "age",
                "label": "Age",
                "description": (
                    "State the character’s exact or approximate age. Optionally include how old they appear versus how old "
                    "they are if relevant to the story."
                ),
                "input_type": "text",
            },
            {
                "key": "gender_pronouns",
                "label": "Gender / Pronouns",
                "description": "Specify gender identity and pronouns.",
                "input_type": "text",
            },
            {
                "key": "basic_information",
                "label": "Basic information",
                "description": (
                    "Summarise foundational context: date and place of birth, nationality or ethnicity, occupation or "
                    "role, and current residence. Mention social or economic status and education level when it defines "
                    "their situation."
                ),
                "input_type": "textarea",
            },
            {
                "key": "physical_appearance",
                "label": "Physical appearance",
                "description": (
                    "Describe height, build, posture, hair and eye colour, skin tone, distinguishing features, clothing "
                    "style, and general demeanour."
                ),
                "input_type": "textarea",
            },
            {
                "key": "personality",
                "label": "Personality",
                "description": (
                    "Summarise behavioural and emotional traits, strengths and weaknesses, motivations and fears, guiding "
                    "values, and notable habits or mannerisms."
                ),
                "input_type": "textarea",
            },
            {
                "key": "background",
                "label": "Background",
                "description": (
                    "Outline upbringing, education or training, key life events or traumas, accomplishments or failures, "
                    "and major relationships that shaped them."
                ),
                "input_type": "textarea",
            },
            {
                "key": "psychology",
                "label": "Psychology",
                "description": (
                    "Explore core wound or psychological drive, main internal conflict, what they want versus need, "
                    "coping mechanisms, and how they rationalise decisions."
                ),
                "input_type": "textarea",
            },
            {
                "key": "additional_notes",
                "label": "Additional notes",
                "description": (
                    "Include any other direction for the assistant—tone, relationships, secrets, or anything that doesn’t "
                    "fit another field."
                ),
                "input_type": "textarea",
            },
        ],
    },
}


def get_character_fields() -> list[dict]:
    """Return the configured character template fields."""

    config = SYSTEM_PROMPTS.get("character_creation", {})
    fields = config.get("fields", [])
    return list(fields)


def get_character_input_fields() -> list[dict]:
    """Return the character input fields that guide outline generation."""

    config = SYSTEM_PROMPTS.get("character_creation", {})
    input_fields = config.get("input_fields", [])
    return list(input_fields)

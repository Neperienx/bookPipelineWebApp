"""Central configuration for system prompts used by the text generator."""

from __future__ import annotations

SYSTEM_PROMPTS = {
    "outline_assistant": (
        "You are a creative writing assistant. The author story outline is as follows: "
        "(Make a story outline in 200 words or less)"
    ),
    "character_creation": {
        "base": "You are a writing assistant and we want to create a character.",
        "fields": [
            {
                "key": "name",
                "label": "Name",
                "description": (
                    "Provide the character’s full name and any commonly used nickname(s). "
                    "The name should reflect the character’s cultural background, era, "
                    "and tone of the story."
                ),
                "word_count": 40,
            },
            {
                "key": "age",
                "label": "Age",
                "description": (
                    "State the character’s exact or approximate age. Optionally include "
                    "how old they appear versus how old they are if relevant to the story."
                ),
                "word_count": 35,
            },
            {
                "key": "gender_pronouns",
                "label": "Gender / Pronouns",
                "description": (
                    "Specify gender identity and pronouns. Mention how this influences "
                    "their self-presentation or relationships if it matters to the story."
                ),
                "word_count": 40,
            },
            {
                "key": "basic_information",
                "label": "Basic Information",
                "description": (
                    "Summarise foundational context: date and place of birth, nationality "
                    "or ethnicity, occupation or role, and current residence. Mention "
                    "social or economic status and education level when it defines their "
                    "situation."
                ),
                "word_count": 85,
            },
            {
                "key": "physical_appearance",
                "label": "Physical Appearance",
                "description": (
                    "Describe height, build, posture, hair and eye colour, skin tone, "
                    "distinguishing features, clothing style, and general demeanour in 4–6 sentences."
                ),
                "word_count": 95,
            },
            {
                "key": "personality",
                "label": "Personality",
                "description": (
                    "Summarise behavioural and emotional traits, strengths and weaknesses, "
                    "motivations and fears, guiding values, and notable habits or mannerisms."
                ),
                "word_count": 110,
            },
            {
                "key": "background",
                "label": "Background",
                "description": (
                    "Outline upbringing, education or training, key life events or traumas, "
                    "accomplishments or failures, and major relationships that shaped them."
                ),
                "word_count": 130,
            },
            {
                "key": "psychology",
                "label": "Psychology",
                "description": (
                    "Explore core wound or psychological drive, main internal conflict, "
                    "what they want versus need, coping mechanisms, and how they rationalise decisions."
                ),
                "word_count": 120,
            },
            {
                "key": "in_story",
                "label": "In the Story",
                "description": (
                    "Explain their narrative role and function, primary goal and obstacles, "
                    "arc from beginning to end, and thematic or symbolic importance within the setting."
                ),
                "word_count": 120,
            },
        ],
    },
}


def get_character_fields() -> list[dict]:
    """Return the configured character template fields."""

    config = SYSTEM_PROMPTS.get("character_creation", {})
    fields = config.get("fields", [])
    return list(fields)

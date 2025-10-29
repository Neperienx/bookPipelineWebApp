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
                    "and tone of the story. You should strickly stick to the format 'Name: XYZ, Nickname:ABC'"
                ),
                "word_count": 5,
            },
            {
                "key": "age",
                "label": "Age",
                "description": (
                    "State the character’s exact or approximate age. Optionally include "
                    "how old they appear versus how old they are if relevant to the story. Our answer should not be longer than 5 words"
                ),
                "word_count": 5,
            },
            {
                "key": "gender_pronouns",
                "label": "Gender / Pronouns",
                "description": (
                    "Specify gender identity and pronouns."
                    "Your answer should not exceed 5 words"
                ),
                "word_count": 4,
            },
            {
                "key": "basic_information",
                "label": "Basic Information",
                "description": (
                    "Summarise foundational context: date and place of birth, nationality "
                    "or ethnicity, occupation or role, and current residence. Mention "
                    "social or economic status and education level when it defines their "
                    "situation. Your answer should not exceed 80 words"
                ),
                "word_count": 80,
            },
            {
                "key": "physical_appearance",
                "label": "Physical Appearance",
                "description": (
                    "Describe height, build, posture, hair and eye colour, skin tone, "
                    "distinguishing features, clothing style, and general demeanour in 4–6 sentences."
                    "situation. Your answer should not exceed 80 words"
                ),
                "word_count": 80,
            },
            {
                "key": "personality",
                "label": "Personality",
                "description": (
                    "Summarise behavioural and emotional traits, strengths and weaknesses, "
                    "motivations and fears, guiding values, and notable habits or mannerisms."
                    "situation. Your answer should not exceed 80 words"
                ),
                "word_count": 80,
            },
            {
                "key": "background",
                "label": "Background",
                "description": (
                    "Outline upbringing, education or training, key life events or traumas, "
                    "accomplishments or failures, and major relationships that shaped them."
                    "situation. Your answer should not exceed 130 words"
                ),
                "word_count": 130,
            },
            {
                "key": "psychology",
                "label": "Psychology",
                "description": (
                    "Explore core wound or psychological drive, main internal conflict, "
                    "what they want versus need, coping mechanisms, and how they rationalise decisions."
                    "situation. Your answer should not exceed 80 words"
                ),
                "word_count": 80,
            },
            {
                "key": "in_story",
                "label": "In the Story",
                "description": (
                    "Explain their narrative role and function, primary goal and obstacles, "
                    "arc from beginning to end, and thematic or symbolic importance within the setting."
                    "situation. Your answer should not exceed 80 words"
                ),
                "word_count": 80,
            },
        ],
    },
}


def get_character_fields() -> list[dict]:
    """Return the configured character template fields."""

    config = SYSTEM_PROMPTS.get("character_creation", {})
    fields = config.get("fields", [])
    return list(fields)

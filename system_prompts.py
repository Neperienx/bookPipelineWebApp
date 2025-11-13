"""Central configuration for system prompts used by the text generator."""

from __future__ import annotations

SYSTEM_PROMPTS = {
    "project_overview": {
        "max_new_tokens": 512,
        "base": (
            "You are a developmental editor distilling an author's questionnaire responses "
            "into a concise creative brief."
        ),
        "response_instructions": (
            "Write a single cohesive paragraph of roughly 200 words that captures the book's "
            "core concept, goals, audience, genre positioning, tone, and stylistic aims."
        ),
    },
    "outline_assistant": {
        "prompt": (
            "You are a creative writing assistant. Analyze the author's pitch and character roster, then draft "
            "three sharply differentiated story outlines that feel ready for production planning."
            " Respond with sections titled 'Outline 1', 'Outline 2', and 'Outline 3'. Under each heading, provide "
            "five numbered beats that cover setup, escalation, midpoint, dark turn, and finale. Each beat must "
            "state concrete goals, conflicts, locations, and any twists—avoid vague language or generic plot "
            "phrases."
        ),
        "refinement_prompt": (
            "You are a narrative development editor. Evaluate the three candidate outlines below and decide which "
            "one delivers the strongest, most marketable story. Select that outline, optionally weaving in standout "
            "beats from the others, and polish it into a single, precise plan."
            " Reply with a final section titled 'Refined Outline' that contains six numbered beats detailing specific "
            "character motivations, scene locations, reversals, and the climactic resolution. Do not stay vague—make "
            "each beat actionable."
        ),
        "max_new_tokens": 512,
    },
    "act_outline": {
        "max_new_tokens": 1012,
        "base": (
            "You are a collaborative narrative designer helping an author expand a "
            "story into a vivid three-act outline. Honour the provided outline, "
            "character notes, and final directions while keeping pacing taut and "
            "dramatic. Feel free to invent supporting characters if needed"
        ),
        "format": (
            "Respond in plain text using this exact structure for each act:\n"
            "Act: Act <Roman numeral> — <Act focus or title>\n"
            "1. First major beat\n"
            "2. Second major beat\n"
            "(Continue numbered beats through 4-6 total before moving to the next act.)\n"
            "Ensure there is a blank line between act sections and no additional commentary."
        ),
        "acts": {
            1: (
                "Establish the core cast, tone, setting, and status quo. Build to a "
                "clear inciting incident or disruption that propels the protagonist "
                "toward a decisive response."
            ),
            2: (
                "Explore rising complications, deepening relationships, and "
                "escalating stakes. Introduce reversals, midpoint revelations, and "
                "choices that force the protagonist to adapt."
            ),
            3: (
                "Deliver the climax, resolve central conflicts, and highlight the "
                "aftermath. Show how character arcs pay off and leave room for "
                "reflection or future possibilities."
            ),
        },
    },
    "chapter_outline": {
        "max_new_tokens": 2512,
        "base": (
            "You are a creative writing assistant specialising in expanding act-level "
            "plans into vivid, sequential chapter breakdowns that honour continuity "
            "and escalate drama."
        ),
        "act_focus": (
            "Concentrate solely on {act_label}. Draw on earlier acts for continuity but do not plan beyond the specified act."
        ),
        "chapter_count": (
            "Outline this act in exactly {chapter_count} chapters, making sure every chapter meaningfully advances the plot and character arcs."
        ),
        "format": (
            "Respond in plain text. For each chapter, start a new section with the header 'Chapter: Chapter <number> — <Title>'. "
            "Follow the header with 2-3 scenes that will be happening in this chapter and leave a blank line between chapters. "
            "Do not include bullets or commentary outside these chapter sections."
        ),
    },
    "chapter_drafting": {
        "max_new_tokens": 3072,
        "base": (
            "You are a collaborative novelist polishing full chapters of prose. Respect the provided outlines, maintain tonal consistency, and deliver immersive narrative ready for a manuscript."
        ),
        "continuity": (
            "Track ongoing arcs, settings, and character motivations so the new chapter seamlessly follows the existing material."
        ),
        "style": (
            "Write vivid, publication-ready prose with rich sensory detail, purposeful pacing, and authentic dialogue."
        ),
        "format": (
            "Reply with plain text paragraphs suitable for a novel manuscript. Do not use markdown headings, bullet points, or screenplay formatting."
        ),
        "length": (
            "Aim for roughly 900-1200 words unless the outline clearly indicates a different scope."
        ),
    },
    "supporting_characters": {
        "max_new_tokens": 712,
        "base": (
            "You are a casting director and narrative designer who ensures a story's supporting cast is complete. "
            "Study the provided three-act outline and the current roster of fully developed characters."
        ),
        "task": (
            "Identify supporting characters who appear or are strongly implied in the acts but have not been fully documented. "
            "Skip any characters whose names already appear in the roster list."
        ),
        "format": (
            "Respond in plain text using one section per character. Follow this exact template for every entry:\n"
            "Character: <Character name>\n"
            "<Provide 2-3 sentences describing their role, personality, and how they support the protagonist or plot.>\n"
            "Leave a blank line between characters and avoid extra commentary before or after the list."
        ),
    },
    "concept_development": {
        "max_new_tokens": 512,
        "analysis_prompt": (
            "You are a developmental editor who reviews story outlines to find concepts that still feel vague."
            " Call out the ideas that need clearer definitions so the author knows what to expand."
        ),
        "analysis_response_instructions": (
            "List each unclear concept on its own line using the format 'Concept Name — brief note about what needs to be clarified.'"
            " If everything already feels concrete, reply with 'No unclear concepts found.'"
        ),
        "definition_prompt": (
            "You are a worldbuilding consultant polishing story concepts."
            " For every concept the editor flagged, explain what it represents in this story's world"
            " and ground the explanation in the outline's tone, genre, and stakes."
        ),
        "definition_response_instructions": (
            "For each concept, start a new paragraph with 'Concept Name:' followed by a concise definition."
            " After the definition, add a sentence beginning with 'Examples:' that offers one or two ways"
            " the concept could appear in the story."
        ),
    },
    "character_creation": {
        "max_new_tokens": 512,
        "base": (
            "You are a writing assistant focused solely on developing character dossiers. "
            "When asked to create a character, limit your response to the requested profile fields. "
            "Do not begin plotting scenes or story beats—deliver only the character details."
        ),
        "json_format_rules": (
            "Respond exclusively with a single valid JSON object that follows the provided schema. "
            "Use double quotes for all keys and string values, avoid trailing commas, and do not wrap the JSON "
            "in Markdown code fences or add any explanatory prose before or after it."
        ),
        "fields": [
            {
                "key": "physical_description",
                "label": "Physical description",
                "description": (
                    "Detail the character's immediate physical impression—age cues, build, posture, distinguishing features, "
                    "and signature clothing or accessories—in concrete, sensory language."
                ),
                "word_count": 80,
            },
            {
                "key": "character_description",
                "label": "Character description",
                "description": (
                    "Capture temperament, core motivations, notable skills, and interpersonal style without outlining plot "
                    "events or future scenes. Keep the focus on who they are in daily life."
                ),
                "word_count": 110,
            },
            {
                "key": "background",
                "label": "Background",
                "description": (
                    "Provide a concise history that explains formative experiences, relationships, and turning points shaping "
                    "the character. Mention context needed to understand them, but do not advance the current story."
                ),
                "word_count": 120,
            },
            {
                "key": "personality_frictions",
                "label": "Potential frictions & hidden motivations",
                "description": (
                    "Surface habits or outlooks that might grate on allies, underlying tensions between their public persona "
                    "and private desires, and any quiet agendas that could complicate relationships."
                ),
                "word_count": 110,
            },
            {
                "key": "secret",
                "label": "Secret",
                "description": (
                    "Reveal one consequential secret the character keeps, why it matters, and what could expose it or raise "
                    "the stakes if discovered."
                ),
                "word_count": 70,
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


def get_prompt_max_new_tokens(name: str, fallback: int | None = None) -> int | None:
    """Return the configured ``max_new_tokens`` for ``name`` if available."""

    entry = SYSTEM_PROMPTS.get(name)
    if not isinstance(entry, dict):
        return fallback

    raw_value = entry.get("max_new_tokens")
    if raw_value is None:
        return fallback

    try:
        tokens = int(raw_value)
    except (TypeError, ValueError):
        return fallback

    if tokens <= 0:
        return fallback

    return tokens

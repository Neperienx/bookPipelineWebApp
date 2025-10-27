import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.config import TestConfig
from app.services import autofill


@pytest.fixture
def app_ctx():
    app = create_app(TestConfig)
    ctx = app.app_context()
    ctx.push()
    yield app
    ctx.pop()


def test_draft_character_profile_calls_generator_per_field(monkeypatch, app_ctx):
    responses = {
        "name": "Radiant Wanderer",
        "role": "Reluctant hero",
        "background": "Raised on the edge of a forgotten colony.",
        "goals": "Wants to reunite the fractured clans.",
        "conflict": "Hunted by the regime they betrayed.",
        "notes": "Secretly allied with the governor's heir.",
    }
    calls = []

    class DummyGenerator:
        def generate_response(self, prompt: str, **_: object) -> str:
            calls.append(prompt)
            for field, value in responses.items():
                if f'"{field}"' in prompt:
                    return json.dumps({field: value})
            return json.dumps({})

    monkeypatch.setattr(autofill, "_get_text_generator", lambda: DummyGenerator())

    result = autofill.draft_character_profile("A drifter tied to ancient ruins", project_title="Field Test")

    assert result.profile == responses
    assert not result.used_fallback
    assert len(calls) == len(responses)
    for field in responses:
        assert any(f'"{field}"' in prompt for prompt in calls)


def test_draft_character_profile_uses_fallback_when_field_missing(monkeypatch, app_ctx):
    fallback = autofill._fallback_single_character("Fallback Tale", "A stubborn pilot")

    def generator_factory():
        class PartialGenerator:
            def generate_response(self, prompt: str, **_: object) -> str:
                for field in ("name", "role", "background", "goals", "conflict", "notes"):
                    if f'"{field}"' in prompt:
                        if field == "conflict":
                            return json.dumps({})
                        return json.dumps({field: f"custom {field}"})
                return json.dumps({})

        return PartialGenerator()

    monkeypatch.setattr(autofill, "_get_text_generator", generator_factory)

    result = autofill.draft_character_profile("A stubborn pilot", project_title="Fallback Tale")

    assert result.profile["conflict"] == fallback["conflict"]
    for field in ("name", "role", "background", "goals", "notes"):
        assert result.profile[field] == f"custom {field}"
    assert result.used_fallback

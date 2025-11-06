import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.config import TestConfig
from app.services import concept_analysis


@pytest.fixture
def app_ctx():
    app = create_app(TestConfig)
    ctx = app.app_context()
    ctx.push()
    yield app
    ctx.pop()


def test_clarify_outline_concepts_uses_generator(monkeypatch, app_ctx):
    outline = (
        "Act I introduces the Chrono Lattice, a shimmering network of time gates. "
        "Heroes struggle to stabilise the Chrono Lattice before the festival."
    )

    responses = iter(
        [
            json.dumps(
                {
                    "concepts": [
                        {"name": "Chrono Lattice", "issue": "Explain how travellers interact with it."}
                    ]
                }
            ),
            json.dumps(
                {
                    "concepts": [
                        {
                            "name": "Chrono Lattice",
                            "definition": "An interlocking field of gateways regulating the city's time flow.",
                            "examples": [
                                "Merchants schedule deliveries around lattice openings.",
                                "Citizens queue at calibration plazas when the lattice falters.",
                            ],
                        }
                    ]
                }
            ),
        ]
    )

    calls = []

    class DummyGenerator:
        def generate_response(self, prompt: str, **_: object) -> str:
            calls.append(prompt)
            return next(responses)

    monkeypatch.setattr(concept_analysis, "_get_text_generator", lambda: DummyGenerator())

    result = concept_analysis.clarify_outline_concepts(outline, project_title="Clockwork Harbor")

    assert result.concepts
    assert not result.used_fallback
    concept = result.concepts[0]
    assert concept.name == "Chrono Lattice"
    assert "time flow" in concept.definition
    assert concept.examples == [
        "Merchants schedule deliveries around lattice openings.",
        "Citizens queue at calibration plazas when the lattice falters.",
    ]
    assert len(calls) == 2


def test_clarify_outline_concepts_fallback(monkeypatch, app_ctx):
    monkeypatch.setattr(concept_analysis, "_get_text_generator", lambda: None)

    outline = (
        "Legends say the Veilstorm arrives each solstice. Villagers build charms to weather the Veilstorm, "
        "yet the Veilstorm's true nature remains unclear."
    )

    result = concept_analysis.clarify_outline_concepts(outline, project_title="Veil Saga")

    assert result.used_fallback
    assert result.concepts
    assert any("Veilstorm" in concept.name for concept in result.concepts)
    for concept in result.concepts:
        assert concept.definition
        assert concept.examples

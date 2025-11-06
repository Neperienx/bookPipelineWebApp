import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import CharacterProfile, ConceptDefinition, OutlineDraft, Project, User
from app.services import concept_analysis


@pytest.fixture
def app_instance():
    app = create_app(TestConfig)
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture
def client(app_instance):
    return app_instance.test_client()


@pytest.fixture
def user(app_instance):
    user = User(email="user@example.com", display_name="Test User")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def project(app_instance, user):
    project = Project(title="Demo Project", description="Desc", owner=user)
    db.session.add(project)
    db.session.commit()
    return project


def _login(client, user):
    client.post(
        "/login",
        data={"email": user.email, "password": "password123"},
        follow_redirects=True,
    )


def test_invalid_character_submission_shows_flash(client, user, project):
    _login(client, user)

    response = client.post(
        f"/projects/{project.id}",
        data={
            "character-csrf_token": "",
            "character-name": "",
            "character-role": "",
            "character-background": "",
            "character-goals": "",
            "character-conflict": "",
            "character-notes": "",
            "character-seed_prompt": "Pilot prompt",
            "character-submit": "Save character",
        },
        follow_redirects=True,
    )

    assert b"Add a character name before saving." in response.data
    assert CharacterProfile.query.count() == 0


def test_valid_character_submission_creates_profile(client, user, project):
    _login(client, user)

    response = client.post(
        f"/projects/{project.id}",
        data={
            "character-csrf_token": "",
            "character-name": "Nova",
            "character-role": "Scout",
            "character-background": "Raised among smugglers.",
            "character-goals": "Wants to chart safe passages.",
            "character-conflict": "Owes a debt to crime lords.",
            "character-notes": "Distrusts authority.",
            "character-seed_prompt": "",
            "character-submit": "Save character",
        },
        follow_redirects=True,
    )

    assert b"Character added to the project." in response.data
    assert CharacterProfile.query.count() == 1
    assert CharacterProfile.query.first().name == "Nova"


def test_generate_concepts_endpoint_creates_entries(monkeypatch, client, user, project):
    _login(client, user)

    outline = OutlineDraft(project=project, title="Outline 1", content="Explore the Astral Bridge and its rituals.")
    db.session.add(outline)
    db.session.commit()

    class DummyResult:
        def __init__(self):
            self.concepts = [
                concept_analysis.ConceptDefinitionPayload(
                    name="Astral Bridge",
                    definition="A luminous causeway connecting floating monasteries.",
                    examples=[
                        "Monks chart safe passage across the bridge at dawn.",
                        "Merchants trade offerings beside the bridge's pylons.",
                    ],
                    issue="Clarify how travellers access the bridge.",
                )
            ]
            self.used_fallback = False

    monkeypatch.setattr(
        "app.projects.routes.clarify_outline_concepts",
        lambda outline_text, project_title=None: DummyResult(),
    )

    response = client.post(
        f"/projects/{project.id}/concepts",
        json={"outline_id": outline.id},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["outline_id"] == outline.id
    assert payload["used_fallback"] is False
    assert len(payload.get("concepts", [])) == 1

    stored = ConceptDefinition.query.all()
    assert len(stored) == 1
    concept = stored[0]
    assert concept.name == "Astral Bridge"
    assert concept.definition == "A luminous causeway connecting floating monasteries."
    assert concept.examples_list == [
        "Monks chart safe passage across the bridge at dawn.",
        "Merchants trade offerings beside the bridge's pylons.",
    ]
    assert concept.outline_id == outline.id

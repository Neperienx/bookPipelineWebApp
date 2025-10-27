import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import CharacterProfile, Project, User


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

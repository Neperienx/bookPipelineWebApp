from __future__ import annotations

from datetime import datetime
from typing import Optional

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    projects = db.relationship("Project", backref="owner", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:  # pragma: no cover - repr for debugging
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(user_id: str) -> Optional["User"]:
    return User.query.get(int(user_id))


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default="draft")
    current_step = db.Column(db.String(50), nullable=False, default="outline")
    last_outline_prompt = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    outlines = db.relationship(
        "OutlineDraft",
        backref="project",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="OutlineDraft.created_at.desc()",
    )
    acts = db.relationship(
        "ActOutline",
        backref="project",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ActOutline.sequence",
    )
    characters = db.relationship(
        "CharacterProfile",
        backref="project",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="CharacterProfile.name",
    )
    stages = db.relationship(
        "ProjectStage",
        backref="project",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ProjectStage.stage",
    )
    concepts = db.relationship(
        "ConceptDefinition",
        backref="project",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ConceptDefinition.name",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.title} ({self.status})>"


class OutlineDraft(db.Model):
    __tablename__ = "outline_drafts"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    prompt = db.Column(db.Text, nullable=True)
    word_count = db.Column(db.Integer, nullable=False, default=0)
    used_fallback = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OutlineDraft {self.title} ({self.word_count} words)>"


class ActOutline(db.Model):
    __tablename__ = "act_outlines"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False, default=1)
    title = db.Column(db.String(150), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    turning_points = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ActOutline {self.sequence}: {self.title}>"


class CharacterProfile(db.Model):
    __tablename__ = "character_profiles"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    background = db.Column(db.Text, nullable=True)
    role = db.Column(db.String(120), nullable=True)
    goals = db.Column(db.Text, nullable=True)
    conflict = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CharacterProfile {self.name}>"


class ProjectStage(db.Model):
    __tablename__ = "project_stages"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    stage = db.Column(db.String(50), nullable=False)
    system_prompt = db.Column(db.Text, nullable=True)
    user_prompt = db.Column(db.Text, nullable=True)
    generated_text = db.Column(db.Text, nullable=True)
    used_fallback = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("project_id", "stage", name="uq_project_stage_stage"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectStage {self.stage} for project {self.project_id}>"


class ConceptDefinition(db.Model):
    __tablename__ = "concept_definitions"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    outline_id = db.Column(db.Integer, db.ForeignKey("outline_drafts.id"), nullable=True, index=True)
    name = db.Column(db.String(150), nullable=False)
    clarity_issue = db.Column(db.Text, nullable=True)
    definition = db.Column(db.Text, nullable=False)
    examples = db.Column(db.Text, nullable=True)
    used_fallback = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ConceptDefinition {self.name} (project {self.project_id})>"

    @property
    def examples_list(self) -> list[str]:
        if not self.examples:
            return []
        return [item.strip() for item in self.examples.splitlines() if item.strip()]

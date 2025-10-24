"""Database helper utilities for ensuring schema consistency."""
from __future__ import annotations

from typing import Iterable, Set

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .extensions import db


def _get_column_names(table_name: str) -> Set[str]:
    inspector = inspect(db.engine)
    return {column["name"] for column in inspector.get_columns(table_name)}


def ensure_database_schema() -> None:
    """Ensure that essential schema updates are applied.

    The function is intentionally light-weight so it can run on every
    application start. It currently makes sure that the ``projects`` table has
    the ``last_outline_prompt`` column which was introduced after the initial
    database creation.
    """

    try:
        inspector = inspect(db.engine)
        table_names: Iterable[str] = inspector.get_table_names()

        if "projects" not in table_names:
            db.create_all()
            inspector = inspect(db.engine)
            table_names = inspector.get_table_names()

        # Import locally to avoid circular import issues during application setup.
        from .models import ActOutline, CharacterProfile, OutlineDraft, ProjectStage

        required_tables = {
            "outline_drafts": OutlineDraft.__table__,
            "act_outlines": ActOutline.__table__,
            "character_profiles": CharacterProfile.__table__,
            "project_stages": ProjectStage.__table__,
        }

        for table_name, table in required_tables.items():
            if table_name not in table_names:
                table.create(bind=db.engine)

        if "projects" in table_names:
            project_columns = _get_column_names("projects")
            if "last_outline_prompt" not in project_columns:
                with db.engine.begin() as connection:
                    connection.execute(
                        text("ALTER TABLE projects ADD COLUMN last_outline_prompt TEXT")
                    )
    except SQLAlchemyError:
        # If we fail to introspect or modify the schema we re-raise the error so
        # that the application does not continue in a partially configured state.
        raise

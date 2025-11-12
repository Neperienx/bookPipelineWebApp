"""Helpers for exporting chapter drafts to plain text files."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional


class TextExportError(RuntimeError):
    """Raised when exporting data to a text file fails."""


def _clean(value: Optional[str]) -> str:
    """Return ``value`` stripped of leading/trailing whitespace."""

    if not value:
        return ""
    return str(value).strip()


def export_chapter_drafts_to_txt(
    project: object,
    drafts: Iterable[object],
    *,
    output_path: Optional[Path] = None,
) -> Path:
    """Write ``drafts`` for ``project`` to a UTF-8 encoded text file."""

    project_name = _clean(getattr(project, "name", "")) or "Untitled Project"
    outline_text = _clean(getattr(project, "outline", ""))

    lines: list[str] = [project_name]
    if outline_text:
        lines.extend(["", "Project outline:", outline_text])

    for draft in drafts:
        lines.append("")
        header = (
            f"Act {getattr(draft, 'act_number', '?')} — "
            f"Chapter {getattr(draft, 'chapter_number', '?')}: "
            f"{_clean(getattr(draft, 'title', '')) or 'Untitled Chapter'}"
        )
        lines.append(header)

        summary = _clean(getattr(draft, "outline_summary", ""))
        if summary:
            lines.append(f"Outline: {summary}")

        content = _clean(getattr(draft, "content", ""))
        if content:
            lines.extend(["", content])
        else:
            lines.extend(["", "(No draft text available.)"])

    text_blob = "\n".join(lines).rstrip() + "\n"

    resolved_path = Path(output_path) if output_path else Path("temp.txt")

    try:
        resolved_path.write_text(text_blob, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - IO failure
        raise TextExportError(f"Unable to export TXT file: {exc}") from exc

    return resolved_path


__all__ = ["TextExportError", "export_chapter_drafts_to_txt"]

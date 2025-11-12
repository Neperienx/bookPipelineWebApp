"""Utility helpers for exporting project data to PDF documents.

This module centralises the FPDF interactions that were previously embedded
in :mod:`chat_interface`.  It provides resilient text normalisation and
layout helpers tailored for the chapter export workflow.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional
import textwrap
import unicodedata

try:  # pragma: no cover - import guarded for runtime availability
    from fpdf import FPDF
except ImportError as exc:  # pragma: no cover - optional dependency
    FPDF = None  # type: ignore[assignment]
    _FPDF_IMPORT_ERROR = exc
else:
    _FPDF_IMPORT_ERROR = None


class PDFExportError(RuntimeError):
    """Raised when exporting data to PDF fails."""


_PDF_LATIN1_REPLACEMENTS = {
    ord("\u2010"): "-",  # hyphen
    ord("\u2011"): "-",  # non-breaking hyphen
    ord("\u2012"): "-",  # figure dash
    ord("\u2013"): "-",  # en dash
    ord("\u2014"): "-",  # em dash
    ord("\u2015"): "-",  # horizontal bar
    ord("\u2212"): "-",  # minus sign
    ord("\u2018"): "'",  # left single quote
    ord("\u2019"): "'",  # right single quote / apostrophe
    ord("\u201A"): "'",  # single low-9 quote
    ord("\u201B"): "'",  # single high-reversed-9 quote
    ord("\u2032"): "'",  # prime
    ord("\u201C"): '"',  # left double quote
    ord("\u201D"): '"',  # right double quote
    ord("\u201E"): '"',  # double low-9 quote
    ord("\u00AB"): '"',  # left-pointing double angle quote
    ord("\u00BB"): '"',  # right-pointing double angle quote
    ord("\u2026"): "...",  # ellipsis
    ord("\u00A0"): " ",  # non-breaking space
    ord("\u2007"): " ",  # figure space
    ord("\u2009"): " ",  # thin space
    ord("\u202F"): " ",  # narrow no-break space
    ord("\u200A"): " ",  # hair space
    ord("\u2002"): " ",
    ord("\u2003"): " ",
    ord("\u2004"): " ",
    ord("\u2005"): " ",
    ord("\u2006"): " ",
    ord("\u2008"): " ",
    ord("\u2000"): " ",
    ord("\u2001"): " ",
    ord("\u200B"): "",  # zero-width space
    ord("\ufeff"): "",  # BOM
}


def _pdf_safe_text(text: str) -> str:
    """Return ``text`` normalised for the PDF Latin-1 core fonts."""

    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\t", " ")
    replaced = normalized.translate(_PDF_LATIN1_REPLACEMENTS)
    return replaced.encode("latin-1", "replace").decode("latin-1")


def _pdf_wrapped_text(text: str, *, width: int = 100) -> str:
    """Return ``text`` converted to a PDF-safe, manually wrapped string."""

    safe_text = _pdf_safe_text(text)
    if not safe_text:
        return ""

    wrapped_lines = []
    for raw_line in safe_text.splitlines():
        if not raw_line:
            wrapped_lines.append("")
            continue

        line_chunks = textwrap.wrap(
            raw_line,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        )

        wrapped_lines.extend(line_chunks or [""])

    return "\n".join(wrapped_lines)


def _safe_multi_cell(pdf: FPDF, width: float, height: float, text: str) -> None:
    """Render ``text`` within a multi-cell, retrying with a fresh line on failure."""

    sanitized = _pdf_wrapped_text(text)
    if not sanitized and not text:
        return

    try:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(width, height, sanitized)
    except Exception:
        pdf.ln(height)
        pdf.set_x(pdf.l_margin)
        try:
            pdf.multi_cell(width, height, sanitized)
        except Exception as exc:  # pragma: no cover - defensive
            raise PDFExportError(f"Failed to render PDF content: {exc}") from exc


def export_chapter_drafts_to_pdf(
    project: object,
    drafts: Iterable[object],
    *,
    output_path: Optional[Path] = None,
) -> Path:
    """Create a PDF containing ``drafts`` for ``project`` and return the path."""

    if FPDF is None:  # pragma: no cover - optional dependency
        raise PDFExportError(
            "Exporting chapters requires the fpdf2 package. Install it and try again."
        ) from _FPDF_IMPORT_ERROR

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_cell_margin(1)

    effective_width = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.add_page()
    pdf.set_font("Times", "B", 18)
    title_text = getattr(project, "name", None) or "Untitled Project"
    _safe_multi_cell(pdf, effective_width, 10, title_text)
    pdf.ln(4)

    outline_text = (getattr(project, "outline", "") or "").strip()
    if outline_text:
        pdf.set_font("Times", "", 12)
        _safe_multi_cell(pdf, effective_width, 6, "Project outline:")
        pdf.ln(2)
        _safe_multi_cell(pdf, effective_width, 6, outline_text)

    for draft in drafts:
        pdf.add_page()
        pdf.set_font("Times", "B", 14)
        header = (
            f"Act {getattr(draft, 'act_number', '?')} — Chapter {getattr(draft, 'chapter_number', '?')}: "
            f"{getattr(draft, 'title', None) or 'Untitled Chapter'}"
        )
        _safe_multi_cell(pdf, effective_width, 10, header)

        summary = (getattr(draft, "outline_summary", "") or "").strip()
        if summary:
            pdf.set_font("Times", "I", 11)
            _safe_multi_cell(pdf, effective_width, 6, f"Outline: {summary}")
            pdf.ln(2)

        pdf.set_font("Times", "", 12)
        content = (getattr(draft, "content", "") or "").strip() or "(No draft text available.)"
        for paragraph in content.split("\n\n"):
            cleaned = paragraph.strip()
            if not cleaned:
                continue
            _safe_multi_cell(pdf, effective_width, 6.5, cleaned)
            pdf.ln(1.5)

    resolved_path = output_path or Path("temp.pdf")
    resolved_path = Path(resolved_path)

    try:
        pdf.output(str(resolved_path))
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise PDFExportError(f"Unable to export PDF: {exc}") from exc

    return resolved_path


__all__ = ["PDFExportError", "export_chapter_drafts_to_pdf"]

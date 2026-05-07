"""
Filename templating for HTML2PDF batch export.

Pure functions — no Flask imports, no I/O state. Used by both single-row
PDF download and batch export to turn a `{{FIELD}}` template plus a row
lookup into a sanitized PDF filename.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Set

from data.excel_loader import load_csv_file, load_excel_file


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def render_filename(
    template: str,
    sender_row: Dict[str, Any],
    lookup_row: Dict[str, Any],
) -> str:
    """Render a filename from a `{{FIELD}}` template.

    Substitutes from the merged dict of sender_row + lookup_row (lookup wins on
    collision). Sanitizes to keep alnum and `._-` only. Strips a trailing
    `.pdf`/`.PDF` from the template before appending a single `.pdf` extension.

    Returns an empty string if the rendered stem is empty after sanitization
    so the caller can apply its own fallback.
    """
    merged = {**sender_row, **lookup_row}

    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = merged.get(key, "")
        return "" if value is None else str(value)

    rendered = _PLACEHOLDER_RE.sub(replace, template)

    # Drop any user-typed extension (case-insensitive) before sanitizing.
    if rendered.lower().endswith(".pdf"):
        rendered = rendered[:-4]

    # If the rendered stem is empty or only whitespace, return empty string.
    if not rendered.strip():
        return ""

    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in rendered)
    return f"{safe}.pdf"


def disambiguate(filename: str, seen: Set[str]) -> str:
    """If `filename` is already in `seen`, append `_2`, `_3`, … before `.pdf`.

    Caller is responsible for adding the chosen filename to `seen` afterwards.
    """
    if filename not in seen:
        return filename

    stem, ext = os.path.splitext(filename)
    counter = 2
    while f"{stem}_{counter}{ext}" in seen:
        counter += 1
    return f"{stem}_{counter}{ext}"


def build_naming_index(
    filepath: str,
    sheet_name: Optional[str],
    naming_key_col: str,
) -> Dict[str, Dict[str, Any]]:
    """Load a naming file (CSV or XLSX) and return {str(key_value): row_dict}.

    For XLSX, `sheet_name` selects the sheet (first sheet if None or unmatched).
    Raises ValueError if `naming_key_col` does not exist in the file's columns.
    If multiple rows share the same key value, the last row wins.
    """
    if filepath.lower().endswith(".csv"):
        rows = load_csv_file(filepath)
    else:
        sheets = load_excel_file(filepath)
        if not sheets:
            raise ValueError(f"Naming file has no sheets: {filepath}")
        if sheet_name and sheet_name in sheets:
            rows = sheets[sheet_name]
        else:
            rows = next(iter(sheets.values()))

    if not rows:
        return {}

    if naming_key_col not in rows[0]:
        raise ValueError(
            f"Naming key column {naming_key_col!r} not found in naming file. "
            f"Available columns: {list(rows[0].keys())}"
        )

    index: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw = row.get(naming_key_col)
        if raw is None or raw == "":
            continue
        key = str(raw).strip()
        if not key:
            continue
        index[key] = row
    return index

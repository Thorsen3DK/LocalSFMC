"""
Content-block helpers.

Provides directory accessors (bound to Flask app config) and a
content_block_loader callable for the AMPScript interpreter.
"""

import os
from pathlib import Path

from flask import current_app


def emails_dir() -> str:
    """Return the absolute path to the emails directory."""
    return current_app.config["EMAILS_DIR"]


def de_dir() -> str:
    """Return the absolute path to the data extensions directory."""
    return current_app.config["DATA_EXTENSIONS_DIR"]


def output_dir() -> str:
    """Return the absolute path to the output directory."""
    return current_app.config["OUTPUT_DIR"]


def content_block_loader(name: str) -> str:
    """Load a content block (HTML fragment) by name from the emails directory.

    The AMPScript interpreter calls this when it encounters a
    ContentBlockByName() function. It searches for a matching .html file
    in the emails folder.

    Returns the file contents as a string, or an empty string if not found.
    """
    edir = Path(emails_dir())

    # Try exact match first
    target = edir / name
    if target.is_file():
        return target.read_text(encoding="utf-8")

    # Try appending .html if not already present
    if not name.lower().endswith(".html"):
        target = edir / f"{name}.html"
        if target.is_file():
            return target.read_text(encoding="utf-8")

    # Search case-insensitively
    name_lower = name.lower()
    for f in edir.iterdir():
        if f.is_file() and (f.name.lower() == name_lower or f.stem.lower() == name_lower):
            return f.read_text(encoding="utf-8")

    return ""

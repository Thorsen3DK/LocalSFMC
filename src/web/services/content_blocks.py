"""
Content block loader — loads and renders content blocks from the emails directory.
"""

import html
import os

from flask import current_app

from ampscript.interpreter import render as ampscript_render


def emails_dir() -> str:
    return current_app.config["EMAILS_DIR"]


def de_dir() -> str:
    return current_app.config["DATA_EXTENSIONS_DIR"]


def output_dir() -> str:
    return current_app.config["OUTPUT_DIR"]


def content_block_loader(name: str, ctx) -> str:
    """Load and render a content block (another .html file from emails/)."""
    path = os.path.join(emails_dir(), name)
    if not os.path.isfile(path):
        return f'[ContentBlock not found: {html.escape(name)}]'
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    return ampscript_render(
        template_source=source,
        subscriber_row=ctx.subscriber_row,
        data_extensions=ctx.data_extensions,
        content_block_loader=content_block_loader,
    )

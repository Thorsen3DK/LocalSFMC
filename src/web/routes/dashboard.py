"""
Dashboard and output routes.
"""

from pathlib import Path

from flask import render_template, send_from_directory

from web.routes import bp
from web.services.content_blocks import output_dir


@bp.route("/")
def index():
    """Dashboard — tool selection landing page."""
    return render_template("dashboard.html")


@bp.route("/html2pdf")
def html2pdf():
    """HTML2PDF — step-by-step upload workflow."""
    return render_template("index.html")


@bp.route("/output")
def list_output():
    """List exported files."""
    out = Path(output_dir())
    if not out.is_dir():
        files = []
    else:
        files = sorted(f.name for f in out.glob("*.html"))
    return render_template("output.html", files=files)


@bp.route("/output/<filename>")
def serve_output(filename: str):
    """Serve an exported HTML file."""
    return send_from_directory(output_dir(), filename)

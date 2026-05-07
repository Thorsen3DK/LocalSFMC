"""
Dashboard and output routes.
"""

from pathlib import Path

from flask import jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename

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
    """List exported PDF files."""
    out = Path(output_dir())
    files: list[dict] = []
    if out.is_dir():
        for f in sorted(out.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    return render_template("output.html", files=files)


@bp.route("/output/<filename>")
def serve_output(filename: str):
    """Serve an exported file (PDF or HTML) from the output directory."""
    return send_from_directory(output_dir(), filename)


@bp.route("/output/<filename>", methods=["DELETE"])
def delete_output(filename: str):
    """Delete an exported file from the output directory."""
    safe = secure_filename(filename)
    target = Path(output_dir()) / safe
    if not target.is_file():
        return jsonify({"error": "Not found"}), 404
    target.unlink()
    return jsonify({"ok": True})

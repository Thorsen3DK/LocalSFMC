"""
File upload and management routes.
"""

import os
from pathlib import Path

from flask import jsonify, request
from werkzeug.utils import secure_filename

from data.excel_loader import load_csv_file
from web.routes import bp
from web.services.content_blocks import de_dir, emails_dir


ALLOWED_CSV_EXTENSIONS = {".csv"}
ALLOWED_HTML_EXTENSIONS = {".html", ".htm"}


def _allowed_csv(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_CSV_EXTENSIONS


def _allowed_html(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_HTML_EXTENSIONS


@bp.route("/upload/sender-csv", methods=["POST"])
def upload_sender_csv():
    """Step 1: Upload the Entry/Sender Data Extension CSV."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_csv(file.filename):
        return jsonify({"error": "Invalid file. Please upload a .csv file."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(de_dir(), filename)
    file.save(filepath)

    try:
        records = load_csv_file(filepath)
        columns = list(records[0].keys()) if records else []
        return jsonify({
            "success": True,
            "filename": filename,
            "rows": len(records),
            "columns": columns,
        })
    except Exception as e:
        return jsonify({"success": True, "filename": filename, "rows": 0, "columns": [], "warning": str(e)})


@bp.route("/upload/lookup-csv", methods=["POST"])
def upload_lookup_csv():
    """Step 2: Upload additional CSV files for lookups."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_csv(file.filename):
        return jsonify({"error": "Invalid file. Please upload a .csv file."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(de_dir(), filename)
    file.save(filepath)

    try:
        records = load_csv_file(filepath)
        columns = list(records[0].keys()) if records else []
        return jsonify({
            "success": True,
            "filename": filename,
            "rows": len(records),
            "columns": columns,
        })
    except Exception as e:
        return jsonify({"success": True, "filename": filename, "rows": 0, "columns": [], "warning": str(e)})


@bp.route("/upload/match-csv", methods=["POST"])
def upload_match_csv():
    """Step 3: Upload CSV with CPR/CUST_ID for matching."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_csv(file.filename):
        return jsonify({"error": "Invalid file. Please upload a .csv file."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(de_dir(), filename)
    file.save(filepath)

    try:
        records = load_csv_file(filepath)
        columns = list(records[0].keys()) if records else []
        return jsonify({
            "success": True,
            "filename": filename,
            "rows": len(records),
            "columns": columns,
        })
    except Exception as e:
        return jsonify({"success": True, "filename": filename, "rows": 0, "columns": [], "warning": str(e)})


@bp.route("/upload/email-html", methods=["POST"])
def upload_email_html():
    """Step 4: Upload the email HTML template."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_html(file.filename):
        return jsonify({"error": "Invalid file. Please upload an .html file."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(emails_dir(), filename)
    file.save(filepath)

    return jsonify({
        "success": True,
        "filename": filename,
    })


@bp.route("/upload/files", methods=["GET"])
def list_uploaded_files():
    """Return currently uploaded CSV and HTML files."""
    de = Path(de_dir())
    emails = Path(emails_dir())

    csv_files = sorted(f.name for f in de.glob("*.csv"))
    html_files = sorted(f.name for f in emails.glob("*.html"))

    return jsonify({"csv_files": csv_files, "html_files": html_files})


@bp.route("/upload/delete/<filename>", methods=["DELETE"])
def delete_uploaded_file(filename: str):
    """Delete an uploaded file."""
    filename = secure_filename(filename)

    # Check in data_extensions
    filepath = os.path.join(de_dir(), filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        return jsonify({"success": True})

    # Check in emails
    filepath = os.path.join(emails_dir(), filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        return jsonify({"success": True})

    return jsonify({"error": "File not found"}), 404

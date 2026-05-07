"""
File upload and management routes.

Data Extensions are split into two semantic types:

* ``sender``  — drives the send list (one row per subscriber). Selectable as
  the data source on the preview page.
* ``mapping`` — lookup tables joined by AMPScript at render time.

Both kinds live in the same directory; the type is tracked in a sidecar
``_de_types.json`` so the listing endpoints can split them. Files without an
entry default to ``mapping`` — sender membership has to be explicit.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from flask import jsonify, request
from werkzeug.utils import secure_filename

from data.excel_loader import load_csv_file
from web.routes import bp
from web.services.content_blocks import de_dir, emails_dir


ALLOWED_CSV_EXTENSIONS = {".csv"}
ALLOWED_DATA_EXTENSIONS = {".csv", ".xlsx"}
ALLOWED_HTML_EXTENSIONS = {".html", ".htm"}

DE_TYPES_FILE = "_de_types.json"


def _allowed_csv(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_CSV_EXTENSIONS


def _allowed_data(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_DATA_EXTENSIONS


def _allowed_html(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_HTML_EXTENSIONS


# ---------- DE type sidecar ----------

def _de_types_path() -> str:
    return os.path.join(de_dir(), DE_TYPES_FILE)


def _load_de_types() -> Dict[str, str]:
    path = _de_types_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_de_types(types: Dict[str, str]) -> None:
    path = _de_types_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(types, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _set_de_type(filename: str, de_type: str) -> None:
    types = _load_de_types()
    types[filename] = de_type
    _save_de_types(types)


def _unset_de_type(filename: str) -> None:
    types = _load_de_types()
    if filename in types:
        del types[filename]
        _save_de_types(types)


def _classify_de_files(files: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """Split files into (sender, mapping, naming). Untracked defaults to mapping."""
    types = _load_de_types()
    sender: List[str] = []
    mapping: List[str] = []
    naming: List[str] = []
    for f in files:
        t = types.get(f)
        if t == "sender":
            sender.append(f)
        elif t == "naming":
            naming.append(f)
        else:
            mapping.append(f)
    return sender, mapping, naming


# ---------- Upload endpoints ----------

def _save_data_upload(de_type: str):
    """Common handler for sender/mapping DE uploads."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_data(file.filename):
        return jsonify({"error": "Invalid file. Please upload a .csv or .xlsx file."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(de_dir(), filename)
    file.save(filepath)
    _set_de_type(filename, de_type)

    if filename.lower().endswith(".xlsx"):
        return jsonify({"success": True, "filename": filename, "type": de_type})

    try:
        records = load_csv_file(filepath)
        columns = list(records[0].keys()) if records else []
        return jsonify({
            "success": True,
            "filename": filename,
            "type": de_type,
            "rows": len(records),
            "columns": columns,
        })
    except Exception as e:
        return jsonify({
            "success": True,
            "filename": filename,
            "type": de_type,
            "rows": 0,
            "columns": [],
            "warning": str(e),
        })


@bp.route("/upload/sender-csv", methods=["POST"])
def upload_sender_csv():
    """Upload a sender Data Extension (CSV or XLSX)."""
    return _save_data_upload("sender")


@bp.route("/upload/lookup-csv", methods=["POST"])
def upload_lookup_csv():
    """Upload a mapping/lookup Data Extension (CSV or XLSX)."""
    return _save_data_upload("mapping")


@bp.route("/upload/naming-csv", methods=["POST"])
def upload_naming_csv():
    """Upload a naming-convention Data Extension (CSV or XLSX)."""
    return _save_data_upload("naming")


@bp.route("/upload/match-csv", methods=["POST"])
def upload_match_csv():
    """Legacy endpoint — accepts a CSV match file. Treated as a mapping DE."""
    return _save_data_upload("mapping")


@bp.route("/upload/email-html", methods=["POST"])
def upload_email_html():
    """Upload the email HTML template."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_html(file.filename):
        return jsonify({"error": "Invalid file. Please upload an .html file."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(emails_dir(), filename)
    file.save(filepath)

    return jsonify({"success": True, "filename": filename})


@bp.route("/upload/files", methods=["GET"])
def list_uploaded_files():
    """Return currently uploaded data and template files, split by DE type."""
    de = Path(de_dir())
    emails = Path(emails_dir())

    csv_files = sorted(f.name for f in de.glob("*.csv"))
    xlsx_files = sorted(f.name for f in de.glob("*.xlsx") if not f.name.startswith("~$"))
    data_files = sorted(csv_files + xlsx_files)

    sender_files, mapping_files, naming_files = _classify_de_files(data_files)
    html_files = sorted(f.name for f in emails.glob("*.html"))

    return jsonify({
        "csv_files": csv_files,
        "xlsx_files": xlsx_files,
        "data_files": data_files,
        "sender_files": sender_files,
        "mapping_files": mapping_files,
        "naming_files": naming_files,
        "html_files": html_files,
    })


@bp.route("/upload/delete/<filename>", methods=["DELETE"])
def delete_uploaded_file(filename: str):
    """Delete an uploaded file (data extension or email template)."""
    filename = secure_filename(filename)

    de_path = os.path.join(de_dir(), filename)
    if os.path.isfile(de_path):
        os.remove(de_path)
        _unset_de_type(filename)
        return jsonify({"success": True})

    email_path = os.path.join(emails_dir(), filename)
    if os.path.isfile(email_path):
        os.remove(email_path)
        return jsonify({"success": True})

    return jsonify({"error": "File not found"}), 404

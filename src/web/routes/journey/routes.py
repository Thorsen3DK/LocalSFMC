"""
Journey Name Generator API routes.

Endpoints:
  GET  /journey              — Serve the Journey Generator page
  GET  /journey/api/lookup   — Get lookup values
  POST /journey/api/lookup   — Update lookup values (editable fields only)
  GET  /journey/api/history  — Get all generated journey names
  POST /journey/api/generate — Generate and save a new journey name
  DELETE /journey/api/history/<idx> — Delete a history entry
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request

from web.routes import bp

DATA_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parents[3] / "data" / "journey"
LOOKUP_FILE = DATA_DIR / "lookup_values.json"
HISTORY_FILE = DATA_DIR / "history.json"

# Fields that cannot be edited by users
LOCKED_FIELDS = ("month", "day")


def _read_json(path: Path):
    """Read and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data):
    """Write data to a JSON file atomically."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------- Page route ----------

@bp.route("/journey")
def journey_page():
    """Journey Name Generator UI."""
    return render_template("journey.html")


# ---------- Lookup API ----------

@bp.route("/journey/api/lookup", methods=["GET"])
def journey_get_lookup():
    """Return all lookup values."""
    data = _read_json(LOOKUP_FILE)
    return jsonify(data)


@bp.route("/journey/api/lookup", methods=["POST"])
def journey_update_lookup():
    """Update lookup values. Only non-locked fields can be modified."""
    incoming = request.get_json()
    if not incoming or not isinstance(incoming, dict):
        return jsonify({"error": "Invalid payload"}), 400

    current = _read_json(LOOKUP_FILE)

    for key, values in incoming.items():
        if key in LOCKED_FIELDS:
            continue
        if not isinstance(values, list):
            return jsonify({"error": f"Field '{key}' must be an array"}), 400
        # Sanitize: strip whitespace, uppercase, remove empties
        cleaned = []
        for v in values:
            v = str(v).strip().upper()
            if v and v not in cleaned:
                cleaned.append(v)
        current[key] = cleaned

    _write_json(LOOKUP_FILE, current)
    return jsonify({"ok": True, "data": current})


# ---------- History API ----------

@bp.route("/journey/api/history", methods=["GET"])
def journey_get_history():
    """Return all generated journey names."""
    data = _read_json(HISTORY_FILE)
    return jsonify(data)


@bp.route("/journey/api/generate", methods=["POST"])
def journey_generate():
    """Validate, generate, and store a new journey name."""
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Invalid payload"}), 400

    lookup = _read_json(LOOKUP_FILE)

    # Required fields (dropdown-based)
    required_fields = ["campaignType", "campaignTrigger", "timing", "target", "year", "month", "day"]
    errors = []

    for field in required_fields:
        val = payload.get(field, "").strip()
        if not val:
            errors.append(f"{field} er påkrævet")
        elif val not in lookup.get(field, []):
            errors.append(f"Ugyldig værdi for {field}: {val}")

    # Description is required (free text)
    description = payload.get("description", "").strip()
    if not description:
        errors.append("description er påkrævet")

    if errors:
        return jsonify({"error": "Validering fejlede", "details": errors}), 400

    # Identifier: use X if empty
    identifier = payload.get("identifier", "").strip() or "X"

    # Build journey name
    date_part = payload["year"].strip() + payload["month"].strip() + payload["day"].strip()
    journey_name = "_".join([
        payload["campaignType"].strip(),
        payload["campaignTrigger"].strip(),
        payload["timing"].strip(),
        payload["target"].strip(),
        date_part,
        identifier,
        description,
    ])

    # Duplicate check
    history = _read_json(HISTORY_FILE)
    if any(entry["name"] == journey_name for entry in history):
        return jsonify({"error": "Duplikat: dette journey name eksisterer allerede", "name": journey_name}), 409

    # Save
    entry = {
        "name": journey_name,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    history.insert(0, entry)
    _write_json(HISTORY_FILE, history)

    return jsonify({"ok": True, "name": journey_name, "entry": entry}), 201


@bp.route("/journey/api/history/<int:idx>", methods=["DELETE"])
def journey_delete_history(idx: int):
    """Delete a history entry by index."""
    history = _read_json(HISTORY_FILE)
    if idx < 0 or idx >= len(history):
        return jsonify({"error": "Ugyldigt indeks"}), 404

    removed = history.pop(idx)
    _write_json(HISTORY_FILE, history)
    return jsonify({"ok": True, "removed": removed})

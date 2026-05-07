"""
Journey Name Generator routes.

Endpoints
---------
GET  /journey                       — Page UI
GET  /journey/api/lookup            — Read lookup values
POST /journey/api/lookup            — Update lookup values (locked fields ignored)
GET  /journey/api/history           — Read generated journey-name history
POST /journey/api/generate          — Validate, generate, and persist a journey name
DELETE /journey/api/history/<idx>   — Remove a history entry by index
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

from flask import current_app, jsonify, render_template, request

from web.routes import bp


# Fields whose values are derived (date) and must not be edited via the UI.
LOCKED_FIELDS = ("month", "day")

# Fields used to compose the journey name.
NAME_FIELDS = ("campaignType", "campaignTrigger", "timing", "target", "year", "month", "day")


def _data_dir() -> str:
    return current_app.config["JOURNEY_DATA_DIR"]


def _lookup_path() -> str:
    return os.path.join(_data_dir(), "lookup_values.json")


def _history_path() -> str:
    return os.path.join(_data_dir(), "history.json")


def _read_json(path: str, default: Any) -> Any:
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Any) -> None:
    """Atomically write JSON to ``path``."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- Page ----------

@bp.route("/journey")
def journey_page():
    """Journey Name Generator UI."""
    return render_template("journey.html")


# ---------- Lookup API ----------

@bp.route("/journey/api/lookup", methods=["GET"])
def journey_get_lookup():
    return jsonify(_read_json(_lookup_path(), {}))


@bp.route("/journey/api/lookup", methods=["POST"])
def journey_update_lookup():
    """Update editable lookup fields. Locked fields in the payload are ignored."""
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify({"error": "Invalid payload"}), 400

    current: Dict[str, List[str]] = _read_json(_lookup_path(), {})

    for key, values in incoming.items():
        if key in LOCKED_FIELDS:
            continue
        if not isinstance(values, list):
            return jsonify({"error": f"Field '{key}' must be an array"}), 400

        cleaned: List[str] = []
        for v in values:
            v = str(v).strip().upper()
            if v and v not in cleaned:
                cleaned.append(v)
        current[key] = cleaned

    _write_json(_lookup_path(), current)
    return jsonify({"ok": True, "data": current})


# ---------- History / generation ----------

@bp.route("/journey/api/history", methods=["GET"])
def journey_get_history():
    return jsonify(_read_json(_history_path(), []))


@bp.route("/journey/api/generate", methods=["POST"])
def journey_generate():
    """Validate, build, and persist a new journey name."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid payload"}), 400

    lookup: Dict[str, List[str]] = _read_json(_lookup_path(), {})
    errors: List[str] = []

    for field in NAME_FIELDS:
        val = str(payload.get(field, "")).strip()
        if not val:
            errors.append(f"{field} er påkrævet")
        elif val not in lookup.get(field, []):
            errors.append(f"Ugyldig værdi for {field}: {val}")

    description = str(payload.get("description", "")).strip()
    if not description:
        errors.append("description er påkrævet")

    if errors:
        return jsonify({"error": "Validering fejlede", "details": errors}), 400

    identifier = str(payload.get("identifier", "")).strip() or "X"
    date_part = str(payload["year"]).strip() + str(payload["month"]).strip() + str(payload["day"]).strip()

    journey_name = "_".join([
        str(payload["campaignType"]).strip(),
        str(payload["campaignTrigger"]).strip(),
        str(payload["timing"]).strip(),
        str(payload["target"]).strip(),
        date_part,
        identifier,
        description,
    ])

    history: List[Dict[str, str]] = _read_json(_history_path(), [])
    if any(entry.get("name") == journey_name for entry in history):
        return jsonify({
            "error": "Duplikat: dette journey name eksisterer allerede",
            "name": journey_name,
        }), 409

    entry = {"name": journey_name, "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    history.insert(0, entry)
    _write_json(_history_path(), history)

    return jsonify({"ok": True, "name": journey_name, "entry": entry}), 201


@bp.route("/journey/api/history/<int:idx>", methods=["DELETE"])
def journey_delete_history(idx: int):
    history: List[Dict[str, str]] = _read_json(_history_path(), [])
    if idx < 0 or idx >= len(history):
        return jsonify({"error": "Ugyldigt indeks"}), 404

    removed = history.pop(idx)
    _write_json(_history_path(), history)
    return jsonify({"ok": True, "removed": removed})

"""
Block Name Generator routes.

Endpoints
---------
GET  /blockname                       — Page UI
GET  /blockname/api/lookup            — Read lookup values
POST /blockname/api/lookup            — Update lookup values
GET  /blockname/api/history           — Read generated block-name history
POST /blockname/api/generate          — Validate, generate, and persist a block name
DELETE /blockname/api/history/<idx>   — Remove a history entry by index
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

from flask import current_app, jsonify, render_template, request

from web.routes import bp


# Fields used to compose the block name (lookup-based).
LOOKUP_FIELDS = (
    "topProductCategory",
    "secondProductCategory",
    "specificProduct1",
    "specificProduct2",
    "specificProduct3",
    "specificProduct4",
    "price",
    "contentType",
)


def _data_dir() -> str:
    return current_app.config["BLOCKNAME_DATA_DIR"]


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


def _next_block_id(history: List[Dict[str, Any]]) -> int:
    """Return the next sequential block ID (max existing + 1, starting at 100)."""
    if not history:
        return 100
    max_id = max(entry.get("blockId", 0) for entry in history)
    return max(max_id + 1, 100)


# ---------- Page ----------

@bp.route("/blockname")
def blockname_page():
    """Block Name Generator UI."""
    return render_template("blockname.html")


# ---------- Lookup API ----------

@bp.route("/blockname/api/lookup", methods=["GET"])
def blockname_get_lookup():
    data = _read_json(_lookup_path(), {})
    # Sort alphabetically, keeping "X" first
    for key in data:
        vals = data[key]
        has_x = "X" in vals
        rest = sorted([v for v in vals if v != "X"], key=str.upper)
        data[key] = (["X"] if has_x else []) + rest
    return jsonify(data)


@bp.route("/blockname/api/lookup", methods=["POST"])
def blockname_update_lookup():
    """Update lookup fields."""
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify({"error": "Invalid payload"}), 400

    current: Dict[str, List[str]] = _read_json(_lookup_path(), {})

    for key, values in incoming.items():
        if not isinstance(values, list):
            return jsonify({"error": f"Field '{key}' must be an array"}), 400

        cleaned: List[str] = []
        for v in values:
            v = str(v).strip().upper()
            if " " in v:
                return jsonify({"error": f"Mellemrum er ikke tilladt i lookup-værdier: '{v}'"}), 400
            if v and v not in cleaned:
                cleaned.append(v)
        current[key] = cleaned

    _write_json(_lookup_path(), current)
    return jsonify({"ok": True, "data": current})


# ---------- History / generation ----------

@bp.route("/blockname/api/history", methods=["GET"])
def blockname_get_history():
    return jsonify(_read_json(_history_path(), []))


@bp.route("/blockname/api/generate", methods=["POST"])
def blockname_generate():
    """Validate, build, and persist a new block name."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid payload"}), 400

    lookup: Dict[str, List[str]] = _read_json(_lookup_path(), {})
    errors: List[str] = []

    # Validate lookup fields — specificProduct1-4 share the "specificProduct" list
    for field in LOOKUP_FIELDS:
        val = str(payload.get(field, "")).strip()
        if not val:
            errors.append(f"{field} er påkrævet")
        else:
            # Map specificProduct1-4 to the shared "specificProduct" lookup key
            lookup_key = field
            if field.startswith("specificProduct"):
                lookup_key = "specificProduct"
            if val not in lookup.get(lookup_key, []):
                errors.append(f"Ugyldig værdi for {field}: {val}")

    if errors:
        return jsonify({"error": "Validering fejlede", "details": errors}), 400

    # Get values
    top = str(payload["topProductCategory"]).strip()
    second = str(payload["secondProductCategory"]).strip()
    spec1 = str(payload["specificProduct1"]).strip()
    spec2 = str(payload["specificProduct2"]).strip()
    spec3 = str(payload["specificProduct3"]).strip()
    spec4 = str(payload["specificProduct4"]).strip()
    price = str(payload["price"]).strip()
    content_type = str(payload["contentType"]).strip()
    notes = str(payload.get("notes", "")).strip()

    # Notes: replace spaces with hyphens, or "X" if empty
    notes_part = notes.replace(" ", "-") if notes else "X"

    # Block ID: auto-increment
    history: List[Dict[str, Any]] = _read_json(_history_path(), [])
    block_id = _next_block_id(history)

    # Build name:
    # BLOCKNAME_{TOP}_{SECOND}_{SPEC1}{SPEC2}{SPEC3}{SPEC4}_{PRICE}_{CONTENTTYPE}_{NOTES}_BLOCKID_{ID}
    block_name = "_".join([
        "BLOCKNAME",
        top,
        second,
        f"{spec1}{spec2}{spec3}{spec4}",
        price,
        content_type,
        notes_part,
        "BLOCKID",
        str(block_id),
    ])

    # Check for duplicates
    if any(entry.get("name") == block_name for entry in history):
        return jsonify({
            "error": "Duplikat: dette block name eksisterer allerede",
            "name": block_name,
        }), 409

    entry = {
        "name": block_name,
        "blockId": block_id,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    history.insert(0, entry)
    _write_json(_history_path(), history)

    return jsonify({"ok": True, "name": block_name, "blockId": block_id})


@bp.route("/blockname/api/history/<int:idx>", methods=["DELETE"])
def blockname_delete_history(idx: int):
    """Remove a history entry by index."""
    history: List[Dict[str, str]] = _read_json(_history_path(), [])
    if idx < 0 or idx >= len(history):
        return jsonify({"error": "Index out of range"}), 404
    # If the deleted entry belongs to a group, remove it from the group
    removed = history.pop(idx)
    group_id = removed.get("groupId")
    if group_id:
        remaining = [e for e in history if e.get("groupId") == group_id]
        # If only one member left, dissolve the group
        if len(remaining) <= 1:
            for e in remaining:
                e.pop("groupId", None)
                e.pop("isMaster", None)
    _write_json(_history_path(), history)
    return jsonify({"ok": True})


# ---------- Grouping (Master Block) ----------

def _next_group_id(history: List[Dict[str, Any]]) -> int:
    """Return the next sequential group ID."""
    max_gid = 0
    for entry in history:
        gid = entry.get("groupId", 0)
        if isinstance(gid, int) and gid > max_gid:
            max_gid = gid
    return max_gid + 1


@bp.route("/blockname/api/group", methods=["POST"])
def blockname_create_group():
    """Link blocks together with a master.

    Payload: { "blockIds": [1, 3, 5], "masterId": 3 }
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid payload"}), 400

    block_ids = payload.get("blockIds", [])
    master_id = payload.get("masterId")

    if not isinstance(block_ids, list) or len(block_ids) < 2:
        return jsonify({"error": "Mindst 2 blocks skal vælges"}), 400
    if master_id not in block_ids:
        return jsonify({"error": "Master block skal være en af de valgte blocks"}), 400

    history: List[Dict[str, Any]] = _read_json(_history_path(), [])

    # Check if any selected block is already in a group
    already_grouped = []
    for entry in history:
        if entry.get("blockId") in block_ids and entry.get("groupId"):
            already_grouped.append(entry["blockId"])
    if already_grouped:
        ids_str = ", ".join(str(b) for b in already_grouped)
        return jsonify({
            "error": f"Block(s) {ids_str} er allerede i en gruppe. Ophæv gruppen først.",
        }), 409

    group_id = _next_group_id(history)

    matched = 0
    for entry in history:
        if entry.get("blockId") in block_ids:
            # Remove from any previous group
            entry.pop("groupId", None)
            entry.pop("isMaster", None)
            # Assign new group
            entry["groupId"] = group_id
            if entry["blockId"] == master_id:
                entry["isMaster"] = True
            matched += 1

    if matched < 2:
        return jsonify({"error": "Ikke nok blocks fundet i historikken"}), 400

    _write_json(_history_path(), history)
    return jsonify({"ok": True, "groupId": group_id})


@bp.route("/blockname/api/group/<int:group_id>", methods=["DELETE"])
def blockname_delete_group(group_id: int):
    """Dissolve a group — unlink all blocks."""
    history: List[Dict[str, Any]] = _read_json(_history_path(), [])
    for entry in history:
        if entry.get("groupId") == group_id:
            entry.pop("groupId", None)
            entry.pop("isMaster", None)
    _write_json(_history_path(), history)
    return jsonify({"ok": True})

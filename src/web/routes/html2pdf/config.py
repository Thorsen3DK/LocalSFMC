"""
Configuration save/load routes.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from flask import current_app, jsonify, request

from web.routes import bp


CONFIGS_FILE = "saved_configs.json"


def _configs_path() -> str:
    # saved_configs.json lives at project root (two levels above content/data_extensions)
    return os.path.join(current_app.config["DATA_EXTENSIONS_DIR"], "..", "..", CONFIGS_FILE)


def _load_configs() -> List[Dict[str, Any]]:
    path = _configs_path()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_configs(configs: List[Dict[str, Any]]):
    path = _configs_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2, ensure_ascii=False)


@bp.route("/configs", methods=["GET"])
def list_configs():
    """List saved configurations."""
    return jsonify(_load_configs())


@bp.route("/configs", methods=["POST"])
def save_config():
    """Save a configuration."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    config = {
        "name": data["name"],
        "sender_csv": data.get("sender_csv"),
        "lookup_csvs": data.get("lookup_csvs", []),
        "match_csv": data.get("match_csv"),
        "email_html": data.get("email_html"),
    }

    configs = _load_configs()
    configs = [c for c in configs if c["name"] != config["name"]]
    configs.append(config)
    _save_configs(configs)

    return jsonify({"success": True, "config": config})


@bp.route("/configs/<name>", methods=["DELETE"])
def delete_config(name: str):
    """Delete a saved configuration."""
    configs = _load_configs()
    configs = [c for c in configs if c["name"] != name]
    _save_configs(configs)
    return jsonify({"success": True})

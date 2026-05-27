"""
Content Search routes.

Endpoints
---------
GET  /search              — Content Search page UI
GET  /search/api/search   — Search SFMC Content Builder for a given string
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from flask import jsonify, render_template, request

from web.routes import bp
from web.services.sfmc_api import get_sfmc_client


def _extract_snippets(content: str, query: str, max_snippets: int = 3) -> List[Dict[str, Any]]:
    """Extract context snippets around matches in content."""
    if not content:
        return []

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches = list(pattern.finditer(content))
    if not matches:
        return []

    snippets: List[Dict[str, Any]] = []
    for match in matches[:max_snippets]:
        start = max(0, match.start() - 80)
        end = min(len(content), match.end() + 80)
        snippet = content[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(content):
            snippet = snippet + "…"
        snippets.append({"context": snippet})

    return snippets


# ---------- Page ----------

@bp.route("/search")
def search_page():
    """Content Search UI."""
    return render_template("search.html")


# ---------- API ----------

@bp.route("/search/api/search", methods=["GET"])
def search_api():
    """Search SFMC Content Builder for a query string.

    Query params:
        q        — the search string (required)
        page     — page number (default: 1)
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    if len(query) < 2:
        return jsonify({"error": "Søgestrengen skal være mindst 2 tegn"}), 400

    page = request.args.get("page", "1")
    try:
        page = max(1, int(page))
    except ValueError:
        page = 1

    try:
        client = get_sfmc_client()
    except ValueError as e:
        return jsonify({"error": str(e)}), 500

    try:
        data = client.search_content(query, page=page, page_size=50)
    except Exception as e:
        return jsonify({"error": f"SFMC API-fejl: {str(e)}"}), 502

    # Transform results for the frontend
    items = data.get("items", [])
    total_count = data.get("count", 0)

    results: List[Dict[str, Any]] = []
    for item in items:
        # Extract content from the asset (could be in views.html.content, content, etc.)
        content_text = ""
        views = item.get("views", {})
        if views:
            html_view = views.get("html", {})
            if isinstance(html_view, dict):
                content_text = html_view.get("content", "")
            subjectline = views.get("subjectline", {})
            if isinstance(subjectline, dict):
                content_text += "\n" + subjectline.get("content", "")
        if not content_text:
            content_text = item.get("content", "") or ""

        # Get asset type info
        asset_type = item.get("assetType", {})
        type_name = asset_type.get("name", "Unknown") if isinstance(asset_type, dict) else "Unknown"

        # Category / folder path
        category = item.get("category", {})
        folder_name = category.get("name", "") if isinstance(category, dict) else ""

        snippets = _extract_snippets(content_text, query)

        results.append({
            "id": item.get("id"),
            "name": item.get("name", "Untitled"),
            "assetType": type_name,
            "folder": folder_name,
            "modifiedDate": item.get("modifiedDate", ""),
            "matchCount": len(re.findall(re.escape(query), content_text, re.IGNORECASE)) if content_text else 0,
            "snippets": snippets,
        })

    return jsonify({
        "query": query,
        "page": page,
        "pageSize": 50,
        "totalCount": total_count,
        "totalFiles": len(results),
        "results": results,
    })

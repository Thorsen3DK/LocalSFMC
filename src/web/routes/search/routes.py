"""
SFMC Content Search routes.
"""

from __future__ import annotations

import json
import queue
import threading

from flask import jsonify, render_template, request, Response

from sfmc.client import SFMCClient
from web.routes import bp


@bp.route("/search")
def search_page():
    """Search tool — find emails/content blocks in SFMC by keyword."""
    sfmc = SFMCClient()
    return render_template("search.html", configured=sfmc.is_configured())


@bp.route("/search/query", methods=["POST"])
def search_query():
    """AJAX endpoint — search SFMC Content Builder assets with SSE progress."""
    sfmc = SFMCClient()
    if not sfmc.is_configured():
        return jsonify({"error": "SFMC API credentials not configured. Set SFMC_CLIENT_ID, SFMC_CLIENT_SECRET, SFMC_AUTH_BASE_URI, and SFMC_REST_BASE_URI environment variables."}), 400

    data = request.get_json()
    if not data or not data.get("query", "").strip():
        return jsonify({"error": "Search query is required"}), 400

    query = data["query"].strip()
    asset_types = data.get("asset_types")
    page = int(data.get("page", 1))
    sort_field = data.get("sort_field", "modifiedDate")
    sort_direction = data.get("sort_direction", "DESC")
    include_journeys = data.get("include_journeys", False)
    include_mapping = data.get("include_mapping", False)

    allowed_sort = {"modifiedDate", "createdDate", "name"}
    if sort_field not in allowed_sort:
        sort_field = "modifiedDate"
    if sort_direction not in ("ASC", "DESC"):
        sort_direction = "DESC"

    event_queue = queue.Queue()

    def run_search():
        def on_progress(scanned, total, matches):
            event_queue.put({"type": "progress", "scanned": scanned, "total": total, "matches": matches})

        try:
            results = sfmc.search_assets(
                query=query,
                asset_types=asset_types,
                page=page,
                sort_field=sort_field,
                sort_direction=sort_direction,
                progress_callback=on_progress,
            )

            # Separate content blocks from emails
            content_blocks = [
                item for item in results["items"]
                if item.get("assetType", "").lower() in ("htmlblock", "codesnippetblock", "contentblock", "freeformblock", "textblock")
            ]
            email_items = [
                item for item in results["items"]
                if item.get("assetType", "").lower() in ("htmlemail", "templatebasedemail")
            ]

            # Map content blocks -> parent emails
            if include_mapping and content_blocks:
                def on_mapping_progress(scanned, total):
                    event_queue.put({
                        "type": "progress_mapping",
                        "scanned": scanned,
                        "total": total,
                    })

                cb_email_map = sfmc.get_emails_for_content_blocks(
                    content_blocks, progress_callback=on_mapping_progress
                )
                for item in results["items"]:
                    if item["id"] in cb_email_map:
                        item["parentEmails"] = cb_email_map[item["id"]]

                # Collect parent email IDs for journey lookup
                parent_email_ids = set()
                for emails in cb_email_map.values():
                    for em in emails:
                        if em.get("id"):
                            parent_email_ids.add(em["id"])

                # Always look up journeys when mapping is on
                # (the whole point is to see the full chain)
                all_email_ids = [item["id"] for item in email_items if item["id"]]
                all_email_ids.extend(parent_email_ids)
                if all_email_ids:
                    journey_map = sfmc.get_journeys_for_assets(all_email_ids)
                    # Attach journeys to email results
                    for item in results["items"]:
                        item["journeys"] = journey_map.get(item["id"], [])
                    # Attach journeys to parent emails of content blocks
                    for item in results["items"]:
                        if "parentEmails" in item:
                            for em in item["parentEmails"]:
                                em["journeys"] = journey_map.get(em["id"], [])
            elif include_journeys and results["items"]:
                asset_ids = [item["id"] for item in results["items"] if item["id"]]
                journey_map = sfmc.get_journeys_for_assets(asset_ids)
                for item in results["items"]:
                    item["journeys"] = journey_map.get(item["id"], [])

            event_queue.put({"type": "result", "data": results})
        except Exception as e:
            event_queue.put({"type": "error", "error": str(e)})

    thread = threading.Thread(target=run_search, daemon=True)
    thread.start()

    def generate():
        while True:
            try:
                event = event_queue.get(timeout=300)
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'error', 'error': 'Search timed out'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("result", "error"):
                break

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    })

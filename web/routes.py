"""
Flask routes for LocalSFMC web UI.
"""

from __future__ import annotations

import os
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint, current_app, jsonify, make_response, redirect,
    render_template, request, Response, send_from_directory, url_for,
)
from werkzeug.utils import secure_filename

from ampscript.interpreter import render as ampscript_render, render_batch_stream
from data.excel_loader import (
    get_send_list, list_excel_files, load_all, load_excel_file,
    load_de_mapping, save_de_mapping, list_all_de_names,
    load_csv_file,
)
from sfmc.client import SFMCClient

bp = Blueprint("main", __name__)


def _emails_dir() -> str:
    return current_app.config["EMAILS_DIR"]


def _de_dir() -> str:
    return current_app.config["DATA_EXTENSIONS_DIR"]


def _output_dir() -> str:
    return current_app.config["OUTPUT_DIR"]


def _list_templates() -> List[str]:
    """List .html files in the emails directory."""
    edir = Path(_emails_dir())
    if not edir.is_dir():
        return []
    return sorted(f.name for f in edir.glob("*.html"))


def _content_block_loader(name: str, ctx) -> str:
    """Load and render a content block (another .html file from emails/)."""
    path = os.path.join(_emails_dir(), name)
    if not os.path.isfile(path):
        return f'[ContentBlock not found: {html.escape(name)}]'
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    # Render the content block with the same context
    return ampscript_render(
        template_source=source,
        subscriber_row=ctx.subscriber_row,
        data_extensions=ctx.data_extensions,
        content_block_loader=_content_block_loader,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    """Dashboard — tool selection landing page."""
    return render_template("dashboard.html")


@bp.route("/html2pdf")
def html2pdf():
    """HTML2PDF — step-by-step upload workflow."""
    return render_template("index.html")


@bp.route("/preview/<template_name>")
def preview(template_name: str):
    """Preview a single subscriber render of an email template."""
    # Validate template exists
    template_path = os.path.join(_emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return "Template not found", 404

    # Get parameters
    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return "No Excel file specified", 400

    # Load data
    send_list = get_send_list(_de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return "No data rows found", 400

    row_index = max(0, min(row_index, len(send_list) - 1))
    subscriber_row = send_list[row_index]

    # Load all DEs for Lookup functions
    all_des = load_all(_de_dir())

    # Read template
    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    # Render
    rendered_html = ampscript_render(
        template_source=template_source,
        subscriber_row=subscriber_row,
        data_extensions=all_des,
        content_block_loader=_content_block_loader,
    )

    return render_template(
        "preview.html",
        template_name=template_name,
        rendered_html=rendered_html,
        subscriber_row=subscriber_row,
        row_index=row_index,
        total_rows=len(send_list),
        excel_file=excel_file,
        sheet_name=sheet_name,
    )


@bp.route("/preview/<template_name>/raw")
def preview_raw(template_name: str):
    """Return just the rendered HTML (for iframe embedding)."""
    template_path = os.path.join(_emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return "Template not found", 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return "No Excel file specified", 400

    send_list = get_send_list(_de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return "No data rows found", 400

    row_index = max(0, min(row_index, len(send_list) - 1))
    subscriber_row = send_list[row_index]
    all_des = load_all(_de_dir())

    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    rendered_html = ampscript_render(
        template_source=template_source,
        subscriber_row=subscriber_row,
        data_extensions=all_des,
        content_block_loader=_content_block_loader,
    )

    return rendered_html


def _html_to_pdf(html_content: str) -> bytes:
    """Convert HTML to PDF using headless Chromium via Playwright."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.set_content(html_content, wait_until="load", timeout=15000)
            page.evaluate(
                "() => Promise.race(["
                "document.fonts.ready,"
                "new Promise(r => setTimeout(r, 5000))"
                "])"
            )
        except Exception:
            pass
        height = page.evaluate("() => document.documentElement.scrollHeight")
        pdf_bytes = page.pdf(
            width="8.5in",
            height=f"{height + 20}px",
            print_background=True,
        )
        browser.close()
    return pdf_bytes


@bp.route("/preview/<template_name>/pdf")
def preview_pdf(template_name: str):
    """Generate a PDF of the rendered email and return it as a download."""
    template_path = os.path.join(_emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return "Template not found", 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return "No Excel file specified", 400

    send_list = get_send_list(_de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return "No data rows found", 400

    row_index = max(0, min(row_index, len(send_list) - 1))
    subscriber_row = send_list[row_index]
    all_des = load_all(_de_dir())

    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    rendered_html = ampscript_render(
        template_source=template_source,
        subscriber_row=subscriber_row,
        data_extensions=all_des,
        content_block_loader=_content_block_loader,
    )

    pdf_bytes = _html_to_pdf(rendered_html)
    base_name = Path(template_name).stem
    filename = f"{base_name}_row{row_index + 1}.pdf"

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.route("/batch-stream/<template_name>")
def batch_export_stream(template_name: str):
    """SSE endpoint — stream batch render progress row by row."""
    template_path = os.path.join(_emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return jsonify({"error": "Template not found"}), 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    fast_mode = request.args.get("fast", "0") in ("1", "true", "yes")
    # Skip waiting for web fonts to swap in. Layout uses fallback (Arial) → fastest.
    skip_fonts = request.args.get("nofonts", "0") in ("1", "true", "yes") or fast_mode
    # Tunable concurrency. Default: cap at 4 so worse-specced machines don't thrash.
    cpu = os.cpu_count() or 4
    default_concurrency = max(2, min(4, cpu))
    try:
        concurrency = int(request.args.get("c", default_concurrency))
    except ValueError:
        concurrency = default_concurrency
    concurrency = max(1, min(16, concurrency))

    if not excel_file:
        return jsonify({"error": "No Excel file specified"}), 400

    send_list = get_send_list(_de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return jsonify({"error": "No data rows found"}), 400

    all_des = load_all(_de_dir())

    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    output_dir = _output_dir()
    os.makedirs(output_dir, exist_ok=True)
    base_name = Path(template_name).stem
    total = len(send_list)

    def _row_filename(idx: int, subscriber_row: Dict[str, Any]) -> tuple:
        row_lower = {k.lower(): v for k, v in subscriber_row.items()}
        identifier = (
            row_lower.get("email")
            or row_lower.get("emailaddress")
            or row_lower.get("firstname", "")
        )
        if identifier:
            safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(identifier))
            return f"{base_name}_{idx+1}_{safe_id}.pdf", str(identifier)
        return f"{base_name}_{idx+1}.pdf", ""

    # Minimal launch flags: skip features irrelevant to headless PDF rendering.
    launch_args = [
        "--disable-extensions",
        "--disable-default-apps",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-sync",
        "--disable-translate",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
        "--safebrowsing-disable-auto-update",
        "--disable-component-update",
        "--disable-dev-shm-usage",
        "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    ]

    def generate():
        import asyncio
        import threading
        import time as _time
        from queue import Queue, Empty
        from playwright.async_api import async_playwright

        msg_q: Queue = Queue()

        def worker():
            async def run():
                try:
                    msg_q.put(("phase", "Launching browser..."))
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(args=launch_args)
                        # Single shared context — HTTP cache (fonts, CSS, images) is reused.
                        context = await browser.new_context()

                        msg_q.put(("phase", f"Rendering HTML for {total} rows..."))
                        # Phase 1: render HTML (pure Python — fast thanks to LookupRows index)
                        html_items = []
                        for i, rendered_html in render_batch_stream(
                            template_source=template_source,
                            send_list=send_list,
                            data_extensions=all_des,
                            content_block_loader=_content_block_loader,
                        ):
                            out_name, identifier = _row_filename(i, send_list[i])
                            html_items.append((out_name, identifier, rendered_html))

                        if fast_mode:
                            async def block_remote(route):
                                rt = route.request.resource_type
                                if rt in ("image", "font", "stylesheet", "media"):
                                    await route.abort()
                                else:
                                    await route.continue_()
                            await context.route("**/*", block_remote)

                        msg_q.put(("phase", f"Rendering {total} PDF(s) with {concurrency} workers..."))

                        # Phase 3: page pool. Pre-create N pages and reuse them via a queue.
                        # Avoids new_page() overhead per row.
                        pages = [await context.new_page() for _ in range(concurrency)]
                        work: asyncio.Queue = asyncio.Queue()
                        for item in html_items:
                            work.put_nowait(item)
                        for _ in range(concurrency):
                            work.put_nowait(None)  # sentinel per worker

                        done_count = 0
                        done_lock = asyncio.Lock()

                        async def worker_loop(page):
                            nonlocal done_count
                            while True:
                                item = await work.get()
                                if item is None:
                                    work.task_done()
                                    break
                                out_name, identifier, html_content = item
                                try:
                                    try:
                                        await page.set_content(
                                            html_content,
                                            wait_until="load",
                                            timeout=15000,
                                        )
                                        if not skip_fonts:
                                            await page.evaluate(
                                                "() => Promise.race(["
                                                "document.fonts.ready,"
                                                "new Promise(r => setTimeout(r, 3000))"
                                                "])"
                                            )
                                    except Exception:
                                        pass
                                    height = await page.evaluate("() => document.documentElement.scrollHeight")
                                    out_path = os.path.join(output_dir, out_name)
                                    await page.pdf(
                                        path=out_path,
                                        width="8.5in",
                                        height=f"{height + 20}px",
                                        print_background=True,
                                    )
                                except Exception as e:
                                    msg_q.put(("error", f"{out_name}: {e}"))
                                async with done_lock:
                                    done_count += 1
                                    idx = done_count
                                msg_q.put(("row", idx, out_name, identifier))
                                work.task_done()

                        await asyncio.gather(*[worker_loop(p) for p in pages])

                        for page in pages:
                            await page.close()
                        await context.close()
                        await browser.close()
                    msg_q.put(("done",))
                except Exception as e:
                    msg_q.put(("error", str(e)))

            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()

        yield f"data: {json.dumps({'type': 'start', 'total': total, 'template': template_name, 'file': excel_file})}\n\n"

        while True:
            try:
                msg = msg_q.get(timeout=10)
            except Empty:
                # Send SSE comment as keepalive to prevent connection timeout
                yield ": keepalive\n\n"
                continue
            kind = msg[0]
            if kind == "phase":
                yield f"data: {json.dumps({'type': 'phase', 'message': msg[1]})}\n\n"
            elif kind == "row":
                _, index, out_name, identifier = msg
                yield f"data: {json.dumps({'type': 'row', 'index': index, 'total': total, 'filename': out_name, 'identifier': identifier})}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'total': total})}\n\n"
                break
            elif kind == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': msg[1]})}\n\n"
                break

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@bp.route("/sheets")
def get_sheets():
    """AJAX endpoint — return sheets for a given Excel file."""
    filename = request.args.get("file", "")
    if not filename:
        return jsonify([])
    filepath = os.path.join(_de_dir(), filename)
    if not os.path.isfile(filepath):
        return jsonify([])
    try:
        sheets = load_excel_file(filepath)
        return jsonify([{"name": name, "rows": len(rows)} for name, rows in sheets.items()])
    except Exception:
        return jsonify([])


@bp.route("/subscriber_data")
def get_subscriber_data():
    """AJAX endpoint — return subscriber row data for preview."""
    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return jsonify({})

    send_list = get_send_list(_de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return jsonify({})

    row_index = max(0, min(row_index, len(send_list) - 1))
    return jsonify(send_list[row_index])


@bp.route("/output")
def list_output():
    """List exported files."""
    output_dir = Path(_output_dir())
    if not output_dir.is_dir():
        files = []
    else:
        files = sorted(f.name for f in output_dir.glob("*.html"))
    return render_template("output.html", files=files)


@bp.route("/output/<filename>")
def serve_output(filename: str):
    """Serve an exported HTML file."""
    return send_from_directory(_output_dir(), filename)


@bp.route("/mapping")
def mapping_page():
    """DE name mapping page."""
    current_mapping = load_de_mapping(_de_dir())
    available_sheets = list_all_de_names(_de_dir())
    return render_template(
        "mapping.html",
        mapping=current_mapping,
        available_sheets=available_sheets,
    )


@bp.route("/mapping/save", methods=["POST"])
def save_mapping():
    """Save DE name mappings from form."""
    # Collect alias/target pairs from form
    aliases = request.form.getlist("alias")
    targets = request.form.getlist("target")
    mapping = {}
    for alias, target in zip(aliases, targets):
        alias = alias.strip()
        target = target.strip()
        if alias and target:
            mapping[alias] = target
    save_de_mapping(_de_dir(), mapping)
    return redirect(url_for("main.mapping_page"))


@bp.route("/mapping/data")
def mapping_data():
    """AJAX endpoint — return current mapping and available sheets."""
    current_mapping = load_de_mapping(_de_dir())
    available_sheets = list_all_de_names(_de_dir())
    return jsonify({"mapping": current_mapping, "sheets": available_sheets})


# ---------------------------------------------------------------------------
# File Upload Routes
# ---------------------------------------------------------------------------

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
    filepath = os.path.join(_de_dir(), filename)
    file.save(filepath)

    # Return info about the uploaded file
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
    filepath = os.path.join(_de_dir(), filename)
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
    filepath = os.path.join(_de_dir(), filename)
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
    filepath = os.path.join(_emails_dir(), filename)
    file.save(filepath)

    return jsonify({
        "success": True,
        "filename": filename,
    })


@bp.route("/upload/files", methods=["GET"])
def list_uploaded_files():
    """Return currently uploaded CSV and HTML files."""
    de_dir = Path(_de_dir())
    emails_dir = Path(_emails_dir())

    csv_files = []
    for f in sorted(de_dir.glob("*.csv")):
        csv_files.append(f.name)

    html_files = []
    for f in sorted(emails_dir.glob("*.html")):
        html_files.append(f.name)

    return jsonify({"csv_files": csv_files, "html_files": html_files})


@bp.route("/upload/delete/<filename>", methods=["DELETE"])
def delete_uploaded_file(filename: str):
    """Delete an uploaded file."""
    filename = secure_filename(filename)

    # Check in data_extensions
    filepath = os.path.join(_de_dir(), filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        return jsonify({"success": True})

    # Check in emails
    filepath = os.path.join(_emails_dir(), filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        return jsonify({"success": True})

    return jsonify({"error": "File not found"}), 404


# ---------------------------------------------------------------------------
# Configuration Save/Load
# ---------------------------------------------------------------------------

CONFIGS_FILE = "saved_configs.json"


def _configs_path() -> str:
    return os.path.join(current_app.config["DATA_EXTENSIONS_DIR"], "..", CONFIGS_FILE)


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
    # Replace if same name exists
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


# ---------------------------------------------------------------------------
# SFMC Content Search
# ---------------------------------------------------------------------------

@bp.route("/search")
def search_page():
    """Search tool — find emails/content blocks in SFMC by keyword."""
    sfmc = SFMCClient()
    return render_template("search.html", configured=sfmc.is_configured())


@bp.route("/search/query", methods=["POST"])
def search_query():
    """AJAX endpoint — search SFMC Content Builder assets with SSE progress."""
    import queue
    import threading

    sfmc = SFMCClient()
    if not sfmc.is_configured():
        return jsonify({"error": "SFMC API credentials not configured. Set SFMC_CLIENT_ID, SFMC_CLIENT_SECRET, SFMC_AUTH_BASE_URI, and SFMC_REST_BASE_URI environment variables."}), 400

    data = request.get_json()
    if not data or not data.get("query", "").strip():
        return jsonify({"error": "Search query is required"}), 400

    query = data["query"].strip()
    asset_types = data.get("asset_types")  # None means all
    page = int(data.get("page", 1))
    sort_field = data.get("sort_field", "modifiedDate")
    sort_direction = data.get("sort_direction", "DESC")
    include_journeys = data.get("include_journeys", False)

    # Validate sort field
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

            # Optionally fetch journey associations
            if include_journeys and results["items"]:
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


@bp.route("/search/debug", methods=["POST"])
def search_debug():
    """Debug endpoint — test what SFMC actually returns for a query."""
    sfmc = SFMCClient()
    if not sfmc.is_configured():
        return jsonify({"error": "Not configured"}), 400

    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query"}), 400

    import requests as req

    sfmc._ensure_token()
    headers = sfmc._headers()

    # Test 1: simple like on views.html.content
    test_body = {
        "page": {"page": 1, "pageSize": 5},
        "query": {
            "property": "views.html.content",
            "simpleOperator": "like",
            "value": f"%{query}%",
        },
    }
    resp1 = req.post(
        f"{sfmc.rest_base_uri}/asset/v1/content/assets/query",
        headers=headers,
        json=test_body,
        timeout=30,
    )

    # Test 2: simple like on name
    test_body2 = {
        "page": {"page": 1, "pageSize": 5},
        "query": {
            "property": "name",
            "simpleOperator": "like",
            "value": f"%{query}%",
        },
    }
    resp2 = req.post(
        f"{sfmc.rest_base_uri}/asset/v1/content/assets/query",
        headers=headers,
        json=test_body2,
        timeout=30,
    )

    # Test 3: single asset fetch to see content structure
    sample_asset = None
    if resp2.ok:
        items = resp2.json().get("items", [])
        if items:
            aid = items[0].get("id")
            r = req.get(f"{sfmc.rest_base_uri}/asset/v1/content/assets/{aid}", headers=headers, timeout=15)
            if r.ok:
                full = r.json()
                sample_asset = {
                    "id": aid,
                    "has_views": "views" in full,
                    "views_keys": list((full.get("views") or {}).keys()),
                    "has_content_key": "content" in full,
                    "content_preview": (full.get("content") or "")[:200],
                    "html_preview": ((full.get("views") or {}).get("html", {}).get("content", "") or "")[:200],
                }

    return jsonify({
        "html_filter": {"status": resp1.status_code, "body": resp1.json() if resp1.ok else resp1.text[:500]},
        "name_filter": {"status": resp2.status_code, "count": resp2.json().get("count") if resp2.ok else resp2.text[:500]},
        "sample_asset": sample_asset,
    })

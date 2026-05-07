"""
Preview and PDF rendering routes.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Dict

from flask import (
    jsonify, make_response, render_template, request, Response,
)

from ampscript.interpreter import render as ampscript_render, render_batch_stream
from data.excel_loader import get_send_list, load_all
from web.routes import bp
from web.services.content_blocks import content_block_loader, emails_dir, de_dir, output_dir
from web.services.pdf import html_to_pdf


def _list_templates():
    """List .html files in the emails directory."""
    edir = Path(emails_dir())
    if not edir.is_dir():
        return []
    return sorted(f.name for f in edir.glob("*.html"))


@bp.route("/preview/<template_name>")
def preview(template_name: str):
    """Preview UI — picks a data file/sheet and renders rows in an iframe.

    The actual rendered HTML is served by ``/preview/<name>/raw`` (iframed by
    the page). When no ``?file=`` is supplied we still render the page so the
    user can pick a data source from the UI; otherwise we resolve the row count
    and current row up-front so the controls render correctly.
    """
    template_path = os.path.join(emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return "Template not found", 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    try:
        row_index = int(request.args.get("row", "0"))
    except ValueError:
        row_index = 0

    subscriber_row: Dict[str, Any] | None = None
    total_rows = 0

    if excel_file:
        send_list = get_send_list(de_dir(), excel_file, sheet_name or None)
        total_rows = len(send_list)
        if total_rows:
            row_index = max(0, min(row_index, total_rows - 1))
            subscriber_row = send_list[row_index]
        else:
            row_index = 0

    return render_template(
        "preview.html",
        template_name=template_name,
        subscriber_row=subscriber_row,
        row_index=row_index,
        total_rows=total_rows,
        excel_file=excel_file,
        sheet_name=sheet_name,
    )


@bp.route("/preview/<template_name>/raw")
def preview_raw(template_name: str):
    """Return just the rendered HTML (for iframe embedding)."""
    template_path = os.path.join(emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return "Template not found", 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return "No Excel file specified", 400

    send_list = get_send_list(de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return "No data rows found", 400

    row_index = max(0, min(row_index, len(send_list) - 1))
    subscriber_row = send_list[row_index]
    all_des = load_all(de_dir())

    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    rendered_html = ampscript_render(
        template_source=template_source,
        subscriber_row=subscriber_row,
        data_extensions=all_des,
        content_block_loader=content_block_loader,
    )

    return rendered_html


@bp.route("/preview/<template_name>/pdf")
def preview_pdf(template_name: str):
    """Generate a PDF of the rendered email and return it as a download."""
    template_path = os.path.join(emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return "Template not found", 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return "No Excel file specified", 400

    send_list = get_send_list(de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return "No data rows found", 400

    row_index = max(0, min(row_index, len(send_list) - 1))
    subscriber_row = send_list[row_index]
    all_des = load_all(de_dir())

    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    rendered_html = ampscript_render(
        template_source=template_source,
        subscriber_row=subscriber_row,
        data_extensions=all_des,
        content_block_loader=content_block_loader,
    )

    pdf_bytes = html_to_pdf(rendered_html)
    base_name = Path(template_name).stem
    filename = f"{base_name}_row{row_index + 1}.pdf"

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Batch Export (SSE streaming)
# ---------------------------------------------------------------------------

# Minimal launch flags: skip features irrelevant to headless PDF rendering.
_BROWSER_LAUNCH_ARGS = [
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


@bp.route("/batch-stream/<template_name>")
def batch_export_stream(template_name: str):
    """SSE endpoint — stream batch render progress row by row."""
    template_path = os.path.join(emails_dir(), template_name)
    if not os.path.isfile(template_path):
        return jsonify({"error": "Template not found"}), 404

    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    fast_mode = request.args.get("fast", "0") in ("1", "true", "yes")
    skip_fonts = request.args.get("nofonts", "0") in ("1", "true", "yes") or fast_mode

    cpu = os.cpu_count() or 4
    default_concurrency = max(2, min(4, cpu))
    try:
        concurrency = int(request.args.get("c", default_concurrency))
    except ValueError:
        concurrency = default_concurrency
    concurrency = max(1, min(16, concurrency))

    if not excel_file:
        return jsonify({"error": "No Excel file specified"}), 400

    send_list = get_send_list(de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return jsonify({"error": "No data rows found"}), 400

    all_des = load_all(de_dir())

    with open(template_path, "r", encoding="utf-8") as f:
        template_source = f.read()

    out_dir = output_dir()
    os.makedirs(out_dir, exist_ok=True)
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

    def generate():
        from playwright.async_api import async_playwright

        msg_q: Queue = Queue()

        def worker():
            async def run():
                try:
                    msg_q.put(("phase", "Launching browser..."))
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(args=_BROWSER_LAUNCH_ARGS)
                        context = await browser.new_context()

                        msg_q.put(("phase", f"Rendering HTML for {total} rows..."))
                        html_items = []
                        for i, rendered_html in render_batch_stream(
                            template_source=template_source,
                            send_list=send_list,
                            data_extensions=all_des,
                            content_block_loader=content_block_loader,
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

                        pages = [await context.new_page() for _ in range(concurrency)]
                        work: asyncio.Queue = asyncio.Queue()
                        for item in html_items:
                            work.put_nowait(item)
                        for _ in range(concurrency):
                            work.put_nowait(None)

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
                                    out_path = os.path.join(out_dir, out_name)
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
    """Return sheet info for a data file. CSV files surface as a single sheet."""
    from data.excel_loader import load_csv_file, load_excel_file

    filename = request.args.get("file", "")
    if not filename:
        return jsonify([])
    filepath = os.path.join(de_dir(), filename)
    if not os.path.isfile(filepath):
        return jsonify([])

    if filename.lower().endswith(".csv"):
        try:
            rows = load_csv_file(filepath)
            return jsonify([{"name": "", "rows": len(rows)}])
        except Exception:
            return jsonify([])

    try:
        sheets = load_excel_file(filepath)
        return jsonify([{"name": name, "rows": len(rows)} for name, rows in sheets.items()])
    except Exception:
        return jsonify([])


@bp.route("/columns")
def get_columns():
    """Return column names for a data file (CSV or XLSX sheet).

    Used by the preview page to populate join-column dropdowns for naming.
    """
    from data.excel_loader import load_csv_file, load_excel_file

    filename = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    if not filename:
        return jsonify([])

    filepath = os.path.join(de_dir(), filename)
    if not os.path.isfile(filepath):
        return jsonify([])

    try:
        if filename.lower().endswith(".csv"):
            rows = load_csv_file(filepath)
        else:
            sheets = load_excel_file(filepath)
            if sheet_name and sheet_name in sheets:
                rows = sheets[sheet_name]
            elif sheets:
                rows = next(iter(sheets.values()))
            else:
                return jsonify([])
    except Exception:
        return jsonify([])

    if not rows:
        return jsonify([])
    return jsonify(list(rows[0].keys()))


@bp.route("/subscriber_data")
def get_subscriber_data():
    """AJAX endpoint — return subscriber row data for preview."""
    excel_file = request.args.get("file", "")
    sheet_name = request.args.get("sheet", "")
    row_index = int(request.args.get("row", "0"))

    if not excel_file:
        return jsonify({})

    send_list = get_send_list(de_dir(), excel_file, sheet_name or None)
    if not send_list:
        return jsonify({})

    row_index = max(0, min(row_index, len(send_list) - 1))
    return jsonify(send_list[row_index])

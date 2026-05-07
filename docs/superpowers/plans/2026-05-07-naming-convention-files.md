# Naming-convention Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users upload a separate "naming file" (CSV/XLSX) to the HTML2PDF tool and use a `{{FIELD}}` template at export time to name generated PDFs from looked-up fields.

**Architecture:** Naming files are a third DE type stored alongside Sender/Mapping DEs in `content/data_extensions/`, tracked via the existing `_de_types.json` sidecar (`upload.py`). A new pure module `web/services/naming.py` builds an index keyed on a join column and renders the filename template. The preview page exposes file/column/template config as query params consumed by both `preview_pdf` (single-row) and `batch_export_stream` (batch). Unmatched rows are skipped and surfaced via a new SSE `skip` event.

**Tech Stack:** Python 3 / Flask, openpyxl, vanilla JS, Server-Sent Events. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-07-naming-convention-files-design.md`

---

## File Structure

| Path | Status | Responsibility |
|------|--------|----------------|
| `src/web/services/naming.py` | Create | Pure functions: `render_filename`, `build_naming_index`. No Flask imports. |
| `tests/test_naming.py` | Create | Script-style tests (run with `python tests/test_naming.py`), matching existing test style. |
| `src/web/routes/html2pdf/upload.py` | Modify | Add `"naming"` DE type, new `/upload/naming-csv` endpoint, extend classifier and listing. |
| `src/web/routes/html2pdf/preview.py` | Modify | Add `/columns` endpoint, accept naming params on `preview_pdf`/`batch_export_stream`, integrate naming module, emit `skip` events. |
| `src/templates/index.html` | Modify | Fourth card "Naming files" with dropzone + list. |
| `src/templates/preview.html` | Modify | "Filnavngivning" config block in toolbar (file/column/template inputs + live preview), localStorage persistence, batch overlay shows skipped count. |

The repo has no pytest setup; existing tests in `tests/` are script-style (`python tests/test_smoke.py`). New tests follow the same convention.

---

## Task 1: Pure naming module (TDD)

**Files:**
- Create: `src/web/services/naming.py`
- Create: `tests/test_naming.py`

- [ ] **Step 1.1: Write failing tests for `render_filename`**

Create `tests/test_naming.py` with the following content:

```python
"""Tests for src/web/services/naming.py — pure functions, run as a script."""

import os
import sys

# Match how app.py augments sys.path so we can `from web.services...`
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from web.services.naming import render_filename, build_naming_index, disambiguate


def assert_eq(actual, expected, label):
    assert actual == expected, f"FAIL {label}: expected {expected!r}, got {actual!r}"
    print(f"  PASS {label}")


# ---------- render_filename ----------

# Basic substitution from lookup row
assert_eq(
    render_filename("{{CPR_NUMBER}}_invoice", {"CUST_ID": "1"}, {"CPR_NUMBER": "0101019995"}),
    "0101019995_invoice.pdf",
    "render_filename basic lookup substitution",
)

# Substitution from sender row
assert_eq(
    render_filename("{{CUST_ID}}_invoice", {"CUST_ID": "42"}, {"CPR_NUMBER": "x"}),
    "42_invoice.pdf",
    "render_filename uses sender row",
)

# Lookup wins on key collision
assert_eq(
    render_filename("{{X}}", {"X": "sender"}, {"X": "lookup"}),
    "lookup.pdf",
    "render_filename lookup wins on collision",
)

# Unknown key substitutes empty string
assert_eq(
    render_filename("{{MISSING}}_x", {"A": "1"}, {"B": "2"}),
    "_x.pdf",
    "render_filename unknown key empty",
)

# Multiple fields and underscores survive
assert_eq(
    render_filename("{{CPR_NUMBER}}_{{CUST_ID}}", {"CUST_ID": "42"}, {"CPR_NUMBER": "9"}),
    "9_42.pdf",
    "render_filename combines sender + lookup",
)

# Trailing .pdf in template is stripped, single .pdf appended
assert_eq(
    render_filename("{{X}}.pdf", {}, {"X": "abc"}),
    "abc.pdf",
    "render_filename strips template's .pdf",
)
assert_eq(
    render_filename("{{X}}.PDF", {}, {"X": "abc"}),
    "abc.pdf",
    "render_filename strips template's .PDF case-insensitive",
)

# Sanitization: keep alnum + . _ -, replace others with _
assert_eq(
    render_filename("{{X}}", {}, {"X": "a/b\\c d:e?f"}),
    "a_b_c_d_e_f.pdf",
    "render_filename sanitizes path-unsafe chars",
)

# Allowed chars: dot, underscore, hyphen
assert_eq(
    render_filename("{{X}}", {}, {"X": "a.b-c_d"}),
    "a.b-c_d.pdf",
    "render_filename keeps . _ - alnum",
)

# Numbers from a lookup get coerced to str cleanly
assert_eq(
    render_filename("{{X}}", {}, {"X": 12345}),
    "12345.pdf",
    "render_filename coerces int values to str",
)

# Empty stem after sanitization → returns empty string (caller decides fallback)
assert_eq(
    render_filename("{{X}}", {}, {"X": ""}),
    "",
    "render_filename empty stem returns empty string",
)
assert_eq(
    render_filename("{{X}}", {}, {"X": "   "}),
    "",
    "render_filename whitespace-only stem returns empty string",
)


# ---------- disambiguate ----------

assert_eq(disambiguate("a.pdf", set()), "a.pdf", "disambiguate first occurrence")
assert_eq(disambiguate("a.pdf", {"a.pdf"}), "a_2.pdf", "disambiguate second occurrence")
assert_eq(disambiguate("a.pdf", {"a.pdf", "a_2.pdf"}), "a_3.pdf", "disambiguate third occurrence")
assert_eq(
    disambiguate("foo.bar.pdf", {"foo.bar.pdf"}),
    "foo.bar_2.pdf",
    "disambiguate handles dotted stem",
)


# ---------- build_naming_index ----------

import csv
import tempfile

with tempfile.TemporaryDirectory() as tmp:
    csv_path = os.path.join(tmp, "naming.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["CUST_ID", "CPR_NUMBER", "Name"])
        w.writerow(["1", "0101019995", "Alice"])
        w.writerow(["2", "0202029995", "Bob"])
        w.writerow([" 3 ", "0303039995", "Charlie"])  # value with whitespace

    idx = build_naming_index(csv_path, None, "CUST_ID")
    assert_eq(set(idx.keys()), {"1", "2", "3"}, "build_naming_index str keys, trims whitespace")
    assert_eq(idx["1"]["CPR_NUMBER"], "0101019995", "build_naming_index returns row dict")
    assert_eq(idx["3"]["Name"], "Charlie", "build_naming_index trims whitespace from key")

    # Missing join column → ValueError with column name
    try:
        build_naming_index(csv_path, None, "DOES_NOT_EXIST")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "DOES_NOT_EXIST" in str(e), f"error message should mention column: {e}"
        print("  PASS build_naming_index raises ValueError on missing key column")


print("\nAll naming tests passed!")
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `python tests/test_naming.py`
Expected: `ModuleNotFoundError: No module named 'web.services.naming'` (file does not exist yet).

- [ ] **Step 1.3: Create the naming module with minimal implementation**

Create `src/web/services/naming.py`:

```python
"""
Filename templating for HTML2PDF batch export.

Pure functions — no Flask imports, no I/O state. Used by both single-row
PDF download and batch export to turn a `{{FIELD}}` template plus a row
lookup into a sanitized PDF filename.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Set

from data.excel_loader import load_csv_file, load_excel_file


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def render_filename(
    template: str,
    sender_row: Dict[str, Any],
    lookup_row: Dict[str, Any],
) -> str:
    """Render a filename from a `{{FIELD}}` template.

    Substitutes from the merged dict of sender_row + lookup_row (lookup wins on
    collision). Sanitizes to keep alnum and `._-` only. Strips a trailing
    `.pdf`/`.PDF` from the template before appending a single `.pdf` extension.

    Returns an empty string if the rendered stem is empty after sanitization
    so the caller can apply its own fallback.
    """
    merged = {**sender_row, **lookup_row}

    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = merged.get(key, "")
        return "" if value is None else str(value)

    rendered = _PLACEHOLDER_RE.sub(replace, template)

    # Drop any user-typed extension (case-insensitive) before sanitizing.
    if rendered.lower().endswith(".pdf"):
        rendered = rendered[:-4]

    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in rendered)
    safe = safe.strip().strip("_")

    if not safe:
        return ""
    return f"{safe}.pdf"


def disambiguate(filename: str, seen: Set[str]) -> str:
    """If `filename` is already in `seen`, append `_2`, `_3`, … before `.pdf`.

    Caller is responsible for adding the chosen filename to `seen` afterwards.
    """
    if filename not in seen:
        return filename

    stem, ext = os.path.splitext(filename)
    counter = 2
    while f"{stem}_{counter}{ext}" in seen:
        counter += 1
    return f"{stem}_{counter}{ext}"


def build_naming_index(
    filepath: str,
    sheet_name: Optional[str],
    naming_key_col: str,
) -> Dict[str, Dict[str, Any]]:
    """Load a naming file (CSV or XLSX) and return {str(key_value): row_dict}.

    For XLSX, `sheet_name` selects the sheet (first sheet if None or unmatched).
    Raises ValueError if `naming_key_col` does not exist in the file's columns.
    """
    if filepath.lower().endswith(".csv"):
        rows = load_csv_file(filepath)
    else:
        sheets = load_excel_file(filepath)
        if not sheets:
            raise ValueError(f"Naming file has no sheets: {filepath}")
        if sheet_name and sheet_name in sheets:
            rows = sheets[sheet_name]
        else:
            rows = next(iter(sheets.values()))

    if not rows:
        return {}

    if naming_key_col not in rows[0]:
        raise ValueError(
            f"Naming key column {naming_key_col!r} not found in naming file. "
            f"Available columns: {list(rows[0].keys())}"
        )

    index: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw = row.get(naming_key_col)
        if raw is None or raw == "":
            continue
        key = str(raw).strip()
        if not key:
            continue
        index[key] = row
    return index
```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `python tests/test_naming.py`
Expected: `All naming tests passed!` with no AssertionError. If `data.excel_loader` import fails, double-check the `sys.path` insert at the top of the test mirrors `app.py:12`.

- [ ] **Step 1.5: Commit**

```bash
git add src/web/services/naming.py tests/test_naming.py
git commit -m "feat(html2pdf): pure naming module for {{field}} filename templates"
```

---

## Task 2: Add `naming` DE type and upload endpoint

**Files:**
- Modify: `src/web/routes/html2pdf/upload.py`

- [ ] **Step 2.1: Extend `_classify_de_files` to return naming files**

Open `src/web/routes/html2pdf/upload.py`. Replace the `_classify_de_files` function (lines 86-96) with:

```python
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
```

- [ ] **Step 2.2: Add the `/upload/naming-csv` endpoint**

Below the existing `upload_lookup_csv` route (after the function, around line 148), add:

```python
@bp.route("/upload/naming-csv", methods=["POST"])
def upload_naming_csv():
    """Upload a naming-convention Data Extension (CSV or XLSX)."""
    return _save_data_upload("naming")
```

- [ ] **Step 2.3: Update `list_uploaded_files` to expose `naming_files`**

Replace the body of `list_uploaded_files` (lines 174-194) with:

```python
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
```

- [ ] **Step 2.4: Manually verify upload + listing**

Run the dev server: `python app.py`

In a separate shell, with a small CSV at `/tmp/naming.csv` containing `CUST_ID,CPR_NUMBER\n1,9995`:

```bash
curl -F "file=@/tmp/naming.csv" http://localhost:5000/upload/naming-csv
curl http://localhost:5000/upload/files
```

Expected:
- First response: JSON `{"success": true, "filename": "naming.csv", "type": "naming", "rows": 1, "columns": ["CUST_ID", "CPR_NUMBER"]}`.
- Second response includes `"naming_files": ["naming.csv"]` and the file is NOT in `mapping_files` or `sender_files`.

Inspect `content/data_extensions/_de_types.json` — should contain `"naming.csv": "naming"`.

Stop the server (Ctrl-C). Delete the uploaded test file via the UI or `curl -X DELETE http://localhost:5000/upload/delete/naming.csv` after restarting if needed.

- [ ] **Step 2.5: Commit**

```bash
git add src/web/routes/html2pdf/upload.py
git commit -m "feat(html2pdf): naming DE type + upload endpoint + listing"
```

---

## Task 3: `/columns` endpoint for column dropdowns

**Files:**
- Modify: `src/web/routes/html2pdf/preview.py`

- [ ] **Step 3.1: Add the `/columns` endpoint**

Open `src/web/routes/html2pdf/preview.py`. Below the existing `/sheets` route (after `get_sheets`, around line 376), add:

```python
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
```

- [ ] **Step 3.2: Manually verify**

With the dev server running and a CSV `naming.csv` already uploaded:

```bash
curl "http://localhost:5000/columns?file=naming.csv"
```

Expected: a JSON array of column names, e.g. `["CUST_ID", "CPR_NUMBER"]`.

- [ ] **Step 3.3: Commit**

```bash
git add src/web/routes/html2pdf/preview.py
git commit -m "feat(html2pdf): /columns endpoint for naming-config dropdowns"
```

---

## Task 4: Wire naming into single-row PDF (`preview_pdf`)

**Files:**
- Modify: `src/web/routes/html2pdf/preview.py`

- [ ] **Step 4.1: Add a helper that resolves naming params**

Open `src/web/routes/html2pdf/preview.py`. Add this helper just below the imports (after the existing `_list_templates` function, around line 32):

```python
from web.services.naming import build_naming_index, render_filename, disambiguate


def _read_naming_params():
    """Pull naming params off the current request. Returns dict or None.

    None means naming is not configured (template/single-row should fall back
    to default behavior). Raises ValueError if params reference a missing file
    or a missing column — caller turns that into a 400.
    """
    naming_file = request.args.get("naming_file", "").strip()
    sender_key = request.args.get("sender_key", "").strip()
    naming_key = request.args.get("naming_key", "").strip()
    template = request.args.get("naming_template", "").strip()
    if not (naming_file and sender_key and naming_key and template):
        return None

    filepath = os.path.join(de_dir(), naming_file)
    if not os.path.isfile(filepath):
        raise ValueError(f"Naming file not found: {naming_file}")

    sheet = request.args.get("naming_sheet", "").strip() or None
    index = build_naming_index(filepath, sheet, naming_key)

    return {
        "index": index,
        "sender_key": sender_key,
        "template": template,
    }
```

- [ ] **Step 4.2: Use the helper in `preview_pdf`**

Replace the body of `preview_pdf` (the function originally at lines 112-151) with:

```python
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

    # Determine filename: naming-config wins when configured.
    try:
        naming = _read_naming_params()
    except ValueError as e:
        return str(e), 400

    filename = f"{base_name}_row{row_index + 1}.pdf"
    if naming is not None:
        if naming["sender_key"] not in subscriber_row:
            return f"Sender column not found: {naming['sender_key']}", 400
        key = str(subscriber_row.get(naming["sender_key"], "")).strip()
        lookup = naming["index"].get(key) if key else None
        if lookup is not None:
            rendered = render_filename(naming["template"], subscriber_row, lookup)
            if rendered:
                filename = rendered

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
```

- [ ] **Step 4.3: Manually verify**

With dev server running, a sender DE `subs.csv` (containing `CUST_ID,FirstName\n1,Alice\n2,Bob`), a naming file `naming.csv` (`CUST_ID,CPR_NUMBER\n1,9995\n2,8884`), and an HTML template uploaded:

```bash
curl -OJ "http://localhost:5000/preview/welcome.html/pdf?file=subs.csv&row=0&naming_file=naming.csv&sender_key=CUST_ID&naming_key=CUST_ID&naming_template={{CPR_NUMBER}}_invoice"
```

Expected: file saved as `9995_invoice.pdf`. Without the naming params, the URL returns `welcome_row1.pdf` as before.

Try with `naming_file=does-not-exist.csv`: HTTP 400 with the error message.

- [ ] **Step 4.4: Commit**

```bash
git add src/web/routes/html2pdf/preview.py
git commit -m "feat(html2pdf): apply naming template to single-row PDF download"
```

---

## Task 5: Wire naming into batch export (`batch_export_stream`)

**Files:**
- Modify: `src/web/routes/html2pdf/preview.py`

- [ ] **Step 5.1: Build naming context up front in `batch_export_stream`**

Open `src/web/routes/html2pdf/preview.py`. In `batch_export_stream`, after the existing line `all_des = load_all(de_dir())` (around line 204) and before `with open(template_path, ...)`, add:

```python
    try:
        naming = _read_naming_params()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if naming is not None and send_list and naming["sender_key"] not in send_list[0]:
        return jsonify({"error": f"Sender column not found: {naming['sender_key']}"}), 400
```

- [ ] **Step 5.2: Compute eligible rows and replace `_row_filename`**

Locate the `_row_filename` function defined inside `batch_export_stream` (originally lines 214-224) and replace it, plus the surrounding setup. Find this block:

```python
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
```

Replace it with:

```python
    out_dir = output_dir()
    os.makedirs(out_dir, exist_ok=True)
    base_name = Path(template_name).stem
    total = len(send_list)

    # Pre-compute which rows have a naming-file match so the progress bar
    # reflects total_eligible, not total raw rows.
    if naming is not None:
        skipped_indices = []
        eligible_indices = []
        for i, row in enumerate(send_list):
            key = str(row.get(naming["sender_key"], "")).strip()
            if key and key in naming["index"]:
                eligible_indices.append(i)
            else:
                skipped_indices.append((i, key))
        total_eligible = len(eligible_indices)
    else:
        skipped_indices = []
        eligible_indices = list(range(total))
        total_eligible = total

    used_filenames: set = set()

    def _row_filename(idx: int, subscriber_row: Dict[str, Any]) -> tuple:
        """Return (filename, identifier_for_progress_log)."""
        if naming is not None:
            key = str(subscriber_row.get(naming["sender_key"], "")).strip()
            lookup = naming["index"].get(key)
            # Caller ensures we only land here for eligible rows.
            rendered = render_filename(naming["template"], subscriber_row, lookup or {})
            if not rendered:
                rendered = f"{base_name}_{idx+1}.pdf"
            final = disambiguate(rendered, used_filenames)
            used_filenames.add(final)
            return final, key

        row_lower = {k.lower(): v for k, v in subscriber_row.items()}
        identifier = (
            row_lower.get("email")
            or row_lower.get("emailaddress")
            or row_lower.get("firstname", "")
        )
        if identifier:
            safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(identifier))
            base_filename = f"{base_name}_{idx+1}_{safe_id}.pdf"
        else:
            base_filename = f"{base_name}_{idx+1}.pdf"
        final = disambiguate(base_filename, used_filenames)
        used_filenames.add(final)
        return final, str(identifier or "")
```

- [ ] **Step 5.3: Skip ineligible rows during render-batch streaming**

Inside the same function, find the loop:

```python
                        for i, rendered_html in render_batch_stream(
                            template_source=template_source,
                            send_list=send_list,
                            data_extensions=all_des,
                            content_block_loader=content_block_loader,
                        ):
                            out_name, identifier = _row_filename(i, send_list[i])
                            html_items.append((out_name, identifier, rendered_html))
```

Replace it with:

```python
                        eligible_set = set(eligible_indices)
                        for i, rendered_html in render_batch_stream(
                            template_source=template_source,
                            send_list=send_list,
                            data_extensions=all_des,
                            content_block_loader=content_block_loader,
                        ):
                            if i not in eligible_set:
                                # Skipped — surface to the client; no PDF will be rendered.
                                skip_key = next(
                                    (k for (idx_, k) in skipped_indices if idx_ == i),
                                    "",
                                )
                                msg_q.put(("skip", i, skip_key))
                                continue
                            out_name, identifier = _row_filename(i, send_list[i])
                            html_items.append((out_name, identifier, rendered_html))
```

- [ ] **Step 5.4: Surface `total_eligible` and `skip` events in the SSE stream**

Find the start-event yield:

```python
        yield f"data: {json.dumps({'type': 'start', 'total': total, 'template': template_name, 'file': excel_file})}\n\n"
```

Replace with:

```python
        yield f"data: {json.dumps({'type': 'start', 'total': total_eligible, 'total_raw': total, 'skipped_total': len(skipped_indices), 'template': template_name, 'file': excel_file})}\n\n"
```

Find the `while True` SSE loop and the existing `if kind == "phase":` chain. Add a new branch above the `elif kind == "row":` line:

```python
            elif kind == "skip":
                _, idx, key = msg
                yield f"data: {json.dumps({'type': 'skip', 'index': idx, 'key': key})}\n\n"
```

Update the `done` event to include the skipped count. Find:

```python
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'total': total})}\n\n"
                break
```

Replace with:

```python
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done', 'total': total_eligible, 'skipped_total': len(skipped_indices)})}\n\n"
                break
```

- [ ] **Step 5.5: Update the row counter to use `total_eligible`**

Inside `worker_loop`, the row event currently reports against `total` implicitly; the SSE handler computes percent against `total` from the start event, which we already changed to `total_eligible`. So no further code change needed — but verify the `msg_q.put(("row", idx, out_name, identifier))` callsite is unchanged; if so, OK.

- [ ] **Step 5.6: Manually verify the batch flow**

Run dev server, with `subs.csv` (3 rows: CUST_IDs `1`, `2`, `99`), `naming.csv` (only `1` and `2` mapped), and a template uploaded:

```bash
curl -N "http://localhost:5000/batch-stream/welcome.html?file=subs.csv&naming_file=naming.csv&sender_key=CUST_ID&naming_key=CUST_ID&naming_template={{CPR_NUMBER}}_invoice"
```

Expected SSE output to include:
- `{"type": "start", "total": 2, "total_raw": 3, "skipped_total": 1, ...}`
- One `{"type": "skip", "index": 2, "key": "99"}` event
- Two `{"type": "row", ...}` events
- `{"type": "done", "total": 2, "skipped_total": 1}`

Verify two PDFs land in `content/output/` named according to `{{CPR_NUMBER}}_invoice.pdf`.

- [ ] **Step 5.7: Commit**

```bash
git add src/web/routes/html2pdf/preview.py
git commit -m "feat(html2pdf): naming-template-aware batch export with skip events"
```

---

## Task 6: Index page — "Naming files" card

**Files:**
- Modify: `src/templates/index.html`

- [ ] **Step 6.1: Add the new card after Mapping DEs**

Open `src/templates/index.html`. Find the `step-grid` block that closes after Mapping DEs (around line 205 — just after `</div>` of the second card and before the `<div class="card" style="margin-bottom:18px;">` that holds Email-skabeloner).

Inside `<div class="step-grid">`, after the Mapping DEs card and before its closing `</div>`, the layout is currently 2-column. We'll keep the grid 2-column and add the new card as a separate full-width card above the email card. Insert this between the closing `</div>` of `step-grid` and the Email-skabeloner card:

```html
<div class="card" style="margin-bottom:18px;">
    <div class="card-body">
        <div class="step-header">
            <span class="step-number">3</span>
            <span class="step-title">Naming files</span>
            <span class="step-hint">filnavngivning · valgfrit</span>
        </div>
        <p class="text-secondary text-sm" style="margin: -4px 0 14px;">
            Lookup-fil til filnavngivning. Joines på en valgt kolonne i preview, og
            navngiver de eksporterede PDF'er ud fra en skabelon som
            <code>{{ '{{CPR_NUMBER}}' }}_invoice</code>.
        </p>
        <label class="dropzone" id="dz-naming">
            <input type="file" accept=".csv,.xlsx" multiple>
            <div class="dropzone-icon">🏷️</div>
            <div class="dropzone-text">Klik eller træk filer hertil</div>
            <div class="dropzone-hint">.csv eller .xlsx</div>
        </label>
        <ul class="file-list" id="naming-list"></ul>
    </div>
</div>
```

Then update the existing Email-skabeloner card's `step-number` from `3` to `4`, and the DE-aliasses card's `step-number` from `4` to `5`.

- [ ] **Step 6.2: Wire the naming dropzone in JS**

In the same file, in the `<script>` block at the bottom, find the lines that grab dropzone refs:

```javascript
    const dzSender = document.getElementById('dz-sender');
    const dzMapping = document.getElementById('dz-mapping');
    const dzHtml = document.getElementById('dz-html');
```

Add below them:

```javascript
    const dzNaming = document.getElementById('dz-naming');
```

Find:

```javascript
    const senderList = document.getElementById('sender-list');
    const mappingList = document.getElementById('mapping-list');
    const htmlList = document.getElementById('html-list');
```

Add below:

```javascript
    const namingList = document.getElementById('naming-list');
```

Find the `loadFiles` function and update it:

```javascript
    async function loadFiles() {
        const resp = await fetch('/upload/files');
        const data = await resp.json();
        renderDataList(senderList, data.sender_files || [], 'Ingen sender-filer endnu.');
        renderDataList(mappingList, data.mapping_files || [], 'Ingen mapping-filer endnu.');
        renderDataList(namingList, data.naming_files || [], 'Ingen naming-filer endnu.');
        renderHtmlList(data.html_files || []);
    }
```

Find the `wireDropzone` calls at the bottom:

```javascript
    wireDropzone(dzSender, '/upload/sender-csv');
    wireDropzone(dzMapping, '/upload/lookup-csv');
    wireDropzone(dzHtml, '/upload/email-html');
```

Add below `wireDropzone(dzMapping, ...)`:

```javascript
    wireDropzone(dzNaming, '/upload/naming-csv');
```

- [ ] **Step 6.3: Manually verify**

Restart the dev server, navigate to http://localhost:5000/html2pdf. Verify:
- A new "Naming files" card with step number 3 appears.
- Email-skabeloner is now step 4, DE-aliasser is step 5.
- Drag/drop a CSV onto the naming dropzone; it appears in the naming list and not in sender/mapping lists.
- Refresh the page; the file persists.
- Delete the file via its delete button; it disappears.

- [ ] **Step 6.4: Commit**

```bash
git add src/templates/index.html
git commit -m "feat(html2pdf): naming files dropzone card on index page"
```

---

## Task 7: Preview page — naming config UI

**Files:**
- Modify: `src/templates/preview.html`

- [ ] **Step 7.1: Add markup for the naming config block**

Open `src/templates/preview.html`. Find the closing of the `preview-toolbar` div — the `</div>` immediately after `<button class="btn btn-sm btn-primary" id="btn-batch" ...>`. Just *after* that closing toolbar `</div>` and *before* the `<div class="preview-body">`, add:

```html
    <div class="preview-toolbar" id="naming-toolbar" style="border-top: 1px solid var(--border-light); padding: 10px 24px; gap: 8px;">
        <span style="font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-right: 4px;">Filnavngivning</span>
        <select id="sel-naming-file" disabled>
            <option value="">— Ingen (standardnavn) —</option>
        </select>
        <select id="sel-sender-key" style="display:none;">
            <option value="">— Sender-kolonne —</option>
        </select>
        <select id="sel-naming-key" style="display:none;">
            <option value="">— Naming-fil kolonne —</option>
        </select>
        <input type="text" id="inp-naming-template" placeholder="{{CPR_NUMBER}}_invoice" style="display:none; flex: 1; min-width: 220px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12.5px; color: var(--text);">
        <span id="naming-preview" style="display:none; font-size: 11.5px; color: var(--text-muted); font-family: 'JetBrains Mono', ui-monospace, monospace; flex-basis: 100%; padding-left: 4px;"></span>
    </div>
```

- [ ] **Step 7.2: Add JS state and DOM refs**

In the same file, in the `<script>` block, find the line `let state = {` (around line 291) and replace the state object with:

```javascript
    let state = {
        excelFile: initial.excelFile || '',
        sheetName: initial.sheetName || '',
        rowIndex: initial.rowIndex | 0,
        totalRows: initial.totalRows | 0,
        // Naming config
        namingFile: '',
        namingSheet: '',
        senderKey: '',
        namingKey: '',
        namingTemplate: '',
        // Cached columns
        senderColumns: [],
        namingColumns: [],
        currentRowData: null,
    };
```

Below the existing DOM ref grabs (after `const rowTableBody = ...`, around line 282), add:

```javascript
    const selNamingFile = document.getElementById('sel-naming-file');
    const selSenderKey = document.getElementById('sel-sender-key');
    const selNamingKey = document.getElementById('sel-naming-key');
    const inpNamingTemplate = document.getElementById('inp-naming-template');
    const namingPreview = document.getElementById('naming-preview');
```

- [ ] **Step 7.3: Add naming-config helpers**

In the same file, just before the `// ============ Init ============` comment (near the bottom of the script, around line 559), add:

```javascript
    // ============ Naming config ============
    const NAMING_LS_KEY = 'localsfmc:naming:' + initial.templateName;

    function loadNamingFromStorage() {
        try {
            const raw = localStorage.getItem(NAMING_LS_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) { return null; }
    }

    function saveNamingToStorage() {
        const payload = {
            namingFile: state.namingFile,
            namingSheet: state.namingSheet,
            senderKey: state.senderKey,
            namingKey: state.namingKey,
            namingTemplate: state.namingTemplate,
        };
        try { localStorage.setItem(NAMING_LS_KEY, JSON.stringify(payload)); } catch (_) {}
    }

    async function fetchColumns(file, sheet) {
        if (!file) return [];
        const u = new URL('/columns', window.location.origin);
        u.searchParams.set('file', file);
        if (sheet) u.searchParams.set('sheet', sheet);
        const resp = await fetch(u.toString());
        return resp.ok ? resp.json() : [];
    }

    function populateSelect(sel, options, currentValue, placeholder) {
        clearChildren(sel);
        sel.appendChild(el('option', { value: '', text: placeholder }));
        options.forEach(opt => sel.appendChild(el('option', { value: opt, text: opt })));
        if (currentValue && options.includes(currentValue)) sel.value = currentValue;
        else sel.value = '';
    }

    async function refreshNamingFiles() {
        const resp = await fetch('/upload/files');
        const data = await resp.json();
        const files = data.naming_files || [];
        clearChildren(selNamingFile);
        selNamingFile.appendChild(el('option', { value: '', text: '— Ingen (standardnavn) —' }));
        files.forEach(f => selNamingFile.appendChild(el('option', { value: f, text: f })));
        selNamingFile.disabled = files.length === 0 && !state.namingFile;
        if (state.namingFile && files.includes(state.namingFile)) selNamingFile.value = state.namingFile;
    }

    async function refreshSenderColumns() {
        if (!state.excelFile) {
            state.senderColumns = [];
            populateSelect(selSenderKey, [], '', '— Sender-kolonne —');
            return;
        }
        state.senderColumns = await fetchColumns(state.excelFile, state.sheetName);
        populateSelect(selSenderKey, state.senderColumns, state.senderKey, '— Sender-kolonne —');
    }

    async function refreshNamingColumns() {
        if (!state.namingFile) {
            state.namingColumns = [];
            populateSelect(selNamingKey, [], '', '— Naming-fil kolonne —');
            return;
        }
        state.namingColumns = await fetchColumns(state.namingFile, state.namingSheet);
        populateSelect(selNamingKey, state.namingColumns, state.namingKey, '— Naming-fil kolonne —');
    }

    function namingConfigured() {
        return Boolean(state.namingFile && state.senderKey && state.namingKey && state.namingTemplate);
    }

    function namingParams() {
        if (!namingConfigured()) return null;
        return {
            naming_file: state.namingFile,
            naming_sheet: state.namingSheet,
            sender_key: state.senderKey,
            naming_key: state.namingKey,
            naming_template: state.namingTemplate,
        };
    }

    function appendNamingParams(url) {
        const p = namingParams();
        if (!p) return;
        for (const [k, v] of Object.entries(p)) {
            if (v) url.searchParams.set(k, v);
        }
    }

    function setNamingVisibility() {
        const has = Boolean(state.namingFile);
        selSenderKey.style.display = has ? '' : 'none';
        selNamingKey.style.display = has ? '' : 'none';
        inpNamingTemplate.style.display = has ? '' : 'none';
        namingPreview.style.display = has ? '' : 'none';
    }

    function renderTemplateLocally(template, senderRow, lookupRow) {
        // Mirror the server's render_filename for live preview only.
        const merged = Object.assign({}, senderRow || {}, lookupRow || {});
        let s = String(template || '').replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (_, k) => {
            const v = merged[k];
            return v == null ? '' : String(v);
        });
        if (/\.pdf$/i.test(s)) s = s.slice(0, -4);
        s = Array.from(s).map(c => /[A-Za-z0-9._-]/.test(c) ? c : '_').join('');
        s = s.replace(/^_+|_+$/g, '').trim();
        return s ? s + '.pdf' : '';
    }

    async function updateNamingPreview() {
        if (!namingConfigured() || !state.currentRowData) {
            namingPreview.textContent = '';
            return;
        }
        // Fetch the lookup row so the preview reflects the actual matched values.
        const senderRow = state.currentRowData;
        const key = senderRow[state.senderKey];
        if (key === undefined || key === null || key === '') {
            namingPreview.textContent = '(ingen sender-værdi for ' + state.senderKey + ')';
            return;
        }
        // Look up the value in the naming file by fetching its rows. Cheap one-shot:
        // hit /columns first to confirm the file is reachable, then fetch all rows
        // via a tiny endpoint. Since we don't have one, do client-side preview using
        // the value of `senderRow[senderKey]` substituted as if it were the lookup
        // value — accurate when senderKey == namingKey and the lookup row's named
        // field equals the join value. For a richer preview, the user can run the
        // single-PDF download once.
        const fakeLookup = {};
        fakeLookup[state.namingKey] = String(key).trim();
        const rendered = renderTemplateLocally(state.namingTemplate, senderRow, fakeLookup);
        namingPreview.textContent = rendered
            ? 'Eksempel: ' + rendered + '  (faktisk navn afhænger af lookup-rækken)'
            : '(skabelon tom)';
    }

    selNamingFile.addEventListener('change', async () => {
        state.namingFile = selNamingFile.value;
        state.namingKey = '';  // reset on file change
        setNamingVisibility();
        await refreshNamingColumns();
        saveNamingToStorage();
        updateNamingPreview();
    });
    selSenderKey.addEventListener('change', () => {
        state.senderKey = selSenderKey.value;
        saveNamingToStorage();
        updateNamingPreview();
    });
    selNamingKey.addEventListener('change', () => {
        state.namingKey = selNamingKey.value;
        saveNamingToStorage();
        updateNamingPreview();
    });
    inpNamingTemplate.addEventListener('input', () => {
        state.namingTemplate = inpNamingTemplate.value;
        saveNamingToStorage();
        updateNamingPreview();
    });
```

- [ ] **Step 7.4: Hook naming params into existing URL builders**

Find `buildPreviewUrl` (around line 300):

```javascript
    function buildPreviewUrl(suffix) {
        const u = new URL('/preview/' + encodeURIComponent(initial.templateName) + (suffix || ''), window.location.origin);
        if (state.excelFile) u.searchParams.set('file', state.excelFile);
        if (state.sheetName) u.searchParams.set('sheet', state.sheetName);
        u.searchParams.set('row', String(state.rowIndex));
        return u.toString();
    }
```

Replace with:

```javascript
    function buildPreviewUrl(suffix) {
        const u = new URL('/preview/' + encodeURIComponent(initial.templateName) + (suffix || ''), window.location.origin);
        if (state.excelFile) u.searchParams.set('file', state.excelFile);
        if (state.sheetName) u.searchParams.set('sheet', state.sheetName);
        u.searchParams.set('row', String(state.rowIndex));
        // Apply naming params only to the /pdf suffix — not to /raw (which renders HTML).
        if (suffix === '/pdf') appendNamingParams(u);
        return u.toString();
    }
```

In `startBatch`, find:

```javascript
        const u = new URL('/batch-stream/' + encodeURIComponent(initial.templateName), window.location.origin);
        u.searchParams.set('file', state.excelFile);
        if (state.sheetName) u.searchParams.set('sheet', state.sheetName);
        evtSource = new EventSource(u.toString());
```

Replace with:

```javascript
        const u = new URL('/batch-stream/' + encodeURIComponent(initial.templateName), window.location.origin);
        u.searchParams.set('file', state.excelFile);
        if (state.sheetName) u.searchParams.set('sheet', state.sheetName);
        appendNamingParams(u);
        evtSource = new EventSource(u.toString());
```

- [ ] **Step 7.5: Track current row data and refresh sender columns when the sender file changes**

Find `loadSubscriberRow`:

```javascript
    async function loadSubscriberRow() {
        if (!state.excelFile || !state.totalRows) {
            clearChildren(rowTableBody);
            return;
        }
        const resp = await fetch(buildSubscriberUrl(state.rowIndex));
        const row = resp.ok ? await resp.json() : {};
        renderSubscriberRow(row);
    }
```

Replace with:

```javascript
    async function loadSubscriberRow() {
        if (!state.excelFile || !state.totalRows) {
            clearChildren(rowTableBody);
            state.currentRowData = null;
            updateNamingPreview();
            return;
        }
        const resp = await fetch(buildSubscriberUrl(state.rowIndex));
        const row = resp.ok ? await resp.json() : {};
        state.currentRowData = row;
        renderSubscriberRow(row);
        updateNamingPreview();
    }
```

In `onFileChange`, after the line `state.excelFile = file;` (around line 374), and again in the no-file branch where state is cleared, ensure sender columns get refreshed. The cleanest spot: at the very end of `onFileChange` (just before the closing `}`), add:

```javascript
        await refreshSenderColumns();
```

- [ ] **Step 7.6: Initialize naming config on page load**

Find the `// ============ Init ============` block at the bottom and replace it with:

```javascript
    // ============ Init ============
    if (initial.subscriberRow) {
        state.currentRowData = initial.subscriberRow;
        renderSubscriberRow(initial.subscriberRow);
    }

    const stored = loadNamingFromStorage();
    if (stored) {
        state.namingFile = stored.namingFile || '';
        state.namingSheet = stored.namingSheet || '';
        state.senderKey = stored.senderKey || '';
        state.namingKey = stored.namingKey || '';
        state.namingTemplate = stored.namingTemplate || '';
        if (state.namingTemplate) inpNamingTemplate.value = state.namingTemplate;
    }

    Promise.all([loadDataFiles(), refreshNamingFiles()]).then(async () => {
        updateControls();
        updateFrame();
        await refreshSenderColumns();
        await refreshNamingColumns();
        setNamingVisibility();
        if (state.totalRows && !initial.subscriberRow) await loadSubscriberRow();
        updateNamingPreview();
    });
```

- [ ] **Step 7.7: Manually verify the preview UI**

Restart the dev server. Open `http://localhost:5000/preview/welcome.html`:
- Pick a sender DE — sender-key dropdown populates with that file's columns.
- Pick the naming file — extra dropdown + template input + preview line appear.
- Pick the join columns; type `{{CPR_NUMBER}}_invoice`. The preview line shows an example filename.
- Click "Hent PDF" — the downloaded file should be named according to the template (matching Task 4).
- Refresh the page; selections should persist (localStorage).
- Clear the naming file selection — extra controls hide; the template URL no longer carries naming params; downloads revert to default name.

- [ ] **Step 7.8: Commit**

```bash
git add src/templates/preview.html
git commit -m "feat(html2pdf): naming-config UI on preview page (file/columns/template + live preview)"
```

---

## Task 8: Batch overlay — show skipped count

**Files:**
- Modify: `src/templates/preview.html`

- [ ] **Step 8.1: Track skipped count in batch state**

Open `src/templates/preview.html`. Find the `startBatch` function. Replace its current body so the SSE handler tracks skipped events and updates progress text accordingly. Locate:

```javascript
    function startBatch() {
        clearChildren(batchFiles);
        batchFill.style.width = '0%';
        batchPhase.textContent = 'Forbinder…';
        batchProgress.textContent = '0 / 0';
        overlay.classList.add('show');

        const u = new URL('/batch-stream/' + encodeURIComponent(initial.templateName), window.location.origin);
        u.searchParams.set('file', state.excelFile);
        if (state.sheetName) u.searchParams.set('sheet', state.sheetName);
        appendNamingParams(u);
        evtSource = new EventSource(u.toString());

        evtSource.onmessage = (e) => {
            let msg;
            try { msg = JSON.parse(e.data); } catch (_) { return; }
            if (msg.type === 'start') {
                batchPhase.textContent = 'Starter render af ' + msg.total + ' rækker…';
                batchProgress.textContent = '0 / ' + msg.total;
            } else if (msg.type === 'phase') {
                batchPhase.textContent = msg.message;
            } else if (msg.type === 'row') {
                const pct = Math.round((msg.index / msg.total) * 100);
                batchFill.style.width = pct + '%';
                batchProgress.textContent = `${msg.index} / ${msg.total}`;
                const line = document.createElement('div');
                line.textContent = msg.filename + (msg.identifier ? '  · ' + msg.identifier : '');
                batchFiles.appendChild(line);
                batchFiles.scrollTop = batchFiles.scrollHeight;
            } else if (msg.type === 'done') {
                batchPhase.textContent = 'Færdig — ' + msg.total + ' filer eksporteret.';
                batchFill.style.width = '100%';
                if (evtSource) { evtSource.close(); evtSource = null; }
            } else if (msg.type === 'error') {
                batchPhase.textContent = 'Fejl';
                const line = document.createElement('div');
                line.className = 'batch-error';
                line.textContent = msg.message;
                batchFiles.appendChild(line);
                if (evtSource) { evtSource.close(); evtSource = null; }
            }
        };
        evtSource.onerror = () => {
            batchPhase.textContent = 'Forbindelse afbrudt';
            if (evtSource) { evtSource.close(); evtSource = null; }
        };
    }
```

Replace with:

```javascript
    function startBatch() {
        clearChildren(batchFiles);
        batchFill.style.width = '0%';
        batchPhase.textContent = 'Forbinder…';
        batchProgress.textContent = '0 / 0';
        overlay.classList.add('show');

        let totalEligible = 0;
        let skippedTotal = 0;
        let skippedSeen = 0;

        const formatProgress = (done) => {
            const base = `${done} / ${totalEligible}`;
            return skippedTotal > 0 ? `${base}  (${skippedTotal} sprunget over)` : base;
        };

        const u = new URL('/batch-stream/' + encodeURIComponent(initial.templateName), window.location.origin);
        u.searchParams.set('file', state.excelFile);
        if (state.sheetName) u.searchParams.set('sheet', state.sheetName);
        appendNamingParams(u);
        evtSource = new EventSource(u.toString());

        evtSource.onmessage = (e) => {
            let msg;
            try { msg = JSON.parse(e.data); } catch (_) { return; }
            if (msg.type === 'start') {
                totalEligible = msg.total || 0;
                skippedTotal = msg.skipped_total || 0;
                const note = skippedTotal > 0
                    ? ` (${skippedTotal} sprunget over — ingen match i naming-fil)`
                    : '';
                batchPhase.textContent = 'Starter render af ' + totalEligible + ' rækker…' + note;
                batchProgress.textContent = formatProgress(0);
            } else if (msg.type === 'phase') {
                batchPhase.textContent = msg.message;
            } else if (msg.type === 'skip') {
                skippedSeen += 1;
                const line = document.createElement('div');
                line.style.color = 'var(--text-muted)';
                line.textContent = '⤬ række ' + (msg.index + 1) + ' sprunget over (key: ' + (msg.key || '∅') + ')';
                batchFiles.appendChild(line);
                batchFiles.scrollTop = batchFiles.scrollHeight;
            } else if (msg.type === 'row') {
                const pct = totalEligible ? Math.round((msg.index / totalEligible) * 100) : 0;
                batchFill.style.width = pct + '%';
                batchProgress.textContent = formatProgress(msg.index);
                const line = document.createElement('div');
                line.textContent = msg.filename + (msg.identifier ? '  · ' + msg.identifier : '');
                batchFiles.appendChild(line);
                batchFiles.scrollTop = batchFiles.scrollHeight;
            } else if (msg.type === 'done') {
                const skipNote = (msg.skipped_total || 0) > 0
                    ? ` · ${msg.skipped_total} sprunget over`
                    : '';
                batchPhase.textContent = 'Færdig — ' + (msg.total || 0) + ' filer eksporteret.' + skipNote;
                batchFill.style.width = '100%';
                if (evtSource) { evtSource.close(); evtSource = null; }
            } else if (msg.type === 'error') {
                batchPhase.textContent = 'Fejl';
                const line = document.createElement('div');
                line.className = 'batch-error';
                line.textContent = msg.message;
                batchFiles.appendChild(line);
                if (evtSource) { evtSource.close(); evtSource = null; }
            }
        };
        evtSource.onerror = () => {
            batchPhase.textContent = 'Forbindelse afbrudt';
            if (evtSource) { evtSource.close(); evtSource = null; }
        };
    }
```

- [ ] **Step 8.2: Manually verify the overlay**

Restart the dev server. Set up a sender DE with at least one row whose join key is *not* present in the naming file. Run a batch export with naming configured. The overlay should:
- Show `Starter render af N rækker… (M sprunget over — ingen match i naming-fil)` when `M > 0`.
- Display a muted "række X sprunget over" line for each skipped row.
- Update progress counter as `done / total_eligible (M sprunget over)`.
- Final phase shows the skip count when finished.

Run the same batch *without* naming configured: overlay should look identical to today (no skip note, no skip lines).

- [ ] **Step 8.3: Commit**

```bash
git add src/templates/preview.html
git commit -m "feat(html2pdf): batch overlay surfaces skipped-row count"
```

---

## Task 9: Acceptance walkthrough

- [ ] **Step 9.1: End-to-end manual test**

Restart the dev server. Walk through the spec's acceptance list:

1. On `/html2pdf`, drag a CSV with `CUST_ID,CPR_NUMBER` onto the **Naming files** card. Verify it appears under "Naming files" and is removable via its delete button.
2. Upload a sender DE with `CUST_ID,FirstName,Email` (a few rows; some matching, one not). Upload an HTML template.
3. Open the template's preview page. Pick the sender DE. Verify the "Filnavngivning" toolbar row appears.
4. Pick the naming file → second-row dropdowns appear. Pick `CUST_ID` for both sender and naming key. Type `{{CPR_NUMBER}}_invoice` into the template input. The preview line shows `Eksempel: <value>_invoice.pdf`.
5. Click **Hent PDF** for the first row. Downloaded file is named `{CPR_NUMBER}_invoice.pdf`. Try a row whose CUST_ID is missing from the naming file — single PDF still downloads using the default `{base}_row{N}.pdf` (since lookup miss falls back).
6. Click **Batch eksport**. Overlay shows `total - skipped` rows in progress. Output directory contains the matched PDFs only, named per the template; the unmatched row is skipped and surfaced.
7. Clear the naming file selection (set the dropdown back to "Ingen"). Re-run batch — file naming reverts to today's `{base}_{idx}_{email}.pdf` pattern. No regressions.
8. Refresh the preview page — naming config persists from localStorage.

- [ ] **Step 9.2: Re-run pure-module tests**

Run: `python tests/test_naming.py`
Expected: `All naming tests passed!`

- [ ] **Step 9.3: Final commit (only if any cleanup remained)**

```bash
git status
# If anything is dirty (config, comments, small fixes), commit it as a final cleanup.
# Otherwise this step is a no-op.
```

---

## Verification checklist

- [ ] `python tests/test_naming.py` passes
- [ ] Uploading a CSV via `/upload/naming-csv` adds it to `naming_files` and tags it `"naming"` in `_de_types.json`
- [ ] `/columns?file=…` returns the file's column names
- [ ] Single-row "Hent PDF" applies the template when configured; falls back when not
- [ ] Batch export emits `start.skipped_total`, `skip` events, and `done.skipped_total`
- [ ] Overlay shows skipped count and per-skip log lines
- [ ] localStorage persists naming config across reloads
- [ ] Removing the naming file selection restores all default behavior

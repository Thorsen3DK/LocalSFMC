# Naming-convention files for HTML2PDF

## Problem

Batch-exported PDFs are named with a hardcoded pattern:
`{base_name}_{idx+1}_{email-or-firstname}.pdf` (`src/web/routes/html2pdf/preview.py:214-224`).
The single-row export uses `{base_name}_row{N}.pdf`. Users need PDFs named
after fields that don't live in the sender DE — e.g. the sender DE has
`CUST_ID`, but the desired filename is `{CPR_NUMBER}_invoice.pdf`, where
`CPR_NUMBER` is in a separate lookup file.

## Goals

1. Upload a separate "naming file" that maps a sender column to one or more
   naming columns.
2. Specify a filename template like `{{CPR_NUMBER}}_invoice` at export time.
3. Apply the template in both batch and single-row PDF export.

## Non-goals

- Persisting the naming template per file. Template is per-export only.
- Multi-key joins. Exactly one sender column joins to exactly one naming-file
  column.
- Using naming files in AMPScript rendering. They are filename-only.
- Retroactive renaming of already-exported PDFs in `data/output/`.

## Design

### File type and storage

Naming files are a third DE type alongside `sender` and `mapping`. They are
stored in the existing `data/de/` directory and tracked in `_de_types.json`
with the value `"naming"`.

- New endpoint `POST /upload/naming-csv` calls `_save_data_upload("naming")`.
- `_classify_de_files` returns `(sender, mapping, naming)`. Untracked files
  continue to default to `mapping` to preserve existing behavior.
- `list_uploaded_files` returns a new `naming_files` array in addition to
  `sender_files` and `mapping_files`.
- Naming files are **not** included in the `data_extensions` dict passed to
  `ampscript_render`. They are loaded only when needed for naming.

### Index page UI

A fourth card joins Sender DEs / Mapping DEs / Email-skabeloner:

> **Naming files** — *valgfrit*
>
> Lookup-fil til filnavngivning. Joines på en valgt kolonne ved batch-eksport.
>
> [dropzone — .csv or .xlsx]

Same upload/list/delete UX as the other two data cards. Posts to
`/upload/naming-csv`.

### Preview page UI

A new "Filnavngivning" group in the preview toolbar, enabled only when a
sender DE is selected. Contents:

- **Naming file** dropdown. Empty option = `(ingen — brug standardnavn)`.
- When a naming file is selected, two more dropdowns appear:
  - **Sender-kolonne** — populated from sender DE columns.
  - **Naming-fil kolonne** — populated from naming-file columns.
- **Filnavn-skabelon** — text input, e.g. `{{CPR_NUMBER}}_invoice`.
- **Preview** line below the input shows the rendered filename for the
  currently-displayed row, updated live as the template is edited.

The whole config (file, two columns, template) is persisted in `localStorage`
keyed by template name, so it survives reload.

### Backend — naming module

New file `src/web/services/naming.py`. Pure functions, no Flask imports:

```python
def build_naming_index(
    filepath: str,
    sheet_name: str | None,
    naming_key_col: str,
) -> dict[str, dict]:
    """Load the naming file and index rows by their join-key value (as str)."""

def render_filename(
    template: str,
    sender_row: dict,
    lookup_row: dict,
) -> str:
    """Substitute {{KEY}} from merged dict (lookup wins on collision),
    sanitize using the same rule as the existing _row_filename, ensure exactly
    one .pdf extension."""
```

Sanitization rule is unchanged from today: keep `isalnum()` characters plus
`._-`, replace others with `_`. The function strips a trailing `.pdf` from
the template if the user types one, then appends `.pdf` itself.

### Wiring — preview and batch endpoints

`preview_pdf` and `batch_export_stream` accept four new optional query params:

- `naming_file` — filename of the naming file in `data/de/`
- `naming_sheet` — sheet name (XLSX only; empty for CSV)
- `sender_key` — column in the sender row used as the join key
- `naming_key` — column in the naming file used as the join key
- `naming_template` — the template string

If `naming_file`, `sender_key`, `naming_key`, and `naming_template` are all
present, the handler builds the naming index once at the top, then uses it
per row. `naming_sheet` is consulted only when the naming file is an XLSX.
Otherwise behavior is unchanged from today.

`batch_export_stream`'s `_row_filename` is replaced. For each row:

1. Compute `key = str(sender_row[sender_key])`.
2. `lookup = index.get(key)`.
3. If `lookup is None`: emit a new SSE event `{"type":"skip", "key": key, "index": idx}` and do not render the row.
4. Otherwise call `render_filename(template, sender_row, lookup)`.
5. If the rendered name is empty after sanitization, fall back to
   `{base}_{idx+1}.pdf`.
6. If the rendered name collides with one already produced in this batch,
   auto-suffix before the `.pdf` extension: `name.pdf` → `name_2.pdf` → `name_3.pdf`, …

The SSE start event also includes `total_eligible` (sender rows with a
matching lookup) so the progress bar reflects what will actually be
rendered.

### Frontend — batch overlay

The overlay's progress text becomes
`<done> / <total_eligible>  (<skipped> skipped)`. A small line above the
file list summarizes skips when the batch finishes:
`X rækker sprunget over (ingen match i naming file)`.

### Error handling

| Case | Behavior |
|------|----------|
| Naming file missing/deleted between selection and export | HTTP 400 with clear message |
| `sender_key` not in sender row, or `naming_key` not in naming-file rows | HTTP 400 returned before any rendering starts |
| Template references unknown `{{X}}` | Substituted as empty string; SSE warning event |
| Rendered filename empty after sanitization | Fallback `{base}_{idx+1}.pdf` |
| Filename collision across rows | Auto-suffix `_2`, `_3`, … |
| Lookup miss for a row | Row skipped, counted, surfaced in UI |

### Data flow (batch)

```
sender DE   ─┐
naming file ─┤── load once → index dict keyed by naming_key value
             │
             ▼
for each sender row:
    key = str(sender_row[sender_key])
    lookup = index.get(key)
    if lookup is None:
        emit "skip"; continue
    filename = render_filename(template, sender_row, lookup)
    if filename in seen: filename = disambiguate(filename)
    render PDF → write to data/output/{filename}
    emit "row"
```

## File changes

- `src/web/routes/html2pdf/upload.py` — new endpoint, extended classifier,
  `naming_files` in listing, naming type in `_de_types.json`
- `src/web/services/naming.py` — new module, pure helpers
- `src/web/routes/html2pdf/preview.py` — accept new query params, use naming
  module, emit `skip` SSE events, update `_row_filename` callsite
- `src/templates/index.html` — new "Naming files" card and dropzone
- `src/templates/preview.html` — naming config UI in toolbar, live preview,
  localStorage persistence, skip count in batch overlay

## Acceptance

- Upload a CSV with `CUST_ID,CPR_NUMBER` to the new Naming files card; the
  file appears in the list and is removable.
- On the preview page after picking a sender DE that has `CUST_ID`: select
  the naming file, pick `CUST_ID` on both sides, type
  `{{CPR_NUMBER}}_invoice`, see the live preview update.
- Batch export produces files named `<CPR>_invoice.pdf` for matched rows and
  surfaces the skip count for unmatched rows.
- Single-row "Hent PDF" honors the same template when configured.
- Removing the naming file selection reverts to current default naming with
  no other behavior change.

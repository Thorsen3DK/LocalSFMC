"""
AMPScript Built-in Function Registry.

All functions are case-insensitive and operate on the interpreter context.
Each function receives (args: list, context: InterpreterContext) and returns a value.
"""

from __future__ import annotations
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .interpreter import InterpreterContext


class AMPScriptRuntimeError(Exception):
    pass


# ---------------------------------------------------------------------------
# Function registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Callable] = {}


def register(name: str):
    """Decorator to register an AMPScript function (case-insensitive)."""
    def decorator(fn):
        _REGISTRY[name.lower()] = fn
        return fn
    return decorator


def call_function(name: str, args: list, ctx: "InterpreterContext") -> Any:
    fn = _REGISTRY.get(name.lower())
    if fn is None:
        raise AMPScriptRuntimeError(f"Unknown function: {name}")
    return fn(args, ctx)


# ---------------------------------------------------------------------------
# Output functions
# ---------------------------------------------------------------------------

@register("v")
def fn_v(args, ctx):
    """v(@var) — return variable value for inline output."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("v() requires exactly 1 argument")
    return args[0]


@register("output")
def fn_output(args, ctx):
    """Output(expr) — return value to be emitted."""
    if len(args) < 1:
        raise AMPScriptRuntimeError("Output() requires at least 1 argument")
    return args[0]


# ---------------------------------------------------------------------------
# Data Extension functions
# ---------------------------------------------------------------------------

_LOOKUP_INDEX_CACHE: Dict = {}


def _resolve_actual_key(rows: list, field_lower: str) -> Optional[str]:
    """Find the real (case-preserved) dict key for a lower-cased field name."""
    if not rows:
        return None
    sample = rows[0]
    if field_lower in sample:
        return field_lower
    for k in sample.keys():
        if k.lower() == field_lower:
            return k
    return None


def _get_field_index(rows: list, field_lower: str):
    """Build (and cache) a hash index {normalized_value -> [rows]} for a DE field.
    Cached by id(rows) so repeated lookups across many subscriber renders are O(1)."""
    cache_key = (id(rows), field_lower)
    cached = _LOOKUP_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    index: Dict[Any, list] = {}
    actual_key = _resolve_actual_key(rows, field_lower)
    if actual_key is not None:
        for row in rows:
            norm = _normalize_for_compare(row.get(actual_key))
            bucket = index.get(norm)
            if bucket is None:
                index[norm] = [row]
            else:
                bucket.append(row)
    _LOOKUP_INDEX_CACHE[cache_key] = index
    return index


def _row_field(row: dict, field_lower: str):
    """Case-insensitive single-row field access. Cheap fallback for non-indexed criteria."""
    val = row.get(field_lower)
    if val is not None:
        return val
    for k, v in row.items():
        if k.lower() == field_lower:
            return v
    return None


def _resolve_de(ctx, de_name: str) -> list:
    return ctx.data_extensions.get(de_name) or ctx.data_extensions.get(de_name.lower()) or []


@register("lookup")
def fn_lookup(args, ctx):
    """Lookup("DEName", "ReturnField", "SearchField1", SearchValue1, ...)"""
    if len(args) < 4 or len(args) % 2 != 0:
        raise AMPScriptRuntimeError("Lookup() requires DE name, return field, and search field/value pairs")
    de_name = str(args[0])
    return_field_lower = str(args[1]).lower()
    rows = _resolve_de(ctx, de_name)
    if not rows:
        return ""

    criteria = []
    for i in range(2, len(args), 2):
        criteria.append((str(args[i]).lower(), _normalize_for_compare(args[i + 1])))

    if not criteria:
        return ""

    first_field, first_value = criteria[0]
    candidates = _get_field_index(rows, first_field).get(first_value, [])
    if not candidates:
        return ""

    rest = criteria[1:]
    for row in candidates:
        ok = True
        for field, value in rest:
            if _normalize_for_compare(_row_field(row, field)) != value:
                ok = False
                break
        if ok:
            val = _row_field(row, return_field_lower)
            return val if val is not None else ""
    return ""


@register("lookuprows")
def fn_lookuprows(args, ctx):
    """LookupRows("DEName", "SearchField1", SearchValue1, ...)"""
    if len(args) < 3 or len(args) % 2 == 0:
        raise AMPScriptRuntimeError("LookupRows() requires DE name and search field/value pairs")
    de_name = str(args[0])
    rows = _resolve_de(ctx, de_name)
    if not rows:
        return []

    criteria = []
    for i in range(1, len(args), 2):
        criteria.append((str(args[i]).lower(), _normalize_for_compare(args[i + 1])))

    if not criteria:
        return list(rows)

    first_field, first_value = criteria[0]
    candidates = _get_field_index(rows, first_field).get(first_value, [])
    if len(criteria) == 1 or not candidates:
        return list(candidates)

    rest = criteria[1:]
    results = []
    for row in candidates:
        ok = True
        for field, value in rest:
            if _normalize_for_compare(_row_field(row, field)) != value:
                ok = False
                break
        if ok:
            results.append(row)
    return results


@register("row")
def fn_row(args, ctx):
    """Row(@rowset, index) — 1-based index into a rowset."""
    if len(args) != 2:
        raise AMPScriptRuntimeError("Row() requires 2 arguments")
    rowset = args[0]
    index = int(args[1])
    if not isinstance(rowset, list) or index < 1 or index > len(rowset):
        return {}
    return rowset[index - 1]


@register("field")
def fn_field(args, ctx):
    """Field(@row, "FieldName") — get a field value from a row dict."""
    if len(args) != 2:
        raise AMPScriptRuntimeError("Field() requires 2 arguments")
    row = args[0]
    field_name = str(args[1])
    if isinstance(row, dict):
        row_lower = {k.lower(): v for k, v in row.items()}
        return row_lower.get(field_name.lower(), "")
    return ""


@register("rowcount")
def fn_rowcount(args, ctx):
    """RowCount(@rowset) — return number of rows."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("RowCount() requires 1 argument")
    rowset = args[0]
    if isinstance(rowset, list):
        return float(len(rowset))
    return 0.0


@register("attributevalue")
def fn_attributevalue(args, ctx):
    """AttributeValue("FieldName") — retrieve from subscriber row."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("AttributeValue() requires 1 argument")
    field_name = str(args[0]).lower()
    row_lower = {k.lower(): v for k, v in ctx.subscriber_row.items()}
    return row_lower.get(field_name, "")


# ---------------------------------------------------------------------------
# String functions
# ---------------------------------------------------------------------------

@register("concat")
def fn_concat(args, ctx):
    return "".join(str(a) for a in args)


@register("uppercase")
def fn_uppercase(args, ctx):
    if len(args) != 1:
        raise AMPScriptRuntimeError("Uppercase() requires 1 argument")
    return str(args[0]).upper()


@register("lowercase")
def fn_lowercase(args, ctx):
    if len(args) != 1:
        raise AMPScriptRuntimeError("Lowercase() requires 1 argument")
    return str(args[0]).lower()


@register("propercase")
def fn_propercase(args, ctx):
    if len(args) != 1:
        raise AMPScriptRuntimeError("ProperCase() requires 1 argument")
    return str(args[0]).title()


@register("trim")
def fn_trim(args, ctx):
    if len(args) != 1:
        raise AMPScriptRuntimeError("Trim() requires 1 argument")
    return str(args[0]).strip()


@register("substring")
def fn_substring(args, ctx):
    """Substring(str, start, length) — 1-based start."""
    if len(args) != 3:
        raise AMPScriptRuntimeError("Substring() requires 3 arguments")
    s = str(args[0])
    start = int(args[1]) - 1  # AMPScript is 1-based
    length = int(args[2])
    return s[start:start + length]


@register("length")
def fn_length(args, ctx):
    if len(args) != 1:
        raise AMPScriptRuntimeError("Length() requires 1 argument")
    return float(len(str(args[0])))


@register("indexof")
def fn_indexof(args, ctx):
    """IndexOf(haystack, needle) — 1-based, 0 if not found."""
    if len(args) != 2:
        raise AMPScriptRuntimeError("IndexOf() requires 2 arguments")
    idx = str(args[0]).lower().find(str(args[1]).lower())
    return float(idx + 1) if idx >= 0 else 0.0


@register("replace")
def fn_replace(args, ctx):
    """Replace(str, old, new)"""
    if len(args) != 3:
        raise AMPScriptRuntimeError("Replace() requires 3 arguments")
    return str(args[0]).replace(str(args[1]), str(args[2]))


# ---------------------------------------------------------------------------
# Date functions
# ---------------------------------------------------------------------------

@register("now")
def fn_now(args, ctx):
    return datetime.now()


@register("format")
def fn_format(args, ctx):
    """Format(value, formatString) — basic .NET-style format for dates/numbers."""
    if len(args) < 2:
        raise AMPScriptRuntimeError("Format() requires at least 2 arguments")
    val = args[0]
    fmt = str(args[1])
    if isinstance(val, datetime):
        # Convert common .NET date tokens to Python strftime
        py_fmt = (
            fmt.replace("yyyy", "%Y").replace("yy", "%y")
            .replace("MMMM", "%B").replace("MMM", "%b").replace("MM", "%m")
            .replace("dd", "%d")
            .replace("HH", "%H").replace("hh", "%I")
            .replace("mm", "%M").replace("ss", "%S")
            .replace("tt", "%p")
        )
        return val.strftime(py_fmt)
    if isinstance(val, (int, float)):
        # Try numeric formatting
        try:
            return f"{val:{fmt}}"
        except (ValueError, KeyError):
            return str(val)
    return str(val)


@register("dateadd")
def fn_dateadd(args, ctx):
    """DateAdd(date, number, interval) — interval: 'D', 'H', 'MI', 'Y', 'M'."""
    if len(args) != 3:
        raise AMPScriptRuntimeError("DateAdd() requires 3 arguments")
    date_val = args[0]
    number = int(args[1])
    interval = str(args[2]).upper()
    if not isinstance(date_val, datetime):
        return date_val
    if interval == 'D':
        return date_val + timedelta(days=number)
    elif interval == 'H':
        return date_val + timedelta(hours=number)
    elif interval == 'MI':
        return date_val + timedelta(minutes=number)
    elif interval == 'Y':
        return date_val.replace(year=date_val.year + number)
    elif interval == 'M':
        month = date_val.month + number
        year = date_val.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date_val.replace(year=year, month=month)
    return date_val


# ---------------------------------------------------------------------------
# Math functions
# ---------------------------------------------------------------------------

@register("add")
def fn_add(args, ctx):
    if len(args) != 2:
        raise AMPScriptRuntimeError("Add() requires 2 arguments")
    return float(args[0]) + float(args[1])


@register("subtract")
def fn_subtract(args, ctx):
    if len(args) != 2:
        raise AMPScriptRuntimeError("Subtract() requires 2 arguments")
    return float(args[0]) - float(args[1])


@register("multiply")
def fn_multiply(args, ctx):
    if len(args) != 2:
        raise AMPScriptRuntimeError("Multiply() requires 2 arguments")
    return float(args[0]) * float(args[1])


@register("divide")
def fn_divide(args, ctx):
    if len(args) != 2:
        raise AMPScriptRuntimeError("Divide() requires 2 arguments")
    if float(args[1]) == 0:
        raise AMPScriptRuntimeError("Division by zero")
    return float(args[0]) / float(args[1])


# ---------------------------------------------------------------------------
# Logic / utility functions
# ---------------------------------------------------------------------------

@register("empty")
def fn_empty(args, ctx):
    """Empty(value) — returns True if value is None, empty string, or 0."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("Empty() requires 1 argument")
    val = args[0]
    if val is None:
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


@register("iif")
def fn_iif(args, ctx):
    """IIF(condition, trueValue, falseValue)"""
    if len(args) != 3:
        raise AMPScriptRuntimeError("IIF() requires 3 arguments")
    return args[1] if _is_truthy(args[0]) else args[2]


# ---------------------------------------------------------------------------
# Content block inclusion
# ---------------------------------------------------------------------------

@register("contentblockbyname")
def fn_contentblockbyname(args, ctx):
    """ContentBlockByName("filename") — include another email template file."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("ContentBlockByName() requires 1 argument")
    name = str(args[0])
    if ctx.content_block_loader:
        return ctx.content_block_loader(name, ctx)
    return f"[ContentBlock: {name}]"


@register("contentblockbyid")
def fn_contentblockbyid(args, ctx):
    """ContentBlockByID(id) — stub for content block inclusion by ID."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("ContentBlockByID() requires 1 argument")
    block_id = str(args[0])
    if ctx.content_block_loader:
        return ctx.content_block_loader(block_id, ctx)
    return f"[ContentBlockByID: {block_id}]"


@register("redirectto")
def fn_redirectto(args, ctx):
    """redirectto(url) — returns the URL (tracking wrapper stub)."""
    if len(args) != 1:
        raise AMPScriptRuntimeError("redirectto() requires 1 argument")
    return str(args[0]) if args[0] else "#"


# ---------------------------------------------------------------------------
# Crypto / encoding functions
# ---------------------------------------------------------------------------

@register("sha256")
def fn_sha256(args, ctx):
    """SHA256(value) — returns the SHA-256 hex digest of a string."""
    if len(args) < 1:
        raise AMPScriptRuntimeError("SHA256() requires at least 1 argument")
    value = str(args[0])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@register("guid")
def fn_guid(args, ctx):
    """GUID() — returns a new GUID string."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# URL / web functions
# ---------------------------------------------------------------------------

@register("cloudpagesurl")
def fn_cloudpagesurl(args, ctx):
    """CloudPagesURL(pageId, ...) — stub that returns a placeholder URL."""
    if len(args) < 1:
        raise AMPScriptRuntimeError("CloudPagesURL() requires at least 1 argument")
    page_id = str(args[0])
    # Build query params from additional key/value pairs
    params = ""
    for i in range(1, len(args) - 1, 2):
        sep = "&" if params else "?"
        params += f"{sep}{args[i]}={args[i + 1]}"
    return f"https://cloud.local/page/{page_id}{params}"


@register("httpget")
def fn_httpget(args, ctx):
    """HTTPGet(url) — stub that returns a placeholder response."""
    if len(args) < 1:
        raise AMPScriptRuntimeError("HTTPGet() requires at least 1 argument")
    url = str(args[0])
    return f"[HTTPGet: {url}]"


# ---------------------------------------------------------------------------
# Date conversion functions
# ---------------------------------------------------------------------------

@register("systemdatetolocaldate")
def fn_systemdatetolocaldate(args, ctx):
    """SystemDateToLocalDate(date) — stub that returns the date unchanged (local simulator)."""
    if len(args) < 1:
        raise AMPScriptRuntimeError("SystemDateToLocalDate() requires at least 1 argument")
    return args[0]


@register("formatdate")
def fn_formatdate(args, ctx):
    """FormatDate(date, format, [datepart]) — formats a date value."""
    if len(args) < 2:
        raise AMPScriptRuntimeError("FormatDate() requires at least 2 arguments")
    val = args[0]
    fmt = str(args[1]).upper()
    # Parse string to datetime if needed
    if isinstance(val, str):
        for pattern in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                val = datetime.strptime(val, pattern)
                break
            except ValueError:
                continue
    if not isinstance(val, datetime):
        return str(args[0])
    # Handle shorthand format codes
    if fmt == "SHORT":
        return val.strftime("%m/%d/%Y")
    elif fmt == "LONG":
        return val.strftime("%B %d, %Y")
    elif fmt == "SHORTTIME":
        return val.strftime("%I:%M %p")
    elif fmt == "LONGTIME":
        return val.strftime("%I:%M:%S %p")
    elif fmt == "SHORTDATE":
        return val.strftime("%m/%d/%Y")
    elif fmt == "LONGDATE":
        return val.strftime("%B %d, %Y")
    # Custom pattern — reuse Format() logic
    py_fmt = (
        str(args[1]).replace("yyyy", "%Y").replace("yy", "%y")
        .replace("MMMM", "%B").replace("MMM", "%b").replace("MM", "%m")
        .replace("dd", "%d")
        .replace("HH", "%H").replace("hh", "%I")
        .replace("mm", "%M").replace("ss", "%S")
        .replace("tt", "%p")
    )
    return val.strftime(py_fmt)


@register("stringtodate")
def fn_stringtodate(args, ctx):
    """StringToDate(dateString) — parse a date string into a datetime object."""
    if len(args) < 1:
        raise AMPScriptRuntimeError("StringToDate() requires at least 1 argument")
    val = args[0]
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    # Try common date formats
    for pattern in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%b %d %Y %I:%M%p",
        "%b  %d %Y %I:%M:%S%p",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(s, pattern)
        except ValueError:
            continue
    # Last resort: try dateutil-style parsing
    # Strip time component like "12:00AM"
    for pattern in ("%b %d %Y", "%b  %d %Y"):
        try:
            return datetime.strptime(s.split(" 12:00")[0].strip() if "12:00" in s else s, pattern)
        except ValueError:
            continue
    raise AMPScriptRuntimeError(f"StringToDate() cannot parse: {val}")


@register("datediff")
def fn_datediff(args, ctx):
    """DateDiff(date1, date2, interval) — returns date2 - date1 in the given interval."""
    if len(args) < 3:
        raise AMPScriptRuntimeError("DateDiff() requires 3 arguments")
    date1 = args[0]
    date2 = args[1]
    interval = str(args[2]).upper()
    # Parse strings if needed
    if isinstance(date1, str):
        date1 = fn_stringtodate([date1], ctx)
    if isinstance(date2, str):
        date2 = fn_stringtodate([date2], ctx)
    if not isinstance(date1, datetime) or not isinstance(date2, datetime):
        return 0
    delta = date2 - date1
    if interval == 'D':
        return delta.days
    elif interval == 'H':
        return int(delta.total_seconds() // 3600)
    elif interval == 'MI':
        return int(delta.total_seconds() // 60)
    elif interval == 'S':
        return int(delta.total_seconds())
    elif interval == 'Y':
        return date2.year - date1.year
    elif interval == 'M':
        return (date2.year - date1.year) * 12 + (date2.month - date1.month)
    return delta.days


# ---------------------------------------------------------------------------
# Data Extension write functions
# ---------------------------------------------------------------------------

@register("insertde")
def fn_insertde(args, ctx):
    """InsertDE("DEName", "Field1", Value1, ...) — inserts a row into a DE."""
    if len(args) < 3 or len(args) % 2 == 0:
        raise AMPScriptRuntimeError("InsertDE() requires DE name and field/value pairs")
    de_name = str(args[0])
    row = {}
    for i in range(1, len(args), 2):
        row[str(args[i])] = args[i + 1]
    # Append to data extensions in context
    de_key = de_name.lower()
    for key in ctx.data_extensions:
        if key.lower() == de_key:
            de_key = key
            break
    if de_key not in ctx.data_extensions:
        ctx.data_extensions[de_key] = []
    ctx.data_extensions[de_key].append(row)
    return len(ctx.data_extensions[de_key])


@register("upsertde")
def fn_upsertde(args, ctx):
    """UpsertDE("DEName", count, "KeyField1", KeyValue1, ..., "Field1", Value1, ...)"""
    if len(args) < 4:
        raise AMPScriptRuntimeError("UpsertDE() requires at least 4 arguments")
    de_name = str(args[0])
    key_count = int(args[1])
    # Extract key pairs
    keys = {}
    idx = 2
    for _ in range(key_count):
        keys[str(args[idx]).lower()] = args[idx + 1]
        idx += 2
    # Extract value pairs
    values = {}
    while idx < len(args) - 1:
        values[str(args[idx])] = args[idx + 1]
        idx += 2
    # Find or create DE
    de_key = de_name.lower()
    for key in ctx.data_extensions:
        if key.lower() == de_key:
            de_key = key
            break
    if de_key not in ctx.data_extensions:
        ctx.data_extensions[de_key] = []
    # Try to find existing row
    rows = ctx.data_extensions[de_key]
    for row in rows:
        row_lower = {k.lower(): v for k, v in row.items()}
        if all(row_lower.get(k) == v for k, v in keys.items()):
            row.update(values)
            return 1
    # Insert new row
    new_row = {**{k: v for k, v in keys.items()}, **values}
    rows.append(new_row)
    return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip() != "" and val.lower() != "false"
    if val is None:
        return False
    return True


def _normalize_for_compare(val):
    """Normalize a value for comparison (case-insensitive strings, numeric coercion)."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip().lower()
    if isinstance(val, (int, float)):
        return float(val)
    return str(val).strip().lower()

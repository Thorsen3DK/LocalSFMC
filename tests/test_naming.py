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

    # Duplicate keys: last row wins
    dup_csv_path = os.path.join(tmp, "naming_dup.csv")
    with open(dup_csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["CUST_ID", "CPR_NUMBER"])
        w.writerow(["1", "0101010001"])
        w.writerow(["1", "0202020002"])

    dup_idx = build_naming_index(dup_csv_path, None, "CUST_ID")
    assert_eq(dup_idx["1"]["CPR_NUMBER"], "0202020002", "build_naming_index duplicate keys: last wins")

    # Missing join column → ValueError with column name
    try:
        build_naming_index(csv_path, None, "DOES_NOT_EXIST")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "DOES_NOT_EXIST" in str(e), f"error message should mention column: {e}"
        print("  PASS build_naming_index raises ValueError on missing key column")


print("\nAll naming tests passed!")

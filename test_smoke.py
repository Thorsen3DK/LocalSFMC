"""Quick smoke test for the AMPScript engine."""

from ampscript.interpreter import render

# Test 1: Personalization strings + SET + function calls
html = render(
    template_source='<h1>Hello %%FirstName%%!</h1><p>%%[SET @x = Uppercase(AttributeValue("FirstName"))]%%%%=v(@x)=%%</p>',
    subscriber_row={"FirstName": "Alice", "Email": "alice@example.com"},
    data_extensions={},
)
print("Test 1:", html)
assert "Hello Alice!" in html
assert "ALICE" in html
print("  PASS")

# Test 2: IF/ELSE
html2 = render(
    template_source='%%[IF 1 == 1 THEN]%%YES%%[ELSE]%%NO%%[ENDIF]%%',
    subscriber_row={},
    data_extensions={},
)
print("Test 2:", html2)
assert html2 == "YES"
print("  PASS")

# Test 3: Lookup
html3 = render(
    template_source='%%=Lookup("Products", "Price", "Name", "Widget")=%%',
    subscriber_row={},
    data_extensions={"Products": [{"Name": "Widget", "Price": "9.99"}, {"Name": "Gizmo", "Price": "14.99"}]},
)
print("Test 3:", html3)
assert html3 == "9.99"
print("  PASS")

# Test 4: FOR loop with LookupRows
template = '''%%[
SET @rows = LookupRows("Items", "Active", "Yes")
SET @count = RowCount(@rows)
FOR @i = 1 TO @count DO
    SET @row = Row(@rows, @i)
    SET @name = Field(@row, "Name")
]%%%%=v(@name)=%% %%[
NEXT @i
]%%'''
html4 = render(
    template_source=template,
    subscriber_row={},
    data_extensions={"Items": [
        {"Name": "Apple", "Active": "Yes"},
        {"Name": "Banana", "Active": "Yes"},
        {"Name": "Cherry", "Active": "No"},
    ]},
)
print("Test 4:", repr(html4))
assert "Apple" in html4
assert "Banana" in html4
assert "Cherry" not in html4
print("  PASS")

# Test 5: Nested IF with tier logic
template5 = '''%%[
SET @tier = AttributeValue("Tier")
IF @tier == "Gold" THEN
    SET @msg = "VIP"
ELSEIF @tier == "Silver" THEN
    SET @msg = "Premium"
ELSE
    SET @msg = "Standard"
ENDIF
]%%%%=v(@msg)=%%'''
for tier, expected in [("Gold", "VIP"), ("Silver", "Premium"), ("Bronze", "Standard")]:
    result = render(template5, {"Tier": tier}, {})
    print(f"Test 5 ({tier}):", result)
    assert result == expected
    print("  PASS")

# Test 6: Full sample email template
with open("emails/welcome_email.html", "r", encoding="utf-8") as f:
    sample_template = f.read()

from data.excel_loader import load_all, get_send_list
all_des = load_all("data_extensions")
send_list = get_send_list("data_extensions", "sample_data.xlsx", "Subscribers")

print(f"\nLoaded DEs: {list(all_des.keys())}")
print(f"Send list rows: {len(send_list)}")

for i, row in enumerate(send_list):
    result = render(sample_template, row, all_des)
    has_error = "[AMPScript Error" in result
    print(f"Row {i} ({row.get('FirstName')}): {len(result)} chars, errors={has_error}")
    if has_error:
        # Print errors
        import re
        for err in re.findall(r'\[AMPScript Error:.*?\]', result):
            print(f"  {err}")

print("\nAll tests passed!")

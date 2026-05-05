"""Quick check of rendering issues."""
from ampscript.interpreter import render
from data.excel_loader import load_all, get_send_list

all_des = load_all('data_extensions')

# Render test.html with customers.xlsx
send_list = get_send_list('data_extensions', 'customers.xlsx')

with open('emails/test.html', 'r', encoding='utf-8') as f:
    template = f.read()

result = render(template, send_list[0], all_des)

import re
errors = re.findall(r'\[AMPScript (?:Error|Parse Error):.*?\]', result)
if errors:
    print(f"Errors ({len(errors)}):")
    for e in errors:
        print(f"  {e}")
else:
    print("No errors found!")

# Show the table area
idx = result.find('<table cellspacing="" cellpadding="" border="0" id="data">')
if idx > 0:
    end = result.find('</table>', idx) + 8
    print(f"\nRendered table:\n{result[idx:end]}")

with open('output/test_render.html', 'w', encoding='utf-8') as f:
    f.write(result)
print("\nSaved to output/test_render.html")

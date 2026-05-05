"""Test the real template rendering."""
from ampscript.interpreter import render
from data.excel_loader import load_all, get_send_list

all_des = load_all('data_extensions')
send_list = get_send_list('data_extensions', 'sample_data.xlsx', 'Subscribers')

with open('emails/welcome_email.html', 'r', encoding='utf-8') as f:
    template = f.read()

result = render(template, send_list[0], all_des)

import re
errors = re.findall(r'\[AMPScript (?:Error|Parse Error):.*?\]', result)
if errors:
    print(f'Found {len(errors)} errors:')
    for e in errors:
        print(f'  {e}')
else:
    print('No errors!')

checks = ['Hej', 'Ny pris', 'abonnement', 'Tlf. nr.', 'Dit abonnement', 'Har du brug for']
for c in checks:
    print(f'  Contains "{c}": {c in result}')
print(f'Output length: {len(result)} chars')

with open('output/debug_render.html', 'w', encoding='utf-8') as f:
    f.write(result)
print('Saved to output/debug_render.html')

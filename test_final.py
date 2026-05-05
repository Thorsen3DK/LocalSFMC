"""Quick check of rendered content with customers.xlsx."""
from ampscript.interpreter import render
from data.excel_loader import load_all, get_send_list

all_des = load_all('data_extensions')
send_list = get_send_list('data_extensions', 'customers.xlsx')

with open('emails/welcome_email.html', 'r', encoding='utf-8') as f:
    template = f.read()

result = render(template, send_list[0], all_des)

import re
errors = re.findall(r'\[AMPScript (?:Error|Parse Error):.*?\]', result)
print(f"Errors: {len(errors)}")
for e in errors:
    print(f"  {e}")

# Check the DINGO_SUBS lookup result
cust_id = send_list[0].get('CUST_ID', 'N/A')
print(f"\nSubscriber CUST_ID: {cust_id}")
print(f"DINGO_SUBS DE exists: {'DINGO_SUBS' in all_des}")
if 'DINGO_SUBS' in all_des:
    rows = all_des['DINGO_SUBS']
    print(f"DINGO_SUBS total rows: {len(rows)}")
    # Check if any rows match this CUST_ID
    matching = [r for r in rows if str(r.get('cust_id', '')).lower() == str(cust_id).lower()]
    print(f"Matching rows for CUST_ID: {len(matching)}")
    if matching:
        print(f"  First match: {matching[0]}")

# Show key content
for label, search in [
    ("Greeting", "Hej"),
    ("Headline", "Ny pris"),  
    ("Phone number", "Tlf. nr."),
    ("Data row", "valign=\"top\""),
    ("Footer section", "Dit abonnement"),
]:
    idx = result.find(search)
    if idx >= 0:
        snippet = result[max(0,idx-10):idx+60].replace('\n', ' ')
        print(f"  {label}: ...{snippet}...")
    else:
        print(f"  {label}: NOT FOUND")

with open('output/debug_customers.html', 'w', encoding='utf-8') as f:
    f.write(result)
print(f"\nSaved {len(result)} chars to output/debug_customers.html")

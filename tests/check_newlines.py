"""Show rendered output structure to find newline issues."""
from ampscript.interpreter import render
from data.excel_loader import load_all, get_send_list

all_des = load_all('data_extensions')
send_list = get_send_list('data_extensions', 'customers.xlsx')

with open('emails/test.html', 'r', encoding='utf-8') as f:
    template = f.read()

result = render(template, send_list[0], all_des)

# Find the data table area and show through to "Har du brug"
idx = result.find('id="data"')
idx2 = result.find('Har du brug')
snippet = result[idx:idx2]
lines = snippet.split('\n')
for i, line in enumerate(lines):
    if line.strip():
        print(f'{i:3}: |{line.rstrip()}|')
    else:
        print(f'{i:3}: [EMPTY]')

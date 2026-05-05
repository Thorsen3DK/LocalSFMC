"""Debug the real template rendering step by step."""
from ampscript.interpreter import render, _DocBuilder, _exec_doc_nodes, InterpreterContext
from ampscript.interpreter import DocLiteral, DocPersonalization, DocInlineExpr, DocSet, DocVar, DocOutput, DocIf, DocFor
from ampscript import lexer
from data.excel_loader import load_all, get_send_list

# Load data
all_des = load_all('data_extensions')
print("Available DEs:", list(all_des.keys()))
for name, rows in all_des.items():
    if rows:
        print(f"  {name}: {len(rows)} rows, columns: {list(rows[0].keys())}")
    else:
        print(f"  {name}: 0 rows")

# Try with customers.xlsx
import os
for xlsx_file in ['customers.xlsx', 'sample_data.xlsx']:
    path = os.path.join('data_extensions', xlsx_file)
    if os.path.exists(path):
        sl = get_send_list('data_extensions', xlsx_file)
        print(f"\n{xlsx_file} send list: {len(sl)} rows")
        if sl:
            print(f"  First row keys: {list(sl[0].keys())}")
            print(f"  First row sample: { {k: v for k, v in list(sl[0].items())[:5]} }")

# Load template
with open('emails/welcome_email.html', 'r', encoding='utf-8') as f:
    template = f.read()

# Tokenize and show token summary
tokens = lexer.tokenize(template)
print(f"\n--- Lexer tokens: {len(tokens)} ---")
for i, tok in enumerate(tokens):
    if tok.type == lexer.TokenType.LITERAL:
        text = tok.value.strip()[:80]
        if text:
            print(f"  [{i}] LITERAL: {repr(text)}...")
    elif tok.type == lexer.TokenType.BLOCK_CODE:
        code = tok.value[:100].replace('\n', ' ').strip()
        print(f"  [{i}] BLOCK_CODE: {code}...")
    elif tok.type == lexer.TokenType.INLINE_EXPR:
        print(f"  [{i}] INLINE_EXPR: {tok.value}")
    elif tok.type == lexer.TokenType.PERSONALIZATION_STRING:
        print(f"  [{i}] PERS_STRING: %%{tok.value}%%")

# Build document AST
builder = _DocBuilder(tokens)
doc_nodes = builder.build()

def describe_node(node, indent=0):
    prefix = "  " * indent
    if isinstance(node, DocLiteral):
        text = node.text.strip()[:60]
        if text:
            print(f"{prefix}Literal: {repr(text)}")
    elif isinstance(node, DocPersonalization):
        print(f"{prefix}Personalization: %%{node.field_name}%%")
    elif isinstance(node, DocInlineExpr):
        print(f"{prefix}InlineExpr: {node.expression}")
    elif isinstance(node, DocSet):
        print(f"{prefix}Set @{node.variable}")
    elif isinstance(node, DocVar):
        print(f"{prefix}Var {node.variables}")
    elif isinstance(node, DocOutput):
        print(f"{prefix}Output: {node.expression}")
    elif isinstance(node, DocIf):
        for j, branch in enumerate(node.branches):
            label = "If" if j == 0 else "ElseIf"
            print(f"{prefix}{label}: {branch.condition}")
            print(f"{prefix}  Body ({len(branch.body)} nodes):")
            for child in branch.body:
                describe_node(child, indent + 2)
        if node.else_body:
            print(f"{prefix}Else ({len(node.else_body)} nodes):")
            for child in node.else_body:
                describe_node(child, indent + 2)
    elif isinstance(node, DocFor):
        print(f"{prefix}For @{node.variable} = {node.start} TO {node.end}")
        print(f"{prefix}  Body ({len(node.body)} nodes):")
        for child in node.body:
            describe_node(child, indent + 2)
    else:
        print(f"{prefix}Unknown: {type(node).__name__}")

print(f"\n--- Document AST: {len(doc_nodes)} top-level nodes ---")
for node in doc_nodes:
    describe_node(node)

# Render with first row from customers.xlsx (if available)
for xlsx_file in ['customers.xlsx', 'sample_data.xlsx']:
    sl = get_send_list('data_extensions', xlsx_file)
    if sl:
        print(f"\n--- Render with {xlsx_file}, row 0 ---")
        result = render(template, sl[0], all_des)
        
        import re
        errors = re.findall(r'\[AMPScript (?:Error|Parse Error):.*?\]', result)
        if errors:
            print(f"  Errors ({len(errors)}):")
            for e in errors:
                print(f"    {e}")
        
        # Check for content sections
        checks = ['Hej', 'Ny pris', 'Dit abonnement', 'Har du brug for', 
                   'phone_number', 'Ekstra Bruger', 'ContentBlock']
        for c in checks:
            print(f"  Contains '{c}': {c in result}")
        
        # Show a rendered snippet around "Hej" or first <span>
        idx = result.find('Hej')
        if idx >= 0:
            print(f"  Around 'Hej': ...{result[max(0,idx-20):idx+80]}...")
        else:
            # Find the content area
            idx = result.find('padding: 30px 15px 20px')
            if idx >= 0:
                snippet = result[idx:idx+300]
                print(f"  Content area: ...{snippet}...")
        
        with open(f'output/debug_{xlsx_file.replace(".xlsx","")}.html', 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"  Saved to output/debug_{xlsx_file.replace('.xlsx','')}.html")
        break

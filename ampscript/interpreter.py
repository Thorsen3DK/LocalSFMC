"""
AMPScript Interpreter — Processes the full template token stream and produces
rendered HTML.

Key design: AMPScript constructs (IF/ELSE/ENDIF, FOR/NEXT) can span across
multiple %%[ ]%% blocks with HTML literals in between. The interpreter builds
a "document-level" AST by consuming the token stream, where HTML literals
inside an IF body or FOR loop become part of that construct's body.

Usage:
    from ampscript.interpreter import render

    html = render(
        template_source='%%[IF @x == 1 THEN]%%<p>Yes</p>%%[ENDIF]%%',
        subscriber_row={"x": "1"},
        data_extensions={},
    )
"""

from __future__ import annotations

import os
import re as re_mod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import lexer
from .lexer import Token, TokenType
from . import parser as amp_parser
from .parser import (
    BinaryOp, BooleanLiteral, Expression, FunctionCall,
    NumericLiteral, ParseError, StringLiteral, UnaryOp,
    VariableRef, _tokenize_code, CodeToken,
)
from .functions import AMPScriptRuntimeError, call_function, _is_truthy


# ---------------------------------------------------------------------------
# Document-level AST nodes (wraps parser-level nodes + HTML)
# ---------------------------------------------------------------------------

@dataclass
class DocLiteral:
    """Raw HTML passthrough."""
    text: str

@dataclass
class DocPersonalization:
    """%%FieldName%% personalization string."""
    field_name: str

@dataclass
class DocInlineExpr:
    """%%=expr=%% inline expression."""
    expression: Expression

@dataclass
class DocSet:
    variable: str
    value: Expression

@dataclass
class DocVar:
    variables: List[str]

@dataclass
class DocOutput:
    expression: Expression

@dataclass
class DocIfBranch:
    condition: Expression
    body: List[DocNode]

@dataclass
class DocIf:
    branches: List[DocIfBranch]
    else_body: List[DocNode]

@dataclass
class DocFor:
    variable: str
    start: Expression
    end: Expression
    body: List[DocNode]


DocNode = (
    DocLiteral | DocPersonalization | DocInlineExpr |
    DocSet | DocVar | DocOutput | DocIf | DocFor
)


# ---------------------------------------------------------------------------
# Document builder — converts lexer tokens into a document-level AST.
# This handles constructs that span multiple %%[ ]%% blocks.
# ---------------------------------------------------------------------------

class _DocBuilder:
    """
    Consumes the lexer token stream and builds a list of DocNodes.

    When a BLOCK_CODE token contains an opening keyword (IF...THEN, FOR...DO)
    without its closing counterpart, the builder continues consuming subsequent
    tokens (literals, inline exprs, and more blocks) as the body of that construct,
    until it finds the matching closing keyword (ENDIF, NEXT).
    """

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def build(self) -> List[DocNode]:
        return self._parse_doc_nodes(stop_keywords=set())

    def _parse_doc_nodes(self, stop_keywords: set) -> List[DocNode]:
        nodes: List[DocNode] = []
        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]

            if token.type == TokenType.LITERAL:
                nodes.append(DocLiteral(token.value))
                self.pos += 1

            elif token.type == TokenType.PERSONALIZATION_STRING:
                nodes.append(DocPersonalization(token.value))
                self.pos += 1

            elif token.type == TokenType.INLINE_EXPR:
                expr = amp_parser.parse_inline_expression(token.value)
                nodes.append(DocInlineExpr(expr))
                self.pos += 1

            elif token.type == TokenType.BLOCK_CODE:
                code_tokens = _tokenize_code(token.value)
                if not code_tokens:
                    self.pos += 1
                    continue

                first = code_tokens[0]
                first_kw = first.value.upper() if first.type == 'KEYWORD' else ''

                # Check if this block starts with a stop keyword
                if first_kw in stop_keywords:
                    break  # Don't consume — let caller handle it

                # Process code tokens statement by statement within this block.
                # A single block can contain SET statements followed by IF/FOR.
                self.pos += 1
                new_nodes = self._process_code_block(code_tokens, stop_keywords)
                nodes.extend(new_nodes)
            else:
                self.pos += 1

        return nodes

    def _consume_block_keyword(self, keyword: str):
        """
        Consume a keyword (NEXT, ENDIF, etc.) from the current block token.

        If the block contains additional code after the keyword (e.g., 'NEXT @i\\nENDIF'),
        only the keyword is consumed and the token is modified in-place to contain the
        remaining code. Otherwise the entire block token is consumed (pos advanced).
        """
        if self.pos >= len(self.tokens):
            return
        token = self.tokens[self.pos]
        if token.type != TokenType.BLOCK_CODE:
            return

        code_tokens = _tokenize_code(token.value)
        if not code_tokens:
            self.pos += 1
            return

        p = amp_parser._Parser(code_tokens)
        first = p._peek()
        if not first or first.type != 'KEYWORD' or first.value.upper() != keyword.upper():
            self.pos += 1
            return

        p._advance()  # consume the keyword

        # NEXT can have an optional @var
        if keyword.upper() == 'NEXT' and p._peek() and p._peek().type == 'VAR':
            p._advance()

        if p._peek() is not None:
            # Remaining tokens exist — trim the block to contain only what's left
            remaining_pos = code_tokens[p.pos].pos
            remaining_code = token.value[remaining_pos:].strip()
            if remaining_code:
                self.tokens[self.pos] = Token(TokenType.BLOCK_CODE, remaining_code, token.pos)
                return

        # Fully consumed
        self.pos += 1

    def _process_code_block(self, code_tokens: List[CodeToken], stop_keywords: set) -> List[DocNode]:
        """
        Process a code block's tokens statement by statement.
        Handles blocks that mix flat statements (SET/VAR) with structural
        constructs (IF/FOR) in the same %%[ ]%% block.
        """
        nodes: List[DocNode] = []
        p = amp_parser._Parser(code_tokens)

        while p._peek() is not None:
            tok = p._peek()
            kw = tok.value.upper() if tok.type == 'KEYWORD' else ''

            if kw == 'SET':
                stmt = p._parse_set()
                nodes.append(DocSet(stmt.variable, stmt.value))
            elif kw == 'VAR':
                stmt = p._parse_var()
                nodes.append(DocVar(stmt.variables))
            elif kw == 'OUTPUT':
                stmt = p._parse_output_stmt()
                nodes.append(DocOutput(stmt.expression))
            elif kw == 'IF':
                # Parse IF...THEN, then remaining tokens in this block + subsequent
                # template tokens form the body (cross-block).
                node = self._parse_if_from_parser(p)
                nodes.append(node)
            elif kw == 'FOR':
                node = self._parse_for_from_parser(p)
                nodes.append(node)
            elif tok.type == 'FUNC':
                expr = p._parse_expression()
                nodes.append(DocOutput(expr))
            else:
                p._advance()  # skip unknown

        return nodes

    def _parse_if_from_parser(self, p: amp_parser._Parser) -> DocIf:
        """
        Parse an IF construct. Handles both:
        1. Entire IF/ELSEIF/ELSE/ENDIF within a single code block (parser p)
        2. IF that spans across multiple %%[ ]%% blocks (cross-block)
        """
        p._expect('KEYWORD', 'IF')
        condition = p._parse_expression()
        p._expect('KEYWORD', 'THEN')

        # Collect IF body: first from remaining tokens in current parser,
        # then from subsequent template tokens (cross-block)
        trailing = self._collect_if_body_from_parser(p)
        if self._parser_has_keyword(p, ('ELSEIF', 'ELSE', 'ENDIF')):
            # Entire construct is in the same code block
            body_nodes = trailing
            branches = [DocIfBranch(condition=condition, body=body_nodes)]
            else_body: List[DocNode] = []
            return self._finish_if_inline(p, branches, else_body)
        else:
            # Cross-block: collect from subsequent template tokens
            body_nodes = trailing + self._parse_doc_nodes(stop_keywords={'ELSEIF', 'ELSE', 'ENDIF'})
            branches = [DocIfBranch(condition=condition, body=body_nodes)]
            else_body: List[DocNode] = []
            return self._finish_if_crossblock(branches, else_body)

    def _collect_if_body_from_parser(self, p: amp_parser._Parser) -> List[DocNode]:
        """Collect statements from parser until ELSEIF/ELSE/ENDIF or end of tokens."""
        nodes: List[DocNode] = []
        while p._peek() is not None:
            tok = p._peek()
            kw = tok.value.upper() if tok.type == 'KEYWORD' else ''
            if kw in ('ELSEIF', 'ELSE', 'ENDIF'):
                break
            if kw == 'SET':
                stmt = p._parse_set()
                nodes.append(DocSet(stmt.variable, stmt.value))
            elif kw == 'VAR':
                stmt = p._parse_var()
                nodes.append(DocVar(stmt.variables))
            elif kw == 'OUTPUT':
                stmt = p._parse_output_stmt()
                nodes.append(DocOutput(stmt.expression))
            elif kw == 'IF':
                nodes.append(self._parse_if_from_parser(p))
            elif kw == 'FOR':
                nodes.append(self._parse_for_from_parser(p))
            elif tok.type == 'FUNC':
                expr = p._parse_expression()
                nodes.append(DocOutput(expr))
            else:
                break
        return nodes

    def _parser_has_keyword(self, p: amp_parser._Parser, keywords: tuple) -> bool:
        tok = p._peek()
        return tok is not None and tok.type == 'KEYWORD' and tok.value.upper() in keywords

    def _finish_if_inline(self, p: amp_parser._Parser, branches: List[DocIfBranch], else_body: List[DocNode]) -> DocIf:
        """Handle ELSEIF/ELSE/ENDIF that are all within the same code block parser."""
        while p._peek() is not None:
            tok = p._peek()
            kw = tok.value.upper() if tok.type == 'KEYWORD' else ''

            if kw == 'ELSEIF':
                p._advance()
                cond = p._parse_expression()
                p._expect('KEYWORD', 'THEN')
                body = self._collect_if_body_from_parser(p)
                branches.append(DocIfBranch(condition=cond, body=body))
            elif kw == 'ELSE':
                p._advance()
                else_body = self._collect_if_body_from_parser(p)
            elif kw == 'ENDIF':
                p._advance()
                break
            else:
                break

        return DocIf(branches=branches, else_body=else_body)

    def _finish_if_crossblock(self, branches: List[DocIfBranch], else_body: List[DocNode]) -> DocIf:
        """Handle ELSEIF/ELSE/ENDIF from subsequent %%[ ]%% blocks."""
        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]
            if token.type != TokenType.BLOCK_CODE:
                break
            code_tokens2 = _tokenize_code(token.value)
            if not code_tokens2:
                break
            kw = code_tokens2[0].value.upper() if code_tokens2[0].type == 'KEYWORD' else ''

            if kw == 'ELSEIF':
                self.pos += 1
                p2 = amp_parser._Parser(code_tokens2)
                p2._expect('KEYWORD', 'ELSEIF')
                cond = p2._parse_expression()
                p2._expect('KEYWORD', 'THEN')
                trailing2 = self._parse_remaining_from_parser(p2)
                body2 = trailing2 + self._parse_doc_nodes(stop_keywords={'ELSEIF', 'ELSE', 'ENDIF'})
                branches.append(DocIfBranch(condition=cond, body=body2))

            elif kw == 'ELSE':
                self.pos += 1
                p3 = amp_parser._Parser(code_tokens2)
                p3._expect('KEYWORD', 'ELSE')
                trailing3 = self._parse_remaining_from_parser(p3)
                else_body = trailing3 + self._parse_doc_nodes(stop_keywords={'ENDIF'})

            elif kw == 'ENDIF':
                self._consume_block_keyword('ENDIF')
                break
            else:
                break

        return DocIf(branches=branches, else_body=else_body)

    def _parse_for_from_parser(self, p: amp_parser._Parser) -> DocFor:
        """
        Parse a FOR construct. Handles both inline (single block) and cross-block.
        """
        p._expect('KEYWORD', 'FOR')
        var_tok = p._expect('VAR')
        var_name = var_tok.value[1:].lower()
        p._expect('ASSIGN')
        start = p._parse_expression()
        p._expect('KEYWORD', 'TO')
        end = p._parse_expression()
        p._expect('KEYWORD', 'DO')

        # Collect body from remaining parser tokens until NEXT
        trailing = self._collect_for_body_from_parser(p)
        if self._parser_has_keyword(p, ('NEXT',)):
            # Entire FOR/NEXT in same block
            p._advance()  # consume NEXT
            if p._peek() and p._peek().type == 'VAR':
                p._advance()  # optional @var after NEXT
            return DocFor(variable=var_name, start=start, end=end, body=trailing)
        else:
            # Cross-block
            body_nodes = trailing + self._parse_doc_nodes(stop_keywords={'NEXT'})
            if self.pos < len(self.tokens):
                token = self.tokens[self.pos]
                if token.type == TokenType.BLOCK_CODE:
                    self._consume_block_keyword('NEXT')
            return DocFor(variable=var_name, start=start, end=end, body=body_nodes)

    def _collect_for_body_from_parser(self, p: amp_parser._Parser) -> List[DocNode]:
        """Collect statements from parser until NEXT or end of tokens."""
        nodes: List[DocNode] = []
        while p._peek() is not None:
            tok = p._peek()
            kw = tok.value.upper() if tok.type == 'KEYWORD' else ''
            if kw == 'NEXT':
                break
            if kw == 'SET':
                stmt = p._parse_set()
                nodes.append(DocSet(stmt.variable, stmt.value))
            elif kw == 'VAR':
                stmt = p._parse_var()
                nodes.append(DocVar(stmt.variables))
            elif kw == 'OUTPUT':
                stmt = p._parse_output_stmt()
                nodes.append(DocOutput(stmt.expression))
            elif kw == 'IF':
                nodes.append(self._parse_if_from_parser(p))
            elif kw == 'FOR':
                nodes.append(self._parse_for_from_parser(p))
            elif tok.type == 'FUNC':
                expr = p._parse_expression()
                nodes.append(DocOutput(expr))
            else:
                break
        return nodes

    def _parse_remaining_from_parser(self, p: amp_parser._Parser) -> List[DocNode]:
        """
        Parse any remaining statements from a partially-consumed code block parser.
        Handles SET, VAR, OUTPUT, bare function calls, and nested IF/FOR.
        """
        nodes: List[DocNode] = []
        while p._peek() is not None:
            tok = p._peek()
            kw = tok.value.upper() if tok.type == 'KEYWORD' else ''

            if kw == 'SET':
                stmt = p._parse_set()
                nodes.append(DocSet(stmt.variable, stmt.value))
            elif kw == 'VAR':
                stmt = p._parse_var()
                nodes.append(DocVar(stmt.variables))
            elif kw == 'OUTPUT':
                stmt = p._parse_output_stmt()
                nodes.append(DocOutput(stmt.expression))
            elif kw == 'IF':
                nodes.append(self._parse_if_from_parser(p))
            elif kw == 'FOR':
                nodes.append(self._parse_for_from_parser(p))
            elif tok.type == 'FUNC':
                expr = p._parse_expression()
                nodes.append(DocOutput(expr))
            else:
                break
        return nodes


# ---------------------------------------------------------------------------
# Interpreter context
# ---------------------------------------------------------------------------

@dataclass
class InterpreterContext:
    subscriber_row: Dict[str, Any]
    data_extensions: Dict[str, List[Dict[str, Any]]]
    variables: Dict[str, Any] = field(default_factory=dict)
    output_parts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    content_block_loader: Optional[Callable] = None


class InterpreterError(Exception):
    pass


# ---------------------------------------------------------------------------
# Expression evaluator
# ---------------------------------------------------------------------------

def _eval_expr(expr: Expression, ctx: InterpreterContext) -> Any:
    if isinstance(expr, StringLiteral):
        return expr.value

    if isinstance(expr, NumericLiteral):
        return expr.value

    if isinstance(expr, BooleanLiteral):
        return expr.value

    if isinstance(expr, VariableRef):
        return ctx.variables.get(expr.name)

    if isinstance(expr, FunctionCall):
        evaluated_args = [_eval_expr(a, ctx) for a in expr.args]
        return call_function(expr.name, evaluated_args, ctx)

    if isinstance(expr, UnaryOp):
        if expr.op == 'NOT':
            return not _is_truthy(_eval_expr(expr.operand, ctx))
        raise InterpreterError(f"Unknown unary operator: {expr.op}")

    if isinstance(expr, BinaryOp):
        left = _eval_expr(expr.left, ctx)
        right = _eval_expr(expr.right, ctx)
        return _eval_binary(expr.op, left, right)

    raise InterpreterError(f"Unknown expression type: {type(expr)}")


def _eval_binary(op: str, left: Any, right: Any) -> Any:
    if op == 'AND':
        return _is_truthy(left) and _is_truthy(right)
    if op == 'OR':
        return _is_truthy(left) or _is_truthy(right)

    nl = _try_number(left)
    nr = _try_number(right)
    if nl is not None and nr is not None:
        left, right = nl, nr
    else:
        left = str(left).strip().lower() if left is not None else ""
        right = str(right).strip().lower() if right is not None else ""

    if op == '==':
        return left == right
    if op == '!=':
        return left != right
    if op == '>':
        return left > right
    if op == '<':
        return left < right
    if op == '>=':
        return left >= right
    if op == '<=':
        return left <= right
    raise InterpreterError(f"Unknown binary operator: {op}")


def _try_number(val: Any) -> Optional[float]:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return None


# ---------------------------------------------------------------------------
# Document node executor
# ---------------------------------------------------------------------------

def _exec_doc_nodes(nodes: List[DocNode], ctx: InterpreterContext):
    for node in nodes:
        try:
            _exec_doc_node(node, ctx)
        except (ParseError, AMPScriptRuntimeError, InterpreterError) as e:
            error_msg = f"[AMPScript Error: {e}]"
            ctx.errors.append(error_msg)
            ctx.output_parts.append(
                f'<span style="color:red;font-weight:bold" title="AMPScript Error">{error_msg}</span>'
            )


def _exec_doc_node(node: DocNode, ctx: InterpreterContext):
    if isinstance(node, DocLiteral):
        ctx.output_parts.append(node.text)

    elif isinstance(node, DocPersonalization):
        field_name = node.field_name.lower()
        row_lower = {k.lower(): v for k, v in ctx.subscriber_row.items()}
        val = row_lower.get(field_name, "")
        ctx.output_parts.append(_to_string(val))

    elif isinstance(node, DocInlineExpr):
        val = _eval_expr(node.expression, ctx)
        if val is not None:
            ctx.output_parts.append(_to_string(val))

    elif isinstance(node, DocSet):
        ctx.variables[node.variable] = _eval_expr(node.value, ctx)

    elif isinstance(node, DocVar):
        for v in node.variables:
            if v not in ctx.variables:
                ctx.variables[v] = None

    elif isinstance(node, DocOutput):
        val = _eval_expr(node.expression, ctx)
        if val is not None:
            ctx.output_parts.append(_to_string(val))

    elif isinstance(node, DocIf):
        for branch in node.branches:
            cond_val = _eval_expr(branch.condition, ctx)
            if _is_truthy(cond_val):
                _exec_doc_nodes(branch.body, ctx)
                return
        _exec_doc_nodes(node.else_body, ctx)

    elif isinstance(node, DocFor):
        start_val = int(_eval_expr(node.start, ctx))
        end_val = int(_eval_expr(node.end, ctx))
        for i in range(start_val, end_val + 1):
            ctx.variables[node.variable] = float(i)
            _exec_doc_nodes(node.body, ctx)

    else:
        raise InterpreterError(f"Unknown doc node type: {type(node)}")


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(
    template_source: str,
    subscriber_row: Dict[str, Any],
    data_extensions: Dict[str, List[Dict[str, Any]]],
    content_block_loader: Optional[Callable] = None,
) -> str:
    """
    Render an HTML+AMPScript template for a single subscriber.

    Handles AMPScript constructs (IF/ELSE/ENDIF, FOR/NEXT) that span
    across multiple %%[ ]%% blocks with HTML literals in between.

    Args:
        template_source: The full HTML source with embedded AMPScript.
        subscriber_row:  Dict of field_name -> value for the current subscriber.
        data_extensions: Dict of DE_name -> list of row dicts.
        content_block_loader: Optional callable(name, ctx) that loads and renders
                              a content block file, returning its rendered HTML.

    Returns:
        Fully rendered HTML string.
    """
    ctx = InterpreterContext(
        subscriber_row=subscriber_row,
        data_extensions=data_extensions,
        content_block_loader=content_block_loader,
    )

    # Tokenize the full template
    tokens = lexer.tokenize(template_source)

    # Build document-level AST (handles cross-block IF/FOR)
    try:
        builder = _DocBuilder(tokens)
        doc_nodes = builder.build()
    except (ParseError, AMPScriptRuntimeError) as e:
        return f'<span style="color:red;font-weight:bold">[AMPScript Parse Error: {e}]</span>{template_source}'

    # Execute the document AST
    _exec_doc_nodes(doc_nodes, ctx)

    result = "".join(ctx.output_parts)

    # Post-process: remove table rows that ended up with empty content
    # (from IF blocks that evaluated to false, leaving only whitespace in cells)
    result = _collapse_empty_rows(result)

    return result


def render_batch(
    template_source: str,
    send_list: List[Dict[str, Any]],
    data_extensions: Dict[str, List[Dict[str, Any]]],
    content_block_loader: Optional[Callable] = None,
) -> List[str]:
    """
    Render a template for multiple subscribers efficiently.

    Parses/tokenizes the template once and executes for each row.
    Returns a list of rendered HTML strings, one per subscriber.
    """
    # Tokenize and parse once
    tokens = lexer.tokenize(template_source)
    try:
        builder = _DocBuilder(tokens)
        doc_nodes = builder.build()
    except (ParseError, AMPScriptRuntimeError) as e:
        error_html = f'<span style="color:red;font-weight:bold">[AMPScript Parse Error: {e}]</span>{template_source}'
        return [error_html] * len(send_list)

    results = []
    for subscriber_row in send_list:
        ctx = InterpreterContext(
            subscriber_row=subscriber_row,
            data_extensions=data_extensions,
            content_block_loader=content_block_loader,
        )
        _exec_doc_nodes(doc_nodes, ctx)
        result = "".join(ctx.output_parts)
        result = _collapse_empty_rows(result)
        results.append(result)

    return results


def render_batch_stream(
    template_source: str,
    send_list: List[Dict[str, Any]],
    data_extensions: Dict[str, List[Dict[str, Any]]],
    content_block_loader: Optional[Callable] = None,
):
    """
    Generator that yields (index, rendered_html) tuples one row at a time.

    Parses/tokenizes the template once, then renders each subscriber row
    individually so callers can stream progress.
    """
    tokens = lexer.tokenize(template_source)
    try:
        builder = _DocBuilder(tokens)
        doc_nodes = builder.build()
    except (ParseError, AMPScriptRuntimeError) as e:
        error_html = f'<span style="color:red;font-weight:bold">[AMPScript Parse Error: {e}]</span>{template_source}'
        for i in range(len(send_list)):
            yield (i, error_html)
        return

    for i, subscriber_row in enumerate(send_list):
        ctx = InterpreterContext(
            subscriber_row=subscriber_row,
            data_extensions=data_extensions,
            content_block_loader=content_block_loader,
        )
        _exec_doc_nodes(doc_nodes, ctx)
        result = "".join(ctx.output_parts)
        result = _collapse_empty_rows(result)
        yield (i, result)


# Regex to find <tr> rows where all <td> cells contain only whitespace
# Catches outer-td rows with empty nested tables or just whitespace
_EMPTY_ROW_RE = re_mod.compile(
    r'\s*<tr[^>]*>\s*<td[^>]*>\s*'
    r'(?:<table[^>]*>\s*<tr[^>]*>\s*<td[^>]*>\s*</td>\s*</tr>\s*</table>\s*)?'
    r'</td>\s*</tr>',
    re_mod.IGNORECASE | re_mod.DOTALL,
)


def _collapse_empty_rows(html: str) -> str:
    """Remove table rows whose cells ended up with only whitespace content."""
    return _EMPTY_ROW_RE.sub('', html)


def _to_string(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "True" if val else "False"
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val)

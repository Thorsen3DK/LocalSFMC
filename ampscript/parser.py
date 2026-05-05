"""
AMPScript Parser — Recursive-descent parser that builds an AST from AMPScript code.

Handles:
  - SET @var = expr
  - VAR @var1, @var2, ...
  - IF expr THEN ... ELSEIF expr THEN ... ELSE ... ENDIF
  - FOR @var = expr TO expr DO ... NEXT @var
  - Function calls: FuncName(arg1, arg2, ...)
  - Variable references: @varname
  - String literals: "..." or '...'
  - Numeric literals: 123, 12.5
  - Comparisons: ==, !=, >, <, >=, <=
  - Boolean operators: AND, OR, NOT
  - Output() and v() calls
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional, Union


# ---------------------------------------------------------------------------
# AST Node types
# ---------------------------------------------------------------------------

@dataclass
class StringLiteral:
    value: str

@dataclass
class NumericLiteral:
    value: float

@dataclass
class BooleanLiteral:
    value: bool

@dataclass
class VariableRef:
    name: str  # stored lowercase, without @

@dataclass
class FunctionCall:
    name: str  # stored lowercase
    args: List[Expression]

@dataclass
class UnaryOp:
    op: str  # 'NOT'
    operand: Expression

@dataclass
class BinaryOp:
    op: str  # '==', '!=', '>', '<', '>=', '<=', 'AND', 'OR'
    left: Expression
    right: Expression


Expression = Union[
    StringLiteral, NumericLiteral, BooleanLiteral,
    VariableRef, FunctionCall, UnaryOp, BinaryOp,
]


@dataclass
class SetStatement:
    variable: str  # lowercase, without @
    value: Expression

@dataclass
class VarStatement:
    variables: List[str]  # lowercase, without @

@dataclass
class OutputStatement:
    """Wraps an expression whose result should be emitted as text."""
    expression: Expression

@dataclass
class IfBranch:
    condition: Expression
    body: List[Statement]

@dataclass
class IfStatement:
    branches: List[IfBranch]       # first = IF, rest = ELSEIF
    else_body: List[Statement]     # may be empty

@dataclass
class ForStatement:
    variable: str  # lowercase, without @
    start: Expression
    end: Expression
    body: List[Statement]


Statement = Union[
    SetStatement, VarStatement, OutputStatement,
    IfStatement, ForStatement,
]


# ---------------------------------------------------------------------------
# Tokenizer for AMPScript code (inside %%[ ]%% blocks)
# ---------------------------------------------------------------------------

_CODE_TOKEN_RE = re.compile(r"""
    (?P<STRING>"[^"]*"|'[^']*')        |  # string literal
    (?P<NUMBER>\d+(?:\.\d+)?)          |  # numeric literal
    (?P<CMP>==|!=|>=|<=|>|<)           |  # comparison operators
    (?P<ASSIGN>=)                      |  # assignment
    (?P<LPAREN>\()                     |  # (
    (?P<RPAREN>\))                     |  # )
    (?P<COMMA>,)                       |  # ,
    (?P<WORD>[A-Za-z_]\w*)             |  # keywords / function names
    (?P<VAR>@[A-Za-z_]\w*)             |  # variable reference
    (?P<WS>\s+)                        |  # whitespace (skip)
    (?P<NEWLINE>\n)                       # newline (skip)
""", re.VERBOSE)

_KEYWORDS = {
    'SET', 'VAR', 'IF', 'THEN', 'ELSEIF', 'ELSE', 'ENDIF',
    'FOR', 'TO', 'DO', 'NEXT', 'AND', 'OR', 'NOT',
    'TRUE', 'FALSE', 'OUTPUT',
}


@dataclass
class CodeToken:
    type: str   # STRING, NUMBER, CMP, ASSIGN, LPAREN, RPAREN, COMMA, KEYWORD, FUNC, VAR
    value: str
    pos: int


def _tokenize_code(code: str) -> List[CodeToken]:
    tokens: List[CodeToken] = []
    for m in _CODE_TOKEN_RE.finditer(code):
        kind = m.lastgroup
        val = m.group()
        if kind in ('WS', 'NEWLINE'):
            continue
        if kind == 'WORD':
            upper = val.upper()
            if upper in _KEYWORDS:
                kind = 'KEYWORD'
                val = upper
            else:
                kind = 'FUNC'
        elif kind == 'VAR':
            kind = 'VAR'
        elif kind == 'STRING':
            kind = 'STRING'
            val = val[1:-1]  # strip quotes
        tokens.append(CodeToken(kind, val, m.start()))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class ParseError(Exception):
    pass


class _Parser:
    """Recursive-descent parser for AMPScript block code."""

    def __init__(self, tokens: List[CodeToken]):
        self.tokens = tokens
        self.pos = 0

    # -- Helpers -------------------------------------------------------------

    def _peek(self) -> Optional[CodeToken]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def _advance(self) -> CodeToken:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, ttype: str, value: Optional[str] = None) -> CodeToken:
        tok = self._peek()
        if tok is None:
            raise ParseError(f"Unexpected end of input, expected {ttype} {value or ''}")
        if tok.type != ttype or (value is not None and tok.value.upper() != value.upper()):
            raise ParseError(
                f"Expected {ttype} '{value or ''}' but got {tok.type} '{tok.value}' at pos {tok.pos}"
            )
        return self._advance()

    def _match_keyword(self, *keywords: str) -> Optional[CodeToken]:
        tok = self._peek()
        if tok and tok.type == 'KEYWORD' and tok.value in keywords:
            return self._advance()
        return None

    # -- Public entry --------------------------------------------------------

    def parse_block(self) -> List[Statement]:
        """Parse a full %%[ ... ]%% block into a list of statements."""
        stmts = self._parse_statements()
        if self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            raise ParseError(f"Unexpected token {tok.type} '{tok.value}' at pos {tok.pos}")
        return stmts

    def parse_inline_expr(self) -> Expression:
        """Parse a single %%= ... =%% inline expression."""
        expr = self._parse_expression()
        return expr

    # -- Statements ----------------------------------------------------------

    def _parse_statements(self, stop_keywords: tuple = ()) -> List[Statement]:
        stmts: List[Statement] = []
        while True:
            tok = self._peek()
            if tok is None:
                break
            if tok.type == 'KEYWORD' and tok.value in stop_keywords:
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
        return stmts

    def _parse_statement(self) -> Optional[Statement]:
        tok = self._peek()
        if tok is None:
            return None

        if tok.type == 'KEYWORD':
            if tok.value == 'SET':
                return self._parse_set()
            elif tok.value == 'VAR':
                return self._parse_var()
            elif tok.value == 'IF':
                return self._parse_if()
            elif tok.value == 'FOR':
                return self._parse_for()
            elif tok.value == 'OUTPUT':
                return self._parse_output_stmt()

        # Bare function call as a statement (e.g., Output(...) or InsertDE(...))
        if tok.type == 'FUNC':
            expr = self._parse_expression()
            return OutputStatement(expression=expr)

        raise ParseError(f"Unexpected token {tok.type} '{tok.value}' at pos {tok.pos}")

    def _parse_set(self) -> SetStatement:
        self._expect('KEYWORD', 'SET')
        var_tok = self._expect('VAR')
        var_name = var_tok.value[1:].lower()  # strip @
        self._expect('ASSIGN')
        expr = self._parse_expression()
        return SetStatement(variable=var_name, value=expr)

    def _parse_var(self) -> VarStatement:
        self._expect('KEYWORD', 'VAR')
        variables = []
        var_tok = self._expect('VAR')
        variables.append(var_tok.value[1:].lower())
        while self._peek() and self._peek().type == 'COMMA':
            self._advance()  # skip comma
            var_tok = self._expect('VAR')
            variables.append(var_tok.value[1:].lower())
        return VarStatement(variables=variables)

    def _parse_output_stmt(self) -> OutputStatement:
        self._expect('KEYWORD', 'OUTPUT')
        self._expect('LPAREN')
        expr = self._parse_expression()
        self._expect('RPAREN')
        return OutputStatement(expression=expr)

    def _parse_if(self) -> IfStatement:
        self._expect('KEYWORD', 'IF')
        condition = self._parse_expression()
        self._expect('KEYWORD', 'THEN')
        body = self._parse_statements(('ELSEIF', 'ELSE', 'ENDIF'))

        branches = [IfBranch(condition=condition, body=body)]
        else_body: List[Statement] = []

        while self._match_keyword('ELSEIF'):
            cond = self._parse_expression()
            self._expect('KEYWORD', 'THEN')
            b = self._parse_statements(('ELSEIF', 'ELSE', 'ENDIF'))
            branches.append(IfBranch(condition=cond, body=b))

        if self._match_keyword('ELSE'):
            else_body = self._parse_statements(('ENDIF',))

        self._expect('KEYWORD', 'ENDIF')
        return IfStatement(branches=branches, else_body=else_body)

    def _parse_for(self) -> ForStatement:
        self._expect('KEYWORD', 'FOR')
        var_tok = self._expect('VAR')
        var_name = var_tok.value[1:].lower()
        self._expect('ASSIGN')
        start = self._parse_expression()
        self._expect('KEYWORD', 'TO')
        end = self._parse_expression()
        self._expect('KEYWORD', 'DO')
        body = self._parse_statements(('NEXT',))
        self._expect('KEYWORD', 'NEXT')
        # optional @var after NEXT
        if self._peek() and self._peek().type == 'VAR':
            self._advance()
        return ForStatement(variable=var_name, start=start, end=end, body=body)

    # -- Expressions (precedence climbing) -----------------------------------

    def _parse_expression(self) -> Expression:
        return self._parse_or()

    def _parse_or(self) -> Expression:
        left = self._parse_and()
        while self._match_keyword('OR'):
            right = self._parse_and()
            left = BinaryOp('OR', left, right)
        return left

    def _parse_and(self) -> Expression:
        left = self._parse_not()
        while self._match_keyword('AND'):
            right = self._parse_not()
            left = BinaryOp('AND', left, right)
        return left

    def _parse_not(self) -> Expression:
        if self._match_keyword('NOT'):
            operand = self._parse_not()
            return UnaryOp('NOT', operand)
        return self._parse_comparison()

    def _parse_comparison(self) -> Expression:
        left = self._parse_primary()
        tok = self._peek()
        if tok and tok.type == 'CMP':
            op = self._advance().value
            right = self._parse_primary()
            left = BinaryOp(op, left, right)
        return left

    def _parse_primary(self) -> Expression:
        tok = self._peek()
        if tok is None:
            raise ParseError("Unexpected end of input in expression")

        # Parenthesised expression
        if tok.type == 'LPAREN':
            self._advance()
            expr = self._parse_expression()
            self._expect('RPAREN')
            return expr

        # String literal
        if tok.type == 'STRING':
            self._advance()
            return StringLiteral(tok.value)

        # Numeric literal
        if tok.type == 'NUMBER':
            self._advance()
            val = float(tok.value) if '.' in tok.value else int(tok.value)
            return NumericLiteral(float(val))

        # Boolean literal
        if tok.type == 'KEYWORD' and tok.value in ('TRUE', 'FALSE'):
            self._advance()
            return BooleanLiteral(tok.value == 'TRUE')

        # Variable reference
        if tok.type == 'VAR':
            self._advance()
            return VariableRef(tok.value[1:].lower())

        # Function call (including v, Output, etc.) or bare field name
        if tok.type == 'FUNC' or (tok.type == 'KEYWORD' and tok.value in ('OUTPUT', 'NOT')):
            name = self._advance().value
            # Check if followed by '(' — if so, it's a function call
            if self._peek() and self._peek().type == 'LPAREN':
                self._advance()  # consume '('
                args: List[Expression] = []
                if self._peek() and self._peek().type != 'RPAREN':
                    args.append(self._parse_expression())
                    while self._peek() and self._peek().type == 'COMMA':
                        self._advance()
                        args.append(self._parse_expression())
                self._expect('RPAREN')
                return FunctionCall(name.lower(), args)
            else:
                # Bare field name — treat as implicit AttributeValue("name")
                return FunctionCall('attributevalue', [StringLiteral(name)])

        raise ParseError(f"Unexpected token {tok.type} '{tok.value}' at pos {tok.pos}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_block(code: str) -> List[Statement]:
    """Parse the code inside a %%[ ... ]%% block, returning a list of AST statements."""
    tokens = _tokenize_code(code)
    if not tokens:
        return []
    p = _Parser(tokens)
    return p.parse_block()


def parse_inline_expression(code: str) -> Expression:
    """Parse the code inside a %%= ... =%% inline expression, returning an AST expression."""
    tokens = _tokenize_code(code)
    if not tokens:
        raise ParseError("Empty inline expression")
    p = _Parser(tokens)
    return p.parse_inline_expr()

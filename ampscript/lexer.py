"""
AMPScript Lexer — Tokenizes HTML containing AMPScript blocks.

Token types:
  LITERAL               — Raw HTML passthrough
  BLOCK_CODE            — Contents of %%[ ... ]%%
  INLINE_EXPR           — Contents of %%= ... =%%
  PERSONALIZATION_STRING — %%FieldName%% (no square brackets or equals signs)
"""

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List


class TokenType(Enum):
    LITERAL = auto()
    BLOCK_CODE = auto()
    INLINE_EXPR = auto()
    PERSONALIZATION_STRING = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    pos: int = 0  # character offset in source


# Master pattern that matches the three AMPScript constructs.
# Handles AMPScript wrapped in HTML comments: <!-- %%[ ... ]%% -->
# Order matters: block code first (most specific), then inline expr, then personalization string.
# The block pattern consumes optional trailing whitespace + newline to prevent blank lines.
_TOKEN_RE = re.compile(
    r'(?:<!--\s*)?%%\[(?P<block>.*?)\]%%(?:\s*-->)?[ \t]*\n?'  # %%[ ... ]%%  optionally wrapped in <!-- -->
    r'|%%=(?P<inline>.*?)=%%'                            # %%= ... =%%  (inline expression)
    r'|%%(?P<pers>[A-Za-z_]\w*)%%',                      # %%FieldName%% (personalization string)
    re.DOTALL | re.IGNORECASE,
)

# Strip C-style comments from AMPScript code blocks
_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)

# Detect the start of actual HTML content
_HTML_START_RE = re.compile(r'<(!DOCTYPE|html)\b', re.IGNORECASE)


def tokenize(source: str) -> List[Token]:
    """Tokenize an HTML+AMPScript source string into a list of Tokens."""
    tokens: List[Token] = []
    last_end = 0

    # Find where the HTML content starts so we can skip the preheader block
    html_start = _HTML_START_RE.search(source)
    html_start_pos = html_start.start() if html_start else 0
    first_block_skipped = False

    for m in _TOKEN_RE.finditer(source):
        start = m.start()

        # Emit any literal HTML between the previous match and this one.
        if start > last_end:
            tokens.append(Token(TokenType.LITERAL, source[last_end:start], last_end))

        if m.group('block') is not None:
            # Skip the first AMPScript block if it appears before the HTML
            if not first_block_skipped and start < html_start_pos:
                first_block_skipped = True
                last_end = m.end()
                continue
            first_block_skipped = True
            code = _COMMENT_RE.sub('', m.group('block')).strip()
            tokens.append(Token(TokenType.BLOCK_CODE, code, start))
        elif m.group('inline') is not None:
            tokens.append(Token(TokenType.INLINE_EXPR, m.group('inline').strip(), start))
        elif m.group('pers') is not None:
            tokens.append(Token(TokenType.PERSONALIZATION_STRING, m.group('pers'), start))

        last_end = m.end()

    # Trailing literal
    if last_end < len(source):
        tokens.append(Token(TokenType.LITERAL, source[last_end:], last_end))

    return tokens

"""Small, dependency-free parser for Valve KeyValues text files.

Steam's ``.vdf`` and ``.acf`` files use a simple sequence of key/value pairs,
where a value can itself be another braced sequence.  Keeping the tokenizer
separate from the recursive parser makes malformed input fail predictably and
avoids the data corruption risks of a regular-expression-only parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TextIO, TypeAlias


class VDFParseError(ValueError):
    """Raised when KeyValues input is incomplete or structurally invalid."""

    def __init__(self, message: str, line: int, column: int) -> None:
        self.message = message
        self.line = line
        self.column = column
        super().__init__(f"{message} at line {line}, column {column}")


class _TokenKind(Enum):
    VALUE = auto()
    OPEN_BRACE = auto()
    CLOSE_BRACE = auto()
    EOF = auto()


@dataclass(frozen=True, slots=True)
class _Token:
    kind: _TokenKind
    value: str
    line: int
    column: int


KVValue: TypeAlias = "str | dict[str, KVValue]"
KeyValues: TypeAlias = dict[str, KVValue]
KeyValueToken: TypeAlias = tuple[str, str]


class _Tokenizer:
    """Turn KeyValues text into scalar and brace tokens."""

    _ESCAPES = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        '"': '"',
        "\\": "\\",
    }

    def __init__(self, text: str) -> None:
        # A UTF-8 BOM is common enough in hand-edited VDF files to accept it.
        self._text = text.removeprefix("\ufeff")
        self._index = 0
        self._line = 1
        self._column = 1

    def next(self) -> _Token:
        self._skip_ignored()
        if self._at_end:
            return _Token(_TokenKind.EOF, "", self._line, self._column)

        line, column = self._line, self._column
        character = self._peek()
        if character == "{":
            self._advance()
            return _Token(_TokenKind.OPEN_BRACE, character, line, column)
        if character == "}":
            self._advance()
            return _Token(_TokenKind.CLOSE_BRACE, character, line, column)
        if character == '"':
            return self._quoted_value()
        return self._bare_value()

    @property
    def _at_end(self) -> bool:
        return self._index >= len(self._text)

    def _peek(self, offset: int = 0) -> str:
        position = self._index + offset
        return self._text[position] if position < len(self._text) else ""

    def _advance(self) -> str:
        character = self._peek()
        if not character:
            return ""
        self._index += 1
        if character == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return character

    def _skip_ignored(self) -> None:
        while not self._at_end:
            if self._peek().isspace():
                self._advance()
                continue
            if self._peek() == "/" and self._peek(1) == "/":
                self._advance()
                self._advance()
                while not self._at_end and self._peek() != "\n":
                    self._advance()
                continue
            if self._peek() == "/" and self._peek(1) == "*":
                start_line, start_column = self._line, self._column
                self._advance()
                self._advance()
                while not self._at_end:
                    if self._peek() == "*" and self._peek(1) == "/":
                        self._advance()
                        self._advance()
                        break
                    self._advance()
                else:
                    raise VDFParseError(
                        "unterminated block comment", start_line, start_column
                    )
                continue
            break

    def _quoted_value(self) -> _Token:
        line, column = self._line, self._column
        self._advance()  # opening quote
        characters: list[str] = []
        while not self._at_end:
            character = self._advance()
            if character == '"':
                return _Token(
                    _TokenKind.VALUE, "".join(characters), line, column
                )
            if character != "\\":
                characters.append(character)
                continue

            if self._at_end:
                raise VDFParseError("unterminated escape", self._line, self._column)
            escaped = self._advance()
            replacement = self._ESCAPES.get(escaped)
            if replacement is None:
                # Unknown escapes are kept literally.  This is important for
                # Windows-style paths found in libraries shared with Wine.
                characters.extend(("\\", escaped))
            else:
                characters.append(replacement)

        raise VDFParseError("unterminated quoted value", line, column)

    def _bare_value(self) -> _Token:
        line, column = self._line, self._column
        characters: list[str] = []
        while not self._at_end:
            character = self._peek()
            if character.isspace() or character in "{}":
                break
            characters.append(self._advance())
        if not characters:
            raise VDFParseError("unexpected character", line, column)
        return _Token(_TokenKind.VALUE, "".join(characters), line, column)


class _Parser:
    def __init__(self, text: str) -> None:
        self._tokens = _Tokenizer(text)
        self._lookahead: _Token | None = None

    def parse(self) -> KeyValues:
        result = self._parse_pairs(expect_close=False)
        trailing = self._peek()
        if trailing.kind is not _TokenKind.EOF:
            raise VDFParseError(
                "unexpected token after top-level object",
                trailing.line,
                trailing.column,
            )
        return result

    def _parse_pairs(self, *, expect_close: bool) -> KeyValues:
        result: KeyValues = {}
        while True:
            key = self._take()
            if key.kind is _TokenKind.EOF:
                if expect_close:
                    raise VDFParseError(
                        "missing closing brace", key.line, key.column
                    )
                return result
            if key.kind is _TokenKind.CLOSE_BRACE:
                if not expect_close:
                    raise VDFParseError(
                        "unexpected closing brace", key.line, key.column
                    )
                return result
            if key.kind is _TokenKind.OPEN_BRACE:
                raise VDFParseError(
                    "expected a key before opening brace", key.line, key.column
                )

            value = self._take()
            if value.kind is _TokenKind.VALUE:
                result[key.value] = value.value
            elif value.kind is _TokenKind.OPEN_BRACE:
                result[key.value] = self._parse_pairs(expect_close=True)
            elif value.kind is _TokenKind.CLOSE_BRACE:
                raise VDFParseError(
                    f"missing value for key {key.value!r}",
                    value.line,
                    value.column,
                )
            else:
                raise VDFParseError(
                    f"missing value for key {key.value!r}",
                    value.line,
                    value.column,
                )

    def _peek(self) -> _Token:
        if self._lookahead is None:
            self._lookahead = self._tokens.next()
        return self._lookahead

    def _take(self) -> _Token:
        token = self._peek()
        self._lookahead = None
        return token


def parse_keyvalues(text: str) -> KeyValues:
    """Parse KeyValues *text* into nested dictionaries of strings."""

    if not isinstance(text, str):
        raise TypeError("KeyValues input must be text")
    try:
        return _Parser(text).parse()
    except RecursionError as error:
        raise VDFParseError("KeyValues nesting is too deep", 1, 1) from error


def tokenize_keyvalues(
    text: str, *, allow_partial: bool = False
) -> tuple[KeyValueToken, ...]:
    """Return scalar/brace tokens, optionally preserving a valid prefix.

    The partial mode is intended for narrowly scoped recovery of independent
    records in a damaged file.  Normal VDF/ACF parsing remains strict.
    """

    if not isinstance(text, str):
        raise TypeError("KeyValues input must be text")
    tokenizer = _Tokenizer(text)
    result: list[KeyValueToken] = []
    while True:
        try:
            token = tokenizer.next()
        except VDFParseError:
            if allow_partial:
                return tuple(result)
            raise
        if token.kind is _TokenKind.EOF:
            return tuple(result)
        kind = {
            _TokenKind.VALUE: "value",
            _TokenKind.OPEN_BRACE: "open",
            _TokenKind.CLOSE_BRACE: "close",
        }[token.kind]
        result.append((kind, token.value))


def load_keyvalues(path: str | Path, *, encoding: str = "utf-8-sig") -> KeyValues:
    """Read and parse a KeyValues file without modifying it."""

    with Path(path).open("r", encoding=encoding, errors="strict") as stream:
        return parse_keyvalues(stream.read())


def loads(text: str) -> KeyValues:
    """Compatibility alias following the conventional serialization API."""

    return parse_keyvalues(text)


def load(stream: TextIO) -> KeyValues:
    """Parse KeyValues text from an already-open text stream."""

    return parse_keyvalues(stream.read())


__all__ = [
    "KVValue",
    "KeyValueToken",
    "KeyValues",
    "VDFParseError",
    "load",
    "load_keyvalues",
    "loads",
    "parse_keyvalues",
    "tokenize_keyvalues",
]

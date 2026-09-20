"""Parser mínimo do formato VDF/ACF de texto usado pela Steam."""
import re

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
_ESC = re.compile(r"\\(.)")
_ESC_MAP = {"n": "\n", "t": "\t"}


def _unescape(s: str) -> str:
    return _ESC.sub(lambda m: _ESC_MAP.get(m.group(1), m.group(1)), s)


def loads(text: str) -> dict:
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    tokens = [("s", _unescape(m.group(1))) if m.group(1) is not None else ("b", m.group(2))
              for m in _TOKEN.finditer(text)]
    pos = 0

    def parse_block() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            kind, value = tokens[pos]
            pos += 1
            if kind == "b":
                if value == "}":
                    return out
                raise ValueError("'{' inesperado no VDF")
            if pos >= len(tokens):
                raise ValueError("VDF truncado")
            nkind, nvalue = tokens[pos]
            pos += 1
            if nkind == "b" and nvalue == "{":
                out[value] = parse_block()
            elif nkind == "s":
                out[value] = nvalue
            else:
                raise ValueError("'}' inesperado no VDF")
        return out

    return parse_block()


def load(path) -> dict:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return loads(f.read())

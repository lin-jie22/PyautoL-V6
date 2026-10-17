from __future__ import annotations
from types import MappingProxyType


ASCII_CTRL = frozenset((chr(i) for i in range(32))) | frozenset(chr(127))
ILLEGAL_BASIC_STR_CHARS = frozenset('"\\') | ASCII_CTRL - frozenset('\t')
BARE_KEY_CHARS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')
COMPACT_ESCAPES = MappingProxyType({'\x08': '\\b', '\n': '\\n', '\x0c': '\\f', '\r': '\\r', '"': '\\"', '\\': '\\\\'})
def format_key_part(part: str) -> str:
    try:
        only_bare_key_chars = BARE_KEY_CHARS.issuperset(part)
    except TypeError:
        raise TypeError(f"Invalid mapping key '{part}' of type '{type(part).__qualname__}'. A string is required.") from None
    if part and only_bare_key_chars:
        return part
    return format_string(part, allow_multiline=False)
def format_string(s: str, *, allow_multiline: bool) -> str:
    do_multiline = allow_multiline and '\n' in s
    if do_multiline:
        result = '"""\n'
        s = s.replace('\r\n', '\n')
    else:
        result = '"'
    pos = seq_start = 0
    while True:
        try:
            char = s[pos]
        except IndexError:
            result += s[seq_start:pos]
            if do_multiline:
                return result + '"""'
            return result + '"'
        if char in ILLEGAL_BASIC_STR_CHARS:
            result += s[seq_start:pos]
            if char in COMPACT_ESCAPES:
                if do_multiline and char == '\n':
                    result += '\n'
                else:
                    result += COMPACT_ESCAPES[char]
            else:
                result += '\\u' + hex(ord(char))[2:].rjust(4, '0')
            seq_start = pos + 1
        pos += 1

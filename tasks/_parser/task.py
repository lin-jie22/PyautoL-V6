from __future__ import annotations


def is_unicode_scalar_value(codepoint: int) -> bool:
    return 0 <= codepoint <= 55295 or 57344 <= codepoint <= 1114111

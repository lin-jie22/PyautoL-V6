import re


ESCAPE = re.compile('[\\x00-\\x1f\\\\"\\b\\f\\n\\r\\t]')
ESCAPE_ASCII = re.compile('([\\\\"]|[^\\ -~])')
ESCAPE_DCT = {'\\': '\\\\', '"': '\\"', '\x08': '\\b', '\x0c': '\\f', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
def py_encode_basestring(s):
    """Return a JSON representation of a Python string

    """

    def replace(match):
        return ESCAPE_DCT[match.group(0)]
    return '"' + ESCAPE.sub(replace, s) + '"'
def py_encode_basestring_ascii(s):
    """Return an ASCII-only JSON representation of a Python string

    """

    def replace(match):
        s = match.group(0)
        try:
            return ESCAPE_DCT[s]
        except KeyError:
            n = ord(s)
            if n < 65536:
                return '\\u{0:04x}'.format(n)
            else:
                n -= 65536
                s1 = 55296 | n >> 10 & 1023
                s2 = 56320 | n & 1023
                return '\\u{0:04x}\\u{1:04x}'.format(s1, s2)
    return '"' + ESCAPE_ASCII.sub(replace, s) + '"'

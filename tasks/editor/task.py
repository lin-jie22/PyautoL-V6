import re
from idlelib import macosx


def index2line(index):
    return int(float(index))
_line_indent_re = re.compile('[ \\t]*')
def get_line_indent(line, tabwidth):
    """Return a line's indentation as (# chars, effective # of spaces).

    The effective # of spaces is the length after properly "expanding"
    the tabs into spaces, as done by str.expandtabs(tabwidth).
    """
    m = _line_indent_re.match(line)
    return (m.end(), len(m.group().expandtabs(tabwidth)))
def prepstr(s):
    """Extract the underscore from a string.

    For example, prepstr("Co_py") returns (2, "Copy").

    Args:
        s: String with underscore.

    Returns:
        Tuple of (position of underscore, string without underscore).
    """
    i = s.find('_')
    if i >= 0:
        s = s[:i] + s[i + 1:]
    return (i, s)
keynames = {'bracketleft': '[', 'bracketright': ']', 'slash': '/'}
def get_accelerator(keydefs, eventname):
    """Return a formatted string for the keybinding of an event.

    Convert the first keybinding for a given event to a form that
    can be displayed as an accelerator on the menu.

    Args:
        keydefs: Dictionary of valid events to keybindings.
        eventname: Event to retrieve keybinding for.

    Returns:
        Formatted string of the keybinding.
    """
    keylist = keydefs.get(eventname)
    if not keylist or (macosx.isCocoaTk() and eventname in {'<<open-module>>', '<<goto-line>>', '<<change-indentwidth>>'}):
        return ''
    s = keylist[0]
    s = re.sub('-[a-z]\\b', lambda m: m.group().upper(), s)
    s = re.sub('\\b\\w+\\b', lambda m: keynames.get(m.group(), m.group()), s)
    s = re.sub('Key-', '', s)
    s = re.sub('Cancel', 'Ctrl-Break', s)
    s = re.sub('Control-', 'Ctrl-', s)
    s = re.sub('-', '+', s)
    s = re.sub('><', ' ', s)
    s = re.sub('<', '', s)
    s = re.sub('>', '', s)
    return s

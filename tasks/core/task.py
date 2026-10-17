import unicodedata
from typing import Optional, Union


class IDNAError(UnicodeError):
    """Base exception for all IDNA-encoding related problems"""
    pass
class IDNABidiError(IDNAError):
    """Exception when bidirectional requirements are not satisfied"""
    pass
def valid_label_length(label: Union[bytes, str]) -> bool:
    if len(label) > 63:
        return False
    return True
def valid_string_length(label: Union[bytes, str], trailing_dot: bool) -> bool:
    if len(label) > (254 if trailing_dot else 253):
        return False
    return True
def check_bidi(label: str, check_ltr: bool=False) -> bool:
    bidi_label = False
    for idx, cp in enumerate(label, 1):
        direction = unicodedata.bidirectional(cp)
        if direction == '':
            raise IDNABidiError('Unknown directionality in label {} at position {}'.format(repr(label), idx))
        if direction in ['R', 'AL', 'AN']:
            bidi_label = True
    if not bidi_label and (not check_ltr):
        return True
    direction = unicodedata.bidirectional(label[0])
    if direction in ['R', 'AL']:
        rtl = True
    elif direction == 'L':
        rtl = False
    else:
        raise IDNABidiError('First codepoint in label {} must be directionality L, R or AL'.format(repr(label)))
    valid_ending = False
    number_type: Optional[str] = None
    for idx, cp in enumerate(label, 1):
        direction = unicodedata.bidirectional(cp)
        if rtl:
            if direction not in ['R', 'AL', 'AN', 'EN', 'ES', 'CS', 'ET', 'ON', 'BN', 'NSM']:
                raise IDNABidiError('Invalid direction for codepoint at position {} in a right-to-left label'.format(idx))
            if direction in ['R', 'AL', 'EN', 'AN']:
                valid_ending = True
            elif direction != 'NSM':
                valid_ending = False
            if direction in ['AN', 'EN']:
                if not number_type:
                    number_type = direction
                elif number_type != direction:
                    raise IDNABidiError('Can not mix numeral types in a right-to-left label')
        else:
            if direction not in ['L', 'EN', 'ES', 'CS', 'ET', 'ON', 'BN', 'NSM']:
                raise IDNABidiError('Invalid direction for codepoint at position {} in a left-to-right label'.format(idx))
            if direction in ['L', 'EN']:
                valid_ending = True
            elif direction != 'NSM':
                valid_ending = False
    if not valid_ending:
        raise IDNABidiError('Label ends with illegal codepoint directionality')
    return True
def check_initial_combiner(label: str) -> bool:
    if unicodedata.category(label[0])[0] == 'M':
        raise IDNAError('Label begins with an illegal combining character')
    return True
def check_hyphen_ok(label: str) -> bool:
    if label[2:4] == '--':
        raise IDNAError('Label has disallowed hyphens in 3rd and 4th position')
    if label[0] == '-' or label[-1] == '-':
        raise IDNAError('Label must not start or end with a hyphen')
    return True

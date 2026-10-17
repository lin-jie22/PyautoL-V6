

ENDMARKER = 0
NT_OFFSET = 256
def ISTERMINAL(x):
    return x < NT_OFFSET
def ISNONTERMINAL(x):
    return x >= NT_OFFSET
def ISEOF(x):
    return x == ENDMARKER

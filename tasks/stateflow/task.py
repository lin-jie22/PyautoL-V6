"""示例环境 3：状态机 / 流转（离散转移 + 合法性 + 可达性 + 路径计数）。

为什么还要第三个环境：
    两个环境只能把"变强"和"讨好一个环境"区分开。但 held-out 本身也可能是
    **偏的** —— 如果 held-out 全是简单题，一个"只会修简单题"的 harness
    会被判成泛化。要让 held-out 有判别力，它必须**有难度分层**
    （见 aether/repair.py 的 classify_difficulty）。

    任务源越多样，held-out 越难被"专项讨好"。这是环境供给优先于算法微调的
    那一派观点（SWE-smith / R2E-Gym）的直接推论。
"""

TRANSITIONS = {
    "draft": {"submit": "review", "cancel": "closed"},
    "review": {"approve": "approved", "reject": "draft", "cancel": "closed"},
    "approved": {"ship": "shipped", "cancel": "closed"},
    "shipped": {"close": "closed"},
    "closed": {},
}
TERMINAL = ("closed",)


def next_state(state, action):
    """单步转移。非法状态或非法动作 ⇒ None（不是抛异常）。"""
    if state not in TRANSITIONS:
        return None
    return TRANSITIONS[state].get(action)


def legal_actions(state):
    """该状态下所有合法动作（字典序）。未知状态 ⇒ 空。"""
    return sorted(TRANSITIONS.get(state, {}).keys())


def is_terminal(state):
    return state in TERMINAL


def run_machine(start, actions):
    """执行一串动作，返回 (最终状态, 是否全程合法)。

    遇到非法动作 ⇒ 停在当前状态并记 False（不抛、不跳过继续执行）。
    """
    cur = start
    for a in actions:
        nxt = next_state(cur, a)
        if nxt is None:
            # ⛔ 返回 list 而不是 tuple：判据是 JSON，tuple 与 list 不相等
            return [cur, False]
        cur = nxt
    return [cur, True]


def first_illegal(start, actions):
    """第一个非法动作的下标；全程合法 ⇒ -1。"""
    cur = start
    for i, a in enumerate(actions):
        if next_state(cur, a) is None:
            return i
        cur = next_state(cur, a)
    return -1


def reachable(start, target, max_steps=4):
    """max_steps 步内（含）是否可达。BFS，不重复访问。"""
    if start == target:
        return True
    frontier = [(start, 0)]
    seen = {start}
    while frontier:
        s, d = frontier.pop(0)
        if d >= max_steps:
            continue
        for a in legal_actions(s):
            nxt = next_state(s, a)
            if nxt is None or nxt in seen:
                continue
            if nxt == target:
                return True
            seen.add(nxt)
            frontier.append((nxt, d + 1))
    return False


def count_paths(start, target, max_steps=3):
    """max_steps 步内（含）到 target 的路径条数。走不到 ⇒ 0。"""
    if start == target:
        return 1
    total = 0
    frontier = [(start, 0)]
    while frontier:
        s, d = frontier.pop(0)
        if d >= max_steps:
            continue
        for a in legal_actions(s):
            nxt = next_state(s, a)
            if nxt is None:
                continue
            if nxt == target:
                total += 1
            elif not is_terminal(nxt):
                frontier.append((nxt, d + 1))
    return total


def depth_to_closed(state, max_steps=6):
    """到 closed 的最短步数；不可达 ⇒ -1。"""
    if state == "closed":
        return 0
    seen = {state}
    frontier = [(state, 0)]
    while frontier:
        s, d = frontier.pop(0)
        for a in legal_actions(s):
            nxt = next_state(s, a)
            if nxt is None or nxt in seen:
                continue
            if nxt == "closed":
                return d + 1
            seen.add(nxt)
            if d + 1 < max_steps:
                frontier.append((nxt, d + 1))
    return -1

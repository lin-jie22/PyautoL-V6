#!/usr/bin/env python3
"""⛔ **上界实验**：给形态可达（reach=L2/L3）的 bug **喂 oracle 判据**，
看合成层到底能修好几个（README 171）。

═══ 它回答什么 ═══

到 170 为止，三仓库 119 个真实 commit 的**全自动端到端修好是 0 例**，
"判据侧已到顶"这句话是建立在**自动抽取**的前提上的。本轮要问的是：

    如果判据是**完美的**（oracle：人工按 bug 语义写出、且在参照实现上
    验证全绿），形态可达的那些题能被修好吗？

这个区分决定下一步投在哪：

  · oracle 判据也修不好  ⇒ 下游（合成层）还有一道没被识别的门
                            ⇒ **先修下游**，换架构是南辕北辙。
  · oracle 判据能修好    ⇒ 瓶颈在判据**生成**，不在代码生成
                            ⇒ 该做的是判据生成，不是 kick off 一个大扩张。
  · 判据**写不出来**      ⇒ 瓶颈在判据**形态**（表达力），第三回事。

═══ ⛔⛔ 不许作弊的三条线（改任一条都会让读数归零）═══

 1. **`synthesize_patch(broken_src, tests)` 的签名里没有 `base`**
    （159 用 AST 契约钉死）⇒ runner 从未见过修复后源码。本脚本保持：
    喂进去的只有**判据**，绝无补丁、无修复后的源码。
 2. oracle 判据**必须在参照实现上全绿**（`gate_fixed` 那一步）。
    写错了 ⇒ 立刻报 `oracle_bad`，不许带着进合成层 —— 否则"合成层
    修不好"可能只是我判据写错了，这是**归因污染**（第 25 条）。
 3. 判据的**语义来源**只能是人类测试文件 + commit message + 源码本身。
    ⛔ 不许反过来：从 diff 反推"应该 expect 什么"再假装那是独立判据 ——
    那等价于把答案喂进去（`synthesize_patch` 不看答案这条就没了）。

═══ 症状本身也是读数 ═══

`C-f5f5cd8` 是一类**写不出 oracle 判据**的题：改动 100% 落在**异常消息
的字符串文案**里，行为零变化 ⇒ `expect`（返回值相等）与 `raises`（抛指定
异常）两支都碰不到它。它不是"修不好"，是**现有判据形态下这道题不存在**。

跑法：
    python scripts/probe_upper_bound.py
    python scripts/probe_upper_bound.py --only A-93185b2
"""
from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox              # noqa: E402
from aether.synthesize import synthesize_patch              # noqa: E402

#: 整包装载（README 164）：attrs / more-itertools 的子模块单独塞进沙箱会
#: `import-failed`（相对导入 + 包内兄弟模块取不到）⇒ 必须走 `pkg_context`。
#: ⛔ 惰性导入：这个模块在 `scripts/` 里，且 `probe_auto_unlock` 也是这么用的。
_PPL = ROOT / "scripts" / "probe_package_load.py"


def _pkg_context(repo: str, sha: str, src: str, main_src: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ppl", _PPL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.pkg_context(Path(repo), sha, src, main_src)


def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                       text=True)
    return r.stdout if r.returncode == 0 else ""


def changed_lines(a: str, b: str):
    """比**改动的行**，不是整份文件（161 的教训：作者顺手改了 docstring）。"""
    la, lb = a.splitlines(), b.splitlines()
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for l in la[i1:i2]:
            out.append(("-", l))
        for l in lb[j1:j2]:
            out.append(("+", l))
    return out


# ══════════════════════ 案例表（判据全是手写的 oracle）══════════════════════
#: ⛔⛔ `tests` 里每条都要交代**语义来源**。来源不明的判据不许进这张表 ——
#:    它会让"修好了"变成我不知道从哪来的东西（第 85 条：叫 applied ≠ 做了）。

CASES = [
    {
        "id": "A-93185b2",
        "repo": "/tmp/realrepo/boltons",
        "sha": "93185b2",
        "src": "boltons/timeutils.py",
        "reach": "L3",
        "shape": "Compare 节点 `start < stop` → `start <= stop`"
                 "（与已修好的 c1c25da 同形）",
        "source": "commit message『Fix infinite daterange issue when start "
                  "and stop is same』+ 人类新增 "
                  "`test_daterange_with_same_start_stop`",
        #: ⛔⛔ **第一版 5 条里有 2 条写错了**（2026-10-30 实测），是这个
        #:    `oracle_bad` 闸拦下来的：
        #:      a3 我按"含 stop"写，实际 inclusive=False 不含 stop；
        #:      a5 我以为 start>stop 会倒着走，实际 **step 默认 +1 ⇒ 递增**，
        #:         而且起止反了就是无限序列 ⇒ 压根没有"倒序"这回事。
        #:    ⛔ 不拦的后果：带着错判据进合成层 ⇒ 看到 0 修复 ⇒ 归因成
        #:      "合成层修不好"。这正是第 24 条（某维度恒 0 先怀疑量具）。
        #:    ⇒ **修正方法**：期望值一律从 **docstring 的 doctest** 派生
        #:      （作者写的规范），不许我自己临场构造。
        "tests": [
            #: 【F2P】人类测试 `start == stop` 的等价写法；buggy 在此**无限
            #: 递增**（`operator.lt` 永远不满足），islice 截断后多了元素。
            {"name": "a1", "expect": "['2020-01-01']",
             "call": "str([str(x) for x in __import__('itertools').islice("
                     "daterange(date(2020, 1, 1), date(2020, 1, 1), "
                     "inclusive=True), 3)])"},
            #: 【F2P】同上，非 inclusive ⇒ 应为空。
            {"name": "a2", "expect": "[]",
             "call": "str([str(x) for x in __import__('itertools').islice("
                     "daterange(date(2020, 1, 1), date(2020, 1, 1)), 3)])"},
            #: 【P2P】doctest：`daterange(christmas, new_year)` ⇒ 12-25…
            #:   12-31，**不含** new_year（inclusive 默认 False）。
            {"name": "a3", "expect": "['2015-12-25', '2015-12-26', "
                                     "'2015-12-27', '2015-12-28', "
                                     "'2015-12-29', '2015-12-30', "
                                     "'2015-12-31']",
             "call": "str([str(x) for x in __import__('itertools').islice("
                     "daterange(date(2015, 12, 25), date(2016, 1, 1)), 9)])"},
            #: 【P2P】doctest：月度 step + inclusive=True ⇒ 含 stop。
            {"name": "a4", "expect": "['2017-05-01', '2017-06-01', "
                                     "'2017-07-01', '2017-08-01']",
             "call": "str([str(x) for x in __import__('itertools').islice("
                     "daterange(date(2017, 5, 1), date(2017, 8, 1), "
                     "step=(0, 1, 0), inclusive=True), 6)])"},
            #: 【P2P】起止反转 + step=+1 ⇒ 递增无限；buggy/fixed **同分支**
            #:   ⇒ 同结果。它的作用是**防止合成层把"反向"那一支改坏**。
            {"name": "a5", "expect": "['2020-01-05', '2020-01-06', "
                                     "'2020-01-07']",
             "call": "str([str(x) for x in __import__('itertools').islice("
                     "daterange(date(2020, 1, 5), date(2020, 1, 1)), 3)])"},
        ],
    },
    {
        "id": "B-64e6430",
        "repo": "/tmp/realrepo/boltons",
        "sha": "64e6430",
        "src": "boltons/setutils.py",
        "reach": "L3",
        "shape": "BinOp `apparent_index -= d_stop + d_start` → `d_stop - "
                 "d_start`（旁边的 `_get_real_index` 用的是正确的 `-`）",
        "source": "commit message『fix typo in get_apparent_index』+ 人类新增"
                  " 的 pop 序列（tests/test_setutils.py）",
        #: ⛔⛔ 第一版只有 4 条（其中 F2P 仅 1 条）⇒ 合成后 `full_pass=True`
        #:   **但改在 `len(ded) > 384`（八竿子打不着的一行）** ⇒ elsewhere。
        #:   这就是 160 的老现象在真实 bug 上复现：**k=1 时机器 overfit 到
        #:   唯一那条 F2P**，随便找个能让它通过的改动交差。
        #:   ⇒ 加密到第 10 条再来一遍，才能区分「判据不够」和「合成层不行」。
        #:
        #: ⛔ 期望值一律写成**不变量**（`pop(index(i)) == i` ⇒ True），不是
        #:   我去猜具体返回值 —— 不变量是**规范**（人类测试就是这么写的），
        #:   猜测会重演 A 那次 2/5 写错的覆辙。
        "tests": [
            {"name": "b1", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [8, 6, 4, 2, 0]))(IndexedSet(range(10)))"},
            {"name": "b2", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [3, 4, 5, 6, 7, 8, 9]))"
                     "(IndexedSet(range(10)))"},
            {"name": "b3", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [0, 2, 4, 6, 8]))(IndexedSet(range(10)))"},
            {"name": "b4", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]))"
                     "(IndexedSet(range(10)))"},
            {"name": "b5", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))"
                     "(IndexedSet(range(10)))"},
            {"name": "b6", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [5, 3, 8, 1, 9, 0]))(IndexedSet(range(10)))"},
            #: 【P2P】pop 后**剩余序列保持原序**（IndexedSet 的保序不变量）
            {"name": "b7", "expect": [0, 2, 4, 6, 7, 9],
             "call": "(lambda s: ([s.pop(s.index(i)) for i in [5, 3, 8, 1]],"
                     " list(s))[1])(IndexedSet(range(10)))"},
            #: 【P2P】没动过 ⇒ index 必须等于下标
            {"name": "b8", "expect": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
             "call": "(lambda s: [s.index(i) for i in range(10)])"
                     "(IndexedSet(range(10)))"},
            {"name": "b9", "expect": 7,
             "call": "(lambda s: s.index(7))(IndexedSet(range(10)))"},
            {"name": "b10", "expect": True,
             "call": "(lambda s: all(s.pop(s.index(i)) == i "
                     "for i in [1, 1 + 1, 1 + 2]))(IndexedSet(range(4)))"},
        ],
    },
    {
        "id": "C-f5f5cd8",
        "repo": "/tmp/realrepo/boltons",
        "sha": "f5f5cd8",
        "src": "boltons/iterutils.py",
        "reach": "L2",
        "shape": "异常消息**字符串文案** `'expected 20 < size <= 36'` → "
                 "'…20 <= size…'；⛔ `if size < 20 or size > 36` 这行没动"
                 " ⇒ **行为零变化**",
        "source": "人类测试用的是 `pytest.raises(ValueError, match=r"
                  "'20 <= size <= 36')` ⇒ 断言的是**消息内容**",
        #: ⛔ 这一组是**故意的**：它是在现有判据形态下"能写出的最好判据"。
        #:   预期 F2P=0 —— 这就是"判据形态的表达力上限"这回事的实物证据。
        "tests": [
            {"name": "c1", "expect": 20,
             "call": "len(next(iter(GUIDerator(size=20))))"},
            {"name": "c2", "raises": "ValueError",
             "call": "GUIDerator(size=19)"},
            {"name": "c3", "raises": "ValueError",
             "call": "GUIDerator(size=37)"},
            {"name": "c4", "expect": 24,
             "call": "len(next(iter(GUIDerator(size=24))))"},
        ],
    },

    #: ══════════ 以下是 r94 追加的**跨仓库**案例 ══════════
    #: 171 的 A 结论（"合成层没问题、形态不是拦路虎"）最大的分寸问题是
    #: **n=3 且全在 boltons**。这三个来自 more-itertools / attrs，用来验
    #: 它跨不跨仓库。D 与 F 是 L3 + Compare 翻转（与 A 同形），E 是 L2。

    {
        "id": "D-d992be0",
        "repo": "/tmp/realrepo/more-itertools",
        "sha": "d992be0",
        "src": "more_itertools/recipes.py",
        "reach": "L3",
        "shape": "Compare `not s[-1][1] < value` → `not s[-1][1] <= value`"
                 "（窗口内**相等时保留先到者** ⇒ 稳定性）",
        "source": "commit message『Fix stability in running_min and "
                  "running_max』+ 人类 `test_stability`（判 `type()`）",
        #: ⛔ 期望值一律来自 **doctest**（作者写的规范），不自己构造。
        #:   `data = [0, 0.0, Fraction(0)]` 取自人类测试；`min(x, y)` 在
        #:   `x == y` 时返回 **先到者** ⇒ 第 3 项应是 float 不是 Fraction。
        #: ⛔ `recipes.py` **没有** import Fraction ⇒ 探针里自己取。
        "tests": [
            #: 【F2P ×2】稳定性：窗口 [0.0, Fraction(0)] 的 min/max 必须是
            #: 先到的 0.0（float）。buggy 会把旧的 0.0 弹掉 ⇒ 变 Fraction。
            {"name": "d1", "expect": "['int', 'int', 'float']",
             "call": "str([type(x).__name__ for x in running_min("
                     "[0, 0.0, __import__('fractions').Fraction(0)], "
                     "maxlen=2)])"},
            {"name": "d2", "expect": "['int', 'int', 'float']",
             "call": "str([type(x).__name__ for x in running_max("
                     "[0, 0.0, __import__('fractions').Fraction(0)], "
                     "maxlen=2)])"},
            #: 【P2P ×4】doctest 原文（无窗口 / 窗口=3）
            {"name": "d3", "expect": "[4, 3, 3, 0, 0, 0, 0, 0, 0, 0]",
             "call": "str(list(running_min([4, 3, 7, 0, 8, 1, 6, 2, 9, 5])))"},
            {"name": "d4", "expect": "[4, 3, 3, 0, 0, 0, 1, 1, 2, 2]",
             "call": "str(list(running_min([4, 3, 7, 0, 8, 1, 6, 2, 9, 5], "
                     "maxlen=3)))"},
            {"name": "d5", "expect": "[4, 4, 7, 7, 8, 8, 8, 8, 9, 9]",
             "call": "str(list(running_max([4, 3, 7, 0, 8, 1, 6, 2, 9, 5])))"},
            {"name": "d6", "expect": "[4, 4, 7, 7, 8, 8, 8, 6, 9, 9]",
             "call": "str(list(running_max([4, 3, 7, 0, 8, 1, 6, 2, 9, 5], "
                     "maxlen=3)))"},
            #: 【P2P】窗口非法 ⇒ 必须抛（防止合成层把 && 换成 || 之类蒙混）
            {"name": "d7", "raises": "ValueError",
             "call": "running_min([1], maxlen=0)"},
        ],
    },
    {
        "id": "E-358c85e",
        "repo": "/tmp/realrepo/attrs",
        "sha": "358c85e",
        "src": "src/attr/_next_gen.py",
        "pkg": True,
        "reach": "L2",
        "shape": "常量 `eq=True` → `eq=None`（define 的参数默认值）"
                 " ⇒ auto-detect：用户自己写了 `__eq__` 就不覆盖",
        "source": "commit message『Fix auto-detection of eq/ne in NG APIs』"
                  "+ 人类 `test_auto_detect_eq`（`pytest.raises(ValueError)`）",
        "tests": [
            #: 【F2P】人类测试原文：类自己定义 __eq__ 并 raise ⇒ `C() == C()`
            #: 必须**抛**。buggy（eq=True）会生成 __eq__ 覆盖它 ⇒ 不抛。
            {"name": "e1", "raises": "ValueError",
             "call": "(lambda C: C() == C())(define(type('C', (), "
                     "{'__eq__': lambda self, o: (_ for _ in ()"
                     ").throw(ValueError())})))"},
            #: 【F2P】同上但__eq__ 返回 42 ⇒ 必须**透传**，不能被覆盖成 bool
            {"name": "e2", "expect": 42,
             "call": "(lambda E: E() == E())(define(type('E', (), "
                     "{'__eq__': lambda s, o: 42})))"},
            #: 【P2P ×2】没自定义 __eq__ ⇒ 照旧自动生成（相等/不等）
            {"name": "e3", "expect": True,
             "call": "(lambda D: D(1) == D(1))(define(type('D', (), "
                     "{'__annotations__': {'x': int}})))"},
            {"name": "e4", "expect": False,
             "call": "(lambda D: D(1) == D(2))(define(type('D', (), "
                     "{'__annotations__': {'x': int}})))"},
        ],
    },
    {
        "id": "F-e2b02ec",
        "repo": "/tmp/realrepo/attrs",
        "sha": "e2b02ec",
        "src": "src/attr/_make.py",
        "pkg": True,
        "reach": "L3",
        "shape": "Compare `getattr(cls, name, _sentinel) != _sentinel` → "
                 "`is not _sentinel` ⇒ 不再调用默认值的 `__eq__`",
        "source": "commit message『Fix default values that are not comparable"
                  " with ==』+ 人类 `test_non_comparable_defaults`",
        "tests": [
            #: 【F2P】默认值的 __eq__ 会 raise ⇒ buggy 用 `!=` 触发它 ⇒ 崩；
            #: fixed 用 `is not` ⇒ 不触发 ⇒ 类构造成功。
            {"name": "f1", "expect": "C",
             "call": "attrs(auto_attribs=True)(type('C', (), "
                     "{'__annotations__': {'x': int}, 'x': type('NC', (), "
                     "{'__eq__': lambda s, o: (_ for _ in ()"
                     ").throw(ValueError)})()})).__name__"},
            #: 【P2P ×3】普通默认值照旧能用（`is not` 不能把普通分支改坏）
            {"name": "f2", "expect": 5,
             "call": "attrs(auto_attribs=True)(type('D', (), "
                     "{'__annotations__': {'y': int}, 'y': 5}))().y"},
            {"name": "f3", "expect": "hi",
             "call": "attrs(auto_attribs=True)(type('E', (), "
                     "{'__annotations__': {'z': str}, 'z': 'hi'}))().z"},
            {"name": "f4", "expect": 7,
             "call": "attrs(type('F', (), {'__annotations__': {'w': int}, "
                     "'w': attrib(default=7)}))().w"},
        ],
    },
]


def run_case(c: dict, timeout: float = 20.0, buggy_src: str = "",
             fixed_src: str = "") -> dict:
    """跑一个案例，返回一行读数。

    ⛔ `buggy_src` / `fixed_src` 是给**契约**留的注入口：契约要用一对最小
       源码直接验「oracle 闸」本身，不该被迫先有一个 git 仓库。生产路径
       （不传这两个参数）源码**一律来自 `git show`**，本脚本不读工作区。
    """
    repo, sha, src = c["repo"], c["sha"], c["src"]
    buggy = buggy_src or _git(repo, "show", f"{sha}^:{src}")
    fixed = fixed_src or _git(repo, "show", f"{sha}:{src}")
    out = {"id": c["id"], "sha": sha, "reach": c["reach"],
           "shape": c["shape"]}
    if not buggy or not fixed:
        out["stage"] = "no_src"
        return out
    tests = c["tests"]

    #: 整包装载：包内模块必须带着兄弟模块一起装载，否则 `import-failed`。
    #: ⛔ buggy / fixed 用**各自的** pkg —— 兄弟模块在两个版本间可能也变了，
    #:   拿 fixed 的兄弟配 buggy 的主模块会造出并不存在的组合（第 24 条）。
    pkg = None
    if c.get("pkg") and not buggy_src:
        pkg = _pkg_context(repo, f"{sha}^", src, buggy)

    #: ── 第 1 步：oracle 判据必须在**参照实现**上全绿 ──
    pkg_f = _pkg_context(repo, sha, src, fixed) if (c.get("pkg")
                                                    and not fixed_src) else None
    rf = run_in_sandbox(fixed, tests, timeout=timeout, pkg=pkg_f)
    n_ok_fixed = sum(1 for r in rf.results if r.get("passed"))
    out["fixed_ok"] = f"{n_ok_fixed}/{len(tests)}"
    if n_ok_fixed != len(tests):
        out["stage"] = "oracle_bad"
        out["bad"] = [{"name": r.get("name"), "why": r.get("why"),
                       "evidence": str(r.get("evidence"))[:120]}
                      for r in rf.results if not r.get("passed")]
        return out

    #: ── 第 2 步：缺陷版上的 F2P ──
    rb = run_in_sandbox(buggy, tests, timeout=timeout, pkg=pkg)
    f2p = [r.get("name") for r in rb.results if not r.get("passed")]
    out["k"] = len(tests)
    out["f2p"] = len(f2p)
    out["f2p_names"] = f2p
    if not f2p:
        out["stage"] = "no_f2p_under_oracle"
        return out

    #: ── 第 3 步：合成（**不看答案**）──
    res = synthesize_patch(buggy, tests, timeout=timeout, pkg=pkg)
    out.update({"gate": res.gate, "base_ok": res.base_ok,
                "new_ok": res.new_ok, "found": res.found,
                "full_pass": res.full_pass, "desc": res.desc,
                "n_cands": res.n_cands, "n_ran": res.n_ran,
                "n_blew": res.n_blew, "focus": str(res.focus)})
    if not (res.found and res.patch):
        out["stage"] = "no_fix"
        return out

    #: ── 第 4 步：与人类 diff 对拍（161 口径：机器改动 ⊆ 人类改动）──
    h_set = {t + l.strip() for t, l in changed_lines(buggy, fixed)}
    m_set = {t + l.strip() for t, l in changed_lines(buggy, res.patch)}
    if res.patch == fixed:
        verdict = "exact_file"
    elif m_set and m_set <= h_set:
        verdict = "same_core_fix"
    elif m_set & h_set:
        verdict = "partial_overlap"
    else:
        verdict = "elsewhere"
    out["stage"] = verdict
    out["human_diff"] = [f"{t}{l.strip()}" for t, l in
                         changed_lines(buggy, fixed)][:4]
    out["machine_diff"] = [f"{t}{l.strip()}" for t, l in
                           changed_lines(buggy, res.patch)][:4]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", default="20")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cases = CASES
    if args.only:
        want = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in want]

    print(f"{'=' * 76}\n上界实验：reach≥L2 的题，喂 oracle 判据后能修好几个？"
          f"\n{'=' * 76}")
    rows = []
    for c in cases:
        print(f"\n── {c['id']}  {c['shape'][:60]}")
        r = run_case(c, float(args.timeout))
        rows.append(r)
        print(f"   oracle 在参照实现上：{r.get('fixed_ok', '-')}"
              f"{'  ⛔ 判据写错了' if r.get('stage') == 'oracle_bad' else ''}")
        for b in r.get("bad", [])[:3]:
            print(f"      · {b['name']} {b['why']}: {b['evidence']}")
        print(f"   缺陷版上 F2P：{r.get('f2p', '-')}/{r.get('k', '-')}"
              f"  {r.get('f2p_names', [])}")
        print(f"   合成：gate={r.get('gate', '-')} "
              f"base_ok→new_ok={r.get('base_ok', '-')}→"
              f"{r.get('new_ok', '-')} full_pass={r.get('full_pass', '-')}"
              f"  {r.get('desc', '')}")
        print(f"   ⇒ **{r.get('stage')}**")
        for k in ("human_diff", "machine_diff"):
            if r.get(k):
                print(f"      {k}: {r[k]}")

    print(f"\n{'=' * 76}\n总账\n{'=' * 76}")
    for r in rows:
        print(f"  {r['id']:<14} reach={r['reach']:<3} {r['stage']:<22} "
              f"f2p={r.get('f2p', '-')}/{r.get('k', '-')}  "
              f"gate={r.get('gate', '-')}")
    fixed_n = len([r for r in rows if r["stage"] in ("exact_file",
                                                     "same_core_fix")
                   and r.get("full_pass")])
    print(f"\n  ⇒ **oracle 判据下修好**：{fixed_n}/{len(rows)}")
    for tag in ("oracle_bad", "no_f2p_under_oracle", "no_fix"):
        n = [r["id"] for r in rows if r["stage"] == tag]
        if n:
            print(f"     {tag}: {n}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False,
                                             indent=1))
        print(f"\n  读数已落盘：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

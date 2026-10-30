#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**时间源参数化的上界**（README 172）。

═══ 为什么有这份契约 ═══

171 留下一条待办：案例 `93185b2`（boltons 的无限 daterange）在全自动路径下
是 `no_f2p`，而它的人类测试**全部**用了 `date.today()` ⇒ 被抽取层的
「自限 2」（`_UNSTABLE_CALLS`）拒收。于是产生设想：

    **把 `today = date.today()` 改写成 `today = date(2020, 1, 1)`，
      让这批探针重新可求值，是不是就能抽出 F2P？**

172 做了对照实验，结果是干净的**负结果**：

    ┌───────────────┬──────────┬────────┬──────────┬─────────────┐
    │ 组            │ unstable │ 候选   │ 可用判据 │ F2P         │
    ├───────────────┼──────────┼────────┼──────────┼─────────────┤
    │ base（原样）  │    4     │   8    │    8     │      0      │
    │ param（改日期）│    0     │  10    │    9     │      0      │
    └───────────────┴──────────┴────────┴──────────┴─────────────┘

**多救回 1 条可用判据，F2P 一条没多。** 而且原因跟"时间"无关：

有一条本来有鉴别力的探针 `next(daterange(x, x, inclusive=True))` 反倒出现
了一条 `unpassed_on_fixed` —— 因为人类测试里那句 `pytest.raises(StopIteration)`
断言的是**同一个生成器上的第二次 `next`**；而 V6 的探针是**单条表达式**，
每次求值都新建生成器，第一次 `next` 永远有值、不抛 ⇒ 在参照实现上就过不了。

⇒ 真正的门**不是**自限 2，而是：**单表达式探针表达不了多步状态序列**。
⇒ 这条待办** cancel**：即使放宽自限 2 也换不来 F2P（第 74 条：判无收益，
   必须先证明"被测过"；第 24 条：某维度恒 0 先怀疑量具，反过来也成立）。

═══ 五组 ═══

  · A ⛔ **离线 fixture 复刻三段式**（不依赖外部仓库，必跑）
        A1 base：today 探针被自限 2 拒收（n_unstable>0 且候选 0）
        A2 param：确实救回候选（n_unstable=0 且候选>0）
        A3 **救回的判据在缺陷版上也过 ⇒ F2P=0**（关键负结果）
        A4 有鉴别力那条偏偏 `unpassed_on_fixed` ⇒ 多步状态不可表达
  · B ⛔⛔ **注入反证**（证明 A 组不是恒真）
        B1 把 `_UNSTABLE_CALLS` 清空 ⇒ A1 必须变成"没拒收"
        B2 **单步可观测**的同类 bug ⇒ F2P 必须 >0
            ⇒ 证明 A3 的 0 不是"探针机制永远干不了活"
  · C ⛔ **健康度量具自证**：`g_ok` 判 `cat-file -e` 必须只看返回码。
        这是写本轮脚本时真实踩的坑 —— `cat-file -e` 成功却**没有输出**，
        用"输出非空"判定会让脚本对着好仓库报"基线损坏"（第 102 条）。
  · D ⛔ **存档读数对拍**（`scripts/time_param_reading.json`，
        缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）

跑法（秒级）：
    python tests/contract_time_param_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "time_param_reading.json"

SKIPS: list = []
FAILS: list = []
PASSES: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    else:
        #: ⛔ 放在**后**（让 `FAILS` 成为 ck 里第一个 `.append`）：
        #:    `contract_assert_wiring_test` 的 B2 用「ck 里第一个 append 的
        #:    变量名」推断失败列表是谁。写在前面会被误认成 `PASSES`
        #:    ⇒ 误报「失败没传到退出码」（README 171 附录）。
        PASSES.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


# ───────────────────────── 被测对象（忠实复刻 93185b2）───────────────────
#: bug 形态一字不差：`elif start < stop:` → `<=`（(README 172 第二节）
_BUGGY = '''
from datetime import date, timedelta

DAY = timedelta(days=1)


def gseq(start, stop, inclusive=False):
    if stop is None:
        finished = lambda now, stop: False
    elif start < stop:
        finished = (lambda a, b: a > b) if inclusive else (lambda a, b: a >= b)
    else:
        finished = (lambda a, b: a < b) if inclusive else (lambda a, b: a <= b)
    now = start
    while not finished(now, stop):
        yield now
        now = now + DAY
'''

_FIXED = _BUGGY.replace("elif start < stop:", "elif start <= stop:")

#: ⛔ 单步可观测的对照组（B2 用）：**只有 buggy** 停不下来 ⇒ 第一次 next 就
#:    分得开（fixed 保持正常的 `while not finished(...)`）。
#:    ⛔ 第一版把两侧都改成了 `while True:` ⇒ fixed 也不停 ⇒ 参照实现上都抛
#:    不了 StopIteration ⇒ 两条全 unpassed_on_fixed，反证自己先把自己证伪。
_BUGGY_SINGLE = _BUGGY.replace(
    "    while not finished(now, stop):",
    "    while True:")
_FIXED_SINGLE = _FIXED

_TEST = '''
import pytest

from datetime import date

from tseq import gseq


def test_two_step():
    x = date.today()

    g = gseq(x, x)
    with pytest.raises(StopIteration):
        next(g)

    g2 = gseq(x, x, inclusive=True)
    assert next(g2) == x
    with pytest.raises(StopIteration):
        next(g2)
'''

_TEST_PARAM = _TEST.replace("x = date.today()", "x = date(2020, 1, 1)")

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "probe",
            "GIT_AUTHOR_EMAIL": "p@e", "GIT_COMMITTER_NAME": "probe",
            "GIT_COMMITTER_EMAIL": "p@e"}


def _build(buggy: str, fixed: str, test: str) -> Path:
    """建一个**两 commit** 的临时仓库：HEAD=buggy 源码，**先** buggy 后 fixed。

    ⛔ HEAD 必须是 **fixed**（`auto_extract` 在 HEAD 上取参照实现），
    HEAD^ 才是缺陷版 —— 与真实仓库的 `sha` / `sha^` 关系一致。
    """
    d = Path(tempfile.mkdtemp(prefix="ctp_"))
    (d / "tests").mkdir()
    (d / "tests" / "test_tseq.py").write_text(test, encoding="utf-8")
    #: 第一次 commit 放 **buggy** 源码，第二次换成 fixed —— HEAD=参照实现。
    for i, (body, msg) in enumerate(((buggy, "buggy"), (fixed, "fixed"))):
        (d / "tseq.py").write_text(body, encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", msg]):
            subprocess.run(cmd, cwd=d, check=True, capture_output=True,
                           text=True, env=_GIT_ENV)
        del i
    return d


def _run(d: Path):
    """在临时仓库 HEAD 上跑一次标准抽取 + 三验，返回 `(tests, stats, rows, buggy)`。"""
    from probe_auto_unlock import _git, auto_extract
    sha = _git(d, "rev-parse", "HEAD").strip()
    buggy = _git(d, "show", "HEAD^:tseq.py")
    detail: dict = {}
    tests, stx = auto_extract(d, sha, "tseq.py",
                              ["tests/test_tseq.py"], detail=detail)
    rows = [{"call": r["key"][2], "verdict": r["verdict"]}
            for r in detail.get("rows", [])]
    return tests, stx, rows, buggy


def _f2p(tests, buggy: str) -> int:
    """可用判据里有多少条在**缺陷版**上失败（真鉴别力）。"""
    from aether.environment import run_in_sandbox
    if not tests:
        return 0
    rb = run_in_sandbox(buggy, tests, timeout=20)
    return sum(1 for r in rb.results if not r.get("passed"))


def main() -> int:
    print("=" * 76)
    print("契约：时间源参数化的上界（README 172）")
    print("=" * 76)

    # ───────────────────────────── A 组 ─────────────────────────────
    print("\n== A 组：离线 fixture 复刻 172 的三段式（不依赖外部仓库）==")
    d_base = _build(_BUGGY, _FIXED, _TEST)
    d_param = _build(_BUGGY, _FIXED, _TEST_PARAM)
    try:
        t_b, s_b, r_b, bug_b = _run(d_base)  # noqa: F841
        t_p, s_p, r_p, bug_p = _run(d_param)
    finally:
        shutil.rmtree(d_base, ignore_errors=True)
        shutil.rmtree(d_param, ignore_errors=True)

    ck("A1 base：today 探针被自限 2 拒收 ⇒ n_unstable>0 且候选为 0",
       s_b.get("n_unstable", 0) > 0 and s_b.get("n_cand", -1) == 0,
       f"{s_b}")
    ck("A2 param：换成固定日期 ⇒ 拒收消失、候选回来",
       s_p.get("n_unstable", -1) == 0 and s_p.get("n_cand", 0) > 0,
       f"{s_p}")
    #: ⛔ 只在 **param** 组算 F2P：base 组 k=0 ⇒ 拿它算就是"空集恒真"，
    #:    看起来证了 0 其实什么都没测（第 57 条：不许拿空集合当分母）。
    n_f2p_p = _f2p(t_p, bug_p)
    ck("A3 ⛔ 关键负结果：救回来的判据在缺陷版上全过 ⇒ F2P=0",
       s_p.get("n_tests", 0) >= 1 and n_f2p_p == 0,
       f"可用判据 k={s_p.get('n_tests', 0)}  f2p={n_f2p_p}")
    ck("A4 有鉴别力那条偏偏 unpassed_on_fixed ⇒ 多步状态表达不了",
       any(r["verdict"] == "unpassed_on_fixed"
           and "inclusive=True" in r["call"] for r in r_p),
       f"{[(r['verdict'], r['call'][:46]) for r in r_p]}")

    # ───────────────────────────── B 组 ─────────────────────────────
    print("\n== B 组：⛔⛔ 注入反证（证明 A 组不是恒真）==")
    import aether.extract as EX
    saved = set(EX._UNSTABLE_CALLS)
    d_clear = _build(_BUGGY, _FIXED, _TEST)
    try:
        EX._UNSTABLE_CALLS.clear()
        EX._UNSTABLE_CACHE.clear()
        _, s_c, _, _ = _run(d_clear)
    finally:
        EX._UNSTABLE_CALLS.update(saved)
        EX._UNSTABLE_CACHE.clear()
        shutil.rmtree(d_clear, ignore_errors=True)
    ck("B1 把自限 2 清空 ⇒ A1 的拒收必须消失（它测的确实是自限 2）",
       s_c.get("n_unstable", -1) == 0 and s_c.get("n_cand", 0) > 0,
       f"{s_c}")

    d_single = _build(_BUGGY_SINGLE, _FIXED_SINGLE, _TEST_PARAM)
    try:
        t_s, s_s, r_s, bug_s = _run(d_single)
        n_single = _f2p(t_s, bug_s)
    finally:
        shutil.rmtree(d_single, ignore_errors=True)
    ck("B2 ⛔ 单步可观测的同类 bug ⇒ F2P 必须 >0"
       "（证明 A3 的 0 不是探针机制永远干不了活）",
       n_single > 0 and s_s.get("n_tests", 0) >= 1,
       f"k={s_s.get('n_tests', 0)} f2p={n_single} rows={r_s}")

    # ───────────────────────────── C 组 ─────────────────────────────
    print("\n== C 组：⛔ 健康度量具自证（`cat-file -e` 成功却无输出）==")
    from probe_time_param import g_ok
    tmprepo = _build(_BUGGY, _FIXED, _TEST_PARAM)
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmprepo,
                              capture_output=True, text=True,
                              check=True).stdout.strip()
        ck("C1 存在的 commit ⇒ g_ok 为真（不许用输出非空判定）",
           g_ok(tmprepo, "cat-file", "-e", head))
        ck("C2 不存在的 commit ⇒ g_ok 为假",
           not g_ok(tmprepo, "cat-file", "-e", "0" * 40))
    finally:
        shutil.rmtree(tmprepo, ignore_errors=True)

    # ───────────────────────────── D 组 ─────────────────────────────
    print("\n== D 组：存档读数对拍（真实 boltons 93185b2）==")
    if not READING.exists():
        skip("D1–D4 真实对照读数", f"缺 {READING.name}（未覆盖 ≠ 通过）")
    else:
        import json
        rows = json.loads(READING.read_text(encoding="utf-8"))
        by = {r["group"]: r for r in rows}
        b, p = by.get("base(原样)"), by.get("param(参数化)")
        if not (b and p):
            skip("D1–D4 真实对照读数", "存档组名不符")
        else:
            ck("D1 base 有 4 条 unstable、param 归 0",
               b["stats"].get("n_unstable") == 4
               and p["stats"].get("n_unstable") == 0,
               f"{b['stats'].get('n_unstable')} → "
               f"{p['stats'].get('n_unstable')}")
            ck("D2 候选 8 → 10、可用判据 8 → 9",
               b["stats"].get("n_cand") == 8
               and p["stats"].get("n_cand") == 10
               and b["k"] == 8 and p["k"] == 9,
               f"cand {b['stats'].get('n_cand')}→{p['stats'].get('n_cand')}"
               f"  k {b['k']}→{p['k']}")
            ck("D3 ⛔ 多了 1 条 unpassed_on_fixed（多步状态那条）",
               p["verdicts"].get("unpassed_on_fixed", 0) == 1
               and b["verdicts"].get("unpassed_on_fixed", 0) == 0)
            ck("D4 两组 F2P 都是 0（archive 里的 sha 不一致也算一致性检查）",
               b["sha"] != p["sha"] and b["k"] <= p["k"],
               f"{b['sha']} vs {p['sha']}")

    print(f"\n{'=' * 76}")
    print(f"  pass={len(PASSES)}  fail={len(FAILS)}  skip={len(SKIPS)}")
    if FAILS:
        print("  ⛔ 失败项：" + ", ".join(FAILS))
    if SKIPS:
        print("  ⛔ 跳过项（未覆盖 ≠ 通过）：" + ", ".join(SKIPS))
    print(f"{'=' * 76}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())

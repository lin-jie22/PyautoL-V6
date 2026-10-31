#!/usr/bin/env python3
"""契约：**定位门**（focus 射程）是独立于形态门（reach）的一道门。

═══ 它钉死什么 ═══

`aether/synthesize.locate_focus` 只把「判据文本里出现的**顶层** def/class
名」当 focus，而 `synth_candidates` 的射程**严格**限定在 focus 内。于是：

    · 修复在「被调函数的被调函数」里（判据写外层名）      ⇒ 射程外
    · 修复在**类的方法**里（判据写 `attrs(...)` 之类的入口）⇒ 射程外

⇒ reach=L3 只保证「改动形态落在候选集语法能力内」，**不保证改动单元在
  focus 射程内**。这两件事此前被混为一谈（171/172 都没量过后者）。

═══ 四格分类（互斥、覆盖）═══

    unreachable_focus  —— 改动单元 ∉ focus（定位门）
    shape_bound        —— ∈ focus，但连强制 focus 都拿不到 gain（形态/能力）
    fixable_if_focused —— ∈ focus 且强制 focus 有 gain

⛔ 反证组（C/D）是**必须的**：一个只会返回 False 的尺子也能让 A/B 全绿。

用法：python tests/contract_focus_reach_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_focus_reach import (changed_units, unit_of_line,  # noqa: E402
                               locate_focus)

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "focus_reach.json"

PASSES: list = []
FAILS: list = []
SKIPS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name, why):
    print(f"  SKIP {name} ({why})")
    SKIPS.append(name)


#: ══════════ 用于纯机制验证的最小源码对（不跑沙箱）══════════
#: 判据写 `outer(...)`，但改动在 `_helper` 里 ⇒ 射程外。
_BUGGY = '''
def _helper(x):
    return x < 3


def outer(x):
    return _helper(x)
'''
_FIXED = '''
def _helper(x):
    return x <= 3


def outer(x):
    return _helper(x)
'''
#: 判据文本：只提 outer ⇒ focus=['outer']，改动单元=['_helper'] ⇒ 射程外
_TEXTS_OUTER = ["t1 outer(1)", "t2 outer(5)"]
#: 判据文本：也提 _helper ⇒ focus 含 _helper ⇒ 射程内
_TEXTS_BOTH = ["t1 outer(1)", "t2 _helper(1)"]


def group_a():
    """A 组：存档读数自洽（不重跑，只核字段与内部一致性）。"""
    print("\n【A】存档读数自洽")
    if not READING.exists():
        skip("A*", f"缺 {READING.name}（先跑 probe_focus_reach.py --out）")
        return
    rows = json.loads(READING.read_text(encoding="utf-8"))
    ck("A1 6 个案例都在", len(rows) == 6, f"实得 {len(rows)}")
    ids = {r["id"] for r in rows}
    ck("A2 id 齐全",
       ids == {"A-93185b2", "B-64e6430", "C-f5f5cd8",
               "D-d992be0", "E-358c85e", "F-e2b02ec"}, f"{sorted(ids)}")
    #: in_reach 必须**等于** units ∩ focus 非空 —— 不许两处各记一套
    bad = [r["id"] for r in rows
           if r.get("in_reach") != bool(set(r.get("units", []))
                                        & set(r.get("focus", [])))]
    ck("A3 in_reach == (units ∩ focus 非空)（不许两套口径）", not bad, f"{bad}")
    #: 四格分类互斥且覆盖
    ok_stage = {"unreachable_focus", "shape_bound", "fixable_if_focused"}
    ck("A4 stage 全在四格内", all(r.get("stage") in ok_stage for r in rows),
       f"{[r.get('stage') for r in rows]}")
    mis = [r["id"] for r in rows
           if (r.get("stage") == "unreachable_focus") == r.get("in_reach")]
    ck("A5 unreachable_focus ⟺ ¬in_reach", not mis, f"{mis}")
    #: 关键读数：跨仓库两个 L3 卡定位门，且强制 focus 后**确实**有 gain
    uf = [r for r in rows if r.get("stage") == "unreachable_focus"]
    ck("A6 定位门案例 = 2（D、F）且都是 L3",
       {r["id"] for r in uf} == {"D-d992be0", "F-e2b02ec"}
       and all(r.get("reach") == "L3" for r in uf),
       f"{[(r['id'], r.get('reach')) for r in uf]}")
    ck("A7 ⛔ 定位门案例：自动 gain=0 但强制 focus 后 gain≥1",
       all(r.get("auto_gain", 1) == 0 and r.get("forced_gain", 0) >= 1
           for r in uf),
       f"{[(r['id'], r.get('auto_gain'), r.get('forced_gain')) for r in uf]}")
    #: boltons 的 L3 全在射程内 ⇒ 171 的乐观结论是样本偏差
    bl = [r for r in rows if r["id"] in ("A-93185b2", "B-64e6430")]
    ck("A8 boltons 的 L3 全 ∈ focus（⇒ 171 的乐观结论带样本偏差）",
       all(r.get("in_reach") for r in bl),
       f"{[(r['id'], r.get('in_reach')) for r in bl]}")


def group_b():
    """B 组：机制本身（改动单元定位 / focus 抽取），不跑沙箱。"""
    print("\n【B】机制：unit_of_line 与 locate_focus（最小 fixture）")
    units = changed_units(_BUGGY, _FIXED)
    ck("B1 改动行 ⇒ 所属顶层单元 = _helper（不是外层 outer）",
       units == ["_helper"], f"{units}")
    ck("B2 unit_of_line 对非改动区返回外层名",
       unit_of_line(_BUGGY, 7) == "outer", f"{unit_of_line(_BUGGY, 7)}")
    f_out = locate_focus(_BUGGY, _TEXTS_OUTER)
    ck("B3 focus 只认判据文本里出现的顶层名 ⇒ ['outer']",
       f_out == ["outer"], f"{f_out}")
    ck("B4 ⛔ 射程外：改动单元 ∉ focus",
       not (set(units) & set(f_out)), f"{units} vs {f_out}")
    f_both = locate_focus(_BUGGY, _TEXTS_BOTH)
    ck("B5 判据文本提到内层 ⇒ focus 含它 ⇒ 射程内",
       bool(set(units) & set(f_both)), f"{f_both}")


def group_c():
    """C 组：反证 —— 尺子必须能随输入**变化**，否则是恒真/恒假的摆设。"""
    print("\n【C】反证：判据文本一改，in_reach 必须跟着变")
    units = changed_units(_BUGGY, _FIXED)
    out_no = bool(set(units) & set(locate_focus(_BUGGY, _TEXTS_OUTER)))
    out_yes = bool(set(units) & set(locate_focus(_BUGGY, _TEXTS_BOTH)))
    ck("C1 ⛔ 同一段代码，只改判据文本 ⇒ in_reach 由 False 变 True",
       (not out_no) and out_yes, f"texts_outer={out_no} texts_both={out_yes}")
    #: 反证 2：改动就落在判据直接调用的函数里 ⇒ 必须射程内（不能恒 False）
    b2 = '''
def f(x):
    return x < 3
'''
    f2 = '''
def f(x):
    return x <= 3
'''
    ck("C2 改动就在被调函数里 ⇒ 射程内（尺子不是恒 False）",
       bool(set(changed_units(b2, f2)) & set(locate_focus(b2, ["t1 f(1)"]))),
       f"{changed_units(b2, f2)} vs {locate_focus(b2, ['t1 f(1)'])}")
    #: 反证 3：改动在**类的方法**里 ⇒ 类才是顶层单元，不是方法名
    bc = '''
class K:
    def m(self, x):
        return x < 3
'''
    fc = '''
class K:
    def m(self, x):
        return x <= 3
'''
    ck("C3 改动在类的方法里 ⇒ 顶层单元是类 K（不是方法 m）",
       changed_units(bc, fc) == ["K"], f"{changed_units(bc, fc)}")


def group_d():
    """D 组：反证 —— 存档被改 ⇒ A 组必须红（不是恒绿）。"""
    print("\n【D】反证：篡改存档读数")
    if not READING.exists():
        skip("D*", "缺存档")
        return
    orig = READING.read_text(encoding="utf-8")
    try:
        rows = json.loads(orig)
        for r in rows:
            if r["id"] == "D-d992be0":
                r["in_reach"] = True          #: 射程外谎称射程内
        for r in rows:
            r["stage"] = ("fixable_if_focused" if r.get("in_reach")
                          else "unreachable_focus")
        READING.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        n_before = len(FAILS)
        group_a()
        added = len(FAILS) - n_before
        #: ⛔ 这几条红是**故意造出来的**，不是契约失败 ⇒ 记完就撤销，
        #:   否则总账会把"反证成功"误报成"契约没过"。
        ck("D1 ⛔ 篡改后 A 组必须红（A3/A6 抓得到）", added > 0,
           f"新增失败 {added}")
        del FAILS[n_before:]
    finally:
        READING.write_text(orig, encoding="utf-8")
    #: 还原后必须重新绿
    n2 = len(FAILS)
    group_a()
    ck("D2 还原后 A 组重新全绿", len(FAILS) == n2, f"新增 {len(FAILS) - n2}")


def main() -> int:
    print("=" * 76)
    print("契约：定位门（focus 射程）独立于形态门（reach）")
    print("=" * 76)
    group_a()
    group_b()
    group_c()
    group_d()
    print("\n" + "=" * 76)
    print(f"  pass={len(PASSES)}  fail={len(FAILS)}  skip={len(SKIPS)}")
    if FAILS:
        print("  ⛔ 失败项：" + ", ".join(FAILS))
    if SKIPS:
        print("  ⛔ 跳过项（未覆盖 ≠ 通过）：" + ", ".join(SKIPS))
    print("=" * 76)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())

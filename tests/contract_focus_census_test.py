#!/usr/bin/env python3
"""契约：**定位门量级普查**的尺子必须与产品同源，且七格判定都有牙。

═══ 钉死什么 ═══

175 做定位门量级普查时踩到一个足以让结论反转的坑：产品
`synthesize_patch` 的定位文本是 `f"{name} {call}"` 且**只取 F2P 判据**
（`aether/synthesize.py:359`）。第一版普查尺子用「全部判据 + 无空格拼接」
另写了一份 texts ⇒ 给 `93185b2` 算出 `focus=['daterange']`、
`d992be0` 算出 `['Stats','running_statistics']`，而**产品实际两者都是
`[]`（S1_no_focus）**。照那版出读数，会把「定位没开工」写成「定位射程外」。

⇒ 三条硬约束：
   A. 普查必须走产品自己的 `synthesize_patch` 取 `focus`（替身只换候选枚举）；
   B. `no_f2p` 与 `f2p_but_no_focus` **必须分开报**（前者该修判据、后者才该修定位）；
   C. 每次普查先验基线锚（174），不健康 ⇒ 拒绝出读数。

⛔⛔ B 组反证全是**离线合成**的，不依赖 /tmp/realrepo：B0 证明替身不改 focus，
   B1 复现「旧尺子」并证明它给出不同答案，B4 逐格证明判定不是死代码，
   B6 用行为（而非源码文案）证明默认路径真的调了验锚。

用法：python tests/contract_focus_census_test.py
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether import synthesize as S              # noqa: E402
from probe_focus_reach import changed_units      # noqa: E402
import probe_focus_census as pfc                 # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "focus_census.json"

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
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


# ══════════════ 合成源码对（全离线、秒级）══════════════
BUGGY = ("def target(x):\n"
         "    return x < 3\n"
         "\n"
         "def other(x):\n"
         "    return x * 2\n")
FIXED = ("def target(x):\n"
         "    return x <= 3\n"
         "\n"
         "def other(x):\n"
         "    return x * 2\n")
#: 顶层 def 藏在 `if` 里 ⇒ `top_defs` 看不见，但沙箱里名字仍可调用
NESTED = ("if True:\n"
          "    def target(x):\n"
          "        return x < 3\n")
INSERT_ONLY = BUGGY + "\ndef extra(y):\n    return y + 1\n"

T_F2P = [{"name": "t1", "call": "target(3)", "expect": True}]
T_PASS = [{"name": "t1", "call": "target(1)", "expect": True}]


# ══════════════ A 组：读数存档自洽 ══════════════
def group_a():
    print("\n【A】定位门普查读数存档")
    if not READING.is_file():
        skip("A 全组", "读数存档不在仓库（未覆盖 ≠ 通过）")
        return None
    try:
        d = json.loads(READING.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        ck("A0 存档可解析", False, str(e))
        return None
    rows = d.get("rows", [])
    ck("A1 schema 正确", d.get("schema") == "focus-census/v1",
       str(d.get("schema")))
    ck("A2 逐行 verdict ∈ 封闭集合",
       bool(rows) and all(r.get("verdict") in pfc.VERDICTS for r in rows),
       "n=%d" % len(rows))
    ck("A3 两本账：120 个候选 / 119 个 census 读数",
       len(rows) == 120 and sum(1 for r in rows if r.get("in_census")) == 119,
       "rows=%d in_census=%d" % (len(rows),
                                 sum(1 for r in rows if r.get("in_census"))))
    calc = collections.Counter(r["verdict"] for r in rows)
    ck("A4 summary.all 与逐行重算一致（不是手写分布）",
       d.get("summary", {}).get("all") ==
       {v: calc.get(v, 0) for v in pfc.VERDICTS})
    ck("A5 focus 非空的行才有 in_reach=True",
       all(not r.get("in_reach") or r.get("focus") for r in rows))
    ck("A6 no_f2p 与 f2p_but_no_focus 分开成列",
       {"no_f2p", "f2p_but_no_focus"} <=
       set(d.get("summary", {}).get("all", {})))
    #: 173 的 D（oracle 判据下判成「射程外」）在全自动路径下是 no_f2p。
    by = {r["sha"]: r for r in rows}
    d_row = by.get("d992be0")
    ck("A7 173 的 D 在全自动路径下不是定位门（no_f2p）",
       bool(d_row) and d_row["verdict"] == "no_f2p",
       str(d_row and d_row["verdict"]))
    #: ⛔⛔ 这几个数就是 175 的结论本身，改一个字等于改结论。
    ck("A8 全量门分布（175 结论）",
       d.get("summary", {}).get("all") ==
       {"no_src": 0, "no_criteria": 30, "blew": 0, "no_f2p": 72,
        "f2p_but_no_focus": 2, "no_units": 2,
        "out_of_reach": 3, "in_reach": 11},
       str(d.get("summary", {}).get("all")))
    shape_ok = [r for r in rows if r.get("reach") in ("L2", "L3")]
    blocked = [r for r in shape_ok
               if r["verdict"] in ("out_of_reach", "f2p_but_no_focus",
                                   "no_units")]
    ck("A9 形态可达 9 个里定位侧零拦截（修 locate_focus 收益 0）",
       len(shape_ok) == 9 and not blocked,
       "shape=%d blocked=%s" % (len(shape_ok),
                                [r["sha"] for r in blocked]))
    ck("A10 判据侧 102/120（no_criteria + no_f2p）",
       sum(1 for r in rows
           if r["verdict"] in ("no_criteria", "no_f2p")) == 102)

    fg = ROOT / "scripts" / "focus_forced_gain.json"
    if not fg.is_file():
        skip("A11/A12 强制 focus 对照", "对照读数不在仓库")
        return d
    fr = {r["sha"]: r for r in
          json.loads(fg.read_text(encoding="utf-8"))["rows"]}
    out3 = ("ead236e", "4aa77cd", "ae37eb3")
    ck("A11 3 个射程外强制 focus 后真修复 0（2 no_fix + 1 elsewhere）",
       all(fr.get(s, {}).get("stage") == "measured" for s in out3)
       and sorted(fr[s]["forced_verdict"] for s in out3)
       == ["elsewhere", "no_fix", "no_fix"],
       str({s: fr.get(s, {}).get("forced_verdict") for s in out3}))
    ck("A12 两个 L3 正例仍修好（装置没把所有题判死）",
       fr.get("c1c25da", {}).get("forced_verdict") == "exact_file"
       and fr.get("766b554", {}).get("forced_verdict")
       == "same_core_fix")
    ck("A13 强制 focus 读数全部标注诊断用途",
       all(r.get("diagnostic_only") is True for r in fr.values()))
    return d


# ══════════════ B 组：注入反证 ══════════════
def group_b():
    print("\n【B】注入反证：同源、七格有牙、验锚必经")

    #: B0 先证明**替身不改 focus**（否则整份普查读数作废）。
    orig_cands = S.synth_candidates
    r_normal = S.synthesize_patch(BUGGY, T_F2P, timeout=5)
    with pfc.no_candidates():
        r_stub = S.synthesize_patch(BUGGY, T_F2P, timeout=5)
    ck("B0 替身版与正常版 focus 逐字相同（且非空）",
       r_normal.focus == r_stub.focus == ("target",),
       "normal=%s stub=%s" % (r_normal.focus, r_stub.focus))
    ck("B0b 替身退出后恢复原函数（不污染后续调用）",
       S.synth_candidates is orig_cands)

    #: B1 复现本轮踩的坑：另写一份 texts 会给出**不同**答案。
    old_texts = [t["name"] + str(t.get("call", "")) for t in T_F2P]
    old_focus = S.locate_focus(BUGGY, old_texts)
    prod_focus = S.locate_focus(
        BUGGY, ["%s %s" % (t["name"], t["call"]) for t in T_F2P])
    ck("B1 旧尺子（无空格拼接）算出空 focus，产品尺子算出 target",
       old_focus == [] and prod_focus == ["target"],
       "old=%s prod=%s" % (old_focus, prod_focus))

    ck("B2 locate_focus 有牙（换 texts 会翻转）",
       S.locate_focus(BUGGY, ["t1 target(3)"]) == ["target"]
       and S.locate_focus(BUGGY, ["t1 unrelated(3)"]) == [])

    ck("B3 changed_units 有牙（纯 INSERT ⇒ 空 ⇒ no_units 格不是死的）",
       changed_units(BUGGY, FIXED) == ["target"]
       and changed_units(BUGGY, INSERT_ONLY) == [])

    #: B4 逐格反证：同一份产品结果，换输入必须落到不同格。
    r_pass = S.synthesize_patch(BUGGY, T_PASS, timeout=5)
    r_nested = S.synthesize_patch(NESTED, T_F2P, timeout=5)
    r_blew = SimpleNamespace(n_blew=1, base_ok=-1, focus=())
    cases = (
        ("in_reach", pfc.classify(r_stub, ["target"], 1)[0]),
        ("out_of_reach", pfc.classify(r_stub, ["other"], 1)[0]),
        ("no_units", pfc.classify(r_stub, [], 1)[0]),
        ("no_f2p", pfc.classify(r_pass, ["target"], 1)[0]),
        ("f2p_but_no_focus", pfc.classify(r_nested, [], 1)[0]),
        ("blew", pfc.classify(r_blew, ["target"], 1)[0]),
    )
    for want, got in cases:
        ck("B4 %-18s 格可触发" % want, got == want, "实得 %s" % got)
    ck("B4b no_f2p 与 f2p_but_no_focus 确实不同格",
       pfc.classify(r_pass, ["target"], 1)[0]
       != pfc.classify(r_nested, [], 1)[0])

    #: B5 汇总拆分有牙：换一行 verdict 必须改读数。
    rows = [{"repo": "x", "reach": None, "in_census": True,
             "verdict": "in_reach"},
            {"repo": "x", "reach": "L3", "in_census": True,
             "verdict": "out_of_reach"}]
    s = pfc.summarize(rows)
    ck("B5 out_of_reach_ratio 会随数据变（不是常量）",
       s["determinable"] == 2 and s["out_of_reach_ratio"] == 0.5,
       str(s["out_of_reach_ratio"]))
    s2 = pfc.summarize([dict(rows[0], verdict="no_f2p"), rows[1]])
    ck("B5b 改成 no_f2p 后可判定数下降",
       s2["determinable"] == 1 and s2["out_of_reach_ratio"] == 1.0)

    #: B6 用**行为**证明默认路径真的调了验锚（不断言源码文案）。
    class Sentinel(Exception):
        pass

    orig_gate = pfc.require_baseline
    orig_argv = sys.argv
    hit = []

    def boom(*a, **k):
        hit.append(1)
        raise Sentinel()

    pfc.require_baseline = boom
    sys.argv = ["probe_focus_census.py", "--only", "no_such_sha"]
    try:
        pfc.main()
    except Sentinel:
        pass
    finally:
        pfc.require_baseline = orig_gate
        sys.argv = orig_argv
    ck("B6 默认路径必经 require_baseline（174 的普查接线）",
       hit == [1], "called=%s" % hit)


# ══════════════ C 组：真实基线本机覆盖 ══════════════
def group_c():
    print("\n【C】真实基线（本机；CI 无基线时单列未覆盖）")
    if not all((Path("/tmp/realrepo") / n / ".git" / "HEAD").is_file()
               for n in ("boltons", "more-itertools", "attrs")):
        skip("C1 三仓库可读", "取证基线不在本机（未覆盖 ≠ 通过）")
        return
    from realrepo_baseline import verify, load_baseline  # noqa: E402
    rep = verify(load_baseline())
    ck("C1 基线锚健康（普查读数的前提）", rep["ok"], str(rep["diffs"])[:200])


def main() -> int:
    group_a()
    group_b()
    group_c()
    print("\npass=%d  fail=%d  skip=%d" % (len(PASSES), len(FAILS),
                                            len(SKIPS)))
    if SKIPS:
        print("  ⛔ 跳过项（未覆盖 ≠ 通过）：" + ", ".join(SKIPS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

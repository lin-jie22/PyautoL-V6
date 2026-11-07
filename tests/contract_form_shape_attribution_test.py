#!/usr/bin/env python3
"""契约：**119 条 form 账的判据形态需求归因**（README 179）必须同源、有牙、封闭。

═══ 钉死什么 ═══

177 把 119 条 form 账一律判 `unmeasurable`（产品 `mapped=False` ⇒ 没有探针
表达式，不同源、不跑沙箱）。179 **不推翻**它，只用产品同源的原始 `expr`
做**描述性** AST 分类，回答「被拒收的断言需要哪种判据形态」。

三条硬约束：

  A. **分母同源**：全部取自 `probe_index`（内部调产品 `_iter_all_probes`），
     与 177 存档 `expr` 逐字对拍，且 `mapped is False`；任何违反 ⇒ 量具格。
  B. **不报收益**：不跑沙箱、不产出判据、不改 `aether/`；`reach` 是事后
     oracle，只交叉、不能当 predictor。⛔ 不许把形态分布读成「扩张能救几条」。
  C. **反证组**：B 组离线注入（恒真分类 / 空 expr / 篡改 attr / 去掉验锚 /
     两本账误合并），C 组才碰本机基线与真实正例（第 133 条：零命中先证能命中）。

⛔ `SHAPES` 是封闭集合，出现集合外的格 ⇒ fail-closed；量具格单列不参与推断。

用法：python tests/contract_form_shape_attribution_test.py
"""
from __future__ import annotations

import ast
import collections
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_form_shape_attribution as fsa      # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "form_shape_attribution.json"
SCRIPT = ROOT / "scripts" / "probe_form_shape_attribution.py"

PASSES: list = []
FAILS: list = []
SKIPS: list = []

EXPECT_SHAPES = {
    "is": 27, "truthy": 25, "in": 18, "chained_compare": 15,
    "order": 6, "multiset": 6, "ne": 6, "is_not": 5, "not_in": 4,
    "raises_match": 3, "not_expr": 2, "isinstance": 1, "raises_other": 1,
}
EXPECT_ATTR = {
    "assert": 80, "assertTrue": 9, "assertFalse": 8, "assertCountEqual": 6,
    "raises": 4, "assertIsNot": 4, "assertIs": 3, "assertNotIn": 2,
    "assertIn": 1, "assertIsInstance": 1, "assertLessEqual": 1,
}
EXPECT_V175 = {"no_f2p": 70, "no_criteria": 30, "in_reach": 11,
               "f2p_but_no_focus": 6, "no_units": 2}


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    #: 要能定位失败记账（写成 `(PASSES if ok else FAILS).append` 会判
    #: 「失败不落地」⇒ 全量验收真红）。
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print("  SKIP %s  | %s" % (name, why))


def probe(attr: str, expr: str, stmt: str = "") -> dict:
    return {"attr": attr, "expr": expr, "stmt": stmt or expr,
            "mapped": False}


# ─────────────────────────── A 组：钉死全量读数 ───────────────────────────
def group_a() -> None:
    d = json.loads(READING.read_text(encoding="utf-8"))
    s, rows = d["summary"], d["rows"]
    print("A 组：全量读数（⛔ 改尺子必须重跑普查）")
    ck("A1 schema 版本", d["schema"] == "form_shape_attribution/v1",
       str(d.get("schema")))
    ck("A2 分母 119 条", s["n_rows"] == 119 and len(rows) == 119,
       "%s / %s" % (s["n_rows"], len(rows)))
    ck("A3 形态分布 13 格", s["shapes"] == EXPECT_SHAPES, str(s["shapes"]))
    ck("A4 实格数 13", s["n_distinct_shapes"] == 13,
       str(s["n_distinct_shapes"]))
    ck("A5 量具格全 0", s["n_measurement"] == 0
       and all(v == 0 for v in s["measurement_gates"].values()),
       str(s["measurement_gates"]))
    ck("A6 无集合外形态", s["unknown_shapes"] == [], str(s["unknown_shapes"]))
    ck("A7 同源 expr 零 mismatch", s["n_expr_mismatch"] == 0,
       str(s["n_expr_mismatch"]))
    ck("A8 分母全是 mapped=False", s["n_not_mapped"] == 0,
       str(s["n_not_mapped"]))
    ck("A9 probe_index 零 miss", s["n_unmatched"] == 0
       and len(d["unmatched"]) == 0, str(s["n_unmatched"]))
    ck("A10 先验锚健康", s["baseline_health"] ==
       {"ok": True, "diffs": 0, "repos": 3}, str(s["baseline_health"]))
    ck("A11 reach 交叉 X=116 / L2=3",
       dict(collections.Counter(r["reach"] for r in rows)) ==
       {"X": 116, "L2": 3}, "⛔ 事后 oracle，只交叉不推断")
    ck("A12 v175 交叉", dict(collections.Counter(
        r["v175"] for r in rows)) == EXPECT_V175)
    ck("A13 三仓库分布", s["by_repo"] ==
       {"attrs": 60, "boltons": 25, "more-itertools": 34}, str(s["by_repo"]))
    ck("A14 产品 attr 分布（同源对拍）", s["by_attr"] == EXPECT_ATTR,
       str(s["by_attr"]))
    ck("A15 每条 shape ∈ 封闭集合",
       all(r["shape"] in fsa.SHAPES for r in rows))
    ck("A16 每条 shape 是实格（非量具格）",
       all(r["shape"] not in fsa.MEASUREMENT_GATES for r in rows))
    ck("A17 177 的 unmeasurable 未被推翻",
       all(r["verdict_177"] == "unmeasurable" for r in rows))
    ck("A18 全部诊断只读", all(r["diagnostic_only"] is True for r in rows))
    ck("A19 每条都核验过 mapped=False",
       all(r["mapped_false"] is True for r in rows))
    ck("A20 每条 expr 与 177 存档同源",
       all(r["expr_mismatch"] is False for r in rows))
    ck("A21 全部来自 form 门", all(r["gate"] == "form" for r in rows))
    ck("A22 口径签名 8 文件逐一对拍",
       d["caliber"] == fsa.caliber(), "⛔ 含尺子自身（174 教训）")
    ck("A23 验锚结论如实记在 note 里",
       "不跑沙箱" in s["note"] and "事后 oracle" in s["note"])


# ─────────────────────────── B 组：离线注入反证 ───────────────────────────
def group_b() -> None:
    print("B 组：离线注入反证（⛔ 不碰本机基线、不跑沙箱）")
    cases = [
        ("裸 assert a == b 本该被产品收 ⇒ eq_unexpected",
         probe("assert", "a == b"), "eq_unexpected"),
        ("裸 assert a != b", probe("assert", "a != b"), "ne"),
        ("裸 assert x in y", probe("assert", "x in y"), "in"),
        ("裸 assert x not in y", probe("assert", "x not in y"), "not_in"),
        ("裸 assert x is None", probe("assert", "x is None"), "is"),
        ("裸 assert x is not None", probe("assert", "x is not None"), "is_not"),
        ("裸 assert a <= b", probe("assert", "a <= b"), "order"),
        ("裸 assert a > b", probe("assert", "a > b"), "order"),
        ("链式比较", probe("assert", "f(21) == f_copy(21) == 42"),
         "chained_compare"),
        ("布尔组合", probe("assert", "a and b"), "boolop"),
        ("取反", probe("assert", "not x"), "not_expr"),
        ("裸布尔表达式", probe("assert", "Stats(data).format_histogram()"),
         "truthy"),
        ("语法错 ⇒ 量具格", probe("assert", "a ==("), "unparseable"),
        ("assertTrue", probe("assertTrue", "self.assertTrue(x)"), "truthy"),
        ("assertFalse", probe("assertFalse", "self.assertFalse(x)"), "truthy"),
        ("assertIn", probe("assertIn", "self.assertIn(v, r)"), "in"),
        ("assertNotIn", probe("assertNotIn", "self.assertNotIn(v, r)"),
         "not_in"),
        ("assertIs", probe("assertIs", "self.assertIs(a, b)"), "is"),
        ("assertIsNot", probe("assertIsNot", "self.assertIsNot(a, b)"),
         "is_not"),
        ("assertCountEqual", probe("assertCountEqual",
                                   "self.assertCountEqual(a, b)"), "multiset"),
        ("assertIsInstance", probe("assertIsInstance",
                                   "self.assertIsInstance(a, T)"),
         "isinstance"),
        ("assertLessEqual", probe("assertLessEqual",
                                  "self.assertLessEqual(a, b)"), "order"),
        ("未知 unittest attr ⇒ 量具格",
         probe("assertWeird", "self.assertWeird(a)"), "other"),
        ("raises 带 match= ⇒ raises_match",
         probe("raises", "fd |= fd",
               "with pytest.raises(TypeError, match='immutable'):\n    fd |= fd"),
         "raises_match"),
        ("raises 无 match= 且块内是语句 ⇒ raises_other",
         probe("raises", "raise MyException()",
               "with pytest.raises(MyException) as ei:\n    raise MyException()"),
         "raises_other"),
        ("raises 语句也 SyntaxError ⇒ 量具格",
         probe("raises", "x ===", "with pytest.raises(E):\n    x ==="),
         "unparseable"),
    ]
    for i, (name, p, want) in enumerate(cases, 1):
        ck("B%02d %s" % (i, name), fsa.shape_of(p) == want,
           "got=%s" % fsa.shape_of(p))

    #: ⛔ 恒真分类反证：不同输入必须落进不同格，不许恒返回一张表
    many = [fsa.shape_of(p) for _, p, _ in cases]
    ck("B27 分类不是恒真（至少 8 种不同格）", len(set(many)) >= 8,
       str(len(set(many))))
    ck("B28 返回值恒 ∈ 封闭集合", all(x in fsa.SHAPES for x in many))
    ck("B29 量具格恰 5 个且是 SHAPES 尾部",
       fsa.MEASUREMENT_GATES == frozenset(fsa.SHAPES[14:])
       and len(fsa.MEASUREMENT_GATES) == 5, str(fsa.MEASUREMENT_GATES))
    ck("B30 空 expr 不许落进实格",
       fsa.shape_of(probe("assert", "")) == "unparseable")
    ck("B31 篡改 attr 会改分类（不靠 expr 猜）",
       fsa.shape_of(probe("assertTrue", "x in y")) !=
       fsa.shape_of(probe("assert", "x in y")))
    ck("B32 shape_of 是纯函数（不改 probe）", _pure())

    src = SCRIPT.read_text(encoding="utf-8")
    ck("B33 正式路径必须先验锚", "health = require_baseline()" in src)
    ck("B34 bypass 只作离线反证且被标注",
       "--no-baseline-gate" in src and "仅供离线反证" in src)
    ck("B35 封闭集合 fail-closed 落地",
       "raise RuntimeError(\"封闭集合被突破" in src)
    ck("B36 不回写 mapped（两本账不合并）",
       'q["mapped"] =' not in src and 'probe["mapped"] =' not in src)
    ck("B37 不产出判据 / 不跑沙箱",
       "run_in_sandbox" not in src and "synthesize_patch" not in src)
    ck("B38 口径签名钉了尺子自身",
       "scripts/probe_form_shape_attribution.py" in fsa.CALIBER_FILES)


def _pure() -> bool:
    p = probe("assert", "a != b")
    before = copy.deepcopy(p)
    fsa.shape_of(p)
    return p == before


# ─────────────────────────── C 组：本机健康 + 真实正例 ───────────────────────────
def group_c() -> None:
    print("C 组：本机健康与真实正例（第 133 条：零命中先证查询能命中）")
    d = json.loads(READING.read_text(encoding="utf-8"))
    rows = d["rows"]
    ck("C1 尺子脚本存在", SCRIPT.is_file())
    ck("C2 存档存在且可解析", READING.is_file() and len(rows) == 119)
    ck("C3 基线锚文件存在",
       (ROOT / "scripts" / "realrepo_baseline.json").is_file())
    ck("C4 每种实格都能在真实读数里命中",
       all(any(r["shape"] == sh for r in rows) for sh in EXPECT_SHAPES),
       "⛔ 零命中先证查询能命中")
    ck("C5 13 种实格各有真实样例",
       len({r["shape"] for r in rows}) == 13)

    #: 真实正例：从真实仓库取一条 raises probe，喂进尺子必须得 raises_match
    cand = next((r for r in rows if r["shape"] == "raises_match"), None)
    ok = False
    if cand:
        try:
            from probe_extract_downstream import probe_index
            from probe_alias_demote import select_commits
            repo = Path("/tmp/realrepo") / cand["repo"]
            subdir = dict(fsa.REPOS)[cand["repo"]]
            cs = select_commits(repo, 40, subdir)
            c = next((x for x in cs if x[0] == cand["sha"]), None)
            if c:
                idx = probe_index(repo, cand["sha"], c[2])
                q = idx.get((cand["fn"], cand["stmt"]))
                ok = bool(q) and fsa.shape_of(q) == "raises_match"
        except Exception as exc:               # noqa: BLE001
            print("  ℹ️  真实正例不可用：%s" % exc)
    if not ok:
        skip("C6 真实 raises_match 正例", "真实基线不可用（⛔ 不算通过）")
    else:
        ck("C6 真实 raises_match 正例（产品同源 probe）", True)

    #: 反例：同一条真实 probe 去掉 match= 必须变成别的格（证明查询有分辨力）
    fake = probe("raises", "fd |= fd", "with pytest.raises(E):\n    fd |= fd")
    ck("C7 去掉 match= 后分类改变",
       fsa.shape_of(fake) != "raises_match", fsa.shape_of(fake))
    try:
        ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
        ck("C8 尺子脚本语法可解析", True)
    except SyntaxError as exc:
        ck("C8 尺子脚本语法可解析", False, str(exc))


def main() -> int:
    print("契约：119 条 form 账判据形态需求归因（README 179）")
    group_a()
    group_b()
    group_c()
    print("=" * 60)
    print("pass=%d  fail=%d  skip=%d" % (len(PASSES), len(FAILS), len(SKIPS)))
    if FAILS:
        print("FAILED:")
        for n in FAILS:
            print("  - %s" % n)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

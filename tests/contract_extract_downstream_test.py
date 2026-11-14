#!/usr/bin/env python3
"""契约：**抽取层门下游上界普查**（README 177）的尺子必须同源、判定有牙。

═══ 钉死什么 ═══

176 留下 254 条 `downstream=unknown`（倒在抽取层门、下游从未测过），
并写明「没测过 ≠ 测出 0」。177 用同源 `run_all` 在 buggy/fixed 两侧
求值产品自己抽出的探针，只判一条**与判据形态无关的必要条件**：

    两侧同值 ⇒ 任何形态的判据都不可能区分两版 ⇒ 永远不可能 F2P

三条硬约束：

  A. **读数是上界不是收益**：`diff` 后面还压着三验 / 判据形态 /
     定位门 / 形态门 / 合成层。⛔ 不许把 diff 条数写成「能修好 N 个」。
  B. **两本账分开**：主账（产品已抽出 `call`）才真跑沙箱；form 门
     119 条产品**没有**探针表达式 ⇒ 一律 `unmeasurable`，⛔ 不拿
     未经加工的原始 `expr` 出读数（构造必然，第 122 条；175 第一版
     就是不同源尺子导致结论反转）。
  C. **反证组**：B0 先证明闸能绿、且**能读出 diff**（第 133 条：
     零命中先证查询能命中）；B1–B9 逐项注入事故确认按预期转红；
     B10 用行为证明默认路径真的调了验锚。

⛔⛔ B 组全部**离线合成**，不依赖 /tmp/realrepo；C 组才碰本机基线。

用法：python tests/contract_extract_downstream_test.py
"""
from __future__ import annotations

import collections
import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_extract_downstream as ped          # noqa: E402
import caliber_superseded as cs                  # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "extract_downstream.json"
SYNTH_READING = ROOT / "scripts" / "truthy_synth.json"

PASSES: list = []
FAILS: list = []
SKIPS: list = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    #: 要能定位失败记账（写成 `(PASSES if ok else FAILS).append` 会判
    #: 「失败不落地」⇒ 全量验收真红）。与其余契约同源。
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print("  SKIP %s  | %s" % (name, why))


def g(v) -> dict:
    return {"got": v, "passed": bool(v)}


# ─────────────────────── A 组：全量读数硬钉 ───────────────────────

def group_a():
    print("\n【A】全量读数（存档未生成 ⇒ SKIP，未覆盖 ≠ 通过）")
    if not READING.is_file():
        skip("A 组", "extract_downstream.json 未生成")
        return
    d = json.loads(READING.read_text(encoding="utf-8"))
    s = d["summary"]
    rows, form = d["rows"], d["form_diagnostic_rows"]
    ck("A1 schema 与本轮尺子一致", d.get("schema") == "extract_downstream/v1")
    #: ⛔ r110 / README 189：量具换口径，旧存档的 caliber 走 superseded 登记。
    #:    旧存档一个字节未改；只有「已登记且 old/new 双向逐字对上」的那一处
    #:    差异可豁免，其余差异照旧转红（契约①）。
    ck("A2 口径签名可读且逐字匹配磁盘（含 189 superseded 登记）",
       cs.caliber_matches_superseded(
           d["caliber"], ped.caliber(),
           "scripts/extract_downstream.json") == [],
       str(cs.caliber_matches_superseded(
           d["caliber"], ped.caliber(),
           "scripts/extract_downstream.json")))
    bad = dict(d["caliber"])
    bad["aether/extract.py"] = "X" * 64
    ck("A2b ⛔ superseded 闸有牙：改未登记键仍报差异",
       cs.caliber_matches_superseded(
           bad, ped.caliber(), "scripts/extract_downstream.json")
       == ["aether/extract.py"])
    bad2 = dict(d["caliber"])
    bad2["scripts/probe_context_gate.py"] = "Y" * 64
    ck("A2c ⛔ superseded 闸有牙：登记键旧值被改也报差异",
       cs.caliber_matches_superseded(
           bad2, ped.caliber(), "scripts/extract_downstream.json")
       == ["scripts/probe_context_gate.py"])
    ck("A3 主账 + form 账 = 176 的 254 条 unknown",
       len(rows) + len(form) == 254,
       "main=%d form=%d" % (len(rows), len(form)))
    ck("A4 主账 135 条（产品已抽出 call）",
       len(rows) == 135, str(len(rows)))
    ck("A5 form 账 119 条", len(form) == 119, str(len(form)))
    ck("A6 主账每行都有非空 call 且非 different_caliber",
       all(r.get("call") and not r["different_caliber"] for r in rows))
    ck("A7 form 账每行 call 为空、标 unmeasurable + 两本账标记",
       all(not r.get("call") and r["verdict"] == "unmeasurable"
           and r["different_caliber"] and r["diagnostic_only"]
           for r in form))
    ck("A8 ⛔ form 账没有被塞进沙箱读数（no got_fixed/got_buggy）",
       all("got_fixed" not in r and "got_buggy" not in r for r in form))
    ck("A9 判定封闭集合（不许新增格来迁就读数）",
       set(r["verdict"] for r in rows) <= set(ped.VERDICTS),
       str(set(r["verdict"] for r in rows)))
    ck("A10 主账逐格计数 == summary.verdicts（不藏进程内状态）",
       dict(collections.Counter(r["verdict"] for r in rows))
       == s["verdicts"], str(s["verdicts"]))
    ck("A11 主账按门计数 == summary.by_gate",
       dict(collections.Counter(r["gate"] for r in rows)) == s["by_gate"],
       str(s["by_gate"]))
    ck("A12 交叉表可复算",
       all(s["cross"].get("%s/%s" % (r["gate"], r["verdict"]), 0) >= 1
           for r in rows))
    ck("A13 diff_by_v175 / diff_by_reach 可复算",
       dict(collections.Counter(r.get("v175") for r in rows
                                if r["verdict"] == "diff"))
       == s["diff_by_v175"]
       and dict(collections.Counter(r.get("reach") for r in rows
                                    if r["verdict"] == "diff"))
       == s["diff_by_reach"])
    ck("A14 基线健康被当读数落盘且为真",
       s["baseline_health"]["ok"] is True
       and s["baseline_health"]["diffs"] == 0
       and s["baseline_health"]["repos"] == 3,
       str(s["baseline_health"]))
    ck("A15 0 跳过（单 commit 异常不连坐）",
       s["n_skipped"] == 0, str(s["skipped"])[:200])
    ck("A16 ⛔ 结论分寸写进存档（上界 / 保留未知 / 两本账）",
       "不是收益" in s["unknown_note"]
       and "不许合并" in s["two_books_note"])
    #: ⛔ 硬钉本轮的核心读数，防止以后有人改了尺子却沿用旧结论
    print("    [读数] verdicts=", s["verdicts"])
    print("    [读数] by_gate =", s["by_gate"])
    print("    [读数] diff_by_v175=", s["diff_by_v175"])
    print("    [读数] diff_by_reach=", s["diff_by_reach"])
    ck("A17 主账读数 == 本轮实测分布（改尺子必须重跑，不许沿用）",
       s["verdicts"] == {"no_result_both": 94, "same": 22,
                         "diff": 10, "import_failed": 9},
       str(s["verdicts"]))
    ck("A18 form 账全是 unmeasurable（构造必然不许出读数）",
       s["form_diagnostic_verdicts"] == {"unmeasurable": 119},
       str(s["form_diagnostic_verdicts"]))
    ck("A19 176 存档的 unknown 总数与分母一致",
       len([r for r in json.loads(
           (ROOT / "scripts" / "criteria_gate_census.json").read_text(
               encoding="utf-8"))["rows"]
            if r["downstream"] == "unknown"]) == 254)
    ck("A20 ⛔ 10 条 diff 全部 reach=X（形态门在下游等着）",
       s["diff_by_reach"] == {"X": 10}, str(s["diff_by_reach"]))
    ck("A21 22 条 same 是结构性排除，且真跑了沙箱",
       all(r.get("got_fixed") is not None
           and r.get("got_fixed") == r.get("got_buggy")
           for r in rows if r["verdict"] == "same"))
    ck("A22 94 条 no_result_both 保留未知（两侧 got 皆空）",
       all(r.get("got_fixed") is None and r.get("got_buggy") is None
           for r in rows if r["verdict"] == "no_result_both"))
    ck("A23 truthy 对照只从主账派生、且可从 passed 复算",
       s["n_truthy_f2p"] == sum(1 for r in rows if r["truthy_f2p"])
       and all(r["truthy_f2p"] == (bool(r["passed_fixed"])
                                   and not bool(r["passed_buggy"]))
               for r in rows))
    ck("A24 ⛔ truthy_f2p 是 diff 的子集（不是新分母）",
       all(r["verdict"] == "diff" for r in rows if r["truthy_f2p"]))
    ck("A25 解开 truthy 门后真 F2P 的落点已落盘",
       s["truthy_f2p_by_reach"] == dict(collections.Counter(
           r.get("reach") for r in rows if r["truthy_f2p"]))
       and s["truthy_f2p_shas"] == sorted({r["sha"] for r in rows
                                           if r["truthy_f2p"]}))
    group_a_synth(s)


def group_a_synth(s: dict):
    """⛔ 决定性对照：解开 truthy 门后，合成层真修复是不是 0。"""
    print("\n【A-synth】合成层决定性对照（诊断读数，不算修复）")
    if not SYNTH_READING.is_file():
        skip("A-synth 组", "truthy_synth.json 未生成")
        return
    d = json.loads(SYNTH_READING.read_text(encoding="utf-8"))
    ck("A26 schema 与 diagnostic_only 标记",
       d.get("schema") == "truthy_synth/v1"
       and d["summary"]["diagnostic_only"] is True)
    ck("A27 口径签名仍逐字匹配磁盘（含 189 superseded 登记）",
       cs.caliber_matches_superseded(
           d["caliber"], ped.caliber(),
           "scripts/truthy_synth.json") == [],
       str(cs.caliber_matches_superseded(
           d["caliber"], ped.caliber(),
           "scripts/truthy_synth.json")))
    ck("A28 4 个 commit 恰好覆盖 5 条 truthy F2P 判据",
       len(d["rows"]) == 4
       and sum(r["n_tests"] for r in d["rows"]) == 5
       and sorted({r["sha"] for r in d["rows"]}) == s["truthy_f2p_shas"],
       str([(r["sha"], r["n_tests"]) for r in d["rows"]]))
    ck("A29 ⛔⛔ 真修复 0（3 no_fix + 1 elsewhere），不许读成修好",
       d["summary"]["verdicts"] == {"no_fix": 3, "elsewhere": 1}
       and all(r["verdict"] not in ("exact_file", "same_core_fix")
               for r in d["rows"]),
       str(d["summary"]["verdicts"]))
    e = [r for r in d["rows"] if r["verdict"] == "elsewhere"]
    ck("A30 ⛔ full_pass=True 的那条是 elsewhere（161 口径）",
       len(e) == 1 and e[0]["sha"] == "06f3181"
       and e[0]["gate"] == "S5_full" and e[0]["full_pass"] is True
       and e[0]["n_machine_changes"] > 0
       and e[0]["n_machine_changes"] <= e[0]["n_human_changes"],
       str(e))
    ck("A31 3 条 no_fix 都是 S3_no_gain（形态门吞掉收益）",
       all(r["gate"] == "S3_no_gain" and not r["found"]
           for r in d["rows"] if r["verdict"] == "no_fix"))
    ck("A32 每行都真跑了候选（cands>0 且 ran>0）⇒ 不是空跑",
       all(r["n_cands"] > 0 and r["n_ran"] > 0 for r in d["rows"]))


# ─────────────────────── B 组：离线注入反证 ───────────────────────

def group_b():
    print("\n【B】离线注入反证（不依赖 /tmp/realrepo）")
    c = ped.classify
    #: B0 先证明闸能绿，且**能读出 diff**（第 133 条）
    ck("B0 正例：已知 F2P 探针两侧异值 ⇒ diff",
       c(g("True"), g("True"), g("False")) == "diff")
    ck("B0b 正例2：fixed 求不出、buggy 有值 ⇒ diff",
       c(g(None), g(None), g("Bits('100')")) == "diff")
    ck("B0c 正例3：恒真常量两侧同值 ⇒ same（结构性排除）",
       c(g("2"), g("2"), g("2")) == "same")
    ck("B0d 正例4：两侧都抛异常 ⇒ 保留未知，不读成排除",
       c(g(None), g(None), g(None)) == "no_result_both")
    ck("B0e 正例5：fixed 自身两次不一致 ⇒ unstable",
       c(g("1"), g("2"), g("1")) == "unstable")
    #: B1–B4 逐项注入事故
    ck("B1 把 fixed 当 buggy（两侧同值）后 diff 归零",
       c(g("True"), g("True"), g("True")) == "same")
    ck("B2 repr 地址噪声 ⇒ 假 diff 被剔除（不是 diff）",
       c(g("<X at 0x10ab>"), g("<X at 0x10ab>"),
         g("<X at 0x20cd>")) == "unstable")
    ck("B3 addr_noise 只认地址，不误伤普通字符串",
       not ped.addr_noise("0x123", "'abc'", None)
       and ped.addr_noise("object at 0x10abcd"))
    ck("B4 删掉稳定性自检会漏：第二次不一致仍必须 unstable",
       c(g("1"), g("9"), g("2")) == "unstable")
    #: B5 判定顺序不许改：把双空提前/延后会翻转结论
    ck("B5 双空 + 一侧地址噪声 ⇒ 仍按噪声优先 unstable",
       c(g(None), g(None), g("<X at 0x10ab>")) == "unstable")
    ck("B6 ⛔ 双空不许被判成 same（缺失不得读作否定）",
       c(g(None), g(None), g(None)) != "same")
    #: B7 口径签名：缺键 / 篡改都必须点名
    disk = {"a.py": "A", "b.py": "B"}
    ck("B7 口径签名一致时差异为空",
       ped.caliber_matches({"a.py": "A", "b.py": "B"}, disk) == [])
    ck("B7b 篡改一个 hash ⇒ 点名该文件",
       ped.caliber_matches({"a.py": "X", "b.py": "B"}, disk) == ["a.py"])
    ck("B7c 缺键也算差异（不许默认值兜底）",
       ped.caliber_matches({"a.py": "A"}, disk) == ["b.py"])
    #: B8 summarize 纯函数：不藏进程内状态，两本账分开
    fake_main = [{"repo": "boltons", "sha": "aaa", "gate": "tuple",
                  "verdict": "diff", "v175": "in_reach", "reach": "L3"},
                 {"repo": "attrs", "sha": "bbb", "gate": "unserializable",
                  "verdict": "same", "v175": "no_f2p", "reach": "X"}]
    fake_form = [{"verdict": "unmeasurable"} for _ in range(3)]
    s = ped.summarize(fake_main, fake_form,
                      {"ok": True, "diffs": [], "repos": {1: 1, 2: 2, 3: 3}},
                      [])
    ck("B8 summarize 主账/form 账分开计数",
       s["n_main"] == 2 and s["n_form_diagnostic"] == 3)
    ck("B8b summarize 的 verdicts 可从 rows 复算",
       s["verdicts"] == {"diff": 1, "same": 1})
    ck("B8c summarize 的 diff 交叉可从 rows 复算",
       s["diff_by_v175"] == {"in_reach": 1}
       and s["diff_by_reach"] == {"L3": 1})
    ck("B8d summarize 不许把 form 混进主账分布",
       "unmeasurable" not in s["verdicts"]
       and s["form_diagnostic_verdicts"] == {"unmeasurable": 3})
    #: B9 判定封装：封闭集合 + docstring 分寸（行为，不是文案断言）
    ck("B9 VERDICTS 封闭且不含 form 专用格",
       set(ped.VERDICTS) == {"same", "diff", "no_result_both",
                             "unstable", "import_failed"}
       and "unmeasurable" not in ped.VERDICTS)
    ck("B9b MAIN_GATES 只含产品已抽出 call 的四门",
       set(ped.MAIN_GATES) == {"unserializable", "tuple",
                               "unstable", "unmatched"}
       and "form" not in ped.MAIN_GATES)
    #: B9c–B9f：truthy 对照的离线反证（不许用 got 推 bool）
    t = ped.truthy_f2p
    ck("B9c truthy 正例：fixed 真 / buggy 假 ⇒ 真 F2P",
       t({"passed": True}, {"passed": False}))
    ck("B9d truthy 反例：两侧都真 ⇒ 不是 F2P",
       not t({"passed": True}, {"passed": True}))
    ck("B9e ⛔ 不许拿 got repr 推 bool（'None' 恒真）",
       not t({"passed": False}, {"passed": False})
       and t({"got": "None", "passed": True},
             {"got": "None", "passed": False}))
    ck("B9f 缺结果一律不算 F2P（fail-closed）",
       not t(None, {"passed": False})
       and not t({"passed": True}, None))
    #: B10 默认路径必经验锚（行为反证，不断言源码文案）
    class Sentinel(Exception):
        pass

    orig_gate, orig_argv = ped.require_baseline, sys.argv
    hits: list = []
    peds_run: list = []

    def boom():
        hits.append(1)
        raise Sentinel()

    def no_candidates(_repo, _limit, _subdir):
        return []

    orig_select = ped.select_commits
    ped.require_baseline = boom
    ped.select_commits = no_candidates
    try:
        sys.argv = ["probe_extract_downstream.py", "--only", "no_such_sha"]
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                ped.main()
            except Sentinel:
                peds_run.append("blocked")
        hits.clear()
        sys.argv = ["probe_extract_downstream.py", "--only", "no_such_sha",
                    "--no-baseline-gate"]
        with contextlib.redirect_stdout(io.StringIO()):
            ped.main()
    finally:
        ped.require_baseline, ped.select_commits = orig_gate, orig_select
        sys.argv = orig_argv
    ck("B10 默认路径调验锚、--no-baseline-gate 绕过（行为）",
       peds_run == ["blocked"] and hits == [],
       "blocked=%s hits=%s" % (peds_run, hits))


# ─────────────────────── C 组：本机基线 ───────────────────────

def group_c():
    print("\n【C】真实基线（本机；CI 无基线时单列未覆盖）")
    if not all((Path("/tmp/realrepo") / n / ".git" / "HEAD").is_file()
               for n in ("boltons", "more-itertools", "attrs")):
        skip("C 组", "取证基线不在本机（未覆盖 ≠ 通过）")
        return
    from realrepo_baseline import verify, load_baseline  # noqa: E402
    rep = verify(load_baseline())
    ck("C1 基线锚健康（普查读数的前提）", rep["ok"],
       str(rep["diffs"])[:200])
    ck("C2 176 存档仍是本轮分母（459 行 / 120 候选）",
       len(json.loads((ROOT / "scripts" /
                       "criteria_gate_census.json").read_text(
                           encoding="utf-8"))["rows"]) == 459)
    ck("C3 175 八格存档仍是 120 行",
       len(json.loads((ROOT / "scripts" /
                       "focus_census.json").read_text(
                           encoding="utf-8"))["rows"]) == 120)
    #: C4 真实沙箱正例：已知 F2P 探针在同源 run_all 下必须判 diff
    #: （⛔ 证明「主账零 diff」不是量具坏，第 133 条）
    try:
        from probe_alias_demote import select_commits, run_all, _git
        p = Path("/tmp/realrepo") / "boltons"
        cand = next((x for x in select_commits(p, 40, "boltons/")
                     if x[0] == "5eb6cc0"), None)
        if cand is None:
            skip("C4 已知 F2P 正例", "5eb6cc0 不在候选池")
            return
        sha, src = cand[0], cand[1]
        fx = _git(p, "show", "%s:%s" % (sha, src))
        bg = _git(p, "show", "%s^:%s" % (sha, src))
        items = [("k0", "Bits('10')[-2]", None),
                 ("k1", "1+1", None)]
        a1 = run_all(fx, items, timeout=20)
        a2 = run_all(fx, items, timeout=20)
        b = run_all(bg, items, timeout=20)
        v0 = ped.classify(a1.get("k0"), a2.get("k0"), b.get("k0"))
        v1 = ped.classify(a1.get("k1"), a2.get("k1"), b.get("k1"))
        ck("C4 真实沙箱正例：已知 F2P 判 diff、恒真判 same",
           v0 == "diff" and v1 == "same",
           "F2P=%s const=%s" % (v0, v1))
    except Exception as exc:
        skip("C4 已知 F2P 正例", "%s: %s" % (type(exc).__name__, exc))


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

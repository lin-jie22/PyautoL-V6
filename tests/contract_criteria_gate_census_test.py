#!/usr/bin/env python3
"""契约：**判据侧门量级普查**（README 176）的尺子必须同源、判定有牙。

═══ 钉死什么 ═══

176 复用 166 的 `attribute_commit`（⛔ 不另写判据 / 三验 / 新增断言集合），
把 120 个候选的 PR 新增断言逐条落到恰好一格门，并与 175 的八格交叉。
三条硬约束：

  A. **口径签名可读不可写**：存档里 5 个相关源码的 sha256 必须与磁盘逐字
     相同。174 的锚只钉了 `select_commits`（候选零点），⛔ 没钉抽取器 ⇒
     166 的 `F2P 22/53` 与当前 `10/54` 就是这么悄悄错开的。
  B. **不许把「倒在抽取层门」读成「被拦下的 F2P」**：这些行一律
     `downstream=unknown`（166 实测解开 assertRaises-lambda 一格后
     10 条全落 ctx、F2P 仍 0）。
  C. **同源恒等式**：175 判 `no_f2p` 的 commit，其 PR 新增断言里
     `F2P` 必须为 0 —— 若不为 0，说明两把尺子的三验口径漂移了。

⛔⛔ B 组反证全是**离线合成**的，不依赖 /tmp/realrepo：B0 先证明闸能绿，
   B1–B7 逐项注入事故确认按预期转红，B8 用行为（而非源码文案）证明
   默认路径真的调了验锚。

用法：python tests/contract_criteria_gate_census_test.py
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

import probe_criteria_gate_census as pgc          # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "criteria_gate_census.json"

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


def fake_row(gate: str = "form", v175: str = "no_criteria",
             repo: str = "boltons", attr: str = "assertNotIn",
             verdict: str = None) -> dict:
    return {"repo": repo, "sha": "deadbeef", "gate": gate,
            "attr": attr, "kind": "expect", "sandbox_verdict": verdict,
            "fn": "test_x", "call": "x()", "stmt": "assert x not in y",
            "v175": v175, "reach": "X", "in_census": True,
            "downstream": pgc.downstream_of(gate)}


def fake_commit(n_unresolved: int = 3, n_unjsonable: int = 0) -> dict:
    return {"repo": "boltons", "sha": "deadbeef", "src": "boltons/x.py",
            "n_new": 2, "n_tests_total": 4,
            "stats": {"n_gold_unresolved": n_unresolved,
                      "n_gold_unjsonable": n_unjsonable,
                      "n_gold_resolved": 5, "n_gold_swapped": 1,
                      "n_raw": 9, "n_out": 5},
            "extract_stats": {"n_gold_unresolved": n_unresolved,
                              "n_unserializable": n_unjsonable},
            "expect_full": {"n_files": 1,
                            "n_gold_unresolved": n_unresolved,
                            "n_gold_unjsonable": n_unjsonable,
                            "n_gold_swapped": 1, "n_raw": 9,
                            "n_out": 5, "n_gold_resolved": 5},
            "gates": {"form": 1, "ctx": 1}, "v175": "no_criteria",
            "reach": "X", "in_census": True, "census_stage": "no_criteria"}


# ══════════════ A 组：全量存档钉死 ══════════════
def group_a():
    print("\n【A】全量读数存档（120 候选）")
    if not READING.is_file():
        skip("A 组", "criteria_gate_census.json 未生成（未覆盖 ≠ 通过）")
        return
    d = json.loads(READING.read_text(encoding="utf-8"))
    rows, commits = d["rows"], d["per_commit"]
    s = d["summary"]

    ck("A1 schema 固定", d["schema"] == "criteria-gate-census/v1",
       str(d["schema"]))
    ck("A2 口径签名与磁盘逐字一致（5 个源码）",
       pgc.caliber_matches(d["caliber"], pgc.caliber()) == [],
       str(pgc.caliber_matches(d["caliber"], pgc.caliber())))
    ck("A2b caliber 覆盖全部登记文件",
       set(d["caliber"]) == set(pgc.CALIBER_FILES))
    ck("A3 rows / per_commit 与 summary 自洽",
       len(rows) == s["n_rows"] and len(commits) == s["n_commits"],
       "%d/%d vs %d/%d" % (len(rows), len(commits),
                           s["n_rows"], s["n_commits"]))

    allowed = set(list(pgc.ALL_GATES) + ["unmatched"])
    bad = [r for r in rows if r["gate"] not in allowed]
    ck("A4 每条新增断言恰好落在已知门", not bad,
       str(collections.Counter(r["gate"] for r in bad))[:160])
    bad2 = [r for r in rows
            if r["downstream"] != pgc.downstream_of(r["gate"])]
    ck("A4b 每行 downstream 与门类别严格一致", not bad2, str(len(bad2)))

    ck("A5 交叉表可由纯函数复算", pgc.cross_tab(rows) == s["cross"])
    total = sum(sum(v.values()) for v in s["cross"].values())
    ck("A5b 交叉表守恒（Σ = n_rows）", total == s["n_rows"],
       "%d vs %d" % (total, s["n_rows"]))

    tb = s["two_books"]
    ck("A6 两本账：120 候选 / 119 census 读数",
       tb["n_candidates"] == 120 and tb["n_census_readings"] == 119,
       str(tb))
    ck("A6b 9f4f31f 单列为 in_census=False",
       ["more-itertools", "9f4f31f"] in tb["not_in_census"],
       str(tb["not_in_census"]))
    ck("A7 普查时基线健康（174 的接线）",
       s["baseline_health"]["ok"] is True
       and s["baseline_health"]["diffs"] == 0, str(s["baseline_health"]))

    ck("A8 同源恒等式：no_f2p 的 commit 里 PR 新增断言 F2P=0",
       s["cross"]["no_f2p"].get("F2P", 0) == 0,
       str(s["cross"]["no_f2p"]))
    ck("A8b no_criteria 的 commit 里 PR 新增断言 F2P=0",
       s["cross"]["no_criteria"].get("F2P", 0) == 0,
       str(s["cross"]["no_criteria"]))
    ck("A8c 判据侧两格都有实测行数（不是空集合）",
       sum(s["cross"]["no_criteria"].values()) > 0
       and sum(s["cross"]["no_f2p"].values()) > 0)

    ds = s["downstream"]
    ck("A9 抽取层门确实标了 unknown（⛔ 不许被读成 F2P）",
       ds.get("unknown", 0) > 0 and ds.get("measured", 0) > 0,
       str(ds))
    ck("A9b 分寸声明随存档落盘", "不得读作" in s["unknown_note"])

    e = s["expect_side_full"]
    ck("A10 167 两道期望侧计数已拆开且都落盘",
       "n_gold_unresolved" in e and "n_gold_unjsonable" in e
       and "n_gold_swapped" in e and "n_files" in e, str(e))
    ck("A10b 全量逐文件累加 ≥ 166 的 commit 级采样",
       e["n_gold_unresolved"]
       >= s["expect_side_sampled_166"]["n_gold_unresolved"],
       "%s vs %s" % (e["n_gold_unresolved"],
                     s["expect_side_sampled_166"]["n_gold_unresolved"]))
    ck("A10c 两条调用链经别名表归一后一致",
       all(s["expect_side_cross_check"][k]
           == s["expect_side_sampled_166"][k]
           for k in ("n_gold_unresolved", "n_gold_unjsonable")),
       "%s vs %s" % (s["expect_side_cross_check"],
                     s["expect_side_sampled_166"]))
    ck("A10d 别名回退次数被显式记账（不是静默吞）",
       s["expect_alias_hits"].get("n_unserializable", 0) > 0,
       str(s["expect_alias_hits"]))
    #: A11 用 git 里的 166 版本源码做**行为对拍**（⛔ 不断言文案、不写 or True）
    import hashlib
    import subprocess
    p = subprocess.run(["git", "show", "cd9e90c:aether/extract.py"],
                       cwd=str(ROOT), capture_output=True)
    if p.returncode == 0:
        old = hashlib.sha256(p.stdout).hexdigest()
        ck("A11 当前抽取器口径 ≠ 166 旧口径（跨口径不可比）",
           d["caliber"]["aether/extract.py"] != old,
           "old=%s new=%s" % (old[:12],
                              d["caliber"]["aether/extract.py"][:12]))
    else:
        skip("A11 旧口径对拍", "git 历史不含 cd9e90c（浅克隆）")

    #: A12–A19 钉死本轮关键读数（⛔ 尺子可读不可写，改了必须重跑普查）
    g = s["gates"]
    ck("A12 全量 120 commit / 459 条新增断言 / 0 跳过",
       s["n_rows"] == 459 and s["n_commits"] == 120
       and s["n_skipped"] == 0,
       "%s/%s/%s" % (s["n_rows"], s["n_commits"], s["n_skipped"]))
    ck("A13 三大门量级 form 119 / unserializable 122 / ctx 123",
       g.get("form") == 119 and g.get("unserializable") == 122
       and g.get("ctx") == 123, str(g))
    ck("A14 F2P 45 条且判据侧两格 F2P 均为 0（同源恒等式）",
       g.get("F2P") == 45
       and s["cross"]["no_criteria"].get("F2P", 0) == 0
       and s["cross"]["no_f2p"].get("F2P", 0) == 0, str(g.get("F2P")))
    ck("A14b 45 条 F2P 全落在 175 的定位/形态侧四格",
       set(s["f2p_by_v175"]) == {"f2p_but_no_focus", "no_units",
                                 "out_of_reach", "in_reach"}
       and sum(s["f2p_by_v175"].values()) == 45,
       str(s["f2p_by_v175"]))
    ck("A15 form 最大家族是裸 assert 80 条（扩张需新比较语义）",
       s["form_attrs"].get("assert") == 80,
       str(s["form_attrs"])[:200])
    ck("A16 254 条抽取层门 downstream=unknown / 205 条沙箱层 measured",
       s["downstream"] == {"unknown": 254, "measured": 205},
       str(s["downstream"]))
    e = s["expect_side_full"]
    ck("A17 文件级全量：132 文件 / unresolved 8172 / 真不可 JSON 370",
       e.get("n_files") == 132 and e.get("n_gold_unresolved") == 8172
       and e.get("n_gold_unjsonable") == 370, str(e)[:200])
    ck("A17b ⛔ 结构性成因存在但占比小（不许沿用 167 的「0 条」）",
       0 < e.get("n_gold_unjsonable", 0) < e.get("n_gold_unresolved", 0))
    ck("A18 别名回退覆盖 111 个 commit（n_unjson 恒 0 的成因）",
       s["expect_alias_hits"].get("n_unserializable") == 111,
       str(s["expect_alias_hits"]))
    ck("A19 unmatched 4 条单列（⛔ 不许静默归 ctx）",
       g.get("unmatched") == 4, str(g.get("unmatched")))


# ══════════════ B 组：离线注入反证 ══════════════
def group_b():
    print("\n【B】离线注入反证（不依赖 /tmp/realrepo）")

    #: B0 先证明闸能绿（否则后面全红没有意义）
    good = [fake_row("form", "no_criteria"),
            fake_row("unserializable", "no_criteria"),
            fake_row("ctx", "no_f2p", verdict="no_result"),
            fake_row("P2P", "no_f2p", verdict="ok"),
            fake_row("F2P", "in_reach", verdict="ok")]
    s0 = pgc.summarize(good, [fake_commit()],
                       {"ok": True, "diffs": [], "caliber_diffs": []}, [])
    ck("B0 合成样本上闸能绿（n_rows=5 / 门分布可算）",
       s0["n_rows"] == 5 and s0["gates"]["form"] == 1
       and s0["gates"]["F2P"] == 1, str(s0["gates"]))

    #: B1 篡改 gate：form → F2P，读数必须变
    mutated = [dict(r, gate="F2P", downstream="measured")
               if r["gate"] == "form" else r for r in good]
    s1 = pgc.summarize(mutated, [fake_commit()], {"ok": True}, [])
    ck("B1 把 form 篡改成 F2P ⇒ 门分布与 F2P 归属都变",
       s1["gates"].get("form", 0) == 0 and s1["gates"]["F2P"] == 2
       and s1["f2p_by_v175"].get("no_criteria", 0) == 1,
       str(s1["f2p_by_v175"]))

    #: B2 注入未知 verdict ⇒ 守恒被破坏（可检出，不静默吞）
    bogus = good + [fake_row("form", "not_a_verdict")]
    s2 = pgc.summarize(bogus, [fake_commit()], {"ok": True}, [])
    total = sum(sum(v.values()) for v in s2["cross"].values())
    ck("B2 未知 verdict 会被交叉表察觉（Σ < n_rows）",
       total == s2["n_rows"] - 1, "%d vs %d" % (total, s2["n_rows"]))

    #: B3 downstream 分类不是死代码
    ck("B3 抽取层门 → unknown / 沙箱层门 → measured",
       all(pgc.downstream_of(g) == "unknown"
           for g in pgc.EXTRACT_GATES)
       and all(pgc.downstream_of(g) == "measured"
               for g in pgc.SAND_GATES), "")
    forced = [dict(r, gate="form", downstream="measured") for r in good]
    ck("B3b 把 form 强行标成 measured ⇒ 与分类函数不符（可检出）",
       any(r["downstream"] != pgc.downstream_of(r["gate"])
           for r in forced))

    #: B4 强制全 P2P ⇒ 其他门清零（证明尺子不是恒真）
    allp = [dict(r, gate="P2P", downstream="measured") for r in good]
    s4 = pgc.summarize(allp, [fake_commit()], {"ok": True}, [])
    ck("B4 强制全 P2P ⇒ form/unserializable/F2P 全 0",
       s4["gates"] == {"P2P": len(good)}
       and s4["downstream"] == {"measured": len(good)},
       str(s4["gates"]))

    #: B5 空集合：0 要能被算出且不能被读成「通过」（第 25 条）
    s5 = pgc.summarize([], [], {"ok": True}, [])
    ck("B5 rows=[] ⇒ n_rows=0 且各门全 0（不是静默通过）",
       s5["n_rows"] == 0 and s5["gates"] == {}
       and s5["downstream"] == {}, str(s5["gates"]))

    #: B6 口径签名反证：改值 / 删键都必须转红
    disk = pgc.caliber()
    saved = dict(disk)
    ck("B6 干净签名 ⇒ 差异为空", pgc.caliber_matches(saved, disk) == [])
    k = sorted(disk)[0]
    bad = dict(saved, **{k: "0" * 64})
    ck("B6b 篡改一个源码 hash ⇒ 点名该文件",
       pgc.caliber_matches(bad, disk) == [k],
       str(pgc.caliber_matches(bad, disk)))
    missing = {a: b for a, b in saved.items() if a != k}
    ck("B6c 删一个键 ⇒ 也算差异（不许默认值兜底）",
       pgc.caliber_matches(missing, disk) == [k])

    #: B7 全 form rows 不许被读成「有 F2P」
    allf = [fake_row("form", "no_criteria") for _ in range(7)]
    s7 = pgc.summarize(allf, [fake_commit()], {"ok": True}, [])
    ck("B7 全 form ⇒ f2p_by_v175 为空、downstream 全 unknown",
       s7["f2p_by_v175"] == {}
       and s7["downstream"] == {"unknown": 7},
       "%s | %s" % (s7["f2p_by_v175"], s7["downstream"]))

    #: B8 默认路径必经验锚（行为反证，不断言源码文案）
    class Sentinel(Exception):
        pass

    orig_gate, orig_argv = pgc.require_baseline, sys.argv
    hit = []

    def boom(*a, **k):
        hit.append(1)
        raise Sentinel()

    pgc.require_baseline = boom
    n_default = n_bypass = -1
    try:
        sys.argv = ["probe_criteria_gate_census.py", "--only", "no_such_sha"]
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                pgc.main()
            except Sentinel:
                pass
        n_default = len(hit)
        hit.clear()
        sys.argv = ["probe_criteria_gate_census.py", "--only", "no_such_sha",
                    "--no-baseline-gate"]
        with contextlib.redirect_stdout(io.StringIO()):
            pgc.main()
        n_bypass = len(hit)
    finally:
        pgc.require_baseline, sys.argv = orig_gate, orig_argv
    ck("B8 默认路径调验锚 1 次、--no-baseline-gate 调 0 次",
       n_default == 1 and n_bypass == 0,
       "default=%s bypass=%s" % (n_default, n_bypass))

    #: B9 别名表（data-artifact-contract 第 77 条）：读漏键名会造出假差异
    hits: dict = {}
    ck("B9 stx 旧键名 n_unserializable 能回退到规范名且命中被记账",
       pgc.stat_of({"n_unserializable": 6}, "n_gold_unjsonable", hits) == 6
       and hits == {"n_unserializable": 1}, str(hits))
    ck("B9b 规范名优先（不被同名字段的脏值带偏）",
       pgc.stat_of({"n_gold_unjsonable": 6, "n_unserializable": 99},
                   "n_gold_unjsonable") == 6)
    ck("B9c 两键皆无 ⇒ 0 且不算别名命中（第 25 条：0 要可解释）",
       pgc.stat_of({}, "n_gold_unjsonable") == 0)


# ══════════════ C 组：真实基线本机覆盖 ══════════════
def group_c():
    print("\n【C】真实基线（本机；CI 无基线时单列未覆盖）")
    if not all((Path("/tmp/realrepo") / n / ".git" / "HEAD").is_file()
               for n in ("boltons", "more-itertools", "attrs")):
        skip("C1 三仓库可读", "取证基线不在本机（未覆盖 ≠ 通过）")
        return
    from realrepo_baseline import verify, load_baseline  # noqa: E402
    rep = verify(load_baseline())
    ck("C1 基线锚健康（普查读数的前提）", rep["ok"],
       str(rep["diffs"])[:200])
    ck("C2 175 八格存档仍与本轮分母一致",
       len(json.loads((ROOT / "scripts" / "focus_census.json").read_text(
           encoding="utf-8"))["rows"]) == 120)


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

#!/usr/bin/env python3
"""契约：**第三次基线破坏 + 174 设施首次端到端实战取证**（README 181）。

═══ 钉死什么 ═══

174 把取证基线的零点钉进版本控制，并加了 `require_baseline()` fail-closed
与 `bootstrap` 按钉死 sha 重建。但建锚那一轮的验证**全是契约里注入的
离线反证**：真实破坏来临时，这套设施到底跑没跑到、有没有按零点复原，
从来没被观察过。

181（2026-11-10 00:01）第三次破坏给了第一次实战机会。三条硬约束：

  A. **钉死快照读数**：3 份真实破坏态骨架（bootstrap `mv` 保留、未删）
     逐份过产品同源 `verify_repo`；3 次 `bootstrap` rc=0 且 head 与锚
     逐字相同；恢复后 `verify` `OK 3/3 diffs=0`；闸覆盖 6/6；
     旁路留痕 5+1；离线反证 4/4；10 条封闭等式逐条为真。
  B. **观察 / 推断 / 未知分开**（契约⑤）：⛔ 真实破坏态下
     `require_baseline()` 的退出码 **NOT_CAPTURED**（恢复动作关掉了窗口）。
     离线反证只证明「机制有牙」，**绝不**合并成「实战拦截成功」。
     破坏源仍未定位；旁路可观测缺口只报不改。
  C. **反证组**（契约⑥）：B 组纯内存注入（篡改读数 / 恒真分类 /
     去掉验锚 / 两本账合并），C 组才碰本机锚与易失骨架；骨架被系统
     清理时单列 SKIP，⛔ **未覆盖 ≠ 通过**。

⛔ 存档是**快照**，不是可复跑的普查：破坏态骨架在系统临时目录，
   下次跑（或 CI 干净检出）可能已不存在。所以 A 组只读存档，
   B 组不依赖骨架，C 组缺失时显式 SKIP。

用法：python tests/contract_baseline_incident_test.py
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_baseline_incident as pbi      # noqa: E402
import realrepo_baseline as rb             # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "baseline_incident.json"
SCRIPT = ROOT / "scripts" / "probe_baseline_incident.py"

PASSES: list = []
FAILS: list = []
SKIPS: list = []

EXPECT_INCIDENTS = ("169", "174", "181")
EXPECT_BROKEN_GATES = {"head_missing_early_exit": 3,
                       "other_diff": 0, "healthy": 0}
EXPECT_HEADS = ("4e5faa3", "67fcf64", "97f8d17")
EXPECT_NO_TRACE = ["probe_focus_census.py"]
CLOSURE_EQS = (
    "eq_3_skeletons", "eq_broken_all_head_missing", "eq_broken_none_healthy",
    "eq_bootstrap_3_rc0", "eq_bootstrap_heads_anchored",
    "eq_old_skeletons_kept", "eq_recovered_3_pass",
    "eq_gate_6_scripts", "eq_bypass_trace_5_plus_1",
    "eq_falsify_all_passed", "eq_three_incidents",
)


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    #: 要能定位失败记账（写成条件表达式会判「失败不落地」⇒ 真红）。
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print("  SKIP %s  | %s" % (name, why))


def read() -> dict:
    return json.loads(READING.read_text(encoding="utf-8"))


# ─────────────────────────── A 组：钉死快照读数 ───────────────────────────
def group_a() -> None:
    print("\n【A】全量读数（⛔ 改尺子必须重跑普查；存档是快照不是可复跑普查）")
    d = read()
    s = d["summary"]
    ck("A1 schema 版本", d["schema"] == "baseline_incident/v1",
       str(d.get("schema")))

    inc = d["incidents"]
    ck("A2 三次破坏时间线 169/174/181 且编号不重排",
       tuple(x["id"] for x in inc) == EXPECT_INCIDENTS,
       str([x["id"] for x in inc]))
    ck("A3 第三次破坏范围写的是三仓库 + 00:01",
       inc[-1]["date"] == "2026-11-10 00:01"
       and "boltons+mi+attrs" in inc[-1]["scope"],
       str(inc[-1]))

    br = d["broken"]
    ck("A4 三份真实破坏态骨架全在且都在锚里",
       br["n_skeletons"] == 3 and len(br["rows"]) == 3
       and all(r["in_anchor"] for r in br["rows"]),
       str(br["n_skeletons"]))
    ck("A5 破坏态逐字段格 3/0/0（无 healthy）",
       br["gates"] == EXPECT_BROKEN_GATES, str(br["gates"]))
    ck("A6 三份骨架 .git/HEAD 全缺、工作区 0 文件、objects 全空",
       br["n_all_head_missing"] == 3
       and all(r["shape"]["worktree_n_files"] == 0
               and r["shape"]["objects_n_files"] <= 2
               and not r["shape"]["head_present"] for r in br["rows"]),
       str([r["shape"] for r in br["rows"]]))
    ck("A7 破坏态验签全部 ok=False 且点名 .git/HEAD 缺失",
       all(not r["verify_ok"] and any(".git/HEAD 缺失" in x
                                      for x in r["diffs"])
           for r in br["rows"]), str([r["diffs"] for r in br["rows"]]))
    ck("A8 ⛔ 早退字段没测过被显式记下，未写成『没问题』",
       all("没测过" in r["note"] for r in br["rows"]))

    boot = d["bootstrap"]
    ck("A9 bootstrap 三次 rc=0 且各有 clone/重建/旧骨架另存",
       boot["n_rc0"] == 3 and boot["n_entries"] == 3
       and all(e["rc"] == 0 and e["clone_line_present"]
               and e["rebuilt_line_present"]
               and e["old_skeleton_moved_not_deleted"]
               for e in boot["entries"]), str(boot["n_rc0"]))
    ck("A10 重建 head 与锚逐字相同（不是远端最新）",
       boot["n_heads_match_anchor"] == 3
       and tuple(e["head"] for e in boot["entries"]) == EXPECT_HEADS,
       str([e["head"] for e in boot["entries"]]))

    rec = d["recovered"]
    ck("A11 恢复后 verify ok=True / 3 pass / 0 diff / caliber 无漂移",
       rec["ok"] and rec["n_pass"] == 3 and rec["n_repos"] == 3
       and not rec["caliber_diffs"]
       and all(not r["diffs"] for r in rec["rows"]), str(rec["ok"]))
    ck("A12 恢复后逐仓库读数：n_commits 40、smoke 3/3、census 对数",
       all(r["readings"].get("n_commits") == 40
           and r["readings"].get("smoke") == "3/3"
           for r in rec["rows"]), str([r["readings"] for r in rec["rows"]]))

    g = d["gate_coverage"]
    ck("A13 闸覆盖：6 把尺子全调 require_baseline 且旁路默认关",
       g["n_scripts"] == 6 and g["n_calls_gate"] == 6
       and g["n_bypass_default_off"] == 6,
       str((g["n_scripts"], g["n_calls_gate"], g["n_bypass_default_off"])))
    ck("A14 ⛔ 两本账分开：旁路留痕 5/6，缺口如实单列不合并",
       g["n_bypass_trace_channel"] == 5
       and g["no_trace_scripts"] == EXPECT_NO_TRACE
       and "两本账" in g["note"], str(g["no_trace_scripts"]))

    f = d["falsification"]
    ck("A15 离线反证 4/4 且明确标为 offline_injected",
       f["n_cases"] == 4 and f["n_passed"] == 4
       and f["kind"] == "offline_injected", str(f["n_passed"]))
    ck("A16 四个 case 齐全且恒真 verify 被放行（闸依赖真输出）",
       set(c["case"] for c in f["cases"]) ==
       {"healthy_anchor", "bad_head", "bad_path", "always_true_verify"}
       and all(c["passed"] for c in f["cases"]), str(f["cases"]))
    ck("A17 坏锚反证 stderr 点名具体字段，不是笼统一句红",
       all(c["stderr_names_failure"] for c in f["cases"]
           if c["case"] in ("bad_head", "bad_path")))

    ck("A18 封闭等式名齐全且不许重排/新增",
       tuple(sorted(s["closure"])) == tuple(sorted(CLOSURE_EQS)),
       str(sorted(s["closure"])))
    for name in CLOSURE_EQS:
        ck("A19.%s 十一条封闭等式逐条为真" % name,
           s["closure"].get(name) is True, str(s["closure"].get(name)))
    ck("A20 n_closure_true == n_closure_items == 11",
       s["n_closure_true"] == 11 and s["n_closure_items"] == 11,
       str((s["n_closure_true"], s["n_closure_items"])))

    obs = s["observations"]
    ck("A21 ⛔⛔ 真实破坏态 require_baseline 退出码 = NOT_CAPTURED",
       obs["real_broken_require_baseline_exit"] == "NOT_CAPTURED",
       str(obs["real_broken_require_baseline_exit"]))
    ck("A22 破坏态 CLI 读数与中途 2/3 读数都记下，未合并成单次读数",
       "repos=0/3" in obs["real_broken_verify_cli"]
       and "repos=2/3" in obs["mid_recovery_verify_cli"])
    unk = s["unknowns"]
    ck("A23 未知逐条保留（破坏源 / 退出码 / 旁路 / CI H 组 / 119 条 form）",
       len(unk) >= 6
       and any("破坏源仍未定位" in x for x in unk)
       and any("CI H 组" in x for x in unk)
       and any("119 条 form" in x for x in unk), str(len(unk)))
    ck("A24 note 明确禁止把离线反证合并成『实战拦截成功』",
       "绝不合并" in s["note"] and "不报真实任务域收益" in s["note"])

    cal = d["caliber"]
    ck("A25 口径签名含尺子自身且无 __MISSING__",
       "scripts/probe_baseline_incident.py" in cal
       and all(v != "__MISSING__" for v in cal.values()), str(sorted(cal)))
    ck("A26 尺子自身 sha256 与当前文件一致（改尺子必须重跑普查）",
       cal.get("scripts/probe_baseline_incident.py")
       == hashlib.sha256(SCRIPT.read_bytes()).hexdigest())


# ───────────────── B 组：纯内存离线反证（不依赖骨架、不 spawn git）─────────────────
def group_b() -> None:
    print("\n【B】离线反证：证明断言不是死代码（⛔ 全在内存，不碰 V6）")
    d = read()

    # B1 篡改读数 ⇒ A 组的核心判据必须转红（防止「怎么改都绿」）
    bad = copy.deepcopy(d)
    bad["broken"]["gates"]["head_missing_early_exit"] = 0
    bad["broken"]["gates"]["healthy"] = 3
    ck("B1 篡改破坏态格 3→0/healthy 3 ⇒ 钉死判据转红",
       not (bad["broken"]["gates"] == EXPECT_BROKEN_GATES))
    bad2 = copy.deepcopy(d)
    bad2["recovered"]["n_pass"] = 0
    bad2["recovered"]["ok"] = False
    ck("B2 篡改恢复态 3→0 ⇒ 钉死判据转红",
       not (bad2["recovered"]["ok"] and bad2["recovered"]["n_pass"] == 3))

    # B3 恒真分类 ⇒ 必须能被反证抓住
    ck("B3 classify_broken 空 diff ⇒ healthy（能绿，不是恒红）",
       pbi.classify_broken([]) == "healthy")
    ck("B4 classify_broken 单 HEAD 缺失 ⇒ head_missing_early_exit",
       pbi.classify_broken(["attrs.path: .git/HEAD 缺失"])
       == "head_missing_early_exit")
    ck("B5 classify_broken 其它 diff ⇒ other_diff（fail-closed 保留未知）",
       pbi.classify_broken(["x.head: 不可读", "y.smoke: hash 不符"])
       == "other_diff")
    ck("B6 封闭格只有三格且不许为迁就读数扩张",
       pbi.BROKEN_GATES == ("head_missing_early_exit", "other_diff", "healthy"))

    # B7 AST 行为级：闸真的调 verify，且真的 raise SystemExit(2)
    src = (ROOT / "scripts" / "realrepo_baseline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((x for x in tree.body if isinstance(x, ast.FunctionDef)
               and x.name == "require_baseline"), None)
    calls_verify = bool(fn) and any(
        isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
        and x.func.id == "verify" for x in ast.walk(fn))
    has_exit = bool(fn) and any(
        isinstance(x, ast.Raise) and isinstance(x.exc, ast.Call)
        and isinstance(x.exc.func, ast.Name) and x.exc.func.id == "SystemExit"
        for x in ast.walk(fn))
    ck("B7 AST：require_baseline 真的调用 verify（不扫注释）", calls_verify)
    ck("B8 AST：require_baseline 真的 raise SystemExit", has_exit)

    # B9 内存 monkeypatch：闸依赖 verify 真输出，恒真会被放行
    orig = rb.verify
    fake_bad = {"ok": False, "diffs": ["x"], "caliber_diffs": [], "repos": {}}
    fake_ok = {"ok": True, "diffs": [], "caliber_diffs": [], "repos": {}}
    try:
        rb.verify = lambda *a, **k: fake_bad
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                rb.require_baseline()
            code_bad = None
        except SystemExit as e:
            code_bad = e.code
        ck("B9 verify 报不健康 ⇒ SystemExit(2)（离线内存反证）",
           code_bad == 2, str(code_bad))

        rb.verify = lambda *a, **k: fake_ok
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                rb.require_baseline()
            code_ok = None
        except SystemExit as e:
            code_ok = e.code
        ck("B10 verify 恒真 ⇒ 放行（⛔ 证明闸不是恒真断言）",
           code_ok is None, str(code_ok))
    finally:
        rb.verify = orig

    # B11 两本账合并的假尺子必须被识别
    merged = copy.deepcopy(d)
    merged["gate_coverage"]["n_bypass_trace_channel"] = 6
    merged["gate_coverage"]["no_trace_scripts"] = []
    ck("B11 把 5+1 抹平成 6/6 ⇒ 两本账判据转红",
       not (merged["gate_coverage"]["n_bypass_trace_channel"] == 5
            and merged["gate_coverage"]["no_trace_scripts"] == EXPECT_NO_TRACE))

    # B12 去掉验锚调用 ⇒ AST 行为级断言必须归零
    fake_src = "def main():\n    args = parse()\n    rows = []\n"
    ck("B12 去掉 require_baseline 调用 ⇒ AST 计数归零",
       pbi._calls(ast.parse(fake_src), "require_baseline") == 0)
    ck("B13 AST 计数对真调用能命中（零命中先证查询能命中）",
       pbi._calls(ast.parse("def f():\n    require_baseline()\n"),
                  "require_baseline") == 1)

    # B14 三次破坏时间线不许重排 / 不许新增为迁就读数
    ck("B14 INCIDENTS 封闭三行、编号不重排",
       tuple(x[0] for x in pbi.INCIDENTS) == EXPECT_INCIDENTS)

    # B15 脚本语法可解析（3.9 兼容由 pycompat 契约单独守）
    try:
        ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
        ck("B15 尺子脚本语法可解析", True)
    except SyntaxError as exc:
        ck("B15 尺子脚本语法可解析", False, str(exc))


# ───────────────── C 组：本机锚与易失骨架（缺失单列 SKIP）─────────────────
def group_c() -> None:
    print("\n【C】本机验锚 + 真实物证复算（⛔ 易失，缺失单列 SKIP ≠ 通过）")
    try:
        bl = rb.load_baseline()
        rep = rb.verify(bl)
    except (OSError, ValueError) as exc:
        skip("C1 本机基线锚健康", "锚不可读：%s" % exc)
        return
    ck("C1 本机基线锚健康 OK 3/3 diffs=0",
       rep["ok"] and len(rep["repos"]) == 3 and not rep["diffs"],
       str(rep["diffs"]))
    ck("C2 恢复后 head 与存档逐字相同（零点未前移）",
       all(rep["repos"][n]["readings"].get("head")
           == bl["repos"][n]["head"][:7] for n in rb.REPOS))

    skels = pbi.find_broken_skeletons()
    if not skels:
        skip("C3 真实破坏态骨架复算",
             "系统临时目录已清理 3 份 broken 骨架（本轮存档是快照）")
        skip("C4 骨架逐份 verify_repo 全部 ok=False", "同 C3，物证已易失")
        return
    ck("C3 真实破坏态骨架仍在本机且恰好 3 份",
       len(skels) == 3, str(len(skels)))
    reps = []
    for s in skels:
        name = s.name.split("_broken_")[0]
        if name in bl["repos"]:
            reps.append(rb.verify_repo(name, dict(bl["repos"][name],
                                                  path=str(s))))
    ck("C4 骨架逐份 verify_repo 全部 ok=False 且点名 HEAD 缺失",
       len(reps) == 3 and all(not r["ok"] and any(".git/HEAD 缺失" in x
                                                  for x in r["diffs"])
                              for r in reps), str([r["diffs"] for r in reps]))
    ck("C5 破坏态骨架与恢复态锚不是同一路径（防把恢复态误当物证）",
       all(str(s) != bl["repos"][s.name.split("_broken_")[0]]["path"]
           for s in skels if s.name.split("_broken_")[0] in bl["repos"]))


def main() -> int:
    print("契约：第三次基线破坏 + 174 设施首次端到端实战取证（README 181）")
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

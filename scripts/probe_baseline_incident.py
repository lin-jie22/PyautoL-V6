#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r181 / README 181：取证基线第三次被破坏 + 174 设施首次端到端实战取证。

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ 纯只读取证：把 2026-11-10 00:01 三仓库 `.git` 骨架被破坏这件事的
   **真实物证**（bootstrap 用 `shutil.move` 原样保留的 3 份 broken 骨架）
   逐份喂给产品同源 `realrepo_baseline.verify_repo`，复算破坏态逐字段读数；
   再把 `bootstrap` 按钉死 sha 重建后的三仓库逐份复算恢复态读数。
✅ 顺带做「闸覆盖普查」：六把普查尺子的 `require_baseline()` 入口形态、
   `--no-baseline-gate` 默认值、以及旁路是否被写成显式留痕。
✅ 做「三次破坏时间线」的可复算对拍：169 / 174 / 181 的破坏范围与形态。
⛔ **不改 `aether/`、不改 `realrepo_baseline.py`、不重建锚、不写 `/tmp/realrepo`**；
   本轮恢复动作由产品既有 `bootstrap` 完成，脚本只在事后读取它的产物。
⛔ 不报任何真实任务域收益，不开 5 项待拍板中的任何一项。

═══ ⛔⛔ 观察 / 推断 / 未知（契约⑤，这轮最容易越界的地方）═══

1. **观察**：三份 broken 骨架的目录形态、mtime、`.git/HEAD` 缺失、
   `verify_repo` 的逐字段 diff、`bootstrap` 日志的三个 `rc=0` 与读数、
   恢复后 `verify` 的 `OK 3/3 diffs=0`。
2. **⛔ 没观察到**：真实破坏态下 `require_baseline()` 的**退出码**。
   恢复动作启动得太快，把窗口关掉了；事发时只跑过 `verify`（CLI），
   它报 `UNHEALTHY repos=0/3 diffs=3`，但那不等于「普查入口真的
   SystemExit(2)」。⇒ 离线注入反证只证明**机制有牙**，不证明**这次
   事故里它被触发过**。绝不把两者合并成「fail-closed 实战拦截成功」。
3. **推断**：三次破坏像周期性清理（都在 00:0x 定点、目录骨架保留、
   只剩文件被清）。⛔ **破坏源仍未定位**，不猜；也不据此宣布是系统
   periodic / 某个 V6 任务 / 外部工具。
4. **未知**：为什么第三次连 `index` 都还在而 `HEAD/config/objects` 没了；
   下一次会不会再发生；CI 侧 H 组仍是 SKIP（需拍板 ③）。

═══ 同源（契约⑥）═══

破坏态与恢复态都用产品自己的 `realrepo_baseline.verify_repo` /
`verify`，不在脚本里另写一份「什么是健康」的词表；闸覆盖用 AST
行为级断言（真的调了 `require_baseline`、真的是 `action="store_true"`），
不扫注释文案。
"""
from __future__ import annotations

import argparse
import ast
import glob
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import realrepo_baseline as rb  # noqa: E402

DEFAULT_OUT = ROOT / "scripts" / "baseline_incident.json"
BOOT_LOG = Path("/tmp/r102_bootstrap.log")
TMP_ROOT = Path(tempfile.gettempdir())

#: ⛔ 封闭集合：定好后不许为迁就读数新增格（166 纪律）。
INCIDENTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("169", "2026-10-29", "boltons", "HEAD/config/index 缺；objects pack 完好；工作区缺点文件"),
    ("174", "2026-11-05", "boltons+mi+attrs", "HEAD/config/index 全缺；mi/attrs objects 全空；工作区 0 文件"),
    ("181", "2026-11-10 00:01", "boltons+mi+attrs", "HEAD/config 缺；index 保留；objects 全空；工作区 0 文件"),
)

#: 六把普查尺子（174 之后新建的都带同一道闸）。
GATED_SCRIPTS: Tuple[str, ...] = (
    "probe_focus_census.py",
    "probe_criteria_gate_census.py",
    "probe_extract_downstream.py",
    "probe_no_result_attribution.py",
    "probe_form_shape_attribution.py",
    "probe_import_failure_attribution.py",
)

CALIBER_FILES = (
    "scripts/realrepo_baseline.py",
    "scripts/realrepo_baseline.json",
    "scripts/probe_alias_demote.py",
    "tests/contract_realrepo_baseline_test.py",
    "scripts/probe_baseline_incident.py",
)

#: ⛔ 破坏态逐字段判据的封闭格；`verify_repo` 早退时后面的字段
#:    **没测过 ≠ 没问题**，所以早退本身单独成一格，不冒充全量归因。
BROKEN_GATES: Tuple[str, ...] = (
    "head_missing_early_exit",   # .git/HEAD 缺失 ⇒ verify_repo 第一条就早退
    "other_diff",                # 其它任何 diff（fail-closed 保留未知）
    "healthy",                   # ⛔ 若出现，说明骨架不足以证伪，须单列报红
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rel in CALIBER_FILES:
        p = ROOT / rel
        out[rel] = sha256_file(p) if p.is_file() else "__MISSING__"
    return out


def find_broken_skeletons() -> List[Path]:
    """bootstrap 把旧骨架 `mv` 到 `realrepo_bootstrap_*/*_broken_*`，不删。"""
    pat = str(TMP_ROOT / "realrepo_bootstrap_*" / "*_broken_*")
    return sorted(Path(d) for d in glob.glob(pat) if Path(d).is_dir())


def skeleton_shape(path: Path) -> Dict[str, Any]:
    """只读目录形态：不猜谁删的，只报看到什么。"""
    git = path / ".git"
    files = [p for p in path.rglob("*") if p.is_file()
             and ".git" not in p.parts]
    obj = git / "objects"
    obj_files = [p for p in obj.rglob("*") if p.is_file()] if obj.is_dir() else []
    mtimes = [p.stat().st_mtime for p in (git, path) if p.exists()]
    return {
        "git_is_dir": git.is_dir(),
        "head_present": (git / "HEAD").is_file(),
        "config_present": (git / "config").is_file(),
        "index_present": (git / "index").is_file(),
        "objects_n_files": len(obj_files),
        "worktree_n_files": len(files),
        "git_mtime": (max(mtimes) if mtimes else None),
    }


def classify_broken(diffs: List[str]) -> str:
    """只做可复算归类；分类不是「异常原因」。"""
    if not diffs:
        return "healthy"
    if len(diffs) == 1 and ".git/HEAD 缺失" in diffs[0]:
        return "head_missing_early_exit"
    return "other_diff"


def probe_broken(bl: Dict[str, Any]) -> Dict[str, Any]:
    """真实破坏态：把保留下来的骨架逐份喂给产品同源 verify_repo。"""
    skels = find_broken_skeletons()
    rows: List[Dict[str, Any]] = []
    for d in skels:
        name = d.name.split("_broken_")[0]
        if name not in bl["repos"]:
            rows.append({"skeleton": str(d), "repo": name,
                         "in_anchor": False, "diffs": ["骨架名不在锚的三个 repo 里"]})
            continue
        cfg = dict(bl["repos"][name], path=str(d))
        rep = rb.verify_repo(name, cfg)
        shape = skeleton_shape(d)
        rows.append({
            "skeleton": str(d),
            "repo": name,
            "in_anchor": True,
            "shape": shape,
            "verify_ok": rep["ok"],
            "diffs": list(rep["diffs"]),
            "gate": classify_broken(rep["diffs"]),
            "note": "⛔ verify_repo 早退 ⇒ objects / head / commit_shas "
                    "等后续字段没测过，不等于没问题",
        })
    return {
        "n_skeletons": len(skels),
        "rows": rows,
        "gates": {g: sum(1 for r in rows if r.get("gate") == g)
                  for g in BROKEN_GATES},
        "n_all_head_missing": sum(1 for r in rows
                                  if r.get("shape", {}).get("head_present") is False),
    }


def probe_bootstrap_log() -> Dict[str, Any]:
    """读产品 bootstrap 自己的日志；⛔ 日志不存在就如实报缺失，不编。"""
    if not BOOT_LOG.is_file():
        return {"log_present": False, "n_rc0": 0, "entries": [],
                "note": "bootstrap 日志不在本机（CI/干净检出属正常），不猜读数"}
    text = BOOT_LOG.read_text(encoding="utf-8", errors="replace")
    entries: List[Dict[str, Any]] = []
    for block in text.split("=== bootstrap ")[1:]:
        name = block.split(" ===")[0].strip()
        rc = None
        for line in block.splitlines():
            if line.startswith("rc="):
                rc = int(line[3:].strip())
        rebuilt = next((l for l in block.splitlines()
                        if "重建完成 head=" in l), "")
        moved = next((l for l in block.splitlines()
                      if "旧基线另存:" in l), "")
        head = ""
        if "head=" in rebuilt:
            head = rebuilt.split("head=", 1)[1].split()[0]
        entries.append({
            "repo": name, "rc": rc, "head": head,
            "rebuilt_line_present": bool(rebuilt),
            "old_skeleton_moved_not_deleted": bool(moved),
            "clone_line_present": "clone " in block,
        })
    return {
        "log_present": True,
        "log_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "n_entries": len(entries),
        "n_rc0": sum(1 for e in entries if e["rc"] == 0),
        "n_heads_match_anchor": 0,   # 由 main 用锚回填
        "entries": entries,
    }


def probe_recovered(bl: Dict[str, Any]) -> Dict[str, Any]:
    """恢复态：产品同源 verify，逐仓库读数单独落，不合并成布尔。"""
    rep = rb.verify(bl)
    rows: List[Dict[str, Any]] = []
    for name in rb.REPOS:
        one = rep["repos"][name]
        rows.append({
            "repo": name,
            "ok": one["ok"],
            "diffs": list(one["diffs"]),
            "readings": dict(one["readings"]),
            "anchor_head": bl["repos"][name]["head"][:7],
        })
    return {
        "ok": rep["ok"],
        "caliber_diffs": list(rep["caliber_diffs"]),
        "n_repos": len(rows),
        "n_pass": sum(1 for r in rows if r["ok"]),
        "rows": rows,
    }


def _calls(node: ast.AST, attr: str) -> int:
    return sum(1 for x in ast.walk(node)
               if isinstance(x, ast.Call)
               and ((isinstance(x.func, ast.Name) and x.func.id == attr)
                    or (isinstance(x.func, ast.Attribute)
                        and x.func.attr == attr)))


def probe_gate_coverage() -> Dict[str, Any]:
    """AST 行为级：真的调了 require_baseline；旁路默认关。

    ⛔⛔ **两本账分开（真红后修口径，不是迁就读数）**：
      · `calls_require_baseline` / `bypass_default_off` = 闸有没有牙（6 把全适用）；
      · `bypass_trace_channel` = 旁路开过时**读数里看不看得到**（只适用于
        有 `health` 落盘通道的尺子）。173 时代的 `probe_focus_census.py`
        直接 `require_baseline()` 且不落盘 health ⇒ 这一格对它**不适用**，
        不是「闸没牙」，而是「旁路不可观测」。合并成一个布尔就会把
        这两种完全不同的修法混掉（规则 138 / 两本账别合并）。
    """
    rows: List[Dict[str, Any]] = []
    for fn in GATED_SCRIPTS:
        p = ROOT / "scripts" / fn
        if not p.is_file():
            rows.append({"script": fn, "present": False,
                         "calls_require_baseline": 0,
                         "bypass_default_off": False,
                         "has_health_channel": False,
                         "bypass_trace_channel": False})
            continue
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        bypass_off = False
        for x in ast.walk(tree):
            if not (isinstance(x, ast.Call)
                    and isinstance(x.func, ast.Attribute)
                    and x.func.attr == "add_argument"):
                continue
            if not (x.args and isinstance(x.args[0], ast.Constant)
                    and x.args[0].value == "--no-baseline-gate"):
                continue
            bypass_off = any(k.arg == "action" and isinstance(k.value, ast.Constant)
                             and k.value.value == "store_true" for k in x.keywords)
        # 是否有「把健康度写进读数」的通道：找 health[...] 的赋值。
        has_health = any(
            isinstance(x, ast.Assign)
            and any(isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "health" for t in x.targets)
            for x in ast.walk(tree))
        rows.append({
            "script": fn,
            "present": True,
            "calls_require_baseline": _calls(tree, "require_baseline"),
            "bypass_default_off": bypass_off,   # store_true ⇒ 默认 False ⇒ 主路径验锚
            "has_health_channel": has_health,
            "bypass_trace_channel": has_health and "BYPASS（仅反证）" in src,
            "sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
        })
    no_trace = [r["script"] for r in rows if r["present"]
                and not r["bypass_trace_channel"]]
    return {
        "n_scripts": len(rows),
        "n_calls_gate": sum(1 for r in rows if r["calls_require_baseline"] > 0),
        "n_bypass_default_off": sum(1 for r in rows if r["bypass_default_off"]),
        "n_has_health_channel": sum(1 for r in rows if r["has_health_channel"]),
        "n_bypass_trace_channel": sum(1 for r in rows
                                      if r["bypass_trace_channel"]),
        "no_trace_scripts": no_trace,
        "rows": rows,
        "note": "⛔ 闸有牙（6/6 调用 + 6/6 默认关）与『旁路可观测』（5/6）"
                "是两本账。no_trace 的那把不是闸没牙，是旁路开过时"
                "读数里查不到 ⇒ 真实的可观测性缺口，本轮如实报出、不擅自改。",
    }


def offline_falsification(bl: Dict[str, Any]) -> Dict[str, Any]:
    """离线注入反证：证明闸依赖 verify 真输出，不是恒真断言。

    ⛔ 这些是**离线构造的坏锚**，不是 11-10 那次真实破坏的运行时读数。
    """
    import contextlib
    import io

    tmp = Path(tempfile.mkdtemp(prefix="r181_falsify_"))
    out: Dict[str, Any] = {"kind": "offline_injected", "cases": []}

    def _gate_case(label: str, mutate, expect_exit: bool) -> None:
        bad = json.loads(json.dumps(bl))
        mutate(bad)
        p = tmp / ("%s.json" % label)
        p.write_text(json.dumps(bad), encoding="utf-8")
        code = None
        stderr = ""
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rb.require_baseline(path=p)
        except SystemExit as e:
            code = e.code
            stderr = err.getvalue()
        out["cases"].append({
            "case": label,
            "exit_code": code,
            "expected_exit": expect_exit,
            "passed": (code == 2) if expect_exit else (code is None),
            "stderr_names_failure": "fail-closed" in stderr or ".git/HEAD 缺失" in stderr
                                    or "目录不存在" in stderr,
        })

    _gate_case("healthy_anchor", lambda b: None, False)
    _gate_case("bad_head", lambda b: b["repos"]["attrs"].update(head="0" * 40), True)
    _gate_case("bad_path", lambda b: b["repos"]["boltons"].update(
        path=str(tmp / "nonexistent")), True)

    # 恒真 verify ⇒ 必须放行（若仍退出，说明闸是恒真/死代码，反证失败）
    bad = json.loads(json.dumps(bl))
    bad["repos"]["attrs"]["head"] = "0" * 40
    p = tmp / "always_true.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    orig = rb.verify
    rb.verify = lambda *a, **k: {"ok": True, "diffs": [],
                                 "caliber_diffs": [], "repos": {}}
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            rb.require_baseline(path=p)
        released = True
    except SystemExit:
        released = False
    finally:
        rb.verify = orig
    out["cases"].append({
        "case": "always_true_verify", "exit_code": None,
        "expected_exit": False, "passed": released,
        "stderr_names_failure": False,
    })
    out["n_cases"] = len(out["cases"])
    out["n_passed"] = sum(1 for c in out["cases"] if c["passed"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    bl = rb.load_baseline()
    broken = probe_broken(bl)
    boot = probe_bootstrap_log()
    recovered = probe_recovered(bl)
    gates = probe_gate_coverage()
    falsify = offline_falsification(bl)

    # bootstrap 的 head 是否与锚逐字相同（钉死的零点，不是远端最新）
    for e in boot.get("entries", []):
        cfg = bl["repos"].get(e["repo"])
        e["head_matches_anchor"] = bool(cfg) and e["head"] == cfg["head"][:7]
    boot["n_heads_match_anchor"] = sum(
        1 for e in boot.get("entries", []) if e.get("head_matches_anchor"))

    closure = {
        "eq_3_skeletons": broken["n_skeletons"] == 3,
        "eq_broken_all_head_missing": (
            broken["gates"]["head_missing_early_exit"] == 3
            and broken["n_all_head_missing"] == 3),
        "eq_broken_none_healthy": broken["gates"]["healthy"] == 0,
        "eq_bootstrap_3_rc0": boot.get("n_rc0") == 3,
        "eq_bootstrap_heads_anchored": boot["n_heads_match_anchor"] == 3,
        "eq_old_skeletons_kept": all(
            e.get("old_skeleton_moved_not_deleted")
            for e in boot.get("entries", [])) and broken["n_skeletons"] == 3,
        "eq_recovered_3_pass": recovered["n_pass"] == 3 and recovered["ok"],
        "eq_gate_6_scripts": (gates["n_scripts"] == 6
                              and gates["n_calls_gate"] == 6
                              and gates["n_bypass_default_off"] == 6),
        "eq_bypass_trace_5_plus_1": (
            gates["n_bypass_trace_channel"] == 5
            and len(gates["no_trace_scripts"]) == 1
            and gates["no_trace_scripts"] == ["probe_focus_census.py"]),
        "eq_falsify_all_passed": falsify["n_passed"] == falsify["n_cases"] == 4,
        "eq_three_incidents": len(INCIDENTS) == 3,
    }

    summary = {
        "n_incidents": len(INCIDENTS),
        "incident_ids": [x[0] for x in INCIDENTS],
        "n_skeletons": broken["n_skeletons"],
        "broken_gates": broken["gates"],
        "n_bootstrap_rc0": boot.get("n_rc0"),
        "n_heads_match_anchor": boot["n_heads_match_anchor"],
        "recovered": {"ok": recovered["ok"],
                      "n_pass": recovered["n_pass"],
                      "n_repos": recovered["n_repos"]},
        "gate_coverage": {"n_scripts": gates["n_scripts"],
                          "n_calls_gate": gates["n_calls_gate"],
                          "n_bypass_default_off": gates["n_bypass_default_off"],
                          "n_has_health_channel": gates["n_has_health_channel"],
                          "n_bypass_trace_channel": gates["n_bypass_trace_channel"],
                          "no_trace_scripts": gates["no_trace_scripts"]},
        "falsification": {"n_cases": falsify["n_cases"],
                          "n_passed": falsify["n_passed"],
                          "kind": "offline_injected"},
        "closure": closure,
        "n_closure_true": sum(1 for v in closure.values() if v is True),
        "n_closure_items": len(closure),
        "observations": {
            "real_broken_verify_cli": "UNHEALTHY repos=0/3 diffs=3（三条 .git/HEAD 缺失）",
            "mid_recovery_verify_cli": "UNHEALTHY repos=2/3 diffs=1（attrs 仍未复原）",
            "real_broken_require_baseline_exit": "NOT_CAPTURED",
        },
        "unknowns": [
            "破坏源仍未定位（第三次）；00:0x 定点与骨架保留只是形态，不是归因",
            "真实破坏态下 require_baseline() 的退出码没抓到：恢复动作关掉了窗口",
            "verify_repo 在 HEAD 缺失时早退 ⇒ 后续字段没测过 ≠ 没问题",
            "正式普查有没有人开过 --no-baseline-gate：本轮没测，不是测出 0",
            "probe_focus_census.py 开旁路时读数里查不到（无 health 通道）："
            "真实的可观测性缺口，本轮只报不改",
            "CI H 组仍 SKIP（需拍板 ③）；bootstrap 是人工触发，不是自动恢复",
            "119 条 form 下游仍 unmeasurable；5 项待拍板口径变更维持未开工",
        ],
        "note": "⛔ 只报设施是否真跑到、是否按钉死零点复原、闸是否有牙；"
                "不报真实任务域收益。离线反证 ≠ 真实事故的运行时读数，"
                "绝不合并成『fail-closed 实战拦截成功』。",
    }
    rep = {
        "schema": "baseline_incident/v1",
        "caliber": caliber(),
        "incidents": [
            {"id": i, "date": d, "scope": s, "shape": shape}
            for i, d, s, shape in INCIDENTS],
        "broken": broken,
        "bootstrap": boot,
        "recovered": recovered,
        "gate_coverage": gates,
        "falsification": falsify,
        "summary": summary,
    }
    out = Path(args.out) if args.out else DEFAULT_OUT
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("incidents=%s skeletons=%d broken_gates=%s" %
          (summary["incident_ids"], broken["n_skeletons"], broken["gates"]))
    print("bootstrap_rc0=%s heads_anchored=%s recovered=%s" %
          (boot.get("n_rc0"), boot["n_heads_match_anchor"],
           recovered["n_pass"]))
    print("gates=%s falsify=%d/%d" %
          (gates["n_calls_gate"], falsify["n_passed"], falsify["n_cases"]))
    print("closure=%d/%d %s" % (summary["n_closure_true"],
                                 summary["n_closure_items"],
                                 json.dumps(closure, ensure_ascii=False,
                                            sort_keys=True)))
    print("written: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

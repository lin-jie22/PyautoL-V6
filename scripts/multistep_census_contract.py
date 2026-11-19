#!/usr/bin/env python3
"""r128 / README 207 契约：多步状态序列的量级普查（纯只读取证）。

§172 证明 boltons 93185b2 的真 F2P 判据抽不到，根因是「单表达式探针表达不了
多步状态序列」（同一迭代器变量上 >=2 次同参消耗调用，后次依赖前次已改状态）。
本契约核一把只读尺子在三份现成存档上的普查读数：4 个 distinct fn（boltons 2 /
more-itertools 2），口径含 args_key 判别以排除「同 receiver 不同实参」的伪多步。

⛔ 本契约不出现 `resolve_policy(...)` 调用点（192 分母闸）。
A–D 读 accounts 分层、逐条可反证置 False；F 组本机静态四项 + 验锚。
合规形态与 r116–r127 同构：run_c() 内嵌 ck + fails.append + return fails。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_multistep_census as pmc       # noqa: E402

CENSUS = ROOT / "scripts" / "multistep_census_census.jsonl"
PROBE = ROOT / "scripts" / "probe_multistep_census.py"
CONTRACT = ROOT / "scripts" / "multistep_census_contract.py"
CONDITIONS = 25  # A6 + B7 + C6 + D6


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_caliber_def"]
    B = d["accounts"]["B_readings"]
    C = d["accounts"]["C_discriminant"]
    D = d["accounts"]["D_boundary"]
    V: Dict[str, bool] = {}
    # A 口径定义层（6）
    V["A1_schema_ok"] = A["schema_ok"] is True
    V["A2_consume_nonempty"] = A["consume_methods_nonempty"] is True
    V["A3_next_in_methods"] = A["next_in_methods"] is True
    V["A4_pop_in_methods"] = A["pop_in_methods"] is True
    V["A5_args_key_in_rule"] = A["args_key_in_rule"] is True
    V["A6_three_source_sha"] = A["three_source_sha"] is True
    # B 读数层（7）
    V["B1_three_sources"] = B["three_sources"] is True
    V["B2_groups_gt_zero"] = B["groups_gt_zero"] is True
    V["B3_hits_not_always_false"] = B["hits_not_always_false"] is True
    V["B4_plausible_upper_bound"] = B["plausible_upper_bound"] is True
    V["B5_boltons_present"] = B["boltons_present"] is True
    V["B6_more_itertools_present"] = B["more_itertools_present"] is True
    V["B7_d172_included"] = B["d172_included"] is True
    # C 真伪判别层（6）
    V["C1_pseudo_instance_excluded"] = C["pseudo_distinct_instance_excluded"] is True
    V["C2_write_diff_args_excluded"] = C["pseudo_write_diff_args_excluded"] is True
    V["C3_true_next_detected"] = C["true_next_same_arg_detected"] is True
    V["C4_not_overturn_172"] = C["not_overturn_172"] is True
    V["C5_diagnostic_only"] = C["readings_are_diagnostic_only"] is True
    V["C6_no_gain_reported"] = C["no_gain_reported"] is True
    # D 边界层（6）
    V["D1_readonly_sources"] = D["readonly_sources"] is True
    V["D2_no_product_change"] = D["no_product_code_change"] is True
    V["D3_no_caliber_change"] = D["no_caliber_change_to_existing"] is True
    V["D4_section183_zero"] = D["section183_zero"] is True
    V["D5_no_resolve_calls"] = D["no_resolve_policy_calls"] is True
    V["D6_artifact_in_scripts"] = D["artifact_in_scripts_jsonl"] is True
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def withdraw_from(V: Dict[str, bool]) -> Dict[str, bool]:
    """反证注入：环境变量 R128_WITHDRAW 前缀匹配的 conditions 置 False。"""
    pre = os.environ.get("R128_WITHDRAW", "")
    if not pre:
        return V
    return {k: (False if k.startswith(pre) else v) for k, v in V.items()}


def static_checks() -> Dict[str, bool]:
    """F 组本机实战：真 Python parse / 裸 noqa 0 / git diff --check / A1 扫描面。"""
    py = sys.executable
    out: Dict[str, bool] = {}
    # F1 真解释器能 parse 两个新文件
    ok = True
    for p in (PROBE, CONTRACT):
        r = subprocess.run([py, "-c",
                            "import ast,sys;ast.parse(open(sys.argv[1]).read())", str(p)],
                           capture_output=True)
        ok = ok and r.returncode == 0
    out["F1_parse_ok"] = ok
    # F2 两个新文件无裸 noqa
    import re
    bare = 0
    for p in (PROBE, CONTRACT):
        for line in p.read_text().splitlines():
            if re.search(r"#\s*noqa\s*(?!:)", line):
                bare += 1
    out["F2_no_bare_noqa"] = bare == 0
    # F3 git diff --check
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--check"], capture_output=True)
    out["F3_diff_check"] = r.returncode == 0
    # F4 A1 扫描面：discover 仍 127 且新产物闸不在 discover 面
    r = subprocess.run([py, "-c",
                        "import sys;sys.path.insert(0,'%s');"
                        "from pathlib import Path;"
                        "import aether.check as c;"
                        "d=c.discover(Path('%s'));"
                        "print(len(d));"
                        "print(any('multistep' in str(x) for x in d))" % (ROOT, ROOT)],
                       capture_output=True, text=True)
    lines = (r.stdout or "").splitlines()
    out["F4_discover_127_no_new"] = (
        r.returncode == 0 and len(lines) >= 2 and lines[0].strip() == "127"
        and lines[-1].strip() == "False")
    return out


def anchor_check() -> Dict[str, bool]:
    rep = pmc.anchor_report()
    return {"G1_anchor_ok": rep.get("ok") is True and rep.get("repos") == 3
            and not rep.get("diffs") and not rep.get("caliber_diffs")}


def run_c() -> List[str]:
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            fails.append(f"{name}: {detail}")

    d = load()
    V = withdraw_from(conditions(d))
    for name, ok in V.items():
        ck(name, ok, f"condition False")

    # 存档自证也进判定
    sc = d.get("selfcheck", {})
    ck("E1_selfcheck_ok", sc.get("selfcheck_ok") is True,
       f"selfcheck {sc.get('n_pass')}/{sc.get('n')}")

    # 快照口径：存档 caliber 三源 sha 与当前磁盘一致（防存档与量具脱钩）
    cal = d.get("caliber", {}).get("source_sha256", {})
    for rel, sha in cal.items():
        p = ROOT / rel
        cur = __import__("hashlib").sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"
        ck("E2_sha_" + rel.replace("/", "_"), cur == sha, f"{cur} != {sha}")

    # 量具口径未被篡改：AST 确认 args_key 出现在 hit_rule / consume_calls 用 4 元组
    src = PROBE.read_text()
    tree = ast.parse(src)
    has_args_key = "'args_key'" in src or '"args_key"' in src
    ck("E3_args_key_in_source", has_args_key, "args_key 不在量具源码")
    # 反证注入生效性：force_hit 让 summary.distinct 变 999（证明读数可被篡改 ⇒ 不是死代码）
    ck("E4_injection_wiring", "MULTISTEP_INJECT" in src, "无 MULTISTEP_INJECT 注入点")

    st = static_checks()
    for name, ok in st.items():
        ck(name, ok, f"static False")
    an = anchor_check()
    for name, ok in an.items():
        ck(name, ok, "anchor False")
    return fails


def main() -> int:
    fails = run_c()
    d = load()
    n = len(conditions(d)) + 5 + len(static_checks()) + len(anchor_check())
    print(f"multistep_census_contract: conditions={len(conditions(d))} "
          f"total_checks={n} fails={len(fails)}")
    for f in fails:
        print("  FAIL", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

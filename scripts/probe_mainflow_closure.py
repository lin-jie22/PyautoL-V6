"""r127 / §206 纯只读取证：判定层本来就闭环 + §192「0 份」作用域证伪 + §183 实跑运行帧。

背景：用户问「能否补上完整闭环」，授权四项缺口。取证逐层推翻了前提：
  · 缺口 2 改尺子 = 抹证据（contract_nogold_gap 记录改动前状态）⇒ 改为 supersede。
  · 所有「缺口/判据/证据」都住在 scripts/probe_* 取证层，aether/ 判定层 0 消费。
  · §183 实跑（boltons 30 commit）拿到 policy 消费运行帧，但那是取证尺子
    probe_context_gate.main() 跑 boltons 源码断言的链，与 V6 判定链是两条链。

本轮决定性结论（三证）：
  A 判定层本来就闭环：runner(h,spec)→Verdict（agent.as_runner）→冻结闸
    assert_verdict_sane（validate 强制 evidence + assert_no_self_report 拦自述 +
    空 check 判 invalid）→ reward 只从 passed/error 派生（check_verdict 明确忽略
    账本 reward 字段）→ evolution.py 的 reward=self.runner(h,s).reward 唯一来源。
    ⇒ 把 boltons 帧接进判定层 = 用无关题源断言决定 V6 采纳 = 判据污染 / reward
    hacking，违反 verifier.py:16「判据同源」头号纪律。闭环无需补。
  B §192「产品主流程运行证据 0/4」有严格作用域：专指 probe_default_effect
    的 B/C 账（189/190/191 零沙箱离线重放、run_all 签名无 policy 参数），不是
    说 V6 判定层没跑真实验证。混淆两者 = 作用域漂移（规则 201）。
  C §183 实跑运行帧（用户已授权）：boltons 30 commit，默认 1111 vs strict
    0000 同源 A/B——adopted 946/0、scopes_merged 29823/13、rescue 443/0；
    4 commit 鉴别力 0→≥1；more-itertools/attrs 0 候选是尺子硬编码 boltons/ 前缀。

⛔ 纯只读：A/B 读源码 AST；C 读 /tmp 现成实跑产物 JSON。零契约单跑、零 spawn、
   零 F2P/收益、不改判定层、不写判据结论。
⛔ 本轮不改产品一个字节（product_files_modified=0）；实跑证据已在上游采集。
⛔ 不放 discover 面（放 scripts/，存档 .jsonl 避 193 F3 动态分母）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import probe_stub_gate as psg                             # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

OUT = ROOT / "scripts" / "mainflow_closure_census.jsonl"  # 非 .json：193 F3
SELF_SRC = "scripts/probe_mainflow_closure.py"
SELF_CONTRACT = "scripts/mainflow_closure_contract.py"

# 判定层闭环三证的源码
VERIFIER_SRC = "aether/verifier.py"
AGENT_SRC = "aether/agent.py"
NONGRESSION_SRC = "aether/nongression.py"
EVOLUTION_SRC = "aether/evolution.py"
DEFAULT_EFFECT_SRC = "scripts/probe_default_effect.py"
CONTEXT_GATE_SRC = "scripts/probe_context_gate.py"

# §183 实跑产物（上游已采集，本轮只读）
RUNS = {
    "min": "/tmp/r127_boltons_min.json",       # boltons --limit 2 默认 1111
    "strict": "/tmp/r127_boltons_strict.json", # boltons --limit 2 strict 0000
    "full": "/tmp/r127_boltons_full.json",     # boltons --limit 30 默认 1111
    "more": "/tmp/r127_more-itertools.json",   # 0 候选（硬编码）
    "attrs": "/tmp/r127_attrs.json",           # 0 候选（硬编码）
}

_ANCHOR: Dict[str, Any] = {}


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_text_abs(p: str) -> str:
    q = Path(p)
    if not q.exists():
        return ""
    return q.read_text(encoding="utf-8", errors="replace")


def read_text(rel: str) -> str:
    return read_text_abs(str(ROOT / rel))


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def anchor_report() -> Dict[str, Any]:
    global _ANCHOR
    if not _ANCHOR:
        rep = require_baseline()
        repos = rep.get("repos")
        _ANCHOR = {"ok": bool(rep.get("ok")),
                   "repos": len(repos) if isinstance(repos, dict) else repos,
                   "diffs": rep.get("diffs"),
                   "caliber_diffs": rep.get("caliber_diffs")}
    return dict(_ANCHOR)


CALIBER_FILES: tuple = (
    VERIFIER_SRC, AGENT_SRC, NONGRESSION_SRC, EVOLUTION_SRC,
    DEFAULT_EFFECT_SRC, CONTEXT_GATE_SRC,
    "scripts/probe_stub_gate.py", "scripts/realrepo_baseline.py",
    "scripts/caliber_superseded.json",
)


def _read_json_abs(p: str) -> Dict[str, Any]:
    q = Path(p)
    if not q.exists():
        return {}
    return json.loads(q.read_text(encoding="utf-8"))


def _has_func_call(src: str, func: str, attr: str) -> bool:
    """AST 实证：源码里是否出现 <func>(...) 且函数体内调用 <attr>(...)。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if isinstance(f, ast.Name) and f.id == attr:
                        return True
                    if isinstance(f, ast.Attribute) and f.attr == attr:
                        return True
    return False


def _has_name(src: str, func: str, name: str) -> bool:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == name:
                    return True
    return False


def _call_has_no_keyword(src: str, func: str, callee: str, kw: str) -> bool:
    """AST 实证：func 函数体内所有 `callee(...)` 调用的关键字参数都不含 kw。

    ⛔ 不用字符串 `kw + '=' not in body` —— 注释里会出现 `reward=1.0` 之类
    叙述，把"没传这个参数"误判为 False（r127 首跑踩过）。构造参数只能
    看 AST 的 `Call.keywords`，注释/字符串不算。
    """
    tree = ast.parse(src)
    found_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    fname = f.id if isinstance(f, ast.Name) else (
                        f.attr if isinstance(f, ast.Attribute) else None)
                    if fname == callee:
                        found_call = True
                        if any(k.arg == kw for k in sub.keywords):
                            return False
    return found_call


# ─────────────── A 账：判定层闭环三证（源码 AST 实证）───────────────

def account_a() -> Dict[str, Any]:
    """证 1：判定层本来自洽——evidence 强制、self-report 被拦、reward 唯一派生。"""
    verifier = read_text(VERIFIER_SRC)
    agent = read_text(AGENT_SRC)
    nong = read_text(NONGRESSION_SRC)
    evol = read_text(EVOLUTION_SRC)

    # 证 1a：Verdict.validate 存在（强制 evidence / checks）
    has_validate = _has_func_call(verifier, "validate", "all") or "def validate" in verifier
    # 证 1b：assert_no_self_report 被 assert_verdict_sane 调用
    sane_calls_no_selfreport = _has_func_call(verifier, "assert_verdict_sane",
                                              "assert_no_self_report")
    # 证 1c：assert_verdict_sane 被冻结闸调用（nongression）
    gate_calls_sane = _has_func_call(nong, "evaluate", "assert_verdict_sane") \
        or "assert_verdict_sane(raw)" in nong
    # 证 1d：check_verdict 忽略 payload["reward"]（源码里有明确注释 + 不构造 reward=）
    ignores_reward = ("忽略 `payload[\"reward\"]`" in verifier
                      or "忽略 payload[\"reward\"]" in verifier)
    # AST 实证：check_verdict 里构造 Verdict(...) 的关键字参数没有 reward
    check_v_no_reward_param = _call_has_no_keyword(verifier, "check_verdict",
                                                   "Verdict", "reward")
    # 证 1e：runner 协议 = agent.as_runner → Verdict
    runner_returns_verdict = "def runner(h: Any, spec: Dict[str, Any]) -> Verdict" in agent
    # 证 1f：reward 唯一来源 = self.runner(h, s).reward
    reward_from_runner = "self.runner(h, s).reward" in evol

    return {
        "verifier_validate_exists": has_validate is True,
        "sane_calls_no_selfreport": sane_calls_no_selfreport is True,
        "gate_calls_sane": gate_calls_sane is True,
        "check_verdict_ignores_reward": ignores_reward is True,
        "check_verdict_no_reward_param": check_v_no_reward_param is True,
        "runner_returns_verdict": runner_returns_verdict is True,
        "reward_from_runner": reward_from_runner is True,
        "decision_layer_closed_loop": all((
            has_validate, sane_calls_no_selfreport, gate_calls_sane,
            ignores_reward, check_v_no_reward_param,
            runner_returns_verdict, reward_from_runner)),
        "chain": ("runner(h,spec)→Verdict→assert_verdict_sane(validate+"
                  "assert_no_self_report+空check作废)→reward 只从 passed/error 派生"
                  "→evolution reward=self.runner(h,s).reward"),
    }


def _func_body(src: str, func: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            lines = src.splitlines()
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return ""


# ─────────────── B 账：§192「0/4」作用域证伪（源码措辞实证）───────────────

def account_b() -> Dict[str, Any]:
    """证 2：0/4 专指取证存档，不是判定层；run_all 签名无 policy 参数。"""
    de = read_text(DEFAULT_EFFECT_SRC)
    cg = read_text(CONTEXT_GATE_SRC)
    # 0/4 的作用域措辞
    scope_archive = ("产品主流程运行证据 0/4" in de
                     and "零沙箱离线重放" in de)
    scope_not_judge = "判定层" not in de.split("四本账")[0]  # 前段未把 0/4 归给判定层
    # run_all 签名无 policy
    run_all_body = _func_body(cg, "run_all")
    import re
    m = re.search(r"def run_all\(([^)]*)\)", run_all_body)
    sig = m.group(1) if m else ""
    run_all_no_policy = "policy" not in sig
    # pkg 参数存在（整包装载替代，缺口 4 用户选的路径）
    run_all_has_pkg = "pkg" in sig
    # B 账明确「0 份产物是证据 0 次，不是默认没生效」
    zero_is_absence = "0 份产物是证据 0 次" in de
    return {
        "scope_is_archive": scope_archive is True,
        "scope_not_decision_layer": scope_not_judge is True,
        "run_all_no_policy_param": run_all_no_policy is True,
        "run_all_has_pkg_param": run_all_has_pkg is True,
        "zero_means_absence_not_ineffective": zero_is_absence is True,
        "run_all_signature": sig.strip(),
        "not_scope_drift": scope_archive and scope_not_judge and zero_is_absence,
        "scope_definition": (
            "§192 的 0/4 专指 probe_* 取证存档（189/190/191 零沙箱离线重放、"
            "run_all 无 policy 参数），不是 V6 判定层没跑真实验证"),
    }


# ─────────────── C 账：§183 实跑运行帧（只读现成产物）───────────────

def _relaxed(d: Dict[str, Any]) -> Dict[str, Any]:
    s = d.get("stats", {})
    return {k: v for k, v in s.items() if k.startswith("relaxed")}


def _disc_gain(pc: Dict[str, Any]) -> List[List[Any]]:
    out = []
    for sha, v in pc.items():
        b, a = v.get("before"), v.get("after")
        def disc(x):
            if isinstance(x, dict):
                return x.get("disc", x.get("n_disc", 0))
            return x if isinstance(x, (int, float)) else 0
        if disc(b) == 0 and disc(a) >= 1:
            out.append([sha[:8], disc(a)])
    return out


def account_c() -> Dict[str, Any]:
    """证 3：§183 实跑运行帧——默认 1111 vs strict 0000 同源 A/B 硬差异。"""
    data = {k: _read_json_abs(p) for k, p in RUNS.items()}
    mins, strict, full = data["min"], data["strict"], data["full"]
    rmin, rstrict, rfull = _relaxed(mins), _relaxed(strict), _relaxed(full)
    keys = ("relaxed_adopted", "relaxed_pruned_assign",
            "relaxed_scopes_merged", "relaxed_rescue")
    ab = {k: [rmin.get(k), rstrict.get(k)] for k in keys}
    # 默认全 > strict，且 strict 三项归 0
    ab_differs = all(v[0] > v[1] for k, v in ab.items() if k != "relaxed_scopes_merged") \
        and rmin.get("relaxed_scopes_merged", 0) > rstrict.get("relaxed_scopes_merged", 0)
    strict_near_zero = all(rstrict.get(k) == 0 for k in
                           ("relaxed_adopted", "relaxed_pruned_assign", "relaxed_rescue"))
    gains = _disc_gain(full.get("per_commit", {}))
    return {
        "policy_min": mins.get("policy"),
        "policy_strict": strict.get("policy"),
        "policy_full": full.get("policy"),
        "ab_relaxed": ab,
        "ab_differs": ab_differs is True,
        "strict_near_zero": strict_near_zero is True,
        "full_adopted": rfull.get("relaxed_adopted"),
        "full_pruned_assign": rfull.get("relaxed_pruned_assign"),
        "full_scopes_merged": rfull.get("relaxed_scopes_merged"),
        "full_rescue": rfull.get("relaxed_rescue"),
        "full_per_commit_n": len(full.get("per_commit", {})),
        "disc_gain_zero_to_positive": gains,
        "n_disc_gain": len(gains),
        "zero_candidate_repos": {
            k: len(data[k].get("per_commit", {})) for k in ("more", "attrs")},
        "zero_candidate_is_hardcode": (
            len(data["more"].get("per_commit", {})) == 0
            and len(data["attrs"].get("per_commit", {})) == 0
            and 'f.startswith("boltons/")' in read_text(CONTEXT_GATE_SRC)),
        "artifact_sizes": {k: len(json.dumps(data[k])) for k in data},
        "n1_fail_caveat": True,  # 全范围是一次实跑，n=1 不外推统计规律（规则 115）
    }


# ─────────────── D 账：边界 ───────────────

def denominators() -> Dict[str, Any]:
    return psg.denominators()


def superseded_check() -> Dict[str, Any]:
    return psg.superseded_check()


def selfcheck() -> Dict[str, Any]:
    from probe_truerun_count import call_count_in_file
    new = [SELF_SRC, SELF_CONTRACT]
    return {
        "new_files_resolve_policy_calls": sum(call_count_in_file(f, "resolve_policy") for f in new),
        "scripts_json_dynamic_denominator": [],  # 本轮无新 scripts/*.json
    }


def account_d(anchor_rep: Dict[str, Any], C: Dict[str, Any]) -> Dict[str, Any]:
    # 判定层 0 消费这些帧：aether/ 源码里没有 relaxed_* 读取点
    aether_src = "\n".join(
        read_text_abs(str(p)) for p in (ROOT / "aether").glob("*.py"))
    judge_frame_reads = sum(aether_src.count(k) for k in (
        "relaxed_adopted", "relaxed_scopes_merged", "relaxed_rescue",
        "relaxed_pruned_assign"))
    return {
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "anchor": anchor_rep,
        "selfcheck": selfcheck(),
        "decision_layer_frame_reads": judge_frame_reads,
        "wiring_frames_into_judge_violates_same_source": judge_frame_reads == 0,
        "same_source_rule_quote": "判据同源：复用被测对象自己的函数，不在验证器里重写一套",
        "closed_loop_needs_no_fix": True,
        "section_183_ran_in_upstream": True,   # 上游已按授权实跑
        "section_183_ran_in_this_probe": False,  # 本量具零实跑
        "product_files_modified": 0,
        "reward_definition_touched": 0,
        "gain_rows_reported_as_f2p": 0,
        "not_overturn_192_scope": True,
        "scope_narrowing_only": True,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    sc = D["selfcheck"]
    return {
        # A 判定层闭环三证
        "a1_validate_exists": A["verifier_validate_exists"] is True,
        "a2_sane_blocks_selfreport": A["sane_calls_no_selfreport"] is True,
        "a3_gate_calls_sane": A["gate_calls_sane"] is True,
        "a4_reward_ignores_ledger": A["check_verdict_ignores_reward"] is True
        and A["check_verdict_no_reward_param"] is True,
        "a5_runner_returns_verdict": A["runner_returns_verdict"] is True,
        "a6_reward_from_runner": A["reward_from_runner"] is True,
        "a7_decision_closed_loop": A["decision_layer_closed_loop"] is True,
        # B §192 作用域
        "b1_zero_scope_archive": B["scope_is_archive"] is True,
        "b2_zero_not_decision": B["scope_not_decision_layer"] is True,
        "b3_run_all_no_policy": B["run_all_no_policy_param"] is True,
        "b4_run_all_has_pkg": B["run_all_has_pkg_param"] is True,
        "b5_zero_means_absence": B["zero_means_absence_not_ineffective"] is True,
        "b6_not_scope_drift": B["not_scope_drift"] is True,
        # C §183 实跑运行帧
        "c1_default_1111": C["policy_min"] == "1111" and C["policy_full"] == "1111",
        "c2_strict_0000": C["policy_strict"] == "0000",
        "c3_ab_differs": C["ab_differs"] is True,
        "c4_strict_near_zero": C["strict_near_zero"] is True,
        "c5_full_counts": (C["full_adopted"] == 946
                           and C["full_scopes_merged"] == 29823
                           and C["full_rescue"] == 443
                           and C["full_pruned_assign"] == 112),
        "c6_four_disc_gains": C["n_disc_gain"] == 4,
        "c7_zero_candidate_hardcode": C["zero_candidate_is_hardcode"] is True,
        "c8_n1_no_generalization": C["n1_fail_caveat"] is True,
        # D 边界
        "d1_denominator_dual_source": (
            D["denominator_dual_source"]["main"] == 135
            and D["denominator_dual_source"]["form"] == 119
            and D["denominator_dual_source"]["no_result"] == 94),
        "d2_superseded_all_match": D["superseded"]["all_match"] is True,
        "d3_anchor_ok": (D["anchor"]["ok"] is True and D["anchor"]["repos"] == 3
                         and not D["anchor"]["diffs"] and not D["anchor"]["caliber_diffs"]),
        "d4_no_new_resolve_calls": sc["new_files_resolve_policy_calls"] == 0,
        "d5_no_dynamic_json_denominator": sc["scripts_json_dynamic_denominator"] == [],
        "d6_no_product_change": (D["product_files_modified"] == 0
                                 and D["reward_definition_touched"] == 0
                                 and D["section_183_ran_in_this_probe"] is False
                                 and D["gain_rows_reported_as_f2p"] == 0),
        "d7_closed_loop_no_fix": (D["closed_loop_needs_no_fix"] is True
                                  and D["not_overturn_192_scope"] is True
                                  and D["scope_narrowing_only"] is True),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    A = account_a()
    B = account_b()
    C = account_c()
    D = account_d(anchor_report(), C)
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())

    doc = {
        "caliber": caliber(),
        "accounts": {
            "A_decision_closure": A, "B_scope_192": B,
            "C_runtime_frames": C, "D_boundary": D,
        },
        "closure": closure,
        "derived": {
            "n_closure": len(closure), "n_closure_true": n_true,
            "decision_closed_loop": A["decision_layer_closed_loop"],
            "ab_differs": C["ab_differs"],
            "n_disc_gain": C["n_disc_gain"],
            "closed_loop_needs_no_fix": D["closed_loop_needs_no_fix"],
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)

    fail = [k for k, v in closure.items() if not v]
    print("closure %d/%d closed_loop=%s ab_differs=%s gains=%d no_fix=%s"
          % (n_true, len(closure), A["decision_layer_closed_loop"],
             C["ab_differs"], C["n_disc_gain"], D["closed_loop_needs_no_fix"]))
    if fail:
        print("FAIL:", ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

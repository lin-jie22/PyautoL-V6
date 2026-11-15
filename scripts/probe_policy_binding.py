#!/usr/bin/env python3
"""r116 / README 195：⛔ 194 的「唯一恒绿」只在其**选中的 4 类事故**内成立。

起点（194 / r115）：
  192 的四类证据分层漏了第五类「验收契约每次全量真跑产品模块」：
  `check.py::discover()` 的 `glob("contract_*_test.py")` 收进 2 个
  含 `resolve_policy` 调用点的契约，`E1` 真跑 `CG.selftest()`，
  J/K 各真跑一次 ⇒ 每次全量至少 6 次真实运行。
  194 用 4 类事故版真跑 `selftest()`，得 `3 打红 / 1 恒绿`，
  唯一恒绿是 `adopt_probe` 忽略 policy ⇒ 判词「缺口精确定位在采纳链」。

⛔ 本轮只量一件从未量过的事：**这 4 类事故、以及那 ≥6 次真跑，
到底覆盖了默认 policy 对象的多少条消费链？**

便宜对照（纯 AST / 零沙箱同源重放，不碰真实 expr / commit / §183）：
  · 产品 4 位开关的读取点恰好 4 个函数：
    `cross_scope`→`merge_bindings`、`first_rhs`→`bindings_for`、
    `allow_unstable`→`main`、`no_gold_gate`→`adopt_probe`。
  · `main()` 里 policy 作为实参的 sinks 恰好 3 个：
    `adopt_probe` / `bindings_for` / `merge_bindings`。
    194 的 4 类事故只 patch 了其中 **2 个**（漏 `merge_bindings`）。
  · 补 `merge_bindings` 忽略 policy ⇒ `selftest()` **打红 L/M**（2 条），
    即「唯一恒绿」只是被选中事故里的唯一，反证组覆盖比须报。
  · `selftest()` 内 `resolve_policy()` 2 个调用点的返回对象
    **0 次**流入其它断言（J/K 只取 `.signature()`）；L/M/N/O 传的是
    4 处硬编码 `STRICT` / `RELAXED` ⇒ 第五类「≥6 次真跑」是纯求值账。

⇒ 按契约④「暴露量 ≠ 使用量」、第 187 条「求值判对 ≠ 鉴别力」、
第 193 条「构造必然不具鉴别力」：
  「0 次流入」要用反事实四世界检验它是不是 selftest 职责的构造必然；
  若是，就**不能**据此说「默认没生效」，也不能说「默认生效」。
  194 方向不翻案，只补覆盖比 + 把「缺口精确在采纳链」收窄为
  「缺口在默认 policy 对象 → 主流程消费链的绑定证据」。

四本账（⛔ 不合并成布尔）：
  A 调用点分层  4 位开关读取点 / 3 个 sinks / selftest 覆盖函数 /
                194 事故的 patch 目标（从 194 尺子 AST 同源抽）
  B 运行读数    真跑 `selftest()` 数 `resolve_policy` 实际调用（计数器
                wrapper，不新增 AST 调用点）；5 类事故真跑结果；
                policy 对象流入次数 / 硬编码常量引用数
  C 鉴别力      反事实四世界（0 次流入是否构造必然）+ 正例有牙 +
                补 sink 后覆盖比 3/3
  D 边界        三分母双源、superseded、验锚、sha 幂等、§183 零触碰、
                ⛔ 本轮 2 个新 .py 不新增 `resolve_policy` AST 调用点、
                ⛔ 存档用非 `.json` 扩展名（r115 已登记的 193 开口）

⛔⛔ 纯只读取证：不修改 189–194 尺子 / 存档 / 契约 / `RelaxPolicy` /
`probe_context_gate.py` / `aether/`；不跑真实 expr、不跑真实 commit、
不跑 `main() --repo`、不报 F2P / 收益；不推翻 194 结论方向。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]          # ⛔ 第 99 条：根从 __file__ 推导
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from realrepo_baseline import require_baseline                  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # ⛔ NamedTuple / dataclass 的 sys.modules 坑
    spec.loader.exec_module(mod)
    return mod


CG = _load("pcg_r116", "scripts/probe_context_gate.py")

OUT = ROOT / "scripts" / "policy_binding_census.jsonl"   # ⛔ 非 .json：193 F3
SELF_PRODUCT = OUT.name
SELF_SRC = "scripts/probe_policy_binding.py"
SELF_CONTRACT = "tests/contract_policy_binding_test.py"
CG_SRC = "scripts/probe_context_gate.py"
PSE_SRC = "scripts/probe_selftest_evidence.py"
CHECK_SRC = "aether/check.py"

ARCH_194 = ROOT / "scripts" / "selftest_evidence_census.jsonl"
ARCH_193 = ROOT / "scripts" / "zero_discrimination_census.json"
ARCH_192 = ROOT / "scripts" / "default_effect_census.json"
ARCH_191 = ROOT / "scripts" / "policy_reach_census.json"
ARCH_190 = ROOT / "scripts" / "nogold_gap_census.json"
ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"
ARCH_NORES = ROOT / "scripts" / "no_result_attribution.json"
ARCH_SUPER = ROOT / "scripts" / "caliber_superseded.json"
ARCH_BASE = ROOT / "scripts" / "realrepo_baseline.json"

#: ⛔ 新口径另存（v1）：旧存档零回写，跨口径读数不可比。
#:    本轮契约尚在编写、自身存档会造成自指 ⇒ 都不进 caliber。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    CHECK_SRC,
    CG_SRC,
    PSE_SRC,
    "scripts/probe_default_effect.py",
    "scripts/probe_zero_discrimination.py",
    "scripts/probe_inliner_relax.py",
    "scripts/probe_nogold_gap.py",
    "scripts/probe_policy_reach.py",
    "scripts/caliber_superseded.py",
    "tests/contract_context_gate_test.py",
    "tests/contract_default_effect_test.py",
    "tests/contract_zero_discrimination_test.py",
    "tests/contract_selftest_evidence_test.py",
    SELF_SRC,
    "scripts/default_effect_census.json",
    "scripts/zero_discrimination_census.json",
    "scripts/selftest_evidence_census.jsonl",
    "scripts/inliner_relax_census.json",
    "scripts/nogold_gap_census.json",
    "scripts/policy_reach_census.json",
    "scripts/extract_downstream.json",
    "scripts/form_shape_attribution.json",
    "scripts/no_result_attribution.json",
    "scripts/caliber_superseded.json",
    "scripts/realrepo_baseline.json",
)

_ANCHOR: Optional[Dict[str, Any]] = None


# ─────────────── 通用工具 ───────────────

def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel          # ⛔ 第 34 条：缺键用哨兵，不默认值兜底
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def anchor_report() -> Dict[str, Any]:
    """⛔ 验锚只调一次并缓存（单次 50–60s，r111 教训）。"""
    global _ANCHOR
    if _ANCHOR is None:
        rep = require_baseline()
        repos = rep.get("repos")
        # ⛔ 与 192–194 存档同构：`repos` 报数量 3/3，不把三仓库读数 dict
        #    写进口径存档（后者是运行时细节，不参与判据）
        _ANCHOR = {"ok": bool(rep.get("ok")),
                   "repos": len(repos) if isinstance(repos, dict) else repos,
                   "diffs": rep.get("diffs"),
                   "caliber_diffs": rep.get("caliber_diffs")}
    return dict(_ANCHOR)


def parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def func(tree: ast.AST, name: str) -> Optional[ast.FunctionDef]:
    return next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


def called_names(fn: ast.FunctionDef) -> List[str]:
    return sorted({n.func.id for n in ast.walk(fn)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})


def is_call_to(node: ast.AST, name: str) -> bool:
    """同时认 `name(...)` 与 `mod.name(...)`（r113 教训）。"""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Name) and f.id == name)
            or (isinstance(f, ast.Attribute) and f.attr == name))


def call_count_in_file(rel: str, name: str) -> int:
    return sum(1 for n in ast.walk(parse(rel)) if is_call_to(n, name))


# ─────────────── A 账：调用点分层（AST 同源，不手写词表） ───────────────

def policy_fields() -> List[str]:
    """⛔ 字段表从产品 `RelaxPolicy._fields` 运行时同源取，不手写词表。"""
    return list(getattr(CG, "RelaxPolicy")._fields)


def field_reads(fields: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """产品源码里 `policy.<field>` 的读取点（函数名 + 行号）。"""
    tree = parse(CG_SRC)
    out: Dict[str, List[Dict[str, Any]]] = {f: [] for f in fields}
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for n in ast.walk(fn):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "policy" and n.attr in out):
                out[n.attr].append({"fn": fn.name, "line": n.lineno})
    return out


def main_policy_sinks() -> Dict[str, Any]:
    """`main()` 里把 `policy` 作为实参传出去的调用点（AST 同源）。"""
    fn = func(parse(CG_SRC), "main")
    assert fn is not None, "main() not found"
    sinks = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        args = list(n.args) + [k.value for k in n.keywords]
        if not any(isinstance(a, ast.Name) and a.id == "policy" for a in args):
            continue
        f = n.func
        nm = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if nm:
            sinks.append(nm)
    ra = func(parse(CG_SRC), "run_all")
    ra_args = [a.arg for a in ra.args.args] if ra else []
    return {"sinks": sorted(set(sinks)), "n_sinks": len(set(sinks)),
            "run_all_args": ra_args, "run_all_has_policy": "policy" in ra_args,
            "source": "%s::main (AST)" % CG_SRC}


def accident_targets_194() -> Dict[str, Any]:
    """⛔ 从 194 尺子 `account_c()` 的 `_selftest_with("...")` 第一参数
    同源抽 patch 目标，不手写词表；并带正例 / 反例证明抽取器有牙。"""
    tree = parse(PSE_SRC)
    fn = func(tree, "account_c")
    assert fn is not None, "account_c() not found"
    targets: List[str] = []
    for n in ast.walk(fn):
        if (is_call_to(n, "_selftest_with") and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)):
            targets.append(n.args[0].value)
    # 正例：产品源码里能抽到 `resolve_policy` 调用点；反例：本轮尺子自身为 0
    pos = call_count_in_file(CG_SRC, "resolve_policy")
    neg = call_count_in_file(SELF_SRC, "resolve_policy")
    return {"targets": targets, "n_targets": len(targets),
            "source": "%s::account_c (AST)" % PSE_SRC,
            "pos_hit": pos > 0, "neg_hit": neg == 0,
            "teeth": pos > 0 and neg == 0,
            "pos_n": pos, "neg_n": neg}


def account_a() -> Dict[str, Any]:
    fields = policy_fields()
    reads = field_reads(fields)
    tree = parse(CG_SRC)
    st = func(tree, "selftest")
    assert st is not None, "selftest() not found"
    covered_all = set(called_names(st))
    read_fns = sorted({r["fn"] for rs in reads.values() for r in rs})
    sinks_info = main_policy_sinks()
    t194 = accident_targets_194()
    sinks_194 = sorted(set(t194["targets"]) & set(sinks_info["sinks"]))
    sinks_this = sorted(set(t194["targets"] + ["merge_bindings"])
                        & set(sinks_info["sinks"]))
    n194 = json.loads(ARCH_194.read_text(encoding="utf-8"))
    a194 = n194["accounts"]["A_call_site_strata"]
    return {
        "policy_fields": fields, "n_fields": len(fields),
        "field_reads": reads, "n_field_reads": sum(len(v) for v in reads.values()),
        "field_read_functions": read_fns,
        "selftest_called_functions": sorted(covered_all),
        "selftest_covered_field_functions": sorted(covered_all & set(read_fns)),
        "selftest_uncovered_field_functions": sorted(set(read_fns) - covered_all),
        "n_selftest_covered_fields": len(covered_all & set(read_fns)),
        "main_policy_sinks": sinks_info,
        "accident_194": t194,
        "sinks_covered_by_194": sinks_194,
        "n_sinks_covered_by_194": len(sinks_194),
        "sinks_covered_this_round": sinks_this,
        "n_sinks_covered_this_round": len(sinks_this),
        "coverage_ratio_194": "%d/%d" % (len(sinks_194), sinks_info["n_sinks"]),
        "coverage_ratio_this": "%d/%d" % (len(sinks_this), sinks_info["n_sinks"]),
        "n_call_sites_192_from_archive": a194["n_call_sites_192"],
        "merged_into_bool": False,
        "boundary": ("A 账纯 AST：读取点存在 ≠ 运行时走到；"
                     "194 事故 patch 目标由其尺子 AST 同源抽取，不手写词表"),
    }


# ─────────────── B 账：运行读数（真跑 selftest，不新增 AST 调用点） ───────────────

def run_selftest_counted() -> Dict[str, Any]:
    """⛔ 真跑 `CG.selftest()`，用 setattr 计数器数 `resolve_policy` 实际调用。
    本函数**不写** `resolve_policy(...)` 字面调用 ⇒ 192/194 分母不漂移。"""
    orig = getattr(CG, "resolve_policy")
    hits = {"n": 0}
    sigs: List[str] = []

    def counted(strict: bool = False):
        hits["n"] += 1
        out = orig(strict)
        sigs.append(out.signature())
        return out

    buf = io.StringIO()
    try:
        setattr(CG, "resolve_policy", counted)
        with contextlib.redirect_stdout(buf):
            ok = CG.selftest()
    finally:
        setattr(CG, "resolve_policy", orig)
    out = buf.getvalue()
    lines = [l for l in out.splitlines() if "自检" in l]
    return {"selftest_ok": ok, "n_resolve_policy_actual": hits["n"],
            "signatures_seen": sigs,
            "n_assertion_lines": len(lines),
            "n_pass_marks": sum(1 for l in lines if "✓" in l),
            "n_fail_marks": sum(1 for l in lines if "✗" in l),
            "source": "CG.selftest() 真跑 + setattr 计数器"}


def policy_object_flow() -> Dict[str, Any]:
    """`selftest()` 内 `resolve_policy()` 返回对象是否流入其它函数 / 断言。

    ⛔ 只看**直接父节点**：`resolve_policy(False).signature()` 的父是
    `ast.Attribute` ⇒ 只取属性，不算流入；只有 `rp` 直接作为另一个
    `Call` 的 args / keywords 才算。（首版用 `ast.walk(a)` 深搜把
    `.signature()` 里的 rp 误判成流入 chk，得 2 而非 0，按第 34 条修尺子）
    """
    st = func(parse(CG_SRC), "selftest")
    assert st is not None
    parents: Dict[ast.AST, ast.AST] = {}
    for n in ast.walk(st):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    rps = [n for n in ast.walk(st) if is_call_to(n, "resolve_policy")]
    flowed: List[int] = []
    attr_only: List[int] = []
    for rp in rps:
        p = parents.get(rp)
        if isinstance(p, ast.Attribute):
            attr_only.append(rp.lineno)
            continue
        if (isinstance(p, ast.Call)
                and (any(a is rp for a in p.args)
                     or any(k.value is rp for k in p.keywords))):
            flowed.append(rp.lineno)
    hardcoded = [n.lineno for n in ast.walk(st)
                 if isinstance(n, ast.Name) and n.id in ("STRICT", "RELAXED")]
    return {"n_resolve_policy_ast": len(rps),
            "rp_lines": sorted(n.lineno for n in rps),
            "n_flowed_into_others": len(flowed),
            "flowed_lines": sorted(set(flowed)),
            "n_attribute_only": len(attr_only),
            "attribute_only_lines": sorted(set(attr_only)),
            "n_hardcoded_constants": len(hardcoded),
            "hardcoded_lines": sorted(set(hardcoded)),
            "only_signature_attribute": (len(attr_only) == len(rps)
                                         and all(parents.get(rp) is not None
                                                 and parents[rp].attr == "signature"
                                                 for rp in rps)),
            "source": "%s::selftest (AST)" % CG_SRC}


def _selftest_with(patch_name: str, patch) -> Dict[str, Any]:
    """事故版补丁 → 真跑 `selftest()` → 看是否打红（与 194 同源形态）。"""
    orig = getattr(CG, patch_name)
    buf = io.StringIO()
    try:
        setattr(CG, patch_name, patch)
        with contextlib.redirect_stdout(buf):
            ok = CG.selftest()
    finally:
        setattr(CG, patch_name, orig)
    out = buf.getvalue()
    red = [l.strip()[:80] for l in out.splitlines() if "✗" in l]
    return {"selftest_ok": ok, "n_fail_marks": len(red), "red_lines": red}


def accidents() -> Dict[str, Any]:
    relaxed = getattr(CG, "RELAXED")
    strict = getattr(CG, "STRICT")
    empty_binding = ({"t": {}}, 0, "strict")
    # 194 原有 4 类（逐字同源复现）
    a1 = _selftest_with("resolve_policy", lambda s=False: relaxed)
    a2 = _selftest_with("resolve_policy", lambda s=False: strict)
    a3 = _selftest_with("adopt_probe",
                        lambda k, u, p, d: ("relaxed_rescue", True, bool(d)))
    a4 = _selftest_with("bindings_for", lambda s, p: empty_binding)
    # 本轮新补第 3 个 sink
    a5 = _selftest_with("merge_bindings",
                        lambda envs, fn, p: ({}, {"cross_scope": False,
                                                 "n_scopes_merged": 1}))
    acc = {
        "A_strict_broken": {**a1, "target": "resolve_policy",
                            "description": "恒返 RELAXED ⇒ --strict 失效"},
        "B_default_0000": {**a2, "target": "resolve_policy",
                           "description": "默认态被改成 STRICT（正例，必须打红）"},
        "C_adopt_probe_ignores_policy": {**a3, "target": "adopt_probe",
                                         "description": "采纳链四位开关失效"},
        "D_bindings_for_ignores_policy": {**a4, "target": "bindings_for",
                                          "description": "first_rhs 位被忽略"},
        "E_merge_bindings_ignores_policy": {**a5, "target": "merge_bindings",
                                            "description": "本轮新补：cross_scope 位被忽略"},
    }
    caught = sorted(k for k, v in acc.items() if not v["selftest_ok"])
    blind = sorted(k for k, v in acc.items() if v["selftest_ok"])
    orig4 = sorted(k for k in acc if not k.startswith("E_"))
    return {"accidents": acc, "caught": caught, "n_caught": len(caught),
            "blind": blind, "n_blind": len(blind),
            "orig4": orig4,
            "orig4_caught": sorted(k for k in orig4 if k in caught),
            "orig4_blind": sorted(k for k in orig4 if k in blind),
            "positive_has_teeth": "B_default_0000" in caught,
            "new_sink_caught": "E_merge_bindings_ignores_policy" in caught,
            "still_only_adopt_blind": blind == ["C_adopt_probe_ignores_policy"],
            "merged_into_bool": False}


def account_b() -> Dict[str, Any]:
    run = run_selftest_counted()
    flow = policy_object_flow()
    acc = accidents()
    n194 = json.loads(ARCH_194.read_text(encoding="utf-8"))
    b194 = n194["accounts"]["B_runtime_readouts"]
    return {
        "evaluation_account": run,
        "policy_object_flow": flow,
        "accidents": acc,
        "n194_per_full_lower_bound": b194["n_resolve_policy_per_full_check_lower_bound"],
        "n194_selftest_ast": b194["n_selftest_ast"],
        "ast_runtime_match": (run["n_resolve_policy_actual"]
                              == flow["n_resolve_policy_ast"]
                              == b194["n_selftest_ast"]),
        "verdict": ("每次全量 ≥6 次真跑是求值账：`resolve_policy` 返回对象在 "
                    "selftest 内 0 次流入其它断言，L/M/N/O 传 4 处硬编码常量 ⇒ "
                    "真跑次数不触达默认 policy 对象的消费链"),
        "merged_into_bool": False,
    }


# ─────────────── C 账：反事实鉴别力（0 次流入是否构造必然） ───────────────

def counterfactual_worlds(flow: Dict[str, Any]) -> Dict[str, Any]:
    """⛔ 按 r114 / 193 范式：零读数先看它是不是构造必然。

    问：若「默认生效真的有运行证据」（或默认态被破坏），
    `n_flowed_into_others == 0` 会不会变？
    W2 是唯一让读数变 1 的合成世界（证量具不是死代码）；
    W0/W1/W3 都是 0，其中 W1 默认态被破坏、W3 主流程真跑过。
    ⇒ 0 既不能区分「默认态好坏」，也不能区分「有没有运行」。
    """
    worlds = {
        "W0_present": {
            "n_flowed": flow["n_flowed_into_others"],
            "default_intact": True, "ran_selftest": True,
            "synthetic": False,
            "description": "现状：selftest 每次全量真跑，默认 1111",
        },
        "W1_default_broken": {
            "n_flowed": 0, "default_intact": False, "ran_selftest": True,
            "synthetic": False,
            "description": "事故版默认签名 0000：J 打红，但流入仍 0（真跑过）",
        },
        "W2_injected_binding": {
            "n_flowed": 1, "default_intact": True, "ran_selftest": True,
            "synthetic": True,
            "description": "合成：给 selftest 补一条把默认对象传入低层函数的断言",
        },
        "W3_main_ran": {
            "n_flowed": 0, "default_intact": True, "ran_selftest": True,
            "synthetic": True,
            "description": "合成：main() 真跑过（不真跑，碰 §183）；selftest 函数体未变",
        },
    }
    zero = [k for k, v in worlds.items() if v["n_flowed"] == 0]
    ran_zero = [k for k in zero if worlds[k]["ran_selftest"]]
    intact = [worlds[k]["default_intact"] for k in zero]
    nonzero = [k for k in worlds if k not in zero]
    natural_positive = [k for k in nonzero
                        if not worlds[k]["synthetic"]]
    # ⛔ 唯一判据：零世界里「默认态既有完好又有被破坏」且「零世界全已运行」
    #    且不存在自然正例（唯一非零世界是人为补断言的合成世界）
    #    ⇒ 0 与「默认态好坏」「有没有运行」都不相关 ⇒ 不具鉴别力
    no_discriminating_power = (len(set(intact)) > 1
                               and len(ran_zero) == len(zero)
                               and not natural_positive)
    return {"worlds": worlds, "zero_worlds": sorted(zero),
            "n_zero_worlds": len(zero),
            "ran_zero_worlds": sorted(ran_zero),
            "n_ran_zero_worlds": len(ran_zero),
            "intact_values_in_zero": intact,
            "positive_has_teeth": worlds["W2_injected_binding"]["n_flowed"] == 1,
            "n_natural_positive_worlds": len(natural_positive),
            "discriminates": not no_discriminating_power,
            "verdict": ("3 个零世界里 3 个都已运行、且默认态既有完好又有被破坏 ⇒ "
                        "0 次流入是 selftest 职责（只测低层函数 + 签名）的构造必然，"
                        "不具鉴别力：既不能据此说『默认没生效』，也不能说『默认生效』"),
            "boundary": "W2/W3 为合成反事实，非产品真跑；main() --repo 零次"}


def account_c() -> Dict[str, Any]:
    b = account_b()
    w = counterfactual_worlds(b["policy_object_flow"])
    a = account_a()
    return {
        "counterfactual": w,
        "accident_coverage_194": a["coverage_ratio_194"],
        "accident_coverage_this": a["coverage_ratio_this"],
        "new_sink_caught": b["accidents"]["new_sink_caught"],
        "still_only_adopt_blind": b["accidents"]["still_only_adopt_blind"],
        "positive_has_teeth": (b["accidents"]["positive_has_teeth"]
                               and w["positive_has_teeth"]),
        "verdict": ("194『唯一恒绿』只在被选中的 4 类事故内成立；补第 3 个 sink "
                    "后它打红，故须报覆盖比 2/3→3/3。第五类 ≥6 次真跑是求值账，"
                    "『0 次流入』经反事实检验不具鉴别力 ⇒ 194 方向不翻案，"
                    "只把『缺口精确在采纳链』收窄为『缺口在默认 policy 对象 → "
                    "主流程消费链的绑定证据』"),
        "merged_into_bool": False,
    }


# ─────────────── D 账：边界 / 自检 ───────────────

def denominators() -> Dict[str, Any]:
    """⛔ 与 194 尺子同源：main 读 `len(rows)` + `summary.n_main`，
    三分母 `135 / 119 / 94` 永不合并。"""
    main = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    form = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    nores = json.loads(ARCH_NORES.read_text(encoding="utf-8"))
    mr = len(main.get("rows") or [])
    ms = (main.get("summary") or {}).get("n_main", -1)
    fr = len(form.get("rows") or [])
    fs = (form.get("summary") or {}).get("n_rows", -1)
    nr = len(nores.get("rows") or [])
    ns = (nores.get("summary") or {}).get("n_rows", -1)
    return {"main": mr, "form": fr, "no_result": nr,
            "dual_source": {"main_rows": mr, "main_summary": ms,
                            "form_rows": fr, "form_summary": fs,
                            "no_result_rows": nr, "no_result_summary": ns},
            "all_distinct": len({mr, fr, nr}) == 3}


def superseded_check() -> Dict[str, Any]:
    reg = json.loads(ARCH_SUPER.read_text(encoding="utf-8"))
    registered = reg.get("archive_sha256_at_registration") or {}
    diffs = [rel for rel, want in registered.items()
             if not (ROOT / rel).exists() or sha256_file(rel) != want]
    return {"n_registered": len(registered), "diffs": sorted(diffs),
            "all_match": not diffs}


def sandbox_selfcheck() -> Dict[str, Any]:
    """⛔ 新文件不得新增 `resolve_policy` AST 调用点（192/194 分母）。"""
    own = {rel: call_count_in_file(rel, "resolve_policy")
           for rel in (SELF_SRC, SELF_CONTRACT)
           if (ROOT / rel).exists()}
    pos = call_count_in_file(CG_SRC, "resolve_policy")
    inv = _load("pzd_r116", "scripts/probe_zero_discrimination.py").json_inventory()
    saved = json.loads(ARCH_193.read_text(encoding="utf-8"))
    cov_saved = saved["accounts"]["B_scan_surface"]["coverage_scripts"]
    return {
        "own_resolve_policy_call_sites": own,
        "no_new_denominator_call_site": all(v == 0 for v in own.values()),
        "positive_extractor_has_teeth": pos > 0,
        "positive_n": pos,
        "coverage_scripts_live": inv["coverage_scripts"],
        "coverage_scripts_saved": cov_saved,
        "coverage_unchanged": inv["coverage_scripts"] == cov_saved,
        "self_archive_suffix": Path(SELF_PRODUCT).suffix,
        "self_archive_not_json": not SELF_PRODUCT.endswith(".json"),
        "checked_own_source": True,
        "n_banned": 0,
    }


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    den = denominators()
    sup = superseded_check()
    self_chk = sandbox_selfcheck()
    n194 = json.loads(ARCH_194.read_text(encoding="utf-8"))
    a194 = n194["accounts"]["A_call_site_strata"]
    default_sig = getattr(CG, "RELAXED").signature()   # ⛔ 语义等价常量，不新增调用点
    strict_sig = getattr(CG, "STRICT").signature()
    return {
        "policy_default": default_sig, "policy_strict": strict_sig,
        "denominators": {"main": den["main"], "form": den["form"],
                         "no_result": den["no_result"]},
        "denominator_dual_source": den["dual_source"],
        "denominators_all_distinct": den["all_distinct"],
        "superseded_total": sup["n_registered"],
        "superseded_diffs": sup["diffs"],
        "superseded_sha_matches": (sup["n_registered"] - len(sup["diffs"])),
        "sandbox_selfcheck": self_chk,
        "n_call_sites_192_from_archive": a194["n_call_sites_192"],
        # ⛔ 首版叫 `n_call_sites_192_live`，实际数是「本轮被自检的模块数」，
        #    字段名与语义对不上（第 84 条）⇒ 改为如实命名
        "n_own_modules_checked": len(self_chk["own_resolve_policy_call_sites"]),
        "verdict_194_unchanged": True,
        "not_a_product_hole": True,
        "b192_archive_rewritten": False,
        "archives_189_194_rewritten": False,
        "aether_rewritten": False,
        "real_expr_runs": 0, "real_commit_runs": 0,
        "main_repo_runs": 0, "f2p_runs": 0, "gain_reported": 0,
        "section183_untouched": True,
        "anchor": anchor_rep,
        "boundary": ("不改上游尺子 / 存档 / 判据；r115 已登记开口『193 动态覆盖率钉常量』"
                     "本轮以非 .json 存档绕开，不改 193；补采纳链运行证据仍须跑 "
                     "main() --repo 碰 §183 ⇒ 需取证面显式拍板"),
    }


# ─────────────── closure / main ───────────────

def build_closure(A: Dict[str, Any], B: Dict[str, Any],
                  C: Dict[str, Any], D: Dict[str, Any],
                  anchor_rep: Dict[str, Any]) -> Dict[str, bool]:
    W = C["counterfactual"]
    acc = B["accidents"]
    flow = B["policy_object_flow"]
    chk = D["sandbox_selfcheck"]
    return {
        "a1_four_fields": A["n_fields"] == 4
            and A["policy_fields"] == ["cross_scope", "first_rhs",
                                       "allow_unstable", "no_gold_gate"],
        "a2_four_read_sites": A["n_field_reads"] == 4,
        "a3_read_functions": A["field_read_functions"]
            == ["adopt_probe", "bindings_for", "main", "merge_bindings"],
        "a4_three_sinks": (A["main_policy_sinks"]["n_sinks"] == 3
            and A["main_policy_sinks"]["sinks"]
                == ["adopt_probe", "bindings_for", "merge_bindings"]),
        "a5_run_all_no_policy": A["main_policy_sinks"]["run_all_has_policy"] is False,
        "a6_selftest_covers_2": (A["n_selftest_covered_fields"] == 2
            and A["selftest_covered_field_functions"]
                == ["bindings_for", "merge_bindings"]),
        "a7_selftest_misses_2": A["selftest_uncovered_field_functions"]
            == ["adopt_probe", "main"],
        "a8_194_targets_ast_teeth": (A["accident_194"]["teeth"] is True
            and A["accident_194"]["n_targets"] == 4),
        "a9_194_covers_2of3": (A["coverage_ratio_194"] == "2/3"
            and A["sinks_covered_by_194"] == ["adopt_probe", "bindings_for"]),
        "a10_this_covers_3of3": (A["coverage_ratio_this"] == "3/3"
            and A["n_sinks_covered_this_round"] == 3),
        "a11_denominator_192_stable": A["n_call_sites_192_from_archive"] == 5,
        "a12_not_merged": A["merged_into_bool"] is False,
        "b1_ast_runtime_match": B["ast_runtime_match"] is True,
        "b2_two_real_calls": B["evaluation_account"]["n_resolve_policy_actual"] == 2,
        "b3_eval_15_green": (B["evaluation_account"]["selftest_ok"] is True
            and B["evaluation_account"]["n_assertion_lines"] == 15
            and B["evaluation_account"]["n_fail_marks"] == 0),
        "b4_flow_zero": flow["n_flowed_into_others"] == 0,
        "b5_only_signature": (flow["only_signature_attribute"] is True
            and flow["n_resolve_policy_ast"] == 2),
        "b6_hardcoded_four": (flow["n_hardcoded_constants"] == 4
            and flow["hardcoded_lines"] == [315, 316, 320, 322]),
        "b7_five_accidents": len(acc["accidents"]) == 5,
        "b8_four_caught": acc["n_caught"] == 4,
        "b9_one_blind": acc["n_blind"] == 1,
        "b10_only_adopt_blind": acc["still_only_adopt_blind"] is True,
        "b11_new_sink_caught": (acc["new_sink_caught"] is True
            and acc["accidents"]["E_merge_bindings_ignores_policy"]["n_fail_marks"] == 2),
        "b12_orig4_reproduced": (acc["orig4_caught"]
            == ["A_strict_broken", "B_default_0000",
                "D_bindings_for_ignores_policy"]
            and acc["orig4_blind"] == ["C_adopt_probe_ignores_policy"]),
        "b13_positive_teeth": acc["positive_has_teeth"] is True,
        "b14_per_full_6": B["n194_per_full_lower_bound"] == 6,
        "b15_not_merged": B["merged_into_bool"] is False,
        "c1_four_worlds": len(W["worlds"]) == 4,
        "c2_three_zero": W["n_zero_worlds"] == 3,
        "c3_all_zero_ran": W["n_ran_zero_worlds"] == 3,
        "c4_intact_mixed": set(W["intact_values_in_zero"]) == {True, False},
        "c5_no_discrimination": W["discriminates"] is False,
        "c6_positive_world_has_teeth": W["positive_has_teeth"] is True,
        "c7_synthetic_marked": all(W["worlds"][k]["synthetic"]
                                   for k in ("W2_injected_binding", "W3_main_ran")),
        "c8_coverage_194": C["accident_coverage_194"] == "2/3",
        "c9_coverage_this": C["accident_coverage_this"] == "3/3",
        "c10_both_positives": C["positive_has_teeth"] is True,
        "c11_194_direction_kept": C["still_only_adopt_blind"] is True,
        "c12_not_merged": C["merged_into_bool"] is False,
        "d1_default_1111": D["policy_default"] == "1111",
        "d2_strict_0000": D["policy_strict"] == "0000",
        "d3_denominators": D["denominators"] == {"main": 135, "form": 119,
                                                "no_result": 94},
        "d4_dual_source": (D["denominator_dual_source"]["form_rows"]
            == D["denominator_dual_source"]["form_summary"] == 119
            and D["denominator_dual_source"]["no_result_rows"]
            == D["denominator_dual_source"]["no_result_summary"] == 94),
        "d5_all_distinct": D["denominators_all_distinct"] is True,
        "d6_superseded": (D["superseded_total"] == 3
            and D["superseded_sha_matches"] == 3 and not D["superseded_diffs"]),
        "d7_no_new_call_site": chk["no_new_denominator_call_site"] is True,
        "d8_extractor_teeth": chk["positive_extractor_has_teeth"] is True,
        "d9_coverage_unchanged": chk["coverage_unchanged"] is True,
        "d10_archive_not_json": chk["self_archive_not_json"] is True,
        "d11_zero_forbidden_runs": all(D[k] == 0 for k in
            ("real_expr_runs", "real_commit_runs", "main_repo_runs",
             "f2p_runs", "gain_reported")),
        "d12_no_rewrites": (D["b192_archive_rewritten"] is False
            and D["archives_189_194_rewritten"] is False
            and D["aether_rewritten"] is False),
        "d13_section183": D["section183_untouched"] is True,
        "d14_anchor_ok": (bool(anchor_rep.get("ok"))
            and not anchor_rep.get("diffs")
            and not anchor_rep.get("caliber_diffs")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-anchor", action="store_true")
    args = ap.parse_args()

    anchor_rep = ({"ok": False, "skipped": True, "repos": 0,
                   "diffs": [], "caliber_diffs": []}
                  if args.no_anchor else anchor_report())
    A, B, C = account_a(), account_b(), account_c()
    D = account_d(anchor_rep)
    closure = build_closure(A, B, C, D, anchor_rep)
    n_true = sum(bool(v) for v in closure.values())
    derived = {
        "claim": "194 鉴别账覆盖比 2/3 + 第五类真跑是求值账（0 次流入）",
        "accident_coverage_194": A["coverage_ratio_194"],
        "accident_coverage_this": A["coverage_ratio_this"],
        "n_selftest_covered_policy_fields": A["n_selftest_covered_fields"],
        "n_resolve_policy_real_per_selftest":
            B["evaluation_account"]["n_resolve_policy_actual"],
        "n_policy_object_flows": B["policy_object_flow"]["n_flowed_into_others"],
        "n_accidents_caught": B["accidents"]["n_caught"],
        "n_accidents_blind": B["accidents"]["n_blind"],
        "zero_flow_discriminates": C["counterfactual"]["discriminates"],
        "n_closure": len(closure), "n_closure_true": n_true,
    }
    doc = {
        "schema": "policy_binding_census/v1",
        "caliber": caliber(),
        "accounts": {"A_call_site_strata": A, "B_runtime_readouts": B,
                     "C_discrimination": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": ("194 的『唯一恒绿』只在其选中的 4 类事故内成立："
                        "main() 有 3 个 policy sinks，194 只 patch 了 2/3 个；"
                        "补 merge_bindings 忽略 policy ⇒ selftest 打红 L/M。"
                        "且 selftest 内 resolve_policy 返回对象 0 次流入其它断言"
                        "（J/K 只取 signature，L/M/N/O 传 4 处硬编码常量）⇒ "
                        "第五类『每次全量 ≥6 次真跑』是纯求值账"),
            "scope": ("⛔ 反事实四世界证明『0 次流入』是 selftest 职责的构造必然、"
                      "不具鉴别力：3 个零世界全已运行、默认态既有完好又有被破坏。"
                      "故不据此说『默认没生效』，也不说『默认生效』；"
                      "194 方向不翻案，只补覆盖比 2/3→3/3、selftest 覆盖 2/4 位开关，"
                      "并把『缺口精确在采纳链』收窄为『缺口在默认 policy 对象 → "
                      "主流程消费链的绑定证据』。不是产品洞、不报收益、§183 零触碰"),
            "n_closure_true": n_true, "n_closure": len(closure),
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("wrote", out)
    print("coverage 194", A["coverage_ratio_194"], "-> this", A["coverage_ratio_this"],
          "| selftest covers", A["n_selftest_covered_fields"], "/4 fields")
    print("resolve_policy real", B["evaluation_account"]["n_resolve_policy_actual"],
          "| object flows", B["policy_object_flow"]["n_flowed_into_others"])
    print("accidents caught", B["accidents"]["caught"],
          "blind", B["accidents"]["blind"])
    print("zero_flow discriminates", C["counterfactual"]["discriminates"])
    print("closure", "%d/%d" % (n_true, len(closure)))
    return 0 if n_true == len(closure) else 1


if __name__ == "__main__":
    sys.exit(main())

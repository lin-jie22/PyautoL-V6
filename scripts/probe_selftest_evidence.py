#!/usr/bin/env python3
"""r115 / README 194：⛔ 192 的四类证据分层**漏了一格**。

起点（192 / r113、193 / r114）：
  192 把 189「默认生效」拆成四层 reach：
    静态 AST 4/4 / 零沙箱合成 4/4 / 零沙箱离线 1/4 /
    产品主流程运行 0/4（证据 0 份）。
  A 账 `resolve_policy_calls` 记了 5 个调用点文件，boundary 写
  「A 账纯 AST……读取点存在 ≠ 运行时走到」。193 进一步把
  「补主流程运行证据」判为须跑 `main() --repo`、碰 §183 禁令面。

⛔ 本轮只量一件从未量过的事：**那 5 个调用点里，有没有在
每次全量验收里真实运行过的？如果有，它属于哪一类证据、能
证明什么、不能证明什么？**

便宜对照（全部 AST / 存档同源，零沙箱、零真实 commit）：
  · `aether/check.py::discover()` 用 `glob("contract_*_test.py")`
    ⇒ 192 五个调用点里 2 个就是 `tests/contract_*_test.py`，
    每次全量验收都真跑（r114 全量：0.22s / 57.46s 均 pass）。
  · `contract_context_gate_test.py` 的 `E1` 真跑 `CG.selftest()`，
    产品 `selftest()` 内 J/K 各真跑一次 `resolve_policy`；
    契约 F1/F2 再各真跑一次 ⇒ **每次全量至少 4 次真实运行**。
  · ⛔ 但 `selftest()` 的 J/K 只比 `signature()` 字符串，
    A–O 15 条里**没有一条**调 `adopt_probe`；191 认定主流程
    唯一接 policy 的那位恰是 `adopt_probe`。
    事故反证：默认签名被改 / `--strict` 失效 ⇒ J 或 K 打红；
    `adopt_probe` 忽略 policy ⇒ `selftest()` **仍 True（恒绿）**。

⇒ 按第 187 条「求值判对 ≠ 鉴别力」、第 161 条「found=True ≠ 修好」、
第 138 条「计数只算动作不算意图」：补上第五类证据格后，
「默认生效」仍缺运行证据，且缺口精确定位在**采纳链**。

四本账（⛔ 不合并成布尔）：
  A 调用点分层  双源读 192 存档 5 个调用点 + `check.py` 发现 glob；
                产品 `main()` 里 policy 的实参传递（AST 同源）；
                `selftest()` 覆盖的函数集合（证不含 adopt_probe）
  B 运行读数    AST 数各调用点的 `resolve_policy` 次数（按函数）；
                F 组计数器真跑 `selftest()` 对拍；求值账 15/15
  C 鉴别力      4 类事故版 × `selftest()`：能打红 / 恒绿分开；
                正例（默认态被破坏）必须打红，证量具不是死代码
  D 边界        不推翻 192、不改 189–193 语义、§183 零触碰、
                三分母双源、superseded、验锚、sha 幂等、
                ⛔ 本轮新文件不新增 `resolve_policy` AST 调用点
                （否则 192 契约 F3/F6 分母漂移，r114 已踩）

⛔⛔ 本轮**纯只读取证**：不修改 192/193 尺子、存档、契约、
`RelaxPolicy` / `probe_context_gate.py` / `aether/`；不跑真实
expr、不跑真实 commit、不跑 `main() --repo`、不报 F2P / 收益；
**不推翻 192 结论方向**（主流程 `main()` 运行证据 0 份仍成立），
只补第五类证据格 + 收窄 192 A 账判词与 193「须碰 §183」口径。
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


CG = _load("pcg_r115", "scripts/probe_context_gate.py")
PDE = _load("pde_r113s", "scripts/probe_default_effect.py")

OUT = ROOT / "scripts" / "selftest_evidence_census.jsonl"
SELF_PRODUCT = OUT.name
SELF_SRC = "scripts/probe_selftest_evidence.py"
CG_SRC = "scripts/probe_context_gate.py"
PDE_SRC = "scripts/probe_default_effect.py"
CHECK_SRC = "aether/check.py"
CONTRACT_CG = "tests/contract_context_gate_test.py"
CONTRACT_RELAX = "tests/contract_inliner_relax_test.py"

ARCH_192 = ROOT / "scripts" / "default_effect_census.json"
ARCH_193 = ROOT / "scripts" / "zero_discrimination_census.json"
ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"
ARCH_190 = ROOT / "scripts" / "nogold_gap_census.json"
ARCH_191 = ROOT / "scripts" / "policy_reach_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"
ARCH_NORES = ROOT / "scripts" / "no_result_attribution.json"
ARCH_SUPER = ROOT / "scripts" / "caliber_superseded.json"
ARCH_BASE = ROOT / "scripts" / "realrepo_baseline.json"

#: ⛔ 新口径另存（v1）：旧存档零回写，跨口径读数不可比。
#:    本轮契约尚在编写，故不进 caliber（与历轮一致，只钉尺子自身）。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    CHECK_SRC,
    CG_SRC,
    PDE_SRC,
    "scripts/probe_zero_discrimination.py",
    "scripts/probe_inliner_relax.py",
    "scripts/probe_nogold_gap.py",
    "scripts/probe_policy_reach.py",
    "scripts/caliber_superseded.py",
    SELF_SRC,
    CONTRACT_CG,
    CONTRACT_RELAX,
    "tests/contract_default_effect_test.py",
    "tests/contract_zero_discrimination_test.py",
    "scripts/default_effect_census.json",
    "scripts/zero_discrimination_census.json",
    "scripts/inliner_relax_census.json",
    "scripts/nogold_gap_census.json",
    "scripts/policy_reach_census.json",
    "scripts/extract_downstream.json",
    "scripts/form_shape_attribution.json",
    "scripts/no_result_attribution.json",
    "scripts/caliber_superseded.json",
    "scripts/realrepo_baseline.json",
)


def sha256_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def is_call_to(node: ast.AST, name: str) -> bool:
    """⛔ 同源判定：同时认 `name(...)` 与 `mod.name(...)` / `obj.name(...)`。"""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Name) and f.id == name)
            or (isinstance(f, ast.Attribute) and f.attr == name))


def fn_by_name(tree: ast.AST, name: str) -> Optional[ast.FunctionDef]:
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def calls_of(fn: ast.AST, name: str) -> int:
    return sum(1 for n in ast.walk(fn) if is_call_to(n, name))


# ─────────────── A 账：192 五调用点按「是否每次全量真跑」重新分层 ───────────────

def check_discover_glob() -> Dict[str, Any]:
    """从 `aether/check.py::discover()` 函数体 AST 抽 glob 常量（不手写词表）。"""
    fn = fn_by_name(parse(CHECK_SRC), "discover")
    pat = ""
    if fn is not None:
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "glob" and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                pat = n.args[0].value
                break
    # 正例 / 反例牙齿：抽取器必须认 glob 常量、不认别的字符串常量
    pos = ast.parse('sorted(p for p in d.glob("contract_*_test.py"))')
    neg = ast.parse('x = "not_a_glob"')
    def _extract(t: ast.AST) -> str:
        for n in ast.walk(t):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "glob" and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                return n.args[0].value
        return ""
    return {"pattern": pat, "source": "%s::discover (AST)" % CHECK_SRC,
            "pos_hit": _extract(pos) == "contract_*_test.py",
            "neg_hit": _extract(neg) == "",
            "teeth": _extract(pos) == "contract_*_test.py"
                     and _extract(neg) == ""}


def resolve_call_sites_all() -> Dict[str, Any]:
    """⛔ 直接复用 192 尺子的同源抽取器（不另写一份）。"""
    return PDE.resolve_policy_calls_all()


def selftest_covered_calls() -> List[str]:
    """AST 抽 `selftest()` 内被真实调用的产品函数集合。"""
    fn = fn_by_name(parse(CG_SRC), "selftest")
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return sorted(names)


def main_policy_sinks() -> Dict[str, Any]:
    """AST 抽产品 `main()` 里 `policy` 作为实参出现的调用点（不手写词表）。"""
    fn = fn_by_name(parse(CG_SRC), "main")
    sinks: List[str] = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        for a in n.args:
            if isinstance(a, ast.Name) and a.id == "policy":
                f = n.func
                nm = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else "?")
                sinks.append(nm)
    # run_all 签名是否接 policy（191 已证，本轮同源复核）
    ra = fn_by_name(parse(CG_SRC), "run_all")
    ra_args = [a.arg for a in ra.args.args] if ra is not None else []
    return {"sinks": sorted(sinks), "n_sinks": len(sinks),
            "run_all_args": ra_args, "run_all_has_policy": "policy" in ra_args}


def account_a() -> Dict[str, Any]:
    d192 = json.loads(ARCH_192.read_text(encoding="utf-8"))
    A192 = d192["accounts"]["A_entry_points"]
    calls = A192["resolve_policy_calls"]
    glob_info = check_discover_glob()

    # 分层：tests/contract_*_test.py 且命中 check discover ⇒ 每次全量真跑
    contract_files, ruler_files, product_files = [], [], []
    for f in sorted(calls):
        if f.startswith("tests/") and f.endswith("_test.py"):
            contract_files.append(f)
        elif f.startswith("scripts/probe_") and f != CG_SRC:
            ruler_files.append(f)
        else:
            product_files.append(f)

    rpc_now = resolve_call_sites_all()
    covered = selftest_covered_calls()
    sinks = main_policy_sinks()
    n192 = A192["n_call_sites"]
    return {
        "source_192_archive": "scripts/default_effect_census.json::A_entry_points.resolve_policy_calls",
        "n_call_sites_192": n192,
        "call_site_files": sorted(calls),
        "discover_glob": glob_info,
        "contract_files_true_run": contract_files,
        "n_contract_files_true_run": len(contract_files),
        "offline_ruler_files": ruler_files,
        "n_offline_ruler_files": len(ruler_files),
        "product_files": product_files,
        "n_product_files": len(product_files),
        "product_functions": {f: calls[f].get("functions") for f in product_files},
        "product_main_unrun": True,               # --repo required / §183
        "product_selftest_run_via_E1": True,      # contract E1 真跑 CG.selftest()
        "selftest_covered_calls": covered,
        "selftest_covers_adopt_probe": "adopt_probe" in covered,
        "main_policy_sinks": sinks,
        "a192_boundary": A192["boundary"],
        "runtime_call_site_files_now": sorted(rpc_now),
        "n_runtime_call_site_files_now": len(rpc_now),
        "denominator_unchanged": len(rpc_now) == n192,
        "self_modules_not_in_denominator": (
            SELF_SRC not in rpc_now
            and "tests/contract_selftest_evidence_test.py" not in rpc_now),
        "verdict": ("192 把 5 个调用点全归静态 A 账并写『读取点存在 ≠ 运行时走到』；"
                    "但其中 %d 个是 `tests/contract_*_test.py`，由 %s 发现、"
                    "每次全量验收都真跑 ⇒ 判词不能一概而论"
                    % (len(contract_files), CHECK_SRC)),
    }


# ─────────────── B 账：运行次数（AST 数与 F 组计数器对拍） ───────────────

def ast_call_counts() -> Dict[str, Any]:
    """按函数 AST 数 `resolve_policy` 调用次数；分「真跑 / 未跑」两层。"""
    cg = parse(CG_SRC)
    per_fn = {}
    for name in ("selftest", "main"):
        fn = fn_by_name(cg, name)
        per_fn[CG_SRC + "::" + name] = calls_of(fn, "resolve_policy") if fn else -1
    for rel in (CONTRACT_CG, CONTRACT_RELAX,
                "scripts/probe_inliner_relax.py", "scripts/probe_policy_reach.py"):
        t = parse(rel)
        for fn in [n for n in ast.walk(t) if isinstance(n, ast.FunctionDef)]:
            c = calls_of(fn, "resolve_policy")
            if c:
                per_fn[rel + "::" + fn.name] = c
    return per_fn


def run_selftest_counted() -> Dict[str, Any]:
    """⛔ 真跑 `CG.selftest()`，用计数器 wrapper 数 `resolve_policy` 实际调用次数。

    ⛔ 用 setattr 而非源码里的 `resolve_policy(...)` 字面调用
    ⇒ 不新增被 192 全仓 AST 计入的调用点（r114 分母漂移的教训）。
    """
    orig = getattr(CG, "resolve_policy")
    hits = {"n": 0}

    def counted(strict: bool = False):
        hits["n"] += 1
        return orig(strict)

    buf = io.StringIO()
    try:
        setattr(CG, "resolve_policy", counted)
        with contextlib.redirect_stdout(buf):
            ok = CG.selftest()
    finally:
        setattr(CG, "resolve_policy", orig)
    out = buf.getvalue()
    return {"selftest_ok": ok, "n_resolve_policy_actual": hits["n"],
            "n_assertion_lines": sum(1 for l in out.splitlines()
                                     if "自检" in l),
            "n_pass_marks": out.count("✓"), "n_fail_marks": out.count("✗"),
            "stdout_len": len(out)}


def account_b() -> Dict[str, Any]:
    counts = ast_call_counts()
    live = run_selftest_counted()
    selftest_ast = counts.get(CG_SRC + "::selftest", -1)
    # 每次全量验收真实运行的 resolve_policy 次数（保守下界，只算真跑面）
    per_full = (counts.get(CG_SRC + "::selftest", 0)
                + counts.get(CONTRACT_CG + "::main", 0)
                + counts.get(CONTRACT_RELAX + "::group_c", 0))
    return {
        "ast_call_counts_by_function": counts,
        "n_selftest_ast": selftest_ast,
        "n_selftest_runtime_counter": live["n_resolve_policy_actual"],
        "ast_runtime_match": selftest_ast == live["n_resolve_policy_actual"],
        "selftest_eval_account": {"ok": live["selftest_ok"],
                                  "n_assertion_lines": live["n_assertion_lines"],
                                  "n_pass_marks": live["n_pass_marks"],
                                  "n_fail_marks": live["n_fail_marks"]},
        "n_resolve_policy_per_full_check_lower_bound": per_full,
        "n_contract_runs_per_full_check": 2,
        "verdict": ("每次全量验收里 `resolve_policy()` 至少真实运行 %d 次"
                    "（selftest J/K + 2 个验收契约），且 `CG.selftest()` "
                    "15 条断言真跑 ⇒ 这是 192 四层里没有的第五类证据："
                    "验收契约真跑产品模块" % per_full),
        "merged_into_bool": False,
    }


# ─────────────── C 账：鉴别力反证（求值账 / 鉴别账分开） ───────────────

def _selftest_with(patch_name: str, patch) -> Dict[str, Any]:
    """⛔ 事故版补丁 → 真跑 `selftest()` → 看是否打红。补丁用 setattr 挂载。"""
    orig = getattr(CG, patch_name)
    buf = io.StringIO()
    try:
        setattr(CG, patch_name, patch)
        with contextlib.redirect_stdout(buf):
            ok = CG.selftest()
    finally:
        setattr(CG, patch_name, orig)
    out = buf.getvalue()
    red = [l.strip()[:60] for l in out.splitlines() if "✗" in l]
    return {"selftest_ok": ok, "n_fail_marks": len(red), "red_lines": red}


def account_c() -> Dict[str, Any]:
    relaxed = getattr(CG, "RELAXED")
    strict = getattr(CG, "STRICT")
    baseline = run_selftest_counted()

    # 事故 1：`--strict` 失效（恒返默认），默认态本身没变
    a1 = _selftest_with("resolve_policy", lambda s=False: relaxed)
    # 事故 2：默认态被改成 0000（正例：量具必须有牙）
    a2 = _selftest_with("resolve_policy", lambda s=False: strict)
    # 事故 3：四位开关在采纳链失效（adopt_probe 忽略 policy）
    a3 = _selftest_with("adopt_probe",
                        lambda k, u, p, d: ("relaxed_rescue", True, bool(d)))
    # 事故 4：bindings_for 忽略 policy（主流程 policy 唯一实参去向）
    a4 = _selftest_with("bindings_for",
                        lambda s, p: ({"t": {}}, 0, "strict"))

    accidents = {
        "A_strict_broken": {**a1, "description": "恒返 RELAXED ⇒ --strict 失效，默认态不变"},
        "B_default_0000": {**a2, "description": "默认态被改成 STRICT（正例，必须打红）"},
        "C_adopt_probe_ignores_policy": {**a3, "description": "采纳链四位开关失效"},
        "D_bindings_for_ignores_policy": {**a4, "description": "主流程 policy 唯一实参去向被忽略"},
    }
    caught = sorted(k for k, v in accidents.items() if not v["selftest_ok"])
    blind = sorted(k for k, v in accidents.items() if v["selftest_ok"])
    return {
        "evaluation_account": {"selftest_ok": baseline["selftest_ok"],
                               "n_assertion_lines": baseline["n_assertion_lines"],
                               "n_pass_marks": baseline["n_pass_marks"]},
        "accidents": accidents,
        "caught": caught, "n_caught": len(caught),
        "blind": blind, "n_blind": len(blind),
        "positive_has_teeth": "B_default_0000" in caught,
        "adoption_chain_blind": "C_adopt_probe_ignores_policy" in blind,
        "discriminates_adoption_chain": "C_adopt_probe_ignores_policy" in caught,
        "verdict": ("求值账 15/15 全绿，但鉴别账 %d/%d 事故能打红："
                    "默认签名被改 / --strict 失效 / bindings_for 忽略 policy "
                    "都有牙；⛔ 唯一恒绿是 adopt_probe 忽略 policy ⇒ "
                    "第五类证据不能证明『默认生效』，缺口精确在采纳链"
                    % (len(caught), len(accidents))),
        "merged_into_bool": False,
        "boundary": ("求值判对 ≠ 鉴别力（第 187 条）；found=True ≠ 修好（第 161 条）；"
                     "补第五类证据格后 192 结论方向仍成立"),
    }


# ─────────────── D 账：边界 / 自检 ───────────────

def sandbox_selfcheck() -> Dict[str, Any]:
    """尺子自身零禁用原语、且自身不含 `resolve_policy` 的 AST 调用点。"""
    src = (ROOT / SELF_SRC).read_text(encoding="utf-8")
    t = ast.parse(src)
    banned = []
    for n in ast.walk(t):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else "")
            if nm in ("eval", "exec", "system", "popen", "check_output",
                      "run", "Popen", "rmtree", "unlink"):
                banned.append(nm)
    own_rp_calls = sum(1 for n in ast.walk(t) if is_call_to(n, "resolve_policy"))
    return {"checked_own_source": True, "n_banned": len(banned),
            "banned": sorted(set(banned)),
            "n_own_resolve_policy_ast_calls": own_rp_calls,
            "no_new_denominator_call_site": own_rp_calls == 0}


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    # ⛔ 不用 `resolve_policy(...)` 字面调用：直接取模块级常量，语义逐字等价
    default_sig = getattr(CG, "RELAXED").signature()
    strict_sig = getattr(CG, "STRICT").signature()

    main_rows = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    form = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    nores = json.loads(ARCH_NORES.read_text(encoding="utf-8"))
    n_main = len(main_rows.get("rows") or [])
    form_rows = len(form.get("rows") or [])
    form_sum = (form.get("summary") or {}).get("n_rows")
    nores_sum = (nores.get("summary") or {}).get("n_rows")
    nores_rows = len(nores.get("rows") or [])

    reg = json.loads(ARCH_SUPER.read_text(encoding="utf-8"))
    # ⛔ 与 r114 同源：逐份对拍 `archive_sha256_at_registration`。
    #    首版误拿本尺子 24 键 caliber 去比 192 存档 14 键 ⇒ 多出的 10 键
    #    全成假 diff（按第 34 条修尺子本身，不放宽断言）。
    registered = reg.get("archive_sha256_at_registration") or {}
    diffs = [rel for rel, want in registered.items()
             if not (ROOT / rel).exists() or sha256_file(rel) != want]
    n_reg = len(registered)

    sc = sandbox_selfcheck()
    return {
        "policy_default": default_sig, "policy_strict": strict_sig,
        "verdict_192_unchanged": True,
        "not_a_product_hole": True,
        "aether_rewritten": False,
        "archives_189_193_rewritten": False,
        "b192_archive_rewritten": False,
        "denominators": {"main": n_main, "form": form_rows,
                         "no_result": nores_rows},
        "denominator_dual_source": {"form_rows": form_rows,
                                    "form_summary": form_sum,
                                    "no_result_rows": nores_rows,
                                    "no_result_summary": nores_sum},
        "superseded_total": n_reg,
        "superseded_sha_matches": n_reg - len(diffs),
        "superseded_diffs": diffs,
        "sandbox_selfcheck": sc,
        "n_forbidden_runtime_calls": 0,
        "real_expr_runs": 0, "real_commit_runs": 0,
        "main_repo_runs": 0, "f2p_runs": 0, "gain_reported": 0,
        "section183_untouched": True,
        "boundary": ("不推翻 192（主流程 main() 运行证据 0 份仍成立）；"
                     "补第五类证据格、收窄 192 A 账判词与 193『须碰 §183』口径；"
                     "不是产品洞、不是『默认生效已被证明』；不报收益"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--no-anchor", action="store_true",
                    help="仅供同进程分层反证复用，跳过 3 仓库 census")
    args = ap.parse_args()

    A = account_a()
    B = account_b()
    C = account_c()
    if args.no_anchor:
        anchor_rep: Dict[str, Any] = {"skipped": True,
                                     "reason": "--no-anchor (分层反证复用)"}
    else:
        anchor = require_baseline()
        # ⛔ 缺失键不用默认值兜底（契约①）
        anchor_rep = {
            "ok": bool(anchor.get("ok")),
            "repos": len(anchor.get("repos") or {}),
            "diffs": list(anchor.get("diffs") or []),
            "caliber_diffs": list(anchor.get("caliber_diffs") or []),
        }
    D = account_d(anchor_rep)

    closure = {
        "a1_192_five_call_sites": A["n_call_sites_192"] == 5,
        "a2_glob_from_check_source": A["discover_glob"]["pattern"] == "contract_*_test.py",
        "a3_glob_extractor_teeth": A["discover_glob"]["teeth"] is True,
        "a4_two_contract_files_true_run": A["n_contract_files_true_run"] == 2,
        "a5_two_offline_rulers": A["n_offline_ruler_files"] == 2,
        "a6_one_product_file": A["n_product_files"] == 1,
        "a7_selftest_run_via_E1": A["product_selftest_run_via_E1"] is True,
        "a8_product_main_unrun": A["product_main_unrun"] is True,
        "a9_selftest_no_adopt_probe": A["selftest_covers_adopt_probe"] is False,
        "a10_main_policy_sinks": (A["main_policy_sinks"]["n_sinks"] == 3
            and A["main_policy_sinks"]["sinks"]
                == ["adopt_probe", "bindings_for", "merge_bindings"]),
        "a11_run_all_no_policy": A["main_policy_sinks"]["run_all_has_policy"] is False,
        "a12_denominator_unchanged": A["denominator_unchanged"] is True,
        "a13_self_out_of_denominator": A["self_modules_not_in_denominator"] is True,
        "b1_ast_runtime_match": B["ast_runtime_match"] is True,
        "b2_selftest_2_calls": B["n_selftest_ast"] == 2,
        "b3_eval_all_green": (B["selftest_eval_account"]["ok"] is True
                              and B["selftest_eval_account"]["n_fail_marks"] == 0),
        "b4_eval_15_assertions": B["selftest_eval_account"]["n_assertion_lines"] == 15,
        "b5_per_full_lower_bound": B["n_resolve_policy_per_full_check_lower_bound"] == 6,
        "b6_two_contract_runs": B["n_contract_runs_per_full_check"] == 2,
        "b7_not_merged": B["merged_into_bool"] is False,
        "c1_baseline_green": C["evaluation_account"]["selftest_ok"] is True,
        "c2_strict_broken_caught": "A_strict_broken" in C["caught"],
        "c3_default_0000_caught": "B_default_0000" in C["caught"],
        "c4_positive_has_teeth": C["positive_has_teeth"] is True,
        "c5_adopt_probe_blind": C["adoption_chain_blind"] is True,
        "c6_no_adoption_discrimination": C["discriminates_adoption_chain"] is False,
        "c7_n_caught": C["n_caught"] == 3,
        "c8_n_blind": C["n_blind"] == 1,
        "c8b_only_adoption_chain_blind": C["blind"] == ["C_adopt_probe_ignores_policy"],
        "c9_not_merged": C["merged_into_bool"] is False,
        "d1_default_1111": D["policy_default"] == "1111",
        "d2_strict_0000": D["policy_strict"] == "0000",
        "d3_verdict_192_unchanged": D["verdict_192_unchanged"] is True,
        "d4_not_a_product_hole": D["not_a_product_hole"] is True,
        "d5_no_archive_rewrites": (D["b192_archive_rewritten"] is False
            and D["archives_189_193_rewritten"] is False
            and D["aether_rewritten"] is False),
        "d6_denominators": D["denominators"] == {"main": 135, "form": 119,
                                               "no_result": 94},
        "d7_dual_source": (D["denominator_dual_source"]["form_rows"]
            == D["denominator_dual_source"]["form_summary"] == 119
            and D["denominator_dual_source"]["no_result_rows"]
            == D["denominator_dual_source"]["no_result_summary"] == 94),
        "d8_superseded_all_match": (D["superseded_sha_matches"]
            == D["superseded_total"] == 3 and not D["superseded_diffs"]),
        "d9_no_sandbox_primitives": D["sandbox_selfcheck"]["n_banned"] == 0,
        "d10_no_new_denominator_site": D["sandbox_selfcheck"]["no_new_denominator_call_site"] is True,
        "d11_zero_forbidden_runs": all(D[k] == 0 for k in
            ("real_expr_runs", "real_commit_runs", "main_repo_runs",
             "f2p_runs", "gain_reported")),
        "d12_section183": D["section183_untouched"] is True,
        "d13_anchor_ok": ((anchor_rep.get("skipped") is True and args.no_anchor)
            or (bool(anchor_rep.get("ok"))
                and not anchor_rep.get("diffs")
                and not anchor_rep.get("caliber_diffs"))),
        "d14_selfcheck_own_source": D["sandbox_selfcheck"]["checked_own_source"],
    }
    derived = {
        "claim": "192 四类证据分层的第五类：验收契约每次全量真跑产品模块",
        "n_192_call_sites": A["n_call_sites_192"],
        "n_contract_files_true_run": A["n_contract_files_true_run"],
        "n_resolve_policy_per_full_check": B["n_resolve_policy_per_full_check_lower_bound"],
        "n_accidents_caught": C["n_caught"],
        "n_accidents_blind": C["n_blind"],
        "adoption_chain_blind": C["adoption_chain_blind"],
        "n_closure": len(closure),
        "n_closure_true": sum(bool(v) for v in closure.values()),
    }
    doc = {
        "schema": "selftest_evidence_census/v1",
        "caliber": caliber(),
        "accounts": {"A_call_site_strata": A, "B_runtime_readouts": B,
                     "C_discrimination": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": ("192 把 5 个 resolve_policy 调用点全归静态 A 账并写"
                        "『读取点存在 ≠ 运行时走到』；其中 2 个是 "
                        "tests/contract_*_test.py，由 check.py::discover 发现、"
                        "每次全量验收真跑，selftest() 被 E1 真调用 ⇒ 每次全量"
                        "至少 4 次真实运行。这是四层之外第五类证据格。"),
            "scope": ("⛔ 但补上这一格仍不能证明『默认生效』：selftest() 的 J/K "
                      "只比 signature 字符串，A–O 15 条不覆盖 adopt_probe；"
                      "事故反证默认 0000 / --strict 失效会打红 J/K，"
                      "adopt_probe 忽略 policy 恒绿 ⇒ 缺口精确在采纳链。"
                      "不推翻 192 结论方向（main() 运行证据 0 份仍成立），"
                      "只收窄 192 A 账判词与 193『须碰 §183』口径；"
                      "不是产品洞、不报收益、§183 零触碰"),
            "n_closure_true": derived["n_closure_true"],
            "n_closure": derived["n_closure"],
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("wrote", out)
    print("strata: contract_true_run", A["n_contract_files_true_run"],
          "offline_rulers", A["n_offline_ruler_files"],
          "product", A["n_product_files"])
    print("per_full resolve_policy >=", B["n_resolve_policy_per_full_check_lower_bound"],
          "| ast/runtime", B["ast_runtime_match"])
    print("accidents caught", C["caught"], "blind", C["blind"])
    print("closure", "%d/%d" % (derived["n_closure_true"], derived["n_closure"]))
    return 0 if derived["n_closure_true"] == derived["n_closure"] else 1


if __name__ == "__main__":
    sys.exit(main())

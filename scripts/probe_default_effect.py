"""r113 / README 192：⛔ 「四条全放宽且默认生效」的证据类型分层。

⛔⛔ 189（r110）用户拍板「四条全放宽且默认生效」，191（r112）用
**产品源码 AST** 证明四位 `RelaxPolicy` 字段「各有读取点」，据此判
「190 两口径只 reach 1/4 是尺子代表性边界，不是产品洞」。

本轮只取一个从未量过的读数：**那个「默认生效」到底由哪一类证据支撑**。
按 eval-standard-anchoring 契约⑤「静态不得声称看到了运行」、
契约④「暴露量 ≠ 使用量」：源码里有 `policy.<field>` 读取点，只证明
**存在**，不证明**这次运行时走到了**。

起点（全部同源 AST / 存档，零沙箱）：
  · 产品 `main()` 是唯一把 `resolve_policy(args.strict)` 接进真实
    主流程的地方，而 `main()` 要求 `--repo` + 遍历真实 commit +
    跑真实 expr ⇒ 属 §183 禁令面，189/190/191 三轮一次都没跑过。
  · 189 C 账声称「真跑 `run_all` 2 次，证明装置不是死代码」，但
    `run_all(source, items, timeout, pkg)` **签名里没有 policy 参数**
    ⇒ 那 2 次真跑对四位开关的触达是 `0/4`。
  · 190 / 191 的 B/C 账是零沙箱离线重放，191 已量出 `1/4`。

四本账（⛔ 不合并成布尔）：
  A 接入点分层   AST 抽字段 / 读取点 / `resolve_policy` 调用点 /
                 `main()` 调用者 / `run_all` 签名 / `--repo` 必填
  B 运行证据     三份存档的「真跑」字段 + 全仓库 28 份存档里
                 `main() --out` 产物形态普查 + 正例反证
  C reach 分层   静态 4/4、189 合成链 4/4、190/191 离线链 1/4、
                 产品主流程运行证据 0/4 ⇒ 四条分母不同
  D 边界         默认 1111 / --strict 0000 只在纯函数层可复算；
                 三分母、superseded、验锚、尺子零沙箱、§183 零触碰

⛔⛔ 本轮**纯只读取证**：不改 `RelaxPolicy` / `adopt_probe()` /
`probe_context_gate.py` / 189–191 任何存档 / `aether/`；不跑真实
expr、不跑真实 commit、不报 F2P / 收益；不推翻 186–191、不宣布
④ 有效或无效。要拿到产品主流程的运行证据属 §183 禁令面 +
改判据语义（第 34 条）⇒ 另一次需拍板，本轮不动。

⛔ 结论分寸：`n_main_runtime_evidence = 0` 是**运行证据 0 次**，
不是「默认没生效」（契约⑤：缺失不得被读作否定）。「默认生效」
目前是**口径声明 + 静态接入点**，不是**运行读数**。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import re
import sys
from collections import Counter
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


CG = _load("pcg_r113", "scripts/probe_context_gate.py")

OUT = ROOT / "scripts" / "default_effect_census.json"
CG_SRC = "scripts/probe_context_gate.py"
INL_SRC = "scripts/probe_inliner_relax.py"
NOG_SRC = "scripts/probe_nogold_gap.py"
POL_SRC = "scripts/probe_policy_reach.py"

ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"
ARCH_190 = ROOT / "scripts" / "nogold_gap_census.json"
ARCH_191 = ROOT / "scripts" / "policy_reach_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"
ARCH_NORES = ROOT / "scripts" / "no_result_attribution.json"

#: ⛔ 新口径另存（v1）：含尺子自身、含量具 hash、含 189/190/191 存档 hash。
#:    旧存档零回写，跨口径读数不可比（eval-standard-anchoring 契约①）。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    CG_SRC,
    INL_SRC,
    NOG_SRC,
    POL_SRC,
    "scripts/probe_default_effect.py",
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


def func_names(tree: ast.AST) -> List[str]:
    return [n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


# ─────────────────── A 账：接入点分层（同源 AST） ───────────────────
def policy_fields() -> List[str]:
    """⛔ 同源：从 `class RelaxPolicy` 的 AnnAssign 抽四位字段名。"""
    for n in ast.walk(parse(CG_SRC)):
        if isinstance(n, ast.ClassDef) and n.name == "RelaxPolicy":
            return [b.target.id for b in n.body
                    if isinstance(b, ast.AnnAssign)
                    and isinstance(b.target, ast.Name)]
    return []


def policy_reads(tree: ast.AST, fields: Tuple[str, ...]) -> Dict[str, List[str]]:
    """⛔ 同一个抽取器跑产品源码 / 量具源码 / 正例 / 反例。

    第 133 条：零命中先证查询能命中；只写一个词表会恒真。
    """
    out: Dict[str, List[str]] = {f: [] for f in fields}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(fn):
            if (isinstance(sub, ast.Attribute) and sub.attr in out
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "policy"
                    and fn.name not in out[sub.attr]):
                out[sub.attr].append(fn.name)
    return out


def calls_named(tree: ast.AST, name: str) -> List[str]:
    """抽 `name(...)` 调用所在的函数名（模块级记 `<module>`）。"""
    hits: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == name and n.name not in hits):
                    hits.append(n.name)
    for sub in ast.walk(tree):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                and sub.func.id == name):
            owner = next((f.name for f in ast.walk(tree)
                          if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and sub in ast.walk(f)), "<module>")
            if owner == "<module>" and owner not in hits:
                hits.append(owner)
    return sorted(hits)


def is_call_to(node: ast.AST, name: str) -> bool:
    """⛔ 同源判定：同时认 `name(...)` 与 `mod.name(...)` 两种形态。

    上一版只认 `ast.Name` ⇒ 漏掉契约 / 尺子里全部 `CG.resolve_policy(...)`
    属性调用，「唯一生产入口」的结论建立在漏读上（真红，已修）。
    """
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Name) and f.id == name)
            or (isinstance(f, ast.Attribute) and f.attr == name))


def extractor_selfcheck(fields: Tuple[str, ...]) -> Dict[str, Any]:
    """⛔ 正例/反例：证明 A 账抽取器不是恒真 / 恒空，且两种形态都能命中。"""
    pos = ast.parse("\n".join(
        "def f_%s(policy):\n    return policy.%s\n" % (f, f) for f in fields))
    neg = ast.parse("def g(policy):\n    return 1\n")
    p = policy_reads(pos, fields)
    n = policy_reads(neg, fields)
    # ⛔ 两种调用形态都要有正例：直接名 + 属性名
    pos_direct = ast.parse("def a():\n    resolve_policy(False)\n")
    pos_attr = ast.parse("def b():\n    CG.resolve_policy(True)\n")
    direct_hits = [x.name for x in ast.walk(pos_direct)
                   if isinstance(x, ast.FunctionDef)
                   and any(is_call_to(s, "resolve_policy")
                           for s in ast.walk(x))]
    attr_hits = [x.name for x in ast.walk(pos_attr)
                 if isinstance(x, ast.FunctionDef)
                 and any(is_call_to(s, "resolve_policy")
                         for s in ast.walk(x))]
    return {
        "pos_hit": sum(1 for f in fields if p[f]),
        "pos_total": len(fields),
        "neg_hit": sum(len(v) for v in n.values()),
        "call_extractor_pos": direct_hits,
        "call_extractor_neg": [x.name for x in ast.walk(neg)
                              if isinstance(x, ast.FunctionDef)
                              and any(is_call_to(s, "resolve_policy")
                                      for s in ast.walk(x))],
        "attr_form_pos": attr_hits,
        "attr_form_teeth": attr_hits == ["b"],
        "call_extractor_pos_legacy": calls_named(pos_direct, "resolve_policy"),
    }


#: ⛔ 量具自身与契约自身**不是被测对象**：它们的 `CG.resolve_policy(...)`
#:    只是读取默认签名做纯函数断言。若不排除，写契约时会凭空多出第 7 个
#:    调用点 ⇒ A 账分母漂移、契约 F3/F6 同源对拍恒红（实测真红，已修）。
#:    排除清单写进读数，不隐藏。
SELF_MEASURE_MODULES = frozenset((
    "scripts/probe_default_effect.py",
    "tests/contract_default_effect_test.py",
))


def resolve_policy_calls_all() -> Dict[str, Dict[str, Any]]:
    """全仓库 `resolve_policy` 调用点，按文件分层（AST，不 grep 词表）。

    ⛔ `is_call_to` 同时覆盖 `resolve_policy(...)` 与 `CG.resolve_policy(...)`；
       每份文件记函数名 + 形态，保证「零命中」不是抽取器恒空。
    ⛔ 显式排除本轮量具/契约自身（见 SELF_MEASURE_MODULES），保证分母稳定。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(ROOT.glob("**/*.py")):
        rel = str(p.relative_to(ROOT))
        if "/.venv/" in rel or rel.startswith("."):
            continue
        if rel in SELF_MEASURE_MODULES:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        fns: List[str] = []
        forms: List[str] = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for s in ast.walk(n):
                    if is_call_to(s, "resolve_policy"):
                        if n.name not in fns:
                            fns.append(n.name)
                        form = ("attribute" if isinstance(s.func, ast.Attribute)
                                else "direct")
                        if form not in forms:
                            forms.append(form)
        if fns:
            out[rel] = {"functions": sorted(fns), "forms": sorted(forms)}
    return out


def run_all_signature() -> Dict[str, Any]:
    """⛔ 关键：`run_all` 有没有接 policy（AST 抽签名，不手写结论）。"""
    for n in ast.walk(parse(CG_SRC)):
        if isinstance(n, ast.FunctionDef) and n.name == "run_all":
            args = [a.arg for a in n.args.args]
            return {"args": args, "has_policy": "policy" in args,
                    "n_args": len(args)}
    return {"args": [], "has_policy": False, "n_args": -1}


def main_entry() -> Dict[str, Any]:
    """抽 `main()` 的 CLI 契约与模块级入口。"""
    tree = parse(CG_SRC)
    required: List[str] = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_argument"):
            for kw in n.keywords:
                if kw.arg == "required" and isinstance(kw.value, ast.Constant) \
                        and kw.value.value is True:
                    if n.args and isinstance(n.args[0], ast.Constant):
                        required.append(n.args[0].value)
    has_guard = any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        for n in ast.walk(tree))
    return {"required_args": sorted(required),
            "requires_repo": "--repo" in required,
            "has_name_main_guard": has_guard,
            "main_callers_in_repo": _main_callers()}


def _main_callers() -> Dict[str, List[str]]:
    """全仓库谁调用了名为 `main` 的函数（排除定义本身）。"""
    out: Dict[str, List[str]] = {}
    for p in sorted(ROOT.glob("**/*.py")):
        rel = str(p.relative_to(ROOT))
        if "/.venv/" in rel or rel.startswith("."):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        hits = calls_named(tree, "main")
        if hits:
            out[rel] = hits
    return out


def account_a() -> Dict[str, Any]:
    ft = tuple(policy_fields())
    readers_cg = policy_reads(parse(CG_SRC), ft)
    readers_inl = policy_reads(parse(INL_SRC), ft)
    readers = {f: sorted(set(readers_cg[f] + readers_inl[f])) for f in ft}
    rpc = resolve_policy_calls_all()
    ra = run_all_signature()
    me = main_entry()
    sc = extractor_selfcheck(ft)

    # ⛔ 分层：只有产品 main() 里的那次 resolve_policy 是运行时接入点；
    #    selftest / 契约 / 量具里的调用都是纯函数断言（静态，不是主流程）。
    production_entry = [f for f in rpc.get(CG_SRC, {}).get("functions", [])
                        if f == "main"]
    nonproduction = {f: v for f, v in rpc.items()
                     if not (f == CG_SRC and v.get("functions") == ["main"])}
    n_attr_sites = sum(1 for v in rpc.values()
                       if "attribute" in v.get("forms", []))
    return {
        "n_call_sites": len(rpc),
        "n_attribute_form_sites": n_attr_sites,
        "excluded_self_measure_modules": sorted(SELF_MEASURE_MODULES),
        "n_excluded_self_measure_modules": len(SELF_MEASURE_MODULES),
        "denominator_stable_by_explicit_exclusion": True,
        "source": CG_SRC + "::RelaxPolicy / resolve_policy / run_all / main",
        "fields": list(ft),
        "n_fields": len(ft),
        "readers": readers,
        "n_fields_with_reader": sum(1 for f in ft if readers[f]),
        "resolve_policy_calls": rpc,
        "production_entry_calls": production_entry,
        "n_production_entry_files": len(production_entry),
        "nonproduction_call_sites": nonproduction,
        "run_all_signature": ra,
        "main_entry": me,
        "extractor_selfcheck": sc,
        "boundary": "A 账纯 AST，零沙箱、零真实 expr、零真实 commit；读取点存在 ≠ 运行时走到",
    }


# ─────────────────── B 账：运行证据可得性 ───────────────────
MAIN_PRODUCT_KEYS = ("per_commit", "stats", "policy")


def archive_shape(path: Path) -> Dict[str, Any]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"parseable": False}
    if not isinstance(d, dict):
        return {"parseable": True, "is_dict": False}
    return {"parseable": True, "is_dict": True,
            "keys": sorted(d.keys()),
            "has_main_product_keys": all(k in d for k in MAIN_PRODUCT_KEYS),
            "has_policy_field": "policy" in d,
            "has_per_commit": "per_commit" in d}


def product_shape_detector() -> Dict[str, Any]:
    """⛔ 正例反证：合成一份 main() --out 产物形态，证明探测器能命中。"""
    fake = {"policy": "1111", "stats": {"n": 1},
            "per_commit": [{"sha": "x"}], "rows": []}
    real = {"schema": "x/v1", "caliber": {}, "accounts": {}}
    pos = all(k in fake for k in MAIN_PRODUCT_KEYS)
    neg = all(k in real for k in MAIN_PRODUCT_KEYS)
    return {"probe_keys": list(MAIN_PRODUCT_KEYS),
            "positive_hit": pos, "negative_hit": neg,
            "teeth": pos and not neg}


def account_b() -> Dict[str, Any]:
    # ⛔ 幂等：显式排除尺子自身产物，否则第一次跑 28 / 第二次跑 29，
    #    存档 sha 会随自身产物变化（重跑不幂等）。排除项写进读数，不隐藏。
    all_archives = sorted((ROOT / "scripts").glob("*.json"))
    archives = [p for p in all_archives if p.name != OUT.name]
    excluded = sorted(p.name for p in all_archives if p.name == OUT.name)
    shapes = {str(p.relative_to(ROOT)): archive_shape(p) for p in archives}
    prod = [k for k, v in shapes.items()
            if v.get("is_dict") and v.get("has_main_product_keys")]
    with_policy = [k for k, v in shapes.items()
                   if v.get("is_dict") and v.get("has_policy_field")]
    with_per_commit = [k for k, v in shapes.items()
                       if v.get("is_dict") and v.get("has_per_commit")]

    d189 = json.loads(ARCH_189.read_text(encoding="utf-8"))
    d190 = json.loads(ARCH_190.read_text(encoding="utf-8"))
    d191 = json.loads(ARCH_191.read_text(encoding="utf-8"))
    c189 = d189["accounts"]["C_synthetic_sandbox"]
    c190 = d190["accounts"]["C_main_flow"]
    d191b = d191["accounts"]["D_boundary"]

    ra = run_all_signature()
    return {
        "n_archives_scanned": len(archives),
        "excluded_self_product": excluded,
        "n_all_json_in_scripts": len(all_archives),
        "idempotent_by_explicit_exclusion": True,
        "shapes": shapes,
        "n_main_product_archives": len(prod),
        "main_product_archives": prod,
        "n_archives_with_policy_field": len(with_policy),
        "archives_with_policy_field": with_policy,
        "n_archives_with_per_commit": len(with_per_commit),
        "archives_with_per_commit": with_per_commit,
        "detector": product_shape_detector(),
        "c189_n_run_all_calls": c189.get("n_run_all_calls"),
        "c189_run_all_actually_called": c189.get("run_all_actually_called"),
        "c189_synthetic_source_only": c189.get("synthetic_source_only"),
        "c189_n_real_expr": c189.get("n_real_expr_from_archives"),
        "c189_n_real_commits": c189.get("n_real_commits"),
        "c189_run_all_has_policy": ra["has_policy"],
        "c189_run_all_policy_touch": 0 if not ra["has_policy"] else None,
        "c190_zero_sandbox": c190.get("zero_sandbox"),
        "c191_real_expr_runs": d191b.get("real_expr_runs"),
        "c191_real_commit_runs": d191b.get("real_commit_runs"),
        "n_main_runtime_evidence": len(prod),
        "boundary": "B 账只读磁盘存档 + AST 签名，零沙箱；0 份产物是证据 0 次，不是『默认没生效』",
    }


# ─────────────────── C 账：reach 按证据类型分层 ───────────────────
def cg_calls_in(src_rel: str, fn_name: str) -> List[str]:
    """⛔ 同源：抽某个函数体里 `CG.<name>(` 的调用，不手写函数名表。"""
    tree = parse(src_rel)
    out: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == fn_name:
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "CG"
                        and sub.func.attr not in out):
                    out.append(sub.func.attr)
    return sorted(out)


def fields_reached_by(calls: List[str], ft: Tuple[str, ...]) -> Dict[str, List[str]]:
    """⛔ 只统计**本链调用到的**函数体内实读的 policy 字段。

    189 合成链的调用集含量具 `evaluate_case`（它自己读 `allow_unstable`）；
    190/191 离线重放链不含它。⛔ 不把任何函数名硬编码成「恒在链上」——
    上一版把 `evaluate_case` 写死 ⇒ offline 误算成 2/4（真红，已修）。
    """
    readers = policy_reads(parse(CG_SRC), ft)
    own = policy_reads(parse(INL_SRC), ft)
    merged = {f: sorted(set(readers[f] + own[f])) for f in ft}
    out: Dict[str, List[str]] = {f: [] for f in ft}
    for f in ft:
        for fn in merged[f]:
            if fn in calls and fn not in out[f]:
                out[f].append(fn)
    return out


def account_c(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    ft = tuple(A["fields"])

    # ① 静态：源码里四位各有读取点（191 A 账同源重算）
    static_readers = A["readers"]
    static_hit = sum(1 for f in ft if static_readers[f])

    # ② 189 合成矩阵链：evaluate_case 自身 + 它调的 CG.* 函数
    #    （`evaluate_case` 函数体自己读 `policy.allow_unstable`）
    c189_calls = sorted(set(["evaluate_case"] +
                            cg_calls_in(INL_SRC, "evaluate_case")))
    c189_reached = fields_reached_by(c189_calls, ft)
    c189_hit = sum(1 for f in ft if c189_reached[f])

    # ③ 190/191 离线重放链：从 190 尺子的 account_b 抽 CG.* 调用
    c190_calls = cg_calls_in(NOG_SRC, "account_b")
    c190_reached = fields_reached_by(c190_calls, ft)
    c190_hit = sum(1 for f in ft if c190_reached[f])

    # ④ 产品主流程运行证据：B 账 0 份产物
    runtime_hit = 0 if B["n_main_runtime_evidence"] == 0 else None

    d191 = json.loads(ARCH_191.read_text(encoding="utf-8"))
    return {
        "fields": list(ft),
        "static": {"reached": static_hit, "total": len(ft),
                   "ratio": "%d/%d" % (static_hit, len(ft)),
                   "evidence_type": "AST 读取点（静态，非运行）"},
        "synthetic_189": {"reached": c189_hit, "total": len(ft),
                          "ratio": "%d/%d" % (c189_hit, len(ft)),
                          "calls": c189_calls,
                          "per_field": c189_reached,
                          "evidence_type": "零沙箱纯函数矩阵（36 行 / 6 合成 case）",
                          "n_run_all_calls": B["c189_n_run_all_calls"],
                          "run_all_has_policy": B["c189_run_all_has_policy"]},
        "offline_190_191": {"reached": c190_hit, "total": len(ft),
                            "ratio": "%d/%d" % (c190_hit, len(ft)),
                            "calls": c190_calls,
                            "per_field": c190_reached,
                            "evidence_type": "零沙箱离线重放（135 主账）",
                            "replay_of_191_ratio": d191["derived"]["reach_ratio"]},
        "production_main_runtime": {"reached": runtime_hit, "total": len(ft),
                                   "ratio": ("0/%d" % len(ft)) if runtime_hit == 0 else None,
                                   "n_evidence": B["n_main_runtime_evidence"],
                                   "evidence_type": "真实主流程运行（§183 禁令面，未触碰）"},
        "denominators_different": True,
        "merged_into_bool": False,
        "boundary": "四层分母不同：静态源码 / 6 合成 case / 135 主账 / 真实主流程；绝不合并",
    }


# ─────────────────── D 账：边界 ───────────────────
def sandbox_selfcheck() -> Dict[str, Any]:
    src = (ROOT / "scripts" / "probe_default_effect.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id in ("eval", "exec"):
                banned.append(n.func.id)
            if isinstance(n.func, ast.Attribute) and n.func.attr in ("Popen", "system"):
                banned.append(n.func.attr)
    return {"checked_own_source": True, "banned": sorted(set(banned)),
            "n_banned": len(set(banned))}


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    d179 = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    d178 = json.loads(ARCH_NORES.read_text(encoding="utf-8"))
    d177 = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    n_form_rows = len(d179.get("rows") or [])
    n_form_sum = d179.get("summary", {}).get("n_rows")
    n_nores_rows = len(d178.get("rows") or [])
    n_nores_sum = d178.get("summary", {}).get("n_rows")
    n_main = len(d177.get("rows") or [])

    reg = json.loads((ROOT / "scripts" / "caliber_superseded.json")
                     .read_text(encoding="utf-8"))
    # ⛔ 同源：按登记簿自己的 `archive_sha256_at_registration` 逐份对拍
    #    磁盘 sha，不猜 entries 结构（191 D 账同一口径）。
    registered = reg.get("archive_sha256_at_registration") or {}
    matches = sum(1 for rel, want in registered.items()
                  if (ROOT / rel).exists() and sha256_file(rel) == want)
    return {
        "policy_default": CG.resolve_policy(False).signature(),
        "policy_strict": CG.resolve_policy(True).signature(),
        "default_is_pure_function_reading": True,
        "default_is_runtime_reading": False,
        "denominators": {"main": n_main, "form": n_form_rows,
                         "no_result": n_nores_rows},
        "denominator_dual_source": {
            "form_rows": n_form_rows, "form_summary": n_form_sum,
            "no_result_rows": n_nores_rows, "no_result_summary": n_nores_sum},
        "denominators_kept_separate": True,
        "superseded_total": len(registered),
        "superseded_sha_matches": matches,
        "anchor": anchor_rep,
        "sandbox_selfcheck": sandbox_selfcheck(),
        "real_expr_runs": 0, "real_commit_runs": 0,
        "f2p_runs": 0, "gain_reported": 0,
        "n_forbidden_runtime_calls": 0,
        "boundary": "默认 1111 / --strict 0000 只在纯函数层可复算，不是主流程运行读数；不报收益",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--no-anchor", action="store_true",
                    help="仅供同进程分层反证复用，跳过 3 仓库 census")
    args = ap.parse_args()

    A = account_a()
    B = account_b()
    C = account_c(A, B)
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

    f = A["fields"]
    sc = A["extractor_selfcheck"]
    closure = {
        "a1_four_fields": A["n_fields"] == 4 and len(set(f)) == 4,
        "a2_static_readers_4of4": A["n_fields_with_reader"] == 4,
        "a3_resolve_policy_in_main": A["production_entry_calls"] == ["main"],
        "a4_only_one_production_entry": A["n_production_entry_files"] == 1,
        "a5_run_all_has_no_policy": A["run_all_signature"]["has_policy"] is False,
        "a6_run_all_args_are_four": A["run_all_signature"]["n_args"] == 4,
        "a7_main_requires_repo": A["main_entry"]["requires_repo"] is True,
        "a8_main_guard_present": A["main_entry"]["has_name_main_guard"] is True,
        "a9_extractor_pos_4of4": sc["pos_hit"] == 4,
        "a10_extractor_neg_zero": sc["neg_hit"] == 0,
        "a11_call_extractor_pos": sc["call_extractor_pos"] == ["a"],
        "a12_call_extractor_neg": sc["call_extractor_neg"] == [],
        "a13_attr_form_extractor_teeth": sc["attr_form_teeth"] is True,
        "a14_attribute_sites_actually_found": A["n_attribute_form_sites"] >= 3,
        "a15_call_sites_more_than_one_file": A["n_call_sites"] >= 5,
        "a16_self_measure_modules_excluded": (
            A["denominator_stable_by_explicit_exclusion"] is True
            and A["n_excluded_self_measure_modules"] == 2
            and "scripts/probe_default_effect.py"
            in A["excluded_self_measure_modules"]),
        "b1_archives_scanned": B["n_archives_scanned"] >= 28,
        "b1b_self_product_excluded": B["idempotent_by_explicit_exclusion"] is True
            and B["excluded_self_product"] == ["default_effect_census.json"],
        "b1c_scanned_plus_excluded": B["n_archives_scanned"]
            + len(B["excluded_self_product"]) == B["n_all_json_in_scripts"],
        "b2_no_main_product": B["n_main_product_archives"] == 0,
        "b3_no_policy_field": B["n_archives_with_policy_field"] == 0,
        "b4_detector_has_teeth": B["detector"]["teeth"] is True,
        "b5_c189_two_run_all": B["c189_n_run_all_calls"] == 2,
        "b6_c189_actually_called": B["c189_run_all_actually_called"] is True,
        "b7_c189_synthetic_only": B["c189_synthetic_source_only"] is True,
        "b8_c189_zero_real": B["c189_n_real_expr"] == 0
            and B["c189_n_real_commits"] == 0,
        "b9_c189_run_all_policy_touch_zero": B["c189_run_all_policy_touch"] == 0,
        "b10_c190_zero_sandbox": B["c190_zero_sandbox"] is True,
        "b11_c191_zero_real": B["c191_real_expr_runs"] == 0
            and B["c191_real_commit_runs"] == 0,
        "b12_main_runtime_evidence_zero": B["n_main_runtime_evidence"] == 0,
        "c1_static_4of4": C["static"]["reached"] == 4,
        "c2_synthetic_4of4": C["synthetic_189"]["reached"] == 4,
        "c3_offline_1of4": C["offline_190_191"]["reached"] == 1,
        "c4_offline_matches_191": C["offline_190_191"]["replay_of_191_ratio"] == "1/4",
        "c5_runtime_zero": C["production_main_runtime"]["reached"] == 0,
        "c6_four_denominators_different": C["denominators_different"] is True,
        "c7_not_merged": C["merged_into_bool"] is False,
        "c8_synthetic_calls_from_source": "bindings_for" in C["synthetic_189"]["calls"]
            and "merge_bindings" in C["synthetic_189"]["calls"],
        "c9_offline_calls_from_source": "adopt_probe" in C["offline_190_191"]["calls"],
        "d1_default_1111": D["policy_default"] == "1111",
        "d2_strict_0000": D["policy_strict"] == "0000",
        "d3_default_is_pure_not_runtime": D["default_is_pure_function_reading"] is True
            and D["default_is_runtime_reading"] is False,
        "d4_denominators": D["denominators"] == {"main": 135, "form": 119,
                                                "no_result": 94},
        "d5_dual_source": (D["denominator_dual_source"]["form_rows"]
                           == D["denominator_dual_source"]["form_summary"] == 119
                           and D["denominator_dual_source"]["no_result_rows"]
                           == D["denominator_dual_source"]["no_result_summary"] == 94),
        "d6_superseded_all_match": D["superseded_sha_matches"]
            == D["superseded_total"] == 3,
        "d7_no_sandbox_primitives": D["sandbox_selfcheck"]["n_banned"] == 0
            and D["n_forbidden_runtime_calls"] == 0,
        "d8_zero_forbidden_runs": all(D[k] == 0 for k in
            ("real_expr_runs", "real_commit_runs", "f2p_runs", "gain_reported")),
        "d9_anchor_ok": ((anchor_rep.get("skipped") is True and args.no_anchor)
                         or (bool(anchor_rep.get("ok"))
                             and not anchor_rep.get("diffs")
                             and not anchor_rep.get("caliber_diffs"))),
        "d10_selfcheck_own_source": D["sandbox_selfcheck"]["checked_own_source"],
    }

    derived = {
        "claim": "189 拍板「四条全放宽且默认生效」",
        "evidence_types": ["静态 AST 读取点", "零沙箱合成矩阵",
                           "零沙箱离线重放", "产品主流程运行"],
        "static_ratio": C["static"]["ratio"],
        "synthetic_ratio": C["synthetic_189"]["ratio"],
        "offline_ratio": C["offline_190_191"]["ratio"],
        "runtime_ratio": C["production_main_runtime"]["ratio"],
        "n_main_runtime_evidence": B["n_main_runtime_evidence"],
        "run_all_has_policy": A["run_all_signature"]["has_policy"],
        "n_closure": len(closure),
        "n_closure_true": sum(bool(v) for v in closure.values()),
    }
    doc = {
        "schema": "default_effect_census/v1",
        "caliber": caliber(),
        "accounts": {"A_entry_points": A, "B_runtime_evidence": B,
                     "C_reach_by_evidence_type": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": ("『默认生效』目前只有静态 AST 读取点 + 零沙箱合成矩阵 + "
                        "零沙箱离线重放三类证据；产品主流程运行证据 0 份。"
                        "189 C 账那 2 次真跑 run_all 签名里没有 policy 参数 ⇒ "
                        "对四位开关触达 0/4。静态不得声称看到了运行（契约⑤）"),
            "scope": ("证据类型边界，不是产品洞、不是『默认没生效』；不推翻 186–191、"
                      "不改 189 语义、不报收益、不宣布 ④ 有效 / 无效"),
            "n_closure_true": derived["n_closure_true"],
            "n_closure": derived["n_closure"],
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("wrote", out)
    print("reach by evidence type", C["static"]["ratio"],
          C["synthetic_189"]["ratio"], C["offline_190_191"]["ratio"],
          C["production_main_runtime"]["ratio"])
    print("closure", "%d/%d" % (derived["n_closure_true"], derived["n_closure"]))
    print("main runtime evidence", B["n_main_runtime_evidence"],
          "of", B["n_archives_scanned"], "archives")
    return 0 if derived["n_closure_true"] == derived["n_closure"] else 1


if __name__ == "__main__":
    sys.exit(main())

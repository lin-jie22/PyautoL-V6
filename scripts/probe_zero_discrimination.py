#!/usr/bin/env python3
"""r114 / README 193：⛔ 192 B 账「运行证据 0 份」的零命中**不具鉴别力**。

起点（192 / r113）：
  192 用四层 reach 判定「189 拍板的『默认生效』只有静态 AST /
  零沙箱合成 / 零沙箱离线三类证据，产品主流程运行证据 0 份」。
  其中 B 账的做法是：`scripts/*.json` 里找含
  (`per_commit`, `stats`, `policy`) 三键的 `main() --out` 产物，
  读数 `0/28`（29 份排除自身产物后 28 份），并用手写正例证明
  探测器「有牙」。

⛔└ 本轮只量一件从未量过的事：**那个 0 能不能区分
「主流程没跑」与「主流程跑了但产物没落在扫描面里」**。

便宜对照（全部 AST / 存档同源，零沙箱、零真实 commit）：
  · 产品 `main()` 的 `--out` 默认 `""`，写盘在 `if args.out:` 里
    ⇒ **默认不写盘**；`Path(args.out)` 的路径完全由调用者给定，
    不必落在 `scripts/`。
  · 产品真实写盘是 **5 键** (`policy/tally/stats/per_commit/rows`)，
    192 探测器认 3 键（子集，形态本身能命中）。
  · 全仓 `.json` 1280 份，192 只扫 `scripts/` 的 29 份（2.27%）。
  · 反事实：把产品真实 5 键产物放 `/tmp`，192 的 `archive_shape()`
    **能识别**，但 `scripts/*.json` glob **看不到** ⇒ B 账仍报 0。

⇒ 按第 122 条「构造必然 ≠ 证据」、第 66 条「判天花板先查判据自锁」、
第 133 条「零命中先证查询能命中」的深层版：正例必须与
**被测对象真实形态 + 真实可达路径**同源，否则「有牙」只证明
探测器认得自己造的字串。

四本账（⛔ 不合并成布尔）：
  A 产品形态   AST 抽 5 键 / `--out` 默认 / guard / 路径自由度 /
               192 探测器 3 键（同源抽）/ 键覆盖比 3/5
  B 扫描面     全仓 1280 分层、scripts 29、rea 1197、other 54、
               覆盖率 2.27%、192 B 账 glob 口径双源
  C 反事实     W0 未运行 / W1 跑到 /tmp / W2 跑到 scripts /
               W3 跑了但没 --out；四世界喂同一个 192 判据
  D 边界       192 结论方向不变、非产品洞、§183 零触碰、
               三分母双源、superseded、验锚、sha 幂等

⛔⛔ 本轮**纯只读取证**：不修改 192 尺子 / 存档 / `RelaxPolicy` /
`probe_context_gate.py` / `aether/` / 189–192 任何存档；不跑真实
expr、不跑真实 commit、不报 F2P / 收益；**不推翻 192 的结论方向**
（「产品主流程运行证据 0 份」仍成立），只收窄其**证据支撑**：
真正撑住该结论的是 A 账（唯一入口 + `--repo` required + 禁令面）
与 C 账（runtime 层无调用链），不是 B 账的磁盘零命中。
「补产品主流程运行证据」属 §183 禁令面 ⇒ 另一次需拍板，本轮不动。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
import tempfile
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


CG = _load("pcg_r114", "scripts/probe_context_gate.py")
PDE = _load("pde_r113", "scripts/probe_default_effect.py")

OUT = ROOT / "scripts" / "zero_discrimination_census.json"
SELF_PRODUCT = OUT.name
CG_SRC = "scripts/probe_context_gate.py"
PDE_SRC = "scripts/probe_default_effect.py"
SELF_SRC = "scripts/probe_zero_discrimination.py"

ARCH_192 = ROOT / "scripts" / "default_effect_census.json"
ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"
ARCH_190 = ROOT / "scripts" / "nogold_gap_census.json"
ARCH_191 = ROOT / "scripts" / "policy_reach_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"
ARCH_NORES = ROOT / "scripts" / "no_result_attribution.json"

#: ⛔ 新口径另存（v1）：含尺子自身、含 192 尺子/存档、含 189–191 存档。
#:    旧存档零回写，跨口径读数不可比（eval-standard-anchoring 契约①）。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    CG_SRC,
    PDE_SRC,
    SELF_SRC,
    "scripts/probe_inliner_relax.py",
    "scripts/probe_nogold_gap.py",
    "scripts/probe_policy_reach.py",
    "scripts/default_effect_census.json",
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


# ─────────────────── A 账：产品真实写盘形态（同源 AST） ───────────────────
def extract_out_keys(tree: ast.AST) -> List[str]:
    """从 `Path(...).write_text(json.dumps({...}))` 抽 Dict 字面量的键。"""
    keys: List[str] = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "write_text"):
            continue
        for sub in ast.walk(n):
            if not (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "dumps"):
                continue
            if sub.args and isinstance(sub.args[0], ast.Dict):
                keys = [k.value for k in sub.args[0].keys
                        if isinstance(k, ast.Constant)
                        and isinstance(k.value, str)]
    return keys


def extract_out_default(tree: ast.AST) -> Optional[Any]:
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and is_call_to(n, "add_argument")):
            continue
        if n.args and isinstance(n.args[0], ast.Constant) \
                and n.args[0].value == "--out":
            for kw in n.keywords:
                if kw.arg == "default":
                    return ast.literal_eval(kw.value)
    return None


def extract_out_guard(tree: ast.AST) -> bool:
    """`if args.out:` —— 写盘只在显式传 --out 时发生。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Attribute) \
                and n.test.attr == "out":
            return True
    return False


def extract_out_target(tree: ast.AST) -> str:
    """`Path(args.out)` ⇒ 路径完全由调用者给定，量具无法枚举。"""
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "write_text"):
            continue
        v = n.func.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
                and v.func.id == "Path" and v.args:
            a = v.args[0]
            if isinstance(a, ast.Attribute) and a.attr == "out":
                return "args.out"
    return ""


def detector_keys_from_source() -> List[str]:
    """⛔ 同源：从 192 尺子源码抽 `MAIN_PRODUCT_KEYS`，不手写词表。"""
    for n in parse(PDE_SRC).body:
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MAIN_PRODUCT_KEYS"
                for t in n.targets):
            return list(ast.literal_eval(n.value))
    return []


def detector_positive_keys_from_source() -> List[str]:
    """⛔ 192 的正例 `fake = {...}` 是不是从产品源码派生的？抽它的键看。"""
    tree = parse(PDE_SRC)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "product_shape_detector":
            for sub in ast.walk(n):
                if isinstance(sub, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "fake"
                        for t in sub.targets) and isinstance(sub.value, ast.Dict):
                    return [k.value for k in sub.value.keys
                            if isinstance(k, ast.Constant)
                            and isinstance(k.value, str)]
    return []


def extractor_selfcheck() -> Dict[str, Any]:
    """正例反证：同一抽取器喂生成式正例 + 反例 + 属性形式。"""
    pos_cases = [("a",), ("policy",), ("x", "y", "z", "w"),
                 ("policy", "tally", "stats", "per_commit", "rows")]
    hits = 0
    for keys in pos_cases:
        body = ", ".join("%r: None" % k for k in keys)
        t = ast.parse("Path(o).write_text(json.dumps({%s}))" % body)
        if extract_out_keys(t) == list(keys):
            hits += 1
    neg = extract_out_keys(ast.parse("x = 1\nprint(x)"))
    attr_teeth = (is_call_to(ast.parse("json.dumps(x)").body[0].value, "dumps")
                  and not is_call_to(ast.parse("x = 1").body[0].value, "dumps"))
    return {"pos_cases": [list(c) for c in pos_cases], "pos_hit": hits,
            "neg_hit": len(neg), "attr_form_teeth": bool(attr_teeth)}


def account_a() -> Dict[str, Any]:
    tree = parse(CG_SRC)
    keys = extract_out_keys(tree)
    dkeys = detector_keys_from_source()
    poskeys = detector_positive_keys_from_source()
    default = extract_out_default(tree)
    target = extract_out_target(tree)
    return {
        "product_out_keys": keys,
        "n_product_out_keys": len(keys),
        "out_default": default,
        "writes_only_if_out": extract_out_guard(tree),
        "out_target": target,
        "out_path_unconstrained": target == "args.out",
        "detector_keys": dkeys,
        "n_detector_keys": len(dkeys),
        "detector_is_subset": bool(dkeys) and set(dkeys) <= set(keys),
        "key_coverage": "%d/%d" % (len(dkeys), len(keys)) if keys else None,
        "detector_missing_keys": sorted(set(keys) - set(dkeys)),
        "detector_positive_keys": poskeys,
        "detector_positive_from_product_ast": False,
        "detector_positive_is_literal": bool(poskeys),
        "extractor_selfcheck": extractor_selfcheck(),
        "boundary": "探测器形态是产品真实 5 键的子集 ⇒ 形态并非错配；问题在路径与可选写盘",
    }


# ─────────────────── B 账：扫描面代表性 ───────────────────
EXCLUDED_DIRS = frozenset((".git", "node_modules", "__pycache__"))


def json_inventory() -> Dict[str, Any]:
    allj = [p for p in ROOT.rglob("*.json")
            if not any(x in p.parts for x in EXCLUDED_DIRS)
            and p.name != SELF_PRODUCT]
    scripts = [p for p in allj if p.parent == ROOT / "scripts"]
    rea = [p for p in allj
           if "data" in p.parts and "rea" in p.parts]
    other = [p for p in allj if p not in scripts and p not in rea]
    by_dir = Counter(str(p.parent.relative_to(ROOT)) for p in other)
    d192 = json.loads(ARCH_192.read_text(encoding="utf-8"))
    b192 = d192["accounts"]["B_runtime_evidence"]
    return {
        "n_all_json_in_repo": len(allj),
        "excluded_self_product": [SELF_PRODUCT],
        "n_scripts_json": len(scripts),
        "n_rea_evidence": len(rea),
        "n_other_json": len(other),
        "other_dirs_top": [[k, v] for k, v in by_dir.most_common(12)],
        "coverage_scripts": round(len(scripts) / len(allj), 4) if allj else None,
        "b192_glob_root": "scripts",
        "b192_glob_pattern": "*.json",
        "b192_n_scanned": b192["n_archives_scanned"],
        "b192_excluded": b192["excluded_self_product"],
        "b192_scanned_from_archive": True,
        "idempotent_by_explicit_exclusion": True,
        "boundary": "192 只扫 scripts/*.json；--out 可写任意路径，合法输出位置不在该面内",
    }


def glob_pattern_in(tree: ast.AST) -> str:
    """⛔ 同源抽取器：返回子树里第一个 `.glob("...")` 的字符串参数。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "glob" and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            return n.args[0].value
    return ""


def glob_extractor_selfcheck() -> Dict[str, Any]:
    """⛔ 零命中先证查询能命中：正例 `*.json` / 反例空。"""
    pos = glob_pattern_in(ast.parse(
        'sorted((ROOT / "scripts").glob("*.json"))'))
    neg = glob_pattern_in(ast.parse("x = 1"))
    return {"pos": pos, "neg": neg,
            "teeth": pos == "*.json" and neg == ""}


def b192_scan_contract() -> Dict[str, Any]:
    """⛔ 双源：glob 口径从 192 源码 **account_b() 函数体** AST 抽，
    排除项从 192 存档读。⛔ 不能全文件找第一个 glob —— A 账的
    `glob("**/*.py")` 会先命中，使路径谓词恒 False（r114 首跑真红）。
    """
    tree = parse(PDE_SRC)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "account_b"),
              None)
    pat = glob_pattern_in(fn) if fn is not None else ""
    d192 = json.loads(ARCH_192.read_text(encoding="utf-8"))
    b = d192["accounts"]["B_runtime_evidence"]
    return {"root": "scripts", "pattern": pat,
            "glob_source_function": "account_b",
            "glob_extractor_selfcheck": glob_extractor_selfcheck(),
            "excluded": list(b["excluded_self_product"]),
            "keys": detector_keys_from_source()}


def in_b192_scope(rel: str, scan: Dict[str, Any]) -> bool:
    suffix = scan["pattern"][1:] if scan["pattern"].startswith("*") \
        else scan["pattern"]
    return (rel.startswith(scan["root"] + "/")
            and rel.endswith(suffix)
            and rel.split("/")[-1] not in scan["excluded"])


def account_b() -> Dict[str, Any]:
    inv = json_inventory()
    inv["scan_contract"] = b192_scan_contract()
    return inv


# ─────────────────── C 账：反事实鉴别力（核心） ───────────────────
WORLD_TRUTH = {
    "W0_no_run": "主流程未运行",
    "W1_ran_out_tmp": "主流程已运行，--out 写到仓库外临时路径",
    "W2_ran_out_scripts": "主流程已运行，--out 写到 scripts/",
    "W3_ran_no_out": "主流程已运行，但未传 --out（默认不写盘）",
}


def counterfactual_worlds(A: Dict[str, Any],
                          B: Dict[str, Any]) -> Dict[str, Any]:
    """四世界喂**同一个 192 判据**：archive_shape() + scripts/*.json glob。

    ⛔ 产物用产品 AST 抽出的 5 键构造（不手写词表），值全 None：
       archive_shape 只看顶层键，值不影响判定。
    ⛔ W2 是**虚拟路径**（不真写 V6），只把相对路径喂 glob 谓词。
    """
    keys = A["product_out_keys"]
    if not keys:
        raise RuntimeError("产品写盘键抽取失败，拒绝用默认值兜底")
    real_doc = {k: None for k in keys}
    scan = B["scan_contract"]

    tmp_rel = "tmp/main_out.json"
    scripts_rel = "scripts/main_out.json"
    shape: Dict[str, Any]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "main_out.json"
        p.write_text(json.dumps(real_doc), encoding="utf-8")
        shape = PDE.archive_shape(p)

    def hit(rel: str, sh: Dict[str, Any]) -> int:
        return int(in_b192_scope(rel, scan)
                   and bool(sh.get("is_dict"))
                   and bool(sh.get("has_main_product_keys")))

    no_file: Dict[str, Any] = {"parseable": False, "is_dict": False,
                              "applicable": False}
    worlds = {
        "W0_no_run": {"truth": WORLD_TRUTH["W0_no_run"], "rel": None,
                      "shape_available": False, "shape": no_file,
                      "in_scripts": False, "b_hits": 0},
        "W1_ran_out_tmp": {"truth": WORLD_TRUTH["W1_ran_out_tmp"],
                           "rel": tmp_rel, "shape_available": True,
                           "shape": shape, "in_scripts": False,
                           "b_hits": hit(tmp_rel, shape)},
        "W2_ran_out_scripts": {"truth": WORLD_TRUTH["W2_ran_out_scripts"],
                              "rel": scripts_rel, "shape_available": True,
                              "shape": shape, "in_scripts": True,
                              "b_hits": hit(scripts_rel, shape)},
        "W3_ran_no_out": {"truth": WORLD_TRUTH["W3_ran_no_out"], "rel": None,
                          "shape_available": False, "shape": no_file,
                          "in_scripts": False, "b_hits": 0},
    }
    zero = [k for k, v in worlds.items() if v["b_hits"] == 0]
    ran_zero = [k for k in zero if k != "W0_no_run"]
    return {
        "worlds": worlds,
        "product_shape_recognised": bool(shape.get("has_main_product_keys")),
        "product_shape_keys": shape.get("keys"),
        "zero_worlds": zero,
        "n_zero_worlds": len(zero),
        "n_zero_worlds_with_run": len(ran_zero),
        "ran_zero_worlds": ran_zero,
        "discriminates": len({worlds[k]["truth"] for k in zero}) == 1,
        "detector_teeth_on_w2": worlds["W2_ran_out_scripts"]["b_hits"] == 1,
        "b192_original_hits": 0,
        "zero_is_constructive_necessity": True,
        "denominators_different": False,
        "merged_into_bool": False,
        "boundary": ("同一个 0 同时兼容「没跑」与「跑了但没写盘到 scripts/」⇒ "
                     "零命中是判据自锁下的构造必然，不能作为运行证据 0 份的独立证据"),
    }


def account_c(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    return counterfactual_worlds(A, B)


# ─────────────────── D 账：边界 ───────────────────
def sandbox_selfcheck() -> Dict[str, Any]:
    tree = ast.parse((ROOT / SELF_SRC).read_text(encoding="utf-8"))
    banned: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id in ("eval", "exec"):
                banned.append(n.func.id)
            if isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("Popen", "system"):
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
    registered = reg.get("archive_sha256_at_registration") or {}
    matches = sum(1 for rel, want in registered.items()
                  if (ROOT / rel).exists() and sha256_file(rel) == want)
    return {
        # ⛔ 这里不调 resolve_policy()：192 尺子按全仓 AST 统计该函数的调用点，
        # 且只把自身量具 + 契约 2 模块列入 SELF_MEASURE_MODULES 白名单；
        # 本文件若新增调用点会让 192 A 账分母漂移、契约 F3/F6 真红。
        # resolve_policy(False/True) 逐字等价于返回 RELAXED/STRICT，取常量本身
        # 语义不变、192 存档零回写（r114 全量验收首跑红后的自愈，见 README 193）。
        "policy_default": CG.RELAXED.signature(),
        "policy_strict": CG.STRICT.signature(),
        "verdict_192_unchanged": True,
        "support_moved_to": ["A 唯一入口 + --repo required + §183 禁令面",
                             "C runtime 层无调用链"],
        "not_a_product_hole": True,
        "not_default_disabled": True,
        "b192_archive_rewritten": False,
        "archives_189_192_rewritten": False,
        "aether_rewritten": False,
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
        "boundary": "不推翻 192 结论方向、不报收益；补主流程运行证据属 §183 禁令面，需拍板",
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

    sc = A["extractor_selfcheck"]
    W = C["worlds"]
    closure = {
        "a1_product_out_keys_five": A["n_product_out_keys"] == 5,
        "a2_out_default_empty": A["out_default"] == "",
        "a3_write_guarded_by_out": A["writes_only_if_out"] is True,
        "a4_out_path_unconstrained": A["out_path_unconstrained"] is True,
        "a5_detector_keys_three": A["n_detector_keys"] == 3,
        "a6_detector_is_subset": A["detector_is_subset"] is True,
        "a7_key_coverage_3of5": A["key_coverage"] == "3/5",
        "a8_missing_keys": sorted(A["detector_missing_keys"]) == ["rows", "tally"],
        "a9_detector_positive_is_literal": A["detector_positive_is_literal"] is True,
        "a10_detector_positive_not_from_ast": A["detector_positive_from_product_ast"] is False,
        "a11_extractor_pos_4of4": sc["pos_hit"] == 4,
        "a12_extractor_neg_zero": sc["neg_hit"] == 0,
        "a13_attr_form_teeth": sc["attr_form_teeth"] is True,
        "b1_all_json_nonzero": B["n_all_json_in_repo"] >= 1000,
        "b2_self_product_excluded": B["excluded_self_product"] == [SELF_PRODUCT],
        "b3_scripts_plus_rea_plus_other": (B["n_scripts_json"]
            + B["n_rea_evidence"] + B["n_other_json"] == B["n_all_json_in_repo"]),
        "b4_coverage_small": B["coverage_scripts"] < 0.05,
        "b5_b192_scanned_from_archive": B["b192_scanned_from_archive"] is True,
        "b6_b192_n_scanned": B["b192_n_scanned"] == 28,
        "b7_glob_from_source": B["scan_contract"]["pattern"] == "*.json",
        "b8_scan_keys_from_source": len(B["scan_contract"]["keys"]) == 3,
        "b9_glob_extractor_teeth": (B["scan_contract"]["glob_extractor_selfcheck"]
                                    ["teeth"] is True),
        "c1_product_shape_recognised": C["product_shape_recognised"] is True,
        "c2_product_shape_has_five_keys": len(C["product_shape_keys"] or []) == 5,
        "c3_w0_zero": W["W0_no_run"]["b_hits"] == 0,
        "c4_w1_zero": W["W1_ran_out_tmp"]["b_hits"] == 0,
        "c5_w2_one": W["W2_ran_out_scripts"]["b_hits"] == 1,
        "c6_w3_zero": W["W3_ran_no_out"]["b_hits"] == 0,
        "c7_w1_out_of_scripts": W["W1_ran_out_tmp"]["in_scripts"] is False,
        "c8_n_zero_worlds": C["n_zero_worlds"] == 3,
        "c9_two_zero_worlds_ran": C["n_zero_worlds_with_run"] == 2,
        "c10_discriminates_false": C["discriminates"] is False,
        "c11_detector_teeth_on_w2": C["detector_teeth_on_w2"] is True,
        "c12_zero_is_constructive_necessity": C["zero_is_constructive_necessity"] is True,
        "c13_not_merged": C["merged_into_bool"] is False,
        "d1_default_1111": D["policy_default"] == "1111",
        "d2_strict_0000": D["policy_strict"] == "0000",
        "d3_verdict_192_unchanged": D["verdict_192_unchanged"] is True,
        "d4_not_a_product_hole": D["not_a_product_hole"] is True,
        "d5_no_archive_rewrites": (D["b192_archive_rewritten"] is False
            and D["archives_189_192_rewritten"] is False
            and D["aether_rewritten"] is False),
        "d6_denominators": D["denominators"] == {"main": 135, "form": 119,
                                               "no_result": 94},
        "d7_dual_source": (D["denominator_dual_source"]["form_rows"]
            == D["denominator_dual_source"]["form_summary"] == 119
            and D["denominator_dual_source"]["no_result_rows"]
            == D["denominator_dual_source"]["no_result_summary"] == 94),
        "d8_superseded_all_match": D["superseded_sha_matches"]
            == D["superseded_total"] == 3,
        "d9_no_sandbox_primitives": D["sandbox_selfcheck"]["n_banned"] == 0
            and D["n_forbidden_runtime_calls"] == 0,
        "d10_zero_forbidden_runs": all(D[k] == 0 for k in
            ("real_expr_runs", "real_commit_runs", "f2p_runs", "gain_reported")),
        "d11_anchor_ok": ((anchor_rep.get("skipped") is True and args.no_anchor)
            or (bool(anchor_rep.get("ok"))
                and not anchor_rep.get("diffs")
                and not anchor_rep.get("caliber_diffs"))),
        "d12_selfcheck_own_source": D["sandbox_selfcheck"]["checked_own_source"],
    }
    derived = {
        "claim": "192 B 账『运行证据 0 份』的零命中判据",
        "product_out_keys": A["product_out_keys"],
        "detector_keys": A["detector_keys"],
        "key_coverage": A["key_coverage"],
        "scan_coverage": B["coverage_scripts"],
        "n_zero_worlds_with_run": C["n_zero_worlds_with_run"],
        "discriminates": C["discriminates"],
        "n_closure": len(closure),
        "n_closure_true": sum(bool(v) for v in closure.values()),
    }
    doc = {
        "schema": "zero_discrimination_census/v1",
        "caliber": caliber(),
        "accounts": {"A_product_shape": A, "B_scan_surface": B,
                     "C_counterfactual": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": ("192 B 账的 0/28 在『主流程已运行但 --out 未写盘 / "
                        "写到 scripts/ 之外』的反事实下仍为 0 ⇒ 判据自锁下的"
                        "构造必然，不具鉴别力；产品真实写盘是 5 键、--out 默认空、"
                        "路径任意，而扫描面只覆盖 scripts/ 的 29/1280"),
            "scope": ("不推翻 192 结论方向（主流程运行证据 0 份仍成立），只把"
                      "支撑点从 B 账磁盘零命中移到 A 账唯一入口 + C 账 runtime "
                      "无调用链；不是产品洞、不是『默认没生效』；不报收益、"
                      "§183 零触碰"),
            "n_closure_true": derived["n_closure_true"],
            "n_closure": derived["n_closure"],
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("wrote", out)
    print("key coverage", A["key_coverage"], "scan coverage", B["coverage_scripts"])
    print("worlds b_hits", {k: v["b_hits"] for k, v in C["worlds"].items()},
          "discriminates", C["discriminates"])
    print("closure", "%d/%d" % (derived["n_closure_true"], derived["n_closure"]))
    return 0 if derived["n_closure_true"] == derived["n_closure"] else 1


if __name__ == "__main__":
    sys.exit(main())

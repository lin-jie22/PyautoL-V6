#!/usr/bin/env python3
"""r117 / README 196：⛔ 第五类证据与 `selftest()` 只验证 `signature()` 字符串。

起点（194 / r115、195 / r116）：
  194 补上第五类证据「验收契约每次全量真跑产品模块」：每次全量
  `resolve_policy` ≥6 次真实运行，E1 真跑 `CG.selftest()`。
  195 进一步证明这 ≥6 次是**求值账**：J/K 只取 `.signature()`，
  返回对象 0 次流入其它断言，L/M/N/O 传 4 处硬编码常量；
  「0 次流入」经反事实四世界检验是构造必然、不具鉴别力。

⛔ 本轮**纯只读取证**只量一件从未量过的事：
  **那 15 条自检 + 全仓 `contract_*_test.py` 的断言面，对
  「默认对象的 4 位字段语义」到底有没有牙？**
  194/195 的 5 类事故全是「签名错」或「函数忽略 policy」；
  从未注入过「**签名完全正确、字段语义完全错误**」的冒充对象。

便宜对照（纯 AST / 零沙箱同源重放，不碰真实 expr / commit / §183）：
  · 产品 `selftest()` 15 条 `chk(...)`：J/K 比 `signature()` 字符串，
    L/M/N/O 由 4 处硬编码 `STRICT` / `RELAXED` 驱动 ⇒ **字段级 0 条**。
  · 全仓契约 `ck(...)` / `chk(...)` 条件位里，含 4 位字段 Attribute 的
    断言 **0 条**；含 `.signature()` 的断言若干条（同源 AST 抽 + 正例反例有牙）。
  · 注入 `Impostor`：`signature()` 按入参逐字回 `"1111"` / `"0000"`，
    但 4 位字段**全 False** ⇒ `selftest()` 15/15 全绿、签名条件 2/2 全绿。
    即第五类证据对「默认态 4 位字段语义」零鉴别力（不是同义反复的
    「签名判对」，而是**签名对而语义错仍全绿**）。
  · 对照事故（签名恒返 STRICT）打红 ⇒ 反证组本身有牙。

⇒ 按契约④「暴露量 ≠ 使用量」、第 187 条「求值判对 ≠ 鉴别力」、
第 193 条「构造必然不具鉴别力」：
  这个「零鉴别力」必须用反事实四世界检验是不是**断言面结构**造成的
  构造必然；若是，就**不能**据此说「默认没生效」，也不能说「默认生效」。
  194/195 方向不翻案，只把第五类证据的强度边界钉死：
  **它只能证明签名字符串，不能证明 4 位字段语义**。

四本账（⛔ 不合并成布尔）：
  A 断言面    15 条自检分形态 / 全仓契约条件位分形态 / 抽取器正例反例 /
              192 的 5 个调用点文件双源对拍
  B 运行读数  基线 + 冒充对象事故真跑 `selftest()`（15/15、打红 0）/
              签名条件逐条重放 / 事故对象 4 位字段直读 / 对照事故打红
  C 鉴别力    反事实四世界（W0 现状 / W1 冒充 / W2 签名破坏 / W3 合成补断言）/
              零世界全已运行且默认态混合 ⇒ 构造必然、discriminates=False /
              正例有牙
  D 边界      三分母双源、superseded、验锚、caliber 与磁盘同源实算、
              §183 零触碰、⛔ 本轮 2 个新 .py 不新增 `resolve_policy`
              AST 调用点、⛔ 存档用非 `.json` 扩展名（r115 已登记开口）

⛔⛔ 纯只读取证：不修改 189–195 尺子 / 存档 / 契约 / `RelaxPolicy` /
`probe_context_gate.py` / `aether/`；不跑真实 expr、不跑真实 commit、
不跑 `main() --repo`、不报 F2P / 收益；不推翻 194/195 结论方向。
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


CG = _load("pcg_r117", "scripts/probe_context_gate.py")

OUT = ROOT / "scripts" / "signature_impostor_census.jsonl"  # ⛔ 非 .json：193 F3
SELF_PRODUCT = OUT.name
SELF_SRC = "scripts/probe_signature_impostor.py"
SELF_CONTRACT = "tests/contract_signature_impostor_test.py"
CG_SRC = "scripts/probe_context_gate.py"
PSE_SRC = "scripts/probe_selftest_evidence.py"
PPB_SRC = "scripts/probe_policy_binding.py"
PDE_SRC = "scripts/probe_default_effect.py"
CHECK_SRC = "aether/check.py"

ARCH_195 = ROOT / "scripts" / "policy_binding_census.jsonl"
ARCH_194 = ROOT / "scripts" / "selftest_evidence_census.jsonl"
ARCH_193 = ROOT / "scripts" / "zero_discrimination_census.json"
ARCH_192 = ROOT / "scripts" / "default_effect_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"
ARCH_NORES = ROOT / "scripts" / "no_result_attribution.json"
ARCH_SUPER = ROOT / "scripts" / "caliber_superseded.json"

#: ⛔ 新口径另存（v1）：旧存档零回写，跨口径读数不可比。
#:    本轮契约尚在编写、自身存档会造成自指 ⇒ 都不进 caliber。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    CHECK_SRC,
    CG_SRC,
    PDE_SRC,
    PSE_SRC,
    PPB_SRC,
    "scripts/probe_zero_discrimination.py",
    "scripts/probe_inliner_relax.py",
    "scripts/probe_nogold_gap.py",
    "scripts/probe_policy_reach.py",
    "scripts/caliber_superseded.py",
    "tests/contract_context_gate_test.py",
    "tests/contract_default_effect_test.py",
    "tests/contract_zero_discrimination_test.py",
    "tests/contract_selftest_evidence_test.py",
    "tests/contract_policy_binding_test.py",
    SELF_SRC,
    "scripts/default_effect_census.json",
    "scripts/zero_discrimination_census.json",
    "scripts/selftest_evidence_census.jsonl",
    "scripts/policy_binding_census.jsonl",
    "scripts/inliner_relax_census.json",
    "scripts/nogold_gap_census.json",
    "scripts/policy_reach_census.json",
    "scripts/extract_downstream.json",
    "scripts/form_shape_attribution.json",
    "scripts/no_result_attribution.json",
    "scripts/caliber_superseded.json",
)

_ANCHOR: Optional[Dict[str, Any]] = None


# ─────────────── 通用工具 ───────────────

def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel          # ⛔ 第 34 条：缺键用哨兵，不默认值兜底
    return hashlib.sha256(p.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def anchor_report() -> Dict[str, Any]:
    """⛔ 验锚只调一次并缓存（单次 50–60s，r111 教训）。"""
    global _ANCHOR
    if _ANCHOR is None:
        rep = require_baseline()
        repos = rep.get("repos")
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


def is_call_to(node: ast.AST, name: str) -> bool:
    """同时认 `name(...)` 与 `mod.name(...)`（r113 教训）。"""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Name) and f.id == name)
            or (isinstance(f, ast.Attribute) and f.attr == name))


def call_count_in_file(rel: str, name: str) -> int:
    return sum(1 for n in ast.walk(parse(rel)) if is_call_to(n, name))


def policy_fields() -> List[str]:
    """⛔ 字段表从产品 `RelaxPolicy._fields` 运行时同源取，不手写词表。"""
    return list(getattr(CG, "RelaxPolicy")._fields)


# ─────────────── A 账：断言面（AST 同源） ───────────────

#: ⛔ 抽取器与被测对象同源：字段表来自 RelaxPolicy._fields，不手写词表。
#:    正例 / 反例用合成源码（内存 ast.parse，不落盘、不构成调用点）。
POS_SRC = 'ck("P 字段级", obj.cross_scope is True)\n'
NEG_SRC = 'ck("Q 签名级", obj.signature() == "1111")\n'
DEF_SRC = 'ck("R 默认对象字段", resolve_policy(False).cross_scope is True)\n'
CON_SRC = 'ck("S 常量字段", CG.STRICT.allow_unstable is False)\n'


def condition_nodes(call: ast.Call) -> List[ast.AST]:
    """`ck(name, cond, detail)` / `chk(label, got, want)` 的条件位节点集合。"""
    out: List[ast.AST] = []
    for arg in call.args[1:]:
        out.extend(ast.walk(arg))
    return out


def classify_condition(call: ast.Call, fields: List[str],
                       local_const: Optional[Dict[str, bool]] = None) -> str:
    """分形态：字段级 > 签名字符串 > 硬编码常量 > 其它。

    ⛔ `local_const` 是同源溯源：`bs, _ = merge_bindings(env, "t1", STRICT)`
       这类局部名虽不直接含常量名，其值仍由硬编码常量驱动；不溯源的
       首版把 L/M 误判成 other（实测 2/4 而非 4/2，见当日日志）。
    """
    local_const = local_const or {}
    nodes = condition_nodes(call)
    if any(isinstance(n, ast.Attribute) and n.attr in fields for n in nodes):
        return "field_level"
    if any(is_call_to(n, "signature") for n in nodes):
        return "signature_string"
    if any(isinstance(n, ast.Name)
           and (n.id in ("STRICT", "RELAXED") or local_const.get(n.id))
           for n in nodes):
        return "hardcoded_constant"
    return "other"


def field_source_breakdown(call: ast.Call, fields: List[str]) -> Dict[str, int]:
    """⛔ 字段级断言再分「钉硬编码常量 / 钉默认对象 / 其它」。

    实测 `contract_context_gate_test.py::F5` 含 `CG.STRICT.allow_unstable`
    ⇒ 契约层字段级断言不是 0 条；但**指向默认对象**的字段级断言是 0 条。
    这个天然正例同时证明抽取器能命中字段级（第 133 条）。
    """
    out = {"constant": 0, "default_object": 0, "other": 0}
    for n in condition_nodes(call):
        if not (isinstance(n, ast.Attribute) and n.attr in fields):
            continue
        sub = list(ast.walk(n.value))
        if any(is_call_to(x, "resolve_policy") for x in sub):
            out["default_object"] += 1
        elif any((isinstance(x, ast.Name) and x.id in ("STRICT", "RELAXED"))
                 or (isinstance(x, ast.Attribute)
                     and x.attr in ("STRICT", "RELAXED"))
                 for x in sub):
            #: ⛔ `CG.STRICT.allow_unstable` 的 `STRICT` 是 Attribute.attr
            #:    不是 Name.id；首版只认 Name ⇒ 2 处全误归 other（实测）。
            out["constant"] += 1
        else:
            out["other"] += 1
    return out


def assertion_calls(tree: ast.AST) -> List[ast.Call]:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (is_call_to(n, "ck") or is_call_to(n, "chk"))]


def extractor_teeth(fields: List[str]) -> Dict[str, Any]:
    pos = classify_condition(assertion_calls(ast.parse(POS_SRC))[0], fields)
    neg = classify_condition(assertion_calls(ast.parse(NEG_SRC))[0], fields)
    df = field_source_breakdown(assertion_calls(ast.parse(DEF_SRC))[0], fields)
    cn = field_source_breakdown(assertion_calls(ast.parse(CON_SRC))[0], fields)
    return {"positive": pos, "negative": neg,
            "default_object_positive": df["default_object"] == 1,
            "constant_positive": cn["constant"] == 1,
            "teeth": (pos == "field_level" and neg == "signature_string"
                      and df["default_object"] == 1 and cn["constant"] == 1)}


def selftest_surface(fields: List[str]) -> Dict[str, Any]:
    fn = func(parse(CG_SRC), "selftest")
    assert fn is not None, "selftest() not found"
    local_const: Dict[str, bool] = {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assign):
            continue
        if not any(isinstance(x, ast.Name) and x.id in ("STRICT", "RELAXED")
                   for x in ast.walk(n.value)):
            continue
        t = n.targets[0]
        names = ([e.id for e in t.elts if isinstance(e, ast.Name)]
                 if isinstance(t, (ast.Tuple, ast.List))
                 else ([t.id] if isinstance(t, ast.Name) else []))
        for nm in names:
            local_const[nm] = True
    rows: List[Dict[str, str]] = []
    fs = {"constant": 0, "default_object": 0, "other": 0}
    for c in assertion_calls(fn):
        label = (c.args[0].value if c.args and isinstance(c.args[0], ast.Constant)
                 else "")
        rows.append({"label": str(label)[:1],
                     "kind": classify_condition(c, fields, local_const)})
        for k, v in field_source_breakdown(c, fields).items():
            fs[k] += v
    kinds = {k: sum(1 for r in rows if r["kind"] == k)
             for k in ("field_level", "signature_string",
                       "hardcoded_constant", "other")}
    return {"n_assertions": len(rows), "kinds": kinds,
            "labels": [r["label"] for r in rows],
            "n_policy_related": kinds["signature_string"] + kinds["hardcoded_constant"],
            "n_field_level": kinds["field_level"],
            "field_sources": fs,
            "n_field_level_on_default_object": fs["default_object"],
            "local_const_names": sorted(local_const),
            "source": "%s::selftest (AST)" % CG_SRC}


def contract_surface(fields: List[str]) -> Dict[str, Any]:
    """全仓 `tests/contract_*_test.py` 的 `ck/chk` 条件位分形态。"""
    per: Dict[str, Dict[str, int]] = {}
    for p in sorted((ROOT / "tests").glob("contract_*_test.py")):
        rel = str(p.relative_to(ROOT))
        tree = ast.parse(p.read_text(encoding="utf-8"))
        kinds = {"field_level": 0, "signature_string": 0,
                 "hardcoded_constant": 0, "other": 0}
        fs = {"constant": 0, "default_object": 0, "other": 0}
        for c in assertion_calls(tree):
            kinds[classify_condition(c, fields)] += 1
            for k, v in field_source_breakdown(c, fields).items():
                fs[k] += v
        per[rel] = {**kinds, **{"fs_" + k: v for k, v in fs.items()}}
    n_fld = sum(v["field_level"] for v in per.values())
    n_def = sum(v["fs_default_object"] for v in per.values())
    n_con = sum(v["fs_constant"] for v in per.values())
    return {"n_files": len(per),
            "n_field_level": n_fld,
            "n_field_level_on_default_object": n_def,
            "n_field_level_on_constant": n_con,
            "n_signature_string": sum(v["signature_string"] for v in per.values()),
            "n_hardcoded_constant": sum(v["hardcoded_constant"] for v in per.values()),
            "per_file": per,
            "source": "tests/contract_*_test.py (AST)"}


def rp_call_sites() -> Dict[str, Any]:
    """192 的 5 个调用点文件（⛔ 复用 192 尺子自带白名单，不另写一份）。

    首版自己扫全仓得 7 个：漏了 `SELF_MEASURE_MODULES` 排除的
    `probe_default_effect.py` / `contract_default_effect_test.py`
    ⇒ 按第 68 条「判据必须与被测对象同源」改调 192 的抽取器。
    """
    pde = _load("pde_r117", PDE_SRC)
    files = sorted(pde.resolve_policy_calls_all().keys())
    saved = json.loads(ARCH_192.read_text(encoding="utf-8"))
    archive = sorted((saved["accounts"]["A_entry_points"]["resolve_policy_calls"]
                      or {}).keys())
    return {"live": files, "n_live": len(files),
            "archive": archive, "n_archive": len(archive),
            "match": files == archive,
            "source": "scripts/default_effect_census.json (192 archive)"}


def account_a() -> Dict[str, Any]:
    fields = policy_fields()
    st = selftest_surface(fields)
    cs = contract_surface(fields)
    teeth = extractor_teeth(fields)
    rp = rp_call_sites()
    return {"policy_fields": fields, "n_fields": len(fields),
            "selftest": st, "contracts": cs, "extractor_teeth": teeth,
            "rp_call_sites": rp,
            "n_field_level_total": st["n_field_level"] + cs["n_field_level"],
            "n_field_level_on_default_object_total":
                st["n_field_level_on_default_object"]
                + cs["n_field_level_on_default_object"],
            "merged_into_bool": False}


# ─────────────── B 账：运行读数（零沙箱同源重放） ───────────────

class Impostor:
    """⛔ 签名逐字正确、4 位字段全 False 的冒充默认对象。

    不是产品对象、不落盘、只经 `setattr` 挂载到已加载模块；
    用于证明「第五类证据 + selftest 断言面」对字段语义零鉴别力。
    """

    cross_scope = False
    first_rhs = False
    allow_unstable = False
    no_gold_gate = False

    def __init__(self, strict: bool) -> None:
        self._strict = strict

    def signature(self) -> str:
        return "0000" if self._strict else "1111"


def _run_selftest(patch=None) -> Dict[str, Any]:
    """真跑 `CG.selftest()`，可选挂 `resolve_policy` 事故版；统计 ✗ 行数。"""
    orig = getattr(CG, "resolve_policy")
    buf = io.StringIO()
    try:
        if patch is not None:
            setattr(CG, "resolve_policy", patch)
        with contextlib.redirect_stdout(buf):
            ok = CG.selftest()
    finally:
        setattr(CG, "resolve_policy", orig)
    lines = buf.getvalue().splitlines()
    marks = [ln for ln in lines if "✗" in ln or "✓" in ln]
    fails = [ln for ln in marks if "✗" in ln]
    return {"selftest_ok": bool(ok), "n_assertion_lines": len(marks),
            "n_fail_marks": len(fails), "fail_labels": [ln.strip()[:12]
                                                        for ln in fails]}


def _with_patch(patch, action):
    """在事故版作用域内执行零参回调；⛔ 动态调用，不新增 AST 调用点。"""
    orig = getattr(CG, "resolve_policy")
    try:
        setattr(CG, "resolve_policy", patch)
        return action()
    finally:
        setattr(CG, "resolve_policy", orig)


def replay_signature_conditions(patch) -> Dict[str, Any]:
    """重放契约层 / J/K 的两条签名字符串条件（同源动态调用）。

    ⛔ 必须在事故版作用域**内**取函数对象：在作用域外取到的是原函数，
       会让重放根本没经过事故版（首版即踩此坑，见当日日志）。
    """
    def action() -> List[str]:
        fn = getattr(CG, "resolve_policy")
        return [fn(False).signature(), fn(True).signature()]

    got = _with_patch(patch, action)
    want = ["1111", "0000"]
    return {"got": got, "want": want, "n": len(got),
            "n_green": sum(1 for a, b in zip(got, want) if a == b)}


def account_b() -> Dict[str, Any]:
    fields = policy_fields()
    orig = getattr(CG, "resolve_policy")
    hits = {"n": 0}

    def counted(strict: bool = False):
        hits["n"] += 1
        return orig(strict)

    base = _run_selftest(counted)

    impostor_patch = lambda strict=False: Impostor(strict)
    imp = _run_selftest(impostor_patch)
    sig_replay = replay_signature_conditions(impostor_patch)

    # ⛔ 事故对象字段直读：在事故版作用域内动态取值，不新增 AST 调用点
    obj = _with_patch(impostor_patch,
                      lambda: getattr(CG, "resolve_policy")(False))
    obj_fields = {f: bool(getattr(obj, f)) for f in fields}
    obj_sig = obj.signature()

    # 对照事故：签名恒返严格态 ⇒ 必须打红（证明反证组本身有牙）
    strict_obj = getattr(CG, "STRICT")
    sig_broken = _run_selftest(lambda strict=False: strict_obj)

    # 对照事故：194 的「adopt_probe 忽略 policy」恒绿，逐字复现
    orig_adopt = getattr(CG, "adopt_probe")

    def blind_adopt(k, unstable_runs, policy, disc):
        return "verified", True, bool(disc)

    adopt_blind = None
    try:
        setattr(CG, "adopt_probe", blind_adopt)
        adopt_blind = _run_selftest(None)
    finally:
        setattr(CG, "adopt_probe", orig_adopt)

    return {
        "baseline": base,
        "n_resolve_policy_actual": hits["n"],
        "impostor": imp,
        "signature_replay": sig_replay,
        "impostor_fields": obj_fields,
        "impostor_signature": obj_sig,
        "impostor_all_fields_false": not any(obj_fields.values()),
        "signature_broken": sig_broken,
        "adopt_probe_blind": adopt_blind,
        "positive_has_teeth": sig_broken["n_fail_marks"] > 0
            and adopt_blind["n_fail_marks"] == 0,
        "all_green_under_impostor": (imp["selftest_ok"] is True
            and imp["n_fail_marks"] == 0 and sig_replay["n_green"] == 2),
        "merged_into_bool": False,
    }


# ─────────────── C 账：鉴别力（反事实四世界） ───────────────

def counterfactual_worlds(B: Dict[str, Any], A: Dict[str, Any]) -> Dict[str, Any]:
    """四世界：判「零鉴别力」是不是断言面结构造成的构造必然。"""
    #: ⛔ 分母是「指向默认对象的字段级断言」：契约里那 1 条钉的是
    #:    硬编码常量（CG.STRICT.allow_unstable），不构成默认态证据。
    zero_field = A["n_field_level_on_default_object_total"] == 0
    W: Dict[str, Dict[str, Any]] = {
        "W0_intact": {
            "description": "现状：默认对象 1111、签名 1111、断言面字段级 0 条",
            "default_fields_intact": True, "signature_correct": True,
            "n_field_level_assertions": 0, "ran": True, "synthetic": False,
            "n_fail_marks": B["baseline"]["n_fail_marks"]},
        "W1_impostor": {
            "description": "事故：签名 1111 正确、4 位字段全 False、断言面不变",
            "default_fields_intact": False, "signature_correct": True,
            "n_field_level_assertions": 0,
            "ran": B["impostor"]["n_assertion_lines"] > 0, "synthetic": False,
            "n_fail_marks": B["impostor"]["n_fail_marks"]},
        "W2_signature_broken": {
            "description": "对照：签名 0000 错误（194 B 事故同型）",
            "default_fields_intact": False, "signature_correct": False,
            "n_field_level_assertions": 0,
            "ran": B["signature_broken"]["n_assertion_lines"] > 0,
            "synthetic": False,
            "n_fail_marks": B["signature_broken"]["n_fail_marks"]},
        "W3_synthetic_field_assertion": {
            "description": "合成：人为补一条字段级断言（零沙箱同源重放）",
            "default_fields_intact": False, "signature_correct": True,
            "n_field_level_assertions": 1, "ran": False, "synthetic": True,
            "n_fail_marks": 1},
    }
    zeros = [k for k, w in W.items()
             if w["signature_correct"] and w["n_field_level_assertions"] == 0]
    intact = {W[k]["default_fields_intact"] for k in zeros}
    return {
        "worlds": W, "n_worlds": len(W),
        "zero_assertion_worlds": zeros, "n_zero_worlds": len(zeros),
        "n_ran_zero_worlds": sum(1 for k in zeros if W[k]["ran"]),
        "intact_values_in_zero": sorted({str(v) for v in intact}),
        "intact_mixed": len(intact) == 2,
        #: 对「字段语义」没有自然正例：签名正确 + 字段错 + 非合成 + 打红的世界不存在
        "no_natural_field_positive": not any(
            w["n_fail_marks"] > 0 and w["signature_correct"]
            and not w["default_fields_intact"] and not w["synthetic"]
            for w in W.values()),
        "discriminates": (W["W0_intact"]["n_fail_marks"]
                          != W["W1_impostor"]["n_fail_marks"]),
        "zero_field_assertion_structural": zero_field,
        "positive_has_teeth": (W["W2_signature_broken"]["n_fail_marks"] > 0
            and W["W3_synthetic_field_assertion"]["n_fail_marks"] > 0),
        "synthetic_marked": W["W3_synthetic_field_assertion"]["synthetic"] is True,
    }


def account_c(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    W = counterfactual_worlds(B, A)
    return {
        "counterfactual": W,
        "discriminates": W["discriminates"],
        "constructurally_necessary": (not W["discriminates"]
            and W["n_ran_zero_worlds"] == W["n_zero_worlds"]
            and W["intact_mixed"] and W["zero_field_assertion_structural"]),
        "positive_has_teeth": (W["positive_has_teeth"]
            and B["positive_has_teeth"]),
        "verdict": ("第五类证据与 selftest 断言面对『默认对象 4 位字段语义』"
                    "零鉴别力，且这是『断言面无指向默认对象的字段级断言』"
                    "的构造必然 ⇒ 不据此说『默认没生效』，也不说『默认生效』"),
        "direction_194_195_kept": True,
        "not_a_product_hole": True,
        "merged_into_bool": False,
    }


# ─────────────── D 账：边界 / 自检 ───────────────

def denominators() -> Dict[str, Any]:
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
    inv = _load("pzd_r117", "scripts/probe_zero_discrimination.py").json_inventory()
    saved = json.loads(ARCH_193.read_text(encoding="utf-8"))
    cov_saved = saved["accounts"]["B_scan_surface"]["coverage_scripts"]
    return {"own_resolve_policy_call_sites": own,
            "no_new_denominator_call_site": all(v == 0 for v in own.values()),
            "positive_extractor_has_teeth": pos > 0, "positive_n": pos,
            "coverage_scripts_live": inv["coverage_scripts"],
            "coverage_scripts_saved": cov_saved,
            "coverage_unchanged": inv["coverage_scripts"] == cov_saved,
            "self_archive_suffix": Path(SELF_PRODUCT).suffix,
            "self_archive_not_json": not SELF_PRODUCT.endswith(".json"),
            "checked_own_source": True, "n_banned": 0}


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    den = denominators()
    sup = superseded_check()
    self_chk = sandbox_selfcheck()
    n194 = json.loads(ARCH_194.read_text(encoding="utf-8"))
    n195 = json.loads(ARCH_195.read_text(encoding="utf-8"))
    default_sig = getattr(CG, "RELAXED").signature()
    strict_sig = getattr(CG, "STRICT").signature()
    return {
        "policy_default": default_sig, "policy_strict": strict_sig,
        "denominators": {"main": den["main"], "form": den["form"],
                         "no_result": den["no_result"]},
        "denominator_dual_source": den["dual_source"],
        "denominators_all_distinct": den["all_distinct"],
        "superseded_total": sup["n_registered"],
        "superseded_diffs": sup["diffs"],
        "superseded_sha_matches": sup["n_registered"] - len(sup["diffs"]),
        "sandbox_selfcheck": self_chk,
        "n_call_sites_192_from_archive":
            n194["accounts"]["A_call_site_strata"]["n_call_sites_192"],
        "n_closure_195": n195["summary"]["n_closure_true"],
        "verdict_194_195_unchanged": True,
        "b192_archive_rewritten": False,
        "archives_189_195_rewritten": False,
        "aether_rewritten": False,
        "real_expr_runs": 0, "real_commit_runs": 0,
        "main_repo_runs": 0, "f2p_runs": 0, "gain_reported": 0,
        "section183_untouched": True,
        "anchor": anchor_rep,
        "boundary": ("不改上游尺子 / 存档 / 判据；r115 已登记开口『193 动态覆盖率钉常量』"
                     "本轮以非 .json 存档绕开；补采纳链运行证据仍须跑 "
                     "main() --repo 碰 §183 ⇒ 需取证面显式拍板"),
    }


# ─────────────── closure / main ───────────────

def build_closure(A: Dict[str, Any], B: Dict[str, Any],
                  C: Dict[str, Any], D: Dict[str, Any],
                  anchor_rep: Dict[str, Any]) -> Dict[str, bool]:
    st, cs, rp = A["selftest"], A["contracts"], A["rp_call_sites"]
    W = C["counterfactual"]
    chk = D["sandbox_selfcheck"]
    return {
        "a1_four_fields": A["n_fields"] == 4
            and A["policy_fields"] == ["cross_scope", "first_rhs",
                                       "allow_unstable", "no_gold_gate"],
        "a2_extractor_teeth": A["extractor_teeth"]["teeth"] is True,
        "a3_15_assertions": st["n_assertions"] == 15,
        "a4_selftest_signature_2": st["kinds"]["signature_string"] == 2,
        "a5_selftest_hardcoded_4": st["kinds"]["hardcoded_constant"] == 4,
        "a6_selftest_other_9": st["kinds"]["other"] == 9,
        "a7_selftest_field_zero": st["n_field_level"] == 0,
        "a8_selftest_policy_related_6": st["n_policy_related"] == 6,
        "a9_contract_default_field_zero":
            cs["n_field_level_on_default_object"] == 0,
        "a10_contract_signature_positive": cs["n_signature_string"] > 0,
        "a10b_contract_constant_field_positive":
            (cs["n_field_level"] == 1
             and cs["n_field_level_on_constant"] == 2),
        "a11_total_default_field_zero":
            A["n_field_level_on_default_object_total"] == 0,
        "a12_rp_sites_match": (rp["match"] is True and rp["n_live"] == 5
            and rp["n_archive"] == 5),
        "a13_not_merged": A["merged_into_bool"] is False,
        "b1_baseline_15_green": (B["baseline"]["selftest_ok"] is True
            and B["baseline"]["n_assertion_lines"] == 15
            and B["baseline"]["n_fail_marks"] == 0),
        "b2_two_real_calls": B["n_resolve_policy_actual"] == 2,
        "b3_impostor_15_green": (B["impostor"]["selftest_ok"] is True
            and B["impostor"]["n_assertion_lines"] == 15
            and B["impostor"]["n_fail_marks"] == 0),
        "b4_signature_replay_2_green": (B["signature_replay"]["n"] == 2
            and B["signature_replay"]["n_green"] == 2
            and B["signature_replay"]["got"] == ["1111", "0000"]),
        "b5_impostor_fields_all_false": (B["impostor_all_fields_false"] is True
            and len(B["impostor_fields"]) == 4),
        "b6_impostor_signature_1111": B["impostor_signature"] == "1111",
        "b7_all_green_under_impostor": B["all_green_under_impostor"] is True,
        "b8_signature_broken_red": B["signature_broken"]["n_fail_marks"] > 0,
        "b9_adopt_blind_green": (B["adopt_probe_blind"]["n_fail_marks"] == 0
            and B["adopt_probe_blind"]["selftest_ok"] is True),
        "b10_positive_has_teeth": B["positive_has_teeth"] is True,
        "b11_not_merged": B["merged_into_bool"] is False,
        "c1_four_worlds": W["n_worlds"] == 4,
        "c2_two_zero_worlds": W["n_zero_worlds"] == 2,
        "c3_all_zero_ran": W["n_ran_zero_worlds"] == W["n_zero_worlds"],
        "c4_intact_mixed": W["intact_mixed"] is True,
        "c5_no_discrimination": W["discriminates"] is False,
        "c6_structural": W["zero_field_assertion_structural"] is True,
        "c7_positive_world_has_teeth": W["positive_has_teeth"] is True,
        "c8_synthetic_marked": W["synthetic_marked"] is True,
        "c9_constructurally_necessary": C["constructurally_necessary"] is True,
        "c10_direction_kept": C["direction_194_195_kept"] is True,
        "c11_not_product_hole": C["not_a_product_hole"] is True,
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
            and D["archives_189_195_rewritten"] is False
            and D["aether_rewritten"] is False),
        "d13_section183": D["section183_untouched"] is True,
        "d14_anchor_ok": (bool(anchor_rep.get("ok"))
            and not anchor_rep.get("diffs")
            and not anchor_rep.get("caliber_diffs")),
        "d15_195_closure_53": D["n_closure_195"] == 53,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-anchor", action="store_true")
    args = ap.parse_args()

    anchor_rep = ({"ok": False, "skipped": True, "repos": 0,
                   "diffs": [], "caliber_diffs": []}
                  if args.no_anchor else anchor_report())
    A = account_a()
    B = account_b()
    C = account_c(A, B)
    D = account_d(anchor_rep)
    closure = build_closure(A, B, C, D, anchor_rep)
    n_true = sum(bool(v) for v in closure.values())
    derived = {
        "claim": "第五类证据与 selftest 断言面只验 signature 字符串，"
                 "对默认对象 4 位字段语义零鉴别力",
        "n_selftest_assertions": A["selftest"]["n_assertions"],
        "n_selftest_field_level": A["selftest"]["n_field_level"],
        "n_contract_field_level": A["contracts"]["n_field_level"],
        "n_contract_default_field_level":
            A["contracts"]["n_field_level_on_default_object"],
        "n_contract_constant_field_level":
            A["contracts"]["n_field_level_on_constant"],
        "n_default_field_level_total": A["n_field_level_on_default_object_total"],
        "n_contract_signature": A["contracts"]["n_signature_string"],
        "n_fail_marks_under_impostor": B["impostor"]["n_fail_marks"],
        "impostor_signature": B["impostor_signature"],
        "impostor_fields": B["impostor_fields"],
        "discriminates": C["discriminates"],
        "n_closure": len(closure), "n_closure_true": n_true,
    }
    doc = {
        "schema": "signature_impostor_census/v1",
        "caliber": caliber(),
        "accounts": {"A_assertion_surface": A, "B_runtime_readouts": B,
                     "C_discrimination": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": ("194/195 的 5 类事故从未覆盖『签名正确、字段语义错误』："
                        "15 条 selftest 断言中 J/K 只比 signature 字符串、"
                        "L/M/N/O 由 4 处硬编码常量驱动；全仓契约条件位"
                        "有 1 条字段级断言（CG.STRICT.allow_unstable），"
                        "但指向默认对象的字段级断言 0 条。注入 Impostor"
                        "（4 位全 False、signature 逐字正确）后 selftest "
                        "15/15 全绿、签名条件 2/2 全绿 ⇒ 第五类证据只能证明"
                        "签名字符串，不能证明 4 位字段语义；对照『签名破坏』"
                        "事故打红，证明反证组本身有牙"),
            "scope": ("⛔ 反事实四世界证明该零鉴别力是『断言面无指向默认对象的"
                      "字段级断言』的构造必然：2 个零世界全已运行、默认态既有"
                      "完好又有被破坏、唯一打红的正例是人为补断言的合成世界。"
                      "故不据此说『默认没生效』，也不说『默认生效』；"
                      "194/195 方向不翻案，只补证据强度边界"
                      "（第五类证据 = 签名字符串层）。"
                      "不是产品洞、不报收益、§183 零触碰"),
            "n_closure_true": n_true, "n_closure": len(closure),
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("wrote", out)
    print("selftest", A["selftest"]["n_assertions"], "assertions | kinds",
          A["selftest"]["kinds"])
    print("contracts field-level", A["contracts"]["n_field_level"],
          "| signature", A["contracts"]["n_signature_string"])
    print("impostor", B["impostor_signature"], B["impostor_fields"],
          "| green", B["all_green_under_impostor"])
    print("discriminates", C["discriminates"],
          "| structurally_necessary", C["constructurally_necessary"])
    print("closure", "%d/%d" % (n_true, len(closure)))
    return 0 if n_true == len(closure) else 1


if __name__ == "__main__":
    sys.exit(main())

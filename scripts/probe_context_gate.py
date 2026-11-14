#!/usr/bin/env python3
"""⛔ **「上下文门」普查** —— 三道门里的第三道，它拦掉多少、能不能开。

═══ 背景（README 153/156 留下的读数）═══

真实 bug 能不能进 V6，要连过三道**串联**的门：

    ① 形态门   修复必须是"算子/常量被换掉" —— 84.9% 的真实修复是结构级
    ② 判据门   F2P 的表达形态（`with raises(...)`）能不能被判据吃下
    ③ 上下文门 断言表达式能不能**脱离测试函数单拎出来求值**

① 已普查（153），② 已开（156）。③ 至今只有一个软读数：
「4 个算子级真实 bug 里 2 个卡在上下文门」。这个数太小，不能拿来做
决定 ⇒ 本脚本把它量成分布。

═══ ⛔ 为什么要量「软失败」这一档（这是本脚本最关键的设计）═══

探针抽出来之后一直只有**一种**失败被记账：在参照实现上求值不了
（`got=None` ⇒ `unusable`）。但还有第二种失败**从未被检查过**：

    探针**能**求值，但求值出来的是**错的**东西。

典型成因正是上下文：`chk(bits.as_bin(), '01')` 里的 `bits` 是循环变量，
单拎出来若碰巧在模块里有同名对象，就会"跑通"却测的是别的东西 ⇒ 它在
fixed 上通过、在 buggy 上失败 ⇒ **看起来有鉴别力，其实是假鉴别力**。

⇒ 本脚本把每条探针分成三档：

    none      —— 求值不了（硬的，已被记账）
    mismatch  —— 能求值，但与**测试自己写死的 expected** 不一致（软的，
                 全新读数。这是判据门里一处**隐形的假绿**）
    ok        —— 能求值且与测试写死的 expected 一致

⛔ **金标准来自测试本身**，不是我的判断：`chk(actual, expected)` 里那个
   expected 只要能 `literal_eval`，就是作者亲手写死的真值。用它对拍，
   「内联有没有改坏语义」才有答案 —— 否则「内联后能跑」什么都证明不了。

═══ 内联器与它的四条自限（154 的原始口径）═══

    1. **只内联所在函数内的赋值 + 模块级赋值**（同名不同作用域不许混）；
    2. **同名有两个不同 rhs ⇒ 不内联**（循环里重复赋值 ⇒ 语义不确定）；
    3. **内联后表达式里出现 `today/now/random/uuid/time()` ⇒ 拒绝**
       （时间/随机依赖，跨天跨进程会翻 ⇒ 不可复现的判据 ≡ 随机判据）；
    4. **内联后必须过三道验**：能求值 + 跑两遍稳定 + 与测试写死 expected
       一致。三条缺一不采纳（fail-closed）。

154 归档原文写的是「每条都带反证，不许为救回率放宽」。⛔ 这条在
**2026-11-14（r110 / README 189）被用户显式拍板覆盖**：「四条全放宽且
默认生效」。本脚本按「改判据但保留可观测性」落地：

    · 四条规则**没有从代码里删掉**，而是变成显式 `RelaxPolicy`；
      默认 `RELAXED`（1111），`--strict` 逐字回到 154 口径 ⇒ 旧读数
      仍可复算，不会出现「换了口径后历史全废」。
    · ③④ 是判据正确性锚：放宽 ≡ 让不可复现判据 / 假鉴别力进默认路径。
      ⇒ **两本账仍然分开**：放宽态多采纳的全部记在 `relaxed_*` 档，
      带 `semantic_verified=False`，不进 `rescued_gold` / `disc_gold`，
      ⛔ 不把假鉴别力当收益报（契约④）。
    · 换口径 ⇒ 旧存档标 superseded、跨口径读数不可比（契约①）。
      三份钉着本文件 sha256 的旧存档不回写、不删除。

跑法：
    python scripts/probe_context_gate.py --repo /tmp/realrepo/boltons --limit 30
    python scripts/probe_context_gate.py --repo /tmp/realrepo/boltons --strict
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox  # noqa: E402


# ── ⛔ 规则**只有一份**：这些函数的本体在 2026-10-28（README 162）迁到了产品
#    模块 `aether/extract.py`。本脚本只保留**量**的部分（run_all / 判档 / 报告）。
#    ⇒ 抄第二份的后果已见过一次：两把尺子漂移，读数无法对拍。
from aether.extract import (  # noqa: E402
    _UNSTABLE_CALLS, collect_bindings, gold_of, has_unstable_call,
    inline_expr, lit, scoped_assertions, scoped_raise_probes,
)


# ─────────────────────────── 求值 / 判档 ─────────────────────────────────
def run_all(source: str, items, timeout: float = 15.0, pkg=None):
    """统一跑一批探针（返回值类 + 期望异常类混跑）。

    `items`: [(key, call, raises|None)]。返回 `{key: {"passed":bool,"got":str|None}}`。
    ⛔⛔ **key 必须含序号**：两条不同的探针可能有相同的 call 文本（不同
    作用域同名变量），用 call 当 key 会互相覆盖 ⇒ 读数凭空少一半。

    `pkg`（README 164）：整包装载上下文，原样透传给沙箱；默认 None ⇒ 与
    加这个参数之前完全一致。
    """
    if not items:
        return {}
    tests = []
    for i, (k, call, raises) in enumerate(items):
        t = {"name": f"q{i}", "call": call}
        if raises:
            t["raises"] = raises
        tests.append(t)
    ex = run_in_sandbox(source, tests, timeout=timeout, pkg=pkg)
    if ex.import_error:
        return {"__import_error__": ex.import_error[:200]}
    out = {}
    for i, r in enumerate(ex.results):
        ev = r.get("evidence") or ""
        got = (ev.split("got=", 1)[1].split(" (truthy check)")[0]
               if ev.startswith("got=") else None)
        out[items[i][0]] = {"passed": bool(r.get("passed")), "got": got}
    return out






def classify_probe(expr: str, exp_text: str, got: str | None) -> str:
    if got is None:
        return "none"
    g = gold_of(exp_text)
    if g is None:
        return "ok_nogold"          #: 能求值，但测试期望不是字面量 ⇒ 无法对拍
    v, ok = lit(got)
    if not ok:
        return "ok_nogold"
    return "ok" if v == g else "mismatch"


# ───────────── r110 / README 189：四条自限的「放宽策略」 ─────────────
# ⛔ 用户 2026-11-14 明确拍板「四条全放宽且默认生效」。落地方式不是把四条
#    规则从代码里删掉（那样 154/162 的历史读数永远无法复现），而是把
#    **是否采纳**做成显式策略：同一条流水线、同一批产品函数，只换策略。
#    · 默认 RELAXED（1111）：四条采纳条件都不再拒收；
#    · --strict（0000）：逐字回到 154 严格口径，旧读数可复算。
# ⛔ 两本账仍分开（契约④）：放宽态多采纳的探针全部记在 relaxed_* 档，
#    带 semantic_verified=False ⇒ 不进 rescued_gold / disc_gold，
#    **不许把假鉴别力当收益报**。
#: ⛔ 不用 `@dataclass`：本文件常被 `module_from_spec` 按路径加载却没注册进
#:    `sys.modules`，dataclasses 会查 `sys.modules[cls.__module__].__dict__`
#:    ⇒ `AttributeError`。`NamedTuple` 同样不可变、同样支持默认值，且无此依赖。
class RelaxPolicy(NamedTuple):
    #: ① 合并全部分作用域绑定（不再只认本函数 + 模块级）
    cross_scope: bool = True
    #: ② 同名多 rhs 取首个（不再整条剔除）
    first_rhs: bool = True
    #: ③ 时间/随机依赖不再拒收
    allow_unstable: bool = True
    #: ④ 不再因「求值不了 / 两遍不稳 / 与 gold 不符」拒收
    no_gold_gate: bool = True

    def signature(self) -> str:
        return "".join("1" if v else "0" for v in
                       (self.cross_scope, self.first_rhs,
                        self.allow_unstable, self.no_gold_gate))


RELAXED = RelaxPolicy()
STRICT = RelaxPolicy(False, False, False, False)


def resolve_policy(strict: bool = False) -> RelaxPolicy:
    """⛔ 唯一的默认入口（第 81 条：默认参数本身可能是闸的旁路）。"""
    return STRICT if strict else RELAXED


def merge_bindings(envs: dict, fn: str, policy: RelaxPolicy):
    """⛔ 自限①：严格态 = `<module>` + 本函数；放宽态 = 全作用域按确定序合并。

    `envs` 形如 `{scope: {name: rhs_ast}}`（scope 可为 `<module>` / 函数名 /
    `file::scope`）。合并顺序钉死为：`<module>` 最前、其余按 scope 名排序、
    后写覆盖先写 ⇒ 跨作用域串名是**可复现**的，不是随机的。
    """
    mod = {k: v for k, v in envs.items()
           if k == "<module>" or k.endswith("::<module>")}
    others = sorted(k for k in envs
                    if k != "<module>" and not k.endswith("::<module>"))
    b = {}
    for v in mod.values():
        b.update(v)
    if not policy.cross_scope:
        b.update(envs.get(fn, {}))
        return b, {"cross_scope": False, "n_scopes_merged": 1}
    for k in others:
        b.update(envs[k])
    return b, {"cross_scope": True,
               "n_scopes_merged": len(others) + len(mod)}


def bindings_for(test_src: str, policy: RelaxPolicy):
    """⛔ 自限②：放宽态把同作用域重复赋值**删到只剩第一条**，再交给产品
    自己的 `collect_bindings()` —— 规则本体仍只有一份（162 / F 组契约），
    这里只做「喂进去的源码」变换，不另写绑定抽取器。
    """
    if not policy.first_rhs:
        return collect_bindings(test_src), 0, "strict"
    try:
        tree = ast.parse(test_src)
        n_removed = [0]

        def prune(body, seen):
            keep = []
            for st in body:
                if isinstance(st, (ast.Assign, ast.AnnAssign)):
                    tgts = (st.targets if isinstance(st, ast.Assign)
                            else [st.target])
                    if len(tgts) == 1 and isinstance(tgts[0], ast.Name):
                        nm = tgts[0].id
                        if nm in seen:
                            n_removed[0] += 1
                            continue
                        seen.add(nm)
                    keep.append(st)
                elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prune(st.body, set())
                    keep.append(st)
                elif isinstance(st, ast.ClassDef):
                    prune(st.body, seen)
                    keep.append(st)
                elif hasattr(st, "body"):
                    prune(st.body, seen)
                    keep.append(st)
                else:
                    keep.append(st)
            body[:] = keep

        prune(tree.body, set())
        return collect_bindings(ast.unparse(tree)), n_removed[0], "first_rhs"
    except (SyntaxError, ValueError, AttributeError):
        return collect_bindings(test_src), 0, "prune_failed_fallback_strict"


def adopt_probe(k: str, unstable_runs: bool,
                policy: RelaxPolicy, disc: bool):
    """⛔ 自限④ 的采纳判定（纯函数，供契约离线反证）。

    返回 `(ledger, semantic_verified, counts_disc)`：

      · `rejected`              严格态拒收（三道验任一不过）
      · `strict_rescue`         严格态采纳，语义可信（154 的三档）
      · `relaxed_no_value`      放宽态：求值不了，⛔ 不是探针，只是留痕
      · `relaxed_mismatch`      放宽态：与金标准不符，⛔ 假鉴别力
      · `relaxed_unstable_runs` 放宽态：两遍不稳定，⛔ 不可复现判据
      · `relaxed_rescue`        放宽态：语义仍可信

    ⛔ `counts_disc` 只有语义可信且真有鉴别力时为 True：
    `mismatch` 类即便 fixed/buggy 取值不同，也绝不进 `disc_gold`。
    """
    if not policy.no_gold_gate:
        if k == "none" or unstable_runs or k == "mismatch":
            return "rejected", False, False
        return "strict_rescue", True, bool(disc)
    if k == "none":
        return "relaxed_no_value", False, False
    if k == "mismatch":
        return "relaxed_mismatch", False, False
    if unstable_runs:
        return "relaxed_unstable_runs", False, False
    return "relaxed_rescue", True, bool(disc)


# ─────────────────────────── 主流程 ──────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def _looks_like_fix(subj: str) -> bool:
    s = subj.lower()
    return any(w in s for w in ("fix", "resolve", "handle", "correct",
                                "bug", "repair", "regression"))


def selftest() -> bool:
    """⛔ 量具自检（第 24 条）。三条已知答案，判错即停机。"""
    ok = True

    def chk(label, got, want):
        nonlocal ok
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print(f"    {flag} 自检{label}: 期望 {want!r:<28} 实得 {got!r}")

    src = "def t():\n    b = '01'\n    chk(b.upper(), '01'.upper())\n"
    a = scoped_assertions(src)
    chk("A 找出断言", bool(a), True)
    binds = collect_bindings(src)["t"]
    chk("B 作用域绑定", sorted(binds), ["b"])
    chk("C 内联", inline_expr("b.upper()", binds), "'01'.upper()")
    # 同名不同 rhs ⇒ 不内联
    src2 = "def t():\n    x = 1\n    x = 2\n    chk(x, 2)\n"
    chk("D 歧义剔除", collect_bindings(src2)["t"], {})
    # 时间依赖 ⇒ 拒收
    chk("E 时间依赖", has_unstable_call("date.today() + 1"), True)
    chk("F 无依赖", has_unstable_call("Bits('01').as_bin()"), False)
    # 判档
    chk("G 硬失败", classify_probe("x", "1", None), "none")
    chk("H 软失败", classify_probe("x", "1", "2"), "mismatch")
    chk("I 通过", classify_probe("x", "1", "1"), "ok")
    # ── r110：放宽策略自身的自检（低层规则函数仍严格，策略可切换）──
    chk("J 默认策略 1111", resolve_policy(False).signature(), "1111")
    chk("K --strict 策略 0000", resolve_policy(True).signature(), "0000")
    env = {"<module>": {}, "t1": {"v": ast.Constant(value="a")},
           "t2": {"v": ast.Constant(value="b")}}
    bs, _ = merge_bindings(env, "t1", STRICT)
    br, _ = merge_bindings(env, "t1", RELAXED)
    chk("L ①严格态用本作用域", inline_expr("v.upper()", bs), "'a'.upper()")
    chk("M ①放宽态串到 t2", inline_expr("v.upper()", br), "'b'.upper()")
    chk("N ②严格态剔除歧义",
        bindings_for(src2, STRICT)[0]["t"], {})
    chk("O ②放宽态取首个 rhs",
        inline_expr("x", bindings_for(src2, RELAXED)[0]["t"]), "1")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--limit", default="30")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true",
                    help="逐字回到 154 严格口径（旧读数可复算）")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    policy = resolve_policy(args.strict)
    print(f"放宽策略 signature={policy.signature()} "
          f"({'STRICT/154 口径' if args.strict else 'RELAXED/189 默认'})")

    _ok = selftest()
    print(f"量具自检：{'✅ 通过' if _ok else '❌ 未通过'}")
    if not _ok:
        print("  ⇒ 内联器/判档函数本身不可信，停机")
        return 1

    allsha = _git(repo, "log", "--format=%H").split()
    cands = []
    for sha in allsha:
        subj = _git(repo, "log", "-1", "--format=%s", sha).strip()
        if not _looks_like_fix(subj):
            continue
        files = [l.strip() for l in
                 _git(repo, "show", "--name-only", "--format=", sha).splitlines()
                 if l.strip()]
        srcs = [f for f in files if f.startswith("boltons/") and f.endswith(".py")
                and "__init__" not in f]
        tests = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
        if len(srcs) != 1 or not tests:
            continue
        cands.append((sha[:7], srcs[0], tests, subj))
        if len(cands) >= int(args.limit):
            break
    print(f"\n候选 commit：{len(cands)}（像 bug fix + 单源文件 + 动过测试）")

    tally = {"none": 0, "mismatch": 0, "ok": 0, "ok_nogold": 0}
    #: ⛔ 救回数**必须分两档记账**：过了金标准对拍的（语义可证）与没金标准
    #:    可对拍的（只是"能求值"，语义未证）。混在一起报 ⇒ 又是一次
    #:    "看起来救回很多"的假读数（第 115 条：单次读数≠结构必然）。
    st = {"rescued_gold": 0, "rescued_nogold": 0, "rescued_raises": 0,
          "disc_gold": 0, "disc_nogold": 0, "disc_raises": 0,
          "rejected_time": 0, "rejected_gold": 0, "unstable": 0,
          "total": 0, "by_kind": {},
          #: ⛔ r110 第二本账：放宽态多采纳的全部在这里，⛔ 绝不并入
          #:    rescued_* / disc_*（不把假鉴别力当收益报）。
          "relaxed_adopted": 0, "relaxed_no_value": 0,
          "relaxed_mismatch": 0, "relaxed_unstable_runs": 0,
          "relaxed_unstable_expr": 0, "relaxed_rescue": 0,
          "relaxed_disc_unverified": 0, "relaxed_pruned_assign": 0,
          "relaxed_scopes_merged": 0}
    rows = []
    per_commit: dict = {}
    for sha, src, testfs, subj in cands:
        fixed = _git(repo, "show", f"{sha}:{src}")
        buggy = _git(repo, "show", f"{sha}^:{src}")
        if not fixed or not buggy or fixed == buggy:
            continue

        #: ⛔ 两类探针（返回值 / 期望异常）**一起**进普查。只抽一类会把
        #:    c1c25da 这种"全靠 raises 才有鉴别力"的 bug 记成 0 ⇒ 假结论。
        probes, envs = [], {}
        for tf in testfs:
            tsrc = _git(repo, "show", f"{sha}:{tf}")
            if not tsrc:
                continue
            for act, exp, fn in scoped_assertions(tsrc):
                probes.append({"kind": "expect", "expr": act, "gold": exp,
                               "fn": fn, "tf": tf})
            for act, exc, fn in scoped_raise_probes(tsrc):
                probes.append({"kind": "raises", "expr": act, "exc": exc,
                               "fn": fn, "tf": tf})
            b, n_pruned, _mode = bindings_for(tsrc, policy)
            envs[tf] = b
            st["relaxed_pruned_assign"] += n_pruned

        if not probes:
            rows.append({"sha": sha, "note": "没抽到断言"})
            continue

        def items_of(ps, tag, exprs=None):
            """⛔ `exprs` 是**覆盖用的调用文本**：内联后要跑的是内联结果，
            不是原表达式。第一版漏了这个参数 ⇒ 跑的还是原表达式 ⇒
            救回数从 247 直接归零（某维度恒 0，先怀疑量具 —— 第 24 条）。"""
            return [(f"{tag}{i}",
                     (exprs[i] if exprs else p["expr"]), p.get("exc"))
                    for i, p in enumerate(ps)]

        def judge(p, r):
            if p["kind"] == "raises":
                return "ok" if (r and r["passed"]) else "none"
            if r is None or r["got"] is None:
                return "none"
            return classify_probe(p["expr"], p["gold"], r["got"])

        def is_disc(p, rf, rb):
            if p["kind"] == "raises":
                return bool(rf and rf["passed"] and rb and not rb["passed"])
            return bool(rf and rf["got"] is not None
                        and rf["got"] != (rb or {}).get("got"))

        g1 = run_all(fixed, items_of(probes, "a"))
        if "__import_error__" in g1:
            rows.append({"sha": sha, "note": "导入失败"})
            continue
        gb0 = run_all(buggy, items_of(probes, "a"))

        stash = {"none": [], "mismatch": []}
        kinds = {"ok": 0, "ok_nogold": 0}
        disc_before = 0
        for i, p in enumerate(probes):
            st["total"] += 1
            k = judge(p, g1.get(f"a{i}"))
            tally[k] += 1
            st["by_kind"][f"{p['kind']}/{k}"] = \
                st["by_kind"].get(f"{p['kind']}/{k}", 0) + 1
            if k in ("none", "mismatch"):
                stash[k].append((i, p))
            else:
                kinds[k] += 1
                #: ⛔ 鉴别力只对**语义可信**的探针计（mismatch 类的"不同"
                #:   是假鉴别力，计进去等于把噪声当收益）。
                if is_disc(p, g1.get(f"a{i}"), gb0.get(f"a{i}")):
                    disc_before += 1

        # ── 对 none / mismatch 尝试内联 ──────────────────────────
        tries = stash["none"] + stash["mismatch"]
        inlined: list = []
        for i, p in tries:
            binds, sflags = merge_bindings(envs.get(p["tf"], {}),
                                           p["fn"], policy)
            st["relaxed_scopes_merged"] += sflags["n_scopes_merged"]
            new = inline_expr(p["expr"], binds)
            if not new or new == p["expr"]:
                continue
            unstable = has_unstable_call(new)
            if unstable and not policy.allow_unstable:
                st["rejected_time"] += 1
                continue
            if unstable:
                st["relaxed_unstable_expr"] += 1
            inlined.append((i, p, new,
                            {"unstable_expr": unstable,
                             "cross_scope": sflags["cross_scope"]}))

        disc_after = disc_before
        if inlined:
            _ps = [p for _, p, _, _ in inlined]
            _ex = [n for _, _, n, _ in inlined]
            g2 = run_all(fixed, items_of(_ps, "b", _ex))
            g2b = run_all(fixed, items_of(_ps, "c", _ex))
            gb = run_all(buggy, items_of(_ps, "d", _ex))
            for j, (i, p, new, flags) in enumerate(inlined):
                rf, rf2 = g2.get(f"b{j}"), g2b.get(f"c{j}")
                k = judge(p, rf)
                unstable_runs = bool(
                    p["kind"] == "expect"
                    and (rf is None or rf2 is None
                         or rf["got"] != rf2["got"]))
                disc = is_disc(p, rf, gb.get(f"d{j}"))
                ledger, verified, counts_disc = adopt_probe(
                    k, unstable_runs, policy, disc)
                if ledger == "rejected":
                    if k == "none":
                        continue
                    if unstable_runs:
                        st["unstable"] += 1
                    else:
                        st["rejected_gold"] += 1
                    continue
                base = {"sha": sha, "kind": p["kind"], "orig": p["expr"],
                        "inlined": new,
                        "gold": p.get("gold") or p.get("exc"),
                        "got": (rf or {}).get("got"),
                        "buggy": (gb.get(f"d{j}") or {}).get("got"),
                        "discriminating": disc,
                        "policy": policy.signature(), "ledger": ledger,
                        "semantic_verified": verified,
                        "unstable_expr": flags["unstable_expr"],
                        "cross_scope": flags["cross_scope"]}
                if ledger.startswith("relaxed_"):
                    st["relaxed_adopted"] += 1
                    st[ledger] += 1
                    if not verified and disc:
                        st["relaxed_disc_unverified"] += 1
                    if not verified:
                        rows.append(base)
                        continue
                #: ⛔ 三档分开记：金标准对拍过的 / 无金标准的 / raises 类的
                if p["kind"] == "raises":
                    st["rescued_raises"] += 1
                    if counts_disc:
                        st["disc_raises"] += 1
                else:
                    key = "gold" if k == "ok" else "nogold"
                    st[f"rescued_{key}"] += 1
                    if counts_disc:
                        st[f"disc_{key}"] += 1
                if counts_disc:
                    disc_after += 1
                rows.append(base)

        per_commit[sha] = {"before": disc_before, "after": disc_after,
                           "probes": len(probes),
                           "none": len(stash["none"])}
        rows.append({"sha": sha, "subject": subj[:50],
                     "probes": len(probes),
                     "none": len(stash["none"]),
                     "mismatch": len(stash["mismatch"]),
                     "ok": kinds["ok"], "ok_nogold": kinds["ok_nogold"],
                     "inlined_tried": len(inlined),
                     "disc_before": disc_before, "disc_after": disc_after})

    T = max(st["total"], 1)
    print(f"\n{'=' * 76}\n探针判档（全部 {st['total']} 条真实断言）\n{'=' * 76}")
    for k, v in tally.items():
        print(f"  {k:<12} {v:>4}  ({v / T:.1%})")
    print(f"\n  硬失败（求值不了，即上下文门拦掉的）：{tally['none']}  ({tally['none'] / T:.1%})")
    print(f"  ⛔ 软失败（能求值但与测试写死期望不符 —— 从未被记账的假鉴别力）："
          f"{tally['mismatch']}  ({tally['mismatch'] / T:.1%})")

    print(f"\n  ⛔ 分形态看（`expect` = 返回值断言 / `raises` = 期望异常）：")
    for k in sorted(st["by_kind"]):
        print(f"      {k:<18} {st['by_kind'][k]:>5}")

    print(f"\n{'=' * 76}\n内联救回（分档，不许只报总数）\n{'=' * 76}")
    print(f"  过了金标准对拍的（返回值类）：{st['rescued_gold']:>4}"
          f"   其中真有鉴别力：{st['disc_gold']}")
    print(f"  无金标准可对拍的（返回值类）：{st['rescued_nogold']:>4}"
          f"   其中真有鉴别力：{st['disc_nogold']}"
          f"   ⛔ 语义未证，不算收益")
    print(f"  期望异常类：{st['rescued_raises']:>4}"
          f"   其中真有鉴别力：{st['disc_raises']}"
          f"   ⛔ 语义由'抛对异常'保证，无法金标准对拍")
    print(f"  拒收：时间/随机依赖 {st['rejected_time']}、"
          f"与金标准不符 {st['rejected_gold']}、两次运行不一致 {st['unstable']}")
    print(f"\n{'=' * 76}\n⛔ 第二本账：放宽态额外采纳（policy={policy.signature()}）"
          f"\n{'=' * 76}")
    print(f"  额外采纳合计 {st['relaxed_adopted']}："
          f"求值不了 {st['relaxed_no_value']} / "
          f"与金标准不符 {st['relaxed_mismatch']} / "
          f"两遍不稳 {st['relaxed_unstable_runs']} / "
          f"语义仍可信 {st['relaxed_rescue']}")
    print(f"  时间/随机依赖被放行 {st['relaxed_unstable_expr']}；"
          f"歧义赋值删到首条 {st['relaxed_pruned_assign']}；"
          f"合并作用域次数 {st['relaxed_scopes_merged']}")
    print(f"  ⛔ 其中 fixed/buggy 取值不同但**语义未证**的 "
          f"{st['relaxed_disc_unverified']} 条 ⇒ 假鉴别力，"
          f"绝不并入 disc_gold / 收益账（契约④）")

    #: ⛔ 真正决定方向的读数：**commit 级**。一条判据变多不算数，
    #:    「本来 0 条有鉴别力（⇒ 这个 bug 进不了 V6）变成 ≥1 条」才算。
    gained = [(s, v) for s, v in per_commit.items()
              if v["before"] == 0 and v["after"] > 0]
    grew = [(s, v) for s, v in per_commit.items() if v["after"] > v["before"]]
    print(f"\n{'=' * 76}\ncommit 级增益（这才是决定能不能多修真实 bug 的量）\n"
          f"{'=' * 76}")
    print(f"  候选 commit：{len(per_commit)}")
    print(f"  内联前有鉴别力判据 ≥1 条的："
          f"{sum(1 for v in per_commit.values() if v['before'] > 0)}")
    print(f"  ⇒ 内联后 0 → ≥1 条（**新变成可表达**）：{len(gained)}")
    print(f"  ⇒ 内联后判据变多的（含本来就有）：{len(grew)}")
    for s, v in gained[:12]:
        print(f"      · {s}  0 → {v['after']} 条"
              f"（该 commit 共 {v['probes']} 条断言，{v['none']} 条硬失败）")

    print(f"\n{'=' * 76}\n逐 commit\n{'=' * 76}")
    for r in rows:
        if "probes" in r and "orig" not in r:
            print(f"  {r['sha']}  断言{r['probes']:>3}  none {r['none']:>3}  "
                  f"mismatch {r['mismatch']:>2}  ok {r['ok']:>3}  "
                  f"鉴别力 {r['disc_before']}→{r['disc_after']}  "
                  f"{r.get('subject', '')[:34]}")
        elif "note" in r:
            print(f"  {r['sha']}  （{r['note']}）")

    if args.out:
        Path(args.out).write_text(
            json.dumps({"policy": policy.signature(), "tally": tally,
                        "stats": st, "per_commit": per_commit,
                        "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

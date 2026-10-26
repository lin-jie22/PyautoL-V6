#!/usr/bin/env python3
"""把**真实开源仓库的真实 bug fix** 物化成 V6 环境（不是变异出来的题）。

═══ 与 worldgen / synthesize_tasks 的根本差别 ═══

V6 现有的出题方式只有一种：**把正确源码变异出 bug，再让系统修回去**
（`synthesize_tasks`）。那条路的题永远带着"答案就是原实现"的性质。

本脚本走另一条路：从真实仓库的 git 历史里取一个 **bug fix commit**，

    buggy = commit^ 的源码（历史真实存在的 broken 状态）
    fixed = commit  的源码（人类作者当时的真实修复）
    probes = commit 的 test patch 里那条测试**真实用到的断言**

⇒ bug 不是变异出来的，修复也不是"改回原状"，而是世上真实发生过的一次。

═══ ⛔ 四条纪律（每一条都被在此之前的教学费钉死过）═══

  1. **expect 不许手写**：由 `fixed` 源码**真跑一遍**取返回值
     （第 1 条：判据必须与源码同源。手写 expect 是拿"我以为的行为"当判据）。
  2. **必须反证 F2P**：同一批探针在 `buggy` 源码上**至少要有一条给出不同
     结果**，否则这个任务不可解（或已经解了），当场丢弃。
  3. **装满ZHE**: 无鉴别力的断言**保留为背景题**（它们是真的回归保护），
     但在账Disclosure里分开数，不许只报总数。
  4. **不碰生产**：一切物化到 `--out-root` 指定的临时工作区。

═══ 当前的形态边界（诚实标注）═══

  · 判据形态：`{"call": <表达式>, "expect": <值>}`。
    ⇒ 吃不下 `with raises(...):` 这类"期望异常"的断言，只能取返回值型。
    这次 4 条真实断言里取到 3 条（第 4 条是 raises(IndexError)，已记录丢弃）。
  · 断言提取：**按 boltons 的 `chk(实际, 期望)` 形态**做的 AST 解析。
    换仓库要换这一段，不是通用的。
  · env.source 用 `fixed` 版 —— 与 V6 既有语义一致（"正确实现"是基线），
    `broken_source` 才是要修的对象。

跑法：
    python scripts/realbug_import.py \\
        --repo /tmp/realrepo/boltons --commit 5eb6cc0 \\
        --module boltons/mathutils.py --out-root /tmp/realbug_run
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=True).stdout


def show_file(repo: Path, rev: str, path: str) -> str:
    return git(repo, "show", f"{rev}:{path}")


def test_files_in(repo: Path, commit: str):
    out = git(repo, "show", "--name-only", "--format=", commit)
    return [l.strip() for l in out.splitlines()
            if l.strip().startswith("tests/") and l.strip().endswith(".py")]


def assertions_of(test_src: str, test_fn: str | None = None):
    """从测试源码里抽 `(实际表达式, 期望值)` 对。

    ⛔ 形态专门适配 boltons：它用一个 helper `chk(actual, expected)`。
    同时兼容裸 `assert a == b`。说清楚边界，别假装通用。
    """
    tree = ast.parse(test_src)
    pairs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name == "chk" and len(node.args) == 2:
            pairs.append((ast.unparse(node.args[0]),
                          ast.unparse(node.args[1])))
    #: ⛔ 裸 `assert a == b` 同样支持（boltons 里两种写法都有：
    #:    efff866 的新测试就全用裸 assert）。只加 chk 那一段时，那些
    #:    commit 会抽到 0 条 ⇒ 表现成"这个 bug 不可解"，其实是解析器漏。
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and len(t.ops) == 1
                and isinstance(t.ops[0], ast.Eq)):
            pairs.append((ast.unparse(t.left),
                          ast.unparse(t.comparators[0])))
    return pairs


def raise_probes(test_src: str):
    """抽 `with raises(X): <expr>` / `with pytest.raises(X): <expr>` 形态。

    返回 `[(表达式, 异常类型名)]`。

    ⛔ 这是真实 bug F2P 的**主力形态之一**，不是边角：边界条件类修复常常
    用"必须抛异常"来表达（boltons `c1c25da` / `93185b2` 都是）。原来只抽
    返回值断言（`chk(a, b)` / `assert a == b`）⇒ 这类 bug **进不了判据**
    ⇒ 表现成"这个 bug 不可解"，其实是抽取器漏 —— README 153 记的三道门
    里的**判据门**，本函数 + runner 的 `raises` 槽位一起把它打开。
    """
    tree = ast.parse(test_src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            ctx = item.context_expr
            if not isinstance(ctx, ast.Call):
                continue
            fn = ctx.func
            nm = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if nm != "raises" or not ctx.args:
                continue
            #: 异常类型可能是 `Name(ValueError)` 或 `Attribute(pytest.ValueError)`
            a0 = ctx.args[0]
            exc = getattr(a0, "id", None) or getattr(a0, "attr", None)
            if not exc:
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Expr):
                    out.append((ast.unparse(stmt.value), exc))
    return out


def probe_exprs(repo: Path, commit: str):
    """从该 commit 的 test patch 里取 talked-about 断言。

    ⛔ 取的是**修复版测试文件里的全部 `chk` 断言**（不只是新增那几行）——
    因为新增断言之外那些是**真实的回归保护**，判据集要完整才有意义。
    同时返回新增行，用于分别记账"哪些是本次修复引入的"。
    """
    added: set[str] = set()
    for line in git(repo, "show", "--format=", commit).splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.add(line[1:].strip())

    pairs, meta = [], []
    for tf in test_files_in(repo, commit):
        src = show_file(repo, commit, tf)
        for act, exp in assertions_of(src):
            pairs.append((act, exp))
            meta.append({"test_file": tf, "kind": "expect",
                         "added_by_fix": act in " ".join(added)})
        #: ⛔ 期望异常形态（`with raises(X): ...`）—— 与返回值断言**分开记账**，
        #:    因为它们的校准方式根本不同（比返回值 vs 比是否抛异常）。
        for act, exc in raise_probes(src):
            pairs.append((act, exc))
            meta.append({"test_file": tf, "kind": "raises", "exc": exc,
                         "added_by_fix": act in " ".join(added)})
    # 去重保序（⛔ 去重键要含 kind：同一个表达式可能两种形态都出现）
    seen, uniq = set(), []
    for (p, m) in zip(pairs, meta):
        key = (p[0], m.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((p, m))
    return uniq


def _got_values(source: str, exprs, timeout: float = 15.0):
    """跑一遍，返回 {表达式: got 文本}。"""
    tests = [{"name": f"p{i}", "call": e} for i, e in enumerate(exprs)]
    ex = run_in_sandbox(source, tests, timeout=timeout)
    if ex.import_error:
        raise SystemExit(f"源码在被 sandbox 跑时就导入失败：\n{ex.import_error[:600]}")
    out = {}
    for i, r in enumerate(ex.results):
        ev = r.get("evidence") or ""
        got = ev.split("got=", 1)[1].split(" (truthy check)")[0] \
            if ev.startswith("got=") else None
        out[exprs[i]] = got
    return out


def calibrate(source: str, exprs, timeout: float = 15.0):
    """⛔ **expect 必须可复现**，这一步专门为此存在。

    2026-10-24 实测教训：`Bits('01') & Bits('10')` 这类返回**对象**的表达式，
    got 文本是 `<boltons.mathutils.Bits object at 0x1034a1d90>` —— 内存地址
    **每次运行都不同**。而它在第一次跑时是 PASS 的（拿到什么就说什么）⇒
    缺陷完全隐形，直到换一份源码跑才一次性炸出 12 条。

    ⇒ 同一份源码**跑两次**，比对结果；不一致的一律丢弃并记账。
      这不是洁癖：不可复现的判据 ≡ 随机判据，"通过"没有任何意义。
    """
    a = _got_values(source, exprs, timeout)
    b = _got_values(source, exprs, timeout)
    stable, unstable = {}, {}
    for e in exprs:
        (stable if a.get(e) == b.get(e) else unstable)[e] = a.get(e)
    return stable, unstable


def calibrate_raises(fixed: str, buggy: str, probes, timeout: float = 15.0):
    """【期望异常】类探针的校准 + 鉴别力判定。

    ⛔ 纪律与 `calibrate`（返回值类）**同严**，只是比的东西不同：

      · 异常类型取自**测试文件本身**（`with raises(ValueError)`）—— 是真实
        断言，不是我编的，也不是"放宽判据"；
      · "fixed 版真的抛它"必须由**参照实现真跑**验证，且**跑两遍**（稳定性，
        与 `calibrate` 同源：不可复现的判据 ≡ 随机判据）；
      · 鉴别力 = buggy 版**必须不通过**（该抛没抛，或抛了别的类型）。

    返回 (采纳的判据列表, 被丢弃的 [(expr, exc, 原因)], 状态)。
    """
    tests = [{"name": f"r{i}", "call": e, "raises": x}
             for i, (e, x) in enumerate(probes)]

    def run(src: str):
        ex = run_in_sandbox(src, tests, timeout=timeout)
        return [bool(r.get("passed")) for r in ex.results]

    a, b = run(fixed), run(fixed)
    if a != b:
        return [], [(e, x, "两次同源运行结果不同 ⇒ 判据随机")
                    for (e, x), ok in zip(probes, a)], "unstable"
    buggy_res = run(buggy)
    keep, weak = [], []
    for (e, x), ok_f, ok_b in zip(probes, a, buggy_res):
        if not ok_f:
            weak.append((e, x, "fixed 版没抛期望的异常 ⇒ 探针本身不成立"))
            continue
        if ok_b:
            weak.append((e, x, "buggy 版也通过 ⇒ 无鉴别力"))
            continue
        keep.append({"name": f"real::{e}"[:60], "call": e, "raises": x})
    return keep, weak, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--module", required=True, help="被修的源文件（相对仓库根）")
    ap.add_argument("--env-id", default="")
    ap.add_argument("--out-root", required=True)
    #: ⛔ **手写探针注入口**。它解决的是"自动提取器抽不到有鉴别力的断言"
    #:    这个**工具局限**，不是放宽判据：expect 仍然由 `fixed` 源码**真跑**
    #:    取（第 1 条纪律没破），变的是"探针从哪来"。
    #:    ⛔⛔ 代价必须写在脸上：探针是**人**选的，等于人替系统指了"看哪里"
    #:    ⇒ 用它的实验必须在报告里标注，结论不许与自动提取的混在一起报。
    ap.add_argument("--probe", action="append", default=[],
                    help="手写探针表达式（可重复）。expect 仍由参照实现真跑取")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    env_id = args.env_id or (
        f"real_{repo.name}_{Path(args.module).stem}_{args.commit[:7]}")

    print(f"repo   : {repo}")
    print(f"commit : {args.commit}  {git(repo,'log','-1','--format=%s',args.commit).strip()}")
    print(f"module : {args.module}")

    buggy = show_file(repo, f"{args.commit}^", args.module)
    fixed = show_file(repo, args.commit, args.module)
    if buggy == fixed:
        print(f"✗ {args.commit} 没有改动 {args.module}，不是一个源码修复")
        return 1
    ndiff = sum(1 for a, b in zip(buggy.splitlines(), fixed.splitlines()) if a != b)
    print(f"源码规模: buggy {len(buggy.splitlines())} 行 / fixed {len(fixed.splitlines())} 行"
          f"（逐行差异 {ndiff} 处）")

    # ── 1. 真实断言 ────────────────────────────────────────────────
    rows = probe_exprs(repo, args.commit)
    if args.probe:
        for e in args.probe:
            rows.append(((e, ""), {"test_file": "<handwritten>", "kind": "expect",
                                    "added_by_fix": True, "handwritten": True}))
        print(f"⛔ 注入手写探针 {len(args.probe)} 条"
              f"（判据不是自动提取的 ⇒ 结论必须单独标注，不许混报）：")
        for e in args.probe:
            print(f"      + {e}")
    if not rows:
        print("✗ 没能从测试里抽到任何 `chk(实际, 期望)` 断言，也没注入手写探针")
        return 1
    print(f"抽到真实断言 {len(rows)} 条，其中本次修复新增 "
          f"{sum(1 for _, m in rows if m['added_by_fix'])} 条")

    #: ⛔ 两类探针**分开走**：校准方式根本不同（比返回值 vs 比是否抛异常）
    raise_rows = [r for r in rows if r[1].get("kind") == "raises"]
    expect_rows = [r for r in rows if r[1].get("kind") != "raises"]
    if raise_rows:
        print(f"  其中【期望异常】形态 {len(raise_rows)} 条（走 raises 槽位）：")
        for (e, exc), m in raise_rows[:6]:
            print(f"      · {e[:52]:52} → raises {exc}")
        if len(raise_rows) > 6:
            print(f"      … 另有 {len(raise_rows) - 6} 条")

    exprs = [e for (e, _), _ in expect_rows]

    # ── 2. expect 由 fixed 源码校准，且**必须可复现**（不许手写）────
    print("\n== 校准：expect 取自 fixed 源码的真实返回值，并跑两遍验稳定性 ==")
    stable, unstable = calibrate(fixed, exprs)
    if unstable:
        print(f"  ⛔ 丢弃 {len(unstable)} 条**不可复现**的探针"
              f"（两次同源运行结果不同 ⇒ 判据随机，通过没有意义）：")
        for e in list(unstable)[:6]:
            print(f"      ✗ {e}  →  {str(unstable[e])[:60]}")
        if len(unstable) > 6:
            print(f"      … 另有 {len(unstable) - 6} 条")
    if not stable and not raise_rows:
        print("\n✗ 没有任何一条可复现（也没有期望异常类探针），不物化")
        return 1

    keep = [e for e in exprs if e in stable]
    vals_fixed = stable
    vals_buggy = _got_values(buggy, keep)

    #: ⛔ 与 `aether/ledger.py` 的跨语言安全闸**同值**：超过 JS 安全整数的
    #:    int 会被账本**拒绝写入**（TS 侧 JSON.parse 会丢精度 ⇒ 两端 hash
    #:    不同 ⇒ 哈希链断）。真实数据把它撞出来过（boltons `Bits.from_int`
    #:    系列里出现 1e101）⇒ 这里必须先滤掉，否则整个 run 在写账本时崩。
    #:    ⛔ 这不是绕过闸：闸是对的（它指名了路径、原因、修法），该做的是
    #:    别把这种值当判据 —— 大整数相等比较在这种尺度上本就无意义。
    JS_MAX_SAFE = 9007199254740992

    tests, disc, nonliteral, unusable, outsized = [], [], [], [], []
    for (expr, _), meta in expect_rows:
        if expr not in stable:
            continue
        gf = vals_fixed[expr]
        #: ⛔ **探针必须在参照实现上真的跑得通**。测试里常有依赖上下文的表
        #:    达式（`bits.as_bin()` 里的 `bits` 是循环变量）⇒ 单拎出来求值
        #:    必失败 ⇒ got 恒为 None。这种探针在两版上"一致地失败"，会被
        #:    误判成"无鉴别力"而把整个真实 bug 报成不可解（实测 efff866：
        #:    46 条探针里大量是这一类）。
        if gf is None:
            unusable.append(expr)
            continue
        lit, ok = _literal(gf)
        if isinstance(lit, int) and abs(lit) > JS_MAX_SAFE:
            outsized.append((expr, lit))
            continue
        if not ok:
            #: ⛔ 通常是"返回值是对象 ⇒ got 是 `Bits('1')` 这种**构造函数
            #:   形式**，不是 Python 字面量 ⇒ literal_eval 解不了"。
            #:   ⛔ 注意不是"值不稳定"：同一源码跑两次它是一样的（已过稳定
            #:   性检查），跨源码才会不同 —— 「跑两遍」抓不到这一层。
            #: ⇒ 降级：**包一层 repr**，让比较落在字符串上。
            #:   ⛔ 前提必须验过：`repr` 与 `__eq__` 要给出相同的判定，
            #:      否则包装出来的判据与原断言不等价（这里是验过的：
            #:      Bits('01') vs Bits('1') 两者都是 False）。
            gb_none = vals_buggy.get(expr)
            nonliteral.append((expr, gf))
            tests.append({"name": f"real::{expr}"[:60],
                          "call": f"repr({expr})",
                          "expect": gf,
                          "via_repr": True})
            if gb_none != gf:
                disc.append(expr)
            continue
        gb = vals_buggy.get(expr)
        tests.append({"name": f"real::{expr}"[:60], "call": expr, "expect": lit})
        if gf != gb:
            disc.append(expr)
    # ── 2b. 【期望异常】类探针：单独校准（比"是否抛异常"，不是比返回值）──
    if raise_rows:
        print("\n== 校准（期望异常）：异常类型取自测试文件，"
              "\"fixed 真的抛\"由参照实现跑两遍验证 ==")
        r_probes = [(e, x) for (e, x), m in raise_rows]
        r_keep, r_weak, r_state = calibrate_raises(fixed, buggy, r_probes)
        if r_state == "unstable":
            print(f"  ⛔ {len(r_weak)} 条**不可复现**，全部丢弃")
        for e, x, why in r_weak[:5]:
            print(f"      ✗ {e[:44]:44} raises {x:12} → {why}")
        if len(r_weak) > 5:
            print(f"      … 另有 {len(r_weak) - 5} 条")
        print(f"  采纳 {len(r_keep)} 条（有鉴别力的 {len(r_keep)}）")
        for t in r_keep[:6]:
            print(f"      · {t['call'][:52]:52} → raises {t['raises']}")
        tests.extend(r_keep)
        disc.extend(t["call"] for t in r_keep)

    if nonliteral:
        print(f"  ℹ️ {len(nonliteral)} 条返回值是对象 ⇒ **降级为 repr 包装**"
              f"（比较落在 repr 字符串上，已验 repr 与 __eq__ 同判）：")
        for e, g in nonliteral[:4]:
            print(f"      · {e[:44]:44} → expect={str(g)[:30]}")
        if len(nonliteral) > 4:
            print(f"      … 另有 {len(nonliteral) - 4} 条")
    if outsized:
        print(f"  ⛔ 剔除 {len(outsized)} 条 expect **超出 JS 安全整数范围**的探针"
              f"（会被 `aether/ledger.py` 的跨语言闸拒绝 ⇒ 整个 run 写账本时崩）：")
        for e, v in outsized[:3]:
            print(f"      ✗ {e[:44]:44} → {v}")
    if unusable:
        print(f"  ⛔ 剔除 {len(unusable)} 条**在参照实现上就求值不了**的探针"
              f"（多为依赖循环变量等上下文，单拎出来必然 raise）：")
        for e in unusable[:4]:
            print(f"      ✗ {e[:60]}")
        if len(unusable) > 4:
            print(f"      … 另有 {len(unusable) - 4} 条")
    if any(t.get("via_repr") for t in tests):
        #: ⛔ 降级之后必须**回头验一遍**：fixed 版要在全部判据上通过，
        #:    否则"保住了更多判据"其实是引入了新的假失败。
        #: ⛔ 只验**返回值类**判据：raises 类没有 `expect` 键（它在
        #:    `calibrate_raises` 里已经按"fixed 跑两遍都抛"验过了），
        #:    混在一起会 KeyError —— 更糟的是如果硬补一个 expect 进去，
        #:    等于用返回值口径去验一个异常口径的判据。
        _re = {}
        for t in tests:
            if "expect" in t:
                _re[t["call"]] = t["expect"]
        _got = _got_values(fixed, list(_re))
        bad = [k for k, v in _got.items() if _literal(v)[0] != _re[k]]
        print(f"  ⛔ 降级后回头验 fixed 版："
              f"{'✅ 全部通过' if not bad else '❌ 有 ' + str(len(bad)) + ' 条仍失败'}")
        if bad:
            for k in bad[:5]:
                print(f"      ✗ {k}: got={_got[k]} expect={_re[k]}")
            return 1
    print(f"  采纳 {len(tests)} 条判据")
    print(f"  ⛔ 有鉴别力的（fixed≠buggy）：{len(disc)} 条")

    if not disc:
        print("\n✗ 没有任何一条能区分两版 ⇒ 这个任务不可解（或已经解了），不物化")
        return 1
    #: ⛔ 两类分开打印：raises 类的"值"是**是否抛异常**，不在 vals_fixed 里
    for e in disc:
        if e in vals_fixed:
            print(f"      ✓ {e}   fixed={vals_fixed[e]}  buggy={vals_buggy.get(e)}")
        else:
            _r = next((t for t in tests if t.get("call") == e and "raises" in t), None)
            _x = _r["raises"] if _r else "?"
            print(f"      ✓ {e}   【期望异常】fixed=raises {_x}  buggy=不抛（或不抛这一种）")

    # ── 3. 物化到临时根 ───────────────────────────────────────────
    out = Path(args.out_root) / "tasks" / env_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "task.py").write_text(fixed, encoding="utf-8")
    (out / "tests.json").write_text(
        __import__("json").dumps(
            {"env_id": env_id,
             "note": (f"真实 bug fix：{repo.name} {args.commit[:7]} "
                      f"（{args.module}）。expect 由修复后源码真实运行校准，"
                      f"不许手写。{len(disc)}/{len(tests)} 条能区分修复前后。"),
             "source_origin": f"{repo.name}@{args.commit[:7]}:{args.module}",
             "real_fix": {"repo": repo.name, "commit": args.commit,
                          "module": args.module,
                          "discriminating": disc},
             "tests": tests},
            ensure_ascii=False, indent=2),
        encoding="utf-8")
    #: broken_source 单独放，供实验脚本直接喂 dev_suite
    (out / "broken_source.py").write_text(buggy, encoding="utf-8")

    print(f"\n✅ 已物化到 {out}")
    print(f"   task.py        {len(fixed.splitlines())} 行（fixed 版，作为基线）")
    print(f"   broken_source  {len(buggy.splitlines())} 行（含真实 bug）")
    print(f"   tests.json     {len(tests)} 条（{len(disc)} 条有鉴别力）")
    return 0


def _literal(got: str | None):
    """把 `got=` 后面的文本还原成 Python 值。

    ⛔ 用 `ast.literal_eval` 而不是 `eval`：来源是进程输出（不可信），
       且绝大多数 expect 是 bool/int/str/list 之一。

    ⛔ 失败时必须返回一个**可判别的失败**，不许退回原字符串：
       2026-10-24 实测 —— `Bits('01') & Bits('10')` 的 got 是
       `<boltons.mathutils.Bits object at 0x1034a1d90>`，literal_eval 抛
       ValueError，而当时 `except` 里 `return got` ⇒ 调用方拿到的"值"看起来
       正常 ⇒ 丢弃分支永远走不到 ⇒ 12 条随机判据被当成有效判据。
    """
    if got is None:
        return None, True
    try:
        return ast.literal_eval(got), True
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return got, False


if __name__ == "__main__":
    sys.exit(main())

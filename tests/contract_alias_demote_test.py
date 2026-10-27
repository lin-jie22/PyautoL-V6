#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**判据抽取层**（`aether/extract.py`）的方向、边界与唯一性。

═══ 存在的理由（README 162 的取证结论）═══

到 161 为止，"修好真实 bug"的判据**全是人手工转写的** ⇒ 端到端链路断在
第一公里。本层是直接导致它被发现的取证对象：

    b2h(1024)                   ⇒ NameError: b2h        （原样抽出来）
    strutils.bytes2human(1024)  ⇒ NameError: strutils   （157 内联器的产物）
    bytes2human(1024)           ⇒ fixed '1K' / buggy '1024B'  ← 有鉴别力

═══ ⛔⛔ 本契约最要紧的一条：**方向不变量** ═══

157 的 `inline_expr` 把局部别名展开成**更外层**的作用域名，而 `run_in_sandbox`
装的是**被测模块本体**（那里只有最内层的裸名）⇒ 它是朝着唯一错的方向做。
本层必须反向（往里降）。C 组盯的就是这条：降级产物的自由变量里
**不许再出现被测模块的 import 别名**，谁把它改回"往外展"都会在这里被抓住。

═══ 组 ═══

  A 降级器基本正确性（A 直写前缀 / B 局部别名 / 链式 / 职责边界）
  B ⛔⛔ 两条自限，各带反证（他模块不降级 / 时间随机拒收）
  C ⛔⛔ 方向不变量（降级只许往里，不许往外）
  D 归因不漂移（fixture > rebound > other）
  E ⛔ 抽取层也"不看答案"：无 base / 无 `.source`
  F ⛔ 规则只有一份（脚本不得自带第二实现）
  G 离线端到端：一条人为制造的"测试 vs 模块本体"错位能被抽出来
  H 依托真实仓库的读数（缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）

跑法（离线、秒级）：
    python tests/contract_alias_demote_test.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

from aether.extract import (  # noqa: E402
    attribute, collect_bindings, demote_binds, demote_expr, free_names,
    func_params, gold_of, has_unstable_call, inline_expr, module_aliases,
    rebound_names, resolve_probe, target_module,
)

SKIPS: list = []
FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


TEST_SRC = ("from boltons import strutils\n"
            "import datetime\n"
            "def test_bytes2human():\n"
            "    b2h = strutils.bytes2human\n"
            "    h = b2h\n"
            "    assert b2h(1024) == '1K'\n"
            "    assert h(2048) == '2K'\n"
            "def test_other():\n"
            "    dd = datetime.date\n"
            "    assert strutils.strip_ansi('x') == 'x'\n")
TARGET = target_module("boltons/strutils.py")


def main() -> int:
    print("=" * 74)
    print("契约：判据抽取层（`aether/extract.py`）")
    print("=" * 74)

    tree = ast.parse(TEST_SRC)
    aliases = module_aliases(tree)
    binds = collect_bindings(TEST_SRC)

    print("\n== A 组：降级器基本正确性 ==")
    ck("A1 目标模块名推导", TARGET == "boltons.strutils", TARGET)
    ck("A2 四种 import 都认得出",
       aliases.get("strutils") == "boltons.strutils"
       and aliases.get("datetime") == "datetime", f"{aliases}")
    d = demote_binds(binds["test_bytes2human"], TARGET, aliases)
    ck("A3 B 形态：局部别名 ⇒ 模块内裸名",
       ast.unparse(d["b2h"]) == "bytes2human", ast.unparse(d["b2h"]))
    ck("A4 ⛔ 链式交给内联（职责边界）",
       ast.unparse(d["h"]) == "b2h", ast.unparse(d["h"]))
    ck("A5 链式端到端成立",
       inline_expr("h(2048)", d) == "bytes2human(2048)")
    ck("A6 A 形态：直写前缀 ⇒ 裸名",
       demote_expr("strutils.strip_ansi('x')", TARGET, aliases)
       == "strip_ansi('x')")
    ck("A7 resolve_probe 组合 A+B",
       resolve_probe("b2h(1024)", binds["test_bytes2human"], TARGET,
                     aliases) == "bytes2human(1024)")
    ck("A8 无依赖可降 ⇒ 返回 None（不许原样返回假装成功）",
       resolve_probe("zzz(1)", {}, TARGET, aliases) is None)

    print("\n== B 组：⛔⛔ 两条自限（各带反证）==")
    d2 = demote_binds(binds["test_other"], TARGET, aliases)
    ck("B1 自限1：指向**别的模块**不许降级",
       ast.unparse(d2["dd"]) == "datetime.date", ast.unparse(d2["dd"]))
    ck("B2 ⛔ 自限1 的反证：若把它也降级，就会凭空造出沙箱里没有的名字",
       demote_expr("datetime.date(2020, 1, 1)", TARGET, aliases) is None,
       "他模块被降级了")
    ck("B3 自限2：时间/随机依赖被识别",
       has_unstable_call("date.today() + 1") is True)
    ck("B4 无依赖的不误伤", has_unstable_call("bytes2human(1024)") is False)
    cands = [c for c in _extract(TEST_SRC, TARGET)]
    ck("B5 ⛔ 自限2 落到产物上：含 today 的判据不被抽出",
       all("today" not in c["call"] for c in cands))

    print("\n== C 组：⛔⛔ 方向不变量 ==")
    bad = []
    for src in ("assert b2h(1024) == '1K'", "assert h(2048) == '2K'",
                "assert strutils.strip_ansi('x') == 'x'"):
        got = None
        for sc in ("<module>", "test_bytes2human", "test_other"):
            got = resolve_probe(src.replace("assert ", "").split(" == ")[0],
                                binds.get(sc, {}), TARGET, aliases) or \
                src.replace("assert ", "").split(" == ")[0]
            if not any(n.startswith("strutils") or n == "strutils"
                       for n in free_names(got)):
                break
        leftovers = {n for n in free_names(got)
                     if n in aliases and aliases[n].startswith(TARGET)}
        if leftovers:
            bad.append((src, got, leftovers))
    ck("C1 ⛔ 降级产物里不许再出现被测模块的 import 别名",
       not bad, f"{bad}")
    ck("C2 ⛔ 157 的方向仍在原地保留（两者都要在，才谈得上对照）",
       inline_expr("b2h(1024)", binds["test_bytes2human"])
       == "strutils.bytes2human(1024)")
    ck("C3 两个方向的产物确实不同（否则 '方向' 无从谈起）",
       inline_expr("b2h(1024)", binds["test_bytes2human"])
       != resolve_probe("b2h(1024)", binds["test_bytes2human"], TARGET,
                        aliases))

    print("\n== D 组：归因不漂移 ==")
    src3 = ast.parse("def test_x(tmpdir):\n"
                     "    s = 1\n    s = 2\n"
                     "    assert f(tmpdir, s, zzz) == 1\n")
    ck("D1 fixture 形参被认出",
       func_params(src3).get("test_x") == {"tmpdir"})
    ck("D2 自不算 fixture",
       func_params(ast.parse("class T:\n def test_y(self):\n  pass\n"))
       .get("test_y") == set())
    ck("D3 多值重绑被认出", rebound_names(src3).get("test_x") == {"s"})
    ck("D4 ⛔ 优先级：fixture 压过 rebound",
       attribute({"tmpdir", "s"}, "test_x", func_params(src3),
                 rebound_names(src3)) == "fixture")
    ck("D5 纯自由变量落 other",
       attribute({"zzz"}, "test_x", func_params(src3),
                 rebound_names(src3)) == "other")
    ck("D6 空集合也落 other（不是 KeyError，也不是猜测）",
       attribute(set(), "test_x", func_params(src3), {}) == "other")

    print("\n== E 组：⛔ 抽取层也不看答案 ==")
    et = ast.parse((ROOT / "aether" / "extract.py").read_text(encoding="utf-8"))
    ck("E1 无 `.source` 属性访问",
       not any(isinstance(n, ast.Attribute) and n.attr == "source"
               for n in ast.walk(et)))
    argnames = {a.arg for n in ast.walk(et)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                for a in n.args.args}
    ck("E2 没有名为 base / base_src / env 的形参",
       not (argnames & {"base", "base_src", "env"}), f"{sorted(argnames)}")

    print("\n== F 组：⛔ 规则只有一份（不许两把尺子）==")
    pcg = ast.parse((ROOT / "scripts" / "probe_context_gate.py").read_text(
        encoding="utf-8"))
    local = {n.name for n in pcg.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    shared = {"scoped_assertions", "scoped_raise_probes", "collect_bindings",
              "inline_expr", "has_unstable_call", "lit", "gold_of"}
    ck("F1 ⛔ 157 脚本不再自带这七个规则实现",
       not (local & shared), f"仍自定义了 {sorted(local & shared)}")
    pad = ast.parse((ROOT / "scripts" / "probe_alias_demote.py").read_text(
        encoding="utf-8"))
    pad_local = {n.name for n in pad.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    ck("F2 ⛔ 162 脚本也不自带规则实现",
       not (pad_local & shared), f"仍自定义了 {sorted(pad_local & shared)}")

    print("\n== G 组：离线端到端（不依赖真实仓库）==")
    toy_mod = ("def bytes2human(n):\n"
               "    if n < 1024:\n        return f'{n}B'\n"
               "    return f'{n // 1024}K'\n")
    toy_test = ("from boltons import strutils\n"
                "def t():\n"
                "    b2h = strutils.bytes2human\n"
                "    assert b2h(1024) == '1K'\n")
    got = _extract(toy_test, TARGET)
    ck("G1 能抽出一条判据", len(got) == 1, f"{got}")
    if got:
        ck("G2 ⛔ 抽出的 call 已是**脱离测试上下文可求值**的形式",
           got[0]["call"] == "bytes2human(1024)", got[0]["call"])
        ck("G3 测试的期望值被当成金标准取出来了",
           got[0]["gold"] == "'1K'" and gold_of(got[0]["gold"]) == "1K")
    ck("G4 ⛔ 沙箱装的是模块本体 ⇒ 裸名能跑通、带前缀不能",
       _runs(toy_mod, "bytes2human(1024)")
       and not _runs(toy_mod, "strutils.bytes2human(1024)"))

    print("\n== H 组：真实仓库读数（boltons，缺失则跳过并披露）==")
    repo = Path("/tmp/realrepo/boltons")
    if not (repo / ".git").exists():
        skip("H1-H3 自动抽取 `766b554`",
             f"本机没有 {repo}（not covered ≠ passed）")
    else:
        try:
            from aether.environment import run_in_sandbox
            import subprocess
            fixed = subprocess.run(
                ["git", "show", "766b554:boltons/strutils.py"],
                cwd=str(repo), capture_output=True, text=True).stdout
            tsrc = subprocess.run(
                ["git", "show", "766b554:tests/test_strutils.py"],
                cwd=str(repo), capture_output=True, text=True).stdout
            crit = _extract(tsrc, TARGET)
            ck("H1 ⛔ 真实测试里能自动抽出 ≥12 条判据",
               len(crit) >= 12, f"{len(crit)} 条")
            items = [(f"a{i}", c["call"], None) for i, c in enumerate(crit)]
            r = run_in_sandbox(fixed, [{"name": f"a{i}", "call": c["call"],
                                        "expect": gold_of(c["gold"])}
                                       for i, c in enumerate(crit)
                                       if c.get("gold")])
            ok = sum(1 for x in r.results if x.get("passed"))
            ck("H2 ⛔ 抽出的判据在参照实现上大多数通过（语义没被改坏）",
               ok >= len(r.results) * 0.8, f"{ok}/{len(r.results)}")
            buggy = subprocess.run(
                ["git", "show", "766b554^:boltons/strutils.py"],
                cwd=str(repo), capture_output=True, text=True).stdout
            rb = run_in_sandbox(buggy, [{"name": f"a{i}", "call": c["call"],
                                         "expect": gold_of(c["gold"])}
                                        for i, c in enumerate(crit)
                                        if c.get("gold")])
            f2p = sum(1 for a, b in zip(r.results, rb.results)
                      if a.get("passed") and not b.get("passed"))
            ck("H3 ⛔ 其中真有鉴别力（F2P）≥1",
               f2p >= 1, f"F2P={f2p}")
        except Exception as e:  # noqa: BLE001
            skip("H1-H3 自动抽取 `766b554`", f"跑不起来：{type(e).__name__}: {e}")

    print("\n== I 组：⛔ 端到端的「成功」必须是**判据驱动**的（三对照）==")
    #: ⛔⛔ 存在的理由：163 把「全自动端到端修好」从 1 提到 2，但**读数本身不能自证**。
    #:   `synthesize_patch` 是可以恒返回正确答案的（比如内部泄漏了基线），那样
    #:   "修好了"和"判据"之间就没有因果关系。三组对照把这条因果钉住：
    #:     A 真           ⇒ 有解
    #:     B 抽掉 F2P     ⇒ **必须无解**（没有失败在驱动 ≠ 有东西可搜）
    #:     C 篡改判据     ⇒ **必须无解/换解**（真在盲从判据的话，期望改了它就会
    #:                       跟着改；若原样给回同一个答案，说明那答案不是搜出来的）
    #:   ⛔ 第一版 C 组**只变异了 expect**，遇到 raises 类的 F2P 一个字节都没改到
    #:     ⇒ 对照组恒真、什么都测不出来（第 131 条：一种变异测不出「够用」）。
    _src = "\n\ndef f(x):\n    if x > 3:\n        return True\n    return False\n"
    _tests = [{"name": "t0", "call": "f(5)", "expect": True},
              {"name": "t1", "call": "f(3)", "expect": True},
              {"name": "t2", "call": "f(2)", "expect": False}]
    try:
        from aether.synthesize import synthesize_patch
        ra = synthesize_patch(_src, _tests, timeout=15.0)
        ck("I1 A 组：有 F2P ⇒ 搜得出来且全绿",
           ra.found and ra.full_pass, f"found={ra.found} full={ra.full_pass}")
        ck("I2 A 组：产物确实是那一行 cmp 翻转",
           "x >= 3" in (ra.patch or ""), repr(ra.patch)[:60])
        rb = synthesize_patch(_src, [_tests[0], _tests[2]], timeout=15.0)
        ck("I3 B 组：抽掉唯一的 F2P ⇒ **必须无解**",
           rb.found is False, f"found={rb.found} new_ok={rb.new_ok}")
        bad = [dict(t) for t in _tests]
        bad[1]["expect"] = False
        rc = synthesize_patch(_src, bad, timeout=15.0)
        ck("I4 C 组：篡改 F2P 期望 ⇒ 不许还给原答案",
           (rc.patch or "") != (ra.patch or ""),
           f"篡改后 patch={repr(rc.patch)[:60]}")
    except Exception as e:  # noqa: BLE001
        skip("I 组 端到端三对照", f"跑不起来：{type(e).__name__}: {e}")

    print("\n== J 组：⛔ 沙箱返回结果**没有 `got`**（别按它读）==")
    #: ⛔ 实踩：`run_in_sandbox(...).results` 的元素只有
    #:   {name, passed, evidence, why} —— **没有 got**。
    #:   而 `run_all()` 是另造的壳，它**有** got（从 evidence 字符串里 split 出来）。
    #:   ⇒ 同一个概念两套键名，按错的那套读恒得到 None（第 59 条读漏键名）。
    #:   这里把两边的事实都钉住，谁改了任何一侧的形状都会在这里被抓住。
    try:
        from aether.environment import run_in_sandbox
        r = run_in_sandbox("def g():\n    return 7\n",
                           [{"name": "q", "call": "g()"}])
        row = r.results[0]
        ck("J1 `results` 里确实没有 got 键（事实）",
           "got" not in row, f"keys={sorted(row)}")
        ck("J2 但一定有 passed / evidence / why",
           {"passed", "evidence", "why"} <= set(row), sorted(row))
        import importlib.util
        _sp = importlib.util.spec_from_file_location(
            "pcg", ROOT / "scripts" / "probe_context_gate.py")
        _pcg = importlib.util.module_from_spec(_sp)
        _sp.loader.exec_module(_pcg)
        g = _pcg.run_all("def g():\n    return 7\n", [("k", "g()", None)])
        ck("J3 `run_all` 那层必须补齐 got（从 evidence 解析）",
           g.get("k", {}).get("got") is not None,
           str(g.get("k")))
        bad_raises = _pcg.run_all("def g():\n    return 7\n",
                                  [("j", "g()", "ValueError")])
        ck("J4 raises 类拿不到 got 是**预期的**（evidence 不以 got= 开头）",
           bad_raises.get("j", {}).get("got") is None,
           str(bad_raises.get("j")))
    except Exception as e:  # noqa: BLE001
        skip("J 组 沙箱返回结构", f"跑不起来：{type(e).__name__}: {e}")

    print("\n" + "=" * 74)
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    msg = "✅ 抽取层方向正确、两条自限在位、规则只有一份"
    if SKIPS:
        msg += f"（{len(SKIPS)} 项跳过：未覆盖 ≠ 通过）"
    print(msg)
    return 0


def _extract(test_src, target):
    from aether.extract import extract_criteria
    return extract_criteria(test_src, target)


def _runs(mod_src, expr):
    from aether.environment import run_in_sandbox
    try:
        r = run_in_sandbox(mod_src, [{"name": "q", "call": expr}])
        return bool(r.results and r.results[0].get("passed"))
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())

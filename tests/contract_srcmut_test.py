#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：让系统能**提议改自己的源码** —— 但提议的落点是证据，不是自动写入。

═══ 为什么这条边界必须画在这儿 ═══

"改 harness" 与 "改源码" 是两种风险量级：

    · harness 是**纯数据**（frozen + replace）⇒ 回滚 = 指针回退，
      而且判据住在 harness 之外 ⇒ 裁决独立于被测对象。
    · 源码里**住着判据层**（`judge.JUDGE_MODULES`）。让循环改它自己的判据
      ⇒ "非回归闸通过"这个结论不再独立于被测对象 ⇒ **自证**；
      而且删错了**不可逆**（账本只能留痕）。

⇒ 本契约守的是：**提议 → 过闸 → 记账** 这条链真的通，
   而 **执行** 那一步在构造上够不到生产源码。

═══ 本轮实测修掉的三件事（都是"看着有闸，实际没有"）═══

  ① **非回归闸恒不触发**：`validate()` 原写 `if non_regression_ok is False`
     ⇒ 调用方不传（None）就**跳过**；两个生产调用点从来不传
     ⇒ 四道闸里唯一会拦的那一道从没被喂过裁决。⇒ 改成 fail-closed。

  ② **排除面不同源**：`build()` 用 `judge ∪ SOURCE_PROTECTED`，
     而 `validate()` 只用 `judge` ⇒ 手工构造的 `aether/judge.py` 提案
     在第 ① 条**不触发**（judge 不在 JUDGE_MODULES 里）。⇒ 收口到一处。

  ③ **跑不起来 ≠ 删坏了**：环境没装 pytest 那次，before/after 都是 rc≠0
     ⇒ 按"两边一样"会得出"删掉没问题"这个**完全错误**的结论。
     ⇒ 跑不起来一律 `unverified`，且原因写清楚是环境问题。

═══ 顺带记一条"用错工具"的坑 ═══

V6 的 `tests/contract_*.py` 是**可执行脚本**（自带 `_main()`，退出码 0 = 全绿），
**不是** pytest 用例。用 `pytest` 跑它 ⇒ `no tests ran` 且退出码 0
⇒ 对拍两边恒等 ⇒ 空闸。与 r39 里用错 `probe()` 而非 `probe_removal()` 同形态。

跑法：
    python tests/contract_srcmut_test.py
"""
import sys
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


# ─────────────────────────────────────────────────────────────────
# 临时仓库脚手架（A/B/F 组用）
# ─────────────────────────────────────────────────────────────────
def _mk(root: Path, src: str) -> Path:
    """⛔ 目录必须叫 `aether`：扫描面是 `selfref.SOURCE_DIRS == ("aether",)`，
    建在别的名字下面 ⇒ 扫不到 ⇒ 断言会假红（红的是脚手架，不是被测对象）。"""
    (root / "aether").mkdir(parents=True, exist_ok=True)
    p = root / "aether" / "mod.py"
    p.write_text(src, encoding="utf-8")
    return p


#: 含不可达块的模块：`return 1` 之后的 2 行永远执行不到。
_SRC_UNREACH = (
    "def f():\n"
    "    return 1\n"
    "    x = 2\n"        # noqa: F841  ← 不可达
    "    return x\n"     # ← 不可达
)

#: ⛔ 对照组形态：`return` 藏在 `if` 里 ⇒ 后面的代码**是可达的**（x 为假时）。
_SRC_REACHABLE = (
    "def g(flag):\n"
    "    if flag:\n"
    "        return 1\n"
    "    y = 2\n"
    "    return y\n"
)


def main():
    import aether.cleanup as CL
    import aether.selfref as SR
    import aether.srcmut as SM

    # ══ A 组：第三类形态（不可达块）的保守口径 ══════════════════
    print("A 组：不可达块扫描（保守：宁可漏报，不可冤报）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _SRC_UNREACH)
        old = SR.ROOT
        try:
            SR.ROOT = root
            r = SR.scan_unreachable()
        finally:
            SR.ROOT = old
        items = r.get("unreachable") or []
        ck("A1 顶层 return 之后的语句 ⇒ 扫得出来",
           r.get("n_unreachable") == 1, f"实得 {r.get('n_unreachable')}")
        ck("A2 区间与语句数正确（第 3–4 行，2 条）",
           bool(items) and items[0]["start_line"] == 3
           and items[0]["end_line"] == 4 and items[0]["n_stmts"] == 2,
           str(items[:1]))
        ck("A3 终止语句的类型与行号被记下来（可审计）",
           bool(items) and items[0]["kind"] == "return"
           and items[0]["terminator_line"] == 2, str(items[:1]))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _SRC_REACHABLE)
        old = SR.ROOT
        try:
            SR.ROOT = root
            r2 = SR.scan_unreachable()
        finally:
            SR.ROOT = old
        ck("A4 ⛔ 保守：`if flag: return` 之后的代码是**可达的** ⇒ 不报",
           r2.get("n_unreachable") == 0, f"实得 {r2.get('n_unreachable')}")

    #: 反证：把 `return 1` 换成 `pass` ⇒ A1 必须转红（A1 不是恒真）
    _src_no_term = _SRC_UNREACH.replace("return 1", "pass")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _src_no_term)
        old = SR.ROOT
        try:
            SR.ROOT = root
            r3 = SR.scan_unreachable()
        finally:
            SR.ROOT = old
        ck("A5 反证：去掉终止语句 ⇒ 扫不出来（A1/A2 不是恒真）",
           r3.get("n_unreachable") == 0, f"实得 {r3.get('n_unreachable')}")

    # ══ B 组：结构化编辑与指纹 ═══════════════════════════════════
    print("B 组：带指纹的编辑（行号会漂，指纹不会）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _SRC_UNREACH)
        e = SM.from_lines(root, "aether/mod.py", 3, 4)
        ck("B1 from_lines 生成带指纹的编辑", e is not None and len(e.sha256) == 64,
           str(e))
        ck("B2 指纹校验通过（内容没变）", SM.verify(root, e)[0],
           SM.verify(root, e)[1])
        ck("B3 行区间被记下来（3–4，共 2 行）",
           e.start_line == 3 and e.end_line == 4 and e.n_lines == 2,
           f"{e.start_line}-{e.n_lines}")

        drift = SM.Edit(file=e.file, start_line=e.start_line + 1,
                        end_line=e.end_line + 1, sha256=e.sha256)
        ok, why = SM.verify(root, drift)
        ck("B4 ⛔ 反证：行号漂移 ⇒ 指纹不匹配 ⇒ 拒绝", not ok, why)
        ck("B5 越界 ⇒ from_lines 返回 None（不抛）",
           SM.from_lines(root, "aether/mod.py", 99, 100) is None)
        ck("B6 文件不存在 ⇒ 返回 None（不抛）",
           SM.from_lines(root, "src/nope.py", 1, 2) is None)

    try:
        SM.Edit(file="aether/mod.py", start_line=1, end_line=2, op="insert")
        ck("B7 op 白名单：只有删除是可证明的 ⇒ insert 抛错", False, "没抛")
    except ValueError:
        ck("B7 op 白名单：只有删除是可证明的 ⇒ insert 抛错", True)
    try:
        SM.Edit(file="", start_line=1, end_line=2)
        ck("B8 空文件名 ⇒ 抛错", False, "没抛")
    except ValueError:
        ck("B8 空文件名 ⇒ 抛错", True)
    ck("B9 寻址键只由身份字段决定（同一编辑同 id）",
       SM.Edit(file="a.py", start_line=1, end_line=2, sha256="x").edit_id
       == SM.Edit(file="a.py", start_line=1, end_line=2, sha256="x").edit_id)

    # ══ C 组：生产根不可写（**构造上**的排除）════════════════════
    print("C 组：生产根不可写")
    from aether.paths import ROOT as PROD_ROOT
    prod_edit = SM.Edit(file="aether/srcmut.py", start_line=1, end_line=1,
                        sha256="x")
    aok, why, n = SM.apply_edits(PROD_ROOT, [prod_edit])
    ck("C1 ⛔ 生产根 ⇒ 拒绝应用（不是警告后照做）", not aok and n == 0, why)
    ck("C2 拒绝原因写清楚了（不留'为什么不行'的空白）",
       "生产根" in why and "人" in why, why)
    ck("C3 ⛔ 反证：判定是真的在认路径，不是一律拒绝",
       SM._is_production_root(PROD_ROOT) is True
       and SM._is_production_root("/tmp/definitely-not-prod") is False)
    #: 对照组：临时根**可以**应用 ⇒ 证明 C1 不是"apply_edits 本身坏了"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _SRC_UNREACH)
        e = SM.from_lines(root, "aether/mod.py", 3, 4)
        aok2, why2, n2 = SM.apply_edits(root, [e])
        after = (root / "aether" / "mod.py").read_text(encoding="utf-8")
        ck("C4 ⛔ 反证（对照组）：临时根上应用成功，文件真被改了",
           aok2 and n2 == 2 and "x = 2" not in after, f"{why2} n={n2}")

    # ══ D 组：判据层排除（build 与 validate **同一个**排除面）══════
    print("D 组：判据层排除")
    prot = SM.SOURCE_PROTECTED
    ck("D1 排除名单含闸自己 / judge / selfref / verifier",
       {"srcmut", "judge", "selfref", "verifier"} <= set(prot), str(sorted(prot)))
    ck("D2 ⛔ 闸自己在名单里 —— 清理能删掉自己的闸 = 没有闸",
       "srcmut" in prot)
    prot_hit = [SM.verify(PROD_ROOT, SM.Edit(file=f"aether/{m}.py",
                                             start_line=1, end_line=2,
                                             sha256="x"))[0]
                for m in ("judge", "selfref", "srcmut")]
    ck("D3 verify 对保护模块一律拒绝", not any(prot_hit), str(prot_hit))
    ck("D4 ⛔ build 与 validate 用**同一个**排除面（实测抓到的缺口）",
       set(CL._protected_modules()) >= set(prot),
       f"validate 面缺 {set(prot) - set(CL._protected_modules())}")
    #: 反证：把 srcmut 从排除面拿掉 ⇒ 它不再因"排除"被拒（D2 不是摆设）。
    #: ⛔ 断言的是**拒绝的性质**变了，不是"拒绝消失" —— 移出名单后它仍会
    #:    因指纹不匹配被拒（那是对的），但**不再是**判据层那道锁在起作用。
    try:
        SM.SOURCE_PROTECTED = frozenset({"verifier"})
        _, why_loose = SM.verify(PROD_ROOT, SM.Edit(file="aether/srcmut.py",
                                                    start_line=1, end_line=2,
                                                    sha256="x"))
    finally:
        SM.SOURCE_PROTECTED = prot
    ck("D5 反证：把闸自己移出名单 ⇒ 它不再是'排除'性质的拒绝",
       "排除名单" not in why_loose, why_loose)

    # ══ E 组：非回归闸 fail-closed（本轮核心修复）═════════════════
    print("E 组：非回归闸 —— 拿不到裁决就拦")
    def _mk_prop(**kw):
        base = dict(kind="unreachable", target="mod:3-4", where="aether/mod.py:3-4",
                    reason="r", load_before={"unreachable": 1},
                    load_after_expected={"unreachable": 0}, patch="# x",
                    edits=[{"file": "aether/mod.py", "start_line": 3,
                            "end_line": 4, "sha256": "y",
                            "op": "delete_range"}])
        base.update(kw)
        return CL.CleanupProposal(**base)

    r_none = CL.validate(_mk_prop())
    ck("E1 ⛔ 不传裁决 ⇒ **拦下**（旧写法这里是放行）",
       r_none.gate_passed is False, str(r_none.gate_reasons))
    ck("E2 拦截原因指向'缺裁决'，不是含糊其辞",
       any("没有通过裁决" in x for x in r_none.gate_reasons),
       str(r_none.gate_reasons))
    r_false = CL.validate(_mk_prop(), non_regression_ok=False)
    ck("E3 裁决=False ⇒ 拦下", r_false.gate_passed is False)
    r_true = CL.validate(_mk_prop(), non_regression_ok=True)
    ck("E4 裁决=True ⇒ 放行", r_true.gate_passed is True,
       str(r_true.gate_reasons))
    #: ⛔⛔ 反证（事故版）：复现旧写法，证明它在 None 时**确实放行**
    #:     不写这条，E1 只证明"现在是对的"，证明不了"原来那个是真的漏"。
    def _old_style_gate(non_regression_ok):
        """旧写法：`if non_regression_ok is False`。"""
        reasons = []
        if non_regression_ok is False:
            reasons.append("非回归闸未通过")
        return not reasons           #: True = 放行

    ck("E5 ⛔⛔ 反证：旧写法在 None 时**放行**（证明这个漏是真的）",
       _old_style_gate(None) is True and _old_style_gate(False) is False)
    ck("E6 新写法在 None 时拦下（与旧写法形成对照）",
       CL.validate(_mk_prop()).gate_passed is False)
    #: 第 ⑤ 条：可证明的那类必须带可执行编辑
    r_noedit = CL.validate(_mk_prop(edits=[]), non_regression_ok=True)
    ck("E7 不可达块缺结构化编辑 ⇒ 拦（对拍无从下手）",
       r_noedit.gate_passed is False, str(r_noedit.gate_reasons))

    # ══ F 组：删前/删后对拍 ══════════════════════════════════════
    print("F 组：对拍（判决权在冻结契约，不在本模块）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _SRC_UNREACH)
        e = SM.from_lines(root, "aether/mod.py", 3, 4)

        p_ok = SM.probe(root, [e], runner=lambda r: {"ok": True, "failed": []})
        ck("F1 两边都绿 ⇒ ok 且**不是** unverified",
           p_ok["ok"] is True and p_ok["unverified"] is False, str(p_ok["reason"]))
        ck("F2 应用行数被记下来（读数：这次真删了几行）",
           p_ok["applied_lines"] == 2, str(p_ok["applied_lines"]))

        #: ⛔⛔ 反证：两边都跑不起来 ⇒ 必须是 **unverified**，
        #:     绝不能因为"两边一样"就判等价（实测踩过）。
        p_dead = SM.probe(root, [e],
                          runner=lambda r: {"ok": False, "failed": [],
                                            "rc": 1, "error": "No module"})
        ck("F3 ⛔⛔ 跑不起来 ⇒ unverified（**不是** ok=False）",
           p_dead["unverified"] is True and p_dead["ok"] is False,
           f"ok={p_dead['ok']} unverified={p_dead['unverified']}")
        ck("F4 原因写明是**环境问题**，不是'删坏了'",
           "跑不起来" in p_dead["reason"], p_dead["reason"])

        p_none = SM.probe(root, [e])
        ck("F5 没有 runner ⇒ unverified（不许冒充'已验证'）",
           p_none["unverified"] is True and p_none["ok"] is False)
        p_empty = SM.probe(root, [], runner=lambda r: {"ok": True})
        ck("F6 没有编辑 ⇒ 无可对拍 ⇒ unverified",
           p_empty["unverified"] is True)
        #: 指纹漂移 ⇒ 拒绝对拍
        ck("F7 指纹漂移 ⇒ 拒绝对拍（证明过的不是这段代码）",
           SM.probe(root, [SM.Edit(file=e.file, start_line=4, end_line=5,
                                   sha256=e.sha256)],
                    runner=lambda r: {"ok": True})["unverified"] is True)
        #: 删后真的红了 ⇒ 必须报出来
        calls = {"n": 0}

        def _red_after(r):
            calls["n"] += 1
            return {"ok": calls["n"] == 1, "failed": [] if calls["n"] == 1
                    else ["aether/mod.py"]}

        p_red = SM.probe(root, [e], runner=_red_after)
        ck("F8 ⛔ 反证：删后契约红了 ⇒ ok=False（probe 能抓到破坏）",
           p_red["ok"] is False and p_red["unverified"] is False,
           str(p_red["reason"]))

    # ══ G 组：真实副本 + 真实子进程（整条链路端到端）══════════════
    print("G 组：真仓库 + 真契约脚本（不是 fake runner）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg").mkdir()
        (root / "pkg" / "mod.py").write_text(_SRC_UNREACH, encoding="utf-8")
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tests").mkdir()
        #: 冻结契约：**独立脚本**，退出码 0 = 全绿（与 V6 的契约同形态）
        contract = (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            "from pkg.mod import f\n"
            "import sys as _s\n"
            "_s.exit(0 if f() == 1 else 1)\n")
        (root / "tests" / "contract_mod_test.py").write_text(
            contract, encoding="utf-8")

        sel, why = SM.select_contracts(root, ["pkg/mod.py"])
        ck("G1 ⛔ 契约按 **import 关系**选中，不是按文件名猜",
           sel == ["tests/contract_mod_test.py"], f"{sel} | {why}")
        e = SM.from_lines(root, "pkg/mod.py", 3, 4)
        pr = SM.probe(root, [e], runner=SM.contract_runner(sel))
        ck("G2 真子进程跑契约 ⇒ 删前删后都绿 ⇒ 放行",
           pr["ok"] is True and pr["unverified"] is False,
           f"{pr['reason']} | {pr.get('before')}")
        ck("G3 对拍**真的跑了两遍**（两个不同的根）",
           pr.get("before", {}).get("rc") == 0
           and pr.get("after", {}).get("rc") == 0, str(pr.get("after")))

        #: ⛔⛔ 反证：把契约改成"数行数" ⇒ 删掉 2 行后必然红
        (root / "tests" / "contract_mod_test.py").write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "n = len(Path('pkg/mod.py').read_text().splitlines())\n"
            "sys.exit(0 if n == 4 else 1)\n", encoding="utf-8")
        pr2 = SM.probe(root, [e], runner=SM.contract_runner(sel))
        ck("G4 ⛔⛔ 反证：契约会因这次删除而红 ⇒ probe 报 ok=False",
           pr2["ok"] is False and pr2["unverified"] is False,
           f"{pr2['reason']}")

    # ══ H 组：进化循环的接线（落点要兑现）════════════════════════
    print("H 组：源码清理 pass 真的被接线了")
    from aether.evolution import EvolutionConfig
    ck("H1 ⛔ 生产默认 source_cleanup_enabled=True",
       EvolutionConfig().source_cleanup_enabled is True)
    ck("H2 ⛔ 生产默认 max_source_probes == 0（循环只提议、不对拍；"
       "对拍是分钟级的，交给 CLI）",
       EvolutionConfig().max_source_probes == 0)
    ck("H3 反证：显式关 ⇒ 真的关掉（不是常量）",
       EvolutionConfig(source_cleanup_enabled=False).source_cleanup_enabled
       is False
       and EvolutionConfig(max_source_probes=2).max_source_probes == 2)
    from aether.evolution import EvolutionLoop
    ck("H4 循环上有 `_source_cleanup_pass` 这个方法（不只是写在文档里）",
       callable(getattr(EvolutionLoop, "_source_cleanup_pass", None)))
    #: ⛔ 「源码清理不进 best 选择」这条**不写成断言**：它是一条**设计说明**，
    #:    用行为断言表达它需要跑一整轮循环，而勉强写成静态断言会得到一条
    #:    恒真的检查 —— 那正是本契约要消灭的东西。⇒ 它记在 `_source_cleanup_pass`
    #:    的 docstring 与 README 里，由人读，不由断言假装守住。

    print()
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 条：{FAILS}")
    else:
        print("✅ 全部通过")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

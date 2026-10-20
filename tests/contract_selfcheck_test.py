#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：`aether selfcheck` 的**每一项都必须能被打破**（否则它是装饰）。

═══ 为什么这份契约比 selfcheck 本身更重要 ═══

`selfcheck` 的全部价值在于"它红的时候你该相信它"。而一个**恒真**的
自检与"没有自检"在输出上一模一样：都是 `✅ 全部通过`。

本项目为此付过最贵的一笔学费（第 9/10 条、第 24 条）：
量具自己会说谎，而"某维度恒为 0"的第一嫌疑人应该是工具。
所以这里不验"selfcheck 全绿"（那是它自己的事），而是逐项注入破坏，
断言**对应那一项必须红** —— 一条不漏。

═══ 五组 ═══

  A 三态：ok / broken / skip 不许合并；沉默（空理由）必须当 broken；
    `--only` 之后报告必须写明这是子集。
  B 不写生产：跑完前后，工作区与被模拟的包目录**文件集合不变**。
  C ⛔ 反证组：8 个注入，逐个精确命中（本文件的正题）。
  D 预算：全跑必须远低于 60s；超预算必须判红（不是"跑完了就算通过"）。
  E 与 `check` 的分工：在没有 `tests/` 的根上，`check` 判失败而
    `selfcheck` 照跑 —— 这正是"装出来的包"的形态。

跑法（离线、秒级）：
    python tests/contract_selfcheck_test.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _status(rep, key):
    for r in rep["results"]:
        if r["key"] == key:
            return r["status"]
    return "<缺失>"


class _Patch:
    """临时替换模块属性（`with _Patch(obj, "attr", value):`）。"""

    def __init__(self, obj, attr, value):
        self.obj, self.attr, self.value = obj, attr, value
        self.old = getattr(obj, attr)

    def __enter__(self):
        setattr(self.obj, self.attr, self.value)
        return self

    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.old)
        return False


def main() -> int:
    from aether import backup as BK
    from aether import caps_candidate as CC
    from aether import install_auto as IA
    from aether import ledger as LG
    from aether import paths as P
    from aether import selfcheck as SC

    ws = Path(tempfile.mkdtemp(prefix="v6_sc_"))
    #: 一个**有任务集**的工作区（让 `seed.tasks` 真的跑起来，而不是 skip）
    (ws / "tasks" / "demo").mkdir(parents=True)
    (ws / "tasks" / "demo" / "task.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (ws / "tasks" / "demo" / "tests.json").write_text(
        json.dumps({"env_id": "demo",
                    "tests": [{"name": "add", "call": "add",
                               "args": [1, 2], "expect": 3}]}),
        encoding="utf-8")

    # ── A 组：三态 ────────────────────────────────────────────────
    print("A 组：三态分开，沉默不算通过")
    t0 = time.time()
    rep = SC.run(ws)
    secs = time.time() - t0
    ck("A1 全跑一遍：10 项都在，且没有坏项",
       rep["n"] == len(SC.CHECKS) and rep["counts"][SC.BROKEN] == 0,
       f"n={rep['n']} broken={rep['counts'][SC.BROKEN]}")
    ck("A2 每项都写了理由（沉默 ≠ 通过）",
       all(str(r["detail"]).strip() for r in rep["results"]),
       str([r["key"] for r in rep["results"] if not r["detail"].strip()]))
    ck("A3 三态的值域与 check / 体检**不重叠**（不同名才不会被误合并）",
       {SC.OK, SC.BROKEN, SC.SKIP}.isdisjoint({"pass", "fail", "timeout",
                                               "error", "skipped"}))
    ck("A4 counts 与 results 自洽",
       sum(rep["counts"].values()) == rep["n"], str(rep["counts"]))
    ck("A5 exit_code 由 ok 推导（不是另写一份判据）",
       (rep["exit_code"] == 0) == bool(rep["ok"]), f"{rep['exit_code']}")

    sub = SC.run(ws, only="ledger.chain")
    ck("A6 --only ⇒ 只跑一项", sub["n"] == 1
       and sub["results"][0]["key"] == "ledger.chain", str(sub["n"]))

    #: ⛔ 自检项返回空理由 / 未知状态时，**必须**当 broken ——
    #:    否则一个 `return ("ok", "")` 就能让所有闸恒绿。
    orig_checks = SC.CHECKS
    try:
        SC.CHECKS = (SC.Check("fake.silent", "沉默项", lambda c: (SC.OK, "")),)
        r_silent = SC.run(ws)
        ck("A7 ⛔ 返回空理由的项 ⇒ broken（沉默不许读成通过）",
           _status(r_silent, "fake.silent") == SC.BROKEN, str(r_silent["results"]))
        SC.CHECKS = (SC.Check("fake.weird", "怪状态", lambda c: ("fine", "好")),)
        r_weird = SC.run(ws)
        ck("A8 ⛔ 返回不认识的状态 ⇒ broken（不是照抄成 ok）",
           _status(r_weird, "fake.weird") == SC.BROKEN, str(r_weird["results"]))
        SC.CHECKS = (SC.Check("fake.boom", "抛异常",
                              lambda c: 1 / 0),)
        r_boom = SC.run(ws)
        ck("A9 ⛔ 抛异常 ⇒ broken 且带异常名（不是让整个自检崩掉）",
           _status(r_boom, "fake.boom") == SC.BROKEN
           and "ZeroDivision" in str(r_boom["results"][0]["detail"]),
           str(r_boom["results"][0]["detail"])[:80])
    finally:
        SC.CHECKS = orig_checks

    # ── B 组：不写生产 ────────────────────────────────────────────
    print("B 组：自检只读 —— 跑完前后文件集合必须不变")

    def snap(d: Path):
        return sorted(p.relative_to(d).as_posix()
                      for p in d.rglob("*") if p.is_file())

    ws2 = Path(tempfile.mkdtemp(prefix="v6_sc_read_"))
    (ws2 / "tasks" / "demo").mkdir(parents=True)
    (ws2 / "tasks" / "demo" / "task.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (ws2 / "tasks" / "demo" / "tests.json").write_text(
        json.dumps({"tests": [{"name": "add", "call": "add",
                               "args": [1, 2], "expect": 3}]}), encoding="utf-8")
    before = snap(ws2)
    SC.run(ws2)
    ck("B1 ⛔ 工作区一个字节都没多（数据全落临时目录）",
       snap(ws2) == before, f"多了 {[x for x in snap(ws2) if x not in before][:4]}")

    pkg = Path(tempfile.mkdtemp(prefix="v6_sc_pkg_")) / "site-packages"
    (pkg / "aether").mkdir(parents=True)
    (pkg / "aether" / "paths.py").write_text("# fake\n", encoding="utf-8")
    b2 = snap(pkg)
    SC.run(ws2, pkg_root=pkg)
    ck("B2 ⛔ 被模拟的包目录（site-packages）也没被写",
       snap(pkg) == b2, f"多了 {[x for x in snap(pkg) if x not in b2][:4]}")

    # ── C 组：反证（本文件的正题）────────────────────────────────
    print("\nC 组：逐个注入破坏 —— 对应那一项必须红")

    #: C1 ③ 仓库态那一支被删 ⇒ ④/⑤ 接管 ⇒ 仓库内的解析会漂
    with _Patch(P, "looks_like_repo", lambda r: False):
        r = SC.run(ws)
        ck("C1 删掉「包位置是仓库」判据 ⇒ root.priority 红",
           _status(r, "root.priority") == SC.BROKEN, str(_status(r, "root.priority")))

    #: C2 反过来：把任何目录都判成仓库 ⇒ 安装态就会写进 site-packages
    with _Patch(P, "looks_like_repo", lambda r: True):
        r = SC.run(ws)
        ck("C2 ⛔ 让它把所有目录都当仓库 ⇒ root.not_pkg 红",
           _status(r, "root.not_pkg") == SC.BROKEN,
           str(_status(r, "root.not_pkg")))

    #: C3 链校验恒真 ⇒ 篡改就抓不到了
    with _Patch(LG.Ledger, "verify_chain",
                lambda self, *a, **k: (True, None, "")):
        r = SC.run(ws)
        ck("C3 ⛔ 链校验恒返回 ok ⇒ ledger.chain 红（闸没牙）",
           _status(r, "ledger.chain") == SC.BROKEN, str(_status(r, "ledger.chain")))

    #: C4 账本读不回来（写入 ≠ 可用）
    with _Patch(LG.Ledger, "events", lambda self: []):
        r = SC.run(ws)
        ck("C4 ⛔ 写完读不回来 ⇒ ledger.roundtrip 红",
           _status(r, "ledger.roundtrip") == SC.BROKEN,
           str(_status(r, "ledger.roundtrip")))

    #: C5 人审闸的启动词白名单被放宽到"什么都行"
    with _Patch(CC, "ALLOWED_LAUNCHERS", frozenset(
            {"brew", "totally-unknown-pm", "pip", "curl"})):
        r = SC.run(ws)
        ck("C5 ⛔ 放宽人审白名单 ⇒ gate.teeth 红",
           _status(r, "gate.teeth") == SC.BROKEN, str(_status(r, "gate.teeth")))

    #: C6 ⛔ 机器执行白名单里混进 curl ⇒ 必须被抓住
    #:    （这是第 136 条那条纪律的可执行形态：两份名单管的不是一件事）
    with _Patch(IA, "AUTO_LAUNCHERS", (("brew", "install"), ("curl", "-sS"))):
        r = SC.run(ws)
        ck("C6 ⛔⛔ 机器白名单里混进 curl ⇒ gate.teeth 红"
           "（否则机器会去执行任意代码）",
           _status(r, "gate.teeth") == SC.BROKEN, str(_status(r, "gate.teeth")))

    #: C7 「不许回退」被拆掉 ⇒ 人的批准会被机器撤销
    class _NoGuard:
        def move(self, cur, nxt):
            return str(nxt)

        def of(self, s):
            return 0

        def is_settled(self, s):
            return False

    from aether import cleanup as CL

    with _Patch(CC, "STATE_RANK", _NoGuard()):
        old_cl = CL.STATE_RANK
        try:
            CL.STATE_RANK = _NoGuard()
            r = SC.run(ws)
            ck("C7 ⛔⛔ 拆掉「不许回退」 ⇒ state.no_downgrade 红",
               _status(r, "state.no_downgrade") == SC.BROKEN,
               str(_status(r, "state.no_downgrade")))
        finally:
            CL.STATE_RANK = old_cl

    #: C8 还原不真的还原 ⇒ 「可撤销」就是句口号
    with _Patch(BK, "restore", lambda *a, **k: {"restored": [], "skipped": []}):
        r = SC.run(ws)
        ck("C8 ⛔ 还原不落地 ⇒ backup.roundtrip 红",
           _status(r, "backup.roundtrip") == SC.BROKEN,
           str(_status(r, "backup.roundtrip")))

    #: C9 任务集坏掉 ⇒ seed.tasks 红（这一项不能靠"有文件"就绿）
    bad_ws = Path(tempfile.mkdtemp(prefix="v6_sc_badtask_"))
    (bad_ws / "tasks" / "demo").mkdir(parents=True)
    (bad_ws / "tasks" / "demo" / "task.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8")
    (bad_ws / "tasks" / "demo" / "tests.json").write_text(
        json.dumps({"tests": [{"name": "add", "call": "add",
                               "args": [1, 2], "expect": 3}]}), encoding="utf-8")
    r = SC.run(bad_ws)
    ck("C9 ⛔ 任务集判据不过 ⇒ seed.tasks 红（不是『文件在就算好』）",
       _status(r, "seed.tasks") == SC.BROKEN, str(_status(r, "seed.tasks")))

    #: C10 空任务集 ⇒ **skip**，不许当 ok（没有 ≠ 是好的）
    empty_ws = Path(tempfile.mkdtemp(prefix="v6_sc_empty_"))
    r = SC.run(empty_ws)
    ck("C10 没有任务集 ⇒ seed.tasks 是 skip（skip ≠ 通过）",
       _status(r, "seed.tasks") == SC.SKIP, str(_status(r, "seed.tasks")))

    #: C11 ⛔⛔ 装出来的包里**漏了控制台页面** ⇒ console.page 必须红。
    #:     这正是 r61 的真实事故：`console/` 是仓库资产、不进 wheel，
    #:     而 `aether ui` 起来就死在「找不到控制台页面」（API 却是好的）。
    #:     ⛔ 用假 pkg_root 而不是删真文件：契约不许改被测仓库（第 51 条）。
    bare_pkg = Path(tempfile.mkdtemp(prefix="v6_sc_noconsole_")) / "sp"
    (bare_pkg / "aether" / "seeds" / "tasks").mkdir(parents=True)
    r = SC.run(ws, pkg_root=bare_pkg)
    ck("C11 ⛔⛔ 包里漏了控制台页面 ⇒ console.page 红"
       "（否则装完 `aether ui` 起不来而自检全绿）",
       _status(r, "console.page") == SC.BROKEN,
       str(_status(r, "console.page")))

    # ── D 组：预算 ────────────────────────────────────────────────
    print("\nD 组：预算是一道判据")
    ck("D1 全跑远低于 60s（实测读数，不是估计）",
       secs < SC.BUDGET_S / 3, f"{secs:.2f}s / {SC.BUDGET_S:.0f}s")
    r_over = SC.run(ws, timeout_s=0.0)
    ck("D2 ⛔ 超预算 ⇒ 判红（超时的自检没人跑，等于不存在）",
       not r_over["ok"] and r_over["exit_code"] != 0
       and r_over["over_budget"], str(r_over["counts"]))
    ck("D3 超预算时**各项读数照旧**（不是把结果一起抹掉）",
       r_over["counts"][SC.OK] == rep["counts"][SC.OK], str(r_over["counts"]))

    # ── E 组：与 `check` 的分工 ──────────────────────────────────
    print("\nE 组：装出来的包里没有 tests/ —— 那正是 selfcheck 的用武之地")
    from aether import check as CK
    no_tests = Path(tempfile.mkdtemp(prefix="v6_sc_notests_"))
    rep_ck = CK.run_checks(no_tests, cross_lang=False)
    ck("E1 没有 tests/ ⇒ `check` 判失败（fail-closed 是对的）",
       rep_ck["empty"] and rep_ck["exit_code"] != 0, str(rep_ck["counts"]))
    ck("E2 但 `selfcheck` 照跑（它不依赖 tests/）",
       SC.run(no_tests)["counts"][SC.BROKEN] == 0,
       str(SC.run(no_tests)["counts"]))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：{len(SC.CHECKS)} 项自检，每一项都被反证过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

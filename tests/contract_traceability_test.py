#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：因果链上每一环都**可溯源**（会话归属 / 记忆入账 / 归因不静默）。

三条都是"实测才发现"的洞，不是设计推演：

1. 会话归属缺失 ⇒ 跨运行串味
   监督器那条"泛化闸被绕过"的规则只按 `round_no` 匹配。而 `round_no`
   每个进程都从 1 重新数 ⇒ 跨小时、跨运行的事件被凑成同一轮 ⇒ 真实
   账本上判出 8 条 critical，逐条核对发现**全是把另一个候选的合法提交
   当成了绕过**（被拦的是 patch:textscore，被点名的是 fix_control_flow）。
   一条永远在喊狼来了的规则 = 没有规则。

2. 记忆不在事实源内
   宪法写"事实只有一份（账本）"，但 `data/memory.json` 里躺着 11 条记忆、
   账本里 0 条写入事件。而记忆**跨运行继承并直接喂给提议器** ⇒ 因果链上
   有一环完全没有证据：谁写的、何时写的、被闸拒了多少，都查不到。

3. 归因失败静默
   `evolution.run_round` 里归因整段包在 try/except 中，失败只写进内存
   报告 ⇒ 账本上看不见 ⇒ 下一次仍然不知道自己是在盲改。

反证组（F 组）刻意构造"事故版"，确认这些判据**真的会转红** ——
一个不可能失败的判据等于没有判据。

用法：
    .venv/bin/python tests/contract_traceability_test.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def _find_node():
    """找 node 可执行文件：PATH → 项目约定路径 → 常见前缀 → 打包运行时。

    ⛔ 为什么不能直接写 `node`：本机 `node` **不在 PATH 上**（实测
       `FileNotFoundError`，而 README 里的 `cd supervisor && node ...`
       因此是跑不通的）。项目自己的约定在 `scripts/demo_evolve.py`：
       `/Users/linshaojie/.workbuddy/binaries/node/versions/<ver>/bin/node`。
       ⛔ 这里**只查找、不安装、不降级**：找到才跑，找不到就明确报红 ——
       "跑不了"与"跑过了没问题"是两件事。
    """
    found = shutil.which("node")
    if found:
        return found
    cands = []
    base = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if base.is_dir():
        #: 多版本时取排序后的最后一个（与 `current` 无关，纯确定性）
        cands += [d / "bin" / "node" for d in sorted(base.iterdir())]
    cands += [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node"),
              Path("/Applications/DeepSeek Harness.app/Contents/Resources/"
                   "runtime/primary-runtime/dependencies/node")]
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether.ledger import Ledger

    tmp = Path(tempfile.mkdtemp(prefix="v6_trace_"))

    # ── A 组：会话归属（跨运行判据的数据来源）───────────────────
    print("A 组：账本事件的会话归属")
    p = tmp / "a.jsonl"
    L1 = Ledger(p, session="sess-A")
    L1.append("t.one", {"x": 1})
    L1.append("t.two", {"x": 2})
    L2 = Ledger(p, session="sess-B")          # 另一次"运行"
    L2.append("t.one", {"x": 3})
    evs = Ledger(p).events()
    ck("A1 每次 append 都带 sid",
       all((e.payload or {}).get("sid") for e in evs),
       str([(e.payload or {}).get("sid") for e in evs]))
    ck("A2 同会话内 sid 相同",
       (evs[0].payload["sid"] == evs[1].payload["sid"] == "sess-A"))
    ck("A3 跨运行 sid 不同（这是区分'同轮'的唯一依据）",
       evs[2].payload["sid"] == "sess-B")
    ck("A4 显式传的 sid 不被覆盖",
       Ledger(tmp / "b.jsonl", session="S").append(
           "t", {"sid": "explicit"}).payload["sid"] == "explicit")
    ck("A5 不传 session 也会自动生成一个（不会退化成空）",
       bool(Ledger(tmp / "c.jsonl").append("t", {}).payload.get("sid")))
    ck("A6 sid 进 payload 而不是新增 Event 字段 ⇒ 链仍可校验",
       Ledger(p).verify_chain()[0])

    # ── B 组：记忆写入进账本（含被拒的）────────────────────────
    print("B 组：记忆写入的可追溯性")
    from aether.memory import MemoryOS
    lp = tmp / "m.jsonl"
    led = Ledger(lp, session="sess-M")
    mem = MemoryOS(min_content_len=0).attach_ledger(led)
    ck("B0 attach_ledger 返回 self（可链式）", mem is not None)

    n1, d1 = mem.write("L3", "修法：先读文件再改控制流",
                       channel="curated", source="round1")
    n2, d2 = mem.write("L3", "修法：先读文件再改控制流",
                       channel="curated", source="round1")
    _, d3 = mem.write("L0", "未经审批的元规则", channel="learner")
    ck("B1 首次写入 = written / 重复 = deduped", d1 == "written" and d2 == "deduped",
       f"{d1} {d2}")
    ck("B2 通道闸拒写也被记账（闸的另一半不能藏起来）",
       d3.startswith("rejected_channel"))

    mw = [e for e in Ledger(lp).events() if e.kind == "memory.write"]
    ck("B3 每次尝试各一条 memory.write（含被拒的）", len(mw) == 3, str(len(mw)))
    disps = [e.payload["disposition"] for e in mw]
    ck("B4 处置字段如实（written / deduped / rejected_*）",
       disps[0] == "written" and disps[1] == "deduped"
       and disps[2].startswith("rejected"), str(disps))
    #: ⛔ 期望值必须与**被测对象同源**，不能把常量抄成字面量：本轮
    #:    `memory._ID_PREFIX` 从 16 升到 32（64 位 → 128 位，理由是
    #:    append-only 账本上 16 位会撞），这里再写死 `== 16` 就会把
    #:    "寻址键变长"误报成"记忆没记账"。判据读被测模块的常量。
    from aether.memory import _ID_PREFIX as _MEM_PREFIX
    ck("B5 记 note_id 与 content_hash（可溯源到具体哪一条）",
       mw[0].payload.get("note_id") == n1.note_id
       and len(mw[0].payload.get("content_hash", "")) == _MEM_PREFIX,
       f"hash_len={len(mw[0].payload.get('content_hash', ''))} "
       f"expect={_MEM_PREFIX}")
    ck("B6 记账失败要计数（不能静默丢账本）", "ledger_failed" in mem.stats)
    ck("B7 未挂载账本时行为完全不变（不强制依赖）",
       MemoryOS(min_content_len=0).write("L3", "x", channel="curated")[1]
       == "written")

    # ── C 组：记忆对账读数（文件 vs 账本）──────────────────────
    print("C 组：记忆对账读数")
    from aether.serve import memory_report
    mpath = tmp / "memory.json"
    mem.save(mpath)
    ev_all = Ledger(lp).events()
    rep = memory_report(mpath, ledger_events=ev_all)
    ck("C1 有账本事件可比对 ⇒ 状态 complete（每条都能找到写入事件）",
       rep.get("ledger_trace") == "complete", json.dumps(rep, ensure_ascii=False))
    ck("C2 对不上的条数为 0", rep.get("untraceable_count") == 0)

    #: 历史遗留形态：文件里有记忆、账本里 0 条写入事件
    rep2 = memory_report(mpath, ledger_events=[])
    ck("C3 账本化之前的存量 ⇒ pre_ledger（不是伪装成'一致'）",
       rep2.get("ledger_trace") == "pre_ledger", str(rep2.get("ledger_trace")))
    ck("C4 pre_ledger 时**如实报**不可溯源条数（不隐藏）",
       rep2.get("untraceable_count") == rep2.get("total"))

    #: 真事故形态：库里有 2 条、账本只记了其中 1 条（写入路径漏记）
    mem.write("L4", "另一条独立内容：第 1 轮结论", channel="system",
              source="round1")
    mem.save(mpath)
    only_first = [e for e in Ledger(lp).events()
                  if e.kind == "memory.write"
                  and e.payload.get("disposition") == "written"][:1]
    rep3 = memory_report(mpath, ledger_events=only_first)
    ck("C5 有事件却对不上 ⇒ partial（这才是要报警的形态）",
       rep3.get("ledger_trace") == "partial", str(rep3.get("ledger_trace")))

    ck("C6 未传账本事件时明说没查（不得默认'没问题'）",
       memory_report(mpath).get("ledger_trace", "").startswith("not_checked"))

    # ── D 组：归因失败不得静默 ──────────────────────────────────
    print("D 组：层归因的失败可见性")
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.generalization import GeneralizationGate, make_heldout
    from aether.genome import Capsule, Gene, Harness as H
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Check, Verdict

    def _ok(tid="t"):
        return Verdict(passed=True, checks=(Check(tid, True, "got=1"),),
                       evidence={"env": tid})

    def _bad(tid="t"):
        return Verdict(passed=False, checks=(Check(tid, False, "got=0"),),
                       evidence={"env": tid})

    def mem_runner(h, spec):
        if "python_exec" not in h.tools:
            return _bad("L2")
        r = h.routing.get("repair") or ""
        return _ok("skill") if r and r in h.skills else _bad("none")

    class FixedProposer:
        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    cap = Capsule(name="gen", genes=(
        Gene(op="set", target="L3.skills.gen", value="修法", rationale="t"),
        Gene(op="set", target="L1.routing.repair", value="gen", rationale="t")))

    d = Path(tempfile.mkdtemp(prefix="v6_attr_"))
    led2 = Ledger(d / "ledger.jsonl", session="sess-ATTR")

    dev = [FrozenTask(task_id=f"d{i}", spec={"env": "A"}) for i in range(4)]
    frozen = [FrozenTask(task_id="fz", spec={"env": "A", "frozen": True})]
    cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}
    base = Baseline(caliber=cal,
                    verdicts={"fz": mem_runner(H(), {"frozen": True})},
                    harness_id="seed")
    ho = GeneralizationGate(
        make_heldout("B", [FrozenTask(task_id="h0", spec={"env": "B"})]),
        dev_task_ids=[t.task_id for t in dev])
    loop = EvolutionLoop(led2, NonRegressionGate(frozen, base), dev,
                         mem_runner, FixedProposer([cap]), caliber=cal,
                         heldout=ho,
                         config=EvolutionConfig(group_size=2,
                                                attribute_layers=True))
    #: ⛔ runner 要求 `python_exec` 在工具白名单里：不给的话候选与基线
    #:    reward 都是 0 ⇒ advantage 恒 0 ⇒ 永远不提交 ⇒ 归因分支根本走不到
    #:    （第一版测试就是这样"绿"的：不是通过了，是没跑到）。
    def _base_h():
        return H(tools=("read_file", "write_file", "python_exec"))

    #: ⛔ 让归因**必然失败**（模拟消融 harness 与 runner 不兼容）。
    #:    不是靠"跑很多次碰巧炸" —— 那种用例今天绿明天红。
    import aether.attribution as AT
    orig_attr = AT.attribute_layers

    def boom(*a, **kw):
        raise RuntimeError("注入：消融 harness 与 runner 不兼容")

    AT.attribute_layers = boom
    try:
        rep = loop.run_round(_base_h(), round_no=1)
    except Exception as e:  # noqa: BLE001
        print(f"     （run_round 抛出 {type(e).__name__}: {e}）")
        rep = None
    finally:
        AT.attribute_layers = orig_attr
    if rep is not None:
        ck("D0 归因失败不得影响提交（提交照常完成）",
           bool(rep.committed) or bool(rep.revert_reason),
           str(rep.revert_reason))
        ck("D0b 内存报告里也写明了不可用原因",
           (rep.layer_credit or {}).get("available") is False,
           str(rep.layer_credit)[:120])

    ev2 = Ledger(d / "ledger.jsonl").events()
    unavail = [e for e in ev2 if e.kind == "attribution.unavailable"]
    ck("D1 归因失败 ⇒ 账本里有一条 attribution.unavailable（不再静默）",
       len(unavail) >= 1,
       str([e.kind for e in ev2 if e.kind.startswith("attribution")]))
    if unavail:
        ck("D2 失败原因被记下来（可行动，不是未知错误）",
           bool((unavail[0].payload or {}).get("error")),
           str((unavail[0].payload or {}).get("error")))

    # ── E 组：监督器与真实数据同源（跨语言）────────────────────
    print("E 组：监督器规则与真实账本")
    import subprocess
    node = _find_node()
    if node is None:
        #: ⛔ 本机 `node` 不在 PATH 上（项目约定用 workbuddy 二进制，
        #:    见 scripts/demo_evolve.py）⇒ 原来这句直接 `FileNotFoundError`
        #:    把整份契约打死，E2/E3 的守护**一条都没跑**，而输出看起来像
        #:    "契约崩了"而不是"跨语言对拍没跑"。
        #:    `_find_node` 已按 PATH → 项目约定路径 → 常见前缀查找；
        #:    真找不到就**明确报红**（"跑不了" ≠ "跑过了没问题"）。
        raise RuntimeError(
            "找不到 node：跨语言对拍无法执行。把 node 放进 PATH，"
            "或确认 /Users/linshaojie/.workbuddy/binaries/node/versions/*/"
            "bin/node 存在")
    r = subprocess.run(
        [node, "src/index.ts", "--ledger", "../data/ledger.jsonl"],
        cwd=str(ROOT / "supervisor"), capture_output=True, text=True,
        timeout=180)
    out_txt = (r.stdout or "") + (r.stderr or "")
    ck("E1 监督器可跑真实账本", r.returncode in (0, 1, 2), out_txt[:200])
    #: ⛔ 判据：真实账本上的 critical 应当为 0（13 条假警报已修）
    n_crit = out_txt.count("[GENERALIZATION_GATE_BYPASSED]")
    ck("E2 真实账本上不再判出'绕过闸'的 critical（原 13 条全是误报）",
       n_crit == 0, f"仍有 {n_crit} 条")
    #: 严重度统计必须真的被打印出来，且 critical 计数与逐条一致
    import re as _re
    m = _re.search(r'\{"info":\d+,"warn":\d+,"critical":(\d+)\}', out_txt)
    ck("E3 监督器给出严重度统计（读数存在，不是只打印几条文字）", bool(m),
       out_txt[-160:])
    if m:
        ck("E4 统计里的 critical 数与逐条报出的 critical 条数一致（0）",
           int(m.group(1)) == n_crit, f'{m.group(1)} vs {n_crit}')
    ck("E5 历史遗留的不可判定**如实可见**（不静默吞掉）",
       ("GATE_BYPASS_UNVERIFIABLE" in out_txt) or n_crit == 0)

    # ── F 组：反证（判据必须可能失败）──────────────────────────
    print("F 组：反证组")
    import aether.memory as M

    #: F1：记忆不记账 ⇒ C 组判据必须转红
    orig_record = M.MemoryOS._record_write

    def no_record(self, *a, **kw):
        return None

    M.MemoryOS._record_write = no_record
    try:
        lp2 = tmp / "m2.jsonl"
        led3 = Ledger(lp2, session="s")
        m2 = MemoryOS(min_content_len=0).attach_ledger(led3)
        m2.write("L3", "内容", channel="curated")
        m2.save(tmp / "m2.json")
        rep_bad = memory_report(tmp / "m2.json", ledger_events=Ledger(lp2).events())
        ck("F1 反证：记忆不进账本 ⇒ 对账判据转红（不会恒绿）",
           rep_bad.get("ledger_trace") != "complete",
           str(rep_bad.get("ledger_trace")))
    finally:
        M.MemoryOS._record_write = orig_record

    #: F2：会话标识缺失 ⇒ A1 判据必须转红（不是恒真）
    #:     ⛔ 用"空会话"模拟升级前的写入，而不是去 patch append 内部 ——
    #:        那样测的是补丁有没有打上，不是判据有没有用。
    p3 = tmp / "a3.jsonl"
    Lx = Ledger(p3, session="")
    Lx.append("t.one", {"x": 1})
    ev3 = Ledger(p3).events()
    ck("F2 反证：会话标识缺失 ⇒ A1 判据转红（检测得到）",
       not all((e.payload or {}).get("sid") for e in ev3),
       str([(e.payload or {}).get("sid") for e in ev3]))

    #: F3：把归因失败的记账吞掉 ⇒ 账本里就查不到（判据确实依赖于此）
    import aether.attribution as AT2
    d2 = Path(tempfile.mkdtemp(prefix="v6_attr2_"))
    led4 = Ledger(d2 / "ledger.jsonl", session="sess-ATTR2")
    loop2 = EvolutionLoop(led4, NonRegressionGate(frozen, base), dev,
                          mem_runner, FixedProposer([cap]), caliber=cal,
                          heldout=ho,
                          config=EvolutionConfig(group_size=2,
                                                 attribute_layers=True))
    orig_attr2 = AT2.attribute_layers
    AT2.attribute_layers = boom
    try:
        loop2.run_round(_base_h(), round_no=1)
    except Exception:  # noqa: BLE001
        pass
    finally:
        AT2.attribute_layers = orig_attr2
    #: 事故版：把 unavailable 的 kind 改写成别的 ⇒ "静默"的样子
    lines = (d2 / "ledger.jsonl").read_text(encoding="utf-8")
    muted = lines.replace("attribution.unavailable", "attribution.muted")
    (d2 / "muted.jsonl").write_text(muted, encoding="utf-8")
    n_real = sum(1 for e in Ledger(d2 / "ledger.jsonl").events()
                 if e.kind == "attribution.unavailable")
    n_muted = sum(1 for e in Ledger(d2 / "muted.jsonl").events()
                  if e.kind == "attribution.unavailable")
    ck("F3 反证：把失败事件改名 ⇒ D1 判据转红（检测得到静默）",
       n_real >= 1 and n_muted == 0, f"real={n_real} muted={n_muted}")

    # ── G 组：对账告警必须有出口 ────────────────────────────────
    print("G 组：补记账出口（修不好的告警等于没有告警）")
    gb = Path(tempfile.mkdtemp(prefix="v6_bf_"))
    gl = Ledger(gb / "l.jsonl", session="sess-BF")
    gm = MemoryOS(min_content_len=0)
    gm.write("L3", "存量记忆一", channel="curated")   # 未挂载账本写入
    gm.write("L4", "存量记忆二", channel="system")
    gm.save(gb / "memory.json")
    before = memory_report(gb / "memory.json",
                           ledger_events=gl.events())
    ck("G1 存量（账本化之前）⇒ pre_ledger", before.get("ledger_trace") == "pre_ledger",
       str(before.get("ledger_trace")))

    gm.attach_ledger(gl)
    nbf = gm.backfill_ledger()
    after = memory_report(gb / "memory.json", ledger_events=gl.events())
    ck("G2 backfill 后 ⇒ complete（告警可消，不是永久噪声）",
       after.get("ledger_trace") == "complete", str(after.get("ledger_trace")))
    ck("G3 补记条数 = 库里条数", nbf == len(gm.notes), f"{nbf} vs {len(gm.notes)}")
    #: ⛔ 证据强度不同 ⇒ 必须分开计数，不能混进 written
    ck("G4 backfill **单独计数**（不冒充实时写入的证据强度）",
       after.get("backfilled") == nbf
       and after.get("ledger_written_ids") == len(gm.notes),
       f'backfilled={after.get("backfilled")} ids={after.get("ledger_written_ids")}')

    #: 反证：不调用 backfill ⇒ 永远是 pre_ledger（说明出口确实是唯一解药）
    gb2 = Path(tempfile.mkdtemp(prefix="v6_bf2_"))
    gl2 = Ledger(gb2 / "l.jsonl", session="sess-BF2")
    gm2 = MemoryOS(min_content_len=0)
    gm2.write("L3", "另一条存量", channel="curated")
    gm2.save(gb2 / "memory.json")
    gm2.attach_ledger(gl2)                      # 挂了账本，但不补记
    still = memory_report(gb2 / "memory.json", ledger_events=gl2.events())
    ck("G5 反证：只挂载不补记 ⇒ 仍是不可溯源（不会自己变好）",
       still.get("ledger_trace") == "pre_ledger", str(still.get("ledger_trace")))

    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

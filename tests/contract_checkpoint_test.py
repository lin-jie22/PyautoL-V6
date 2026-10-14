#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：断点续跑（checkpoint / resume）—— 中断的代价不能是"账本自己跟自己对不上"。

没有这个功能时的真实后果（两条，都不是"浪费时间"这么轻）：

  · 账本 append-only 且没有幂等键 ⇒ `--rounds 20` 在第 17 轮崩了，重跑
    会把前 16 轮的事件**再写一遍** ⇒ 账本里出现两个"第 3 轮"；
  · `_run_seq` 锚来自账本 seq ⇒ 锚变大 ⇒ 前后两半的 run_id 不同
    ⇒ 投影对账时它们看着像两次独立实验，其实是一次被劈成两半。

所以本契约钉的不是"能不能存文件"，而是三件事：

  1. **续跑必须接着编号**（轮号接续，不重号）；
  2. **环境换了必须拒绝**（换尺子/换配置/账本回退 ⇒ fail-closed，
     且要说清是哪一种对不上 —— 合起来报"不一致"会让人去改 checkpoint）；
  3. **checkpoint 是派生量**：写失败不得中断进化，但必须记账
     （不记账 = "我以为能续跑"的假仪表）。

跑法（离线，秒级）：
    python tests/contract_checkpoint_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether import checkpoint as CP
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.genome import Harness
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.providers import TemplateProposer
    from aether.verifier import Check, Verdict

    from dataclasses import asdict

    tmp = Path(tempfile.mkdtemp(prefix="v6_cp_"))
    CAL = {"seed": 0, "timeout": 10.0}
    #: ⛔ 造数据也要守被测对象的闸：cfg 指纹必须按**真实**算法算出来。
    #:    第一版我图省事写了字面量 "cfgfp" ⇒ C 组的 cfg 校验永远先失配
    #:    ⇒ 后面"账本回退""完全一致"两条根本没被测到（假红）。
    CFG = EvolutionConfig(group_size=2)

    def mk(passed, name="c"):
        return Verdict(passed=passed, checks=(Check(name, passed, "ev"),),
                       evidence={"n": 1})

    def new_loop(led_path, cp_path=None, cal=None, cfg=None):
        """⛔ 同源构造：与 `contract_evolution_test` 用同一套 stub，
        不在本文件里另造一套进化机制（判据必须与被测对象同源）。"""
        led = Ledger(led_path)
        suite = [FrozenTask(task_id="f1", spec={"x": 1})]
        base = Baseline(caliber=CAL, verdicts={"f1": mk(True)},
                        harness_id="seed")
        gate = NonRegressionGate(suite, base)
        dev = [FrozenTask(task_id="d1", spec={"x": 10, "topic": "text"}),
               FrozenTask(task_id="d2", spec={"x": 20, "topic": "text"})]

        def runner(h, spec):
            return mk("restore_from_baseline" in h.skills)

        proposer = TemplateProposer(skills={
            "restore_from_baseline": "恢复到基线实现",
            "useless_skill": "没用的技能"})
        return EvolutionLoop(led, gate, dev, runner, proposer, store=None,
                             caliber=cal if cal is not None else CAL,
                             config=cfg or EvolutionConfig(group_size=2),
                             checkpoint_path=cp_path)

    # ── A 组：指纹与往返 ────────────────────────────────────────
    print("== A 组：指纹与读写往返 ==")
    ck("A1 指纹与键序无关（否则内容相同却拒绝恢复 = 假红）",
       CP.fingerprint({"a": 1, "b": 2}) == CP.fingerprint({"b": 2, "a": 1}))
    ck("A2 内容不同 ⇒ 指纹不同", CP.fingerprint({"a": 1}) != CP.fingerprint({"a": 2}))
    p = str(tmp / "cp.json")
    cp0 = CP.Checkpoint(round_no=2, rounds_total=4,
                        harness={"meta_rules": "元规则"},
                        harness_id="h1", ledger_seq=42, run_seq=7,
                        caliber_fp=CP.fingerprint(CAL),
                        cfg_fp=CP.fingerprint(asdict(CFG)))
    CP.write(p, cp0)
    cp1 = CP.read(p)
    ck("A3 写→读往返：轮次一致", cp1.round_no == 2 and cp1.rounds_total == 4)
    ck("A4 往返：harness payload 一致（存 payload 不存引用）",
       cp1.harness == {"meta_rules": "元规则"})
    ck("A5 rounds_left = 计划 − 已完成", cp1.rounds_left == 2)
    ck("A6 未完成 ⇒ complete=False", not cp1.complete)
    CP.write(p, CP.Checkpoint(round_no=4, rounds_total=4, harness={}))
    ck("A7 跑完 ⇒ complete=True 且 left=0", CP.read(p).complete)
    ck("A8 负数剩余被夹到 0（超额完成不算负轮）",
       CP.Checkpoint(round_no=9, rounds_total=4, harness={}).rounds_left == 0)

    # ── B 组：损坏一律拒绝（fail-closed，不填默认值）─────────────
    print()
    print("== B 组：损坏的 checkpoint 必须拒绝，不许补全 ==")
    for name, raw, why in (
            ("B1 空文件（写被中断）", "", "空"),
            ("B2 截断 JSON", '{"round_no": 1, "rounds_tot', "JSON"),
            ("B3 不是对象", "[1,2,3]", "对象"),
            ("B4 缺 round_no（不填默认 0）", '{"rounds_total": 3, "harness": {}}',
             "缺字段"),
            ("B5 harness 类型不对", '{"round_no":1,"rounds_total":2,'
             '"harness":"h1"}', "harness")):
        f = tmp / "bad.json"
        f.write_text(raw, encoding="utf-8")
        try:
            CP.read(str(f))
            ck(name, False, "没抛 ⇒ 半截文件被当成可用进度")
        except CP.CheckpointError as e:
            ck(name, True)
            ck(f"   └ 原因可读且点名「{why}」", why in str(e), str(e))
    f = tmp / "old.json"
    f.write_text('{"format": 99, "round_no": 1, "rounds_total": 2,'
                 ' "harness": {}}', encoding="utf-8")
    try:
        CP.read(str(f))
        ck("B6 格式版本不符 ⇒ 拒绝（不猜旧字段含义）", False, "没抛")
    except CP.CheckpointError:
        ck("B6 格式版本不符 ⇒ 拒绝（不猜旧字段含义）", True)
    try:
        CP.read(str(tmp / "nope.json"))
        ck("B7 不存在 ⇒ 拒绝并说明怎么产生它", False, "没抛")
    except CP.CheckpointError as e:
        ck("B7 不存在 ⇒ 拒绝并说明怎么产生它", "--resume" in str(e)
           or "evolve" in str(e), str(e))
    ck("B8 原子写不留临时文件（.cp_* 残留会污染下次判断）",
       not list(tmp.glob(".cp_*")), str(list(tmp.glob(".cp_*"))))

    # ── C 组：三类失配必须分开报 ────────────────────────────────
    print()
    print("== C 组：环境不一致 ⇒ 拒绝，且说清是**哪一种** ==")
    CAL2 = {"seed": 999, "timeout": 10.0}
    ok, kind, reason = CP.verify(cp1, ledger_seq=42, caliber=CAL,
                                 cfg=asdict(CFG))
    ck("C1 完全一致 ⇒ 放行", ok and kind == "", f"{kind} {reason}")
    ok, kind, reason = CP.verify(cp1, ledger_seq=42, caliber=CAL2,
                                 cfg=asdict(CFG))
    ck("C2 换了尺子 ⇒ kind=caliber", (not ok) and kind == "caliber", kind)
    ck("C2b 理由给出出路（重新跑，而不是改 checkpoint）",
       "重跑" in reason or "重新" in reason, reason)
    ok, kind, _ = CP.verify(cp1, ledger_seq=42, caliber=CAL,
                            cfg=asdict(EvolutionConfig(group_size=5)))
    ck("C3 配置变了 ⇒ kind=cfg（「还剩几轮」这个量不成立）",
       (not ok) and kind == "cfg", kind)
    ok, kind, reason = CP.verify(cp1, ledger_seq=41, caliber=CAL,
                                 cfg=asdict(CFG))
    ck("C4 账本回退 ⇒ kind=ledger", (not ok) and kind == "ledger", kind)
    ck("C4b 理由指向 --root（找对账本，不是删 checkpoint）",
       "root" in reason, reason)
    ok, _, _ = CP.verify(cp1, ledger_seq=1000, caliber=CAL,
                         cfg=asdict(CFG))
    ck("C5 账本**前进**是正常的 ⇒ 放行（只有回退才是事故）", ok)

    # ── D 组：反证（C 组的断言必须有判别力）───────────────────────
    print()
    print("== D 组：反证 —— 短路校验后，不一致的 checkpoint 必须被接受 ==")
    loop_d = new_loop(tmp / "ld.jsonl", str(tmp / "cp_d.json"))
    h0 = Harness(meta_rules="元规则", memory_policy={"recall_topk": 5})
    loop_d.run(h0, rounds=1)
    ck("D0 前置：checkpoint 已落盘（否则后面测的是空文件）",
       Path(tmp / "cp_d.json").exists())
    # 换了尺子的 loop 去读同一个 checkpoint ⇒ 本应被拒
    loop_other = new_loop(tmp / "ld.jsonl", str(tmp / "cp_d.json"), cal=CAL2)
    try:
        loop_other.load_checkpoint()
        ck("D1 换尺子 ⇒ load_checkpoint 拒绝", False, "没抛 ⇒ 校验是死的")
    except CP.CheckpointError:
        ck("D1 换尺子 ⇒ load_checkpoint 拒绝", True)
    saved = CP.verify
    try:
        CP.verify = lambda *a, **k: (True, "", "")   # 事故版：校验恒放行
        cp_bad, _h = loop_other.load_checkpoint()
        ck("D2 ⛔ 反证：校验短路 ⇒ 不一致的 checkpoint **被接受**"
           "（证明 D1 拦的是校验，不是别的）",
           cp_bad is not None and cp_bad.round_no >= 1)
    except CP.CheckpointError as e:
        ck("D2 ⛔ 反证：校验短路 ⇒ 不一致的 checkpoint **被接受**", False,
           f"仍被拒 ⇒ D1 可能是恒真的：{e}")
    finally:
        CP.verify = saved
    try:
        loop_other.load_checkpoint()
        ck("D3 还原后重新拒绝（事故版没残留）", False, "还原后竟放行")
    except CP.CheckpointError:
        ck("D3 还原后重新拒绝（事故版没残留）", True)

    # ── E 组：端到端续跑（轮号必须接续）──────────────────────────
    print()
    print("== E 组：端到端 —— 跑到第 2 轮中断，续跑必须接着第 3 轮 ==")
    led_path = tmp / "le.jsonl"
    cp_path = str(tmp / "cp_e.json")
    loop_e = new_loop(led_path, cp_path)
    cur, reps1 = loop_e.run(h0, rounds=2)
    ck("E1 前 2 轮跑完，轮号 1,2", [r.round_no for r in reps1] == [1, 2],
       str([r.round_no for r in reps1]))
    cpx = CP.read(cp_path)
    ck("E2 checkpoint 记到第 2 轮", cpx.round_no == 2, str(cpx.round_no))
    ck("E3 计划 = 已完成 ⇒ 无剩余（这条路径不该被当成中断）",
       cpx.rounds_left == 0)
    #: 模拟"计划 4 轮、跑到第 2 轮崩了"：直接改计划数重写 checkpoint。
    #: ⛔ 不伪造文件文本 —— 走与生产相同的 `write_checkpoint`，否则测的
    #:   是"我编的 JSON"，不是"生产写出来的 JSON"。
    loop_e.write_checkpoint(cur, reps1, rounds_total=4)
    cpx2 = CP.read(cp_path)
    ck("E4 模拟中断：剩余 2 轮", cpx2.rounds_left == 2, str(cpx2.rounds_left))

    loop_r = new_loop(led_path, cp_path)
    cpz, h_resumed = loop_r.load_checkpoint()
    ck("E5 续跑拿回的 harness 与中断时一致（payload 往返无损）",
       h_resumed.to_dict() == cur.to_dict())
    ok_e, kind_e, why_e = CP.verify(cpz, ledger_seq=cpx2.ledger_seq,
                                    caliber=CAL, cfg=asdict(CFG))
    ck("E6 校验通过（同尺子 / 同配置 / 账本只前进）", ok_e, f"{kind_e} {why_e}")
    _cur2, reps2 = loop_r.run(h_resumed, rounds=cpz.rounds_left,
                              start_round=cpz.round_no)
    ck("E7 ⛔ 轮号**接续** 3,4（不是从 1 重号 ⇒ 账本不会出现两个第 3 轮）",
       [r.round_no for r in reps2] == [3, 4],
       str([r.round_no for r in reps2]))
    led_e = Ledger(led_path)
    ck("E8 账本链在续跑后仍完整", led_e.verify_chain()[0])
    ev = [e for e in led_e.events() if e.kind == "evolution.checkpoint"]
    ck("E9 落点有读数：账本里能查到进度存到哪、存到第几轮", len(ev) >= 1,
       f"{len(ev)} 条")
    ck("E10 读数带 resumable（跑完 ⇒ False，不再诱导重复续跑）",
       ev and ev[-1].payload.get("resumable") is False,
       str(ev[-1].payload if ev else None))
    ck("E11 续跑不重置 run_id 锚（锚只从账本 seq 取 ⇒ 天然接续）",
       loop_r._run_seq > 0)

    # ── F 组：写失败不得中断进化，但必须记账 ────────────────────
    print()
    print("== F 组：checkpoint 是派生量 —— 写失败不中断，但不许静默 ==")
    led_f = tmp / "lf.jsonl"
    loop_f = new_loop(led_f, str(tmp / "dev_null_dir" / "cp.json"))
    (tmp / "dev_null_dir").write_text("我是文件不是目录", encoding="utf-8")
    try:
        _c, reps_f = loop_f.run(h0, rounds=1)
        ck("F1 写不进去 ⇒ 进化照常跑完（checkpoint 不是事实源）",
           len(reps_f) == 1)
    except Exception as e:      # noqa: BLE001
        ck("F1 写不进去 ⇒ 进化照常跑完（checkpoint 不是事实源）", False,
           f"{type(e).__name__}: {e}")
    evs = [e for e in Ledger(led_f).events()
           if e.kind == "evolution.checkpoint_failed"]
    ck("F2 失败被记账（没有读数 = 假仪表『我开了续跑』）", len(evs) >= 1,
       f"{len(evs)} 条")
    ck("F3 失败原因可读（带路径与异常类型）",
       evs and "path" in evs[-1].payload and "error" in evs[-1].payload,
       str(evs[-1].payload if evs else None))
    ck("F4 没开 checkpoint ⇒ 不写也不报错（默认 None = 旧行为）",
       new_loop(tmp / "lg.jsonl").run(h0, rounds=1)[1][0].round_no == 1)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f_ in FAILS:
            print(f"   ✗ {f_}")
        return 1
    print("✅ 断点续跑契约全部通过：轮号接续、换环境拒绝、写失败有读数")
    return 0


if __name__ == "__main__":
    sys.exit(main())

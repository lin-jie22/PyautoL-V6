#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：把「人审」换成「机器自审」的三条能力（r60 F）。

用户要求的原话是「三条都打能，不要人审，机器自审」。三条对应：

  A 自动改源码   —— 原先 `apply_edits` 在**构造上**拒绝生产根
  B 冻结集变强   —— 原先 delta 的定义域只含"基线通过的题" ⇒ 永远 ≤ 0
  C 跨轮累积     —— 原先每轮从 seed 起 ⇒ 同一件事重学 93 遍

本文件守的**不是"能不能做"**，而是"做的同时有没有丢掉可复核性"：

  · 机器自审必须有**三态**：ok / rejected / **unverified**（没裁决 ≠ 安全）
  · 落盘必须**先快照**、**后复验**、失败**原子回滚**
  · 增益读数**只许进读数，不许进裁决**（否则等于让系统优化尺子）
  · 接续必须**能显式关掉**（第 12 条：默认开的开关必须关得掉）

用法：
    python tests/contract_autonomy_test.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _copy_src(dst: Path) -> None:
    """复制一份可跑的源码树（不含 data/，与 `srcmut.COPY_IGNORE` 同口径）。"""
    for name in ("aether", "tests", "tasks"):
        shutil.copytree(ROOT / name, dst / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def main():
    from aether import autonomy as AU
    from aether import cleanup as CL
    from aether import srcmut as S
    from aether.genome import Harness
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Check, Verdict

    TARGET = "aether/ghprobe.py"

    # ── A 组：机器自审（对拍 → 快照 → 落盘 → 复验 → 回滚）──────────
    print("A 组：机器自审 —— 用冻结契约对拍替代人点头")
    tmp = Path(tempfile.mkdtemp(prefix="v6_auto_"))
    try:
        root = tmp / "repo"
        root.mkdir()
        _copy_src(root)
        orig = (root / TARGET).read_bytes()
        ed = S.from_lines(root, TARGET, 9, 11)      # 删模块文档里的两行说明
        prop = CL.CleanupProposal(
            prop_id="p1", kind="unreachable", target="ghprobe:9-11",
            where=f"{TARGET}:9-11", reason="合成：删文档行（行为必然等价）",
            state=CL.PENDING, gate_passed=True, edits=[ed.to_dict()])

        v = AU.auto_apply(prop, root, apply_to_disk=False, max_contracts=4)
        ck("A1 等价删除 ⇒ 对拍判 ok", v.state == AU.OK, f"{v.state} {v.reasons}")
        ck("A2 ⛔ 演练后源码逐字节未变", (root / TARGET).read_bytes() == orig)
        ck("A3 演练结论带 dry_run 且没有备份（不许当成『已经删了』）",
           bool(v.evidence.get("dry_run")) and not v.backups, str(v.evidence.keys()))

        v2 = AU.auto_apply(prop, root, max_contracts=4)
        ck("A4 落盘成功 ⇒ ok 且有快照",
           v2.state == AU.OK and bool(v2.backups) and not v2.rolled_back,
           f"{v2.state} {v2.backups}")
        ck("A5 删掉的行数 = 编辑区间行数",
           v2.evidence.get("applied_lines") == 3,
           str(v2.evidence.get("applied_lines")))
        ck("A6 ⛔ 落盘后**复验跑过**且通过（不是『写完就算』）",
           (v2.evidence.get("verify_after") or {}).get("ok") is True,
           str(v2.evidence.get("verify_after"))[:120])
        ck("A7 文件确实变短了",
           (root / TARGET).stat().st_size < len(orig),
           f"{(root / TARGET).stat().st_size} vs {len(orig)}")

        ok_rb, why_rb = S.rollback(root, v2.backups, v2.snapshot_dir)
        ck("A8 ⛔ 回滚能逐字节还原", ok_rb and (root / TARGET).read_bytes() == orig,
           why_rb)

        #: ⛔⛔ 反证：删**有行为**的代码 ⇒ 对拍必须发现差异。
        #:    没有这一条，A1 可能是"对拍恒绿"（第 9 条：闸恒绿先做反证）。
        ed2 = S.from_lines(root, TARGET, 100, 100)   # `_SLUG_RE` 定义行
        prop2 = CL.CleanupProposal(
            prop_id="p2", kind="unreachable", target="ghprobe:100",
            where=f"{TARGET}:100-100", reason="合成：删掉 slug 白名单定义",
            state=CL.PENDING, gate_passed=True, edits=[ed2.to_dict()])
        v3 = AU.auto_apply(prop2, root, apply_to_disk=False, max_contracts=4)
        ck("A9 ⛔⛔ 反证：删有行为的代码 ⇒ 对拍判 rejected（闸有牙）",
           v3.state == AU.REJECTED, f"{v3.state} {v3.reasons}")
        ck("A10 反证那次源码也没被改", (root / TARGET).read_bytes() == orig)

        # ── 三态的另一半：unverified 绝不并入 ok ──
        prop3 = CL.CleanupProposal(prop_id="p3", kind="unreachable",
                                   target="t", where="w", reason="r")
        ck("A11 没有结构化 edits ⇒ unverified（不是 ok）",
           AU.auto_apply(prop3, root, apply_to_disk=False).state == AU.UNVERIFIED)
        drift = S.Edit(file=TARGET, start_line=ed.start_line,
                       end_line=ed.end_line, sha256="0" * 64)
        prop4 = CL.CleanupProposal(prop_id="p4", kind="unreachable", target="t",
                                   where=f"{TARGET}:9-11", reason="r",
                                   edits=[drift.to_dict()])
        ck("A12 指纹漂移（源码已变）⇒ unverified，不当成『删坏了』",
           AU.auto_apply(prop4, root, apply_to_disk=False).state == AU.UNVERIFIED)

        # ── 生产根的权限边界 ──
        ok_x, why_x, n_x = S.apply_edits(ROOT, [S.from_lines(ROOT, TARGET, 9, 11)])
        ck("A13 普通入口仍拒写生产根", (not ok_x) and n_x == 0, why_x)
        ok_y, why_y, n_y, _by = S.apply_edits_privileged(
            ROOT, [S.from_lines(ROOT, TARGET, 9, 11)], snapshot_dir="")
        ck("A14 ⛔ 特权入口缺快照 ⇒ 拒绝（无快照的删除不可逆）",
           (not ok_y) and n_y == 0, why_y)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── B 组：冻结集增益读数（只读，不进闸）─────────────────────────
    print("\nB 组：冻结集的『增益』必须有读数，且绝不许进裁决")

    CAL = {"seed": 0, "timeout": 10.0}

    def mk(p):
        return Verdict(passed=p, checks=(Check("c", p, "x"),), evidence={"n": 1})

    suite = [FrozenTask(task_id="t1", spec={"x": 1}),
             FrozenTask(task_id="t2", spec={"x": 2}),
             FrozenTask(task_id="t3", spec={"x": 3})]
    base = Baseline(caliber=CAL,
                    verdicts={"t1": mk(True), "t2": mk(True), "t3": mk(False)},
                    harness_id="seed")
    gate = NonRegressionGate(suite, base)

    #: 全绿 ⇒ t3 被修好 ⇒ 这就是"冻结集上真的变强了"
    r = gate.evaluate(Harness(), lambda h, s: mk(True), CAL)
    ck("B1 基线挂→候选过 ⇒ frozen_gain=1", r.frozen_gain == 1, str(r.frozen_gain))
    ck("B2 分母单列（frozen_open=1，与 gain 分得开）",
       r.frozen_open == 1 and r.frozen_gain_detail == ["t3: 0→1"],
       f"{r.frozen_open} {r.frozen_gain_detail}")
    #: ⛔ 同一个 `deltas` 里仍然是 0（定义域只含基线通过的题）——
    #:    这正是"delta 永远不可能为正"的现场证据。
    ck("B3 ⛔ deltas 定义域仍是『基线通过的题』⇒ 这里只可能是 0",
       set(r.deltas) == {"t1", "t2"} and all(v == 0 for v in r.deltas.values()),
       str(r.deltas))

    #: 与基线相同 ⇒ 没有增益
    r2 = gate.evaluate(Harness(), lambda h, s: mk(s["x"] != 3), CAL)
    ck("B4 与基线一致 ⇒ frozen_gain=0（不是恒 1）",
       r2.frozen_gain == 0 and r2.frozen_open == 1, str(r2.frozen_gain))

    #: ⛔⛔ 关键：回归换增益 —— 增益照记，但裁决仍是红。
    r3 = gate.evaluate(Harness(), lambda h, s: mk(s["x"] == 3), CAL)
    ck("B5 ⛔⛔ 回归换增益 ⇒ passed=False（增益不许讨好回归）", not r3.passed,
       str(r3.regressions))
    ck("B6 但 gain 照样被记下（读数与裁决分开）", r3.frozen_gain == 1,
       str(r3.frozen_gain))
    d = r.to_dict()
    ck("B7 三个读数都进 to_dict（第 35d 条）",
       all(k in d for k in ("frozen_gain", "frozen_gain_detail", "frozen_open")),
       str(sorted(d.keys())))

    # ── C 组：默认接续（跨轮累积）─────────────────────────────────
    print("\nC 组：同一条命令跑两次 ⇒ 轮号必须连续（跨轮累积）")
    tmp2 = Path(tempfile.mkdtemp(prefix="v6_cont_"))
    try:
        shutil.copytree(ROOT / "tasks", tmp2 / "tasks",
                        ignore=shutil.ignore_patterns("__pycache__"))
        cp = tmp2 / "data" / "checkpoint.json"

        def run(*extra):
            cmd = [sys.executable, "-B", "-m", "aether.cli", "--root", str(tmp2),
                   "evolve", "--rounds", "1", "--group", "1", "--max-tasks", "1",
                   "--kinds", "constant", "--parallel", "0", *extra]
            return subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                                  text=True, timeout=600)

        r1 = run()
        ck("C1 首次运行成功", r1.returncode == 0, (r1.stdout + r1.stderr)[-200:])
        cp1 = json.loads(cp.read_text("utf-8"))
        ck("C2 首次 round_no=1 / rounds_total=1",
           cp1["round_no"] == 1 and cp1["rounds_total"] == 1,
           f'{cp1["round_no"]}/{cp1["rounds_total"]}')

        r2 = run()
        cp2 = json.loads(cp.read_text("utf-8"))
        ck("C3 ⛔⛔ 同一命令第二次 ⇒ **自动接续**，round_no=2",
           cp2["round_no"] == 2, f'{cp2["round_no"]}/{cp2["rounds_total"]}')
        ck("C4 接续是**说出来**的（不静默）",
           "接续" in (r2.stdout + r2.stderr), (r2.stdout + r2.stderr)[-200:])

        #: ⛔ 第 12 条：默认开的开关必须**显式关得掉**。
        r3 = run("--no-continue")
        cp3 = json.loads(cp.read_text("utf-8"))
        ck("C5 --no-continue ⇒ 回到 round_no=1（旧行为仍在）",
           cp3["round_no"] == 1, f'{cp3["round_no"]}/{cp3["rounds_total"]}')

        #: 账本要能分辨三条路（fresh / auto / resume），否则"为什么从 1 起"
        #: 事后无从归因。
        from aether.ledger import Ledger
        from aether.paths import paths as _paths
        evs = [e for e in Ledger(_paths(tmp2)["ledger"]).events()
               if e.kind == "evolution.run_config"]
        got = [e.payload.get("continuity") for e in evs]
        ck("C6 run_config 记了 continuity（fresh/auto/fresh）",
           got == ["fresh", "auto", "fresh"], str(got))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：机器能自己点头，但点头的判据仍在它之外")
    return 0


if __name__ == "__main__":
    sys.exit(main())

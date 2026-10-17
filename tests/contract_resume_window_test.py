#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**进化必须能跨进程累积**（resume 不许有语义死锁）。

为什么要有它（2026-10-17）：生产账本 4989 条事件、87 次 `harness.commit`，
按血缘重建后分布是 ——

    距种子 1 代：73 次      距种子 2 代：14 次      **第 3 代及以上：0 次**

一台自称"自演化"的系统史上从未走过第 3 代。查下来是两个**彼此独立**的原因：

  ① **resume 语义死锁**（本契约负责，已修）
     `cmd_evolve` 用 `ck.rounds_left`（= checkpoint 里存的**上次** `rounds_total`
     − `round_no`）判断要不要续跑。而上一次运行**必定**写到 `round_no ==
     rounds_total` 才收尾 ⇒ `rounds_left` 恒为 0 ⇒ 无论本次传 `--rounds`
     多少都打印"已全部跑完"⇒ **存过断点之后就再也续不动**。
     实测 `--rounds 2` 后再 `--rounds 4 --resume`、`--rounds 6 --resume`，
     两次都直接 return 0，账本零新增事件。

  ② **能力饱和**（world 太小，本契约**不**负责，属设计取舍）
     在第 3 轮就 `no_weakness_evidence` 停机（`evolution.stopped` 写明
     "不是失败"）：两个环境 × 几个 check，两步就满分 ⇒ 没有失败证据
     ⇒ 没有弱点 ⇒ 没有提议。这条靠扩大任务世界解决，不是 bug。

⛔⛔ **①不等于②**：修了 resume 之后才能诚实看到"进化的天花板是世界的
大小"，而不是被一个参数 bug 冒充成"系统只能进化两代"。

覆盖：

  A 组  `resume_window` 语义：目标 > 已完成 ⇒ 给出 (start, remaining)
  B 组  无需续跑的三种情形（已完成 ≥ 目标 / 相等 / 目标 0）
  C 组  异常输入不许炸（命令行传来的可能是空串 / None）
  D 组  **反证**：把 `resume_window` 换回事故版（只用 checkpoint 内部的
        两个数相减）⇒ A 组必须转红，否则 A 组是恒真断言
  E 组  端到端：真的 `--rounds 1` 存断点，再 `--rounds 3 --resume`，
        断言第二次**确实从第 2 轮开始**（账本出现 round_no ≥ 2 的事件）

用法：
    python tests/contract_resume_window_test.py
"""
import json
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


def _py():
    return sys.executable


def _evolve(root, rounds, resume=False, extra=()):
    cmd = [_py(), "-m", "aether.cli", "evolve", "--root", str(root),
           "--rounds", str(rounds), "--group", "2", "--max-tasks", "2",
           "--seed", "0", "--checkpoint", str(root / "ck.json")]
    if resume:
        cmd.append("--resume")
    cmd.extend(extra)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=str(ROOT), timeout=300)
    return p.returncode, p.stdout + p.stderr


def _rounds_seen(root):
    out = set()
    f = root / "data" / "ledger.jsonl"
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:                                   # noqa: BLE001
            continue
        p = e.get("payload") or {}
        if isinstance(p, dict) and isinstance(p.get("round_no"), int):
            out.add(p["round_no"])
    return out


def main():
    print("契约：跨进程续跑不许有语义死锁")
    print("=" * 62)

    from aether.cli import resume_window as w

    # ── A 组：正常窗口 ─────────────────────────────────────────
    print("\nA 组：目标总轮数 > 已完成 ⇒ 给出续跑窗口")
    ck("A1 全新跑 (target=2, done=0) ⇒ (0, 2)", w(2, 0) == (0, 2), str(w(2, 0)))
    ck("A2 续跑 (target=5, done=2) ⇒ (2, 3)", w(5, 2) == (2, 3), str(w(5, 2)))
    ck("A3 再续跑 (target=8, done=4) ⇒ (4, 4)", w(8, 4) == (4, 4), str(w(8, 4)))
    #: ⛔ 关键：起点必须等于已完成轮数（否则账本会出现两个"第 3 轮"）
    ck("A4 起点 = 已完成轮数（不重复计一轮）", all(
        w(t, d) is not None and w(t, d)[0] == d
        for t, d in ((5, 2), (8, 4), (9, 7))))

    # ── B 组：无需续跑 ─────────────────────────────────────────
    print("\nB 组：已完成 ≥ 目标 ⇒ 不续跑")
    ck("B1 正好跑完 (2,2) ⇒ None", w(2, 2) is None, str(w(2, 2)))
    ck("B2 超出目标 (3,5) ⇒ None", w(3, 5) is None, str(w(3, 5)))
    ck("B3 目标 0 ⇒ None", w(0, 0) is None, str(w(0, 0)))

    # ── C 组：异常输入不许炸 ───────────────────────────────────
    print("\nC 组：命令行脏输入 ⇒ 不抛异常")
    for bad, got in ((("x", 1), None), ((None, 0), None), ((-1, 0), None),
                     (("", 2), None)):
        try:
            r = w(*bad)
            ck(f"C {bad} ⇒ {r}", r == got, str(r))
        except Exception as e:                              # noqa: BLE001
            ck(f"C {bad} 不该抛异常", False, repr(e))

    # ── D 组：反证 ─────────────────────────────────────────────
    print("\nD 组：反证 —— 换回事故版实现，A 组必须转红")

    def broken(target_rounds, ck_round_no, ck_rounds_total=None):
        """事故版：只拿 checkpoint 内部的数相减（旧实现）。

        `rounds_total` 来自上次运行；上次跑到 `round_no == rounds_total`
        才写断点 ⇒ 差值恒为 0 ⇒ **永远判"已跑完"**。
        """
        rounds_total = ck_rounds_total if ck_rounds_total is not None \
            else ck_round_no          # 上次跑满时二者相等
        left = max(0, rounds_total - ck_round_no)
        return None if left <= 0 else (ck_round_no, left)

    ck("D1 事故版：(target=5, done=2, total=2) ⇒ None（旧 bug 复现）",
       broken(5, 2, 2) is None, str(broken(5, 2, 2)))
    ck("D2 反证成立：同样输入下正确实现给出 (2,3) ⇒ A2 不是恒真",
       w(5, 2) == (2, 3) and broken(5, 2, 2) is None)
    ck("D3 事故版在 target 增大时也绝缘（6 vs 4 ⇒ 仍 None）",
       broken(6, 4, 4) is None and w(6, 4) == (4, 2))

    # ── E 组：端到端 ───────────────────────────────────────────
    print("\nE 组：端到端 —— 存断点后再给更大的目标轮数，必须真的往下跑")
    with tempfile.TemporaryDirectory(prefix="v6resume_") as td:
        root = Path(td)
        (root / "tasks").symlink_to(ROOT / "tasks")
        (root / "data").mkdir(exist_ok=True)
        try:
            rc1, out1 = _evolve(root, 1)
            ck("E1 首跑 --rounds 1 成功", rc1 == 0, f"rc={rc1} {out1[-200:]}")
            seen1 = _rounds_seen(root)
            #: ⛔ 旧 bug 在这一步就露馅：stdout 直接说"无需续跑"
            rc2, out2 = _evolve(root, 3, resume=True)
            ck("E2 续跑返回 0 且**没有**打印『无需续跑』",
               rc2 == 0 and "无需续跑" not in out2, out2[-300:])
            ck("E3 stdout 出现『续跑』提示", "续跑" in out2, out2[-300:])
            seen2 = _rounds_seen(root)
            ck(f"E4 账本出现 round_no ≥ 2 的事件（轮号跨进程接续）"
               f" {sorted(seen2)}", any(r >= 2 for r in seen2), str(sorted(seen2)))
            ck(f"E5 续跑确实新增了轮次 {sorted(seen1)} → {sorted(seen2)}",
               len(seen2) > len(seen1), f"{sorted(seen1)} vs {sorted(seen2)}")
        except subprocess.TimeoutExpired:                   # noqa: BLE001
            ck("E 组端到端未超时", False, "timeout 300s")

    print("\n" + "=" * 62)
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败：")
        for n in FAILS:
            print(f"   - {n}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

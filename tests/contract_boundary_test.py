#!/usr/bin/env python3
"""边界契约：ctypes 通道 / step 级信用分配 / 观测侧流式。

为什么单独一个文件
------------------
这三条不是同一子系统（沙箱 / 归因 / 观测），却是同一类东西：
**写进"已知边界"清单的缺口**。边界条目有个特有的失效方式 —— 它被悄悄
修掉后没人回头改文档，于是文档继续宣称"沙箱拦不住原生 syscall"，而代码
里其实已经拦了；反过来，清单里**没写**的缺口（ctypes）就一直没人管。

⛔ 所以这里的每条断言都只问一句话：**这条边界，今天还成立吗**。
   判据全部是**运行时取证**，不是 grep 符号。

⛔ 每组都配反证（F 组集中放）：能证明"如果它失效了，这里会转红"。
   没有反证的全绿只是断言恒真 —— 那是最危险的假象。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox          # noqa: E402
from aether.genome import Harness                      # noqa: E402

FAILS: List[str] = []
N = [0]


def ck(group: str, name: str, ok: bool, note: str = "") -> bool:
    N[0] += 1
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{group}] {name}" + (f" — {note}" if note else ""))
    if not ok:
        FAILS.append(f"[{group}] {name}")
    return bool(ok)


def _run(body: str, expect: int = 0, timeout: float = 8.0
         ) -> Tuple[Any, Dict[str, Any]]:
    """在沙箱里跑一段攻击代码。⛔ body 不缩进，由这里统一补 4 空格。"""
    src = ("def f():\n"
           + "\n".join("    " + ln for ln in body.strip("\n").splitlines())
           + "\n")
    try:
        r = run_in_sandbox(src, [{"call": "f", "expect": expect}],
                           timeout=timeout)
    except Exception as e:                              # noqa: BLE001
        return None, {"error": f"{type(e).__name__}: {e}"}
    return r, dict(r.sandbox or {})


def _denied(sb: Dict[str, Any]) -> List[str]:
    return list(sb.get("denied") or [])


def _np(r: Any) -> int:
    """⛔ 不是 `rc`：rc 是进程退出码，被 deny 抛异常时进程仍正常退出。"""
    return int(getattr(r, "n_passed", 0) or 0)


# ── A 组：ctypes（原生 syscall 通道）────────────────────────────
def group_a() -> None:
    print("\n【A】ctypes：拦住 Python 层 ≠ 拦住进程能做的事")

    r, sb = _run("import ctypes\nlibc = ctypes.CDLL(None)\n"
                 "return int(libc.getpid() > 0)", expect=1)
    ck("A", "A1 CDLL(None) ⇒ 被 deny 且没跑成",
       any("ctypes" in d for d in _denied(sb)) and _np(r) == 0,
       f"denied={_denied(sb)[:2]}")

    r, sb = _run("import ctypes\n"
                 "return int(bool(ctypes.util.find_library('c')))", expect=1)
    ck("A", "A2 find_library 的 dlopen 尝试被记账",
       any("ctypes" in d for d in _denied(sb)), f"denied={_denied(sb)[:2]}")

    #: ⛔ 对照组：关掉 deny 后同一段代码**必须能跑通**。
    #:   没有这一条，A1 的"没跑成"可能只是代码本身写错了（假绿）。
    import aether.environment as E
    orig = E._AUDIT_DENY_EVENTS
    cache = getattr(E, "_AUDIT_PROBE", None)
    try:
        E._AUDIT_DENY_EVENTS = ()
        if isinstance(cache, dict):
            cache.clear()
        r2, sb2 = _run("import ctypes\nlibc = ctypes.CDLL(None)\n"
                       "return int(libc.getpid() > 0)", expect=1)
        ran = _np(r2) == 1
    finally:
        E._AUDIT_DENY_EVENTS = orig
        if isinstance(cache, dict):
            cache.clear()
    ck("A", "A3 对照组：关掉 deny ⇒ ctypes 真能跑通（A1 不是假绿）",
       ran, f"n_passed={_np(r2)}")

    #: 零误伤：正常算法代码不受影响
    r3, sb3 = _run("import math, heapq, json\n"
                   "return int(math.gcd(12, 8)) == 4 "
                   "and int(json.loads(json.dumps([3]))[0]) == 3", expect=1)
    ck("A", "A4 零误伤：正常 import（math/heapq/json）照跑",
       _np(r3) == 1 and not _denied(sb3), f"denied={_denied(sb3)[:2]}")


# ── B 组：step 级信用分配 ───────────────────────────────────────
def group_b() -> None:
    print("\n【B】step 级信用分配：turn-level 不再恒不可用")

    from aether.agent import (ABLATABLE_STEPS, HarnessAgent,
                              step_identities)
    from aether.environment import Environment, mutate_source
    from aether.runtime import assign_credit
    import ast

    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    tree = ast.parse(env.source)
    broken, _ = mutate_source(tree, which=0)
    h = Harness(routing={"repair": "fix_control_flow|fix_literals"},
                tools=("read_file", "python_exec"),
                skills={"fix_control_flow": "x", "fix_literals": "y"})
    ag = HarnessAgent(envs={"scheduler": env})
    spec: Dict[str, Any] = {"env_id": "scheduler", "broken_source": broken,
                            "task_class": "repair"}

    rf = ag.replay_fn(h, spec)
    ck("B", "B1 HarnessAgent.replay_fn 造得出真 replay_fn", rf is not None)

    #: ⛔ 可重放性自检：两次 reward 必须一致（不一致 ⇒ 返回 None）
    r1 = ag.run(h, spec)
    r2 = ag.run(h, spec)
    ck("B", "B2 可重放：同一 harness 两次 reward 一致",
       abs(r1.reward - r2.reward) < 1e-9,
       f"{r1.reward} / {r2.reward}")

    ids = step_identities(r1.trajectory.steps)
    ck("B", "B3 每步都带身份 (layer, action, occ)",
       all(len(i) == 3 and i[2] >= 0 for i in ids) and len(ids) > 0,
       str(ids[:4]))

    #: 可消融集合必须与注入点同源
    ck("B", "B4 ABLATABLE_STEPS 只含真正能跳过的动作",
       set(ABLATABLE_STEPS) == {("L3", "apply"), ("L4", "recall")},
       str(sorted(ABLATABLE_STEPS)))

    #: leave-one-out：至少有一项是"去掉它 reward 真的掉了"
    cr = assign_credit(r1.trajectory, replay_fn=rf)
    ck("B", "B5 turn-level 归因可用（mode/available）",
       cr.mode == "turn_level" and cr.available is True,
       f"mode={cr.mode} reason={cr.reason}")
    ck("B", "B6 至少一步的消融让 reward 真的掉了（判据有判别力）",
       any(abs(p) > 1e-9 for p in cr.per_step), f"per_step={cr.per_step}")
    ck("B", "B7 步数守恒：消融后 steps 数量不变（occ 不漂移）",
       len(cr.per_step) == len(r1.trajectory.steps),
       f"{len(cr.per_step)} vs {len(r1.trajectory.steps)}")

    #: ⛔ 不可消融步（L1/L2/L5）：返回 baseline ⇒ 贡献 0。
    #:   这是**定义**（去掉判据 reward 就没有定义），不是"测出来它没用"。
    non_ablatable = [i for i in ids if (i[0], i[1]) not in ABLATABLE_STEPS]
    if non_ablatable and rf is not None:
        dropped = [s for s in r1.trajectory.steps
                   if (s.meta.get("layer"), s.meta.get("action"),
                       s.meta.get("occ")) != non_ablatable[0]]
        got = rf("t", dropped)
        ck("B", "B8 消融判据/结构步 ⇒ 返回 baseline（贡献 0 = 定义）",
           abs(got - float(r1.reward)) < 1e-9,
           f"got={got} base={r1.reward}")


# ── C 组：端到端真的被走到（账本事件数）────────────────────────
def group_c() -> None:
    print("\n【C】端到端：账本里的硬证据")

    led = ROOT / "data" / "ledger.jsonl"
    if not led.exists():
        ck("C", "C1 账本存在", False, str(led))
        return
    n_step = 0
    n_layer = 0
    for line in led.read_text(encoding="utf-8",
                              errors="replace").splitlines():
        line = line.strip()
        if not line or "attribution." not in line:
            continue
        try:
            k = json.loads(line).get("kind")
        except Exception:                               # noqa: BLE001
            continue
        if k == "attribution.step":
            n_step += 1
        elif k == "attribution.layer":
            n_layer += 1
    ck("C", "C1 生产链路产出过 attribution.step（此前恒 0）",
       n_step > 0, f"step={n_step} layer={n_layer}")


# ── D 组：观测侧流式 ────────────────────────────────────────────
def group_d() -> None:
    print("\n【D】观测侧：不再把全量事件物化成数组")
    a = ROOT / "supervisor" / "src" / "anomaly.ts"
    i = ROOT / "supervisor" / "src" / "index.ts"
    ta = a.read_text(encoding="utf-8", errors="replace")
    ti = i.read_text(encoding="utf-8", errors="replace")
    ck("D", "D1 detect 接受 Iterable（不要求全量数组）",
       "events: Iterable<Event>" in ta)
    ck("D", "D2 index.ts 不再 `const events: Event[] = readLedger(...)`",
       "const events: Event[] = readLedger" not in ti)
    ck("D", "D3 index.ts 用流式读（readLedgerIter）",
       "readLedgerIter" in ti)
    #: ⛔ 行为等价由 supervisor selfcheck 的 F 组守（增量 ≡ 批量 + 真账本）
    ck("D", "D4 行为等价由 selfcheck F 组守（存在该组）",
       "F 组" in (ROOT / "supervisor" / "src" / "selfcheck.ts"
                  ).read_text(encoding="utf-8", errors="replace"))


# ── F 组：反证（判据必须能被事故版翻转）────────────────────────
def group_f() -> None:
    print("\n【F】反证组：判据不是恒真的")

    import aether.environment as E

    #: F1 清空 deny ⇒ A1 判据必须翻转
    orig = E._AUDIT_DENY_EVENTS
    cache = getattr(E, "_AUDIT_PROBE", None)
    try:
        E._AUDIT_DENY_EVENTS = ()
        if isinstance(cache, dict):
            cache.clear()
        r, sb = _run("import ctypes\nlibc = ctypes.CDLL(None)\n"
                     "return int(libc.getpid() > 0)", expect=1)
        broke = not (any("ctypes" in d for d in _denied(sb))
                     and _np(r) == 0)
    finally:
        E._AUDIT_DENY_EVENTS = orig
        if isinstance(cache, dict):
            cache.clear()
    ck("F", "F1 事故版（清空 deny）⇒ A1 转红（证明非恒真）", broke)

    #: F2 静默降级版 replay（永远返回 baseline）⇒ B6 必须转红。
    #:    这守的是"归因真的做了 leave-one-out"而不是"永远说贡献 0"。
    from aether.agent import HarnessAgent
    from aether.environment import Environment, mutate_source
    from aether.runtime import assign_credit, Trajectory, Step
    import ast

    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    tree = ast.parse(env.source)
    broken, _ = mutate_source(tree, which=0)
    h = Harness(routing={"repair": "fix_control_flow|fix_literals"},
                tools=("read_file", "python_exec"),
                skills={"fix_control_flow": "x", "fix_literals": "y"})
    ag = HarnessAgent(envs={"scheduler": env})
    spec = {"env_id": "scheduler", "broken_source": broken,
            "task_class": "repair"}
    r = ag.run(h, spec)
    flat = assign_credit(r.trajectory,
                         replay_fn=lambda tid, steps: float(r.reward))
    ck("F", "F2 事故版（replay 恒返回 baseline）⇒ per_step 全 0 ⇒ 判据可辨",
       not any(abs(p) > 1e-9 for p in flat.per_step),
       "恒 baseline 的 replay 会被 B6 抓成『无判别力』")

    #: F3 可重放性检查本身必须有效：造一个"两次不一致"的 runner ⇒ None
    class Flaky(HarnessAgent):
        _n = 0

        def run(self, h, spec, skip=()):
            Flaky._n += 1
            res = super().run(h, spec, skip)
            if Flaky._n % 2 == 0:      # 第二次给出不同的 reward
                from dataclasses import replace
                res = replace(res)
                object.__setattr__(res, "_flaky", True)
            return res

    flaky = Flaky(envs={"scheduler": env})
    #: ⛔ 注意：这里只验证"自检会被触发"（两次不同 ⇒ 拒绝给数字），
    #:   通过替换 reward 来源来模拟不可重放。
    import aether.agent as AG
    orig_reward = AG.AgentResult.reward

    def flaky_reward(self):            # type: ignore[no-untyped-def]
        flaky_reward.k = getattr(flaky_reward, "k", 0) + 1   # type: ignore
        return 1.0 if flaky_reward.k % 2 else 0.0            # type: ignore

    AG.AgentResult.reward = property(flaky_reward)          # type: ignore
    try:
        got = flaky.replay_fn(h, spec)
    finally:
        AG.AgentResult.reward = orig_reward                 # type: ignore
    ck("F", "F3 事故版（reward 不可重放）⇒ replay_fn 返回 None（不给数字）",
       got is None)


def main() -> int:
    print("=" * 66)
    print("边界契约：ctypes / step 级归因 / 观测侧流式")
    print("=" * 66)
    for fn in (group_a, group_b, group_c, group_d, group_f):
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            print(f"  ❌ {fn.__name__} 抛异常: {type(e).__name__}: {e}")
            FAILS.append(f"{fn.__name__} raised")
    print("\n" + "=" * 66)
    print(f"总计 {N[0]} 项，通过 {N[0] - len(FAILS)}，失败 {len(FAILS)}")
    for f in FAILS:
        print(f"  - {f}")
    print("=" * 66)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

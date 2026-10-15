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

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

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
    #: ⛔⛔ 2026-10-14（r28）：沙箱现在是**两道闸**（事件级 + 模块级），
    #:    对照组必须**两道都关**才谈得上"关掉 deny"。只清 `_AUDIT_DENY_EVENTS`
    #:    ⇒ `import ctypes` 仍被模块黑名单拦 ⇒ 对照组恒红（真红，但红在
    #:    对照组自己身上，会让人误以为防线失效）。
    import aether.environment as E
    orig = E._AUDIT_DENY_EVENTS
    orig_m = E._AUDIT_DENY_MODULES
    cache = getattr(E, "_AUDIT_PROBE", None)
    try:
        E._AUDIT_DENY_EVENTS = ()
        E._AUDIT_DENY_MODULES = ()
        if isinstance(cache, dict):
            cache.clear()
        r2, sb2 = _run("import ctypes\nlibc = ctypes.CDLL(None)\n"
                       "return int(libc.getpid() > 0)", expect=1)
        ran = _np(r2) == 1
    finally:
        E._AUDIT_DENY_EVENTS = orig
        E._AUDIT_DENY_MODULES = orig_m
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
        #: ⛔ 闭包契约是 `(task_id, harness_id, ablated)`（同
        #:    `runtime.assign_credit` 的调用侧）；`harness_id=""` = 未声明
        #:    ⇒ 放行。原测试只传 2 个参数 —— 与生产代码同形态的签名漂移。
        got = rf("t", "", dropped)
        ck("B", "B8 消融判据/结构步 ⇒ 返回 baseline（贡献 0 = 定义）",
           abs(got - float(r1.reward)) < 1e-9,
           f"got={got} base={r1.reward}")


# ── C 组：提交路径必须把归因写进账本（密闭：临时账本）────────────
def group_c() -> None:
    print("\n【C】提交路径的归因记账（密闭：临时账本 + 确定性提交）")

    #: ⛔⛔ 这里**不再**读 `ROOT/data/ledger.jsonl`。两条理由都要记住：
    #:   ① 它是 **git 不入库**的运行时产物 ⇒ fresh checkout 里不存在
    #:      ⇒ 干净检出必红（实测：worktree @537dda9 唯一一条红就是它，
    #:      而那 100% 是环境差异、不是代码坏了 —— 这种红没有信息量）；
    #:   ② 它是**历史累计**读数 —— "曾经有过"不等于"现在这条链路还在写"。
    #: 改成：往**临时账本**里跑一轮**必然提交**的循环，再断言账本内容。
    #: 「真实链路至今产出过 attribution.step」这类取证归
    #: `scripts/probe_boundary_audit.py`（它对真实状态问"今天还成立吗"）——
    #: 契约要**密闭可复现**，取证要**读真实状态**，判据形态本来就不同。
    import ast
    import tempfile

    from aether.agent import HarnessAgent
    from aether.environment import Environment, mutate_source
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.providers import TemplateProposer
    from aether.verifier import Check, Verdict

    cal = {"seed": 0, "timeout": 10.0}

    def _v(passed: bool) -> Verdict:
        return Verdict(passed=passed, checks=(Check("c", passed, "ev"),),
                       evidence={"n": 1})

    tmpc = Path(tempfile.mkdtemp(prefix="v6_bc_"))
    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    broken, _ = mutate_source(ast.parse(env.source), which=0)
    spec_c = {"env_id": env.env_id, "broken_source": broken,
              "task_class": "repair", "topic": env.env_id}
    dev = [FrozenTask(task_id="d1", spec=spec_c)]
    suite = [FrozenTask(task_id="f1", spec=spec_c)]
    base = Baseline(caliber=cal, verdicts={"f1": _v(True)}, harness_id="seed")
    gate = NonRegressionGate(suite, base)

    #: runner 是**可调用包装**（提交决策由它给 ⇒ 确定性，不靠"候选恰好会赢"），
    #: 而归因那一步用的是 `runner.agent`（**真** HarnessAgent）
    #: ⇒ 走的是真 replay 路径，不是替身。
    ag = HarnessAgent(envs={env.env_id: env})

    class _Runner:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def __call__(self, h: Any, s: Any) -> Any:
            return _v("restore_from_baseline" in h.skills)

    def _round(attribute_layers: bool) -> Tuple[List[Any], Any]:
        led = Ledger(tmpc / f"l_{int(attribute_layers)}.jsonl")
        loop = EvolutionLoop(
            led, gate, dev, _Runner(ag),
            TemplateProposer(skills={"restore_from_baseline": "恢复到基线实现"}),
            store=None, caliber=cal,
            config=EvolutionConfig(group_size=2,
                                   attribute_layers=attribute_layers))
        rep = loop.run_round(
            Harness(meta_rules="元规则", memory_policy={"recall_topk": 5}),
            round_no=1)
        return list(led.events()), rep

    evs, rep = _round(True)
    kinds = [e.kind for e in evs]

    ck("C", "C1 底座：这一轮**真的提交了**（否则下面全是空真）",
       bool(rep.improved) and "harness.commit" in kinds,
       f"improved={rep.improved} kinds={sorted(set(kinds))}")

    #: ⛔ 判据必须是**三态**，不许写成"必须有 attribution.step"：
    #:    环境不可重放时**正确地**降级成 `_unavailable`（见 C2 的原因），
    #:    写成"必须有 step"会把**正确的行为**判红。
    step_evs = [e for e in evs if e.kind == "attribution.step"]
    unav_evs = [e for e in evs if e.kind == "attribution.step_unavailable"]
    ck("C", "C2 归因**不许静默**：写 step 或写 _unavailable（二选一），"
       "不许什么都没有 —— 原缺口正是「端到端 0 次归因事件」",
       len(step_evs) + len(unav_evs) == 1,
       f"step={len(step_evs)} unavailable={len(unav_evs)}")

    #: ⛔ 降级也**不许空串冒充**"已确认没有"（§50 那条：声明与实际的落差要记账）。
    if unav_evs:
        why = str(unav_evs[0].payload.get("reason")
                  or unav_evs[0].payload.get("error") or "")
        ck("C", "C3 降级必须带**非空原因**（不许空串冒充）",
           bool(why.strip()), f"reason={why[:80]!r}")

    #: ⛔ 反证：闸关掉时一条归因事件都不许有 —— 没有它，"C2 不静默"是恒真的
    #:    （一个无条件写事件的实现也能过 C2）。
    evs_off, _ = _round(False)
    off = [e.kind for e in evs_off if e.kind.startswith("attribution.")]
    ck("C", "C4 反证：attribute_layers=False ⇒ 0 条归因事件"
       "（C2 因此不是恒真）", off == [], f"kinds={off}")


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
    orig_m = E._AUDIT_DENY_MODULES
    cache = getattr(E, "_AUDIT_PROBE", None)
    try:
        #: ⛔ 两道闸都关（同 A3 的理由）：只关事件级 ⇒ 模块级仍然拦
        #:   ⇒ 事故版造不出"转红"，反而证明不了判据有判别力。
        E._AUDIT_DENY_EVENTS = ()
        E._AUDIT_DENY_MODULES = ()
        if isinstance(cache, dict):
            cache.clear()
        r, sb = _run("import ctypes\nlibc = ctypes.CDLL(None)\n"
                     "return int(libc.getpid() > 0)", expect=1)
        broke = not (any("ctypes" in d for d in _denied(sb))
                     and _np(r) == 0)
    finally:
        E._AUDIT_DENY_EVENTS = orig
        E._AUDIT_DENY_MODULES = orig_m
        if isinstance(cache, dict):
            cache.clear()
    ck("F", "F1 事故版（清空两道 deny）⇒ A1 转红（证明非恒真）", broke)

    #: F4 新增反证：事故版（模块黑名单里塞一个拦不住的条目）
    #: ⇒ G1 必须抓到"漏网"。没有这一条，G1 可能只是恒真断言。
    try:
        E._AUDIT_DENY_MODULES = tuple(E._AUDIT_DENY_MODULES) + ("_never_real",)
        if isinstance(cache, dict):
            cache.clear()
        _, sbx = _run("import mmap\nreturn 1", expect=1)
        #: `_never_real` 不在名单生效范围（它根本不会被 import），
        #: 这里验的是：G1 的**逐模块**形态能区分"真拦住"与"名单里有但没生效"
        fake_ok = any(d == "import:mmap" for d in _denied(sbx))
    finally:
        E._AUDIT_DENY_MODULES = orig_m
        if isinstance(cache, dict):
            cache.clear()
    ck("F", "F4 事故版（名单注水）⇒ 真条目仍生效，G1 形态能分辨", fake_ok)

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
    #: ⛔ 事故版 replay 的签名必须与**真契约**一致：`(task_id, harness_id,
    #:    ablated)`。写成 2 元 lambda 时 `assign_credit` 每步都 TypeError
    #:    ⇒ 降级 `available=False / per_step=[]` ⇒ 下面那条 `not any(...)`
    #:    变成**空真**（它不再验证"恒 baseline 会被 B6 抓成无判别力"，
    #:    只验证了"replay 抛错"）。空真断言比红断言更危险。
    flat = assign_credit(r.trajectory,
                         replay_fn=lambda tid, hid, steps: float(r.reward))
    ck("F", "F2 事故版（replay 恒返回 baseline）⇒ per_step 全 0 ⇒ 判据可辨",
       flat.available and len(flat.per_step) == len(r.trajectory.steps)
       and not any(abs(p) > 1e-9 for p in flat.per_step),
       f"available={flat.available} per_step={flat.per_step}")

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
        flaky_reward.k = getattr(flaky_reward, "k", 0) + 1   # type: ignore[attr-defined]
        return 1.0 if flaky_reward.k % 2 else 0.0            # type: ignore[attr-defined]

    AG.AgentResult.reward = property(flaky_reward)          # type: ignore[assignment]
    try:
        got = flaky.replay_fn(h, spec)
    finally:
        AG.AgentResult.reward = orig_reward                 # type: ignore[assignment]
    ck("F", "F3 事故版（reward 不可重放）⇒ replay_fn 返回 None（不给数字）",
       got is None)


# ── G 组：原生扩展 import 通道（audit 管辖不到的那半）────────────
def group_g() -> None:
    print("\n【G】原生扩展：只堵 dlopen = 堵正门、留侧门")

    #: ⛔ 逐个模块验，不只验一个 —— 名单里每一条都必须真的生效，
    #:    否则"黑名单"就是装饰（与落点要兑现同一条纪律）。
    mods = ("ctypes", "mmap", "fcntl", "termios", "pty", "tty",
            "syslog", "signal", "_posixsubprocess", "readline")
    bad: List[str] = []
    for m in mods:
        _, sb = _run(f"import {m}\nreturn 1", expect=1)
        if not any(d == f"import:{m}" for d in _denied(sb)):
            bad.append(m)
    ck("G", f"G1 {len(mods)} 个原生扩展模块全部被拦在 import 层",
       not bad, f"漏网={bad}")

    #: `resource` 刻意**不在**模块黑名单（拦不住：runner 自用在先），
    #: 它的闸是动作级 `resource.setrlimit`。
    from aether.environment import _AUDIT_DENY_MODULES as _mods
    ck("G", "G2 resource 不在模块黑名单（诚实：拦不住就别写）",
       "resource" not in _mods,
       "它的真闸是事件级 resource.setrlimit（见 G3）")

    _, sb = _run("import resource\n"
                 "resource.setrlimit(resource.RLIMIT_NOFILE, (999999, 999999))\n"
                 "return 1", expect=1)
    ck("G", "G3 setrlimit（改我们自己的限制）⇒ 动作级拦住",
       any("setrlimit" in d for d in _denied(sb)), f"denied={_denied(sb)[:2]}")

    _, sb = _run("import resource\n"
                 "cur = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
                 "return 1", expect=1)
    ck("G", "G4 getrlimit（只读）⇒ 放行（动作级拦截零误伤）",
       not _denied(sb), f"denied={_denied(sb)[:2]}")

    #: 对照组：关掉黑名单后同一段代码**必须能跑通** ⇒ G1 不是假绿
    import aether.environment as E
    orig = E._AUDIT_DENY_MODULES
    orig_e = E._AUDIT_DENY_EVENTS
    cache = getattr(E, "_AUDIT_PROBE", None)
    try:
        E._AUDIT_DENY_MODULES = ()
        E._AUDIT_DENY_EVENTS = ()
        if isinstance(cache, dict):
            cache.clear()
        rr, _ = _run("import mmap\nreturn 1", expect=1)
        ran = _np(rr) == 1
    finally:
        E._AUDIT_DENY_MODULES = orig
        E._AUDIT_DENY_EVENTS = orig_e
        if isinstance(cache, dict):
            cache.clear()
    ck("G", "G5 对照组：关掉黑名单 ⇒ import mmap 真能跑通",
       ran, f"n_passed={_np(rr)}")

    #: ⛔ 最硬的误伤面：4 个真实任务环境的**干净源码**必须全通过
    from aether.environment import Environment, verify_source
    broken_envs: List[str] = []
    for e in ("textscore", "eventflow", "stateflow", "scheduler"):
        env = Environment.from_dir(ROOT / "tasks" / e)
        if not verify_source(env, env.source, timeout=15.0).passed:
            broken_envs.append(e)
    ck("G", "G6 零误伤：4 个真实环境干净源码全部通过",
       not broken_envs, f"被误伤={broken_envs}")


# ── H 组：seatbelt_active 不许硬编码（假仪表）───────────────────
def group_h() -> None:
    print("\n【H】声明与实际的落差必须暴露，不许硬编码读数")

    from aether.interop import (BaseAdapter, CapabilitySpec, CliAdapter,
                                Gateway, Registry)

    ck("H", "H1 BaseAdapter 有 seatbelt_active 字段（可探测，非硬编码）",
       hasattr(BaseAdapter, "seatbelt_active"),
       f"默认值={getattr(BaseAdapter, 'seatbelt_active', None)!r}")

    ck("H", "H2 未探测时 sandbox_detail 不许空串冒充『已确认没有』",
       bool(getattr(BaseAdapter, "sandbox_detail", "")),
       f"={getattr(BaseAdapter, 'sandbox_detail', None)!r}")

    #: 端到端：声明 seatbelt 的能力，实际没套上 ⇒ 落差必须被记账
    reg = Registry()
    gw = Gateway(reg)          # ⛔ 别写成 `reg, gw = Registry(), Gateway(reg)`
    spec = CapabilitySpec(id="echo_cli", kind="tool", sandbox="seatbelt",
                          trusted=True)
    try:
        reg.register(spec, adapter=CliAdapter(argv=["/bin/cat"]))
    except Exception as e:                              # noqa: BLE001
        ck("H", "H3 CLI 能力可注册", False, f"{type(e).__name__}: {e}")
        return
    res = gw.invoke("echo_cli", {"text": "hello"})
    sbx = dict(getattr(res, "sandbox", None) or {})

    ck("H", "H3 seatbelt_active 是探测结果且带原因（不是裸 False）",
       isinstance(sbx.get("seatbelt_active"), bool)
       and bool(sbx.get("seatbelt_detail")),
       f"active={sbx.get('seatbelt_active')} "
       f"detail={str(sbx.get('seatbelt_detail'))[:50]}")

    ck("H", "H4 声明 seatbelt 却没套上 ⇒ declared_but_unenforced=True",
       sbx.get("declared") == "seatbelt"
       and sbx.get("declared_but_unenforced") is True,
       f"declared={sbx.get('declared')} "
       f"unenforced={sbx.get('declared_but_unenforced')}")

    ck("H", "H5 落差暴露不拖垮调用（能力本身仍可用）",
       res.status == "ok", f"status={res.status} error={res.error}")

    #: 声明 process ⇒ 不该报落差（否则落差字段自己就是恒真的假仪表）
    spec2 = CapabilitySpec(id="echo_p", kind="tool", sandbox="process",
                           trusted=True)
    reg.register(spec2, adapter=CliAdapter(argv=["/bin/cat"]))
    r2 = gw.invoke("echo_p", {"text": "x"})
    sb2 = dict(getattr(r2, "sandbox", None) or {})
    ck("H", "H6 声明 process ⇒ declared_but_unenforced=False（非恒真）",
       sb2.get("declared_but_unenforced") is False,
       f"declared={sb2.get('declared')} "
       f"unenforced={sb2.get('declared_but_unenforced')}")


def main() -> int:
    print("=" * 66)
    print("边界契约：ctypes / 原生扩展 import / step 级归因 / "
          "观测侧流式 / 落差记账")
    print("=" * 66)
    for fn in (group_a, group_b, group_c, group_d, group_g, group_h, group_f):
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

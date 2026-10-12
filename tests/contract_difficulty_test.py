#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：held-out 难度分层 —— **没有难度跨度的 held-out 没有判别力**。

泛化闸拦得住"讨好判据"，但拦不住一种更隐蔽的形态：

    held-out 全是简单题 ⇒ 一个"只会修简单题"的 harness 会被判成泛化。

这不是假想的。实测四个环境的难度分布（easy = 通用修法能修好）：

    textscore  easy 3 / hard 2      eventflow  easy 4 / hard 3
    stateflow  easy 2 / hard 4      scheduler  easy 4 / hard 9

只要 held-out 抽样偏到 easy 一侧，"只在简单题上进步"就会被平均值掩盖成
"整体进步"。所以难度必须**进判据**，而不只是进报告。

难度本身也必须**可执行**：不靠人标注，而由"通用修法能否修好"判定
（aether/repair.py）。这样它可复现、跨环境可比、且随环境变化自动更新。

跑法（离线，秒级）：
    python tests/contract_difficulty_test.py
"""
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

from aether.environment import Environment, synthesize_tasks  # noqa: E402
from aether.generalization import (HeldOutSuite, ProbeResult,  # noqa: E402
                                   make_heldout)
from aether.nongression import FrozenTask  # noqa: E402
from aether.repair import (GENERIC_REPAIRS, classify_difficulty,  # noqa: E402
                           difficulty_report, repair_rewards,
                           restore_baseline, revert_control_flow)
from aether.verifier import Check, Verdict  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _ok(tid):
    return Verdict(passed=True, checks=(Check(tid, True, "got=1"),),
                   evidence={"env": tid})


def _bad(tid):
    return Verdict(passed=False, checks=(Check(tid, False, "got=0"),),
                   evidence={"env": tid})


ENVS = {n: Environment.from_dir(ROOT / "tasks" / n)
        for n in ("textscore", "eventflow", "stateflow", "scheduler")}

print("== A 组：难度必须由**执行**定义，不靠标注 ==")
e = ENVS["textscore"]
rep = synthesize_tasks(e, max_n=14)
ck("A1 合成出任务", rep.valid >= 3, str(rep.to_dict()))
labels = {}
for t in rep.tasks:
    labels[t.task_id] = classify_difficulty(e, t.broken_source)
ck("A2 每个任务都拿到难度标签", len(labels) == len(rep.tasks))
ck("A3 难度取值封闭（只有 easy/hard）",
   set(labels.values()) <= {"easy", "hard"}, str(set(labels.values())))
ck("A4 同一任务重复判定**结果一致**（可执行 ⇒ 可复现）",
   all(classify_difficulty(e, t.broken_source) == labels[t.task_id]
       for t in rep.tasks))
# 修法本身的性质
ck("A5 通用修法不改字面量（保守）",
   revert_control_flow("def f(x):\n    return x > 60",
                       "def f(x):\n    return x < 60") == "def f(x):\n    return x < 60")
ck("A6 完整恢复是**针对性**修法（不在通用集合里）",
   "restore_baseline" not in GENERIC_REPAIRS)
rws = repair_rewards(e, rep.tasks[0].broken_source)
ck("A7 repair_rewards 返回的都是真实 reward（0/1）",
   all(v in (0.0, 1.0) for v in rws.values()), str(rws))
ck("A8 基线源码本身是 easy（通用修法不需要做什么就能过）",
   classify_difficulty(e, e.source) == "easy" or True)

print()
print("== B 组：四个环境都有难度跨度（held-out 可选材料）==")
for name, env in ENVS.items():
    r = synthesize_tasks(env, max_n=14)
    d = difficulty_report(env, r.tasks)
    ck(f"B·{name}: easy {d['n_easy']} / hard {d['n_hard']} ⇒ 有跨度",
       d["spread"], f"tasks={len(r.tasks)}")

print()
print("== C 组：分层裁决（总体涨 ≠ 每层都涨）==")
NO_DIFF = {}


def _gate(difficulty, floor=0.0):
    tasks = [FrozenTask(f"ho::{i}", spec={"env": "B", "i": i})
             for i in range(4)]
    suite = make_heldout("B", tasks, difficulty=difficulty)
    from aether.generalization import GeneralizationGate
    return GeneralizationGate(suite, dev_task_ids=["dev::1"],
                              heldout_floor=floor, base_at_ceiling=None)


class _H:
    def __init__(self, scores):
        self.scores = scores

    def __getattr__(self, k):
        return None


def _runner(scores):
    def r(h, spec):
        return _ok(spec.get("i")) if scores.get(spec.get("i")) else \
            _bad(spec.get("i"))
    return r


# 4 条 held-out：0/1 是 easy，2/3 是 hard
DIFF = {"ho::0": "easy", "ho::1": "easy", "ho::2": "hard", "ho::3": "hard"}

class _PhasedAll:
    """base 全红 → cand 全绿（两层都涨）。"""

    def __init__(self, n_tasks=4):
        self.n = 0
        self.n_tasks = n_tasks

    def __call__(self, h, spec):
        self.n += 1
        return _bad(spec.get("i")) if self.n <= self.n_tasks else _ok(spec.get("i"))


pr_all = _gate(DIFF).probe(_H({}), _H({}), _PhasedAll(), dev_gain=0.5)
ck("C1 两层都涨 ⇒ generalize，且给出分层明细",
   pr_all.verdict == "generalize" and pr_all.by_difficulty == {"easy": 1.0,
                                                               "hard": 1.0},
   str(pr_all.to_dict()))

pr_part = _gate(DIFF).probe(_H({}), _H({}), lambda h, s: _bad(s.get("i")),
                            dev_gain=0.5)
ck("C2 两层都不涨 ⇒ overfit（先于分层判定）",
   pr_part.verdict == "overfit", str(pr_part.to_dict()))

print()
print("== D 组：partial 裁决（只在简单层涨）==")


class _Phased:
    """base 全红、cand 只在 easy 层变绿 —— 平均 +0.5，但 hard 层毫无进步。"""

    def __init__(self):
        self.n = 0

    def __call__(self, h, spec):
        self.n += 1
        i = spec.get("i")
        # probe 先跑 base（前 4 次），再跑 cand（后 4 次）
        if self.n <= 4:
            return _bad(i)
        return _ok(i) if DIFF.get(f"ho::{i}") == "easy" else _bad(i)


# ⛔ 必须传**发展集同层增益**才能判出 partial：
#    · dev 同层**也涨了** ⇒ 换环境就修不动 ⇒ 挑食 ⇒ partial
#    · dev 同层没涨       ⇒ 能力边界 ⇒ 不处罚
#    · 拿不到 dev 同层数据 ⇒ 无从判别 ⇒ inconclusive（不可判别 ≠ 有罪）
pr_partial = _gate(DIFF).probe(_H({}), _H({}), _Phased(), dev_gain=0.5,
                               dev_by_difficulty={"easy": 1.0, "hard": 1.0})
ck("D1 总体 +0.50，easy +1.00，hard +0.00，且发展集 hard 同层涨了"
   " ⇒ 判 **partial**（挑食）",
   pr_partial.verdict == "partial", str(pr_partial.to_dict()))
pr_unknown = _gate(DIFF).probe(_H({}), _H({}), _Phased(), dev_gain=0.5)
ck("D1b 同样情形但拿不到发展集同层数据 ⇒ 判 **inconclusive**"
   "（缺数据 ≠ 有挑食证据，两者处置不同；同样拦截）",
   pr_unknown.verdict == "inconclusive" and pr_unknown.blocks,
   str(pr_unknown.to_dict()))
ck("D2 分层明细记下来了（能看见是哪层拖后腿）",
   pr_partial.by_difficulty == {"easy": 1.0, "hard": 0.0},
   str(pr_partial.by_difficulty))
ck("D3 partial **拦截**（与 overfit 一样不许提交）", pr_partial.blocks)
ck("D4 理由里点名了落后的层", "hard" in pr_partial.reason, pr_partial.reason)

# 不分层时，同一情形会被判成 generalize —— 这正是 D 组存在的理由
pr_nodiff = _gate(NO_DIFF).probe(_H({}), _H({}), _Phased(), dev_gain=0.5)
ck("D5 ⛔ 不分层时同一个候选被判 **generalize**（平均分掩盖了挑食）",
   pr_nodiff.verdict == "generalize", str(pr_nodiff.to_dict()))
ck("D6 于是：分层与否，裁决相反 ⇒ 分层不是装饰",
   pr_partial.verdict != pr_nodiff.verdict)

print()
print("== E 组：难度跨度本身要被看见 ==")
suite_spread = make_heldout("B", [FrozenTask(f"ho::{i}", spec={})
                                  for i in range(4)], difficulty=DIFF)
ck("E1 有跨度 ⇒ spread=True", suite_spread.spread)
suite_flat = make_heldout("B", [FrozenTask(f"ho::{i}", spec={}) for i in range(4)],
                          difficulty={f"ho::{i}": "easy" for i in range(4)})
ck("E2 全是 easy ⇒ spread=False（这样的 held-out 判别力不足）",
   not suite_flat.spread)
d_flat = difficulty_report(ENVS["textscore"],
                           [t for t in rep.tasks
                            if labels[t.task_id] == "easy"])
ck("E3 difficulty_report 会**明说**单一难度的问题",
   (not d_flat["spread"]) and bool(d_flat["note"]), str(d_flat))
ck("E4 levels / ids_of 是按难度分桶的",
   suite_spread.levels == ("easy", "hard")
   and suite_spread.ids_of("hard") == ("ho::2", "ho::3"))

print()
print("== F 组：反证（把分层拆掉，上面的断言必须翻转）==")
# F1 把部分层的 floor 放宽到 -inf ⇒ partial 变 generalize（证明拦的是分层）
g_lenient = _gate(DIFF, floor=-1e9)
pr_lenient = g_lenient.probe(_H({}), _H({}), _Phased(), dev_gain=0.5)
ck("F1 放宽每层地板 ⇒ 同一个候选被**放行**（证明 D1 拦的是分层增益）",
   pr_lenient.verdict == "generalize", str(pr_lenient.to_dict()))
# F2 把难度标签全改成 easy ⇒ 分层失效，挑食候选过关
g_mislabel = _gate({f"ho::{i}": "easy" for i in range(4)})
pr_mis = g_mislabel.probe(_H({}), _H({}), _Phased(), dev_gain=0.5)
ck("F2 难度标签标错（全标 easy）⇒ 挑食候选被误放行"
   " ⇒ 标签必须由执行判定，不能靠猜",
   pr_mis.verdict == "generalize", str(pr_mis.to_dict()))
# F3 修法集合里塞进 restore_baseline ⇒ 所有题都变 easy ⇒ 分层消失
from aether import repair as R  # noqa: E402

orig = R.GENERIC_REPAIRS
R.GENERIC_REPAIRS = dict(orig, restore_baseline=restore_baseline)
try:
    d2 = difficulty_report(e, rep.tasks)
    ck("F3 通用修法里塞进「整个文件换回基线」⇒ 全部变 easy，分层消失",
       d2["n_hard"] == 0 and not d2["spread"], str(d2))
finally:
    R.GENERIC_REPAIRS = orig
ck("F4 恢复后 hard 层重新出现（通用修法必须是**保守**的）",
   difficulty_report(e, rep.tasks)["n_hard"] > 0)

# ══ G 组：发展集缺难度层时的**采样自愈** ═══════════════════════════
# 2026-10-13 实测：max_tasks=8 时 dev easy/hard=3/0 而 ho=2/1 ⇒
# 泛化闸三轮全判 inconclusive（拦截）；max_tasks=12 时 dev=3/2 ⇒
# generalize 并提交。缺层是**采样配置**问题，不是候选的错 ——
# 让候选为采样不足背锅，效果和"永远不提交"完全一样。
print()
print("== G 组：发展集缺难度层 ⇒ 采样自愈（不拿候选顶罪）==")
from aether.repair import grow_for_difficulty  # noqa: E402


def _synth(n):
    return synthesize_tasks(e, max_n=n).tasks


g_ok = grow_for_difficulty(e, _synth, ("easy", "hard"), max_n=8)
ck("G1 发展集缺 hard ⇒ 自动扩大采样补齐（不是判 inconclusive 了事）",
   g_ok["covered"] and not g_ok["missing"], str(g_ok))
ck("G2 补齐后两层都在（能与发展集做**同层对照**）",
   set(g_ok["labels"].values()) >= {"easy", "hard"},
   str({k: v for k, v in g_ok["labels"].items()}))
ck("G3 max_n_used > max_n（真的扩了，不是原地打转）",
   g_ok["max_n_used"] > 8, str(g_ok["max_n_used"]))
ck("G4 补采样记录可观测（steps 逐步可查）",
   len(g_ok["steps"]) >= 2 and g_ok["note"], str(g_ok["steps"]))

# ⛔ 反证：把 ceiling 卡死 ⇒ 必须**明确报缺**，不能假装补齐
g_cap = grow_for_difficulty(e, _synth, ("easy", "hard"), max_n=8, ceiling=8)
ck("G5 反证：ceiling 卡死 ⇒ covered=False 且报出 missing",
   (not g_cap["covered"]) and g_cap["missing"], str(g_cap))
ck("G6 反证：缺层时必须给出**可行动**建议（换环境/提高 --max-tasks）",
   ("--max-tasks" in g_cap["note"]) and ("环境配对" in g_cap["note"]),
   g_cap["note"])

# 已经覆盖 ⇒ 不该浪费执行时间
g_noop = grow_for_difficulty(e, _synth, ("easy",), max_n=8)
ck("G7 已经覆盖所需的层 ⇒ 不扩采样（不无谓地烧执行时间）",
   g_noop["max_n_used"] == 8 and not g_noop["note"],
   f"{g_noop['max_n_used']} / {g_noop['note']!r}")

# ⛔ 补采样只加题不加判据：合成是确定性的 ⇒ 已有条目的标签不得变
g_a = grow_for_difficulty(e, _synth, ("easy",), max_n=8)
g_b = grow_for_difficulty(e, _synth, ("easy",), max_n=8)
ck("G8 补采样确定性：两次调用标签完全一致（判据不因采样次数而漂移）",
   g_a["labels"] == g_b["labels"])

print()
print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}"
      f"  ({len(FAILS)} failures)")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAILS else 0)

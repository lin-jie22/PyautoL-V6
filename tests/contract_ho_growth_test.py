#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：held-out 的**判别力**必须能自愈（r53）。

## 为什么要有这份契约

r52 修好了「层内可提升空间 < 3 ⇒ 不作反证」，顺手暴露出下一个天花板：
当 held-out **每层**都没空间时，闸判 `inconclusive`，并在理由里写

        出路：补 held-out 的题数，不是放宽判据

⛔ 但**系统自己没有这个出口**。实测（4 环境 / depth1 / 6 轮）：

        轮2 有 +0.4 的发展集增益可用（fix_literals: dev 0.6 → 1.0）
        轮2/3/4 三轮**逐字重复**地被同一条理由拦下

三轮里系统做的事完全一样（提名同 3 个候选、跑同一套评估、拿到同一个
裁决），唯一变化是账本多了三条相同的事件。这不是"闸在工作"，是
**闸给了正确的建议却没人去执行**。

## 修的是什么

`ensure_headroom()`：每轮用**当前基线**体检 held-out 每层还剩几条能涨
的题，不足 `min_level_headroom` 就补采样 —— 只补「基线也做不对」的题
⇒ 与候选无关 ⇒ 不给任何候选优待（H1 一条没动）。

⛔ 为什么必须在**循环内**每轮做，不能建卷时做一次：headroom 的定义是
   「基线还没修好几条」⇒ 它随能力提升而**缩小**。建卷时用种子基线量
   ⇒ 基线最弱 ⇒ headroom 最大 ⇒ 永远达标 ⇒ 这个读数毫无意义。

## 决定性实验（同 seed / 同初始状态，唯一变量 = --ho-grow）

    开   2 代   reward 0 → 0.6 → 1.0
    关   1 代   reward 0 → 0.6 → 0.6 → 0.6（后三轮逐字重复）

## 这组契约守什么

  K1 组：默认**关** ⇒ 与历史逐字同口径（不注入 grow_fn ⇒ 绝不改考卷）
  K2 组：headroom 是**结构化字段**，不是只活在文案里（改措辞不得失真）
  K3 组：体检与裁决**同源**（同一份 `reward < 1.0` 判据）
  K4 组：补题**不给候选优待**（只看基线；不与发展集重叠 H2；不入 dev）
  K5 组：反证组 —— 补采样抛异常 / 合成空 / 到顶补不齐，三种失败都必须可见
  K6 组：真实环境上开它**确实**能多吃一代（有牙性，不是恒等断言）

用法：
    python tests/contract_ho_growth_test.py
"""
import sys
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} "
          f"{detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether.generalization import (GeneralizationGate, HeldOutSuite,
                                       ProbeResult, make_heldout)
    from aether.genome import Harness
    from aether.nongression import FrozenTask

    print("K1 组：默认关闭 ⇒ 与历史逐字同口径")
    from aether.evolution import EvolutionConfig
    cfg = EvolutionConfig()
    ck("K1.1 ⛔ EvolutionConfig.ho_grow 默认 False（默认不开新口径）",
       cfg.ho_grow is False, repr(cfg.ho_grow))
    ck("K1.2 补采样封顶默认非空（合成要真跑变异体 ⇒ 不许无上限）",
       cfg.ho_grow_max_n > 0 and cfg.ho_grow_max_tasks > 0)
    import inspect
    sig = inspect.signature(GeneralizationGate.__init__)
    ck("K1.3 闸的 grow_fn / difficulty_fn 默认 None（不注入 ⇒ 不自愈）",
       sig.parameters["grow_fn"].default is None
       and sig.parameters["difficulty_fn"].default is None)
    cli_src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("K1.4 CLI 默认关：grow_fn 由 `_ho_grow` 三元决定，不无条件注入",
       "grow_fn=(_ho_synth if _ho_grow else None)" in cli_src)
    ck("K1.5 ⛔ `ho_grow` 进 run_config（换的是考卷 ⇒ 必须在账本里可查）",
       '"ho_grow": bool(_ho_grow)' in cli_src)

    print("\nK2 组：headroom 是结构化字段，不是只活在文案里")
    pr = ProbeResult(verdict="inconclusive", headroom={"easy": 0, "hard": 2},
                     thin=("easy",), reason="…可提升空间 0 条…")
    d = pr.to_dict()
    ck("K2.1 `headroom` 进 to_dict（下游不许靠解析 reason 拿它）",
       d.get("headroom") == {"easy": 0, "hard": 2}, repr(d.get("headroom")))
    ck("K2.2 `thin` 进 to_dict（样本不足的层必须可点名）",
       d.get("thin") == ["easy"], repr(d.get("thin")))
    ck("K2.3 ⛔ `thin` 与 `out_of_reach` 是两个字段（出路不同：补题 vs 到顶）",
       "thin" in d and "out_of_reach" in d)

    print("\nK3 组：体检与裁决同源（同一份 reward<1.0 判据）")
    gen_src = (ROOT / "aether" / "generalization.py").read_text(encoding="utf-8")
    ck("K3.1 闸内的 headroom 用 `by_id.get(t, 0.0) < 1.0`",
       "if base_by_id.get(t, 0.0) < 1.0" in gen_src)
    ck("K3.2 体检用同一个判据（不另写一份 ⇒ 不会漂）",
       "if by_id.get(t, 0.0) < 1.0" in gen_src)
    #: ⛔ 用**逐字片段**判定，不用全文件计数：计数会被"别处又出现一次
    #:    1.0"打破 ⇒ 要么假绿要么假红（第 48 条：先问这个闸在数什么）。
    ck("K3.3 ⛔ 补题筛选用**同一个**判据（`reward >= 1.0 ⇒ 不要`）",
       '(getattr(v, "reward", 0.0) or 0.0) >= 1.0' in gen_src)

    print("\nK4 组：补题不给候选优待")
    ck("K4.1 ⛔ 只收**当前基线做不对**的题（判据里跑的是 runner(h, …)，"
       "h 是基线不是候选）", "v = runner(h, dict(t.spec))" in gen_src)
    ck("K4.2 ⛔ 与发展集重叠的题直接跳过（H2 不靠调用方自觉）",
       "in set(self.dev_task_ids)" in gen_src)
    ck("K4.3 ⛔ 只补**缺空间的层**（不顺便加别的层 ⇒ 不改变难度分布）",
       "if lv not in deficit:" in gen_src)
    evo_src = (ROOT / "aether" / "evolution.py").read_text(encoding="utf-8")
    ck("K4.4 ⛔ 体检在**提议之前**（此时还没有候选 ⇒ '给谁优待'不可能）",
       evo_src.index("ensure_headroom") < evo_src.index("mine_weaknesses(runs)"))
    ck("K4.5 每轮补题都进账本（`heldout.grow`，不静默换考卷）",
       '"heldout.grow"' in evo_src)

    print("\nK5 组：反证 —— 三种失败都必须可见（不许静默）")
    # ── 造一套最小 held-out：3 条 easy + 3 条 hard ──────────────
    def mk(task_id: str) -> FrozenTask:
        return FrozenTask(task_id=task_id,
                          spec={"env_id": "ho", "broken_source": "x = 1\n",
                                "topic": "ho", "task_class": "repair"})

    ids = [f"ho::{i}" for i in range(6)]
    tasks = [mk(i) for i in ids]
    labels = {ids[0]: "easy", ids[1]: "easy", ids[2]: "easy",
              ids[3]: "hard", ids[4]: "hard", ids[5]: "hard"}
    #: runner：id 含 3/4/5 的（hard）**基线做不对** ⇒ headroom easy=0 hard=3
    def runner(h, spec):
        class V:
            reward = 0.0 if str(spec.get("broken_source")).endswith("#f") else 1.0
            passed = reward >= 1.0
            error = ""
            evidence = {}
        return V()

    #: 让 hard 三条做不对：改 spec（同源：spec 是 runner 的入参）
    for i in (3, 4, 5):
        tasks[i] = FrozenTask(
            task_id=ids[i],
            spec={"env_id": "ho", "broken_source": "x = 1\n#f",
                  "topic": "ho", "task_class": "repair"})

    def build(grow_fn, difficulty_fn, **kw):
        return GeneralizationGate(
            make_heldout("ho", list(tasks), difficulty=labels),
            dev_task_ids=[], grow_fn=grow_fn, difficulty_fn=difficulty_fn,
            **kw)

    h0 = Harness()
    g_none = build(None, None)
    r_none = g_none.ensure_headroom(h0, runner, round_no=1)
    ck("K5.1 未注入 ⇒ enabled=False 且**不改** suite",
       r_none.get("enabled") is False and g_none.suite.n_tasks == 6,
       repr(r_none))

    def boom(n):
        raise RuntimeError("合成炸了")

    g_boom = build(boom, lambda t: {})
    r_boom = g_boom.ensure_headroom(h0, runner, round_no=1)
    ck("K5.2 ⛔ 补采样抛异常 ⇒ 记 error 且不崩（不许静默跳过）",
       "RuntimeError" in str(r_boom.get("steps") or r_boom), repr(r_boom))
    ck("K5.3 抛异常时 suite 不变（坏了就停手，不半改）",
       g_boom.suite.n_tasks == 6)

    g_empty = build(lambda n: [], lambda t: {})
    r_empty = g_empty.ensure_headroom(h0, runner, round_no=1)
    ck("K5.4 合成 yield=0 ⇒ `missing` 如实报缺失层（不假装补齐）",
       r_empty.get("missing") == ["easy"], repr(r_empty))

    #: ── 正向：能补 ⇒ 真的补进去，且只补缺的那一层 ──────────────
    made = {"n": 0}

    def grow_ok(n):
        made["n"] += 1
        return [FrozenTask(task_id=f"new::{n}::{k}",
                           spec={"env_id": "ho", "broken_source": "y = 2\n#f",
                                 "topic": "ho", "task_class": "repair"})
                for k in range(3)]

    g_ok = build(grow_ok, lambda t: {x.task_id: "easy" for x in t})
    n0 = g_ok.suite.n_tasks
    r_ok = g_ok.ensure_headroom(h0, runner, round_no=1)
    ck("K5.5 正向：easy 层缺空间 ⇒ 真的补进去（有牙，不是恒等）",
       g_ok.suite.n_tasks > n0, repr(r_ok))
    ck("K5.6 补完 easy 层空间达标（体检读数跟着变）",
       (r_ok.get("headroom_after") or {}).get("easy", 0) >= 3,
       repr(r_ok.get("headroom_after")))
    ck("K5.7 ⛔ 只补 easy，hard 题数不变（不顺便动别的层）",
       len(g_ok.suite.ids_of("hard")) == 3,
       str(len(g_ok.suite.ids_of("hard"))))
    ck("K5.8 补题不打乱原有难度标签（旧题的层不变）",
       g_ok.suite.difficulty_map.get(ids[3]) == "hard")

    #: ── 反证：补题**不许**与发展集重叠 ─────────────────────────
    #: ⛔ fixture 的 id 必须与 `grow_ok` 产出的 id **逐字对得上**，
    #:    否则这条断言是"补进去了"而不是"被拒了"（假绿）。`grow_ok` 第一
    #:    轮 n 从 6 翻倍到 12 ⇒ id 形如 `new::12::k`。先跑一遍拿到真 id。
    _probe_ids: list = []
    g_ids = GeneralizationGate(
        make_heldout("ho", list(tasks), difficulty=labels),
        dev_task_ids=[], grow_fn=grow_ok,
        difficulty_fn=lambda t: {x.task_id: "easy" for x in t})
    g_ids.ensure_headroom(Harness(), runner, round_no=1)
    _probe_ids = [t for t in g_ids.suite.task_ids if t.startswith("new::")]
    ck("K5.9a 反证组 fixture 有效（先确认这批 id 本来是能补进去的）",
       len(_probe_ids) >= 3, str(_probe_ids[:5]))
    #: ⛔ 补采样必须**每次返回同一批 id**：否则重试会换一批新 id ⇒
    #:    "被拒"看起来像"压根没产出"（与第 44 条同形：量具自己撒谎）。
    grow_fixed = (lambda n: [FrozenTask(
        task_id=f"devdup::{k}",
        spec={"env_id": "ho", "broken_source": "y = 2\n#f",
              "topic": "ho", "task_class": "repair"}) for k in range(3)])
    g_dup = GeneralizationGate(
        make_heldout("ho", list(tasks), difficulty=labels),
        dev_task_ids=[f"devdup::{k}" for k in range(3)],
        grow_fn=grow_fixed,
        difficulty_fn=lambda t: {x.task_id: "easy" for x in t})
    n1 = g_dup.suite.n_tasks
    g_dup.ensure_headroom(h0, runner, round_no=1)
    ck("K5.9 ⛔ 与发展集同 id 的新题被拒（H2：重叠 ⇒ 闸自我取消）",
       g_dup.suite.n_tasks == n1
       and not ({"devdup::0", "devdup::1", "devdup::2"}
                & set(g_dup.suite.task_ids)),
       f"n={g_dup.suite.n_tasks} ids={list(g_dup.suite.task_ids)[-3:]}")

    print("\nK6 组：有牙性 —— 同一道闸、同一个候选，补题前后裁决必须变")
    #: ⛔ 恒等断言是最危险的假绿灯（第 8 条）。这里用**同一套闸 + 同一个
    #:    候选**跑两次，唯一变量是"卷子补没补" ⇒ 裁决必须从"拦"翻成"放行"，
    #:    否则这条自愈机制就是空转的，而所有契约看起来还是绿的。
    #:
    #:    场景（与 r53 实测形态一致）：held-out 的 easy 层基线已全对
    #:    （空间 0）、hard 层还剩 2 条（< 门槛 3）⇒ 每层都不足 ⇒ 拦。
    def mk2(tid: str, need: str) -> FrozenTask:
        return FrozenTask(task_id=tid,
                          spec={"env_id": "ho2", "broken_source": need,
                                "topic": "ho2", "task_class": "repair"})

    def runner2(h, spec):
        need = str(spec.get("broken_source") or "")
        ok = (need == "") or (
            need in [x for x in str(
                (h.routing or {}).get("repair", "")).split("|") if x])

        class V:
            reward = 1.0 if ok else 0.0
            passed = ok
            error = ""
            evidence = {}
        return V()

    t_easy = [mk2("k6::e0", ""), mk2("k6::e1", "")]
    t_hard = [mk2("k6::h0", "fix_literals"), mk2("k6::h1", "fix_literals")]
    lab2 = {"k6::e0": "easy", "k6::e1": "easy",
            "k6::h0": "hard", "k6::h1": "hard"}
    h_base = Harness(meta_rules="k6")
    h_cand = Harness(meta_rules="k6", skills={"fix_literals": "c"},
                     routing={"repair": "fix_literals"})

    def probe_now(gate):
        return gate.probe(h_base, h_cand, runner2, dev_gain=0.4,
                          dev_by_difficulty={"easy": 0.0, "hard": 0.5})

    g6 = GeneralizationGate(
        make_heldout("ho2", list(t_easy) + list(t_hard), difficulty=lab2),
        dev_task_ids=[], min_level_headroom=3)
    p_before = probe_now(g6)
    print(f"     补题前：verdict={p_before.verdict} "
          f"headroom={p_before.headroom} n={p_before.n_tasks}")
    ck("K6.1 ⛔ 补题前：每层空间都不足 ⇒ 拦（inconclusive，且 blocks）",
       p_before.verdict == "inconclusive" and p_before.blocks,
       f"{p_before.verdict} / {p_before.reason[:60]}")
    ck("K6.2 ⛔ 补题前 headroom **必须**是结构化字段（不许只写在文案里）",
       p_before.headroom == {"easy": 0, "hard": 2}, repr(p_before.headroom))

    #: 补 3 条 easy 层、且**基线做不对**的题 ⇒ easy 空间 0 → 3
    def grow6(n):
        return [mk2(f"k6::new{i}", "fix_other") for i in range(3)]

    g6b = GeneralizationGate(
        make_heldout("ho2", list(t_easy) + list(t_hard), difficulty=lab2),
        dev_task_ids=[], min_level_headroom=3,
        grow_fn=grow6,
        difficulty_fn=lambda ts: {x.task_id: "easy" for x in ts})
    rep6 = g6b.ensure_headroom(h_base, runner2, round_no=1)
    p_after = probe_now(g6b)
    print(f"     补题后：verdict={p_after.verdict} "
          f"headroom={p_after.headroom} n={p_after.n_tasks}"
          f" 补了 {rep6.get('grew')} 条")
    ck("K6.3 ⛔ 有牙：补题后 easy 层空间达标（0 → ≥3）",
       (rep6.get("headroom_after") or {}).get("easy", 0) >= 3,
       repr(rep6.get("headroom_after")))
    ck("K6.4 ⛔ 有牙：同一个候选由**拦**翻成**放行**（不是恒等断言）",
       p_after.verdict == "generalize" and not p_after.blocks,
       f"{p_after.verdict} / {p_after.reason[:80]}")
    ck("K6.5 ⛔ 反证：翻案不是靠放宽判据 —— 判据字段一模一样，"
       "只是 easy 层从 thin 变成有样本",
       (p_after.thin == () or "easy" not in p_after.thin),
       repr(p_after.thin))
    print("     端到端实测依据：scripts/probe_candidate_squeeze.py "
          "--ho-grow --compare（唯一变量 = 该开关）"
          "⇒ 提交代数 1 → 2，reward 0.6 → 1.0")

    print(f"\n{'=' * 60}")
    print(f"结果：{len(FAILS)} 条失败" if FAILS else "结果：全部通过")
    for f in FAILS:
        print(f"  ✗ {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：held-out 补题撞上**题源硬顶**时，必须换采样口径而不是静默失败（r55）。

## 起因（本轮取证，全部来自 `scripts/probe_headroom_source.py`）

r53 给了「补 held-out 的题数」这个出口，但它在**单采样口径**下会撞墙：

    A 段  max_n = 16/32/64/128/256 ⇒ 产出恒定 **26** 条（后三档新增 0）
    E 段  depth = 2/3/4            ⇒ 产出恒定 **26** 条（新增 0）

⇒ r54 实测的「补了 0 条」不是筛子太严，是**题源到顶**：

    F 段  建卷 13 条，headroom={'easy': 0, 'hard': 6}，missing=['easy']
          steps = [max_n=26 n_added=0, max_n=52 n_added=0, max_n=64 n_added=0]

而更糟的是**归因不可分辨**：题源里明明还有 13 条建卷外的题（其中 3 条
正是"基线做不对"、本可以补进 headroom 的），但它们的层当时不在 `deficit`
里 ⇒ 被 `lv not in deficit` 拒掉。此时 `grew=0` 与「题源真的没了」在
输出里**完全同形**（第 10 条同形病）。

## 出路：换采样口径（不是加大采样量）

    G 段  均衡(balanced=True)  产出 26 条 ⇒ 与建卷去重新增 0
          顺序(balanced=False) 产出 26 条 ⇒ 与建卷去重**新增 26**（重叠 0）

同一环境、同一 depth/kinds，只换**取点顺序** ⇒ 得到几乎不重叠的另一批题。
这与第 29 条那个「`which` 全局序号 ⇒ 换顺序换一批题」是同一个结构。

⛔ 刻意**不换环境**：held-out 的"另一个环境"是这套闸的定义（H1），
   换环境等于换尺子的单位；换取点顺序不换单位，语义不变。

## 顺带钉死的一个结构事实（H3）

`easy` 层的定义是「`GENERIC_REPAIRS` 里某个修法能修好」，而
`GENERIC_REPAIRS` 实测**只有一个成员** `revert_control_flow`。
基线一装上 `fix_control_flow`（轮 1 就装了）⇒ easy 层**结构性**全对：

    D 段  easy 题 11 条：单修法修好 11、轮5链修好 11、**不一致 0 条**

⇒ 该层的 headroom 不是"样本不足"，是"定义上不可能再有空间"。
   所以补题对它永远无效，而 `all_thin` 仍把它算进分母 —— 这解释了
   r54 轮 5/6 那两条 inconclusive 为什么逐字重复。

## 这组契约守什么

  M1 组：默认 **1 档口径** ⇒ 与 r53/r54 逐字同口径（第 40 条）
  M2 组：换口径**有牙** —— 主口径补不动时第二口径必须真能补进去（含反证）
  M3 组：`grew=0` **可归因** —— 题源到顶 vs 有题但层已达标，必须可分辨
  M4 组：换口径**不给候选优待**（H1/H2 一条没动）
  M5 组：源码级反证（拿掉 variant 循环 ⇒ M2 转红）
  M6 组：真实环境上两种口径确实给出**不重叠**的题（M2 的前提不是假的）

用法：
    python tests/contract_headroom_source_test.py
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
    import inspect

    from aether.generalization import GeneralizationGate, make_heldout
    from aether.genome import Harness
    from aether.nongression import FrozenTask

    gen_src = (ROOT / "aether" / "generalization.py").read_text(encoding="utf-8")
    cli_src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")

    # ── fixture：3 条 easy（基线做对）+ 3 条 hard（基线做不对）─────
    #: ⇒ headroom = {'easy': 0, 'hard': 3} ⇒ deficit = ['easy']
    def mk(task_id: str, broken: bool = False) -> FrozenTask:
        return FrozenTask(
            task_id=task_id,
            spec={"env_id": "ho",
                  "broken_source": "x = 1\n#f" if broken else "x = 1\n",
                  "topic": "ho", "task_class": "repair"})

    ids = [f"ho::{i}" for i in range(6)]
    tasks = ([mk(i) for i in ids[:3]]
             + [mk(i, broken=True) for i in ids[3:]])
    labels = dict(zip(ids, ["easy"] * 3 + ["hard"] * 3))

    def runner(h, spec):
        class V:
            reward = 0.0 if str(spec.get("broken_source")).endswith("#f") else 1.0
            passed = reward >= 1.0
            error = ""
            evidence = {}
        return V()

    h0 = Harness()

    def build(grow_fn, difficulty_fn, variants=(), **kw):
        return GeneralizationGate(
            make_heldout("ho", list(tasks), difficulty=labels),
            dev_task_ids=[], grow_fn=grow_fn, difficulty_fn=difficulty_fn,
            grow_variant_fns= variants, **kw)

    #: difficulty_fn 按 id 前缀决定层，便于各组精确控制
    def lab_by(prefix_lv: dict):
        def _fn(pending):
            out = {}
            for t in pending:
                for p, lv in prefix_lv.items():
                    if t.task_id.startswith(p):
                        out[t.task_id] = lv
                        break
            return out
        return _fn

    print("M1 组：默认 1 档口径 ⇒ 与 r53/r54 逐字同口径")
    sig = inspect.signature(GeneralizationGate.__init__)
    ck("M1.1 ⛔ `grow_variant_fns` 默认 None（不注入 ⇒ 不换口径）",
       sig.parameters["grow_variant_fns"].default is None,
       repr(sig.parameters["grow_variant_fns"].default))
    g_def = GeneralizationGate(
        make_heldout("ho", list(tasks), difficulty=labels))
    ck("M1.2 默认构造下 `grow_variant_fns == ()`",
       tuple(g_def.grow_variant_fns) == ())
    ck("M1.3 CLI `--ho-grow-variants` 默认 '1'（不无条件开启新口径）",
       '"--ho-grow-variants", default="1"' in cli_src)
    ck("M1.4 CLI 由 `_ho_grow_variants >= 2` 三元决定（默认不注入备选口径）",
       "grow_variant_fns=((_ho_synth_alt,) if (_ho_grow and" in cli_src)
    ck("M1.5 ⛔ 新口径进 run_config（换的是考卷 ⇒ 账本里必须可查）",
       '"ho_grow_variants": int(_ho_grow_variants)' in cli_src)

    print("\nM2 组：换口径有牙 —— 主口径补不动时，第二口径必须真能补进去")
    #: 主口径：题源到顶（恒定返回已在卷子里的题 ⇒ `cand` 为空）
    def grow_seen(n):
        return [mk(ids[0])]

    g_one = build(grow_seen, lab_by({"new::": "easy"}))
    r_one = g_one.ensure_headroom(h0, runner, round_no=1)
    ck("M2.1 前提成立：只给主口径且它撞顶 ⇒ grew=0（对照组的基线）",
       r_one.get("grew") == 0 and g_one.suite.n_tasks == 6, repr(r_one))
    ck("M2.2 只给主口径 ⇒ `n_variants == 1`（口径数如实记账）",
       r_one.get("n_variants") == 1, repr(r_one.get("n_variants")))

    def grow_alt(n):
        return [FrozenTask(task_id=f"new::{k}",
                           spec={"env_id": "ho", "broken_source": "y = 2\n#f",
                                 "topic": "ho", "task_class": "repair"})
                for k in range(3)]

    g_two = build(grow_seen, lab_by({"new::": "easy"}), variants=(grow_alt,))
    r_two = g_two.ensure_headroom(h0, runner, round_no=1)
    ck("M2.3 ⛔ 有牙：换了口径之后**真补进去了**（不是恒等断言）",
       r_two.get("grew", 0) > 0, repr(r_two))
    ck("M2.4 `n_variants == 2`（用了几个口径必须可查）",
       r_two.get("n_variants") == 2, repr(r_two.get("n_variants")))
    ck("M2.5 steps 里带 `variant` 下标（每档口径各跑了几轮可分辨）",
       any(s.get("variant") == 1 for s in (r_two.get("steps") or [])),
       repr(r_two.get("steps")))
    ck("M2.6 补完 easy 层达标（换口径是为了补空间，不是为了加题）",
       (r_two.get("headroom_after") or {}).get("easy", 0) >= 3,
       repr(r_two.get("headroom_after")))

    #: ⛔ 反证：换口径**不是**无条件收题 —— 第二口径给的题若不在
    #:    缺空间的层里，照样一粒不收（否则换口径就成了绕开判据的后门）。
    g_wronglv = build(grow_seen, lab_by({"new::": "hard"}),
                      variants=(grow_alt,))
    r_wronglv = g_wronglv.ensure_headroom(h0, runner, round_no=1)
    ck("M2.7 ⛔ 反证：第二口径的题不在 deficit 层 ⇒ **不收**"
       "（换口径不绕过 `lv in deficit`）",
       r_wronglv.get("grew") == 0, repr(r_wronglv))
    ck("M2.8 反证有效（这批题本身是能被第一口径那一关放行的）",
       (r_wronglv.get("outside_deficit") or 0) > 0,
       repr(r_wronglv.get("outside_deficit")))

    print("\nM3 组：`grew=0` 必须可归因（第 10 条同形病）")
    ck("M3.1 `outside_deficit` 进了 rep（不许只活在 note 文案里）",
       "outside_deficit" in r_two, str(sorted(r_two)))
    ck("M3.2 ⛔ 有题但层已达标 ⇒ note 指向**换采样口径**（不是泛泛的到顶）",
       "换采样口径" in str(r_wronglv.get("note", "")),
       repr(r_wronglv.get("note")))
    ck("M3.3 ⛔ 题源到顶 ⇒ note 指向**单口径题源已到顶**",
       "题源已到顶" in str(r_one.get("note", "")),
       repr(r_one.get("note")))
    ck("M3.4 ⛔ 反证：两种失败的原因**必须不同**（否则仍不可分辨）",
       r_one.get("note") != r_wronglv.get("note"))
    ck("M3.5 题源到顶时 `outside_deficit == 0`（没有「被层挡掉的题」）",
       r_one.get("outside_deficit") == 0, repr(r_one.get("outside_deficit")))

    print("\nM4 组：换口径不给候选优待（H1/H2 一条没动）")
    #: 第二口径给的题**基线修得好** ⇒ 照样不收（判据仍是 runner 的 reward）
    def grow_alt_ok(n):
        return [FrozenTask(task_id=f"ok::{k}",
                           spec={"env_id": "ho", "broken_source": "y = 2\n",
                                 "topic": "ho", "task_class": "repair"})
                for k in range(3)]

    g_ok = build(grow_seen, lab_by({"ok::": "easy"}), variants=(grow_alt_ok,))
    r_ok = g_ok.ensure_headroom(h0, runner, round_no=1)
    ck("M4.1 ⛔ 反证：第二口径的题基线做得对 ⇒ 不收"
       "（换口径不绕过 `reward < 1.0` 判据）",
       r_ok.get("grew") == 0, repr(r_ok))

    g_dup = GeneralizationGate(
        make_heldout("ho", list(tasks), difficulty=labels),
        dev_task_ids=[f"new::{k}" for k in range(3)],
        grow_fn=grow_seen, difficulty_fn=lab_by({"new::": "easy"}),
        grow_variant_fns=(grow_alt,))
    n_dup = g_dup.suite.n_tasks
    g_dup.ensure_headroom(h0, runner, round_no=1)
    ck("M4.2 ⛔ 反证：第二口径的题与发展集同 id ⇒ 不收（H2 不靠自觉）",
       g_dup.suite.n_tasks == n_dup, str(g_dup.suite.n_tasks))

    print("\nM5 组：源码级反证（拿掉 variant 循环 ⇒ M2 转红）")
    ck("M5.1 `ensure_headroom` 真的在遍历口径序列",
       "variants = (self.grow_fn,) + tuple(self.grow_variant_fns)" in gen_src)
    ck("M5.2 每个口径**重新起步**（`n` 不继承上一口径的进度）",
       "n = max(len(tasks), 4)" in gen_src)
    ck("M5.3 ⛔ 换口径的合成函数由 CLI 注入（闸不 import environment）",
       "def _ho_synth_alt(n: int):" in cli_src
       and "def _ho_synth_with(balanced: bool):" in cli_src)

    print("\nM6 组：真实环境上两种口径确实给出不重叠的题（M2 的前提）")
    #: ⛔ M2/M3 的 fixture 是我造的；若真实合成器在两种口径下给出**同一批
    #:    题**，那整套"换口径"就是空转。这一组用真实环境钉死前提。
    from aether.cli import DEFAULT_ENVS, TASK_CLASS          # noqa: F401
    from aether.environment import Environment, synthesize_tasks
    env = Environment.from_dir(ROOT / "tasks" / "eventflow")
    KINDS = ("compare", "binop", "constant", "boolop", "unaryop")
    a = {t.task_id for t in synthesize_tasks(
        env, max_n=16, depth=2, kinds=KINDS, balanced=True).tasks}
    b = {t.task_id for t in synthesize_tasks(
        env, max_n=16, depth=2, kinds=KINDS, balanced=False).tasks}
    inter = len(a & b)
    union = len(a | b)
    print(f"      均衡 {len(a)} 条 / 顺序 {len(b)} 条"
          f" ⇒ 重叠 {inter} 条，并集 {union} 条")
    ck("M6.1 两种口径都真的产出了题（量具本身没坏）",
       len(a) > 0 and len(b) > 0, f"{len(a)}/{len(b)}")
    ck("M6.2 ⛔ 重叠率 < 50% ⇒ 换口径确实能拿到**另一批**题",
       union > 0 and inter * 2 < union, f"{inter}/{union}")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"结果：{len(FAILS)} 条未通过 ⇒ {FAILS}")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**拦截必须可复核** —— overfit 裁决不许在零信息下判决（r54）。

## 起因：一次「差点改错判据」的取证

全口径实测（14 环境 / depth2 / balanced / ho-grow / 全 5 维 / 6 轮）里，
轮 5 出现一次 `overfit` 拦截，它的裁决读数是：

    轮5 overfit  ho={'easy': 0.0, 'hard': 0.0}
                 dev={}                    ← 空
                 headroom={}               ← 空（体检实测是 {'easy':6,'hard':0}）

⛔ 而**同一个候选**如果 ho 总体增益是 +0.01 而不是 0.00，就会走
`partial` 分支，那份裁决里 `ho` / `dev` / `headroom` / `thin` **四个字段全在**。

也就是说：判据在临界点**不连续** —— 差 0.01 的浮点，决定了这个候选
是享受完整同层对照，还是被零信息判决为「讨好判据」。

## 真根因：不是判据错，是**观测被丢弃**

`evolution.py` 明明把 `dev_by_difficulty` 一路传进了闸：

    pr = self.heldout.probe(h, new_h_probe, self.runner,
                            dev_gain=..., dev_by_difficulty=cand.dev_by_difficulty)

而 `overfit` 分支构造 `ProbeResult` 时，只带了 `by_difficulty`，
**`headroom` / `thin` / `dev_by_difficulty` 一个都没填**。

## ⛔⛔ 补齐观测后，第一件事是**证伪自己的假设**

我原本准备改判据（把「ho 该层已饱和」的情况从 overfit 降级为
inconclusive）。补齐字段后重跑，轮 5 的真实读数是：

    dev={'hard': 0.2222, 'easy': 1.0}
    headroom={'easy': 6, 'hard': 0}

⇒ 发展集 easy 层**涨满 1.0**，held-out easy 层**有 6 条可提升空间却纹丝
不动** ⇒ 确凿的挑食证据 ⇒ **闸判对了**，`patch:textscore` 是定点技能。

⇒ **判据一个字都不该改**（改了就是把真挑食放过，第 33/34 条禁止）。

这次交付的价值因此不是"闸更严/更松"，而是：**从这个版本起，"闸拦得
对不对"第一次可以被复核**。在此之前，这个问题的答案是"没有人能回答"。

## 这组契约守什么

  L1 组：overfit 裁决必须带**完整分层读数**（可复核性，不是可解释性）
  L2 组：`saturated`（完全饱和）与 `thin`（样本不足）是**两件事**，出路不同
  L3 组：分层诊断在**所有分支之前**算好，两个分支**不各写一份**（第 13 条）
  L4 组：判据**一个字没改** —— 真挑食仍然被拦（不许借"补观测"放宽）
  L5 组：反证 —— 事故版必须转红；且先证明 fixture 本来能判到目标分支
  L6 组：与 partial 分支**对拍**（同源 ⇒ 同一套 headroom 读数）

⛔ 与 r53 的 K2 组的分工：
   K2 守「headroom 必须是结构化字段，不是只活在 reason 文案里」；
   本组守「**每一个裁决分支**都得带上它，不能只有放行分支记得填」
   —— 与 `discriminative` 曾经踩过的坑完全同形（实测：放行分支填 False、
   overfit 分支是 None ⇒ 观测侧把"拦了但本就无判别力"读成"没测过"）。

用法：
    python tests/contract_overfit_provenance_test.py
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
    from aether.generalization import GeneralizationGate, make_heldout
    from aether.nongression import FrozenTask

    # ── fixture：三层 held-out，每层 3 条 ────────────────────────
    #:  ⛔ 三层刻意设计成**三种可提升空间**，这样 saturated / thin /
    #:     有空间 三种情况在同一份裁决里同时出现：
    #:       easy   基线全红        ⇒ headroom=3  ⇒ 有空间（涨不了 = 真挑食）
    #:       hard   基线红 1 条     ⇒ headroom=1  ⇒ thin（样本不足）
    #:       expert 基线全绿        ⇒ headroom=0  ⇒ saturated（必然测不出）
    LEVELS = {}
    for _i in range(3):
        LEVELS[f"ho::e{_i}"] = "easy"
        LEVELS[f"ho::h{_i}"] = "hard"
        LEVELS[f"ho::x{_i}"] = "expert"

    _ORDER = ([f"ho::e{i}" for i in range(3)]
              + [f"ho::h{i}" for i in range(3)]
              + [f"ho::x{i}" for i in range(3)])

    def _mk(tid: str) -> FrozenTask:
        return FrozenTask(tid, spec={"env_id": "ho", "i": tid})

    tasks = [_mk(t) for t in _ORDER]

    class _Runner:
        """前 9 次（base）按基线得分，后 9 次（cand）按候选得分。

        ⛔ 候选与基线**逐条相同** ⇒ 三层增益全 0 ⇒ ho_gain = 0 ⇒
           必然落到 `overfit` 分支（这是 L5 要反证的那个前提）。
        """

        def __init__(self):
            self.n = 0

        def __call__(self, h, spec):
            i = str(spec.get("i", ""))
            self.n += 1
            if i.startswith("ho::e"):
                return _sc(i, 0.0)          # easy：base 与 cand 都红
            if i.startswith("ho::h"):
                # hard：第 0 条红、其余绿 ⇒ headroom=1
                return _sc(i, 0.0 if i.endswith("h0") else 1.0)
            return _sc(i, 1.0)              # expert：全绿 ⇒ headroom=0

    def _gate(**kw):
        suite = make_heldout("ho", tasks, difficulty=LEVELS)
        return GeneralizationGate(suite, dev_task_ids=["dev::1"],
                                  heldout_floor=0.0, base_at_ceiling=None,
                                  **kw)

    print("L1 组：overfit 裁决必须带完整分层读数（可复核性）")
    r1 = _Runner()
    #: ⛔ dev 分层里 **easy 涨满**：这就是"确凿挑食证据"，与轮 5 实测同形。
    pr = _gate().probe(None, None, r1, dev_gain=0.5,
                       dev_by_difficulty={"easy": 1.0, "hard": 0.0,
                                          "expert": 0.0})
    d = pr.to_dict()
    ck("L1.0 ⛔ 前提：这一发**确实**进了 overfit 分支（否则后面全成恒真断言）",
       pr.verdict == "overfit", f"verdict={pr.verdict} reason={pr.reason}")
    ck("L1.1 `by_difficulty` 有值（三层增益都在）",
       d.get("by_difficulty") == {"easy": 0.0, "hard": 0.0, "expert": 0.0},
       repr(d.get("by_difficulty")))
    ck("L1.2 ⛔ `headroom` **不许为空**（此前是 {}，闸拦得对不对无从复核）",
       d.get("headroom") == {"easy": 3, "hard": 1, "expert": 0},
       repr(d.get("headroom")))
    ck("L1.3 ⛔ `dev_by_difficulty` 不许为空（进化循环传进来的不许丢）",
       d.get("dev_by_difficulty") == {"easy": 1.0, "hard": 0.0,
                                      "expert": 0.0},
       repr(d.get("dev_by_difficulty")))
    ck("L1.4 `thin` 点名样本不足的层（hard 只有 1 条可提升）",
       "hard" in (d.get("thin") or []), repr(d.get("thin")))
    ck("L1.5 `saturated` 点名完全饱和的层（expert 一条都涨不了）",
       d.get("saturated") == ["expert"], repr(d.get("saturated")))
    ck("L1.6 仍然**拦截**（补观测 ≠ 放宽）", pr.blocks, str(d))

    print("\nL2 组：saturated 与 thin 是两件事（出路不同，不许合并）")
    ck("L2.1 saturated ⊆ thin（饱和是样本不足的极端情形，故处置相同）",
       set(d.get("saturated") or []) <= set(d.get("thin") or []),
       f"{d.get('saturated')} ⊄ {d.get('thin')}")
    ck("L2.2 ⛔ 但它们是**两个字段**（补题 vs 换更难的题，出路不同）",
       "saturated" in d and "thin" in d
       and d["saturated"] != d["thin"],
       f"saturated={d.get('saturated')} thin={d.get('thin')}")
    #: ⛔ 判据独立性：`thin` 由 `min_level_headroom` 决定，`saturated` 不由它
    #:    决定（headroom==0 是绝对事实）。⇒ 改门槛只能改 thin，改不动 saturated。
    r2 = _Runner()
    pr0 = _gate(min_level_headroom=0).probe(
        None, None, r2, dev_gain=0.5,
        dev_by_difficulty={"easy": 1.0, "hard": 0.0, "expert": 0.0})
    d0 = pr0.to_dict()
    ck("L2.3 ⛔ 门槛降到 0 ⇒ thin 变空（说明它真由门槛决定）",
       list(d0.get("thin") or []) == [], repr(d0.get("thin")))
    ck("L2.4 ⛔ 而 saturated **不变**（headroom==0 是绝对事实，不是统计判断）",
       d0.get("saturated") == ["expert"], repr(d0.get("saturated")))

    print("\nL3 组：分层诊断在所有分支之前算好（不许两个分支各写一份）")
    gen = (ROOT / "aether" / "generalization.py").read_text(encoding="utf-8")
    ck("L3.1 ⛔ `lagging_all` 的定义在 overfit 分支**之前**"
       "（早退分支才能拿到分层读数）",
       gen.index("lagging_all = sorted(")
       < gen.index("if ho_gain <= self.heldout_floor:"),
       "lagging_all 算在分支之后 ⇒ overfit 仍是零信息判决")
    ck("L3.2 `thin` 的定义同样在分支之前",
       gen.index("thin = sorted(")
       < gen.index("if ho_gain <= self.heldout_floor:"))
    ck("L3.3 `saturated` 的定义同样在分支之前",
       gen.index("saturated = sorted(")
       < gen.index("if ho_gain <= self.heldout_floor:"))
    #: ⛔ 反证：如果哪天有人把 headroom 挪回分支内，L3.1 必须转红。
    #:    用**逐字片段 + 位置**判定，不用全文件计数（第 48 条）。
    ck("L3.4 ⛔ `headroom` 只算一次（两处同形 ⇒ 改一处必漏一处，且不报错）",
       gen.count("headroom = {\n") == 1,
       f"出现 {gen.count('headroom = {')} 次")

    print("\nL4 组：判据一个字没改（真挑食仍被拦）")
    #: ⛔ 这是本轮最重要的一条：**不许借"补观测"顺手放宽判据**。
    #:    fixture 里 easy 层：dev 涨满 1.0、ho 有 3 条空间却 0 增益
    #:    ⇒ 教科书式挑食 ⇒ 必须仍是 overfit，不是 inconclusive。
    ck("L4.1 ⛔ dev 涨满 + ho 同层有空间却不涨 ⇒ **overfit**（不是 inconclusive）",
       pr.verdict == "overfit", str(pr.verdict))
    ck("L4.2 裁决里仍然写明「讨好判据」（措辞未变 ⇒ 下游正则不失真）",
       "讨好判据" in pr.reason, pr.reason)
    #: ⛔ 与轮 5 实测同形的**反面对照**：若 dev 在**有空间的层**也没涨
    #:    ⇒ 没有挑食证据 ⇒ 不该被当成"更严重的"事，但本闸仍按 overfit 处理
    #:    （总体没涨是硬事实），只是分层读数让人能看出该去补 dev 还是改候选。
    r3 = _Runner()
    pr_noev = _gate().probe(None, None, r3, dev_gain=0.5,
                            dev_by_difficulty={"easy": 0.0, "hard": 0.0,
                                               "expert": 0.0})
    ck("L4.3 没有同层挑食证据时**同样**拦（不因读数变全而放行）",
       pr_noev.blocks and pr_noev.verdict == "overfit",
       f"{pr_noev.verdict}/{pr_noev.blocks}")
    ck("L4.4 ⛔ 但两种情形在**读数上可区分**（这是本轮的全部价值）",
       pr_noev.dev_by_difficulty != pr.dev_by_difficulty,
       f"{pr_noev.dev_by_difficulty} == {pr.dev_by_difficulty}")

    print("\nL5 组：反证 —— 事故版必须转红（L1 不是恒真断言）")
    #: ⛔⛔ 第一版 L5 写成了同义反复：自己造一个不带字段的 ProbeResult，
    #:    再断言它字段为空 —— 那必然为真，**没有牙**（第 8 条：恒真断言
    #:    是绿的）。反证必须**跑生产代码**，只把补齐的读数抹掉。
    #: ⛔ 第 10 条：反证前先证明这批数据本来能进目标分支（L1.0 已做）。
    import dataclasses as _dc
    _orig_inner = GeneralizationGate._probe_inner

    def _strip(res):
        """模拟 r54 之前的旧行为：**裁决照出，读数全丢**。"""
        return _dc.replace(res, headroom={}, thin=(), saturated=(),
                           dev_by_difficulty={})

    def _bad_inner(self, *a, **kw):
        return _strip(_orig_inner(self, *a, **kw))

    r5 = _Runner()
    GeneralizationGate._probe_inner = _bad_inner
    try:
        pr_bad = _gate().probe(None, None, r5, dev_gain=0.5,
                               dev_by_difficulty={"easy": 1.0, "hard": 0.0,
                                                  "expert": 0.0})
    finally:
        GeneralizationGate._probe_inner = _orig_inner
    d_bad = pr_bad.to_dict()
    ck("L5.1 ⛔ 事故版（生产路径跑出来后抹掉读数）⇒ L1.2 判据**转红**",
       d_bad.get("headroom") != {"easy": 3, "hard": 1, "expert": 0},
       repr(d_bad.get("headroom")))
    ck("L5.2 ⛔ 事故版 ⇒ L1.3 判据转红（dev 分层丢失被检出）",
       not d_bad.get("dev_by_difficulty"), repr(d_bad.get("dev_by_difficulty")))
    ck("L5.3 ⛔ 事故版 ⇒ L1.5 判据转红（saturated 丢失被检出）",
       d_bad.get("saturated") != ["expert"], repr(d_bad.get("saturated")))
    #: ⛔⛔ 最关键的一条：**事故版的裁决与正常版逐字相同**。
    #:    这解释了它为什么能藏这么久 —— 丢的只是"怎么解释这次拦截"，
    #:    拦截本身照旧，所以没有任何一条既有契约会红。
    ck("L5.4 ⛔ 事故版的 verdict/reason 与正常版**完全相同**"
       "（⇒ 丢的是可复核性，不是裁决；既有契约一条都不会红）",
       (pr_bad.verdict, pr_bad.reason) == (pr.verdict, pr.reason),
       f"{pr_bad.verdict} vs {pr.verdict}")
    #: ⛔ 源码级反证：把字段从 to_dict 里拿掉 ⇒ L1.5 必然转红。
    ck("L5.5 `saturated` 真的进了 to_dict（不只是加了个 dataclass 字段）",
       '"saturated": list(self.saturated)' in gen,
       "to_dict 里没有 saturated ⇒ 观测侧看不到，等于没加")
    ck("L5.6 ⛔ `_downgrade_if_flat` 重建时**继承**这三个读数"
       "（不继承 ⇒ 一次降级把读数全抹成空，与 discriminative 同形）",
       "saturated=res.saturated" in gen)

    print("\nL6 组：与 partial 分支对拍（同源 ⇒ 分层读数一致）")
    #: ⛔ 同一套 held-out、同一个候选行为，只让 ho 的 easy 层**多涨一条**
    #:    ⇒ 总体增益转正 ⇒ 走 partial 分支。两个分支的 headroom 必须**逐字
    #:    相同**，否则说明它们各算了一份（第 13 条）。
    class _RunnerPartial(_Runner):
        def __call__(self, h, spec):
            i = str(spec.get("i", ""))
            self.n += 1
            if i.startswith("ho::e") and i.endswith("e0") and self.n > 9:
                return _sc(i, 1.0)          # 候选把 easy 的第 0 条修好了
            return _Runner.__call__(self, h, spec)

    r4 = _RunnerPartial()
    pr_p = _gate().probe(None, None, r4, dev_gain=0.5,
                         dev_by_difficulty={"easy": 1.0, "hard": 0.0,
                                            "expert": 0.0})
    d_p = pr_p.to_dict()
    ck("L6.1 前提：这一发走的是**另一个**分支（否则对拍是恒等断言）",
       pr_p.verdict != "overfit", f"verdict={pr_p.verdict}")
    ck("L6.2 ⛔ 两个分支的 `headroom` **完全相同**（同源，不是各写一份）",
       d_p.get("headroom") == d.get("headroom"),
       f"{d_p.get('headroom')} vs {d.get('headroom')}")
    ck("L6.3 ⛔ 两个分支的 `saturated` 完全相同",
       d_p.get("saturated") == d.get("saturated"),
       f"{d_p.get('saturated')} vs {d.get('saturated')}")
    ck("L6.4 两个分支的 `thin` 完全相同",
       d_p.get("thin") == d.get("thin"),
       f"{d_p.get('thin')} vs {d.get('thin')}")
    ck("L6.5 ⛔ 分支不同**只因 ho 增益符号**（判据在临界点两侧的读数同形）",
       (pr.heldout_gain <= 0 < pr_p.heldout_gain),
       f"{pr.heldout_gain} vs {pr_p.heldout_gain}")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


def _sc(name: str, reward: float):
    """造一个最小 Verdict。

    ⛔ 与 `contract_difficulty_test` 的 `_ok`/`_bad` 同形：`Verdict` 没有
       `task_id` / `reward` 字段（reward 由 `passed` 派生），而 `Check.name`
       **必须是非空字符串** —— 传 int 会在构造处抛 ValueError ⇒ 被
       `evaluate` 记成 error verdict（reward=0）⇒ 全场读数归零，而现场
       看起来像"闸坏了"（第 24 条：读数是 0，不是异常）。
    """
    from aether.verifier import Check, Verdict
    ok = bool(reward >= 1.0)
    return Verdict(passed=ok,
                   checks=(Check(str(name), ok, "got=%d" % int(ok)),),
                   evidence={"env": str(name)})


if __name__ == "__main__":
    raise SystemExit(main())

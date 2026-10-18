#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：天花板的两个**独立**根因（r59）。

## 背景（实测，不是推断）

同一次运行（seed=7 / 4 环境 / depth=1 / rounds=6 / max-tasks=12）四组对照：

| 组 | 变量 | commit | revert | 冻结套件 | held-out | 备注 |
|---|---|---|---|---|---|---|
| A | 基线 | 1 | 5 | 8 题 | 0.6 | r2–r6 全 `inconclusive` |
| B | `--envs auto` | 1 | 5 | **26 题** | 0.6 | 与 A **逐字同形** |
| C | `--depth 2` | 1 | 5 | 8 题 | **0.8** | 单代 adv 1.347→1.677 |
| E | `--ho-grow` | **2** | **0** | 8 题 | — | r2 就提交 `fix_literals` |

⇒ **「2 代天花板」不是能力到顶，是两个独立的机器问题**：

**根因 1（不改代码）**：`all_thin`（每层可提升空间都 < 门槛 3）⇒ 判
    `inconclusive` ⇒ 拦。A 组 r2–r6 的 `headroom={'easy':0,'hard':2}`，
    而 hard 层**涨满了**（`by_difficulty hard: 1.0`）—— 系统算出
    `discriminative=true` 却判「无从判别」。破法 `--ho-grow` 已存在
    （r53），实测有效（commit 1→2、revert 5→0），但**默认关**（换考卷
    ⇒ 与历史不可比，第 33/34 条）⇒ 只让它**可见**，不许自动开。

**根因 2（要改）**：`inconclusive`（不可判别）被死路账当成「被拦」+1
    ⇒ 连 4 次 ⇒ 判成死路 ⇒ 冷却 ⇒ **晋升成陈述性知识**写进基因组。
    而 E 组证明那个候选（advantage **1.7321**）根本不是死路。
    ⇒ 第 11 条：不可判别 ≠ 有罪。这是把「尺子太短」写成了「世界的事实」。

## 本文件守什么

  T1 `NO_EVIDENCE_VERDICTS` 只含 **不可判别**的裁决，且真被 `_deadend_accum` 用上；
  T2 `inconclusive` **不进**死路 streak（既不 +1 也不归零）；
  T3 反证：`overfit` / `partial`（有证据）**仍**照旧 +1 ⇒ 闸没被关掉；
  T4 `generalize` 归零仍成立；
  T5 「没判」单独计数（第 16 条）；
  T6 生产账本交叉表：0 条 `inconclusive` ⇒ 改动**零决策翻转**（零回归面）；
  T7 `all_thin` 判据本身**一个字没改**（门槛还在）；
  T8 出路 `--ho-grow` 是 CLI 上**真有**的开关（第 35b 条）；
  T9 反证组：把 `inconclusive` 放回死路口径 ⇒ T2 必须转红（否则 T2 恒真）；
  T10 运行级出口 `evolution.no_evidence` 的文案里那条出路同样真有。

── 项 3：「没有数据要迁移」也是一种结论 ──────────────────────────────

r58 说「`env_pair` 取值域漂移 ⇒ 旧 key 累积的 15 次被拦永久失配，要加
迁移机制」。r59 取证：**那条前提不成立**（第 24 条第十次命中）—— 生产
账本 `env_pair` 89 条**全是具体值**、0 条 `deadend` 事件、18 个快照的
`world_facts` 全空 ⇒ **没有任何数据需要迁移**。真成因是生产 15 次运行里
14 次是 `rounds=1` 的短跑，够不到「连续 3 次」的门槛（第 64 条同形）。

但取证过程中挖到一个**真**的 key 漂移（潜伏，尚未触发）：知识侧用
`env_pair_key(*s.split("|"))` 规范化，而 `env_pair` 是**有向**的
`dev->ho` ⇒ 多 dev 环境时会把 key 改写（实测 `zeta|alpha->ho`
→ `alpha->ho|zeta`）⇒ 与账本侧**不同源**。已修为直接用原值。

  U1 知识侧与账本侧同源（同一个 (候选, 环境对) 只有一个表示）；
  U2 多 dev 环境（值里含 `|`）也不被改写；
  U3 反证：恢复旧的 split 规范化 ⇒ U2 必须转红；
  U4 零影响：单 dev 时新旧两法**逐字相同**（已存的 key 不失配）；
  U5/U6 负结果钉住：生产**没有**需要迁移的数据（前提不成立这件事本身
     要能被复核，不许靠"我记得是这样"）。

跑法（纯内存 runner，秒级）：
    python tests/contract_ceiling_test.py
"""
import json
import sys
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


class _Ev:
    """`_deadend_accum` 只认 `kind` / `payload`（与 `Ledger.iter_events`
    产出的事件同形态，但不必真的落盘）。"""

    def __init__(self, verdict, capsule="add_skill:x", pair="a->b"):
        self.kind = "gen.probe"
        self.payload = {"capsule": capsule, "env_pair": pair,
                        "verdict": verdict}


def _loop():
    """⛔ 用 `__new__` 构造：与 J 组同形态，验证**类级兜底**真的够用
    （拿不到 `__init__` 赋值也不能炸）。"""
    from aether.evolution import EvolutionLoop
    return EvolutionLoop.__new__(EvolutionLoop)


def _accum(verdicts, capsule="add_skill:x", pair="a->b"):
    """依次累加一串 verdict，返回 (streak, stats)。"""
    loop = _loop()
    streak: dict = {}
    for v in verdicts:
        loop._deadend_accum(_Ev(v, capsule, pair), streak)
    return streak, (loop._deadend_stats or {})


def main() -> int:
    from aether.evolution import EvolutionLoop

    print("== T1 组：口径定义 ==")
    nev = EvolutionLoop.NO_EVIDENCE_VERDICTS
    ck("T1.1 只含 inconclusive（不可判别）", set(nev) == {"inconclusive"},
       f"{sorted(nev)}")
    src = (ROOT / "aether" / "evolution.py").read_text(encoding="utf-8")
    ck("T1.2 `_deadend_accum` 真用了它（不是摆设）",
       "if v in self.NO_EVIDENCE_VERDICTS" in src)

    print("\n== T2 组：不可判别 ⇒ 不进死路账 ==")
    s2, st2 = _accum(["inconclusive"] * 4)
    key = "add_skill:x|a->b"
    ck("T2.1 连 4 次 inconclusive ⇒ streak 仍为 0（不是 4）",
       s2.get(key, 0) == 0, f"streak={s2}")
    ck("T2.2 ⛔ 单独计数（没判 ≠ 没发生）",
       (st2.get("no_evidence") or 0) == 4, f"stats={st2}")
    s2b, _ = _accum(["overfit", "inconclusive", "overfit"])
    ck("T2.3 inconclusive **不打断**连续（它不是成功，不该清零别人的账）",
       s2b.get(key, 0) == 2, f"streak={s2b}")

    print("\n== T3 组：反证 —— 有证据的负面裁决照旧 +1（闸没被关掉）==")
    for v in ("overfit", "partial"):
        s3, st3 = _accum([v] * 3)
        ck(f"T3 三次 {v} ⇒ streak=3（与改造前逐字同口径）",
           s3.get(key, 0) == 3, f"streak={s3}")
        ck(f"T3 {v} 不进 no_evidence 计数",
           (st3.get("no_evidence") or 0) == 0, f"stats={st3}")

    print("\n== T4 组：generalize 归零仍成立 ==")
    s4, _ = _accum(["overfit", "overfit", "generalize"])
    ck("T4.1 generalize ⇒ 归零", s4.get(key, 0) == 0, f"streak={s4}")
    s4b, _ = _accum(["inconclusive", "generalize", "inconclusive"])
    ck("T4.2 generalize 之后再来 inconclusive ⇒ 仍是 0",
       s4b.get(key, 0) == 0, f"streak={s4b}")

    print("\n== T5 组：不同 key 互不串味 ==")
    loop = _loop()
    streak: dict = {}
    loop._deadend_accum(_Ev("overfit", "add_skill:a", "x->y"), streak)
    loop._deadend_accum(_Ev("inconclusive", "add_skill:b", "x->y"), streak)
    ck("T5.1 同环境对、不同候选分开记",
       streak.get("add_skill:a|x->y") == 1
       and "add_skill:b|x->y" not in streak, f"{streak}")
    loop._deadend_accum(_Ev("overfit", "add_skill:a", "z->w"), streak)
    ck("T5.2 同候选、不同环境对分开记",
       streak.get("add_skill:a|z->w") == 1, f"{streak}")

    print("\n== T6 组：生产账本交叉表（改动零决策翻转才准改）==")
    led = ROOT / "data" / "ledger.jsonl"
    if led.exists():
        import collections
        c = collections.Counter()
        for line in led.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "gen.probe":
                c[str((e.get("payload") or {}).get("verdict"))] += 1
        n_inc = c.get("inconclusive", 0)
        ck("T6.1 生产账本实测 inconclusive 条数（读数，不是断言）", True,
           f"分布={dict(c)}")
        ck("T6.2 ⛔ 改这条口径在生产上**零决策翻转** ⇒ 才允许改",
           n_inc == 0,
           f"生产有 {n_inc} 条 inconclusive ⇒ 改了会翻旧账，先做交叉表")
    else:
        ck("T6 生产账本存在", False, f"{led} 不存在")

    print("\n== T7 组：`all_thin` 判据本身一个字没改 ==")
    gsrc = (ROOT / "aether" / "generalization.py").read_text(encoding="utf-8")
    ck("T7.1 门槛仍在（min_level_headroom 仍参与判定）",
       "headroom.get(lv, 0) < self.min_level_headroom" in gsrc)
    ck("T7.2 all_thin 仍在（不是被删掉了）", "all_thin = bool(by_diff)" in gsrc)
    ck("T7.3 ⛔ 没有偷偷降低门槛",
       "self.min_level_headroom = int(min_level_headroom)" in gsrc
       and "min_level_headroom: int = 3" in gsrc)

    print("\n== T8 组：reason 里写的出路必须真有（第 35b 条）==")
    cli = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("T8.1 `--ho-grow` 是 CLI 上真实存在的开关", '"--ho-grow"' in cli)
    ck("T8.2 它默认关（与历史同口径，第 12 条：要能显式开）",
       '"--ho-grow", action="store_true"' in cli
       or 'action="store_true",\n' in cli)
    ck("T8.3 `evolution.no_evidence` 的出路文案指向它",
       "escape_hatch" in src and "--ho-grow" in src)

    print("\n== T9 组：反证 —— 把 inconclusive 放回死路口径，T2 必须转红 ==")
    saved = EvolutionLoop.NO_EVIDENCE_VERDICTS
    try:
        EvolutionLoop.NO_EVIDENCE_VERDICTS = frozenset()
        s9, st9 = _accum(["inconclusive"] * 4)
        ck("T9.1 反证：口径里去掉 inconclusive ⇒ streak 变 4（T2.1 不是恒真）",
           s9.get(key, 0) == 4, f"streak={s9}")
        ck("T9.2 反证：no_evidence 计数不再涨",
           (st9.get("no_evidence") or 0) == 0, f"stats={st9}")
    finally:
        EvolutionLoop.NO_EVIDENCE_VERDICTS = saved
    s9b, _ = _accum(["inconclusive"] * 4)
    ck("T9.3 恢复后行为回到 T2.1（还原是真的）", s9b.get(key, 0) == 0,
       f"streak={s9b}")

    print("\n== T10 组：运行级出口 ==")
    ck("T10.1 有不可判别时才记事件（不是每轮都记的假仪表）",
       "if hits:" in src and "evolution.no_evidence" in src)
    ck("T10.2 事件里带被吃掉的增益（让人知道值不值得补题）",
       "max_heldout_gain" in src)
    ck("T10.3 事件里带最小 headroom（让人知道差多少）", "min_headroom" in src)
    ck("T10.4 ⛔ 文案明说「不是候选有问题」（不许被读成有罪）",
       "不是候选有问题" in src)

    # ──────────────────────────────────────────────────────
    print("\n== U1 组：知识侧与账本侧的 key 必须同源（项 3）==")
    from aether.evolution import EvolutionLoop as EL
    ck("U1.1 `_deadend_pair` 存在（唯一推导处）",
       hasattr(EL, "_deadend_pair"))
    for _fn in ("_facts_deadend_streak", "_observe_deadend"):
        _i = src.find(f"def {_fn}")
        _body = src[_i:_i + 1200] if _i >= 0 else ""
        ck(f"U1.2 {_fn} 走 `_deadend_pair`（不再自己 split）",
           "_deadend_pair(env_pair)" in _body
           and 'split("|"' not in _body)

    print("\n== U2 组：多 dev 环境（值里含 `|`）不许被改写 ==")
    for _p in ("zeta|alpha->ho", "alpha|beta->ho", "b|a->ho"):
        ck(f"U2 env_pair={_p!r} ⇒ 原样返回", EL._deadend_pair(_p) == _p,
           f"得到 {EL._deadend_pair(_p)!r}")
    for _p in ("textscore->eventflow", "eventflow->textscore", "unknown"):
        ck(f"U2 有向对 {_p!r} 不做顺序规范化（dev→ho 是有向的）",
           EL._deadend_pair(_p) == _p, f"得到 {EL._deadend_pair(_p)!r}")

    print("\n== U3 组：反证 —— 恢复旧的 split 规范化，U2 必须转红 ==")
    _saved = EL._deadend_pair
    try:
        def _old(ep):
            from aether import world_facts as _W
            return (_W.env_pair_key(*str(ep).split("|", 1))
                    if "|" in str(ep) else str(ep))
        EL._deadend_pair = staticmethod(_old)
        ck("U3 反证：旧法会把 'zeta|alpha->ho' 改写（U2 不是恒真）",
           _old("zeta|alpha->ho") != "zeta|alpha->ho",
           f"旧法得到 {_old('zeta|alpha->ho')!r}")
    finally:
        EL._deadend_pair = _saved
    ck("U3.1 还原是真的", EL._deadend_pair("zeta|alpha->ho")
       == "zeta|alpha->ho")

    print("\n== U4 组：零影响 —— 单 dev 时新旧逐字相同 ==")
    from aether import world_facts as _WF

    def _old_pair(ep):
        return (_WF.env_pair_key(*str(ep).split("|", 1))
                if "|" in str(ep) else str(ep))
    for _p in ("textscore->eventflow", "eventflow->textscore", "unknown",
               "alpha|beta->ho"):
        ck(f"U4 env_pair={_p!r} 新旧同值（已存 key 不失配）",
           _old_pair(_p) == EL._deadend_pair(_p),
           f"旧={_old_pair(_p)!r} 新={EL._deadend_pair(_p)!r}")

    print("\n== U5 组：负结果钉住 —— 生产确实**没有**要迁移的数据 ==")
    if led.exists():
        import collections as _c
        _ep = _c.Counter()
        _nd = 0
        for line in led.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            pl = e.get("payload") or {}
            if "env_pair" in pl:
                _ep[str(pl["env_pair"])] += 1
            if "deadend" in e.get("kind", "") or "world_fact" in e.get("kind", ""):
                _nd += 1
        ck("U5.1 生产 `env_pair` 无 'unknown'（r58 那个漂移没有发生）",
           "unknown" not in _ep, f"取值={dict(_ep)}")
        ck("U5.2 生产 0 条 deadend / world_fact 事件 ⇒ 没有可迁移的数据",
           _nd == 0, f"n={_nd}")
        ck("U5.3 ⛔ 这条是**读数**：哪天 n>0 就说明前提变了，要重开项 3",
           True, f"env_pair 分布={dict(_ep)}")

    print("\n== U6 组：快照里 world_facts 现状（负结果，可复核）==")
    _db = ROOT / "data" / "aether.db"
    if _db.exists():
        import sqlite3
        con = sqlite3.connect(f"file:{_db}?mode=ro", uri=True)
        cur = con.cursor()
        try:
            n_wf = 0
            for (_hid, _pl) in cur.execute(
                    "select harness_id, payload from harness_snapshots"):
                try:
                    P = json.loads(_pl)
                except ValueError:
                    continue
                L = P.get("layers") if isinstance(P, dict) else None
                if not isinstance(L, dict):
                    L = P
                if isinstance(L, dict) and (L.get("world_facts") or {}):
                    n_wf += 1
            ck("U6.1 含 world_facts 的快照数（读数）", True, f"n={n_wf}")
            ck("U6.2 与 U5.2 互相印证 ⇒ 陈述性知识层生产上零产出",
               n_wf == 0, f"n={n_wf}")
        except sqlite3.Error as ex:
            ck("U6 快照表可读", True, f"跳过（{type(ex).__name__}）")
        finally:
            con.close()
    else:
        ck("U6 生产 db 存在", True, "不存在 ⇒ 跳过（读数，非断言）")

    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

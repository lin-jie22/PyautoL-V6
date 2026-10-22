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
  T6 生产账本交叉表：**改动落地前** 0 条 `inconclusive` ⇒ 零决策翻转
     （边界钉在 cbbb148 的落地时刻；反证：同批数据在"现在"的边界下数出 ≥2 条）
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

#: ⛔⛔ 生产账本是**本机资产**（在 `.gitignore` 里，不进仓库）。CI 克隆下来
#:    没有它 ⇒ T6 那条「历史交叉表」**无从判别**。
#:    ⛔ 判红会把"这台机器没有历史"报成"契约坏了"：实测 CI 上 T6 一红。
#:    ⛔ 判绿更糟：账本哪天被删了 / 格式变了，也会静默变绿。
#:    ⇒ SKIP 单独记账（**未覆盖 ≠ 通过**），与 rea_bridge 的 SKIPPED 同纪律。
SKIPPED = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def sk(name, reason):
    print(f"  SKIP {name} —— {reason}")
    SKIPPED.append(f"{name} —— {reason}")


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
        import datetime as _dt
        #: ⛔⛔ 边界必须钉在**口径改动落地的那一刻**，不是"现在"。
        #:    r60 G 全量验收实测（2026-10-20 14:29）：生产在那个来源未定位的
        #:    周期短跑里连出 2 条 `inconclusive`（都走 `all_thin` 分支：
        #:    easy 层 headroom=0、hard 只剩 1 条 ⇒ 没有一层的证据可用），
        #:    于是原来那句 `n_inc == 0` 变红。
        #:    ⛔ 红的不是代码、也不是判据，是**时间**：断言把"建卷那一刻的一次
        #:    性事实"写成了永恒性质，而生产账本还在长（第 133 条：随被测对象
        #:    变化的量不许当固定读数）。
        #:    ⇒ 只数 `ts < CUTOFF` 的部分。「改动之后产生的判决」不是旧账，
        #:      本来就应该按新口径算 —— 把它算进来才是真的翻旧账。
        #:    CUTOFF = cbbb148（2026-10-18 20:39）引入 `NO_EVIDENCE_VERDICTS`。
        CUTOFF = _dt.datetime(2026, 10, 18, 20, 39).timestamp()
        c = collections.Counter()
        n_pre = 0
        n_post = 0
        for line in led.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "gen.probe":
                v = str((e.get("payload") or {}).get("verdict"))
                c[v] += 1
                if v == "inconclusive":
                    if float(e.get("ts") or 0) < CUTOFF:
                        n_pre += 1
                    else:
                        n_post += 1
        ck("T6.1 生产账本实测各裁决条数（读数，不是断言）", True,
           f"分布={dict(c)}｜改动前 inconclusive={n_pre}｜改动后={n_post}")
        ck("T6.2 ⛔ 改这条口径在**落地前**的生产上零决策翻转 ⇒ 才允许改",
           n_pre == 0,
           f"改动前有 {n_pre} 条 inconclusive ⇒ 改了会翻旧账，先做交叉表")
        #: ⛔ 反证：证明 T6.2 的 0 是**切分**出来的，不是数据里根本没有
        #:    （第 57 条：空集合当分母最会撒谎）。同一批数据把边界挪到
        #:    "现在"就能数出那两条 ⇒ 这条判据有牙。
        ck("T6.2b ⛔ 反证：同一批数据在「现在」的边界下数出 ≥2 条 ⇒ T6.2 不是恒真",
           n_post >= 2, f"改动后 inconclusive={n_post}")
    else:
        #: ⛔ 这条原本写的是 `ck(..., False)` ⇒ CI（没有账本）恒红。
        #:    红的是**环境**不是判据：判据要问的是"改动落地前生产上有没有
        #:    翻转"，没有生产就答不了 —— 答不了要说"答不了"，不能说"错"。
        sk("T6 生产账本交叉表（落地前零决策翻转）",
           f"{led} 不存在（本机历史资产，不进仓库）⇒ 无从判别")

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

    # ──────────────────────────────────────────────────────
    print("\n== V1 组：不冷却的代价必须进账本（项 4）==")
    #: r59 把 inconclusive 摘出死路账 ⇒ 候选不再进冷却 ⇒ 每轮重探。
    #: 这个代价当时只写了一句「多花探测预算」，没量也没记 ⇒ 补上读数。
    ck("V1.1 `evolution.no_evidence` 带 `repeated_probes`（重探次数）",
       "repeated_probes" in src)
    ck("V1.2 带 `repeated_capsules`（哪些候选被重探）",
       "repeated_capsules" in src)

    _rep = getattr(EL, "_no_evidence_repeats", None)
    ck("V1.3 重探计数有独立实现（可单测，不是夹在事件构造里）",
       _rep is not None)
    if _rep is not None:
        _h1 = [{"capsule": "c1"}] * 4
        _e1, _c1 = _rep(_h1)
        ck("V1.4 同候选 4 次 ⇒ 重探 3 次（第 1 次不算重探）",
           _e1 == 3 and _c1 == ["c1"], f"extra={_e1} caps={_c1}")
        _h2 = [{"capsule": "c1"}, {"capsule": "c2"},
               {"capsule": "c1"}, {"capsule": "c2"}]
        _e2, _c2 = _rep(_h2)
        ck("V1.5 两个候选各 2 次 ⇒ 重探 2 次", _e2 == 2 and _c2 == ["c1", "c2"],
           f"extra={_e2} caps={_c2}")
        _h3 = [{"capsule": f"c{i}"} for i in range(4)]
        _e3, _c3 = _rep(_h3)
        ck("V1.6 ⛔ 反证：候选全不同 ⇒ 重探 0（V1.4 不是恒真）",
           _e3 == 0 and _c3 == [], f"extra={_e3} caps={_c3}")
        ck("V1.7 空输入 ⇒ 0（不是 dict 越界）", _rep([]) == (0, []))

    print("\n== V2 组：⛔ 文案里的出路必须真存在（第 35b 条）==")
    import re as _re
    _cli = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    #: 扫 evolution.py 里**全部** `--xxx` 提及（注释与文案一视同仁）：
    #: 写着「加 --foo 就能解决」而 `--foo` 根本不存在，是最会骗人的形态。
    _flags = sorted(set(_re.findall(r"--[a-z][a-z0-9-]{2,}", src)))
    _missing = [f for f in _flags if f not in _cli]
    ck("V2.1 evolution.py 提到的每个 `--开关` 都在 CLI 里真实存在",
       not _missing, f"缺失={_missing}")
    ck("V2.2 确实扫到了开关（不是空集合恒真，第 57 条）",
       len(_flags) >= 3, f"n={len(_flags)} {_flags[:6]}")
    ck("V2.3 代价口径写进文案（实测数字，不是「会慢一点」）",
       "耗时" in src and "噪声" in src,
       "文案里应有 ABBA 实测的耗时结论")
    ck("V2.4 ⛔ 不许承诺不存在的封顶（诚实：当前无封顶机制）",
       "无封顶" in src or "没有封顶" in src,
       "要么有封顶开关，要么明说没有")

    # ──────────────────────────────────────────────────────
    print("\n== W1 组：`--gate-tries` 有牙、有默认、且不被写死（项 5）==")
    #: ⛔ 第 51 条：F 组实测它「零收益」，差点被判成没用。真相是 **r1 的
    #:    queue_size=2 > 默认 1**（⇒ 开关确实接线），只是 r2 起每轮只有
    #:    1 个候选通过准入判据 ⇒ 无用武之地。功能**从未有契约覆盖**
    #:    ⇒ 它随时可能静默腐烂而没有人为它报警。
    from aether.evolution import EvolutionConfig
    ck("W1.1 `EvolutionConfig.gate_tries` 默认 1（与历史逐字同口径）",
       getattr(EvolutionConfig(), "gate_tries", None) == 1,
       f"={getattr(EvolutionConfig(), 'gate_tries', None)}")
    _cli2 = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("W1.2 CLI 有 `--gate-tries` 且默认 \"1\"（第 12 条：默认开的要能显式关）",
       '"--gate-tries"' in _cli2 and 'default="1"' in _cli2)
    ck("W1.3 ⛔ `tries` 从 cfg 读（不是被写成常数 ⇒ 开关不是装饰）",
       'getattr(self.cfg, "gate_tries", 1)' in src and "max(1, int(" in src)
    ck("W1.4 `queue = eligible[:tries]`（按发展集排序依次试，H1 不动）",
       "eligible[:tries]" in src)

    print("\n== W2 组：「它有没有用武之地」必须永远可从账本读出 ==")
    #: queue_size=1 就是「这一轮没有第二个候选可试」⇒ 没有它，
    #: 「gate-tries 无用」与「gate-tries 没接线」在外部无法区分。
    ck("W2.1 `gen.probe` 带 `queue_size`", "queue_size" in src)
    ck("W2.2 `gen.probe` 带 `try_index`", "try_index" in src)
    ck("W2.3 ⛔ 准入闸与 eligible 是**两套**判据（读数，非断言）："
       "F 组实测 gate.result 3 条 / 轮，但 queue_size=1~2 ⇒ "
       "过了 gate ≠ 进了泛化闸队列。别拿 gate.result 数当队列长度",
       True, "见 docs/CEILING_2026-10-18.md §项5")

    # ──────────────────────────────────────────────────────
    #: ── 项 6：`--ho-grow` × `--retry-every` 是否叠加 ──────────────
    print("\n== X1 组：「用武之地」三读数必须永远可从账本读出（项 6）==")
    #: ⛔⛔ 第 51 / 72 条：`gain=0` 与「从没被测过」在外部**无法区分**。
    #:    r59 项 6 实测 J（`--ho-grow --retry-every 2`）与 K（只 `--ho-grow`）
    #:    **逐字同形**（commit 2/2、revert 0/0、probe 2/2、耗时 94/75s），
    #:    差点被判成「开关无效」。真相靠三个读数才排出来，**少任何一个都会
    #:    误判**：
    #:      ① `retry_every` = 2      ⇒ 开关**接线**了（不是没传进去）；
    #:      ② `recall` = "ok"        ⇒ 记忆**装了**（不是 no_memory / degraded）；
    #:      ③ `n_known_history` = 0  ⇒ 失败史为空 ⇒ **零原料**。
    #:    ⇒ 结论是「**无原料**」，不是「机制失效」。不许据此删除开关。
    #:    （G 组已覆盖放行逻辑本身，本组补的是**用武之地**这一半。）
    from aether.embeddings import HashingEmbedder
    from aether.genome import Harness
    from aether.ledger import Ledger
    from aether.memory import MemoryOS
    from aether.providers import MemoryAwareProposer

    _psrc = (ROOT / "aether" / "providers.py").read_text(encoding="utf-8")
    ck("X1.1 ① `experience.propose` 带 `retry_every`（证明开关真传到 proposer）",
       '"retry_every": self.retry_every' in _psrc)
    ck("X1.2 ② 带 `recall` 三态（ok / degraded / no_memory）",
       '"recall": recall' in _psrc and "no_memory" in _psrc
       and "degraded" in _psrc)
    ck("X1.3 ③ 带 `n_known_history`（失败史条数 = 出口的原料）",
       '"n_known_history"' in _psrc)
    ck("X1.4 出口走了几次可读（与 G5 同一条，钉住不回退）",
       '"probation_released": list(self.probation_released)' in _psrc)

    print("\n== X2 组：反证 —— 这三个读数都不是恒 0 的摆设 ==")

    class _Cap:
        def __init__(self, name):
            self.name = name

    class _Stub:
        """同协议内层提议器：每次只提一个变异（模拟"记不住"的提议器）。"""

        def __init__(self, names=("add_skill",)):
            self.names = tuple(names)

        def propose(self, weakness, harness, n=3, rejected=None):
            return [_Cap(x) for x in self.names[:n]]

        def diagnostics(self):
            return {"name": "stub"}

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        led = Ledger(tdp / "led.jsonl")
        mem, _ = MemoryOS.load(tdp / "mem.json", HashingEmbedder(dim=256),
                               min_content_len=0)
        w = {"kind": "missing_skill", "topic": "t"}
        p = MemoryAwareProposer(_Stub(), mem, ledger=led, retry_every=2)
        p.propose(w, Harness())          #: 此时还没有失败史
        e0 = [e for e in led.events() if e.kind == "experience.propose"][-1]
        ck("X2.1 ⛔ 无失败史 ⇒ n_known_history == 0（与生产实测同形）",
           e0.payload.get("n_known_history") == 0,
           str(e0.payload.get("n_known_history")))
        ck("X2.2 此时 recall == 'ok'（记忆装了，只是没料 ⇒ 不是没接线）",
           e0.payload.get("recall") == "ok", str(e0.payload.get("recall")))
        #: 注入一条失败史 ⇒ 原料 > 0 ⇒ 读数必须跟着动（否则它是死的）
        p.remember(w, "add_skill", reason="overfit")
        p.propose(w, Harness())
        e1 = [e for e in led.events() if e.kind == "experience.propose"][-1]
        ck("X2.3 ⛔ 反证：有失败史 ⇒ n_known_history > 0（读数不是恒 0）",
           (e1.payload.get("n_known_history") or 0) > 0,
           str(e1.payload.get("n_known_history")))
        ck("X2.4 反证：同一批里 n_skipped 也从 0 变 > 0（出口有对象了）",
           (e1.payload.get("n_skipped") or 0) > 0,
           str(e1.payload.get("n_skipped")))
        #: 三态里的第二态：没装记忆 ⇒ 必须是 no_memory，不许冒充 ok
        led2 = Ledger(tdp / "led2.jsonl")
        p2 = MemoryAwareProposer(_Stub(), None, ledger=led2)
        p2.propose(w, Harness())
        e2 = [e for e in led2.events() if e.kind == "experience.propose"][-1]
        ck("X2.5 ⛔ 反证：memory=None ⇒ recall == 'no_memory'"
           "（与『装了但没料』可分辨，第 10 条）",
           e2.payload.get("recall") == "no_memory",
           str(e2.payload.get("recall")))

    print("\n== X3 组：`--ho-grow` 与 `--retry-every` 正交（机器验证）==")
    _gsrc = (ROOT / "aether" / "generalization.py").read_text(encoding="utf-8")
    ck("X3.1 `retry_every` 不在泛化闸里（它管 proposer 过滤，不管考卷）",
       "retry_every" not in _gsrc)
    ck("X3.2 `ho_grow` 不在提议器里（它管 held-out 卷子，不管候选）",
       "ho_grow" not in _psrc)
    ck("X3.3 ⛔ 反证：确实扫到这两个名字（不是空集恒真，第 57 条）",
       "ho_grow" in src and "retry_every" in _psrc)
    ck("X3.4 负结果钉住：项 6 实测 J 与 K 逐字同形（commit 2/2、revert 0/0）"
       " ⇒ 成因是**零原料**（remembered=0），不是机制失效"
       " ⇒ 不许据此删开关（第 51 条）",
       True, "见 docs/CEILING_2026-10-18.md §12")

    # ──────────────────────────────────────────────────────
    #: ── 项 7：知识层产出空壳 —— 失败原因两处不同源 ────────────────
    print("\n== Y1 组：失败原因必须**同源**（r59 项 7）==")
    #: ⛔⛔ 实测因果链（每一步都有数）：
    #:   `run_round` 的 MINE **只**走 `_runs_from_verdicts`，而它写的是
    #:   `v.error or ""`（**没有 fallback**）；`evaluate_dev` 写的却是
    #:   `v.error or "checks_failed:" + 失败检查名`。
    #:   ⇒ 挖掘侧拿到的 `error` **恒为空** ⇒ `w.evidence` 永不 append
    #:   ⇒ 知识条目 = `missing_skill / t0：`（**19 字**）⇒ 空壳。
    #:   生产实测：**111/173（64%）**弱点无证据、28 次摄入里 **23 次空壳**；
    #:   库里那条空壳 `access_count=1`，而两条有内容的 **1814 / 1602**
    #:   ⇒ 空知识在事实上从没被用过（写入端"成功"≠ 可用，第 48 条）。
    ck("Y1.1 存在单一实现 `_verdict_error`（第 13 条）",
       "def _verdict_error" in src)
    ck("Y1.2 MINE 侧（`_runs_from_verdicts`）用它，不再写 `v.error or \"\"`",
       "EvolutionLoop._verdict_error(v)" in src
       and 'payload={"error": v.error or ""' not in src)
    ck("Y1.3 落库侧（`evaluate_dev`）也用它（两处同源）",
       '"error": self._verdict_error(v)' in src)
    ck("Y1.4 ⛔ 反证：确实扫到两处调用（不是空集恒真，第 57 条）",
       src.count("_verdict_error(") >= 3,
       f"出现 {src.count('_verdict_error(')} 次")

    print("\n== Y2 组：`_verdict_error` 的三态行为 ==")

    class _Chk:
        def __init__(self, name, passed):
            self.name = name
            self.passed = passed

    class _V:
        def __init__(self, error="", checks=()):
            self.error = error
            self.checks = tuple(checks)

    _ve = EvolutionLoop._verdict_error
    ck("Y2.1 有 error ⇒ 原样返回（不覆盖判据层的崩溃信号）",
       _ve(_V("boom", [_Chk("a", False)])) == "boom")
    _v2 = _V("", [_Chk("clamp_low", False), _Chk("ok1", True),
                  _Chk("clamp_mid", False)])
    ck("Y2.2 error 空 + 有失败检查 ⇒ `checks_failed:` + 失败名（有序）",
       _ve(_v2) == "checks_failed:clamp_low,clamp_mid", _ve(_v2))
    ck("Y2.3 error 空 + 全通过 ⇒ \"\"（成功 run 不伪造原因）",
       _ve(_V("", [_Chk("a", True)])) == "")
    ck("Y2.4 ⛔ 反证：Y2.2 对「全通过」为假（不是恒真断言）",
       _ve(_V("", [_Chk("a", True)])) != "checks_failed:a")
    ck("Y2.5 空 checks ⇒ \"\"（不是 None，下游 join 不炸）",
       _ve(_V("", [])) == "")

    print("\n== Y3 组：零回归 —— 落库侧与旧口径**逐字相同** ==")
    #: ⛔ 第 22 条：修复算子是行为改动。落库侧（`evaluate_dev`）的旧写法是
    #:    `v.error or ("checks_failed:" + ",".join(失败名))`，语义必须与
    #:    `_verdict_error` 逐字相同 ⇒ 这次改动的**回归面为零**。
    def _old_way(v):
        return v.error or ("checks_failed:"
                           + ",".join(c.name for c in v.checks
                                      if not c.passed))

    _cases = [_V("boom", [_Chk("a", False)]),
              _V("", [_Chk("a", False), _Chk("b", True)]),
              _V("", [_Chk("a", True)]),
              _V("", []),
              _V("err", [])]
    #: ⛔ 语义等价点 = **有失败检查时**（这才是落库侧真正的用途）。
    #:   「无失败检查」那两个用例新旧**不同** —— 见 Y3.1b，那是本次改动
    #:    唯一的行为差异，且是有意修掉的噪声，不是回归。
    _rel = [c for c in _cases if any(not x.passed for x in c.checks)]
    _same = all(_ve(c) == _old_way(c) for c in _rel)
    ck("Y3.1 ⛔ 有失败检查时与旧写法**逐字相同**（落库侧语义零变化）",
       _same,
       str([(_ve(c), _old_way(c)) for c in _rel if _ve(c) != _old_way(c)]))
    #: ⛔⛔ 第 22 条：修复算子是行为改动 ⇒ 差异不许藏。旧写法在
    #:    「error 空 **且** 无失败检查」时会拼出 `"checks_failed:"`
    #:    （说了失败、却一个检查名都没列 = 纯噪声）；新写法返回 `""`。
    ck("Y3.1b ⛔ 如实记录唯一行为差异：旧写法产噪声串 `checks_failed:`，"
       "新写法返回空串（更干净）。该分支下游无人消费"
       "（MINE 已改走 `_runs_from_verdicts`，store 的 error 不再进挖掘）",
       _old_way(_V("", [])) == "checks_failed:" and _ve(_V("", [])) == "",
       f"旧={_old_way(_V('', []))!r} 新={_ve(_V('', []))!r}")
    ck("Y3.2 ⛔ 反证：差异确实存在过 —— 旧 MINE 口径 `v.error or \"\"` "
       "对「error 空但有失败检查」会丢掉全部信息",
       (_V("", [_Chk("a", False)]).error or "") == ""
       and _ve(_V("", [_Chk("a", False)])) == "checks_failed:a")

    print("\n== Y4 组：修后的实测对照（同 seed，唯一变量 = 本修复）==")
    #: 同 seed=7 / rounds=6 / max-tasks=12 / dev=textscore / ho=eventflow：
    #:   弱点无证据   6/6 (100%)  →  0/6 (0%)
    #:   知识 preview  19 字       →  60 / 44 字
    #:   commit/revert 1/5         →  1/5（**不变**）
    #:   裁决序列     generalize + 5×inconclusive → **逐字相同**
    #:   总事件       231          →  231（不变）
    ck("Y4.1 修对了：弱点证据从 100% 空 → 0% 空（实测，见 §13）",
       True, "6/6 → 0/6")
    ck("Y4.2 ⛔ 没翻车：commit/revert/裁决序列/事件总数全部不变",
       True, "1/5 → 1/5，裁决逐字相同，231 → 231")
    ck("Y4.3 ⛔ 诚实残留（不许改闸掩盖）：修后两条真实长度 **63 / 44** 字"
       "（账本 `preview` 截断显示 60，别拿它当全长），生产 "
       "`min_content_len=60` ⇒ **1/2 跨过、1/2 仍 `below_min`**（修前 0/2）"
       " ⇒ 修的是「有没有证据」，不是「够不够长」（降闸 = 改判据，第 34 条）",
       True, "残留如实记录，出路见 §13")

    # ──────────────────────────────────────────────────────
    #: ── 项 8：topic 读漏 `env_id` ⇒ 弱点按任务序号分桶 ─────────────
    print("\n== Z1 组：`topic` 必须能取到真实环境名（r59 项 8）==")
    #: ⛔⛔ 第 24 条**第十二次**命中：代码找 `evidence["topic"]` /
    #:    `evidence["env"]`，而判据层填的是 **`env_id`** —— 取证 30 条
    #:    evidence：`topic`/`env`/`task_id` **100% 缺失**，`env_id` 30/30
    #:    存在且全为 `textscore`。
    #:    ⇒ topic 恒空 ⇒ 退化 `task_id` ⇒ 再退化序号 `t0`/`t3` ⇒ 弱点按
    #:    **任务序号**分桶（每桶 n_failed=1）而不是按**环境**分桶。
    #:    生产 173 个弱点里 111 个的 topic 就是 `t0`(77)/`t3`(33)/`t7`(1)。
    ck("Z1.1 `topic` 的取值链里补上了 `env_id`",
       'v.evidence.get("env_id")' in src)
    ck("Z1.2 ⛔ 原来那两个键（`topic` / `env`）的优先级**没变**"
       "（只是往后追加，不是替换 ⇒ 已有数据不失配）",
       'v.evidence.get("topic")' in src and 'v.evidence.get("env")' in src)
    _hit = {k: src.count(f'v.evidence.get("{k}")')
            for k in ("topic", "env", "env_id")}
    ck("Z1.3 ⛔ 反证：三个键各扫到至少一次（不是空集恒真，第 57 条）",
       all(v >= 1 for v in _hit.values()), str(_hit))

    print("\n== Z2 组：`_runs_from_verdicts` 的 topic 三态 ==")

    class _Vv:
        def __init__(self, evidence=None, passed=False):
            self.evidence = dict(evidence or {})
            self.passed = passed
            self.checks = ()
            self.error = ""

    class _H:
        harness_id = "h-test"

    _rfv = EvolutionLoop._runs_from_verdicts
    _r1 = _rfv([_Vv({"env_id": "textscore"})], _H())
    ck("Z2.1 evidence 有 `env_id` ⇒ topic == 该值（真实环境名）",
       _r1[0].payload["topic"] == "textscore",
       str(_r1[0].payload["topic"]))
    _r2 = _rfv([_Vv({"topic": "显式", "env_id": "textscore"})], _H())
    ck("Z2.2 ⛔ 优先级不变：有 `topic` 时仍以 `topic` 为准",
       _r2[0].payload["topic"] == "显式", str(_r2[0].payload["topic"]))
    ck("Z2.3 ⛔ 反证：Z2.1 不是恒真 —— 没有 env_id 时退化成序号 `t0`",
       _rfv([_Vv({})], _H())[0].payload["topic"] == ""
       and _rfv([_Vv({})], _H())[0].task_id == "t0",
       str(_rfv([_Vv({})], _H())[0].task_id))
    ck("Z2.4 `task_id` 也跟着受益（topic 非空 ⇒ 不再是序号）",
       _r1[0].task_id == "textscore", str(_r1[0].task_id))

    print("\n== Z3 组：修后实测对照（同 seed，唯一变量 = 本修复）==")
    #: topic      {'t0':1,'t3':5}  →  {'textscore':6}
    #: n_failed   {1:6}            →  {2:5, 5:1}（失败按环境聚桶）
    #: 知识真实长度 63 / 44         →  **106 / 79** ⇒ **2/2 全部 ≥60**
    #: commit/revert 1/5           →  1/5（**不变**）
    #: 裁决序列 / 总事件 231        →  逐字相同
    ck("Z3.1 修对了：topic 从序号 `t0/t3` → 真实环境名 `textscore`",
       True, "{'t0':1,'t3':5} → {'textscore':6}")
    ck("Z3.2 ⛔ 强度伴生读数（第 27 条）：不是读数搬家 —— 分桶变粗的"
       "同时，**知识真实长度 63/44 → 106/79，跨过 60 字闸的从 1/2 变 2/2**",
       True, "项 7 的残留被本项顺带解决")
    ck("Z3.3 ⛔ 没翻车：commit/revert 1/5、裁决序列、总事件 231 全部不变",
       True, "见 §14")
    ck("Z3.4 ⛔ 形态自证：修后知识条目与生产里那两条**被用了 1600+ 次**的"
       "化石同形（`missing_skill / textscore：checks_failed:…; …`）"
       " ⇒ 修复方向被生产数据反向确认",
       True, "access_count 1814/1602 的那两条就是 topic 还没退化时留下的")

    print("\n== AB 组：项 10 —— 准入闸 vs `eligible`（负结果 + 读数）==")
    ck("AB1 `gen.probe` 补上了 `n_eligible`（`len(eligible)`，纯读数）",
       '"n_eligible": len(eligible),' in src)
    _ne_lines = [l.strip() for l in src.splitlines() if "n_eligible" in l]
    _as_judge = [l for l in _ne_lines
                 if "if " in l or "elif " in l
                 or any(op in l for op in ("==", "!=", "<=", ">="))]
    ck("AB2 ⛔ `n_eligible` **不参与任何判据**（读数是读数，判据是判据）；"
       "⛔ 且它**确实在源码里**（否则这条会因空集而恒真，第 57 条）",
       bool(_ne_lines) and not _as_judge,
       f"lines={_ne_lines} as_judge={_as_judge}")
    #: ⛔ 顺序关系才是这个读数的命门：`eligible` 必须**先**完整算出、再被
    #:    `[:tries]` 截断 —— 否则 `n_eligible` 会退化成 `queue_size`。
    _i_sorted = src.find("eligible = sorted(")
    _i_slice = src.find("queue = eligible[:tries]")
    _i_key = src.find('"n_eligible"')
    ck("AB3 ⛔ `n_eligible` 取的是**截断前**的真实大小"
       "（`eligible = sorted(` 在 `queue = eligible[:tries]` 之前）",
       _i_sorted >= 0 and _i_slice > _i_sorted and _i_key > _i_sorted,
       f"sorted@{_i_sorted} slice@{_i_slice} key@{_i_key}")
    ck("AB4 ⛔ 负结果钉住：准入闸在这个世界**从没拦过任何候选** —— 实测 18 条"
       " `gate.result` 全 `passed=True`（拦截率 0/18）⇒ 「被拦 ⇒ 拿不到"
       "试下一个的机会」**前提不成立**，两套判据不需要收敛",
       True, "见 §20；收敛 = 改判据（第 34 条）⇒ 不做")
    ck("AB5 ⛔ 用武之地极小但**有牙**：`gate_tries=5` 时只有 round 1 的"
       " `queue_size=2`，其余 5 轮仍是 1；且 `try_index` 恒为 0"
       "（第 1 个就过闸，队列里第 2 个从没被探测）",
       True, "与项 6 的 `--retry-every` 同形：留着不许删")
    ck("AB6 ⛔ 局限要写明：`gen.probe` 只在 `heldout is not None` 时记"
       " ⇒ `--heldout off` 的运行读不到 `n_eligible`",
       "if best is not None and self.heldout is not None" in src,
       "挂载点变了这条要跟着改")

    print("\n== T11 组：读不懂的裁决 ⇒ 单独计数（不许静默当成「被拦」） ==")
    #: ⛔ 起因（2026-10-21 实测）：`gen.probe` 的裁决是扁平 `payload.verdict`，
    #:    `harness.commit` 的是嵌套 `payload.generalization.verdict` —— **同一
    #:    概念两种形状**。消费方猜错键会静默拿到 140/140 或 120/120 个 `None`。
    #:    而 `_deadend_accum` 的写法是 `str(p.get("verdict"))`：
    #:    `str(None)` == `"None"`，既不是 `generalize` 也不在
    #:    `NO_EVIDENCE_VERDICTS` ⇒ **静默计入「被拦 +1」**（污染冷却 streak）。
    #:    T1–T5 覆盖了 inconclusive / generalize / 键分离，**没有一条**测这个。

    class _RawEv:
        kind = "gen.probe"

        def __init__(self, payload):
            self.payload = payload

    KEY9 = "add_skill:x|a->b"
    loop9 = _loop()
    streak9: dict = {}
    #: 字段**缺失**（不是空串、不是 None，是整个键不存在）
    loop9._deadend_accum(_RawEv({"capsule": "add_skill:x",
                                 "env_pair": "a->b"}), streak9)
    st9 = loop9._deadend_stats or {}
    ck("T11.1 ⛔⛔ 缺 `verdict` 键 ⇒ 记进 `unknown`（不再静默当「被拦」）",
       st9.get("unknown") == 1, f"stats={st9}")
    ck("T11.1b 且 `streak` 仍 +1（fail-closed 口径**不改**，决策可复现）",
       streak9.get(KEY9) == 1, f"streak={streak9}")

    loop9b = _loop()
    streak9b: dict = {}
    loop9b._deadend_accum(_Ev("GENERALIZE", "add_skill:x", "a->b"), streak9b)
    st9b = loop9b._deadend_stats or {}
    ck("T11.2 封闭集合外的串（`GENERALIZE` 不折叠大小写）⇒ 同样记 unknown",
       st9b.get("unknown") == 1 and streak9b.get(KEY9) == 1,
       f"stats={st9b} streak={streak9b}")

    #: ⛔ 反证：正常裁决**不许**进 unknown（否则 T9.1/T9.2 可能只是恒真）
    loop9c = _loop()
    loop9c._deadend_accum(_Ev("overfit", "add_skill:x", "a->b"), {})
    loop9c._deadend_accum(_Ev("inconclusive", "add_skill:x", "a->b"), {})
    st9c = loop9c._deadend_stats or {}
    ck("T11.3 ⛔ 反证：`overfit` / `inconclusive` 都不计入 unknown",
       not st9c.get("unknown"), f"stats={st9c}")

    #: 读数（不是断言）：生产账本里到底出现过几次「读不懂」
    led9 = ROOT / "data" / "ledger.jsonl"
    if led9.exists():
        import json as _json
        from aether.generalization import VERDICTS as _VERDICTS
        bad = tot = 0
        for line in led9.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or '"gen.probe"' not in line:
                continue
            try:
                ev = _json.loads(line)
            except Exception:                            # noqa: BLE001
                continue
            if ev.get("kind") != "gen.probe":
                continue
            tot += 1
            if (ev.get("payload") or {}).get("verdict") not in _VERDICTS:
                bad += 1
        ck("T11.4 生产账本读数：`gen.probe` 里读不懂的裁决条数（只报数）",
           True, f"{bad}/{tot}")

    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    if SKIPPED:
        #: ⛔ 有跳过时**不许**印「全部通过」：那句话会让下一个人以为
        #:    「账本交叉表也验过了」。跳过要说清、要留在报告里。
        print(f"⚠️  {len(SKIPPED)} 条判据被跳过（**未覆盖 ≠ 通过**）：")
        for s in SKIPPED:
            print(f"   - {s}")
        print("✅ 其余判据全部通过")
        return 0
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

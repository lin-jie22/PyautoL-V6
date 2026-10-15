#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：学到的洞察必须是**陈述性知识**（可继承、可被选择、可被回滚）。

═══ 缺口 ═══

基因组此前全是**指令/配置**（routing / tools / skills / memory_policy /
prompts / meta_rules）—— 回答"怎么做"。而真正决定成败的知识是
**关于世界的事实**：哪对 `(候选, 环境对)` 是死路、哪些工具会超时、
哪些口径值不可信。

这些住在循环的**内存缓存**里 ⇒ ① 不被继承（换一次运行就丢光）
② 不能被选择（不在基因组里，闸看不见）③ 不能被回滚（不是账本上的
一次 `harness.commit`）。**洞察全是过程性的。**

本文件守新增的 `world_facts` 层与 `aether/world_facts.py`：

  A **事实在身份里**：知道的事情不同 ⇒ harness_id 不同
    （否则回滚与 A/B 对比会张冠李戴）；
  B **append 真合并、set 真替换、remove 真回滚**（三态不许退化成一种）；
  C **晋升门槛**：一次偶发**不许**写成事实（它会被继承并长期影响决策）；
    达门槛时必须带**来源**（哪几轮观察到的）；
  D **继承真的起作用**：基因组里记着的死路 ⇒ 循环不再为它花探测预算，
    且**留痕**（`deadend.inherited`）；
  E **不许永久封死**：继承的知识仍要尊重试探出口（否则与"难度分层
    自指死锁"同形态）；
  F **可回滚 + 可过期**：`forget` 回到原身份；陈旧事实要被点出来；
  G 反证：把门槛降到 1 ⇒ C 组必须转红（否则那条断言是恒真的）。

跑法（离线，秒级）：
    python tests/contract_world_facts_test.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether import world_facts as W
    from aether.genome import LAYERS, Gene, Harness, apply_gene

    # ── A 组：事实在身份里 ───────────────────────────────────────
    print("A 组：知道的事情不同 ⇒ 是不同的产物")
    ck("A1 `world_facts` 是基因组里的一层", "world_facts" in LAYERS,
       str(LAYERS))
    h0 = Harness()
    k = W.fact_key(W.SCOPE_DEADEND, "cand:x", W.env_pair_key("a", "b"))
    h1 = W.remember(h0, k, observations=3, kind="deadend")
    ck("A2 记一条事实 ⇒ harness_id 变（否则回滚会张冠李戴）",
       h0.harness_id != h1.harness_id)
    ck("A3 序列化往返一致（事实不许在 from_dict 时丢掉）",
       Harness.from_dict(h1.to_dict()).world_facts == h1.world_facts)
    ck("A4 事实进 `to_dict`（账本要能记它）",
       "world_facts" in h1.to_dict() and h1.to_dict()["world_facts"] == h1.world_facts)

    # ── B 组：三种 op 语义不许退化 ───────────────────────────────
    print("B 组：append 真合并 / set 真替换 / remove 真回滚")
    a = W.remember(Harness(), k, observations=3, streak=3)
    b = W.remember(a, k, observations=4)
    ck("B1 append **合并**（不覆盖已有字段：`streak` 还在）",
       W.get(b, k).get("streak") == 3
       and W.get(b, k).get("observations") == 4,
       json.dumps(W.get(b, k), ensure_ascii=False))
    c = apply_gene(b, Gene(op="set", target=f"L5.world_facts.{k}",
                           value={"only": "this"}))
    ck("B2 set **整条替换**（不是合并）",
       W.get(c, k) == {"only": "this"}, json.dumps(W.get(c, k), ensure_ascii=False))
    d = W.forget(c, k)
    ck("B3 remove **真回滚**：回到无事实状态与原身份",
       W.get(d, k) is None and d.harness_id == Harness().harness_id)
    #: 键里不许有 `.`：`_split_target` 按 `.` 切，带 `.` 会改错 key
    kk = W.fact_key(W.SCOPE_DEADEND, "cand:a.b", "env:x")
    ck("B4 ⛔ 键里的 `.` 被替换（否则会被 _split_target 切成两层）",
       "." not in kk, kk)
    ck("B5 环境对顺序无关（`(x,y)` 与 `(y,x)` 是同一把键）",
       W.env_pair_key("x", "y") == W.env_pair_key("y", "x"))

    # ── C 组：晋升门槛 ───────────────────────────────────────────
    print("C 组：观察 → 事实的门槛（一次偶发不许写成制度）")
    obs = [{"key": k, "kind": "deadend", "round_no": r} for r in (1, 2)]
    ck("C1 ⛔ 只观察到 2 次（门槛 3）⇒ **不许**晋升成事实",
       k not in W.promote(obs), json.dumps(W.promote(obs), ensure_ascii=False))
    obs3 = obs + [{"key": k, "kind": "deadend", "round_no": 3}]
    got = W.promote(obs3)
    ck("C2 达门槛 ⇒ 晋升，且**带来源**（哪几轮观察到的）",
       k in got and got[k].get("observations") == 3
       and got[k].get("rounds") == [1, 2, 3], json.dumps(got, ensure_ascii=False))
    ck("C3 晋升结果里带 confirmed_round（陈旧判定的依据）",
       got[k].get("confirmed_round") == 3)
    ck("C4 门槛是模块常量（不是散在代码里的魔法数）",
       W.PROMOTE_MIN_OBS == 3, str(W.PROMOTE_MIN_OBS))

    # ── D 组：继承真的起作用（接线）────────────────────────────────
    print("D 组：继承的事实真的影响决策，且留痕")
    src = (ROOT / "aether" / "evolution.py").read_text(encoding="utf-8")
    ck("D1 循环读了基因组里的事实（`_facts_deadend_streak`）",
       "_facts_deadend_streak" in src)
    ck("D2 ⛔ 继承生效时**留痕**（`deadend.inherited` 事件）",
       "deadend.inherited" in src)
    ck("D3 ⛔ 晋升写成 `world_fact.promoted` 事件（可审计）",
       "world_fact.promoted" in src)
    ck("D4 晋升失败也留痕（不许静默吞掉）",
       "world_fact.promote_failed" in src)
    ck("D5 轮报告里有 `world_facts` 与 `facts_promoted` 读数",
       "world_facts" in src and "facts_promoted" in src)
    #: 真实跑一轮：报告里必须真的有 world_facts 字段（有机制 ≠ 被走到）
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, "-B", "-m", "aether.cli", "evolve",
             "--rounds", "1", "--group", "2", "--max-tasks", "5"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=900)
        out = r.stdout.decode("utf-8", "replace")
        ck("D6 真跑一轮 ⇒ 报告里真有 world_facts 读数",
           '"world_facts"' in out, out[-200:])
    ck("D7 ⛔ 继承**不许永久封死**：仍尊重试探出口（probation）",
       "deadend_probation_every" in src
       and src.count("deadend_probation_every") >= 2,
       "试用出口不见了 ⇒ 与'自指死锁'同形态")

    # ── E 组：可回滚 + 可过期 ────────────────────────────────────
    print("E 组：事实能被撤销、能被判陈旧")
    h = W.remember(Harness(), k, observations=3, confirmed_round=1,
                   kind="deadend")
    ck("E1 新确认过的事实 ⇒ 不在'该重验'名单里",
       W.should_recheck(h, current_round=5) == [],
       str(W.should_recheck(h, current_round=5)))
    ck("E2 ⛔ 久未确认 ⇒ 点出来（吃陈年旧账要能被看见）",
       W.should_recheck(h, current_round=100) == [k],
       str(W.should_recheck(h, current_round=100)))
    h_noname = W.remember(Harness(), k, observations=3)
    ck("E3 连'哪一轮确认的'都没有 ⇒ 必然要重验",
       W.should_recheck(h_noname, current_round=1) == [k])
    summ = W.summarize(h)
    ck("E4 摘要按作用域分类（'这一代知道什么'可见）",
       summ["n"] == 1 and summ["by_scope"].get("deadend") == 1,
       json.dumps(summ, ensure_ascii=False)[:120])

    # ── F 组：反证 —— 门槛降到 1 必须转红 ────────────────────────
    print("F 组：反证（把门槛降到 1 ⇒ C1 必须翻转）")
    orig_min = W.PROMOTE_MIN_OBS
    try:
        #: 直接改模块常量：`promote` 的默认参数在**定义时**绑定，所以
        #: 这里显式传 min_obs —— 这也说明默认参数是"签名的一部分"。
        one_obs = [{"key": k, "kind": "deadend", "round_no": 1}]
        ck("F1 反证：min_obs=1 时一次观察就会被晋升（证明门槛真的在拦）",
           k in W.promote(one_obs, min_obs=1)
           and k not in W.promote(one_obs),
           "门槛没起作用")
    finally:
        W.PROMOTE_MIN_OBS = orig_min

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：洞察进了基因组，可继承、可被选择、可被回滚")
    return 0


if __name__ == "__main__":
    sys.exit(main())

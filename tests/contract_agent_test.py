#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：harness 的 5 层**真的**影响 reward（不是装饰）。

上一版的 runner 只读 `h.skills` ⇒ 5 层里只有 L3 有后果 ⇒ 改 L0/L1/L2/L4
对结果毫无影响 ⇒ 进化循环永远不可能发现"路由没接通""工具没开放"
"记忆召回不够"这类真实弱点。5 层基因组是装饰性的。

本文件逐层做**单变量对照**：只动这一层，reward 必须改变。

    L0 meta_rules    元规则可**禁用**具体修法 ⇒ "改 L0 需人工审批"真的有后果
    L1 routing       技能必须被路由到才可达（⇒ Capsule 必须存在）
    L2 tools         缺 read_file / python_exec ⇒ 连判据都跑不了
    L3 skills        修法本身
    L4 memory_policy 技能不可达时的第二条通路；recall_topk=0 ⇒ 这条路断掉

以及反证组（D）：把每一层的判据改成恒通/恒断，对应组必须翻转。

跑法：
    python tests/contract_agent_test.py
"""
import sys
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    import aether.agent as AG
    from aether.agent import HarnessAgent, directives
    from aether.environment import Environment, mutate_source
    from aether.genome import Capsule, Gene, Harness, apply_capsule
    from aether.memory import MemoryOS

    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    env2 = Environment.from_dir(ROOT / "tasks" / "eventflow")
    broken, _desc = mutate_source(env.source, which=2)     # 比较符变异
    broken2, _ = mutate_source(env2.source, which=3)       # 另一个环境的同类变异
    envs = {env.env_id: env, env2.env_id: env2}
    SPEC = {"env_id": env.env_id, "broken_source": broken,
            "topic": env.env_id, "task_class": "repair"}
    SPEC_B = {"env_id": env2.env_id, "broken_source": broken2,
              "topic": env2.env_id, "task_class": "repair"}

    agent = HarnessAgent(envs)

    def H(**kw):
        base = dict(meta_rules="V6 元规则：奖励只来自执行判据。",
                    routing={}, tools=("read_file", "write_file", "python_exec"),
                    skills={}, memory_policy={})
        base.update(kw)
        return Harness(**base)

    def reward(h, spec=None):
        return agent.run(h, spec or SPEC).verdict.reward

    def with_skill(name="fix_control_flow", route=True, **kw):
        r = {"repair": name} if route else {}
        return H(skills={name: "修法"}, routing=r, **kw)

    # ── A 组：L3 技能层 ──────────────────────────────────────────
    print("A 组：L3 技能层（修法本身）")
    ck("A1 无技能 ⇒ reward 0", reward(H()) == 0.0)
    ck("A2 有技能且可达 ⇒ reward 1", reward(with_skill()) == 1.0)

    # ── B 组：L1 路由层（⇒ Capsule 必须存在）─────────────────────
    print("B 组：L1 路由层（技能不可达 = 没有技能）")
    ck("B1 有技能但**没路由** ⇒ reward 0（技能不可达）",
       reward(with_skill(route=False)) == 0.0)
    ck("B2 路由指向不存在的技能 ⇒ reward 0",
       reward(H(skills={}, routing={"repair": "fix_control_flow"})) == 0.0)
    # ⛔ 路由**键的选择本身就是泛化维度**：
    #    按任务类别路由 ⇒ 换环境照样可达；按环境名路由 ⇒ 换环境立刻失效。
    ck("B3 按**任务类别**路由 ⇒ 两个环境的同类任务都能修（1/1）",
       reward(H(skills={"fix_control_flow": "x"},
                routing={"repair": "fix_control_flow"})) == 1.0
       and reward(H(skills={"fix_control_flow": "x"},
                    routing={"repair": "fix_control_flow"}), SPEC_B) == 1.0)
    ck("B4 按**环境名**路由 ⇒ 只在本环境能修，换环境立刻失效（1/0）",
       reward(H(skills={"fix_control_flow": "x"},
                routing={env.env_id: "fix_control_flow"})) == 1.0
       and reward(H(skills={"fix_control_flow": "x"},
                    routing={env.env_id: "fix_control_flow"}), SPEC_B) == 0.0)
    ck("B5 胶囊（技能+路由）⇒ reward 1；单基因（只加技能）⇒ reward 0"
       " ⇒ Capsule 不是装饰",
       reward(apply_capsule(H(), Capsule(
           name="capsule", genes=(
               Gene(op="set", target="L3.skills.fix_control_flow", value="修法"),
               Gene(op="set", target="L1.routing.repair",
                    value="fix_control_flow"))))) == 1.0
       and reward(apply_capsule(H(), Capsule(
           name="gene_only", genes=(
               Gene(op="set", target="L3.skills.fix_control_flow",
                    value="修法"),)))) == 0.0)

    # ── C 组：L2 工具层 ──────────────────────────────────────────
    print("C 组：L2 工具层（缺工具 ⇒ 连判据都跑不了）")
    ck("C1 缺 python_exec ⇒ reward 0（即使技能路由都齐）",
       reward(with_skill(tools=("read_file",))) == 0.0)
    ck("C2 缺 read_file ⇒ reward 0",
       reward(with_skill(tools=("python_exec",))) == 0.0)
    r_c1 = agent.run(with_skill(tools=("read_file",)), SPEC)
    ck("C3 缺工具被**记账**（不是静默当通过）", bool(r_c1.verdict.error),
       r_c1.verdict.error or "")
    ck("C4 缺工具时 evidence 记了层（能归因到 L2）",
       any(s.layer == "L2" and not s.ok for s in r_c1.steps),
       str([s.layer for s in r_c1.steps]))

    # ── D 组：L0 元规则层 ────────────────────────────────────────
    print("D 组：L0 元规则层（改 L0 真的会改变能力边界）")
    ck("D1 指令解析：识别中文指令",
       "no_full_replace" in directives("禁止整文件替换"))
    ck("D2 未登记的元规则 ⇒ 不产生任何指令（fail-open 到无约束）",
       directives("随便写一句人话") == frozenset())
    h_patch = with_skill(name=f"patch:{env.env_id}")
    ck("D3 定点修法在无禁令时可用 ⇒ reward 1", reward(h_patch) == 1.0)
    ck("D4 元规则【禁止整文件替换】⇒ 定点修法被禁用 ⇒ reward 0",
       reward(with_skill(name=f"patch:{env.env_id}",
                         meta_rules="V6 元规则；禁止整文件替换。")) == 0.0)
    ck("D5 同一条禁令**不影响**保守通用修法（禁令是有针对性的）",
       reward(with_skill(meta_rules="V6 元规则；禁止整文件替换。")) == 1.0)

    # ── E 组：L4 记忆层（第二条通路）────────────────────────────
    print("E 组：L4 记忆层（技能不可达时的第二条路）")
    mem = MemoryOS(min_content_len=0)
    mem.write("L2", f"{env.env_id} 的修法经验 repair_hint:{env.env_id} "
                    f"（fingerprint={env.fingerprint}）：失败模式是控制流，"
                    f"用保守修法 revert_control_flow。",
              channel="curated", source="test", tags=("experience", "exec"))
    agent_mem = HarnessAgent(envs, memory=mem)
    ck("E1 无技能 + recall_topk=0 ⇒ reward 0（记忆通路关闭）",
       agent_mem.run(H(memory_policy={"recall_topk": 0}), SPEC).verdict.reward
       == 0.0)
    r_mem = agent_mem.run(H(memory_policy={"recall_topk": 5}), SPEC)
    ck("E2 无技能 + recall_topk=5 ⇒ 走记忆通路（strategy=memory_hint），reward 1",
       r_mem.strategy == "memory_hint" and r_mem.verdict.reward == 1.0,
       f"{r_mem.strategy}/{r_mem.verdict.reward}")
    ck("E3 记忆里没有修法经验 ⇒ reward 0（召回不等于有用）",
       HarnessAgent(envs, memory=MemoryOS(min_content_len=0)).run(
           H(memory_policy={"recall_topk": 5}), SPEC).verdict.reward == 0.0)
    # ⛔ E4 是本轮（2026-10-13）最关键的教训，必须有契约守着：
    #    记忆里如果存"整份基线/答案"，L4 一召回就把 reward 顶到 1.0，
    #    基线随即到达天花板 ⇒ 再也挖不到弱点 ⇒ 进化循环永久空转，
    #    而所有数字都很好看。记忆必须只给**修法**，且修法**修不全**。
    from aether.repair import classify_difficulty
    hard_src = None
    for w in range(0, 12):
        try:
            s, _d = mutate_source(env.source, which=w)
        except Exception:  # noqa: BLE001
            continue
        if s != env.source and classify_difficulty(env, s) == "hard":
            hard_src = s
            break
    if hard_src is None:
        ck("E4 （跳过：本环境未合成出 hard 变异）", True)
    else:
        SPEC_HARD = dict(SPEC, broken_source=hard_src)
        ck("E4 记忆通路在 hard 题上 reward 0（记忆给的是**修法**，不是答案）"
           " ⇒ 不会把基线顶到天花板、不会掩盖弱点",
           agent_mem.run(H(memory_policy={"recall_topk": 5}),
                         SPEC_HARD).verdict.reward == 0.0)
        ck("E4b 同一道题上，整文件替换能拿 1.0 ⇒ E4 的 0 确实来自"
           "“记忆修法修不全”，不是题本身无解",
           reward(with_skill(name=f"patch:{env.env_id}"), SPEC_HARD) == 1.0)

    # ── F 组：反证（证明每组判的不是恒 0/恒 1）──────────────────
    print("F 组：反证 —— 改掉判据，对应组必须翻转")
    orig_route_ok = HarnessAgent.run

    def run_always_routed(self, h, spec):
        """事故版：路由恒通（无视 harness 里有没有路由）。"""
        hh = Harness(meta_rules=h.meta_rules,
                     routing={"repair": next(iter(h.skills), "")}
                     if h.skills else {},
                     tools=h.tools, skills=h.skills,
                     memory_policy=h.memory_policy)
        return orig_route_ok(self, hh, spec)

    HarnessAgent.run = run_always_routed
    ck("F1 事故版：路由恒通 ⇒ B1 必须翻转（无路由也变 1）",
       HarnessAgent(envs).run(with_skill(route=False), SPEC).verdict.reward == 1.0)
    HarnessAgent.run = orig_route_ok

    orig_directives = AG.DIRECTIVES
    AG.DIRECTIVES = {}
    try:
        ck("F2 事故版：指令表清空 ⇒ D4 必须翻转（禁令失效，reward 回 1）",
           reward(with_skill(name=f"patch:{env.env_id}",
                             meta_rules="V6 元规则；禁止整文件替换。")) == 1.0)
    finally:
        AG.DIRECTIVES = orig_directives

    orig_tools = AG.REQUIRED_TOOLS
    AG.REQUIRED_TOOLS = ()
    try:
        ck("F3 事故版：必需工具表清空 ⇒ C1 必须翻转（缺 python_exec 也能跑）",
           reward(with_skill(tools=("read_file",))) == 1.0)
    finally:
        AG.REQUIRED_TOOLS = orig_tools

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

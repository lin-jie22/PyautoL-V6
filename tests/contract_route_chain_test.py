#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：L1 路由是**链**（能力可叠加），不是单值。

## 它为什么必须存在（不是"多加一个字段"）

2026-10-14 实测：加了一维新的通用能力 `fix_literals` 之后，闭环**仍然**
停滞，只是原因从 `saturated:stagnated` 变成 `no_candidate_passed`。证据：

    fix_literals 的 dev_by_difficulty = {hard: +1.0, easy: −1.0}

它修好了 hard 层，**却把 easy 层打回 0**。根因是 `L1.routing[repair]` 是
**单值**：装一个新技能就把旧的顶掉 ⇒ 两个通用能力维度互斥 ⇒ 加再多通用
修法也叠不上。

链的语义是**能力叠加**：链上每项依次应用到同一个源码，末尾**只跑一次**
判据；新技能 `append` 到链尾，旧的仍在链上。

⛔ 刻意不做「依次尝试直到通过」：那要为每项各跑一次执行判据 ⇒ reward
   变成"试出来的"，违反「reward 只来自一次执行」，且成本随链长线性增长
   （观测改变了被测成本）。

## 契约（六组，每组配反证）

    A parse_route 是路由语义的**唯一解释处**（三种写法归一）
    B apply_gene 的 append 不顶掉旧项 / remove 不连坐
    C agent 真的叠加（单技能各修一层，链两层都修）
    D L0 禁令只摘被禁项，**不连坐**链上未被禁的项
    E 归因：chain / applied 字段记清"谁真的改了源码"
    F 提议器产出 append（不是 set 覆盖）

跑法（离线，秒级）：
    python tests/contract_route_chain_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import Environment, synthesize_tasks   # noqa: E402
from aether.genome import (Gene, Harness, apply_gene,          # noqa: E402
                           parse_route)
from aether.repair import classify_difficulty                  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


# ── 样本：只取一个环境，保证秒级 ──────────────────────────────
ENV_NAME = "textscore"
ENV = Environment.from_dir(ROOT / "tasks" / ENV_NAME)
TASKS = synthesize_tasks(ENV, max_n=12).tasks
EASY = [t for t in TASKS if classify_difficulty(ENV, t.broken_source) == "easy"]
HARD = [t for t in TASKS if classify_difficulty(ENV, t.broken_source) == "hard"]
print(f"== 样本：easy {len(EASY)} 条 / hard {len(HARD)} 条（{ENV_NAME}）==")
ck("样本：两层都采到了（否则叠加无从对照）", len(EASY) > 0 and len(HARD) > 0,
   f"easy={len(EASY)} hard={len(HARD)}")


# ══════════════════════════════════════════════════════════════
print("\n== A 组：parse_route —— 路由语义的唯一解释处 ==")
ck("A1 单值 str ⇒ 单元素链", parse_route("fix_control_flow")
   == ("fix_control_flow",))
ck("A2 串写 `a|b` ⇒ 两项链（LLM 友好写法）",
   parse_route("a|b") == ("a", "b"))
ck("A3 结构化 list ⇒ 与串写**同解**（写侧与读侧不会漂移）",
   parse_route(["a", "b"]) == parse_route("a|b"))
ck("A4 空值 ⇒ 空链（不是 ('',) 这种假单值）",
   parse_route("") == () and parse_route(None) == ())
ck("A5 空白项被丢弃（'a||b' 不会产出空技能名）",
   parse_route("a||b") == ("a", "b"))

# 反证：parse_route 恒返回空 ⇒ 链整体失效 ⇒ C 组必然转红
import aether.agent as AG                                     # noqa: E402
_orig_parse = AG.parse_route
AG.parse_route = lambda v: ()
try:
    from aether.agent import HarnessAgent
    _a = HarnessAgent({ENV_NAME: ENV})
    _h = Harness(meta_rules="", tools=("read_file", "python_exec"),
                 skills={"fix_control_flow": "x", "fix_literals": "y"},
                 routing={"repair": ["fix_control_flow", "fix_literals"]},
                 memory_policy={})
    _rw = _a.run(_h, {"env_id": ENV_NAME,
                      "broken_source": EASY[0].broken_source,
                      "topic": ENV_NAME, "task_class": "repair"}).verdict.reward
    ck("A6 反证：parse_route 恒空 ⇒ 链失效 ⇒ 必须转红"
       "（否则说明 agent 根本没走 parse_route）", float(_rw) == 0.0,
       f"reward={_rw}")
finally:
    AG.parse_route = _orig_parse


# ══════════════════════════════════════════════════════════════
print("\n== B 组：apply_gene —— append 不顶掉旧项 ==")
h0 = Harness(meta_rules="", tools=(), skills={}, routing={}, memory_policy={})
h1 = apply_gene(h0, Gene(op="append", target="L1.routing.repair",
                         value="fix_control_flow"))
ck("B1 空路由 append ⇒ 链长出一项",
   parse_route(h1.routing.get("repair")) == ("fix_control_flow",))
ck("B1b 单元素仍存成 **str**（向后兼容：既有 harness/账本/断言不漂）",
   isinstance(h1.routing.get("repair"), str),
   f"实得 {type(h1.routing.get('repair')).__name__}")

h2 = apply_gene(h1, Gene(op="append", target="L1.routing.repair",
                         value="fix_literals"))
ck("B2 ⛔ 核心：append 新技能 ⇒ **旧的仍在链上**（不顶掉）",
   parse_route(h2.routing.get("repair"))
   == ("fix_control_flow", "fix_literals"),
   f"实得 {h2.routing.get('repair')}")

h2b = apply_gene(h2, Gene(op="append", target="L1.routing.repair",
                          value="fix_literals"))
ck("B3 重复 append 同一项 ⇒ 不产生重复项",
   parse_route(h2b.routing.get("repair"))
   == ("fix_control_flow", "fix_literals"), f"{h2b.routing.get('repair')}")

h3 = apply_gene(h2, Gene(op="set", target="L1.routing.repair",
                         value="fix_literals"))
ck("B4 反证：op=set（旧单值行为）⇒ 旧项**被顶掉**"
   " ⇒ 证明 B2 不是恒真断言",
   parse_route(h3.routing.get("repair")) == ("fix_literals",),
   f"{h3.routing.get('repair')}")

h4 = apply_gene(h2, Gene(op="remove", target="L1.routing.repair",
                         value="fix_control_flow"))
ck("B5 remove 只摘点名的那一项，**其余保留**（不连坐）",
   parse_route(h4.routing.get("repair")) == ("fix_literals",),
   f"{h4.routing.get('repair')}")


# ══════════════════════════════════════════════════════════════
print("\n== C 组：agent 真的叠加（两个维度同时生效）==")
from aether.agent import HarnessAgent                          # noqa: E402
agent = HarnessAgent({ENV_NAME: ENV})
SKILLS = {"fix_control_flow": "保守通用修法", "fix_literals": "字面量修法"}


def H(routing):
    return Harness(meta_rules="V6 元规则：奖励只来自执行判据。",
                   tools=("read_file", "write_file", "python_exec"),
                   skills=dict(SKILLS),
                   routing={"repair": routing} if routing else {},
                   memory_policy={})


def run(routing, task):
    return float(agent.run(
        H(routing), {"env_id": ENV_NAME, "broken_source": task.broken_source,
                     "topic": ENV_NAME, "task_class": "repair"}).verdict.reward)


c_easy_cf = run("fix_control_flow", EASY[0])
c_hard_cf = run("fix_control_flow", HARD[0])
c_easy_lit = run("fix_literals", EASY[0])
c_hard_lit = run("fix_literals", HARD[0])
ck("C1 单 fix_control_flow：easy 修好、hard 修不好",
   c_easy_cf >= 1.0 and c_hard_cf == 0.0,
   f"easy={c_easy_cf} hard={c_hard_cf}")
ck("C2 单 fix_literals：hard 修好、easy 修不好",
   c_easy_lit == 0.0 and c_hard_lit >= 1.0,
   f"easy={c_easy_lit} hard={c_hard_lit}")

chain = ["fix_control_flow", "fix_literals"]
c_easy_chain = run(chain, EASY[0])
c_hard_chain = run(chain, HARD[0])
ck("C3 ⛔ 核心：链 ⇒ 两层**同时**修好（单技能做不到）",
   c_easy_chain >= 1.0 and c_hard_chain >= 1.0,
   f"easy={c_easy_chain} hard={c_hard_chain}")
ck("C4 反证：链退化成单值（旧行为）⇒ easy 掉回 0"
   " ⇒ 证明 C3 的 1.0 确实来自叠加",
   run("fix_literals", EASY[0]) == 0.0)
ck("C5 顺序无关：换序后两层仍都修好（叠加无隐式耦合）",
   run(["fix_literals", "fix_control_flow"], EASY[0]) >= 1.0
   and run(["fix_literals", "fix_control_flow"], HARD[0]) >= 1.0)


# ══════════════════════════════════════════════════════════════
print("\n== D 组：L0 禁令只摘被禁项，不连坐 ==")
h_patch_chain = Harness(
    meta_rules="V6 元规则；禁止整文件替换。",
    tools=("read_file", "write_file", "python_exec"),
    skills={"patch:" + ENV_NAME: "定点", "fix_control_flow": "通用"},
    routing={"repair": ["patch:" + ENV_NAME, "fix_control_flow"]},
    memory_policy={})
r_d = agent.run(h_patch_chain, {"env_id": ENV_NAME,
                                "broken_source": EASY[0].broken_source,
                                "topic": ENV_NAME, "task_class": "repair"})
ck("D1 链上被禁项被摘、未禁项仍在 ⇒ 控制流题仍修好（不连坐）",
   float(r_d.verdict.reward) >= 1.0, f"reward={r_d.verdict.reward}")
ck("D2 被禁有**记账**（L0 step ok=False 且点名被摘项）",
   any(s.layer == "L0" and not s.ok and "patch:" in s.detail
       for s in r_d.steps),
   str([(s.layer, s.ok, s.detail[:50]) for s in r_d.steps]))
ck("D3 归因：生效清单里只有未被禁的那项",
   (r_d.verdict.evidence or {}).get("applied") == ["fix_control_flow"],
   f"{r_d.verdict.evidence}")


# ══════════════════════════════════════════════════════════════
print("\n== E 组：归因与降级（链上缺失项不拖垮整链）==")
r_e = agent.run(H(chain), {"env_id": ENV_NAME,
                           "broken_source": EASY[0].broken_source,
                           "topic": ENV_NAME, "task_class": "repair"})
ev = r_e.verdict.evidence or {}
ck("E1 evidence.chain 记下完整链（事后能复盘【当时链上有什么】）",
   ev.get("chain") == chain, f"{ev.get('chain')}")
ck("E2 evidence.applied 记下**真的生效**的项",
   ev.get("applied") == chain, f"{ev.get('applied')}")

h_missing = Harness(
    meta_rules="", tools=("read_file", "write_file", "python_exec"),
    skills={"fix_control_flow": "通用"},          # fix_literals **没装**
    routing={"repair": ["fix_control_flow", "fix_literals"]},
    memory_policy={})
r_e3 = agent.run(h_missing, {"env_id": ENV_NAME,
                             "broken_source": EASY[0].broken_source,
                             "topic": ENV_NAME, "task_class": "repair"})
ck("E3 链上缺技能 ⇒ 该项跳过并**记账**，链继续（其余项仍生效）",
   float(r_e3.verdict.reward) >= 1.0
   and any(s.layer == "L1" and "fix_literals" in s.detail
           for s in r_e3.steps),
   f"reward={r_e3.verdict.reward}")
ck("E4 缺失项**不进** applied（归因不含没做的事）",
   (r_e3.verdict.evidence or {}).get("applied") == ["fix_control_flow"],
   f"{r_e3.verdict.evidence}")


# ══════════════════════════════════════════════════════════════
print("\n== F 组：提议器产出 append（不是 set 覆盖）==")
from aether.providers import TemplateProposer                  # noqa: E402
p = TemplateProposer(skills={"fix_control_flow": "a", "fix_literals": "b"},
                     route_key="repair")
caps = p.propose({"kind": "missing_skill"}, H("fix_control_flow"))
ops = {g.op for c in caps for g in c.genes
       if g.target.endswith("L1.routing.repair") or "routing" in g.target}
ck("F1 已有路由时，新技能用 **append**（不是 set）",
   ops == {"append"}, f"ops={ops}")
ck("F2 已在链上的技能**不再重复提**（否则提议器原地踏步）",
   all(not any(g.value == "fix_control_flow" for g in c.genes
               if "routing" in g.target) for c in caps),
   f"提出的候选={[c.name for c in caps]}")

# 反证：把 append 换成 set ⇒ 旧技能从链上消失（B2 的端到端版本）
_bad = [Gene(op="set", target="L1.routing.repair", value="fix_literals")
        if g.op == "append" and "routing" in g.target else g
        for c in caps for g in c.genes]
h_bad = H("fix_control_flow")
for g in _bad:
    h_bad = apply_gene(h_bad, g)
ck("F3 反证：append 换成 set ⇒ 旧技能从链上消失"
   " ⇒ 证明 F1 的 append 不是摆设",
   parse_route(h_bad.routing.get("repair")) == ("fix_literals",),
   f"{h_bad.routing.get('repair')}")

# ══════════════════════════════════════════════════════════════
print("\n== G 组：UCI 绑定与 apply_gene 走**同一个写入点** ==")
from aether.interop import (CapabilitySpec, Gateway, ParamSpec,   # noqa: E402
                            Registry, bind_capability)
from aether.verifier import Check                                 # noqa: E402

h0 = H("fix_control_flow")
h_bind = bind_capability(h0, "ext.litfix", "外部字面量修法",
                         route_from="repair")
ck("G1 ⛔ 核心：绑定 UCI 能力 ⇒ **追加**到链尾，不顶掉已有技能",
   parse_route(h_bind.routing.get("repair"))
   == ("fix_control_flow", "uci:ext.litfix"),
   f"{h_bind.routing.get('repair')!r}")

h_bind2 = bind_capability(h_bind, "ext.litfix", "外部字面量修法",
                          route_from="repair")
ck("G2 幂等：重复绑同一个能力 ⇒ 链上不出现重复项",
   parse_route(h_bind2.routing.get("repair"))
   == ("fix_control_flow", "uci:ext.litfix"),
   f"{h_bind2.routing.get('repair')!r}")

h_over = bind_capability(h0, "ext.litfix", route_from="repair",
                         append=False)
ck("G3 反证：append=False（旧行为）⇒ 旧技能从链上消失"
   " ⇒ 证明 G1 的追加不是摆设",
   parse_route(h_over.routing.get("repair")) == ("uci:ext.litfix",),
   f"{h_over.routing.get('repair')!r}")

#: ⛔ 同源的核心断言：两条写入路径产出的 **routing 值**必须逐字相同。
#:    各写一份就会在这里分叉。
#: ⛔ 但**不能**断言 harness_id 相同 —— `bind_capability` 还会把能力写进
#:    `skills`（绑定 = 声明"这个 harness 有这个技能"），而 `apply_gene`
#:    的路由基因只改路由。两者本来就不是一个东西，把它们捆成一个断言
#:    ⇒ 又是一对分不开的孪生故障。
h_gene = apply_gene(h0, Gene(op="append", target="L1.routing.repair",
                             value="uci:ext.litfix"))
ck("G4 ⛔ 同源：bind_capability 与 apply_gene(append) 写出的"
   "**路由值**逐字相同",
   h_gene.routing.get("repair") == h_bind.routing.get("repair"),
   f"gene={h_gene.routing.get('repair')!r} "
   f"bind={h_bind.routing.get('repair')!r}")

#: 顺序无关：先绑再 append 同一个能力 ⇒ 链不变（幂等可交换）
h_again = apply_gene(h_bind, Gene(op="append", target="L1.routing.repair",
                                  value="uci:ext.litfix"))
ck("G4b 顺序无关：绑过之后再 append 同一能力 ⇒ 链不变（幂等）",
   parse_route(h_again.routing.get("repair"))
   == ("fix_control_flow", "uci:ext.litfix"),
   f"{h_again.routing.get('repair')!r}")


def _ext_litfix(source: str, baseline: str) -> str:
    """外部实现（**刻意不复用** repair.py）：把数值常量按遍历序对齐基线。

    ⛔ 它存在的意义是证明"外部技能"在结构上跟内置技能**平权** ——
       走同一条链、同一套判据，没有任何特权通道。
    """
    import ast as _ast
    b = _ast.parse(source)
    g = _ast.parse(baseline)

    def nums(tree):
        return [n for n in _ast.walk(tree)
                if isinstance(n, _ast.Constant)
                and isinstance(n.value, (int, float))
                and not isinstance(n.value, bool)]

    bo, go = nums(b), nums(g)
    for i in range(min(len(bo), len(go))):
        bo[i].value = go[i].value
    _ast.fix_missing_locations(b)
    return _ast.unparse(b)


reg = Registry()
reg.register(CapabilitySpec(
    id="ext.litfix", kind="skill", trusted=True,
    inputs={"source": ParamSpec("str"), "baseline": ParamSpec("str", False),
            "spec": ParamSpec("dict", False)},
    outputs={"source": ParamSpec("str")},
    checker=lambda a, o: Check("changed", o.get("source") != a["source"],
                               "源码有改动")),
    lambda source, baseline="", spec=None: {
        "source": _ext_litfix(source, baseline)})

h_mix = bind_capability(H("fix_control_flow"), "ext.litfix",
                        route_from="repair")
r_g = HarnessAgent({ENV_NAME: ENV}, gateway=Gateway(reg)).run(
    h_mix, {"env_id": ENV_NAME, "broken_source": HARD[0].broken_source,
            "topic": ENV_NAME, "task_class": "repair"})
_applied = (r_g.verdict.evidence or {}).get("applied")
ck("G5 ⛔ 端到端：链上【内置 + UCI】两项**都真执行** ⇒ 叠加生效",
   _applied == ["fix_control_flow", "uci:ext.litfix"]
   and float(r_g.verdict.reward) >= 1.0,
   f"applied={_applied} reward={r_g.verdict.reward}")

print()
if FAILS:
    print(f"❌ {len(FAILS)} 条失败：{FAILS}")
    sys.exit(1)
print("✅ 全部通过")
sys.exit(0)

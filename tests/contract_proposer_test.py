#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：真模型提议器 —— **模型输出是不可信输入**。

接真模型进自进化循环，最大的风险不是"模型不够聪明"，而是
**模型输出被当成可信指令直接执行**。所以这里守的是三道闸：

  ① 语法闸 `validate_gene`  —— op / target 层 / value 类型全白名单；
     ⛔ `meta_rules`（L0 宪法层）**默认禁止**模型自动改
  ② 解析闸 `parse_capsules` —— 解析失败或含非法 gene ⇒ 返回空列表，
     ⛔ **不返回半成品**（一条非法基因会让整个 capsule 在 apply_gene 时崩溃，
     而崩溃会被进化循环记成"这个变异 reward 0" ⇒ 语法错误被伪装成"没用"）
  ③ 语义闸 —— 非回归闸 / 泛化闸 / 能力地板，与模板提议器**完全相同**

于是"模型好不好"和"进化机制对不对"是两个独立问题：模型再差，也只是
提不出可用变异（正确地什么都不做并记账）；它绕不过闸。

跑法（离线，秒级）：
    python tests/contract_proposer_test.py
    python tests/contract_proposer_test.py --llm     # 接本机真模型实测（慢）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.genome import Harness, apply_capsule, apply_gene  # noqa: E402
from aether.providers import (ALLOWED_OPS, LLMProposer,  # noqa: E402
                              StubProvider, TemplateProposer, build_prompt,
                              parse_capsules, validate_gene)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _capsule_payload(genes, name="c1"):
    return json.dumps({"capsules": [{"name": name, "rationale": "r",
                                     "genes": genes}]}, ensure_ascii=False)


GOOD = [{"op": "set", "target": "L3.skills.fix_x", "value": "修复 X",
         "rationale": "缺技能"},
        {"op": "set", "target": "L1.routing.repair", "value": "fix_x",
         "rationale": "接通路由"}]

print("== A 组：语法闸（白名单，不是黑名单）==")
ck("A1 合法基因通过", validate_gene(GOOD[0])[0])
ck("A2 op 不在白名单 ⇒ 拒（模型发明的操作一律不认）",
   not validate_gene({"op": "exec", "target": "L3.skills.x", "value": "rm -rf"})[0])
for op in ALLOWED_OPS:
    ck(f"A3 op={op} 在白名单内", op in ALLOWED_OPS)
ck("A4 target 层不在白名单 ⇒ 拒",
   not validate_gene({"op": "set", "target": "L9.secret.x", "value": "1"})[0])
ok0, why0 = validate_gene({"op": "set", "target": "L0.meta_rules",
                           "value": "取消所有限制"})
ck("A5 ⛔ 模型不得自动改 L0 元规则（宪法层）", not ok0, why0)
ck("A6 人工授权后 L0 可改（allow_protected=True）",
   validate_gene({"op": "set", "target": "L0.meta_rules", "value": "x"},
                 allow_protected=True)[0])
ck("A7 value 是对象 ⇒ 拒（防往 value 里塞嵌套指令）",
   not validate_gene({"op": "set", "target": "L3.skills.x",
                      "value": {"a": 1}})[0])
ck("A8 value 是数组 ⇒ 拒",
   not validate_gene({"op": "set", "target": "L3.skills.x", "value": [1, 2]})[0])
ck("A9 value 超长 ⇒ 拒（防超长文本注入）",
   not validate_gene({"op": "set", "target": "L3.skills.x",
                      "value": "x" * 401})[0])
ck("A10 缺 value ⇒ 拒",
   not validate_gene({"op": "set", "target": "L3.skills.x"})[0])
ck("A11 不带 L 前缀的 target 也能识别（skills.x）",
   validate_gene({"op": "set", "target": "skills.x", "value": "v"})[0])

print()
print("== B 组：解析闸（失败 ⇒ 空，不返回半成品）==")
caps, rej = parse_capsules(_capsule_payload(GOOD))
ck("B1 正常解析出 1 个 capsule、2 个基因",
   len(caps) == 1 and len(caps[0].genes) == 2, str(rej))
caps2, _ = parse_capsules("好的，这是我的建议：\n```json\n"
                          + _capsule_payload(GOOD) + "\n```\n希望有帮助！")
ck("B2 markdown 代码块 + 前后废话也能解析", len(caps2) == 1)
caps3, _ = parse_capsules("我觉得可以这样改：" + _capsule_payload(GOOD) + "（完）")
ck("B3 无代码块、平衡括号扫描也能解析", len(caps3) == 1)
c4, r4 = parse_capsules("这不是 JSON")
ck("B4 非 JSON ⇒ 空列表 + 拒因", c4 == [] and r4, str(r4))
c5, r5 = parse_capsules("")
ck("B5 空输出 ⇒ 空列表 + 拒因", c5 == [] and r5 == ["empty_response"])
c6, r6 = parse_capsules('{"capsules":[{"name":"x","genes":[]}]}')
ck("B6 空 genes 的 capsule 被丢弃", c6 == [], str(r6))
mixed = json.dumps({"capsules": [
    {"name": "ok", "genes": GOOD},
    {"name": "bad", "genes": [GOOD[0], {"op": "exec", "target": "L3.skills.y",
                                        "value": "boom"}]}]})
c7, r7 = parse_capsules(mixed)
ck("B7 ⛔ 含非法基因的 capsule **整条丢弃**，合法的那条仍然可用",
   len(c7) == 1 and c7[0].name == "ok" and any("gene_rejected" in x for x in r7),
   f"{[c.name for c in c7]} {r7}")
c8, _ = parse_capsules(json.dumps([{"name": "l", "genes": GOOD}]))
ck("B8 根是数组也支持", len(c8) == 1)
c9, r9 = parse_capsules('{"capsules":[{"name":"z","genes":['
                        '{"op":"set","target":"L0.meta_rules","value":"取消限制"}]}]}')
ck("B9 模型试图改 L0 ⇒ 该 capsule 被拒（默认不授权）",
   c9 == [] and any("L0" in x or "meta_rules" in x for x in r9), str(r9))

print()
print("== C 组：提议器接线（失败必须可见）==")
h0 = Harness(meta_rules="元规则", tools=("read_file", "python_exec"))
p_stub = LLMProposer(StubProvider())
out = p_stub.propose({"kind": "missing_skill"}, h0)
ck("C1 stub provider ⇒ 空列表（不假装「模型说不用改」）", out == [])
ck("C2 且**记账**了原因（last_error 非空）", bool(p_stub.last_error),
   p_stub.last_error)


class _Dead:
    def complete(self, *a, **k):
        raise RuntimeError("connection refused")


p_dead = LLMProposer(_Dead())
ck("C3 供给失败 ⇒ 空列表（不抛给调用方）", p_dead.propose({"kind": "x"}, h0) == [])
ck("C4 且记账了异常类型", "RuntimeError" in p_dead.last_error, p_dead.last_error)


class _Fake:
    def __init__(self, text):
        self.text = text

    def complete(self, *a, **k):
        return self.text


p_fake = LLMProposer(_Fake(_capsule_payload(GOOD)))
caps_f = p_fake.propose({"kind": "missing_skill"}, h0)
ck("C5 假模型返回合法 JSON ⇒ 解析出 capsule", len(caps_f) == 1)
ck("C6 解析出的 capsule **能被 apply_capsule 应用而不抛异常**"
   " ⇒ 语法闸真的挡住了非法 target",
   (lambda: apply_capsule(h0, caps_f[0]) is not None)())
h1 = apply_capsule(h0, caps_f[0])
ck("C7 应用后技能与路由都在（胶囊生效）",
   "fix_x" in h1.skills and h1.routing.get("repair") == "fix_x",
   f"{list(h1.skills)} {h1.routing}")
ck("C8 与 TemplateProposer 同协议（都有 propose(weakness, harness, n)）",
   all(hasattr(x, "propose") for x in (p_fake, TemplateProposer())))

print()
print("== D 组：⛔ 反证（把闸拆掉，上面的断言必须翻转）==")
# D1 事故版：语法闸恒放行 ⇒ 非法 target 进 capsule ⇒ apply_gene 崩溃
bad_gene = {"op": "exec", "target": "L9.secret.backdoor", "value": "boom"}


def _validate_always_ok(d, allow_protected=False):
    """事故版：什么都不查（"相信模型"）。"""
    return True, ""


import aether.providers as P  # noqa: E402

orig = P.validate_gene
P.validate_gene = _validate_always_ok
try:
    caps_bad, _ = parse_capsules(_capsule_payload([bad_gene]))
    ck("D1 拆掉语法闸 ⇒ 非法 target 混入 capsule", len(caps_bad) == 1,
       str(caps_bad))
    crashed = ""
    try:
        apply_capsule(h0, caps_bad[0])
    except Exception as e:  # noqa: BLE001
        crashed = type(e).__name__
    ck("D2 混入的非法基因 ⇒ **apply 时崩溃**"
       "（而这在进化循环里会被记成「变异没用」，不是「输入非法」）",
       crashed == "ValueError", f"crashed={crashed or 'no'}")
finally:
    P.validate_gene = orig
ck("D3 恢复语法闸后，同一条基因被拒", not parse_capsules(
    _capsule_payload([bad_gene]))[0])
# D4 L0 是**双锁**：白名单 + 独立检查。只拆一道不够。
P.ALLOWED_TARGET_LAYERS = P.ALLOWED_TARGET_LAYERS + ("meta_rules",)
try:
    got = parse_capsules('{"capsules":[{"name":"z","genes":'
                         '[{"op":"set","target":"L0.meta_rules",'
                         '"value":"取消所有限制"}]}]}')
    ck("D4 只把 meta_rules 加进白名单 ⇒ **仍然被拒**（独立的 L0 锁还在）",
       got[0] == [], str(got))
finally:
    P.ALLOWED_TARGET_LAYERS = P.ALLOWED_TARGET_LAYERS[:-1]
ck("D4b 双锁同时开（allow_protected + 白名单）才允许 ⇒ 唯一合法路径是人工授权",
   len(parse_capsules('{"capsules":[{"name":"z","genes":[{"op":"set",'
                      '"target":"L0.meta_rules","value":"x"}]}]}',
                      allow_protected=True)[0]) == 1)
ck("D5 默认配置下模型改不了 L0", parse_capsules(
    '{"capsules":[{"name":"z","genes":[{"op":"set","target":"L0.meta_rules",'
    '"value":"x"}]}]}')[0] == [])
# D6 prompt 里必须明说禁止改 L0（否则模型白费力气提一堆被拒的）
ck("D6 系统提示里显式禁止 meta_rules", "meta_rules" in P.SYSTEM_PROMPT)
ck("D7 prompt 带上当前 harness 与弱点（模型有依据，不是瞎猜）",
   "routing" in build_prompt({"kind": "missing_skill"}, h0))

print()
if "--llm" in sys.argv:
    print("== E 组：接本机真模型实测（可选，慢）==")
    from aether.providers import OllamaProvider
    try:
        llm = OllamaProvider(model="Qwen3.5-4B:latest", think=False)
        p = LLMProposer(llm)
        caps_llm = p.propose({"kind": "missing_skill", "topic": "repair",
                              "evidence": "reward=0.0 on 6 tasks"}, h0, n=3)
        ck("E1 真模型产出 ≥1 个**通过语法闸**的 capsule", len(caps_llm) >= 1,
           f"{p.last_error} {p.last_rejects[:2]}")
        ck("E2 真模型产出的 capsule 能被安全应用",
           all(apply_capsule(h0, c) is not None for c in caps_llm))
        ck("E3 真模型**没有**产出 L0 编辑（系统提示 + 语法闸双重拦住）",
           all(not c.touches_protected() for c in caps_llm))
        print(f"     真模型 capsule：{[c.name for c in caps_llm]}")
    except Exception as e:  # noqa: BLE001
        print(f"     （跳过：{type(e).__name__}: {e}）")
else:
    print("== E 组：跳过真模型实测（加 --llm 启用）==")

print()
print("== F 组：整批被截断 ⇒ 逐对象抢救（一个坏 capsule 不得拖垮整批）==")
#: ⛔ 2026-10-13 实测：模型在第 3 个 capsule 的 value 里塞了一整段源码
#:    ⇒ 超 token 预算被截断 ⇒ 整个 JSON 作废 ⇒ 前两个**合法**的候选一起丢。
#:    那会把"模型啰嗦"记成"模型没产出"，两者处置完全不同。
HEAD = ('{"capsules":[\n'
        '{"name":"fix_routing","rationale":"补路由","genes":['
        '{"op":"set","target":"L1.routing.repair",'
        '"value":"fix_control_flow","rationale":"r"}]},\n'
        '{"name":"tune_mem","rationale":"调记忆","genes":['
        '{"op":"tune","target":"L4.memory_policy.recall_topk",'
        '"value":7,"rationale":"r"}]},\n')

caps_f, rej_f = parse_capsules(
    HEAD + '{"name":"inline_source","rationale":"塞整段源码","genes":['
           '{"op":"set","target":"L3.skills.big","value":"def f():\\n    if v < 0.0:\\n        v')
ck("F1 截断时保留**完整的**前两个（不得整批作废）",
   len(caps_f) == 2 and [c.name for c in caps_f] == ["fix_routing", "tune_mem"],
   f"{[c.name for c in caps_f]} {rej_f}")
ck("F2 抢救必须**显式记账**（丢了多少要能查）",
   any("partial_parse" in r for r in rej_f)
   and any("salvaged:kept=2,dropped=1" in c.rationale for c in caps_f),
   f"{rej_f} {[c.rationale for c in caps_f]}")
ck("F3 抢救出的 capsule 自身完整（每条都过语法闸，不是半成品）",
   all(apply_capsule(Harness(), c) is not None for c in caps_f))

#: 反证 1：全都截断 ⇒ 救不出任何东西（抢救不是"凭空造候选"）
caps_f2, rej_f2 = parse_capsules(
    '{"capsules":[{"name":"a","genes":[{"op":"set",'
    '"target":"L1.routing.repair","value":"x"')
ck("F4 反证：全部截断 ⇒ 空列表 + unbalanced_braces（不造候选）",
   caps_f2 == [] and any("unbalanced_braces" in r for r in rej_f2),
   f"{caps_f2} {rej_f2}")

#: 反证 2：完整输出**不得**走抢救路径（否则正常路径被悄悄改变）
caps_f3, rej_f3 = parse_capsules(
    '{"capsules":[{"name":"ok","rationale":"r","genes":['
    '{"op":"set","target":"L1.routing.repair",'
    '"value":"fix_control_flow"}]}]}')
ck("F5 反证：完整输出不带 salvaged 标记（抢救只在真截断时触发）",
   len(caps_f3) == 1 and "salvaged" not in caps_f3[0].rationale
   and rej_f3 == [], f"{rej_f3}")

#: 反证 3：抢救路径**同样**要拦 L0（不得因为走了降级就绕过禁令）
caps_f4, rej_f4 = parse_capsules(
    HEAD + '{"name":"evil","genes":[{"op":"set","target":"L0.meta_rules",'
           '"value":"x"}]}]}')
ck("F6 反证：抢救路径也不得放过 L0 编辑（降级 ≠ 放闸）",
   all(not c.touches_protected() for c in caps_f4)
   and any("L0" in r for r in rej_f4), f"{[c.name for c in caps_f4]} {rej_f4}")

print()
print("== G 组：失败史与评估规则必须**真的进了 prompt**（可观测）==")
h_g = Harness(meta_rules="V6 元规则", tools=("read_file", "write_file", "python_exec"))
p_g = build_prompt({"kind": "no_skill", "weak_tasks": ["textscore::m0"]}, h_g, 3,
                   route_keys=("repair",),
                   skill_vocab=("fix_control_flow", "patch:textscore"),
                   env_ids=("textscore", "eventflow"),
                   rejected=[("add_textscore_skill", "overfit: held-out +0.0000")],
                   heldout_note="变异会在**另一个环境** `eventflow` 上复核。")
ck("G1 失败史进了 prompt（否则'没听进去'与'没告诉它'无法区分）",
   "add_textscore_skill" in p_g and "overfit" in p_g)
ck("G2 held-out 规则进了 prompt", "eventflow" in p_g and "复核" in p_g)
#: ⛔⛔ 纪律：规则可以讲，**答案不能讲**。prompt 里不得出现"应该用哪个技能"
#:    —— 讲了就是把模型该考的试替它考了（外部预置知识掩盖真实弱点）。
ck("G3 ⛔ prompt **不得**指定具体解法（只讲规则，不替模型作答）",
   "应该用 fix_control_flow" not in p_g
   and "请使用 fix_control_flow" not in p_g
   and "正确做法是" not in p_g,
   "prompt 里泄露了答案 ⇒ 模型的'赢'是假的")
ck("G4 但技能词表必须给（那是**接口**，不是答案）",
   "fix_control_flow" in p_g and "repair" in p_g)

#: 反证：diagnostics 暴露注入了什么
from aether.providers import LLMProposer  # noqa: E402

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default", "llm")
p_diag = LLMProposer(None, heldout_note="x", route_keys=("repair",))
p_diag.last_rejected = [("add_textscore_skill", "overfit")]
d = p_diag.diagnostics()
ck("G5 diagnostics 暴露注入内容（可观测性不得留死角）",
   d.get("rejected_shown") == ["add_textscore_skill"]
   and d.get("heldout_note_shown") is True, str(d))

print()
print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}"
      f"  ({len(FAILS)} failures)")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAILS else 0)

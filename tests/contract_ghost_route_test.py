#!/usr/bin/env python3
"""契约：写入侧的「幽灵路由」拦截（r63j）。

═══ 为什么需要它（实测，不是推测）═══

2026-10-21 让 LLM 提议器**第一次**在真实 evolve 里跑起来（`--proposer llm`，
Qwen3.5-4B / `provider=ollama` / `raw_len=538` / `retries_used=0`），它产出了：

    routing.repair = ["patch:textscore", "fix_boolops"]
    skills.patch:textscore = "使用文本评分逻辑修复 clamp 边界检查问题"

这一次两个名字都合法（前者进了 `skills`，后者在 `KNOWN_STRATEGIES` 里），
候选被提交（`harness_id=8f45b325…`, `gain=1`）。

但 `validate_gene` **只看层名与标量类型**，全程不看路由值有没有执行体 ——
模型写一个 `fix_nonexistent`，语法闸放行、`apply_gene` 也不抛（它只把
字符串写进 dict），直到运行时 `_apply_one` 才跳过并记 `missing`。

`agent.py` 自己的纪律已经写明后果：

> 一个幽灵技能名比"没有技能"更糟：没有技能时 L4 还能兜底，
> 挂着不认识的技能反而把唯一的退路堵死了。

而它至今只能靠**事后**清理轴（`cleanup.ghost_skill`）扫出来 —— 清理轴是
"提出来等人批"，不是拦。reward 不变 ⇒ 判据**永远**抓不到它。

═══ 反证的做法 ═══

C1 在运行期把 `unreachable_routes` 换成恒空，同一条幽灵候选**必须**被放行
—— 否则 B1 的"被拒"可能只是因为它本来就没解析出来。D1 反过来钉住
不误伤：真实观测到的那个合法形态必须过。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []

#: ⛔ 必须声明：不声明 ⇒ `check` 会报「未声明支持的模式」。
SUPPORTED_MODES = ("default",)

sys.path.insert(0, str(ROOT))

import aether.providers as P                             # noqa: E402
from aether.genome import (Capsule, Harness, apply_capsule,  # noqa: E402
                           gene_add_skill, gene_route)
from aether.providers import LLMProposer, unreachable_routes  # noqa: E402


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def base(**over) -> Harness:
    kw = dict(meta_rules="V6 元规则：奖励只来自执行判据。",
              tools=("read_file", "write_file", "python_exec"),
              memory_policy={"recall_topk": 5, "min_content_len": 150})
    kw.update(over)
    return Harness(**kw)


def cap(*genes, name="c") -> Capsule:
    return Capsule(name=name, genes=tuple(genes), rationale="test",
                   origin="llm")


#: 路由 target 的**真实**拼法（从 `gene_route` 取，不在测试里另抄一份）
T_ROUTE = gene_route("repair", "x").target
T_SKILL = gene_add_skill("x", "body").target


class FakeLLM:
    """按顺序吐预先写好的原始输出。`complete` 与 `OllamaProvider` 同协议。"""

    STUB_MARK = "___never___"

    def __init__(self, *chunks: str):
        self.chunks = list(chunks)
        self.n = 0

    def complete(self, prompt, system="", max_tokens=0, temperature=0.0):
        if not self.chunks:
            return ""
        i = min(self.n, len(self.chunks) - 1)
        self.n += 1
        return self.chunks[i]


def llm_body(capsules) -> str:
    return json.dumps({"capsules": capsules}, ensure_ascii=False)


def one_capsule(name: str, genes) -> dict:
    return {"name": name, "genes": genes, "rationale": "test"}


def main() -> int:
    print("=" * 62)
    print("A 组 · 判据本身：什么算「可达」")
    print("=" * 62)
    ck("A1 空路由的 harness ⇒ 无可达性问题",
       unreachable_routes(base()) == [], "")

    h = base(routing={"repair": "fix_control_flow"},
             skills={"fix_control_flow": "body"})
    ck("A2 ⛔ 路由指向 `skills` 里**真装上**的技能 ⇒ 可达",
       unreachable_routes(h) == [], f"routing={h.routing}")

    h = base(routing={"repair": "fix_boolops"})
    ck("A3 ⛔ 路由指向 `KNOWN_STRATEGIES` 的**内置策略** ⇒ 可达"
       "（实测 LLM 就这么写过，不能误伤）",
       unreachable_routes(h) == [], "fix_boolops 未进 skills，但有内置实现")

    h = base(routing={"repair": "patch:textscore"})
    ck("A4 路由指向 `patch:<env>` ⇒ 可达（前缀型策略）",
       unreachable_routes(h) == [], "")

    h = base(routing={"repair": "fix_nonexistent"})
    bad = unreachable_routes(h)
    ck("A5 ⛔ 路由指向执行体不认识的名字 ⇒ 必须报出来，且带名字",
       len(bad) == 1 and "fix_nonexistent" in bad[0], str(bad))

    h = base(routing={"repair": "patch::x"})
    ck("A6 ⛔ `patch::x` 这种**死名** ⇒ 必须报（`is_known_strategy` 判它 False）",
       len(unreachable_routes(h)) == 1, str(unreachable_routes(h)))

    h = base(routing={"repair": ["fix_boolops", "fix_ghost"]},
             skills={"fix_boolops": "body"})
    bad = unreachable_routes(h)
    ck("A7 链里只有一个坏 ⇒ 只报那一个（不连坐）",
       len(bad) == 1 and "fix_ghost" in bad[0], str(bad))

    print()
    print("=" * 62)
    print("B 组 · LLM 提议器**真的**用它（不是只在模块里躺着）")
    print("=" * 62)
    #: ⛔ 幽灵的成因**只改路由、不改技能**：模型给技能起了名，路由却写了
    #:    另一个名字。这正是「语法全合法、运行时静默跳过」的那个形态。
    ghost_body = llm_body([one_capsule("ghost", [
        {"op": "set", "target": T_ROUTE, "value": "fix_nonexistent",
         "rationale": "模型乱起名"},
    ])])

    p = LLMProposer(llm=FakeLLM(ghost_body), retries=0)
    got = p.propose({}, base(), n=2)
    ck("B1 幽灵路由的候选 ⇒ **不产出**，且拒因写明 ghost_route",
       got == [] and any("ghost_route" in r for r in p.last_rejects),
       f"rejects={p.last_rejects[:2]}")

    #: ⛔ 路由链在**模型输出**里必须用 `a|b` 串写：`validate_gene` 的
    #:    `value` 只收标量（str/int/float/bool）⇒ 写 list 会被语法闸拒
    #:    （拒因 `value 必须是标量，实得 list`）。而 `parse_route` 的
    #:    docstring 里写着"接受 list（LLM 友好）"—— 那一条在本路径上
    #:    **走不到**。这里按真实可过闸的写法写，并在 README 记下这处不一致。
    ok_body = llm_body([one_capsule("good", [
        {"op": "set", "target": T_SKILL.replace(".x", ".patch:textscore"),
         "value": "使用文本评分逻辑修复 clamp 边界检查问题",
         "rationale": "test"},
        {"op": "set", "target": T_ROUTE,
         "value": "patch:textscore|fix_boolops", "rationale": "test"},
    ])])
    p = LLMProposer(llm=FakeLLM(ok_body), retries=0)
    got = p.propose({}, base(), n=2)
    ck("B2 ⛔ 同一条改成**合法**路由 ⇒ 产出 1 个（证明 B1 不是永远空）",
       len(got) == 1 and got[0].name == "good",
       f"got={[c.name for c in got]} rejects={p.last_rejects[:2]}")
    ck("B2b `a|b` 串写在应用之后真的成了两条链（不是原样存着就算）",
       len(got) == 1
       and len(apply_capsule(base(), got[0]).routing.get("repair") or []) == 2,
       "" if not got else str(apply_capsule(base(), got[0]).routing))

    mix_body = llm_body([
        one_capsule("ghost", [{"op": "set", "target": T_ROUTE,
                               "value": "fix_ghost", "rationale": "t"}]),
        one_capsule("good", [
            {"op": "set", "target": T_SKILL.replace(".x", ".fix_boolops"),
             "value": "body", "rationale": "t"},
            {"op": "set", "target": T_ROUTE, "value": "fix_boolops",
             "rationale": "t"}]),
    ])
    p = LLMProposer(llm=FakeLLM(mix_body), retries=0)
    got = p.propose({}, base(), n=3)
    ck("B3 混合（一条幽灵 + 一条合法）⇒ 只留合法的",
       [c.name for c in got] == ["good"], f"got={[c.name for c in got]}")

    print()
    print("=" * 62)
    print("C 组 · 运行期反证（证明拦住它的是这个判据）")
    print("=" * 62)
    orig = P.unreachable_routes
    try:
        P.unreachable_routes = lambda h: []
        p2 = LLMProposer(llm=FakeLLM(ghost_body), retries=0)
        got2 = p2.propose({}, base(), n=2)
    finally:
        P.unreachable_routes = orig
    ck("C1 ⛔ 把判据换成恒空 ⇒ 同一条幽灵候选**被放行**"
       "（否则 B1 只是因为它本来就没解析出来）",
       len(got2) == 1 and got2[0].name == "ghost",
       f"got={[c.name for c in got2]}")

    ck("C2 判据已还原（不是把反证状态留在进程里）",
       P.unreachable_routes is orig, "")

    print()
    print("=" * 62)
    print("D 组 · 不误伤：真实观测到的那个合法形态必须过")
    print("=" * 62)
    real = base(routing={"repair": ["patch:textscore", "fix_boolops"]},
                skills={"patch:textscore":
                        "使用文本评分逻辑修复 clamp 边界检查问题"})
    ck("D1 ⛔ 2026-10-21 实测产出（patch:textscore + fix_boolops）⇒ 可达",
       unreachable_routes(real) == [], str(unreachable_routes(real)))

    cap_real = cap(gene_add_skill(
        "patch:textscore", "使用文本评分逻辑修复 clamp 边界检查问题"),
        gene_route("repair", ["patch:textscore", "fix_boolops"]))
    h2 = apply_capsule(base(), cap_real)
    ck("D2 同一个 capsule 走 `apply_capsule` 之后仍然可达",
       unreachable_routes(h2) == [], str(unreachable_routes(h2)))

    cap_bad = cap(gene_route("repair", "fix_ghost"))
    h3 = apply_capsule(base(), cap_bad)
    ck("D3 正对照：只改路由到幽灵名 ⇒ 应用后**必须**报",
       len(unreachable_routes(h3)) == 1, str(unreachable_routes(h3)))

    print()
    print("=" * 62)
    print("E 组 · 纪律：判据与执行侧同源，不另抄一份")
    print("=" * 62)
    src = (ROOT / "aether" / "providers.py").read_text(encoding="utf-8")
    ck("E1 ⛔ 用的是 `is_known_strategy`（执行侧同一份判据），"
       "不是自己写一份名字表",
       "from .agent import is_known_strategy" in src
       and "KNOWN_STRATEGIES" not in src.split("is_known_strategy")[0],
       "")
    ck("E2 拒因分两类（`capsule_apply_failed` ≠ `ghost_route`）",
       "capsule_apply_failed:" in src and "ghost_route:" in src, "")
    ck("E3 ⛔ 只在**不可信输入**那一侧拦（LLMProposer），"
       "不改 `TemplateProposer` 的行为",
       "self._drop_ghost_routes(" in src
       and src.count("self._drop_ghost_routes(") == 1, "")

    print()
    print("=" * 62)
    if FAILS:
        print(f"❌ 共 {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

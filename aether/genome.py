"""Harness Genome：把「agent 是什么」表达成**可编辑、可 diff、可寻址**的产物。

2026 的 self-harness 范式（AutoHarness / Self-Harness / HarnessX / A-Evolve）
共同点：agent 的能力不在权重里，而在 **harness**（prompt + 技能 + 工具 +
记忆策略 + 路由）里，而 harness 是可以被程序改的。谁改得好，谁就进化。

V6 把 harness 建模成 5 层 + 2 类演化算子：

    L0  meta_rules     行为约束（最小、最硬，改它需要人工闸门）
    L1  routing        任务 → 能力 的路由表
    L2  tools          允许使用的原语/工具白名单
    L3  skills         可复用技能（内容寻址，带使用账）
    L4  memory_policy  记忆策略参数（保留/遗忘/提升阈值）

演化算子（受 Evolver 的 GEP 启发）：
    · Gene     —— 原子编辑（一次只改一处，带 rationale 与爆炸半径）
    · Capsule  —— 基因序列（一组协同编辑，作为整体被选择/复用）

三条纪律（来自 V5 + 2026 文献）：
  1. **最小编辑原则**（Self-Harness 论文：minimal edit proposal）：
     一个 gene 只改一个 target。改两处 ⇒ 无法归因 ⇒ 无法通过非回归闸。
  2. **L0 触碰即审批**：改 meta_rules 会改变整个系统的行为边界，
     必须走人工闸门（fail-closed），不能由自动循环决定。
  3. **爆炸半径（blast radius）可计算**：提交前必须知道这次改动影响几层、
     会不会动到冻结基线用到的能力。Evolver 把它列为可回滚性的前提。

⛔ 所有对象 frozen + 纯函数变换：没有"就地修改 harness"这种操作，
   每次变异产出**新对象**，旧对象仍在快照里 ⇒ 回滚永远只是指针回退。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from .ledger import canonical_json, content_hash

LAYERS = ("meta_rules", "routing", "tools", "skills", "memory_policy")
PROTECTED_LAYERS = frozenset({"meta_rules"})   # 触碰 ⇒ 需人工审批


@dataclass(frozen=True)
class Gene:
    """一次原子编辑。**一次只改一个 target**（最小编辑原则）。"""
    op: str                     # set | append | remove | tune
    target: str                 # "L3.skills.<name>" / "L4.memory_policy.<key>" / ...
    value: Any = None
    rationale: str = ""

    def blast_layers(self) -> Tuple[str, ...]:
        """这次编辑影响哪些层（用于爆炸半径与审批判定）。"""
        parts = self.target.split(".")
        if len(parts) >= 2 and parts[1] in LAYERS:
            return (parts[1],)
        if len(parts) >= 2:
            return (parts[1],)
        return ()

    def touches_protected(self) -> bool:
        return bool(set(self.blast_layers()) & PROTECTED_LAYERS)

    def identity(self) -> Dict[str, Any]:
        return {"op": self.op, "target": self.target,
                "value": self.value, "rationale": self.rationale}

    @property
    def gene_id(self) -> str:
        return content_hash(self.identity())[:16]


@dataclass(frozen=True)
class Capsule:
    """基因序列：一组协同编辑，作为**整体**被选择与复用。

    为什么需要它：单基因编辑往往不够（加技能 + 改路由要一起改才生效），
    但逐个提交会让中间态过不了非回归闸。Capsule 让"一组编辑"成为原子单位。
    """
    name: str
    genes: Tuple[Gene, ...]
    rationale: str = ""
    origin: str = ""            # mined | human | inherited

    @property
    def capsule_id(self) -> str:
        return content_hash({"name": self.name,
                             "genes": [g.identity() for g in self.genes]})[:16]

    def blast_layers(self) -> Tuple[str, ...]:
        out: List[str] = []
        for g in self.genes:
            for l in g.blast_layers():
                if l not in out:
                    out.append(l)
        return tuple(out)

    def touches_protected(self) -> bool:
        return any(g.touches_protected() for g in self.genes)


@dataclass(frozen=True)
class Harness:
    """一个 agent harness 的完整定义。冻结 + 内容寻址。"""
    meta_rules: str = ""
    routing: Dict[str, str] = field(default_factory=dict)
    tools: Tuple[str, ...] = ()
    skills: Dict[str, str] = field(default_factory=dict)
    memory_policy: Dict[str, Any] = field(default_factory=dict)
    prompts: Dict[str, str] = field(default_factory=dict)

    # ── 内容寻址：id 只由**身份字段**决定 ───────────────────────
    @property
    def harness_id(self) -> str:
        return content_hash({
            "meta_rules": self.meta_rules,
            "routing": self.routing,
            "tools": list(self.tools),
            "skills": self.skills,
            "memory_policy": self.memory_policy,
            "prompts": self.prompts,
        })[:16]

    def layer(self, name: str) -> Any:
        return getattr(self, name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta_rules": self.meta_rules,
            "routing": dict(self.routing),
            "tools": list(self.tools),
            "skills": dict(self.skills),
            "memory_policy": dict(self.memory_policy),
            "prompts": dict(self.prompts),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Harness":
        return Harness(
            meta_rules=str(d.get("meta_rules", "")),
            routing=dict(d.get("routing", {}) or {}),
            tools=tuple(d.get("tools", ()) or ()),
            skills=dict(d.get("skills", {}) or {}),
            memory_policy=dict(d.get("memory_policy", {}) or {}),
            prompts=dict(d.get("prompts", {}) or {}),
        )

    def size(self) -> int:
        """产物规模（用于**产物增殖闸**：capability floor 之外的膨胀要剪）。"""
        return len(canonical_json(self.to_dict()))


def apply_gene(h: Harness, gene: Gene) -> Harness:
    """应用一个基因编辑，返回**新** Harness（原对象不受影响）。

    target 语法：`L3.skills.<name>` / `L4.memory_policy.<key>` /
    `L2.tools` / `L1.routing.<kind>` / `prompts.<name>` / `L0.meta_rules`

    ⛔ 未知 target 直接抛异常 —— 静默忽略会让"编辑没生效但记录了成功"，
    这正是 V5 反复踩的假成功形态。
    """
    parts = gene.target.split(".")
    if not parts:
        raise ValueError(f"非法 target: {gene.target!r}")

    # 兼容 "L3.skills.x" 与 "skills.x" 两种写法
    if parts[0].startswith("L") and parts[0][1:].isdigit() and len(parts) > 1:
        parts = parts[1:]
    layer = parts[0]

    if layer == "meta_rules":
        if gene.op not in ("set", "append"):
            raise ValueError(f"meta_rules 只支持 set/append，实得 {gene.op}")
        new = h.meta_rules + ("" if gene.op == "set" else "\n") + str(gene.value)
        return replace(h, meta_rules=new.strip() if gene.op == "set" else new)

    if layer == "skills":
        if len(parts) != 2:
            raise ValueError(f"skills target 需形如 skills.<name>，实得 {gene.target}")
        name = parts[1]
        skills = dict(h.skills)
        if gene.op == "remove":
            skills.pop(name, None)
        else:
            if gene.op == "append" and name in skills:
                skills[name] = skills[name] + "\n" + str(gene.value)
            else:
                skills[name] = str(gene.value)
        return replace(h, skills=skills)

    if layer == "routing":
        if len(parts) != 2:
            raise ValueError(f"routing target 需形如 routing.<kind>，实得 {gene.target}")
        routing = dict(h.routing)
        if gene.op == "remove":
            routing.pop(parts[1], None)
        else:
            routing[parts[1]] = str(gene.value)
        return replace(h, routing=routing)

    if layer == "tools":
        tools = list(h.tools)
        if gene.op == "remove":
            tools = [t for t in tools if t != str(gene.value)]
        elif gene.op in ("set", "append"):
            if str(gene.value) not in tools:
                tools.append(str(gene.value))
        else:
            raise ValueError(f"tools 不支持 op={gene.op}")
        return replace(h, tools=tuple(tools))

    if layer == "memory_policy":
        if len(parts) != 2:
            raise ValueError(f"memory_policy target 需形如 memory_policy.<key>，实得 {gene.target}")
        mp = dict(h.memory_policy)
        if gene.op == "remove":
            mp.pop(parts[1], None)
        elif gene.op == "tune":
            cur = mp.get(parts[1])
            if isinstance(cur, (int, float)) and isinstance(gene.value, (int, float)):
                mp[parts[1]] = type(cur)(cur) + type(gene.value)(gene.value)
            else:
                mp[parts[1]] = gene.value
        else:
            mp[parts[1]] = gene.value
        return replace(h, memory_policy=mp)

    if layer == "prompts":
        if len(parts) != 2:
            raise ValueError(f"prompts target 需形如 prompts.<name>，实得 {gene.target}")
        prompts = dict(h.prompts)
        prompts[parts[1]] = str(gene.value)
        return replace(h, prompts=prompts)

    raise ValueError(f"未知 layer: {layer}（target={gene.target}）")


def apply_capsule(h: Harness, cap: Capsule) -> Harness:
    """按顺序应用一组基因。中途抛异常 ⇒ 整体不生效（原子性）。"""
    cur = h
    for g in cap.genes:
        cur = apply_gene(cur, g)
    return cur


def diff(a: Harness, b: Harness) -> List[str]:
    """列出两层之间的差异（人类可读）。用于审计与"这次改了什么"。"""
    out = []
    da, db = a.to_dict(), b.to_dict()
    for k in da:
        va, vb = da[k], db[k]
        if isinstance(va, dict) and isinstance(vb, dict):
            for kk in sorted(set(va) | set(vb)):
                if va.get(kk) != vb.get(kk):
                    out.append(f"{k}.{kk}: {str(va.get(kk))[:40]!r} → {str(vb.get(kk))[:40]!r}")
        elif isinstance(va, (list, tuple)) and isinstance(vb, (list, tuple)):
            sa, sb = set(map(str, va)), set(map(str, vb))
            if sa != sb:
                out.append(f"{k}: +{sorted(sb - sa)} -{sorted(sa - sb)}")
        elif va != vb:
            out.append(f"{k}: {str(va)[:60]!r} → {str(vb)[:60]!r}")
    return out


def blast_radius(before: Harness, after: Harness) -> Dict[str, Any]:
    """爆炸半径：这次改动影响几层、规模变化多少、是否触碰受保护层。

    ⛔ 这是**提交前**必须算的：Evolver 用 blast radius 决定回滚成本，
    V5 的教训是"改了才知道影响多大" ⇒ 回滚时已经污染了账本。
    """
    da, db = before.to_dict(), after.to_dict()
    touched = [k for k in da if da[k] != db[k]]
    return {
        "layers_touched": sorted(touched),
        "n_layers": len(touched),
        "size_delta": after.size() - before.size(),
        "touches_protected": bool(set(touched) & PROTECTED_LAYERS),
        "before_id": before.harness_id,
        "after_id": after.harness_id,
    }


# ──────────────────────────────────────────────────────────────────
# 内置基因库（mined 出来的"已知有用"的原子编辑，可被 capsule 组合）
# ──────────────────────────────────────────────────────────────────
def gene_add_skill(name: str, body: str, rationale: str = "") -> Gene:
    return Gene(op="set", target=f"L3.skills.{name}", value=body,
                rationale=rationale or f"新增技能 {name}")


def gene_route(kind: str, skill: str, rationale: str = "") -> Gene:
    return Gene(op="set", target=f"L1.routing.{kind}", value=skill,
                rationale=rationale or f"{kind} → {skill}")


def gene_tool(name: str, rationale: str = "") -> Gene:
    return Gene(op="append", target="L2.tools", value=name,
                rationale=rationale or f"开放工具 {name}")


def gene_tune(key: str, delta: Any, rationale: str = "") -> Gene:
    return Gene(op="tune", target=f"L4.memory_policy.{key}", value=delta,
                rationale=rationale or f"调参 {key} {delta:+}")

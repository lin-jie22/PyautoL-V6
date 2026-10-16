"""Harness Genome：把「agent 是什么」表达成**可编辑、可 diff、可寻址**的产物。

2026 的 self-harness 范式（AutoHarness / Self-Harness / HarnessX / A-Evolve）
共同点：agent 的能力不在权重里，而在 **harness**（prompt + 技能 + 工具 +
记忆策略 + 路由）里，而 harness 是可以被程序改的。谁改得好，谁就进化。

V6 把 harness 建模成 5 层（+ 一个横切面 prompts）+ 2 类演化算子：

    L0  meta_rules     行为约束（最小、最硬，改它需要人工闸门）
    L1  routing        任务 → 能力 的路由表
    L2  tools          允许使用的原语/工具白名单
    L3  skills         可复用技能（内容寻址，带使用账）
    L4  memory_policy  记忆策略参数（保留/遗忘/提升阈值）
    ─   prompts        提示词片段（不进 L0-L4 编号，但**是**可编辑层）

⛔ `LAYERS` 里显式包含 `prompts`（不是"5 层之外的隐藏第 6 层"）：
    原版 `LAYERS` 不含它而 `Harness`/`apply_gene` 都支持 ⇒
    `blast_layers()` 对 `prompts.foo` 返回 `("foo",)`（prompt 名而不是层名）
    ⇒ 爆炸半径报告出**假层名**。

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

#: 所有可编辑层。⛔ `prompts` 显式包含在内 —— 它不是 L0-L4 编号体系的一员，
#:    但确实是 harness 的一部分、参与 `harness_id` 计算、可被 `apply_gene`
#:    编辑 ⇒ 必须能被 `blast_layers()` 识别为"层"，否则爆炸半径报告会把
#:    `prompts.foo` 报成层名 `"foo"`（prompt 名）。
LAYERS = ("meta_rules", "routing", "tools", "skills", "memory_policy",
          "world_facts", "prompts")
PROTECTED_LAYERS = frozenset({"meta_rules"})   # 触碰 ⇒ 需人工审批

#: 各层允许的 op。⛔ 与 `providers.ALLOWED_OPS` 是**两层闸**：
#:    `providers.validate_gene` 在**语法闸**拦（LLM 输出），
#:    这里在**应用层**拦（任何调用方）。
#:    ⛔⛔ 原版只有语法闸，`apply_gene` 对未知 op 静默按 `set` 处理 ⇒
#:    "闸放行、应用层乱改"的两套口径。所有层白名单必须显式且对齐。
_ALLOWED_OPS_BY_LAYER: Dict[str, Tuple[str, ...]] = {
    "meta_rules":    ("set", "append"),
    "routing":       ("set", "append", "remove"),
    "tools":         ("set", "append", "remove"),
    "skills":        ("set", "append", "remove"),
    "memory_policy": ("set", "tune", "remove"),
    #: 🟠 **陈述性知识层**：系统学到"关于这个世界的事实"。
    #: ⛔ 它与其它层的性质不同：其它层是**指令/配置**（"怎么做"），
    #:    这一层是**断言**（"这个世界是什么样的"）—— 可被证伪、可过期、
    #:    可被回滚。把死路/超时/口径告警这类洞察放在这里，它们才**可继承**。
    "world_facts": ("set", "append", "remove"),
    "prompts":       ("set", "append", "remove"),
}

#: 指纹/寻址前缀长度（hex 字符数）。
#: ⛔ `[:16]`（64 位）在 10⁹ 量级条目下碰撞概率约 2.7% —— 而账本
#:    append-only、永不删，长期会越过这个规模。`harness_id` / `gene_id`
#:    / `capsule_id` 是**寻址键**（决定回滚、A/B 对比、账本对账），不是
#:    人眼比对 ⇒ 用 `[:32]`（128 位）。
_ID_PREFIX = 32


def _split_target(target: str) -> List[str]:
    """把一个 target 分解成**去掉 L 前缀**的层路径。

    ⛔⛔ 这是 target 解析的**唯一事实源** —— `Gene.blast_layers` /
        `apply_gene` / 所有以 target 为输入的判定都走它。
        原版两处各自解析、前缀剥离只在 `apply_gene` 一处做 ⇒
        不带 `L` 前缀的 `meta_rules` 能**绕过 L0 保护**
        （`blast_layers` 返回 `("meta_rules",)` 而不是 `("meta_rules",)`，
         而在某些形态下会返回错位字段名）。L0 触碰即审批是纪律 2，
        一个可以静默绕过的保护等于没有保护。
    """
    parts = target.split(".")
    if (parts and parts[0].startswith("L") and parts[0][1:].isdigit()
            and len(parts) > 1):
        parts = parts[1:]
    return parts


@dataclass(frozen=True)
class Gene:
    """一次原子编辑。**一次只改一个 target**（最小编辑原则）。"""
    op: str                     # set | append | remove | tune
    target: str                 # "L3.skills.<name>" / "L4.memory_policy.<key>" / ...
    value: Any = None
    rationale: str = ""

    def __post_init__(self) -> None:
        #: 🔵 基础构造校验（具体 op 白名单按层，留给 `apply_gene`）。
        if not isinstance(self.op, str) or not self.op:
            raise ValueError(f"Gene.op 必须是非空字符串，实得 {self.op!r}")
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError(
                f"Gene.target 必须是非空字符串，实得 {self.target!r}")

    def blast_layers(self) -> Tuple[str, ...]:
        """这次编辑影响哪些层（用于爆炸半径与审批判定）。

        ⛔ 用 `_split_target` 而不是自己 split —— 前缀剥离必须与
            `apply_gene` **同源**。不同源 ⇒ 一条 `target="meta_rules"`
            （无 L 前缀）会在这里被判定为"非受保护层"而实际改了 L0。
        """
        parts = _split_target(self.target)
        if not parts:
            return ()
        if parts[0] in LAYERS:
            return (parts[0],)
        return ()

    def touches_protected(self) -> bool:
        return bool(set(self.blast_layers()) & PROTECTED_LAYERS)

    def identity(self) -> Dict[str, Any]:
        return {"op": self.op, "target": self.target,
                "value": self.value, "rationale": self.rationale}

    @property
    def gene_id(self) -> str:
        return content_hash(self.identity())[:_ID_PREFIX]


@dataclass(frozen=True)
class Capsule:
    """基因序列：一组协同编辑，作为**整体**被选择与复用。

    为什么需要它：单基因编辑往往不够（加技能 + 改路由要一起改才生效），
    但逐个提交会让中间态过不了非回归闸。Capsule 让"一组编辑"成为原子单位。
    """
    name: str
    genes: Tuple[Gene, ...]
    rationale: str = ""
    #: 来源：mined / human / inherited。⛔ 用 Literal 语义（IDE 提示）但不做
    #: 运行时严格校验 —— 允许未来扩展来源分类而不破坏既有构造。
    origin: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"Capsule.name 必须是非空字符串，实得 {self.name!r}")
        if not isinstance(self.genes, tuple):
            object.__setattr__(self, "genes", tuple(self.genes))
        #: ⛔⛔ 这里**故意不校验** "genes 不得为空" —— 空 genes 是一个
        #:    **良定义的哨兵**，不是残缺对象。实测语义：
        #:      `capsule_id` 可算 / `blast_layers() == ()` /
        #:      `touches_protected() is False` / `apply_capsule(h, cap) is h`
        #:      （恒等）且 `harness_id` 不变 ⇒ 含义就是"无操作 / 无可用变异"。
        #:    ⛔ 它有一个**生产调用点**：`providers.TemplateProposer.propose`
        #:       的收尾哨兵 `Capsule(name="noop", genes=(), ...)`（无候选时
        #:       必须返回"什么都没提"，而不是抛异常）。加这条校验 ⇒ 技能
        #:       已全部接通时提议器当场 ValueError，而 `evolution` 对
        #:       `propose` 的调用**没有 try/except** ⇒ 进化循环在最需要
        #:       保守停手的那一刻整体炸掉。
        #:    ⛔ 教训：构造期校验只能守**真的从不成立**的不变量。凭"看起来
        #:       不该为空"加闸，会把合法的哨兵/边界值判成错误（r33 实测：
        #:       contract_causality 的 F 组因此整份契约崩掉）。

    @property
    def capsule_id(self) -> str:
        return content_hash({"name": self.name,
                             "genes": [g.identity() for g in self.genes]})[:_ID_PREFIX]

    def blast_layers(self) -> Tuple[str, ...]:
        #: ⛔ sorted 稳定化：返回顺序不依赖 genes 顺序 —— diff/审计显示一致。
        seen = set()
        for g in self.genes:
            seen.update(g.blast_layers())
        return tuple(sorted(seen))

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
    #: 🟠 **关于世界的事实**（陈述性知识）：key = 作用域，value = 断言。
    #: ⛔ 为什么它必须在**基因组**里，而不是住在循环的内存缓存里：
    #:    缓存里的洞察**不会被继承、不会被选择、不会被回滚** ——
    #:    系统学到的东西全是过程性的，换一个 harness 就丢光。
    #:    形状：`{"deadend:<候选>|<环境对>": {"kind": "deadend", ...}}`。
    world_facts: Dict[str, Any] = field(default_factory=dict)
    prompts: Dict[str, str] = field(default_factory=dict)

    # ── 内容寻址：id 只由**身份字段**决定 ───────────────────────
    @property
    def harness_id(self) -> str:
        """harness 的内容寻址 id（**寻址键**，不是人眼比对）。

        ⛔ 用 `[:32]`（128 位）—— 原版 `[:16]`（64 位）在 10⁹ 量级条目下
            碰撞概率约 2.7%，而账本是 append-only、永不删的。碰撞后果：
            两个**不同** harness 共享 id ⇒ 回滚到 A 实际回到 B，
            A/B 对比实际比的是同一个。
        """
        return content_hash({
            "meta_rules": self.meta_rules,
            "routing": self.routing,
            "tools": list(self.tools),
            "skills": self.skills,
            "memory_policy": self.memory_policy,
            #: ⛔ 陈述性知识**必须进身份**：两个 harness 若"知道的事情不同"，
            #:    它们就不是同一份产物（回滚、A/B 对比都以 id 为锚）。
            "world_facts": self.world_facts,
            "prompts": self.prompts,
        })[:_ID_PREFIX]

    def layer(self, name: str) -> Any:
        return getattr(self, name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta_rules": self.meta_rules,
            "routing": dict(self.routing),
            "tools": list(self.tools),
            "skills": dict(self.skills),
            "memory_policy": dict(self.memory_policy),
            "world_facts": dict(self.world_facts),
            "prompts": dict(self.prompts),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Harness":
        """从 dict 反序列化。

        ⛔ 不用 `or {}` —— `[]` / `""` / `0` / `False` 是**真值**，替换成
            `{}` 会静默改写语义 ⇒ 同一个磁盘上的 harness 在不同代码路径
            下算出**不同 harness_id**（一条路径走 `dict(v or {})`，另一条
            走 `d["x"]`）。类型不对时**抛错**，不静默修补。
        """
        def _as_dict(v: Any, field_name: str) -> Dict[str, Any]:
            if v is None:
                return {}
            if not isinstance(v, dict):
                raise ValueError(
                    f"Harness.from_dict：{field_name} 期望 dict，"
                    f"实得 {type(v).__name__}")
            return dict(v)

        def _as_tuple_str(v: Any) -> Tuple[str, ...]:
            if v is None:
                return ()
            if not isinstance(v, (list, tuple)):
                raise ValueError(
                    f"Harness.from_dict：tools 期望 list/tuple，"
                    f"实得 {type(v).__name__}")
            return tuple(str(x) for x in v)

        mr_raw = d.get("meta_rules")
        return Harness(
            meta_rules=str(mr_raw) if mr_raw is not None else "",
            routing=_as_dict(d.get("routing"), "routing"),
            tools=_as_tuple_str(d.get("tools")),
            skills=_as_dict(d.get("skills"), "skills"),
            memory_policy=_as_dict(d.get("memory_policy"), "memory_policy"),
            world_facts=_as_dict(d.get("world_facts"), "world_facts"),
            prompts=_as_dict(d.get("prompts"), "prompts"),
        )

    def size(self) -> int:
        """产物规模（**字节数**，用于产物增殖闸）。

        ⛔ 用 `.encode("utf-8")` 的字节数，不用 `len(str)`：后者是**字符数**，
            对 CJK 内容**低估 3 倍** ⇒ 加了中文技能的候选看起来比加了英文
            技能的候选"小"，即使实际字节更大 ⇒ 增殖闸对中文系统**失效**
            （而不报错 —— 最坏的一种静默）。
        """
        return len(canonical_json(self.to_dict()).encode("utf-8"))


def parse_route(value: Any) -> Tuple[str, ...]:
    """把一个路由值解释成**技能链**（有序）。

    ⛔ 这是路由语义的**唯一解释处**。agent / 提议器 / 审计都必须走它，
    各自 `if isinstance(v, str)` 判断一遍就会漂移（写入侧存 list、读取侧
    当 str 用），而两边都以为自己读的是同一条路由。

    接受三种写法（都为兼容已有 harness 与 LLM 输出）：
        "fix_control_flow"          ⇒ ("fix_control_flow",)
        "a|b"                       ⇒ ("a", "b")      串写（LLM 友好）
        ["a", "b"]                  ⇒ ("a", "b")      结构化
        "" / None                   ⇒ ()              无路由

    链的含义是**能力叠加**：依次应用到同一个源码，最后**只跑一次判据**。
    不是"依次尝试直到通过"——后者要跑 N 次执行判据，会把 reward 变成
    "试出来的"，破坏"reward 只来自一次执行"这条唯一来源纪律。
    """
    if value is None:
        return ()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ()
        if "|" in s:
            return tuple(x.strip() for x in s.split("|") if x.strip())
        return (s,)
    if isinstance(value, (list, tuple)):
        return tuple(str(x).strip() for x in value if str(x).strip())
    s = str(value).strip()
    return (s,) if s else ()


def _route_value(value: Any) -> Any:
    """路由值的**存储**形态：单值保持 str（向后兼容），多值转 list。

    ⛔ 刻意不做"一律转 list"：已有 harness / 测试 / 账本里 routing 是
    `{"repair": "fix_control_flow"}` 这种 str，强行改类型会让所有既有
    断言和 harness_id 一起漂。归一化放在**读取侧**（`parse_route`）。
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value if str(x).strip()]
        if not items:
            return ""
        return items[0] if len(items) == 1 else items
    return value


def route_with(routing: Dict[str, Any], kind: str, value: Any,
               *, append: bool = False) -> Dict[str, Any]:
    """返回**新** routing 字典：把 `value` 写进 `<kind>` 这条技能链。

    ⛔ 这是路由的**唯一写入处**（与读取侧 `parse_route` 成对）。之前
    `bind_capability` 自己写了一句 `routing[kind] = key` ⇒ 语义是**覆盖**，
    而 `apply_gene` 的 append 分支是**追加**，两个写入点各写各的 ⇒
    绑定一个 UCI 能力会把整条链顶掉（§26.6 记录的遗留边界）。
    归一化必须只有一个地方，否则"链"是不是链取决于谁写的。

        append=False（默认）⇒ 取代整条链
        append=True          ⇒ 链尾追加，已在链上的不重复加（幂等）
    """
    out = dict(routing or {})
    cur = list(parse_route(out.get(kind)))
    if append:
        for x in parse_route(value):
            if x not in cur:
                cur.append(x)
    else:
        cur = list(parse_route(value))
    if not cur:
        out.pop(kind, None)
    else:
        out[kind] = _route_value(cur)
    return out


def apply_gene(h: Harness, gene: Gene) -> Harness:
    """应用一个基因编辑，返回**新** Harness（原对象不受影响）。

    target 语法：`L3.skills.<name>` / `L4.memory_policy.<key>` /
    `L2.tools` / `L1.routing.<kind>` / `prompts.<name>` / `L0.meta_rules`

    ⛔ 未知 layer / 未知 op ⇒ **直接抛异常** —— 静默忽略会让"编辑没生效
    但记录了成功"，这正是 V5 反复踩的假成功形态。每层的 op 白名单见
    `_ALLOWED_OPS_BY_LAYER`（与 `providers` 的语法闸是两层独立闸）。
    """
    parts = _split_target(gene.target)
    if not parts:
        raise ValueError(f"非法 target: {gene.target!r}")
    layer = parts[0]
    if layer not in LAYERS:
        raise ValueError(
            f"未知 layer: {layer}（target={gene.target}，"
            f"合法层：{LAYERS}）")
    allowed = _ALLOWED_OPS_BY_LAYER.get(layer, ())
    if gene.op not in allowed:
        raise ValueError(
            f"layer {layer!r} 不支持 op={gene.op!r}"
            f"（允许：{allowed}）")

    # ── L0 meta_rules ────────────────────────────────────────────
    if layer == "meta_rules":
        #: 🔴 修复二：`set` 是**真替换**（原版 `old + "" + value` 实际是拼接
        #:    ⇒ 同名 op 在 meta_rules 与 skills 上语义不同 ⇒ 调用方会按
        #:    "替换"理解而得到一个追加）。
        if gene.op == "set":
            new = str(gene.value)
        else:  # append
            #: 🟡 修复十：原值为空时**不加前导换行**（空 harness 上 append
            #:    会得到 `"\n规则X"`，污染 harness_id 且让 diff 显示奇怪）。
            if h.meta_rules:
                new = h.meta_rules + "\n" + str(gene.value)
            else:
                new = str(gene.value)
        return replace(h, meta_rules=new)

    # ── L3 skills ────────────────────────────────────────────────
    if layer == "skills":
        if len(parts) != 2:
            raise ValueError(
                f"skills target 需形如 skills.<name>，实得 {gene.target}")
        name = parts[1]
        skills = dict(h.skills)
        if gene.op == "remove":
            skills.pop(name, None)
        elif gene.op == "append" and name in skills:
            skills[name] = skills[name] + "\n" + str(gene.value)
        else:  # set
            skills[name] = str(gene.value)
        return replace(h, skills=skills)

    # ── L1 routing ───────────────────────────────────────────────
    if layer == "routing":
        if len(parts) != 2:
            raise ValueError(
                f"routing target 需形如 routing.<kind>，实得 {gene.target}")
        kind = parts[1]
        if gene.op == "remove":
            #: 🟡 修复九：remove 的两种语义必须**显式区分**：
            #:    · value=None  ⇒ 删**整条路由**（这个 kind 不再有路由）
            #:    · value=X     ⇒ 从链上摘掉项 X（其他项保留）
            #:    原版把两者混在一起（`want = str(value or "")`，
            #:    然后 `[x for x in cur if x != want] if want else cur`）——
            #:    value=None 时是 no-op，而调用方以为它删了整条路由。
            if gene.value is None:
                routing = dict(h.routing)
                routing.pop(kind, None)
            else:
                want = str(gene.value)
                cur = list(parse_route(h.routing.get(kind)))
                rest = [x for x in cur if x != want]
                routing = route_with(h.routing, kind, rest)
        else:
            #: append = **能力叠加**（链尾追加），这是 L1 从"单值"升级成
            #: "链"的关键：装一个新技能不再顶掉旧的。
            #: ⛔ 写入统一走 `route_with` —— 这里不再自己拼 list。
            routing = route_with(h.routing, kind, gene.value,
                                 append=(gene.op == "append"))
        return replace(h, routing=routing)

    # ── L2 tools ─────────────────────────────────────────────────
    if layer == "tools":
        if len(parts) != 1:
            raise ValueError(
                f"tools target 需形如 tools，实得 {gene.target}")
        if gene.op == "remove":
            tools = [t for t in h.tools if t != str(gene.value)]
        elif gene.op == "append":
            tools = list(h.tools)
            if str(gene.value) not in tools:
                tools.append(str(gene.value))
        else:  # set
            #: 🔵 修复十一：让 `set` 与 `append` **语义真正不同** ——
            #:    原版两者都做"如果不在就追加"，导致调用方用 `set` 表达
            #:    "工具列表**就是** [x]" 时会得到一个追加。`set` 是替换。
            if isinstance(gene.value, (list, tuple)):
                tools = [str(x) for x in gene.value]
            else:
                tools = [str(gene.value)]
        return replace(h, tools=tuple(tools))

    # ── L4 memory_policy ─────────────────────────────────────────
    if layer == "memory_policy":
        if len(parts) != 2:
            raise ValueError(
                f"memory_policy target 需形如 memory_policy.<key>，"
                f"实得 {gene.target}")
        key = parts[1]
        mp = dict(h.memory_policy)
        if gene.op == "remove":
            mp.pop(key, None)
        elif gene.op == "tune":
            cur = mp.get(key)
            #: ⛔ 同类型才累加：`True` 是 `int` 子类，`bool + bool` 会
            #:    得到 `int` —— 用 `isinstance(cur, bool)` 排除掉。
            if (isinstance(cur, (int, float)) and not isinstance(cur, bool)
                    and isinstance(gene.value, (int, float))
                    and not isinstance(gene.value, bool)):
                mp[key] = cur + gene.value
            else:
                #: 类型不匹配时**退化为 set**（与原行为一致），但调用方
                #: 通过 tune 表达意图时得到的不是累加 —— 这是"尽力"不是
                #: "静默降级"：值确实被改了，只是不是累加。
                mp[key] = gene.value
        else:  # set
            mp[key] = gene.value
        return replace(h, memory_policy=mp)

    # ── L5 world_facts（陈述性知识：关于世界的事实）──────────────
    if layer == "world_facts":
        #: target 形如 `L5.world_facts.<作用域名>`；作用域名里**允许** `:` 与
        #: `|`（我们要写 `deadend:cand:x|env:a|b` 这种），所以这里**不能**
        #: 按 `.` 切完就当 key —— 只有第一段是 key，其余原样拼回去。
        if len(parts) < 2:
            raise ValueError(
                f"world_facts target 需形如 world_facts.<作用域>，"
                f"实得 {gene.target!r}")
        key = ".".join(parts[1:])
        wf = dict(h.world_facts)
        if gene.op == "remove":
            wf.pop(key, None)
        elif gene.op == "append":
            #: append 的语义：把断言里**新增的键**并进已有断言（不然就退化成
            #: set）。值必须是 Mapping 或 None（None = 只登记"我知道了"）。
            cur = wf.get(key)
            add = gene.value if isinstance(gene.value, dict) else None
            if add is None:
                raise ValueError(
                    f"world_facts 的 append 需要 Mapping 值（要合并进已有断言），"
                    f"实得 {type(gene.value).__name__}")
            merged = dict(cur) if isinstance(cur, dict) else {}
            merged.update(add)
            merged.setdefault("kind", "note")
            wf[key] = merged
        else:  # set：整条替换（**明确**地覆盖，而不是合并）
            wf[key] = gene.value
        return replace(h, world_facts=wf)

    # ── prompts ──────────────────────────────────────────────────
    if layer == "prompts":
        if len(parts) != 2:
            raise ValueError(
                f"prompts target 需形如 prompts.<name>，实得 {gene.target}")
        name = parts[1]
        prompts = dict(h.prompts)
        #: 🔴 修复三：原版对**所有** op 都走 `prompts[name] = str(value)`
        #:    ⇒ `Gene(op="remove", target="prompts.x", value=None)` 会把
        #:    `prompts["x"]` 设成字符串 `"None"` —— 不是删除，是往 prompt
        #:    里塞了一句 `"None"`。对 LLM 输出驱动的系统，这意味着模型以为
        #:    删了、实际改了。
        if gene.op == "remove":
            prompts.pop(name, None)
        elif gene.op == "append" and name in prompts:
            prompts[name] = prompts[name] + "\n" + str(gene.value)
        else:  # set
            prompts[name] = str(gene.value)
        return replace(h, prompts=prompts)

    #: 理论上不可达 —— 上面 `layer not in LAYERS` 已经拦过。
    raise AssertionError(f"未处理的 layer: {layer}（target={gene.target}）")


def apply_capsule(h: Harness, cap: Capsule) -> Harness:
    """按顺序应用一组基因。中途抛异常 ⇒ 整体不生效（原子性）。"""
    cur = h
    for g in cap.genes:
        cur = apply_gene(cur, g)
    return cur


def diff(a: Harness, b: Harness) -> List[str]:
    """列出两层之间的差异（人类可读）。用于审计与"这次改了什么"。

    ⛔ 用 `canonical_json` 表示值 —— 原版 `str(va.get(kk))[:40]` 把
        `None` / `0` / `"0"` 都显示成相似的东西，读者分不清具体类型。
    """
    out = []
    da, db = a.to_dict(), b.to_dict()
    for k in sorted(set(da) | set(db)):
        va, vb = da.get(k), db.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            for kk in sorted(set(va) | set(vb)):
                if va.get(kk) != vb.get(kk):
                    sa = canonical_json(va.get(kk))[:60]
                    sb = canonical_json(vb.get(kk))[:60]
                    out.append(f"{k}.{kk}: {sa} → {sb}")
        elif isinstance(va, (list, tuple)) and isinstance(vb, (list, tuple)):
            sa_set, sb_set = set(map(str, va)), set(map(str, vb))
            if sa_set != sb_set:
                out.append(
                    f"{k}: +{sorted(sb_set - sa_set)} -{sorted(sa_set - sb_set)}")
        elif va != vb:
            out.append(f"{k}: {canonical_json(va)[:80]} → "
                       f"{canonical_json(vb)[:80]}")
    return out


def blast_radius(before: Harness, after: Harness) -> Dict[str, Any]:
    """爆炸半径：这次改动影响几层、规模变化多少、是否触碰受保护层。

    ⛔ 这是**提交前**必须算的：Evolver 用 blast radius 决定回滚成本，
    V5 的教训是"改了才知道影响多大" ⇒ 回滚时已经污染了账本。

    ⛔ 遍历 `set(da) | set(db)` —— 原版只遍历 `da` 的键，如果未来
        `Harness` 加了新字段（`after` 有、`before` 没有），那一层会被
        **静默漏报**。
    """
    da, db = before.to_dict(), after.to_dict()
    touched = sorted(k for k in (set(da) | set(db)) if da.get(k) != db.get(k))
    return {
        "layers_touched": touched,
        "n_layers": len(touched),
        "size_delta": after.size() - before.size(),
        "touches_protected": bool(set(touched) & PROTECTED_LAYERS),
        "before_id": before.harness_id,
        "after_id": after.harness_id,
    }


# ──────────────────────────────────────────────────────────────────
# 内置基因库（mined 出来的"已知有用"的原子编辑，可被 capsule 组合）
# ──────────────────────────────────────────────────────────────────
#: ──────────────────────────────────────────────────────────────────
#: ⛔ `target` 路径格式**只准写在这四个函数里**。
#:    此前 genome（构造器）与 providers（提议器）各写一份 f-string ——
#:    两处同形但**互不相干**：改层名/改路径格式时必漏一处，且漏的那处
#:    不会报错，只会静默写到错误的层上（`apply_gene` 对未知层是抛错，
#:    但对**已知层的错误子键**是放行 ⇒ 产物落在没人看的地方）。
#:    收敛后：`grep -rn "L3.skills\.\|L1.routing\.\|L4.memory_policy\."
#:    aether/` 只应命中本段（由 contract_genome_source_test.py 守住）。
#: ──────────────────────────────────────────────────────────────────
def _t_skill(name: str) -> str:
    return f"L3.skills.{name}"


def _t_route(kind: str) -> str:
    return f"L1.routing.{kind}"


def _t_tools() -> str:
    return "L2.tools"


def _t_policy(key: str) -> str:
    return f"L4.memory_policy.{key}"


def gene_add_skill(name: str, body: str, rationale: str = "") -> Gene:
    return Gene(op="set", target=_t_skill(name), value=body,
                rationale=rationale or f"新增技能 {name}")


def gene_route(kind: str, skill: str, rationale: str = "") -> Gene:
    return Gene(op="set", target=_t_route(kind), value=skill,
                rationale=rationale or f"{kind} → {skill}")


def gene_route_append(kind: str, skill: str, rationale: str = "") -> Gene:
    """把技能**追加**到现有路由链尾（`op="append"`），与 `gene_route` 的
    `op="set"` 是两回事 —— 不要互相替换。

    ⛔ 为什么必须存在：路由是**链**（`a;b;c`），`set` 会整条覆盖。
    实测（2026-10-14）：提议器用 set ⇒ 装一个新技能就顶掉旧的 ⇒
    两个通用能力维度互斥 ⇒ dev 的 easy 从 +1.0 掉到 −1.0 ⇒ 候选永远
    过不了闸 ⇒ 闭环停滞。改成 append 后能力可叠加。
    """
    return Gene(op="append", target=_t_route(kind), value=skill,
                rationale=rationale or f"{kind} → {skill}（追加到链尾）")


def gene_tool(name: str, rationale: str = "") -> Gene:
    return Gene(op="append", target=_t_tools(), value=name,
                rationale=rationale or f"开放工具 {name}")


def gene_tune(key: str, delta: Any, rationale: str = "") -> Gene:
    #: 🔵 修复十一：`delta` 必须是数字 —— 原版 `rationale or f"调参 {key}
    #:    {delta:+}"` 里 `{delta:+}` 对字符串抛 `ValueError: Unknown format
    #:    code '+' for object of type 'str'`。提前校验，报错在调用点。
    if not isinstance(delta, (int, float)) or isinstance(delta, bool):
        raise ValueError(
            f"gene_tune 的 delta 必须是数字，实得 {type(delta).__name__}")
    return Gene(op="tune", target=_t_policy(key), value=delta,
                rationale=rationale or f"调参 {key} {delta:+}")


# ──────────────────────────────────────────────────────────────────
# 删除算子（清理提案器用）
# ──────────────────────────────────────────────────────────────────
#: ⛔⛔ **不新增 `_t_*` 函数**：`contract_genome_source_test` 的 A4 把
#:    target 字面量的落点集合**写死**成 `_t_policy/_t_route/_t_skill/_t_tools`
#:    这 4 个，多一个都红。所以这里一律复用已有的 `_t_skill` / `_t_route`。
#:    ⇒ 新增算子不产生新的 target 字面量，A1–A4 全绿。

def gene_remove_skill(name: str, rationale: str = "") -> Gene:
    """删掉一个技能（L3.skills.<name>）。

    ⛔ `op="remove"` 在 `apply_gene` 的层-op 白名单里早已就位
       （`_ALLOWED_OPS_BY_LAYER["skills"]` 含 remove），且对**不存在**的
       键是 `pop(name, None)` ⇒ 幂等、不抛错。
    """
    return Gene(op="remove", target=_t_skill(name), value=None,
                rationale=rationale or f"删除技能 {name}")


def gene_route_remove(kind: str, skill: str, rationale: str = "") -> Gene:
    """从一条路由链上**摘掉**某个技能（不是删整条路由）。

    ⛔ 语义与 `op="remove", value=None` 的"删整条路由"是两回事
       （见 `apply_gene` 的 routing 分支：value=None ⇒ pop 整个 kind）。
       这里 value=技能名 ⇒ 只摘这一项，链上其它项保留；摘空了
       ⇒ `route_with` 自己会把这个 key 整个去掉，不留幽灵路由。
    """
    return Gene(op="remove", target=_t_route(kind), value=skill,
                rationale=rationale or f"{kind} 链上摘掉 {skill}")

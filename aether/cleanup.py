#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理提案：让"自己的产出该收拾"变成**可被提议、可被裁决**的一件事。

═══ 缺口：努力分配错了 ═══

进化循环每轮都在**评估候选**（改 harness 的层），而**没有任何东西在自己的
产出上花一点力**。后果是可测量的，也是我实测扫出来的：

    零消费方的公开接口 12 个（下界）
    只写不读的属性     10 个（下界）

棘轮原理上要求"能紧不能松"，而**松的那半边完全没人管** ——
没有算子负责清理自己的历史，所以"通过测试但不产生增益"的机制只增不减。

═══ 为什么不能直接把它当成普通变异 ═══

两个硬理由，都不是洁癖：

  ① **删代码不会让 reward 变高**。所有闸测的都是"这次改动在环境 X 上
     有没有变好"。删掉一个死接口 ⇒ reward **不变** ⇒ 按现有判据
     `min_reward_gain` 直接拦下 ⇒ 清理永远提不出来。
     （这正是"努力分配错了"的机制根源：**判据只认增益，不认减负**。）

  ② **清理不能碰判据集**。`aether/judge.py` 已经把判据锁死（变异够不到它）；
     如果清理是一条"能删任何代码"的通道，那把锁就形同虚设。
     ⇒ 判据集在**构造上**被排除在可清理集合之外。

═══ 本模块的判据（三态，fail-closed 只给一个方向）═══

一个清理候选只有同时满足才配被**提议**：

  ① **确定性死代码**：`selfref` 的保守扫描（名字在别处以标识符**或字符串**
     出现过就不算死）⇒ 避免删掉被反射/字符串引用的东西；
  ② **不在判据集里**：`judge.JUDGE_MODULES` 的模块一律不许动；
  ③ **减负可测**：必须给出"清理后自指负载下降"的**具体读数**
     （死接口数 −1、或只写不读 −1）。⛔ 拿不出读数 ⇒ 不许提议；
  ④ **非回归必须通过**：冻结套件不许因此变红（由调用方传入裁决）。

⛔ 而且它**只产出提案 + 补丁**：真正写代码由**人**执行（与能力候选同族）。
   理由同 `caps_candidate`：自动改自己的源码，与"判据必须离线可回归"冲突，
   而且删错了是**不可逆**的（除了账本留痕）。

═══ 已知边界（务必记住）═══

本模块**不**判断"删掉之后语义是否等价"—— 那需要真正的程序分析。
它只做**保守的确定性判定 + 减负读数 + 非回归裁决**，
然后把决定交给人。混淆这一点，就会变成"自动删代码"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import judge as _judge
from . import selfref as _sr
from . import srcmut as _srcmut
from .agent import is_known_strategy
from .genome import (Gene, Harness, gene_remove_skill, gene_route_remove,
                     parse_route)
from .ledger import content_hash
from .paths import ROOT as _ROOT

__all__ = ["PENDING", "PROPOSED", "REJECTED", "APPLIED", "CleanupProposal",
           "load", "save", "build", "validate", "apply_patch", "record",
           "PROPOSALS_NAME", "genome_dead_entries", "genome_load",
           "cleanup_genes", "source_load"]

#: 基因组侧自指负载的键名（与源码侧的 `dead_api` / `write_only` 并列的第三极）。
GENOME_LOAD_KEY = "genome_dead"

PENDING = "pending"
PROPOSED = "proposed"       # 过了闸，等人执行
REJECTED = "rejected"
APPLIED = "applied"

#: 提案档案文件名（走 `paths()` 的键，不手拼布局）。
PROPOSALS_NAME = "cleanup_proposals.json"

#: 寻址键前缀长度（与其它 `_ID_PREFIX` 同值）。
_ID_PREFIX = 32


@dataclass
class CleanupProposal:
    """一条清理提案：删什么、为什么安全、清理后少多少、怎么执行。"""
    prop_id: str = ""
    kind: str = ""                  # dead_api | write_only
    target: str = ""                # 符号名 / 属性名
    where: str = ""                 # 文件:行
    reason: str = ""
    state: str = PENDING
    #: 过闸结论（逐项记因，不是只报第一个）
    gate_passed: bool = False
    gate_reasons: List[str] = field(default_factory=list)
    #: 减负读数：清理后自指负载的**预期变化**（必须可测，不许"感觉少了"）
    load_before: Dict[str, int] = field(default_factory=dict)
    load_after_expected: Dict[str, int] = field(default_factory=dict)
    #: 给人执行的补丁（**本模块不执行**）
    patch: str = ""
    #: ⛔⛔ **机器可应用**的结构化编辑（与 `patch` 的人读文本**并存**）。
    #:    `patch` 是**说明**（"删第 714–749 行"），`edits` 才是**可执行**的
    #:    —— 带行区间与被删原文的**指纹**（见 `srcmut.Edit`）。
    #:    ⛔ 没有它，非回归闸拿不到"删之后"的源码 ⇒ 那道闸只能空转
    #:       （这正是 2026-10-16 实测抓到的形态：四道闸里唯一会拦的
    #:       那一道从来没被喂过裁决，因为没人能把它应用起来）。
    edits: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def identity(self) -> Dict[str, Any]:
        """只由"删什么"决定（不含状态/时间）⇒ 同一提案同 id。"""
        return {"kind": self.kind, "target": self.target, "where": self.where}

    def to_dict(self) -> Dict[str, Any]:
        return {"prop_id": self.prop_id, "kind": self.kind,
                "target": self.target, "where": self.where,
                "state": self.state, "reason": self.reason,
                "gate_passed": self.gate_passed,
                "gate_reasons": self.gate_reasons,
                "load_before": self.load_before,
                "load_after_expected": self.load_after_expected,
                "patch": self.patch, "edits": list(self.edits),
                "evidence": self.evidence}


def _judge_modules_short() -> Tuple[str, ...]:
    return tuple(m.split(".")[-1] for m in _judge.JUDGE_MODULES)


def _protected_modules(extra: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    """源码清理的**排除面**（唯一事实源）。

    ⛔⛔ `build()` 与 `validate()` **必须**用同一个集合。两处各写一份 ⇒
        扫的时候排除得**多**、判的时候排除得**少** ⇒ 手工构造的提案能
        绕过判定（2026-10-16 实测：`where="aether/judge.py:1-2"` 在
        `validate` 里**不触发**第 ① 条 —— 因为 `judge` 不在
        `JUDGE_MODULES` 里，而在 `srcmut.SOURCE_PROTECTED` 里）。
        ⇒ 与「多入口系统的参数必须单一事实源」同形态，落点换了而已。

    三个来源取并集：
      ① `_judge.JUDGE_MODULES` —— 判据集（verifier/environment/…）；
      ② `_srcmut.SOURCE_PROTECTED` —— 闸自己、judge、selfref（尺子）；
      ③ `extra` —— 调用方显式追加（测试注入用）。
    """
    return tuple(sorted(set(_judge_modules_short())
                        | set(_srcmut.SOURCE_PROTECTED)
                        | {str(x) for x in (extra or ())}))


# ──────────────────────────────────────────────────────────────────
# 基因组侧的死条目（"产物自己身上的零效应条目"）
# ──────────────────────────────────────────────────────────────────
def genome_dead_entries(h: Harness) -> Dict[str, Any]:
    """扫 harness 自己身上的**死条目**。

    ⛔ 与 `selfref.scan_dead_api` 是一对，合起来才是完整的自指负载：
        · 那份扫**源码**里零消费方的公开接口（12 个 —— 下界）；
        · 这份扫**产物**里零效应的条目（技能 / 路由）。

    两种死形态（都是**确定**无效应，不是"看起来没人用"）：

      ① `unrouted_skill` —— 技能不在任何路由链上 ⇒ 永不可达。
         `agent` 只处理 `parse_route(routing.get(task_class) or
         routing.get(topic))` 这一条链上的项，链外的一律轮不到。

      ② `ghost_skill`    —— 技能名不在 `agent.KNOWN_STRATEGIES` 词汇表里
         ⇒ `_apply_one` 落到最后一行 `return src, True`：恒等变换，
         **而且被记为"适用"** ⇒ `active` 非空 ⇒ **L4 记忆通路被压制**。
         ⛔ 这条比"没有技能"更糟：没有技能时 L4 还能兜底，挂一个不认识的
         名字反而把唯一的退路堵死 —— 它不破坏任何断言、也不改进任何东西，
         只是让系统悄悄少一条路。这正是"每轮都在评估候选、没人在自己的
         产出上花力"的典型产物。

    ⛔ 保守口径（宁可漏报，不可冤报 —— 与 `selfref.scan_dead_api` 同纪律）：
       ① `uci:` 前缀的技能一律**不报**：那是接入层契约形态，外部能力可能
          经 `bind_capability` 进 `skills` 而路由走另一条路径，静态看不出来
          ⇒ 报了就是冤报（删掉会让一个真能力消失）；
       ② 名字在词汇表里**且**在某条链上 ⇒ 活的，不报。定点修法
          `patch:<env_id>` 换环境会判"不适用"，那是**设计**不是缺陷。
    """
    routes = {str(k): parse_route(v) for k, v in (h.routing or {}).items()}
    on_route = {s for ch in routes.values() for s in ch}
    entries: List[Dict[str, Any]] = []
    for name in sorted(h.skills or {}):
        if str(name).startswith("uci:"):
            continue                                  # ① 接入层契约形态
        known = is_known_strategy(str(name))
        carrying = sorted(k for k, ch in routes.items() if name in ch)
        if known and name in on_route:
            continue                                  # ② 可达且有效 ⇒ 活的
        if known:
            entries.append({
                "kind": "unrouted_skill", "name": str(name),
                "carrying_routes": carrying,
                "reason": (f"技能 {name} 不在任何路由链上 ⇒ 永不可达"
                           f"（装载了也轮不到它）")})
        else:
            entries.append({
                "kind": "ghost_skill", "name": str(name),
                "carrying_routes": carrying,
                "reason": (f"技能名 {name} 不在 agent 策略词汇表里 ⇒ "
                           f"`_apply_one` 恒等且被记为『适用』⇒ active 非空"
                           f" ⇒ L4 记忆通路被压制")})
    return {
        "dead_entries": entries, "n_dead": len(entries),
        "note": ("保守口径：`uci:` 前缀一律不报（接入层契约形态，静态不可判）；"
                 "名字在词汇表里且在链上 ⇒ 活的。⇒ 同样是**下界**，"
                 "真实死条目只会更多。"),
    }


def cleanup_genes(h: Harness, entry: Mapping[str, Any]) -> Tuple[Gene, ...]:
    """一条死条目 ⇒ 一组**必须一起生效**的删除基因。

    ⛔ 为什么是一组而不是一个：只删技能 ⇒ 名字仍挂在路由链上 ⇒ 留下一条
       **幽灵路由**（agent 遇到链上未装载的项会跳过并记 `missing`，
       不崩，但账本上永远留着"链上有它、skills 里没有它"的不一致）。
       ⇒ 摘链 + 删技能必须在**同一个 capsule** 里，否则清理本身在制造
       新的死条目 —— 那比不清理更糟（它让死条目从一处变成两处）。

    ⛔ 这也正是 `Capsule` 存在的理由：一组协同编辑作为整体被选择。
    """
    name = str(entry.get("name") or "")
    if not name:
        return ()
    genes: List[Gene] = [gene_route_remove(str(k), name)
                         for k in (entry.get("carrying_routes") or [])]
    genes.append(gene_remove_skill(name,
                                   rationale=str(entry.get("reason") or "")))
    return tuple(genes)


def genome_load(h: Harness) -> int:
    """基因组侧的自指负载：死条目数。**每次现扫**，不吃缓存。

    ⛔ 为什么减负读数必须现扫、不能相信提案时算好的数：
       "清理后少 1 条"这个说法要能被**复核** —— 真正的验证是拿**应用后**
       的 harness 再扫一遍。对不上就是提案在撒谎（哪怕是无意的）。
       这也是它必须是一根**独立轴**的原因：reward 是执行出来的，
       load 是扫出来的，两者互不替代。
    """
    return int(genome_dead_entries(h).get("n_dead") or 0)


def source_load() -> Dict[str, int]:
    """源码侧的自指负载：**三类**死形态的读数。

        · `dead_api`     零消费方的公开接口（下界 12）—— **启发式**
        · `write_only`   只写不读的属性（下界 10）      —— **启发式**
        · `unreachable`  控制流上永不可达的语句块        —— **可证明**

    ⛔ ⛔ **很贵**（三次全仓 AST 扫描）⇒ 只许每**次运行**扫**一次**，
       不许每轮扫。落点：`evolution.run()` 末尾记一个 `cleanup.scan` 事件。
       每轮扫它 ⇒ 进化循环会在编码工作上花掉比评估还多的时间，
       那是把"努力分配"砍向了另一端。

    ⛔ 三个键的**性质不同**，别把它们加到一起当成"债有多少"：
       前两个是"看起来没人用"（只能支撑提案），第三个是"执行流到不了"
       （能支撑判定）。混成一个数 ⇒ 判据失去它唯一可证明的那一半。
    """
    return {"dead_api": int(_sr.scan_dead_api().get("n_dead") or 0),
            "write_only": int(_sr.scan_write_only().get("n_write_only") or 0),
            "unreachable": int(
                _sr.scan_unreachable().get("n_unreachable") or 0)}


def build(*, limit: int = 20) -> List[CleanupProposal]:
    """从自指负载扫描生成清理提案（**只读**：不改任何文件）。

    ⛔ 判据集模块**在构造上**被排除：清理不是"能删任何代码"的通道，
       否则 `judge` 锁死的那件事就白做了。
    """
    dead = _sr.scan_dead_api()
    wo = _sr.scan_write_only()
    un = _sr.scan_unreachable()
    load_before = {"dead_api": int(dead["n_dead"]),
                   "write_only": int(wo["n_write_only"]),
                   "unreachable": int(un["n_unreachable"])}
    #: ⛔ 排除面与 `validate()` **同源**（见 `_protected_modules`）。
    #:    这里是**扫都不扫**，不是"扫出来再拦掉"。
    protected = set(_protected_modules())
    out: List[CleanupProposal] = []
    #: ⛔⛔ **只清理产品代码**（`aether/` 之内）。测试与脚本里的"零消费方
    #:    符号"不是死代码 —— 它们由**测试夹具**在运行时读写（实测踩过：
    #:    `_FS_WARNED`（contract_sandbox_test）与 `scores`
    #:    （contract_difficulty_test）被提了出来，而它们都是夹具的一部分）。
    #:    把"静态扫不到调用方"当成"没人用"，在测试代码里尤其危险：
    #:    测试的调用方式（fixture / 动态改属性）天生静态不可见。
    def _is_prod(where: str) -> bool:
        return str(where).startswith("aether/")

    for d in dead["dead_api"][:limit]:
        if not _is_prod(d["file"]):
            continue
        fname = Path(str(d["file"])).stem
        if fname in protected:
            continue
        p = CleanupProposal(
            kind="dead_api", target=str(d["name"]), where=f"{d['file']}:{d['line']}",
            reason=(f"零消费方的公开接口（保守扫描：名字未在别处以标识符"
                    f"**或字符串**出现过）"),
            load_before=dict(load_before),
            load_after_expected={"dead_api": load_before["dead_api"] - 1,
                                 "write_only": load_before["write_only"],
                                 "unreachable": load_before["unreachable"]},
            patch=(f"# 删除 {d['file']} 第 {d['line']} 行起的 "
                   f"`def {d['name']}(...)`\n"
                   f"# ⛔ 由**人**执行：本模块只提案，不改源码"),
            evidence={"scan_entry": d,
                      "scan_note": dead["note"],
                      #: ⛔ 这一类**不给** `edits` —— 它的判据是启发式的
                      #:    （名字没出现过 ⇒ 可能有反射/框架回调），
                      #:    撑不起"删掉行为等价"这个证明 ⇒ 只能提案。
                      "editable": False,
                      "protected_excluded": sorted(protected)})
        p.prop_id = content_hash(p.identity())[:_ID_PREFIX]
        out.append(p)
    for w in wo["write_only"][:limit]:
        if not _is_prod(w["first_site"]):
            continue
        p = CleanupProposal(
            kind="write_only", target=str(w["attr"]),
            where=str(w["first_site"]),
            reason=(f"只写不读的属性（赋值 {w['n_assign']} 次，全仓无读取）"),
            load_before=dict(load_before),
            load_after_expected={"dead_api": load_before["dead_api"],
                                 "write_only": load_before["write_only"] - 1,
                                 "unreachable": load_before["unreachable"]},
            patch=(f"# 删除 {w['first_site']} 的赋值（`self.{w['attr']} = …`）\n"
                   f"# ⛔ 由**人**执行"),
            evidence={"scan_entry": w, "scan_note": wo["note"],
                      "editable": False,
                      "protected_excluded": sorted(protected)})
        p.prop_id = content_hash(p.identity())[:_ID_PREFIX]
        out.append(p)

    # ── ③ 不可达块：**唯一可证明**的一类 ⇒ 带可执行编辑 ──────────
    for u in un["unreachable"][:limit]:
        if not _is_prod(u["file"]):
            continue
        fname = Path(str(u["file"])).stem
        if fname in protected:
            continue
        #: ⛔ 拿不到指纹 ⇒ **没有编辑**（读不动/区间非法）⇒ 不提。
        #:    提出一条执行不了的提案，比不提更糟：它占着"待办"的位置，
        #:    而人看不出来它根本做不了。
        e = _srcmut.from_lines(_ROOT, str(u["file"]),
                               int(u["start_line"]), int(u["end_line"]))
        if e is None:
            continue
        p = CleanupProposal(
            kind="unreachable",
            target=f"{fname}:{u['start_line']}-{u['end_line']}",
            where=f"{u['file']}:{u['start_line']}-{u['end_line']}",
            reason=(f"控制流上永不可达：`{u['kind']}` 在第 {u['terminator_line']}"
                    f" 行终止，其后 {e.n_lines} 行 / {u['n_stmts']} 条语句"
                    f" 永远执行不到 ⇒ 删掉**行为等价**"),
            load_before=dict(load_before),
            load_after_expected={"dead_api": load_before["dead_api"],
                                 "write_only": load_before["write_only"],
                                 "unreachable": load_before["unreachable"] - 1},
            patch=(f"# 删除 {u['file']} 第 {u['start_line']}–{u['end_line']} 行"
                   f"（共 {e.n_lines} 行，位于第 {u['terminator_line']} 行的 "
                   f"`{u['kind']}` 之后）\n"
                   f"# ⛔ 由**人**执行（本模块只提案，不改源码）"),
            edits=[e.to_dict()],
            evidence={"scan_entry": u, "scan_note": un["note"],
                      "editable": True, "edit_id": e.edit_id,
                      "sha256": e.sha256, "n_lines": e.n_lines,
                      "proof": ("控制流级证明，不是测试通过：终止语句之后的"
                                "同级语句不可达 ⇒ 删掉不改变任何执行结果"),
                      "protected_excluded": sorted(protected)})
        p.prop_id = content_hash(p.identity())[:_ID_PREFIX]
        out.append(p)
    return out


def validate(p: CleanupProposal, *,
             non_regression_ok: Optional[bool] = None,
             judge_modules: Optional[Sequence[str]] = None) -> CleanupProposal:
    """过闸。**逐项记因**（只报第一个会让人修完一个又冒一个）。

    ① 不在判据集里（**构造上**排除"清理碰判据"这条通道）；
    ② 减负可测：必须给出"清理后自指负载下降"的具体读数；
    ③ 非回归必须有**通过裁决**（冻结契约删前删后结果一致）；
    ④ 必须给出补丁（拿不出怎么改 ⇒ 不可执行 ⇒ 不可验收）；
    ⑤ 可证明的那类（不可达块）必须带**可执行的结构化编辑**。

    ⛔⛔ **第 ③ 条是 2026-10-16 实测修掉的**：原写法是
        `if non_regression_ok is False:` ⇒ 调用方不传（None）就**跳过**，
        而两个生产调用点（`cli.py` 的 validate / approve）从来不传
        ⇒ 四道闸里唯一会拦的那一道**从未被喂过裁决**。
        看着四道闸，实际零道在拦 —— 标准假仪表。
        ⇒ 改成 **fail-closed**：拿不到**明确的通过裁决**就拦。
        ⛔ 代价是"没接 runner 的调用点会全部转红"，而那是**正确的**：
          它把"这条提案从没被验证过"这件事显式暴露出来，
          而不是让它在 `gate_passed=True` 下面静默躺着。
    """
    p.gate_reasons = []
    #: ⛔ 排除面与 `build()` **同源**（`_protected_modules`）。
    #:    ⛔ `judge_modules` 参数是**追加**进来的，不是替换 —— 传了它却把
    #:       默认排除面顶掉，等于让调用方有能力松开判据层那道锁。
    mods = _protected_modules(judge_modules)
    # ① 不许碰判据集
    if any(m and m in p.where for m in mods):
        p.gate_reasons.append(
            f"目标落在判据集模块里（{p.where}）⇒ 清理不是'能删任何代码'的"
            f"通道；判据集只走重新发布（见 scripts/relaunch_ledger.py）")
    # ② 减负可测
    b, a = p.load_before, p.load_after_expected
    if not b or not a:
        p.gate_reasons.append("缺减负读数（自指负载清理前后）⇒ 无法判它是否真的减负")
    else:
        delta = {k: int(b.get(k, 0)) - int(a.get(k, 0)) for k in set(b) | set(a)}
        if not any(v > 0 for v in delta.values()):
            p.gate_reasons.append(
                f"减负读数为 {delta} ⇒ 没有任何一项下降 ⇒ 这次清理不可测")
        p.evidence["load_delta"] = delta
    # ③ 非回归：⛔⛔ **fail-closed**（见 docstring：原来这道闸恒不触发）
    if non_regression_ok is not True:
        p.gate_reasons.append(
            "非回归闸**没有通过裁决**"
            + ("" if non_regression_ok is None
               else f"（裁决={non_regression_ok}）")
            + " ⇒ 拿不到「删前删后冻结契约一致」的证据就不放行"
              "（想放行请注入 runner 跑 `srcmut.probe`）")
    # ④ 可执行
    if not p.patch.strip():
        p.gate_reasons.append("缺补丁 ⇒ 拿不出'怎么改'的提案不可执行")
    # ⑤ 可证明的那类必须带**可执行**的编辑（对拍只能靠它）
    if p.kind == "unreachable" and not p.edits:
        p.gate_reasons.append(
            "不可达块缺结构化编辑 ⇒ 无法在副本里应用 ⇒ 拿不到对拍裁决")
    p.gate_passed = not p.gate_reasons
    p.state = PROPOSED if p.gate_passed else REJECTED
    return p


def apply_patch(p: CleanupProposal, *, approved_by: str,
                yes: bool = False) -> CleanupProposal:
    """**人批准**这一步（与 `caps_candidate` 同族：没有 `--yes` 一律不推进）。

    ⛔ 本函数**不写任何文件**：它只把提案标成可执行并附上补丁。
       真正改源码由人做 —— 自动改自己的源码与"判据必须离线可回归"冲突，
       而且删错了**不可逆**（账本只能留痕，不能还原没提交的删除）。
    """
    if not p.gate_passed or p.state == REJECTED:
        p.gate_reasons.append("未过闸的提案不得批准（先把闸的问题解决）")
        return p
    if not yes:
        p.gate_reasons.append("缺少显式批准（--yes）：本系统不代你决定删什么")
        return p
    if not str(approved_by).strip():
        p.gate_reasons.append("批准必须留名（approved_by 不能为空）")
        return p
    p.state = APPLIED
    p.evidence["approved_by"] = str(approved_by).strip()
    return p


def load(path: Any) -> Dict[str, CleanupProposal]:
    """读提案档案（坏文件 ⇒ 空表，**不抛错**：读路径要稳）。"""
    import json
    f = Path(path)
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return {}
    out: Dict[str, CleanupProposal] = {}
    for d in (data.get("proposals") or []):
        if not isinstance(d, dict):
            continue
        p = CleanupProposal(
            prop_id=str(d.get("prop_id") or ""), kind=str(d.get("kind") or ""),
            target=str(d.get("target") or ""), where=str(d.get("where") or ""),
            reason=str(d.get("reason") or ""), state=str(d.get("state") or PENDING),
            gate_passed=bool(d.get("gate_passed")),
            gate_reasons=list(d.get("gate_reasons") or []),
            load_before=dict(d.get("load_before") or {}),
            load_after_expected=dict(d.get("load_after_expected") or {}),
            patch=str(d.get("patch") or ""),
            edits=list(d.get("edits") or []),
            evidence=dict(d.get("evidence") or {}))
        if p.prop_id:
            out[p.prop_id] = p
    return out


def save(path: Any, props: Mapping[str, CleanupProposal]) -> None:
    """写提案档案（**原子替换**：半截文件会让人读到"没有提案"）。"""
    import json
    import os
    f = Path(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(
        {"note": "清理提案（便利用缓存；**事实源是账本**的 cleanup.proposal 事件）",
         "proposals": [p.to_dict() for p in
                       sorted(props.values(), key=lambda x: x.prop_id)]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def record(ledger: Any, p: CleanupProposal, *, actor: str = "cleanup") -> Optional[int]:
    """提案进账本。**没挂账本 ⇒ 返回 None**（不假装记过）。"""
    if ledger is None:
        return None
    return ledger.append("cleanup.proposal", p.to_dict(), actor=actor).seq

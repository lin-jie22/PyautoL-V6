"""跨环境泛化闸（Held-Out Generalization Gate）。

非回归闸回答的是：**有没有变坏**。
泛化闸回答的是另一个方向的问题：**"发展集上变好了"是真的变强了，还是
只是学会了讨好这一批判据？**

这两件事在单环境里**无法区分**。self-harness 系统一旦只在一个判据集上做
选择，最优策略很快会滑向"针对判据硬编码"——它每一轮都在涨，但它没有变强。
这是 reward hacking 在自进化系统里的温和形态，也是最难被发现的一种：
因为所有数字都很好看，而且每一步都是"真的通过了执行判据"。

V6 的处理方式：**另开一个从不参与选择的环境**，只在裁决时用一次。

三条硬纪律（缺一条这套闸就退化成第二个发展集）：

  H1 **held-out 不参与选择**。候选之间的比较只用发展集 reward；
     held-out 的分数**不得**进入 `group_relative`、不得进入 best 的排序。
     一旦它参与选择，它就变成了发展集，闸就自我取消了。
  H2 **结构性不重叠**。`GeneralizationGate` 在构造时就校验 held-out
     与发展集没有共同 task_id，重叠直接抛异常 —— 不靠调用方自觉。
  H3 **held-out 上不涨 ⇒ 判 overfit，不提交**。哪怕发展集涨得很好看。
     宁可少提交一次，也不能把"讨好判据"固化进 harness。

⛔ 泛化闸是**准入闸**（gate），不是评估项（metric）：
   它不参与"哪个候选更好"的比较，只在最后做一次放行/拦截。
   与非回归闸的关系：非回归闸拦"变坏"，泛化闸拦"假变好"，两道都要过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple)

from .genome import Harness
from .nongression import FrozenTask
from .verifier import Verdict, error_verdict

Runner = Callable[[Harness, Dict[str, Any]], Verdict]

#: 裁决取值。**封闭集合**：出现集合外的值说明有新的失败形态没被归类。
#: `partial` = 整体涨了但**只在部分难度层涨**（典型：只会修简单题）。
#:
#: ⛔ 后面两个是**清理候选**专用的：`removal_safe` / `removal_regress`。
#:    为什么不复用 `generalize`：泛化闸问的是"发展集上涨的那部分是真的吗"，
#:    而清理**根本不涨**（删掉死条目 reward 不变）⇒ 它在 `_probe_inner` 的
#:    第一道 `dev_gain <= dev_floor` 就会被判 `no_gain` ⇒ `blocks=True`
#:    ⇒ 清理在结构上永远无法提交。不是闸太严，是**用错了闸**：
#:    对清理该问的是"删完之后 held-out 变差没有"，不是"涨了没有"。
VERDICTS = ("generalize", "overfit", "partial", "no_gain", "inconclusive",
            "removal_safe", "removal_regress")

#: 放行的裁决。**封闭集合**（与 VERDICTS 同源）。
PASS_VERDICTS = ("generalize", "removal_safe")


@dataclass(frozen=True)
class HeldOutSuite:
    """一组**不参与选择**的任务。来自与训练/发展环境不同的环境。"""
    name: str
    tasks: Tuple[FrozenTask, ...]
    env_id: str = ""
    #: task_id → 难度（easy/hard）。空 ⇒ 不分层（行为与早期版本一致）。
    #: 见 aether/repair.py：难度由"通用修法能否修好"**执行判定**，不靠标注。
    difficulty: Tuple[Tuple[str, str], ...] = ()

    @property
    def task_ids(self) -> Tuple[str, ...]:
        return tuple(t.task_id for t in self.tasks)

    @property
    def n_tasks(self) -> int:
        return len(self.tasks)

    @property
    def difficulty_map(self) -> Dict[str, str]:
        return dict(self.difficulty)

    def ids_of(self, level: str) -> Tuple[str, ...]:
        d = self.difficulty_map
        return tuple(t.task_id for t in self.tasks if d.get(t.task_id) == level)

    @property
    def levels(self) -> Tuple[str, ...]:
        return tuple(sorted({v for _k, v in self.difficulty}))

    @property
    def spread(self) -> bool:
        """有没有难度跨度。⛔ 没有跨度的 held-out **判别力不足**。"""
        return len(self.levels) >= 2

    @property
    def discriminative(self) -> Optional[bool]:
        """这套 held-out **有没有能力**区分「真泛化」与「只会修一类题」。

        三态，⛔ 三态必须区分开，合成两态会把"没测"当成"没问题"：

          True  —— 有难度标签**且**有跨度 ⇒ 判别力已确认；
          False —— 有难度标签**但只有一层** ⇒ **有证据**的判别力不足
                   （缺的那一层没被测，放行结论不可信）；
          None  —— **没有难度标签** ⇒ 判别力**未测**，不是"不足"。
                   （V5：不可判别 ≠ 有罪 —— 没测过不等于有问题，
                     所以这种情况不拦，只在裁决里标注。）

        ⛔ 存在理由（`False` 这一支）：2026-10-13 实测反证
           `scripts/probe_spread_audit.py` —— 同一个"只在 easy 层涨"的
           挑食候选，held-out 采到 hard 层 ⇒ `partial`（拦）；少采一层
           ⇒ `generalize`（**放行**），且输出里照样带着 `by_difficulty`
           字段，看起来像做过分层裁决。也就是说放行结论依赖的是
           **采样量**，不是候选行为。
        """
        if not self.difficulty:
            return None
        return self.spread


@dataclass
class ProbeResult:
    """一次泛化探针的完整裁决记录（无论放行还是拦截都保留）。"""
    verdict: str
    dev_gain: float = 0.0
    heldout_gain: float = 0.0
    base_heldout: float = 0.0
    cand_heldout: float = 0.0
    n_tasks: int = 0
    reason: str = ""
    #: 各难度层的 held-out 增益。⛔ 只看总体会放过"只在简单题上泛化"。
    by_difficulty: Dict[str, float] = field(default_factory=dict)
    #: 各难度层的**发展集**增益（同层对照组）。没有它就无法区分
    #: 「挑食」与「能力边界」——见 probe() 的说明。
    dev_by_difficulty: Dict[str, float] = field(default_factory=dict)
    #: 被判为"超出能力边界"的层（同层发展集也涨不动 ⇒ 不处罚，不是挑食）
    out_of_reach: Tuple[str, ...] = ()
    #: held-out 自身的判别力三态（True/False/None，见 `HeldOutSuite.discriminative`）。
    #: ⛔ False 出现在一个 `generalize` 裁决里 ⇒ 那个放行**不可信**，
    #:   必须是 bug。观测侧要能一眼看见，不能只看 verdict。
    discriminative: Optional[bool] = None
    #: ⛔⛔ **每层还剩几条能涨**（r53）。此前它只活在 `reason` 的文案里
    #:    （「easy(可提升空间 0 条)」）⇒ 观测侧要拿它做趋势/告警，只能去
    #:    正则解析一句给人看的话 —— 改一次措辞，所有下游读数同时失真。
    #:    ⇒ 判据用到的量**必须**是结构化字段，文案只是它的一种渲染。
    headroom: Dict[str, int] = field(default_factory=dict)
    #: 因「可提升空间 < min_level_headroom」而未计入裁决的层（样本不足）。
    #: ⛔ 与 `out_of_reach`（能力边界）分开：两者都不处罚，但**出路不同**
    #:    —— 这条要去补 held-out 该层的题数，那条是能力到此为止。
    thin: Tuple[str, ...] = ()
    #: ⛔ **完全饱和层**（r54）：该层基线**已全部修好**（可提升空间 = 0）
    #:   ⇒ 这一层的 held-out 增益**必然**是 0，承载不了任何信息。
    #:   ⛔ 与 `thin` 是两件事，不许合并：
    #:     · `thin`      = 还剩几条能涨，但太少 ⇒ 统计上不可判别（要补题）；
    #:     · `saturated` = 一条都涨不了 ⇒ 读数必然为 0（要换更难的题/换环境）。
    #:     合并后"该去补题"与"该去换环境"这两条出路会一起消失，而账面
    #:     仍然显示"这一层被豁免了"（第 14 条：两种含义共用一个字段 ⇒ 读错）。
    saturated: Tuple[str, ...] = ()

    @property
    def blocks(self) -> bool:
        """是否拦截提交。⛔ 未知裁决一律拦截（fail-closed）。

        ⛔ 从 `PASS_VERDICTS` 派生，不写死 `== "generalize"`：
           新增 `removal_safe` 后，"放行"有两个名字，写死就会把
           **合法通过的清理**判成拦截 —— 而那种失败不会报错，只是
           清理永远提交不了，看起来像"清理没用"。
        """
        return self.verdict not in PASS_VERDICTS

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict,
                "discriminative": self.discriminative,
                "dev_gain": round(self.dev_gain, 4),
                "heldout_gain": round(self.heldout_gain, 4),
                "base_heldout": round(self.base_heldout, 4),
                "cand_heldout": round(self.cand_heldout, 4),
                "n_tasks": self.n_tasks,
                "by_difficulty": {k: round(v, 4)
                                  for k, v in self.by_difficulty.items()},
                "dev_by_difficulty": {k: round(v, 4)
                                      for k, v in self.dev_by_difficulty.items()},
                "out_of_reach": list(self.out_of_reach),
                "headroom": dict(self.headroom),
                "thin": list(self.thin),
                "saturated": list(self.saturated),
                "reason": self.reason}


class GeneralizationGate:
    """跨环境泛化闸。**只在 best 选出之后跑一次**。"""

    def __init__(self, suite: HeldOutSuite,
                 dev_task_ids: Optional[Iterable[str]] = None,
                 heldout_floor: float = 0.0,
                 dev_floor: float = 0.0,
                 min_tasks: int = 1,
                 base_at_ceiling: Optional[float] = None,
                 min_level_headroom: int = 3,
                 grow_fn: Optional[Callable[[int], List[FrozenTask]]] = None,
                 difficulty_fn: Optional[
                     Callable[[List[FrozenTask]], Dict[str, str]]] = None,
                 grow_ceiling: int = 64,
                 grow_max_tasks: int = 96,
                 grow_variant_fns: Optional[
                     Sequence[Callable[[int], List[FrozenTask]]]] = None):
        # H2：结构性不重叠。重叠 ⇒ 这套闸自我取消 ⇒ 构造期就炸，不靠自觉。
        dev_ids = set(dev_task_ids or ())
        overlap = sorted(set(suite.task_ids) & dev_ids)
        if overlap:
            raise ValueError(
                f"held-out 与发展集重叠 {overlap[:5]} ⇒ 它就不再是 held-out，"
                f"泛化闸会自我取消")
        self.suite = suite
        self.dev_task_ids = frozenset(dev_ids)
        #: held-out 增益必须**严格超过**这个值（等于即判 overfit）。
        #: 默认 0 ⇒ 必须在 held-out 上真的有正向增益，光"没退化"不算。
        self.heldout_floor = heldout_floor
        self.dev_floor = dev_floor
        self.min_tasks = min_tasks
        #: held-out 的满分线（reward 上界）。None ⇒ 不做天花板判定。
        self.base_at_ceiling = base_at_ceiling
        #: ⛔⛔ **分层裁决的最小样本量**（r52）。一条难度层里基线**还没修好**
        #:    的题少于这个数 ⇒ 该层"没涨"**不可判别** ⇒ 归入 undecidable
        #:    （`inconclusive`），**不许**判挑食（第 11 条：不可判别 ≠ 有罪）。
        #:
        #:    实测（r51 的 depth2+均衡口径）：轮 2 起 held-out 的 hard 层
        #:    **只剩 1 条**未解题（7 条里 6 条已被上一代修好），而发展集
        #:    同层还剩 9 条 ⇒ 候选在 dev 上修好 4/9、在 ho 上修好 0/1
        #:    ⇒ `dev_by_difficulty` 是 +0.138 而 `by_difficulty` 是 +0.000
        #:    ⇒ 判「挑食」，连续 4 轮。**0/1 与 4/9 在统计上无法区分** ——
        #:    那不是挑食的证据，是分母太小。
        #:    ⛔ 处置：样本不足的层**不进 lagging** ⇒ 它既不能作证也不能
        #:    作反证（与 `out_of_reach` 同处置：不处罚，但**必须点名**）。
        #:    这不是放宽判据 —— 判据"某层没涨 ⇒ 挑食"对**有样本**的层
        #:    一字未改（G2 守着它）；对没样本的层，它本来就不适用。
        #:    ⛔ 默认 3：不小于 3 条才有资格谈"这一层涨没涨"。
        self.min_level_headroom = int(min_level_headroom)
        #: ── r53：判别力**自愈**的入口 ──────────────────────────────
        #: `all_thin` 分支（每层可提升空间都不足）报的出路一直是
        #: 「补 held-out 的题数，不是放宽判据」，但**系统自己没有这个出口**
        #: ⇒ 明知有 +0.4 的增益，也只能三轮逐字重复地拦着（r52 实测）。
        #:
        #: ⛔⛔ 补题的筛选判据是「**当前基线**做不对」—— 它是**基线**的属性，
        #:    不是候选的属性 ⇒ 不看候选 ⇒ **不给任何候选优待**（与 dev 侧
        #:    `grow_for_difficulty` 同形态：那边补的是"层"，这边补的是"空间"）。
        #:    ⇒ H1（held-out 不参与选择）一条没动：补出来的题不进发展集、
        #:      不进排序、不进 `group_relative`，只用于裁决。
        #:
        #: ⛔ 两个函数都**由调用方注入**（None ⇒ 不自愈，与历史逐字同口径）：
        #:    本闸不 import `environment` / `repair`，否则会成循环依赖，
        #:    而且合成参数（depth / kinds / balanced）属于**实验口径**，
        #:    只能由CLI 决定 —— 闸不该知道这次实验用什么口径造题。
        self.grow_fn = grow_fn
        self.difficulty_fn = difficulty_fn
        #: ── r55：**换采样口径**（不是加大采样量）────────────────────
        #: ⛔⛔ 为什么需要它（实测 `probe_headroom_source` A/E/G 段）：
        #:    单个环境在**一个**采样口径下的题源有**硬上限**：
        #:
        #:        max_n = 16/32/64/128/256 ⇒ 产出恒定 26 条（新增 0）
        #:        depth = 2/3/4           ⇒ 产出恒定 26 条（新增 0）
        #:
        #:    ⇒ r53 那条「加大 `max_n` 补题」的自愈，撞上这个顶之后**静默
        #:      失败**（实测 `steps` 三档 `n_added` 全是 0，而 note 只说
        #:      「补到上限仍缺」—— "题源没了" 与 "没补对层" 在文案里同形）。
        #:
        #:    但换一个采样口径（`balanced` 取反）在同一环境上产出的是
        #:    **完全不重叠**的另一批题（实测新增 26 条，重叠 0 条）
        #:    —— 与第 29 条那个「`which` 序号挤压」同构：**换顺序会换一批题**。
        #:
        #: ⛔ 这里刻意**不换环境**：换环境会动 held-out 的语义（H1 的
        #:    "另一个环境"是这套闸的定义），而换采样口径只换"从同一个
        #:    环境里取哪几个变异点"，语义不变、与候选依旧无关。
        #:
        #: ⛔ 默认 None/空 ⇒ 只用 `grow_fn` 一个口径 ⇒ 与 r53/r54 逐字
        #:    同口径（第 40 条：默认口径保持原样，新口径显式开启）。
        self.grow_variant_fns = tuple(grow_variant_fns or ())
        self.grow_ceiling = int(grow_ceiling)
        self.grow_max_tasks = int(grow_max_tasks)

    @property
    def env_pair(self) -> str:
        """「哪套发展集 → 哪套 held-out」的稳定标识。

        ⛔ 存在理由：死路冷却必须能区分「这个候选在这套环境对上是死路」与
        「换了一套环境，它也许能过」。没有这个字段，冷却只能按 capsule 全局
        生效 ⇒ 换环境后被永久误伤（实测 `gen.probe` 事件里它一直是 None）。
        ⛔ 取不到就如实返回 `unknown`，**不许**编一个看似正常的值 ——
        冷却对 unknown 会退化成全局维度，并在事件里标注（不静默）。
        """
        ho = str(getattr(self.suite, "env_id", "") or "")
        devs = sorted({str(t).split("::", 1)[0]
                       for t in self.dev_task_ids if "::" in str(t)})
        if not devs or not ho:
            return "unknown"
        return f"{'|'.join(devs)}->{ho}"

    # ── 评估 held-out（**不得**用于候选之间比较）─────────────────────
    def evaluate(self, h: Harness, runner: Runner) -> Tuple[float, List[Verdict]]:
        """在 held-out 上跑一遍，返回 (平均 reward, 判定列表)。"""
        vs: List[Verdict] = []
        for t in self.suite.tasks:
            try:
                vs.append(runner(h, t.spec))
            except Exception as e:  # noqa: BLE001 —— 记账，不吞
                vs.append(error_verdict(f"heldout/{t.task_id}", e))
        if not vs:
            return 0.0, vs
        return sum(v.reward for v in vs) / len(vs), vs

    def _gain_by_difficulty(self, base_vs: List[Verdict],
                            cand_vs: List[Verdict]) -> Dict[str, float]:
        """按难度层分别算 held-out 增益。

        ⛔ 总体增益会**掩盖**"只在简单题上泛化"：easy 涨、hard 不动，
        平均下来仍然是正的。分层之后这种候选会被判 `partial`。
        """
        d = self.suite.difficulty_map
        if not d:
            return {}
        ids = self.suite.task_ids
        base_by_id = {tid: v.reward for tid, v in zip(ids, base_vs)}
        cand_by_id = {tid: v.reward for tid, v in zip(ids, cand_vs)}
        out: Dict[str, float] = {}
        for level in self.suite.levels:
            sel = self.suite.ids_of(level)
            if not sel:
                continue
            b = sum(base_by_id.get(t, 0.0) for t in sel) / len(sel)
            c = sum(cand_by_id.get(t, 0.0) for t in sel) / len(sel)
            out[level] = c - b
        return out

    # ── r53：判别力体检 + 自愈 ────────────────────────────────────────
    def headroom_of(self, h: Harness, runner: Runner) -> Dict[str, int]:
        """每层**还剩几条基线没修好**的题 = 这一层真正能涨的空间。

        ⛔ 与 `_probe_inner` 里那个 `headroom` **同源**（同一份判据：
        `reward < 1.0`），不许各写一份 —— 两处口径一漂，"闸说没空间"
        和"体检说还有空间"就会同时成立，而两边看起来都对。
        """
        _mean, vs = self.evaluate(h, runner)
        by_id = {tid: v.reward for tid, v in zip(self.suite.task_ids, vs)}
        return {
            lv: sum(1 for t in self.suite.ids_of(lv)
                    if by_id.get(t, 0.0) < 1.0)
            for lv in self.suite.levels}

    def ensure_headroom(self, h: Harness, runner: Runner,
                        min_headroom: Optional[int] = None,
                        round_no: int = 0) -> Dict[str, Any]:
        """把 held-out 补到「每层都还有 ≥ min_headroom 条能涨的题」。

        ⛔⛔ 为什么必须在**循环内**做（每轮一次），不能建卷时做一次：
            headroom 是「基线还没修好几条」⇒ 它随能力提升而**缩小**
            （实测：轮 1 装上 fix_control_flow 后，held-out easy 层
            可提升空间立刻从 N 变 0）。建卷时用种子基线量 ⇒ 基线最弱
            ⇒ headroom 最大 ⇒ 永远达标 ⇒ 这个读数完全没有意义。
            ⇒ **尺子要跟着被测对象长**（与第 42 条"基线随 best 升级
            才有判别力"是对称的两件事：那边长的是基线，这边长的是卷子）。

        ⛔ 补题**只看当前基线**：不用候选、不用发展集、不用 reward 排序
            ⇒ 不给任何候选优待，只解决"这张卷子已经考不出差别了"。

        ⛔ 到顶仍补不齐 ⇒ **如实报缺失**（`missing`），绝不假装补齐：
            "卷子不够"必须能被看见，否则它会被读成"候选不好"。
        """
        want = int(min_headroom if min_headroom is not None
                   else self.min_level_headroom)
        if self.grow_fn is None or self.difficulty_fn is None:
            return {"enabled": False, "grew": 0,
                    "note": "未注入补采样函数 ⇒ 不自愈（与历史同口径）"}
        head0 = self.headroom_of(h, runner)
        if not self.suite.levels:
            return {"enabled": True, "grew": 0, "headroom": head0,
                    "note": "held-out 没有难度标签 ⇒ 无从谈层内空间"}
        deficit = sorted(lv for lv, c in head0.items() if c < want)
        rep: Dict[str, Any] = {"enabled": True, "round_no": round_no,
                               "want": want, "headroom_before": dict(head0),
                               "deficit": list(deficit), "steps": [],
                               "grew": 0, "missing": []}
        if not deficit:
            rep["note"] = "每层空间都够 ⇒ 不补"
            return rep

        seen = set(self.suite.task_ids)
        tasks = list(self.suite.tasks)
        labels = dict(self.suite.difficulty)
        head = dict(head0)
        #: ⛔ r55：口径序列 = 主口径 + 备选口径（默认只有主口径 ⇒ 历史同形）。
        variants = (self.grow_fn,) + tuple(self.grow_variant_fns)
        rep["n_variants"] = len(variants)
        #: ⛔ 「建卷外有题、但它的层已经达标」的题数。没有这个读数，
        #:    `grew=0` 读不出是"题源没了"还是"有题但那层不缺"—— 而这两者
        #:    的出路完全相反（换题源 vs 什么都不用做）。第 10 条同形病。
        outside_deficit = 0
        for vi, gfn in enumerate(variants):
            if not deficit:
                break
            #: ⛔ 每个口径都从**当前卷子大小**重新起步：`n` 是合成器的
            #:    "最多取几个变异点"，不该继承上一个口径的进度。
            n = max(len(tasks), 4)
            while deficit and n < self.grow_ceiling and len(tasks) < self.grow_max_tasks:
                n = min(self.grow_ceiling, n * 2)
                try:
                    cand = [t for t in (gfn(n) or [])
                            if getattr(t, "task_id", "") not in seen]
                except Exception as e:                         # noqa: BLE001
                    #: ⛔ 合成挂了 ⇒ **记账并停手**，不许静默跳过（第 10 条）：
                    #:   "补不动"与"不用补"在输出里必须可分辨。
                    #:   ⛔ r55：这里只跳出**当前口径**的 while，for 会继续
                    #:   试下一个口径 —— 一个口径的合成器挂了，不等于别的
                    #:   口径也挂了。每次失败都留在 `steps` 的 error 里，
                    #:   不会因为有退路就变得不可见。
                    rep["steps"].append({"variant": vi, "max_n": n, "error":
                                         f"{type(e).__name__}: {e}"})
                    break
                if not cand:
                    rep["steps"].append({"variant": vi, "max_n": n,
                                         "n_new": 0})
                    continue
                try:
                    lab = self.difficulty_fn(cand) or {}
                except Exception as e:                         # noqa: BLE001
                    rep["steps"].append({"variant": vi, "max_n": n, "error":
                                         f"{type(e).__name__}: {e}"})
                    break
                added = 0
                for t in cand:
                    if len(tasks) >= self.grow_max_tasks:
                        break
                    lv = str(lab.get(getattr(t, "task_id", ""), "") or "")
                    #: ⛔ 只收**缺空间的层**，且**不与已有/发展集重复**（H2）。
                    if lv not in deficit:
                        outside_deficit += 1
                        continue
                    if getattr(t, "task_id", "") in set(self.dev_task_ids):
                        continue
                    #: 判据同源：这条题**当前基线做不对**才算"还有空间"。
                    try:
                        v = runner(h, dict(t.spec))
                    except Exception:                          # noqa: BLE001
                        continue
                    if float(getattr(v, "reward", 0.0) or 0.0) >= 1.0:
                        continue
                    seen.add(t.task_id)
                    tasks.append(t)
                    labels[t.task_id] = lv
                    added += 1
                    if head.get(lv, 0) >= want:
                        deficit.remove(lv)
                        if not deficit:
                            break
                rep["steps"].append({"variant": vi, "max_n": n,
                                     "n_cand": len(cand), "n_added": added})
                if added:
                    head = self._recompute_headroom(tasks, labels, h, runner)
                    deficit = sorted(lv for lv, c in head.items() if c < want)
        rep["outside_deficit"] = outside_deficit
        n_before = self.suite.n_tasks
        if len(tasks) != n_before:
            #: ⛔ 重建 suite 而不是就地改：`HeldOutSuite` 是 frozen（内容寻址
            #:    的承诺）。`replace` 保持其它字段不变 ⇒ 不会悄悄丢 env_id。
            import dataclasses as _dc
            self.suite = _dc.replace(
                self.suite, tasks=tuple(tasks),
                difficulty=tuple(sorted(labels.items())))
        rep["grew"] = len(self.suite.tasks) - n_before
        rep["headroom_after"] = dict(self.headroom_of(h, runner))
        rep["n_tasks"] = self.suite.n_tasks
        rep["missing"] = sorted(lv for lv, c in
                                rep["headroom_after"].items() if c < want)
        if not rep["missing"]:
            rep["note"] = "补到每层空间 ≥ %d" % want
        elif rep["outside_deficit"]:
            #: ⛔ 这条分支此前**不存在**：`grew=0` 只有一句「补到上限仍缺」，
            #:    于是"题源没了"与"有题但那层不缺"在输出里完全同形（实测
            #:    F 段：题源里明明有 13 条建卷外的题，其中 3 条正是缺空间
            #:    那层能用的，却因为该层当时不在 deficit 里被拒掉了）。
            rep["note"] = ("仍缺 %s：题源里还有 %d 条建卷外的题，但它们不在"
                           "缺空间的层里 ⇒ 出路是**换采样口径**（同一环境的"
                           "另一种取点顺序会给出几乎不重叠的一批题），"
                           "加大 max_n 无效 —— 单口径题源已到顶"
                           % ("/".join(rep["missing"]),
                              rep["outside_deficit"]))
        else:
            rep["note"] = ("补到上限仍缺：%s（单采样口径的题源已到顶 —— 加大"
                           " max_n / depth 都不再产出新题；这是卷子的上限，"
                           "不是候选的错）" % "/".join(rep["missing"]))
        return rep

    def _recompute_headroom(self, tasks: List[FrozenTask],
                            labels: Dict[str, str], h: Harness,
                            runner: Runner) -> Dict[str, int]:
        """按 (任务集, 标签) 重算每层可提升空间（与 `headroom_of` 同判据）。"""
        out: Dict[str, int] = {}
        for lv in sorted({str(v) for v in labels.values()}):
            n = 0
            for t in tasks:
                if str(labels.get(t.task_id, "")) != lv:
                    continue
                try:
                    v = runner(h, dict(t.spec))
                except Exception:                              # noqa: BLE001
                    continue
                if float(getattr(v, "reward", 0.0) or 0.0) < 1.0:
                    n += 1
            out[lv] = n
        return out

    # ── 判别力兜底：只降级"放行"，不覆盖已有的拦截结论 ──────────────
    def _downgrade_if_flat(self, res: ProbeResult,
                           pass_verdict: str = "generalize") -> ProbeResult:
        """把一次**本来要放行**的裁决，在 held-out 无跨度时降级为拦截。

        ⛔ `pass_verdict` 只影响**文案**：放行有两条路径（能力泛化 / 清理
           安全），它们"本来要判的那个值"不同，但"held-out 没跨度 ⇒ 这个
           放行不可信"这条判据是同一个。⇒ 降级逻辑共用，只有措辞分叉。

        ⛔⛔ 为什么只作用在这一条路径上：
          · `overfit` / `partial` / `no_gain` 都是**有确凿证据**的拦截
            （held-out 没涨、某层挑食、发展集没涨）。判别力不足只会让
            "放行"失去依据，**不会**让这些拦截失效 —— 用 inconclusive
            覆盖它们等于把硬证据换成"我也不知道"，是信息倒退。
          · 反过来，判别力不足时判 `generalize` 是在**下一个没有证据的
            结论**，它与真放行在输出上完全同形（连 `by_difficulty` 都在）。

        ⛔ 为什么 `None`（没打难度标签）不拦：那只是"没测过"，不是
           "测了发现不行"。V5 纪律：不可判别 ≠ 有罪。拦它只会让所有
           未分层的老配置一夜之间变成"永远不提交"。

        ⛔ 这是**兜底**不是出路：正确的处置是先把 held-out 补采样到有
           跨度（`repair.grow_for_difficulty`），本方法只负责"补不出来
           的时候不许假装已验证"。
        """
        if self.suite.discriminative is not False:
            return res
        #: ⛔ 两条路径"无法区分的两件事"不同 ⇒ 文案必须分叉，
        #:    否则清理的放行理由会写成"无法区分真泛化"（答非所问）。
        what = ("「真泛化」与「只会修这一类题」" if pass_verdict == "generalize"
                else "「删掉的确实是死条目」与「删掉的东西只在没测到的"
                     "难度层上才被用到」")
        return ProbeResult(
            verdict="inconclusive", dev_gain=res.dev_gain,
            heldout_gain=res.heldout_gain, base_heldout=res.base_heldout,
            cand_heldout=res.cand_heldout, n_tasks=res.n_tasks,
            by_difficulty=res.by_difficulty,
            dev_by_difficulty=res.dev_by_difficulty,
            out_of_reach=res.out_of_reach, discriminative=False,
            #: ⛔ 重建时**从 res 继承**这三个读数：不继承的话，一次"降级"
            #:    就把 headroom/thin/saturated 全抹成空 ⇒ 观测侧看见的
            #:    inconclusive 与放行分支不同形 ⇒ 又一处"只有放行记得填"。
            headroom=dict(res.headroom), thin=res.thin,
            saturated=res.saturated,
            reason=(f"held-out 有难度标签但只有一层 {sorted(self.suite.levels)}"
                    f" ⇒ 它无法区分{what} ⇒ 本可判 "
                    f"{pass_verdict}（ho {res.heldout_gain:+.4f}），"
                    f"但放行结论不可信"
                    f" ⇒ 按拦截处理。出路：先把 held-out 补采样到有难度跨度"
                    f"（--max-tasks 或 grow_for_difficulty），不要放宽判据"))

    # ── 裁决 ────────────────────────────────────────────────────────
    def probe(self, base_h: Harness, cand_h: Harness, runner: Runner,
              dev_gain: float,
              dev_by_difficulty: Optional[Dict[str, float]] = None
              ) -> ProbeResult:
        """公开入口 = 裁决 + **统一后处理**。真正的判据在 `_probe_inner`。

        ⛔ 判别力三态由这里统一写入，不在各分支里分别赋值：
           它是 **held-out 自身的属性**，不是某个裁决分支的属性。让每个
           分支自己填 ⇒ 只有"放行"分支会记得填，拦截分支全是 `None`
           ⇒ 观测侧会把"拦了但 held-out 本就没判别力"误读成"没测过"
           （实测：`overfit` 裁决上出现过 `discriminative=None`，
            而同一套 held-out 在放行时是 `False`）。
        """
        res = self._probe_inner(base_h, cand_h, runner, dev_gain,
                                dev_by_difficulty)
        if res.discriminative is None:
            res.discriminative = self.suite.discriminative
        return res

    def _probe_inner(self, base_h: Harness, cand_h: Harness, runner: Runner,
                     dev_gain: float,
                     dev_by_difficulty: Optional[Dict[str, float]] = None
                     ) -> ProbeResult:
        """对 (基线, 候选) 做一次泛化裁决。

        ⛔ 判据顺序有讲究：先判"发展集到底涨没涨"，再判"涨的是不是泛化的"。
        反过来的话，"发展集没涨"会被误记成 overfit —— 那是两回事：
        没涨是能力问题，涨了但不泛化是**诚信**问题。

        ⛔⛔ 分层裁决必须拿 `dev_by_difficulty` 做**同层对照**，否则会死锁：
          `hard` 的定义是「通用修法修不好」（见 repair.classify_difficulty），
          于是**任何**通用修法在 hard 层的增益必然是 0 ⇒ 只要判据是
          "某层没涨 ⇒ 拦截"，唯一能泛化的修法就会被唯一能鉴别泛化的闸
          永久拦住 ⇒ 自进化系统再也不会提交任何东西（2026-10-13 实测：
          三轮 × 两组全部被拦，improved=False）。

          同层对照把两件事分开：
            · dev 同层**也**涨不动 ⇒ 该层超出候选能力（超纲），**不处罚**；
            · dev 同层涨了、held-out 同层没涨 ⇒ **挑食**，拦截。
          这不是放宽判据 —— 真挑食的候选仍然被拦，只是不再把"能力不够"
          误判成"不诚实"（V5：不可判别 ≠ 有罪）。

        ⛔ 拿不到对照数据时 fail-closed：仍按挑食处理（与旧行为一致）。
        """
        if self.suite.n_tasks < self.min_tasks:
            return ProbeResult(
                verdict="inconclusive", dev_gain=dev_gain, n_tasks=self.suite.n_tasks,
                reason=f"held-out 任务数 {self.suite.n_tasks} < 最小要求 {self.min_tasks}"
                       f" ⇒ 无法裁决，按拦截处理")

        base_ho, base_vs = self.evaluate(base_h, runner)
        cand_ho, cand_vs = self.evaluate(cand_h, runner)
        ho_gain = cand_ho - base_ho
        by_diff = self._gain_by_difficulty(base_vs, cand_vs)

        # ⛔⛔ r54：**分层诊断提到所有分支之前**，成为公共事实。
        #    此前它只存在于「总体已涨」的那条路径上，`overfit`（总体未涨）
        #    在任何分层逻辑**之前**就早退了 ⇒ 同一个候选只因 ho 总体增益
        #    差 0.01，就一个享受完整同层对照、另一个被零信息判决。
        #    判据在临界点不连续 ⇒ 这次拦得对不对，全看浮点落在哪一侧。
        #: 该层里基线**尚未修好**的题数 = 这一层真正能涨的空间（r52）。
        base_by_id = {tid: v.reward
                      for tid, v in zip(self.suite.task_ids, base_vs)}
        headroom = {
            lv: sum(1 for t in self.suite.ids_of(lv)
                    if base_by_id.get(t, 0.0) < 1.0)
            for lv in by_diff}
        #: ⛔ 与 `headroom_of()` 同判据（`reward < 1.0`），不许各写一份。
        lagging_all = sorted(lv for lv in by_diff
                             if by_diff[lv] <= self.heldout_floor)
        thin = sorted(lv for lv in lagging_all
                      if headroom.get(lv, 0) < self.min_level_headroom)
        all_thin = bool(by_diff) and all(
            headroom.get(lv, 0) < self.min_level_headroom for lv in by_diff)
        #: ⛔ r54：**完全饱和层**（该层基线已全部修好 ⇒ 任何变化都测不出来）。
        #:    与 `thin`（样本不足，统计上不可判别）是**两件事**：
        #:      · thin     = 还有几条能涨，但太少 ⇒ 涨没涨都说不清；
        #:      · saturated= 一条都涨不了 ⇒ 这一层的读数**必然**是 0。
        #:    它们在裁决里的处置相同（不作证也不作反证、必须点名），
        #:    但出路不同：thin 要补题，saturated 要换更难的题（或换环境）。
        saturated = sorted(lv for lv in lagging_all
                           if headroom.get(lv, 0) == 0)

        # ⛔ 与 heldout_floor 同一个口径：**等于地板也算"没涨"**。
        #    两侧口径不一致会让"dev 涨 0"和"ho 涨 0"得到相反的对待。
        if dev_gain <= self.dev_floor:
            return ProbeResult(verdict="no_gain", dev_gain=dev_gain,
                               heldout_gain=ho_gain, base_heldout=base_ho,
                               cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                               reason=f"发展集增益 {dev_gain:.4f} 未超过地板 {self.dev_floor}"
                                      f" ⇒ 不是本闸职责（进化循环自会否决）")
        # ⛔ held-out 已在天花板 ⇒ **没有判别力**，判 inconclusive（拦截）。
        #    不这么处理的话，base 已经满分时任何候选的增益都是 0，
        #    会被误判成 overfit —— 那是把"无从判别"当成"有罪"。
        if self.base_at_ceiling is not None and base_ho >= self.base_at_ceiling - 1e-9:
            return ProbeResult(
                verdict="inconclusive", dev_gain=dev_gain, heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks,
                reason=f"held-out 基线已在天花板 {base_ho:.2f} ⇒ 无从判别是否泛化"
                       f"（按拦截处理，需换一批更难的任务）")
        # ⛔ 用 `<=`：等于地板也算"没跟着涨"。用 `<` 的话 0 增益会被判成
        #    泛化 —— 那正好放过最典型的一种 reward hacking（dev 涨、held-out 纹丝不动）。
        if ho_gain <= self.heldout_floor:
            #: ⛔⛔ r54：这里原先**带走了 `by_diff` 却丢掉了 `headroom`
            #:    / `thin` / `dev_by_difficulty`** —— 而进化循环是把
            #:    `dev_by_difficulty` 一路传进来的（evolution.py 的
            #:    `probe(dev_by_difficulty=cand.dev_by_difficulty)`）。
            #:    结果：观测侧看到的 overfit 裁决里 `headroom={}`、
            #:    `dev={}`（实测轮 5），于是"这一轮闸判得对不对"**根本
            #:    无从复核** —— 而 partial 分支三个字段全在。
            #:    ⛔ 这不是"少记了几个字段"：判据用到的量必须进 `to_dict`
            #:    （第 35d 条），只活在 reason 文案里的量，改一次措辞
            #:    就让所有下游同时失真。
            return ProbeResult(
                verdict="overfit", dev_gain=dev_gain, heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks, by_difficulty=by_diff,
                dev_by_difficulty=dict(dev_by_difficulty or {}),
                headroom=dict(headroom), thin=tuple(thin),
                saturated=tuple(saturated),
                reason=(f"发展集 +{dev_gain:.4f} 但 held-out {ho_gain:+.4f} "
                        f"（{base_ho:.2f}→{cand_ho:.2f}）⇒ 讨好判据，不提交"))
        # ⛔ 分层裁决：总体涨了，但某一难度层没涨 ⇒ **不能直接判挑食**，
        #    必须与发展集的同层增益对照（理由见 docstring 的"自指死锁"）。
        #: ⛔ 上面的 `headroom` / `lagging_all` / `thin` / `all_thin` 已在
        #:    **分支之前**算好（r54），这里**不再各算一份** —— 两处同形却
        #:    互不相干 ⇒ 改一处必漏一处，而漏的那处不报错，只是某个分支的
        #:    裁决悄悄失去依据（第 13 条：判据名单 build 与 validate 同源）。
        #:    ⛔ 未涨的层里，样本不足（`thin`）的**不作反证**，但要在裁决里
        #:    **点名**（不作证）。已经涨了的层不需要这层豁免。
        #:    ⛔ 与 `undecidable`（发展集没这一层）分开：处置相同，但出路
        #:       完全不同 —— 这条要去补 held-out 该层的**题数**，不是补层。
        lagging = [lv for lv in lagging_all if lv not in thin]
        if lagging or thin:
            out_of_reach: List[str] = []
            cherry: List[str] = []
            undecidable: List[str] = []
            for lv in lagging:
                d_gain = (dev_by_difficulty or {}).get(lv)
                if d_gain is None:
                    # ⛔ 发展集**没有这一层** ⇒ 无从对照。这**不是**挑食的证据，
                    #    是"不可判别"（V5：不可判别 ≠ 有罪）⇒ 归入 undecidable，
                    #    最终判 inconclusive（**同样拦截**，但理由不同：
                    #    看到它你就知道该去补发展集的该难度层任务，而不是改候选）。
                    undecidable.append(lv)
                elif d_gain <= self.dev_floor:
                    out_of_reach.append(lv)      # 发展集同层也修不好 ⇒ 超纲
                else:
                    cherry.append(lv)            # 发展集能修、换环境就修不了 ⇒ 挑食
            if cherry:
                shown = sorted(f"{lv}{by_diff[lv]:+.4f}" for lv in cherry)
                return ProbeResult(
                    verdict="partial", dev_gain=dev_gain, heldout_gain=ho_gain,
                    base_heldout=base_ho, cand_heldout=cand_ho,
                    n_tasks=self.suite.n_tasks, by_difficulty=by_diff,
                    dev_by_difficulty=dict(dev_by_difficulty or {}),
                    out_of_reach=tuple(out_of_reach),
                    headroom=dict(headroom), thin=tuple(thin),
                    saturated=tuple(saturated),
                    reason=(f"总体 held-out {ho_gain:+.4f}，但 {shown} 未涨，"
                            f"且发展集同层有增益（{ {lv: round((dev_by_difficulty or {}).get(lv, 0.0), 4) for lv in cherry} }"
                            f"）⇒ 只在部分难度上泛化（挑食），不提交"))
            if undecidable and not out_of_reach:
                # ⛔ 无从判别 ⇒ **拦截**（与安全相关的未知一律 fail-closed），
                #    但裁决是 inconclusive 而不是 partial：前者说"缺数据"，
                #    后者说"有挑食证据"。两者处置完全不同，不能混。
                return ProbeResult(
                    verdict="inconclusive", dev_gain=dev_gain,
                    heldout_gain=ho_gain, base_heldout=base_ho,
                    cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                    by_difficulty=by_diff,
                    dev_by_difficulty=dict(dev_by_difficulty or {}),
                    out_of_reach=(), headroom=dict(headroom), thin=tuple(thin),
                    saturated=tuple(saturated),
                    reason=(f"held-out 的 {undecidable} 层未涨，但发展集**没有"
                            f"该难度层** ⇒ 无从对照，无法区分「能力边界」与"
                            f"「挑食」⇒ 按拦截处理（补该难度的 dev 任务后再判）"))
            if all_thin:
                #: ⛔ 每一层都只剩零星空地 ⇒ 这套 held-out 对这道题**整体**
                #:    无判别力 ⇒ fail-closed（与 `discriminative=False` 同形态，
                #:    但那是"没有难度标签"，这是"有标签但每层的样本都不够"）。
                shown = sorted(f"{lv}(可提升空间 {headroom.get(lv, 0)} 条)"
                               for lv in thin)
                return ProbeResult(
                    verdict="inconclusive", dev_gain=dev_gain,
                    heldout_gain=ho_gain, base_heldout=base_ho,
                    cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                    by_difficulty=by_diff,
                    dev_by_difficulty=dict(dev_by_difficulty or {}),
                    headroom=dict(headroom), thin=tuple(thin),
                    saturated=tuple(saturated),
                    reason=(f"held-out **每层**的可提升空间都不足 "
                            f"{self.min_level_headroom} 条（{shown}）⇒ 这次"
                            f"裁决没有任何一层的证据可用 ⇒ 按拦截处理。"
                            f"出路：补 held-out 的题数，不是放宽判据"
                            + ("（开了 --ho-grow 则由闸自己补，仍补不齐才会到这一步）"
                               if self.grow_fn is not None else
                               "（可开 --ho-grow 让闸自己补采样）")))
            # 未涨的层全是"发展集也修不动" ⇒ 能力边界，不是挑食 ⇒ 放行
            shown = sorted(f"{lv}{by_diff[lv]:+.4f}" for lv in out_of_reach)
            thin_note = ""
            if thin:
                #: ⛔⛔ 样本不足的层**必须点名**：它没进 lagging（不作反证），
                #:    但不点名就成了"悄悄放宽判据"（第 13 条：闸自己要在
                #:    名单里）。出路也不同：这条要补题，不是补层。
                thin_note = ("".join(
                    f" {lv} 层未涨但该层只有 {headroom.get(lv, 0)} 条可提升"
                    f"题 ⇒ 样本不足，未计入裁决（要补题，不是放宽判据）；"
                    for lv in thin))
            return self._downgrade_if_flat(ProbeResult(
                               verdict="generalize", dev_gain=dev_gain,
                               heldout_gain=ho_gain, base_heldout=base_ho,
                               cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                               by_difficulty=by_diff,
                               dev_by_difficulty=dict(dev_by_difficulty or {}),
                               out_of_reach=tuple(out_of_reach),
                               headroom=dict(headroom), thin=tuple(thin),
                               saturated=tuple(saturated),
                               reason=(f"发展集 +{dev_gain:.4f} 且 held-out {ho_gain:+.4f}"
                                       f"（分层 {by_diff}）⇒ 泛化，放行。"
                                       f" {shown} 未涨但发展集同层同样未涨"
                                       f" ⇒ 判为能力边界而非挑食。" + thin_note)))
        return self._downgrade_if_flat(ProbeResult(
                           verdict="generalize", dev_gain=dev_gain,
                           heldout_gain=ho_gain, base_heldout=base_ho,
                           cand_heldout=cand_ho,                            n_tasks=self.suite.n_tasks,
                           by_difficulty=by_diff,
                           dev_by_difficulty=dict(dev_by_difficulty or {}),
                           headroom=dict(headroom), thin=tuple(thin),
                           saturated=tuple(saturated),
                           reason=f"发展集 +{dev_gain:.4f} 且 held-out {ho_gain:+.4f}"
                                  + (f"（分层 {by_diff}）" if by_diff else "")
                                  + " ⇒ 泛化，放行"))


    # ── 清理候选：held-out **不许变差**（不要求涨）────────────────────
    def probe_removal(self, base_h: Harness, cand_h: Harness,
                      runner: Runner) -> ProbeResult:
        """清理候选的泛化裁决：**删完之后 held-out 不许变差**。

        ⛔⛔ 为什么不走 `probe()`：`probe()` 的第一道判据是
            `dev_gain <= dev_floor ⇒ no_gain`（`_probe_inner` 第 319 行）。
            而清理的 dev_gain **恒为 0** —— 删掉一个死条目不改变 reward，
            这正是它"看起来无用"的原因 ⇒ 走 `probe()` 必得 `no_gain`
            ⇒ `blocks=True` ⇒ **清理在结构上永远无法提交**。
            那不是"闸在工作"，是**用错了闸**：`overfit` 的定义是
            "发展集涨了但 held-out 没跟着涨"，而清理根本没涨，
            谈不上"讨好判据"。

        ⛔ 所以对清理，held-out 的职责从**「要涨」换成「不许掉」**：
            删掉一个"看起来没人用、其实在 held-out 那个环境里真被用到"
            的条目 ⇒ held-out reward 会掉 ⇒ 这里会响。
            ⇒ 这不是放松判据，是**换了职责**：它防的是"删错了"，
            不是"涨得假"。两者共用同一张 held-out，不是一个旁路。

        ⛔ 与 `probe()` 共用：同一套 held-out、同一个 `min_tasks`、
           同一条判别力降级（held-out 只有一层难度 ⇒ 放行不可信）
           ⇒ 是同一张表的另一种读法，不是第二张表。
        """
        if self.suite.n_tasks < self.min_tasks:
            return ProbeResult(
                verdict="inconclusive", n_tasks=self.suite.n_tasks,
                reason=f"held-out 任务数 {self.suite.n_tasks} < 最小要求 "
                       f"{self.min_tasks} ⇒ 无法裁决，按拦截处理")
        base_ho, base_vs = self.evaluate(base_h, runner)
        cand_ho, cand_vs = self.evaluate(cand_h, runner)
        ho_gain = cand_ho - base_ho
        #: ⛔ 容差：浮点求和得到的 -1e-17 不是"变差"。
        if ho_gain < -1e-9:
            return ProbeResult(
                verdict="removal_regress", heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks,
                by_difficulty=self._gain_by_difficulty(base_vs, cand_vs),
                discriminative=self.suite.discriminative,
                reason=(f"清理后 held-out {ho_gain:+.4f}"
                        f"（{base_ho:.2f}→{cand_ho:.2f}）⇒ 删掉的条目在 "
                        f"held-out 环境里是真被用到的 ⇒ 不提交"))
        res = ProbeResult(
            verdict="removal_safe", heldout_gain=ho_gain,
            base_heldout=base_ho, cand_heldout=cand_ho,
            n_tasks=self.suite.n_tasks,
            by_difficulty=self._gain_by_difficulty(base_vs, cand_vs),
            reason=(f"清理后 held-out {ho_gain:+.4f}（{base_ho:.2f}→"
                    f"{cand_ho:.2f}）⇒ 没变差 ⇒ 放行（减负，不要求增益）"))
        if res.discriminative is None:
            res.discriminative = self.suite.discriminative
        return self._downgrade_if_flat(res, pass_verdict="removal_safe")


def make_heldout(env_id: str, tasks: List[FrozenTask], name: str = "",
                 difficulty: Optional[Dict[str, str]] = None) -> HeldOutSuite:
    """便捷构造：给一组冻结任务打上 env_id（可选带上难度标签）。"""
    return HeldOutSuite(
        name=name or f"heldout::{env_id}", tasks=tuple(tasks), env_id=env_id,
        difficulty=tuple(sorted((difficulty or {}).items())))

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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

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
                "reason": self.reason}


class GeneralizationGate:
    """跨环境泛化闸。**只在 best 选出之后跑一次**。"""

    def __init__(self, suite: HeldOutSuite,
                 dev_task_ids: Optional[Iterable[str]] = None,
                 heldout_floor: float = 0.0,
                 dev_floor: float = 0.0,
                 min_tasks: int = 1,
                 base_at_ceiling: Optional[float] = None):
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
            return ProbeResult(
                verdict="overfit", dev_gain=dev_gain, heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks, by_difficulty=by_diff,
                reason=(f"发展集 +{dev_gain:.4f} 但 held-out {ho_gain:+.4f} "
                        f"（{base_ho:.2f}→{cand_ho:.2f}）⇒ 讨好判据，不提交"))
        # ⛔ 分层裁决：总体涨了，但某一难度层没涨 ⇒ **不能直接判挑食**，
        #    必须与发展集的同层增益对照（理由见 docstring 的"自指死锁"）。
        lagging = sorted(lv for lv in by_diff if by_diff[lv] <= self.heldout_floor)
        if lagging:
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
                    out_of_reach=(),
                    reason=(f"held-out 的 {undecidable} 层未涨，但发展集**没有"
                            f"该难度层** ⇒ 无从对照，无法区分「能力边界」与"
                            f"「挑食」⇒ 按拦截处理（补该难度的 dev 任务后再判）"))
            # 未涨的层全是"发展集也修不动" ⇒ 能力边界，不是挑食 ⇒ 放行
            shown = sorted(f"{lv}{by_diff[lv]:+.4f}" for lv in out_of_reach)
            return self._downgrade_if_flat(ProbeResult(
                               verdict="generalize", dev_gain=dev_gain,
                               heldout_gain=ho_gain, base_heldout=base_ho,
                               cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                               by_difficulty=by_diff,
                               dev_by_difficulty=dict(dev_by_difficulty or {}),
                               out_of_reach=tuple(out_of_reach),
                               reason=(f"发展集 +{dev_gain:.4f} 且 held-out {ho_gain:+.4f}"
                                       f"（分层 {by_diff}）⇒ 泛化，放行。"
                                       f" {shown} 未涨但发展集同层同样未涨"
                                       f" ⇒ 判为能力边界而非挑食")))
        return self._downgrade_if_flat(ProbeResult(
                           verdict="generalize", dev_gain=dev_gain,
                           heldout_gain=ho_gain, base_heldout=base_ho,
                           cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                           by_difficulty=by_diff,
                           dev_by_difficulty=dict(dev_by_difficulty or {}),
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

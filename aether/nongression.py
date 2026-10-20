"""冻结非回归闸（Deterministic Non-Regression Gate）。

2026 的 self-harness 综述把**灾难性遗忘 / 回归**列为自进化系统的头号
失败模式，而论文给出的缓解手段正是 "deterministic non-regression gates"
——确定性的、不受模型波动影响的非回归闸门。

这一条恰好是 V5 最硬的资产（红契约 + 冻结基线 + 三轮对照 + 零新红），
V6 把它从"工程师的纪律"升级成"系统的准入条件"：

    **任何 harness 变体，必须先过冻结闸，才允许被评估"有没有变好"。**

闸门语义（三条，缺一不可）：
  G1 冻结套件全绿：候选在冻结任务集上不得让任何一条**原本通过**的任务变红；
  G2 口径一致：评估口径（caliber）没变才能比；口径变了必须 supersede 留痕，
     并**禁止**与旧口径同表对比（V5：`wait_terminal` ON/OFF 两口径不可比）；
  G3 判定同源：闸用的是 verifier 自己的 Verdict.reward，不是这里另写一套
     （V5 实测：重写判据差 3.8pt，与效应同量级）。

⛔ 闸门只对"冻结套件"负责，不对"目标集"负责：
   先证明没变坏（闸），再证明变好了（进化选择）。两个方向不能混在一张表里。
"""
from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from math import comb
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional, Tuple

from .genome import Harness
from .ledger import content_hash
from .verifier import (SelfReportDetected, Verdict, assert_verdict_sane,
                       env_fingerprint, error_verdict)

Runner = Callable[[Harness, Dict[str, Any]], Verdict]

#: 寻址指纹前缀长度（hex 字符数）。
#: ⛔ 与 `genome._ID_PREFIX` 同值 —— 保持跨模块一致。用 `[:16]`（64 位）
#:    在 append-only、永不删的账本上长期会碰撞；`FrozenTask.fingerprint`
#:    和 `Baseline.baseline_id` 都是**寻址键**（前者校验"同一 task_id 的
#:    spec 没变"，后者是 supersede 前后的对照锚点）。
_ID_PREFIX = 32

#: 回归消息里 error 的截断长度。
_ERR_TAIL_CHARS = 120


def _freeze_mapping(value: Any, field_name: str) -> MappingProxyType:
    """把一个 Mapping 转成浅冻结的 MappingProxyType。

    ⛔ 用于 `frozen=True` dataclass 的 dict 字段：`frozen=True` 只冻结
        **字段赋值**，字段内部的 dict 仍可被原地改 ⇒ 打破"冻结基线"承诺。
        MappingProxyType 挡顶层修改（"嵌套 dict 仍可变"是已知边界——
        递归深冻结代价高，且需要与反序列化路径协同）。
    """
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError(
            f"{field_name} 必须是 Mapping，实得 {type(value).__name__}")
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class FrozenTask:
    """冻结任务：spec 是可执行判据所需的输入，**不含期望答案**。

    ⛔ spec 里不许塞 expected_reward —— 那会让闸变成"比对数字"，
    而数字是被测对象自己报的 ⇒ 立刻退化成自述即奖励。
    """
    task_id: str
    spec: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError(
                f"FrozenTask.task_id 必须是非空字符串，实得 {self.task_id!r}")
        #: 🟠 修复三：`frozen=True` 只冻结字段赋值 —— 内部 dict 仍可原地改。
        #:    浅冻结顶层（见 `_freeze_mapping` 的边界说明）。
        object.__setattr__(self, "spec",
                           _freeze_mapping(self.spec, "FrozenTask.spec"))

    @property
    def fingerprint(self) -> str:
        #: ⛔ `content_hash` → `ledger._norm` 用 `isinstance(obj, dict)` 判，
        #:    MappingProxyType **不是** dict 子类 ⇒ 必须先 `dict()` 展开。
        return content_hash({"task_id": self.task_id,
                             "spec": dict(self.spec)})[:_ID_PREFIX]


@dataclass(frozen=True)
class Baseline:
    """冻结基线：某口径下、某 harness 在冻结套件上的判定集合。"""
    caliber: Dict[str, Any]                  # 口径签名（seed/模型/超时/…）
    verdicts: Dict[str, Verdict]             # task_id → Verdict
    harness_id: str = ""
    superseded: bool = False
    supersede_reason: str = ""

    def __post_init__(self) -> None:
        #: 🟠 修复三：`verdicts` 是**冻结基线**的核心，必须挡住顶层原地改。
        #:    否则 `baseline.verdicts["t"] = other` 会静默改掉"没变坏"的
        #:    对照锚点，supersede 前后对比彻底失效。
        object.__setattr__(self, "verdicts",
                           _freeze_mapping(self.verdicts, "Baseline.verdicts"))
        object.__setattr__(self, "caliber",
                           _freeze_mapping(self.caliber, "Baseline.caliber"))
        #: 🟡 修复十一（附）：caliber 为空 ⇒ 这轮实验**没有钉住任何维度**。
        #:    ⛔ 措辞必须与 `check_caliber` 的实际行为一致（r33 修正）：
        #:       原警告断言"口径闸将恒通过"是**过时的** —— 同轮已把口径闸
        #:       改成双向（见 `check_caliber`），baseline 空而候选非空时会
        #:       因 `extra` 字段被**直接拒绝**。真正剩下的风险是：两侧都空
        #:       时闸确实放行，于是"换了模型 / 换了种子"这类改动**无法被
        #:       察觉**（根本没有维度可比）。警告要对准这个真实风险，
        #:       而不是对准一个已经被同轮改动推翻的行为。
        if not self.caliber and not self.superseded:
            warnings.warn(
                f"Baseline.caliber 为空 ⇒ 这轮实验没有声明任何可比维度"
                f"（seed / timeout / model …）。声明了维度的候选会被"
                f"`check_caliber` 以『多出未声明字段』拒绝；而两侧都空时，"
                f"换模型、换种子这类改动**无法被察觉**。通常是漏配。",
                UserWarning, stacklevel=2)

    @property
    def baseline_id(self) -> str:
        #: ⛔ 与 `FrozenTask.fingerprint` 同款：MappingProxyType 需要先展开。
        return content_hash({
            "caliber": dict(self.caliber),
            "verdicts": {k: v.to_dict()
                         for k, v in sorted(self.verdicts.items())},
        })[:_ID_PREFIX]

    def passed_ids(self) -> List[str]:
        return sorted(k for k, v in self.verdicts.items() if v.reward > 0)

    def failed_ids(self) -> List[str]:
        return sorted(k for k, v in self.verdicts.items()
                      if v.reward <= 0 and not v.error)

    def errored_ids(self) -> List[str]:
        return sorted(k for k, v in self.verdicts.items() if v.error)


@dataclass
class GateResult:
    """闸门裁决。**结构化**：不只是 bool，还要能说清哪条退了、怎么退的。

    ⛔ `passed` 是 **property**（从 regressions/errors/caliber_ok 派生），
        不是字段。原版是字段 ⇒ 调用方能在 `evaluate()` 返回后 append 到
        `errors`，得到 `passed=True` 但 `errors=非空` 的自相矛盾状态。
        派生后 `passed` 永远与列表一致。
    """
    regressions: List[str] = field(default_factory=list)
    caliber_ok: bool = True
    caliber_reason: str = ""
    deltas: Dict[str, float] = field(default_factory=dict)
    verdicts: Dict[str, Verdict] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    #: ⛔ 与 errors 分开：**不拦**但要说清这次裁决的判别力（例如基线全 0）
    warnings: List[str] = field(default_factory=list)
    #: ⛔ 本次裁决**真正参与回归判定**的题数（= 基线通过集大小）。
    #:    它 <= n_tasks：其余题仍会执行（崩溃要可见），但判定用不上。
    n_judged: int = 0
    #: ⛔⛔ **只读观测，不是判据**（r60 F）：基线里**没通过**、而候选通过了
    #:    的题数。它回答的是"冻结集上有没有真的变强"，而不是"有没有变坏"。
    #:
    #:    为什么必须单列：`deltas` 的定义域是「基线通过的题」（见 evaluate
    #:    里 `if base.reward <= 0: continue`）⇒ 每条 delta 的取值只可能是
    #:    0 或负数 ⇒ **在数学上永远不可能为正**。实测 314 次 `gate.result`
    #:    里 313 次 frozen deltas 全 0、唯一非零是 -1 —— 那不是"没有泛化"，
    #:    是"增益不在读数定义域里"（第 24 条：恒为 0 先怀疑量具）。
    #:
    #:    ⛔⛔ 一旦把它接进 `passed`，就等于让系统去优化冻结套件 ⇒ 尺子被
    #:        污染，宪法第一条（判据必须离线可回归）当场失效。它只许流向
    #:        「给人看的读数」，永远不许流向裁决。
    frozen_gain: int = 0
    frozen_gain_detail: List[str] = field(default_factory=list)
    #: 分母：基线里没通过的题数。`frozen_gain=0` 与「分母为 0」必须能分辨
    #: （第 51 条：`gain=0` 与「从没被测过」同形）。
    frozen_open: int = 0

    @property
    def passed(self) -> bool:
        """闸门裁决 = 口径一致 ∧ 无回归 ∧ 无执行错误。

        ⛔ 从列表派生，绝不与 regressions/errors 矛盾。
        """
        return self.caliber_ok and not self.regressions and not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "regressions": list(self.regressions),
            "caliber_ok": self.caliber_ok,
            "caliber_reason": self.caliber_reason,
            "deltas": dict(self.deltas),
            "n_tasks": len(self.verdicts),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "n_judged": self.n_judged,
            #: ⛔ 第 35d 条：判据/读数用到的量必须进 `to_dict`，否则账本里
            #:    看不见 ⇒ 事后只能靠重跑复现（而重跑不是同一个世界）。
            "frozen_gain": self.frozen_gain,
            "frozen_gain_detail": list(self.frozen_gain_detail),
            "frozen_open": self.frozen_open,
            #: ⛔ `verdicts` 有意不导出 —— 逐条 Verdict 已在 evaluate 的
            #:    返回值里，`to_dict` 是给账本/日志的**摘要**，不是数据备份。
            #:    需要完整判定的消费者直接读 `result.verdicts`。
        }


class NonRegressionGate:
    """非回归闸。

    ⛔ 设计取舍：**不做**"允许少量回归换取大幅增益"的权衡开关。
    V5 的交叉表经验表明，一旦允许 trade-off，回归会被系统性低估
    （失败项会静默累积，直到某天"零新红"这个判据本身失效）。
    想要 trade-off ⇒ 显式 supersede 基线并留痕，走人工闸门。
    """

    def __init__(self, suite: List[FrozenTask], baseline: Baseline):
        self.suite = suite
        self.baseline = baseline

    # ── 口径闸（G2）───────────────────────────────────────────────
    def check_caliber(self, caliber: Dict[str, Any]) -> Tuple[bool, str]:
        """口径必须与基线一致，否则禁止对比。

        ⛔ 这是 fail-closed：口径对不上就判"不可比"，不是"大致可比"。

        ⛔⛔ 双向比较：不仅要检查"基线里的键都在候选里且相等"，也要检查
            候选里没有**基线未声明的多余键**。原版单向 ⇒ 调用方从
            `{"seed":0,"timeout":10}` 变成 `{"seed":0,"timeout":10,
            "model":"qwen3"}` 会被判"同口径" ⇒ 判据**已变**（换了模型）
            却与旧基线对比 ⇒ G2 要防的事原样复活。
        """
        if self.baseline.superseded:
            return False, (f"基线已被 supersede（{self.baseline.supersede_reason}），"
                           f"禁止再作为非回归基准")
        missing = [k for k in self.baseline.caliber if k not in caliber]
        if missing:
            return False, f"口径缺字段 {sorted(missing)}"
        extra = [k for k in caliber if k not in self.baseline.caliber]
        if extra:
            #: ⛔ 多出未声明字段 ⇒ **不能证明**同口径。有些字段（trace_id）
            #:    对判定无影响，但"我不知道它有没有影响"不是"它没影响"。
            #:    宁可让调用方显式加进 baseline.caliber 或 supersede。
            return False, (f"口径多出未声明字段 {sorted(extra)} ⇒ 无法证明"
                           f"与基线同口径（若确认无影响，请显式加进 "
                           f"baseline.caliber）")
        diff = {k: (self.baseline.caliber[k], caliber[k])
                for k in self.baseline.caliber
                if caliber[k] != self.baseline.caliber[k]}
        if diff:
            return False, f"口径不一致 {diff} ⇒ 两口径不可比，需先 supersede"
        return True, ""

    # ── 主闸 ──────────────────────────────────────────────────────
    def evaluate(self, harness: Harness, runner: Runner,
                 caliber: Dict[str, Any]) -> GateResult:
        """跑冻结套件并裁决。

        ⛔ 单条任务崩了 ⇒ 整闸失败（fail-closed）。
        "跑挂了"不等于"没回归"，把它当通过是自进化系统最危险的默认。
        """
        ok, why = self.check_caliber(caliber)
        if not ok:
            return GateResult(caliber_ok=False, caliber_reason=why)

        # ⛔⛔ 2026-10-13 实测：套件为空时，regressions 与 errors 都是空 ⇒
        #     `passed = (not regressions) and (not errors)` = **True**。
        #     准入闸在自己最该响的时候恒绿 —— 任何变体都能"证明没变坏"。
        #     这是最危险的假绿灯形态：不是读数假，是**闸本身**假。
        #     ⇒ 空套件 = 无从判别 = fail-closed 拦下（与 caliber 不可比同处理）。
        if not self.suite:
            return GateResult(
                caliber_ok=True,
                errors=["empty_frozen_suite: 冻结套件为空 ⇒ 无从证明"
                        "「没变坏」（fail-closed：没被证明 ⇒ 不放行）"])

        #: 🟠 修复二：**suite 与 baseline.verdicts 键必须一致** ——
        #:    不一致时"没变坏"根本无法被证明：
        #:      · suite 少了某个 baseline 里的 task ⇒ 该 task 的判定"缺失"，
        #:        被记成回归（措辞误导，读起来像 runner 挂了）；
        #:      · suite 多了某个 baseline 没有的 task ⇒ 该 task 的判定被执行
        #:        但**从不参与回归判定**（通过/失败静默丢弃）。
        #:    这与"空套件 fail-closed"是同一条纪律：判据集必须自洽。
        suite_ids = {t.task_id for t in self.suite}
        base_ids = set(self.baseline.verdicts.keys())
        if suite_ids != base_ids:
            only_suite = sorted(suite_ids - base_ids)
            only_base = sorted(base_ids - suite_ids)
            return GateResult(
                caliber_ok=True,
                errors=[f"suite_mismatch: 冻结套件与基线判据集不一致 "
                        f"（仅在 suite: {only_suite[:5]}；"
                        f"仅在 baseline: {only_base[:5]}）⇒ "
                        f"「没变坏」无法被证明（fail-closed）"])

        #: ⛔⛔ 但"基线里没有一条通过"**不能**同样拦死 —— 那会自指死锁：
        #:    系统还没能力 ⇒ 基线全 0 ⇒ 任何候选都被拦 ⇒ 永远无法进化。
        #:    它与"套件为空"是两件事：套件非空时闸**跑得起来**（崩溃会被
        #:    errors 抓到），只是"没有可回归的东西"。所以判据照跑，只**标注**
        #:    这次裁决的判别力为零（同层对照里的「能力边界 vs 挑食」同构）。
        no_passing = not any(v.reward > 0
                             for v in self.baseline.verdicts.values())

        verdicts: Dict[str, Verdict] = {}
        errors: List[str] = []
        #: ⛔⛔ **不许跳过"基线没通过"的题**（2026-10-17 实测打回）：
        #:    它的结果确实不参与回归判定（下面 `if base.reward <= 0:
        #:    continue`），看着像纯浪费，于是我做过一版"跳过不执行"的优化
        #:    —— `contract_nongression_test` 的 C2 / G5 **立刻转红**：
        #:      · C2 崩溃被记账 3 条 ⇒ 跳过后只剩 2 条（t3 基线红被跳过）
        #:      · G5 基线全 0 时 runner 全崩 ⇒ 跳过后一条都不跑 ⇒
        #:        `errors` 为空 ⇒ **闸放行** —— 这正是"跑挂了 ≠ 没回归"
        #:        这条 fail-closed 纪律要挡的事，被优化悄悄开了口子。
        #:    ⇒ 执行的意义不止"产生 delta"：**崩溃必须可见**。省成本只能
        #:    省在**套件大小**上（见 `cli.FROZEN_BROKEN_PER_ENV`），
        #:    不能省在"这条题要不要跑"上。
        for t in self.suite:
            #: ⛔ 捕获 SelfReportDetected 单独一类：它不是"runner 崩了"，
            #:    而是"runner 返回的 Verdict 里混了自述键" —— 归因不同。
            try:
                raw = runner(harness, dict(t.spec))
                #: 🔴 修复一：**闸不信任 runner** —— 它是外部注入的。
                #:    `assert_verdict_sane` 会把"没有 check" / "evidence
                #:    含自述键" / "checks 缺 evidence" 的 verdict 判为
                #:    invalid（reward 归 0 并留痕）。绕过这一步，一个
                #:    `Verdict(passed=True, checks=())` 就能让任何任务"通过"
                #:    —— 闸变成假绿灯（而文件开头恰恰承诺"判定同源 G3"）。
                verdicts[t.task_id] = assert_verdict_sane(raw)
            except SelfReportDetected as e:
                v = error_verdict(f"gate/{t.task_id}/self_report", e)
                verdicts[t.task_id] = v
                errors.append(f"{t.task_id}: self_report: {e}")
            except Exception as e:  # noqa: BLE001 —— 记账，不吞
                #: 🟡 修复七：崩溃的 verdict 也要带环境指纹 —— 否则账本里
                #:    所有 gate 崩溃长得一模一样，无法按环境/任务归因。
                try:
                    env_fp = env_fingerprint(
                        {"task_id": t.task_id, "spec": dict(t.spec)})
                except Exception:
                    env_fp = ""
                v = error_verdict(f"gate/{t.task_id}", e,
                                  env_fingerprint_str=env_fp)
                verdicts[t.task_id] = v
                errors.append(f"{t.task_id}: {v.error}")

        regressions: List[str] = []
        deltas: Dict[str, float] = {}
        for tid, base in self.baseline.verdicts.items():
            if base.reward <= 0:
                continue                       # 本来就没过 ⇒ 不参与回归判定
            cur = verdicts.get(tid)
            if cur is None:
                #: 到这里其实**不应该**发生（上面 suite_ids == base_ids
                #: 已校验）。留作防御性编程 —— 未来若引入 suite 动态变更
                #: 的路径，这里能兜住。
                regressions.append(f"{tid}: 缺失判定")
                continue
            deltas[tid] = cur.reward - base.reward
            if cur.reward <= 0:
                #: 🟡 修复八：截断 error —— 它可能是一整个 traceback。
                err = cur.error or ""
                if len(err) > _ERR_TAIL_CHARS:
                    err = err[:_ERR_TAIL_CHARS] + "…"
                regressions.append(
                    f"{tid}: {base.reward:.0f}→{cur.reward:.0f}"
                    + (f" ({err})" if err else ""))

        #: ⛔ 判别力**上限**必须写在读数里：基线没通过的题不参与回归判定
        #:    （它们仍会执行 —— 崩溃要可见 —— 但判定用不上）。不写这条，
        #:    "套件 12 条"看起来就有 12 条判别力，实际可能只有 4 条。
        n_judged = sum(1 for v in self.baseline.verdicts.values()
                       if v.reward > 0)
        #: ── 只读增益读数（r60 F）：**不进闸** ───────────────────────
        #: 基线没通过、而候选通过了 ⇒ 这一条就是"冻结集上真的变强了"。
        #: 它与 `regressions` 是同一份 `verdicts` 的两个方向：
        #:   · 基线过 → 候选挂 = 回归（进闸，会拦）
        #:   · 基线挂 → 候选过 = 增益（只记，不拦，也不许被优化）
        #: ⛔ 别把这两件事合成一个 `score = pass_after - pass_before`：
        #:    那个数会被"用回归换增益"讨好，而本闸明确不接受 trade-off。
        frozen_gain = 0
        frozen_gain_detail: List[str] = []
        frozen_open = 0
        for tid, base in self.baseline.verdicts.items():
            if base.reward > 0:
                continue
            frozen_open += 1
            cur = verdicts.get(tid)
            if cur is not None and cur.reward > 0:
                frozen_gain += 1
                frozen_gain_detail.append(f"{tid}: 0→{cur.reward:.0f}")
        warns: List[str] = []
        if no_passing:
            warns.append("baseline_all_red: 基线里没有一条原本通过的任务 ⇒"
                         " 本次闸**无回归可判**（不等于『已证明无回归』）")
        elif n_judged < len(self.suite):
            warns.append(
                f"partial_discrimination: {len(self.suite)} 条冻结题里只有 "
                f"{n_judged} 条在基线上通过 ⇒ 本次判别力上限 = {n_judged} "
                f"（其余仍在执行，崩溃照样拦，但不参与回归判定）")
        return GateResult(
            regressions=regressions,
            caliber_ok=True,
            deltas=deltas,
            verdicts=verdicts,
            errors=errors,
            warnings=warns,
            n_judged=n_judged,
            frozen_gain=frozen_gain,
            frozen_gain_detail=frozen_gain_detail,
            frozen_open=frozen_open,
        )


def supersede(baseline: Baseline, new_caliber: Dict[str, Any],
              reason: str) -> Tuple[Baseline, Baseline]:
    """口径变更：返回 **(新基线, 旧基线标记 superseded 后的副本)**。

    ⛔ 留痕是强制的：V5 的 `baseline_superseded_old_caliber.json` 就是这个
    形态，它让"为什么分数突然变了"可以被事后查到。
    ⛔ 返回二元组而不是"就地替换"：调用方必须显式处理旧基线，
    不能假装它不存在。

    ⛔ 新基线的 `verdicts` 是**空**的 —— 新口径必须**重跑**才能得到判定。
        调用方不要拿空基线去跑 `NonRegressionGate`（空套件会被拦）。
    """
    new = Baseline(caliber=dict(new_caliber), verdicts={}, harness_id="",
                   superseded=False, supersede_reason=reason)
    old = replace(baseline, superseded=True, supersede_reason=reason)
    return new, old


def pass_at_k(scores: List[float], k: Optional[int] = None) -> float:
    """pass@k：k 次里至少一次通过的概率（无偏估计）。

    用于"冻结套件"之外的发展集衡量：单次通过率方差太大，V5 三轮合并
    就是这个道理（score 0.7184 / pass@3 0.7931）。

    ⛔ `k=None` ⇒ `pass@n`（全部采样）；`k<=0` ⇒ **返回 0.0**，不静默
        替换成 `pass@n`。原版 `k = k or n` 把 `k=0` 当成 `k=n`，
        调用方以为拿到了"0 采样"的结果，实际是全采样。
    """
    n = len(scores)
    if n == 0:
        return 0.0
    if k is None:
        k = n
    elif k <= 0:
        #: "0 次采样至少一次通过" = 不可能。
        return 0.0
    k = min(k, n)
    c = sum(1 for s in scores if s > 0)
    if c == 0:
        return 0.0
    # 无偏估计：1 - C(n-c, k)/C(n, k)
    return 1.0 - comb(n - c, k) / comb(n, k)

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据集（judge set）：**给"判它的那段代码"一个身份**。

═══ 缺口：系统记下了被判的东西，没记下判它的东西 ═══

账本里每条 `eval.verdict` 的 payload 有：
`caliber / harness_id / reward / split / tag / task_id / verdict / run_id / sid`
内层 verdict 有 `checks / cost_s / env_fingerprint / evidence / passed`。

⇒ **环境**（被判的对象）有指纹：`env_fingerprint = hash({env_id, source,
  tests})`；**harness**（被进化的对象）有内容寻址 id。
⇒ 但**判据本身**（`verifier` / `environment.verify_source` / 闸的阈值 /
  难度分层的口径）**没有任何身份写进裁决**。

后果不是"可能出问题"，是结构上**看不见**：

  · 改一行 `verify_source` 的判据 ⇒ 之后所有 verdict 与之前所有 verdict
    **不可比**，而账本里没有任何字段能发现这件事；
  · 于是"零新红""非回归""发展集涨了"这些判据全部建立在一个
    **静默可变的标尺**上 —— 尺子变了，没人知道。

这与 `provenance`（账本出处）是同一个形态的另一半：
  · `provenance` 管**数据**是谁写的；
  · `judge` 管**尺子**是哪一版。
  两者都缺了，"回放"就只是愿望。

═══ 设计取舍 ═══

· **身份 = 判据模块的源码内容哈希 + 口径常量**。不搞版本号人工递增
  （人会忘），直接对**当前代码内容**取哈希 ⇒ 改一个字符就变。
· **不改跨语言 hash 契约**：`judge_version` 只进 payload（与 `sid`、
  `src_version` 同族），不进 `Event.identity()`。
· **三态可比性**（与 `provenance` 同源）：两侧都有且相同 ⇒ 可比；
  一侧没有（历史存量）⇒ `unknown`，**放行但标注**；
  两侧都有且不同 ⇒ `incomparable`，**拒绝拿它做裁决**。
· ⛔ **诚实性不外包**：本模块只提供身份与可比性判据，**不参与**
  "改判据"这件事的决策 —— 它让"改判据"变成**可见的、需要重新发布**的动作。
"""
from __future__ import annotations

import hashlib
import inspect
from typing import Any, Dict, List, Optional, Tuple

from .ledger import content_hash

__all__ = ["JUDGE_MODULES", "JUDGE_CALIBER", "identity", "describe",
           "normalize_source",
           "incomparable", "check_verdict_comparable", "assert_boundary",
           "COMPARABLE", "UNKNOWN", "INCOMPARABLE"]

#: 判据集包含哪些模块。⛔ **这份名单本身就是判据**：往这里加/减一个模块
#:    会改变 `identity()`，从而让历史裁决变成"不可比" —— 那正是我们要的
#:    可见性（改尺子必须留痕，不能悄悄改）。
JUDGE_MODULES: Tuple[str, ...] = (
    "aether.verifier",        # 奖励的唯一定义（Verdict.reward）
    "aether.environment",     # 执行判据的落地（verify_source / 沙箱结果）
    "aether.nongression",     # 准入闸（冻结套件是否退化）
    "aether.generalization",  # 泛化闸（发展集涨 ≠ 变强）
    "aether.repair",          # 难度分层（easy/hard 由"通用修法能否修好"定义）
)

#: 参与判定的**口径常量**（不是代码，但也决定判据）。
#: ⛔ 只放"读一次就影响裁决"的常量；放多了会让 identity 每次无关改动都变，
#:    那就退化成"永远不可比"，比没有身份更坏。
JUDGE_CALIBER: Tuple[str, ...] = (
    "aether.genome:LAYERS",
    "aether.genome:PROTECTED_LAYERS",
    "aether.verifier:SELF_REPORT_KEYS",
    "aether.nongression:pass_at_k",
)

COMPARABLE = "comparable"
UNKNOWN = "unknown"
INCOMPARABLE = "incomparable"


def normalize_source(raw: str) -> str:
    """把源码**规范化**：去掉注释与空白，只留语义。

    ⛔ 为什么必须规范化：如果对原始字节取哈希，改一个注释、调一次缩进就会
       让全部历史裁决"不可比" ⇒ 没人受得了 ⇒ 这条判据会被关掉。
       规范化后：**格式变化不算**，逻辑/字面量变化才算。
    ⛔⛔ **不能按 `#` 切**：字符串字面量里也会有 `#`（本仓库到处是
       `f"... #{x}"` 这种）。实测：按 `#` 切会把同一行的后半段一起吃掉
       ⇒ 加一行**注释**都会让**别的行**的语义被截断 ⇒ 身份乱变。
       正确做法：用 `tokenize` 拿到**精确的注释区间**，只替换那一段。
    """
    import io
    import tokenize
    out_lines = raw.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(raw).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks = []
    comments: Dict[int, Tuple[int, int]] = {}
    for t in toks:
        if t.type == tokenize.COMMENT:
            comments[t.start[0]] = (t.start[1], t.end[1])
    keep: List[str] = []
    for i, ln in enumerate(out_lines, 1):
        if i in comments:
            a, b = comments[i]
            ln = ln[:a] + ln[b:]
        ln = ln.rstrip()
        if ln.strip():
            keep.append(ln)
    return "\n".join(keep)


def _module_source(name: str) -> str:
    """某个判据模块的**规范化源码**（`normalize_source` 的取源封装）。

    ⛔ 用 `inspect.getsource` 拿源码（不是读文件）：这样"**实际被加载的**
       那份代码"才是被哈希的东西 —— 若加载的是别处的同名模块，也能看出来。
    """
    import importlib
    import inspect
    mod = importlib.import_module(name)
    return normalize_source(inspect.getsource(mod))


def identity() -> str:
    """判据集的**内容寻址身份**（128 位前缀，与其它 `_ID_PREFIX` 同值）。

    ⛔ 它只由"判据代码的内容"决定 ⇒ 同代码同身份、改一个字符就变。
       这就是我们要的：**尺子有指纹**。
    """
    payload: Dict[str, Any] = {
        "modules": {n: hashlib.sha256(
            _module_source(n).encode("utf-8")).hexdigest()
            for n in JUDGE_MODULES},
        "caliber": list(JUDGE_CALIBER),
    }
    return content_hash(payload)[:32]


def describe() -> Dict[str, Any]:
    """自述读数（给 `doctor` / `status` / 报告用）。"""
    per_module: Dict[str, str] = {}
    for n in JUDGE_MODULES:
        try:
            per_module[n] = hashlib.sha256(
                _module_source(n).encode("utf-8")).hexdigest()[:12]
        except Exception as e:                             # noqa: BLE001
            per_module[n] = f"<不可读：{type(e).__name__}>"
    return {
        "judge_version": identity(),
        "modules": per_module,
        "caliber": list(JUDGE_CALIBER),
        "note": ("判据集身份 = 上述模块的**规范化源码**哈希 + 口径常量。"
                 "改判据代码 ⇒ 身份改变 ⇒ 与历史裁决**不可比**"
                 "（这是刻意的：改尺子必须留痕）。"),
    }


def incomparable(a: Optional[str], b: Optional[str]) -> Tuple[str, str]:
    """两条 verdict 的**可比性**三态。

    ⛔ 三态不合并（与 `provenance` 同源）：
        · 两侧都有且相同   ⇒ `comparable`
        · 任一侧缺失       ⇒ `unknown`（历史存量）—— **放行但标注**
          （"不可判别 ≠ 有罪"：升级前的裁决不该被判成非法）
        · 两侧都有且不同   ⇒ `incomparable` —— **拒绝拿它做裁决**
          （拿不同尺子量出来的数比大小，是这套系统最不该犯的错）
    """
    if not a or not b:
        return UNKNOWN, ("有一侧没有判据身份（升级前的存量）⇒ 无法判别是否"
                         "同一把尺子；放行但不要让这两个数直接比大小")
    if a == b:
        return COMPARABLE, ""
    return INCOMPARABLE, (
        f"判据集不同：{a[:12]} vs {b[:12]} ⇒ 这两组读数是**不同尺子量出来的**，"
        f"不可比。要跨版本比较，必须先用同一把尺子重跑一组"
        f"（`aether rebuild` 只能重建投影，重跑判据要重跑评估）")


def check_verdict_comparable(verdicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一组 verdict 的判据身份是否一致（**一致性判据**，不是数据校验）。

    ⛔ 这个判据存在的理由：`status` 的投影对账、`evolution` 的历史对照、
      监督器的跨轮规则，全都在**跨轮/跨会话比较读数**。只要其中一组的
      判据身份不同，那些比较就是拿不同尺子量出来的数在比大小 ——
      而现在的代码结构上无法发现这件事。
    """
    vers = []
    for v in verdicts:
        jv = str((v or {}).get("judge_version") or "")
        if jv:
            vers.append(jv)
    uniq = sorted(set(vers))
    if not vers:
        return {"state": UNKNOWN, "n": 0, "versions": [],
                "reason": "这一组 verdict 都没有判据身份（升级前的存量）"}
    if len(uniq) == 1:
        return {"state": COMPARABLE, "n": len(vers), "versions": uniq,
                "reason": ""}
    state, why = incomparable(uniq[0], uniq[1])
    return {"state": INCOMPARABLE, "n": len(vers), "versions": uniq,
            "reason": why}


def assert_boundary() -> Dict[str, Any]:
    """**边界不变量**：变异/提议**不得**寻址到判据集。

    ⛔ 这是本模块存在的最硬理由。现状是：
        · `genome.apply_gene` 按 `LAYERS` 白名单拒绝未知 target
          ⇒ 基因**在构造上够不到** `aether/verifier.py`（实测：
          `target="verifier.SELF_REPORT_KEYS"` ⇒ `未知 layer` 直接抛）。
        · 但这条保护依赖两个**可以被改的东西**：
          `genome.LAYERS` 与 `genome._ALLOWED_OPS_BY_LAYER` ——
          它们本身就在变异可选中的命名空间旁边。
       ⇒ 所以这里把"够不到"变成一个**可被检查的不变量**：
          ① 判据模块的名字不得出现在任何 `LAYERS` 条目里；
          ② `apply_gene` 必须拒绝一批"试图改判据"的 target
            （用真实调用做反证，而不是读常量）；
          ③ `JUDGE_MODULES` 里每个模块都必须能被读到（否则身份是假的：
             读不到就跳过 ⇒ 身份对那部分代码不敏感 ⇒ 静默失效）。
    返回读数；**不通过时抛异常**（这是不变量，不是报表）。
    """
    import importlib
    from .genome import LAYERS, Harness, Gene, apply_gene

    problems: List[str] = []
    # ① 判据模块名不得是（或出现在）可变异层
    for name in JUDGE_MODULES:
        short = name.split(".")[-1]
        for layer in LAYERS:
            if short in str(layer) or str(layer) in short:
                problems.append(f"判据模块 {name} 与可变异层 {layer!r} 名字重叠")
    # ② 用**真实调用**做反证：这些 target 必须被拒
    probes = ["verifier.reward", "judge.threshold", "L9.verifier.x",
              "aether.verifier.Verdict.reward", "nongression.floor",
              "generalization.probe"]
    accepted = []
    for t in probes:
        try:
            apply_gene(Harness(), Gene(op="set", target=t, value="x"))
            accepted.append(t)
        except Exception:                                  # noqa: BLE001
            pass
    if accepted:
        problems.append(f"这些 target 本应被拒却被接受：{accepted}")
    # ③ 判据模块必须可读（读不到 ⇒ 身份对那部分不敏感 = 假身份）
    unreadable = []
    for name in JUDGE_MODULES:
        try:
            src = _module_source(name)
            if not src.strip():
                unreadable.append(f"{name}（源码为空）")
        except Exception as e:                             # noqa: BLE001
            unreadable.append(f"{name}（{type(e).__name__}）")
    if unreadable:
        problems.append(f"判据模块读不到 ⇒ 身份是假的：{unreadable}")
    if problems:
        raise RuntimeError("判据边界不变量被破坏：" + "；".join(problems))
    return {"ok": True, "layers": list(LAYERS),
            "probes_rejected": len(probes),
            "modules_readable": len(JUDGE_MODULES),
            "note": ("变异/提议在**构造上够不到**判据集：基因 target 只认 "
                     "LAYERS 白名单，越界即抛；本函数用真实调用做反证。")}

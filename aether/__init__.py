"""V6 · AETHER —— 可验证的自进化智能体骨架。

一句话命题（承接 V5 的"能否自证在变好"，再往前一步）：

    **每一次自我修改，都必须能被独立地证伪、回放和撤销。**

V5 证明了"纪律 + 契约 + 冻结基线"能让一个自改进系统不至于自我欺骗；
V6 把这些从**工程师的纪律**变成**系统的结构**：

    ledger         —— 唯一事实源，内容寻址 + 哈希链，改一条就断链
    verifier       —— RLVR 内核：奖励只来自可执行判据，自述不算
    genome         —— harness 5 层 + 基因/胶囊，最小编辑 + 爆炸半径
    nongression    —— 冻结非回归闸：先证没变坏，再谈变好
    environment    —— 环境优先 + 合成任务 + 执行验证（F2P）
    runtime        —— 轨迹 + token 归属记账（tool-observation masking）
    memory         —— 5 层记忆 OS + 链接演化 + 睡眠巩固 + 对抗验证
    evolution      —— self-harness 循环（MINE/PROPOSE/VALIDATE/SELECT/COMMIT）
    store          —— SQLite 投影（可由账本重建，不是第二份事实源）
    interop        —— 通用接入层：任何 agent/skill（函数/对象/CLI/HTTP）

设计取舍：**零第三方依赖**（stdlib only），可插拔 LLM/embedding。
理由：自进化系统的判据必须离线可回归 —— 依赖网络或模型，
"零新红"这类判据就会随供给波动而失效（V5 实测踩过）。
"""
from __future__ import annotations

#: ⛔ 版本的**唯一事实源**是 `_version.py`（不是这里）。
#:    这里只把名字再导出一次，保持 `aether.__version__` 这个惯例读法。
#:    依赖方向是单向的：`_version` → `provenance` → `ledger` → 这里，
#:    所以版本号不会和 `ledger` 形成循环 import。
#:    ⛔ 任何地方都不许再写版本号字面量（此前 `serve.py` 与这里各写一份，
#:    实测漂移过：包报 0.1.0、端点报 0.2.0）。
from ._version import CODE_VERSION, LEDGER_FORMAT

__version__ = CODE_VERSION

from .ledger import Ledger, Event, content_hash, canonical_json
from .verifier import Verdict, Check, Verifier, assert_no_self_report
from .genome import Harness, Gene, Capsule, apply_gene, apply_capsule, blast_radius, diff
from .nongression import NonRegressionGate, FrozenTask, Baseline, GateResult, pass_at_k
from .environment import Environment, run_in_sandbox, verify_source, synthesize_tasks
from .runtime import Trajectory, Step, group_relative, assign_credit
from .memory import MemoryOS, MemNote, hash_embedding
from .evolution import EvolutionLoop, EvolutionConfig, Weakness, mine_weaknesses
from .store import Store, RunRow
from . import provenance
from .interop import (
    Registry, Gateway, CapabilitySpec, ParamSpec, Budget,
    Invocation, InvocationResult, CapabilityError, capability,
    load_manifest, bind_capability,
)

__all__ = [
    "__version__", "CODE_VERSION", "LEDGER_FORMAT", "provenance",
    "Ledger", "Event", "content_hash", "canonical_json",
    "Verdict", "Check", "Verifier", "assert_no_self_report",
    "Harness", "Gene", "Capsule", "apply_gene", "apply_capsule", "blast_radius", "diff",
    "NonRegressionGate", "FrozenTask", "Baseline", "GateResult", "pass_at_k",
    "Environment", "run_in_sandbox", "verify_source", "synthesize_tasks",
    "Trajectory", "Step", "group_relative", "assign_credit",
    "MemoryOS", "MemNote", "hash_embedding",
    "EvolutionLoop", "EvolutionConfig", "Weakness", "mine_weaknesses",
    "Store", "RunRow",
    "Registry", "Gateway", "CapabilitySpec", "ParamSpec", "Budget",
    "Invocation", "InvocationResult", "CapabilityError", "capability",
    "load_manifest", "bind_capability",
]

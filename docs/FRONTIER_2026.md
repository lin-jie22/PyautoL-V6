# 2026 前沿依据与 V6 的落点

> 本文记录 V6 设计时所依据的公开工作，以及**每条在代码里的落点**。
> 来源为 2026-10 的公开检索（综述站点 / 论文索引 / 项目 README），
> 不是逐条精读原文 —— 引用处以"工作名 + 年份"标注，便于后续核对原文。
>
> ⛔ 原则：**写进代码的每一条前沿主张，都必须对应一个可被测试断言的结构**。
> 只是"听说过"而没有落点的，列在最后一节，不写进代码注释。

---

## 1. Self-Harness：agent 改自己的 harness（V6 的骨架）

2026 的主线从"改 prompt"升级为"改 harness"，代表工作：

| 工作 | 机制 | V6 的落点 |
|---|---|---|
| Self-Harness (Zhang et al., 2026) | 弱点挖掘 → **最小编辑** → 自动验证 | `evolution.run_round()` 的 MINE/PROPOSE/VALIDATE 三段 |
| AutoHarness (Lou et al., 2026) | 迭代代码合成 + 反馈回路 | `genome.apply_gene`（算子化编辑，非法 target 直接抛错） |
| HarnessX (Chen et al., 2026) | 符号编辑代数 | `Gene(op/target/value)` 即编辑代数的最小闭包 |
| A-Evolve (Lin et al., 2026) | 线性链 evolver：改 prompt/skills/memory/tools | harness 5 层全可编辑（`meta_rules/routing/tools/skills/memory_policy`） |
| GEPA (Agrawal et al., 2025) | 用文本反馈做 Pareto 提示演化 | 未采纳（见 §5） |
| Meta-Harness (Lee et al., 2026) | 增长的文件系统档案 + proposer | `store.snapshots()`（harness 血缘可回放） |
| Continual Harness (Karten et al., 2026) | 单次部署内在线适配 | `EvolutionLoop.run()` 多轮连续进化 |
| SIA (Hebbar et al., 2026) | harness 与**权重**共进化 | 不采纳（V6 不训练权重，见 §5） |
| SkillOS (Ouyang et al., 2026) | 用 RL 学技能策展策略 | `memory.prune()` + 产物增殖闸（规则版，非 RL 版） |
| Evolver (GEP) | genes / capsules / `events.jsonl` + git 回滚 + 爆炸半径 | `genome.Capsule`、`ledger.jsonl`、`blast_radius()` |

**关键取舍：最小编辑原则。** Self-Harness 论文强调 minimal edit proposal ——
一个 `Gene` 只改一个 target，改两处就无法归因，也就过不了非回归闸。

---

## 2. RLVR / Agentic RL：奖励只来自可执行判据

2026 的共识（多家综述一致）：**"if you can execute a check, you have a reward
function."** 前沿实验室的训练重心已从 next-token 转向可验证结果。

- **verifiable rewards**：奖励来自可检查结果（单测通过/答案匹配/终局状态），
  不由学习的 reward model 给出 —— 后者会被 reward hacking 攻陷。
- **GRPO**：一个终端奖励 + 一组 G 个采样，用组内均值作 baseline，
  不需要 critic（rollout 昂贵时这是决定性优势）。
- **tool-observation masking**：工具返回的 token 不是策略生成的，
  算 loss 时必须 mask 掉。

V6 的落点：

| 概念 | 代码 |
|---|---|
| r(τ) ∈ {0,1} | `verifier.Verdict.reward`（唯一定义） |
| verifiable check | `environment.verify_source()`（沙箱真跑，报 got/expect） |
| GRPO 组内相对优势 | `runtime.group_relative()` + `evolution` 的 SELECT |
| tool-observation masking | `runtime.Trajectory.model_tokens / env_tokens / masking_ratio` |
| 信用分配 | `runtime.assign_credit()`（可重放才给 turn-level，**否则明确标注不可用**） |

⛔ 反作弊（文献点名的头号失败模式）在 V6 是**结构性的**：
`SELF_REPORT_KEYS` 一旦出现在 evidence 里，判定直接作废；
`Verdict` 没有"模型觉得好"这个字段可以填。

---

## 3. 环境优先与任务合成（V6 的燃料）

- **SWE-smith**（NeurIPS 2026 收录）：倒置 SWE-bench 的做法 ——
  先建**共享**执行环境（O(repos) 而非 O(instances) 镜像），
  再在环境里合成成百上千个任务实例，用"是否让既有测试变红"（Fail-to-Pass）
  做 execution-based validation。50k 实例 / 128 仓库。
- **Meta ARE**：明确指出 agentic 训练的瓶颈是**环境与判据的供给**，不是 GPU。

V6 的落点：`environment.py`

```
Environment（共享，含 tests.json）
   → mutate_source()  AST 变异（比较符反转/运算符替换/常量+1）
   → verify_source()  执行验证
   → synthesize_tasks()  只保留真让测试变红的（F2P），并统计 yield
```

⛔ **yield 必须统计**：`SynthesisReport` 记录 attempted / valid /
rejected_no_failure / rejected_error。合成不出任务 = 环境没用，
不能拿"尝试次数"冒充"任务数"（V5 的教训：假成功最常伪装成"做过"）。

---

## 4. 记忆：分层 + 演化 + 睡眠巩固 + 对抗验证

2026 的共识与代表工作：

| 工作 | 主张 | V6 落点 |
|---|---|---|
| GenericAgent | 5 层记忆（L0 元规则 / L1 洞察索引 / L2 事实 / L3 技能 / L4 归档），L1 作路由层 | `memory.TIERS` + 通道闸 |
| A-MEM | Zettelkasten 式链接生成与演化，检索沿链接扩散 | `MemoryOS._link()`（双向互链）+ `search(expand_links=True)` |
| MemoryOS | 短期/中期/长期三层 + FIFO + 热度提升 | `consolidate()`（L4 → L3） |
| MemOS | MemCube 统一记忆单元 + 把记忆当系统资源调度 | `MemNote`（带来源/使用史/可迁移） |
| Mem0 2026 报告 | memory engineering 成为独立学科；审计与留存成为合规要求 | 记忆写入走账本，可回放 |
| 睡眠期巩固（sleep-time consolidation） | 离线把情景轨迹转成稳定语义知识（"LLM 的 REM 睡眠"） | `consolidate()`（默认**抽取式**摘要，不编造） |

**V6 多做的一件事（主流框架都没做）：对抗验证。**
`MemoryOS.promote()` 要求传入 verifier，且 verifier 的职责是**尽力证伪**；
`verifier=None` ⇒ 拒绝提升（fail-closed）。
理由：慢速记忆污染是长跑 agent 的真实故障，而现有基准根本不测时间尺度效应。
"没人反对"不等于"通过验证"。

---

## 5. 明确**未**采纳的三项（及理由）

| 前沿项 | 为什么不采纳 |
|---|---|
| **权重更新（SIA 的 harness+weight 共进化）** | V6 的定位是"推理侧自进化"：不动权重、可离线回归、可审计。权重更新会让"非回归"失去确定性 —— 而确定性正是 V5 资产的核心价值 |
| **GEPA（Pareto 提示演化）** | Pareto 前沿需要多目标标量化，而多目标很容易被"某个目标刷分"污染。V6 只有**一个**目标（可执行判据的 reward），其余都是闸（0/1 准入）而非目标 |
| **生成式记忆摘要** | 默认用抽取式（`_extractive_summary`）。离线巩固里一次幻觉会永久污染 L2 事实层，而 L2 会去影响 harness 决策。要生成式就显式传 `summarizer=` |

---

## 6. 文献点名的失败模式 → V6 的对应闸

| 失败模式 | V6 的闸 |
|---|---|
| catastrophic forgetting / regression | `nongression.NonRegressionGate`（准入）+ `supersede` 留痕 |
| reward hacking | `assert_no_self_report()` + `Verdict.validate()`（无 evidence 即作废） |
| capability floor（弱模型激活不了进化产物） | `EvolutionConfig.min_reward_gain`（能力地板） |
| artifact / sub-agent proliferation | `EvolutionConfig.max_size_growth_ratio` + `MemoryOS.prune()` |
| environment overfitting | `env_fingerprint`（跨环境/跨口径不可比可被检测） |
| 进化停滞 | `supervisor/anomaly.ts` 的 `EVOLUTION_STALLED` |
| 记忆污染 | `MemoryOS.promote()` 的对抗验证（fail-closed） |

---

## 6.5 兑现审计（2026-10-14）与它挖出来的真缺口

对上面 §1–§7 的每一条"落点"做了一次**运行时取证**（不 grep 符号 ——
grep 到 `def foo` 只证明写过，不证明被调用过），脚本见
`scripts/probe_frontier_audit.py`。结论分两半：

1. **§1/§3/§4/§6/§7 的落点 56/56 全部兑现**（含反证组：注入事故版，
   对应判据会翻转）。
2. **§2 里有一支是空账** —— `tool-observation masking` 与 `信用分配`
   的落点 `runtime.Trajectory` / `masking_ratio` / `assign_credit`，
   **从未被生产链路 import**。它们在 `scripts/demo_evolve.py` 里被手工
   构造过，所以看起来"有落点"，实质是**平行于主流程的死代码**。
   佐证：`aether/attribution.py` 的文档字符串自己就写着
   "端到端 evolve 里 `assign_credit` 被调用 **0 次**"。

**已修**：`HarnessAgent.run()` 现在按层动作序列产出真实 `Trajectory`
（`trajectory_from_steps()`，kind 与 `runtime.MODEL_KINDS`/`ENV_KINDS`
同源），挂到 `AgentResult.trajectory` 并在 `to_dict()` 里暴露
`masking` 读数。至此 masking 在生产链路上第一次有真值。

⚠️ 与 §5 一致的处理：`assign_credit` 的 turn-level 归因**仍然恒不可用**
（没有 `replay_fn`），这是**刻意**的 —— 不可重放就不给数字，替代方案是
`attribution.attribute_layers()` 的反事实消融。

---

## 7. 与 V5 经验的对应

V5 三年积累的硬教训，在 V6 里全部有落点（不是重述，是变成结构）：

| V5 教训 | V6 结构 |
|---|---|
| 判据必须与被测对象同源 | `Verdict.reward` 唯一定义；测试不重写判据 |
| 写入端"降级成功 ≠ 可用" | `MemNote.has_embedding`、`Verdict.validate()`（无证据即作废） |
| 加闸前做成败交叉表 | 每个闸都有反证组（事故版注入）验证判别力 |
| 报效果前先查判据触发几次 | demo 报告里 yield、闸裁决逐条列出 |
| 记账方式变了 ⇒ Δ 要拆账 | 账本单一事实源，投影可由账本重建 |
| 并发写会被热进程回写覆盖 | `ledger.append()` 先重读再写（`_hydrate()`）+ flock |
| 数字 1.0 vs 1 的序列化漂移 | `_norm()` + TS 侧同规则（selfcheck A1 守） |
| 「有机制」≠「这条路被走到」 | 兑现审计：运行时取证 + 可达性计数（本次抓出 masking 空账） |

---

## 8. 2026-10 新纳入的工作（本轮对照后补记）

本节记录**上一版清单里没有**的 2026 工作，以及它们在 V6 里的落点。
⛔ 与 §1 同一条原则：**没有落点的，不写进代码注释，只列在 §9**。

| 工作 | 主张 | V6 落点 |
|---|---|---|
| **ALE**（Agent Lifespan Engineering, 2026） | 自进化系统会**长期退化**：压缩 / 干扰 / 修订 / 维护四种机制，让"每轮单看都没亏"的系统在长跑后变弱 —— **行为测试正常，准确率已衰减**。读法是时间依赖图 + 反事实探针，不是 aggregate reward | `aether/aging.py`：`RoundTrace` 纵向读数 + `detect_aging()` 四类信号（含 `interference` 此消彼长指纹） |
| **EFC**（Effective Feedback Compute, 2026） | 只统计 **valid / legal / non-redundant / retained** 的反馈；它预测失败率远好于 raw token 数或工具调用数 | **只落地了 token 归属**那一层：`runtime.Trajectory.masking_ratio`。四分类有效性记账未落地（见 §9） |
| **HarnessAudit**（2026） | 全轨迹审计**边界合规 / 执行保真 / 系统稳定性**；违规随轨迹变长而累积，多 agent 协作扩大风险面 | 逐任务层有 `verifier.Verdict.validate()`；**全轨迹聚合**未落地（见 §9） |
| **META 综述**（衔远 + 清华, 2026-06） | 进化上限由**经验落点**决定；记忆的演进层应当学会"何时该写 / 何时该取 / **何时该忘**" | 写与取有（`consolidate` / `search`）；**"何时该忘"目前是固定阈值参数**，不可学习（见 §9） |
| **MOSS**（源级自进化, 2026） | 失败取证 → 外部代码 agent 改系统源码 → 批量重放 + 试运行 + 人工审批 + 热替换 + 回滚 | `evolution` 的 MINE/PROPOSE/VALIDATE 三段 + `supersede` 留痕 + `ledger` hash 链 |
| **Runtime interface adaptation**（2026） | 从训练轨迹里提取**反复出现的交互失败**，转成可复用干预（环境约束 / 流程技能 / 动作实现） | 部分：`MemoryAwareProposer.remember()` 把"被闸拒的经验"喂回提议器 |

---

## 9. 听说过、但**没有落点**的项（不写进代码注释）

⛔ 这是 §0 那条原则要求的"最后一节"：下面这些**没有**可被测试断言的
结构，因此它们**不是** V6 的能力，读代码时不要被注释误导。

| 项 | 为什么没落点 |
|---|---|
| **EFC 的反馈有效性四分类** | 目前只记了 token 归属（model vs env）。"这条反馈后来有没有被决策用到"（retained）需要跨轮因果追踪，尚未实现 |
| **记忆"何时该忘"的可学习策略** | `memory_policy` 是**固定阈值参数**（`recall_topk` / `min_content_len`），不是学出来的；`prune()` 现在是规则版 |
| **全轨迹边界合规聚合（HarnessAudit 那一层）** | 逐任务判定有，跨步骤的违规累积计数没有 |
| **多 agent 协作的风险面** | V6 目前是单 agent + 技能链；UCI 能挂外部能力，但没有多 agent 审计面 |
| **200 轮尺度的寿命追踪** | 老化检测目前只在**单次 `loop.run()` 内**跨轮；跨进程 / 跨部署的寿命线尚未打通 |
| **权重更新（SIA 式共进化）** | 已在 §5 明确**不采纳**（会让"非回归"失去确定性） |
| **生成式记忆摘要** | 已在 §5 明确**不采纳**（离线巩固里一次幻觉会永久污染 L2） |

**边界（诚实记录，别当"已解决"）**：
- 沙箱只到进程层，**文件系统未隔离**，拦不住 ctypes / 原生 syscall。
- held-out 环境只有 4 个，泛化结论的强度受限于此。
- 默认嵌入仍是 hashing（不是语义嵌入）。
- `inproc` 适配器**没有真超时**。

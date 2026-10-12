# V6 · AETHER 架构

## 1. 数据流

```
                    ┌──────────────── 账本 ledger.jsonl（唯一事实源）──────────────┐
                    │  内容寻址 + prev_hash 哈希链 · append-only · 可回放            │
                    └───────┬──────────────────────────────────────┬───────────────┘
                            │ 写（唯一写入路径）                     │ 读
                            │                                        │
   ┌────────────────────────▼─────────────────┐        ┌─────────────▼──────────────┐
   │  EvolutionLoop（self-harness 循环）        │        │  Store（SQLite 投影）        │
   │                                            │        │  可 rebuild_from_ledger()    │
   │  MINE     从失败 run 挖弱点（须有证据）     │        └─────────────┬──────────────┘
   │  PROPOSE  capsule = 一组最小编辑            │                        │
   │  VALIDATE ① 非回归闸(准入) ② 发展集评估     │        ┌─────────────▼──────────────┐
   │  SELECT   组内相对优势(GRPO式) + 能力地板    │        │  supervisor（TypeScript）    │
   │  COMMIT   过则提交，否则 revert（都记账）    │        │  独立校验链 + 异常检测        │
   └──────┬──────────────────────┬──────────────┘        └─────────────┬──────────────┘
          │                      │                                        │
   ┌──────▼────────┐    ┌────────▼─────────┐                    ┌─────────▼────────┐
   │ Harness(5层)   │    │ Environment      │                    │ console（只读）   │
   │ genes/capsules │    │ 合成任务 + 沙箱   │                    │ 进化树/曲线/事件流 │
   └──────┬────────┘    └────────┬─────────┘                    └──────────────────┘
          │                      │ verify(env_state)
   ┌──────▼────────┐    ┌────────▼─────────┐
   │ MemoryOS(5层) │    │ Verdict(RLVR)    │  ← reward ∈ {0,1}，只来自执行
   └───────────────┘    └──────────────────┘
```

**唯一的奖励入口是右下角。** 任何"这个变体更好"的说法都必须先变成一个
`Verdict`，而 `Verdict` 只能由 `verify(env_state)` 产生。

---

## 2. 模块职责

| 模块 | 职责 | 不是什么 |
|---|---|---|
| `ledger.py` | 唯一事实源；内容寻址 + 哈希链；并发安全追加 | 不是日志（它是**判据对象**：读之前先验链） |
| `verifier.py` | RLVR 内核；reward 唯一定义；反作弊 | 不做"综合评分"（只有 0/1 + 逐条 check） |
| `genome.py` | harness 5 层；基因/胶囊；纯函数变换 + 爆炸半径 | 不就地修改（每次变异产出新对象） |
| `nongression.py` | 冻结非回归闸（准入）+ 口径闸 + supersede | 不做 trade-off（不允许"少量回归换增益"） |
| `environment.py` | 共享环境 + AST 变异合成 + 执行验证 + yield 统计 | 不做 Docker（缩到本机秒级，接口可换） |
| `runtime.py` | 轨迹；token 归属（masking 同款记账）；信用分配 | 不可重放时**不给**近似归因 |
| `memory.py` | 5 层记忆；链接演化；睡眠巩固；对抗验证；剪枝 | 不默认生成式摘要（防幻觉污染事实层） |
| `evolution.py` | self-harness 循环五段；两种结果都记账 | 不做 I/O 决策（runner/proposer 注入） |
| `store.py` | SQLite 投影；版本化迁移；可由账本重建 | 不是第二份事实源 |
| `providers.py` | 可插拔 LLM/embedding；默认确定性 stub | stub 不假装是模型（显式标记） |
| `supervisor/` | 独立运行时校验账本 + 异常检测 | 只读，不写任何状态 |
| `console/` | 只读可视化 | 不引入构建链 |

---

## 3. 闸门清单（准入 vs 评估）

| 闸 | 位置 | 类型 | 失败后果 |
|---|---|---|---|
| 通道闸（learner 不许写 L2） | `memory.write` | 准入 | 拒绝写入并计数 |
| 去重闸（内容指纹） | `memory.write` | 准入 | 合并到已有 note |
| 完整性闸（embedding 非空） | `memory.write` | 准入 | 拒绝入库（不是"降级成功"） |
| 自述闸（`SELF_REPORT_KEYS`） | `verifier` | 作废 | 判定无效，reward 归 0 |
| evidence 闸 | `Verdict.validate` | 作废 | 判定无效 |
| 口径闸 | `nongression.check_caliber` | 准入 | 判"不可比"，禁止对比 |
| 非回归闸 | `NonRegressionGate.evaluate` | 准入 | 候选淘汰，不进发展集 |
| 受保护层闸（L0） | `evolution.run_round` | 阻断 | 停等人工审批 |
| 能力地板 | `EvolutionConfig.min_reward_gain` | 评估 | 不提交 |
| 产物增殖闸 | `EvolutionConfig.max_size_growth_ratio` | 评估 | 不提交 |
| 对抗验证 | `MemoryOS.promote` | 准入 | 不提升（fail-closed） |

⛔ 准入闸（gate）和评估项（metric）**不能混**：
先证没变坏（闸），再证变好了（评估）。混在一张表里，
新任务的收益会掩盖旧任务的回归。

---

## 4. 一轮进化做了什么（可对照代码）

```
run_round(h, n):
  base_reward = evaluate_dev(h)                    # ① 先探测，产生失败证据
  weakness    = mine(store.runs(harness_id=h.id))  # ② 无证据 ⇒ 记 noop 并结束
  capsules    = proposer.propose(weakness, h, G)   # ③ 最小编辑
  for cap in capsules:
      cand = apply_capsule(h, cap)                 # 纯函数，产出新对象
      if blast.touches_protected: blocked; continue
      gate = NonRegressionGate.evaluate(cand)      # ④ 准入
      if not gate.passed: reject; continue
      reward = evaluate_dev(cand)                  # ⑤ 发展集（另一批任务）
  adv = group_relative([base] + rewards)           # ⑥ 基线进组（防 G=1 退化）
  best = 优势为正 ∧ 增益过地板 ∧ 体积未失控
  commit(best) 或 revert(reason)                   # ⑦ 两种都进账本
```

---

## 5. 已知边界（诚实清单）

1. **runner 是演示实现**：`cli._default_runner` 用"harness 有没有某个技能"
   来决定修法。真 agent 的 runner 要接进来 —— 机制与 runner 无关（依赖注入），
   这正是这套设计的意义：先证明机制，再换真执行体。
2. **环境只带了一个 toy**（`tasks/textscore`）。SWE-smith 式的规模
   （共享 Docker + 上千实例）是接口上的扩展，不是结构上的改动。
3. **proposer 是模板化的**（`TemplateProposer`）。真 LLM proposer 通过
   `Proposer` 协议注入，接口不变。
4. **嵌入是 hashing trick**：确定性、离线、零依赖，但语义能力弱于真模型。
   换真模型通过 `MemoryOS(embedder=...)` 注入。
5. **不训练权重**：这是刻意的定位选择（见 `FRONTIER_2026.md` §5）。
6. **沙箱是 subprocess + 超时**，不是容器级隔离。跑不可信代码前需要加强。

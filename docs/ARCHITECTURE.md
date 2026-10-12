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
| `agent.py` | harness 驱动的确定性执行体：**5 层都有后果** | 不调模型（因果链必须能被单变量对照验证） |
| `genome.py` | harness 5 层；基因/胶囊；纯函数变换 + 爆炸半径 | 不就地修改（每次变异产出新对象） |
| `nongression.py` | 冻结非回归闸（准入）+ 口径闸 + supersede | 不做 trade-off（不允许"少量回归换增益"） |
| `generalization.py` | held-out 泛化闸：发展集涨 ≠ 变强；**难度分层** | **不参与候选选择**（一参与就变第二张发展集） |
| `repair.py` | 修法基线 + **可执行**难度分层（easy/hard） | 难度不靠标注（由"通用修法能否修好"判定） |
| `environment.py` | 共享环境 + AST 变异合成 + 执行验证 + yield 统计 | 不做 Docker（缩到本机秒级，接口可换） |
| `embeddings.py` | 可插拔嵌入；**dim 契约**；探测 + 显式降级 | ⛔ 绝不静默降级（异维混库 = 检索报废且无报错） |
| `retrieval.py` | 检索判别力基准（recall@1/MRR/margin）+ 嵌入选型闸 | 不看绝对相似度（跨 embedder 不可比） |
| `runtime.py` | 轨迹；token 归属（masking 同款记账）；信用分配 | 不可重放时**不给**近似归因 |
| `memory.py` | 5 层记忆；链接演化；睡眠巩固；对抗验证；剪枝 | 不默认生成式摘要（防幻觉污染事实层） |
| `evolution.py` | self-harness 循环五段；两种结果都记账 | 不做 I/O 决策（runner/proposer 注入） |
| `store.py` | SQLite 投影；版本化迁移；可由账本重建 | 不是第二份事实源 |
| `providers.py` | 可插拔 LLM/proposer；默认确定性 stub；`LLMProposer` 三道闸 | 模型输出当**不可信输入**（语法闸→解析闸→语义闸） |
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
| 泛化闸（held-out） | `GeneralizationGate.probe` | 准入 | dev 涨而 held-out 不涨 ⇒ 判 overfit，不提交 |
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
  pr = heldout.probe(h, best)                      # ⑥.5 **选出之后**才跑，只跑一次
  commit(best) 或 revert(reason)                   # ⑦ 两种都进账本
```

⛔ **第 ⑥.5 步的位置不能挪**。held-out 的分数一旦进入 `group_relative`
或 best 排序，它就变成了第二张发展集，泛化闸自我取消。契约测试
`contract_generalization_test.py` 的 C4 用"换掉 held-out 打分方式 ⇒
发展集判定必须一字不差"把这条钉死。

---

## 5. 已知边界（诚实清单）

1. **agent 不调模型**（`agent.HarnessAgent` 是确定性的规则执行体）。
   这是刻意的：机制的**因果链**必须能被单变量对照验证，而模型只能被评测。
   真 agent 走同一个 `Runner` 协议注入，接口不变。
   （已不再是"只有 L3 有后果"的演示实现 —— 见 §7。）
2. **环境是四个 toy**（`textscore` / `eventflow` / `stateflow` / `scheduler`），
   解法形状刻意不同（数值归一化 / 流式聚合 / 图搜索 / 扫描线+贪心）。
   四个是**最低要求**的两倍：两个环境只能区分"变强"与"讨好一个环境"，
   四个才能让 held-out 有难度跨度（实测四个环境都 easy/hard 混合）。
   SWE-smith 式的规模（共享 Docker + 上千实例）是接口上的扩展。
3. **proposer 默认仍是模板化的**（`TemplateProposer`）。`LLMProposer` 已接上
   真模型（本机 Qwen3.5-4B）并**实测能产出合法 capsule**，但受限于本机 4B 模型
   的输出稳定性（偶发 JSON 截断、偶发超时），端到端循环里常常
   "正确地什么都不做"。⛔ 这不掩盖机制：**模型不好 ≠ 闸失效**，
   它只会少提变异，不可能绕过闸改坏系统。
4. **默认嵌入仍是 hashing trick**：确定性、离线、零依赖。真嵌入
   （`OllamaEmbedder` / `HTTPEmbedder`）已实现并实测，但**切换要过选型闸** ——
   实测本机 nomic-embed-text 在英文上 recall@1 0.20→0.50（放行），
   在中文上 0.40→0.10（拒绝）。⛔ 换嵌入必须 `reindex()` 全量重算。
5. **不训练权重**：这是刻意的定位选择（见 `FRONTIER_2026.md` §5）。
6. **沙箱是 subprocess + 超时**，不是容器级隔离。跑不可信代码前需要加强。

---

## 6. 泛化闸：为什么"发展集涨了"还不够

非回归闸回答**有没有变坏**，回答不了另一个方向：

> 在单一判据集上做选择，最优策略会滑向**针对判据硬编码**。
> 它每一轮都在涨，但它没有变强。

这是 reward hacking 在自进化系统里的温和形态，也是最难被发现的 ——
所有数字都很好看，而且每一步都**真的**通过了执行判据。

V6 的处理：另开一个**从不参与选择**的环境，只在裁决时用一次。

### 实测（`scripts/ab_generalization.py`，单变量对照）

| 组 | 候选池 | 泛化闸 | 发展集增益 | held-out 增益 | 裁决 | 提交 |
|---|---|---|---|---|---|---|
| A | 通用修法 | on | +0.60 | +0.60 | generalize | ✅ |
| B | 定点修法 | on | +1.00 | **+0.00** | overfit | ❌ |
| C | 两类都给 | on | +1.00 | **+0.00** | overfit | ❌ |
| D | 两类都给 | **off** | — | — | （无闸） | ✅ |

C 组是重点：定点修法在发展集上**严格更好**（1.00 > 0.60）⇒ 系统一定会
选它 ⇒ 只有闸拦得住。D 组只把闸关掉，同一个候选就被提交了 ——
这证明拦住它的是闸，不是别的。

### 三条硬纪律

| | 纪律 | 怎么保证不是口号 |
|---|---|---|
| H1 | held-out **不参与选择** | 契约 C4：换掉 held-out 打分 ⇒ 发展集判定必须一字不差 |
| H2 | 与发展集**结构性不重叠** | 构造期校验，重叠直接抛异常（契约 A2） |
| H3 | dev 涨而 held-out 不涨 ⇒ **不提交** | 判据用 `<=`（等于地板也算没涨），契约 B2 |

### 一个反直觉的取舍

held-out 基线若已在天花板（reward=1.0），任何候选的增益都是 0，
会被误判成 overfit —— 那是把"无从判别"当成"有罪"。所以这种情况下
判 `inconclusive`（**同样拦截**，因为没被证明泛化），而不是放行，
也不是判 overfit。

---

## 6b. 难度分层：held-out 自己也可能没判别力

泛化闸拦得住"讨好判据"，但拦不住另一种更隐蔽的形态：

> held-out 全是简单题 ⇒ 一个"只会修简单题"的 harness 会被判成泛化。

**实测**（通用修法能修好 = easy）：

| 环境 | 合成任务 | easy | hard | 有跨度 |
|---|---|---|---|---|
| textscore | 5 | 3 | 2 | ✅ |
| eventflow | 7 | 4 | 3 | ✅ |
| stateflow | 6 | 2 | 4 | ✅ |
| scheduler | 13 | 4 | 9 | ✅ |

只要 held-out 抽样偏到 easy 一侧，"只在简单题上进步"就会被**平均值**
掩盖成"整体进步"。于是难度进了判据，而不只是进报告：

| 情形 | 总体 held-out | easy | hard | 裁决 |
|---|---|---|---|---|
| 两层都涨 | +1.00 | +1.00 | +1.00 | `generalize` ✅ |
| 只在 easy 涨 | +0.50 | +1.00 | **+0.00** | `partial` ❌ |
| 都不涨 | +0.00 | 0 | 0 | `overfit` ❌ |

`partial` 是 2026-10-13 新增的裁决取值，**拦截**不放行。
契约里那条"裁决取值是封闭集合"的断言是**故意写死**的 ——
新增取值必须有人来确认它是拦截态（`A3b` 守着这一点）。

难度本身也必须**可执行**：不靠人标注，而由"通用修法能否修好"判定
（`repair.classify_difficulty`）。反证 F3 证明：只要把"整个文件换回基线"
塞进通用修法集合，所有题都会变成 easy、分层立刻消失 ——
所以通用修法必须是**保守**的。

---

## 6c. 真模型与真嵌入：接进来之后发生了什么

### 嵌入：真模型不是"换上就更好"

本机 nomic-embed-text（768 维真嵌入）实测（`scripts/embedding_bench.py`）：

| 语言 | hashing recall@1 | nomic recall@1 | margin 变化 | 闸的裁决 |
|---|---|---|---|---|
| 英文 | 0.20 | **0.50** | +0.098 | ✅ 允许切换 |
| 中文 | 0.40 | **0.10** | −0.041 | ❌ 拒绝切换 |

同一个模型、同一个闸，两侧裁决**相反** —— 这证明闸有判别力。
nomic 在短中文上把非同义对也判到 0.776，绝对相似度全面虚高，
于是"相似度变高"反而意味着"更能区分"消失。
⛔ **绝对余弦值跨 embedder 不可比**（与"分数不可跨口径比较"同源）。

配套的三条写入端纪律（`EmbeddingSwitchGate` / `MemoryOS.reindex`）：

- 真嵌入失败 ⇒ **拒写并记账**，⛔ 绝不静默降级到 hashing
  （256 维混进 768 维的库 ⇒ 余弦静默返回 0 ⇒ 库越写越废且无任何报错，
  这就是 V5 那 169 条事故的形态，契约 C 组复刻了它）
- 换 embedder 的唯一合法路径是 `reindex()` **全量重算**，失败原样回滚
- `embedder_id`（名字+dim）写进每条记忆 ⇒ 混库事故可被 `integrity()` 点名

### 真模型 proposer：模型输出是不可信输入

`LLMProposer` 与 `TemplateProposer` **同协议**可互换，三道闸：

1. **语法闸** `validate_gene`：op / target 层 / value 类型全白名单；
   ⛔ `meta_rules`（L0 宪法层）**双锁**（独立检查 + 白名单），
   必须人工授权才能改
2. **解析闸** `parse_capsules`：非法基因 ⇒ **整条 capsule 丢弃**，
   ⛔ 不返回半成品（一条非法基因会让 `apply_gene` 崩溃，
   而崩溃在进化循环里会被记成"这个变异没用" —— 语法错误被伪装成能力问题）
3. **语义闸**：非回归闸 / 泛化闸 / 能力地板，与模板提议器完全相同

本机 Qwen3.5-4B 实测：**隔离调用**能产出 3 个通过语法闸的 capsule
（82.7s 含首次加载），并全部能被 `apply_capsule` 安全应用。
⛔ 端到端循环**没能稳定复现**：本机 ollama 只有 1 个推理槽（`-np 1`），
被另一个常驻模型占着 ⇒ Qwen3.5 请求排不进去 ⇒ 推理超时
（180s / 600s 都超时过，`retries` 也救不了）。于是端到端的结果是
"0 个候选 ⇒ 正确地什么都不做并记账"。

这**不是**机制失败，恰恰是机制在工作：模型不可用时它不会硬凑变异，
也不会把"没提变异"伪装成"闸通过了"。要真正跑起来，需要
一个独占的推理服务（或更大的槽位），代码侧不需要改。
⛔ 前提是**可观测**：`LLMProposer.diagnostics()` 会落盘到 CLI 输出，
否则"0 候选"时无法区分「闸在工作」/「模型没接通」/「输出被截断」。

过程中修掉两个只在真模型上暴露的缺陷（离线 stub 永远测不到）：

- `/v1/chat/completions` 关不掉 thinking ⇒ token 预算被思考吃光 ⇒
  `content` 空字符串，表现为"模型没输出"。改走原生 `/api/chat` +
  `think:false`：41s 且拿不到东西 → **3.2s 且内容完整**
- fenced JSON 用非贪婪正则 `\{.*?\}` 解析 ⇒ 遇到第一个 `}` 就停，
  嵌套 JSON 必被截断。改成**跳过字符串内括号**的平衡扫描

---

## 7. Harness 的 5 层：每一层都真的有后果

上一版的 runner 只读 `h.skills` ⇒ 5 层里**只有 L3 会影响 reward** ⇒
改 L0/L1/L2/L4 对结果毫无影响 ⇒ 进化循环永远不可能发现"路由没接通"
"工具没开放""记忆召回不够"这类真实弱点。基因组是装饰性的。

`agent.py` 把每一层都接进因果链（契约 `contract_agent_test.py`，21 项）：

| 层 | 单变量改动 | reward 变化 | 结构含义 |
|---|---|---|---|
| L0 meta_rules | 加【禁止整文件替换】 | 定点修法 1 → **0**；通用修法不受影响 | "改 L0 需人工审批"不再是口号：它真的改变能力边界 |
| L1 routing（无） | 有技能但没路由 | 1 → **0** | 技能不可达 = 没有技能 |
| L1 routing（键） | 按**环境名** vs 按**任务类别** | 换环境后 0 vs 1 | **路由键的选择本身就是泛化维度** |
| L2 tools | 去掉 `python_exec` | 1 → **0**（并记账 error） | 连判据都跑不了 ⇒ 直接失败，不是"降级成功" |
| L3 skills | 加修法技能 | 0 → 1 | 修法本身 |
| L4 memory_policy | `recall_topk` 0 → 5 | 0 → **1** | 技能不可达时的第二条通路 |

### 两个直接结论

**① Capsule 不是装饰。** 单基因（只加技能）reward 不变 ⇒ 过不了能力地板；
胶囊（技能 + 路由一起改）reward 变 ⇒ 被提交。最小编辑原则（一个 gene 只改
一个 target）与"改一处不够"之间的矛盾，正是靠 Capsule 解决的。

**② 路由键是泛化维度。** 按 `textscore → 修法` 路由，换到 `eventflow`
立刻失效；按 `repair → 修法` 路由，两个环境都通。泛化与否在这里第一次
变成**结构选择**，而不是策略层的行为差异 —— 也就是说，泛化闸要拦的东西，
在路由层就已经可以预防。

### 反证组（F）

改掉判据 ⇒ 对应组必须翻转，否则那些 0/1 只是巧合：
- 路由恒通 ⇒ "无路由 ⇒ 0" 翻转成 1
- 指令表清空 ⇒ L0 禁令失效，reward 回 1
- 必需工具表清空 ⇒ 缺 `python_exec` 也能跑，reward 回 1

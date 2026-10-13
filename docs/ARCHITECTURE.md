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

| 模块                  | 职责                                            | 不是什么                          |
| ------------------- | --------------------------------------------- | ----------------------------- |
| `ledger.py`         | 唯一事实源；内容寻址 + 哈希链；并发安全追加                       | 不是日志（它是**判据对象**：读之前先验链）       |
| `verifier.py`       | RLVR 内核；reward 唯一定义；反作弊                       | 不做"综合评分"（只有 0/1 + 逐条 check）   |
| `agent.py`          | harness 驱动的确定性执行体：**5 层都有后果**                 | 不调模型（因果链必须能被单变量对照验证）          |
| `genome.py`         | harness 5 层；基因/胶囊；纯函数变换 + 爆炸半径                | 不就地修改（每次变异产出新对象）              |
| `nongression.py`    | 冻结非回归闸（准入）+ 口径闸 + supersede                   | 不做 trade-off（不允许"少量回归换增益"）    |
| `generalization.py` | held-out 泛化闸：发展集涨 ≠ 变强；**难度分层**               | **不参与候选选择**（一参与就变第二张发展集）      |
| `repair.py`         | 修法基线 + **可执行**难度分层（easy/hard）                 | 难度不靠标注（由"通用修法能否修好"判定）         |
| `environment.py`    | 共享环境 + AST 变异合成 + 执行验证 + yield 统计             | 不做 Docker（缩到本机秒级，接口可换）        |
| `embeddings.py`     | 可插拔嵌入；**dim 契约**；探测 + 显式降级                    | ⛔ 绝不静默降级（异维混库 = 检索报废且无报错）     |
| `retrieval.py`      | 检索判别力基准（recall@1/MRR/margin）+ 嵌入选型闸           | 不看绝对相似度（跨 embedder 不可比）       |
| `runtime.py`        | 轨迹；token 归属（masking 同款记账）；信用分配                | 不可重放时**不给**近似归因               |
| `memory.py`         | 5 层记忆；链接演化；睡眠巩固；对抗验证；剪枝                       | 不默认生成式摘要（防幻觉污染事实层）            |
| `evolution.py`      | self-harness 循环五段；两种结果都记账                     | 不做 I/O 决策（runner/proposer 注入） |
| `store.py`          | SQLite 投影；版本化迁移；可由账本重建                        | 不是第二份事实源                      |
| `providers.py`      | 可插拔 LLM/proposer；默认确定性 stub；`LLMProposer` 三道闸 | 模型输出当**不可信输入**（语法闸→解析闸→语义闸）   |
| `supervisor/`       | 独立运行时校验账本 + 异常检测                              | 只读，不写任何状态                     |
| `console/`          | 只读可视化                                         | 不引入构建链                        |

---

## 3. 闸门清单（准入 vs 评估）

| 闸                       | 位置                                      | 类型 | 失败后果                               |
| ----------------------- | --------------------------------------- | -- | ---------------------------------- |
| 通道闸（learner 不许写 L2）     | `memory.write`                          | 准入 | 拒绝写入并计数                            |
| 去重闸（内容指纹）               | `memory.write`                          | 准入 | 合并到已有 note                         |
| 完整性闸（embedding 非空）      | `memory.write`                          | 准入 | 拒绝入库（不是"降级成功"）                     |
| 自述闸（`SELF_REPORT_KEYS`） | `verifier`                              | 作废 | 判定无效，reward 归 0                    |
| evidence 闸              | `Verdict.validate`                      | 作废 | 判定无效                               |
| 口径闸                     | `nongression.check_caliber`             | 准入 | 判"不可比"，禁止对比                        |
| 非回归闸                    | `NonRegressionGate.evaluate`            | 准入 | 候选淘汰，不进发展集                         |
| 泛化闸（held-out）           | `GeneralizationGate.probe`              | 准入 | dev 涨而 held-out 不涨 ⇒ 判 overfit，不提交 |
| 受保护层闸（L0）               | `evolution.run_round`                   | 阻断 | 停等人工审批                             |
| 能力地板                    | `EvolutionConfig.min_reward_gain`       | 评估 | 不提交                                |
| 产物增殖闸                   | `EvolutionConfig.max_size_growth_ratio` | 评估 | 不提交                                |
| 对抗验证                    | `MemoryOS.promote`                      | 准入 | 不提升（fail-closed）                   |

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
3. **真模型「跑通了」但「还没赢过」。** `LLMProposer` 已接真模型并端到端跑通  
   （9 个候选全部过语法闸与非回归闸），但泛化闸**放行 0 次** ⇒ `improved=False`。  
   它提的是定点修法（发展集满分 / held-out +0.0000），被正确拦下。  
   单变量对照（注入失败史 9 个候选 vs 不注入 7 个）⇒ 失败史**中性**：  
   不添乱，也没救场。⛔ 结论如实：这个模型在这个任务上还没赢过一次。  
   为了让它赢去放宽泛化闸是自欺 —— 那等于把"讨好判据"判成"泛化"。  
   详见 §6c「跑通与赢过是两个不同的读数」。
4. **默认嵌入仍是 hashing trick**：确定性、离线、零依赖。真嵌入  
   （`OllamaEmbedder` / `HTTPEmbedder`）已实现并实测，但**切换要过选型闸** ——  
   实测本机 nomic-embed-text 在英文上 recall@1 0.20→0.50（放行），  
   在中文上 0.40→0.10（拒绝）。⛔ 换嵌入必须 `reindex()` 全量重算。
5. **不训练权重**：这是刻意的定位选择（见 `FRONTIER_2026.md` §5）。
6. **沙箱是 subprocess + 超时**，不是容器级隔离。跑不可信代码前需要加强。
7. **账本里有 495 条历史事件不带 `run_id`**（本轮改动之前写的）。重建时
   它们用派生键 `rebuilt:{seq}` 补齐 ⇒ 条数能对上、可溯源，但**无法与当年
   那次运行用的键对上** ⇒ 这 495 条不参与键比对（只参与条数比对）。
   这是诚实边界：不是"已对齐"，是"补齐了但对齐不了"。
   `overwrite_count` 同理 —— 历史覆盖发生在记账之前，**追溯不出来**。

---

## 6. 泛化闸：为什么"发展集涨了"还不够

非回归闸回答**有没有变坏**，回答不了另一个方向：

> 在单一判据集上做选择，最优策略会滑向**针对判据硬编码**。  
> 它每一轮都在涨，但它没有变强。

这是 reward hacking 在自进化系统里的温和形态，也是最难被发现的 ——  
所有数字都很好看，而且每一步都**真的**通过了执行判据。

V6 的处理：另开一个**从不参与选择**的环境，只在裁决时用一次。

### 实测（`scripts/ab_generalization.py`，单变量对照）

| 组 | 候选池  | 泛化闸     | 发展集增益 | held-out 增益 | 裁决         | 提交 |
| - | ---- | ------- | ----- | ----------- | ---------- | -- |
| A | 通用修法 | on      | +0.60 | +0.60       | generalize | ✅  |
| B | 定点修法 | on      | +1.00 | **+0.00**   | overfit    | ❌  |
| C | 两类都给 | on      | +1.00 | **+0.00**   | overfit    | ❌  |
| D | 两类都给 | **off** | —     | —           | （无闸）       | ✅  |

C 组是重点：定点修法在发展集上**严格更好**（1.00 > 0.60）⇒ 系统一定会  
选它 ⇒ 只有闸拦得住。D 组只把闸关掉，同一个候选就被提交了 ——  
这证明拦住它的是闸，不是别的。

### 三条硬纪律

|    | 纪律                           | 怎么保证不是口号                           |
| -- | ---------------------------- | ---------------------------------- |
| H1 | held-out **不参与选择**           | 契约 C4：换掉 held-out 打分 ⇒ 发展集判定必须一字不差 |
| H2 | 与发展集**结构性不重叠**               | 构造期校验，重叠直接抛异常（契约 A2）               |
| H3 | dev 涨而 held-out 不涨 ⇒ **不提交** | 判据用 `<=`（等于地板也算没涨），契约 B2           |

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

| 环境        | 合成任务 | easy | hard | 有跨度 |
| --------- | ---- | ---- | ---- | --- |
| textscore | 5    | 3    | 2    | ✅   |
| eventflow | 7    | 4    | 3    | ✅   |
| stateflow | 6    | 2    | 4    | ✅   |
| scheduler | 13   | 4    | 9    | ✅   |

只要 held-out 抽样偏到 easy 一侧，"只在简单题上进步"就会被**平均值**  
掩盖成"整体进步"。于是难度进了判据，而不只是进报告：

| 情形        | 总体 held-out | easy  | hard      | 裁决             |
| --------- | ----------- | ----- | --------- | -------------- |
| 两层都涨      | +1.00       | +1.00 | +1.00     | `generalize` ✅ |
| 只在 easy 涨 | +0.50       | +1.00 | **+0.00** | `partial` ❌    |
| 都不涨       | +0.00       | 0     | 0         | `overfit` ❌    |

`partial` 是 2026-10-13 新增的裁决取值，**拦截**不放行。  
契约里那条"裁决取值是封闭集合"的断言是**故意写死**的 ——  
新增取值必须有人来确认它是拦截态（`A3b` 守着这一点）。

难度本身也必须**可执行**：不靠人标注，而由"通用修法能否修好"判定  
（`repair.classify_difficulty`）。反证 F3 证明：只要把"整个文件换回基线"  
塞进通用修法集合，所有题都会变成 easy、分层立刻消失 ——  
所以通用修法必须是**保守**的。

### 分层裁决必须与发展集**同层对照**

⛔ 一个自指陷阱：`hard` 的定义就是"通用修法修不好" ⇒ 任何通用修法在
hard 层的增益**必然**是 0 ⇒ "某层没涨 ⇒ 拦截"会把**唯一能泛化的修法**
永久拦死。实测三轮 × 两组全部被拦，`improved=False` ⇒ 系统再也不会进化。

修法是把判据从"这一层涨没涨"换成"**发展集同层涨没涨**"：

| 发展集同层 | held-out 同层 | 裁决 | 含义 |
|---|---|---|---|
| 涨了 | 没涨 | `partial` ❌ | **挑食**（换环境就不行） |
| 没涨 | 没涨 | `generalize` ✅ | 能力边界（不是诚信问题） |
| **没有这一层** | — | `inconclusive` ❌ | 无从判别（不可判别 ≠ 有罪） |

⛔ 缺层是**采样配置**问题，不能让候选背锅：难度层的覆盖依赖采样量
（`max_tasks=8` 时发展集可能采不到 hard 层 ⇒ 闸无从做同层对照）。
现在会自动**补采样**（实测 8→16，1–3s；合成任务与候选无关 ⇒
不给任何优待），补到上限仍缺 ⇒ 明确报"该环境配对难度分布不可比"，
而不是沉默拦截三轮。

---

## 6c. 真模型与真嵌入：接进来之后发生了什么

### 嵌入：真模型不是"换上就更好"

本机 nomic-embed-text（768 维真嵌入）实测（`scripts/embedding_bench.py`）：

| 语言 | hashing recall@1 | nomic recall@1 | margin 变化 | 闸的裁决   |
| -- | ---------------- | -------------- | --------- | ------ |
| 英文 | 0.20             | **0.50**       | +0.098    | ✅ 允许切换 |
| 中文 | 0.40             | **0.10**       | −0.041    | ❌ 拒绝切换 |

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

### 「跑通」与「赢过」是两个不同的读数

推理槽空出来之后端到端跑通了，但立刻要区分两件事 —— 它们在日志里
长得几乎一样（都有候选、都有 reward、都有裁决）：

| | 判据 | 实测（Qwen3.5-4B，dev=textscore / held-out=eventflow，3 轮） |
|---|---|---|
| **跑通** | 产出候选 > 0 且有能过非回归闸的 | ✅ 9 个候选，9 个全过非回归闸 |
| **赢过** | `improved == True`（必须**同时**过泛化闸） | ❌ 泛化闸放行 0 次 |

`scripts/llm_e2e.py` 把这两列单独打出来。**结论如实**：这个模型在这个
任务上**还没赢过一次**。它提的是定点修法（`patch:textscore`），发展集
满分、held-out +0.0000 ⇒ 被正确拦下。

⛔ 为了让它"赢"去放宽泛化闸是**自欺**：那等于把"讨好判据"判成"泛化"。
真要它赢，得让模型自己想到跨环境的改法。

### 告诉模型规则，但不替它作答

模型第一次提的 capsule 结构完全合法，却把路由键写成技能名
`L1.routing.textscore_eval`（真实键是任务类别 `repair`）、技能正文写成
自然语言描述（真实技能名只有 `fix_control_flow` / `patch::<env_id>`）
⇒ reward=0。这时**"闸在工作"和"模型不知道接口"长得一模一样**，缺的是
API 文档。于是契约由调用方注入（`route_keys` / `skill_vocab` / `env_ids`），
不硬编码（硬编码 ⇒ 换个环境就骗模型一次）。

⛔⛔ 但注入有**红线**：只能讲**规则**，不能讲**答案**。

- 可以讲："你提的变异会在另一个环境 `eventflow` 上复核，只对某一个环境
  有效的会被判 overfit 拦下"、"这些变异试过并被拦了（附原因）"
- 不能讲："你应该用 `fix_control_flow`"

讲了答案就是把模型该考的试替它考了 —— 外部预置的知识会掩盖
"这个系统还不会修"这个真实弱点，与 §7b「记忆只能由执行填充」是同一条
纪律。契约测试 G3 守着这条红线（prompt 里出现答案句式 ⇒ 红）。

### 整批被截断 ⇒ 逐对象抢救

实测：模型在第 3 个 capsule 的 `value` 里塞了一整段源码 ⇒ 超 token 预算
被截断 ⇒ **整个 JSON 作废 ⇒ 前两个合法的候选一起丢掉**。那会把
"模型啰嗦"记成"模型没产出"，而两者处置完全不同。

`parse_capsules` 降级为逐对象扫描：完整的保留、截断的丢弃，并**显式记账**
（`partial_parse:truncated_tail` + capsule 上带 `salvaged:kept=2,dropped=1`）。

⛔ 纪律不松动：每个被保留的 capsule 自身仍是完整解析 + 过语法闸的，
这里做的是**隔离**不是"返回半成品"；抢救路径同样拦 L0（契约 F6 反证）。
全部截断时救不出任何东西 ⇒ 仍然返回空列表（F4 反证）。

---

## 7. Harness 的 5 层：每一层都真的有后果

上一版的 runner 只读 `h.skills` ⇒ 5 层里**只有 L3 会影响 reward** ⇒  
改 L0/L1/L2/L4 对结果毫无影响 ⇒ 进化循环永远不可能发现"路由没接通"  
"工具没开放""记忆召回不够"这类真实弱点。基因组是装饰性的。

`agent.py` 把每一层都接进因果链（契约 `contract_agent_test.py`，21 项）：

| 层                | 单变量改动                 | reward 变化               | 结构含义                        |
| ---------------- | --------------------- | ----------------------- | --------------------------- |
| L0 meta_rules    | 加【禁止整文件替换】            | 定点修法 1 → **0**；通用修法不受影响 | "改 L0 需人工审批"不再是口号：它真的改变能力边界 |
| L1 routing（无）    | 有技能但没路由               | 1 → **0**               | 技能不可达 = 没有技能                |
| L1 routing（键）    | 按**环境名** vs 按**任务类别** | 换环境后 0 vs 1             | **路由键的选择本身就是泛化维度**          |
| L2 tools         | 去掉 `python_exec`      | 1 → **0**（并记账 error）    | 连判据都跑不了 ⇒ 直接失败，不是"降级成功"     |
| L3 skills        | 加修法技能                 | 0 → 1                   | 修法本身                        |
| L4 memory_policy | `recall_topk` 0 → 5   | 0 → **1**               | 技能不可达时的第二条通路                |

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

---

## 8. 观测侧不得有假仪表

### 恒亮绿灯

`serve.py` 的 `/api/status` 里 memory 段原先是**硬编码**的：

```python
"memory": {"tiers": ["L0", "L1", "L2", "L3", "L4"]}
```

它与真实记忆库**完全无关**：库里 0 条它报五层，库被污染它报正常，
库文件被删了它还报五层。写入侧刚建好污染闸（整库拒载 / `inherited`
标记 / 未验证词表），观测侧却是一块假表 ⇒ **污染在账本里可见、
在控制台上不可见**。

这与 §7b 那条"写入成功 ≠ 可用、验收必须查字段完整性"是同一条纪律，
只是发生在**读侧**：一个不随被测对象变化的读数，等于没有读数。

现在的读数（`serve.memory_report`，CLI 与 HTTP **共用同一个函数**）：

| 字段 | 点名什么 |
|---|---|
| `present` / `readable` | 库是否存在 / 能否解析（半截 JSON 必须报错，不能当空库） |
| `total` / `layer_sizes` | 条数与层分布（按 `tier` 字段，与 `MemNote` 同源） |
| `dim_histogram` / `embedder_histogram` | 实际维度与 embedder 分布 |
| `foreign_embedder` / `wrong_dim` | **混库事故**（与库声明不符的条目数） |
| `no_embedding` | 空向量条目（永远检索不到 —— V5 那 169 条的形态） |
| `unverified` | 含"大概是 / 待确认 / TODO"的条目（词表复用 `memory.py`，不抄第二份） |
| `integrity_ok` | 上面四项全 0 才为真 |

⛔ 判据复用 `memory.py` 的 `UNVERIFIED_MARKS` / `PLACEHOLDER_BODIES`
（唯一事实源）。在 serve 里抄一份词表 ⇒ 两侧漂移 ⇒ "控制台说没事"
就变成假没事。

### 拒载必须有出口

换 embedder 后 `MemoryOS.load` 会**整库拒载**（设计正确：异维余弦不可比）。
但拒载之后如果没有恢复命令，系统就永久停在"读不了" ——
**一条正确的闸配一个死胡同**。

    python -m aether.cli --root . memory show
    python -m aether.cli --root . memory reindex --to hash

`reindex` 走 `load_for_reindex`（**只装载内容、丢弃旧向量**）→
`reindex()` 全量重算 → 落盘。三重保护：

- 装载后每条都无向量且打 `embed_missing` ⇒ 不存在"看起来能用"的中间态
- 重算有条目失败 ⇒ **不落盘**（落盘 = 把 `embed_missing` 固化成正常库）
- 整批失败 ⇒ 原样回滚，embedder 不变

⛔ 记账必须说真话：`reindex()` 的 `from` 取自**文件里声明的**
embedder（经 `load_for_reindex` 传回）。否则 `MemoryOS` 已经指向新
embedder，报告会写成 `from hashing:256 → to hashing:256` —— 一句同义
反复，看起来像什么都没干，实际全量重算过。

---

## 8b. 投影（SQLite）也必须能与账本对账

同一条纪律往下一层：**读数假**之外，还有**视图丢数据**这种形态。

### 实测：账本 495 条执行，投影只剩 51 条

| 读数 | 值 |
|---|---|
| 账本 `eval.verdict` | **495** |
| 投影 `runs` | **51** |
| 丢失 | 89.7% |

根因三条，一起才解释得通：

1. `run_id = f"{tag}{harness[:8]}:{task_id}"` —— 同一 (harness, task)
   组合的每次执行 id 相同 ⇒ `INSERT OR REPLACE` 后一条覆盖前一条；
2. 账本的 `eval.verdict` **不带** `run_id` ⇒ 重建只能造 `rebuilt:{seq}`
   ⇒ 与现状库键空间完全不同 ⇒ "库可由账本重建"其实**无从比对**；
3. 初始 harness `h0` 由 CLI 直接 `store.save_snapshot()` 写库、绕过账本
   ⇒ 重建会丢掉它（实测 7 个快照重建剩 6 个）。

而守着这件事的测试是：

```python
n = st.rebuild_from_ledger(...)
ck("E1 从账本重建成功", n >= 0)          # ⛔ 恒真
ck("E2 重建后投影可读", st.stats()["n_runs"] >= 0)   # ⛔ 恒真
```

**绿了很久，什么都没证明。** 这是"判据必须能被反证"最朴素的反例：
一个不可能失败的断言，测的是自己的乐观程度。

### 修法（三处同源）

| 处 | 改动 |
|---|---|
| run_id | `next_run_id()`：**唯一事实源**，锚在账本 seq（跨运行也不重置） |
| 账本 | `eval.verdict` payload 带上 `run_id`（与 `RunRow` 同一个对象） |
| 初始 harness | `loop.register_root(h)`：**先进账本**（`harness.init`）再落投影 |
| 覆盖本身 | `save_run` 检出"同键不同内容" ⇒ 累加 `meta.overwrite_count` |
| 出口 | `cli rebuild`（`--check` 只判 / 默认备份旧库后重放） |

### 判据：键集合 + 字段 + **条数**

`Store.consistency_report()` 每次 `status` 都跑（`serve._collect` 同函数）：

```json
{"ok": true, "ledger_verdicts": 515, "projection_runs": 515,
 "count_ok": true, "missing_count": 0, "drift_count": 0,
 "unkeyed_events": 495, "overwrite_count": 0}
```

⛔ **条数比对是丢历史的唯一抓法**：键比对只能发现"对不上"，发现不了
"压根没写进来"。495 → 51 就是靠这一条被抓到的 —— 当时的控制台只显示
"运行记录 51"，看起来完全正常。

⛔ 历史事件缺 `run_id` 的，重建时用**派生键** `rebuilt:{seq}` 写入并单独
计数 `derived`：seq 就是它在账本里的位置，可溯源，不是假键；但它不参与
键比对，免得"补齐历史"被算成"对齐"。

---

## 8c. console 的三处假仪表（同一条病，在读侧）

`console/index.html` 这一轮一起修掉：

| 位置 | 症状 | 修法 |
|---|---|---|
| 闸门裁决表 | "优势"列**硬编码** `—`（账本里根本没这个值） | 换成真有的字段：冻结任务数 / 回归数 / 口径原因 |
| 缺失的核心表 | 泛化闸（dev 增益 vs held-out 增益 / 裁决）**根本没有** | 新增表格 —— V6 的核心判据在控制台上居然看不见 |
| 平均 reward 图 | 标题写"平均 reward"，**高度画的是判定条数** | 高度 = reward 均值（条数退到 tooltip） |
| 运行记录 | 只显示投影的 51，不说账本的 495 | 两个数**并排**显示，不一致时弹红色告警区 |

⛔ 最后一条是这轮的关键：**事故在账本里可见、在控制台上不可见 = 没被看见**。
观测侧不报这件事，等于替写入侧遮丑。

---

## 9. 闸自己不能是假的

`NonRegressionGate` 的裁决原本是：

```python
passed = (not regressions) and (not errors)
```

**冻结套件为空时两项都空 ⇒ `passed = True`。** 准入闸在自己最该响的时候
静默放行 —— 那不是读数假，是**闸本身**假：任何变体都能"证明没变坏"。
现在空套件 ⇒ fail-closed（`errors` 点名 `empty_frozen_suite`）。

### 但"基线全 0"不能跟着拦 —— 那是自指死锁

第一版把这两种情况一起拦了，立刻在 `contract_causality_test` E 组撞红：

> 系统还没能力 ⇒ 基线恒 0 ⇒ 每个候选都被"无通过项"拦下 ⇒ 永远无法进化。

这与 §6b 那个难度分层的自指死锁是**同一个形态**（`hard` 的定义就是
"通用修法修不好" ⇒ 通用修法在 hard 上增益必 0 ⇒ 唯一可泛化的修法被永久
拦死）。分界线是：

| 情形 | 判据跑得起来吗 | 处理 |
|---|---|---|
| 套件为空 | 跑不起来 | **拦**（无从判别 ⇒ fail-closed） |
| 套件非空、基线全 0 | 跑得起来（崩溃会被 `errors` 抓） | **照跑**，只在 `warnings` 标注 `baseline_all_red` |

⛔ 标注不是装饰：`baseline_all_red` 的意思是"本次裁决**无回归可判**"，
而不是"已证明无回归"。这两句话在系统自证时差得很远。

## 10. 跨轮判据必须有会话归属（监督器曾 8/8 误报）

### 实测：一条规则报了 13 条 critical，逐条核对全是假的

`supervisor/src/anomaly.ts` 的 `GENERALIZATION_GATE_BYPASSED` 是**跨语言
不变量**的核心：Python 侧说"我拦下了 overfit 候选"，监督器只读账本就能
独立验证"确实没提交"。它原来的匹配条件是：

```ts
commits.filter((c) => payloadOf(c).round_no === rn && c.seq > pe.seq)
```

只按 `round_no`。而 `round_no` **每个进程都从 1 重新数** ⇒ 跨小时、跨
运行的事件被凑成"同一轮"。真实账本上的输出：

```
❌ @208 泛化闸裁决=overfit（round 1），但同轮仍提交了 harness ⇒ 闸被绕过
   · @323 49ea71f7 · @356 db2ee5ab · @430 4efcad1e · @463 c098e95d ...
```

逐条核对后：被拦的候选是 `add_skill:patch:textscore`，被点名的提交**全是
另一个候选 `add_skill:fix_control_flow`**（它自己的泛化闸判 `generalize`，
提交完全合法），时间上相隔 69 秒到几小时。**8/8 是误报**，退出码 2。

代码路径也印证了不可能是真绕过：`evolution.py` 里 `if pr.blocks:` 之后
是 `best = None` ⇒ 被拦的候选根本到不了提交分支。

### 修法：同候选 + 同会话，缺一不可

```ts
const cCap = String(p.capsule ?? "");
if (pCap && cCap && cCap !== pCap) continue;   // ① 同一个候选
// ② 同一次运行（sid）—— round_no 每次进程启动都重置
```

`sid` 由 `Ledger.append()` 注入 `payload`（**不新增 Event 字段** —— 那会改
跨语言 hash 契约，TS 侧必须同步才能校验链；payload 本就参与 hash，历史
文件缺 `sid` 也不影响 `verify_chain`）。

### ⛔ 归属未知时不许硬判

历史账本（718 条）里没有 `sid`。`round_no` 相同 + `capsule` 相同，仍然
无法排除"这是两次独立运行"。此时：

- **不判** critical（那是把无法判定当成已证明）；
- **也不静默跳过** —— 报 `GATE_BYPASS_UNVERIFIABLE`（warn，18 组），
  写明"无法判定是跨运行串味还是真绕过，新事件已带 sid 可精判"。

修完：真实账本 critical 从 13 → **0**，退出码 2 → 1（那条 warn）。

### 反证组（selfcheck C7–C13）

| 用例 | 期望 |
|---|---|
| C7 同会话同候选被拦却提交 | **critical**（真绕过，退出码 2） |
| C9 被拦 A、提交 B | 不报（闸在工作） |
| C10 不同 sid 同 round_no | 不报（round_no 会重置） |
| C11 旧账本缺 sid | 不报 critical，但报 **unverifiable warn** |
| C12 同会话不同轮 | 不报 |

注入事故版（退回"只看 round_no"）实测 **C9 / C11 转红** ⇒ 反证不是摆设。

## 11. 记忆必须进账本（因果链上不能有没有证据的一环）

宪法第 1 条说"事实只有一份"，但实测：

```
data/memory.json  →  11 条记忆
data/ledger.jsonl →  0 条 memory.write
```

而记忆**跨运行继承、直接喂给提议器**（`MemoryAwareProposer`），是因果链
上真实的一环 ⇒ 谁写的、什么时候写的、被闸拒了多少，全都查不到。

现在：`MemoryOS.attach_ledger()`（挂载式，未挂载行为完全不变 ——
`MemoryOS` 有几十处构造点，改签名会让所有调用点跟着动），每次写入
**尝试**（含 `rejected_*`）记一条 `memory.write`：

```json
{"tier":"L3","channel":"curated","source":"round1","disposition":"written",
 "note_id":"L3:7b3e9f8cbe0ce049","content_hash":"9ffcd716…","inherited":false}
```

⛔ 只记成功写入的话，"被拒了多少、为什么被拒"在事实源里就不存在 —— 那
是把闸的另一半藏起来。

### 对账读数：`pre_ledger` ≠ `partial`

| 状态 | 含义 | 处置 |
|---|---|---|
| `complete` | 每条记忆都能在账本里找到写入证据 | 正常 |
| `pre_ledger` | 账本 0 条事件（存量早于账本化） | 历史遗留，**不算事故** |
| `partial` | 有事件，但仍有条目对不上 | **真问题**（写入路径漏记） |

两者必须分开说，否则会拿历史遗留冒充事故、或拿事故冒充遗留。

### 出口：`memory backfill`

`pre_ledger` 如果不给出口，就是一条永远修不好的告警 —— 而修不好的告警
会被忽略，等于没有告警。补记后 `untraceable` 归零（实测 13 条）。

⛔ 但 backfill **不是**实时写入的等价物：它只证明"此刻库里有这条"，
证明不了"它当初是这么被写进来的"（中途被外部改过也看不出来）。所以
`disposition="backfill"` 单独计数，**不混进 `written`**。

## 12. 失败不许只写在内存里（层归因）

```python
except Exception as e:
    rep.layer_credit = {"available": False, "reason": ...}   # ← 只到这里
```

归因整段包在 try/except 里是对的（归因失败不得影响提交），但结果只进
内存报告 ⇒ 账本上看不见、控制台看不到 ⇒ 下一次仍然不知道自己在盲改。

现在同一分支 `append("attribution.unavailable", {error})`，监督器加
`ATTRIBUTION_MISSING` / `ATTRIBUTION_FAILED` 两条 warn，控制台状态栏显示
"层归因（成功/失败）"。

⛔ 实测顺带澄清了一件事：账本里 18 次提交只有 4 条归因事件，看着像漏跑；
逐条查 `round_no` 后确认——归因功能是后来才引入的，**引入之后的 4 次提交
100% 有归因**。数量差不是缺陷，是时间差；若不看数据就会去"修"一个不存
在的漏跑。

## 13. 到顶 / 死路：空转必须能被识别并停止

### 实测：10 轮压力跑里 r2–r10 是同一轮，重复了 9 次

```
improved: True | rounds: 10
  r1: committed=4efcad1edea76a13 base=0.0
  r2: committed=None base=1.0  reason=no_advantage(0.000)
  r3: committed=None base=1.0  reason=no_advantage(0.000)
  ... r10 同
```

每轮挖出**同一个**弱点、提**同一个**候选、得到**同一个** `advantage=0.000`。
而报出来的原因是含糊的 `no_candidate_passed` ⇒ 看上去像"努力了 9 轮没成功"，
实际是"已经没有可提升空间"。

### 根因：弱点挖到了**上一次运行**的失败

`mine_weaknesses` 从 store 按 `harness_id` 取 run。`harness_id` 由内容决定
⇒ 新一次运行的 seed harness 与上次**同 id** ⇒ 上次遗留的失败 run 被当成
"当前弱点"。与监督器那个误报（§10）**同形态：缺会话归属**。

⛔ 而投影里的 run **没有 sid**（`RunRow` 无此字段），按 sid 过滤在当前 schema
下做不到 ⇒ 不绕路，改数据源：

```python
runs = self._runs_from_verdicts(base_vs, h)   # 只用本轮 base 评估的判定
```

本轮判定天然属于"这一轮 + 这个 harness + 当前这套发展集"，串味在结构上
不可能发生。历史 run 照旧写进 store（供投影/归因/审计）。

### 「没提交」有三种含义，不许合并成一句

| 状态 | 含义 | 处置 |
|---|---|---|
| `generalization_gate(...)` | 被泛化闸拦下 | **失败**，值得重试 |
| `saturated:dev_clean` | 发展集全绿、无候选能超过 | 到顶，停 |
| `saturated:stagnated` | 有弱点、有候选，但没人赢得过基线 | 停滞，停 |
| `no_weakness_evidence` | 发展集无失败证据 | 无事可做，停 |

后三种都打 `rep.idle = True`，**连续 2 轮**即终止整个循环（不是累计 ——
偶尔一轮没事做是正常的，比如刚提交完新 harness 还没暴露失败），并记
`evolution.stopped`（带 `rounds_requested` ⇒ 能看出被砍了多少轮）。

⛔ 饱和时跑一次 held-out 读数，但记 `saturation.probe` **不是** `gen.probe`：
后者是监督器规则读的判定流，混进去会污染"闸被绕过"等判据的样本。此时
`best` 已是 None ⇒ 没有候选在拿 held-out 互相比 ⇒ 不存在"用考试题选答案"。

## 14. 已知死路不许反复探测（带出口）

真实账本上 `add_skill:patch:textscore`：**15 次探测 / 15 次 overfit /
0 次通过**，占掉 55% 的探测预算，仍被一遍遍提议。

冷却：同一 `(capsule, env_pair)` **连续**被拦 ≥3 次 ⇒ 本轮不再花预算。
两个关键点：

- ⛔ **连续**（遇到一次 `generalize` 归零），不是累计 —— 累计会把"早期失败
  过、后来成了"的候选永久封死（与难度分层那个自指死锁同形态）；
- ⛔ **按环境对**：`gen.probe` 事件原先**没有** `env_pair` 字段（实测 15 条
  全是 `None`）⇒ 冷却只能按 capsule 全局生效 ⇒ 换环境后被误伤。现在
  `GeneralizationGate.env_pair` 取 `dev_envs->ho_env`，取不到如实返回
  `unknown`（不编造）。
- ⛔ **出口**：冷却后每 5 轮允许试探一次（`deadend_probation_every`）。

数据来自**账本**不是内存 ⇒ 换一次运行不会"忘了"这条死路。
交叉表：15 条全拦、0 条曾通过 ⇒ 误伤为 0。

## 15. 跨语言 hash 契约：半整数毫秒会漂移

### 实测：监督器把一条完好的账本判成"被篡改"

```
❌ [LEDGER_CHAIN_BROKEN] hash 不匹配 @788（实得 2848712c2c… 应为 ad847be8e0…）
```

而 Python 侧 `verify_chain()` 返回 **True**。两端都自称在校验同一条链。

`ts = 1791871960.7365` ⇒ `ts * 1000 = 1791871960736.5`（恰好半整数）：

| 端 | 表达式 | 结果 |
|---|---|---|
| Python | `int(round(x))` | `...736`（银行家舍入，取偶） |
| TypeScript | `Math.round(x)` | `...737`（half-up） |

1028 条里命中 1 条 —— **概率性**漂移。合成对拍用例的 `ts` 全是整数，
永远撞不到，所以 selfcheck 一直全绿。

### 修法：TS 侧实现 `roundHalfEven` 与 Python 对齐

选"改 TS"而不是"改 Python"：Python 是写入端，改它会让历史 1028 条的
hash 全部失效（那才是真的破链）。改完后真实账本 `ok: true`。

### 反证：A8/A9/A10 用**真实值**对拍

```
halfTs = 1791871960.7365
A8  TS 与 Python 算出同一个 hash
A9  链仍完整（不误报篡改）
A10 舍入取偶：...736.5 ⇒ ...736
```

⛔ 教训与 §10 同源：**反证的构造必须与真实 payload 同源**。合成用例里
ts 取整数，测的不是真实形态，绿了也是假的。


## 13. held-out 的判别力必须由闸自己检查（有属性 ≠ 进了判据）

泛化闸的全部价值建立在一个前提上：**这套 held-out 有能力区分"真泛化"
与"只会修一类题"**。这个前提此前是**没被检查**的。

`HeldOutSuite.spread` 早就存在，`repair.py` 的 docstring 里也白纸黑字写了
危害（"held-out 全是 easy ⇒ 只会修 easy 的 harness 会被判成 generalize"），
`cli.py` 也会打 ⚠️ 警告 —— 但 `probe()` 的判据里**从来没有读过它**。
grep 确认：`spread` 只被**测试**（E1/E2）读到。

这是最容易漏的一类缺口：有属性、有文档、有告警、唯独没进判据。

### 反证：裁决被采样量翻转

`scripts/probe_spread_audit.py` 用同一个候选、同一份行为（held-out 的 easy
层涨、hard 层**不涨** ⇒ 典型挑食）做对拍：

    held-out 采到 hard 层  ⇒ partial       （拦）
    held-out 少采一层      ⇒ generalize    （放行）

放行输出里还照样带着 `by_difficulty={'easy': 1.0}` 字段，看起来像做过
分层裁决 —— **它和真放行完全同形**。也就是说"放行"这个结论依赖的是
采样量，不是候选行为。

### 交叉表（加闸前必做）

| `--max-tasks` | 4 个环境里无跨度的个数 |
|---|---|
| 2 / 3 | 4 / 4 |
| 4 / 5 / 6 | 3 / 4 |
| 8 | 2 / 4（stateflow、textscore） |
| 10 / 12 / 16 / 24 | 0 / 4 |

⛔ 默认 12 下这套闸**命中 0 次** —— 也就是说默认配置能跑通是**巧合**，
不是保证。正是这一点决定了修法必须是"补采样出口 + 兜底"，不能只是加闸。

### 修法：三段，缺一段就变成"永远不提交"

1. **出口优先**（`cli.py`）：held-out 无跨度 ⇒ 先 `grow_for_difficulty`
   扩合成采样（实测 8→16 补齐），到顶仍缺层才报"判别力不足"。
   ⛔ 合成任务来自 AST 变异、与候选无关 ⇒ 扩 held-out 采样不给任何候选
   优待，不违反 H1（held-out 不参与选择）。
2. **闸侧兜底**（`_downgrade_if_flat`）：仍无跨度 ⇒ 不许判 `generalize`，
   降级为 `inconclusive`（拦截），reason 指向"去补采样"而不是"改候选"。
3. **可观测**：`discriminative` 三态写进裁决本身、账本、控制台表格与告警区；
   监督器加 `PASS_WITHOUT_DISCRIMINATION`（critical）—— 即**闸的闸**。

### 三条边界（都有实测依据）

- ⛔ **只降级"放行"，不盖掉拦截**。无跨度 + held-out 完全没涨 ⇒ 仍判
  `overfit`。判别力不足只会让"放行"失去依据，不会让硬证据失效；用
  inconclusive 覆盖 overfit 是把硬证据换成"我也不知道"（信息倒退）。
- ⛔ **三态必须分开**：`True`（有跨度）/ `False`（只有一层，**有证据**的
  判别力不足）/ `None`（没打标签 = **没测过**）。`None` 不拦 —— V5 纪律
  "不可判别 ≠ 有罪"，拦它会让所有未分层配置一夜变成"永远不提交"。
  监督器侧同样用 `=== false` 而不是 falsy（`null` 会漏进来）。
- ⛔ **判别力是 held-out 的属性，不是某个分支的属性** ⇒ 由 `probe()` 统一
  后处理写入。让各分支自己填 ⇒ 只有放行分支记得填，实测 `overfit` 裁决上
  出现过 `discriminative=None`（H9 守这条）。

### 反证组

- `contract_difficulty_test.py` F5：把 `_downgrade_if_flat` 短路掉 ⇒
  标错标签的 held-out 重新**放行**挑食候选（F2 必须翻转，否则恒真）。
- H1–H9：无跨度 ⇒ 不判 generalize / 仍拦 / 给出路 / 与 D 组对拍 /
  不盖 overfit / 三态齐全 / 分支一致。
- **事故版注入**：临时把兜底改成 `if True` ⇒ 5 项（F2、H1、H2、H4、H5）
  集体转红，反证探针重新报缺口。确认测试不是恒真的之后已还原。
- `supervisor/src/selfcheck.ts` C14/C15/C15b：`discriminative=false` 却
  `generalize` ⇒ critical；`true` 与 `null` 都不许报。


## 14. 沙箱必须是**实测过的隔离**，不是"用了 subprocess 就叫沙箱"

`run_in_sandbox` 一直自称"沙箱隔离"，实际是
`subprocess.run(timeout=, cwd=临时目录)`。2026-10-13 用
`scripts/probe_sandbox_audit.py` 逐条实测，7 项里 **5 项没拦住**：

| 项 | 实测结果 |
|---|---|
| 读宿主环境变量 | ❌ **181 个变量全被继承**，含 `PYAUTOL_VAULT_KEY`、`TAVILY_API_KEY`、`WORKBUDDY_PAC_RPC_TOKEN` |
| 写工作目录之外 | ❌ 能写 `/tmp` |
| 读工作目录之外 | ❌ 能读 `/etc/hosts` |
| 输出大小 | ❌ 20 MB 被 `capture_output` **全量读进内存** |
| fork 子进程 | ❌ 能再起进程 |
| 无限循环（CPU） | ✅ 靠 timeout 兜住 |
| 无限吃内存 | ❌ 靠 timeout 兜住（内存限制根本没生效） |

⛔ 这里执行的是**合成器与模型产出的不可信代码**。最严重的一条是密钥：
env 白名单必须是**白名单**而不是黑名单 —— 黑名单拦不住没见过的名字，
`WORKBUDDY_PAC_RPC_TOKEN` 这种前缀根本猜不到。

### 修法：进程层（总是生效）+ OS 层（探测式接入）

- **进程层**（跨平台、零依赖）：env 净化（白名单 + 掐掉 `PYTHONPATH`）、
  rlimit、输出按字节封顶（写文件 + 上限读取，不用 `capture_output`）。
- **OS 层**：macOS `sandbox-exec` / Linux `bwrap`。⛔ **探测式**接入：
  本函数会先真跑一次自探测，失败就**不启用**。
  实测 macOS 上 **任何 `(deny default)` 的 Seatbelt profile 都
  `sandbox_apply: Operation not permitted`**（`allow default` 却能跑）
  ⇒ 不是"没有这个工具"，是"这个环境收紧不了"。硬依赖它会让执行层
  在部分环境直接不可用。

### ⛔ 假仪表：声明了意图 ≠ 生效了

第一版把 `rlimits` 直接写进结果，看着像"内存 1 GB 已限制"。实测
`resource.setrlimit(RLIMIT_AS, ...)` 抛
`ValueError: current limit exceeds maximum limit`，而异常被 `except` 吞掉
⇒ **内存限制根本没生效，结果里却写着 1 GB**。`RLIMIT_DATA` / `RLIMIT_RSS`
同样三个全失败。

所以现在分两个字段：`rlimits`（意图）与 `rlimits_applied`（**实测**，由
子进程在 `preexec_fn` 里写回文件）。两者不一致时一眼可见。

### ⛔ 审计探针自己不许成为 DoS

第一版探针写的是 `while True` 无限吃内存 —— 在内存限制失效的环境里它
**真的把系统内存吃光**（load 打到 9.3，后续命令全部 SIGTERM）。
现在探针自限到 512 MB，且**解析失败一律 fail-closed 判"未隔离"**
（第一版因 `repr()` 输出单引号导致 `json.loads` 失败 ⇒ payload 为空 ⇒
"吃到 0 MB" 被误判成"已拦住"，是个假绿灯）。

### 仍保留的边界（如实）

- **文件系统未隔离**：越界读/写、fork 子进程仍可能。堵住它必须靠 OS 层，
  而 OS 层在部分环境不可用 ⇒ `fs_isolated=false` 会写进每个 Verdict 的
  evidence，并在控制台与 CLI 各提示一次（环境级属性，不每次刷屏）。
- **内存没有有效限制**：只剩 timeout 兜底。

### 反证组（`tests/contract_sandbox_test.py`）

A（env 净化，含"恢复成继承 ⇒ 密钥泄漏"反证）/ B（意图 vs 实测，含
"setrlimit 全失败 ⇒ applied 不许出现 True"反证）/ C（输出封顶，含
"上限放大 ⇒ truncated 翻 False"反证）/ D（OS 层降级，含"伪造 ⇒ tier 翻转"
反证）/ E（Verdict evidence 带 sandbox）/ F（告警只一次）。
**事故版注入**：把 `build_child_env` 退回 `dict(os.environ)` ⇒ A1–A6
集体转红、探针重新报出 181 个变量与真密钥 ⇒ 确认非恒真后还原。


## 15. 只读 ≠ 无隐私：对外暴露面的四道默认闸门

`serve.py` 是**唯一对外暴露内部状态的地方**（账本全文 / harness 状态 /
记忆正文 / 失败轨迹）。它很容易被当成"一个只读小服务"而放松 ——
但只读和"可以给任何人看"是两件事。2026-10-13 逐条实测，四处全部属实：

| 项 | 旧写法 | 实际后果 |
|---|---|---|
| 端点匹配 | `path.startswith("/api/status")` | `/api/status_extra` **也命中** status |
| 静态文件 | `str(f).startswith(str(base))` | `/app/console_bak/x` 被当成 console 子路径 |
| CORS | 硬编码 `ACAO: *` | **任意网页**的 JS 能读本机账本 |
| `limit` | `int(q["limit"])` 无保护 | `?limit=abc` 直接 500 而非 400 |
| `/raw` | 整个账本 `.read_bytes()` | append-only ⇒ 内存峰值随历史线性增长 |

修法一句话：**字符串前缀比较一律换成结构化比较**。路径按分量
（`Path.relative_to`）、端点按边界（相等或以 `/` 续接）、CORS 按白名单
（且回显 origin 而不是回 `*` —— 后者既扩大暴露面又不兼容 credentials）。

### ⛔ 全绿的断言不等于有判别力

新写的 `tests/contract_serve_test.py` 第一轮全绿。按纪律把
`serve.py` **整体退回 HEAD 版本**再跑一遍（ accidents injection），结果分三类：

- **真·翻转**：A2/A3（200→404）、B1/B2（500→400）、D1/D4（`*`→无）、
  E2（200→413）。旧代码确实错，且这些断言真能抓到。
- **两边都绿**：F1（旧版两个端点的事件键集合本就相同）、F2、F3
  （`Event` 是**无 slots** 的 frozen dataclass ⇒ "处理 slots"这条是
  **前瞻守卫**，从未抓到过 bug）、G1/G2（靠 G3 的反证才立得住）。
- ⇒ 补了 **F5**：给 `_event_dict` 打标记，看两个端点是否**同时**带上。
  注入事故版（让 `/api/events` 自己写一份 `e.to_dict()`）⇒ F5 立刻转红。
  这才是"两端共用一个序列化函数"这条契约能被证伪的形态。

⛔ 别把"两边都绿"的断言当成战果：它是护栏，不是抓到的 bug。这句话写进了
测试文件的 docstring，也写进了 README 宪法第 28 条。

### 一处被我夸大的注释（改实）

`_collect` 的注释原本写着用 `deque` 是"避免内存峰值随历史线性增长"。
如实说：`consistency_report` 与 `memory_report` 都要过**全量**事件，
所以 `_collect` 的内存仍是 O(n)。真正的收益是把"三次全量读取且彼此可能
不一致"降成"一次快照喂三个消费者"。要消除线性得让那两个函数支持流式聚合
—— 那是另一件事，不在这里假装已经解决。

### 反证组

A（端点边界，含还原 `startswith` ⇒ `_extra` 命中）/ B（400 不是 500）/
C（兄弟前缀目录 + 向上穿越，含还原字符串前缀 ⇒ secret 被读出）/
D（默认不发 / 白名单回显 / Vary / 无 Origin 不发，含还原硬编码 `*`）/
E（超限 413 + 给出替代路径，靠压低 `MAX_RAW_BYTES` 触发）/
F（含 slots 退路 + **打标记看两端同源代码**）/ G（对称条件 + 非对称反证）。


## 16. 一次只读请求，账本被解析了 4.69 遍

起因是上一轮留的边界 #2：`_collect` 的内存仍是 O(n)。动手前先量化 ——
结果靶子不在"内存持有"，而在**重复劳动**。

### 先钉时间归属（差点改错地方）

第一次量 `/api/status` 是 **1.81s**，看着像 O(n) 解析。但逐段拆分加起来只有
0.16s，对不上；再跑四次得到 0.37 / 2.57 / 1.62 / 0.70s —— **`uptime` 显示
load 15.06**，单次 wall time 会被环境噪声淹没 10 倍。

⛔ **本机 load 常年 10 以上时，单次计时不能当判据。** 换成「最小耗时」+
「数 `json.loads` 次数」这类**与负载无关的结构性计数**再决策。

### 真凶：4 遍全量

数 `json.loads` 得到 6865 次 / 1465 行 = **4.69 遍**，来源四选：

| 遍 | 出处 |
|---|---|
| 1 | `Ledger.__init__._hydrate()` 全扫 —— 只为拿 `_seq` / `_last_hash` |
| 2 | `_collect` 自己的 `led.events()` |
| 3 | `stats()` 内部的 `events()` |
| 4 | `stats()` 里的 `verify_chain()` 又 `$ events()` |
| ~0.69 | Store / memory.json 的其它 JSON（与本轮无关） |

⛔ 比浪费更糟的是**正确性**：这四遍读的是四个不同时刻的快照 ⇒
`ledger.count` / `chain_ok` / `consistency` 可能基于不同长度的账本，
**同一份响应内部自相矛盾而不自知**。

### 修法

1. `verify_chain(events=None)` / `stats(events=None)`：可复用已读好的事件，
   不传时行为与改动前**逐字相同**（调用点全是零参，向后兼容）。
2. `_collect` 只读一次，把同一份快照喂给 stats / consistency / memory_report。
3. **冷启动只读尾部**：账本 append-only 且写入在独占锁内串行 ⇒ 最新事件必然
   在末尾，为了两个字段全扫一遍是纯浪费。改 `_read_tail_event()` 读末 64 KB
   并往回找第一条可解析事件。

结果 **4.69 遍 → 1.69 遍**（剩下的 ~0.69 是 Store / memory.json 的固有开销）。
⛔ 内存仍是 O(n) —— `consistency_report` 与 `memory_report` 要遍历全量，
这次消掉的是"重复读"，不是"线性增长"。

### 尾读的四条等价边界（都有测试）

| 场景 | 期望 |
|---|---|
| 末尾坏行（崩出来的半截写入） | 回退到上一条好事件，**不许**认 `*seq=999*` |
| 末 64 KB 全是不可解析内容 | 退回全扫，**不许**把缺失假装成 genesis |
| 单行超过 64 KB | 允许退化全扫，但结果必须仍正确 |
| 空文件 / 文件不存在 | 与全扫一致（都是 genesis） |

写入路径同样要验：`append()` 在锁内重算 `_hydrate()` ⇒ 它拿的也是尾读结果。
尾读错了就会造出**分叉链**。四种场景实测（`tests` 外的临时验证）：
单实例连写 50 条 / 30 次新建实例写 / 交错续写 / 垃圾尾之后续写 —— 四条链
`verify_chain()` 全部 True。

### ⛔ 又一次：亮绿的断言不等于有判别力（第二轮复发）

G 组第一轮写完就全绿。把 `ledger.py` 退回 HEAD 再跑 ⇒ **G1–G5 依然全绿**
（那时两条路径都是全扫，自然相等），只有新加的 **G0**（断言 `_read_tail_event`
存在）转红。⇒ G1–G5 是"等价性护栏"，不是"尾读已生效"的证据。

同一坑还出现在反证本身：我最早写的 G6 是"强制 `_read_tail_event` 返回 None ⇒
走全扫退路 ⇒ 结果应与全扫一致" —— 那**恒真**，它去调的就是全扫。改成"返回
第一条而不是最后一条"的 off-by-one 事故版才真的能证伪。

⛔ 判据小结：**对着旧代码跑一遍，并把"两边都绿"与"事故版不红"都标出来**。
这条纪律在 §15 已经立过，这里第二次救场。

---

## §17 一次遍历：读数不得靠"把整份事实源放进内存"换来

（2026-10-13 实测，对应宪法第 30 条）

### 起点：一次只读请求要五份读数

`/api/status` 一次要给出：**统计** / **链校验** / **一致性（账本 vs 投影）** /
**记忆对账** / **尾部事件窗口**。上一轮（§16）已经把"解析 4.69 遍"消到 1 遍，
但那 1 遍是**先把整份账本物化成 `List[Event]`** 再给五个消费者传阅。

实测（4000 条 / 5.14 MB 账本）：

| 读数 | 峰值 |
|---|---|
| 仅 `events()` 全量持有 | 8.18 MB |
| `_collect` 总计 | **8.27 MB** |

⇒ **99% 的峰值花在"持有"上**，而五个消费者**没有一个需要随机访问**。

### 改法：一次磁盘遍历，五个聚合器共享同一条流

```
led.iter_events() ─┬─ LedgerStatsAgg     (by_kind + 链校验)
                   ├─ ConsistencyAgg     (账本 vs 投影)
                   ├─ MemoryLedgerAgg    (记忆对账)
                   └─ TailWindowAgg      (deque(maxlen=limit))
```

⛔ **每个聚合器都必须与它的"非流式"入口共用同一份逻辑**：

| 流式入口 | 非流式入口 | 共用点 |
|---|---|---|
| `LedgerStatsAgg` | `stats(events=)` | 都是建 agg → feed → result |
| `ConsistencyAgg` | `consistency_report(events)` | 同上 |
| `MemoryLedgerAgg` | `memory_report(ledger_events=)` | 同上 |
| `Ledger.iter_events` | `events()` | `events()` = `list(iter_events())` |
| `_chain_feed` | `verify_chain` | 唯一的链推进逻辑 |

两处判据一旦漂移，"控制台说一致"就是假一致 —— 这是 §13 那条纪律的又一次应用。

### ⛔ 第三次：只修一半等于没修

账本侧改完，探针（当时只造账本、不造投影库）报 8.27 → 0.65 MB。但**生产配置
下有投影库**，实测：

```
_collect(生产) 峰值 7.72 MB
  其中 runs() 全量加载 5.83 MB（810 行）
```

**76% 的峰值来自 `runs()` 把每行的 `payload` 全量 `json.loads`** —— 而一致性
判据只比 `reward / passed / harness_id` 三个字段。

⇒ 加 `Store.run_digests()`：`SELECT run_id, reward, passed, harness_id`，
不碰 payload。⛔ 取值必须与 `RunRow` 逐字一致（`float(reward or 0)` /
`bool(passed)` / harness_id 原样可为 None）—— 不一致就会让 drift 判据在
"db 里是 NULL"这种边界上给出不同结论。

**同口径对照**（4000 条账本 + 1334 runs，`scripts/probe_memory_audit.py`）：

| 场景 | 改造前 | 改造后 |
|---|---|---|
| 无投影库 | 8.27 MB | **0.65 MB** |
| 有投影库（真实部署形态） | 14.24 MB | **2.70 MB** |

剩下的 2.70 MB 里，1.5 MB 是 `runs(limit=200)` —— 那是**响应体本身要吐出去
的数据**，是观测的内存下界，不是浪费。

### ⛔ 又一次：反证项会随实现翻转

上一轮 H2 的反证注入在 `Ledger.stats`（"忽略传入的 events，强制重读"）。
流式化后 `_collect` **根本不调 `stats()`**（它用 `stats_agg()`）⇒ 注入点变成
死点，**H2 恒红却查不出任何真问题**。

⇒ 反证必须打在**当前咽喉**上：现在改成给 `iter_events` 注入"多扫一遍"，
断言 H1 的阈值（< 2 遍）会被打破。

### ⛔ 又一次：战果 vs 护栏要分开标

把 `_collect` 退回物化实现（`for ev in list(led.iter_events())`）后重跑
`tests/contract_streaming_test.py`：

```
26/29 通过
FAIL D1 _collect 峰值 < 物化持有的 60%   比值=1.02
FAIL D2 _collect 峰值 < 账本字节数        6.25MB vs 3.86MB
FAIL D3 规模翻倍时峰值增长 < 2×           6.25 → 12.52 MB
```

⇒ **A/B/C/E/F/G 组 26 项是护栏**（防改坏语义），它们对旧代码也全绿；
**只有 D 组三条是真判据**。这与 §15、§16 的教训同形：绿不等于有判别力。

### ⛔ 顺带：合并遍历不得改变断链时的读数

旧 `stats()` 是**两遍**（`by_kind` 一遍 + `verify_chain` 一遍），合并成一遍后
最容易犯的错是"断链就跟着 break" ⇒ `count` / `by_kind` 只统计到断点，而断链
恰恰是最需要完整读数的时刻。

`_chain_feed` 断链后**不再推进 prev**（与旧 `verify_chain` 的 `return` 同源），
但 `stats` 不 break ⇒ `count` 仍是全量（C1/C2 守着）。

---

## §18 第二个人口：同一个洞换个入口还开着

（2026-10-13 实测，对应宪法第 28 / 30 条）

§16 修了"解析 4.69 遍"，§17 修了"把整份账本放进内存"。两轮都在 `/api/status`
上。**同一个形态的问题在别处还完整地开着** —— 这一轮把它们挖出来。

### 发现一：`/api/events` 从未被流式化（只修一半，第二次）

为了取尾部 200 条，`led.replay()` 先把 **1533 条全量解析两遍**（`events()`
一遍 + 列表推导再造一份）⇒ 峰值 **6.79 MB**。

改：`Ledger.iter_replay()`（`replay()` = `list(iter_replay(...))`，同源）+ 端点
走 `TailWindowAgg` ⇒ **6.79 → 1.19 MB（5.7×）**。

### ⛔ 关键教训：测组件 ≠ 测端点

给 `/api/events` 补完 H1–H4（等价性 / 过滤 / 内存 7.2×）后，把端点代码**退回
物化实现**再跑一遍 ⇒ **34/34 依然全绿**。

原因：H1–H4 测的是 `iter_replay` 与 `TailWindowAgg` 这两个**组件**，而事故版改
的是 `do_GET` 里那两行 —— 没有任何一条断言真的走到那条代码。

⇒ 补 **H5**：起真 HTTP 服务，monkeypatch `Ledger.replay` 计数，打一次
`/api/events`，断言 **`replay` 被调用 0 次、`iter_replay` 1 次**。这是"端点走的
是哪条路"的可证伪形式；注入事故版后 H5 立刻转红（`replay=1`）。

> 与 V5 的"有机制 ≠ 这条路被走到"同形，只是从"查 SQL 的 FROM 哪张表"变成了
> "给物化入口装计数器"。

### 发现二：`limit` 是"无上限读取"的第二个人口

§15 修了 `/api/ledger/raw` 无上限（>64 MB 就 413）。但
`/api/status?limit=N` 和 `/api/events?limit=N` 的 `N` **完全没有校验**。

最阴的是**负数**：

```
原生 SQL `LIMIT -1`   → 810 行（全表）
runs(limit=-1)        → 810 行      ← 无限制，不是 0 条
```

于是 `?limit=-1` 在**旧**实现里是：

- `events` = `[]`（`deque(maxlen=0)`）
- `runs` = **全表 810 行**

同一份响应里两个列表语义相反 —— 与 §16"同一份响应自相矛盾"完全同形，只是
这次不是时间差造成的，是**同一时刻两个入口对同一个参数的解释不同**。

### 改法：HTTP 层明确拒绝 + 数据层也夹一道

1. `_parse_limit()`（两端点共用）：非负、不超 `MAX_LIST_LIMIT=5000`，越界
   **400 并说明原因**。⛔ 不 clamp：clamp 把"危险输入"伪装成"正常响应"。
2. **深层防御**：`runs / run_ids / run_digests / snapshots` 每个 limit 入口都
   `max(0, int(limit))`。理由是 HTTP 闸能被绕过（别的调用方直接调 Store），
   而 `LIMIT -1` 的语义不会。
3. 旧断言 B3（"负 limit ⇒ 200 + 空列表"）**期望已改**为 400 —— 这是过期期望，
   不是回归；理由写在测试注释里。

### 发现三：`snapshots` 是无界的（形态上）

谱系 append-only ⇒ 不设上限时响应体与内存随历史线性增长。改为按 limit 取
**最后 N 条**，并新增 `snapshots_total` / `snapshots_truncated`。

⛔ **切了就要说切了**：不报的话"谱系只有 7 个节点"会被当成全部。控制台加了
截断提示。

⛔ **判据不受响应截断影响**：一致性走 `snapshot_ids()`（全量），H8 守着。

### 本轮实测汇总

| 项 | 改前 | 改后 |
|---|---|---|
| `/api/events` 峰值 | 6.79 MB | **1.19 MB**（5.7×） |
| `?limit=-1`（`/api/status`） | 200（`events=[]` 而 `runs`=全表） | **400 + 原因** |
| `?limit=99999` | 200（无上限） | **400** |
| `snapshots` | 全量（无界） | 有界 + `truncated` 标记 |

---

## §19 第三个人口：CLI 侧与热路径从未被数过遍数

（2026-10-13 实测，对应宪法第 30 条）

§16/§17/§18 都在 HTTP 侧（`/api/status`、`/api/events`）排查。**CLI 与热路径
从来没人去数过遍数** —— 这一轮去数了，同一个洞在两处完整保留。

### 发现一：`cmd_status` 一次问账本 3.01 遍

```
cmd_status → json.loads 4608 次 / 账本 1533 条 = 3.01 遍
```

三遍分别来自 `led.stats()`、`consistency_report(led.events())`、
`memory_report(ledger_events=led.events())`。

⛔ **比浪费更糟的是正确性**：三遍读的是三个**不同时刻**的快照 ⇒ 同一份
输出里 `chain_ok` / `missing_count` / `untraceable_count` 可能互相矛盾。
与 §16 完全同形，只是这次的成因不是"一次响应内"，而是"一次命令内"。

改：三个聚合器（`stats_agg` / `consistency_agg` / `MemoryLedgerAgg`）共享
**一次** `iter_events()`。给 `memory_report` 加了 `ledger_agg=` 流式入口
（与 `ledger_events=` 共用同一个 `MemoryLedgerAgg` ⇒ 判据仍只有一份）。

**同口径对照**（生产 1533 条 + 810 runs）：

| 项 | 改造前 | 改造后 |
|---|---|---|
| 解析遍数 | **3.01** | **1.00** |
| 峰值 | 7.93 MB | 5.67 MB |
| 常驻 | 0.77 MB | 0.77 MB |

⛔ **峰值只降 1.4× 而常驻完全不变** —— 因为峰值主体是 sqlite 连接与
import（tracemalloc 按行号定位：top1 是 `importlib._bootstrap`），**不是账本**。
⇒ 诚实结论：**这一处的战果是正确性（同一快照），不是内存**。把"遍数"当
主指标、"峰值"当次要指标，别拿 1.4× 冒充 108×。

顺带：`cmd_rebuild` 也是两遍（`events()` 一遍 + `verify_chain()` 一遍），
改成 `verify_chain(evs)`。

### 发现二：`_deadend_map` 每轮物化整份账本

它在 `run_round` 里被调用 ⇒ **每轮一次** `reversed(self.ledger.events())`，
为算 **7 个** key 的计数而物化整份账本。

```
旧 reversed(events())   峰值 6.49 MB
新 正序流式             峰值 0.06 MB   （108×）
```

### ⛔⛔ 顺带挖出一个判据层 bug（本次**故意不改**）

改造前我以为新旧语义等价，写了"总数 − 最后一次 generalize 的累计条数"，
随机对拍 **26/40 不一致**。查下去发现旧实现的真实语义是**二元的**：

```
倒序遇 generalize ⇒ out[key] = 0（覆盖此前累加值）+ done.add
⇒ 最终 = 「曾否 generalize 过」? 0 : 全部条数
```

而它的 docstring 写的是"连续（遇到一次 generalize 就归零）"。两者在
`[G, 拦, 拦]`（成功过、之后又连续被拦两次）上分歧：旧给 **0**，"连续"
语义应给 **2** ⇒ **只要一个候选曾经成功过，它就永远不会进冷却**。

⛔ 这是判据语义，不是性能问题。按纪律（改判据 ≠ 改语义）：

1. 本次**只换实现、不动语义**，保持二元值；
2. 随机对拍 **40/40 与旧实现逐 key 相等**（用 `git show` 出的旧代码当
   oracle，不许"顺手美化" oracle —— 美化了它就不再证明同值）；
3. 加 **J2** 把当前语义钉死（`[G,拦,拦] ⇒ 0`），注释写明"这是已知 bug，
   哪天要修成连续语义这条会先红，红的人必须先做「成败 × 是否命中冷却」
   交叉表"；
4. 生产 7 个 key 上新旧同值 ⇒ 当前**误伤面为 0**，但"成功过又退化"是
   极常见形态 ⇒ 修它的收益要等真出现再量化。

### 发现三：`snapshots` 全量拉回再过滤

`evolution.py` 为取**一条**快照而 `snapshots()` 全表拉回再按 `harness_id`
过滤 ⇒ 每行 payload 都 `json.loads` 一遍，每轮提交一次。改走 SQL `WHERE`
（`snapshots(harness_id=, limit=1)`）。

### 发现四：`Ledger.tail(n)` 是 `events()[-n:]`

零调用点（死代码），但它是公开 API 上的**物化陷阱**。改 `deque(maxlen=n)`。

⛔ 顺带发现一个真陷阱：`events()[-0:]` 在 Python 里返回**全部**（`-0 == 0`）
⇒ `tail(0)` 若写成旧形式会"取 0 条"变成"取全部"。K3 守着，事故版下
实测 `tail(0)=60`（全量）⇒ 与 §18 的 `LIMIT -1` 是同形态的"边界值反转"。

### ⛔ 又一次：判别力必须逐条验证

注入三处事故版后 **I2/I4/J3/J4/K2/K3 六条转红**，但 **I1 仍绿** —— 因为我
那版事故只让 stats 走物化，解析次数仍是 1 遍。补一个专门的"各读一遍"
事故版 ⇒ I1 转红（2.02 遍）。

⇒ **一条断言只对一种事故敏感**。想证明 I1 有判别力，就得造**它对应的那个**
事故，不能用别处的事故顺带证明。

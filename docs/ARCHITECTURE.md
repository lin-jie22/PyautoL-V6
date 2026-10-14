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

### ⛔⛔ 顺带挖出一个判据层 bug（第 14 轮**故意不改** ⇒ 第 17 轮已修，见下节）

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

### ⛔⛔ 上面那个 bug **已修**（2026-10-13 第 17 轮）：二元 ⇒ 连续

修的不是"值算错"，而是**充分统计量选错**：旧实现记 `total` + `ever_gen`
（曾否成功过），由这两个量只能算出二元值；而"连续"语义的充分统计量是
`streak`（当前连续被拦数）—— 正序扫一遍即可维护，**不需要重放历史**，
因此增量路径与全量路径在数学上严格等价（少了一条漂移路径）。
副作用：`ever_gen` 集合消失，缓存 state 更小。

**拍板依据是实测交叉表，不是"看起来 better"：**

| 证据 | 实测结论 |
|---|---|
| 生产账本 39 条 `gen.probe` / 7 key | **0 个** key 处在「曾成功过 + 末尾连续被拦」形态（成功过的 key 全是纯 G 或以 G 结尾）⇒ 新旧**逐键同值**，冷却决策翻转 **0** |
| 冷却阈值误伤率（连续失败 ≥k 次后下一次真 generalize） | k=3：12 次命中 / **0 次**误伤（样本小，只作方向参考） |
| 不永久封死的保证 | 靠 `deadend_probation_every`（每 5 轮强制试探一次），**不靠**上面那份小样本统计 |

⛔ 反面同样要记：样本只有 39 条，**证明不了未来零影响**，只证明当前零回归。

⛔⛔ **本轮第二个坑（比 bug 本身更值钱）：oracle 自己写错了**。
我照抄旧实现的控制流结构写了 oracle（倒序 + 遇 G 则 `out[key] = 0`），
结果 **oracle 本身就是二元的** ⇒ 对拍 **24/40 假红**。
"连续"在倒序回看里的正确写法是**遇 G 停止但保留已数到的值**（不是清零）。
⇒ **oracle 只能照抄定义，不能照抄实现结构**——照抄结构会把被测对象的
bug 一起抄进基准里，两边一起错，对拍照样绿（或一起假红）。

判别力（注入事故版实测）：累计语义 ⇒ J1/J2/J2b/J2d 红；退回二元 ⇒
J1/J2/J2b/**J2c** 红；增量路径不累加 ⇒ M5 红。

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

---

## §20 判据不得有隐性上限（"这一块"审计时补上的缺口）

（2026-10-13，对应宪法第 32 条）

§19 收尾后回头审计"读数/判据完整性这一块是否真的完整"，在**判据侧**发现
一个此前四轮都没碰到的洞 —— 它不关乎内存，而关乎**判据会不会悄悄少看**。

### 洞：`run_digests()` / `run_ids()` 默认 `limit=100000`

```python
def run_ids(self, limit: int = 100000) -> List[str]:
    #: ⛔ 同上：`LIMIT -1` == 无限制。判据调用方**不传** limit 即可全量。
```

**注释与实现矛盾**：注释说"不传 = 全量"，实现是不传 = 前 10 万行。
而一致性判据（`ConsistencyAgg.__init__`）正是**不传**调用的。

超过 10 万行后，溢出部分不进比对，**两个方向同时失真**：

| 被截断的东西 | 管什么 | 失真方向 |
|---|---|---|
| `run_ids`（键集合） | 键级：账本有 / 投影有没有 | 投影里有的被算成 missing ⇒ **假红** |
| `run_digests`（三列） | 字段级：reward/passed/harness_id 是否漂移 | 那些行根本不比 ⇒ **假绿** |

⛔ **假绿比假红危险**：假红会让人去 rebuild（浪费但无害）；假绿会让
"投影被改过"这件事永远查不出来。L3/L4 两条反证分别钉死这两个方向
（L4 造一处真 drift：投影 reward 改成 0.9，截断后判据照样报 `ok=True`）。

改：默认 `None` = 真全量（`limit is None` 时不加 `LIMIT`），并修正注释。

### ⛔ 关键教训：断言"调用时传了什么"是恒真断言

第一版 L2 给 `run_digests` 装 spy，断言"判据调用时不传上限（记录到 None）"。
注入"默认改回 100000"的事故版后 —— **L 组 5 条全绿**。

原因：调用方本来就不传 limit，spy 永远记到 `None` ⇒ 断言恒真。
**事故改的是"默认值"，而断言看的是"实参"** —— 两者根本不是同一个东西。

⇒ 想证明"默认没有上限"，就得去看**签名默认值本身**：

```python
inspect.signature(Store.run_digests).parameters["limit"].default is None
```

事故版下立刻转红。这与 V5"断言键不存在"（`success` 键）是同形错误：
**断言打在了事故的旁边，不是事故上。**

### 判别力清单（本轮 L 组，6 条）

| 断言 | 事故版（默认改回 100000） | 定性 |
|---|---|---|
| L1 默认调用返回全量 | 绿（5 行 ≪ 10 万） | 护栏 |
| L1b 显式 limit 仍生效 | 绿 | 护栏 |
| **L2 签名默认值 is None** | **红** | **真判据** |
| L2b 判据调用时不自带上限 | 绿 | 护栏 |
| L3 反证·假红 | 绿 | 反证（证明洞有害） |
| L4 反证·假绿 | 绿 | 反证（证明洞有害） |

⛔ 又一次印证 §17/§18 的教训：**绿不等于有判别力**，只有 L2 一条会红。

### 顺带：TS 监督器侧仍是"整文件物化"（未修，形态记录）

`supervisor/src/ledger.ts:92` 是 `readFileSync(path, "utf8")` + `split("\n")`
+ 逐行 `JSON.parse` ⇒ 一次性物化整份账本。实测（1.54 MB / 1533 条）：

```
readFileSync 整文件字符串   +3.11 MB
split 成行数组             +0.10 MB
逐行 JSON.parse 成对象      +2.17 MB
⇒ 合计 5.38 MB（账本的 3.5×）
```

⛔ 但它**只读一遍**（`index.ts:40` 一次 `readLedger`，`verifyChain` 与
`detect` 共用同一份 `events`）⇒ 没有 §16/§19 那种"多遍读不同快照"的正确性
问题，只是成本结构问题。监督器是**按需运行的独立进程**，当前规模不构成
问题；账本涨到 100 MB 量级时会到 350 MB ⇒ 那时再改流式行读取。

## §21 三处收口：死路账增量 / 监督器流式 / 沙箱隔离

§20 之后剩下的三处边界，本轮一起收口。三处的共同点：**都不是"功能没写"，
而是"写了但只在部分条件下成立"**。

### 21.1 死路账从"每轮全扫"改成"只读新增"（+ 失效必须能证）

`_deadend_map` 上一轮已经流式化，但仍**每轮解析全账本** ⇒ O(轮 × 账本)。
改：缓存 `(offset, head_hash, streak)`，只解析新增部分。
（⛔ 第 17 轮起缓存的是 `streak` —— 连续语义的充分统计量；不再是
`total` + `ever_gen`。理由见上节。）

⛔ 增量**不是**"相信文件只会追加"，而是**能证明前面没变**：

| 检查 | 抓什么 | 失效时 |
|---|---|---|
| offset 只停在行边界 | 并发 append 写半行 | 重读一条（不错位） |
| 新增首条 `prev_hash` == 缓存链头 | 中间被插入 / rebuild | 回退全量 |
| 文件变小 ⇒ 偏移越界 | truncate | 回退全量 |
| 无新行但文件指纹变了 | rebuild 后长度恰好没变 | 回退全量（fail-closed） |

⛔⛔ **踩到的坑：文本模式下 `for line in f` 里调 `f.tell()` 直接抛
`OSError: telling position disabled by next() call`**。第一版用
`except: pass` 兜住 ⇒ 位置永远没被记录（停在 0）⇒ 每次续读都从头开始，
而 `stats` 里 `full=6 / incremental=0` 一眼看穿。**把失败吞掉就是把降级
伪装成成功**（V5 同款病）。改二进制读 + 不再吞异常。

实测（6 轮 × 40 条）：扫描条数 **240**（= 账本总量）而不是 1440。
失效路径（rebuild / truncate / touch）全部回退全量且与 oracle 同值。

⛔ **判别力是注入事故版量出来的，不是我以为的**：真判据只有 M1/M2b/M6；
M2 在"去掉链校验"下**值仍同值**（rebuild 前后 payload 一样），真正抓到
"抱着旧缓存"的是 M2b（失效必须记账）；M4 被两条兜底路径救回来，注入
"offset 记在解析前"也照样绿 ⇒ 是护栏不是判据。

### 21.2 TS 监督器改流式行读取

`readLedger` 改为分块读 + `StringDecoder`。同口径（生产账本 1.54 MB /
1533 条）堆增量 **7.75 MB → 4.58 MB（-41%）**。

⛔ 两个必须处理的坑：**跨块多字节字符**（块边界落在中文字符中间 ⇒
直接 `toString("utf8")` 会变成 U+FFFD ⇒ hash 校验把好事件判成"被篡改"）、
**跨块行**（残留留到下一块）。

⛔ 顺带修了一处**注释与实现矛盾**：旧注释写"坏行计入统计，由调用方决定
是否告警"，代码里却只有 `continue` —— 统计根本不存在。现在 `ReadStats`
真的记 `bad`，`readLedger(path, stats?)` 可选传入。

⛔ **剩下的 3.5 MB 是 `detect` 必须持有的 `Event[]`**（实测不 collect 时
只读 1.15 MB）。`detect` 有 7 个检测项、且要"用 probe 找它之后的 commit"
⇒ 改单遍聚合器等于**重写跨语言判据逻辑**。判据漂移的代价（假绿）远大于
3.5 MB ⇒ **不做这个交易**，如实记在这里。

### 21.3 沙箱：audit hook 层 + 内存的两条替代路线

⛔⛔ **内存 rlimit 在本机是死路**：实测 macOS 上 `RLIMIT_AS` /
`RLIMIT_DATA` / `RLIMIT_RSS` **三条全部**抛
`ValueError: current limit exceeds maximum limit`（即使宿主 hard limit 是
无限，收紧也被拒）。⇒ 换两条路线，都实测有效：

1. **进程内 RSS 守卫**：runner 里起守护线程看 `ru_maxrss`，超限 `os._exit(137)`
   （⛔ `ru_maxrss` 单位跨平台不同：macOS 字节 / Linux KB）。实测 0.54s 拦住。
2. **父进程看门狗**：`ps -o rss= -p <pid>` 轮询，超限 kill。它覆盖"进程内
   守卫线程没机会跑"的情况（例如 C 扩展不释放 GIL）。`ps` 不可用 ⇒ 记账
   `usable=False`，**不假装装上了**。

**文件系统隔离**加了 audit hook 层（`sys.addaudithook`）：白名单 = 工作目录
+ `sys.prefix`/`exec_prefix`（⛔ 少了它，冷 `import math` 都会被拒 ——
装了 hook 之后 import 也要读 .py 文件）+ `/dev/null`、`/dev/urandom`；
拦截越界 `open`/`os.open`/`os.remove`/… 与 `subprocess.Popen`/`socket.connect`。
**探测式接入**：跑同一份 runner 的 `--selftest` 分支（与真实执行同源，
不写第二份探测代码），失败就降级并记账。

实测：越界读 `/etc/hosts` 被拒、越界写 `/tmp` **文件真的没被创建**、
冷 import 零误伤、正常代码零误伤。

⛔⛔ **拒绝事件必须与"代码有 bug"分开记账**（`sandbox.denied`）：混在一起
的话，自演化会把"被沙箱拦下"当成"这段代码有 bug"去修一个不存在的 bug
（V5 同款病：静默降级 ⇒ 假信号）。

⛔ 字段也拆了：`os_isolated`（OS 层是否生效）与 `fs_isolated`（能力：OS 层
或 audit 层任一生效）不再混为一谈 —— 原来 `fs_isolated == (tier=="seatbelt")`
的断言在加了 audit 层之后会**把真生效的隔离判成谎报**，已改成"OS 层管字段
一致、`fs_isolated` 管行为实测"（D2/D2b）。

⛔ **诚实边界**：audit hook 拦得住 Python 层的 `open`，拦不住 ctypes /
原生代码直接系统调用。想堵住仍需 OS 层，而 OS 层在部分环境用不了。

---

## 22. 跨语言判据同源：canonical 序列化的四类漂移（2026-10-13 第 18 轮）

监督器的全部价值在于**独立于 Python 实现**重算哈希链。但"独立"的前提是
`canonical_json` 在两端逐字节一致 —— 而 Python `json.dumps` 与 JS
`JSON.stringify` 的 **number → string 与键序规则并不相同**。改造前实测
**4 类形态全部 drift**，且 drift 方向是最坏的一种：

> 监督器把**完好的账本**判成 `payload 被篡改` ⇒ 假红，而真因是语言差异，
> 排查时会一路查到"是不是有人改了账本"。

| 形态 | Python | JS（改造前） | 触发条件 |
|---|---|---|---|
| 指数补零 | `1e-07` | `1e-7` | **任何 1e-7 量级的 reward / ε / 概率** |
| 指数阈值 | `1e-05` | `0.00001` | `1e-6 ≤ \|x\| < 1e-4` |
| 整数值大 float | `int(1e21)` ⇒ 22 位定点 | `1e+21` | `\|x\| ≥ 1e21` |
| 非 BMP 键序 | 按**码位**：`�` < `😀` | 按 **UTF-16 码元**：`😀` < `�` | emoji / U+20000+ 键名 |

⛔ **生产账本（1533 条 / 994 个 float）四类形态全部 0 命中** ⇒ 目前没有假红，
但这是**潜伏地雷**，不是已爆的雷：一旦某个小 float 进账本就立刻假红。
（这也是"合成用例永远撞不到"的又一条：合成 ts 都是整数。）

### 修法

**TS 侧**：`canonicalJson` 不再用 `JSON.stringify`（它不允许自定义 number
序列化），改手写递归序列化器 `ser()`：

- `fmtNumber()`：整数值 ⇒ `BigInt(x).toString()`（定点，含 1e308 的 309 位）；
  非整数且 `|x| < 1e-4` ⇒ `toExponential()` + 指数补到 2 位；其余定点。
  ⛔ 简化依据：`|x| ≥ 2^53` 的 float **必为整数值**（精度所限）⇒ 走 BigInt 分支；
  于是"非整数 float"必 `< 9e15 < 1e16` ⇒ **只有 `< 1e-4` 才需要指数形式**。
- `cmpCodePoint()`：键排序按码位（`Array.from` 切分后逐码位比），不用 `.sort()`。

**Python 侧**：`|int| > 2^53` ⇒ `canonical_json` **抛 `ValueError`**（fail-closed）。
这是 JS 的语言限制（`JSON.parse` 直接丢精度），TS 侧无法补救 ⇒ 只能在写入侧拦。
⛔ 只拦**原始 int**：整数值的 float（`1e308` ⇒ 309 位）不拦 —— 它本来就是某个
float 的精确十进制展开，TS 侧 `BigInt(x).toString()` 能精确往返。

### 验证

| 证据 | 结果 |
|---|---|
| 随机 float 对拍 | **3014 / 3014** 逐字一致（含 1e-308、1e308、整数值 309 位） |
| 边界形态逐条对拍 | 7 条（emoji 键 / 嵌套 emoji / 1e-7 / 1e-5 / 1e21 / 1e308 / -1e-7）全一致 |
| **生产账本整链** | TS 侧 `chain_ok=true`（1533 条）—— **hash 未变，零回归** |
| fail-closed | 大整数 / 纳秒时间戳（1.79e18）被拦，且**抛错时不写入**（账本条数不变） |

⛔ 判别力靠注入事故版实测：指数不补零 ⇒ A11×3 + A13 红；键序退回 UTF-16 ⇒
A11 + A14 红；整数值不用 BigInt ⇒ A11×2 + A13 红；指数阈值改 1e-6 ⇒ A11 + A13 红；
关闭 fail-closed ⇒ A12 红。

⛔⛔ **踩到的坑（写测试的人自己错了两次）**：
① 对拍基准里 `{"v": repr(rnd()), ...}` 又调了一次 `rnd()` ⇒ v 与 cj 是两个
不同的随机数 ⇒ **600/600 假红**，看起来像"实现全错"。
② 内嵌 Python 代码的注释里写了**反引号** ⇒ 终止外层 TS 模板字符串 ⇒
报 `ERR_INVALID_TYPESCRIPT_SYNTAX`，指向八竿子打不着的行。
⇒ **造对拍基准时，"生成值"和"序列化值"必须是同一个值**；跨语言内嵌代码
里的注释要当成"字符串内的字符串"来小心。

---

## 23. 通用接入层（UCI）：任何 agent / skill 都挂得上、叫得动（2026-10-13 第 19 轮）

### 改造前的真实形态（不是"缺个接口"，是根本没有接口）

    Harness.skills: Dict[str, str]        # 技能是**字符串**
    agent.py 的 if/elif strategy == ...   # 执行逻辑写死在分支里
    runner(h, spec) -> Verdict            # agent 的约定**没有任何地方声明**

⇒ 加一个技能 = 改 `agent.py` 源码；加一个 agent = 手写一个恰好长成那个形状
的函数。外部的东西（别人项目的函数、CLI 工具、HTTP 服务、MCP server）
**根本没有入口**：既不能声明自己能干什么，也不能被路由到，更不能被调用。
进化循环因此只能进化"它自己已经认识的那几个字符串"。

### 结构

    spec      声明：我是谁 / 我要什么 / 我给什么 / 我需要什么权限
    adapter   适配：callable · 对象(run/invoke/execute) · CLI · HTTP
    registry  注册：内容寻址 fingerprint + 冲突检测 + 可导出清单
    gateway   调用：校验 → 预算 → 执行 → 输出校验 → 判据 → 记账
    manifest  装载：JSON 清单，接外部能力**不用改 Python 代码**

| 形态 | transport | timeout_enforced | 约束 |
|---|---|---|---|
| callable / 对象 | inproc | **`none`** | 只准 `trusted=True` |
| CLI（stdin/stdout JSON） | subprocess | `forced` | env 净化、`shell=False` |
| HTTP（POST JSON） | network | `forced` | 响应按字节封顶 |

⛔ `timeout_enforced` 必须**如实**：inproc 杀不掉一个跑飞的实现，写 `forced`
就是假仪表。所以**不可信实现不许 inproc** —— 注册时直接拒（D1/D7 钉住）。
实测：CLI 不传 timeout 时同一个用例从 0.4s 变成 5.03s 才返回（事故 7）⇒
证明 `forced` 不是声称。

### 三条宪法级约束

**1. 调用结果不产 reward。** `InvocationResult` **故意没有** reward 字段，
只有 `checks`；reward 只由 `Verdict` 产生。于是"外部自称 ok / self_reported"
在结构上**没有通路**（不是被过滤掉，是不存在）。C4 钉这条。

**2. 失败必须能分诊。**

| status | 含义 | 反例（不许这么做） |
|---|---|---|
| `unavailable` | 未注册 / 适配器不可用 | 返回 ok + 空输出 |
| `rejected` | 输入不合规 | 尽力调一把 |
| `failed` | 实现抛错 / 声明的输出没兑现 | 缺字段也判 ok |
| `timeout` | 真的被杀了 | 跑完才报超时 |

混成一类 ⇒ 调用方分不清"没调用成功"和"调用成功但没用" —— 那是假成功的温床。

**3. 端点只接受能力引用。** `/api/invoke` 的请求体里出现 `impl` / `argv` /
`code` / `transport` 一律 400：否则这个端点就是"远程执行任意命令"，能力清单
沦为摆设（F5 钉住）。接外部 Python 对象（`transport: python`）等同任意代码
进本进程 ⇒ 清单必须显式 `trusted: true`，默认 false。

### 与 harness 打通

`bind_capability(h, cid, route_from=...)` 把能力写进 `h.skills`（键为
`uci:<id>`）并加 L1 路由 ⇒ `harness_id` 随之改变（技能层真的进了基因组，
不是旁路）。执行时外部技能给的源码**照样**要过 `verify_source` —— 判据只来自
执行，技能自称修好了不算（E2 实测 reward=1.0 来自环境判据）。
⛔ 没挂网关时**显式记"技能不可达"**（step ok=False、reward 0），不静默退化
成"无策略"（E4）。

### 验证

| 证据 | 结果 |
|---|---|
| 四形态接入 | callable / 对象 / CLI / HTTP（真实本机服务）全 ok |
| 失败分诊 | unavailable / rejected / failed / timeout 各归其位 |
| 判据 | 有 checker ⇒ verified；无 checker ⇒ `verified=False` + 显性 reason |
| 自述 | `{"ok":true,"self_reported":true}` ⇒ **不产生 Verdict**（C4） |
| 端点 | 携带 `argv`/`impl`/`code` ⇒ 400；body 超限 ⇒ 413；未装载 ⇒ 503 |
| 事故注入 | **8/8 全部转红**（输入校验 / 输出校验 / 未注册 / adapt 降级 / verified / trusted / timeout / 端点） |

⛔⛔ **本轮踩到的坑：空容器在布尔上下文里是 falsy**。
`Registry` 定义了 `__len__`，于是 `Gateway(registry or _default_registry())`
在传入**空注册表**时静默换成默认空表 ⇒ 冒烟时 **8/8 全 `unavailable`**，
而报错信息写的是"已注册 0 个" —— 极易误判成"注册没生效"。
⇒ 凡是有 `__len__` / `__bool__` 的对象，**一律显式写 `is not None`**。

⛔ 第二个坑：`fingerprint` **故意不含 summary**（改一句描述 ≠ 能力变了，
否则改错别字都让指纹漂移）。我第一版用 summary 造差异写 D2 ⇒ 指纹相同 ⇒
"没拦住"的假红。现已补 D2b 把这条设计判断钉住。

### 仍保留的边界

1. inproc 形态**没有真正的超时**（Python 杀不掉线程）⇒ 只给可信实现；
   要真超时请走 CLI / HTTP。这是能力边界，不是待办。
2. 沙箱：接入层复用的仍是 `environment` 的进程沙箱；`trusted=True` 的
   inproc 能力**不受沙箱约束**（它在宿主进程里跑）—— 这是 `trusted` 的语义。
3. 清单里的 `python` 形态 = 任意代码进本进程 ⇒ 信任边界是"写清单的人"。

---

## 24. 巩固链：参数与接口契约（2026-10-14 第 23 轮）

这一节是**接口对齐**，不含论证（论证见宪法第 39 / 40 条）。

### 24.1 `MemoryOS` 侧参数

| 签名 | 变更 | 说明 |
|---|---|---|
| `consolidate(*, max_age_s=86400.0, min_access=2, summarizer=None, min_group=2, now=None)` | **新增 `min_group`** | 一组少于 `min_group` 条不归纳（1 条归纳不出"模式"）。其余参数语义不变 |
| `summarizer` 的契约 | **改了** | 旧：`Callable[[List[MemNote]], str]`，但被**逐条**调用（`summ([n])`）。新：**整组传入**（`summ(grp)`），必须返回"跨条"文本。⛔ 传一个逐条复制的函数 ⇒ 产出恒 0（E1 钉住） |
| `consolidation_report()` | **新增** | 返回上一次巩固的分账 dict；没跑过返回全零表 + `note="not_run"`，**绝不返回 None** |
| `dedup_tags()` | **新增** | 幂等，返回被修改条数。装载时自动调一次（`load()` 里），报告记 `tags_deduped` |

返回值：`consolidate` 仍返回 `List[MemNote]`（向后兼容），**分账不在这里**，
必须读 `consolidation_report()`。

### 24.2 分账字段（`consolidation_report()`）

| 字段 | 含义 | 处置不同在哪 |
|---|---|---|
| `pool` | 进入候选的 L4 条数 | `0` ⇒ 根本没有素材 |
| `groups` | 分出的组数 | — |
| `made` | 真写出新知识的条数 | 这才是"巩固成功" |
| `deduped` | 同样的归纳已存在 | **正确结果**：没有新信息 ⇒ 不该产出 |
| `skipped_below_min_group` | 组内不足 `min_group` 条 | 素材不够 |
| `skipped_not_isomorphic` | 槽位数不同 **或** 文字骨架不同 | 不是同一个模板 ⇒ 拒绝，不编造 |
| `skipped_empty` / `rejected` | 摘要为空 / 写入被拒 | — |
| `trigger` | CLI 侧附加 | `stagnant` / `all_committed` |
| `idle_rounds` / `uncommitted_rounds` | CLI 侧附加 | 让"为什么触发"可读 |

⛔⛔ `made` 绝不能单独使用。只看一个数字的话，"没有新信息"与"机制坏了"
在输出里长得一模一样 —— 这正是原 bug 藏了两轮的原因。

### 24.3 L4 写入契约（CLI → 巩固的**输入**契约）

CLI 每轮写一条 L4，`source=f"round{n}"`，内容**必须**是同一套模板，且
**数字个数固定**（否则归纳器判不同构 ⇒ 拒收）：

```
第 {round_no} 轮进化：基线 reward={base:.2f}，候选 {n} 个，被拒 {rej} 个，
结果={提交|回滚}，裁决 {verdict}，易层增益 {easy:+.2f}，难层增益 {hard:+.2f}，
空转 {0|1}
```

⛔ 三条硬约束：
1. **不许把 hash / id 写进内容** —— 十六进制 hash 里的数字个数随 hash 浮动
   （实测 3~7 槽），会让同模板的记录互相判成不同构。要带 id 请放 `source`
   或 `keywords`，不要放 `content`。
2. **数字必须恒定存在**：`%+.2f` 保证增益位哪怕为 0 也是一个数字。写成
   `"增益 {x}"` 而 `x` 有时为空 ⇒ 槽位数漂移 ⇒ 拒收。
3. `verdict` 取自受控词表（`generalize` / `overfit` / `inconclusive` /
   `out_of_reach` / `none`），不是自由文本 —— 自由文本会打碎骨架。

### 24.4 触发条件契约

```
触发 ⟺ 存在未提交的轮次（uncommitted_rounds > 0）
```

⛔ 不是 `idle`。`idle` 只在"压根没事可做"时为真，而
`no_candidate_passed`（**有候选、全被闸拒**）恰恰是最该沉淀的场景 ——
实测三轮 `idle_rounds=0`，用 `idle` 当触发会让修复好的机制一次都不跑。

### 24.5 兼容性

- `consolidate` 返回值类型不变；`memory_consolidated: int` **保留**
  （= `made`），新增 `memory_consolidation: dict` 承载分账。
- 旧格式 L4 不会被删，只会被判不同构而 `skip`（有账）。要让它们重新可归纳，
  只能靠新格式积累 —— 不为历史数据放宽判据。

---

## 25. 产物增殖闸：接口与接线（2026-10-14 第 23 轮 · D）

`MemoryOS.prune()` 一直有实现，但**全仓库零调用点** ⇒ 这条闸是装饰。接线时
的关键判断：**剪枝不可逆**，所以"闸被接上"的判据不是"会自动清理"，而是
**增殖这件事第一次可读**。

### 25.1 三条路径

| 路径 | 调用 | 行为 |
|---|---|---|
| `evolve` 结束 | `prune_dryrun()` | 只把"将剪 N 条"写进输出 `memory_prune`。**不删** |
| `aether.cli memory prune` | `prune_dryrun()` | 同上（安全默认），并提示加 `--apply` |
| `aether.cli memory prune --apply` | `prune()` | 真删，**逐条往账本 append `memory.pruned`** |

⛔ 自动路径一律不删。该不该剪由人决定 —— 与"涉及生产语义的变更保留人工
审批节点"是同一条纪律。

### 25.2 判据唯一事实源

`_select_prune_victims()` 是**唯一**的选人逻辑，`prune()` 与 `prune_dryrun()`
都调它。⛔ 两份判据的后果：`memory prune` 报"要剪 5 条"、真剪剪了 7 条，
而没人能说清差在哪（B3 钉住）。

| 原因 | 条件 |
|---|---|
| `low_trust` | `trust < min_trust` |
| `stale_unused` | `now - last_access > stale_s` **且** `access_count == 0` |
| `over_capacity` | 超出 `max_notes`，按 `(access_count, last_access)` 升序剪 |

⛔ L0 / L1（元规则与路由索引）**永不剪**，哪怕 `trust=0` 且 900 天没访问（A2）。

### 25.3 留痕契约

剪枝不可逆 ⇒ 每条删除都 `append("memory.pruned", {...})`，payload 含
`note_id` / `tier` / `trust` / `access_count` / `age_s` / `content_hash`。

⛔ 没挂账本时**照删但点亮 `_ledger_error`**，CLI 见到它就打印 ❌ 并返回 1 ——
不留痕的删除是数据事故，不是"删成功就完事"（C4 / E2）。

### 25.4 CLI 参数

```
memory prune [--apply] [--max-notes N] [--min-trust F] [--stale-days D]
```

默认 `max-notes=10000`、`min-trust=0.05`、`stale-days=30`。

### 25.5 为什么契约里有 G 组（端点测试）

只测 `MemoryOS.prune` 的话，CLI 那行根本没被调用也能全绿 —— 与 §18
"测组件 ≠ 测端点"是同一个洞。G 组走**真 CLI**（`--root` 指向临时目录），
断言 dry-run 落盘条数不变、`--apply` 真删了且 L0 保住。

---

## 26. 路由链：接口契约（2026-10-14 第 24 轮）

`L1.routing` 的值从「单技能名」升级为「**有序技能链**」。这是能力**可叠加**
的前提，不是语法糖。

### 26.1 值的三种写法（唯一解释处：`genome.parse_route`）

| 写法 | 解释 | 用途 |
|---|---|---|
| `"fix_control_flow"` | `("fix_control_flow",)` | 既有 harness / 账本，原样兼容 |
| `"a\|b"` | `("a", "b")` | LLM 友好（value 只能是标量，不能输出数组） |
| `["a", "b"]` | `("a", "b")` | 结构化（模板提议器用） |

⛔ **存储**形态刻意不统一转 list（`_route_value`）：单元素仍存成 `str`，
否则既有 harness_id、账本记录、断言一起漂。归一化只在**读取侧**做。

### 26.2 `apply_gene` 的 routing 分支

| op | 行为 |
|---|---|
| `set` | 整条替换（**会顶掉旧项** —— 保留它，因为"覆盖"仍是合法意图，且契约 B4 用它当反证） |
| `append` | **链尾追加**，已存在则跳过（幂等） ⇒ 能力叠加 |
| `remove` | 只摘点名那一项，其余保留（不连坐）；摘空则整键消失 |

### 26.3 执行语义（agent）

```
plan = 链上「已装载 且 未被 L0 禁用」的项（保序）
fixed = src
for s in plan:  fixed, applicable = _apply_one(s, fixed, ...)
verify_source(fixed)      # ⛔ 全程只跑这一次
```

⛔ 不是「依次尝试直到通过」。后者要为每项各跑一次执行判据 ⇒ reward 变成
"试出来的"，违反「reward 只来自一次执行」，且成本随链长线性增长（观测
改变了被测成本）。

### 26.4 归因字段（`Verdict.evidence`）

| 字段 | 含义 |
|---|---|
| `strategy` | `"\|".join(plan)`（单元素时等于原名，向后兼容） |
| `chain` | 完整链（含被跳过/被禁的项） |
| `applied` | **真的改了源码**的项 |

⛔ 没有 `applied`，"链修好了这道题"就说不清是谁修的。

### 26.5 提议器契约

`TemplateProposer` 在 `missing_skill` 时：
- 新技能用 **op=append**（不是 set）；
- 已在链上的技能**不再重复提**（否则提议器原地踏步，看起来在动实际没动）。

### 26.6 写入点统一（2026-10-14 收口）

上一轮留下的边界：`interop.bind_capability()` 用 `routing[kind] = key`
**覆盖**整条链，而 `apply_gene` 的 append 分支是**追加** ⇒ 同一个 harness
"能不能叠加"取决于技能是提议器加的还是外部绑的。

修法不是"把那行也改成追加"，而是**收敛写入点**：

    genome.route_with(routing, kind, value, *, append=False)

- `apply_gene` 的 routing 分支改为调用它（语义逐字不变）；
- `bind_capability` 改为调用它，新增 `append: bool = True`；
- 契约 `contract_route_chain_test` G 组同时盯住两条路径：
  **写出的路由值必须逐字相同 + 幂等 + 顺序无关**（G1–G4b）。

⛔ 顺带记录一个断言设计教训：G4 初版把"路由值相同"和
"`harness_id` 相同"捆成一条断言 ⇒ 假红。`bind_capability` 还会把能力
写进 `skills`（绑定 = 声明"这个 harness 有这个技能"），而路由基因
只改路由 ⇒ 两者本来就不该同 `harness_id`。把两个语义不同的判据捆成
一条，等于又造了一对分不开的孪生故障。

---

## 27. 能力池的三个正交维度

### 27.1 三个通用修法（互不越界）

| 维度 | 修法函数 | 只动 |
|---|---|---|
| 比较/运算 | `revert_control_flow` | `Compare.ops` / `BinOp.op` |
| 数值常量 | `revert_literals` | 数值 `Constant.value` |
| 布尔运算 | `revert_boolops` | `BoolOp.op`（`and`↔`or`） |

⛔ 后两者**刻意不进** `GENERIC_REPAIRS`：难度由"通用修法（= 保守控制流
修法）能否修好"定义，把它们加进那个集合 ⇒ 常量/布尔题从 hard 变 easy
⇒ 难度跨度消失、泛化闸退化。它们只作为**技能**进 proposer 池，
泛不泛化交给 held-out 闸在执行中判。

### 27.2 正交性是可判定的（3×3 交叉矩阵）

`scripts/probe_boolop_dimension.py` 跑 变异类型 × 修法 的全矩阵，要求
**对角线全 1.0（每维修得好自己那类题）+ 非对角线全 0.0（不越界）**。
实测 4 个环境全绿；`stateflow` 的 boolop 行 `n=0`（该环境源码里没有
`and/or` ⇒ 无题可考，不是修不好）⇒ 统计对角线时**只算 n>0 的格子**。

### 27.3 变异深度 `depth`（抬天花板）

单点变异题：任一对口单技能就能修好 ⇒ "链能叠加"在题面上**测不出来**
⇒ 这就是天花板。开 `depth=2`（双点变异）后，只有链能修好
（实测 `flow=0 / lit=0 / chain=1.0`）。

- 默认 `depth=1` **与既有基线同口径**（任务集不变 ⇒ 历史账本可比）；
- `--depth 2` 是**新实验**，必须单独跑、单独报，不与默认口径混在一张表里。

⛔ 同理 `--kinds`：默认三种不变，加 `boolop` / `unaryop` 需显式传。
   `which` 是全局遍历序编号，默认行为任何改动都会让历史账本不可比。

### 27.4 变异器与修法的**选点口径必须同源**

`environment.numeric_literals(tree)` 是数值字面量的唯一收集器，
变异器选点与修法对齐都用它。

⛔⛔ 这是修一个**自洽性 bug** 后的产物：变异器用
    `isinstance(v, (int, float))` 选点（`bool` 是 `int` 子类 ⇒
    `False` 会被 `+1` 成 `1`），修法收集器却**排除 bool**
    ⇒ 两边序列长度不同 ⇒ `min(len)` 对齐后**整体错位** ⇒
    把本来正确的常量改坏。这比"修不好"更糟：它污染了没坏的地方。
    改修法侧、不动变异器（动后者会缩小任务空间并改变 `which` 语义）。

### 27.5 错位防线（fail-closed）

三个修法在 `len(broken) != len(base)` 时**一律原样返回**，不再
`min(len)` 部分对齐 —— 错位一次污染一片，没有"部分修"这种折中。

---

## 28. 跨轮退化检测（aging）与轨迹记账接线（2026-10-14 · r26）

### 28.1 为什么需要它：闸是横截面，老化是纵向

已有的 `nongression.NonRegressionGate` 每次只回答一件事：
**这个变体相对冻结基线有没有变坏**。它是**准入闸**。

它看不到的是：一个已经放行的系统，在第 1 轮到第 N 轮之间，
**是不是在自己往下掉**。本项目实测过这个形态 —— 装一个新技能把旧的顶掉
⇒ easy 层从 +1.0 掉到 −1.0，而那一轮的非回归闸**照样是绿的**
（因为回归比的是冻结基线，不是上一轮的自己）。

ALE（Agent Lifespan Engineering, 2026）研究的正是后者：压缩、干扰、修订、
维护四种机制会造成长期退化，**行为测试看起来正常，准确率却已衰减**。

⛔ 两者**必须正交**，不能合进一个闸：合一之后，一个"放行但正在退化"
的系统会同时骗过准入与寿命两件事。

### 28.2 四类信号

`aether/aging.py` 的 `detect_aging(traces, eps)`：

| 信号 | 事件（这一轮发生了什么） | 判定 |
|---|---|---|
| `compression` | `consolidated > 0` | 巩固后探针下降 |
| `interference` | `skill_count` 增加 | 装新技能后旧任务下降 |
| `maintenance` | `pruned > 0` | 剪枝后探针下降 |
| `revision` | `caliber` 变更 | **口径一变，纵向 Δ 就不可比** ⇒ 直接报 |

### 28.3 四条纪律

1. ⛔ **探针集必须跨轮完全一致**。集合一变，Δ 是"换尺子"不是"变弱"
   ⇒ 整体 `comparable=False`（fail-closed），不判健康。
2. ⛔ **只看下降，不看上升**。上升是进步。把上升也计入 healthy 会造出
   一个"涨了就永远绿"的闸。
3. ⛔⛔ **事件没发生过 ⇒ 该类 `available=False`**，单列进 `undetermined`。
   **它不是通过** —— "这 2 轮没做过巩固"不等于"巩固没退化"。
   这是全项目最贵的一条：不可判别 ≠ 通过。
4. ⛔ **证据是逐条探针的 Δ，不是聚合分**。聚合分会把"一升一降"
   抹平成"没变化"，而那正是 ALE 点名的**干扰指纹**。

### 28.4 轨迹记账（masking）终于接上了主流程

兑现审计实测：`aether/runtime.py` 此前**从未**被生产链路 import，
`Trajectory` 只在 demo 脚本里被手工构造过 ⇒ `masking_ratio` 在生产上是空账。

现在 `HarnessAgent.run()` 在返回前用 `trajectory_from_steps()` 把层动作
序列折算成轨迹账，挂到 `AgentResult.trajectory`，并在 `to_dict()` 暴露
`{model_tokens, env_tokens, masking_ratio}`。

⛔ 折算放在**返回前一次性做**，而不是每步各记一次：两个地方记同一份事实，
迟早漂移（V5：必有一个是假的）。steps 是唯一事实源，Trajectory 是投影。

⛔ kind **只做映射不新造**：`_STEP_KIND` 把 (layer, action) 映到
`runtime.MODEL_KINDS` / `ENV_KINDS` 里已有的值；未知 ⇒ 归到 **env 侧**
（保守：宁可高估环境占比去触发告警，也不能把环境观测算成策略产出）。

⚠️ `assign_credit` 的 turn-level 归因**仍然恒不可用**（生产没有
`replay_fn`），这是刻意的 —— 不可重放就不给数字。想读"哪一层真正起
作用"用 `attribution.attribute_layers()` 的反事实消融。

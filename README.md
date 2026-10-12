# V6 · AETHER

**可验证的自进化智能体骨架**（Autonomous Evolving Task-Harness with Empirical Reward）

---

## 1. 一句话命题

> **每一次自我修改，都必须能被独立地证伪、回放和撤销。**

V5（mnemosyne）证明了：一个自改进系统要在"自证变好"这件事上不自我欺骗，
靠的是**纪律**——红契约、冻结基线、反证组、三轮对照、零新红。
但那些纪律住在工程师（和一份记忆手册）里，系统本身并不知道它们。

V6 做的事是把这些纪律**下沉成系统的结构**：

| V5 的做法 | V6 的结构 |
|---|---|
| 靠人记得"先写红测试" | 冻结非回归闸是**准入条件**：闸不过，根本不评估它有没有变好 |
| 靠人记得"发展集涨了也可能是假的" | held-out 泛化闸：另一个**从不参与选择**的环境，只在裁决时用一次 |
| 靠人记得"别信自述" | `Verdict` 无 evidence 即作废；自述/打分字段出现 ⇒ 判定作废 |
| 靠人记得"判据同源" | `Verdict.reward` 是唯一奖励定义，结构上去掉了第二条通路 |
| 靠人记得"口径变了要留痕" | `supersede()` 强制返回（新基线, 旧基线标记），旧基线禁止再当基准 |
| 状态散在 3 个 DB + 多个 JSON | 唯一事实源 = 账本（内容寻址 + 哈希链），改一条就断链 |
| 事后查日志才知道发生了什么 | 提交与回滚**两种结果都进账本**，可回放 |
| 靠外部 AI 写补丁来改进自己 | 自进化闭环内建：MINE → PROPOSE → VALIDATE → SELECT → COMMIT/REVERT |

---

## 2. 30 秒上手（离线，零依赖）

```bash
cd /Users/linshaojie/V6
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3

$PY -m aether.cli synth            # 合成任务：看 yield，不是看尝试次数
$PY -m aether.cli evolve --rounds 3 --group 3
$PY -m aether.cli verify-chain     # 账本链是否被篡改过
$PY scripts/demo_evolve.py         # 端到端 demo + 出报告

# 契约测试（含反证组）
for t in tests/contract_*_test.py; do $PY $t --e2e; done

# TypeScript 监督器（Node ≥22 原生跑 TS，无需 npm install）
cd supervisor && node src/selfcheck.ts                     # 含跨语言一致性验证
node src/index.ts --ledger ../data/ledger.jsonl --watch 30

# 控制台
$PY -m aether.serve --console console     # → http://127.0.0.1:8611/console/
```

**零第三方依赖**（Python stdlib + Node 内置模块）。这不是洁癖：
自进化系统的判据必须离线可回归 —— 一旦依赖网络或模型，
"零新红""非回归"这些判据就会随供给波动而失效
（V5 实测：链首供应商 82% 429，整条供给链废掉两轮）。

---

## 3. 目录

```
V6/
├── aether/                 核心（Python，stdlib only）
│   ├── ledger.py           内容寻址追加账本（唯一事实源）
│   ├── verifier.py         RLVR 内核：奖励只来自可执行判据
│   ├── agent.py            确定性执行体：harness 5 层**都有后果**
│   ├── genome.py           harness 5 层 + 基因/胶囊 + 爆炸半径
│   ├── nongression.py      冻结非回归闸（准入，不是评估项）
│   ├── generalization.py   held-out 泛化闸（发展集涨 ≠ 变强）+ 难度分层
│   ├── repair.py           修法基线 + **可执行**难度分层（easy/hard）
│   ├── environment.py      环境优先 + 任务合成 + 执行验证（F2P）
│   ├── embeddings.py       可插拔嵌入 + dim 契约 + 显式降级（禁静默混库）
│   ├── retrieval.py        检索判别力基准 + 嵌入选型闸
│   ├── runtime.py          轨迹 + token 归属记账 + 信用分配
│   ├── attribution.py      反事实层归因（逐层消融 + 联合消融 ⇒ 冗余）
│   ├── memory.py           5 层记忆 OS + 持久化 + 装载污染闸 + 继承闸
│   ├── evolution.py        self-harness 循环（MINE/PROPOSE/VALIDATE/SELECT/COMMIT）
│   ├── store.py            SQLite 投影（可由账本重建）
│   ├── providers.py        可插拔 LLM + LLMProposer（模型输出当不可信输入）
│   ├── cli.py / serve.py   命令行 / 只读 HTTP API
├── tasks/                  4 个可验证环境（解法形状刻意不同）
│   ├── textscore/          数值归一化与打分
│   ├── eventflow/          事件流聚合（会话切分 / 滑动窗口 / 分位数）
│   ├── stateflow/          状态机（转移 / 合法性 / 可达性 / 路径计数）
│   └── scheduler/          区间调度（冲突 / 最大并发 / 贪心 / 区间合并）
├── tests/                  10 个契约测试（RED→GREEN + 反证组）
├── supervisor/             TypeScript 外部监督器（只读账本 + 异常检测）
├── console/                零构建的只读可视化控制台
├── scripts/demo_evolve.py  端到端 demo + 报告
├── scripts/ab_generalization.py  泛化闸单变量对照实验（A/B/C/D 四组）
├── scripts/embedding_bench.py    嵌入判别力对照（中/英）+ 选型闸裁决
└── docs/                   ARCHITECTURE / FRONTIER_2026 / demo 报告
```

---

## 4. 语言选择与理由

| 语言 | 用在哪 | 为什么 |
|---|---|---|
| **Python 3.13** | 核心运行时 | 生态（AST 操作、sqlite3、subprocess 沙箱）最好；类型注解 + dataclass 让"产物可编辑可寻址"表达得很干净 |
| **TypeScript** | 外部监督器 + 控制台 | 监督器必须**独立于被监督系统**：不同语言 = 不同运行时 = 不可能被同一批 bug 带偏。Node 22 原生运行 TS，零构建 |
| **SQL（SQLite）** | 投影查询 | 只做索引视图，可由账本重建 |
| **JSON Lines** | 跨语言契约 | 账本格式。两端各自实现同一条判据，任一侧漂移都会被 `verify_chain` 抓到 |

没有引入 Rust/Go：本机无工具链，且它们能提供的（更强隔离/更快检索）
在当前规模不是瓶颈。**接口留好了**（`providers.py` 的 embedder/LLM、
`environment.py` 的沙箱 backend），要换就换。

---

## 5. 系统如何自证（判据清单）

这十一条是 V6 的"宪法"。每条都有对应的契约测试守着：

1. **事实只有一份**：账本（hash 链）。SQLite 是投影，`rebuild_from_ledger()` 可重建。
2. **奖励只来自执行判据**：`Verdict.reward` ∈ {0,1}，来自 `verify(env_state)`，
   不来自模型自述/打分（`SELF_REPORT_KEYS` 一出现即作废）。
3. **没有证据的判定不算判定**：check 缺 evidence ⇒ 判定无效、reward 归 0。
4. **先证没变坏，再谈变好**：非回归闸（冻结套件）是准入；闸不过 ⇒ 不评估发展集。
5. **发展集涨了 ≠ 变强了**：held-out 泛化闸 —— dev 涨而 held-out 不涨 ⇒
   判 overfit，不提交。held-out **不参与候选选择**（一参与就变第二张发展集）。
6. **held-out 自己也要有判别力**：难度分层（easy/hard，由"通用修法能否修好"
   **执行判定**）。分层裁决必须与发展集**同层对照**：发展集同层也涨不动 =
   能力边界（放行）；发展集同层涨了而 held-out 同层没涨 = 挑食（拦截）；
   拿不到同层数据 = 无从判别（判 inconclusive，同样拦截）。
   ⛔ 少了同层对照会**自指死锁**：`hard` 的定义就是"通用修法修不好" ⇒
   通用修法在 hard 层增益必然 0 ⇒ 唯一能泛化的修法被永久拦死（实测过）。
7. **harness 的 5 层都有后果**：改 L0/L1/L2/L4 都会改变 reward（单变量实测）。
   路由键按"任务类别"而非"环境名"—— 前者换环境仍可达，后者立刻失效。
8. **口径不可比就不比**：`check_caliber` fail-closed；改口径必须 supersede
   并留痕；绝对相似度跨 embedder 不可比（嵌入切换只认 recall@1 与 margin）。
9. **弱点必须有证据**：没有失败 run ⇒ 这一轮正确地什么都不做（并记账）。
10. **两种结果都算成功**：提交成功、正确地拒绝 —— 都进账本。
11. **模型输出是不可信输入**：语法闸（L0 双锁/白名单）→ 解析闸（不返回
    半成品）→ 语义闸（与模板提议器完全相同）。模型再差也只是少提变异。
12. **有机制 ≠ 这条路被走到**：因果链可达性要**计数**，不能靠"模块写好了"。
    实测普查抓到三条装饰链路（L4 记忆 0 次 / 信用分配 0 次 / 端到端只用 2 个
    环境）。绿测试证明机制存在，不证明机制被使用。
13. **记忆只能由执行填充，不能预置答案**：预置"基线/修法"会把 reward 顶到
    天花板 ⇒ 弱点被掩盖 ⇒ 进化永久空转（两种预置都实测翻车）。
    ⛔⛔ **跨运行继承的记忆同样不得进入因果链**：它把同一个种子 harness 的
    基线从 0.0 抬到 0.6 ⇒ 结果依赖历史（不可复现）⇒ 且记忆在**冒充能力**。
    继承的记忆只供提议器参考，reward 判据只依赖本轮执行。
14. **能力要能归因到层**：反事实层消融（去掉这一层，reward 掉多少）。
    先验证可重放，不可重放 ⇒ 不给数字；归因只在提交**之后**算，不参与选择。
    ⛔ 归因必须挑**提升真的发生**的任务：在恒 0 的任务上消融，答案必然全 0
    —— 那不是"层没贡献"，是在永远读 0 的尺子上量差值。
15. **缺的是采样还是能力，别搞混**：发展集没采到某个难度层 ⇒ 无从同层对照
    ⇒ 闸只能判"不可判别"。那是**采样配置**问题，不能让候选背锅 ——
    自动补采样补齐（合成任务与候选无关，不给任何优待）。

---

## 6. 与 V5 的关系

V6 不是 V5 的替代品，是它的**结构化重写**：

- 保留并升级的：冻结非回归（→ 准入闸）、红契约 + 反证组（→ `tests/contract_*`）、
  判据同源、写入端完整性检查（`has_embedding` → `Verdict.validate()`）、
  产物归属与去重（→ 内容指纹写入侧去重）。
- 新增的：self-harness 进化闭环、RLVR 奖励内核、内容寻址账本、
  环境优先的任务合成、5 层记忆 OS、外部监督器（跨语言）。
- 明确不做的：不训练权重（那是训练侧的事）、不接真 LLM 作为默认路径
  （默认 stub，保证判据可回归）、不做"允许少量回归换取大增益"的权衡开关。

详细取舍见 `docs/ARCHITECTURE.md`，前沿依据与出处见 `docs/FRONTIER_2026.md`。

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
$PY -m aether.cli rebuild --check  # 投影（SQLite）与账本对不对得上
$PY -m aether.cli rebuild          # 对不上时的恢复路径（备份旧库 → 重放 → 再对账）
$PY -m aether.cli memory show      # 记忆库完整性读数（混库/空向量/污染）
$PY -m aether.cli memory reindex --to hash   # 换 embedder 后的唯一恢复路径
$PY scripts/demo_evolve.py         # 端到端 demo + 出报告
$PY scripts/llm_e2e.py --rounds 3  # 真模型端到端：跑通 vs 赢过
$PY scripts/llm_e2e.py --no-llm    # 离线对照组（模板提议器）

# 契约测试（含反证组）—— ⛔ **两种模式都要跑**
# e2e 组默认跳过；只跑默认模式等于把最重的那几组永久关掉
# （实测：e2e 组因接口变更坏掉而无人发现，就是因为它从没被跑过）
for t in tests/contract_*_test.py; do $PY $t; $PY $t --e2e; done

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
│   ├── store.py            SQLite 投影（可由账本重建 + 账本/投影对账）
│   ├── providers.py        可插拔 LLM + LLMProposer（模型输出当不可信输入）
│   ├── cli.py / serve.py   命令行 / 只读 HTTP API（共用同一份记忆读数）
├── tasks/                  4 个可验证环境（解法形状刻意不同）
│   ├── textscore/          数值归一化与打分
│   ├── eventflow/          事件流聚合（会话切分 / 滑动窗口 / 分位数）
│   ├── stateflow/          状态机（转移 / 合法性 / 可达性 / 路径计数）
│   └── scheduler/          区间调度（冲突 / 最大并发 / 贪心 / 区间合并）
├── tests/                  11 个契约测试（RED→GREEN + 反证组）
├── supervisor/             TypeScript 外部监督器（只读账本 + 异常检测）
├── console/                零构建的只读可视化控制台
├── scripts/demo_evolve.py  端到端 demo + 报告
├── scripts/ab_generalization.py  泛化闸单变量对照实验（A/B/C/D 四组）
├── scripts/embedding_bench.py    嵌入判别力对照（中/英）+ 选型闸裁决
├── scripts/llm_e2e.py            真模型端到端：跑通 vs 赢过（可复现读数）
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
   ⛔⛔ 实测（2026-10-13）：投影曾**丢掉 89.7% 的执行历史** —— 账本 495 条
   `eval.verdict`、库里只剩 51 条（`run_id` 不唯一 ⇒ `INSERT OR REPLACE`
   互相覆盖），而"库只是视图"这句话被一个**恒真断言**（`n >= 0`）绿了很久。
   修法三处：run_id 唯一化（锚在账本 seq）、账本带上 run_id、初始 harness
   也必须先进账本。现在 `rebuild` 是命令，`status` 每次都对账（见第 19 条）。
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
16. **观测侧不得有假仪表**：不随被测对象变化的读数等于没有读数。
    实测 `/api/status` 的 memory 段是硬编码的 `{"tiers": ["L0".."L4"]}`
    —— 库里 0 条也报五层、被污染也报正常 ⇒ 污染在账本里可见、在控制台
    不可见。读数判据必须与写入侧**同源**（词表不抄第二份）。
17. **一条闸配一条出口**：换 embedder ⇒ 整库拒载（正确），但拒载后若无
    恢复路径就是死胡同。`memory reindex` 是唯一出口，且失败**不落盘**。
18. **「跑通」≠「赢过」**：真模型能产出 9 个过闸候选（跑通），但泛化闸
    放行 0 次（没赢过）。为了让它赢去放宽闸 = 把"讨好判据"判成"泛化"。
    ⛔ 只能告诉模型**规则**，不能告诉它**答案**（讲了就是替它考试）。
19. **视图必须能与事实源对账**：`status` 每次都做「账本 vs 投影」比对
    （键集合 + 字段 + **条数**），对不上就明确报"基于投影的读数不可信"，
    且 `rebuild` 是它的出口（备份旧库 → 从账本重放 → 再对账）。
    ⛔ 条数比对是**丢历史**的唯一抓法：键比对只能发现"对不上"，发现不了
    "压根没写进来"（495 → 51 就是这么被抓到的，控制台当时只显示 51）。
    ⛔ 同键覆盖本身必须进账（`overwrite_count`）：静默正确是这里最大的风险。
20. **闸自己不能是假的**：冻结套件为空时，非回归闸原本恒绿
    （`passed = 无回归 and 无错误`）—— **准入闸在自己最该响的时候放行**。
    改成 fail-closed。
    ⛔⛔ 但"基线里没有一条通过"**不能**同样拦死：系统还没能力 ⇒ 基线全 0
    ⇒ 每个候选都被拦 ⇒ 永远无法进化 —— 与难度分层那个自指死锁同形态。
    两者的分界是：**套件为空 = 无从判别（拦）**；
    **套件非空而基线全 0 = 确实没有可回归的东西（照跑，只标注判别力为零）**。

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

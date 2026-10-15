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

# ⛔ 只用 stdlib：**不需要 pip install**（`pyproject.toml` 只为"可安装"而存在，
#    运行期依赖是空的，且这一点由 `contract_packaging_test.py` 静态守着）。
#    `python -m aether` 与 `python -m aether.cli` 是同一个入口（前者是转发）。
$PY -m aether --version            # V6 · AETHER <版本>（账本格式 vN）
$PY -m aether synth                # 合成任务：看 yield，不是看尝试次数
$PY -m aether.cli evolve --rounds 3 --group 3
$PY -m aether.cli verify-chain     # 账本链是否被篡改过
$PY -m aether.cli rebuild --check  # 投影（SQLite）与账本对不对得上
$PY -m aether.cli rebuild          # 对不上时的恢复路径（备份旧库 → 重放 → 再对账）
$PY -m aether.cli memory show      # 记忆库完整性读数（混库/空向量/污染/对账）
$PY -m aether.cli memory backfill  # 存量记忆补记账本证据（对账告警的出口）
$PY -m aether.cli memory reindex --to hash   # 换 embedder 后的唯一恢复路径
$PY scripts/demo_evolve.py         # 端到端 demo + 出报告
$PY scripts/llm_e2e.py --rounds 3  # 真模型端到端：跑通 vs 赢过
$PY scripts/llm_e2e.py --no-llm    # 离线对照组（模板提议器）

# 契约测试（含反证组）—— ⛔ **一条命令，一个退出码**
# ⛔⛔ 以前这里是一句 shell `for` 循环。它**不是闸**：没有失败聚合 ⇒
#    退出码 = 最后一次迭代 ⇒ 中间任何一份契约变红都被吞掉；
#    而且 `--e2e` 被传给不支持的契约 ⇒ 几十次纯重复。
#    现在判据只有一份（`aether/check.py`），下面两条命令共用它。
$PY -m aether.cli check -j 4            # ← 全量验收（含 --e2e 与五态分类）
$PY scripts/run_contracts.py -j 4       # ← 同一判据的薄壳（默认并行）

# `check` 的五态：pass / fail / timeout / error / skip
#   · 任何 fail/timeout/error ⇒ 整条命令退出码非 0（真闸）
#   · skip 不算通过、也不拦（"不可判别 ≠ 有罪"），但报告里必须可见
#   · `--only X` 只跑子集 ⇒ 报告**明说**是子集，不与"全绿"同形
# ⛔ 每份契约用 `SUPPORTED_MODES = ("default", "e2e")` 声明自己支持哪些
#    模式；**未声明 ≠ 只有一种模式**（前者是"没接线"，报告要点名）。

# 运维面（r34 补齐）
$PY -m aether.cli doctor                # 一条命令体检（只读）
$PY -m aether.cli backup --to backups   # 备份账本/记忆/投影（自带校验和）
$PY -m aether.cli restore --from backups/backup-xxx --yes
$PY -m aether.cli evolve --checkpoint cp.json --resume   # 断点续跑
$PY -m aether.serve --api-token $TOKEN  # 非本机部署必须带上

# 环境变量：全部**可选**（不给就离线跑，这是设计不是妥协）。
# ⛔ 变量清单与代码**同源**：`contract_env_docs_test.py` 会把 `aether/` 里
#    出现的每个 V6_* 与 .env.example 逐个对照（漏一个就转红）。样例见
#    `.env.example`，其中如实标注了一个**已知缺口**：V6_EMB_API_KEY
#    被读入后没有被任何地方使用（嵌入端点若需鉴权会静默 401）。
cp .env.example .env.my && $EDITOR .env.my   # 例：填入 LLM / 嵌入端点

# 发布与变更
$PY -m aether --version              # 版本 + 账本格式（唯一事实源 _version.py）
cat CHANGELOG.md                     # 含"已知边界"一节：没做/没验的事都在里面
git tag v0.2.0                       # 版本号必须能锚定到某个提交（有契约守）

# 种子现场：新克隆的仓库没有 data/（运行产物不进版本库）
$PY -m aether --root data/seed doctor    # ⛔ `--root` 在子命令前后都可以写
$PY -m aether --root data/seed rebuild   # 库只是视图：从账本重放出来

# 反证探针：单独挖某一类缺口的**对拍脚本**（不是契约测试，是取证工具）
$PY scripts/probe_spread_audit.py   # held-out 没判别力时闸会不会假放行
$PY scripts/probe_sandbox_audit.py  # 沙箱是"真隔离"还是"只是 subprocess"
$PY scripts/probe_memory_audit.py   # 一次 _collect 的内存峰值花在哪（含投影库场景）

# TypeScript 监督器（Node ≥22 原生跑 TS，无需 npm install）
# ⛔ 本机 `node` 不在 PATH 上；项目约定用 workbuddy 的二进制（契约测试的
#    `_find_node()` 会按同一顺序查找，找不到就明确报红而不是静默跳过）
NODE=/Users/linshaojie/.workbuddy/binaries/node/versions/22.22.2-6/bin/node
cd supervisor && $NODE src/selfcheck.ts                    # 含跨语言一致性验证
$NODE src/index.ts --ledger ../data/ledger.jsonl --watch 30

# 控制台（三栏工作台）
$PY -m aether.cli ui                       # 起服务 + 开桌面窗口（推荐）
$PY -m aether.cli ui --no-open            # 只起服务
$PY -m aether.serve --console console     # → http://127.0.0.1:8611/console/

# REA（外部逆向引擎，经能力清单调用）
$PY -m aether.cli caps --caps capabilities.rea.json list
$PY -m aether.cli caps discover                  # 探本机已有工具 → 生成能力清单
$PY -m aether.cli caps discover --out my.json    # 写成文件，之后 --caps my.json
# ⛔ 这个命令**只发现、不下载不安装**：它探路径与版本、生成清单。
#    "从网上找了装"是刻意不做的 —— 判据必须离线可回归（宪法）、
#    子进程的网络动作是动作级拦下的（沙箱）、且"自动下载并执行"
#    会把"人审过这份清单"这一步整个拿掉（见 aether/discover.py 的说明）。
#    装工具是**人**的事（会改环境、涉许可证与供应链）；装完重跑即可。
# ⛔ 探到的条目状态是 `discovered_unwrapped`：它证明工具**存在**，
#    但 argv 是版本探针命令 ⇒ 还没被包装成可用能力（存在 ≠ 可用）。
$PY -m aether.cli caps --caps capabilities.rea.json invoke rea.doctor --json '{}'
$PY -m aether.cli caps --caps capabilities.rea.json \
    invoke rea.analyze_javascript_application --json '{"path":"/绝对路径/目标"}'
$PY -m aether.cli caps --caps capabilities.rea.json \
    invoke rea.inspect_android_package --json '{"path":"/绝对路径/目标.apk"}'
# ⛔ `--caps` 是 **caps 子命令自己的**开关，写在 `caps list` 之后会报
#    unrecognized arguments（实测踩过）。
# ⛔⛔ APK **不走** `rea.analyze`：它的自动候选只有 ghidra/hopper/ida
#    三个**可执行体** provider，会统一拒掉 `target_kind=archive`。
# ⛔⛔ 原生二进制（Mach-O / ELF / PE）走 rea.analyze，需 Ghidra 引擎；
#    其 `normalized_result` 是**概览**：给 image_base / 段表 / 三个计数，
#    **不给**过程名与字符串实体（要名字需上游别的操作，别拿概览硬要）。
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
│   ├── cli.py / serve.py   命令行 / HTTP API（共用同一份记忆读数）
│   ├── checkpoint.py       断点续跑（轮号接续 + 换环境拒绝）
│   ├── backup.py           备份/恢复（唯一事实源此前无退路）
│   ├── doctor.py           一条命令体检（三态：ok / fail / skipped）
│   ├── check.py            全量验收（一条命令、五态、**聚合退出码**）
│   ├── provenance.py       账本出处（谁写的、按哪版格式写）
├── discover.py         从本机已有工具生成能力清单（**发现**，不下载不安装）
│   ├── _version.py         版本号/格式号的**唯一事实源**
│   ├── paths.py            目录布局的唯一事实源（cli/serve/doctor 共用）
│   ├── learner.py          外部知识摄入（复用记忆闸，不自搞一套）
│   ├── aging.py            跨轮退化检测（ALE 四类信号）
│   ├── interop.py          UCI 通用接入层（agent/skill/tool 挂得上叫得动）
│   └── ui.py               桌面窗口（同源托管，不引 Electron）
├── tasks/                  4 个可验证环境（解法形状刻意不同）
│   ├── textscore/          数值归一化与打分
│   ├── eventflow/          事件流聚合（会话切分 / 滑动窗口 / 分位数）
│   ├── stateflow/          状态机（转移 / 合法性 / 可达性 / 路径计数）
│   └── scheduler/          区间调度（冲突 / 最大并发 / 贪心 / 区间合并）
├── tests/                  42 个契约测试（RED→GREEN + 反证组；`aether check` 一条命令跑全）
├── supervisor/             TypeScript 外部监督器（只读账本 + 异常检测）
├── console/                零构建的只读可视化控制台
├── data/seed/              **种子数据**（随仓库发布的最小现场：小账本 + 记忆）
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

这 54+18 条是 V6 的"宪法"（第 55–64 条见 §5-bis，第 65–68 条见 §5-ter，
第 69–72 条见 §5-quater）。
每条都有对应的契约测试守着：

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
21. **跨轮/跨运行的判据必须有会话归属**：`round_no` 每个进程都从 1 重新
    数，只按它匹配会把跨小时的两次运行凑成"同一轮"。实测：监督器那条
    "泛化闸被绕过"的规则在真实账本上报了 **13 条 critical，逐条核对后
    8/8 全是误报**（被拦的是 `patch:textscore`，被点名的却是另一个候选
    的合法提交）。现在事件带 `sid`，匹配要同 capsule **且**同会话。
    ⛔ 永远在喊狼来了的规则 = 没有规则；归属未知时**不许**硬判，要报
    "不可判定"（`GATE_BYPASS_UNVERIFIABLE`）而不是判成"已证明绕过"。
22. **记忆也在因果链上，所以也要进账本**：记忆跨运行继承、直接喂给提议
    器。实测账本里 0 条写入事件、而 `data/memory.json` 躺着 11 条 ⇒
    谁写的、何时写的、被闸拒了多少都查不到。现在每次写入尝试（**含被拒
    的**）都记 `memory.write`，`status` 做「文件 vs 账本」对账。
    ⛔ 存量补票走 `memory backfill`，证据强度**低于**实时写入（只证明
    "此刻库里有它"）⇒ 单独计数，不混进 `written`。
23. **失败不许只写在内存里**：层归因整段包在 try/except 中，失败只进内存
    报告 ⇒ 账本上看不见 ⇒ 下一次仍不知道自己在盲改。现在记
    `attribution.unavailable`（带异常类型），控制台与监督器都可读。
    两者的分界是：**套件为空 = 无从判别（拦）**；
    **套件非空而基线全 0 = 确实没有可回归的东西（照跑，只标注判别力为零）**。

24. **"没提交"必须说清是哪一种**：被闸拦下（失败，值得重试）、发展集到顶、
    停滞（有候选但没人赢得过基线）、无失败证据 —— 四者原先合并成一句
    `no_candidate_passed`。实测 10 轮压力跑里 r2–r10 是同一轮重复 9 次，
    看起来像"努力过没成功"，实际是"没空间了"。现在后三者打 `idle`，
    **连续 2 轮**即停（记 `evolution.stopped`，带 `rounds_requested`）。
    ⛔ 根因是弱点挖到了**上一次运行**的失败（`harness_id` 由内容决定 ⇒
    两次运行的 seed harness 同 id），与第 21 条同形态 ⇒ 弱点改用**本轮
    base 评估的判定**当证据，串味在结构上不再可能。
25. **已知死路不许反复探测**：某候选实测 15 次探测 / 15 次 overfit / 0 次
    通过，仍被一遍遍提议。现在同一 `(候选, 环境对)` **连续**被拦 ≥3 次即
    冷却（连续而非累计，否则把"后来成了"的候选永久封死）；每 5 轮留一次
    试探出口。冷却依赖的 `env_pair` 原先根本没记账（15 条全是 None）。
26. **跨语言契约要在真实数据上对拍，不能只在合成用例上对拍**：监督器把一条
    完好的账本判成"被篡改 @788"，而 Python 侧说链完整。根因是
    `ts*1000` 恰好半整数时 Python 银行家舍入（…736）与 JS half-up（…737）
    不同 —— 1028 条命中 1 条，而 selfcheck 的 ts 全是整数，永远撞不到。
    修法：TS 侧 `roundHalfEven` 对齐 Python（不改写入端 ⇒ 历史 hash 不失效）。
27. **闸的判别力必须由闸自己检查（有属性 ≠ 进了判据）**：`HeldOutSuite.spread`
    早就存在、docstring 写了危害、CLI 也打 ⚠️，但 `probe()` 的判据里没读过它
    —— 实测同一挑食候选，held-out 采到 hard 层 ⇒ `partial`（拦），少采一层
    ⇒ `generalize`（**放行**），且放行输出照样带 `by_difficulty`，与真放行
    完全同形。⛔ 加闸前先做交叉表：`--max-tasks` ≥10 时 4 个环境全有跨度
    ⇒ 新闸命中 0 次，默认配置能跑通是**巧合**不是保证 ⇒ 所以修法必须是
    "补采样出口 + 兜底"两段，只加闸就会变成"永远不提交"。
    ⛔ 三态不能合并：`False`（只有一层，有证据的判别力不足，拦）/
    `True`（有跨度）/`None`（没打标签 = 没测过，**不拦** —— 不可判别 ≠ 有罪）。
    ⛔ 兜底只降级"放行"，**不许**盖掉 `overfit` 这类有硬证据的拦截。
28. **沙箱必须是实测过的隔离，不是"用了 subprocess 就叫沙箱"**：实测原实现
    7 项里 5 项没拦住 —— 宿主 **181 个环境变量全被继承**（含 `PYAUTOL_VAULT_KEY`、
    `TAVILY_API_KEY` 等真密钥）、能读 `/etc/hosts`、能写 `/tmp`、能 fork、
    20 MB 输出全量进内存。这里执行的是**不可信代码**。⛔ env 用**白名单**不用
    黑名单（黑名单拦不住没见过的名字）。⛔ **声明了意图 ≠ 生效了**：
    `RLIMIT_AS`/`DATA`/`RSS` 在本机全部 `ValueError` 却被 except 吞掉，
    结果里却写着"1 GB" ⇒ 必须把 `rlimits`（意图）与 `rlimits_applied`（实测）
    分开显示。    ⛔ OS 层（macOS `sandbox-exec`）**探测式**接入：实测 deny-default
    一律 `Operation not permitted` ⇒ 用不了就降级到 `process`，但
    `fs_isolated=false` 必须落进每个 Verdict 与控制台。⛔ **审计探针自己不许
    成为 DoS**（第一版把机器内存吃光，load 打到 9.3）。
28. **只读 ≠ 无隐私：对外暴露面必须默认关闭，且不得用前缀判断**。
    `/api/status` 上的 `/api/status_extra` 会误命中 ⇒ 端点按**路径边界**
    匹配；静态文件 `str(f).startswith(str(base))` 会把 `/app/console_bak/x`
    当成 console 子路径 ⇒ 按**路径分量**判断；硬编码
    `Access-Control-Allow-Origin: *` 让任意网页的 JS 能读本机账本（含 harness
    状态与记忆正文）⇒ CORS 默认不发、开了也回显 origin 而不回 `*`；
    `/api/ledger/raw` 无上限 ⇒ append-only 账本的内存峰值随历史线性增长。
    ⛔ **全绿的断言不等于有判别力**：把新测试对着旧代码跑一遍，F1/F2/F3
    两边都绿（旧版两个端点的键集合本就相同、`Event` 也不是 slots）⇒
    它们是护栏不是战果；真正能证伪的是"给序列化函数打标记看两端是否同时
    带上"这类反证。
    ⛔ **"无上限读取"有第二个人口 —— `limit` 参数**：修完 `/api/ledger/raw`
    不算完，`/api/status?limit=N` 与 `/api/events?limit=N` 的 `N` 此前
    **完全没有校验**。最阴的是**负数**：SQLite 里 `LIMIT -1` == **无限制**
    （实测 `runs(limit=-1)` 返回全表 810 行）⇒ `limit=-1` 不是"取 0 条"，
    是"全部吐出来"；而旧实现把它静默变成空列表，于是同一份响应里
    `events=[]` 而 `runs` 是全表 —— 两个列表语义相反。⇒ 明确 400，并且
    **数据层每个 limit 入口也要夹一道**（HTTP 闸能被绕过，`LIMIT -1` 不会）。
29. **只读请求不得把同一事实源读多遍；判据要用结构性计数而不是墙钟**。
    `/api/status` 曾把账本全量解析 **4.69 遍**（`_hydrate` / `events` /
    `stats` / `verify_chain` 各一遍）—— 更糟的是这四遍是**四个不同时刻的
    快照** ⇒ `count` / `chain_ok` / `consistency` 可能互相矛盾而不自知。
    append-only ⇒ 最新事件必在末尾，冷启动**只读尾部**（外加"全是坏行就退
    全扫"的保底）。⛔ 本机 load 常年在 10 以上 ⇒ 单次 wall time 会被噪声
    淹没 10 倍（同一函数实测 0.37 / 2.57 / 1.62 / 0.70s），必须换成
    "数 `json.loads` 次数"这类**与负载无关**的计数来决策。
30. **观测不得改变被观测对象的成本结构：读数只能来自一次遍历，且不
    许把整份事实源放进内存**。
    一次 `/api/status` 要同时给出统计 / 链校验 / 一致性 / 记忆对账 /
    尾部窗口五份读数 —— 它们**全都只是遍历一遍**，那就只准遍历一遍，
    且遍历时不许持有整份账本。
    ⛔ **只修一半等于没修**：账本侧流式化后峰值 8.27 → 0.65 MB，但生产
    （带投影库）实测仍有 **7.72 MB**，其中 **5.83 MB（76%）来自
    `runs()` 把每行的 payload 全量 `json.loads`** —— 而一致性判据只比
    `reward/passed/harness_id` 三个字段。改成 `run_digests()` 只取三列
    ⇒ 同口径（4000 条账本 + 1334 runs）**14.24 MB → 2.70 MB（5.3×）**。
    ⛔ **反证项会随实现翻转**：上一轮的反证注入在 `Ledger.stats`（"忽略
    传入的 events"），流式化后 `_collect` 根本不调 `stats()` ⇒ 注入点变
    成死点、H2 恒红却查不出任何问题。反证必须打在**当前咽喉**上。
    ⛔ **哪些断言是战果、哪些是护栏要明说**：把 `_collect` 退回物化实现
    后，29 项里只有 D 组（内存峰值）三条转红，A/B/C/E/F/G 全绿 ⇒ 等价性
    断言是护栏（防改坏），不是战果（证明改好了）。
    ⛔ **流式化要覆盖所有端点，且"测组件 ≠ 测端点"**：`/api/status` 改完
    后 `/api/events` 仍走 `led.replay()` 物化全量（为取尾部 200 条而解析
    1533 条**两遍** ⇒ 峰值 6.79 MB）。给 `/api/events` 补的 H1–H4 全是组件
    级断言 —— 把端点代码退回物化后 **34/34 依然全绿**，因为没人真的走到
    那条代码。⇒ 必须打真 HTTP + 计数"物化入口被调用几次"（H5），那才是
    "端点走的是哪条路"的可证伪形式。改完：6.79 → 1.19 MB（5.7×）。
31. **CLI 也是读数入口，热路径也要数遍数；判据语义不许"顺手改"**。
    ⛔ **同一个洞的第三个人口**：§16/§17/§18 全在 HTTP 侧排查，CLI 与热
    路径从没人去数过。`cmd_status` 一次问账本 **3.01 遍**（`stats()` /
    `consistency_report(led.events())` / `memory_report(led.events())`）
    ⇒ 三条判据来自三个**不同时刻**的快照，与 §16 同形。改：三个聚合器
    共享一次 `iter_events()`（`memory_report` 新增 `ledger_agg=` 流式入口，
    与 `ledger_events=` 共用同一个聚合器 ⇒ 判据仍只有一份）。
    ⛔ **战果是正确性不是内存，别拿小倍数冒充大倍数**：同口径下遍数
    3.01 → **1.00**，但峰值只降 1.4×（7.93 → 5.67 MB）、**常驻完全不变**
    （0.77 MB）—— tracemalloc 按行号定位显示峰值主体是 sqlite 连接与
    import，不是账本。⇒ 报 Δ 时要把"主指标/次要指标"分开说。
    ⛔ **热路径**：`_deadend_map` 在 `run_round` 里**每轮** `reversed(
    events())` 物化整份账本，为算 **7 个** key 花 **6.49 MB** ⇒ 正序流式
    **0.06 MB（108×）**。
    ⛔⛔ **改判据 ≠ 改语义（本轮最值钱）**：我第一版按"连续"语义重写，
    随机对拍 **26/40 不一致** ⇒ 查出旧实现其实是**二元值**（曾否 generalize
    ⇒ 0 或全部），与它自己的 docstring 不符 ⇒ **成功过又退化的候选永远
    不会进冷却**。这是判据 bug，本次**只换实现、不动语义**：用 `git show`
    出的旧代码当 oracle 对拍 40/40 同值（⛔ oracle 不许"顺手美化"——美化了
    它就不再证明同值），并加断言把当前语义钉死，注释写明"要修必须先做
    「成败 × 是否命中冷却」交叉表"。生产 7 个 key 新旧同值 ⇒ 当前误伤面 0。
    ⛔ **一条断言只对一种事故敏感**：注入三处事故版后 I2/I4/J3/J4/K2/K3
    转红而 **I1 仍绿** —— 那版事故没让解析遍数上升 ⇒ 必须给 I1 造它自己
    对应的那个事故（"各读一遍"）才能证明它有判别力。

    ⛔⛔ **已修（第 17 轮）**：那条"成功过 ⇒ 永不冷却"的二元判据，按上面
    那条纪律做完交叉表后修成了**连续**语义。修法是换**充分统计量**
    （`total`+`ever_gen` ⇒ `streak`），不是补条件分支 —— `streak` 正序扫
    一遍即可维护，顺带让增量路径与全量路径严格等价。
    依据是实测交叉表：生产 39 条 `gen.probe` / 7 个 key 中 **0 个**处在
    「曾成功 + 末尾连续被拦」形态 ⇒ 新旧逐键同值、**冷却决策翻转 0**；
    真正保证不永久封死的是 `deadend_probation_every` 出口，不是这份小样本。
    ⛔⛔ **oracle 不能照抄被测对象的实现结构**：我照抄旧实现的"遇 G 清零"
    写 oracle ⇒ oracle 自己也是二元的 ⇒ 对拍 24/40 假红。倒序回看的
    正确写法是**遇 G 停止、保留已数到的值**。照抄结构会把 bug 一起抄进
    基准，两边一起错，对拍还照样"有结果"。
    ⛔ **边界值反转的另一例**：`events()[-0:]` 在 Python 里返回**全部**
    （`-0 == 0`）⇒ `tail(0)` 写成旧形式会"取 0 条"变成"取全部"，与
    `LIMIT -1` 同形态。`Ledger.tail()` 改 `deque(maxlen=n)` 并由 K3 守着。
32. **判据不得有隐性上限：宁可慢，也不能悄悄少看**。
    `run_digests()` / `run_ids()` 原本默认 `limit=100000`，注释还写着"不传
    limit 即可全量" —— **注释与实现矛盾**（比没注释更危险，它会让人以为
    判据是全量的）。而一致性判据正是用**默认**调用的 ⇒ 表超过 10 万行后
    溢出部分不进比对，**两个方向同时失真**：不进 `run_ids` ⇒ 被算成
    "账本有、投影没有" ⇒ `missing` **假红**；不进 `run_digests` ⇒ 那些行
    根本不做字段比对 ⇒ `drift` **假绿**（L3/L4 两条反证分别钉死）。
    改：默认 `None` = 真全量，判据侧不自带上限。
    ⛔ **断言"调用时传了什么"是恒真断言**：我第一版给 `run_digests` 装
    spy 断言"判据调用时不传 limit"，注入"默认改回 100000"的事故版后
    **L 组 5 条全绿** —— 调用方本来就不传，spy 永远记到 None。
    ⇒ 想知道"默认有没有上限"，就必须去看**签名默认值本身**
    （`inspect.signature(...).parameters["limit"].default is None`），
    事故版下立刻转红。
33. **增量缓存必须能证明"前面没变"，不能靠"相信文件只会追加"**。
    `_deadend_map` 每轮全扫账本 ⇒ O(轮 × 账本)。改成记住文件偏移 + 链头，
    只解析新增部分；三条失效检查（偏移只停行边界 / 新增首条 `prev_hash`
    接得上 / 无新行但文件被写过 ⇒ 可疑）任一不成立就回退全量。
    ⛔⛔ **踩到的坑**：文本模式下 `for line in f` 里调 `f.tell()` 直接抛
    `OSError: telling position disabled by next() call`；第一版用
    `except: pass` 兜住 ⇒ 偏移永远是 0 ⇒ 每次都从头读，而 `stats` 里
    `full=6 / incremental=0` 一眼看穿。**吞掉失败 = 把降级伪装成成功**。
    ⛔ **失效必须可观测**（`_deadend_stats.invalidated`）：不记的话，
    "真增量"和"每轮全扫"在外部完全等价（假仪表）。
34. **rlimit 设不上就是设不上，不许靠记账假装生效**。
    实测 macOS 上 `RLIMIT_AS` / `RLIMIT_DATA` / `RLIMIT_RSS` **三条全抛**
    `ValueError: current limit exceeds maximum limit`。⇒ 改两条实测有效
    的路线：进程内看 `ru_maxrss` 自杀（⛔ 单位跨平台不同：macOS 字节 /
    Linux KB）+ 父进程 `ps` 看门狗（覆盖"守卫线程没机会跑"）。
    文件系统隔离补 audit hook 层（白名单 = 工作目录 + `sys.prefix`，
    ⛔ 少了 stdlib 冷 import 也会被拒），**探测式接入**（跑同一份 runner
    的 `--selftest`，不写第二份探测代码）。
    ⛔⛔ **拒绝事件必须与"代码有 bug"分开记账**（`sandbox.denied`）：
    混在一起，自演化会把"被沙箱拦下"当成 bug 去修一个不存在的 bug。
    ⛔ 字段也拆开：`os_isolated`（OS 层）≠ `fs_isolated`（能力，含 audit
    层）—— 合成的字段会把真生效的隔离判成谎报。
35. **断言的判别力必须靠注入事故版量，不许靠"我觉得它能抓到"**。
    本轮实测：M 组我以为 5 条是真判据，注入后只有 **M1/M2b/M6** 转红；
    M2 在"去掉链校验"下**值仍同值**（rebuild 前后 payload 一样），真正
    抓到"抱着旧缓存"的是 M2b；M4 被两条兜底路径救回，注入"offset 记在
    解析前"也照样绿 ⇒ 是护栏。E2 只查第一条时也不红（块边界没落到它身上）
    ⇒ 改成断言**每一条**。
    ⛔ 连带：**替身要透传 `*a, **k`**（J4 的 `lambda self:` 在新签名下
    TypeError —— 那是替身跟不上接口，不是被测代码错了）。
36. **改判据：拍板靠交叉表，对拍靠独立 oracle（不许照抄实现结构）**。
    `_deadend_map` 的二元语义（曾成功 ⇒ 永不冷却）第 14 轮故意不改、钉死
    在 J2 上；第 17 轮修成"连续"，拍板依据是**实测交叉表**而不是"看起来
    better"：生产 39 条 `gen.probe` / 7 个 key 里 **0 个**处在「曾成功 +
    末尾连续被拦」形态 ⇒ 新旧逐键同值、**冷却决策翻转 0**；阈值 3 的历史
    误伤率 0/12。⛔ 反面也要写：样本小 ⇒ 只证明**当前零回归**，不证明未来
    零影响；真正保证不永久封死的是 `deadend_probation_every` 出口。
    修法是换**充分统计量**（`total`+`ever_gen` ⇒ `streak`），不是补分支 ——
    `streak` 正序扫一遍即可维护，顺带让增量与全量严格等价。
    ⛔⛔ **oracle 只能照抄定义，不能照抄实现结构**：我照抄旧实现的控制流
    （倒序 + 遇 G 就 `out[key]=0`）当 oracle ⇒ oracle 自己也是二元的 ⇒
    对拍 **24/40 假红**。"连续"在倒序回看里的正确写法是**遇 G 停止但保留
    已数到的值**。照抄结构会把被测对象的 bug 一起抄进基准：两边一起错，
    对拍照样"有结果"。
    ⛔ 反证物（`_legacy_binary_deadend`）**可以**逐字照抄 —— 它守的是
    J2c「旧不冷却 / 新冷却」，用途是证明语义**变了**，不是证明同值。
37. **跨语言判据同源不能靠注释声称，必须逐条对拍**。
    监督器（`supervisor/`，TypeScript）的全部价值是**独立于 Python 实现**
    重算哈希链；而 Python `json.dumps` 与 JS `JSON.stringify` 的
    number→string 与键序规则**并不相同** ⇒ 实测 **4 类形态全部 drift**：
    指数补零（`1e-07` vs `1e-7`）、指数阈值（`1e-5`）、整数值大 float
    （`1e21` ⇒ 定点 22 位 vs `1e+21`）、非 BMP 键序（码位 vs UTF-16 码元）。
    ⛔ drift 方向一律是**监督器把完好的账本判成"payload 被篡改"** —— 假红，
    且真因是语言差异，排查时会一路查到"是不是有人改了账本"。
    生产账本 1533 条里四类**全部 0 命中** ⇒ **潜伏地雷，不是已爆的雷**
    （合成用例永远撞不到：合成 ts 都是整数、reward 都是"正常"量级）。
    修法：TS 侧手写序列化器（整数值走 `BigInt` 定点、非整数 `<1e-4` 走
    补零指数、键按**码位**排序）；Python 侧对 `|int| > 2^53` **抛错**
    （JS 精度限制，TS 侧救不回来 ⇒ 只能在写入侧 fail-closed）。
    ⛔ 硬约束：**生产账本 hash 不许变**（改完 1533 条仍 `chain_ok=true`）。
    ⛔ 验证必须含**随机对拍**（3014/3014 逐字一致）+ 真实账本整链 ——
    只有边界用例会被"我自己挑的值"骗过去。
38. **接入层：外部实现自称成功，在结构上必须变不成 reward**。
    通用接入层（`aether/interop.py`）让任意 agent/skill（函数 / 对象 /
    CLI / HTTP）挂上来被调用。三条硬约束：
    · **`InvocationResult` 故意没有 reward 字段**，只有 `checks` ——
      reward 只由 `Verdict` 产生。于是"外部自称 ok / self_reported"没有
      通路可走（不是被过滤掉，是**不存在**）。C4 钉这条。
    · **失败必须能分诊**：`unavailable`（未注册）/ `rejected`（输入不合规）
      / `failed`（实现抛错或契约未兑现）/ `timeout`。混成一类 ⇒ 调用方
      分不清"没调用成功"和"调用成功但没用"，那是假成功的温床。
    · **不可信实现不许 inproc**：inproc 适配器杀不掉一个跑飞的实现 ⇒
      `timeout_enforced` 只能是 `"none"`。声明了超时却做不到 = 假仪表。
      不可信实现走 CLI / HTTP（`timeout_enforced="forced"`，真能杀）。
    ⛔ 端点层面：`/api/invoke` **只接受已注册能力的引用**，请求体里出现
    `impl` / `argv` / `code` / `transport` 一律 400 —— 否则这个端点就是
    "远程执行任意命令"，能力清单沦为摆设（F5 钉这条）。
    ⛔ 接外部 Python 对象（`transport: python`）等同任意代码进本进程 ⇒
    清单里必须显式 `trusted: true`，默认 false（不是默认放行）。
39. **聚合型机制（巩固/归纳/压缩）：产物恒等于输入 ⇒ 被自己的去重闸吃掉**。
    睡眠期巩固 `consolidate()` 恒产出 0，表层看像"L4 内容没信息量"，真根因
    在**循环结构**：`for n in pool: summ([n])` 逐条调抽取式摘要，而抽取式
    摘要取的是首句 ⇒ 对单条 note 而言**摘要恒等于原文**（实测
    `摘要 == 原文` 为 True）⇒ 写回 L3 时内容指纹必然命中它自己 ⇒ 一律
    `deduped`。生产库 33 条 L4 ⇒ **0 条**；把摘要换成"够长且唯一"的文本
    ⇒ 立刻 **33 条**。⇒ 机制本身是通的，卡在「产物 == 输入」。
    修法是**换聚合形态**：一组同构 L4 → 一条跨条归纳（模板骨架 + 各槽位
    取值分布），产物文本必然不同于任何单条原文 ⇒ 不会自我去重；同样一组
    再巩固仍会 dedup —— 那是对的（没有新信息就不该产出新知识），**但必须有账**。
    ⛔⛔ 记账必须分开（made / deduped / skipped_*）：只报一个数字的话，
    "没新信息"与"机制坏了"在输出里长得一模一样 —— 这正是本 bug 藏了两轮、
    被误判成"内容问题"的原因。
    ⛔ 分组键 =（归一化 source，**槽位数**）：只按 source 分，会让新旧两代
    模板混成一组 ⇒ 整组不同构 ⇒ 历史脏数据把新机制一起拖死（实测
    `skipped_not_isomorphic=33`）。⛔ 槽位数相同但**文字骨架不同**也必须
    拒绝 —— 否则会拿第一条的骨架编造出一个两条都不成立的"模式"。
40. **触发信号必须实测，不能按语义想当然**。
    巩固"只在空闲时跑"是对的，但我把触发条件写成 `rep.idle` ⇒ 实测三轮
    **`idle_rounds=0`，一次都没触发** ⇒ 等于把刚修好的机制又关掉了。
    原因：round2/3 是 `no_candidate_passed` —— **有候选、全被闸拒**，这恰恰
    是最该沉淀教训的场景，而 `idle` 只在"压根没事可做"时才为真。
    ⇒ 正确语义是「这一轮没提交 ⇒ 有失败素材可反思」（`uncommitted_rounds`）。
    ⛔ 触发条件本身必须是**读数**（`trigger` / `idle_rounds` /
    `uncommitted_rounds` 都进输出），否则"没触发"和"触发了但产出 0"
    又是一对分不开的孪生故障。
41. **能力必须可**叠加**：单值路由会让两个通用维度互斥 ⇒ 闭环必然停滞**。
    加了第二维通用修法 `fix_literals` 之后闭环**仍然**停滞，只是原因从
    `saturated:stagnated` 变成 `no_candidate_passed`；实测它的
    `dev_by_difficulty = {hard: +1.0, easy: −1.0}` —— 修好了 hard，
    **却把 easy 打回 0**。根因不在修法，在 `L1.routing[repair]` 是**单值**：
    装一个新技能就把旧的顶掉 ⇒ 控制流与字面量两个维度互斥 ⇒ 加再多通用
    修法也叠不上。⇒ 路由值升级为**有序链**（`append` 到链尾，`parse_route`
    是唯一解释处）。取证：单技能各修一层（1.0/0.0 与 0.0/1.0），链两层
    都 1.0，**换序同分**，held-out 三个环境两层都不掉。
    ⛔⛔ 链的语义是**叠加**不是「依次尝试到通过」：后者要为每项各跑一次
    执行判据 ⇒ reward 变成"试出来的"，违反「reward 只来自一次执行」，
    且成本随链长线性增长（观测改变了被测成本）。叠加只在末尾跑一次判据。
    ⛔ 反证必须钉在"退化回单值"上（B4/C4/F3）：不钉的话，"链修好了"与
    "恰好这个技能就能修"根本分不开 —— 两者输出都是 1.0。
    ⛔ 归一化只在**读取侧**做（单元素仍存成 `str`）：一律转 list 会让既有
    harness_id、账本、断言一起漂 —— 那是把"兼容"和"整洁"搞反了。

42. **同一份数据只能有一个写入点和一个读取点**。
    路由曾有两个写入处：`apply_gene`（append）与 `interop.bind_capability`
    （`routing[kind] = key`，覆盖）⇒ 同一个 harness "能不能叠加"取决于
    技能是谁加的。修法不是"把那行也改成追加"，而是收敛成
    `genome.route_with(...)`，契约同时盯住两条路径（路由值逐字相同 +
    幂等 + 顺序无关）。
    ⛔ 更隐蔽的同源问题在**选点口径**：变异器用
    `isinstance(v,(int,float))` 选点（`bool` 是 `int` 子类 ⇒ `False` 被
    `+1` 成 `1`），修法收集器却排除 `bool` ⇒ 两边序列长度不同 ⇒
    按索引对齐时**整体错位** ⇒ 把没坏的常量改坏。这比"修不好"更糟。
    现在两边共用 `environment.numeric_literals(tree)`。
    ⛔ 入参必须是**已解析的树**不是源码字符串：内部重新 parse 会得到
    另一棵树的节点对象 ⇒ 赋值改不到调用方那棵树上 ⇒ 修法静默失效。

43. **结构对齐宁可不动，不可错半格**（fail-closed）。
    三个修法在 `len(候选节点) != len(基线节点)` 时一律原样返回，
    不再 `min(len)` 部分对齐 —— 错位一次污染一片，没有"部分修"这种折中。

44. **落点要兑现，不只是存在**（2026-10-14 兑现审计）。
    对 `docs/FRONTIER_2026.md` 每条"V6 落点"做**运行时取证 + 可达性计数**，
    而不是 grep 符号 —— grep 到 `def foo` 只证明写过，不证明被调用过。
    审计抓出：`runtime.Trajectory` / `masking_ratio` / `assign_credit`
    从未被生产链路 import ⇒ masking 在生产上是**空账**（旁证：
    `attribution.py` 自己就写着"evolve 里 assign_credit 被调用 0 次"）。
    已修：`HarnessAgent.run()` 产出真实轨迹账并挂到 `AgentResult`。
    ⛔ 判据脚本自身也要有反证组：注入事故版必须让原判据翻转，
       否则"全绿"只是断言恒真（判据没有判别力）。

45. **边界清单必须逐条实测，不能凭记忆挑条目来修**（2026-10-14 · §29）。
    "已知边界"清单会朝**两个**方向失效，且两个方向都让人白干：
    ① 边界已被修掉而文档没改 ⇒ 照清单重修一遍已修好的；
    ② 真缺口不在清单里 ⇒ 一直没人管。
    实测结果：§14 写的"文件系统未隔离 / 内存无限制"**均已不成立**
    （audit 层 / mem_guard 早就生效），而清单里**没写**的 `ctypes`
    才是真缺口 —— 三种加载方式全部成功，原生 syscall 通道敞开着。
    ⇒ 常驻 `scripts/probe_boundary_audit.py` 逐条问"今天还成立吗"。
    ⛔ **要断言"拦不住"，先证明它"能跑通"**：关掉 deny 后同一段代码
       必须成功（对照组）。少了这条，"没跑成"可能只是代码本身没编译
       —— 本轮审计自己先踩了一次（攻击代码缩进错 ⇒ 看着像被拦住了）。

46. **计数与 evidence 必须分开存**（改写扫描器时 5 处全栽在这）。
    把"detail 里的总数"和"展示用的 evidence"合成一个有界数组 ⇒
    总数被窗口截断（真实账本 37 组 ⇒ 显示 5 组）。那不是省内存，
    是**改了读数**。一律：`xxxCount++`（真计数）+ 有界 evidence 窗口。
    ⛔ 改写判据后的验收方式是**与旧版对拍**：从 git 取旧实现，
    合成场景 + 真实账本（2271 条）逐条 diff，不等价就要说清是哪一处、
    为什么是刻意的。

47. **消融必须留占位步**（step 级反事实重放的隐形陷阱）。
    leave-one-out 靠 `(layer, action, occ)` 定位"去掉了哪一步"。
    消融后少一步 ⇒ 后续 occ 整体前移 ⇒ 第二次消融就对不上号 ⇒
    归因**静默错位**。跳过时照样 append 一条 `ok=False` 的占位步。
    ⛔ 判据/结构步（L5 验证、L1/L2）**不参与**消融：去掉判据 reward
       就没有定义，对它们返回 baseline（贡献 0）是**定义**不是"测出来
       它没用"。

48. **拦不住的条目不许留在名单里**（2026-10-14 · §30.2）。
    `resource` 一度写进原生扩展模块黑名单，实测 `import resource`
    **仍然成功**（runner 自用在先 ⇒ 已在 `sys.modules` ⇒ CPython 不再发
    `import` 事件）⇒ 名单看着有防线，实际没有 = **假仪表**。
    处置两条：① 从名单移除；② 改拦**动作** `resource.setrlimit`
    （拦动作比拦模块更准，且 `getrlimit` 仍能读 ⇒ 零误伤）。
    ⛔ 拦模块与拦动作的选择标准：**能拦住哪个就记哪个**，不是哪个好看。
    ⛔ 连带记账要直白：`import subprocess` 会先撞 `import:signal`，
       若只记 signal 会让观测侧读成"想装信号处理器"（实际是"想 fork"）
       ⇒ 归因错位让排障方向全反 ⇒ `subprocess` 也单列进名单。

49. **每加一道闸，所有对照组都要重审一遍**（2026-10-14 · §30.5）。
    沙箱从一道闸（事件级）变两道（事件级 + 模块级）之后，4 个对照组
    **集体恒红** —— 它们只清 `_AUDIT_DENY_EVENTS`，没清
    `_AUDIT_DENY_MODULES` ⇒ `import ctypes` 仍被拦 ⇒ "关掉 deny"根本
    没关干净。红在对照组自己身上最误导：看着像"防线失效"，
    实际是"对照组没关干净"。⇒ 契约 A3/F1/G5 与探针 X4 全部改为两道都关。
    ⛔ **加闸不是只改判据，是把所有"关掉闸"的地方同步更新**。

50. **声明与实际的落差必须被记账**（2026-10-14 · §30.4）。
    UCI 调用记账里 `seatbelt_active` 原先**硬编码 `False`**。问题不在
    这个值当前对不对（本环境确实没有），而在**将来真套上了也永远报
    False** —— 与 `rlimits_applied` 同一个坑（假仪表）。
    改为适配器**自己探测**并写回值 + 原因（不许空串冒充"已确认没有"）。
    ⛔⛔ 新增 `declared_but_unenforced`：spec 声明 `sandbox="seatbelt"`
       而实际没套上，此前**没有任何读数**暴露 ⇒ 调用方以为声明生效了。
       这是"声明了但没兑现"，与第 44 条同一条纪律。
       （H6 守着它非恒真：声明 `process` ⇒ 必须 False。）

51. **「调得通」不等于「读数可信」**（2026-10-14 · §23.1）。
    UCI 端到端实测（skill / tool / agent × CLI / HTTP 两个入口全通）之后，
    仍抓到三处失真，共同点是**接口都正常，错的是读数**：
    ① 超时只有形态读数（`timeout_enforced`）没有本次结果读数 ⇒ 声明 0.5s
    的 inproc 技能实跑 1.50s 而 `status` 仍为 ok ⇒ 补 `timeout_declared_s`
    + `timeout_declared_but_unenforced`（判据排除 `status=="timeout"`，
    被中断正是兑现的样子）；
    ② JSON 清单写 `checker` 会被**静默忽略**（判据是代码、不可序列化）⇒
    改为注册时显式 rejected 并给两条出路；
    ③ `/api/capabilities?kind=X` 的 `n` 是注册表总数而非过滤后条数 ⇒
    `n` 跟随过滤、总数另给 `n_total`。
    ⛔ 判据类改动必须配**"放宽参数"对照组**：否则 `status=timeout` 可能只是
    "能力根本没跑起来"（F1b 钉住）。

52. **两个入口必须同源装载；热装载默认关闭**（2026-10-14 · §23.2）。
    CLI 与 HTTP 此前**各写一份**装载接线（`Registry→load_manifest→Gateway`）。
    底层判据同源，但入口接线两份 ⇒ "CLI 看得到被拒的能力、HTTP 一声不响"
    的幽灵差异 ⇒ 抽 `interop.mount_capabilities` 作为唯一接线，三处
    （CLI / HTTP 启动 / HTTP 热装载）都调它，契约用 fingerprint 对拍钉住。
    新增 `POST /api/capabilities/reload`，三道闸：**默认关闭**（须
    `--caps-hot`；热装载 = 运行时改变本进程能力集，比 invoke 更靠近信任
    边界）、**只接受清单路径**（不接受内联能力描述，否则 = 远程注入实现）、
    **装载 0 条不替换**（换空 = 能力凭空消失，比装载失败更难查）。
    ⛔ 有 skip 回 207、装载 0 条回 409 —— 只回 200 会让只看 HTTP 码的人
    漏掉 skipped（与第 51 条同源）；409 没替换 ⇒ **不计数**。
    ⛔ 热装载存在时**磁盘清单 ≠ 进程清单** ⇒ 必须暴露 `source` /
    `hot_reload` / `reload_count`，否则靠猜。

53. **进化循环必须能止损；摄入必须查往返**（2026-10-14 · §23.3 / §23.4）。
    ① 全局预算 `RunBudget`（rounds / seconds / propose_calls / eval_runs），
    任一耗尽即停并记账；**默认不限**（改默认口径会让旧基线不可比），
    生产接真 LLM 必须显式设，`budget.limited` 是"有没有止损"的读数。
    ⛔ 计量挂在**唯一咽喉**（`evaluate_dev` / `proposer.propose`），逐个
    调用点各记一次 ⇒ 迟早漏一处 ⇒ 止损形同虚设。⛔ 提议预算**在调用
    之前**查（真 LLM 时调用完再查钱已经花了）。⛔ 预算停止与"无事可做"
    是两个原因，混在一起会把"钱花完"记成"没得做"。
    ② 知识摄入 `aether/learner.py`：**不自己实现判据/去重/嵌入**，全复用
    `MemoryOS` 的闸（V5 事故正是 learner 自搞一套 ⇒ 39x 重复还报成功）；
    `channel` 固定 learner 不可改（否则写入权就成了调用方自己说了算）。
    ⛔⛔ **写入侧放行 ≠ 加载侧放行**：实测摄入 2 条短知识，进程内
    `written=2 / retrievable=True`，而 `save → load` 往返后 **2 条全被
    `rejected_unverified`（过短）拒载** ⇒ 知识根本没留下 ⇒ 报告加
    `n_below_min_content`、CLI 加 `persist_probe`（存盘后重新 load 数还在
    几条）、退出码把"丢了"算失败。
    ⛔ 取证探针必须用**完整内容**：拿截断的 preview 去比对库里的
    content ⇒ 恒不相等 ⇒ 取证永远 False（假故障）。

54. **并发先取证；UI 不做假按钮**（2026-10-14 · §23.5 / §23.6）。
    ① 并发评估**先取证再动手**（常驻 `scripts/probe_parallel_feasibility.py`）：
    单次 runner ~99ms（真子进程）、4 workers 加速 1.74–2.05x、结果逐条一致、
    账本并发 append 链仍完整。⇒ `parallel_workers` **默认 0=串行**（改默认
    口径会让历史账本不可比；且逼近 timeout 的任务在并行下可能被挤成
    timeout ⇒ 结果会变）。
    ⛔⛔ 并行**只包住 runner 计算**，run_id 分配 / `_runs` / `store` /
    `ledger.append` 一律串行：`_run_seq += 1` 非原子；事件顺序漂移 ⇒ 账本
    不可复现。⛔ 结果按输入序归位。⛔ 读数 `parallel.batches/runs` 记本轮
    增量且由 `run_round` 薄包装填写 —— 写在 `_run_round` 末尾会让 6 处提前
    return 的轮次读到 `None`（读数必须在**所有**返回路径上有值）。
    ⛔ 契约要断言**真并发**（探针测最大并发度>1）：只断言"走了并行分支"不够。
    ② 桌面控制台（§23.6）由 serve **同源托管**，`aether ui` 一键开 Chrome
    `--app` 窗口。⛔ 显式绕代理（HTTP_PROXY 会把 127.0.0.1 也代理掉 ⇒ 白屏）。
    ⛔ **没有端点的动作不做按钮**（进化/摄入只给 CLI 命令）；热装载未开时
    按钮禁用并写明会 403。⛔ 调用结果原样展示超时落差读数（只显示成功/失败
    会把它藏起来）。⛔ 端口被占**顺延**不复用（否则连到别人的服务上）。

---

## 5-bis. 第 55 条起：r33 收口（2026-10-14）

这一轮的形状与前几轮不同：工作区里躺着 **7 个核心文件、+1572/−369 行**的
修改，测试是红的，而**没有任何测试、账本或文档记录它**。收口它比继续找新
缺口更值钱 —— 因为"改到一半的代码 + 红的测试"是最危险的状态：它看起来像
新功能，实际是**未验证的破坏**。

### 55. **改到一半的改动必须按"结论"收口，而不是按"意图"**

14 组编号改动里，有 3 组是**方向错的**，而它们的注释都写得很自信：

- **舍入（ledger）**：注释说"Python `round()` 是银行家舍入，JS `Math.round`
  是 half-up，两端 hash 不同"，于是把 Python 改成 `floor(x+0.5)`。
  **前提是错的**：本仓库对拍的 TS 实现是 `roundHalfEven`（取偶），
  `supervisor/src/selfcheck.ts` 里就有 `...736.5 ⇒ ...736` 的断言。
  ⇒ 改动**制造**了它声称要消除的漂移（2026-10-13 已实测修好过一次）。
  撤回到 `round()`，并把这条契约钉进 Python 侧（`contract_ledger_test` I 组），
  这样"只在 Python 侧跑测试"也能发现它 —— 原来唯一的守护在 TS 侧。
  ⛔ 教训：**改跨语言契约前必须读对端实现**，不能按语言常识推断
  （JS `Math.round` 确实 half-up，但我们比对的不是它）。
- **`Capsule(genes=())` 不得为空**：新加的构造校验，理由是"胶囊不该没有基因"。
  但空 genes 是**良定义的生产哨兵** —— `TemplateProposer.propose` 在
  "无可用变异"时就返回它，而 `evolution._run_round` 对 `propose` 的调用
  **没有 try/except** ⇒ 技能全部接通时进化循环当场炸。
  ⇒ 撤回该校验，并补 C7 组把哨兵语义（恒等应用 / 零爆炸半径 / 两条可达
  路径）钉住。⛔ 构造期校验只能守**真的从不成立**的不变量。
- **`replay_fn` 3 元签名**：调用侧（`runtime.assign_credit`）改成
  `(task_id, harness_id, steps)`，生产者（`agent.replay_fn` 的闭包）没跟上
  ⇒ 每一步都 TypeError ⇒ 整份归因降级 `available=False`。改的是**代码**
  （生产者）而不是测试；同时把 `harness_id` 真正消费掉（与闭包捕获的 id
  不一致即拒绝：那是"换 harness 重考"，不是归因）。
  ⛔ 残留的同形态漂移（2 元 lambda）会让断言变成**空真**（`per_step=[]`
  时 `not any(...)` 恒真）—— 已一并修掉。

### 56. **"修了"不等于"生效"：假校验比缺校验更坏**

`verifier` 里三处校验**写了但永远不会触发**：

- `_coerce_pass(name, bool(p))` —— 调用点先把 `p` 转成 `bool`，
  `isinstance(p, bool)` 因此恒真 ⇒ 类型闸是死的。实测
  `all_of("n", [("c","false","ev")])` 不抛错且 `passed=True`，
  与它自己的 docstring 正好相反；
- `check_verdict` 对 `checks[*].passed` 严格、对**顶层 `passed`** 仍走
  `bool()` ⇒ 账本里一条 `{"passed": "false"}` 被读成 `reward=1.0`（双标）；
- `Verdict.__post_init__` 的 `evidence=None` 归一化分支写的是普通赋值，
  而 `Verdict` 是 `frozen=True` ⇒ 实测直接 `FrozenInstanceError`
  （同一个函数里其他字段都用了 `object.__setattr__`，只有这一行漏了）；
- `all_of/any_of` 不传 `caliber` 时指纹从 `env_fingerprint([names])` 退化成
  **空串** ⇒ 口径闸静默失效（而唯一生产调用点恰好不传）。

⇒ 四处全部补齐，并加 G 组：**每条校验都必须有一条"不抛就是死的"的反证**，
外加"合法输入照样通过"（闸不是恒红）。⛔ 缺代码会被发现，假校验不会 ——
它让"非 bool 的 passed 被静默翻成通过"这件事继续存在，而读数看起来很干净。

### 57. **并发正确性不做微优化交换**（实测数据说话）

`Ledger.append()` 里加过一个"文件没变就跳过重读尾巴"的快路径
（`(size, mtime_ns)` 缓存）。实测：重载下 24 轮 × 4 进程并发 **1 轮分叉**
（现场：两个进程都在 `seq=6` 写出指向同一个 `seq=5` 的 `prev_hash`，
链从那里断开），而它省下 **0.068 ms/条**（0.153 → 0.085 ms，2000 条账本）。
⇒ **撤掉快路径**，锁内无条件重读尾巴（仍是 O(尾巴) 而非 O(n)）。

⛔ 中间还试过"长度 + 本进程已写字节数"这个更严的判据：它**仍然分叉**
（同样的概率）。这排除了"时间戳粒度"这一种解释，说明问题在
"用一个可失真的快照去代替锁内的真实读"这个**形态**本身。
⛔ 契约侧同时补了三件事：并发组从 1 轮加到 4 轮（单轮绿证明不了并发）、
检查子进程退出码（原版不看 rc：子进程崩了会伪装成"链断了"）、
J 组用**反证**钉住"别的写者写过之后本次 `prev_hash` 必须指向它"
（对照物必须用对方 `append` 返回的 Event，回头再读会读到我们自己写的那条
⇒ 断言空真）。

### 58. **测试助手也要跟着契约走**（否则红的是判据、坏的是助手）

`contract_difficulty` 一次性红了 **13 项**，现场看起来像"泛化闸坏了"：
`by_difficulty` 全空、候选分恒为 0。真因是测试助手
`_ok(tid)` 把**整数**当 `Check.name` 传，而本轮新增的"name 必须是非空
字符串"校验让每个 verdict 在构造处抛 `ValueError` ⇒ 被 `evaluate` 记成
error verdict（reward=0）⇒ **候选分永远是 0**，而异常轨迹里只有一行
`error_verdict`，不显眼。
同形态还有：`MemNote` 冻结后测试里 `n.tags = ...` 抛
`FrozenInstanceError`；`prune` 的测试夹具漏了 CLI 有的 `attach_ledger`
⇒ 留痕前置条件挡住全部删除 ⇒ 真剪恒 0，**连带 A2/A5 变成空真**。
⇒ 助手与夹具一律改成与被测对象同形；`prune_dryrun` 也补上
`will_delete` / `blocked_by`，让"报的数 == 真剪会做的数"可被断言
（原版报 `n=2` 而真剪 0 条）。
⛔ 同族还抓到一处**静默归零**：`cli.py` 读 `prune_dryrun()["victims"]`
（当时根本不返回该键）⇒ `aging_events["pruned"]` **恒 0** ⇒ 老化读数对
"库在增殖"永久失明。

### 59. **节点不在 PATH 也不许静默变绿**

`contract_traceability` 的跨语言对拍直接 `node src/index.ts`，而本机
`node` **不在 PATH 上** ⇒ `FileNotFoundError` 把整份契约打死，
E2–E5（监督器误报、严重度统计）**一条都没跑**。⇒ 加 `_find_node()`：
PATH → 项目约定路径（`~/.workbuddy/binaries/node/versions/*/bin/node`，
与 `scripts/demo_evolve.py` 同源）→ 常见前缀；真找不到就**明确报红**
并说清原因。"跑不了"与"跑过了没问题"必须能分辨。

### 60. **运行的中间态必须可恢复 —— 中断的代价不是"浪费时间"**

`--rounds 20` 在第 17 轮崩了，重跑的后果比"再来一遍"严重：账本
append-only 且没有幂等键 ⇒ 前 16 轮事件被**再写一遍** ⇒ 账本里出现两个
"第 3 轮"；`_run_seq` 锚来自账本 seq ⇒ 前后两半的 run_id 不同 ⇒ 投影对账
时它们看着像两次独立实验，其实是**一次被劈成两半**。

⇒ `aether/checkpoint.py` + `evolve --checkpoint/--resume`。三条硬约束：
① **轮号接续**（否则账本自相矛盾）；② **换环境拒绝**（换尺子 / 换配置 /
账本回退，三类分开报 —— 合起来说"不一致"会让人去改 checkpoint，而正确
处置是"别恢复"）；③ **checkpoint 是派生量**：写失败不得中断进化，但必须
记账（`evolution.checkpoint_failed`），否则"我开了续跑"是假仪表。

### 61. **默认安全必须由启动期强制，不能靠隐式约定**

六个端点此前零鉴权，唯一保护是"默认绑 127.0.0.1"这条**隐式约定** ——
`--host` 一个开关就能拆掉它，且拆掉时没有任何提示。⇒ `serve --api-token`
（或 `V6_API_TOKEN`）：**监听非回环地址且无令牌 ⇒ 拒绝启动**（fail-closed）。
⛔ 比较用 `hmac.compare_digest`（`==` 会按字节提前退出 ⇒ 逐字节可还原令牌）；
⛔ 鉴权状态必须可读（`/api/status.auth`）—— 否则"服务上没上锁"事后无从查证。

### 62. **要保护的是不可重建的那个**

`rebuild` 能从账本重建 SQLite 投影 ⇒ 投影有退路；而唯一事实源
`ledger.jsonl` 自己**没有任何备份命令**。即：能重建的被保护了，不能重建的
没人管。⇒ `aether backup / restore`：自带 sha256 清单、**备份完立刻验副本
的链**（不验的话"拷成功"会被当成"备份可用"，半截拷贝要到恢复那一刻才暴露）、
恢复前先留当前状态（覆盖写不可逆）、校验不过一律拒绝。
⛔ `doctor` 把"没有备份"判为 **fail** 而不是 skipped —— 唯一事实源无退路
是坏消息，不是"没查"。

### 63. **"没查过" ≠ "查过没问题"**

诊断能力此前散在 `status` / `verify-chain` / `rebuild --check` 四个入口，
没有一条命令能回答"现在健康吗"；唯一的自检入口在 TS 侧，只跑 Python 的人
拿不到一次成型的结论。⇒ `aether doctor`：每项**三态**（ok / fail / skipped），
skipped 必须带原因与出路、**不计入通过**、且在汇总里被点名。
⛔ doctor **只读**，每项失败给 `action`（该跑哪条命令）但自己不动手 ——
体检恰恰是在数据可疑时跑的，自动修改 = 未告知就改数据。

### 64. **探活与体检是两个端点，不能拿重的那个顶替**

最轻的端点 `/api/status` 也要全量扫账本（4 个聚合器）⇒ 拿它当 liveness，
账本一大就把探活拖成超时 ⇒ 编排层会重启一个**其实是健康的**服务。
⇒ `/api/health`：只做 O(1) 的 stat（在不在 / 多大 / uptime / 有没有上锁），
**不返回事件列表**。契约里用"5 次探活 < 1 次 status"实测钉住它。

---

## 5-ter. 第 65–68 条：r35 收口（2026-10-14）

这一轮补的四项有一个共同点：它们**都不影响单次运行对不对**，只决定
"这套系统能不能被信任、被交接、被复盘"。

### 65. **判据/路径只能有一份；两处同形 = 两处都会漏**

`providers.TemplateProposer` 手搓 `Gene(target=f"L3.skills.{n}")`，而
`genome.gene_add_skill` 写了**同形的一份**。⇒ 收敛为 `genome._t_skill/
_t_route/_t_tools/_t_policy` 四个唯一构造点，提议器改为复用。
⛔⛔ 危险之处在于漏的那处**不报错**：`apply_gene` 对未知层抛错，但对
"已知层的错误子键"放行 ⇒ 产物落在没人看的地方。
⛔ `gene_route`(set) 与 `gene_route_append`(append) **不许互相替换**：路由是
链，set 覆盖 ⇒ 装一个新技能顶掉旧的 ⇒ 两个通用能力维度互斥 ⇒ dev 的 easy
从 +1.0 掉到 −1.0 ⇒ 候选永远过不了闸 ⇒ 闭环停滞。契约 D 组用"append 三条
都还在 / set 只剩最后一条"实测钉住。
⛔ 静态闸用 **AST 扫代码字符串**（跳过 docstring 与注释）—— 正则扫文本会把
注释里的说明当成实现（本项目已栽过："断言文案，不断言行为"）。
⛔ 验收用**旧版对拍**：从 `git archive` 取改动前的 aether，导出提议器产物逐字
diff（实测零变化）。

### 66. **诊断走 stderr 并可分级；结果走 stdout**

收口前 `aether/` 里 `import logging` **零次**，46 处诊断是 `print(...,
file=sys.stderr)`。⇒ `aether/logging_setup.py` + `--log-level` / `--log-json`。
⛔ **默认 INFO**：改造前这些诊断全部可见，默认 WARNING 会让它们凭空消失
⇒ 静默改了用户可见行为。
⛔ **库层不装 handler**（与 `checkpoint_path` 默认 None 同理）：装 handler 是
进程级副作用，不该由 import 触发；只由 cli/serve/ui 显式 `setup()`。
⛔ **`setup()` 幂等**：重复调用替换自己的 handler，不叠加（否则每条打两遍）。
⛔ **loggor 名显式传 `"aether.cli"`** 不用 `__name__` —— `python -m` 时
`__name__` 是 `"__main__"` ⇒ 三个进程的日志全叫 __main__（契约 C2 实测抓到）。
⛔ 判据：stdout 里**不能出现日志行**（带 ts+级别+`│`，或 JSON 的 `"level"`）。
注意 `status` 的人类可读摘要（"账本链：✅ 完整"）是**产物**不是污染 ——
第一版判据写成"stdout 必须是纯 JSON"⇒ 误红了既有设计。

### 67. **实验参数必须进账本，否则事后无法复现**

`evolve` 的命令行参数此前**只活在终端里**。账本能回答"改了什么"，却回答不了
"是用什么口径跑出来的" ⇒ 同一份代码跑出不同结果时，无从分辨是随机性还是参数
变了。⇒ 跑之前写 `evolution.run_config` 事件（含 seed/rounds/group/proposer/
dev_env/heldout/parallel/embedder/budget/checkpoint/resumed_from）。
⛔ `resumed_from` 记 `0` 而不是 `None`：None 分不清"不是续跑"和"忘了记"。
⛔ 契约断言记的是**实际传入值**（`--seed 7` 时不能记成默认的 0），否则记常量
也能让断言变绿。

### 68. **归档不重造判据；归档是数据不是代码**

`backup --archive` 打成单个 `.tar.gz`（便于传输），`restore --from x.tar.gz`
先解包再走**完全相同**的恢复流程。⇒ 不另写一份"归档校验"—— 打包格式对不对
与备份内容对不对是两件事，各一份判据就够（第 65 条同一条纪律）。
⛔ **防路径穿越**：归档里可能带 `../`，解压前逐条检查目标仍在 dest 之内。
⛔ manifest 补 `path` 字段：此前记了内容却没记位置 ⇒ 事后拿到 manifest 还要靠
人记得它拷到哪去了。

---

## 5-quater. 第 69–71 条：r36 收口（2026-10-14 · 统一参数与接口）

### 69. **入口参数只能有一份；三个入口各自声明 = 三个真相**

`cli` / `serve` / `ui` 三个入口共用一批参数（root / port / log-level / log-json /
caps / caps-hot / api-token / cors / console）。此前各写一遍，实测出四处漂移：
`--log-level` 的 choices 在 ui 里是**硬编码**的一份列表；`--port 8611` 三处各写
一遍；`--root` 三个入口各自 `Path(__file__).parent.parent`。⇒ 公共参数收进
`aether/entry.py`，三个入口只调 `add_root_arg / add_log_args / add_serve_args`。
⛔ 契约直接遍历三个 `build_parser()` 的 `_actions` 逐项比对（不是抓 `--help`
字符串 —— 那会把"改了 help 文案"误报成"参数不一致"）。

### 70. **透传清单不手写：以"接收方的 parser"为准动态导出**

⛔⛔ 收口前最坏的一条：`aether ui --log-level DEBUG` **静默无效** —— ui 起服务
的手写转发清单漏了 `--log-level / --log-json / --api-token`，命令行开关丢了却
一切正常。⇒ `entry.build_forward()` 遍历**接收方** parser 的选项导出清单：
以后给 serve 加参数，ui 要么自动跟着长出来，要么在这里**抛 ValueError**
（缺属性 ⇒ 抛错，绝不静默跳过 —— 静默跳过 = 开关失效却无人知晓）。
⛔ 三层包装（cli → ui → serve）每一层都只认**下一层**的参数：拿 serve 的 parser
去导出给 ui 的命令，会多出 ui 不认的 `--console` ⇒ rc=2（实测抓到过）。

### 71. **子 parser 的默认值会盖掉主 parser 已解析的值（argparse 陷阱）**

`argparse` 派发子命令时会把子 parser 的 namespace **整个拷回**主 namespace，
**包括它的默认值**。于是 `aether --log-level DEBUG ui` 里的 DEBUG 会被 ui 子
命令的默认值 INFO 悄悄盖掉；`aether --root /x ui` 同理。⇒ 子命令的参数一律用
`default=argparse.SUPPRESS`（没解析到的键不进 namespace）+ `normalize_log_args()`
兜底。契约 D13 用**事故版**（不用 SUPPRESS）证明这个坑真实存在 —— 反证不翻转，
就说明这条闸是恒真的空账。

### 72. **数据布局是声明式的，且只有一份**

`data/ledger.jsonl` 这组路径此前在 **4 处各自手拼**（cli._paths / doctor._paths /
serve 三处 / backup.BACKUP_TARGETS）。改布局要改四处，漏一处 ⇒ `status` 读的文件
与 `backup` 带走的文件**不是同一个**—— 备份报成功，恢复时才发现少东西。
⇒ `aether/paths.py`：`ROOT` / `paths()` / `data_dir()` / `flat_name()` /
`BACKUP_TARGETS` 唯一事实源，四处消费者全部引用它。
⛔ 静态闸只判**路径拼接**形态（`"data" / "ledger.jsonl"` 的 BinOp），不判所有字符串
常量 —— `"data"` 也是 HTTP 响应里的常见键名，按常量判会误伤；误伤一次就会有人
加白名单，白名单一开这条闸就废了。

### 73. **降级 / 失败读数必须带机器可读的 `reason`，不许只给泛泛的 message**

经桥调 REA 每次 `rc=1`、`code=cleanup_incomplete`，调用方只看得到
"Provider cleanup could not be fully confirmed"。真因埋在

```
details.diagnostics.primary_error.diagnostics.reason
  = "process table could not be inspected: spawn EPERM"
```

⇒ 为此花掉大量二分（env 净化 / stdin / cwd / `--format` / 管道 /
临时目录堆积 / 后台负载 **全部无差别复现**）才定位到：REA 收尾靠 `ps`
查进程表，而**沙箱拒 `ps`**。

⛔ 判据：任何 `degraded` / `error` / `timeout` 读数**都**必须带机器可读
reason；只有 message 的降级 = 让下一个人重做一遍二分。
   · reason 必须**下钻取** —— 顶层字段常常只是复述 message；
   · 契约钉一条「降级 ⇒ reason 非空」，否则这条读数会静默退回泛文案。
⛔ **超时不许混进 `degraded`**：一个是「跑完了但收尾没确认（结果可用）」，
   一个是「没跑完（结果没有）」，归因完全不同。

### 74. **一个布尔不许兼两职**

桥的 `ok` 只表示「**拿到结果了**」，但调用方极易读成「引擎都齐全」。
实测：`ok=true` 的同时 REA 自报 `healthy=false` / `environment_healthy=false`。

⇒ 拆成**两个独立读数**：`ok`（这步成没成）+ `rea_self_check.healthy`
（环境齐不齐）；且 `healthy` 为假时必须列出 `failing_checks`
（否则只知道坏了，不知道缺什么）。

⛔ 判据：一个布尔若可能被读成两种意思，就拆。同形态已栽三次 ——
   UI「面板写了 ≠ 渲染了」、桥「ok ≠ 引擎齐全」、闸「全 0 ≠ 失效」。

### 75. **外部工具的 remediation 文案只是建议，不许当诊断结论**

REA 报 `Decompiler executable may not be compatible with your system and may
need to be rebuilt`，看着像架构不对 ⇒ 顺着文案去折腾 `-arch` 是白费力气。
它实际是 Ghidra 原生二进制用 `FileManage::discoverGhidraRoot(argv[0])`
从**自己的可执行路径**上溯找 `Ghidra` 目录，而它被 `analyzeHeadless` 从
**项目临时目录**拉起 ⇒ 上溯必然失败。

⛔ 判据：`remediation` / 报错文案 = **给人类看的下一个动作**，不是根因。
   诊断必须来自**实测读数**（退出码 / stderr / reason 字段）。
   本例真正的修法：`SLEIGHHOME` 与 `GHIDRA_INSTALL_DIR` **设成同一个目录**。

### 76. **外部工具的隐式依赖要先取证：文档没写 ≠ 不存在**

REA 在关闭 provider 前会 exec 一次
`ps -axo pid=,ppid=,pgid=,uid=,stat=,command=`（见
`node_modules/rea-agents/dist/process/ProcessOwnershipObservation.js` 里的
`execFileOutput("ps", ["-axo", ...])`），用它扫进程表确认 worker 进程组已退出。
**这条依赖在任何文档里都没写**。它拿不到进程表时 fail-closed：把一次**分析成功**
的运行记成 `cleanup_incomplete`（rc=1）并丢弃结果 —— 看 `message` 是同一句泛泛的
话，看 `reason` 才知"读不到进程表"，再钻一层才是 `spawn EPERM`。

⇒ 接一个你控制不了的外部工具前，先去它的**产物**里 grep 它到底 exec 了什么，
   别信 README 的"Requirements"章节。
⇒ 同源：探测**必须**用与真实调用同形态的输入 —— 这里就是真的 exec 一次
   `ps -p 1`，而不是 `shutil.which("ps")`。`/bin/ps` 在本环境**存在**且权限位
   齐全，只是 exec 被沙箱拒（errno = EPERM，而 `pgrep` 可用）⇒ 只看
   `which` 会把"有文件"读成"能用"。

### 77. **兼容层要自报家门，且只在原生实现不可用时接管**

`scripts/shims/ps_shim.c` 用 libproc（`KERN_PROC_ALL` + `struct kinfo_proc`，
与 `ps` 同源）补上进程表。三条纪律：

⛔ **不编造字段**：某个进程读不到 ⇒ **整行跳过**，不填占位。宁可少一行，
   也不能让调用方拿"看起来齐但字段是编的"的表去判活。
⛔ **不追求与 ps 全等**：只实现消费者真正请求的 spec；不支持的参数组合
   一律 exit 64 + stderr。静默猜一个像样的输出 = 假仪表。
   STAT 只吐**首字符**：`+`/`s`/`X` 需要"控制终端前台进程组"信息，
   拿不到就不猜（且消费者的判活只看 `startsWith("Z")`，首字符足够）。
⛔ **只在原生不可用时接管**：系统 `ps` 能跑就让它跑 —— 那才是权威实现，
   无条件接管是自找麻烦。

⇒ 读数记账：`process_table_source ∈ {system:ps, shim:libproc, unavailable}`。
   调用方必须能看出这次的进程表**是谁给的**（与第 75 条同一条纪律：
   声明与实际的落差必须记账）。

**78. 外部工具的真实路径靠运行时取证，不靠文档或直觉猜。**
   接一个你控制不了的外部工具前，先 `pgrep -fl <工具名>` 读它**实际** exec 的
   argv[0]，再决定往哪装补丁 / 兼容层。
   ⛔ 本次差了一层 `build/os`：装在 `.../Decompiler/os/mac_arm_64/decompile`
   的兼容层**零调用**，真实路径是 `.../Decompiler/build/os/mac_arm_64/decompile`
   （REA 两个位置都认，其 remediation 文案明说 “or its build/os equivalent”）。
   ⛔ 「文档说在 X 目录」不构成证据；「按命名规律推出来应该在 X」更不构成。
   ⇒ 兼容层必须**自报家门**，让调用方能验证「改动是否真被走到」（第 77 条同纪律）。
   ⇒ 取证后**必须清理**：临时包装器、孤儿进程、`/tmp` 日志一律复位。

**79. 一个源码树有多个 `main()` 时，先确认哪个产物才是被调用方加载的那个。**
   ⛔ 「能编过」≠「编对了」。判据是**安装目标 / 入口契约**，不是编译成功。
   ⛔ 上游构建脚本可能**硬编码**平台分支（本次 Ghidra 12.1.4 的 Darwin 分支
   写死 `ARCH_TYPE=-arch x86_64` 与 `OSDIR=mac_x86_64`，上方还挂着原作者
   TODO 明说不支持 arm64）⇒ 跨平台构建必须显式覆盖，否则**静默**产出错架构产物。
   ⇒ 判定信号：**卡在 I/O（CPU≈0）而不是在算** ⇒ 先怀疑协议/形态对不上，
     别先去调超时、加机器（与第 76 条「文档没写 ≠ 不存在」成对使用）。

### 80. **子进程入口的 argv 必须严格：不认识的参数报错，不许静默忽略**

`scripts/rea_bridge.py` 的输入**只有 stdin**；argv 只负责钉死 `--command`。
旧实现只是在 argv 里找 `--command`，其余 token **一律丢弃**：

```
rea_bridge.py --command analyze --params '{"path":"/x"}'   # 看起来完全合理
```

⇒ `--params` 被吞掉，脚本照常走到 `sys.stdin.read()` 等 EOF
⇒ **表现为"桥挂住了"**，日志里没有任何异常
⇒ 归因时极易被引到"引擎慢 / 沙箱又禁了什么"上去（这两条都**查过**了）。

⛔ **静默忽略 = 把"参数没生效"伪装成"命令正在跑"**。本次排查绕了一大圈，
   真根因是**我自己传参的方式桥根本不支持**，而桥对此一声不吭。
   ⇒ 与第 69 条「入口参数只能有一份」同源：那一管的是**发送侧**
      （三个入口各自声明 = 三个真相），这一条管的是**接收侧**
      （契约明文写着"输入只在 stdin"，那就必须对 argv 里多余的东西报错）。

⇒ 现在未知 argv ⇒ **rc=2** 且在 stderr 点名那个参数（契约 I1–I3，
   I3 是反证：合法形态 `--command capabilities` 不被误伤）。

### 81. **拿外部工具做对拍前，先用对照组确认"那把尺子"自己怎么读数**

给 Ghidra 的原生通道找外部真值时，我用 `otool -l` 数 Mach-O 的段数：

```
segname __PAGEZERO        ← 段命令
cmd LC_SEGMENT_64
segname __TEXT            ← 段命令
sectname __text           ← 节
segname __TEXT            ← ⛔ 这是**节的所属段名**，不是新的一段
```

⇒ 按「segname 行数 + sectname 行数」数出 **14**，而真实段命令只有 **4**
⇒ 若直接拿 14 去对拍，会把**通道正常**误判成**通道输出错了**。

⛔ **对拍工具不是免检的** —— 它的输出格式也要先取证。与「判据必须与被测
   对象同源」「反证要跟着判据一起改」同源，但新的一面是：**外部权威工具
   同样会骗人**，而且骗得更像真的（因为它是权威）。
   ⇒ 判定信号：真值和上游读数对不上时，**先怀疑自己的尺子**；
     用一个结构已知的最小样本把尺子校准一遍，再回来判上游。

⇒ 契约 J 组的真值口径因此写成 `(LC_SEGMENT_64 数) + (sectname 数)`，
   并且**不钉段的绝对个数**（随工具链漂），只钉它与 otool 一致。

---

### 82. **词表/名单里的条目必须有"真实生产者"，否则是假仪表**

`evolution._ERROR_TO_KIND` 写着 7 条「失败信号 → 弱点类别」的映射。用被测
对象**自己的** `classify_error` 对 **649 条真实失败串 + 边界串**做全量对拍，
暴露出三个问题：

```
· 4 条（no_skill / skill_not_found / tool_not_allowed / primitive）
  在整个代码库里**只出现在词表自己**（各 1 处）⇒ 永远命中 0 次 —— 看着
  有 4 条分类规则，实际只有 1 条在跑；
· 真实生产者 `PermissionError: 缺工具 ['python_exec']`（agent.require_tools
  的 L2 闸）**没有任何 needle 覆盖** ⇒ L2 层**结构上不可达**：哪怕真的缺
  工具，也只会被归成"缺技能" ⇒ 去加一个不解决病因的技能（"看着在动"）；
· 宽泛词 `memory` 会把 `some memory error` 这类文本误判成 `recall_miss`。
```

⇒ 只保留有生产者的条目；对拍确认 **649 条真实串的分类零变化**，而**漏检
（缺工具）与误伤（memory）各修掉一处**。

⛔ 判据（契约 K 组，静态自证）：**每条 needle 必须在代码里有字符串生产者**。
   两处必须做对，否则判据恒真：① 用 AST 取**字符串字面量**（注释里提到
   needle 不算生产者 —— 生产者是"代码会产出这个串"）；② **排除词表自身
   所在的那些常量**（不排除 ⇒ needle 自己就是"生产者"）。K2 用注入假条目
   做反证，确认判据会红。

与「拦不住的条目不许留在名单里」是同一条纪律，只是这次落在**词表**上。

---

### 83. **默认关的记账开关 = 假仪表**（能力调用 `cap.invoke` 从 0 次说起）

```
账本里 `cap.invoke` 事件 0 次。查下来是**两层缺口叠在一起**：
  · `mount_capabilities()` **完全不收 ledger** ⇒ CLI / HTTP / doctor 三个
    入口造出来的 gateway 都没有账本；
  · `Gateway.invoke(..., write_ledger=False)` **默认关**，而全代码库
    **没有任何调用方传 True**（grep 只有定义与使用两处）。
```

单独修任何一层，读数都不会从 0 变成 1 —— 这正是它藏得久的原因。

⛔ 判定信号：**一个开关如果从来没有调用方打开过，它就不是开关，是装饰**。
   ⇒ 要么接线（这里改成"挂了 ledger 就写"，不想记的调用方须**显式**传
   `False`），要么删掉。
⛔ 与第 80 条同族：那条管「参数没生效伪装成命令在跑」，这条管
   「记账没生效伪装成系统在跑」。同族的还有：**写进 dict 却没人读的字段**
   （`aging_events["prune_candidates"]` 曾是这样）也算假仪表的一种。

---

### 84. **验证流程自己也要能被证伪：路径钉死 ⇒ "验过了"是假的**

「提交前验证干净检出可运行」这一步本轮**真的做了**，结果是它一直在骗人。

```
worktree @537dda9 全量契约：33 绿 / 1 红
扫 tests/contract_*_test.py 的 ROOT 解析方式：
  27 个写死 ROOT = Path("/Users/linshaojie/V6")   ← 导入主仓库、读主仓库 data/
   5 个用 Path(__file__).resolve().parents[1]     ← 真正测检出
   2 个无 ROOT
```

⇒ 在 worktree 里跑那 27 个契约，跑的是**主仓库的代码**：
把它们改成 `__file__` 相对后，同一个干净检出变成 **32 绿 / 2 红**，
且耗时整体上升（difficulty 42.6→89.6s / ops 116.6→172.8s / rea_bridge 30.5→58.0s）
—— 这才是"真的在跑检出"的代价。

⛔ **判据**：一条验证步骤如果在**被测对象不成立时照样绿**，它就是假仪表。
   这里的形态是**路径钉死**：验证跑了、输出很好看、但跑的根本不是待验对象。
   ⇒ 与「有机制 ≠ 这条路被走到」同源，只是这次发生在**验证流程自己**身上。
   ⇒ 顺带一条好用的性质：`__file__` 相对**在主仓库里路径等价**
     （解析出来就是同一个字符串）⇒ 这个修法对既有读数**零行为影响**，
     只改变检出里的行为 ⇒ 可以放心改。

### 85. **比值型判据的分母不许取自生产数据**

上面那 2 条红是同一形态：契约读 **git 不入库的运行时产物**。

```
· contract_boundary_test C 组：led = ROOT/"data"/"ledger.jsonl"；
  not led.exists() ⇒ 判红。主工作区里历次运行**恰好留下**它 ⇒ 长期假绿。
· contract_ledger_test H 组：_collect(ROOT, limit=200)，
  H1 判 json.loads 次数 / n_lines < 2.0 遍，H2 反证 ≥ 2.0 遍。
  ⇒ 分母 n_lines 来自**生产账本**：干净检出里分母 ≈ 0 ⇒ 比值失真
  ⇒ H1 假红，而 H2"反证"的是**一个假分母**（反证失效且看不出来）。
```

⇒ 三条处置：
1. **契约不得读 git 不入库的运行时产物**（账本/投影库/备份）。要读就把证据
   **自造到 temp root**，再对**自己的产物**下判据。
2. **"真实链路至今产出过 X"这类取证不属于契约**，属于 probe 脚本
   （`scripts/probe_boundary_audit.py` 那类"今天还成立吗"）——
   契约要**密闭可复现**，取证要**读真实状态**，两种判据的形态本来就不同。
3. **比值型判据的分母必须自造**；拿生产数据的规模当分母 ⇒ 判据随环境漂。
   ⛔ 且**不许凭空定阈值**：分母一换，`< 2.0` / `≥ 2.0` 很可能落到边界上
   ⇒ 必须先把真实比值跑出来再写判据（否则是把假红换成抖动红）。

⛔ 密闭版判据的**底座要先探针后改判据**（本轮实做）：临时账本 + 确定性
   "必然提交"的循环 + 带 `.agent` 的 runner 包装，实测得到
   `harness.commit 1 / attribution.layer 1 / attribution.step_unavailable 1`，
   且 `_unavailable.reason` **非空**（"环境不可重放…"）。
   ⇒ 判据要写**三态**：写 `step` / 写 `_unavailable`（带原因）/ 静默。
   只断言"必须有 step"会把**正确的降级**判红；只断言"不许静默"要配
   `attribute_layers=False ⇒ 0 条`的反证才不是恒真。

---

### 86. **断言辅助函数的参数错位 = 恒真断言（人眼抓不住，交给 AST）**

34 个 `contract_*_test.py` **各自**在文件顶部定义了一个 `ck(...)`，
签名**不统一**：33 个三参 `ck(name, cond, detail="")`、
1 个四参 `ck(group, name, ok, note="")`（`contract_boundary_test.py`）。

```
⇒ 在四参文件里照三参风格写 ⇒ ok 位上落进一个**非空 f-string**（truthy）
  ⇒ 断言恒真 ⇒ 全绿，一条都没测。而且：不报错、不警告。
```

⛔ 它长得和正常调用**一模一样**，只有逐条数实参个数才看得出 ⇒ review 抓不住。
   ⇒ 修法不是"下次小心"，而是**加一条机器闸**：

```
tests/contract_assert_wiring_test.py
  A 组：扫全部契约文件，ck() 调用的"条件位"上不许是字符串常量 / f-string
  B 组：每个契约都得有 FAILS.append（失败记账）+ return 1 if FAILS（退出码）
  X 组：反证（注入错位调用 ⇒ 必须红；干净版 ⇒ 不许误报）

⛔ 定位条件位必须按**形参名**（三参找 cond、四参找 ok），
   不许按"倒数第二个实参" —— 四参文件里漏传 group 的调用只剩 3 个实参，
   倒数第二位**恰好**还是条件位 ⇒ 抓不到（这条我自己踩过）。
```

⇒ 再往上一层：**验证流程自己也是一种"机制"**，
   同样要满足「有机制 ≠ 这条路被走到」。
   ⛔ 所以每加一条纪律，都要问：**这条纪律自己，谁来验证它失效？**

### 87. **改判据的时候，最容易把判据改没了**

本轮实况：重写 boundary C 组时把 4 条 `ck()` 的参数写错位 ⇒ 全绿但零判别力。

⛔ 衍生两条：
1. 动契约文件的每一处，都要确认**改完它还能红**（这正是反证组的用途）；
2. **同类错误不会只犯一次** ⇒ 别只修那 4 条调用，要修**产生它的条件**
   （这里是：签名不统一 + 没有机器校验）⇒ 于是有了第 86 条那条静态闸。

---

## 5-ter. 第 90 条：账本的出处（r38）

### 90. **唯一事实源必须能回答"我是谁、按哪版格式写的"**

r33 收口时发现：账本自称"唯一事实源"，但**它里面没有任何出处**。

- `Event.identity()` 只有 `seq/ts/kind/actor/payload/prev_hash`；
- `LEDGER_VERSION` **只参与算 hash**，全仓只有"定义一处、写入一处"两次
  命中，**没有任何读取** ⇒ 格式版本是惰性元数据；
- 账本里查不到"这份数据是哪个代码版本写的"。

⇒ 两种事故，而报错都指向**错误的方向**：

1. **格式漂移 ⇒ 报"链断了"**。读者按新规则复算 hash 与旧 hash 对不上，
   `verify_chain` 说 `hash 不一致`。现场看起来像"账本被篡改"，
   真相是"代码换了、数据没迁移"——排障方向完全错。
2. **跨版本比读数**。`status` 的投影对账、`evolution` 的历史对照、
   监督器的跨轮判据，都会拿"不同代码版本产出的事件"当同一口径比，
   而没有任何一处能发现这件事。

**修法**（`aether/provenance.py` + `aether/_version.py`）：

- 版本的**唯一事实源**收进 `_version.py`（依赖方向 `_version → provenance
  → ledger → __init__`，单向、无循环）；`--version` 是新的命令行入口。
  ⛔ 契约里有一条**反证式静态检查**：除 `_version.py` 外，源码里出现版本号
  字面量就转红（此前 `__init__.py` 与 `serve.py` 各写一份，实测漂移过：
  包报 0.1.0、端点报 0.2.0）。
- 出处**不改跨语言 hash 契约**：不新增事件字段，而是由写入侧在**自己的
  第一条事件**的 payload 里盖章（`src_version` / `format_version`）。
  `payload` 本就参与 hash ⇒ 历史事件不受影响，TS 侧不用改 `identity()`。
  这与当年 `sid`（会话归属）的做法同源。
- 兼容判据是**三态**，处置各不相同（合并成两态就是灾难）：

  | 状态 | 含义 | 处置 |
  |---|---|---|
  | `ok` | 有声明且兼容 | 放行 |
  | `unknown` | 没声明（升级前存量） | **放行**，但读数写明"出处不可判别" |
  | `incompatible` | 有声明但不兼容 | **拒绝**，报出两个版本 + 迁移动作 |

  ⛔ `unknown` 必须放行：升级前的账本不该被判成非法（"不可判别 ≠ 有罪"）。
  ⛔ 判据本身踩过一次坑：`is_compatible` 返回 `None` 有两种来源（没声明 /
  **声明了但形状非法**），一开始只判 `is False`，于是
  `{"src_version": "一句话不是版本"}` 被判成 **ok** —— "不能判定"被当成了
  "没问题"。现在形状非法 ⇒ 判 `incompatible`。
- 读数接线：`doctor` 新增 `ledger.provenance`（不兼容 ⇒ fail + 给动作），
  `Ledger.source()` / `source_compat()` 供 CLI / HTTP / 监督器取用。

**顺带发现的两个真实缺陷**（都如实记录，其中一个**未修**）：

1. ⛔ **REA 桥的"超时诊断"被适配层丢掉**（未修）：桥在超时时写了一份完整
   信封（`scripts/rea_bridge.py` 的 `env["timeout_reason"]`），但
   `CliAdapter.__call__`（`aether/interop.py`）对**非零退出码**直接抛
   `CapabilityError("failed", "CLI 退出码 …" + stderr[:300])` —— 而 REA 的
   输出在 **stdout**、stderr 是空的 ⇒ 信封被整个丢弃 ⇒ 调用方只看到
   `status=timeout` + 空 output，**看不到原因**。这与桥自己的注释
   （"超时单独开一个读数，不许混进 degraded"）直接矛盾。
   `contract_rea_bridge_test` 的 C2b **故意断言当前这个坏行为**：它是缺口的
   存在证明；修掉适配层后它会转红，提醒把断言改成"原因必须可见"。
2. ⛔ **REA 契约在并发下会假红**（已修）：实测"两份契约同时跑"时 REA 一律
   `status=timeout`（共享外部资源，不是代码错），而原断言把超时当失败
   ⇒ 同一份代码单独跑 3/3 绿、并发跑 4/4 红。现在超时**跳过该断言并写明
   未测**（跳过 ≠ 通过）。

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

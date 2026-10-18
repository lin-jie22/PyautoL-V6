# 架构审计 · 2026-10-18

> 目的：回答三个问题 —— **流程合不合理 / 架构整合得合不合理 / 到底哪几处真要改**。
> 方法：**不靠人画架构图**，全部读数来自可复跑的 `scripts/arch_scan.py`
> （AST 依赖图 + 入口可达性 BFS + 一次真实 evolve 的行级覆盖 + 账本零次事件差分）。
> ⛔ 本轮**只做诊断，不动生产代码**（除新增审计脚本与本文件）。

---

## 0. 怎么量的（以及四条必须一起看的边界）

| 段 | 量什么 | 数据源 |
|---|---|---|
| A | 依赖图：规模 / 咽喉 / 环 / 反向依赖 | AST 解析 150 个 `.py` 的 import |
| B | 静态可达性：图里有、但没人走到的模块 | 从所有入口做 BFS |
| C | 运行时覆盖：一次真实 `evolve` 跑到了哪些模块 | `sys.settrace` 行级 |
| D | 零次事件：源码声明过、账本里一次都没有 | AST 扫事件名 − 生产账本实到 |

⛔ **四条边界**（不一起看就会读错）：

1. `settrace` 只覆盖**本进程**。任务执行走 `subprocess`（python_exec）⇒ 子进程里的
   aether 代码不计入。**覆盖率低 ≠ 死代码**，它只是「值得去查」的线索。
2. 覆盖率采自**一次** `evolve`。`serve` / `doctor` / `check` / `backup` 那些入口
   0% 是**正常**的（本来就不在 evolve 路径上）—— 脚本已把它们点名为「别的入口」，
   与「可疑」分开列。
3. tracer 晚于 `import aether.cli` 启动 ⇒ 导入期执行的代码不计入；
   `__init__.py` / `_version.py` 偏低是量具偏差。
4. 零次事件只统计 **aether/ 下声明的**生产事件（契约里的 `c.d` / `t.one`
   这类假 kind 是 fixture，已排除 —— 第 19 条：归因前先排除 fixture）。

审计脚本自带**反证组**（`python3 scripts/arch_scan.py --self-test`，rc=0 全绿）：
造一个孤岛模块 + 一个永不发生的事件，抓不到就退出码非 0。
⇒ 它若静默恒绿，下面所有读数都不可用。

---

## 1. 依赖全景（A 段）

```
py 文件 150   总行数 52092
  aether    40 个 / 22114 行   ← 生产
  scripts   35 个 /  8880 行   ← 探针与实验
  tests     61 个 / 20065 行   ← 契约
  tasks     14 个 /  1033 行   ← 可验证环境
```

**结论先说：静态架构是干净的。**

| 检查项 | 结果 |
|---|---|
| 循环依赖（SCC） | **无** |
| 生产包反向依赖 tests/scripts/tasks | **无** |
| aether 模块静态不可达 | **0 / 40** |

**咽喉（被依赖最多 ⇒ 改它的爆炸半径最大）**

```
indeg=50  ledger        indeg=27  nongression   indeg=19  evolution
indeg=47  genome        indeg=26  verifier      indeg=18  providers
indeg=39  environment   indeg=25  aether        indeg=17  repair
```

分层是自洽的，**零依赖的地基真的只有常量层**：

```
地基（0 依赖）  paths / _version / logging_setup / embeddings
事实源          ledger → provenance → _version
能力/判据        genome / verifier / judge / nongression / environment
编排            evolution（依赖 15 个 aether 模块）
入口            cli（依赖 28 个）/ serve（7 个）/ ui（4 个）
```

## 2. aether 内部依赖清单（谁直接依赖谁）

```
模块            indeg  直接依赖
ledger           16    provenance
genome           11    ledger
paths            10    —            ← 布局唯一事实源，零依赖（对）
verifier          9    ledger
memory            5    embeddings, ledger
embeddings        4    —
environment       4    ledger, verifier
logging_setup     4    —
store             4    —
cleanup           3    agent, genome, judge, ledger, paths, selfref, srcmut
discover          3    —
entry             3    logging_setup, paths
interop           3    environment, genome, ledger, verifier
judge             3    genome, ledger
nongression       3    genome, ledger, verifier
runtime           3    ledger, verifier
srcmut            3    ledger, paths
agent             2    environment, genome, memory, repair, runtime, verifier
backup            2    ledger, paths
providers         2    cleanup, genome, runtime
repair            2    environment
serve             2    entry, interop, ledger, logging_setup, memory, paths, store
attribution       1    genome, verifier
caps_candidate    1    discover, ledger
check             1    discover
checkpoint        1    —            ⛔ 见 P0-2
doctor            1    backup, embeddings, interop, judge, ledger, memory, paths, store
evolution         1    aging, attribution, checkpoint, cleanup, genome, judge,
                       ledger, nongression, paths, providers, runtime, srcmut,
                       store, verifier, world_facts
generalization    1    genome, nongression, verifier
learner           1    memory
provenance        1    _version
retrieval         1    embeddings
selfref           1    paths
ui                1    entry, logging_setup, paths, serve
world_facts       1    genome          ⛔ 见 P0-3
cli               1    （28 个：几乎全部）⛔ 唯一总装点，见 P1-1
```

## 3. 运行时覆盖（C 段 · 一次真实 evolve：2 轮 / 114s / rc=0）

```
 82.76%  learner          51.03%  generalization  31.62%  genome
 81.69%  attribution      50.43%  evolution       31.41%  providers
 69.84%  agent            47.81%  selfref         31.25%  judge
 64.78%  repair           43.42%  environment     30.77%  nongression
 63.33%  cleanup          40.28%  world_facts     30.10%  memory
                          39.31%  ledger          29.92%  aging
 25.35%  srcmut           34.39%  runtime         28.57%  verifier
                          32.44%  cli             18.85%  store
                                                  7.25%   embeddings
```

0% 的 13 个模块里，**11 个是别的入口**（serve / ui / doctor / check / backup /
discover / caps_candidate / retrieval / interop / `_version` / `__main__`），
**2 个值得查**：`checkpoint`（P0-2）、`__init__`（量具偏差，见边界 3）。

## 4. 零次事件（D 段 · 生产账本 6119 条 / 28 种）

源码声明 35 种，实到 22 种，**零次 13 种**：

```
好事（条件未满足 = 没出事）：attribution.unavailable / attribution.step_unavailable
                            baseline.supersede_failed / cleanup.propose_failed
                            experience.ledger_failed
默认关的新口径：            heldout.grow（--ho-grow）
─────────────────────────────────────────────────────
⛔ 真可疑（6 条，逐条见 §6）
gate.blocked                 evolution.py:1695
world_fact.promoted          evolution.py:1662
world_fact.promote_failed    evolution.py:1674
deadend.cooldown             evolution.py:1632
deadend.inherited            evolution.py:1620
evolution.checkpoint(_failed) evolution.py:2209 / 2085
memory.pruned                memory.py:716
```

⛔ 零次有两种含义，**必须分开标定**：结构性不可达（要修）vs 条件未满足（好事，别误杀）。
下面每一条都做了「它有没有牙」的取证。

---

## 5. 正反双方：整个流程合不合理

### 5.1 正方（哪些真的站得住，有证据）

1. **唯一事实源 + 哈希链是真的**：生产账本 6119 条，**逐行可解析、0 条损坏**；
   `doctor` 报 `ledger.chain: 链完整`、`projection.consistency: 一致`。
2. **静态架构无环、无反向依赖、0 孤岛** —— 40 个模块全都从入口可达。
   这是同规模项目里少见的。
3. **分层是真的分开了**：地基（paths / _version / logging_setup）零依赖；
   `paths` 作为布局唯一事实源被 10 个模块引用。
4. **进化五步里四步有账**：MINE `weakness.mined` 165 / VALIDATE `gate.result` 282 +
   `eval.verdict` 2321 / SELECT `gen.probe` 114 / COMMIT `harness.commit` 103。
5. **判据可回归这条宪法守住了**：`aether check` 60 份契约全绿。

### 5.2 反方（哪些站不住）

1. **系统当前自检是红的**（见 P0-0）：`aether doctor` 退出码 1，
   `judge.boundary` fail。契约全绿 ≠ 系统健康 —— **契约测的是代码，体检测的是数据**。
2. **五步里唯独 PROPOSE 没有账**。提议器提名了谁、谁被永久跳过了，
   账本里**一个事件都没有** —— r52（候选挤压）与 r54（被拒候选永久不提）
   连续两轮故障**都出在这最不可观测的一步**。
3. **三条「承诺过的路径」从未走过**（P0-2 / P0-3 逐条取证，区分有牙还是没牙）。
4. **天花板是硬的，不是机器问题**：2 代、4 个环境、单口径题源硬顶 26 条、
   easy 层结构性饱和（`GENERIC_REPAIRS` 只有一个成员）。
5. **自证机器比被证系统还大**：scripts 8880 + tests 20065 = **28945 行**
   vs 生产 aether **22114 行** ⇒ **1.31×**。这台机器现在主要产出
   「关于自己的证据」，而不是能力。
6. **两块巨石**：`evolution.py` 2222 行 + `cli.py` 2086 行 = aether 的 **19.5%**；
   cli 扇出 28 个模块，是**唯一**总装点。

---

## 6. 真正要改的地方（按「改了会不会变强」排序，不按「看起来脏」）

### P0-0 · 系统当前自检是红的：生产账本混了 14 把尺子

- **证据（实测）**：`python -m aether.cli doctor` ⇒ `healthy: false`，`exit_code: 1`，
  `judge.boundary` **fail**：账本里有 **14 种判据身份**（`judge_version`），
  分布在 905 条 `eval.verdict` 上（最大一把 480 条，其余 12–132 条不等，按 seq 分段连续）。
- **为什么是真问题**：这不是「历史遗留的脏」，它是**当前生效的读数约束** ——
  任何跨轮/跨版本的 reward 对比（本项目 r48–r56 的结论大多属于此类）
  在严格意义上**不可采信**。而 doctor 给的出路是「用同一把尺子重跑一组」，
  ⛔ **系统里没有这个工具**（`rebuild` 只能重建投影，重跑判据要重跑评估）
  ⇒ 按第 35b 条：这条出路目前是**装饰**。
- **改法（三选一，都要先钉口径）**：
  ① 给账本做**尺子分区**，所有对比工具只在同一 `judge_version` 内比较；
  ② 提供 `rejudge` 命令，用当前尺子重跑历史评估并**留痕**（不删事件）；
  ③ 明说「跨尺子不可比」并把历史段标为封存（不再进任何对比）。
- **不改会怎样**：下次再有人拿今天的 reward 去比三个月前的 reward，结论仍然无效。

### P0-1 · PROPOSE 必须记账 —— 让最不可观测的一步可审计

- **证据**：账本 28 种事件里没有 propose/candidate 类事件；MINE/VALIDATE/
  SELECT/COMMIT 四步都有。r52 + r54 两轮根因都在这步。
- **为什么是真问题**：不是「少了一条日志」。是**当挤压/永久跳过发生时系统无法自证**，
  只能靠外部探针 monkey patch 才看得到（r52/r54 都是这么发现的）。
- **改法**：提名即 `candidate.proposed`（含：提名了谁 / 池子顺序 / 被谁以什么理由排除）。
  ⛔ 必须与 `MemoryAwareProposer` 的 skip 同源，否则又是「有机制不走」的摆设。
- **不改会怎样**：下一次候选挤压仍然只能靠人猜。

### P0-2 · 三条零次路径：先定性，再决定接线还是标注

| 事件 | 有没有牙（本轮取证） | 为什么 0 次 | 处置 |
|---|---|---|---|
| `gate.blocked`（L0 保护） | **有牙**：构造 `target=L0.meta_rules` 的胶囊 ⇒ `touches_protected=True`（`L0.meta_rules` 与 `meta_rules` 两种形态都识别，`_split_target` 的绕过洞已补） | **结构性**：两个提议器都被设计成永不碰 `meta_rules`，且**没有构造器**能产出碰 L0 的胶囊 | 纵深防御第二道锁，永不触发是设计意图 ⇒ **加负结果契约钉住**（第 58 条），不许改判据 |
| `evolution.checkpoint` | 未触发（默认路径不可达，覆盖 0%） | **默认关**：`checkpoint_path=None` ⇒ 不落盘 | 第 60 条承诺「中间态可恢复」，但**默认口径下不成立**。要么默认开，要么在报告里明写「本次不可续跑」 |
| `memory.pruned` | 未取证 | 未知（可能阈值未到） | ⛔ 本轮**不下结论**，列为下一轮取证项 |

### P0-3 · 死路冷却与陈述性知识：触发条件与提名策略**互斥**

这是本轮最绕的一条，也是**唯一一条我一开始判错、被取证纠正**的。

- **第一版假设（已证伪）**：两处 key 拼法不同源 ⇒ streak 恒 0。
  **实测**：`_deadend_accum` 用 `gen.probe` 的 `capsule|env_pair`，
  `_in_cooldown` 用 `cap_name|heldout.env_pair` —— 两者**逐字同源**
  （实测交集 = 全集，且 streak 正常累加：轮 3 时 `fix_literals` streak=1）。
- **第二版假设（已证伪）**：`world_facts` 模块没牙。
  **实测**：`streak=3` 时 `W.promote` 命中且 `harness_id` 改变；`streak=1/2` 不晋升 ⇒ 门槛正确。
- **真因（实测）**：
  ① **生产账本里确实有达标的历史** —— 用生产同一份累加逻辑重算 114 条 `gen.probe`：
     `add_skill:patch:textscore|unknown` **连续被拦 15 次**、
     `add_skill:patch:textscore|textscore->eventflow` **3 次**，都 ≥ 门槛 3。
  ② 但这些读数**从未转化为冷却/晋升**，原因是两层：
     - **层 1（取值域漂移）**：早期事件的 `env_pair` 是 `unknown`，后来变成
       `textscore->eventflow` ⇒ 历史死路 key 永久失配，**且没有迁移或失效机制**
       （与第 52 条「缓存的结论必须有失效机制」同形）。
     - **层 2（触发条件与提名策略互斥）**：`_in_cooldown` 只对**本轮被提名的候选**
       调用。候选一旦被拦，提议器就不再提名它 ⇒ streak 停在 1，永远到不了 3。
- **决定性实验（负结果，如实记录）**：`evolve --rounds 4` 开/关 `--retry-every 2`
  对照 —— `deadend.cooldown` / `world_fact.promoted` **两组都是 0**，
  reward 轨迹逐字相同 `0→0.6→0.6→0.6` ⇒ **`--retry-every` 不是这条路的出口**
  （它修的是「被闸拒后重审」，不是「被提名后重新进池」）。
- **改法**：要么让 world_facts 的观察**不依赖"再次被提名"**（提名失败本身就能记一次观察），
  要么把 world_facts 从「已交付」降级为「未接线」并写清出路（第 35b 条）。

### P1-1 · 拆两块巨石（evolution 2222 / cli 2086）

- cli 依赖 28 个模块 = 唯一总装点 ⇒ 加任何能力都要改它，它是事实上的编排层。
- 建议方向（**未做**）：把 `cmd_evolve` 的编排移进 evolution 的 `run_from_args()`，
  让 cli/serve 都只做参数解析 ⇒ 消除「第二个入口」的漂移风险。

### P1-2 · 收敛「第二份」常量（4 处，逐个点名）

| 常量 | 两处 | 判定 |
|---|---|---|
| `ROOT` | `paths.py:30` vs **`selfref.py:54`**（自己算 `parent.parent`） | ⛔ 真违反第 46 条：r36 收口就是禁止这件事，它又长出来一份 |
| `CANDIDATES_NAME` | `paths.py:34` vs **`caps_candidate.py:71`** | ⛔ 第二份布局定义；且后者**从未被使用**（死常量） |
| `LAYERS` | `genome.py:48`（层名）vs `attribution.py:68`（L0–L4 代号） | ⚠️ 同形不同源：两个东西共用一个名字，风险是改名不同步 |
| `FAIL` | `check.py:49` vs `doctor.py:47` | ⚠️ 同形不同源（五态 vs 三态），同级 |

### P2 · 天花板（唯一能让它真正「变强」的，但代价最大）

4 个环境 / 单口径 26 题硬顶 / easy 层结构性饱和 / 代数 2 代。
前三项已由 r47–r56 逐轮实测钉死，**不是机器问题**。
⇒ 想突破只能换世界或重构难度层，这是**研究方向**不是「收拾代码」。本轮不动。

---

## 7. 不该改的（负结果，别下一轮又来一遍）

1. **依赖图本身**：无环、无反向依赖、0 孤岛 —— 现状是对的，别为了「更清晰」重构。
2. **`gate.blocked` 的判据**：本轮取证证明它有牙，只是结构性不触发 ⇒ 不许改判据，
   改的是「给它一条反证契约」（第 9 条：闸恒绿先做反证）。
3. **死路 key 的拼法**：已证两处同源，别去「统一」（那是把对的改坏）。
4. **`world_facts` 的门槛 3**：实测 `streak=3` 晋升、`1/2` 不晋升 ⇒ 门槛正确，别调。
5. **静态可达性 0 不可达**：这是好结果，别读成「覆盖率太低所以要加 import」。

## 8. 诚实未做

- 覆盖率只采**一次** evolve 的**本进程**；serve / ui / doctor / check 等
  **入口路径未做运行时覆盖**（它们的健康度未知）。
- `memory.pruned`（0 次）未取证，未下结论。
- 世界规模（4 环境）未扩；难度层未重构。
- P0-0 的三选一改法**未实施**，本轮只把问题与影响面量化。
- 未改任何生产代码。

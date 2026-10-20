# AETHER 怎么用 · 用在哪

> 这份文档只回答两个问题：**怎么敲**、**什么时候值得敲**。
> 想知道"为什么这么设计"看 `README.md`（那里面有 100+ 条纪律条目），
> 想知道"它到底是什么"看 `docs/ARCHITECTURE.md`。

---

## 0. 一句话定位

**AETHER 是一个"能被证伪"的自我改进工具。**

它做的事和别的 agent 框架没什么不同 —— 让程序自己改自己。区别在于**每一次改动都必须留下可独立复核的证据**：

- 改了什么 → 账本里有一条事件（哈希链，改一个字就断）
- 为什么算改好了 → 冻结的非回归题 + held-out 泛化题，**两道闸都要过**
- 改坏了怎么办 → 自动回滚，回滚也进账本
- 过了闸还能反悔吗 → `backup` / `restore` 逐字节还原

所以它最诚实的用途不是"帮我写代码"，而是：**当你打算让机器自己动你的代码时，用它来兜住"到底变好了没有"这件事。**

---

## 1. 三种姿势（先选一种，别混着用）

| 姿势 | 怎么装 | 数据落在哪 | 适合 |
|---|---|---|---|
| **A 仓库开发态** | 不用装，在仓库里跑 | `./data/`（仓库自带） | 改 AETHER 本身、跑全量契约 |
| **B 装成工具** | `pip install .` | 当前工作区的 `./data/`（`aether init` 建的） | 拿它去管**别的**项目 |
| **C 临时看一眼** | 不用装 | `~/.local/share/aether/` | 试一下，不想留东西 |

**工作区根怎么定的**（五支，顺序固定，别记错）：

```
① --root 显式      ② $AETHER_ROOT      ③ 包位置是仓库（开发态）
④ 向上找 .aether   ⑤ ~/.local/share/aether
```

> ⛔ 只有在 ③ **不成立**、且你**站在一个 `aether init` 过的工作区里**时，④ 才会赢。
> 在仓库里跑任何命令，落点永远是仓库自己的库（这是保护，不是 bug）。

---

## 2. 五分钟上手

```bash
# ── B 姿势：装成工具（推荐给"拿它管别的项目"的人）
pip install .                       # 或 pip install /path/to/V6

mkdir ~/my-agent && cd ~/my-agent
aether init                         # 建 data/ tasks/ backups/ + .aether，并物化 14 个示例任务
aether status                       # 看账本：条数、哈希头、按类型分布
aether synth --max 24               # 先确认"有活干"：看 yield_rate，不看 attempted
aether evolve --rounds 1            # 跑一轮进化（默认 4 个环境，几十秒到几分钟）
```

`aether evolve` 跑完会打印一份 JSON 摘要。**只看四个字段**：

| 字段 | 含义 | 什么算好 |
|---|---|---|
| `improved` | 这一轮有没有候选被接受 | `false` 是**正常**的，不代表坏了 |
| `improved=false` + `gate.result.passed=true` | 过了非回归闸，但泛化证据不足 | 说明你的题太少，不是代码坏了 |
| `cost_s` | 这一轮花了多少秒 | 与历史同口径比，别跨口径比 |
| `ledger.count` | 这一轮写了多少条事件 | 每条都能 `verify-chain` 复核 |

> ⛔ `--max 2` 这种小数字会得到 `yield_rate=0`，看着像"坏了"，其实是采样不够。
> 至少 `--max 24`。

---

## 3. 每个子命令到底做什么

### 3.1 开始工作

| 命令 | 关键参数 | 实际在做什么 |
|---|---|---|
| `aether init` | `--dir` / `--dry-run` | 建骨架（幂等，已存在的**不动**）；把包内 14 个示例任务物化到 `tasks/` |
| `aether status` | — | 唯一"总读数"：账本 + 尺子分区 + 投影一致性；`chain_ok:false` 时给 `first_bad_seq` |
| `aether synth` | `--max 24` `--depth {1,2}` `--kinds` `--balanced` | 从环境合成任务并统计 `yield_rate`。**`valid` 才是可用的题** |

`init` 是**幂等**的：第二次跑不会覆盖你改过的 `task.py`，只补缺的。

### 3.2 自证没坏（这是它和普通框架的分界线）

| 命令 | 耗时 | 做什么 |
|---|---|---|
| `aether selfcheck` | **~1 秒** | 11 项**不变量**：根解析 / 账本往返 / 链 / 重放 / 闸有牙 / 状态不许回退 / 备份往返 / 示例能跑 / 控制台可达 / 零依赖 |
| `aether doctor` | ~2 秒 | 9 项体检（只读）：架构巨石、数据可写、能力清单可装载……**skip ≠ 通过** |
| `aether check` | **30+ 分钟** | 71 份契约 / 73 次执行。一条命令、一个退出码，CI 用这个 |

分工要说清楚：`selfcheck` 是**装完就能跑**的子集（不依赖仓库），`check` 是**开发时**的全量。
给 CI 接 `check`，给用户装完自证接 `selfcheck`。

### 3.3 账本与恢复

| 命令 | 关键参数 | 做什么 |
|---|---|---|
| `aether verify-chain` | — | 验哈希链。改过任何一行，立刻指到 `seq` |
| `aether backup` | `--to` `--note` `--archive` | 备份账本 / 记忆 / 投影，**自带校验和** |
| `aether restore` | `--from` `--yes` | **默认只演练**；`--yes` 才覆盖，覆盖前自动留一份当前状态 |
| `aether rebuild` | `--check` `--out` | 从账本重建 SQLite 投影。**库只是视图**，账本才是事实源 |
| `aether caliber` | `--seal --note` | 尺子分区：哪段读数能跟哪段比。换过判据/模型就 `--seal` 一下 |

### 3.4 进化闭环（正题）

```bash
aether evolve --rounds 4 --max-tasks 16            # 常规
aether evolve --depth 2 --require-behavior         # 抬高天花板 / 只要真行为变化
aether evolve --heldout on --ho-grow               # 泛化题不够时，让闸自己补采样
aether evolve --resume                             # 断点续跑（r58 起默认写 checkpoint）
```

一轮 `evolve` 内部是五步，每步都落账本：

```
挖弱点 → 提候选(胶囊) → 冻结非回归闸 → held-out 泛化闸 → 接受 or 回滚
          │                │                │
          │                │                └─ 判别力不足 ⇒ "不可判别"（不是通过，也不是失败）
          │                └─ 有回退立刻拦（哪怕总分涨了）
          └─ 提议池：both / general / narrow
```

两条原则值得记住：

- **`passed` ≠ 该接受**。实测一例：候选 `fix_control_flow` 过了非回归闸（`frozen_gain=2`），
  dev 涨 0.6、held-out 涨 0.667，但 `advantage=1.3472` 不够，且 held-out 的 hard 层
  只剩 1 条可提升空间 ⇒ 判 `inconclusive`，**回滚**。这是设计意图，不是能力不足。
- **"不可判别"要单独记**（`evolution.no_evidence`）。它不计入死路账，所以会每轮重探 ——
  代价随"看不清的候选 × 轮数"线性增长。想消除代价得先消除成因：补题的难度层，不是放宽判据。

### 3.5 外部能力

```bash
aether caps discover                  # 探本机已装工具（只读，不下载不装）
aether caps candidates list           # 看候选档案与状态
aether caps candidates auto --tool X  # 机器自审：过五道判据才装（只认 brew install）
aether caps list / show / invoke      # 用已注册的能力
```

⛔ 两份白名单**不通用**：给人看的那份（`ALLOWED_LAUNCHERS`，23 个词，含 curl/pip）
和给机器执行的那份（`AUTO_LAUNCHERS`，只有 `brew install`）是两个东西。

### 3.6 界面

```bash
aether ui                             # 唯一常驻形态：API + 控制台（同源托管）
aether ui --port 8611 --no-open       # 只起服务
aether ui --host 0.0.0.0 --api-token xxx   # ⛔ 非回环地址**必须**给 token，否则拒绝启动
```

暴露的端点：`/api/status` `/api/health` `/api/events` `/api/ledger/raw`
`/api/files` `/api/file` `/api/capabilities` `/api/invoke`。

> 装出来的包里也带一份控制台快照（`aether/seeds/console/index.html`，49 KB），
> 所以 `pip install` 之后 `aether ui` 直接可用。

---

## 4. 怎么加自己的任务（核心扩展点）

一个任务集 = `<root>/tasks/<env>/` 下两个文件：

```
tasks/my_env/
├── task.py      # 被测代码（**故意留缺陷**，由 evolve 去改）
└── tests.json   # 判据：call / args / expect
```

`tests.json` 的最小形态：

```json
{
  "env_id": "my_env",
  "oracle": "characterization",
  "tests": [
    {"name": "clamp_low",  "call": "clamp", "args": [-5, 0, 10], "expect": 0},
    {"name": "clamp_high", "call": "clamp", "args": [15, 0, 10], "expect": 10},
    {"name": "normalize",  "call": "normalize", "args": [[2, 2]], "expect": [0.5, 0.5]}
  ]
}
```

三条要点：

1. **`expect` 是"生成时刻的实现行为"**，不是规格。所以 `oracle` 标 `characterization`：
   它保证的是"改完不许变"，不是"本来对"。要写规格判据就自己改 `expect` 并去掉这个标记。
2. **判据必须能被缺陷打穿**（F2P 过滤）。写不出失败判据的题会被丢弃 ——
   题太简单，`synth` 的 `rejected_no_failure` 就会高，这不是 bug。
3. **难度要分层**。held-out 闸按 `easy` / `hard` **逐层**判；
   某一层可提升空间 < 3 条 ⇒ 整次裁决判"不可判别"。想让闸有话说，就得给两层的题。

---

## 5. 用在哪（四个真实场景）

### 场景 1｜给"自改进"这件事装护栏（最贴本意）

你在做任何"让模型/程序自己改自己"的东西（prompt 自优化、代码自修复、超参自演化），
最难的从来不是"怎么改"，而是**"改完到底变好了没有"**。

AETHER 提供的是那一层：冻结题（防退化）+ held-out（防过拟合）+ 账本（可回放）+ 回滚（可撤销）。
你的变异策略可以随便换，闸和账本不用动。

### 场景 2｜当"我改的这版到底有没有变好"的裁判

把你要评的东西做成任务集（`task.py` + `tests.json`），跑 `evolve`。
它会告诉你三件事，而且**分开说**：涨了（`improved`）、没涨（`no_advantage`）、
看不清（`inconclusive`）。第三种最容易被别的工具糊弄过去。

### 场景 3｜给仓库配一条"能自证没坏"的 CI

```bash
aether check -j 4        # 71 份契约，一个退出码
aether selfcheck         # 1 秒版，装在用户机器上跑
aether verify-chain      # 账本被动过没有
```

这里的价值是**判据本身可复核**：契约全是可执行脚本（`tests/contract_*_test.py`），
不是描述性文档。想反驳某条结论，去改那份契约、看它红。

### 场景 4｜外部工具接入前的"闸"

`caps discover` 查本机装了什么，`caps candidates` 生成候选，**人审或机器自审**过闸才装。
装在装之前就被记进账本，事后能查"谁、什么时候、基于什么证据批准了这次安装"。

---

## 6. 不适用（先说清楚，省得试错）

- **不是聊天助手**。没有对话循环，没有工具编排 DSL。它是**离线批处理**。
- **不是通用代码生成器**。变异算子是结构化的（比较/算术/常量/布尔/一元/字符串），
  它擅长"小函数的行为修复"，不擅长"从零写一个模块"。
- **没有常驻调度**。没有 watchdog / daemon，`ui` 是唯一会常驻的形态。
  想定时跑就自己配 cron，但要保证**不并发**（两个进程同时写同一账本 = 哈希链打架）。
- **不能离线判"对不对"**。判据是行为对照（`expect`），不是语义正确性。
  题目设计得差，闸就只能告诉你"没变化"，不能告诉你"变得更好了"。

---

## 7. 排错速查

| 症状 | 先敲什么 | 常见原因 |
|---|---|---|
| `synth` 出 `yield_rate=0` | `aether synth --max 24 --depth 1` | `--max` 太小；或题太简单（判据打不穿） |
| `evolve` 一直 `improved=false` | 看 `gate.result.passed` | `true` ⇒ 泛化证据不足（补题）；`false` ⇒ 真有回退 |
| 报"没有可变异点" | `--kinds` 换一组 | 该函数里没有默认三种算子的落点 |
| 装完 `synth` 报"没有任务源" | `aether init` | 示例任务没物化（工作区是空目录） |
| 装完 `ui` 报"找不到控制台页面" | `aether selfcheck --only console` | 包的 `package-data` 漏了页面（r61 已修，装态自带快照） |
| 改了代码没生效 | `pip install --no-cache-dir --force-reinstall .` | **pip 会复用 `~/.cache/pip` 里同版本旧 wheel** |
| 账本 `chain_ok:false` | `aether verify-chain` | 有人手改了 `ledger.jsonl`；用 `restore` 回滚 |
| `doctor` 有 `skipped` | 看那条要什么参数 | **skip 不是通过**（例如 `caps.loadable` 要 `--caps`） |

---

## 8. 三条最容易踩的纪律

1. **别拿不同口径的分数互相比**。换过判据、换过 embedder、换过题目集合之后，
   历史分数就不能直接比。先 `aether caliber --seal --note "换了什么"`。
2. **全量 `check` 期间别动仓库**。每份契约一个子进程，后半段会加载新代码 ⇒
   改一半的代码会被当成"契约失败"，你会去查一个不存在的问题。
3. **别并发跑两个写账本的进程**。哈希链是单写入者假设；并发写会让 `prev_hash` 打架。

---

## 附：一分钟自检清单

```bash
aether status        # 账本在、链完整、库对得上
aether selfcheck     # 11 项不变量全绿
aether doctor        # 没有 fail（skip 要单独看）
aether synth --max 24   # 有活干（yield_rate > 0）
aether evolve --rounds 1  # 能跑完，且两种结局都进账本
```

五条都过，这套东西在你的机器上是**可用**的。有一条过不去，先修那条，别往下走。

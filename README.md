# V6 · AETHER

**可验证的自进化智能体骨架**（Autonomous Evolving Task-Harness with Empirical Reward）

> 📖 **想直接用它**（怎么敲、用在哪、任务怎么写）→ **`docs/USAGE.md`**
> 本文件是设计理由与纪律条目（110+ 条），当参考手册查，不要当入门读。

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
cd <你的仓库>                       # 本仓库根（clone 下来的那个目录）
PY=python3                          # 3.11+ 即可；也可写 venv 里的绝对路径

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
$PY scripts/probe_self_reference.py # 自指负载：多少产出在给自己造机器（只读）

# TypeScript 监督器（Node ≥22 原生跑 TS，无需 npm install）
# ⛔ node 不一定在 PATH 上：脚本与契约都按「V6_NODE → PATH → 受管家目录」
#    同一顺序查找，找不到就明确报红而不是静默跳过。
NODE=${V6_NODE:-$(command -v node || ls -d ~/.workbuddy/binaries/node/versions/*/bin/node 2>/dev/null | tail -1)}
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

# 能力候选：发现 → 候选 → 过闸 → **人批准** → 人装 → 复验
# ⛔ 这一串是"想装东西但**不自己装**"的完整路径：引擎不执行安装、
#    不发网络请求；它只产出提案、过闸、留痕，并把安装命令**交给人**。
$PY -m aether.cli caps candidates propose                    # 生成本机缺什么
$PY -m aether.cli caps candidates validate --tool radare2     # 过闸（看原因）
$PY -m aether.cli caps candidates approve --tool radare2 --by 你 --yes
#   ⛔ 没有 --yes 一律不推进（退出码非 0）；不留名也不批。
$PY -m aether.cli caps candidates hint --tool radare2        # 给人执行的命令
#   装完重跑 `caps discover` 复验；每次状态都进账本（cap.candidate）。

# 判据集（尺子）变更之后：**重新发布**，不是删事件
# ⛔ 判据集有内容寻址身份（`aether/judge.py`）：改一行判据代码，
#    历史读数与未来读数就**不同尺子** ⇒ 跨尺子比较被拒（doctor 会报）。
#    出路是"保留前缀 + 留痕 + 断点续新链"，删事件会**断链**（实测过）。
$PY scripts/relaunch_ledger.py --out data/ledger.v2.jsonl \
    --reason "为什么换尺子" --by 你            # 默认 dry-run，先看方案
$PY scripts/relaunch_ledger.py --out data/ledger.v2.jsonl \
    --reason "为什么换尺子" --by 你 --apply    # 真写新链（不覆盖原账本）
# ⛔ 为什么不直接自动装（三条都绕不过）：判据必须离线可回归（宪法）、
#    子进程网络动作被动作级拦下（沙箱）、"自动下载并执行"会跳过
#    "人审过这份清单"（信任）。详见 aether/caps_candidate.py。
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

### 2-bis. 作为**安装包**使用（`pip install`，r61）

```bash
python3 -m pip install .        # 零依赖；装完直接有 `aether` 命令
cd ~/my-agent-project           # ⛔ 换一个**空目录**，别有仓库
aether init                     # 建骨架：data/ tasks/ backups/ .aether
aether status                   # 数据落点 = ./data/ledger.jsonl
```

**装完就有能真跑的事（r61 修掉的「内容断层」）**

以前 `tasks/` 是仓库资产、刻意不打进 wheel ⇒ 装完能建骨架，但**没有一件能真跑
的事**（`synth` 直接报「没有任务源」）。现在 wheel 里带一份**示例任务集**：

```
aether/seeds/tasks/          ← 仓库里是 symlink → ../../tasks（零重复）
   _inspect/{task.py,tests.json}
   textscore/{task.py,tests.json}
   …（14 个任务 / 28 个文件）
```

- 构建时 setuptools 会**跟随符号链接**把真实文件写进 wheel ⇒ 仓库只有一份源码，
  安装物是真快照（不是指向仓库的指针）。
- `aether init` 把它们**物化**到 `<root>/tasks/`：已存在的不覆盖（记 `existing`）、
  `--dry-run` 一字节不落、包里没有就 `available=false` 并给理由。
- 判据是**装完能出数**，不是「文件在不在」：

```
$ cd ~/my-agent-project && aether init
   示例任务集：_inspect, _parser, …, utils（28 个文件，来自 …/aether/seeds/tasks）
$ aether synth --max 24
   "attempted": 10,  "valid": 5,  "yield_rate": 0.5
```

⚠️ `--max 24` 只跑出 10 次尝试：**受可变异点总数限制，不是坏了**。
`--max 2` 会给出 `yield=0`，看着像"示例跑不动"，其实是样本量不够。

**装完怎么知道自己没装坏（r61 修掉的「可信断层」）**

68 份契约是仓库资产、全量要 30+ 分钟，装出来的包里一份都没有 ⇒
`aether check` 在安装态只会说「0 份契约」。所以另有一条**自检子集**：

```bash
aether selfcheck              # 10 项 / ~1.5s / 预算 60s
aether selfcheck --only ledger,seed
aether selfcheck --json
```

| 项 | 守什么 |
|---|---|
| `root.priority` | 五支优先级 + 兜底不落包目录 |
| `root.not_pkg` | 安装态解析到 home，**包目录零写入** |
| `ledger.roundtrip` | 跨实例读回一致，hash/prev_hash 都在 |
| `ledger.chain` | 干净过、篡改被抓住 |
| `ledger.replay` | 两次重放逐条相同 |
| `gate.teeth` | 人审闸拒未知启动词 / `--force` / 缺复验；机器闸只认 `brew install` |
| `state.no_downgrade` | 已裁决状态不许被机器回退 |
| `backup.roundtrip` | 还原后 sha256 一致 |
| `seed.tasks` | **物化出来的示例真能跑**（不是"文件在"） |
| `deps.zero` | 除自己外没有第三方模块被加载 |

三态 `ok / broken / skip` **不许合并**：`skip` 是「这条没条件测」，
既不算过也不算坏事；而 `selfcheck` 红的时候你该相信它 —— 这一点由
`contract_selfcheck_test.py` 的 **10 个注入反证**逐项钉住（把每道闸的
背后机制打断，对应那一项必须红，一条不漏）。

**数据根怎么定（五支，顺序固定；`path_source` 可查）**：

| # | 判据 | 来源标签 | 语义 |
|---|---|---|---|
| ① | `--root X` | `flag` | 用户说了算 |
| ② | `$AETHER_ROOT` | `env` | 脚本 / CI 说了算 |
| ③ | 包位置是仓库 | `repo` | **开发态**（`pip install -e .` 也是这一支）；cwd 在**别的工作区**里时让位于 ④ |
| ④ | 向上找到 `.aether` | `workspace` | `aether init` 过的工作区 |
| ⑤ | `~/.local/share/aether` | `home` | 安装态兜底 |

⛔⛔ `paths.ROOT` 的语义是**「代码在哪」**，`resolve_root()` 才是「**数据在哪**」。
两者在仓库里恰好相等，装成 wheel 后就分开了 —— 实测（干净 venv）修前：

```
$ aether status
"path": "…/venv/lib/python3.13/site-packages/data/ledger.jsonl"
```

它**不是崩溃**：能读能写、`rc=0`、读数看着正常。三条后果没有一条会报错 ——
`pip install -U` 重装即丢账本、两个项目共用一个 venv 就混库、只读镜像第一次
写账本就崩。**「悄悄写错地方」比「崩」更坏**：崩了会有人查，写错了没人查。

⛔ ③ 的判据是「仓库资产在不在」，**不是「cwd 在不在仓库里」**：68 份契约都
在仓库根跑，而它们大半把子进程的 `cwd` 设成临时目录；若按 cwd 判，那些子进程
会掉到 ⑤（`~/.local/share/aether`）⇒ **测试写生产库**。

⛔⛔ 但 ③ 因此有个反直觉的副作用，r61 第二块修掉了：`pip install -e .` 装的
就是**仓库本身**，于是 `cd ~/my-project && aether evolve` 也落回仓库账本 ——
两个项目混库，而两边都「看着正常」（与修前 `site-packages/data/` 是同一个病，
换了件衣服）。修法是给 ③ 加一个**唯一的例外**：

```
cwd 在仓库**外** 且 那里确实是一个 `aether init` 过的工作区 ⇒ 工作区说了算
```

⛔ 例外只认**找得到的 ④**，不是「cwd 在仓库外就换根」—— 后者会让上面那批
在临时目录里跑的子进程集体掉到 ⑤。两条都钉在 `contract_root_resolve_test.py`：

| 断言 | 守什么 |
|---|---|
| `F1` | cwd 在仓库外的工作区里 ⇒ 工作区优先（`-e` 装不再混库） |
| `F1c` ⛔⛔ | 例外只认找得到的 `.aether`；临时目录里没有 ⇒ **仍然落仓库** |
| `F1d` ⛔ | 仓库内部的 `.aether` 不生效（根不许被拽出仓库） |
| `F1e` ⛔ | 反证：`_within` 恒 True（= 修前行为）⇒ `F1` 必须翻回仓库 |

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
│   ├── discover.py         从本机已有工具生成能力清单（**发现**，不下载不安装）
│   ├── _version.py         版本号/格式号的**唯一事实源**
│   ├── paths.py            目录布局的唯一事实源（cli/serve/doctor 共用）
│   ├── caps_candidate.py   能力候选：发现 → 闸 → 留痕 → **人批准**（引擎不装）
│   ├── judge.py            **判据集身份**：尺子有指纹，变异够不到它
│   ├── world_facts.py      **陈述性知识**：学到的事实可继承/可选/可回滚
│   ├── learner.py          外部知识摄入（复用记忆闸，不自搞一套）
│   ├── aging.py            跨轮退化检测（ALE 四类信号）
│   ├── interop.py          UCI 通用接入层（agent/skill/tool 挂得上叫得动）
│   └── ui.py               桌面窗口（同源托管，不引 Electron）
├── tasks/                  可验证环境（`--envs auto` 时全部纳入，见第 104/105 条）
│   ├── textscore/          数值归一化与打分（手写种子，4 个之一）
│   ├── eventflow/          事件流聚合（会话切分 / 滑动窗口 / 分位数）
│   ├── stateflow/          状态机（转移 / 合法性 / 可达性 / 路径计数）
│   ├── scheduler/          区间调度（冲突 / 最大并发 / 贪心 / 区间合并）
│   └── core/ token/ textwrap/ _inspect/ _parser/ _writer/
│       chaos/ encoder/ shlex/ utils/
│                           由 `scripts/worldgen.py` 从 **Python 标准库真实源码**
│                           切出（oracle 由执行得到，非人工编造）。
│                           ⛔ 入套件要过**题源强度闸**：有效变异体 < 2 ⇒ 弃
│                           （世界大小会撒谎，题量不会，见第 105 条）
├── tests/                  55 个契约测试（RED→GREEN + 反证组；`aether check` 一条命令跑全）
├── supervisor/             TypeScript 外部监督器（只读账本 + 异常检测）
├── console/                零构建的只读可视化控制台
├── data/seed/              **种子数据**（随仓库发布的最小现场：小账本 + 记忆）
├── scripts/demo_evolve.py  端到端 demo + 报告
├── scripts/worldgen.py     从真实 Python 源码切出可验证环境（三道硬闸，见第 104 条）
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
  27 个写死 ROOT = Path("/Users/…/V6")   ← 导入主仓库、读主仓库 data/
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

### 88. **判据只认增益 ⇒ 没有东西会在自己的产出上花力（努力分配）**

扫出来的自指负载：**零调用方接口 12 个、只写不读属性 10 个**（都是下界）。
它们不是"没人想起去做"，而是**结构上做不出来**：

```
删掉一个死条目 ⇒ reward 不变 ⇒ 按当时那套判据它永远提不出来：
  · 组内相对优势  —— 为**有噪声的 reward 采样**设计 ⇒ 清理恒 0
  · 能力地板      —— gain < 0.01 ⇒ 清理（gain=0）**必死在这条**
  · 增殖闸        —— 防膨胀；清理必然收缩 ⇒ 结构性不适用
  · 泛化闸        —— dev_gain <= 0 ⇒ no_gain ⇒ blocks ⇒ 又被拦一次
⇒ 四道闸三道拦不住它、一道必杀它 ⇒ 机制只增不减。
```

修法不是"加一个清理通道"，是**加第二根轴**（reward 轴 ∪ 减负轴）：

```
⛔ 两轴**取或、不加权求和**：reward 单位是分、load 单位是条，
   换算成同一个数 ⇒ 那个比例是编的 ⇒ 系统只是去讨好比例
   （与"held-out 参与选择 ⇒ 闸自我取消"同形态）。
⇒ 排序键 (gain, load_gain) ⇒ **能力优先**：有得涨就涨，没得涨才收拾。

⛔ 减负轴**不判组内优势**：组内相对优势是给有噪声的采样用的，
   死条目数是**确定性计数**，没有噪声要归一化。
⛔ 减负读数必须**在候选产物上现扫复核**，不许相信提案自报的数。
⛔ 清理是**常设提案器**（不看 weakness）：码健康是维护轴，不是失败信号
   —— 发展集全绿它也不会消失。放在弱点分支之后且不受其 return 影响。
```

⛔ 顺带修掉那个更大的机制缺陷：原先 `if not weaknesses: return rep`
   ⇒ 发展集一全绿这一轮就什么都不做（实测 10 轮里 r2–r10 全这个，空转 9 轮）。

---

### 89. **用错闸比没有闸更隐蔽：清理要问「变差没有」，不是「涨了没有」**

```
generalization.py:319  if dev_gain <= self.dev_floor: return no_gain
⇒ 清理的 dev_gain 恒 0 ⇒ 必得 no_gain ⇒ blocks=True ⇒ 永远提交不了。
```

看起来是"闸在工作"，实际是**把泛化闸装在了它不该管的量上**：
`overfit` 的定义是"发展集涨了但 held-out 没跟着涨"，而清理**根本不涨**，
谈不上讨好判据。

```
⇒ 对清理，held-out 的职责从「要涨」换成「不许掉」：
  · removal_safe   —— held-out 没变差 ⇒ 放行
  · removal_regress —— 删掉的东西在 held-out 上真被用到 ⇒ 拦
  同一套 held-out、同一个 min_tasks、同一条判别力降级，只是换了个读法。
```

⛔ 三条纪律：
1. **新裁决名必须登记进 `VERDICTS` / `PASS_VERDICTS` 封闭集合**。
   `blocks` 若写死 `!= "generalize"`，合法的放行会被判成拦截 ——
   那种失败**不报错**，只是清理永远提交不了，看起来像"清理没用"。
2. **反证必须能翻转**：删对 ⇒ 放行，删错 ⇒ 拦（E2/E3 两条裁决不同）。
   只有"放行"没有"拦截"的闸是橡皮图章。
3. ⛔ **不加"清理豁免"这种开关**：想删有用的东西，闸照样要响。
   清理走的不是旁路，是同一套闸的另一个职责面。

⇒ 更一般的一条：**新机制上线前，先问它落在现有哪一道闸上、
  那道闸管的是不是这个量**。答案是"没有一道管它" ⇒ 才需要新闸；
  答案是"有一道，但管的量不对" ⇒ 要换职责，不是加开关。

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

## 5-quater-2. 第 91–93 条：r39 收尾（清理轴上线后的三个反噬）

cleanup 提案器落地之后，反噬全落在**测试自己身上**。三条同族：
每一次都是"机制本身是对的，但它让别人测到的不再是那件事"。

### 91. **加一根新轴 ⇒ 所有对照组必须逐个重审（红在对照组自己身上最误导）**

`CleanupProposer` 是**常设**的（不看 weakness，每轮都跑），
`EvolutionConfig.cleanup_enabled` 默认 **True**（默认关 = 假仪表，第 83 条）
⇒ **9 份契约 / 15 处 `EvolutionLoop(...)`** 的行为被悄悄改了。

```
contract_evolution_test D6 翻红：第二轮"已修好"仍提清理并提交
                        ⇒ "no_weakness" in revert_reason 为假
```

⛔ 那**不是回归，是新机制按设计在工作** —— 但它让这条断言测到的
   不再是它声称测的东西（"能力侧有没有在硬凑变异"）。

⇒ 处置一律**单变量对照**：只测本轴的契约显式 `cleanup_enabled=False`。
   ⛔ 只传 `cleanup=None` **不够** —— `__init__` 会**自动再造一个**；
      默认开的开关不能用"不传"来关。
   ⇒ 同时补**反证**证明"关掉"真在起作用（D7：打开清理 ⇒ 同一个
      "已修好"的轮次真的会提交；否则 D6 的"不提交"可能是恒不提交）。

⛔ 夹具要点：`genome_dead_entries` 按 **sorted** 扫、`cleanup_group_size=1`
   只取第一条 ⇒ 靶子名必须排在撑 reward 的技能**之前**，否则清掉的
   就是撑 reward 那条 ⇒ `cleanup_regresses` ⇒ 测不到想测的东西。

### 92. **契约的判据落点必须与被测对象同源（加载错目标 = 恒真 + 整组崩）**

`contract_self_reference_probe_test._load_probe()` 加载的是
`scripts/probe_self_reference.py`。而扫描判据 2026-10-15 已搬进
`aether/selfref.py`，脚本那边只剩 25 行 CLI 薄壳 —— **加载目标没跟着搬**：

```
· A1/A2 对着 25 行空壳扫"没有任何写动作" ⇒ 恒真（绿）；
· B/C/D 三组在第 93 行 AttributeError **整组崩掉**（红）。
⇒ 一份"看起来有 20 条断言"的契约，实际只有 2 条恒真的在跑。
```

⇒ 改对目标之后 B/C/D 三组第一次真正执行 —— 这就是修好假绿的代价：
   它们立刻暴露出自己的真问题（第 93 条）。

⛔ 同一趟搬迁还顺带把布局的**第五份**带进了 `aether/`：
   `selfref._main` 里手拼 `ROOT / "data" / "ledger.jsonl"`。
   它此前住在 `scripts/`（静态闸不扫那里），搬进 `aether/` 之后
   `contract_entrypoints_test` 的 E1 立刻响 —— **它是唯一发现这件事的东西**。
   ⇒ 修法不是加白名单，是改问 `paths.paths(ROOT)["ledger"]`
     （"布局只有一份"这条纪律，连搬家都会破）。

### 93. **契约不得押在生产状态上（形状会随运行漂移 ⇒ 断言看运气）**

修好加载目标后暴露出来的：D 组读 `ROOT / "data" / "ledger.jsonl"`
并对它的**形状**下断言（"增益数 > 0""按会话分组 ≥ 3 组"）。

```
· 契约变成非确定的 —— 生产账本的形状随每次 evolve 漂移；
· 更糟的是它**从未真正跑到过**（第 92 条）⇒ 这份耦合从未生效过，
  一旦修好加载目标，它立刻变成"看生产账本心情"的红灯。
⇒ 换成**形状已知的合成账本**：断言针对探针的判据，不针对数据的运气。
```

⛔ 顺带把三处"假通过"一起修掉：
   ① 账本不存在时原实现写 `ck(..., True, ...)`（**跳过 = 通过**）⇒ 现在
      `present=False` 必须被断言，且 `note` 非空；
   ② "没有增益 ⇒ 比值是 **None 而不是 0.0**" —— 0.0 会伪装成"有读数，
      只是很小"；
   ③ **无 sid 的事件单独成桶**，不许并进具名会话（并进去 ⇒ 那个会话的
      机制/增益比值被别人的事件污染，而输出上完全看不出来 ——
      与"缺会话归属 ⇒ 跨运行串味"是同一个坑，只是在读数侧）。
   ④ `_bare()` 归一化是**承重**的，不是装饰：反证直接断言
      `"eval.verdict" not in GAIN_KINDS`（不归一化就认不得）。

---

## 5-quater-3. 第 94 条：默认开的开关，契约必须直接断言"默认值本身"

r39 收尾的最后一格。**用显式参数构造的测试，只能证明"传了会生效"，
证明不了"生产默认就启用"。**

```
EvolutionLoop(..., config=EvolutionConfig(cleanup_enabled=False))   # 对照组
```

那条断言证明的是"关掉会怎样"。若某天 D 组的构造式被照抄成这种写法
（对照组喧宾夺主），把默认值翻成 `False` ⇒ **契约全绿，而生产里这根
轴是关的** —— 整轴空转，无人知晓。与第 83 条「默认关 = 假仪表」同族，
只是这次落在**测试侧**。

⇒ 每加一个"默认开的开关 / 阈值 / 配额"，必须另有一组**直接断言
`EvolutionConfig()` 的默认值本身**的条目：它不依赖任何传参路径。

**判别力是实测出来的，不是我以为的**（三个事故版，逐个翻、逐个跑）：

| 翻掉的默认值 | 转红的条目 | 条数 |
|---|---|---|
| `cleanup_enabled` True→False | D1 D2 D3 D4 **+ F1 F4** | 6 |
| `min_load_gain` 1.0→0.1 | **只有 F2** | 1 |
| `cleanup_group_size` 1→5 | **只有 F3** | 1 |

⇒ 两条结论，与我写下 F 组时的设想**相反**（别照旧注释改回去）：

① F1/F4 的价值**不是**"唯一能看见"，而是**直接命中根因**。翻
   `cleanup_enabled` 时 D1–D4 也会红，但那 4 条读起来是"这一轮无事可做"
   —— 与关掉清理之前的旧行为**同形**，只会把人引向"循环动不了"，
   而不是"开关默认就是关的"。
② F2/F3 才是**全文件唯一的读数**：把减负地板放松到 0.1 条、或一次清
   5 条，除它们之外 30 条断言一条都不响 —— 这两个旋钮改了没人知道。

⛔ E1–E5 在三个事故版下**都不红**（E 组直接构造 gate，不经
   `EvolutionConfig`）—— 预期之内：不该让不相干的组来盯默认值。

常驻探针：`scripts/probe_default_values_audit.py`
（**不进回归**：逐个翻 + 逐个复制仓库跑契约，实测 3 个 mutant 近 3 分钟）。
⛔ 探针自己也有反证 —— 它登记 `CANARY` 并断言它**必须**转红：
报 0 条 ⇒ 探针自己坏了，此时所有"无人盯"的结论作废。
（与"审计脚本自己也要有反证组"同一条纪律，这次防的是**"探针没跑起来"
被误读成"默认值没人盯"**。）

---

## 5-quater-4. 第 95 条：让系统能提议「改自己的源码」，但落点是证据不是写入

把清理轴从**基因组**推到**源码**。这是两种风险量级，边界必须画清楚：

```
改 harness —— 纯数据（frozen + replace）⇒ 回滚 = 指针回退，
              且判据住在 harness 之外 ⇒ 裁决独立于被测对象
改源码   —— 源码里**住着判据层**（judge.JUDGE_MODULES）。
              让循环改它自己的判据 ⇒ "非回归闸通过"不再独立于被测对象
              ⇒ **自证**；而且删错了**不可逆**（账本只能留痕）
```

⇒ 系统可以**提议**、可以**过闸**、可以**记账**；**执行**那一步由人完成，
   并且在**构造上**够不到生产源码（`apply_edits` 对生产根直接拒绝）。

**侦察先推翻了一个想当然**：系统其实**早就**能提议改自己的源码
（`cleanup.build()` 扫 `selfref`、过滤只留 `aether/`、排除判据模块）。
缺的不是"能提议"，是**提议之后没有闭环** —— 三个实测缺口：

| # | 缺口 | 实测 |
|---|---|---|
| ① | 循环里从没调过 `build()` | `evolution.py` 只调 `source_load()` 读个数 |
| ② | ⛔⛔ 「走同一套闸」是假的 | `validate()` 写 `if non_regression_ok is False` ⇒ 调用方不传就跳过；两个生产调用点**从来不传** ⇒ 四道闸里唯一会拦的那一道**没被喂过裁决** |
| ③ | 第三类死形态没人扫 | 36 行不可达代码既不是 dead_api 也不是 write_only ⇒ `build()` 扫不出来 |

**三类死形态的性质不同**（别加成一个"债有多少"的数）：

| 形态 | 判据 | 能撑住什么 |
|---|---|---|
| 零消费方接口 | **启发式**（名字没在别处出现过） | 只能**提案**（反射/框架回调静态不可见） |
| 只写不读属性 | **启发式**（读形态扫不全） | 只能**提案** |
| **不可达块** | **语法级确定**（终止语句之后的同级语句永不可达） | **证明** ⇒ 能过闸 |

⇒ 第三类才带**可执行的结构化编辑**（行区间 + 被删原文的 sha256）。
   行号会漂，指纹不会；漂了 ⇒ 拒绝对拍（证明过的不是这段代码了）。

**三条硬约束（构造上，不是"记得别这么做"）**：
① 生产根不可写；② 指纹不匹配 ⇒ 不许应用；③ 没有对拍裁决 ⇒ 不许放行
（`unverified` 就是红，不冒充"通过"）。

**本轮实测抓到的三个坑**（都不是洁癖，是会让闸空转的）：

① **排除面不同源**：`build()` 用 `judge ∪ SOURCE_PROTECTED`，
   `validate()` 只用 `judge` ⇒ 手工构造的 `aether/judge.py` 提案在第 ① 条
   **不触发**（`judge` 不在 `JUDGE_MODULES` 里）⇒ 收口到 `_protected_modules()`。
   ⛔ **闸自己（`srcmut`）也在排除名单里** —— 清理能删掉自己的闸 = 没有闸。
② **跑不起来 ≠ 删坏了**：环境缺 pytest 那次，before/after 都是 rc≠0
   ⇒ 按"两边一样"会得出"删掉没问题"这个**完全错误**的结论
   ⇒ `error` 字段只保留给"压根没跑起来"，契约红了只进 `failed`。
③ **用错工具**：V6 的 `tests/contract_*.py` 是**可执行脚本**（退出码 0 = 全绿），
   不是 pytest 用例。用 `pytest` 跑它 ⇒ `no tests ran` 且退出码 0
   ⇒ 对拍两边恒等 ⇒ 空闸。与 r39 里用错 `probe()` 而非 `probe_removal()` 同形态。

**成本实测 ⇒ 职责分离**：一次对拍 = 复制一份仓库 + 跑两遍冻结契约，
选中 8 份时真实耗时 **10 分钟级**。

```
循环 = 提议器   source_cleanup_enabled=True（默认开）
               扫描 → 提议 → 记账，max_source_probes=1 ⇒ 每轮对拍 1 条
CLI  = 验证器   aether caps cleanup validate ⇒ 花时间给出真裁决
```

⛔⛔ **本条已由第 98 条推翻，别照这里改回去**：r40 交付时 `max_source_probes`
   默认是 **0**（"循环只提议、不对拍，对拍交给 CLI"），依据是「一次对拍 ≈
   10 分钟」。实测那是一条**未跑完**的取证留下的依据，真实成本 **159.7s**。
   更关键的是：预算 0 ⇒ 唯一可证明的那类**永远**拿不到裁决 ⇒ 提议 127 次 /
   过闸 0 次 ⇒ 整条"过闸 → 等人执行"的流水线是**死的**。
   ⇒ 默认值现为 **1**；`contract_srcmut_test` 的 H2 钉住它。

常驻探针新增两个 mutant：`source_cleanup_enabled` True→False（整轴空转）、
`max_source_probes` 1→0（默认值朝便宜方向漂 ⇒ 死闸复现）。

---

## 5-quater-5. 第 96 条：**「跳过 / 不适用」是最会撒谎的那种通过**

r40 收口时，三个坑全不在新机制里，而在**验证装置自己身上**。
它们长得一模一样：报告说"通过/失败"，而那件事根本没发生。

**① 跳过分支自己会坏，且坏法最误导**

```
契约写了：外部工具跑不动 ⇒ 显式跳过、不算失败也不算通过
实际是：  `if nr is not None:` 只包住第一条断言，J3–J12 全在守卫外
⇒ 退化分支把 nr = subj = None 之后，下一条断言立刻 None.get() ⇒ AttributeError
⇒ **"机器忙"被报成"契约失败"**（rc=1 + traceback，而不是"我没跑起来"）
```

`contract_rea_bridge_test` 走 Ghidra（重资源 JVM 工具），并发时退化成
**空壳信封**（`ok=true` 但读数为空）。单独跑 18.9s 全绿，并发跑必崩
⇒ ⛔ **并发才红的契约，先单独跑一遍再定归属**。

修法必须两条一起做，缺一条就成假仪表：
① 退化分支**整段**进守卫（脚本重排缩进，先校验边界再动手）；
② 新增 `SKIPPED = []` 单独记账，末尾打
`⚠️ 断言 N 组被跳过，**未覆盖 ≠ 通过**`。

⛔ **不判红** —— 「跑不起来」≠「测不过」（与第 95 条的三态同纪律，但这里
选择不阻断回归：红会把"机器忙"报成"契约坏了"，让回归不可用）。
⛔ 但**必须**记：跳过混进 PASS ⇒ 工具哪天彻底跑不动了，这份契约会
**恒绿且零覆盖** —— 与「空冻结套件不得恒绿」同一个病。

反证方向是**反的**：这里要注入"空读数"，证明它**能干净跳过**（不是崩），
而不是证明它会红。实测：修前 rc=1 崩在 `None.get`；修后 rc=0 + 告警可见。

**② 一条 `SyntaxWarning` 底下埋着一条空转断言**

```python
not ({"\.git", "node_modules", "__pycache__"} & set(names))
```

`"\.git"` **不是** `".git"` —— 反斜杠被原样保留，集合里装的是长度 5 的
`\\.git` ⇒ **永远匹配不到任何真实目录名**。那条断言声称检查 `.git`，
实际只检查了后两个。

⛔ 它是被**顺出来的**：`scan_dead_api` 的扫描面含 `tests/`，用
`ast.parse(txt)`（无文件名）⇒ 那条 `invalid escape sequence '\.'`
报成 `<unknown>:652`。⇒ 把仓库里每个 `.py` 都 `compile()` 一遍并收集
SyntaxWarning，全仓库几秒钟 —— 这种警告几乎总指向"作者想写的是别的东西"。

⛔ 修完必须证明它有牙：摘掉 `serve.py` 里 `FILE_SKIP_DIRS` 的 `.git`
⇒ **修好后** J4 转红；**修好前**只有 J3 转红、J4 完全无感。

**③ 探针的"目标串"必须有词边界**

新增字段 `source_cleanup_enabled` **整串包含**旧 mutant 的目标串
`cleanup_enabled` ⇒ 裸 `re.escape(old)` 把它数成 2 次 ⇒ 注入守卫拒绝
⇒ 探针连同 `CANARY` 自检一起报废。

⛔ 好消息是它 **fail-closed** 了（没发布任何错误结论）。
⇒ **修机制不修条目**：匹配式两端夹 `(?<![A-Za-z0-9_])` / `(?![A-Za-z0-9_])`，
  否则下一个 `xxx_cleanup_enabled` 还会再废一次。

---

## 5-quater-6. 第 97 条：契约守护的路径 ≠ 生产走的路径

本轮"跑几轮看有没有进化"，结果跑出的缺陷**不在进化循环里，而在验证它的装置上**。

现象：进化运行都在第 3 轮撞上 `baseline_reward=1.0` 并空转停止。要判断这个
"到顶"可不可信，得先看泛化闸有没有判别力 —— 一查，它没有。

```python
# aether/repair.py · difficulty_report
spec = getattr(t, "spec", None)
if isinstance(spec, dict):                    # ⛔ 生产传的是 mappingproxy
    src = spec.get("broken_source")
if src is None:
    src = getattr(t, "broken_source", None)   # ⛔ FrozenTask 没这个属性
if not src:
    continue                                  # ⇒ 每个任务都被跳过
```

`FrozenTask.__post_init__` 为兑现"冻结基线"的承诺，把 `.spec` 包成了
`MappingProxyType` —— 它**不是** dict 子类。于是生产路径上 labels **恒为空**，
`spread` 恒为 `False`，泛化闸拿不到任何同层对照。

**为什么既有契约没抓到**（这才是最值得记的）：

`contract_difficulty_test.py` 的 A/B 组喂的是 `synthesize_tasks()` 直接返回的
**TaskInstance**：没有 `.spec`、有 `.broken_source` ⇒ 走兜底分支 ⇒ 正常工作
⇒ **契约全绿**。而生产 `cmd_evolve` 喂的是 **FrozenTask**，是**另一条路**。

⇒ 契约写的判据是对的，它只是**压根没扫到生产那个入口**。
   与第 92 条「契约的判据落点必须与被测对象同源」同形，但侧面不同：
   · 第 92 条：扫错了**文件/目标**（模块从 scripts 搬进包，扫描目标没跟着搬）；
   · 第 97 条：喂错了**输入类型**（契约用 TaskInstance，生产用 FrozenTask）。
   ⇒ **同源 = 同目标 + 同入参形态**，两头都要对。

修法：`isinstance(spec, Mapping)`（`collections.abc`）。修复前后对拍（同一批任务）：

| 路径 | 输入类型 | 修复前 | 修复后 |
|---|---|---|---|
| 契约 | `TaskInstance` | easy 3 / hard 2 ✅ | easy 3 / hard 2 ✅ |
| 生产 | `FrozenTask` | **labels = {}** ⛔ | easy 3 / hard 2 ✅ |

⇒ 修复后**两条路径一致**，泛化闸第一次真正拿到同层对照。
   代价真实：难度分层从"空转"变成"真跑"，每次运行多花约 2s。

⛔ 补契约 I 组 4 条（含反证 I3：复原旧写法 ⇒ labels 恒为空 ⇒ I1 必须转红，
   否则 I1 在新旧实现下都绿，是一条没有牙的断言）。

**顺带确认**：`contract_streaming_test.py` 在全量回归里 timeout=1（>600s），
单独跑 **66/66 全绿 / 2m15s**；已 grep 确认其完全不涉及 repair / difficulty /
evolve ⇒ 归因并发负载，非本次改动（与第 96 条① 同形）。

---

## 5-quater-7. 第 98 条：**默认关的「可选验证」比默认关的开关更隐蔽 —— 它让整条流水线恒红**

第 94 条钉的是"默认开的开关不许静默关掉"。这一条是它的**镜像**：

```
第 94 条   默认**开**的开关 ⇒ 不能用「不传」来关（会被 __init__ 再造）
第 98 条   默认**关**的验证 ⇒ 不能用「太贵」来关（会让下游恒红，且恒红看起来
                                                  跟「没有问题」一模一样）
```

⛔⛔ **而"太贵"还不是它真正承重的原因** —— 本轮把默认值放开后实测撞到了
     更硬的那一条：**跨进程无限递归**。详见下面「真正的承重原因」。

**实测经过**

r40 把源码清理的对拍预算默认设成 `max_source_probes=0`，依据是「一次对拍 ≈
10 分钟」。但真正的来源是一次**没跑完**的取证（11m54s 被 kill）—— 未完成的
耗时被当成了测得的耗时。本次实测：

| 项 | 实测 |
|---|---|
| 删前基线（8 份冻结契约） | **29.7s** |
| 端到端（复制仓库 + 删前 + 删后） | **159.7s**，裁决 `ok=True, applied_lines=36` |

⇒ 与"太贵"这条依据差 **4.5 倍**。而它的代价不是"慢一点"：

```
源码清理里**只有** unreachable 这一类带可执行编辑
（dead_api / write_only 是启发式，本就过不了闸）
        ↓ 预算 = 0
它永远拿不到裁决 ⇒ validate 第 ③ 条 fail-closed
        ↓
提议 127 次 / 过闸 0 次 / 执行 0 次
```

`cleanup.source_pass` 连续 3 次均为 `n_proposed 9 / n_probed 0 / n_passed 0`。
**它有事件、有读数、看起来很健康 —— 只是永远记 0。** 这比"默认关的开关"更
隐蔽：后者至少能被 `EvolutionConfig()` 直接戳穿。

**修法（三条）**

- **A ⛔⛔ 实测证伪，默认保持 0**：放开试过了，代价是**回归套件被拖垮**
  （`pass=50 fail=0 timeout=1` → `pass=46 fail=1 timeout=4`）。
  ⇒ r40 那个「循环 = 提议器 / CLI = 验证器」的切分由此从**约定**
     升级为**有实测支撑的设计**。详见 98-bis。
- **A′ 可重入守卫 + A″ 进程级一次性闩锁**：**这才是本轮的硬收获** ——
  放开预算之所以会失控，是因为没有守卫。现在有了 ⇒ 以后想开就开，
  不会再出现 108 个进程那种事故。
- **B**：「没跑」必须**可分辨** ——
  - 每条提案记 `reason_code`（受控集合：`verified_ok` / `verified_fail` /
    `probe_unverified` / `no_contracts` / `over_budget` / `heuristic_no_edits`）
  - `cleanup.source_pass` 新增 `probe_budget` / `n_provable` / `unverified_reasons`
  - 预算为 0 时 `note` 自带警告 ⇒ 外部能区分「闸被关着」与「没有债」

⛔ **注入点必须在类上，不能进 `EvolutionConfig`**：配置会被 `asdict()` →
   `fingerprint()` JSON 化，塞一个函数进去 ⇒ TypeError ⇒ 被 `except` 兜住
   ⇒ **指纹静默退成空串** ⇒ checkpoint 续跑的一致性校验从此恒过。
   ⇒ 机制（可注入）与事实源（可序列化）必须分开存放。

---

### 98-bis. ⛔⛔ **真正的承重原因是「可重入」，不是「太贵」**

放开默认值后，一次 `evolve --rounds 5` 跑了 **12 分钟仍未结束**，账本停在
`cleanup.proposal` 之后一动不动，`pgrep` 显示 **108 个** `tests/contract_*.py`
进程在增长。逐个隔离后定位到：

```
run()  →  _source_cleanup_pass()  →  srcmut.probe()
                                          ↓ 起**子进程**跑冻结契约
        contract_evolution_test / contract_checkpoint_test
              → 进程内构造 EvolutionLoop 并 run(...)   ← 回到第一行
```

⛔ **对拍是跨进程的**，所以"进程内旗子"挡不住：子进程的内存从 `os.environ`
复制，看到的是初始值 `False` ⇒ 第二层继续 probe ⇒ 继续起子进程 ⇒ 层数无上限。

⇒ 这也解释了为什么当初 `max_source_probes=0` 能一直"正常工作"：
   **它不是因为太贵被关掉，而是关掉它恰好把递归压住了。**
   一个成本参数在隐式承担安全性职责 —— 这两件事正交，不该由它兜底。

**修法**：把守卫做成**双份**，`_source_cleanup_pass`（外层）负责
"要不要做 + 记账"，`_source_cleanup_pass_inner`（内层）只管"怎么做"：

| 层 | 机制 | 挡住什么 |
|---|---|---|
| 进程内 | 类旗子 `_in_source_cleanup` | 同一进程里重入 |
| **跨进程** | **环境变量 `V6_SOURCE_CLEANUP_ACTIVE`** | **子进程里再跑一遍（真正的递归）** |

⛔ 拆成两个函数是必需的：守卫与业务逻辑写在一个函数里 ⇒ `return` 散落多处，
迟早漏掉 `finally` 复位（与「退化分支要整段进守卫」同族事故）。

⛔ **`contract_runner` 用 `env = dict(os.environ)` 生成子进程环境** ⇒ 这里设的
   环境变量会被子进程继承 ⇒ 这才让"跨进程守卫"成立（也是它当初清掉
   `PYTHONPATH` 的同一个机制，见 `srcmut.py` docstring）。

**A″ 进程级一次性闩锁**（同一进程只真对拍一次）：对拍的前提是"源码未变"，
而**同一进程内源码不会变** ⇒ 第二次及以后重跑**零信息量**，只是把分钟级
成本乘上调用次数。⛔ 守卫挡"嵌套"，闩锁挡"重复"，是两件事；跳过一律记
原因码 `already_probed`，不许记成通过。

**实测对照**（同一份 `contract_checkpoint_test.py`，单变量）：

| 配置 | 结果 |
|---|---|
| 改动前（`=0`） | **27s 全绿** |
| 无守卫放开 `=1` | **>7min 不终止**，108 进程失控 |
| 守卫 + 闩锁放开 `=1` | **2m26s 全绿** |

⚠️ 但**放开后的全量回归仍是 `pass=48 fail=1 timeout=2`**（基线 `pass=50
fail=0 timeout=1`）：`contract_ops_test` 单独跑 113s（改动前 58s），在全量
并发下仍超 600s ⇒ 循环里做分钟级验证的**边际代价会落在回归套件上**。
⇒ 所以默认回到 **0**：验证仍由 CLI 承担（`aether caps cleanup validate
--probe-limit N`），守卫与闩锁留在原地，等要给这条轴提速时再开。

**契约落点**：新增 **J 组 5 条**，其中 J5 是**运行时嵌套实验**——注入廉价
runner 在"删前基线"里再调一次 `_source_cleanup_pass`，断言嵌套那次**只记
`cleanup.source_skipped`、不 `cleanup.scan`**（即递归层数封顶为 2）。
⛔ 这个实验**只能**用廉价 runner 做：用真对拍做 ⇒ 子进程里再起子进程
   ⇒ 就是那 108 个进程。

⛔ 顺带修掉一条我自己刚写下的**恒真断言**：J1 第一版拿常量的**值**
   （`"V6_SOURCE_CLEANUP_ACTIVE"`）去源码里找，而源码里只有常量的**名** ⇒
   恒真、永远绿。改回按名检查后才对。（这正是第 92 / 97 条在契约侧的重演。）

---

⛔⛔ **附带结论（关于第 97 条的呼应）**：本轮"到顶"复跑与修复前**逐轮一致**
（0.0→0.6→1.0→1.0，第 4 轮空转停止）⇒ 泛化闸修复**不改变进化轨迹**，
只恢复了一个恒空读数。因为 weakness 只吃 `eval.verdict` 的**失败**证据，
不吃难度分层。⇒ **"到顶"是题面到顶，不是能力到顶**：每次 evolve 只在
`--dev-env textscore`（**10 条 test**）上跑，`--depth 1`、`--proposer
template`，第 3 轮把 10 条全解掉 ⇒ 无失败 ⇒ idle。要看更长的曲线，
得加题或换 proposer，不是修闸。

---

## 5-quater-8. 第 99 条：**测量型契约在并发套件里读数不可信 —— 超时只是症状**

第 98 条收口后，全量回归**连续两次独立运行**都是
`pass=49 fail=0 timeout=2`（rc=1）。两份红：`contract_ops_test` /
`contract_streaming_test`。

⛔ 第一反应是"并发负载抖动"，但**两次逐项一致 ⇒ 稳定可复现 ⇒ 按真缺陷处理**。

**往下挖，推翻了自己的假设**：

| 环节 | 实测 |
|---|---|
| 单次 `Ledger.append()` | **34.5 ms**，几乎全是 `os.fsync` |
| ops 契约放大账本 3000 条 | **103 s**（与实测 113s 吻合） |
| 再叠 4 路并发磁盘队列争抢 | **>600 s** ⇒ timeout |

⇒ 咽喉在 `ledger.py:391-394` 的 `f.flush(); os.fsync(f.fileno())` —— **每条事件都同步落盘**。

⛔ **超时只是最显眼的症状**。真正的病是：并发下的性能读数**根本不可信** ——
   即使没超时，量到的也是别的契约的负载。所以**不能靠提 timeout 糊过去**，
   那只是把失效藏进十分钟里。

**修法（两条，各自解决一层）**

- **① 批量写入**：新增 `Ledger.append_many(items, ...)` —— 整批**只在末尾
  fsync 一次**。语义与逐条 append **等价**（hash 链逐条串联不变，落盘文件
  **逐字节相同**），差别只在**中间态持久性**（批量中途崩溃丢整批，而非只丢
  最后一条）。⇒ **生产写入路径仍用 `append()`**；只有"构造基准账本 / 回填"
  这类**可重放**场景用批量。
  ⛔⛔ 这里踩过一个坑：**批次内绝不能再 `_hydrate`** —— 自己的行还在
  Python 缓冲区（可能只落盘一部分）⇒ 重读尾巴会读到**中间某行** ⇒
  `_seq` 被拉回旧值 ⇒ **hash 链错位**（实测 300 条时第 25 条起 seq 少 1）。
  批次期间锁全程持有 ⇒ 内存里推进的 `_seq` / `_last_hash` 才是权威值。
- **② 独占段**：契约可声明 `EXCLUSIVE = True`，`aether check` 把它们排进
  **串行段**（不与任何契约同时跑）。ops / streaming 已声明。
  ⛔ 串行化必须**可见** ⇒ 报告新增 `exclusive` 名单字段，否则"全绿"里
  藏着"这些其实没并发跑过"，与全量并发验收不同形却长得一样。

**收益**（同一套件，前后对比）

| 时点 | pass | fail | timeout | 墙钟 |
|---|---|---|---|---|
| 改动前（两次一致） | 49 | 0 | **2** | ~25 min |
| 改动后 | **51** | **0** | **0** | **8m14s** |

`contract_ops_test`：113s → **1s**（100x）；`contract_streaming_test`：135s → 82s。
批量写入本身：300 条 10.60s → 0.03s（**352x**）。

**契约落点**：`contract_check_test.py` 新增 **F 组 4 条**（F1 钉住两份必须声明
EXCLUSIVE、F2 防普通契约被误判、F3 反证 ⇒ 把 `is_exclusive` 换成恒 False 时
F1 必须转红，证明它有牙）。

---

## 5-quater-9. 第 100 条：**闸恒绿 ≠ 闸没牙 —— 先看判据集与变异空间相不相交**

机检死路清单里排第一的是：`gate.result` **234 条全 True**、`gate.blocked`
**0 次**、`deltas` **全 0**。第一反应是"闸形同虚设"。

⛔ **先反证，再下结论**：用生产同源的 envs / 种子 / runner 喂已知变坏的候选
（`scripts/probe_gate_discrimination.py`）：

| 反证组 | 裁决 |
|---|---|
| C0 恒等（基线自身） | 🟢 绿 |
| C1 砍掉全部工具 | 🔴 红（4 条回归） |
| C2 砍掉 `python_exec` | 🔴 红（4 条回归） |
| C3 口径漂移（换 seed） | 🔴 红（caliber_ok=False） |
| C4 口径多出字段 | 🔴 红 |

⇒ **4/5 转红 ⇒ 闸有牙**。恒绿的病不在闸，在**题面**。

**真根因（三段实测）**

1. 冻结任务用的是 `env.source` = **原始、未被破坏**的源码 ⇒ 种子 4/4 满分
   ⇒ 基线在**天花板**。
2. 种子是**裸**的（只有工具、没有修法）⇒ 它唯一修得好的题就是"本来就
   不需要修的题" ⇒ 通过集 = {原始源码那 4 条}。
3. 提案器只会装技能 / 改路由 / 改元规则 ⇒ **全部**不改变"已正确的源码
   仍然全绿" ⇒ 判据集与变异空间**不相交** ⇒ 闸在结构上恒绿。

```
基线停在种子  ⇒  守的永远是"种子能力"
种子没有修法  ⇒  通过集只含"不用修的题"
变异不改这类题 ⇒  delta 恒 0 ⇒ 234 条恒绿
```

**走过的弯路（实测证伪，别重复）**：给种子**预置**一条 `fix_control_flow`
链，判别力立刻出现（C2/C3/C4 由绿转红，各 6 条回归），但进化活动直接减半：

| 3 轮对照 | 基线 v1 | 种子预置 cf | **基线随 best 升级** |
|---|---|---|---|
| 账本事件 | 155 | **70** | **166** |
| 闸门裁决数 | 10 | **3** | **10** |
| `harness.commit` | 2 | **1** | **2** |
| `eval.verdict` | 65 | **30** | **65** |
| 基线通过集 | 4（恒定） | 7（恒定） | **4 → 7 → 12** |

⇒ 与"记忆预置修法 ⇒ 候选增益 0 ⇒ 空转"是同一条纪律：**外部预置的能力会
掩盖「系统还没有它」这个真实弱点**。

**修法（两条，缺一不可）**

- **① 冻结套件 v2** = 原始源码（防"读不到/跑不动"这类**粗**回归）
  + 真破坏题（防"已具备的修法被顶掉"这类**细**回归）。
  ⛔ 破坏题**不按种子筛**（种子现在修不好，best 装上修法后就修得好）；
  ⛔ `max_n` 必须给够（实测 `max_n=2` 时只有 scheduler 出得来题，会误判成
  "另外三个环境合成不出任务"）；
  ⛔ **每环境只取 1 条**（`FROZEN_BROKEN_PER_ENV`）—— 套件大小是**每个候选
  的付费项**，12 条时契约执行量翻倍而判别力只从 8 涨到 12 ⇒ 不划算。
- **② 基线随 best 升级**（`evolution._rebaseline`）：每次 `harness.commit`
  之后把基线 supersede 到新 best，并记 `baseline.superseded`
  （含 `n_passing_before/after`、`delta`、`shrank`）。
  ⛔ 被 supersede 的旧基线会**拒绝一切对比**（`check_caliber` 首行）⇒ 必须
  **同步替换** `gate.baseline`，不能只标记旧的。
  ⛔ `shrank=True`（通过集反而变小）= 有题从"必须守住"里掉出去 ⇒ 一次
  能力倒退被放行 ⇒ 记账时直接标红，不许混进正常升级里。

**契约落点**：新增 `tests/contract_gate_discrimination_test.py`
**A–E 组 13 条**，其中 **D 组是反证**：基线停在种子时，C2/C3 那两个候选
**是绿的** ⇒ 证明 B1「升级后通过集扩大」这条断言真的有牙，不是恒等断言。

⚠️ **代价真实**：冻结套件 4 → 8 条 ⇒ `evolve --rounds 3` 与相关契约的
执行量上升（全量回归 8m14s → **~39min**，机器负载同期偏高，取数需谨慎）。
判别力是用执行成本换的，不是白来的。

---

### 100-bis. ⛔⛔ **省成本不能省在"这条题要不要执行"上 —— 契约当场把我打回**

套件从 4 涨到 8/12 后第一反应是优化：基线**没通过**的题反正不参与回归判定
（`if base.reward <= 0: continue`），跳过不执行不就省了？

`contract_nongression_test` **两条立刻转红**：

| 断言 | 跳过后 | 为什么 |
|---|---|---|
| C2 崩溃被记账（3 条） | 只剩 2 条 | 基线红的 t3 被跳过 ⇒ 它的崩溃没人记 |
| **G5 基线全 0 时 runner 全崩 ⇒ 仍然拦** | **闸放行** | 一条都不跑 ⇒ `errors` 为空 ⇒ `passed=True` |

⇒ 第二条是**真 fail-open**：闸在自己最该响的时候（runner 全崩）变成绿灯，
而"跳过"看起来只是性能优化。**执行的意义不止产生 delta：崩溃必须可见。**

⛔ 结论写进代码注释：省成本只能省在**套件大小**（`FROZEN_BROKEN_PER_ENV`），
不能省在"这条题要不要跑"。读数里另加 `n_judged`（真正参与判定的题数）
与 `partial_discrimination` 警告 ⇒ "判别力上限"从此可读，不再被套件
大小冒充。

**归因纪律（本轮另外两份红，都不是我的改动）**

- `contract_rea_bridge_test` C2b/C2c：**改前的 HEAD 单独跑也是红的**
  ⇒ REA 是共享外部资源，超时时契约在 `status == "timeout"` 分支里仍硬断言
  ⇒ 契约自身缺陷（它自己注释里就写了"超时是假红"，但这两条没跟着跳过）。
- `contract_repro_test`：并发套件里被 timeout 杀掉，**单独跑 212s 全绿**
  ⇒ 负载，不是代码。

⛔ **"并发跑才红"的必须单独再跑一遍才能定归属**（第 6 条纪律）。

---

### 101. 「会学习」的真缺口：机制在跑，**账上看不见它跑没跑**

回答"还缺什么"时用的机器化差分（AST 扫 `aether/` 里所有 `append()` 事件名
− 4853 条账本实到）给出 14 个零次事件。按「会学习 / 会创造 / 会使用工具」
三条能力线归类后，`knowledge.ingested` 与 `world_fact.promoted` 落在"学习"上，
而它们的根因是**同一件事**：

`providers.MemoryAwareProposer` 的 docstring 写着"记忆进入因果链"，但它把
学习效果**只存在 Python 实例属性里**（`self.skipped` / `self.asked`）——
进程一退出就蒸发。于是账本给出的画像是：

| 读数 | 值 | 含义 |
|---|---|---|
| `evolution:reject` 写入（`source`） | **5 条** | 累计只记过 5 次失败 |
| `knowledge.ingested` | **0 条** | `KnowledgeLearner` 在循环里从未被构造 |
| "因学到而少提了几个候选" | **没有这个数** | 只能用 grep 代码推断，不能用账本读 |

⇒ **看不出这个系统到底有没有在学**。这同时犯了第 4 条（有机制 ≠ 这条路
被走到）和第 10 条（三态必须分开：`search` 抛异常时这里静默
`return []`，"检索崩了 ⇒ 本轮照提重复候选"和"查了、没有这条失败史"
在后续完全不可分辨）。

**修法：把闭环两端各自记账**（`experience.remembered` / `experience.propose`）

- 写入侧：`key / reason / disposition / deduped`。
  ⛔ `disposition` 必须留下来：第二条同样内容的失败会被记忆层判 `deduped`
  ⇒ **没有新增知识**，而事件照样写 ⇒ 不分开就会把「记了 1 条」读成
  「学了 N 次」（V5 的 39x 重复同族）。写入崩 ⇒ `write_failed`。
- 起效侧：`n_in / n_out / n_skipped / n_known_history / skipped_sample`
  + **`recall` 三态**：`ok` / `degraded`（读崩 ⇒ 放行，**必须可见**）/
  `no_memory`（压根没装记忆，**也必须有事件**，否则"没学"和"没装"都是
  "没有事件"，无法分辨 —— 这正是改动前的原状）。
- `cmd_evolve` 里 `MemoryAwareProposer(proposer, mem)` ⇒ 显式 `ledger=led`。
  ⛔ 不传就是 no-op ⇒ **有机制、没被走到**。

**同时把 `KnowledgeLearner` 接进循环**：`weakness` 那条写本来就是
`channel="learner"`，以前却是裸 `mem.write` ⇒ learner 的四道闸（去重 /
最短内容 / 配额截断 / 可达性取证）在生产上一次都没生效。改走 learner 后
`knowledge.ingested` 从 0 → 3，且立刻暴露一条以前看不见的事实：

```
round1 written  retrievable=True   # 真学到了
round2 deduped                     # ⛔ 与 round1 弱点完全相同 ⇒ 第二轮在同一条弱点上空转
round3 written  retrievable=True
```

⛔ `ingest()` 的返回值必须**用起来**：跨轮汇总（`learning_ingest`）才看得出
"这几轮到底学进去了多少"，只看单轮报告会把它读成"每轮都成功"。

**对照（同一 tmp 根、同 seed、4 轮）**：`memory.write` 11 条常数不变
（written 10 / deduped 1）⇒ **写入行为零改变**，只是多了一层可读账。

**契约落点**：新增 `tests/contract_experience_loop_test.py` **A–F 组 19 条**，
F 组为反证（把 `memory.search` 换成恒空 ⇒ A2「记之后不再提议它」必须转红，
否则它就是恒真断言）。探针 `scripts/probe_experience_loop.py` 同源 19/19。

---

### 102. ⛔⛔ **0 次没有一阶信息**：把零次事件分成三类，逐个注入验证

上一条的差分找出 14 个零次事件。**但它们 0 次的含义完全不同**，混在一起
就会误判 —— 上一次踩的坑正是把三者当成一类：

| 类别 | 含义 | 处置 |
|---|---|---|
| ① 参数驱动 | 没人传过参 | 传一次参就能区分好/坏 |
| ② 退避（fallback）分支 | 从没出错过 | **必须注入**：验不出来就是死代码 |
| ③ 功能路径 | 条件从未同时成立 | 用生产数据复算条件是否真的满足 |

②是最危险的：V5 的 `will_delete` 恒 0、`aging_events["pruned"]` 恒 0 都是
这一类 —— **一个静默归零的读数比没有读数更坏**，因为它持续冒充"没问题"。

**实测结果**（`scripts/audit_zero_events.py`，全部注入而非推断）：

| 事件 | 结论 |
|---|---|
| `evolution.checkpoint` / `budget_stop` | ✅ 传 `--checkpoint` / `--max-seconds` 立刻触发 ⇒ 健康，**0 次=没人用** |
| `evolution.checkpoint_failed` | ✅ 指向不可写路径 ⇒ 2 条、精确归因 `ENOENT` ⇒ **有牙** |
| `memory.pruned` | ✅ `prune --apply` ⇒ 68 条逐条留痕 ⇒ **有牙** |
| `baseline.supersede_failed` | ✅ 单元注入 ⇒ **有牙**（契约 A 组） |
| `experience.ledger_failed` | ⚠️ **结构上写不出**：用**同一条坏通道**记自己的失败 |
| `deadend.cooldown` / `world_fact.promoted` | ⚠️ 生产账本上 `_in_cooldown` 返回 `cooling=True`（streak=3 达标）⇒ **机制通**，只是没出现"达标且同轮仍被提议" |

⛔ **诚实未验证 8 个**（`world_fact.promote_failed` / `cleanup.propose_failed` /
`attribution.*` / `gate.blocked` 等）：它们的记账点都在 `_run_round` 内部，
单元级注入进不去。⛔ B 组的教训 —— 最初我在测试里替被测代码补写那条事件，
那是**测试伪造结果**。现已改回只验"异常会冒出这一层"，并在输出里标注
"此层验不到最终记账"。

**一条没修的真发现（属设计取舍，写在代码注释里了）**

同一个 capsule 的失败史被 key 的**默认值**劈开：

```
add_skill:patch:textscore|unknown              streak=15   ← 历史缺 env_pair
add_skill:patch:textscore|textscore->eventflow streak= 3   ← 读取侧只查这个
```

⇒ 15/18（**83%**）的死路证据**永久不可读**（写入点 evolution.py:1757 现在
已必带 `env_pair`，所以这是历史包袱，未来不会再产生）。
⛔ **不修**：把 `|unknown` 并入任意 env_pair = 退回注释 1755–1756 明确否决
过的设计（"缺了 env_pair ⇒ 冷却按候选全局生效 ⇒ 换环境后被误伤"）。
⇒ 取舍的代价是这批证据作废，换来的是**不会跨环境误伤**。这笔账要留下，
不要为了"数据看起来能用"把它改回去。

**契约落点**：新增 `tests/contract_fallback_reachability_test.py` A–E 组 7 条
（含 D 组反证：不注入时 failure 事件**不许**出现，否则是误报）。

**本轮两次自我纠错**（都写进记忆，别重犯）：

1. 续跑实验用 `tail[-40:]` 看 gen.probe ⇒ **把历史尾部当成本轮新增**，
   误判"streak 达标却没冷却"。精确按"续跑前行数"切分后，本轮 `gen.probe`
   只有 1 条，`patch:textscore` 本轮压根没被提议 ⇒ **没有矛盾**。
2. 看到 prune 剪掉的笔记 `access_count` 高达 1383 ⇒ 怀疑"剪枝剪掉了最有用
   的知识"。实测保留的 5 条是 1441–1604（最热），剪掉的中位数是 6 ⇒
   **策略正确，是我怀疑错了**。⛔ 可疑 ≠ 坏，先验再判。

---

## 103. 为什么「进化史上从未走过第 3 代」—— resume 的语义死锁

把生产账本 87 次 `harness.commit` 按 `parent_id` 重建血缘，分布是：

```
距种子 1 代：73 次        距种子 2 代：14 次        ★ 第 3 代及以上：0 次
```

一台自称「自演化」的系统，历史上**从未累积到第三代**。拆开是两个**彼此独立**
的原因，混在一起会看错方向：

### ① 参数语义死锁（ bug，已修 ）

旧代码 `cli.py` 里判断要不要续跑：

```python
rounds_left = cpx.rounds_total - cpx.round_no     # 两个数都来自 checkpoint
if cpx.rounds_left <= 0: return 0                 # 「已全部跑完」
```

上一次运行**必定**写到 `round_no == rounds_total` 才收尾 ⇒ `rounds_left` 恒为 0
⇒ **无论本次命令行传 `--rounds` 多少，都判「已跑完」**。
实测 `--rounds 2` 之后 `--rounds 4 --resume`、`--rounds 6 --resume`，
两次都直接 `return 0`，账本零新增。

生产侧佐证：34 次 `run_config` 里 `checkpoint` 全为 `None`、`resumed_from` 全 0
⇒ **34 次运行没有一次续跑过**，想续也续不动。

修法：抽成纯函数 `resume_window(target, done)`，语义改为
**「目标总轮数来自命令行（人的意图），checkpoint 只提供已完成到第几轮」**。
修复后实测 `resumed_from 0 → 2 → 4`，账本轮号跨进程连续。

### ② 能力饱和（**不是** bug，是设计取舍 ）

修完 resume 后重跑，代数**仍然只有 2**。因为：

```
轮1  reward 0.0 → 0.6   commit  fix_control_flow
轮2  reward 0.6 → 1.0   commit  fix_literals
轮3  no_weakness_evidence（无失败证据 ⇒ 不提议）  idle
轮4  idle_streak=2 ⇒ evolution.stopped「不是失败」
```

`tasks/` 下只有 4 个环境、每个一个 `task.py`（3–5 条 check），
两个胶囊就把 dev 集打满 ⇒ **没有失败 ⇒ 没有弱点 ⇒ 没有提议**。

⇒ **20793 行进化引擎的能力上限，被 4 个小函数锁死了。**
这条只能靠扩大任务源解决（见 `docs/VERDICT_2026-10-17.md` 的 P1），不是缺陷。

⛔ **① 和 ② 不许混为一谈**：修 ① 的价值不是让系统变强，而是让人第一次能
诚实看到 —— 天花板在世界，不在机器。在此之前，「只能进化两代」会被错读成
「机器只能消化两代」。

**契约落点**：新增 `tests/contract_resume_window_test.py` A–E 组 19 条 ——
语义 / 边界 / 脏输入 / **D 组反证**（换回事故版实现 ⇒ A 组必须转红）/
**E 组端到端**（真跑两次 CLI，断言第二次从第 2 轮开始跑）。

---

## 104. 「世界太小」不是比喻：把世界 4→10，然后被实证打脸

第 103 条的 ② 给出一句**推论**：天花板在任务世界，不在机器。推论不是事实，
r48 把它当成假设去**证伪** —— 判据是 r47 定的那一条：**账本里第一次出现第 3 代提交**。

### ① 先造世界：`scripts/worldgen.py`

手写环境写不出规模。做法是从 **Python 标准库的真实源码**里切函数，生成标准环境
（`task.py` + `tests.json`）。三道硬闸，缺一不可：

| 闸 | 判据 | 为什么必须有 |
|---|---|---|
| oracle 由**执行**得到 | 参数自动生成 → 起子进程真跑 → 记录返回值 | 人工编造的期望值会编出「永远对」的题 |
| JSON 无损 | `expect` 必须能 `json.dumps → loads` 往返且 `==` 原式 | 沙箱只传 JSON；float/bytes 会静默变形 |
| **F2P 必须能打破它** | 对源码做变异，至少 1 条 check 必须由绿转红 | 打不破的题是**恒绿题**，进套件只会稀释判别力 |

结果：**新增 6 个环境**（`doctest` / `editor` / `encoder` / `shlex` / `_writer` /
`chaos`），世界 **4 → 10**；54 次变异尝试只有 **15 个**通过 F2P ⇒ **39 个被诚实拒绝**
（拒绝是产出，不是失败 —— 留着它们就是往判据里掺水）。

⛔ 两个坑都是真踩过的：切片把 header 里的 `import fcntl` 一起留下 ⇒ 落在
`_AUDIT_DENY_MODULES` 里 ⇒ 沙箱 `PermissionError`，而探针单独跑是 8/8 全绿
（**两个证人不一致时，别急着判「不可用」，先找差异源**）；把**局部变量**当成外部
符号 ⇒ 11 个 stdlib 模块全被判「闭包不自包含」⇒ 加了 `_local_bound()` 才通。

### ② 顺手补了第 4 个变异维度，以及**它对称的修法**

`string`（交换字符串常量）是新的变异轴。⛔ 按第 43 条，**变异与修法必须成对出现**：
只加变异不加 `revert_strings` ⇒ 造出来的是「谁也修不好」的题 ⇒ Δ 恒 0 ⇒ 注水。
实测：`revert_strings` 3/3 修好，另外三个通用修法在同样的题上 **0.00**（正交）。

⛔⛔ `string` **默认关闭**：变异器的 `which` 是全局 DFS 序号，默认算子多一个 ⇒
编号整体漂移 ⇒ 历史基线不可比。显式 `--kinds …,string` 是**新实验口径**，
契约 A2 钉的就是这条边界（不许拿它和历史读数放一张表上比）。

### ③ 实验矩阵（同 seed 0、8 轮、group 3、max-tasks 4；只有最后一行改采样量）

| # | 口径 | envs | depth | kinds | commit | 距种子代数 | 停机 |
|---|---|---|---|---|---|---|---|
| 基线 | 第 103 条原状 | 4 | 1 | 默认 3 | 2 | 2 | `no_weakness_evidence` |
| **A** | **只扩世界** | **10** | 1 | 默认 3 | 2 | **2** | `no_weakness_evidence`（r4） |
| **B** | 只加 depth | 10 | **2** | 默认 3 | 2 | **2** | `no_weakness_evidence`（r4） |
| **C** | 只加全算子 | 10 | 1 | **6** | **1** | **1** | `saturated:stagnated`（dev 3/4、held 1） |
| **D** | depth + 全算子 | 10 | **2** | **6** | **1** | **1** | `saturated:stagnated`（dev **14/15**、held 1） |
| **E** | 上面全要 **+ 采样 12** | 10 | 1 | 6 | **3** | **★ 3** | `no_candidates`（r7） |

### ④ 假设被证伪：**光把世界变大，一代都没多**

A 组是最直接的那一刀：世界 4 → 10，代数**仍然只有 2**，round 4 就
`no_weakness_evidence` 停机。⇒ **「世界小 ⇒ 只有两代」这个因果不成立**，
或者说得更准：**世界小是必要条件，不是充分条件**。

C/D 组是反向证据，同样重要：只加算子（不加采样）反而**退到 1 代**，
D 组发展集 14/15 快满了、held-out 只过 1 ⇒ 判据自己报的是
「任务分布或合成器不够」，而不是「能力到顶」。⇒ 题变多变难，但**采不到**那些题，
等于没变。

### ⑤ 真配方：**世界 × 变异维度 × 采样量，三者同时到位**

只有 E 组走通。三次提交的 diff 是干净的证据 —— 三个**正交**维度靠第 41 条那条
**有序链**叠起来了：

```
r1  null                                     → fix_control_flow     reward 0     → 0.4286
r3  [fix_control_flow]                       → + fix_boolops        reward 0.4286 → 0.5714
r5  [fix_control_flow, fix_boolops]          → + fix_strings        reward 0.5714 → 0.7143
```

```
★ 代数分布（距种子）: {1: 1, 2: 1, 3: 1}
停机: evolution.stopped reason=no_candidates（能力侧与清理侧都没产出候选）@ round 7
      ⛔ 这是「没空间了」，不是失败（第 24 条的四态分得开）
```

⇒ **V6 历史上第一次出现第 3 代**。非回归闸 11 次裁决 `deltas` 全 0 ⇒ 三代叠加
没有打掉任何一条既有题 —— 是**叠上去**的，不是**换掉**的。

⛔ 但必须诚实标注：这条链只在 E 组口径下出现，**默认口径仍是 4 环境**。
所以 `run_config` 新增 `n_envs` / `envs` / `world` 三个字段，让每一次运行的
世界规模都**写在账本里** —— 否则「两代」这个读数会再次被错读成机器的问题。

### ⑥ 诚实未做 / 已知取舍

- **`expect` 是表征（characterization），不是规格**：oracle 来自被测函数**当前**的
  行为。若 stdlib 那个函数本身有错，我们把它固化成了「正确」。这是刻意的取舍 ——
  换规格就要有人写规格，那就回到手写环境的老路。
- **纯度是启发式**（IO 黑名单），不是证明。
- **6 个**来自 stdlib 全量扫描：绝大多数候选因跨模块引用 / 依赖 IO / 无法被 F2P
  打破而落选。**世界 10 仍然很小**，只是比 4 大。
- 第 102 条里那批「诚实未验证」的零次事件（`world_fact.promoted` /
  `attribution.*` / `gate.blocked` 等）**本轮仍未验证** —— 它们需要单元级注入
  进不去的路径，不因为世界变大就自动解决。

**契约落点**：新增 `tests/contract_worldgen_test.py` **A–F 组 14 条**
（A 默认算子不变号 / B 可修 / C 正交 / **D 反证** / E 切片纪律 / F 环境发现顺序稳定）。

---

## 105. ⛔⛔ **「世界大小」会撒谎：题源强度才是读数 —— 又两个假设被证伪**

第 104 条把世界 4 → 10。本轮（r49）去量**每个环境到底能贡献几条题**，
结果数字很难看，而且**连续推翻了我自己的两个假设**。

### ① 第一个被证伪的：我以为是「判据选得不好」

按算子逐个量下来（`scripts/worldgen.py` 的 F2P 阶段）：

| env | 变异成功 | **真能打红判据的变异体** | 候选判据 |
|---|---|---|---|
| doctest（637 行！） | 6/8 | **1** | 18 |
| editor | 8/8 | **0** | 12 |
| encoder | 8/8 | **0** | 12 |
| shlex | 7/8 | **1** | 12 |
| chaos（对照，人造） | 6/6 | **6** | 15 |

我断定是「判据没按杀伤力选过」，于是写了变异杀伤导向的选择器
（`--pick kill`：贪心选能杀死最多变异体的判据）。**方向错了。**

⛔ 打脸的证据很简单：把候选池从 6 条放宽到全部 18 条，有效变异体**还是 1**。
⇒ 多给判据没用，问题不在判据侧。

（这个选择器后来留下了 —— 它是对的，只是当时不是瓶颈。契约 H 组反证它有牙：
无矩阵时覆盖 0 个变异体，有矩阵时覆盖 3 个。）

### ② 真主杠杆：**变异采样量**（`which` 的上限）

变异体是按 `which=0,1,2…` 顺序取的，默认只取到 7。而这些变异点大多落在
**没有判据执行到的分支**上。把上限扩到 63：

| env | which 0..7 | **which 0..63** | 变异点总数 |
|---|---|---|---|
| doctest | 1 | **3** | 48 |
| encoder | 0 | **6** | 36 |
| shlex | 1 | **5** | 52 |
| editor | 0 | **0** ← 素材本身不合格 | 39 |

⇒ **有效变异体 0→6 / 1→5 / 1→3**。同时 `editor` 露馅了：39 个变异点、
0 个有杀伤力，它那「1/8 的产出」来自**变异后跑不完**（不是行为变了）。

于是加了 **题源强度闸**：`--min-lethal 2`，有效变异体不够的直接弃。
宁可世界小一点，也不要虚胖。

### ③ 落地结果：题量 3.6 倍，世界 10 → 14

用新口径（`--mutations 32 --pick kill --min-lethal 2`）重扫标准库：

| | 旧口径（6 env） | **新口径（10 env）** |
|---|---|---|
| 判据合计 | 44 | **107** |
| **有效变异体** | **15** / 54 次尝试 | **54** / 198 次尝试 |
| 单环境最弱 | 1 | **2**（全部过闸） |

淘汰 `doctest` / `editor`，新增 `core`(9) / `token`(4) / `textwrap`(4) /
`_inspect`(5) / `_parser`(4) / `utils`(2)。

### ④ 第二个被证伪的：**题源强度提上去，代数没变**

终极判据是「账本里第 3 代」。新世界（14 环境、题源强度 3.6×）重跑
r48 那个成功口径：

```
r1 fix_control_flow  0      → 0.4286
r3 + fix_boolops     0.4286 → 0.5714
r5 + fix_strings     0.5714 → 0.7143     ★ 3 代 —— 与旧世界**完全一样**
停机 no_candidates @ r7
```

**第 3 代重现了（不是偶然），但没有第 4 代。** ⇒ 题源强度**不是**当前瓶颈。

换 dev/held-out 配对（r47 判决书的 P2）更差：`--dev-env core --heldout chaos`
⇒ 只有 **2 代**，round 4 就 `no_weakness_evidence` 停机（core 的题被两个
修法就打满了）。⇒ 配对也不是瓶颈。

### ⑤ 那瓶颈在哪：提议空间的**正交维度数**

三个观察合起来指向同一处：

```
r5 之后 no_candidates（能力侧与清理侧都没产出候选）
⇒ 四个通用修法里装了三个，第四个（fix_literals）没赢过基线
⇒ 提议器没东西可提了
```

⛔ 顺手查了纪律 32 的**对称面**：有 6 个变异算子却只有 4 个修法，
`unaryop` 是孤儿 ⇒ 去验它 ⇒ **10 个环境里 unaryop 变异点 0 个，一条题都造不出**
⇒ 这条也死了（第三次证伪）。

⇒ **代数上限 = 提议器能产出的「有效且彼此正交」的候选数。**
真代码里的下一批候选维度（实测节点数）：下标/切片（core 9 / scheduler 13 /
utils 12 / eventflow 12）、调用（shlex 64 / scheduler 25）。
⛔ 按第 32 条，加维度必须**变异算子与对称修法成对**加，否则又是注水题源。

**契约落点**：`tests/contract_worldgen_test.py` 新增 **G/H 组 8 条**
（G1–G4 题源强度与主杠杆 / G5–G6 判据选择 / **H1–H2 反证**）。

---

### 106. 「题量」也会撒谎：打红原因三态分账 + 一个安静失效的算子（2026-10-17）

第 105 条把「世界大小」换成「题量」，第 106 条发现**题量自己也会撒谎**，顺带
挖出一个**安静失效了整整一轮**的算子。

#### ① `valid` 只数「测试红了」，不区分**为什么红**

给 runner 加了一个由**产出方**写死的字段 `why`（`mismatch` / `raise` /
`import`），下游归一成三态（⛔ 不许靠解析 evidence 文案去猜 —— 那是两套
逻辑，文案一改分类就静默错位，与 BFS/DFS 错位同一个病）：

| 态 | 含义 | 含金量 |
|---|---|---|
| `behavior` | 函数跑完了，但**返回值变了** | 真 bug |
| `crash` | 调用抛异常 / 源码导入就炸 | **崩了**，不是行为变化 |
| `unknown` | 装置自己出问题（超时 / rc≠0） | 单独记账，**不许并入** |

崩溃题任何「把源码改回接近基线」的修法都能顺手修好，甚至恒等变换都能在别的
维度上蒙对 ⇒ **账面多了题，reward 里看不见**。实测：

```
env           valid  behavior  crash  含金量
eventflow        19         9     10    47%   ← 默认 dev 环境之一
utils             2         0      2     0%
shlex             3         1      1     33%
合计             122        92     29    75%
```

#### ② 立竿见影的后果：**r48 那个「第 3 代」含崩溃题的贡献**

同口径（14 环境 / 全算子 / max-tasks 12 / seed 0）单变量对照：

| 口径 | 提交代数 | 能力链 | reward |
|---|---|---|---|
| 不过滤（含崩溃题） | **3 代** | control_flow → **boolops** → strings | 0.4286 → 0.5714 → 0.7143 |
| `--require-behavior` | **2 代** | control_flow → **literals** | 0.5 → **0.6667** |

⇒ 过滤后**退化成 2 代**，而且**能力链都换了**。崩溃题影响的不只是代数，
还有**系统学到的是什么**。⛔ 默认**不过滤**（`--require-behavior` 显式开
才是新口径），历史读数没被动过。

#### ③ 一个**安静失效**的算子：工具坏了被读成「世界里没有」

第 105 条判定「10 个环境里 unaryop 变异点 0 个 ⇒ 这条维度是死路」。**那是
误判。** 真因：

```
ast.UnaryOp._fields 在 Python 3.12 起从 ('op', 'value') 改成 ('op', 'operand')
⇒ 原代码 `return node.value` 在 3.13 上**必抛** AttributeError
⇒ 被 synthesize_tasks 的 note_error 记成「合成失败」
⇒ 账面看到的是「这个环境造不出 unaryop 题」
```

它不报错、不崩，只是让一个维度安静地消失，而所有下游读数看起来都像事实。
修好（兼容 `operand` / `value` 两个字段名）后，**单维度就能造出 14 条题**
（7 条真行为变化）。

> ⛔ **一个维度产出恒为 0 时，先怀疑工具，再怀疑世界。**
> 这类失效是最贵的一种：它不会红，只会让结论安静地错。

#### ④ 第五个正交维度 `revert_unaryops`（变异与修法成对）

⛔ 它**不能**沿用前四维修法的「按遍历序对齐常量」写法：`unaryop` 变异是
**删除节点**（`not x` → `x`）⇒ broken 树的 UnaryOp 少一个 ⇒ 按序号对齐会
**整体错位**（每删一个 not，后面全部前移）⇒ 修法把本来正确的节点改坏。
⇒ 改用**同位置递归**：baseline 在该位置是 UnaryOp 而 broken 不是 ⇒ 包回一层。

真实环境实测（14 条题）：**14/14 被它修好，其余四个修法 0/14**（连改动都没有）
⇒ 可逆 + 正交都成立。

#### ⑤ 但装上第五维，代数**仍是 3**

同口径单变量对照（唯一变量 = unaryop 修好 + `fix_unaryops` 入池）：

```
r49 口径（unaryop 坏）  ⇒ 3 代  control_flow → boolops → strings
r50 口径（第五维装上）  ⇒ 3 代  同一条链，fix_unaryops 没被选进去
```

账本里看得到它为什么没赢：`experience.propose` 第 5 次起
`skipped_sample=["add_skill:fix_literals", "add_skill:fix_unaryops"]`
⇒ **它被提名过，但没赢过闸**。根因取证：

```
实际 dev 采样（max_n=12）下，unaryop 题 = 0 条（每个环境都是 0）
```

`which` 是**全局 DFS 序号**，前 12 个位置被 compare/binop/constant/boolop/
string 占满 ⇒ unaryop 永远轮不到。（第 ③ 步那 14 条是 `--kinds unaryop`
**单独**跑出来的。）⇒ 与第 105 条那个「变异采样量」主杠杆是**同一个病**。

⇒ 下一步的方向因此是**按维度均衡采样**（而不是继续加维度）：
每个维度各采 n 条，而不是按全局序号取前 n 个。⛔ 这会改变 `which` 语义，
必须作为新口径显式开启。

**契约落点**：`tests/contract_task_quality_test.py`（新）**I–N 组 31 条** ——
I 组三态归一 / J 组分账闭合 / K 组 `require_behavior` / **L 组反证**（把分类
函数改成恒返回 behavior ⇒ 放行 6 条 vs 正常 1 条，闸有牙）/ M 组默认口径
不变 / **N 组钉住 unaryop 不再安静失效** + 第五维可逆与正交。

**验收**：见本轮提交说明。观测层改动（打红原因分账）已举证**业务行为零
影响**：8 个环境的 `task_id` 集合、`fail_to_pass`、各项计数**全部相同**。

---

### 107. 采样口径也会撒谎：按维度均衡采样（2026-10-17）

第 106 条结尾留下的缺口是：**第五维修好了、能单独造出 14 条题，但实际采样
里一条都进不来**。这一轮把采样修了，顺带**第三次踩到"量具自己撒谎"**，最后
**证伪了「题量涨 ⇒ 代数涨」这个假设**。

#### ① 根因：`which` 是全局 DFS 序号，不是「第 n 个某维度的点」

`_Mutator` 遍历时遇到的第 i 个可变异点（**不分种类**）就是 `which=i`。于是
AST 里数量最多的那种算子会把前 N 个序号**全部占满**，`max_n=12` 的采样窗口
里后加的维度永远轮不到。

按维度轮流取点（`balanced_which_order`，round-robin）后的实测（14 环境 /
全算子 / `max_n=12`，**尝试次数完全相同**）：

```
              题量   binop  compare  constant  string  boolop  unaryop
顺序采样        59       8       12        17      19       2        1
均衡采样        81      20       18        16       7       9       11
```

⇒ 题量 +37%，**最稀缺的两个维度翻了 5–11 倍**。dev 环境（textscore）从
「3 条全是 compare」变成「6 条 / 3 个维度」；顺序采样下 **0 题** 的 `utils`
也能出题了。

#### ② 量具自己也在撒谎（第 106 条第 ③ 点，第三次命中）

第一版探针用「逐个 `mutate_source(which=i)` 直到抛异常」来枚举点位。但那个
异常有**两种**完全不同的含义：

```
(a) which 超出点位总数            ⇒ 真结束了
(b) 点位存在但变不动              ⇒ 空串没有字符串变体 / `is`、`in`
                                     不在 _FLIP_OPS 里
```

一遇异常就 `break` ⇒ 把 (b) 读成 (a) ⇒ **unaryop 点位从 15 读成 2**、
boolop 从 17 读成 2 ⇒ 均衡采样的底数从一开始就是错的。

⇒ 修法：枚举走**被测对象的同一份遍历** —— `_Mutator(which=-1)` 探针模式
（永不命中 ⇒ 遍历完整棵树 ⇒ `site_kinds` 拿到全部点位）。契约 S1 组逐个
`which` 反查，证明它与真实变异**同源**。

#### ③ 落点（⛔ 默认关闭，且**不动冻结套件**）

| 位置 | 改动 |
|---|---|
| `aether/environment.py` | `mutation_sites()`（同源枚举）+ `balanced_which_order()`（轮流）+ `synthesize_tasks(balanced=)` |
| `SynthesisReport` | 新增 `sampling` / `sites_by_kind` / `chosen_by_kind`（题量读数必须带口径） |
| `aether/cli.py` | `evolve --balanced`、`synth --balanced`，`balanced` 进 `run_config` |
| `scripts/worldgen.py` | `--balanced`（影响 `--min-lethal` 的含义 ⇒ 新口径，不与旧表并排看） |

⛔ `balanced` **刻意不作用于冻结套件**：那是基线锚点，非回归闸的判据集不能
随采样实验漂移。本轮唯一变量是 dev / held-out 的取点顺序。

#### ④ 决定性实验：题量涨了，**代数反而降了**

四格交叉表（6 轮 / 14 环境 / 全算子 / `max-tasks 12` / seed 0；每组一个
独立 root，**初始状态完全相同** —— 含同一份 `memory.json`，否则记忆会
跨运行污染对照）：

| 口径 | dev 题 | easy/hard | 提交代数 | reward 轨迹 |
|---|---|---|---|---|
| depth1 / 顺序（现状） | 3 | 3 / 5 | **3 代** | 0 → 0.375 → 0.5 → 0.625 |
| depth1 / 均衡 | 6 | 3 / 3 | **2 代** | 0 → 0.5 → **0.8333** |
| depth2 / 顺序 | 11 | 18 / 5 | **3 代** | 0 → 0.78 → 0.83 → **0.87** |
| depth2 / 均衡（修前） | 11 | **0 / 26** | **0 代** | 恒 0（死锁） |
| depth2 / 均衡（修后） | 11+6 | 3 / 29 | 1 代 | 0 → 0.625 |

⇒ **「题量 ↑ ⇒ 代数 ↑」被证伪。** 一次提交能吃下更多题 ⇒ 剩余弱点更少 ⇒
更快撞到 `no_candidates`。代数由**剩余能力缺口的个数**决定，不由题量决定。

#### ⑤ 顺带撞到的下一个天花板：**难度层死锁**，以及它的出口

`depth2 + 均衡` 是四格里唯一真正产出**跨维度叠加题**的组合（26 条**全 hard**
—— 这正是 r48 起一直想要的"能检验叠加"的题）。但它立刻卡死：

```
dev 集 26 条全是 hard，held-out 有 easy 层
⇒ 泛化闸：「dev 没有该层 ⇒ 无从对照」⇒ inconclusive
⇒ 6 轮 0 提交，而 dev_gain 明明是 +0.69
```

⛔ 闸的行为是**对的**（fail-closed），但系统因此**没有任何出口**：
「明明有增益却永不提交」与「候选不好」长得一模一样。而当时的补采样
（`grow_for_difficulty`）只会加大 `max_n` —— 加大 n 补不出**这个口径根本
造不出**的层。

⇒ 出口：**换变异深度补层**（缺层且 `depth≥2` ⇒ 降级用单点题补 easy 层，
单点题 = easy）。修后 `n_added=6`、easy 层出现、`spread=True`，轮 1 即提交
`fix_boolops`（+0.625）。⛔ 只在「缺层 且 depth≥2」触发 ⇒ 默认口径零影响。

**契约落点**：`tests/contract_sampling_test.py`（新）**S1–S6 组 36 条** ——
S1 同源（逐 `which` 反查，另写枚举必错位）/ S2 轮流定义（长度不变、公平性、
确定性）/ **S3 默认路径零影响**（给 `mutation_sites` 装炸弹，默认调用不许
炸到）/ S4 真实环境有牙（**反证：把均衡退化成顺序 ⇒ 维度数回落**）/
S5 口径进报告与账本 / **S6 降级补层的出口不是空的**（实测 depth=1 能采到
easy 层）。

#### ⑥ 全量回归当场抓到的一条**真回归**（r50 引入，本轮修）

跑全量时监督器报 `REGRESSION_DETECTED`：

```
@5460 add_skill:fix_unaryops: frozen::_writer: 1→0
       (AttributeError: 'NoneType' object has no attribute '_fields')
```

r50 的 `revert_unaryops` 用"沿两棵树同一条路径递归对齐"，而 AST 的**列表
字段**里合法地含 `None`（实测 `ast.arguments.kw_defaults == [None]`）⇒
`gn._fields` 崩 ⇒ 修法抛异常 ⇒ 冻结任务从通过变不通过。

⛔ 这条值得一提的不是 bug 本身，而是**它是怎么被发现的**：全量回归 + 监督器
抓到的（闸在工作），不是人眼看到的。⇒ 修法（None 守卫，fail-closed）+
契约 N7/N8 钉住（触发条件真实存在 + 该环境每条题都不抛异常）。

⛔ 顺带修了契约 **E4 的"用错闸"**：它拿"绕过闸"这**一种** critical 的条数
去比统计里的**全部** critical ⇒ 只要出现别类型的 critical 就红 ⇒ 归错因。
改成逐条数所有 critical 行（第 9 条：用错闸和闸太严看起来一模一样）。

---

### 108. 「同层对照」的层没有同**样本量**：0/1 与 4/9 不可判别（2026-10-17）

承接第 107 条结尾留下的天花板：`depth2 + 均衡` 修完死锁之后，轮 2–5 **全部**
判 `partial`，理由一字不差：

```
总体 held-out +0.3000，但 ['hard+0.0000'] 未涨，
且发展集同层有增益（{'hard': 0.1379}）⇒ 挑食，不提交
```

#### ① 先证伪：是不是「dev 的 hard 与 held-out 的 hard 不是同一层」？

`hard` 的定义是「通用修法修不好」（`GENERIC_REPAIRS` 只含
`revert_control_flow`）⇒ `hard` 是个**大杂烩**，把五个互不相干的维度装进
同一个桶。于是假设：两个 hard 桶里装的**不是同一批维度** ⇒ "同层对照"
从根上就不可比。

新探针 `scripts/probe_level_alignment.py` 用**组合签名**（修好该题所需的
**最小**技能组合 —— 单修法签名会低估链条能力）实测：

```
dev hard（29 条）  20×boolops  4×boolops+control_flow  2×boolops+literals
                    1×boolops+strings  1×boolops+unaryops  1×unaryops
ho  hard（ 7 条）   6×boolops  1×unaryops
```

⇒ **假设被证伪**：两侧 hard 桶的维度是**对齐的**（ho 的 2 种签名 dev 全有）。
顺带也排除了"闸恒红"：31 个修法子集里 **28 个判 `generalize`**、3 个
`no_gain`，**没有一个是 `partial`**。

#### ② 真根因：分母不一样大

轮 1 提交 `fix_boolops` 之后，held-out 的 hard 层 **7 条里已被修好 6 条** ⇒
**只剩 1 条**还能涨；而发展集同层还剩 9 条。于是同一个候选：

```
dev hard  修好 4 / 9  ⇒ +0.1379
ho  hard  修好 0 / 1  ⇒ +0.0000
⇒ 判据："dev 涨了、ho 没涨 ⇒ 挑食"
```

**0/1 与 4/9 在统计上完全无法区分。** 分母是 1 的时候，"没涨"根本不是
证据 —— 那不是挑食，是**不可判别**（第 11 条：不可判别 ≠ 有罪）。

⇒ 修法：分层裁决新增**层内可提升空间**门槛（`min_level_headroom`，默认 3）。
某层基线未通过的题少于 3 ⇒ 该层**不进 `lagging`**（不作反证）。
⛔ 这不是放宽判据：有样本的层**一字未改**（契约 G2 守着"可提升空间 = 3
仍判 partial"）；⛔ 但放行的同时**必须点名**（契约 G5b），否则"样本不足就
跳过"会变成一条暗门。

#### ③ 决定性实验（同口径、同 seed、同初始状态，唯一变量 = 这个判据）

| 口径 | 提交代数 | reward 轨迹 | 轮 2 的裁决 |
|---|---|---|---|
| 修前 | 1 代 | 0 → 0.625 | `partial`（挑食，连判 4 轮） |
| 修后 | **2 代** | 0 → 0.625 → **0.8438** | `generalize`，并点名「hard 层只有 1 条可提升题 ⇒ 样本不足，未计入裁决（要补题，不是放宽判据）」 |

⇒ **代数 1 → 2**，reward 天花板 0.625 → 0.8438。连续 4 轮的假"挑食"消失。

#### ④ 下一个天花板（诚实）：held-out 太小

轮 4–6 判 `inconclusive`，理由是另一条新分支：

```
held-out **每层**的可提升空间都不足 3 条（['easy(可提升空间 0 条)']）
⇒ 这次裁决没有任何一层的证据可用 ⇒ 按拦截处理
```

轮 2 之后 held-out 基线已到 0.90（10 题里 9 题通过）⇒ **它已经没有判别力了**。
这是 fail-closed 的正确行为，出路是**加大 held-out**（`--max-tasks` 或换
环境配对），不是放宽判据。⛔ 与第 37 条同源：**规模会撒谎，要配"强度"
伴生读数** —— 这里"规模"是 held-out 题数，"强度"是每层还剩几条能涨。

#### ⑤ 顺带撞到：**候选也会挤压**（与第 43 条同构）

修 `gate_tries`（被拦 ⇒ 同轮按**发展集**排序试下一个，H1 一条没动）之后
实测：`--gate-tries 3` 与 `1` 的轮次**逐字相同**，因为队列恒为 1 ——

```
轮2 候选：control_flow 0.844(z=+1.67) | literals 0.688(z=-0.19) | strings 0.656(z=-0.56)
          ⇒ 只有 z>0 的那个过 advantage ⇒ 队列长度 1
```

⇒ 两把挤压叠在一起：`advantage` 的 z-score 把第二三名判成不合格，**加上**
提议器按固定顺序取「链上还没有的技能」（`fix_unaryops` 永远排第 5 ⇒ 6 轮里
一次都没被提过）。这与第 43 条那个「`which` 全局序号把后加维度挤掉」
**完全同构** —— 不是巧合，是"固定顺序 + 固定配额"这一类结构的通病。

⛔ `gate_tries` 的机制已落地且默认 1（与历史逐字同口径），但在当前口径下
是空转 —— 这一点如实记录，不宣称它带来了提升。

#### ⑥ 探针自己连栽 5 个跟头（同源纪律，值得单列）

同一个探针跑出全 0 时，原因**不是题太难**，是探针自己没跑起来：

1. `Harness(harness_id=...)` —— `harness_id` 是 **property**（内容寻址），不是构造参数；
2. 只给 `skills` ⇒ **L2 工具为空** ⇒ 修法不可达 ⇒ 全 0；
3. 不给 `routing` ⇒ **L1 报"没有可达路由"** ⇒ 全 0；
4. 定点技能写成 `patch::textscore`（生产是 `patch:<env>`，单冒号）⇒ 反证组根本没生效；
5. 漏了 `grow_for_difficulty` ⇒ 题数对不上（17 vs 32）。

⇒ 每一次都长得像"题太难 / 闸没牙"。**第 11 条（没跑起来 ≠ 没通过）在这里
命中了五次。**

**契约落点**：`tests/contract_generalization_test.py` 新增 **G 组 10 条 +
H 组 8 条** —— G1 样本不足不判挑食 / G2 反证（空间够 ⇒ 仍判挑食，证明不是
放宽）/ G3 门槛可回退 / G5 **r51 轮 2 的真实形态**（部分层不足 ⇒ 放行但点名）
/ H1–H8（默认=现状、同轮试下一个、提交的是真泛化那个、队列顺序来自发展集、
每个候选都记 `gen.probe`、反证）。

⛔ 改判据后**重审了对照组**（第 50 条）：默认门槛 3 让既有 E2
（`contract_generalization_test`）与 D1/H5（`contract_difficulty_test`）当场
转 `inconclusive` —— 那不是回归，是**对照组自己样本不足**（它们的 hard 层只有
2 条题）⇒ 把两处 fixture 的层都扩到 3 条（让它们继续考"挑食"），
「样本不足不判挑食」交给 G 组单独守。

**验收**：全量 **56 份契约 / 58 次执行，pass=58 fail=0**。

---

### 109. 闸给了正确的建议，但没人去执行（2026-10-18）

第 108 条修完「层内可提升空间不足 ⇒ 不作反证」之后，暴露出下一个天花板，
而且它长得很特别：**系统自己把出路写在了裁决理由里，却没有那条出路**。

#### ① 现象：三轮逐字重复

```
轮2/3/4（完全相同）提名 → fix_literals / fix_boolops / fix_strings
轮2/3/4（完全相同）裁决 → add_skill:fix_literals rew=1.0 adv=+1.73
                          inconclusive: held-out **每层**的可提升空间都不足
                          3 条（['easy(可提升空间 0 条)']）⇒ 按拦截处理。
                          出路：补 held-out 的题数，不是放宽判据
```

发展集上摆着 **+0.4 的增益**（0.6 → 1.0），三轮里系统做的事一模一样：
提名同三个候选、跑同一套评估、拿到同一个裁决。唯一的变化是账本多了三条
相同的事件。⛔ 这不是"闸在工作"，是**闸说对了但没人去做**。

#### ② 为什么补采样必须放在**循环内**，不能建卷时做一次

`headroom` 的定义是「**基线**还没修好几条」⇒ 它随能力提升而**缩小**
（实测：轮 1 装上 `fix_control_flow` 之后，held-out 的 easy 层空间立刻
从 N 变成 0）。建卷时用种子基线量 ⇒ 基线最弱 ⇒ headroom 最大 ⇒ 永远达标
⇒ 这个读数毫无意义。

⇒ **尺子要跟着被测对象长**。这与第 42 条「基线随 best 升级才有判别力」
是对称的两件事：那边长的是**基线**，这边长的是**卷子**。

#### ③ 落点

| 位置 | 改动 |
|---|---|
| `aether/generalization.py` | `ProbeResult.headroom` / `.thin` 升为**结构化字段**（此前只活在用 `reason` 文案里）；新增 `headroom_of()` / `ensure_headroom()` |
| `aether/evolution.py` | `EvolutionConfig.ho_grow`（**默认 False**）；第 1.5 步体检（在基线评估**之后**、提议**之前**）⇒ 记账 `heldout.grow` |
| `aether/cli.py` | `evolve --ho-grow` / `--ho-grow-max-n` / `--ho-grow-max-tasks`；`ho_grow` 进 `run_config` |
| `scripts/probe_candidate_squeeze.py` | 候选挤压 + 判别力体检，`--compare` 跑决定性对照 |
| `tests/contract_ho_growth_test.py` | **K 组 25 条**（默认关 / 结构化 / 同源 / 不优待 / 三种失败可见 / 有牙性） |

⛔ 补题的筛选判据是「**当前基线**做不对」⇒ 它是**基线**的属性，不是候选的
属性 ⇒ 不看候选 ⇒ **不给任何候选优待**（与 dev 侧 `grow_for_difficulty`
同形态：那边补的是"层"，这边补的是"空间"）。H1 一条没动：补出来的题不进
发展集、不进排序、不进 `group_relative`。

⛔ 两个函数（`grow_fn` / `difficulty_fn`）**由 CLI 注入**而不是闸自己 import：
合成口径（depth / kinds / balanced）是**实验变量**，只有命令行知道这次跑的是
什么口径。闸自己 import 一份 ⇒ 补出来的题与本次实验**不同源** ⇒ "补采样"
变成悄悄换了一把尺子。

#### ④ 决定性实验（同 seed / 同初始状态，唯一变量 = `--ho-grow`）

| 口径 | 提交代数 | reward 轨迹 |
|---|---|---|
| 关（现状） | 1 代 | 0 → 0.6 → 0.6 → 0.6（后三轮逐字重复） |
| **开** | **2 代** | 0 → 0.6 → **1.0** |

体检读数（开了之后第一次能看见"卷子还剩多少判别力"）：

```
轮1 headroom {'easy':3,'hard':2} → 补 4 条 → {'easy':3,'hard':6}
轮2 headroom {'easy':0,'hard':6}（easy 已穷尽，如实记「这是卷子的上限，
     不是候选的错」）⇒ hard 有 6 条空间 ⇒ generalize ⇒ 提交
```

#### ⑤ 顺带量到的另一件事：**候选挤压**（与第 43 条同构）

同一个探针还量出：提议器按 `dict` 固定顺序取「链上还没有的技能」的前 3 个
⇒ 6 轮里 `fix_unaryops` 与 `patch:textscore` **一次都没被提名**。这与第 43 条
那个「`which` 全局序号把后加维度挤掉」完全同构 —— 不是巧合，是
"**固定顺序 + 固定配额**"这一类结构的通病。

⛔ 这一条**本轮没修**：因为在当前口径下它并不造成损失（没被提名的两个技能
在 dev 上 gain=0）。它先如实记录，等提名真的成为瓶颈再动 —— 第 30 条说
"卡住时先怀疑采样维度"，而这里采样维度（提名）不是当前瓶颈，**判别力**才是。

> ⚠️ **r54 更正（2026-10-18）**：上面这段的**结论是错的**，它是口径产物。
> 该读数来自 3 维小口径（`compare,binop,constant`），其中根本没有 unaryop 题
> ⇒ `fix_unaryops` 无从涨起。换到全 5 维 + balanced 口径实测：**「从未被提名
> = 无」，6 个技能全部被提过名**（`fix_unaryops` 1 次、`patch:textscore` 2 次），
> 提交 3 代、reward 0 → 0.381 → 0.5714 → 0.6667。
> ⛔ 教训（第 24 条第五次命中）：「gain=0」与「**从没被测过**」在账本里长得
> 一模一样。判"它不是瓶颈"之前，先证明"它在有它题的口径下被测过"。

**验收**：全量 **57 份契约 / 59 次执行，pass=59 fail=0**。

⛔ 契约 K 组第一版自己抓到自己两条假绿灯（第 8 条「恒真断言是绿的」）：
① K6 原本写成 `ck(..., True)` ⇒ 改成进程内最小场景，**同一道闸 + 同一个
候选**，只变"卷子补没补" ⇒ 裁决必须从 `inconclusive` 翻成 `generalize`；
② K5.9 的反证 fixture 用了猜的 id（`new::8::k`，实际产出是 `new::12::k`）
⇒ 断言测的是"补进去了"而不是"被拒了"。而且补采样重试会**换一批 id**（n 翻倍）
⇒ 要测"被拒"必须用每次返回同一批 id 的 `grow_fn`。

---

### 110. 拦截必须可复核 —— overfit 不许在零信息下判决（2026-10-18）

第 109 条把"卷子随基线长"补上之后，全口径（14 环境 / depth2 / balanced /
ho-grow / 全 5 维 / 6 轮）跑到 3 代、reward 0 → 0.381 → 0.5714 → 0.6667。
轮 5 出现一次 `overfit` 拦截，它的裁决读数是：

```
轮5  overfit  ho={'easy': 0.0, 'hard': 0.0}
              dev={}                     ← 空
              headroom={}                ← 空（体检实测是 {'easy': 6, 'hard': 0}）
```

#### ① 判据在临界点**不连续**

同一个候选，如果 held-out 总体增益是 **+0.01** 而不是 **0.00**，就会走
`partial` 分支，那份裁决里 `ho` / `dev` / `headroom` / `thin` **四个字段全在**。

⇒ 差 0.01 的浮点，决定了这个候选是享受完整同层对照，还是被零信息判决。

#### ② 真根因：不是判据错，是**观测被丢弃**

`evolution.py` 明明把 `dev_by_difficulty` 一路传进了闸
（`probe(dev_by_difficulty=cand.dev_by_difficulty)`），而 `overfit` 分支构造
`ProbeResult` 时只带了 `by_difficulty`，**`headroom` / `thin` /
`dev_by_difficulty` 一个都没填** —— 因为它在任何分层逻辑**之前**就早退了。

#### ③ ⛔⛔ 补齐观测后，第一件事是**证伪自己的假设**

我原本准备改判据（把「ho 该层已饱和」的情况从 `overfit` 降级为
`inconclusive`）。字段补齐后重跑，轮 5 的真实读数是：

```
dev={'hard': 0.2222, 'easy': 1.0}    headroom={'easy': 6, 'hard': 0}
```

⇒ 发展集 easy 层**涨满 1.0**，held-out easy 层**有 6 条可提升空间却纹丝不动**
⇒ 确凿的挑食证据 ⇒ **闸判对了**（`patch:textscore` 是定点技能，只修
textscore 环境）。⇒ **判据一个字都不改**（改了就是把真挑食放过，第 33/34 条）。

这次交付的价值因此不是"闸更严或更松"，而是：**从这个版本起，"闸拦得对不对"
第一次可以被复核**。在此之前，这个问题的答案是"没有人能回答"。

#### ④ 落点

- `ProbeResult.saturated`（新字段）：**完全饱和层**（该层基线已全部修好 ⇒
  读数必然是 0）。⛔ 与 `thin` 是两件事，不许合并：thin = 还剩几条但太少
  （出路：补题），saturated = 一条都涨不了（出路：换更难的题/换环境）。
- 分层诊断（`headroom` / `lagging_all` / `thin` / `all_thin` / `saturated`）
  **上移到所有分支之前**，成为公共事实 ⇒ 两个分支不再各写一份（第 13 条）。
- `_downgrade_if_flat` 重建裁决时**继承**这三个读数（不继承 ⇒ 一次降级把读数
  全抹成空，与 `discriminative` 曾踩的坑同形）。

#### ⑤ 第 22 条取证：观测层改动对业务行为零影响

同 seed / 同初始状态，改动前后**逐字相同**：

```
队列长度 [0,1,0,0,0,0]  /  提交 3 代  /  reward [0, .381, .381, .5714, .6667, .6667]
```

#### ⑥ 契约：`tests/contract_overfit_provenance_test.py`（L 组 24 条）

L1 完整分层读数 / L2 saturated≠thin（门槛降到 0 ⇒ thin 变空而 saturated 不变）
/ L3 分层诊断在分支之前（`headroom` 只算一次）/ L4 **判据一字未改**（真挑食仍
被拦）/ L5 反证 / L6 与 `partial` 分支**对拍**（同源 ⇒ headroom 逐字相同）。

⛔ L 组第一版自己抓到自己一条假绿灯：L5 原本是「自己造一个不带字段的
`ProbeResult`，再断言它字段为空」—— 同义反复，**没有牙**。改成**跑生产代码**
再把读数抹掉 ⇒ 三条判据同时转红。改完发现最有价值的一条是 **L5.4：事故版的
verdict/reason 与正常版逐字相同** ⇒ 这解释了它为什么能藏这么久 —— 丢的只是
"怎么解释这次拦截"，拦截本身照旧，所以既有契约一条都不会红。

#### ⑦ 第二部分：被拒候选必须能**重审**（上游 skip 短路了下游的出口）

顺着"轮 6 只剩 1 个候选"追下去，触及一个**独立于观测**的根因。给探针
`_in_cooldown` 包一层记录后，冷却实录是：

```
13 次判定，streak **全部为 0** —— 冷却一次都没触发过
轮3–6  fix_control_flow 连 `_in_cooldown` 都没进（在更上游就被过滤了）
```

⇒ `deadend_probation_every=5`（"不永久封死"的出口）**一次都没被走到**
（第 4 条：明星机制 ≠ 这条路被走到）。

真凶是 `MemoryAwareProposer`：它按"试过并被闸拒的变异**不再重复提**"工作，
而 `partial` / `overfit` 都在它的永久名单里。**它没有出口**——下游的
probation 出口够不着上游，因为候选在到达冷却之前就消失了。

三条实测证据说明它必须能重审：

1. `partial` / `overfit` 的结论依赖**当时的 held-out 卷子**，而 r53 起卷子
   会随基线长（`--ho-grow`）⇒ 旧判定会失效，可这条 skip **没有失效机制**
   （与第 35c 条同构：随被测对象变化的量，不许只量一次）。
2. 损失可量化：轮 2 的 `fix_control_flow` rew=**0.7143**（基线 0.381，+0.33）
   被判 `partial` ⇒ 从第 3 轮起永久消失，4 轮里再没被评估过。
3. `deadend_probation_every` 的存在说明**设计者自己承认**"被拦 N 次后情况
   可能变化"—— 只是一套有出口、一套没有，而没有出口的那套**先触发**。

**落点**：`MemoryAwareProposer(retry_every=N)`（与 `deadend_probation_every`
同形）。每被跳过 N 轮放行一次让闸重审；**不清除记忆条目**（记的是"曾被拒"
这个事实，它没变，变的是"这张卷子还作不作数"）。`--retry-every` **默认 0**
= 现状，与历史逐字同口径；进 `run_config`。契约 G 组 10 条（含反证 G7）。

#### ⑧ 决定性实验（同 seed / 同初始状态，唯一变量 = `--retry-every`）

| | 提交代数 | reward 轨迹 |
|---|---|---|
| 关（现状） | 3 代 | 0 → .381 → .381 → .5714 → **.6667** → .6667 |
| **开（=2）** | 3 代 | 0 → .381 → .381 → .5714 → **.9048** → .9048 |

代数相同、终值差 **+0.238**。机制不是"多给一次机会碰运气"，看轮 4 就清楚：

```
轮2  fix_control_flow  rew=0.7143  裁决 partial（hard 层未涨、dev 同层有增益）
轮3  age=1 ⇒ 跳过
轮4  age=2 ⇒ probation 放行 ⇒ rew=0.9048，裁决 **generalize**
     （headroom 已从 {'easy':6,'hard':6} 变成 {'easy':6,'hard':1}
       ⇒ hard 层只剩 1 条可提升 ⇒ 统计上不可判别 ⇒ 不作反证）
```

⇒ **同一个候选在新基线下的正确裁决完全不同**，而永久 skip 让系统永远
看不到它。r52 的 `min_level_headroom` 与 r54 的 probation 在这里协同：
那边让"样本不足不作反证"，这边让"旧判定能被重审"。

⛔ 更正确的做法是「**卷子指纹变了就重审**」（因果维度），但需要跨层把卷子
身份传到提议器 —— 本轮先给时间维度的出口，缺口如实记录。

**验收**：全量 **58 份契约 / 60 次执行，pass=60 fail=0**。

⛔ **诚实未做 / 更正**：
- ⛔ **更正**（本轮取证推翻了上一版的推测）："轮 6 只剩 1 个候选是因为冷却"
  **是错的** —— G 表证明冷却一次都没触发。真因是**提议器层的永久 skip**。
- ① 即便开了 `--retry-every`，池子本身仍只有 6 个技能、3 个已上链 ⇒
  **世界太小**（第 39 条：必要条件与充分条件分开标定 —— 给机会是必要的，
  不等于机会一定兑现）。
- ② `gate_tries` 仍空转（准入队列恒 ≤1）。
- ③ 第 102 条零次事件：`world_fact.promoted` 实测 6 轮 **0 次**（本口径下
  没有任何"被拦"达到 `deadend_streak=3` 的晋升门槛）仍未验证。
- ④ 契约写作期间自己抓到一条**想错的理由**：起初把 `n_skipped` 改成
  `len(skipped) - before`，理由是"probation 放行会漏算"—— **那个理由是错的**
  （被放行的候选也在 `out` 里 ⇒ `len(caps) - len(out)` 恒等，既有契约 B6
  正是断言此式）。已撤回，不为一句想错的理由引入第二种算法。

---

### 111 · r55 · 补题撞的是**题源硬顶**，不是筛子太严 ⇒ 换采样口径

r53 给了「补 held-out 的题数」这个出口，r54 跑起来却发现它**补了 0 条**，
而且连续两轮逐字重复。在动手放宽任何判据之前，先把三个互斥假设分开标定
（第 39 条：必要条件与充分条件不许混着判）。新探针
`scripts/probe_headroom_source.py` 五段取证：

| 段 | 读数 | 判定 |
|---|---|---|
| A | `max_n` = 16/32/64/128/256 ⇒ 产出 13/26/**26/26/26** | H1 **成立**：单环境题源硬顶 **26 条** |
| E | `depth` = 2/3/4 ⇒ 产出恒定 **26** | 加深合成**出不来**新题 |
| C | 种子链 easy **11/11** 有空间 → 轮 5 链 easy **0/11** | H2 **证伪**：是基线变强，不是题变少 |
| D | easy 11 条：单修法修好 11、链修好 11、**不一致 0 条** | H3 **成立**：结构性饱和 |
| G | 均衡 26 条 vs 顺序 26 条 ⇒ **重叠 0** | 换采样口径 ⇒ 另一批题 |

**F 段拆开了「补了 0 条」这个黑箱** —— 它其实有两个完全不同的原因，被同一
句 note 盖住：

```
建卷 13 条   headroom={'easy': 0, 'hard': 6}   missing=['easy']
steps = [max_n=26 n_added=0, max_n=52 n_added=0, max_n=64 n_added=0]
题源 26 条 ⇒ 建卷外 13 条：easy 5（全做对）+ hard 8（其中 3 条做不对）
```

① easy 层**结构性**补不进（见下）；② 那 3 条本可以补进 hard 层空间的题，
因为当时 hard 不在 `deficit` 里，被 `lv not in deficit` 拒掉了。三档 `max_n`
全白跑，而输出只说「补到上限仍缺」—— **"题源没了" 与 "有题但那层不缺" 在
输出里完全同形**（第 10 条同形病）。

**⛔ H3 是一个设计级事实**：`GENERIC_REPAIRS` 实测**只有一个成员**
`revert_control_flow`。而 `easy` 的定义正是「这个修法能修好」⇒ 基线一装上
`fix_control_flow`（轮 1 就装了），easy 层的 headroom 就**结构性归零，且永远
补不回来**（D 段：单修法与链判定 0 条不一致，说明链没有引入任何新破坏）。
而 `all_thin` 仍把它算进分母 ⇒ 这就是 r54 轮 5/6 那两条 inconclusive 逐字
重复的根源。

**落点**：

· `GeneralizationGate.grow_variant_fns`（默认 `()` ⇒ 与 r53/r54 **逐字同
  口径**）；`ensure_headroom` 遍历口径序列，每个口径**重新起步**
  （`n = max(len(tasks), 4)`，不继承上一口径的进度）
· `rep["n_variants"]` / `rep["outside_deficit"]`：用了几个口径、有多少题是
  "有但那层不缺"
· note 分叉：「换采样口径」vs「单口径题源已到顶」⇒ `grew=0` 从此可归因
· CLI `--ho-grow-variants`（默认 **1**）；`_ho_synth_alt` = `balanced` 取反

⛔ 刻意**不换环境**：held-out 的"另一个环境"是这套闸的定义（H1），换环境等于
换尺子的**单位**；换取点顺序不换单位，与候选依旧无关（第 29 条同结构）。

**决定性实验**（同 seed / 同初始状态，唯一变量 = `--ho-grow-variants`）：

```
            提交代数  reward 轨迹                          held-out（轮5/6）
variants=1     3 代   0 → .381 → .381 → .5714 → .9048      hard 空间 1 条
variants=2     3 代   0 → .381 → .381 → .5714 → .9048      hard 空间 **2** 条
```

⛔⛔ **这是一个负结果，如实记录**：换口径**有牙**（轮 4 实测补进 1 条，
hard 空间 1 → 2），但**不足以改变任何一次裁决** —— 因为 `min_level_headroom=3`
要的是 ≥3，而换口径在这个世界上只能再挤出 1 条（与 G 段预测一致）。
两组的 reward 轨迹、提交代数、被吞候选、队列长度**逐字相同**。

所以本轮**真正的交付不是"多进化一代"，是把一句无法行动的文案换成带正确
出路的读数，并把这个天花板的高度量出来**。改之前轮 4 的体检写的是
「补到上限仍缺」（听起来该去调 `--max-tasks`），改之后写的是：

```
仍缺 hard：题源里还有 44 条建卷外的题，但它们不在缺空间的层里
⇒ 出路是**换采样口径**，加大 max_n 无效 —— 单口径题源已到顶
```

⛔ **副产品（意外但更硬的一条取证）**：对照组（`--ho-grow-variants 1`，
即 r55 改动后的默认路径）跑出的 reward 轨迹与 r54 的实验组**逐字相同**
（`0 → .381 → .381 → .5714 → .9048 → .9048`、队列 `[0,1,0,0,1,1]`、
被吞候选一致）⇒ 同时证明了两件事：观测层改动**对业务行为零影响**
（第 21/22 条），以及这套实验**跨进程可复现**（对照组可以跨运行复用）。

**契约**：新 `tests/contract_headroom_source_test.py`，M 组 22 条。
⛔ 反证是**真跑生产代码**的：M2.7（第二口径的题不在 deficit 层 ⇒ 不收）、
M4.1（第二口径的题基线做得对 ⇒ 不收）、M4.2（与发展集同 id ⇒ 不收）—— 换
口径**没有**绕过 `lv in deficit` / `reward < 1.0` / H2 中的任何一条。M6 用
真实环境钉死前提（均衡 13 条 / 顺序 14 条，重叠 **0**，并集 27）。

**验收**：全量 **59 份契约 / 61 次执行，pass=61 fail=0**。

⛔ **诚实未做**（其中第一条已在 r56 **被证伪**，见下）：
- `all_thin` 的分母**仍然包含结构性饱和层**（easy 永远 0）。本轮只把"结构性"
  这件事**测出来并写进文档**，没有改判据 —— 改它等于动 r52 那条判据的语义，
  需要单独一轮 + 单独的对照实验，不能夹在这次改动里。
- 换口径在 eventflow 上拿到的**有效空间有限**（G 段：新建卷 26 条里只有 1 条
  是"轮 5 链做不对"的）。它解决的是"补题静默失败"，不是"判别力不够"。
- 题源 26 条是**单环境**的硬顶；要再往上只能扩世界（第 39 条）。

---

### 112. r56 · 分母是对的，是我的量具错了 —— 两条怀疑都被证伪

r55 留了一条"待单独一轮"的怀疑：`all_thin` 的分母含结构性饱和层 ⇒ 疑似假
红灯。读源码时又发现第二处：`all_thin` 用 `by_diff`（全部层），而同一个函数
里 `thin` 用 `lagging_all`（只未涨层）—— 两处同形不同源。两条看起来都像 bug。

**两条都被证伪，判据一个字没改。** 新探针
`scripts/probe_allthin_denominator.py`（走真实 `cmd_evolve`，hook 闸本身，
三种分母只用闸自己的输出字段和常量重算 ⇒ 同源）：

```
     V0 现行（分母=全部层）    V1（只未涨层）   V2（全层剔除饱和）
轮1  0                        0                0
轮2  0                        0                0
轮3  0                        0                0
轮4  0                        1  ← 唯一差异     0
轮5  1                        1                1
轮6  1                        1                1        V0≡V2：6/6
```

**B 类（饱和层造成假红灯）—— 数学上就不可达。`V0 ≡ NOT any(headroom ≥ 3)`，
饱和层 `headroom=0` 永远不可能让 `any(...)` 成真 ⇒ 它既不能造成假放行，也不
能单独造成假拦截。实测 V0≡V2 恒等 6/6。**

**A 类（涨层进分母）—— 方向与我猜的相反。轮 4 是决定性反例：**

```
by_diff={'easy': 1.0, 'hard': 0.0}   headroom={'easy': 6, 'hard': 2}
V0=False ⇒ 放行    V1=True ⇒ 会拦截
```

easy 涨满 1.0 且空间 6（≥3）= **强正证**。改成"只算未涨层"会把"有强正证、
无有效反证"判成"没有任何一层的证据可用" ⇒ **假拦截**。现行是对的。

⛔⛔ **本轮最贵的一步：我的第一版量具把 V2 的分母写成 `lagging_all −
saturated`。** 轮 5/6 的未涨层全是饱和层 ⇒ 分母变空 ⇒ `bool([])`=False
⇒ 读出「剔除饱和后 all_thin 翻 False ⇒ 裁决被改变 2 次」。若信了它去改判据，
方向是**放宽** —— 正好放过"涨层空间 2 条、涨了 1 条"这种不可判别的证据。
正确分母是 `by_diff − saturated`（含涨层）。**第 24 条第六次命中：读数反常，
先怀疑量具，再怀疑世界。** 修正后"裁决被改变"从 2 次变成 **0 次**。

**本轮唯一的代码改动是文案，不是判据**（第 35b 条：闸写在 reason 里的"出路"，
系统必须真有那条路）。实测轮 5 的 `easy` 是饱和层，而 r55 F 段已证明对它
补题一条都补不进 ⇒ 旧文案对它写"补 held-out 的题数"是**装饰性出路**。现在
按层分叉：饱和层明说"补题无效，需换更难的题或换环境（当前系统无此出路）"；
只有非饱和的 thin 层才说补题。⛔ 顺带修掉一处失真：旧文案在所有未涨层都饱和
时说"所有层都结构性饱和"，而涨了的层**不是**饱和（它涨了），只是空间小 ——
说错会让下游去找错的原因。

**第 21/22 条取证**（文案改动对业务行为零影响）：改动前后同参数两次运行
**逐字相同** —— `V0/V1/V2` 序列 `000011 / 000111 / 000011`、baseline 轨迹
`0→.381→.381→.5714→.9048→.9048`、提交哈希四轮一致。（同时是第三次跨进程
可复现取证 ⇒ 对照组可跨运行复用。）

**契约**：新 `tests/contract_allthin_denominator_test.py`，N 组 24 条。它是
**负结果契约** —— 交付的是"不许改哪里、为什么"，不是"改了什么"：
- N2.4 真反证（跑生产代码）：门槛抬到 7 ⇒ 涨层也不达标 ⇒ 必须转拦截 ⇒ 证明
  分母含涨层**有牙**（不是死代码）；N2.1 与 N2.4 只差门槛、结论相反 ⇒ N2.1
  不是恒真断言。
- N3.4/N3.5/N3.6 钉住 V0≡V2，并断言"空分母不许被读成有判别力" —— 这条守的
  就是我自己的量具错误，防止它复发。
- N4 组守文案分叉；N4.3b 专门守"不许说所有层都结构性饱和"。

**验收**：全量 **60 份契约 / 62 次执行，pass=62 fail=0**。

⛔ **诚实未做**：
- 轮 5/6 的 `inconclusive` **是真实的天花板**，不是 bug：easy 饱和（零信息）、
  hard 空间 2 条且只涨 1 条（不可判别）⇒ 这套卷子对这道题确实没有判别力。
  要破它只能换更难的题或换环境 —— **而系统当前没有这条路**，这才是下一个真
  天花板（比"分母"这个伪问题硬得多）。
- 题源 26 条单环境硬顶、世界只有 4 个环境（第 39 条）。
- `gate_tries` 空转；`world_fact.promoted` 仍 0 次。

---

### 113. **"图里可达" ≠ "跑到了"：架构要用运行时取证，不能用依赖图**

起因：要回答「整个流程合不合理、架构整合得合不合理」。第一反应是画依赖图，
但依赖图只能回答**静态**问题（第 7 条：「有机制」≠「这条路被走到」）。
所以本轮把架构体检做成**可复跑的读数**：`scripts/arch_scan.py`
（AST 依赖图 + 入口可达性 BFS + 一次真实 evolve 的行级覆盖 + 账本零次事件差分），
自带反证组（`--self-test`：造孤岛模块 + 造零次事件，抓不到就退出码非 0）。

**静态结果是好的**（这部分别改）：无循环依赖、生产包不反向依赖 tests/scripts、
aether 40 个模块**静态不可达 0 个**；`paths` 作为布局唯一事实源零依赖。

**真正的问题全在运行时**，四条：

1. **系统当前自检是红的**：`doctor` 退出码 1，`judge.boundary` fail ——
   生产账本里有 **14 种判据身份**，分布在 905 条 `eval.verdict` 上。
   ⇒ 任何跨轮 reward 对比在严格意义上**不可采信**；而 doctor 给的出路
   「用同一把尺子重跑一组」**系统里没有这个工具** ⇒ 那条出路是装饰（第 35b 条）。
2. **五步里唯独 PROPOSE 没有账**。MINE/VALIDATE/SELECT/COMMIT 都有事件，
   提名这一步没有 —— 而 r52（挤压）与 r54（永久 skip）**两轮根因都在这一步**。
   最不可观测的地方最容易藏故障，而它恰好一次都没被观测。
3. **三条承诺过的路径从未走过**，逐条做了「有没有牙」的取证：
   `gate.blocked` **有牙**（构造 L0 胶囊 ⇒ `touches_protected=True`）但结构性不触发
   ⇒ 该加负结果契约钉住而不是改判据；`evolution.checkpoint` **默认关**
   ⇒ 第 60 条「中间态可恢复」在默认口径下不成立；`memory.pruned` 未取证、不下结论。
4. **死路冷却与陈述性知识：触发条件与提名策略互斥**。这条我一开始判错了两次，
   都被取证纠正：① 两处 key 拼法**同源**（不是我以为的不同源）；
   ② `world_facts` **有牙**（streak=3 晋升、1/2 不晋升）。
   真因是两层：历史 `env_pair` 从 `unknown` 变成具体值 ⇒ 旧 key 永久失配（无迁移机制）；
   且 `_in_cooldown` 只对**本轮被提名的**候选调用，被拦后不再提名 ⇒ streak 永远到不了 3。
   ❼ 决定性实验是**负结果**：`--retry-every 2` 对照两组都是 0、轨迹逐字相同
   ⇒ 它**不是**这条路的出口（第 56 条：负结果照样是交付，但要写清交付的是什么）。

**顺带记录两个规模读数**：自证机器（scripts 8880 + tests 20065 = 28945 行）
已经比被证系统（aether 22114 行）**大 1.31×**；两块巨石
`evolution.py` 2222 + `cli.py` 2086 行占 aether 的 **19.5%**，且 cli 扇出 28 个模块
⇒ 它是事实上的唯一总装点。

**交付**：`scripts/arch_scan.py`（可复跑 + 反证组）、`docs/ARCH_review_2026-10-18.md`
（依赖清单 + 正反论证 + P0/P1/P2 排序 + 负结果 + 诚实未做）。
⛔ 本轮**未改任何生产代码**。

---

### 114. **修完复查：r57 的三条结论里两条是量具错了（第 24 条第七、八次命中）**

起因：r58 把 r57 列的 P0/P1 全修了，然后**从头复查一遍**。复查先复查的是**量具**：

⛔ **r57 判「PROPOSE 一个事件都没有」是错的** —— 账本里 `experience.propose`
有 **21 条**。审计只扫字面量，扫不到 `self.EVENT_PROPOSE` 这种**常量别名**，
更扫不到 `_emit()` 的**包装函数中转**（第 24 条第七次命中）。
⇒ 量具从 1 层升级到 **3 层**（字面量 / 常量别名 / 函数中转），补反证 [3][4]。

⛔ **r57 判「死路冷却与提名策略互斥 ⇒ 结构性不可达」也是错的** ——
把实验从 4 轮加到 **6 轮**（门槛是 3，4 轮根本攒不够 streak）：
`deadend.cooldown=2` / `world_fact.promoted=2` / 最长 streak=4（第 24 条第八次命中）。
⇒ 机制**有牙且真被走到**；生产账本 0 次是**历史成因**（`env_pair` 取值域漂移
⇒ 旧 key 永久失配），不是结构缺陷。

**真修的东西**（逐项都有决定性实验 + 契约）：
① 尺子分区 `aether/caliber.py` + `aether caliber --seal`：给 doctor 一条**真有的**
出路（此前那条出路是装饰，第 35b 条）；封存**只追加不删**，边界取「最后一个旧
尺子段的末尾」（第一版取到账本末尾 ⇒ 把当前尺子段一起封了 ⇒ 可比归零，实测后修）；
封存被 `status` 真消费。`doctor` 现为 `healthy: true`。
② 提名名单进账：`experience.propose` 增 `proposed` / `accepted` ⇒ 被永久跳过的
候选第一次在账本里留下名字。
③ `evolution.checkpoint` **默认开**（配 `--no-checkpoint`），零影响取证：
开/关对照 reward 轨迹逐字相同。
④ 收敛 4 处第二份定义（`selfref.ROOT` 取 paths 默认值且**保留可注入性**、
删死常量 `caps_candidate.CANDIDATES_NAME`、`attribution.LAYERS` → `ABLATION_LAYERS`
（与 `genome.LAYERS` 同名不同物）、`check/doctor.FAIL` 点名**不合并**（五态 vs 三态）。
⑤ `gate.blocked` 与冷却都加**负结果契约**钉住「不许改」。

**明确不做的**：`cmd_evolve`（807 行）**不拆** —— 取证发现 `serve` 根本不 import
`cmd_evolve`（r57 担心的「第二份编排」不存在），而拆它**没有判据收益**、回归面最大
⇒ 改成 arch_scan **F 段**每次体检报「最大模块 / ≥150 行函数 / 入口层直接依赖判据层」，
配反证 [5]。这是用装置代替一次性重构。

**复查结论**：静态架构仍无环、无反向依赖、0 不可达；剩下的不合理是 5 条**已知读数**
（巨石 / 入口当编排层 / 自证机器 1.31× / 天花板 / 封存后可比只剩 12 条），没有新病。

交付：`docs/ARCH_review_2026-10-18.md`（含「改完之后哪里还不合理」与诚实未做）。

---

## 106. ⛔⛔ **「2 代天花板」是判据自锁，不是能力到顶**（r59 项 2）

r58 把「4 环境 / 26 题硬顶 / easy 层饱和 / 2 代」归成 P2 研究方向，暗示
「换世界才破」。r59 做了四组对照（**同 seed / 同 rounds / 同 max-tasks，
只差一个变量**），结果把那条结论**证伪**：

| 组 | 变量 | commit | revert | 冻结套件 | held-out | 单代 advantage |
|---|---|---|---|---|---|---|
| A | 基线 | 1 | **5** | 8 题 | 0.6 | 1.347 |
| B | `--envs auto`（14 环境） | 1 | **5** | **26 题** | 0.6 | 1.347 |
| C | `--depth 2` | 1 | **5** | 8 题 | **0.8** | **1.677** |
| D | 两者都开 | 1 | **5** | 26 题 | 0.8 | 1.677 |
| **E** | **`--ho-grow`** | **2** | **0** | 8 题 | — | 1.347 → **1.732** |

**B 组与 A 组逐字同形** —— 换世界把冻结套件从 8 题撑到 26 题（判别力
4 → 14），但对「能进化几代」**零影响**。r58 那个「26 题硬顶」的出处就是
这个冻结套件数，它和天花板**不是一回事**（第 39 条：必要 ≠ 充分 —— 换
世界增强的是**非回归闸**，卡点在**泛化闸**）。

**C/D 组有用但只抬单代**：题数 5→10、held-out 0.6→0.8、advantage
1.347→1.677。**第 2 代依然进不去。**

**E 组是决定性的一组**：只补 held-out 卷子（5→9 题，hard 层可提升空间
2→**6**）⇒ 同一个候选 `fix_literals` 从「连拦 5 次」变成「第 2 轮就提交」
（advantage **1.7321**），revert 5→**0**。

⇒ **「2 代天花板」是 `all_thin` 判据自锁的假象**，不是能力到顶。

### 根因 1（不改代码）：`all_thin` 把「已饱和的层」当成否决票

A 组 r2–r6 每次都是：

```
headroom   = {'easy': 0, 'hard': 2}     ← hard 层只剩 2 条空间（门槛是 3）
by_diff    = {'easy': 0, 'hard': 1.0}   ← hard 层其实**涨满了**
thin=['easy']  saturated=['easy']        ← easy 同时被判「样本不足」和「已饱和」
discriminative = true                    ← 系统自己算得出它有判别力
⇒ all_thin（每层空间都 < 3）⇒ inconclusive ⇒ 拦
```

⛔ 破法**早就存在**（r53 的 `--ho-grow`），系统自己在 r52 就知道这个卡点
（`generalization.py` 的注释写着「明知有 +0.4 的增益，也只能三轮逐字重复地
拦着」）。它**默认关**是**正确的**（换考卷 ⇒ 与历史基线不可比，第 33/34 条）
⇒ 不许改默认，改成**让它可见**：新增运行级事件 `evolution.no_evidence`
（拦截条数 / 被吃掉的 held-out 增益 / 最小 headroom / 真出路 `--ho-grow`）。
第 35b 条：reason 里写的出路必须真有 ⇒ 实测开了它 commit 1→2。

### 根因 2（已修）：`inconclusive`（不可判别）被当成「被拦」写进了知识层

`_deadend_accum` 原逻辑是「不是 `generalize` 就 +1」⇒ `inconclusive`
连 4 次 ⇒ streak=4 ⇒ 判成**死路** ⇒ 冷却 ⇒ **晋升成陈述性知识**写进基因组
（跨代继承、换运行也不掉）。

而 E 组证明那个候选**根本不是死路**（advantage 1.7321，第 2 轮就提交）。

⇒ 第 11 条（不可判别 ≠ 有罪）的实证命中：**把「尺子太短」写成了
「世界的事实」**。修法：

```python
NO_EVIDENCE_VERDICTS = frozenset({"inconclusive"})
# 不可判别 ⇒ 不计入、也不归零（它不是成功的证据，也不该给自己累加）
```

判据**没放松**：`overfit` / `partial`（有证据的讨好与挑食）一个字没改。
**零回归面**：生产账本 116 条 `gen.probe` 里 `inconclusive` **0 条**
⇒ 改这条口径在生产上**零决策翻转**（交叉表先做，再改）。

代价（如实记）：不再冷却 ⇒ 每轮会重新探测那些「看不清」的候选 ⇒ A 组
从拦 4 次变 5 次、多花一次探测预算。**宁可多探，不可误判死路。**

---

## 107. 「没有数据要迁移」也是一种结论（r59 项 3 的取证）

r58 说「`env_pair` 取值域漂移（`unknown` → 具体值）⇒ 旧 key 累积的 15 次
被拦永久失配，要加迁移机制」。r59 取证发现**那条前提不成立**：

- 生产账本 `env_pair` 89 条**全是具体值**，从来没有 `unknown`；
- 生产 **0 条** `deadend.cooldown` / `world_fact.promoted`；
- 18 个 harness 快照的 payload 恰好**只缺 `world_facts` 这一层**。

⇒ 根本不存在需要迁移的数据。那条「历史」来自 `world_facts.py` 的**文档
注释**，被当成了生产现状（第 24 条第十次命中）。真问题是：**陈述性知识层
在生产上零产出** —— 而 r59 项 2 给出了原因：生产 15 次运行里 **14 次是
`rounds=1` 的短跑**，死路门槛是连续 3 次，1 轮根本攒不到。

r59 项 2 的对照实验（6 轮）里 `deadend.cooldown` / `world_fact.promoted`
都真的产出了 ⇒ **机制有牙，是实验条件不够**（第 64 条同形）。

⇒ 项 3 **不做迁移机制**（没有可迁移的数据），改成把「key 形态多套」这个
真风险用契约钉住。

### 第 108 条（r59 项 4）：改了语义就要量代价，别只写「会慢一点」

把 `inconclusive` 摘出死路账 ⇒ 候选不再进冷却 ⇒ **每轮重探**。当时只留下
一句「多花探测预算」。补做 **ABBA 交替对照**（修前/修后各 2 次，同 seed，
唯一变量 = `evolution.py` 版本）：

| 读数 | 修前 | 修后 | 差 |
|---|---|---|---|
| 耗时 | 79.2s（89.6 / 68.9） | 81.7s（73.9 / 89.4） | **+3.0%**（组内方差 ±19% ⇒ 无意义） |
| `gen.probe` | 5 | 6 | +20%（零方差） |
| `eval.verdict` | 115 | 120 | +4.3% |
| commit / revert | 1 / 5 | 1 / 5 | **不变**（非回归） |
| cooldown / fact | 1 / 1 | **0 / 0** | 修对了 |

⇒ 未达预设判据（耗时 +20%）⇒ **不封顶**。但代价**无界**（正比于
「看不清的候选 × 轮数」）⇒ 把 `repeated_probes` / `repeated_capsules`
写进 `evolution.no_evidence`，并要求文案明说**当前没有封顶机制**
（契约 V2.4 —— 不许承诺一个不存在的出路，第 35b 条）。

⛔ 顺带一条方法：**单跑一次的耗时不能当对照** —— 这次两组组内极差都是
±20s，比组间差（2.5s）大一个量级。必须交替重复（ABBA）。

### 第 109 条（r59 项 5）：`gain=0` 之前先证明它「被测过」（第 51 条实证）

`--gate-tries 3` 与基线**逐字同形**，差点判它「没接线」。真相：

- 它**有牙**：r1 的 `gen.probe.queue_size=2 > 默认 1` ⇒ 开关确实生效；
- r2 起 `queue_size=1` ⇒ 每轮只有 1 个候选通过**准入判据**，无用武之地；
- ⛔ `gate.result` 每轮 **3 条**（3 个候选都过了准入闸）⇒ **过了 gate
  ≠ 进了泛化闸队列**，这是两套判据，别拿 `gate.result` 条数当队列长度。

而 `tests/` 里对它**零覆盖** ⇒ 它随时可能静默腐烂而没人报警。补 W 组契约
钉住：默认 1、`tries` 从 cfg 读（不许写死成常数）、`queue_size`/`try_index`
必须进账本。**不改判据**去给它更多候选（那等于为了让结论变绿调默认值）。

组合实测：`--ho-grow` 与 `--gate-tries 3` **不叠加**（第 29 条：加维度 ≠
能叠加）—— H 组与 G 组逐字同形，只多花 105s。

---

### 115. ⛔⛔ **「无收益」之前先证明它「被测过」：先量用武之地**（2026-10-18）

`--ho-grow` × `--retry-every` 组合实测：J 与 K **逐字同形**（commit 2/2、
revert 0/0、probe 2/2），看着就是「开关没用」。但这一形和「开关没接线」、
「记忆没装」在外部**完全无法区分**（第 51 / 72 条第二次命中）。

⇒ 定三个读数，**缺一不许下结论**：

| # | 读数 | 排除掉的误判 |
|---|---|---|
| ① | `experience.propose.retry_every` = 2 | 不是「没接线」 |
| ② | `recall` = `ok` | 不是「没装记忆」（`no_memory`/`degraded`） |
| ③ | `n_known_history` > 0 | 不是「零原料」 |

实测 ①=2、②=`ok`、③=**0** ⇒ 真结论是「**零原料**」：`remember()` 只在
reason 含 `gate_reject/overfit/partial/blocked/apply_failed` 时写失败史，
而 6 轮的裁决只有 `generalize` 与 `inconclusive`（后者**本来就不该**记，
第 11 条）⇒ 从来没有旧判定需要重审。

⇒ **开关留着（它有牙），结论写成「无用武之地」，不许据此删除**。补 X 组
契约：三读数进账本 + 各自配**反证组**（注入失败史后必须从 0 变 > 0；
`memory=None` 必须报 `no_memory` 不许冒充 `ok`）。

⛔ 单跑一次的**耗时**不能当对照：J 94s vs K 75s 看着慢 25%，而项 4 实测
组内极差 ±20s ≫ 组间差 ⇒ 不做判据。

---

### 116. ⛔⛔ **知识层「零产出」是假象，真病是「产出了一堆空壳」**（2026-10-18）

上一轮结论「陈述性知识层生产零产出，成因是短跑」**只对了一半**：短跑只
解释「写得少」，解释不了「写得空」。生产实测：

- `knowledge.ingested` **28 条**（不是 0），但只有 **2 个** `note_id`，
  **24/28** 条 `below_min_content=true`；
- `weakness.mined` 173 条里 **111 条（64%）`evidence` 为空**，
  `kind` **100% 是保守默认 `missing_skill`**；
- 库里 learner 通道只有 3 条：两条有内容的 `access_count` 是
  **1814 / 1602**，那条 19 字空壳的是 **1** ⇒ 空壳写了从未被用过
  （第 48 条：写入端成功 ≠ 可用）。

**根因是失败原因两处不同源**：`evaluate_dev` 写
`v.error or "checks_failed:"+失败名`（有 fallback），而 MINE 唯一入口
`_runs_from_verdicts` 写 `v.error or ""`（**没有** fallback）⇒ 挖掘侧
`error` 恒空 ⇒ 证据永不 append。取证：30 条 payload 的 `error`
**100% 为空**，但 15 条失败裁决的 `checks` 里**全都有失败项** ⇒ 可补 15/15。

修法 = 抽 `_verdict_error()` 单一实现，两处同源（第 13 条）。对照（同 seed，
唯一变量 = 代码版本）：弱点无证据 **6/6 → 0/6**、知识长度 **19 → 60/44 字**，
而 **commit/revert 1/5、裁决序列、总事件 231 全部不变**。

⛔ 诚实残留：修后库里两条真实长度 **63 / 44** 字（账本 `preview` 只截断显示
60 字，别拿它当全长），生产 `min_content_len=60` ⇒ **1/2 跨过、1/2 仍
`below_min`**（修前是 0/2）。这次修的是「**有没有证据**」，不是「**够不够
长**」；垫长度只能降闸（改判据，第 34 条）或塞信息（改语义，交给人定）
⇒ **都不做**，如实记录。（该残留在第 117 条被**顺带**解决 —— 不是靠垫
长度，而是修对了分桶口径。）

---

### 117. ⛔⛔ **读漏键名是一类病：同一个模式本轮命中两次**（2026-10-18）

`_runs_from_verdicts` 要 `evidence["topic"]` / `["env"]`，而判据层填的是
**`env_id`**。取证 30 条 evidence：`env_id` **30/30**（全为 `textscore`），
`topic` / `env` / `task_id` **0/30**。

⇒ topic 恒空 ⇒ 退化 `task_id`（也没有）⇒ 再退化序号 `t0`/`t3` ⇒ 弱点按
**任务序号**分桶（每桶 `n_failed=1`）而非按**环境**分桶。生产 173 个弱点里
111 个的 topic 就是 `t0`(77)/`t3`(33)/`t7`(1)。

⛔ 与第 116 条是**同一种病**（第 24 条第十二次）：要 `error` 而判据层填
`checks`；要 `env` 而判据层填 `env_id`。⇒ **凡从 dict 取值，先打印键名
再断言**（第 24 条的可操作形态）。

修法 = **往后追加不替换**（`topic or env or env_id`），原优先级一字未动
⇒ 已有数据不失配。对照（同 seed，唯一变量 = 本修复）：

| 读数 | 修前 | 修后 |
|---|---|---|
| topic | `{'t0':1,'t3':5}` | **`{'textscore':6}`** |
| `n_failed` | `{1:6}` | **`{2:5, 5:1}`** |
| 知识真实长度 | 63 / 44 | **106 / 79** |
| 跨过 60 字闸 | 1/2 | **2/2** ✅ |
| commit/revert | 1/5 | **1/5（不变）** |
| 裁决序列、总事件 | — | **逐字相同 / 231 不变** |

⛔ **形态自证**（第 45 条两个证人互相印证）：修后条目与生产里那两条
`access_count`=**1814/1602** 的化石**同形**（`环境名：…; …`）。那两条正是
topic 还没退化时留下的，而且是库里**唯一被真正用过**的知识 ⇒ 修复方向被
生产数据反向确认，不是我拍的。

⛔ 顺带解决 ≠ 目标：本项**没动** `min_content_len`（那仍是改判据）。按环境
分桶 ⇒ `n_failed` 变大 ⇒ `evidence[:2]` 有两条可拼 ⇒ 长度自然够。修的是
「分桶口径错了」，长度只是它的下游症状。

### 118. ⛔⛔ **「写入成功」之后还有第三道闸：检索侧长度闸**（2026-10-18）

知识/记忆的可用性有**三道互不抵消**的闸：
① **写入侧**（`learner`，短内容照收，只标 `below_min_content`）
② **加载侧**（save→load 往返，见 `contract_learner_test` G 组）
③ **检索侧**（`memory.py:430`：`len(content) < min_content_len ⇒ continue`）

⇒ 一条内容可以「写入 `written` + 往返还在」却**永远检索不到**。r59 实测：
19 字空壳 2 条，下一轮检索 24 次，`Δaccess = 0`；内容修成 79/106 字后，
同样 24 次检索，`ΣΔaccess = 102`（扣掉写入侧 dedup 后真消费 ≥ 96）。

⛔ **`access_count` 有两个增长点，读数上完全同形**：写入侧 dedup（304 行，
**假消费**）与检索侧命中（500 行，**真消费**）。判「真被消费」必须
**扣掉写入次数**，否则「写了 N 遍」会被读成「被读了 N 遍」（第 75 条）。

⛔ **追踪条目要按 `note_id`，不能按 `channel`**：`promote` 会用
`replace(n, tier="L2", channel="curated")` ⇒ note_id 不变而 channel 变
⇒ 只按 `channel=="learner"` 过滤会把**晋级**的条目读成「消失了」。

### 119. ⛔ **前提不成立就别建机制，改用读数钉住**（2026-10-18）

怀疑「被准入闸拦下的候选不在 `eligible` 里 ⇒ 永远拿不到试下一个的机会」，
看起来该收敛两套判据（代价大）。实测否决：**`gate.result` 18 条全部通过，
拦截率 0/18** ⇒ 这个洞在当前世界从未发生。

⇒ 不收敛（收敛 = 改判据，第 34 条）。真缺口是**观测**：`queue_size =
min(tries, len(eligible))`，默认 `tries=1` ⇒ 恒为 1 ⇒ `eligible` 到底有几个
**读不出来**。补 `n_eligible` 纯观测字段（不参与任何判据）即可。

⛔ 放宽到 `gate_tries=5` 后只有第 1 轮 `queue_size=2`、其余仍是 1，且
`try_index` 恒为 0（第 1 个就过闸，队列第 2 个从没被探测）⇒ **有牙但
无用武之地**，与项 6 的 `--retry-every` 同形：开关留着不许删。

### 120. ⛔ **修完要在生产读数里验收一次，别只信 /tmp 对照**（2026-10-19）

受控对照只能证明「改动有效」，证明不了「生产真的走到了这条路径」。
r59 收口时只读生产账本与生产库，得到三条独立印证：

- `knowledge.ingested` 时间线：`t0`(19 字, below) → `t0`(60) →
  **textscore**(60) ⇒ 与 /tmp 对照逐条吻合，且是**周期任务自己跑出来的**。
- `L3:9551c8fa`（63 字，项 7 产出）access=**26**、`L3:b1e31f93`（106 字，
  项 8 产出）access=**4 扣掉 1 次 dedup ⇒ 真消费 3** ⇒ 修完的产出
  **在生产里被真消费了**。
- 19 字空壳 `L3:93af6d24` 连写 3 次**每次都是 `written`**（从不是
  `deduped`）且**不在库里** ⇒ 加载侧把它拒了 ⇒ 下次又当新的写。
  这正好把三道闸（写入/加载/检索）串成一条完整闭环。

⛔ **归因「生产为什么是短跑」时先看是不是自己的锅**：生产 58 次运行里
46 次是 `rounds=1 / max_tasks=5`，而 CLI 默认是 `rounds=3 / max-tasks=12`
⇒ **短跑是调用方显式传的，不是系统默认值**（第 19 条：归因前先排除
fixture/自身调用）。⇒ 不动外部调度；且项 9 已证明消费可**跨运行**发生，
生产数据也证明消费确实发生了 ⇒ 前提不成立（第 69 条）。

### 121. ⛔ **默认参数可能是闸的旁路：验「闸有牙」要连默认一起验**（2026-10-19）

`memory.py:430` 的长度闸在主循环里 `continue`，但 `search()` 默认
`expand_links=True`，链接扩散会把被拦掉的短条目**加回来**：

| `min_content_len` | `expand_links` | 19 字是否被召回 |
|---|---|---|
| 60 | **True（默认）** | **是** |
| 60 | False | 否 ⇒ 闸有牙 |
| 0 | False | 是（且能抢到主循环名额） |

⇒ 这是**设计**不是 bug：`memory.py:186` 的意图是防「极短条目**霸占
top-k**」，而旁路带进来的只落在链接段、不占主循环名额 ⇒ 意图未违背
（第 58 条：看着像 bug 的正确设计要用负结果契约钉住）。

⛔ **方法论**：写「闸有牙」的反证组时，若没显式关掉默认开启的旁路，
断言可能**恒真**（设 0 和设 60 都能召回 ⇒ 什么都没证明）。契约要补
「默认参数下是什么行为」这一格，并把「不霸占」表达成
**位置 ≥ 主循环命中数**，而不是「排第 1」（后者取决于具体文本，会翻车
—— 我自己先写错了一次）。

⛔ 账本只能看 `top_note` 一个 ⇒ 判断「新知识有没有被消费」只能靠
`access_count` 间接推。补 `hit_channels`（召回列表按 channel **聚合计数**）
：channel 是条目自身属性、不经二次判断 ⇒ 不会像「手工定义哪些算知识」
那样把口径做坏（项 9 我自己做坏过两次）。

---

### 122. ⛔⛔ **状态名叫 "applied" 不等于"已经删了"：记账成功 ≠ 事实发生**（2026-10-19）

清理链的状态机停在 `APPLIED`（人已批准）就没有下一步了。而
`apply_patch()` 的文档明写「**不写任何文件**」—— 它的真实语义是
「人已批准，可以去改了」。名字骗人 ⇒ 账本上根本分辨不出：

| 读数 | 值 |
|---|---|
| `sandbox_detail` 状态 | `applied`（2026-10-15，`approved_by=linshaojie`） |
| `aether/interop.py` 里 `self.sandbox_detail =` | 10-19 实测**还有 4 处** |

⇒ 批准了、但补丁没被执行，而账本看不出来。三个后果同时发生：
① 账本会说谎；② 提案永远结不了案（30 个独立提案被提了 **509 次**）；
③ `load_after_expected` 算过「应该少 1」，从没有一次拿执行后的真相去对拍它。

⛔ **方法论**：状态机要有**终态**，且终态必须由**消费侧证据**授予
（第 75 条）。「人批准」是写入端成功，「目标真的不在原位了」才是消费侧。
修法是补 `EXECUTED` 终态 + `verify_executed()` 的**位置级复验**
（三态：通过 / 没通过 / 不可判别，不可判别按 fail-closed 保持 APPLIED）。

⛔ **我原以为该改的是"把对拍预算默认打开"，那条已被 r42 自己证伪**
（909b374 打开 → 7154e3d 实测回归退化 50→46 pass 后改回）。查历史提交
比重新做实验便宜 —— 本轮因此**没动默认值、没动判据**。

⛔ 两个判据都是**实测逼着改的**，不是设计时想到的：

1. 按「全文里还有没有出现过」判 ⇒ `sandbox_detail` 有 4 处赋值，
   只删其中一处就**永远结不了案** ⇒ 改成按**次数**（4→3 就判得出来）。
2. 只按 `prop_id` 去重 ⇒ 行号漂移（`uci_skill_id` 501→518）被当成新提案
   ⇒ 另加**语义键**（删哪个文件里的哪个东西，**不含行号**）。
   ⛔ 不要反过来改 `identity()`：那会让历史账本的 `prop_id` 全部失效。

---

### 123. ⛔⛔ **状态机有终态，不等于有"到达终态"的路**（2026-10-19）

r60 A 修的是「终态被冒充」（卡在 `applied` 不动）。r60 B 撞上的是**同族
但相反**的形态：**终态是空的，因为走到它的路不存在**。

能力候选链上，`installed` 这个态一直存在、`verify()` 也一直写着，但
实测（假 r2 放进 PATH，四步）：

| 步骤 | 读数 |
|---|---|
| 批准 radare2 | `approved` ✅ |
| 装好后再 propose | 打印「✅ **没有待办候选**」（**看着像问题解决了**） |
| 候选档案 | **0 条**（radare2 直接消失） |
| 账本 `installed` | **0 次** |

根因：`from_probe` 对「探到了且已包装」返回 `None` —— 那是**对的**
（A3 契约钉着：没有待办就不该提候选）。副作用是**工具一装好，它就连
记录一起从流水线上消失**。propose 打印「没有待办候选」这句，把这件事
伪装成了成功。

⇒ 判「某条链通不通」，别只看**状态有哪些**，要看**每个状态有没有
被真实到达过**：账本里 `installed = 0` 才是判据，`verify()` 存在不是。

⛔ 同族的两条教训要合起来看：

- A：状态**停在** approved 不动，冒充已经完成；
- B：状态**消失**了，连"待办"都不显示，看起来像根本不存在问题。

⇒ 两条都要靠**消费侧读数**验收，不是靠读代码。

⛔ 反过来还有一条更会骗人的：账本 170 条 `cap.candidate` **不是 170 个
候选**，是同一个 radare2 被记了 170 遍（独立 tool 数 = **1**）。把
「事件数」当成「对象数」读，会凭空多出 55 个"已批准候选"。
⛔ 其中 53 条署名 `tester` 是**契约测试直接跑在真仓库上**写进去的 ——
契约 F 组已改用临时数据根：测试不该是生产账本的并发写入者（第 90 条）。

### 124. ⛔⛔ **外部读数是给机器的，不许直接透传给人**（2026-10-20）

`radare2` 实际是 LGPL，而 GitHub 因为它有多个 LICENSE 文件报
`NOASSERTION`。照原样打印 ⇒ 读者看到的是「这工具许可有问题」，
而真相只是「SPDX 没认出来」。

⛔ 空串与 `NOASSERTION` 也是两件事：前者**真的没声明**，后者**声明了
但没被认出来**。合成一句会把一个有许可的项目说成没有许可。

⇒ 外部 API 的每个字段进显示层之前都要问：这个值**人读得懂吗**，
读不懂会被理解成什么。

### 125. ⛔ **判「默认零网络」要把默认目标也指向回环**（2026-10-20）

只数回环服务器的请求数**不够**：代码只要绕过 `api_base` 直接用
`API_BASE` 常量，请求就打到真外网，而回环数不到 ⇒ 断言假绿。
实测：`annotate` 被去掉 `net` 检查后，跨进程那条（D1）是绿的，
只有进程内那条（C1）红了。

⇒ 把**默认目标**也临时改成回环地址，任何意外请求都会被数到。
测的必须是「联网」这件事，不是「有没有打回环」。

### 126. ⛔ **收外来字符串按字符白名单，别数分隔符的个数**（2026-10-20）

`slug.count("/") != 1` 挡不住 `../b` —— 它正好一个斜杠 ⇒ 拼出
`…/repos/../b` = `…/b`（路径穿越）。

⛔ 改成字符白名单**也还没修掉**：`.` 是仓库名的合法字符，
`[A-Za-z0-9._-]+/[A-Za-z0-9._-]+` 照样放行 `../b`（owner 段被
匹配成 `..`）。⇒ 最终按**对方的命名规则**收：GitHub 用户名不含点，
于是 owner 段要求字母数字开头且不含点，`..` / `.hidden` 直接被拒。

⇒ 「看起来对」的白名单要拿**攻击串**去喂（契约 E 组 8 条），
不能只看它放不放行合法输入。

### 127. ⛔⛔ **「过闸」只许重算闸的结论，不许顺手重设状态**（2026-10-20）

两条链的 `validate()` 都写了同一行：

```python
state = VALIDATED if gate_passed else REJECTED   # 无条件覆写
```

⇒ 人批准过的候选，**重跑一次过闸就降级**：

```
人批准            approved
再跑 validate     approved → validated     ← 降级
再跑 propose      validated → pending      ← 冲回
```

生产账本上有完整的时间线（seq 6955 → 7021 → 7022，23:56 批的，
00:52 没了）。⛔ 它比「卡在 approved 不动」更隐蔽：那是**停住**，
看得见；这是**往后退**，而「退回 pending」在界面上与「从没提过」
**同形** —— 批准消失了，没有人会去问。

⇒ 修法是把不变式抽成一份（`aether/statem.py`）：

```
未裁决（秩 < 已裁决线）⇒ 机器可自由覆写；
已裁决（秩 ≥ 已裁决线）⇒ 只能前进，回退一律挡住。
```

⛔ 三条配套纪律，缺一条这个修法就变成另一种坑：

- **读数必须照常刷新**。保住 `state` 却把 `gate_passed` 一起保住，
  「人批了、闸后来变红」就再也看不见 ⇒ 两件事分开记账。
- **`rejected` 与 `pending` 同秩**。它不是"比 pending 更差"，而是
  "这一轮没过，重跑可以再判"；排在 pending 下面的话
  `validated → rejected` 会被当成回退挡住 ⇒ 闸失去牙。
- **复验不套这条保护**。`verify` / `verify_executed` 的本职就是
  回答「现在还成立吗」，而"装好的工具后来被卸了"必须能被看见。

⇒ 加这道保护后，**必须同时**补「闸还有牙」的反证组（H7–H9），
否则它跟"把状态机焊死"看起来一模一样。

### 128. ⛔ **同一个 bug 会同时在两条链上出现，修的时候要一起看**（2026-10-20）

`caps_candidate`（装工具）与 `cleanup`（删代码）的状态机是**同构**的：

```
pending/rejected → validated/proposed → approved/applied → installed/executed
```

⇒ 一处「无条件覆写 state」在另一处**必然**也有。只修看到的那一条，
下次就会以"另一条链上的新 bug"的形式再出一次。

⛔ 所以：**规则抽一份共用，序表各自持有**。规则抄两遍的话，
下次只改一处就会出现「一条链守住了、另一条没有」—— 而那正是
r60 A/B/D 三例共同的形态（都是"有机制，但那条路没被守住"）。

### 129. ⛔⛔ **一个 `None` 不许表示两件事**（2026-10-20）

`_mk()` 的三种返回：

```
(None, None)  —— 工具名不在清单里   ⇒ 真不认识
(None, pr)    —— 认识，但"没有待办" ⇒ 已装且已探到，候选从流水线上消失
(c,    pr)    —— 正常
```

调用方一律写 `if c is None: 报「不认识工具」`，把两义压成一义。
后果：工具一装好，`validate` / `approve` / `hint` **全部退出 2**，而报错是：

```
不认识工具 'radare2'；已知：['ghidra', 'radare2', …]
```

—— 同一句话里自证它认识。⛔ 这比报错更贵：读者会去查工具名写错没有，
而真正的问题是"装好之后没有退路"。

⇒ 判据要钉在**能区分两义的那个字段**上（这里是非空的 `pr` = 探针结果），
不是"看起来都是空"的那个。
⇒ 而且「没有待办」是**正常结论**不是错误：rc=0，并说清下一步该做什么
（⛔ 别说「先跑 propose」—— 已装且已探到的工具，propose 永远不会再提它，
那是个**死循环式建议**）。

⛔ 顺带：补上一处退路之后，**原来那处退路会变成死代码**（`_mk` 兜了
档案之后，`verify` 分支里那句 `c = saved.get(...)` 就永远走不到）。
同一条退路写两遍的害处不是重复，是"下次只改一处"（第 128 条同族）。

### 130. ⛔ **注入锚点必须唯一命中，否则「0 红」与「闸没牙」同形**（2026-10-20）

写事故版时，锚点 `if pr is None:` + 下一行在 `verify` 与 `validate`
两个分支里**一模一样**，`replace(..., 1)` 打到了另一个分支 ⇒ 读数是 0 红。

⛔ 0 红有两种解释：**闸没牙**，或者**注入根本没生效**。两者的读数
完全相同 —— 这和「`gain=0` 与『从没被测过』同形」是同一族病。

⇒ 注入器必须先 `count(anchor)`，**不是 1 就拒绝注入**，
不给出会读错的读数。锚点带上**上下文注释**即可唯一。

---

### 131. ⛔⛔ **契约的根必须从 `__file__` 推导，不许写死绝对路径**（2026-10-20）

13 份契约写着 `ROOT = Path("/Users/…/V6")`。在生产根上跑毫无问题，
但 `srcmut.probe` 的对拍方式是**在副本根上跑同一批契约**：写死路径 ⇒ 契约
永远 import 生产那份 ⇒ 删前删后跑的是**同一份代码** ⇒ 对拍恒绿。

⛔ 实测代价：在副本里删掉 `ghprobe._SLUG_RE` 的定义（一个必然 NameError 的
破坏），`contract_ghprobe_test.py` 仍然 **rc=0 全绿**。也就是说那条
「源码清理的非回归闸」对 13 个模块**一直是空闸** —— 它看起来在跑，实际在
拿生产跟自己比。

⇒ 契约里只许 `ROOT = Path(__file__).resolve().parents[1]`。这是**等价改动**
（生产根下解析出同一个路径），但副本场景下才**可能是错的**。

⛔ 一条推论：任何"在别的目录上跑一遍"的机制，都要先问
**「被跑的东西会不会自己找回家」**。

---

### 132. ⛔ **「机器自审」= 换裁决者，不是删闸**（2026-10-20）

用户要求「不要人审，机器自审」。正确的落地不是把 `--yes` 全拆掉，而是：

| 原来 | 现在 |
|---|---|
| 人点头 | **冻结契约对拍**（判决权仍在被测对象之外） |
| 人小心 | **快照 + 落盘后复验 + 失败原子回滚** |
| 人记得 | **默认接续**（`--rounds 1` 跑两次 ⇒ 轮号 1→2） |

⛔ 新增权限必须用**等强度的另一个约束**顶上（`apply_edits_privileged`
强制 `snapshot_dir`，缺了就拒绝写）。只给权限不给约束 = 不可逆；
只留约束不给权限 = 那条路永远是死的（第 60 条）。

⛔ 三态照旧：`ok` / `rejected` / **`unverified`**。「跑不起来」绝不许
并入「通过」——把没裁决当通过，是最贵的那类事故。

---

### 133. ⛔ **某维度恒为 0 时，先问「这个值在不在读数的定义域里」**（2026-10-20）

314 次 `gate.result` 里 313 次 `frozen deltas` 全 0。看起来是"没有泛化"，
实际是 `evaluate()` 里一句 `if base.reward <= 0: continue` —— **基线没通过的
题根本不进 `deltas`** ⇒ 每条 delta 只可能取 0 或负数 ⇒ **数学上永不为正**。
（第 24 条的第二次命中：恒为 0 先怀疑量具。）

⇒ 修法不是"让它变正"，而是**加一个只读读数**（`frozen_gain` / `frozen_open`），
并明确写死「不许进 `passed`」：一旦接进裁决，就等于让系统去优化那把尺子。
`frozen_gain` 只许流向「给人看的读数」。

⛔ 判据：`gain=0` 与「分母为 0」必须能分辨（第 51 条）。

---

### 134. ⛔ **判退出码时不许接管道**（2026-10-20）

`python3 x.py | tail -6; echo $?` 拿到的是 **`tail` 的退出码**，恒 0。
本轮我据此判「契约崩溃了还返回 0」，差点把一条正常的崩溃路径当成新缺陷。

⇒ 取 rc 一律重定向到文件再读：`cmd > out 2>&1; echo $?`。
⛔ 这一条和第 27 条同族：**读数来自哪个进程**必须先钉住。

---

### 135. ⛔⛔ **「换人审为机器自审」要逐件列出替代约束，缺一件就是拆闸**（2026-10-20）

人审挡住的从来不是"一个动作"，而是**一串判断**。拿掉人之前，先把那串
逐件写出来，再逐件给出替代 —— 少写一件，就等于悄悄少一道闸（第 132 条）。

r60 F/G 两轮的实际清单（装工具这条链）：

| 人审原来挡住的 | 机器换成什么 |
|---|---|
| 要不要装 | 四道闸**当场重跑**（不读档案旧读数） |
| 装的是什么命令 | 与清单**逐字比对** |
| 谁负责 | `approved_by="auto"`，事后可分 |
| 装错了怎么办 | 必能**推导**卸载命令 + 装后探不到就自动卸 |
| 会不会跑任意代码 | 执行白名单按**(启动词, 动词)二元组逐个点名** |

### 136. ⛔⛔ **「给人看的命令白名单」与「给机器跑的命令白名单」必须是两份**（2026-10-20）

`ALLOWED_LAUNCHERS` 有 23 个词，含 `curl` / `wget` / `git` / `python3` /
`pip` / `npm`。那份名单是给**人**看的（人不读源码、读命令），宽松无害。

一旦交给机器执行，它们全是**任意代码执行**：`curl … | sh`、`pip install`
会跑 `setup.py`、`npm install` 会跑 postinstall。

⇒ `AUTO_LAUNCHERS` 只留 `("brew", "install")` 一个二元组。
⛔ 逐个点名，不用全称量词（第 59 条）—— 写「包管理器都行」，
下一个人就会以为 `pip` 也在里面。

### 137. ⛔ **验证一道闸之前，先确认它前面没有别的闸把它短路掉**（2026-10-20）

r60 G 实测：我用「改档案里的 `install_argv`」来验证第 ② 道闸（清单逐字
比对），结果 CLI 走的是 `_mk()` —— 它对 missing 的工具**用清单重建候选**，
档案里的 argv 被整个丢掉 ⇒ 契约实际触发了**真的** `brew install radare2`
（被 120s 超时杀掉，复查环境零改动）。

⇒ 你测的可能是另一条路。判据要挑**只有那道闸能拦下**的形态
（改成同一个 `brew install` 但换包名，③④ 都会放它过去）。

### 138. ⛔ **「这次没装成」不是「提案被否决」**（2026-10-20）

批准说的是「**可以**装」，不是「**装得上**」。一次网络抖动就把它打成
`rejected`，等于吃掉批准 —— 与 r60 D（过闸降级）同族，只是凶手从
「过闸」换成了「复验」。

⇒ 失败时状态**退回 `approved`**（下次可再试），这一轮的失败由
`InstallVerdict.state=rejected` 单独记。**裁决结论与状态是两件事**。

### 139. ⛔ **演练必须一个字节都不落**（2026-10-20）

`--dry-run` 若把状态推进到 `validated`、把 `approved_by` 写上，
它与**真装**在档案里长得一模一样（第 51 条：`gain=0` 与「从没被测过」同形）。
E1b 实测抓到过一次。
⇒ 演练：不落盘、不记账里的 `cap.candidate`、不写批准人；
读数只进 `cap.install_auto` 且带 `dry_run=True`。

### 140. ⛔ **「零翻转」类断言的时间边界必须钉在改动落地那一刻**（2026-10-20）

`contract_ceiling_test.py` 的 T6.2 写的是「改这条口径在生产上零决策翻转」，
实现成 `n_inc == 0`（当前账本里 `inconclusive` 条数为 0）。

r60 G 全量验收时它红了。取证：生产在 **13:36 / 14:28** 连出 2 条
`inconclusive`，两条都走 `all_thin`（easy 层 headroom=0、hard 只剩 1 条）。
而那正是 r60 F **自动接续**生效之后 —— 轮 2/3 时 held-out 已被上一代修到
没得测。⇒ 判据本身是对的（fail-closed），错的是**断言的边界**：
它把"建卷那一刻的一次性事实"写成了永恒性质，而账本还在长。

⛔ 这不是"为了让老断言变绿去调默认值"（第 34 条）：被测口径一个字没改，
改的是「数哪一段」。判据要有牙，还得配一条反证 —— 把边界挪到"现在"，
同一批数据必须数出那 2 条。

⇒ 通用做法：`CUTOFF = <改动那次提交的时刻>`，只数 `ts < CUTOFF`；
再补一条「落地后数出 N>0 条」的反证，证明 0 是切分出来的，不是数据里没有。

### 141. ⛔⛔ **「执行失败」与「落点找不到」必须分叉，rc≠0 不许念成回滚失败**（2026-10-20）

r60 G 第一次**真跑**（生产，`brew install radare2`）：本机执行宿主拒绝
`brew` 内部的 `sandbox-exec`（`sandbox_apply: Operation not permitted`），
rc=1、一秒都没装上。而当时的文案写的是：

    装完仍探不到 ⇒ 这个工具装完本系统的探针找不到它

—— 把**执行失败**说成了**落点问题**。两条路的下一件事完全不同：
一条去查执行环境/权限/网络，一条去补 `discover` 的落点规则。混成一句，
等于让人去修错的地方。

同时：`brew uninstall` 对没装上的包返回非 0（`No such keg`），
把那个 rc 念成「回滚失败」会让一次**干净的回滚**看起来像事故。

⇒ 两条规矩：
· 原因按 `rc` 分叉 —— `rc≠0` ⇒ 说执行失败 + stderr 末行；
  `rc==0` 且探不到 ⇒ 才说落点问题。
· 回滚的判据是**探针**（回滚后再探一次是否 missing），rc 只作参照。

### 142. ⛔⛔ **「没被闸看过」不许与「被闸拦了」长得一样**（2026-10-21）

r65 第一次让**真模型提议器**（`--proposer llm`，本机 `Qwen3.5-4B`）跑完整
链路时出现。同一数据根三次同参数运行**逐字可复现**，一轮的结算是：

    add_textscore_skill  adv=+1.5882  reason="overfit: …"   ← 闯过闸，被拦
    tune_memory_recall   adv=-0.8552  reason="no_advantage" ← 准入淘汰
    fix_literals_skill   adv=+0.1222  reason=""             ← ？？

第三个过了准入闸、优势为正，只是**排第二**。7.5 只让 `eligible[:gate_tries]`
（默认 1）去闯泛化闸 ⇒ 它**从未被评估**，却与被拦的、被淘汰的共享同一个
`reason=""` + `accepted=False`（第 25 条：一个值两种含义）。

⛔ 这不是"可读性"问题：同一数据根加 `--gate-tries 2`，它判 `generalize`
**并被提交**（产出的 `harness_id` 与默认口径下那个候选逐字相同）。也就是说
空 reason 盖住的是一个**真实存在的提交**，而观测侧读到的是
"这条也试过了、也被拦了" —— 于是没人会想到去提升 `gate_tries`。

⇒ 未被评估的候选自报 `not_probed(rank=N/M, tries=T)` 并写明出路
（`--gate-tries`，或"本轮没装 held-out"）。

⛔ 只写读数，不碰任何判据：谁去闯闸仍由 7.5 决定；`not_probed` **不是**
裁决名，`VERDICTS` 封闭集合不动，也不许为它产出 `gen.probe`（否则死路账
会被虚构记录污染）。注入反证：删掉这段补丁 ⇒ 5 条契约转红，而「进闸 /
提交 / accepted」三项结算一条未变。

⛔ 两条连带教训：

1. 这条注释**早就在源码里写着**要区分二者（"否则观测侧会把它们读成
   『没探测』"），却只实现了 `accepted=False` 那一半
   ⇒ **注释不等于证据，读数才是**。
2. 它在模板提议器下八十多轮都不可见 —— 模板的第二名 advantage 恒为负，
   会被准入闸淘汰并写明理由 ⇒ **换一个提议器，会把一直都在的那类缺陷
   变成第一次看得见的缺陷**。判你觉得没问题的时候，先问是不是没人走过那条路。

---

### 143. ⛔⛔ **路径写死 = 把能力锁在一台机器上**（2026-10-22）

仓库第一次推到 GitHub，第一眼看过去是**别人的家目录**：44 行绝对路径散在
23 个跟踪文件里。⛔ 但"清掉 44 行"不是目标 —— 它们分三类，性质完全不同，
混为一谈就会改错地方：

| 类别 | 例子 | 写死的后果 |
|---|---|---|
| **运行时必需** | `capabilities.rea.json` 的 `argv`、桥的 node/REA/ghidra | 换台机器 ⇒ 每条 cli 能力都指向不存在的可执行文件，报错是 `unavailable: 可执行文件不存在` ⇒ **看起来像"这个能力没装"** |
| **溯源元数据** | `tasks/*/tests.json` 的 `source_origin` | 全仓 `grep` 只有 1 个写入点、**0 个消费者** ⇒ 不影响运行，但让人以为这库带着私人路径 |
| **文档示例** | `cd /Users/<某人>/V6` | 对任何人都没用 |

第一类最贵：REA 桥的契约 A–J 全绿，能力是**真验证过**的，但它只在一个人的
机器上成立。⇒ 判据要换问法：不是"有没有绝对路径"，而是**"换台机器解析得出吗"**。

**三个出口，三种修法：**

1. **清单 → 占位符**：`impl.argv` 认 `{python}`（当前解释器）与 `{repo}`
   （**代码根**，不是数据根 —— Y109）。未知占位符 **fail-closed**（`rejected`）：
   留成字面量会让"清单写错了"表现成"能力坏了"。展开后**每个 argv 指向的
   文件都要真的存在**，这条才是可移植性判据。
2. **脚本 → 发现**：node / REA / ghidra 按「显式 env → PATH → 受管目录 →
   常见前缀」查找，**且不许猜** —— 显式给了不存在的路径就返回空串、由调用方
   报错，不许 fallback 到别的路径凑合用。
3. **溯源 → 标签**：`stdlib/core.py` 这种可反推的标签，而不是删字段。

⛔ 三个当场踩到的坑：

- `~/tools/ghidra_*` 同时命中 `ghidra_12.1.4_PUBLIC` 与 `…PUBLIC.zip`，而
  **`.zip` 在字典序上更大** ⇒ 不按类型过滤就把压缩包当安装目录 ⇒ `isdir`
  失败 ⇒ 引擎**静默消失**（表现成"这台机器没装 ghidra"）。
- 契约第一版断言"REA 一定能被发现" ⇒ 在 Linux CI 上恒红：**把自己的机器
  当成世界**。改成"发现了就必须真的存在，没装就返回空串"。
- 把 `--command analyze` 的值当路径去查 ⇒ 5 条**假红**：判据写宽了，
  和写窄一样糟。

**契约** `contract_portability_test.py`（30 项：静态闸 / 占位符 / 发现 / 溯源 /
出口各一组）。核心断言是"零命中"，而零命中可能是扫描器坏了（第 133 条）
⇒ 每条闸都配一条**必须命中**的注入项；四条修复各撤一次，**4/4 转红**，
源码 sha 逐字还原。

---

### 144. ⛔⛔ **一个语法错误不许伪装成三种缺陷**（2026-10-22）

`contract_commit_verdict_test.py` 里有一行 f-string 的表达式部分带 `\"`：

```python
f"demo读commits={'get(\"commits\")' in demo_src}"
```

3.11 及以下是 **SyntaxError**（PEP 701 到 3.12 才放开）。在 3.9 上这一行让
**四份判据同时红**：

| 红项 | 它判的是什么 | 实情 |
|---|---|---|
| 该契约自己 | 提交裁决的形状 | 真（语法错误） |
| `assert_wiring` A2 | 定位不到 `ck` 的条件位 | **假** |
| `assert_wiring` A4 | 条件位上是字符串 ⇒ 恒真断言 | **假** |
| `assert_wiring` B1 | `ck` 只 print 不记账 | **假** |

根因在扫描器：`scan_file()` 遇到 `SyntaxError` 时返回
`kind="stringy"`（"条件位是字符串"）。于是**解析失败**被读成**判据写错**，
再被三条不同语义的断言各自认领一次。修法不是"把断言的判据放宽"，而是
**单列一种事实**：`kind="syntax"` + `parsed=False`，由 A0 一条专属断言报它，
其余组把它**从分母里排除并显式披露**（"85 份里 1 份因语法错误未参与"）。
实测：注入回真实标本后 3.9 上从 **4 红 → 1 红**，红项自己就说清了文件、行号
（L160）和报错原文。

⛔ 顺带查清一件容易想当然的事：`ast.parse(src, feature_version=(3, 9))`
**抓不到它**。`feature_version` 管的是"这个版本有没有这个语法特性"，覆盖不了
f-string 解析器本身的差异 ⇒ 这条判据唯一可靠的证据是**用真实的最低版本
解释器编译**，模拟不算。

### 145. ⛔⛔ **版本闸：`MIN_PYTHON` 由框架裁定，「全部跳过」是假绿的第二扇门**（2026-10-22）

`contract_packaging_test.py` 顶层 `import tomllib`（3.11+），而
`pyproject.toml` 自己声明 `requires-python = ">=3.9"` ⇒ 在 3.9 上这份契约整份
`ModuleNotFoundError`，**零覆盖**，报告里却只是一个"退出码 1"。

⛔ 修法有两条路，选错就滑向"讨好判据"：

1. **让契约自己 `try: import tomllib`** —— 每份需要高版本的契约各写一套 try
   边界，很容易写成"悄悄跳过"（用户只看到绿）。✗
2. **由框架统一裁定**：契约声明 `MIN_PYTHON = (3, 11)`，`check` 静态读它，
   低于该版本就记 **SKIP** 并把原因印进报告（`⏭ contract_x：声明
   MIN_PYTHON=3.11，当前 3.9 ⇒ 不适用（不是失败）`）。✓

⛔ 第二条自带一个新风险，必须同时堵：**全部契约都被跳过**时，`ok` 仍然是
`True` ⇒ 一个断言都没执行却报"全部通过"。这与"0 份契约 ⇒ fail-closed"
是同源假绿，所以 `all_skipped` 也 fail-closed，且摘要必须写
「全部契约都被跳过」而不是「全部通过」。

⛔ 覆盖缺口**写在契约头上而不是省略**：packaging 的 A 组（零第三方依赖）本来
不需要 `tomllib`，但它和 B–D 组同住一个文件；拆出去并不划算 ——
`_stdlib_names()` 取的是**当前解释器**的 stdlib 名单，3.9 的名单比 3.13 小，
同一个 import 在两个版本上会得出相反结论 ⇒ 那会把"环境差异"读成"代码缺陷"。
要拆就得先把口径改成"在所有支持版本里都存在"，那是另一件事。

### 146. ⛔ **判据不许问「证据从哪个文件描述符出来」**（2026-10-22）

`contract_sandbox_test.py` 的 I3 要证明"退回只放行 `prefix` ⇒ 冷 import 被拒"，
原判据是 `any("V6_SANDBOX_DENY" in ln for ln in stderr[-1:])`。3.13 绿，3.9 红，
读数 `rc=0 passed=0 tail=[]`。

真因不是沙箱没拒，**拒绝是真的发生了**，只是两个版本把它放在不同地方：

| 版本 | `PermissionError: V6_SANDBOX_DENY:open` 出现在 |
|---|---|
| 3.13 | 直接冒到 **stderr** |
| 3.9 | runner 捕获成结构化字段 **`stdout.import_error` 的 traceback**，stderr 是空的 |

⇒ 判据改问**证据在不在**（`stdout + stderr` 里有没有 `V6_SANDBOX_DENY`），
不问它走哪个通道。⛔ 但**不许**顺势退化成"`n_passed == 0` 就算被拒" ——
任务失败的原因有很多种（语法错、断言错、模块真不存在），必须点名 DENY 这个
身份。所以配一条反向对照 I3b：**正确白名单下那次输出里不许出现 DENY**，
否则 I3 只是"任何跑动都会印 DENY"的恒真断言。

### 147. ⛔ 诊断正则要覆盖契约**实际用的符号**，夹具要避开「无条件保留区」（2026-10-22）

`_summarize_out()` 的信号行正则里没有 `✗`，而 `contract_serve_test.py` 的
**全部**判据都用 `✗`。后果：那份契约红了以后尾部一条判据文字都没有，只剩
"退出码 1" —— CI 3.9 上就是这样，看得到 I7/I8/I9/I11 四个名字，**四个红点
零诊断**。⇒ 正则加 `✗` / `×`。

⛔ 而给它写夹具时踩了第二层：第一版把 `✗` 行放在输出的**第二行**，注入反证
（把 `✗` 从正则里删掉）时它**照样绿** —— 因为失败分支无条件保留**头 6 行 +
尾 12 行**，那一行本来就留在里面，跟"信号检测"毫无关系。⇒ 夹具必须把目标行
放在**中段**（第 19 行、共 44 行），注入后才 4 红 → 真转红。**"加了断言"和
"断言有牙"之间隔着一个夹具位置**（第 9 条的老形状，第 N 次）。

### 148. ⛔⛔ **环境缺口的范围要「最小必要」—— 拆组，不是把整份关掉**（2026-10-22）

第 145 条讲了"缺口要写在契约头上，不许省略"，但没说**缺口该有多大**。实测把它
逼了出来：`contract_release_test.py` 在 3.9 上红，原因是第 72 行 `import tomllib`
（3.11+）。而这条 import **只服务 A3 一条判据**，它却写在 `main()` 的顺序流里 ⇒
A3 之后的 B/C/D 组共 **7 条判据一条都没跑完**，报告里只剩"退出码 1"。

两种修法，差别在覆盖：

| 修法 | 3.9 上的实际覆盖 | 缺口 |
|---|---|---|
| 整份 `MIN_PYTHON = (3, 11)` | 10 条判据**全部消失** | 10 |
| 把需要高版本的那一条拆成独立契约 | 9 条照跑，1 条 SKIP 并点名 | **1** |

选后者。⇒ **缺口的大小 = 需要高版本的判据条数，不是契约文件数。** 判据是"同一份
文件里，有没有判据因为邻居的依赖而白丢"。

⛔ 拆完要三头验，只跑一遍不算：
1. 高版本上直跑新契约，全绿（本机 3.13 → 5 项 PASS）；
2. 低版本上 `--only <新契约>`：框架记 SKIP **并点名原因**，且"全部契约都被跳过
   ⇒ 按失败处理"要同时生效（这条路本身也得被验，第 145 条下半段）；
3. 低版本上 `--only <原契约>`：原契约 pass + 新契约 skip，份数对得上。
4. 外加**注入反证**：副本根里给 `pyproject.toml` 塞一个静态 `version = "9.9.9"`
   ⇒ 拆出去的那条判据必须红、且 detail 指名 `static='9.9.9'`。

⛔ 一个容易踩的读数陷阱：`--only contract_release` 是**子串匹配**，会把
`contract_release_toml` 一起捞进来。验证拆组时看的是**份数与每份的状态**，
不是"我指定了哪个名字"。

---

### 149. ⛔⛔ **判据要判「谁拉进来的」，不是「此刻内存里有没有」**（2026-10-22）

`selfcheck` 的 `deps.zero`（零第三方依赖）原先数的是「此刻 `sys.modules` 里
有多少模块的文件落在 site-packages 下」。它在本机恒绿、在 **python:3.10/3.11-slim
里恒红**，而红的那个模块是 `_distutils_hack` —— setuptools 的
`distutils-precedence.pth` 在**解释器启动阶段**就注入的，与本包一个字节的关系都没有。

同一条判据、四种环境的读数：

| 环境 | 启动时 `sys.modules` 里的第三方模块 |
|---|---|
| python:3.9-slim / 3.10-slim / 3.11-slim | `['_distutils_hack']` ⇒ 旧判据恒红 |
| python:3.12-slim / 3.13-slim | `[]` ⇒ 恒绿 |
| macOS `/usr/bin/python3` 3.9.6 | `[]` ⇒ 恒绿 |

分界线正是「3.12 起 `ensurepip` 不再带 setuptools」。⇒ **这条判据测的是
"镜像里装没装 setuptools"，不是"本包有没有依赖"。**

修法：`aether/__init__.py` 在**导入任何子模块之前**捕一份 `sys.modules` 快照
（`_boot_modules()`），只有**基线之后**新出现的第三方模块才算「被拉进来」。

⛔ **不要改成"白名单豁免 setuptools"**：那会把运行期真的 `import setuptools`
一起放过。按**时机**分与按**名字**分不是一回事 —— 名字法需要维护，且它假设
"永远不该用这个包"，而时机法只假设"启动前就有的不是我拉进来的"。

⛔ 豁免必须**点名**（detail 里写"环境自带 N 个：…"），否则读者看不出这条判据
在当前机器上少看了几个模块。契约 F 组 4 条：F0 正对照 / F1 注入基线之后的模块
⇒ 必须 BROKEN / **F2 同一次注入只挪进基线 ⇒ 必须 OK** / F3 真样本（有则断言，
没有就明说"这里没样本，未覆盖 ≠ 通过"）。
F2 是 F1 的另一半：只证明"会红"不证明"红的是基线这条路"，判据写成恒 BROKEN 也能过 F1。

### 150. ⛔⛔ **缺本机资产 ⇒ 点名跳过，不许判红**（2026-10-22）

同一个病在 CI/容器里第二次出现：`observability` 的 G3/G4、`portability` 的 C1
在**没有 node 的机器上判红**，而红的措辞是"控制台三态渲染错了""node 发现逻辑坏了"
—— 与真正的病因（这台机器没装 node）毫无关系。这和第 117 条（把"本机没装 radare2"
当夹具）是同一族，只是方向相反：那次是**依赖缺席**，这次是**把缺席读成故障**。

判据分两步，缺一不可：

1. **先独立探一次**这台机器上有没有这个资产（`shutil.which` + 受管目录 +
   真跑一次 `node -e 0`）。没有 ⇒ `sk()` 打印 `SKIP <判据> ⇒ <原因>` 并计入
   `SKIPPED`，**不进 FAILS**；
2. 结尾**不许只印「全部通过」** —— 有跳过时印 `⚠️ N 条判据被跳过（未覆盖 ≠ 通过）`。
   否则"没跑"与"通过了"在输出上完全一样（第 9/24 条）。

⛔ 必须两头验，只验一头等于没验：
- 有 node 的机器上，那条 `ck` **照旧真跑**（本机 3.13/3.9 全绿，说明它没被跳过路径吃掉）；
- 无 node 的机器上，它**印 SKIP 而不是 PASS**（python:3.9-slim 实测）。

⛔ 另一个同族坑（撞在复现过程里）：容器里复跑 portability 时 `A0 这是 git 检出`
和 `A2 扫描面 ≥200 个文本文件` 双红 —— 因为 `python:3.9-slim` **没装 git**，
`git ls-files` 输出为空。**先装 git 再怪契约**：用来复现环境的那个镜像本身
也是一个环境，它缺工具时给出的红同样是假红。

---

### 151. ⛔⛔ **溯源字段是「渲染」出来的，不是「记录」下来的**（2026-10-22）

r67 把 44 处本机绝对路径换成了"可读标签"（`stdlib/<x>.py`）。当时只验了
"不是绝对路径"，**没验标签是不是真的**。往下查一层，10 条生成环境里 **7 条
标签指向不存在的文件**：

| 环境 | 旧标签 | 真相 |
|---|---|---|
| `chaos` | `stdlib/chaos.py` | `stdlib/turtledemo/chaos.py` |
| `encoder` | `stdlib/encoder.py` | `stdlib/json/encoder.py` |
| `core` | `stdlib/core.py` | `pip/_vendor/idna/core.py` |
| `utils` | `stdlib/utils.py` | `pip/_vendor/requests/utils.py` |
| `_inspect` / `_writer` / `_parser` | 同样 | `pip/_vendor/rich|tomli_w|tomli/…` |

两层错，第二层比第一层坏：

1. **子路径被压平**：`_origin_label(mod)` 收的是 stem，用 `src_root/f"{mod}.py"`
   **重建**一个路径 —— 而主循环 `for mod, tree in trees.items()` 只按 stem 索引，
   真实路径早就丢了。
2. **vendor 被冒充成 stdlib**：`site-packages` 在 stdlib **里面**，所以
   `stdlib/` 前缀也能套到 pip 的 vendor 代码上。字段值"真"到能通过
   "不是绝对路径"的闸，但读的人第一眼看到的是"标准库"。

⛔ 还有一层同源病：`note` 写死 `由 aether/{mod}.py 切片自动生成…` ⇒
**10/10 与 `source_origin` 不同源**（一个说 `aether/utils.py`、一个说
`stdlib/utils.py`，两个文件都不存在）。同一件事渲染两遍就会漂，
而且两边都不知道自己错（第 46 条的老形状，换了个字段）。

**怎么证明哪条标签是错的**（可复用）：函数名不够（`utils` 全仓到处都是），
要拿**函数体的规范化 AST** 去反查：

```python
ast.dump(node, annotate_fields=False)     # 去掉位置信息
# 索引：全 stdlib 1034 个 .py 的每个 FunctionDef → {名字: [dump…]}
# 判据：task.py 里**所有**函数的 dump 都落在同一个文件里 ⇒ 唯一命中
```

`encoder` 三个函数全落 `json/encoder.py`、`core` 五个全落 `idna/core.py` ——
这种"全体一致"才是证据；单个函数撞上不说明任何事。

**修法与判据**：

- `origin_label(真实路径)` 四支：仓库内 / `site-packages` / 其余 stdlib / 其它。
  ⛔ 顺序不能反：先判 stdlib 就会产出 `stdlib/site-packages/pip/_vendor/…`
  （字段虽真，但读起来还是"标准库"，与要修的错法同形）。
- `note` 与 `source_origin` 用**同一个** label；扫描时 `paths_by_stem[p.stem] = p`
  把真实路径当场记下来。
- 契约旧 D2（「≥5 条以 `stdlib/` 开头」）和旧 D3（「worldgen 用了
  `_origin_label`」）**把错法写成了标准** —— 判据要跟着真相改，不能留着
  维持已实现的行为。新增 D4（note⊃source_origin）/ D5（`stdlib/` 里不许含
  `site-packages`、`_vendor`）/ D6a–e（合成树单测：子目录不许压平、同名不同
  目录必须给出**三个不同**标签）/ D7（反证：旧写法在顶层与子目录给出同一个
  标签 ⇒ 所以 D6c 有牙）。

⛔ 一条写法教训：别写「源码里不许出现 `f"{mod}.py"`」—— 修复说明的
docstring 里正引用着旧写法，这种断言会**逼人删掉解释**。判**接线**
（两处都走 `origin_label(paths_by_stem[mod]`），不判字面量。

---

## 108. ⛔⛔ 「代码在哪」与「数据在哪」是两件事 —— 仓库里它们恰好相等，于是没人发现

`paths.ROOT = Path(__file__).resolve().parent.parent`。在仓库里它等于仓库根，
**看起来**就是数据根 —— 于是 11 个模块直接拿它当数据根用，68 份契约也跟着它指。
这个等式只在**开发态**成立；`pip install` 之后同一个表达式指向 `site-packages`。

实测（干净 venv，修前）：`aether status` 给的账本路径是
`…/venv/lib/python3.13/site-packages/data/ledger.jsonl`，而且它**真的能跑**：
`rc=0`、账本 0 条、链完整 ✅、`site-packages/data` 被 `mkdir` 出来了。

⛔ 三条后果一条都不会报错：`pip install -U` 重装即丢账本、两个项目共用一个
venv 就混库、只读镜像第一次写账本就崩。⇒ **「悄悄写错地方」比「崩」更坏**：
崩了会有人查，写错了没人查（与第 97 条同族）。

修法**不是**把 `ROOT` 改成函数（11 个模块 import 它取**代码资产**：`console/`、
`arch_baseline` —— 那些跟着代码走才是对的），而是新增一个语义：

| | 含义 | 装成 wheel 后 |
|---|---|---|
| `ROOT` | 代码在哪（包位置） | `site-packages` —— **正确** |
| `resolve_root()` | 数据在哪（工作区根） | 五支解析，永不落包位置 |

## 109. ⛔⛔ 五支里「仓库态」必须排在「工作区」前面，且判据只能是**仓库资产在不在**

判据试过三种，只有一种成立：

| 判据 | 结果 |
|---|---|
| `(p/"aether"/"paths.py").is_file()` | ❌ `site-packages` 里也有它 |
| 路径里含 `site-packages` | ❌ `-e` 安装、vendor 到别处都不长这样 |
| **`tasks/` 与 `tests/` 都在** | ✅ 恰恰是**刻意不打进 wheel** 的那批 |

⛔ 顺序不能调：若让「工作区」先跑，`cd <仓库>/tasks && aether status` 会掉到
兜底目录 ⇒ **同一台机器两个库**，而两边都可能「看着正常」。

⛔ 代价要写明、不许隐藏：开发态下 `.aether` 被仓库支遮蔽（包位置=仓库时 ④
永远走不到），要用它必须显式 `--root`。这是**负结果契约**（`F1`）钉住的取舍，
不是「已知 bug 懒得修」。

## 110. ⛔ `--root` 的默认值只有一个收口点，改那一处就够

三个入口（cli / serve / ui）加全部子命令的 `--root`，都由
`entry.add_root_arg` / `normalize_log_args` / `build_forward` 三处决定，
而命令实现一律 `Path(args.root)`。所以**不用动那 11 个模块**，只要让这三处
从 `resolve_root()` 取值 —— 实测改完 `args.root == str(paths.ROOT)` 逐字不变，
7412 条生产账本照读。

⛔ 若改成「每个 `cmd_*` 各自解析」，第 46 条的老病立刻复发（布局有两份）。

## 111. ⛔ 安装态的失败必须是**人话**，且「没有燃料」≠「环境坏了」

装出来的包里 `tasks/` 原来是空的，`aether synth` 直接抛 `FileNotFoundError`
栈 —— 用户从中读不出「这个安装没有任务源」，也读不出该怎么办，而这两件事的
下一件事完全相反（补燃料 vs 查环境）。改成前置判断 + 三条出路，仍然 `rc=1`：
没有燃料**就是**失败，不是「这次跳过」。

⛔ 顺带被自己的静态闸抓了一次：`cmd_init` 里手拼 `root / "data" / "ledger.jsonl"`
⇒ `contract_entrypoints` 的 E1 立刻转红。**抓到了才说明那道闸有牙** ——
落点已改成从 `_paths()` 取（第 46 条）。

> ⚠️ 本条的**前提**已被第 112 条改掉：现在 wheel 里带示例任务集，
> `aether init` 会物化到 `<root>/tasks/`，新装的包不再是「没有燃料」。
> 这段前置判断仍然保留 —— 用户手动清空 `tasks/` 之后要走的正是这条路。

## 112. ⛔⛔ 「装得上」与「装完有活干」是两件事，而第二件没人测

第 108 条修完之后，安装态的命令都能跑、账本落在正确位置、`rc=0`。
**但那时链路上没有任何一件事可做**：`tasks/`（14 个任务）、`tests/`（68 份契约）、
`console/`、`supervisor/` 全都不进 wheel ⇒ 一个用户 `pip install` 完，
能建骨架、能看 `status`，然后**无事可做**。

⛔ 它不会被任何"安装测试"抓到：所有断言（能跑吗 / rc 是不是 0 / 文件在不在）
在「没事可做」的状态下**全绿**。判据必须钉在**产出**上：

```
$ aether init && aether synth --max 24
   "attempted": 10,  "valid": 5,  "yield_rate": 0.5
```

修法是**带一份示例任务集**进 wheel，用符号链接做到仓库零重复：

```
aether/seeds/tasks -> ../../tasks        # 仓库里只有一份源码
```

setuptools 构建时**跟随符号链接**把真实文件写进 wheel ⇒ 安装物是**真快照**
（不是指向仓库的指针，删掉仓库也照样能跑）。

⛔ `workspace.SEED_FILES = ("task.py", "tests.json")` 与 `pyproject.toml` 的
`package-data` 是两个地方，会漂 ⇒ `contract_seeds_test` 的 `Z0` 断言两者同源
（第 76 条：同一份数据两处消费，构造口径必须同源）。

## 113. ⛔⛔ `include-package-data = true` 会把 `__pycache__/*.pyc` 一起打进 wheel

符号链接带来一个反直觉的副作用：开了 `include-package-data` 之后，**链接目标下
的所有文件**都被当成包数据。实测三次读数：

| 配置 | wheel 内条目 | 脏文件 |
|---|---|---|
| `seeds/**/*` | 29 | 含 `_worldgen_report.json` |
| 点名两个 glob + `include-package-data=true` | 42 | 14 个 `__pycache__/*.pyc` |
| 点名两个 glob + **`false`** | 28 | 0 |

⇒ 判据是**数 wheel 里的条目**，不是看本地目录干不干净（本地 `find -name '*.pyc'`
看不见它们在不在包里）。

## 114. ⛔⛔ `pip install /path` 会复用 `~/.cache/pip` 里的同名旧 wheel

改完 `pyproject.toml`、重装、再数 —— **还是 42 条脏文件**。代码是新的，
包是旧的：`pip` 按 `名字-版本` 命中缓存就跳过重新构建。

⇒ 一律 `pip wheel --no-cache-dir` / `pip install --no-cache-dir`。
⇒ 更一般的纪律：**「改了没生效」先查缓存，再查代码**。它和第 24 条同族 ——
量具（这里是构建缓存）比被测对象更常说谎。

## 115. ⛔⛔ venv 里 stdlib 在 `sys.base_prefix` —— 这条缺陷**在仓库里永远测不出来**

安装态 `selfcheck` 报 `seed.tasks` 红：`_inspect` 这个示例任务跑不过。
而**同一份** `task.py` 在仓库里逐字节相同、在仓库解释器下 pass。

取证过程（值得记，因为前三次全错）：

| 假设 | 结果 |
|---|---|
| 文件内容不同 | ❌ `cmp` 逐字节相同 |
| 解释器版本不同 | ❌ 都是 3.13.12 |
| 是 `expect` 写错了 | ❌ 仓库里同一份通过 |
| **真实异常是什么** | 看不到 —— `traceback.format_exc()` 自己读源码行时又被同一条机制拒了 |

差异落在**有没有 import**：通过的 `textscore` 不 import，失败的 `_inspect` 有
`from typing import ...`（冷 import）。真根因：

```
_audit_allowed_paths() 只放行 sys.prefix / sys.exec_prefix
而 venv 里 stdlib 在 sys.base_prefix（解释器本体）
⇒ 安装态下任何冷 import 都 PermissionError: V6_SANDBOX_DENY:open
```

⛔⛔ 为什么仓库里测不出来：仓库直接用解释器本体跑，`prefix == base_prefix`
⇒ 漏掉那一项也看不出差别。**只有安装态暴露**。
⛔ 而"看出来"之前先看到的是**假象**：任务像是"代码写错了"，实际是沙箱配置
少了一个前缀 —— 与第 25 条（一个异常两种含义）同族。

修法：白名单补 `base_prefix` / `base_exec_prefix` 并去重（非 venv 时四个值
全是同一个目录，写四遍只会让白名单**看起来比实际更宽**）。

反证写在 `contract_sandbox_test.py` 的 **I 组**——它不建 venv，而是**伪造**
`sys.prefix` 指向空目录（白名单在父进程算好写进 cfg，所以与真 venv 同义）：

```
I2 venv 形态下冷 import 零误伤            → rc=0 passed=1
I3 ⛔ 退回「只放行 prefix」⇒ 必须红        → rc=1 PermissionError: V6_SANDBOX_DENY:open
```

没有 I3，I2 在本机是**恒真**的（`prefix==base_prefix` 时它永远绿）。

## 116. ⛔ 自检子集不能是「全量的缩水版」，它的价值在**能被打破**

68 份契约全量 30–37 分钟。想加个「快检」时最自然的做法是挑几份快的跑一遍 ——
那是**缩水版**：它红的时候你不知道是坏了还是没跑全，绿的时候也不知道它到底
测了什么。

`aether selfcheck` 换了个口径：不跑契约，**跑不变量**（10 项 / 1.5s）。
它与 `check` 的分工由契约钉住 —— 在没有 `tests/` 的根上，`check` 判失败、
`selfcheck` 照跑，而**这恰恰就是安装态的形态**（`contract_selfcheck_test` E 组）。

⛔ 关键不在"10 项全绿"，而在**每一项都能被打破**：
`contract_selfcheck_test` 的 C 组逐个注入破坏（拆掉链接校验 / 放宽人审白名单 /
机器白名单混进 `curl` / 拆掉「不许回退」…），断言**对应那一项必须红**。
没有这一组，`selfcheck` 的绿灯与"什么都没测"在输出上完全一样（第 9/24 条）。

## 117. ⛔ 契约夹具不许依赖「本机没装某个工具」

r60 G 把装工具的人审换成机器自审之后，它**真的装上了 radare2**。
于是三份契约集体转红 —— 不是回归，是它们把「本机没有 radare2」当成了夹具：

```
✅ radare2 已经装好（/opt/homebrew/bin/r2），档案里没有它的待办候选
   ⇒ 没有可 approve 的东西
```

`git stash -u` 后在 HEAD 上跑**同样红**（这就是"不是本轮引入"的取证方式，
第 96 条：代码不是本轮写的，但要说清是谁弄坏的）。

修法：夹具自造 —— 逻辑名仍是 `radare2`（判据要认的名字不变），但 exe 换成
私有文件 `r2_fixture`，由契约在临时目录里现造并注入时钟清单。
⇒ **夹具的可控性必须由契约自己持有**，不许借用"这台机器恰好没装"。

## 118. ⛔⛔ 「代码根」这条判据会让 `-e` 装变成另一种混库 —— 而它只在"站在别人家里"时暴露

第 109 条把 ③ 的判据定成「仓库资产在不在」，理由是 68 份契约都在仓库根跑、
不宜按 cwd 判。这个理由成立，但它顺带把**开发态装法**推进了同一个坑：

```
$ pip install -e .            # 装的就是仓库本身
$ cd ~/my-project && aether init && aether evolve
$ aether status
   "path": "/Users/…/V6/data/ledger.jsonl"     ← 仓库的库，7506 条
```

`~/my-project/data/` 空着，事件全追加到仓库账本上。它和修前的
`site-packages/data/` 是**同一个病**（p.109 的三条后果一条不少：重装即丢、
多项目混库、两边都"看着正常"），只是换成了 `-e` 这件衣服 —— 而 `-e` 恰恰是
"开发者工具"这个产品形态的**默认装法**。

⛔ 修法不能是"改成按 cwd 判"：那批把子进程 `cwd` 设成临时目录的契约会集体
掉到 ⑤（`~/.local/share/aether`）⇒ **测试写生产库**。所以例外必须窄：

| 情况 | 落点 | 为什么 |
|---|---|---|
| cwd 在仓库内 | ③ 仓库 | 契约、常驻进程都在这里 |
| cwd 在仓库外，没 `.aether` | ③ 仓库 | 同上（子进程 cwd 是临时目录） |
| **cwd 在仓库外，有 `.aether`** | **④ 工作区** | 用户明说过"这是工作区" |
| cwd 在仓库内的子目录且有 `.aether` | ③ 仓库 | 根不许被拽出仓库 |

⛔ 四条里后三条都是**负结果契约**（`F1c` / `F1d` / `F1e`）：只写"例外生效"
那条，等于给了一个能被滥用的开口 —— 而这种开口的代价是"测试写生产库"，
在读数上同样看不出来。


## 119. ⛔⛔ 「内容断层」不是修了一次就没了 —— 同族资产要**逐个点名**

第 112 条修了 `tasks/`（装完有活干），当时以为这一族收口了。**没有。**
同一类资产 `console/` 只会更晚暴露，因为它不在"开始工作"这条路上，在"看结果"那条路上：

```
$ pip install . && cd ws && aether init && aether ui
   ❌ 找不到控制台页面：site-packages/console/index.html     rc=1
```

而 `aether status` / `synth` / `evolve` 全都正常 —— API 也是好的，只是页面 404。
⛔ **它比 `tasks/` 那次更险**：`tasks/` 缺了立刻"无事可做"（一眼看出来），
`console/` 缺了只是"这个命令起不来"，用户会以为 UI 就是这样。

修法和 `seeds/tasks` 完全同构，这正说明它该被模式化：

| 资产 | 包内快照 | 读取侧收口 | 自检项 |
|---|---|---|---|
| `tasks/` | `aether/seeds/tasks`（symlink） | `paths.seeds_dir()` | `seed.tasks` |
| `console/` | `aether/seeds/console`（symlink） | `paths.console_dir()` | `console.page` |

⛔ `console_dir()` 的**顺序不许反**：仓库那份在就用仓库那份（改了就生效、
一个事实源），不在才落回快照。判据是"仓库那份在不在"，不是"包在不在"——
后者两边都成立，等于没判。反证在 `contract_seeds_test` **H4**：一棵假包里
**同时**放两份且内容不同 ⇒ 必须拿到仓库那份（顺序反了就红）。

⛔ 自检项只看"目录在不在"**不够**：真出过"目录在、页面是空的"（glob 点错 ⇒
只带进一个空目录条目）。所以 `console.page` 还要验文件非空（H7 用 1 字节页面
做反证：必须判 `broken`，否则白屏而自检全绿）。

⛔ 教训落到操作层：**凡是"仓库根下的目录、运行时要读"的东西，都进不了 wheel。**
新增一个就要同时加三样 —— 包内 symlink、`package-data` 点名（不许通配，
那会把 `.DS_Store` 一起发货）、`selfcheck` 一项。少一样，装完就是"看着装好了"。

用法与"用在哪"见 `docs/USAGE.md`。


---

## 120. ⛔⛔ 「打算用什么模型」与「实际跑没跑成」必须是两条事件

`evolution.run_config` 写在**跑之前**，它只能记意图：端点连不上时它照样写
`kind="llm"`、`fallback=""`（构造 `OllamaProvider` 不校验可达性，第一次
真发请求才失败）。于是「接了模型、跑了、失败了」与「接了模型、跑了、
成功了但没产出」在账本里**逐字相同**。

⇒ 补一条**运行后**的 `evolution.llm_result`，数据直接取自
`proposer.diagnostics()`（同源，不另算一遍）：`error` 非空就是"链路走到了
但失败了"，`raw_len>0` 就是"真拿到了模型输出"。

⛔ 顺带更正一个读错的数：全量账本 71 次 `run_config` 曾被读成
`provider=None×71`，实测 `"provider"` 这个键**命中 0 次** —— 是把"没有这个
键"当成了"值是 None"（第 24/133 条第八次命中）。真实读数是：
`proposer=template ×71`，且**没有任何字段记模型名**。

## 121. ⛔⛔ 存档要按 `blast_layers` 分层，层内做帕累托 —— 不是存 Top-K

每轮只留一个 best ⇒ 其余**通过准入闸**的候选随轮次回收：它们的 reward
与 blast 都算过了（评估成本已付），只因"这一轮不是第一名"被永久丢弃。
下一轮基线变了，同一个胶囊值不值得提交**要重测才知道**，而旧代码连重测的
机会都不给 ⇒ 单线爬坡。

三条缺一不可：
1. **分层键 = `blast_layers`**（改了哪几层）。同一层内的解才可比；
   `["skills"]` 与 `["skills","tools"]` 的 reward 差异里混着"改动面不同"。
2. **层内帕累托** `(reward, -size_delta)`。被支配的（两项都不差、至少一项
   更好）不入档。⛔ 刻意不做加权求和：`size_delta` 是字节、`reward` 是
   比例，加权等于给两者编一个汇率，而那个汇率是所有后续结论的隐含前提。
3. **存档不参与判据**：`resample` 取回来的每条都带 `stale=True`，必须在
   **当前**基线上重算 reward 才许进任何判据。默认 `--archive-resample 0`
   ⇒ 一条 `archive.*` 事件都没有（与历史逐字同口径）。

⛔ 还有一个只靠数值判不出来的坑：记 `baseline_reward`（数值）**不够** ——
"还没提交"的那些轮里它恒为 `0.0` ⇒ 靠数值判断"新旧"会把所有条目混成
同一个基线。必须记 `baseline_harness_id`（内容寻址，提交一次就变）。

## 122. ⛔⛔ 物化真实题源时，`task.py` 必须是**修复版**，不是 bug 版

V6 环境的语义是"**这段代码是对的**，变异它来造题"，合成器要的是 F2P
（变异**前**通过、变异**后**失败）。第一版把 patch **前**的源码写进
`task.py`（想的是"让 agent 去修 bug"），判据却来自 patch **后**的行为 ⇒
判据从一开始就 100% 失败 ⇒ 三个环境全部：

    attempted=12  valid=0  yield=0.0  behavior=0

—— 基线本来就红，就没有"可失去的东西"，任何改动都测不出差别。这与
"发展集基线 0/5 ⇒ `advantage` 全 0"是**同一个病**，只是发生在题源层。

⇒ 契约必须**同时**断言两件事：修复版 `yield > 0`、patch 前 `yield == 0`。
只断言前者 ⇒ 换回 bug 版也可能因为"变异点少了"碰巧过。

## 123. ⛔⛔ 产率由**判据覆盖率**决定，不由"源码真假"决定

把真实仓库的 22K 模块接进来，只配"那道 bug 的 2 条判据" ⇒ 合成器在整模块
上撒 12 个变异点，命中率 0：

    swb_django_10914（global_settings.py 22K / 2 条判据）→ yield=0.0
    同文件改配 136 条判据（自动枚举模块级常量）        → yield=1.0（12/12）

⇒ 换真源码**不解决**产率问题；补覆盖率才解决。这与 V5 的结论同源
（bench_gain 12 条判据只覆盖 32 个点位里的 3 个）。

⛔ 但两类判据**两套标准**，别用一把尺子卡：
- 手工探针（这道题的判据）：必须能区分 gold / patch 前两版。
- 覆盖率探针（同一模块的其它行为）：只需在当前版本为绿 ⇒ **不做**
  "必须区分两版"的反证（做了就会把 134/138 条全丢掉，覆盖率还是上不去）。

⛔ 纯类库模块（`OrderedSet` 那种）没法靠自动枚举拉起覆盖率：没有模块级
常量、也没有无参函数 ⇒ 只能人给问法。实测该模块 7 条判据、`yield=0.0`。

## 124. ⛔⛔ 「advantage 恒 0」是单次读数，不是结构必然 —— 提交后复验推翻了它

同一个 `--proposer llm`、同一份 HEAD 代码，只换了挖到的弱点，读数完全不同：

| 弱点 | 模型产出 | 读数 | 结局 |
|---|---|---|---|
| `fix_boolops` 一类 | 3 颗胶囊，全过语法闸 | `advantage` 全 `0.000` | 全被拦 |
| `textscore`（dev 0/5 全红） | 1 颗 `fix_textscore_skills` | `reward=advantage=1.0`，dev 0/5→**5/5** | 被泛化闸按 `overfit` 拦 |

⇒ 两条纪律：

1. **结论必须带前提。** 写"LLM 提议没用"之前先问"在哪个弱点上"。
   `advantage` 是 `候选 − 当前基线` 的差，基线红得多深、模型打没打中那个点，
   都能单独把它压成 0。
2. **不许拿旧临时目录当证据。** 复验时才发现：`/tmp/v6_arch_ws` 的存档行里
   **没有** `baseline_harness_id`（跑在加字段之前）、`/tmp/v6_llm_ws*` 的
   `run_config.llm` 是 `null`（跑在打补丁之前）。出数一律在当前 HEAD 上重跑。

## 125. ⛔⛔ `overfit` 拦的不是"假修复"，是"只修了发展集那一侧"

上面那颗胶囊的完整判据读数：

    baseline    dev 0/5 红，held-out 基线 0.0（3 题：easy 2 / hard 1，headroom 有）
    candidate   dev 5/5（easy 1.0 / hard 1.0），held-out 仍然 0.0 → 0.0
    verdict     overfit，held-out gain 0.0 ⇒ accepted=false，committed=null

⇒ **发展集侧的分辨率好得很**（0/5 → 5/5，不是"判据没分辨率"）。真正的问题是
发展集与 held-out 的**弱点不同源**：模型看到的是 textscore 的 7 个失效检查名，
它就逐字点名修那 7 个 —— 对 held-out 那些题目一点没动。

⇒ 下一步该动的是"候选生成时能不能看见 held-out 的弱点**类别**"，
而不是换更大的模型、也不是去修判据。⛔ 若把这道闸放宽（"dev 涨了就提交"），
得到的就是一颗把 7 个检查名硬编码进去、换个模块就归零的胶囊。

## 126. ⛔ 把检查名喂给模型 = 让它抄答案（假设被实测推翻了一半）

`weakness.evidence` 里带的是 `checks_failed:clamp_low,clamp_high,...` 这种
**具体检查名**。qwen2.5:7b 拿到这份名单后的行为极其一致：把 7 个名字
**逐字写进技能正文**（`L3.skills.patch:textscore` = "修复 clamp_low,
clamp_high, normalize_basic, ..."）⇒ 发展集 0/5 → 5/5 满血，held-out
0.0 → 0.0 ⇒ 判 `overfit`。

检查名是**发展集**的信息。把它当输入，等于让模型抄答案：它抄得越好，
越不可能给出跨环境的通用修法。于是加了 `--evidence coarse`：只给
`failure_modes`（behavior_mismatch / crash / import_failed / timeout /
unknown）与 `n_failing_checks`，**不出现任何检查名**；账本
`weakness.mined` 仍记原文（审计要能回答"它当时到底看到了什么"）。

⛔ **但它没解决问题** —— 2×2 实测（rounds=1 / group=2 / max-tasks=4）：

| 证据粒度 | 给不给推论 | 模型产出 | 结局 |
|---|---|---|---|
| exact | 不给 | `fix_textscore_skills` | dev +1.0 / ho 0 ⇒ **overfit** |
| coarse | 不给 | `fix_textscore`（dev +1.0）+ `fix_control_flow`（0.0） | overfit / no_advantage |
| exact | 给 | `fix_textscore_common`(0.0) + `fix_textscore_eventflow`(routing 语法错) | 全拦 |
| coarse | 给 | `fix_textscore_common`(0.0) | no_advantage |

⇒ 粗化**不改变**"修法只对点名环境生效"这个选择，反而让模型失去依据；
补一句逻辑推论（`--ho-implication`）也没用，它转头写出
`L1.routing.repair.patch:eventflow` 这种被 `apply` 闸（`routing target
需形如 routing.<kind>`）当场挡下的东西。**假设不成立，如实记在这里，
别重复试**。

## 127. ⛔⛔ `patch:<env>` 在别的环境上 0 增益是**构造必然** —— 不是"讨好判据"

上面那张表暴露的真问题不在模型，在判据：泛化闸把 `ho_gain <= floor`
一律判 `overfit`，可对一颗**只改定点技能**的胶囊来说，held-out 上必然是
0 —— 这不是"证据说明它没泛化"，这是**构造上不可能涨**。拿它判 overfit
⇒ 定点修法永久封死：实测 dev 0/5→5/5 也被拦，连续 6 轮 `committed=null`。

⇒ `--allow-scoped-commit`：这类候选判 `scoped`（放行，**不是** generalize）。

判定用 `capsule_scope()`，而它的判据是**内容承载，不是层名**：

· 只看层名（第一版）⇒ 把所有 LLM 胶囊判成 generic，因为模型为了让新技能
  **可达**，必然会顺手写 `L1.routing.<kind>` 与 `L2.tools`。可那两处写的
  值是 `patch:textscore` —— 一个只对某环境生效的技能名：路由指向它，
  别的环境走到也调不动；工具表挂它，它不是真工具。
  ⛔ 把层名当成能力，就会把"结构必需的陪衬"读成"通用能力"。
· 放宽只到"值本身是定点技能名"为止：路由指向 `fix_control_flow`（通用）
  ⇒ generic；工具表出现 `read_file`（真工具）⇒ generic；
  `L4.memory_policy` / `L5.world_facts` / `L0.*` ⇒ 一律 generic。
· 被当成"中性"的目标写进账本 `scope.neutral_targets`（放行必须可复核）。

实测（3 轮 / LLM / `--allow-scoped-commit`）：

    probe r1  fix_textscore_skills  verdict=scoped  dev +1.0  ho 0
              neutral = [L1.routing.repair=patch:textscore, L2.tools+=patch:textscore]
    ⇒ harness.commit 1 次，committed=258a11ba…，improved=True
    r2/r3    no_weakness_evidence（dev 5/5 全绿 ⇒ 没弱点了，不是失败）

⛔ 两条不许含糊的话：
1. **这是"发展集修好了"，不是"变强了"。** 账本的 `scoped` 与 `generalize`
   分开数；把两者相加去算"泛化了几次"就是自欺。
2. 它放宽了一道闸的**适用范围**，所以必须显式开（默认关）、必须进
   `run_config`、且新增放行态要过 `contract_generalization` 的 A3/A4
   （那两条是写死的"须人工确认"，这次是 2026-10-21 人工确认过的）。

## 128. ⛔ 契约里"固定时间戳 + 挂钟 now" = 定时炸弹

`contract_ghprobe_test` 的 B3 原本这么写：

```python
now = time.time()
G.stale_days("2026-10-19T16:16:32Z", now=now + 3 * _day) == 3
```

被减数是**写死的时间戳**，另一头却跟着挂钟走 ⇒ 写这条断言的那天刚好跨
3 天，能过；隔两天再跑就是 4 天。2026-10-21 的验收里它红了，而红的原因
与 `stale_days` 毫无关系。

⛔ 更贵的是它的**连带面**：`contract_autonomy` 把"消费该模块的契约"
当机器自审的对拍基线（`srcmut.select_contracts` 按 import 关系挑）——
基线一红，`auto_apply` 直接拒判（"删前基线就是红的 ⇒ 对拍无意义"）⇒
**6 条断言一起红**，而真正的根因只有一行 `time.time()`。
⇒ 读验收报告时，先看有没有"级联"：一处红会点亮它的所有下游。

修法：`now` 从**同一个固定时间戳**派生（`calendar.timegm(...) + 3*_day`）。
断言名原本就写着"不依赖跑的这一刻"，是实现没做到，不是判据改了。
另补两条反证：挪 7 天 ⇒ 天数 +7；`now` 早于 `pushed_at` ⇒ 0（不许负数）。

## 129. ⛔ 层名前缀只有一份 —— 新代码想"判层"就去用 `genome.split_target`

我写 `capsule_scope()` 时直接在 `generalization.py` 里写了
`"L3.skills." / "L1.routing." / "L2.tools"` 三个前缀字面量。
`contract_genome_source` 当场转红（A1/A2：target 字面量只许出现在
`genome.py` 的四个 `_t_*` 里）。

⛔ 这不是"契约太严"：层名有两份 ⇒ 改层名时必漏一处，而**漏的那处不会
报错** —— `capsule_scope` 会静默匹配不到，把所有定点技能判成 `generic`，
表现成"模型给的都是通用修法"，也就是**又回到"定点修法永远提交不了"**。
症状会被归因到模型/判据，真因是一处没同步的前缀。

修法：`genome.split_target(target) -> (层, 子键)`，前缀由 `_t_*` 现算
（`_t_skill("") == "L3.skills."`），所以"改层名"依旧只有四个落点。
`split_target` 不认得的一律 `("other", target)` ⇒ 保守算通用（收紧，不是放宽）。

## 130. ⛔ `n_envs` 是**世界**规模，不是**选择面**

`--envs auto` 把 `tasks/` 下全部合法目录读进来（当前 13 个），可
`--dev-env` / `--heldout-env` 的默认值仍是 `textscore` / `eventflow`
⇒ 另外 11 个环境**只以冻结题身份进入非回归闸**，既不进发展集也不进
held-out。原实现对这件事**完全静默**：读 `n_envs=13` 的人会以为
选择面也是 13。

⛔ 没有自动挑 —— 替用户挑 dev/heldout 是改实验语义（第 103 条）。
只把事实算清楚并写进账本：`run_config.envs_role = {dev, heldout,
n_world, n_selection, n_frozen_only, frozen_only[], note}`，
并在 `frozen_only` 非空时往 stderr 说一句（人能看见，不只账本能）。

实测（`--envs textscore,eventflow,scheduler`）：
`envs_role.frozen_only == ["scheduler"]`、`n_selection == 2`，
`n_envs` 与 `n_world` 都是 3（两个字段不许打架）。

## 131. ⛔⛔ `scoped` 的豁免理由只有一句，**负数不在其中**

`scoped` 放行的唯一依据是：「`patch:<env>` 在别的环境上 0 增益是**构造
必然**」（第 127 条）。可原实现写的是 `if ho_gain <= heldout_floor:`
（floor 默认 0.0）—— **负数一起放行**。负数不是必然：它恰恰是"那几处被记
成 `neutral_targets` 的陪衬**真的碰到了** held-out"的实据，等于把豁免的
前提自己证伪了 ⇒ 放行一次**真回归**。

可达路径（不是理论假设）：路由是**链**，`op="set"` 会把基线那条链**顶掉**
（`op="append"` 不会 —— r63 提交的 `258a11ba` 用的是 append，所以
ho_gain 恰好 0.0 没踩到）。`set L1.routing.repair=patch:A` ⇒ 在 held-out
里 `patch:A` 因 env 门控调不动 ⇒ 链上可用的只剩空 ⇒ 基线原本依赖的通用
兜底消失 ⇒ 掉分，而这颗胶囊的 `capsule_scope` 仍是 `env`。

⛔ 修法**不新增裁决名**：`VERDICTS` 是封闭集合（A3 守着），加名字要走人工
确认；而**收紧**不需要新状态 —— 负数落回既有的 `overfit`（拦截），差异由
`heldout_gain` 这个数值字段承载。容差 `< -1e-9` 与 `probe_removal` **同一个
数**（两处对同一个偏差必须给出同一裁决）。

⛔⛔ 契约 G 组的两次教训，第二次比第一次值钱：
1. **先只加断言、不修代码 ⇒ G2 必须红**（否则不知道断言是不是死的）。
   实测红：`scoped blocks=False ho=-0.667` ⇒ 洞复现。
2. 第一版夹具**打偏了**：held-out 基线给成 3/3 ⇒ 候选还没走到 scoped 分支，
   就先撞 `base_at_ceiling` 判 `inconclusive` —— G2 居然"绿"了（因为
   `blocks=True`），只有 G3（scope 为空）露了馅。
   ⇒ **"被拦"不等于"按我预期的那条规则被拦"**，夹具必须自证走的是那条路
   （G1 现在断言 `base_heldout < 1.0`）。

⛔ 同一族还剩一个**口径**问题（未动，先记着）：`RoundReport.improved`
的定义是 `committed is not None` ⇒ 一次 `scoped` 提交也算"变强了一次"。
裁决名在 `generalization.verdict` 里分得开，但全仓没有任何统计在数 `scoped`
⇒ 谁拿 `improved` 当计数，就会把"发展集修好了"读成"变强了"。
改它等于改历史口径，要单独拍板。

## 132. ⛔⛔ 「验收不写生产账本」要**每个会落盘的子命令各自钉一次**

第 129 轮修好 `contract_world_facts` 的 D6（`evolve` 落回仓库账本）之后，
我写下了"验收零写入"。**这个结论是错的**：第二轮全量验收期间
`data/ledger.jsonl` 的 mtime 变成了 13:31，末尾多出 4 条 `cap.invoke`
（`seq 7716–7719`，12:26 与 13:31 各一对）。

真因：`contract_rea_bridge_test.py` 的 C 组用
`cwd=ROOT` + 不传 `--root` 跑 `caps invoke` ⇒ 解析落回仓库 ⇒ **每跑一次
验收写 2 条**（C2 一次 + C4 一次，与账本逐字对上）。
同一个病、换了个契约 —— "上次修过了"不是理由，**收口点必须是"每个会写
盘的子命令"**。

⛔ 顺带做了一次穷举（16 处契约里的 `aether` 子进程调用）：5 处没传
`--root`，其中 4 处只读（`--version` ×2 / `caps list` / `discover`），
实测账本行数不变；**只有 rea_bridge 那 2 处真写**。⇒ 结论要这样得到，
不能靠"应该没事"。

⛔ 断言不许写成"我传了 `--root`"（那是抄答案），要写成**事件落在哪**：
C5 遍历临时目录里的 `ledger.jsonl` 数 `cap.invoke` ≥ 2。
反证时**用 `AETHER_ROOT` 当诱饵**验证它真会红 —— 这样反证本身也不写生产库。

## 133. ⛔⛔ 纯类库模块的覆盖率可以**模板化**，但模板只许碰协议

r62 的结论是「`OrderedSet` 那种模块没法自动拉覆盖率，只能人给问法」
（第 123 条）。本轮把它做成了模板，边界比预期清楚，也比预期窄。

**允许模板化的理由**：判据的真值从来不来自提议方 —— `auto_probes` 只枚举
**候选表达式**，真值一律由 `calibrate_coverage` 拿 gold 源码真跑得出，
求值失败的当场丢弃 ⇒ 模板给的夹具不合适只会被丢掉，**留不下假判据**。
模板扩大的是**候选集**，验收标准一点没松。这与「手写 expect」是两件事。

**模板长什么样**（`_CLASS_TEMPLATES` / `_CLASS_OBSERVERS`）：

    构造：C()  C([])  C([1, 2, 3])  C('ab')  C(0)  C('')
    观察：len(E)  list(E)  bool(E)  list(reversed(E))  E == E  E[0]  E[-1]

⛔ 这些**全是协议**（`__len__` / `__iter__` / `__bool__` / `__reversed__` /
`__eq__` / `__getitem__`）。**普通方法一个都不碰**（`last()` / `snapshot()` /
`count_if()`）—— 调用它们要传参，那就是猜。这条边界是实测出来的：

    196 行 / 整文件一个类：20 个点位，10 个变得动
      判据 2 条  ⇒ 撞到 1 个点位        yield=0.1
      判据 27 条 ⇒ 撞到 2 个点位        yield=0.2   ← 翻倍
      剩余 8 个变得动的点位**全在普通方法里** ⇒ 20% 是这条纪律下的天花板

⛔ 但**换一种形状就没收益**，同一套模板：

    292 行 shlex（一个类 + 一堆模块级函数）：2 条 vs 11 条判据 → yield 都是 0.1333
    30 行小夹具：2 条 vs 22 条 → yield 都是 0.5

⇒ 「类模板」补的是**类**，补不了散落在模块里的函数；模块小到变异点本来就
少时，多判据也没有增益。**同一个工具在三种形状上三种结果**，只报其中一种
就是在挑数据。

⛔⛔ 踩到的坑（比收益本身更值得记）：第一版观察器里有 `tuple(E)`。
`list(E)` 之外多了它"看起来更全"，但 `expect` 要落进 `tests.json`，而 JSON
没有 tuple —— `tuple([1,2,3])` 存成 `[1,2,3]`，执行器求回 `(1,2,3)` ⇒
**25 条自动探针里 5 条基线就是红的**（`got=(1, 2, 3) expect=[1, 2, 3]`）。

最刺眼的是：这 5 条在**我自己的求值器里全绿**，只有落盘后经生产执行器才红。
⇒ 判据「能生成」不等于「能落盘用」（同第 109 条），而"只测自己那条路径"
等于用我写的校验器验我写的生成器。契约因此加了 F 组：**物化成真环境后
基线必须 100% 绿**，走的是 `Environment.from_dir` + `verify_source`，
不是 `eval_in_ns`。

⛔ 另一个只测一个变异种类就会漏的：只测「缺 `__reversed__`」时 22 条判据
的 yield 与 2 条**完全相同** —— 因为唯一撞得到的变异点就在 `__reversed__`
上，多出来的 20 条一条都没碰上。补上 `E == E`（撞 `__eq__` 反转）之后
才对 0.1 → 0.2。**契约里必须有两种以上的变异类型**，否则"够用"是假的。

## 134. ⛔⛔ `str()` 会把「字段缺失」变成**合法字符串**

`evolution._deadend_accum` 的原写法：

```python
v = str(p.get("verdict"))
if v in self.NO_EVIDENCE_VERDICTS: ...   # {"inconclusive"}
if v == "generalize": streak[key] = 0
else: streak[key] = streak.get(key, 0) + 1
```

字段缺失时 `str(None)` == `"None"` —— 它既不是 `generalize`，也不在
`NO_EVIDENCE_VERDICTS` ⇒ **静默计入「被拦 +1」**。不报错、不告警，
只是冷却 streak 被污染物、某条候选被误判成死路。

⛔ 判据必须拿**原始值**判（`in 集合` / `is None`），不能先 `str()` 再比。
只有"要写进日志/拼接"时才 `str()`，那是格式化，不是判定。

**这个洞现实里真会踩**，因为同一条裁决在账本里有**两种形状**（实测
7719 条事件）：

| 事件 | 裁决字段 | 另一形状 |
|---|---|---|
| `gen.probe`（140 条） | `payload.verdict`（扁平） | `generalization` 键 **140/140 不存在** |
| `harness.commit`（120 条） | `payload.generalization.verdict`（嵌套） | 扁平 `verdict` **0/120** |

⇒ 消费方猜错键**不会报错**，只会拿到一整列 `None`。所以"同一个概念
在账本里只有一种形状"是必须显式维持的纪律，不是自然事实。

处置：**只让异常可见，不改判据** —— `unknown` 计数器 +1，`streak` 照旧
+1（fail-closed，历史决策可复现）。要改 streak 语义是另一件事。
契约 `contract_ceiling_test` 的 T11 组 4 条钉住（缺失 / 未知串 /
反证 `overfit` 不涨 / 生产读数 0/140）。T1–T5 原来只覆盖
`inconclusive`、`generalize`、键分离，**没有一条**测"读不懂"。

## 135. ⛔⛔ 可达条件是**合取**的代码，等于没被跑过 —— 存档「取回并装配」

r62 加的分层非支配存档（`aether/archive.py`）本意是打断单线爬坡。它的
零件测得很硬：J 组 7 条把 `resample()` 的三重排除与层间轮转钉住，G 组
把胶囊快照往返钉住。**但两者之间的那段装配，一次都没被执行过。**

| 读数 | 结果 |
|---|---|
| 生产账本 `archive.*` 事件 | **0 条**（默认关，从未开启） |
| `contract_archive_test` 的端到端轮数 | `--rounds 1` ⇒ 轮首存档必空 |
| 四次专门运行（template 2 轮 / llm 3 轮 / llm+`--allow-scoped-commit` 3 轮 / llm+`--group 4` 3 轮） | **11 次 resample 全部 `n_picked=0, n_restored=0`** |
| 测试里断言 `archive.resample` 的地方 | **0 处**（H3 只断言 `archive.offer`） |

⛔ 注意：那 11 次 `n_picked=0` **每一次都是设计正确**，不是坏了。三重排除
各管一件事，而且三次是三个不同原因：

| 情形 | 谁把它排除了 | 为什么正确 |
|---|---|---|
| 本轮没有提交（template 两轮） | `exclude_baseline` | 存档里每条都量在**当前**基线上 ⇒ 重测结果必然一样 |
| 本轮提交了，但存档里就那一条 | `exclude_harness` | 提交那条的 harness **已经**是当前 harness ⇒ 等价物 |
| 本轮提过同名胶囊 | `exclude_capsules` | 同胶囊 + 同基线 ⇒ 结果必然一样 |

⇒ 能真正取回的条件是**一个合取**：「同一轮里 ≥2 个够格候选」**且**
「其中一条被提交」。生产 81 轮里 **80 轮只有 1 个候选进闸**（均值 1.01，
只有 1 轮出现过 2 个）⇒ 这个合取在整个历史里几乎不成立，所以
**「取回 → 还原成 Capsule → 并进候选 → 参与评分」那段从没执行过**。

而装配恰恰是最容易坏的地方，且三种失败**都不报警**：名字撞车被
`continue`、`cap.genes` 空被跳过、`capsule_from_dict` 抛错记一条
`restore_failed` 后静默跳过（规则 74 的形态）。

处置：把装配从 `_run_round` 里抽成 `archive.reseed(archive, harness_id,
capsules, n_wanted, round_no) -> (restored, payload, errors)`。抽函数不是
为了好看 —— 内联时**契约构造不出那个合取**，只能靠运气；抽出后三重排除
各自可打。⛔ 函数**不碰 ledger**：失败以 `errors` 返回、由调用方落账 ⇒
单测不需要账本，账本里也不会出现单测伪造的事件。

契约 `contract_archive_reseed_test` 17 条（A 存在性 / B 真取回+往返逐字
相等 / C 三重排除**各配一个正对照** / D 取回≠装配 + 坏快照留痕 /
E 真接回循环）：

- ⛔ 排除类断言一律用**正对照**做反证，不用 monkeypatch：同一条目在换掉
  那个条件后**必须能取回**（C1b / C3b）。若换掉后仍是 0，说明 0 是夹具
  坏了，不是排除造成的。
- ⛔ D1 断言 `n_picked=1` 而 `n_restored=0` 两个数**不同** —— 空壳胶囊能
  被取回但装不回去，如果两个数永远相等，就说明"取回"与"装配"又混成了
  一件事。

口径顺带钉住：**`--archive-resample` 在没有提交的运行里按设计是空转的**
（已写进 `docs/USAGE.md`）。别把"开了开关没反应"读成开关坏了。

## 136. ⛔ `improved` 答的是「有没有提交」，不是「变强了」

`RoundReport.improved` 的定义是 `committed is not None`。r63 加了 `scoped`
之后，一次只改定点技能的提交（实测 `dev=+1.0 ho=0`，held-out 一动没动）
也算 `improved=True`。于是同一句话在三个消费方被读成三种意思：

| 位置 | 它印出来的 | 数据实际支持的 |
|---|---|---|
| `scripts/demo_evolve.py` | 「是否进化：✅ 是」 | 发展集修好了 |
| `scripts/llm_e2e.py` | 「赢过 = improved == True（必须同时过泛化闸）」 | 闸放行了，可能是构造必然的 0 增益 |
| `docs/USAGE.md` | 「有没有候选被接受」 | ✅ 这句是准的 |

⇒ 不是数据错，是**标签承诺了数据没有的东西**。

⛔ **不改 `improved` 的语义**。它是历史口径（生产 120 次提交：110 次
`generalize` / 10 次无裁决），改掉等于让新老读数不可比。处置是**加一层
聚合 + 修标签**：

- `RoundReport.commit_verdict` —— `""` = 没提交；`"no_gate"` = 提交了但闸
  没跑；否则是裁决名。⛔ `no_gate` 不许归空串：空串已经被"没提交"占用
  （第 134 条同族：缺字段不许变成合法值）。
- `evolution.summarize_commits(reports)` → `{total, by_verdict, generalized,
  scoped, no_gate}`，与 `improved` **同一份 reports**、进**同一个字典**。
- 两个脚本读拆开后的数；`demo_evolve` 的「是否进化」那一行删掉。

端到端实测（`--allow-scoped-commit`，qwen2.5:7b，1 轮）：
`improved=True` 而 `commits = {"total": 1, "scoped": 1, "generalized": 0}`
—— 正是旧标签会说成"进化了"的那种情况。

契约 `contract_commit_verdict_test` 15 条（A 缺口 / B 聚合分得开 /
C `no_gate` 与空串不同形 / D 真进输出且脚本改读 / E 生产读数）。
其中 **B3 主动断言 `scoped` 仍算 `improved=True`** —— 它不是实现细节，
是在**保护一个决定**：谁想把语义改成"只有 `generalize` 才算"，这条会先红，
逼出一次显式拍板，而不是悄悄改掉历史口径。

## 6. 与 V5 的关系

V6 不是 V5 的替代品，是它的**结构化重写**：

- 保留并升级的：冻结非回归（→ 准入闸）、红契约 + 反证组（→ `tests/contract_*`）、
  判据同源、写入端完整性检查（`has_embedding` → `Verdict.validate()`）、
  产物归属与去重（→ 内容指纹写入侧去重）。
- 新增的：self-harness 进化闭环、RLVR 奖励内核、内容寻址账本、
  环境优先的任务合成、5 层记忆 OS、外部监督器（跨语言）。
- 明确不做的：不训练权重（那是训练侧的事）、不接真 LLM 作为默认路径
  （默认 stub，保证判据可回归）、不做「允许少量回归换取大增益」的权衡开关。

详细取舍见 `docs/ARCHITECTURE.md`，前沿依据与出处见 `docs/FRONTIER_2026.md`。

## 137. ⛔⛔ 「超过上限 ⇒ 什么都不做」会自锁：契约越多，能验证的模块越少

源码清理的非回归闸要「删掉这段代码后，冻结契约还过不过」。选契约的规则是
`select_contracts`：按 import 关系扫出所有消费方，**超过 `max_contracts`
就返回空**（理由正当：跑前 12 份就宣称通过，等于拿断掉的尺子量东西）。

但契约份数**随项目增长单调上升**，而"被很多契约 import"的模块**恰恰是
核心模块** ⇒ 越核心越早撞线 ⇒ **永远得不到验证**。全仓实测：

| 模块 | 需要份数 | 结果 |
|---|---|---|
| `ledger.py` | 35 | 空 |
| `genome.py` | 31 | 空 |
| `environment.py` | 20 | 空 |
| `evolution.py` | 19 | 空 |
| `nongression.py` / `verifier.py` | 18 | 空 |
| `generalization.py` / `providers.py` | 13 | 空 |
| 其余 39 个模块 | ≤ 12 | 能跑 |

触发点很具体：`providers.py` 原本恰好 12 份，r63k 新增一份契约
（`contract_ghost_route_test`）把它推到 13 ⇒ 全仓唯一一条「可证明」的
提案当场从「能对拍」变成「未验证」。**加契约反而让验收能力下降。**

⇒ 解法不是调大上限（跑两遍，12 份 ≈ 十几分钟，调到 40 等于这道闸永不执行），
而是**把话说准**：拆成 `plan_contracts()`，分开回答「选哪些」与「够不够格」。

- `sampled=False` ⇒ 选全了，通过才是 `verified_ok`
- `sampled=True` ⇒ 只跑了子集，通过只能叫 `verified_sampled`（**仍 unverified**）
- `allow_sample` **默认关**：`autonomy.auto_apply` 会真的往根上写，
  抽样不足以授权落盘 —— 抽样只出现在出报告的路径

## 138. ⛔⛔ 计数只算「真跑过的」，不算「打算跑的」

同一处还有一条独立的谎：`probed += 1` 写在 `if sel:` **之前**，于是
「选不出契约」（跑不了）也记成"探过一条"。实测读数
`n_probed=1 / runner_calls=0` —— 外部读到的是意图，不是动作。

⛔ 这与第 85 条（状态名叫 `applied` ≠ 做了）、第 14 条（写入成功 ≠ 可用）
同族：**计数点必须落在动作之后的唯一那一支里**，不在分支入口。

配套：抽样读数（`n_contracts_run` / `n_contracts_total` / `sampled`）必须
落在 `evidence.nonregression` 里 —— 只写"未通过"会让「跑了但没过」与
「一份都没跑」不可分辨。

## 139. ⛔⛔ 净化子进程环境是对的，但**数据根不是秘密** —— 它会退回 `__file__`

`CliAdapter` 故意不继承宿主环境（`env_allow` 只有 PATH/HOME/LANG/TMPDIR），
这是对的。但外部实现（`scripts/rea_bridge.py`）需要知道「证据该写哪」，
而它唯一能拿到的是环境变量 ⇒ 不给就只能退回

    ROOT = Path(__file__).resolve().parent.parent

—— 这是**代码位置**，不是**数据位置**（Y109 换了个地方复发）。实测：
`aether caps --root /tmp/ws invoke rea.doctor` 把账写对了，却把 7 个证据
文件写进了**生产仓库**的 `data/rea/`；装成 wheel 后会写进
`site-packages/data/rea`。

修法三处，缺一不可：

1. 桥改用 `paths.resolve_root()`（① --root ② `$AETHER_ROOT` ③ 仓库
   ④ 向上找 `.aether` ⑤ 用户数据目录），**不另写一份顺序**；
2. `CliAdapter.env_extra` —— 显式注入，不走白名单（是我们算出来的，不是继承的）；
3. `mount_capabilities(..., root=)` —— **唯一接线点**，CLI / HTTP / doctor
   都经过这里；`root=None` ⇒ 走 `resolve_root()`，不许"什么都不给"。

契约 C6 判据是**两侧都数**：临时根下必须 ≥1、生产仓库必须**零增长**。
反证：桥退回 `paths(ROOT)` ⇒ C6 立刻红。

## 140. ⛔⛔ 「提交」不等于「变强」：同一个状态被记了 105 遍

`data/ledger.jsonl` 的纯诊断读数（`novelty.blocked_replay`，只读）：

| 读数 | 值 |
|---|---|
| `harness.commit` | **120** 条 |
| 不同 `(parent_id, capsule)` | **12** |
| 重复提交**已存在状态** | **105（87.5%）** |
| 去重后真实状态 | **18** |
| 单条最狠 | 同一父 + 同一胶囊 **57 遍** |

账本写"提交 120 次"，事实是"产生了 12 个新状态"。看账本的人会以为自己
变强了 120 次 —— 这是**读数撒谎**，与第 85 条（状态名叫 `applied` ≠ 做了）
同族，但更隐蔽：这一次**每一次都真的执行了**，只是执行出了同一个结果。

### 为什么三层都没拦住

| 层 | 为什么拦不住 |
|---|---|
| `archive.add` | 有重复检查，但在**闸之后**（浪费已发生），且只去重"已入档的" |
| `_in_cooldown` | 只管"**被拦过的**"；提交成功的恰恰不在它视野里 |
| 提议器 | 每个胶囊只在"不在路由上"时才提；提交后路由变了 ⇒ 它认为"这条已用完" ⇒ 换基线再跑又回到同一个起点 |

根因是**每一次运行都从同一个基线重爬**（`cli.py` 里那个裸种子
`Harness()`）。`--warm-start` 修的是"下次从哪开始"，但它是**默认关**的
实验口径；而且即便开了，同一轮内的重复也没有任何东西管。

### 判据：内容地址，不另算指纹

    harness_id = 基因组的内容哈希

同一份基因组 ⇒ 同一个 id ⇒ **同一个状态**。不另做"胶囊签名"，因为签名
必须回答"rationale 算不算""基因顺序算不算"，而这两个答案都会与内容寻址
不一致（第 99 条：唯一事实源）。白捡的收益：**两条不同名字、不同
rationale 的胶囊，只要落点相同就被同一个判据拦住**（契约 A6）。

三种判定，只有两种算重复：

- `no_change`（子状态 == 父状态）⇒ 拦。空变异不产生状态。
- `exists`（子状态在账本里）⇒ 拦。重试的结果由内容寻址定死。
- `apply_failed` ⇒ **不拦**，回原路径。那是"候选坏了"，另一个事实。

### 位置：必须在闸之前

`apply_capsule` 是纯函数（无 runner、无 IO）⇒ 在它之后立刻判是**免费**的；
一旦进了闸，非回归闸 + 发展集 + held-out 的成本已经付掉。契约 C3 的判据
是"被拦的候选不产生 `gate.result`"——不是"少写一条账"。

### 不许把两种"没做事"合并

全轮都被守卫拦下时，`revert_reason` 是 `no_novel_state(...)`、`idle=True`，
事件是 `evolution.no_novelty`。⛔ 不许写成 `no_candidate_passed`（那是
"试了但不够好"，值得换招）或 `generalization_gate`（那是"证据不足"）。
混在一起会把"没有新东西可做"读成"候选质量差"——两者修法完全相反。

### 契约

`contract_novelty_test` 23 条（A 判据 / B 事实源与增量等价 / C 真接线 +
开关有牙 / D 诊断只读 / E 纪律）。四条注入反证全部命中：判据恒空 ⇒
A3/A6/C2/C4/C5 红；不吃 `parent_id` ⇒ B1 红；`seen` 恒 None ⇒
C2/C4/C5 红；原因改写成 `no_candidate_passed` ⇒ C5 红。文件逐字还原。

## 141. ⛔⛔ 数据根与代码根分开之后，**读的一方**也要认同一个根

r61 把「代码在哪」与「数据在哪」拆成两件事（第 109 条），r63k 修了
**写**的一侧（桥把证据写进生产仓库）。r64 实测发现**读**的一侧还没修：

```
scripts/rea_bridge.py:  "path": str(fpath.relative_to(ROOT))
                                        ^^^^^^^^^^^^^^^^ 代码根
```

数据根一旦与代码根不同（`$AETHER_ROOT` / `--root` / 装成 wheel），这一行
直接抛 `ValueError: '.../data/rea/x.json' is not in the subpath of '/Users/…/V6'`
⇒ **整条能力 rc=1**，而报错只说"不在子路径下"，看不出是路径口径错了。

实测触发链：给契约的子进程注入 `$AETHER_ROOT=<临时目录>`（为了让验收
不写生产），`doctor` / `analyze` / `capabilities` 三条当场全崩。

⇒ 判据统一成「相对 `_data_root()`」，并且**必须带兜底**：

    try:    return str(Path(p).relative_to(_data_root()))
    except (ValueError, OSError): return str(p)

⛔ 兜底不是"和稀泥"：读数难看（绝对路径）比整条能力崩掉好（第 97 条：
   返回值语义要能区分"没读到"与"坏了"）。

⛔ 契约侧同样要认同一个根：`full_output.path` 是**相对数据根**的，
   用 `ROOT / path` 拼一律 `is_file()=False` ⇒ 六条假红（B4 / G5–G10）。
   ⇒ `_evidence_path()` 统一拼到 `$AETHER_ROOT` 上。

### 附带：验收写生产这件事，必须**整份契约**判一次

r63k 的 C6 只钉住「C 组那一次 `--root` 调用」，而 A 组是**直接调桥**、
不经 Gateway ⇒ 绕过 `--root` 注入 ⇒ 每次验收仍往生产 `data/rea/` 写
6 个文件（实测 2026-10-21 21:42：analyze / doctor / capabilities /
inspect_android×2 / analyze_javascript 各一个）。

⇒ 修法两件：
1. `run_bridge` 给子进程 `AETHER_ROOT=<临时目录>`（② 优先级高于 ③「仓库」）；
2. 契约末尾加 `Z1`：**整份跑完，生产仓库 `data/rea/` 零增长**。

判据必须在**所有组之后**：放在某一组里只能证明"那一次调用没写"。
反证：去掉 `AETHER_ROOT` 注入 ⇒ Z1 立刻红（B4/G5–G8/G10 一起红）。

---

### 152. ⛔⛔ **「能修自己出的题」与「能修真实 bug」之间隔着一整个判据形态**（2026-10-24）

这一天做了一件早该做的事：把**真实开源仓库的真实 bug** 喂给 V6，看它修不修得动。

═══ 实验怎么搭（关键：不用改任何产品代码）═══

`EvolutionLoop.dev_suite` 是**显式注入**的 `List[FrozenTask]`，而
`FrozenTask.spec` 有 `broken_source` 字段 ⇒ 直接把真实 bug 源码喂进去即可。
其余全用真实组件（真 runner / 真非回归闸 / 真提议器 / 真 loop）。
装置在 `scripts/realbug_{import,experiment,batch}.py`。

| | 来源 |
|---|---|
| bug | 真实仓库 git 历史里某个 bug fix commit 的**父提交**源码 |
| 参照修复 | 人类作者当时的真实修改（不告诉系统） |
| 判据 | 该仓库**真实 pytest** 里真实存在的断言，expect 由真实源码跑出来校准 |

═══ 读数 ═══

**12 个真实 bug fix（boltons），只有 1 个能被这套判据形态表达；那 1 个没修好。**

- 唯一进入评测的那个（`5eb6cc0`，`Bits` 负索引）：起跑 24/26，
  跑满 **10 轮**后仍是 **24/26**，失败的两条一字未变；
  5 个通用修法胶囊**全部 reward=0.0**，0 提交，2 轮后就 `saturated`。
- **对照组**（同装置、同胶囊，只换题源）：`synthesize_tasks` 自造的变异题
  ⇒ **第 1 轮 `fix_control_flow` reward=1.0 并提交**。
  ⇒ 装置是好的，差别只在"题是不是自己出的"。

⛔ **11/12 根本进不了评测**，这比"0/1"更根本：不是修不修得好的问题，
是这套机器能表达的题**本来就不是真实 bug**。三类原因逐条不同：

1. **判据形态吃不下"期望异常"**：`{"call","expect"}` 只能表达返回值比较。
   `c1c25da`（off-by-one `>`→`>=`）的测试是 `with raises(ValueError):` ⇒ 无解。
2. **真实断言依赖上下文**：`bits.as_bin()` 里的 `bits` 是循环变量、
   `clamp(INF)` 里的 `INF` 是模块级常量 ⇒ 单拎表达式出来必然 raise。
   判据要求"纯表达式可独立求值"，真实测试没这个约束。
3. **参照实现上就跑不通的探针要先剔除**：它们在两版上"一致地失败"
   ⇒ 会被误判成"无鉴别力" ⇒ 整个真 bug 被报成不可解。

═══ 途中抓到的四个真问题（都比结论本身更值钱）═══

1. **不可复现的判据**：返回值是对象时 got 是 `Bits('1')` 这种构造函数形式，
   `ast.literal_eval` 解不了。第一版 `_literal` 的 `except` 里 `return got`
   ⇒ **丢弃分支永远走不到** ⇒ 12 条随机判据被当成有效判据。
   修法：降级为 `repr(...)` 包装（先验过 `__repr__` 与 `__eq__` 同判），
   并**回头验一遍**参照实现全通过才放行。
   ⛔ 「同一源码跑两遍」**抓不到**这一层：repr/地址在同源下稳定，跨源码才变。
2. **`TemplateProposer(pool)` 会把整个技能 dict 当成 `name` 吃掉**
   （第一个位置参数是 `name: str`，字段名是 `skills`）⇒ skills 空 ⇒ 只提 `noop`
   ⇒ 表现得像"系统提不出修法"。`cli.py:1089` 用的就是关键字 ⇒ **产品是对的**。
   ⛔ 差点把自己的笔误写成产品缺陷 —— 下结论前先查产品那一侧怎么传的。
3. **`None == None` 为真**：批量汇总里运行失败的项三个字段全 `None` ⇒
   被判成"✅ 修好"。第 135 条的老毛病，加了 `_readable()` 闸。
4. **真实数据把账本的跨语言闸撞出来了**：`Bits.from_int` 系列产生 1e101 的
   expect ⇒ `ledger._norm` 拒绝写账本（TS 侧 `JSON.parse` 会丢精度 ⇒ 两端
   hash 不同 ⇒ 哈希链断）⇒ 整个 run 崩。⛔ **闸是对的**（它指名了路径、原因、
   修法），该做的是别把这种值当判据 —— 大整数相等比较在这个尺度上本就无意义。

═══ 结论（这条是给"做下去有没有意义"这个问题的最新读数）═══

V6 在自己造的世界里 121 次提交全部"成功"，而**进入真实任务域的第一天**，
样本就是 0。并且暴露的不是"能力不够"，而是**表达能力不够**：
真实 bug 的判据里有一半是"期望抛异常"，这套形态根本没有对应的槽位。

⇒ 下一步不该是继续加固验证机器（它已经过剩），而是一座桥：
让判据形态多一个"期望异常"的槽位，然后再谈修不修得好。

### 153. ⛔⛔ **修好了一个真实 bug —— 同时暴露出「判据看不见」的补丁噪声**（2026-10-25）

152 条的结论是「12 个真实 bug，0 个修好」。那一天留下的疑问是：这到底是
**V6 不行**，还是**我挑的样本恰好都不在它的射程内**。本条把它钉死。

═══ 一、形态普查：真实修复与 V6 修法族的射程几乎不相交 ═══

`scripts/realbug_shape_census.py`（新）。不猜，逐个 commit 取父版本与本版本的
同一源文件，**解析成 AST 逐节点比形状**，分成两类：

| 形态 | 含义 | V6 五个 `revert_*` 够得着吗 |
|---|---|---|
| `operator` | 树形同构，差异只在算子类型 / 常量值 / `not` 增删 | 够得着 |
| `structural` | 树形不同构（语句增删、改分支结构、换算法） | **够不着** |

⛔ 同一把尺子先量 V6 自己的出题器（对照组），否则无法判断是尺子错还是世界错：

| | 样本 | operator | structural | other | unparsed |
|---|---|---|---|---|---|
| **真实 bug fix**（boltons，73 个） | 73 | **7（9.6%）** | **62（84.9%）** | 3 | 1 |
| 其中真正改算子的（剔除只改消息文本的） | | **4（5.5%）** | | | |
| **V6 `synthesize_tasks` 自造题** | 21 | **21（100%）** | 0 | 0 | 0 |

⇒ **V6 的题源 100% 落在算子轨道上，真实 bug 修复只有 5.5% 落在那条轨道上。**
这不是采样偏差：修法族（`aether/repair.py` 的 5 个 `revert_*`）从一开始就是
V6 自己那批变异算子的**对称逆运算**（该文件注释逐字写着"加一个变异维度，
必须同时加它的对称修法"）⇒ 题源与修法族同源 ⇒ 在自己世界里必然自洽，
出了那个世界必然失配。**121 次成功提交是被这个同源性担保的。**

⛔ 量具自检救了这次结论：第一版 `_same_shape` 用 `ast.iter_child_nodes`，
而 `ast.Compare` 的比较符（`Gt()` / `GtE()`）**本身也是子节点** ⇒
`a > b` vs `a >= b` 被判成"结构级" ⇒ 算子级修复会被**系统性误判**成不可达
⇒ 结论会偏悲观而无人察觉（第 24 条：某维度恒为一个极端值，先怀疑量具）。
自检第 2 条就是为它准备的，跑出来后才修：比形状时剔除算子节点。

═══ 二、形态一旦对上，第 1 轮就修好了 ═══

普查筛出的算子级真实 bug 里，`93185b2`（boltons #302，`daterange` 在
start == stop 时无限递增）能被物化：

```
-    elif start < stop:
+    elif start <= stop:
```

⛔ 它的判据**不是自动提取的**：新增测试全是 `pytest.raises(StopIteration)`
（152 条记过的"期望异常"形态），自动抽取拿不到有鉴别力的断言 ⇒
给 `realbug_import.py` 加了 `--probe`（可重复）—— **expect 仍然由参照实现
真跑取**（第 1 条纪律没破），变的是"探针从哪来"。注入的那条：

    len(list(daterange(date(9999,12,25), date(9999,12,25), inclusive=True)))
      fixed=1    buggy=OverflowError（无限递增到 date 溢出）

物化结果：7 条判据，**1 条有鉴别力**。跑 `realbug_experiment.py --mode real`：

| | 起跑 | 轮 1 | 最终 |
|---|---|---|---|
| **真实 bug** | 6/7 | `add_skill:fix_control_flow` reward=1.0 **提交** | **7/7 ✅** |
| 对照组（自造变异题） | 2/7 | `add_skill:fix_literals` reward=1.0 提交 | 7/7 ✅ |

⇒ **V6 修好了一个真实开源 bug**，用的就是它自己那套闸（非回归闸 + 泛化闸 +
能力地板），没开任何后门。152 条那句"0 个修好"就此**更正**：真实原因是那
12 个样本里唯一能进判据的那个（`5eb6cc0`，新增两行语句）恰好是**结构级**。

⛔ 但这是 **n=1**，且判据有一条是人选的 ⇒ 不许说成"V6 能修真实 bug"。
准确说法是：**在形态匹配、判据可表达的真实 bug 上，截至目前 1/1。**

═══ 三、⛔⛔ 修法产出是 `ast.unparse` 全量重写 —— 判据永远看不见它 ═══

把修后源码与人类作者的真实修复逐字比（同一个 `93185b2`）：

    >>> revert_control_flow(buggy, fixed) == fixed
    False

逻辑**改对了**（`elif start < stop:` → `elif start <= stop:`），但同时：

| | 人类真实修复 | V6 产出 |
|---|---|---|
| 行数 | 581 | **468** |
| 文件头 license 注释（32 行） | 在 | **被删** |
| 所有空行 / 行内注释 | 在 | **被删** |
| 字符串引号、括号、`divmod((a+b), 12)` | 原样 | **被 `ast.unparse` 重写** |

根因：`aether/repair.py` 五个 `revert_*` 的**最后一行都是 `return ast.unparse(b)`**
—— 它们返回的是"修改后的 AST 重新打印"，不是"对原文本的最小编辑"。

⇒ 这个补丁在真实代码库上**不可提交**。而它的判据只看行为 ⇒ 7/7 全绿 ⇒
缺陷**完全隐形**。更糟的是：V6 自己的题源也是 `synthesize_tasks` 用 AST 往返
造出来的 ⇒ 题的源码本身就是 unparse 过的 ⇒ **格式一致，噪声无从显现**。
（这正是第 24 / 133 条的形状：量具看不见的缺陷，因为被测物与量具同构。）

⛔ 修它的方向（未做，需拍板）：改成"只替换目标节点的源码片段、其余保留原
文本"，或产出 unified diff 而非整份重写。⛔ 不要直接删 `unparse` —— 五个
`revert_*` 的"按遍历序对齐"依赖它输出的确定性，改法要配契约。

═══ 四、我自己又踩了一次量具坑（记下来，别再踩）═══

查"UCI 通道有没有接线"时用了 `grep -rn "register_capability\|Gateway("`
⇒ 空 ⇒ 差点写下"UCI 零接线"。**本机 grep 是 toybox 的 BRE，`\|` 不是"或"
而是字面竖线**，那条命令搜的是一整个不存在的字符串（第 133/134 条同一个坑，
第二次踩）。用 `grep -E` 或分两次查重测：

- ✅ `mount_capabilities` **有**接线：`cli.py:1682` / `serve.py:996` / `doctor.py:200`
- ❌ 但 `HarnessAgent(gateway=...)` 只在 `tests/` 与 `scripts/probe_uci_e2e.py`
  出现；生产构造点 `cli.py:650` **不传 gateway** ⇒ 外部能力（含
  `capabilities.example.json` 里的 `ext.repairer`）**进不了进化闭环**。
  ⇒ 与 REA 同形：协议 + 契约 + 清单齐全，缺的是把它接进主链路的那一行。

═══ 结论（对"做下去有没有意义"的最新读数）═══

152 条说"隔着一整个判据形态"，只对了一半。完整的是**三道串联的门**，
73 个真实 bug 逐道过滤下来，进入评测的期望数是它们的乘积：

1. **形态门**：84.9% 的真实修复是结构级 ⇒ 修法族够不着（剩 ~9.6%）
2. **判据门**：算子级里 F2P 大量是"期望异常"（`raises` / `pytest.raises`）
   ⇒ 判据形态没有槽位 ⇒ 再过滤（`c1c25da`、`93185b2` 都卡在这，后者靠手写探针绕过）
3. **上下文门**：断言依赖循环变量 / 模块级常量 ⇒ 单拎出来求值不了
   ⇒ 再过滤（`64e6430` 一次剔除 31 条）

三道都过去 ⇒ **第 1 轮修好**（本条实测）。所以它不是"行/不行"的二元问题，
而是**适用域**：真实且存在，但很窄（73 个里 4 个，5.5%），并且修出来的补丁
目前**在真实工程里不能用**（第三条）。

⇒ 想让它变宽，三个门的代价完全不同：①最贵（要引入能写任意代码的能力，
唯一合法入口是 UCI —— 而它还没接进闭环）；②最便宜（加个"期望异常"槽位）；
③中（spec 带 preamble）。别一上来挑最贵的那个。

### 154. ⛔⛔ **修法产出改成最小编辑：补丁终于能提交了**（2026-10-25）

153 条留下一个「判据看不见」的缺陷：五个 `revert_*` 以
`ast.unparse(整棵树)` 收尾 ⇒ 真实 bug `93185b2` 上逻辑改对了，产出却与人类
真实修复**逐字不一致**（581 → 468 行，license 头 / 注释 / 空行 / 引号全丢）
⇒ 补丁**不可提交**，而判据只看行为 ⇒ 7/7 全绿。本条把它修掉。

═══ 一、改法：渲染层只替换变了的那一小段 ═══

新增 `aether/repair.py::render_patch(broken, base, mutate)`，五个修法改成
「写变异逻辑 + 交给渲染层」。渲染层：

  1. 变异**之前**对原树拍快照（每个节点的浅层签名 `_sig`：自身字段 + 直接
     子节点的 **id** ⇒ 后代内部的改动不会污染祖先的签名）；
  2. 变异后找出**最深的**一批变了的节点；
  3. 每个只替换它在原文本里的那一段；未变的子节点一律用**原文本**；
  4. ⛔⛔ **渲染后自检**：`ast.dump(parse(结果))` 必须等于 `ast.dump(m)`，
     不等 ⇒ 立刻退回整份 unparse。**宁可丢格式，不可丢语义。**

⛔ 第 4 步不是可选项。渲染层写错 ⇒ 静默产出**语义不同**的补丁，这比格式
噪声坏得多；而它又不可能靠肉眼看出来 ⇒ 必须有内建的 fail-closed。

同一 `93185b2` 修后：

    revert_control_flow(buggy, human_fix) == human_fix   ⇒ True
    581 行 → 581 行，unified diff 只有：`- elif start < stop:` / `+ elif start <= stop:`

═══ 二、语义回归：**120 组对拍，0 分叉** ═══

`scripts/probe_render_mode.py`（新）。把 `render_patch` 换成旧行为，逐环境
（14）× 题 × 修法（5）跑同一批变异样本，对拍 **AST** 与 **`verify_source`
的 reward**（ABBA，第 71 条）：

    语义（AST）不一致：0      行为（reward）不一致：0
    新旧文本不同：0/120      ⇒ V6 自己的语料上两种渲染**逐字相同**
                              （合成题的源码本来就是 unparse 过的）
                              ⇒ 回归风险 ≈ 0，收益只在真实源码上显现

═══ 三、我自己踩的三个坑（都已被量具/自检抓到，记下来别再踩）═══

 1. **算子包装节点没有位置**。`ast.walk` 会走到 `cmpop` / `operator` /
    `boolop` / `unaryop`，它们也是 `ast.AST`，但**没有 lineno/col_offset**，
    且每次改 op 都是**新建对象** ⇒ 被当成"新增节点" ⇒ 成了"最小改动" ⇒
    定位不到 ⇒ **全部 fallback**。实测第一版：五个维度**一个都没真正生效**
    （表面全绿，因为 fallback 的输出仍然语义正确）。⇒ 可渲染单元必须是
    `expr` / `stmt`（parse 出来一定有位置）。**第 24 条**：五个维度同时
    "生效了却又没生效"，先怀疑量具。

 2. **量具的基准选错了**。第一版对拍测的是「离**缺陷版**源码多远」，而合成题
    的缺陷版本身就是 `mutate_source` 用 `ast.unparse` 造的 ⇒ 旧渲染与它天然
    逐字相同 ⇒ 这把尺子**恒偏向旧行为**，一量就是"新渲染更差 3 组"。正确的
    基准是**人类基线** `env.source`。⇒ 一把尺子恒站一边时，先怀疑尺子。

 3. **子节点的分组括号被吃掉**。节点的 `col_offset` 指向括号**里面**
    （`(not x)` 的 UnaryOp 从 `not` 起）⇒ 只按节点区间取原文本 ⇒
    `A and (not x)` 变成 `A and not x`。这里能丢、那里就可能丢出语义差别
    （`A and (B or C)` ⇒ `A and B or C`）。⇒ 渲染**中缀/前缀算子的子节点**
    时要把它紧邻的成对括号一起带上（那种位置的括号一定是分组括号，
    不像 `f(x)` 里属于父节点）。

═══ 四、契约 + 注入验证 ═══

`tests/contract_repair_render_test.py`（新，37 项）：

  A 五个修法在**非规范** fixture（license 头 / 行内注释 / 空行 / 双引号 /
    分组括号）上修完必须与基线**逐字一致**
  B ⛔ 反证：换回旧渲染 ⇒ A 必须转红（B1），且旧渲染真的会删 license 头、
    真的会改行数（B2/B3）⇒ A 组不是恒真断言
  C 新旧渲染的 AST 必须相等（渲染只许动格式）
  D fail-closed：mutate 拒绝 / 抛异常 ⇒ 原样返回；定位不到 ⇒ 回退整份
  E 补丁只允许落在 **1 行**上

⛔ **真注入验证（不是靠 B 组的 monkeypatch 推断）**：把产品代码临时改成
「强制旧行为」⇒ 契约**大面积转红**（A2–A5、E 全组）⇒ 复原 ⇒ 全绿。

═══ 五、残留（如实记录）═══

  · 认不出来的节点类型（模板表只覆盖 Compare / BinOp / BoolOp / UnaryOp /
    Constant）⇒ 退回 `ast.unparse(那一个节点)`：仍比整份重写好，但那一段
    的格式会变。
  · `ast.arguments` 之类的非 `expr`/`stmt` 改动 ⇒ 定位不到 ⇒ 回退整份
    （有对拍兜底：真变了就走 unparse，不会静默丢改动）。
  · 多行替换按 `col_offset` 补缩进；三引号 docstring 会被压成单行引号串
    （语义不变，自检会兜住）。
  · ⛔ **`mutate_source` 自己也在 unparse**（`environment.py:1324`）⇒ 合成题
    的源码仍是规范化的 ⇒ 题面本身不像真实代码。那是**题源**的问题，与本次
    渲染层改动无关，未动。

### 155. ⛔⛔ **出题器也改成最小编辑：题面终于像真实代码了**（2026-10-25）

154 条把**修法**的产出改成最小编辑，但留了一句残留：「`mutate_source` 自己也
在 unparse（`environment.py:1324`）⇒ 题面本身不像真实代码」。本条把它收掉。

⛔ 这不是"顺手美化"，而是一道**结构性的遮蔽**：题面与 `ast.unparse` 同构 ⇒
154 条那种"补丁把 license 头 / 注释 / 空行 / 引号全抹掉"的缺陷，在 V6 自己的
语料上**永远不显现**（第 24 / 133 条：量具与被测物同构 ⇒ 缺陷落在量具盲区）。

═══ 一、改法：出题器复用 154 条那个渲染层 ═══

    mutate_source(source, which)  →  render_patch(source, source, _mutate)

`base` 与 `broken` 传同一份（变异的"基线"就是原文自己）。五个变异维度
（compare / binop / constant）不变，变的只是**渲染**。

⛔ 保留 `ast.fix_missing_locations`：原实现在 `visit` 之后有这一步，渲染层
内部不做 ⇒ 少了它就与原行为不同。变异会新建节点（`ast.Not()`、新 `cmpop`），
补位置是原语义的一部分。

⛔ 只能**函数内** `from .repair import render_patch`：`repair` 依赖本模块，
模块级导入会成环。

实测（boltons `mathutils.py`，251 行）：

| | 行数 | license 头 | 注释 | 空行 | 双引号 |
|---|---|---|---|---|---|
| 人类源码 | 251 | 在 | 在 | 在 | 原样 |
| 旧题面 | **212** | **被删** | **被删** | **被删** | **被重写** |
| 新题面 | **251** | 在 | 在 | 在 | 原样 |

═══ 二、⛔⛔ 真正要防的是"题面变了 ⇒ 题还是不是题" ═══

判据是在题面上**真跑**出来的（`verify_source` 执行源码）。"只改渲染不动语义"
是推断，不是证据。新 `scripts/probe_mutate_render.py`：

    14 个环境 × which 0..11 = 116 组变异点对拍
      一边报错一边成功  0     AST 不一致  0     desc 不一致  0
      新题面离【人类基线】更近 116 / 持平 0 / 更远 0
    合成任务判据对拍 16 组：结果不一致 0，题面离基线更近 16 / 更远 0

⇒ 语义零分叉、鉴别力零分叉、格式 100% 改善。

═══ 三、我自己踩的两个坑（都被抓到，记下来别再踩）═══

 1. **恒真断言**：`verify_source` 的真实签名是 `verify_source(env, source=...)`，
    我写成 `verify_source(src, t)` ⇒ 每条都抛 `AttributeError` ⇒ 新旧签名都是
    一串 `ERR` ⇒ "结果一致" **恒真**，16 组全绿却什么都没测到。
    ⇒ 签名里必须带上 **checks 条数与通过数**，空了直接判失败（第 9 / 133 条）。

 2. **判据挑错了对象**：D 组第一版写"每个环境必须出题 > 0"，`_inspect` 红了。
    对拍后确认：**新旧出题器在 `_inspect` 上都出 0 道**（有变异点，但变异后
    判据无鉴别力 ⇒ 被 `synthesize_tasks` 滤掉）—— 那是环境本身的属性，
    **与本次改动无关**。写"必须 > 0"会对着一个既有事实报错。
    ⇒ 正确的判据是 **ABBA：出题数与旧出题器相等**（第 71 条），不是绝对值。

 3. ⛔⛔ **全量验收抓到一个真回归（我自己造的，本地契约一份都没红）**：
    `mutate_source` 现在要走 `source.splitlines()`，**只接受 str**；而旧实现
    的 `ast.parse(source)` 底层是 `compile()`，`compile()` **接受 AST** ⇒
    传 `ast.Module` 一直能用。`contract_aging_test.py:159` 与
    `contract_boundary_test.py` 的 group_b/c/f 就是这么调的 ⇒ 3 处全崩
    （`AttributeError: 'Module' object has no attribute 'splitlines'`）。

    ⛔ **不许为了迁就新实现去改那 3 处判据** —— 它们依赖的是既有行为，
    改判据等于把回归藏起来。修法是在入口处归一化：`not isinstance(source, str)`
    ⇒ `ast.unparse(source)`（AST 里本来就没有注释/空行/引号，这一步不存在
    "丢格式"）。并钉成契约 **F 组**（传 Module 不抛异常 / 产出与传 str 的
    AST 相等 / desc 一致），⛔ 真注入验证：去掉归一化 ⇒ F1 立刻转红。

    ⇒ 教训：一个函数**文档上写着 `source: str`**，不等于调用方都传 str。
    `compile()` 那类宽松兼容会养出一批"文档上说不通、实际能用"的调用点
    （第 144 条的形状：动手前先查是不是已有契约/既有行为钉死了这条）。

═══ 四、契约 + 真注入验证 ═══

`tests/contract_mutate_render_test.py`（新，58 项）：

  A 非规范 fixture 上变异 ⇒ license 头 / 注释 / 空行 / 双引号全在、行数不变
  B ⛔ 反证：旧行为**真的**会删 license 头、改行数、删注释、把双引号改单引号
    （B1–B4），且新旧产出确实不同（B5）、新行为满足 B1–B4 的反面（B6）
  C 新旧题面 AST 相等
  D ⛔ **鉴别力**：题面格式变了之后判据仍必须"缺陷版失败 / 基线通过"，
    且出题数与旧出题器一致（ABBA），且空分母判失败
  E 整份题面只允许一处改动
  F ⛔ **向下兼容**：`mutate_source` 必须同时接受 str 与 `ast.Module`
    （全量验收抓到的真回归，见上）

⛔ **真注入验证**：把 `mutate_source` 临时改成强制旧行为 ⇒ **20 项转红**
（A 全组 + E 全组）⇒ 复原 ⇒ 全绿。3.13 与 3.9 双版本全绿。

═══ 五、这条改动的真正意义 ═══

154 条让**补丁**能提交了；155 条让**题面**像真实代码了。两者合起来才把 153 条
那个盲区真正关掉：

- 之前：题面是规范化的 ⇒ 修法产出也是规范化的 ⇒ 两者**格式一致** ⇒
  "补丁噪声"无从显现 ⇒ 87 份契约一份都碰不到它。
- 现在：题面保留 license 头 / 注释 / 空行 / 引号 ⇒ 修法若再整份 unparse，
  判据视野里立刻出现一个 16 行 → 9 行的 diff。

⛔ 但要注意分寸：**这没有让 V6 变宽**。形态门（84.9% 的真实修复是结构级）
和判据门（`raises` 无槽位）都还在原位。这条做的是让"已经能修的那一类"产出
可用、且让缺陷**可见**。

═══ 六、收尾：重跑 154 条那个对拍（题源变了，旧结论必须重验）═══

154 条的 120 组对拍是在**旧题面**（规范化）上跑的。题源改了 ⇒ 那个结论
必须重跑，不能继承（第 70 条）。`scripts/probe_render_mode.py` 重跑：

| 读数 | 154 条时（旧题面） | 155 条后（新题面） |
|---|---|---|
| 语义（AST）不一致 | 0 | **0** |
| 行为（reward）不一致 | 0 | **0** |
| 新旧文本不同（**最小编辑真的生效**） | **0 / 120** | **120 / 120** |
| 新渲染离基线更近 / 更远 | — | **120 / 0** |

⇒ 语义结论**不变**（零分叉），但**最小编辑的生效面从 0 组变成 120 组**。
这正是 155 条论点的量化证据：之前题面与 `ast.unparse` 同构 ⇒ 修法的格式
噪声在 V6 自己的语料上**一组都显现不出来**；题面一保留真实格式，120 组
全部显现。

⛔ 另一个读数要如实记：`新渲染产出过改动 24/120` —— 只有 24 组修法真的动手
（其余 96 组因树形不匹配而拒绝，这是五个 `revert_*` 既有的 fail-closed 行为，
与本次改动无关）。

### 156. ⛔⛔ **判据门打开：加【期望异常】槽位 —— 一个此前完全不可解的真实 bug 变成第 1 轮修好**（2026-10-26）

153 条记了三道串联的门（形态 / 判据 / 上下文），155 条收尾时留了代价排序：
「判据门最便宜」。本条把它做掉，并给出**这道门值多少**的实测读数。

═══ 一、缺口：F2P 大量用异常表达，而判据只有两支 ═══

判据求值在 `aether/environment.py` 的沙箱 runner 里，原来只有两支：

    if "expect" in t:   ok = (got == t["expect"])     # 返回值相等
    else:               ok = bool(got)                # truthy

⇒ 没有"期望抛出指定异常"这一支。而**边界条件类的真实修复大量用异常表达**
（`pytest.raises` / `with raises(...)`）⇒ 这类 bug 根本进不了判据。

═══ 二、改法：加第三支 `raises`，⛔ 但 `why` 的归属才是关键 ═══

```json
{"name": "neg", "call": "Bits", "args": [4, 2], "raises": "ValueError"}
```

⛔⛔ **不新增第四类 `why`** —— 三类口径是**尺子**，为迁就新功能改尺子会让
既有读数不可比（第 83 条）。失败时映射进既有三类，而这个映射**决定这道题
是强题还是会吹胖读数的弱题**（`classify_failure` 注释明写：crash 类"任何
把源码改回基线的修法都能顺手修好 ⇒ reward 里完全看不见"）：

| 情形 | why | classify | 性质 |
|---|---|---|---|
| 抛对了期望的异常 | `behavior` | behavior | 通过 |
| **该抛没抛** | `mismatch` | **behavior** | ✅ **强题**（函数跑完了，只是"该抛没抛"，不是崩溃）|
| 抛了别种类型 | `raise` | crash | 弱题（崩溃）|

⇒ 关键设计：**"该抛没抛"必须归 `mismatch`**。若它被归成 crash，那用 raises
造出来的题全是弱题 —— 那还不如不加这一支。契约 B3 专门钉这一条。

⛔ 异常名走**受控词表**（`builtins` 里的 `BaseException` 子类），**绝不 eval**：
`raises: "__import__('os').system"` 只是查表失败（`bad raises spec`），
不会被当成表达式执行。

═══ 三、⛔ 实测：这道门值多少 ═══

`c1c25da`（boltons，`Bits` 长度检查 off-by-one，`val > 2**len_` → `val >= 2**len_`，
**纯算子级 ⇒ 形态门过得去**，之前卡在判据门）：

| | 加槽位前 | 加槽位后 |
|---|---|---|
| 有鉴别力的判据 | **0 条** ⇒ 完全不可解 | **2 条**（`Bits(4,2)` / `Bits(1,0)` ⇒ raises ValueError）|

闭环结果：

    起跑 23/25（失败的正是那 2 条 raises 判据）
    轮 1：add_skill:fix_control_flow  reward=1.0  **提交**
    最终 25/25 ✅

补丁与人类真实修复**逐字一致**（249 行不变，diff 只有 1 行）。
对照组（同装置、自造变异题）同样轮 1 修好 ⇒ 装置没变魔术。

⇒ 这是**第二个被修好的真实开源 bug**，也是第一个"靠打开一道门才变得可解"的。

═══ 四、⛔ 诚实读数：4 个里只成了 1 个，门是**串联**的 ═══

把形态普查筛出的 4 个算子级真实 bug 全部重跑物化：

| commit | 形态 | 结果 |
|---|---|---|
| `c1c25da` | op（Gt→GtE） | ✅ **2 条有鉴别力 ⇒ 物化成功 ⇒ 轮 1 修好** |
| `766b554` | const_str（只改消息文本） | ✗ 0 条 —— **形态门**：判据本就测不到文本差异 |
| `93185b2` | op（Lt→LtE） | ✗ 0 条 —— **上下文门**：测试写 `date_range = daterange(today, today)` 再 `next(date_range)` ⇒ 抽出的探针依赖局部变量，单拎出来是 `NameError` |
| `64e6430` | op | ✗ 0 条 —— **上下文门**：`cab.pop()` / `len(cab)` 依赖 fixture 构造的对象 |

⇒ 打开判据门**不等于**那些 bug 就能解：三道门串联，通过数是它们的乘积。
`93185b2` 之前是靠**手写探针**过的（README 153），自动抽取至今过不了上下文门。

⛔ 不许把"1/4"报成"判据门解决了" —— 准确说法是：**判据门这一段确实开了，
且 c1c25da 是它直接换来的；另外 3 个卡在别的门上。**

═══ 五、契约 + 真注入 ═══

`tests/contract_raises_criteria_test.py`（新，36 项）：

  A 三情形的 passed / why 各自正确（抛对 / 该抛没抛 / 抛错类型）
  B ⛔ why 归属：该抛没抛 ⇒ behavior（**强题**），抛错类型 ⇒ crash
  C ⛔ 受控词表：`eval` / `__import__('os').system` / 非字符串 ⇒ 判失败
    且**不执行**；不许静默降级成 truthy
  D ⛔ 反证：没有这一支时，同一个判据在**修复版上恒失败**、在缺陷版上通过
    ⇒ 判据方向是反的 ⇒ 证明"加这个槽位"是那道门本身，不是锦上添花
  E 回归：既有两支（expect / truthy）不受影响

⛔ **真注入**：把 `if "raises" in t:` 换成 `if False:` ⇒ **17 项转红**
（含"任意表达式没有被执行"那条）⇒ 复原 ⇒ 全绿。3.13 与 3.9 双版本全绿。

═══ 六、残留 ═══

  · `realbug_import.py` 的 `raise_probes` 只抽 `with raises(X): <单条表达式>`；
    多语句 body 只取 Expr 语句。
  · raises 与 expect 同时出现时**以 raises 为准**（未显式校验互斥）。
  · ⛔ 键名拼错（如写成单数 `raise`）会静默落进 truthy 分支 —— 既有形态必须
    保留（仓库内有 truthy 判据），所以没在产品里加"未知键报错"。代价是这类
    拼写错误**不会报错**。**未修**，如实记。

### 157. ⛔⛔ **上下文门量过了：它是三道门里最大的一道（81.1%），但打开它的收益是 0 —— 因为形态门没开**（2026-10-26）

═══ 一、为什么轮到它 ═══

153 条记了三道**串联**的门，154/155/156 分别动的是修法渲染、出题器渲染、
判据形态。剩下没动过的只有第三道 —— 上下文门。156 的诚实读数是
「4 个算子级真实 bug 里 2 个卡在上下文门」，n=4 太小，不够做决定，
所以先把它量成分布（`scripts/probe_context_gate.py`，新）。

═══ 二、量具：内联器 + 四条自限 + 三档判档 ═══

做法是把测试函数里的局部赋值**常量传播内联**进断言表达式
（`date_range = daterange(today, today)` ⇒ `next(daterange(...))`）。

⛔ 四条自限（每条都带反证，钉在契约里，不许为救回率放宽）：

  1. 只内联**所在函数内**的赋值 + 模块级（同名不同作用域不许混）
  2. 同作用域同名有两个不同 rhs ⇒ **整条剔除**（循环里重复赋值）
  3. 内联后出现 `today/now/random/uuid/time()` ⇒ **拒收**（不可复现 ≡ 随机）
  4. 内联后必须过三道验：能求值 + 跑两遍稳定 + **与测试写死的 expected 一致**

⛔ **第 4 条是整个量具的核心**：内联有没有改坏语义，裁判是 `chk(actual, expected)`
里那个作者亲手写的 expected，不是"能不能跑"。能跑但值不对 = **假鉴别力**
（在 fixed 上过、在 buggy 上挂，看起来完美无缺）。

═══ 三、读数（40 个 boltons 真实 bug fix，1882 条真实断言）═══

| 档 | 条数 | 占比 |
|---|---|---|
| **硬失败**：在参照实现上求值不了（**上下文门拦掉的**） | 1526 | **81.1%** |
| **软失败**：能求值，但与测试写死期望不符（**从未被记账的假鉴别力**） | 8 | 0.4% |
| ok（能求值且与写死期望一致） | 252 | 13.4% |
| ok_nogold（期望不是字面量 ⇒ **无法对拍，不算已验证**） | 96 | 5.1% |

分形态看（⛔ 两类探针**必须分开**：第一版只抽返回值断言，漏了整整一类）：

| 形态 | 总数 | 存活（ok） | 存活率 |
|---|---|---|---|
| 期望异常 `raises` | 190 | 60 | **31.6%** |
| 返回值 `expect` | 1692 | 192 | 11.3% |

内联救回（⛔ 分三档，不许只报总数）：

| 档 | 救回 | 其中**真有鉴别力** |
|---|---|---|
| 过了金标准对拍（返回值类） | 247 | **8** |
| 无金标准可对拍（返回值类，语义未证） | 293 | 4 |
| 期望异常类（语义由"抛对异常"保证） | 76 | 1 |
| **合计** | **616** | **13** |

拒收：时间/随机依赖 9、与金标准不符 123、两次运行不一致 10。

═══ 四、⛔⛔ 决定性交叉：三道门是串联的 ═══

commit 级（这才是决定能不能多修真实 bug 的量）：37 个候选里，
内联后从「0 条有鉴别力」变成「≥1 条」的只有 **4 个**：

| commit | 内联后鉴别力 | **形态门**（153 那把尺子） |
|---|---|---|
| `99dec84` | 0 → 5 | structural ✗ |
| `d0a284f` | 0 → 1 | structural ✗ |
| `4aa77cd` | 0 → 2 | structural ✗ |
| `8a2a93d` | 0 → 3 | structural ✗ |

⇒ **4 个全被形态门拦下**。形态门过得去的 5 个 operator commit 里，只有
`c1c25da` 三道门全过 —— 而它**不需要内联**（2→2，鉴别力本来就来自 raises
探针，156 条里已修好）。

⇒ **靠开上下文门才变可解的真实 bug：0 个。**

⛔ 结论不是"内联器没用"，是**串联门的性质**：开了收益最小的那道，等于没开。
81.1% 拦掉看着吓人，但那些探针救回来之后绝大多数是 **P2P（回归保护）**，
本来就在 fixed/buggy 上同值 —— 616 条里只有 13 条真能区分两版，
而这 13 条分散到的 commit 全部死在形态门上。

⇒ **下一步的方向因此改变：不是继续开后面的门，是攻形态门（84.9% 结构级
那道墙）。未动工，待拍板 —— 那是"新增修法族"级别的改动，不是本轮这种
量具活。**

═══ 五、过程中自己踩的两个量具坑（都在结论出来之前被抓到）═══

  1. **口径漏了一整个形态**：第一版只抽返回值断言 ⇒ `c1c25da`（明明已修好）
     显示「0 条鉴别力」⇒ 差点据此写下"内联增益全在形态门外"。
     ⛔ 一个「0」有两种含义（第 25 条）：**真的没有** vs **抽漏了**。
  2. **救回数从 247 直接归零**：改批量跑分时 `items_of` 传的是**原始表达式**
     而不是内联结果 ⇒ 跑的还是原表达式 ⇒ 恒失败。
     ⛔ 某维度恒为一个极端值，先怀疑量具（第 24 条）—— 两次都靠这条救回来。

═══ 六、契约 + 真注入 ═══

`tests/contract_context_gate_test.py`（新，21 项 + 9 项自检）：

  A 内联器基本正确性（绑定 / 内联 / 递归内联 / 无得可展返回 None）
  B ⛔ 四条自限各带**反证**：放宽②（歧义绑定）⇒ 必然产出与金标准不符的探针
  C 判档四档（none / mismatch / ok / ok_nogold）
  D ⛔⛔ **反证：能求值 ≠ 语义对；有判据 ≠ 有鉴别力** —— 这正是 616 vs 13
    的机制。谁把这两个数合并上报，谁就是重复本轮的误读
  E 量具自检可复现（`selftest()` 9 条全绿）

⛔ **不落地**：内联器**没有**接进 `realbug_import.py`。收益实测为 0，
接进去等于给导入器增加一层未获收益的复杂度。脚本与契约保留 —— 它们是
「这道门开不开、开了值多少」这问题的答案，不是产品代码。

═══ 七、残留 ═══

  · `n=40` 个 commit，且全部来自 boltons 单仓 ⇒ 分布是**这一个仓库**的。
  · 软失败 0.4% 是好消息（既有判据基本可信），但也只有 8 条样本；
    raises 类**无法**用金标准对拍（它比的是"抛没抛"，没有返回值可比）
    ⇒ 那 76 条救回的语义风险**没有**被排除，只是被标注。
  · 内联只处理 `name = <expr>`；fixture（`pytest.fixture` 注入的对象）、
    类属性、import 别名**都不在内联射程内** ⇒ 真实拦截率只会更高不会更低。

### 158. ⛔⛔ **「修好真实 bug」里的那一改是从基线抄来的 —— 拿掉答案后，模板提议器与真模型都是 0**（2026-10-26）

═══ 一、怎么撞出来的 ═══

上一轮（157）的方向是"下一步攻形态门"。动工前先回头质疑了一个**从没被
质疑过的前提**：真实任务域里 `env.source`（基线）用的是**修复后**的源码，
而五个 `revert_*` 的签名是 `(broken_src, base_src)` —— 它们把 broken
按位置对齐回 base。

    ⇒ base 里有什么，修法就能产出什么；base 里没有的，它一个字节也造不出来。

═══ 二、2×2 对照（同一道真实 bug boltons `c1c25da`，只改基线来源）═══

| 组 | 题源 | 基线 | 提议器 | 结果 |
|---|---|---|---|---|
| **A** | 真实 bug | 修复后源码 | 模板 | 轮 1 `fix_control_flow` reward=1.0 ⇒ **25/25 修好** |
| **B** | 真实 bug | **缺陷版本身** | 模板 | 3 轮 **0 提交**，23/25 依旧失败 |
| **C** | 真实 bug | **缺陷版本身** | **真模型** qwen2.5:7b | 3 轮 **0 提交**，23/25 依旧失败 |
| **D1** | V6 自造题 | 原实现（= 答案） | 模板 | 轮 1 修好 25/25（这就是 121 次提交的世界） |
| **D2** | V6 自造题 | **变异版本身** | 模板 | 3 轮 **0 提交**，23/25 依旧失败 |

⛔ C 组必须证明模型**真的被调用了**（否则"LLM 也不行"是个假证据）：
`last_error=(空)、重试=0、原始输出长度=371` ⇒ 模型确实输出了，提了
「修复布尔运算」胶囊 ⇒ reward 仍 0。
（第一次跑 C 组时拿到的其实是 **404 `model 'qwen2.5' not found`** ——
本机模型带 tag（`qwen2.5:7b`）。那一格我当时就标注为"读数不成立"，没拿它
当证据。⛔ 一个 404 有两种含义：端点挂了 vs 模型名没写全。）

⛔ B/D2 的失败形态是 `saturated:stagnated`（**无候选能超过基线**），不是
被闸拦下 ⇒ 是"修法没产出"，不是"闸挡住了"。这两种失败长得像，含义相反。

═══ 三、机制级证据（比实验更硬）═══

    base == broken  ⇒ 五个 revert_* **全部逐字返回输入**（恒等操作）
    base = fixed    ⇒ revert_control_flow 改 1 行：
                       `if val > 2 ** len_:` → `if val >= 2 ** len_:`
                       —— 正是人类修复的那一行，**值取自 base**
    base = 错的（`<=`）⇒ 修法跟着产出 `<=`（**盲从**）

第三条是刀刃：改动的来源**只能是 base**。若修法是在"修"，它不会把一个
raise 阈值从 `>` 改成 `<=`（方向完全相反）。它照做了 ⇒ 它在对齐，不在修复。

═══ 四、这条结论的分量 ═══

  · 153/156 记的"第 1 轮修好真实 bug"，其准确含义是：
    **系统自己贡献的是"定位到那一处"（靠判据，这是真能力）；
    "改成什么"来自基线（= 人类答案）。**
  · 121 次成功提交在自造题上**没有**这个问题（题就照基线变异而来，
    对齐基线即正确答案，同源性在 153 已记）—— 但机制是同一个：**对齐**。
  · ⇒ 谁要拿"修好了真实 bug"当能力证据，必须同时报这一条。

═══ 五、真正的缺口因此被重新定义 ═══

不是"形态门"（157 的推断方向需要修正），而是：

    **V6 没有「判据驱动生成补丁」这一层。它只有「对齐」，没有「合成」。**

`LLMProposer` 也填不上这个缺口：它提的是"**用哪个胶囊**"（技能选择），
不是"**改什么代码**"。所以即使模型提对胶囊，执行层仍是 `revert_*(broken, base)`
⇒ base 无答案 ⇒ 恒等。C 组就是这个结果。

⇒ 补这个缺口不是加一个 `revert_*` 能解决的，需要新的一层。未动工，待拍板。

═══ 六、契约 ═══

`tests/contract_baseline_leak_test.py`（新，20 项）：

  A 恒等：`base == broken` ⇒ 五个修法逐字返回输入
  B 有效：`base = fixed` ⇒ control_flow 产出改动（证明 A 不是"修法本就空转"）
  C ⛔⛔ **反证（盲从）**：base 写错 ⇒ 修法跟着产出那个错的值
  D 对照开关 `--baseline {fixed,buggy}` / `--proposer {template,llm}` 都在
    （否则这张表无从复现）

`scripts/realbug_experiment.py` 新增 `--baseline` 与 `--proposer`
（默认 `fixed` / `template` ⇒ **生产行为逐字不变**）。

═══ 七、残留（⛔ 全是削弱结论的方向，如实记）═══

  · **n=1**：真实 bug 那一格只跑了 `c1c25da` 一个。不是偷懒 ——
    boltons 里"能自动物化（过上下文门）**且**形态门过得去"的真实 bug
    **目前只有这一个**（`93185b2` 自动物化 0 条，766b554 卡上下文门，
    64e6430 卡上下文门）。这个"只剩 1 个样本"本身就是三道门串联的读数。
    ⛔ **更正（161 实测）**：这里原写「766b554 只改文本」**是错的** ——
    它的真实 diff 是 `abs_bytes <= next_size` → `<`，纯比较符翻转，
    形态**完全在合成层射程内**；它卡的是上下文门（71/71 条探针求值不了）。
    161 已把它**解锁**（判据给足后第 1 轮修好）。详见 161。
  · C 组只跑了 3 轮、1 个模型（qwen2.5:7b）。更强的模型未必 0 ——
    但**执行层恒等**这一条与模型无关：`revert_*(broken, broken) ≡ broken`。
  · D1/D2 用的是同一环境上的合成题（`::m0`），不是仓库 `tasks/` 里的标准题。

### 159. **补上了「判据驱动生成补丁」这一层：签名里没有 base —— 拿掉答案后它照样修好同一个真实 bug**（2026-10-26）

═══ 一、起因：158 留下的缺口 ═══

158 条取证出：`revert_*(broken, base)` 只会「按位置对齐回 base」，真实
任务域 `env.source` = 修复后源码 = **人类答案** ⇒ 「修好真实 bug」里
「改成什么」是抄来的。缺口被定义为：**V6 没有「判据驱动生成补丁」这一层
—— 只有对齐，没有合成。**

本轮把这一层做出来了：`aether/synthesize.py`（新模块）。

═══ 二、⛔⛔ 它的定义性特征：签名里没有 base ═══

    revert_*(broken_src, base_src)      ← 答案来自 base
    synthesize_patch(broken_src, tests) ← 答案来自**判据反馈**

这不是"换个写法"，是**信息源换了**：base 是答案，判据只是尺子。
把 base 从签名里拿掉，是为了让「有没有偷看答案」**结构上可判定** ——
契约用 AST 静态查：本模块不存在任何 `.source` 属性访问、任何 `base`
形参。想偷看都偷不到。

═══ 三、⛔ 它是**枚举式程序合成**，不是"模型凭空造代码"═══

做法：定位（失败判据 ⇒ 顶层定义）→ 枚举候选（比较算子翻转 / 整数常量
±1 / 加减翻转）→ 每个候选跑一遍判据 → 取严格优于基线者。

搜索空间由候选生成器给定 ⇒ **覆盖不到的形态它一个字节也造不出来**。
而 153 普查过：真实修复 **84.9% 是结构级**，真算子级只有 **5.5%**
⇒ 本层的射程上限就在这 5.5% 里。

═══ 四、决定性对照（同一道真实 bug boltons `c1c25da`）═══

| 修法 | 基线=修复后（有答案） | 基线=缺陷版（**拿掉答案**） |
|---|---|---|
| `revert_*`（对齐） | 轮 1 修好 25/25 | 3 轮 **0 提交**（158 的 B 格） |
| **`synth`（合成）** | 轮 1 修好 25/25 | 轮 1 **修好 25/25** |

⛔⛔ 最强的一条：两组 synth 实验的输出**逐字相同**，连 harness id 都是
`8bbe33a1` ⇒ **结果与基线无关**（这正是"不看基线"的可执行定义）。
而 `revert_*` 在同样两组上是「修好 / 0 提交」的**强依赖**。

产出的补丁与人类修复**逐字一致**（1 行 diff：`val > 2 ** len_` →
`val >= 2 ** len_`）。搜索统计：候选 60、实跑 60、跑挂 0、**全绿候选
只有 1 个** —— 那唯一解正是人类那一行。

═══ 五、机制反证：盲从**判据**（与 158 的"盲从基线"对称）═══

| | 158 的 `revert_*` | 159 的 `synth` |
|---|---|---|
| 把 **base** 写错（`<=`） | 跟着产出 `<=` | ——（它根本没有 base） |
| 把 **判据期望**改掉 | 不受影响 | **产出跟着判据变** |

⇒ 信息源确实换了：一个跟 base 走，一个跟判据走。契约 C 组钉的就是这条
（且带"反证不是空跑"的对照：原判据下**确实**能找到，改动后才找不到）。

═══ 六、⛔⛔ 分寸：它没有让 V6「更宽」═══

它把「能修的那一类」从"必须抄答案"变成"能自己搜出来"，但**没有扩大
"能修的那一类"的边界** —— 形态门（84.9% 结构级）仍在原位，合成层
结构上够不到。契约 D 组钉死了射程边界：结构级缺陷（缺一个分支）
⇒ `found=False`，但**必须记账搜过**（`n_cands>0`，不是"没开工"）。

⛔ 谁把「拿掉答案也修得好」读成「V6 会修 bug 了」，谁就漏了这一段。

═══ 七、⛔ 固有风险：它选出的补丁必然在发展集上更绿 ═══

筛选用的是**发展集判据** ⇒ 选出来的必然是"发展集上更绿"的那个
⇒ 这就是 overfit 的形状，held-out 闸照常要过。
（与 `revert_*` 的差别不是"更安全"，是 **overfit 的方式不同**：
revert 抄答案，synth 搜答案 —— 两者都要由闸裁决。）

═══ 八、代价（第 71 条：量代价）═══

  · 单个候选一次判据跑分 ⇒ 本例 **60 次沙箱 / 7 秒**
  · 接入 evolution loop 后一轮 **3m27s**（模板组是秒级）
  ⇒ 每一次调用都是真代价，所以**默认不在任何生产技能池里**。

═══ 九、⛔ 默认不上场（机制就绪 ≠ 接入生产）═══

`synth_operators` 进了 `KNOWN_STRATEGIES`（`_apply_one` 认得这个名字），
但 **`cli.py` 的生产技能池里没有它** ⇒ 没有任何生产调用点会提名它
⇒ 默认关。契约 H 组钉死这条（若哪天 cli.py 里出现了它，说明它已默认
上场，必须重新评估代价）。

═══ 十、过程中实踩的三个坑（都在结论之前被抓到）═══

  1. **`render_patch` 的回调是两个参数** `mutate(b, g)`。写成单参数 ⇒
     每次 TypeError 被**静默**吞掉 ⇒ 全部返回 `unchanged` ⇒ 看起来
     像"搜不出修复"。**"全部 unchanged" 是这个 bug 的唯一症状。**
  2. **`render_patch` 返回 `(源码, 渲染模式)` 元组**。当字符串用 ⇒ 每条
     候选都"跑不起来" ⇒ 又被 `if blew: continue` 静默跳过 ⇒ 假阴性。
  3. **定位器只看判据名会漏**：判据名常常是 `t1` / `p3` 这种编号，
     标识符只在 `call` 里（`f(10)` ⇒ `f`）⇒ 量具自检搜不出修复，
     看起来像"这一层不行"，实际是**定位没开工**。改为名 + call 拼接。

⛔ 三次都是同一个形状：**「0 / 全 unchanged / 搜不出」既是"世界没有"
也可能是"尺子没量"** —— 第 24 条，本轮第三次靠它救回。

═══ 十一、契约 ═══

`tests/contract_synthesize_test.py`（新，30 项）：

  A ⛔⛔ 结构性不偷看（AST 查：无 `.source` 属性访问、无 base 形参、
    签名必需参数只有 `(broken_src, tests)`）
  B 量具自检（已知算子级 bug 必须能搜出，且产物是最小编辑）
  C ⛔⛔ 刀刃反证：判据改向 ⇒ 产出跟着变（+ 反证不是空跑的对照）
  D 射程边界：结构级修不了，**但必须记账搜过**
  E 不劣化：基线已全绿 ⇒ 不动手
  F 记账：语法错误 / 非 str 源码 ⇒ `blew=True`，**不许**当 0 分
  G 定位与渲染（渲染模式 split/fallback 分得开，必须可见）
  H ⛔ 默认不上场（生产 cli.py 池里没有它）

═══ 十二、残留（⛔ 全是削弱结论的方向，如实记）═══

  · **n=1**：真实 bug 那一格只有 `c1c25da`。boltons 里"能自动物化
    （过上下文门）**且**形态门过得去"的真实 bug 目前就这一个。
  · ⛔ **闸没参与**：两轮实验的 `verdict` 都是 `no_gate`（装置本身没挂
    held-out 闸）⇒ 这个"修好"**未经泛化裁决**。E1/E2 同口径可比，
    但"它在 held-out 上会不会被拦"**本轮没量**。
  · **射程没在大样本上量**：5.5% 是 153 的形态普查推出来的上限，
    不是本层在 73 个真实 bug 上的实测命中率。要量得先解决物化率。
  · 定位器只认顶层 `def`/`class`；fixture、嵌套定义、import 别名都不在
    射程内 ⇒ 真实定位成功率只会更低。
  · 候选集只有三类（比较算子 / 整数常量 / 加减）。每加一类，搜索空间
    与 overfit 风险同步放大 —— 扩之前必须先量代价。

⛔⛔ **补记（160 条实测）**：本条"轮 1 修好 25/25"是在**判据最密**
（25 条全给）的条件下取得的。160 条量了判据稀疏度：只给 1 条判据时
本层 **5/5 全错**（未见判据通过率 0.25），k≥3 才收敛。
⇒ **引用本条读数时必须同时引用 160 条那张表。**

### 160. ⛔⛔ **159 那条"未验证的 overfit 风险"实测为真：只给 1 条判据时，合成层 5/5 全错**（2026-10-27）

═══ 一、起因：159 留下的一条声明，和一个陷阱 ═══

159 条补上合成层时写了：

    筛选用的是发展集判据 ⇒ 选出来的必然是"发展集上更绿"的那个
    ⇒ 这就是 overfit 的形状。

但那轮实验的 `verdict` 是 `no_gate`（闸没参与）—— 已确认是**实验装置
固有**（158 的 A 组同样是 `no_gate`）⇒ 两组同口径可比，但**风险没被测过**。

⛔⛔ 这里有个陷阱必须先避开：159 的合成补丁与人类修复**逐字一致** ⇒
拿去 held-out 上跑必然 100% 通过 ⇒ 那是**恒真验证，没有信息量**
（第 122 条：构造必然 ≠ 证据）。

═══ 二、改量「判据稀疏度」═══

只给合成层 k 条判据，看它搜出的补丁在**没给它看的那些判据**上还成不成立：

    判据给少了 ⇒ 它会搜出一个"满足你给的这些、但不满足其余"的补丁
    ⇒ 那不是修复，是**拟合判据**。

（`scripts/probe_synth_overfit.py`，新。子集必须含 ≥1 条在缺陷版上失败
的判据，否则缺陷版在子集上全绿 ⇒ 本层判定"无增益可图" ⇒ 不动手。）

═══ 三、读数（真实 bug boltons `c1c25da`，25 条判据，每档 5 个随机子集）═══

| 给的判据数 k | 搜出 | 与人类修复一致 | **未见判据**通过率 |
|---|---|---|---|
| **1** | 5/5 | **0/5** | **0.25** |
| 2 | 5/5 | 4/5 | 0.843 |
| **3** | 5/5 | 5/5 | **1.0** ← 收敛 |
| 5 / 10 / 25 | 5/5 | 5/5 | 1.0 |

k=1 时它搜出的是 `cmp@L160`，而人类修复在 `L164` ⇒ **满足给它那 1 条、
破坏其余 18 条**。这是"拟合判据"的教科书形态，不是理论担心。

═══ 四、⛔⛔ 两条必读推论 ═══

  1. **判据必须够密**：这条 bug 上 **k≥3 才收敛**。判据稀疏时本层产出的
     是凑数解 —— 过了给它的、挂了没给它的。
  2. ⛔ **159 报的"修好"是在 k=25（最宽松）条件下取得的。**
     谁引用那个读数，必须同时引用这张表。

⇒ 这两条已写进 `aether/synthesize.py` 模块头，且有契约盯着不许删
（文档即契约 —— 删掉警示 = 删掉这条风险的唯一入口）。

═══ 五、机制复现（契约，秒级，与真实 bug 同形）═══

`tests/contract_synth_overfit_test.py`（新，15 项）：

  O1 量具对照：人类修复在未见判据上必须全过（否则 O3 是恒真空判据）
  O2 判据够密（2 条）⇒ 搜出正确解，与人类修复逐字一致
  O3 ⛔⛔ 只给 1 条 ⇒ 补丁 ≠ 人类修复，且**在未见判据上挂**
  O4 反证不是空跑：只给 1 条时**确实**搜出了东西（否则 O3 恒真）
  O5 ⛔ 凑数补丁**确实满足**给它的那条判据 ⇒ 是拟合判据，不是修 bug
  O7 ⛔ 模块头的 overfit 警示 + 实测阈值不许被删

⛔ 玩具设计上有个坑（第一版白跑了）：**正确解必须排在枚举顺序后面**。
若正确的候选恰好排第一，它会先命中 ⇒ overfit 复现不出来。真实 bug 上
之所以复现了，是因为凑数解 `L160` 恰好排在正确解 `L164` 前面。

═══ 六、残留（⛔ 削弱结论的方向）═══

  · **这不是真正的 held-out 闸**。本实验用的是同一批真实判据的**随机子集**，
    量的是「判据稀疏度 → 泛化」；真正的闸（`NonRegressionGate` +
    `FrozenTask`）是另一回事，**仍然没跑过**。两者别混。
  · **n=1**：只有 `c1c25da` 一道题 ⇒ "k≥3 收敛"是**这一道题**的阈值，
    不是普适常数。别的 bug 可能需要更密的判据。
  · 每档只跑 5 个随机子集（seed 固定，可复现）。
  · k=1 的"全错"是在「必须含 1 条失败判据」的约束下取的；若那 1 条恰好
    鉴别力极强，结论未必成立 —— 本轮没测这种情形。

### 161. ⛔⛔ **重测「形态」那把尺子（假设被否决）+ 把 n=1 提到 n=3 —— 并在第 4 个上撞见「found ≠ 修好了」**（2026-10-27）

═══ 一、起因：一个自己的假设，和一个悬着的 n=1 ═══

160 留下的开口是 **n=1**（只有一道可物化的真实 bug）。开口之前，先去查
了一个**从未被质疑过的口径**：153 的形态普查判「可达」的条件是

    整个 Module 树同构 + 差异只在算子/常量

⇒ 一个 commit 只要顺手改了别处（重构、加 import、调类结构），整棵树就
不同构 ⇒ 被判不可达 —— **哪怕核心修复就是那个 `>` → `>=`**。

我的假设：**旧尺子偏悲观，放宽到「某个顶层 def/class 内部」应该能救回一批。**

═══ 二、新尺子：不问「整棵树同不像」，问「有没有一个单元不用 INSERT」═══

`scripts/probe_shape_ruler.py`（新）。三档，从宽到严全部报告：

    L1 整个文件的差异全是算子/常量级（= 旧口径）
    L2 至少一个顶层单元内部是纯算子/常量级
    L3 至少一个顶层单元内部的变化**全落在合成层实际候选集**里
       （cmp 翻转 / 整数 ±1 / Add↔Sub）—— 其余 UPDATE（字符串、and/or、
       not 增删）是 revert_* 够得着而 synth 够不着的

⛔ 8 条自检先跑，判错就停机。其中两条必须这样：
`c1c25da` ⇒ L3；`5eb6cc0`（新增两行 `if`）⇒ X（INSERT 语句谁都够不到）。

═══ 三、⛔⛔ 读数：**假设被否决** ═══

| | 数量 | 占比 |
|---|---|---|
| 旧口径 operator | 11/215 | **5.1%** |
| 新口径（单元级，剔除 docstring） | 13/215 | 6.0% |
| ⛔ 其中真落在 synth 候选集内（L3） | 4/215 | 1.9% |

153 报 5.5%（n=73），这里独立重测得 5.1%（n=215）⇒ **旧尺子没偏。**

放宽只多救回 2 个，而那 2 个的差异**全是 `const_str`**（改错误消息文本），
不是假设的"别处连坐"。

⛔⛔ 而且第一版的结果比这个糟得多：不剔除 docstring 时，8 个「旧判死、
新判活」的分歧里**无一例外**都是「核心是结构改动、顺手改了 docstring」
⇒ 新尺子被那个 docstring 骗成了"可达"。
**放宽口径 ≠ 更准。** 这条现在是自检 S4（契约盯着）。

═══ 四、形态墙的硬度：值不值得扩候选集 ═══

202 个当前不可达的修复里：

| 类型 | 数量 | 含义 |
|---|---|---|
| E | **7** | 结构差异全是「搬文件里**已有**的子树」⇒ 候选集扩到"用已有表达式替换"即够得着 |
| mix | 0 | 部分靠搬运 |
| N | **143** | ⛔ 必须凭空造新东西（新名字/新调用/新常量）⇒ 扩候选集也没用 |
| none | 52 | 顶层增删等 |

⇒ **扩候选集的天花板：7/150 = 4.7%。** 投入到产出比极差，不建议做。

═══ 五、端到端漏斗（形态 × 判据，157 的 37 个样本）═══

    有明确改动的 commit        37  ████████████████████████████████
    ≥1 条 F2P（判据关最低线）     11  ██████████      ← 掉 26
    ≥3 条 F2P（参考）            6  █████          ← 掉 5
    且形态 ≥ L2                1  █              ← 掉 5
    且形态 = L3（synth 射程）     1  █

⇒ **37 个里同时过两关的只有 1 个，就是 `c1c25da`** —— 与 n=1 完全吻合。

⛔ 口径校准：160 的「k≥3」是**给合成层的判据总数**（含回归保护），而这里
的 F2P 是**有鉴别力的判据数**。159 那次成功是「给全 25 条（23 条 P2P +
2 条 F2P）」⇒ 若拿 ≥3 当 F2P 门槛，连唯一成功案例都会被算掉。**P2P 是有
用的**——它们是"别破坏其余行为"的负约束。

═══ 六、⛔ 反向看：提高物化率能解锁的**全部**候选 ═══

形态 = L3 但 F2P = 0 的，一共 **3 个** ⇒ 这就是上界（1 → 最多 4）：

| commit | 位置 | 形态 | 卡在哪 |
|---|---|---|---|
| `766b554` | strutils.bytes2human | cmp（`<=`→`<`） | 上下文门 71/71 |
| `93185b2` | timeutils.daterange | cmp（`<`→`<=`） | 上下文门 12/18 |
| `64e6430` | setutils.IndexedSet | addsub（`+`→`-`） | 上下文门 37/39 |

机制是**同一个**：测试里用了局部别名 / fixture / 多步序列
（如 `b2h = strutils.bytes2human`，然后断言 `b2h(1024) == '1K'`）
⇒ 抽出来的探针脱离上下文是 `NameError`。

═══ 七、解锁实证：n=1 → n=3（⛔ 基线＝缺陷版，即**不看答案**）═══

| 真实 bug | 判据来源 | 结果 |
|---|---|---|
| **`766b554`** | 作者测试 `test_bytes2human` 的 12 条断言（照抄，只把别名换回顶层名） | 5/12 → **12/12**，补丁 `abs_bytes <= next_size` → `<`（人类那一行） |
| **`93185b2`** | 153 注入的那条（含 `inclusive=True`）+ 4 条回归保护 | 4/5 → **5/5**，补丁 `start < stop` → `<=`（人类那一行） |

⛔ 复现：`scripts/probe_unlock_candidates.py`（3 个候选全在里面，含那条
写坏的判据 —— 它是第八节现象的成因，不要"顺手修好"它）。

⛔ **判定口径**：看**改动的行**，不是整份文件。`766b554` 的作者在同一 commit
里顺手往 docstring 加了两行 doctest ⇒ 整份文件与人类版本**不相等**，但改动
的行完全相等。第一版脚本比的是整份文件，把它误判成了"改在别处"（还好在
写结论之前核对了 —— 第 24 条的又一次兑现）。
**不是**「系统能自动从仓库端到端修」。自动抽取至今过不了上下文门。
（分寸同 158：谁把这条读成"V6 会修 bug 了"，谁就漏了这一段。）

⇒ `93185b2` 还有一层特殊价值：它是**同一道题在两种机制下的对照** ——
153 那次是 revert_* + 有答案 ⇒ 修好；158 证明那条是抄答案；本轮改成
synth + 无答案 ⇒ **还是修好**。同一个 bug，换了机制，结论站住了。

═══ 八、⛔⛔ 第 4 个候选 `64e6430`：**found=True，但改错了地方** ═══

只有 2 条 F2P、且其中一条我写成了 SyntaxError（恒失败）⇒ 「全绿」**根本
不可能达到** ⇒ 正确答案（`+`→`-`，L155）与一个改在**不相干位置**的凑数解
（`len(ded) > 384` → `<`，L122）并列 6/7 ⇒ 按「通过数最多、先到先得」
选中了凑数解。

    found=True、gain>0、new_ok 6/7 —— 可它跟那个 bug 毫无关系。

⇒ **`found` 有两个含义**：真找到 vs 找到个凑数的。这是 160 那条 overfit
机制在**真实 bug 上的第二例**（第一例是随机判据子集实验）。

⇒ 产品侧加了 `SynthResult.full_pass`：**全绿是天然的分水岭**，现在显式可查，
调用方不必自己算 `new_ok == n_tests`。
⛔ 但它是**必要非充分**：契约 P3 证明了存在「另一种改法也能全绿」
（`>= 10` 与 `> 9` 在这套整数判据上不可区分）⇒ 全绿之后**仍要过 held-out**。

═══ 九、⛔ 对自己旧记载的一处**更正** ═══

156/158 里写的「766b554 ✗形态门（只改文本）」**是错的**（5964 行已就地更正）：
它的真实 diff 是 `abs_bytes <= next_size` → `<`，纯比较符翻转，形态完全
在射程内 —— 它卡的是上下文门，本轮已解锁。
⇒ 记载偏了一格，形态墙就会看起来比实际更厚，决策方向会被带偏
⇒ 就地更正而不是悄悄改，改动痕迹留着。

═══ 十、实踩的三处量具 bug（都在读数出来之前被自检抓到）═══

  1. **`diff_points` 先做 `type(a) is not type(b)` 检查** ⇒ `Gt` vs `GtE`
     还没轮到算子分类就被判 struct ⇒ 连纯 `>`→`>=` 都判不出来
     （症状：`c1c25da` 冒出 90 多处差异）。⇒ 原子节点必须先于类型检查。
  2. **常量没判「完全相同」** ⇒ `Constant(10)` vs `Constant(10)` 被记成
     一处改动 ⇒ 文件里每个字面量都变成"差异"。同上一条形状。
  3. **`_point_kind` 只处理「两边都是原子」** ⇒ `Constant(0)` vs `BinOp`
     （一边是原子一边不是）返回 None ⇒ **漏判**（偏乐观）。补齐后才有
     「搬运已有子树」与「凭空造新子树」的区分。

⛔ 三次都是同一形状：**「处处是差异」与「处处没差异」同等可疑**（第 24 条）。

═══ 十一、契约 ═══

`tests/contract_shape_ruler_test.py`（新，13 项）：
A 离线自检（**复用脚本自己的实现**，契约不抄第二份 ⇒ 不会漂移成两把尺子）
B 判档性质（docstring 不算变化 / 别处不连坐 / INSERT 一律不放过）
C ⛔⛔ 候选集映射不许漂移（`_SYNTH_DESC_MAP` ↔ `synth_candidates` 的产物，
  谁给合成层加一类而不登记，L3 就会静默低估射程）

`tests/contract_synth_overfit_test.py` 加 **P 组**（9 项）：found ≠ 修好了
（P2 恒失败判据 ⇒ found=True 而 full_pass=False；P3 另一种改法也能全绿）。

═══ 十二、残留（⛔ 全是削弱结论的方向）═══

  · ⛔ **判据是人工转写的**。三个候选里两个成功靠的是"人告诉它测什么"。
    自动抽取（别名绑定 / fixture / 多步序列）**一行没动**，仍是上下文门。
  · n=215 个 commit 全部来自 boltons **单仓**；且 37 个有判据读数的样本
    来自 157 的 limit=40。别的仓库分布未知。
  · `64e6430` 只试了两版判据就撞上 `found≠修好`；**没有**继续调判据去把它
    修对 ⇒ 它现在是"未解锁"，不是"不可能"。
  · 扩候选集的天花板（E 档 7 个）是**静态**推的：搬来的子树是否**真的**
    让判据通过，一次都没跑过 ⇒ 7 是乐观上界。
  · 三个成功案例都**没有**过 held-out 闸（装置是 `no_gate`，与 159 同口径）。

### 162. ⛔⛔ **上下文门的方向一直是反的 —— 判据抽取层落地，第一个「全自动端到端」的真实 bug**（2026-10-28）

═══ 一、起点：161 留的缺口，加一个从没被问过的量 ═══

161 收尾时白纸黑字写着：那三个"修好的真实 bug"，**判据全是人手写的**。
⇒ 端到端链路断在**第一公里**。而上下文门虽然被量成 81.1%，它一直是个
**没有成因分布的总数** —— 失败就是失败，没人问过「为什么失败」。

本轮先做了一个三行探针的最小验证（同一个真实 bug boltons `766b554`）：

| 探针 | 在 fixed 上 | 在 buggy 上 |
|---|---|---|
| `b2h(1024)`（原样抽出） | `NameError: b2h` | `NameError` |
| `strutils.bytes2human(1024)`（**157 内联器的产物**） | `NameError: strutils` | `NameError` |
| `bytes2human(1024)`（降回裸名） | `'1K'` | `'1024B'` ← **有鉴别力** |

═══ 二、⛔⛔ 结论：**157 的内联器方向反了** ═══

`run_in_sandbox(source, tests)` 的 source 是**被测模块源码本体** ⇒ 沙箱命名
空间里只有该模块**内部**的名字，**模块对象自己并不在那儿**。而 157 的
`inline_expr` 做的是「把局部别名展开成**更外层**的作用域名」：

    b2h  ⇒  strutils.bytes2human     ← 引进了一个沙箱里根本不存在的新自由变量

⇒ 它不是"没做"，是**朝着唯一错的方向做**。正确方向是**降回模块内裸名**。
这一类叫 **自指模块别名**。它是**纯 AST 可判定**的：不需要模型、不需要 pytest、
不需要联网。

═══ 三、⛔ 两种形态，缺一个就漏一大片 ═══

    A 直写前缀   assert strutils.strip_ansi(x) == y      ⇒  strip_ansi(x)
    B 局部别名   b2h = strutils.bytes2human; b2h(1024)   ⇒  bytes2human(1024)

⛔ **A 比 B 普遍得多**：`test_strutils.py` 里绝大多数断言是直写形态，用别名的
只有 `test_bytes2human` 一个函数。第一版只做了 B ⇒ 71 条里 **56 条落进
「other」** —— 一个维度全压在一档就该怀疑量具（第 24 条），这才补的 A。

═══ 四、读数（1882 条真实断言，与 157 同一批样本）═══

| | 数量 | 占比 |
|---|---|---|
| 能直接用的（含 gold 对拍） | 252 | 13.4% |
| 上下文门拦掉的 | 1630 | 86.6% |

⛔ 口径说明：这里的「拦掉」比 157 的 `none`（81.1%）**更严** —— 它把
`mismatch`（假鉴别力）和**期望值不是字面量、无法对拍**的也一并算作不可用。
多出来的 96 条就是后者。

**公平对照（⛔ 两个机制走完全同一套验收）**：

| | 尝试 | 变可求值 | 过金标准 | **有鉴别力** | 拒收 |
|---|---|---|---|---|---|
| 157 内联器（往外展） | 1139 | 616 | 248 | **13** | 9 |
| 162 降级（降回裸名） | 1233 | 748 | 370 | **20** | 17 |

⛔⛔ **这份对照第一版是不公平的**：给新机制走完整三验（含金标准对拍），
却只给老内联器量"能不能求值" ⇒ 两行数字口径不同，不能比。改成**候选按
去重后的表达式统一评测**（同一表达式只跑一次，标注来自 old / new / both）
才是这版。**能求值 ≠ 能用，相差 18 倍**（748 : 20）。

**剩下的按成因分**（908 条）：`other` 712 (43.7%) / `rebound` 171 (10.5%，
多步序列) / `fixture` 25 (1.5%，pytest 注入，静态不可解)。

**commit 级**（这才是决定性的）：40 个候选里 37 个有效，本来 ≥1 条鉴别力
判据的有 6 个 ⇒ **降级后新变成 ≥1 条的有 5 个**（`99dec84` / `d0a284f` /
`4aa77cd` / `766b554` / `8a2a93d`）。

═══ 五、⛔⛔ **本节这张表已作废 —— 见 163** ═══

⛔⛔ **不要用本节的数字。** 它是在 162 落地过程中、抽取层重构（`extract_criteria`
抽出来成为产品模块）**之前**的一次一次性脚本读数上取的；那版代码没有提交，
契约也没覆盖它 ⇒ **不可复现**。

163 用 `scripts/probe_auto_unlock.py --reach --out <file>`（已提交、契约已覆盖）
重跑，得到的是**另一个分布**（成功数 1 → **2**，found 未全绿 3 → 1）。差异来自口径，
不是随机波动。旧表留在这里只是为了让记载连续，**数字一律作废**。

    作废的行：   1 same_core_fix / 3 found 未全绿 / 13 no_fix / 12 no_f2p / 11 no_criteria
    163 的读数： 2（1 exact_file + 1 same_core_fix）/ 1 elsewhere / 14 no_fix
                 / 14 no_f2p / 9 no_criteria

⇒ 教训（第 49-52 条可复核性的一个具体反例）：**读数必须由「仓库里那份脚本」产出，
而且脚本必须能 `--out` 落盘**。跑在 `/tmp` 里的一次性脚本上、或跑在未提交的
中间态上的读数，口径一变就永远无法自证，只能整节作废。

链路：`git` → 自动抽断言 → 降级 → 沙箱验语义 → `synthesize_patch`（**不看答案**）
→ 与人类 diff 对拍。`scripts/probe_auto_unlock.py`。

 **`766b554`（boltons bytes2human rolls over at powers of 1024）**：

    71 条断言 → 60 条候选（7 条期望值是 bytes、不可序列化 ⇒ 显式丢弃）
    → 59 条过三验 ⇒ k=59、其中 F2P=7
    synthesize_patch: base_ok 52/59 → new_ok 59/59，full_pass=True
    机器产的一改：if abs_bytes <= next_size:  ⇒  if abs_bytes < next_size:
    人类产的一改：同上（逐字相同；人类还在 docstring 里多加了 2 行 doctest）

⛔ 判定口径：**比改动的行，且作者的顺手改动不算**（161 第七节）。第一版比
"改动集合相等" ⇒ 把这次成功案例判成了 `elsewhere`（同一个坑第三次踩）。

⛔ `k=59 ≥ 3` —— 按 160 的判据密度约束，这条读数才敢报。

**那 3 个凑数解值得一提**：`ec1ff9f`（人类修的是 typo `issupserset`）与
`d0a284f`（人类修的是负索引边界）**是两道完全不同的题，机器给出的是同一个
补丁**（`if index < 0` → `if index > 0`）。三者 `full_pass` 全 False
⇒ 161 第八节的「found ≠ 修好了」在这里得到**第三、四、五个实例**。

═══ 六、⛔ 一条新的静态限制：期望值必须能进沙箱 ═══

沙箱用 JSON 传参 ⇒ `bytes` / `bytearray` / `datetime` 类的期望值**序列化不了**
⇒ 这类判据**结构上进不了 V6**（`766b554` 有 7 条、`strutils` 尤其多，因为它
大量测 bytes）。第一版这个坑被踩了两次（探针崩溃、契约 H 组崩溃各一次）
⇒ 提到抽取层 `extract_criteria` 处理，并**记账**（`stats.n_unjsonable`），
不静默丢弃（跳过 ≠ 通过，第 149 条）。

═══ 七、落地：`aether/extract.py`，且**规则只有一份** ═══

新增产品模块 `aether/extract.py`（与 `synthesize.py` 同款处理：**可用性已验证、
但不自动进生产池**，接不接生产留给后续决策）。⛔ 同时把 157 脚本里的那七个
规则实现**迁出来**（`probe_context_gate.py` 现在是重导出，保留 `run_all` /
判档 / 报告）—— 规则的 duplicated copy 一旦各自演进，两把尺子就会漂移、读数
无法对拍（161 的 C 组盯的就是这类漂移）。迁移后 157 的自检与契约组**逐条重跑
通过**，读数未变。契约 F 组锁住这件事。

═══ 八、实踩的四处量具 bug（都在读数出来之前被自检抓到）═══

  1. **职责错位**：把"链式别名传播"塞进 `demote_binds` ⇒ 自检 E 失败。
     它属于 `inline_expr` 的递归展开 —— 拆开后 A4 契约盯着不许再合。
  2. **漏掉 A 形态** ⇒ 归因里 `other` 一档吸走 43.7%，读数是假的。
  3. **对照口径不一致**（见第四节那张表的注）——重做后才敢比。
  4. **对拍把作者的 docstring 算进去** ⇒ 唯一成功案例被判 `elsewhere`。

  四次形状一致：**读数一旦极化（全有 / 全无），先怀疑量具**（第 24/25 条）。

═══ 九、契约 ═══

`tests/contract_alias_demote_test.py`（新，A–H 组）。其中三条不许退化：

  · **C 组方向不变量**：降级产物的自由变量里**不许再出现被测模块的 import 别名**
    ⇒ 谁把它改回"往外展"都会在这里被抓住（这就是本轮最要紧的不变量）。
  · **B 组两条自限，各带反证**：他模块不降级（否则凭空造出不存在的名字）、
    时间/随机拒收。
  · **F 组规则唯一性**：157/162 两个脚本都不许自带第二份规则实现。

═══ 十、残留（⛔ 全是削弱结论的方向）═══

  · **40 个里只成 1 个**，且这 1 个的形态是 `cmp` 翻转 —— 合成层的射程
    **没有因此变宽**，变宽的是「能拿到判据」这件事。
  · 自动抽出的判据是在**参照实现**上验过的（P2P/F2P 的标准定义，
    SWE-bench 同口径）⇒ 它不代表"在从没见过的 bug 上也成立"。
  · `full_pass` 是**必要非充分**（160 的 P3：另一种改法也能全绿）⇒ 本轮
    三个凑数解都是未全绿的，侥幸没冒充成功；held-out 闸**仍然没跑过**。
  · 样本仍是 boltons **单仓**；137 个候选之外的分布未知。
  · 归因里的 `other` 712 条**没再细分** ⇒ 里面可能还有第二大类可救形态。
  · `93185b2` 自动抽取注定 0 条：它的 F2P 依赖 `date.today()` ⇒ 被自限 2 拒收
    （这是**正确的拒收**，谁为了救它去放宽那一条，谁就是引入了随机判据）。

### 163. ⛔⛔ **瓶颈定位完成 —— 形态是唯一的硬门槛，密度不是；以及一张作废的表**（2026-10-28）

═══ 一、起点：162 收尾时的推断，本轮被摊漏斗推翻了一半 ═══

162 收尾写下的下一步是「细分 `other` 712 条，找第二大类可救形态」。先摊开了
162 自己的漏斗才发现**最大的那道门已经挪位置了**：

    40 → 抽不到可用判据 29 → 抽不到有鉴别力的 17 → **有判据有鉴别力但修不好 16**
    → 真正修好 1（当时以为）或 2（真读数）

按第 157 条（串联门要先开最大的那道）：该投的**不再是上下文门**。于是改问一个
从没问过的问题 —— **那批已经拿到判据的，形态分布是什么？**

═══ 二、⛔ 第一次交叉：拿到判据 ≠ 形态够得着 ═══

161 那次形态普查是在**全部** bug 上做的。它没回答「已经过了抽取这一关的那些
长什么样」—— 而这两个分布可以完全不同。

| 分组 | n | X（够不着） | L3（射程内） |
|---|---|---|---|
| **有鉴别力判据** | 11 | 9 (82%) | 2 (18%) |
| 无鉴别力判据 | 29 | 26 (90%) | 2 (7%) + L2 1 |

⇒ **两件事基本独立**：抽取做到再多，也换不来「合成层够得着」。

⛔ 更硬的一行：有判据却够不着的 9 个里，**`extend=E`（扩候选集即可达）是 0 个**，
全是 `N`（必须凭空造新代码）。⇒ 161 说的「扩候选集天花板 4.7%」，在**已过抽取
那一关**的子样本上是 **0**。这条路堵死了。

═══ 三、⛔⛔ 正式读数（`--reach --out`，可复现）═══

`scripts/probe_auto_unlock.py --reach --out /tmp/auto_unlock_r85.json` —— 一次
运行同时输出 k / f2p / 形态档 / 结果，四个数不再需要两次脚本才能拼起来。

    40 个真实 commit → no_criteria 9 → no_f2p 14 → 进入合成 17
                     → found 3（其中 2 个全绿且落在人类那一行）→ **成功 2**

═══ 四、⛔⛔ **核心结论：形态是唯一硬门槛** ═══

| reach | 总 | 有鉴别力判据 | **成功** |
|---|---|---|---|
| **L3** | 4 | 2 | **2/2 (100%)** |
| L2 | 1 | 1 | 0/1 |
| X | 35 | 14 | 0/14 |

⇒ 在 40 个样本上，**形态档完全决定了成败**（L3 ∧ f2p≥1 ⇒ 100%；其余 ⇒ 0%）。

═══ 五、⛔ 反过来看：**密度 k 在这个样本上没有区分力**（修正 160）═══

160 量出「只给 k=1 条判据 ⇒ 5/5 全错，k≥3 才收敛」。那是在**同一个 bug 上人为
只喂 k 条**。真实自动抽取给的不是这个分布：

    成功者：k=16, k=59
    失败者：k=6 …… k=72（其中 k=70 / 72 / 62 三个全都失败）
    f2p 条数同样无区分力：成功的最小 f2p=2；失败的 f2p=22、19、19

⇒ **k≥3 在真实域是必要但 trivial 的条件**（只要有鉴别力判据，自动抽取天然给到
k≥6）。⇒ 160 的结论成立范围要收窄：**它约束的是人工喂判据的情形，不是瓶颈**。

═══ 六、⛔⛔ **最大的限制：`reach` 是事后 oracle** ═══

`reach` 由 `analyze(broken, fixed)` 算出 —— **它需要人类已经修好的那份源码**。

⇒ 上面的 100%/0% **不是**一个可用的事前信号，不能说"预测形态就能挑出可修的 bug"。
它只是一个**瓶颈定位**：告诉我们该在哪里投入，以及投入的天花板是多少。

⛔ 谁要把这个表读成「我们已经能用形态筛选题目」，谁就把 oracle 当成了 predictor。

═══ 七、⛔ 反过来量大门：**这台装置的天花板到底是多少** ═══

    形态 L3 的占比：4/40 = 10%
    其中能自动抽到鉴别力判据的：2/40 = 5%  ⇐ **这才是当前装置的天花板**
    落到这一档之后的命中率：2/2 = 100%

⇒ 这是一台「**命中率 100%、覆盖率 5%**」的装置。要提高覆盖率，**唯一**路径是
扩大「合成层射程」（让更多的形态变得够得着）；而 X 那一群里 `extend=E` 是 0
⇒ 扩候选集（GenProg 那类搬子树）无效 ⇒ 必须要真正的**结构化代码生成**能力。

═══ 八、⛔ 成功数从 1 变成 2，得先验它不是假的 —— 三对照反证 ═══

`synthesize_patch` 内部要是泄漏了基线，它也能"修好"，那样"修好"与"判据"之间
就没有因果关系。三组对照（同一 buggy，只改判据）：

| | `c1c25da` | `766b554` |
|---|---|---|
| A 真（原样判据） | `exact_file` ✅ | `same_core_fix` ✅ |
| B 抽掉 F2P | **no_fix** ✅ | **no_fix** ✅ |
| C 篡改判据期望 | **no_fix** ✅ | **no_fix** ✅ |

⇒ 抽掉失败信号就搜不到、把期望改了就不再给原答案 ⇒ **答案确实是搜出来的**。

⛔ **C 组第一版是恒真的**：只变异了 `expect` 字段，而 `c1c25da` 那两条 F2P
**恰巧全是 `raises` 类**（期望抛 `ValueError`）⇒ 一个字节都没改到，对照形同虚设。
补了 `raises` 类的变异才有用。第 131 条的形状：**一种变异测不出「够用」**。

 **`c1c25da`（boltons Bits 长度检查 off-by-one）**：k=16、F2P=2，
`base_ok 14/16 → new_ok 16/16`，`full_pass=True`，**整份产出与人类修复逐字相同**。

═══ 九、162 第五节那张表作废 ═══

`scripts/probe_auto_unlock.py` 的新增 `--out` / `--reach` 参数不是顺手加的：
162 那张分布表读自**未提交的一次性 /tmp 脚本**，口径一变就再也无法自证。现在读数
都由仓库里那份脚本产出，契约覆盖。作废原因与替代读数见 162 第五节抬头。

═══ 十、契约 ═══

`tests/contract_alias_demote_test.py` 增两组：

  · **I 组**：把第八节的三对照**离线化**（玩具 bug，`x > 3` → `x >= 3`）。三条都是
    负向断言（必须**无解**/必须**换答案**）⇒ 谁把合成层改成恒返回某个东西，
    这三条会同时红。
  · **J 组**：钉住**沙箱返回结构**这个坑 —— `run_in_sandbox(...).results` 的元素
    只有 `{name, passed, evidence, why}`，**没有 `got`**；而 `run_all()` 是另造的壳、
    它**有** `got`（从 evidence 字符串 split 出来）。同一个概念两套键名，按错的
    那套读恒得到 None（第 59 条）。本轮踩过一次。

═══ 十一、残留（⛔ 全是削弱结论的方向）═══

  · **单仓库样本**（boltons），137 个候选之外的分布未知；**n=2**。
  · **held-out 闸仍然没跑过**（`no_gate` 口径，与 159 同）；`full_pass` 是必要
    非充分（160 的 P3）—— 本轮那 1 个 `elsewhere`（`1e61524`）就是没全绿的。
  · **`reach` 是事后 oracle**（第六节），别当 predictor 用。
  · `other` 712 条的细分 **已否决**：第二节证明投这块换不到 L3。
  · `93185b2` / `64e6430` 形态明明是 L3，却因为抽不到鉴别力判据（`64e6430` 的
    F2P 依赖多步副作用、`93185b2` 依赖 `date.today()` ⇒ 被自限 2 正确拒收）
    ⇒ **天花板的另一半损失在抽取，不在形态**。

### 164. ⛔⛔ **装载门：换仓库后连门都进不去 —— 开门了，但收益是 0**（2026-10-28）

═══ 一、起点：用户拍板「换第 2 个仓库验证 5%」═══

163 在 boltons 上量出「覆盖率 5%」。要判断这个数是普遍规律还是 boltons 特有，
必须先做第 133 条要求的那一步：**零命中先证查询能命中**。结果不是「覆盖率低」，
是**根本读不到**：

| 仓库 | 取到源码 | 沙箱**装得起** | 抽得到判据 |
|---|---|---|---|
| boltons（对照） | 30/30 | 29/30 (97%) | 27/30 (90%) |
| more-itertools（同类） | 25/25 | **2/25 (8%)** | **0/25** |
| attrs（异类） | 25/25 | **2/25 (8%)** | 2/25 |

Root cause 只有一处，**与能力无关**：

    more_itertools/more.py:46
    ImportError: attempted relative import with no known parent package

沙箱把被测模块当 `task.py` **单独 exec** ⇒ 没有父包 ⇒ 任何包内相对导入都炸。
boltons 恰好不用相对导入 —— 当初选它的理由正是「113 文件零依赖」（第 30 条）
⇒ **5% 是选择偏差的产物**：我们挑了一个装置恰好能读的仓库，然后在它上面量出 5%。

═══ 二、⛔⛔ 门被重新排序：装载门拦掉 92%，它站在最前面 ═══

之前排的门是 抽取 → 鉴别力 → 形态。现在前面还站着一道，而且它拦掉 **92%**：

> **装载**：被测模块能不能被沙箱装起来。

═══ 三、落地：整包装载 `run_in_sandbox(pkg=...)` ═══

`pkg = {"name": 包名, "module": 模块名, "modules": {相对名: 源码}}`。装载顺序：
同包依赖模块（拓扑序）→ 被测模块（**先注册到 `sys.modules` 再 exec**）→ `__init__`。

⛔⛔ **隔离边界不因它而削弱**（这是本轮最要紧的一条）：源码仍**全部**来自调用方
传入的 dict，runner 一行文件系统都不读、不联网；变的只是"这批不可信源码内部能
否互相 import"。越界读/写、deny-modules、audit 记账照常 —— **契约 C 组逐条盯**。

═══ 四、读数（`scripts/probe_package_load.py`，可复现）═══

| 仓库 | n | 装得起·前 | 装得起·后 | 抽得到判据 |
|---|---|---|---|---|
| boltons | 25 | 24/25 (96%) | **24/25 (96%)** ← 无回退 | 24/25 |
| more-itertools | 25 | 2/25 (8%) | **25/25 (100%)** | 0/25 |
| attrs | 25 | 2/25 (8%) | **25/25 (100%)** | 25/25 |

⛔ boltons 唯一的失败是 `fcntl` 被 deny-modules 拦（**正确的拒收**），前后同一条。

═══ 五、⛔⛔ **开门之后，端到端收益是 0** ═══

`probe_auto_unlock.py --repo ... --subdir src/attr/`（整包装载已贯通到合成层）：

    attrs 25 个真实 commit → no_criteria 12 → no_f2p 13 → 修好 **0**

漏斗（25 个合计）：

    断言 1715 → 剔掉 1093 条（64%，**期望值不可序列化**）→ 候选 877
              → 过三验 58 → **F2P 0**

⇒ **装得起来了，但拿不到一条有鉴别力的判据。** 到手的 58 条在缺陷版上全通过。

原因很具体：attrs 的断言绝大多数是**对象相等**（`assert attrs.fields(C) == (...)`），
而沙箱走 JSON ⇒ 期望值必须是字面量 ⇒ 这类判据**结构上进不了 V6**（162 第六节
那条限制，在 attrs 上从「7 条」变成了「64%」）。

═══ 六、⛔ 第三道门：断言的**写法风格** ═══

more-itertools 装载 100% 了，却抽到 **0 条**判据。量出来的原因：

| 仓库 | 裸 `assert` | `self.assertX` | unittest 占比 |
|---|---|---|---|
| boltons | 549 | 7 | 1% |
| **more-itertools** | 20 | **10013** | **100%** |
| attrs | 1638 | 0 | 0% |

⇒ 抽取层只认裸 `assert` 与 `pytest.raises`；`unittest.TestCase` 那套
（`self.assertEqual(a, b)`）一个都不认。这对 more-itertools 是 **100% 的墙**。

═══ 七、⛔ 于是门又重新排了一次（第 157 条：开完一道就重新排序）═══

    装载门（拦 92%）  ──已开──►  **判据表达力门**  ──► 鉴别力 ──► 形态

现在最大的一道是**判据表达力**：① 期望值必须可序列化；② 只认裸 assert。
⛔ 注意这道门**不扩张判据形态**就能推进第二条 —— `self.assertEqual(a, b)` 可以
直接映射成已有的 `expect` 形态（`assertTrue` → truthy）；而 `assertNotEqual` /
`assertIn` 这类**映射不成**（我们没有"不等于"判据）⇒ 属于真扩张，**需用户拍板**。

═══ 八、实踩的三个 bug（都在读数出来之前被自检/契约抓住）═══

  1. **无关模块连坐**：第一版把**整个包**塞进沙箱 ⇒ boltons 从 **100% 掉到 0%**。
     `ecoutils.py` 会 `import getpass → termios`，而 `termios` 在 deny-modules 里
     ⇒ 一个八竿子打不着的模块让整批装载失败。⇒ **按依赖闭包裁剪**
     （`extract.needed_modules`）。契约 E3/E4 用同一份源码做反证。
  2. **`from . import a, b` 形态整个漏掉**：`ImportFrom` 的 `module` 是 **None**，
     依赖藏在 `names` 里 ⇒ attrs 25 个里 23 个报
     `cannot import name '_config' from 'attr'`。补识别 + 父包属性绑定。
  3. **排序误判**：为修循环依赖引入的 `pend = set(stack)` 把**同层兄弟节点**也
     当成"已满足" ⇒ `setters` 被排到它依赖的 `_config`/`exceptions` **前面**
     （实测 attrs `validators.py` 得到 `[_compat, setters, _make, _config, ...]`）。
     ⇒ 循环检测必须按 **DFS 路径**（`onpath`）判，不能按"在不在栈里"。契约 E5。

═══ 九、契约：`tests/contract_package_load_test.py`（A–G）═══

  · **C 组隔离不变性**（最要紧）：传了 `pkg` 之后，越界读 / 越界写 / deny-modules
    （termios）/ 沙箱记账字段 / "未在 modules 里的同包模块不去磁盘找" —— 五条全在。
    谁为了让装载率好看去放宽任何一条，C 组会红。
  · **A 组向后兼容**：不传 `pkg` ⇒ 单文件路径一字不变（boltons 的两个成功案例
    回归读数与 163 完全一致：k=16/f2p=2→14→16、k=59/f2p=7→52→59）。
  · **E 组依赖裁剪**：含"不裁剪 ⇒ 连坐失败"的反证，与"兄弟节点不算已满足"的
    顺序不变量（第 3 个 bug 的回归闸）。

═══ 十、残留（⛔ 全是削弱结论的方向）═══

  · **开门的收益是 0**（第五节）。装载率 8%→100% 是实测，但它**没有**转化成
    任何一个被修好的 bug —— 这是本轮最该被记住的一句。
  · attrs 判据的 64% 因不可序列化被丢弃 ⇒ 这个比例是**结构性**的（沙箱走 JSON），
    不是抽取层能修的；要动就得动判据/沙箱的**传参方式** ⇒ 属扩张，**未做**。
  · more-itertools 的 unittest 门 **未动**（第七节），做了它才谈得上"开门有收益"。
  · boltons 的 5% 仍然是**单仓库**读数；现在多了两个仓库，但它们的 0% 成因不同
    （attrs=判据表达力，more-itertools=断言风格）⇒ **仍没有第二个仓库的覆盖率**。
  · `reach`（形态档）依旧是**事后 oracle**（163 第六节），别当 predictor 用。
  · 依赖裁剪用的是 **AST 静态依赖**：`importlib.import_module` 那种动态导入、
    条件导入（`if sys.version_info`）都看不见 ⇒ 装载失败会**如实**报，不静默。

### 165. ⛔⛔ **unittest 抽取层落地：判据 0 → 544；以及一个被当场抓住的「假 F2P」——那 69 条有鉴别力的判据全是量具造的**（2026-10-29）

═══ 一、起点：164 留下的一道 100% 的墙 ═══

| 仓库 | 裸 `assert` | `self.assertX` | 抽取层能读 | 端到端判据 |
|---|---|---|---|---|
| boltons | 549 | 7 | ✅ | 59 / 16 条 |
| **more-itertools** | 20 | **10013** | ❌ | **0 条** |
| attrs | 1638 | 0 | ✅ | 58 条（F2P 0） |

⇒ 抽取层只认裸 `assert` 与 `pytest.raises`，`unittest.TestCase` 那套不认。
more-itertools 是 **100% unittest** ⇒ 装得起来（164 已开到 100%），但一条判据
都拿不到。这是当时排序后**最大的一道门**：判据表达力。

═══ 二、落地：只做**同形映射**，一步都不扩张 ═══

判据层只有两种形状：`expect`（探针 == 一个可 JSON 的值）与 `raises`。
所以只收**写法不同、形状相同**的：

    assertEqual(a, b)      ≡ assert a == b      ⇒ expect  ✅
    assertTupleEqual(a,b)  ≡ assert a == b      ⇒ expect  ✅（但见第五节）
    assertRaises(E)        ≡ with raises(E)     ⇒ raises  ✅（with 式 + 直接调用式）

⛔⛔ **需要新比较语义的一律不收**（`_UNITTEST_SAME` 上方的长注释是裁决依据）：

    assertNotEqual ⇒ ≠     assertTrue/False ⇒ truthy    assertIn ⇒ ∈
    assertIs ⇒ 身份        assertAlmostEqual ⇒ 容差      assertRaisesRegex ⇒ 匹配

把它们塞进 `expect` 是**造假判据**：形状一样、语义不同。契约 D 组用**注入反证**
证明这条拦得住（放开 `assertNotEqual` 立刻产出 `first([1]) == 2`，而原意是 ≠ 2）。
⇒ 要不要扩张是**形态决策**，需用户拍板（第 126 条），本轮不做。

配套三件（都在 `aether/extract.py`）：

  1. **期望侧也要内联**：`assertEqual(a, expected)` 里 `expected` 是局部变量
     ⇒ 第一版 `literal_eval` 拿不到值 ⇒ **整条丢弃**。more-itertools 的 921 条
     assertEqual 里 **293 条**长这样。新增 `gold_value`（值）与 `gold`（原文）
     两个字段 —— ⛔ 只留原文的话调用方会静默拿到 0 条。
  2. **父包降级（试探性）**：测试用 `mi.first(...)`（包的公开 API），而沙箱装的是
     `more.py` **本体**（里面没有 `mi`）⇒ 628 条一条都跑不起来。现在把它降成
     `first(...)`。⛔ 它与"按被测模块降级"**不是一个可信度**：后者静态可证，
     前者只证明"这名字是从包上取的" ⇒ **只能靠三验兜底**（名字不存在 ⇒
     NameError ⇒ 剔除），并且**单独记账**（`n_pkg_demote=729`）。
  3. **记账不静默**：`n_unmapped` + `unmapped_kinds` 分布、`n_tuple_gold`、
     `n_gold_resolved`、`n_pkg_demote` —— 每一条被丢的都有名字和理由。

═══ 三、读数（`tests/contract_unittest_extract_test.py` I 组，可复现）═══

    more-itertools/tests/test_more.py（870 条断言）
      n_out = 544       （改前：0）
      n_unjsonable 242  （期望值不可序列化，结构性的 —— 162 第六节那条）
      n_tuple_gold  84  （第五节：全是假判据）
      n_gold_resolved 208（期望侧内联救回）
      n_pkg_demote  729（父包降级救回，待三验收割）
      n_unmapped    128（拒收：assertTrue 39 / assertFalse 35 / assertIn 9 /
                         assertRaisesRegex 8 / assertNotIn 8 / …）

boltons **不退化**（A 组回归）：`c1c25da` k=16→18 f2p=2 `exact_file`、
`766b554` k=59→65 f2p=7 `same_core_fix`，两个都仍 `full_pass=True`。

═══ 四、⛔⛔ 端到端：修好仍然 0/6，而且第一次的读数是**假的** ═══

`probe_auto_unlock.py --repo more-itertools --subdir more_itertools/ --limit 6`：

| | 修前（第一版） | 修后（拦掉假判据） |
|---|---|---|
| 可用判据 | 383–393 | **250–255** |
| **F2P** | **68–70** | **0** |
| 修好 | 0/6 | **0/6** |

═══ 五、⛔⛔ **那 69 条"有鉴别力的判据"是量具自己造的** ═══

第一版跑出 F2P≈69 时，最自然的解读是"判据够密了、卡在合成层"。**错了**：

    minmax(2, 3, 1) || got=(1, 3) expect=[1, 3]

沙箱用 **JSON** 传 `expect` ⇒ **tuple 被降级成 list**，而 `got` 是真 tuple ⇒
`got == expect` **恒为 False** ⇒ 这类判据在**任何**版本上都失败 ⇒ 全被算成 F2P。

⛔⛔ 为什么三验没拦住（这是最该记住的一条）：

    三验比对的是  got 的 literal_eval  vs  gold        （两边都是 tuple ⇒ 相等 ✅）
    判据执行比的是 JSON 往返后的 expect vs got         （list vs tuple ⇒ False ❌）

**同一件事有两套比较口径** ⇒ 三验放行了它自己执行时会失败的东西。
修法是**两处都动**：抽取层 `_has_tuple` 显式丢弃并记账（`n_tuple_gold`），
`probe_auto_unlock` 的三验第 1 步**补上 `passed`**（`n_unpassed_on_fixed`）。
契约 J 组两头都盯（J5 是源码级检查：脚本里必须有 `a.get("passed")`）。

⇒ 如果只修一处，另一处会让它**以另一种形状回来**。

═══ 六、⛔ F2P=0 是**真的**，不是第二个量具 bug（n=1 证物）═══

按第 25 条，一个新出现的 0 必须先自证。取 `67fcf64`（"honour a falsey validator
in bucket"），人类新增的测试**恰好三条**，一条都进不来：

```python
class AllowedKeys(set):            # ← 测试函数**内部**定义的辅助类
    def __call__(self, key): return key in self
allowed = AllowedKeys()
self.assertFalse(allowed)                                  # ① truthy 形态 ⇒ 拒收
D = mi.bucket(iter([10, 20, 11, 21]), key=lambda x: x, validator=allowed)
self.assertEqual(list(D[10]), [])                          # ② 辅助类在沙箱里不存在 ⇒ 上下文门
self.assertNotIn(10, D)                                    # ③ 需要 ∈ 判据 ⇒ 拒收
self.assertEqual(set(D), set())                            # ④ set 不可序列化 ⇒ 丢
```

人类修的是 `validator or (...)` ⇒ `validator is None`。四条断言分别卡在
**truthy 形态 / 上下文（辅助类）/ ∈ 形态 / 不可序列化** —— 与 163 的三道门
完全同形，只是这次是**判据那一侧**的门。

⛔ n=1，且是手工归因。**下一步**要做成可复现的普查（按 commit 归因"PR 新增断言
各被哪道门拦掉"），现在不能说"more-itertools 的 F2P 恒为 0"。

═══ 七、契约：`tests/contract_unittest_extract_test.py`（A–J）═══

  · **C/D 组（最要紧）**：非同形断言不产出，且 D 组注入反证证明"0 条"是**拦的**
    不是没扫到。⛔ 第一版用 `assertTrue` 做反证是**恒假**的（它是单参断言，放进
    表也产不出）⇒ 改用双参的 `assertNotEqual`，顺带演示"放开就造出语义相反的判据"。
  · **F 组**：父包降级默认不开、多段属性（`mi.recipes.f`）不许降。
  · **J 组**：tuple 期望不产出 + 反证（list 期望**不**被误拦）。

═══ 八、实踩的四个 bug（都在读数之前被契约/自检抓住）═══

  1. **父包降级的兜底写法全漏**：第一版写成"strict 失败才试 pkg"，而
     `resolve_probe` 只要**动过**就返回非 None（内联一个变量也算）⇒
     `mi.last(iterable)` 早就在 strict 里"成功"了 ⇒ pkg 分支永不进入
     ⇒ 628 条只救回 159 条，剩下的全带着 `mi.` 进沙箱（那里没有 `mi`）。
     ⇒ 改成**两条都跑**，差额即为 `n_pkg_demote`。
  2. **属性链中段被误降**：`mi.recipes.f` 的内层 `mi.recipes` 会被自底向上的
     `generic_visit` 单独判一次（tail 恰好单段）⇒ 整条链变成 `recipes.f(1)`
     —— 一个被测模块里根本不存在的名字。⇒ `_Demote` 引入 `_in_chain`。
  3. **假 F2P**（第五节）：两套比较口径。
  4. **契约 A3 随第五节变了口径**：`assertTupleEqual` 形状同形但期望**必然是
     tuple** ⇒ 结构性不可达。⛔ 不放进映射表又会留下"表里能抽、实际抽不到"的
     假象 ⇒ 保留在表内，归因记在 `n_tuple_gold`。

═══ 九、残留（⛔ 全是削弱结论的方向）═══

  · **端到端修好仍是 0/6**。判据 0→544 是实测，但它**没有**换来任何一个修好的 bug
    —— 与 164「开门的收益是 0」是同一句，只是这次门开在判据侧。
  · F2P=0 的归因目前是 **n=1 手工**（第六节），未做普查。
  · 163 那张表里的 boltons 判据条数**已变**（16→18、59→65，因期望侧内联）；
    结论（修好 2/2）未变，但**数字不能与旧表混用**。
  · `truthy` / `∈` / `≠` 三种形态仍未做（第二节），more-itertools 有 128 条
    （单文件）被这道形态门挡在外面 ⇒ 这是**扩张**，需用户拍板。
  · attrs 的 64% 不可序列化、本次的 242 条，同为**结构性**限制（沙箱走 JSON），
    要动就得改判据传参方式 ⇒ 属扩张，未做。
  · `reach` 依旧是事后 oracle；held-out 闸仍未跑过（`no_gate` 口径）。

### 166. ⛔⛔ **门归因普查：more-itertools 的 F2P=0 是真的（10 commit / 43 断言）；顺带实测「解开 form 门最大的一格，收益是 0」**（2026-10-29）

═══ 一、起点：165 的两个 0 只能靠手工看一个 commit 解释 ═══

165 结束时写下的东西是**不可复现**的：为了证明 more-itertools 的 `F2P=0`
不是量具瞎了，手工打开 `67fcf64` 看了它新增的四条断言，逐条归因。**n=1，
且是人工的** ⇒ 165 自己白纸黑字写着「现在不能说 more-itertools 的 F2P 恒为 0」。

本轮把它做成普查：`scripts/probe_gate_attribution.py`。

═══ 二、口径：只统计 **PR 自己新增的断言** ═══

⛔ 对整个测试文件做门分布**毫无意义** —— 那测的是"这个仓库的测试长什么样"，
不是"修这个 bug 需要什么判据"。

    新增 = fixed 版测试文件里有、父提交里没有的 `(函数名, 语句)` **多重集差**

⛔ 两个必须：按 `(fn, stmt)` 而不是只按 `stmt`（重名率极高，只按语句会把
存量当增量）；用**多重集**不是集合（同一函数里写两条一样的断言是合法的）。

═══ 三、门：每条断言**恰好一格**（守恒）═══

    ── 抽取层（纯 AST，`aether/extract.py` 的 `attribute_statements`）──
    form            判据形态不支持    unserializable  期望值不可 JSON
    tuple           期望是 tuple      unstable        时间/随机依赖
    ── 沙箱层（真跑，**复用** `probe_auto_unlock.auto_extract` 的三验）──
    ctx             参照实现上就跑不过/跑不稳/与写死期望不符
    P2P             两版都过 ⇒ **无鉴别力**      F2P  参照过、缺陷挂 ⇒ **真有鉴别力**

⛔⛔ **三验不许自带第二份**（165 第五节）：`auto_extract(detail=...)` 现在会
吐出**逐条**裁决（`ok / no_result / unstable / unpassed_on_fixed /
gold_mismatch`），归因脚本只负责把它**对回**测试源码，不重新判一次。
契约 E 组盯着恒等式 `#{gate==criterion} == len(extract_criteria())`，
且**集合也要相同**（只比条数会漏掉"规则漂移但条数巧合"）。

═══ 四、⛔⛔ 读数：三个仓库，同一把尺 ═══

| 仓库 | commit | 新增断言 | form | unserial | tuple | unstable | ctx | P2P | **F2P** | 有 F2P 的 commit |
|---|---|---|---|---|---|---|---|---|---|---|
| **boltons** | 10 | 53 | 7 (13%) | 7 (13%) | 0 | 0 | 7 (13%) | 10 (19%) | **22 (41.5%)** | **6/10** |
| **more-itertools** | 10 | 43 | 10 (23%) | 11 (26%) | 0 | 0 | **16 (37%)** | 6 (14%) | **0** | **0/10** |
| **attrs** | 10 | 50 | **26 (52%)** | 21 (42%) | 0 | 0 | 3 (6%) | 0 | **0** | **0/10** |

（`--limit 10`，`n_skip=0`。落盘：`/tmp/gate_{boltons,mi,attrs}.json`）

═══ 五、⛔⛔ 结论一：那两个 0 是**真的**，不是量具瞎了 ═══

这是本轮唯一能回答 165 开放问题的证据，而且**必须靠对照才成立**：

    **同一把尺、同一个脚本、同一套口径，boltons 读出了 22/53 的 F2P、6/10
    个 commit 有鉴别力** ⇒ 尺子能读出 F2P ⇒ more-itertools / attrs 的 0
    不是"尺子恒 0"。

反过来看 more-itertools 也不是"沙箱根本没跑起来"：**6 条进了沙箱、两版都过
（P2P）** ⇒ 判据真的被执行了，只是那个改动不在这些断言覆盖的行为上。
attrs 则是另一回事：50 条里 **47 条倒在前两道结构性门**（form 26 + unserial
21），只有 3 条进过沙箱 ⇒ 它的 0 比 more-itertools 更"脆"。

═══ 六、⛔⛔ 结论二：**解开 form 门最大的一格，收益实测是 0** ═══

普查第一次跑出来时，form 门最大的一格不是 165 猜的 truthy/∈，而是
**`assertRaises` 10 条（占 form 的一半、总断言的 23%）**：

    self.assertRaises(FalsyError, lambda: mi.one(count(), too_long=too_long))

而它**是同形写法**（≡ `with raises(E): X`，不引入任何新比较语义）⇒ 165 的
实现只认 `Name/Attribute` 作 callee，**这是漏，不是扩张** ⇒ 补上（只收
**无参** lambda；带参的 lambda 参数无从求值，那才是扩张，不收）。

补完重跑，把这 10 条从 form 门里放出来：

| | form | ctx | P2P | **F2P** |
|---|---|---|---|---|
| 补之前 | 20 (46.5%) | 6 (14%) | 6 | **0** |
| 补之后 | **10 (23.3%)** | **16 (37.2%)** | 6 | **0** |

⇒ **10 条一条没变成 F2P，全部落在 ctx**（13 条 `unpassed_on_fixed`：在参照
实现上就抛不出那个异常 —— 探针里的 `too_long` 是测试函数内的对象，字面内联
不了 ⇒ 这正是 157 那道**上下文门**）。

⛔ 这条要记牢：**串联门不是"开最大那道就有收益"**。163 说"形态是唯一硬门槛"
是在 boltons 上量的；换到 more-itertools，形态门开了，上下文门立刻接住。

═══ 七、回答 r85 留下的问题：**5% 确实是 boltons 特有的，而且三个仓库的瓶颈各不相同** ═══

  · **boltons**：三道门各占 13% 左右，**41.5% 的新增断言真有鉴别力** ⇒ 它
    的瓶颈不在判据侧，在下游（合成层形态门，163 已量）。
  · **more-itertools**：**上下文门最大（37%）**，其次不可序列化（26%）、
    形态（23%）⇒ 三道各拦一块，**没有任何一道是"开它就能通"**。
  · **attrs**：**形态门 52% + 不可序列化 42% = 94%**，且 form 里 24 条是
    **裸 assert 的非 `==` 形态**（`assert x != y` / `assert not ...` /
    `assert x in y`）—— 与 more-itertools 的 truthy/∈ 不是同一个来源。

⛔ 所以"换个仓库能不能复现 5%"这个问题，答案不是"能不能"，而是
**"每个仓库的测试风格决定了它倒在哪道门上"** —— 覆盖率数字跨仓库不可比，
除非同时报门分布。

═══ 八、⛔ 口径修订（165 的数字**不能**与本轮混用）═══

  1. `n_out`：more-itertools/test_more.py **544 → 582**（lambda 补漏 +38）。
  2. `n_unmapped`：**165 → 127**。两个原因：① lambda 那批被收了；
     ② 修掉一个**双计数** —— `with self.assertRaises(E):` 的 context_expr
     本身也是个 Call，会被扫描器**再记一次**（单参 ⇒ 判为"形态不支持"）
     ⇒ 一条 with 变成两条记录，其中一条是凭空造的。
  3. 旧 `unmapped_unittest_calls` 把 `assertRaises` **一律**算"支持"（不管
     能不能映射）⇒ 低估了拒收。现在它是 `_iter_all_probes` 的投影，只算
     **真的没映射上**的那些。
  4. boltons 两个成功案例在**新增口径**下重测：`c1c25da` 3 条新增断言
     F2P=2、`766b554` 12 条 F2P=7 ⇒ 与 163 的 `f2p=2 / f2p=7` **完全吻合**
     （说明那两题上"全量判据"与"PR 新增"口径一致），且零 loss。

═══ 九、重构：判据形态只定义**一次** ═══

165 的假 F2P 是"两套口径"的产物。为免重演，本轮把三个 `scoped_*` 与
`unmapped_unittest_calls` 全部改成 `_iter_all_probes` 的**投影**：

    _iter_all_probes()  ← 唯一枚举（含被拒收的，带 stmt / attr / family）
      ├─ scoped_assertions        （bare 家族的同形部分）
      ├─ scoped_raise_probes      （pytest 家族）
      ├─ scoped_unittest_probes   （unittest 家族）
      └─ unmapped_unittest_calls  （unittest 家族里 mapped=False 的）

⛔ 改前先存了基线（五个真实测试文件的 a/b/c/u/out 全部逐条对拍），改后
**一字不变**。产出顺序用**稳定排序**保持与旧的三段拼接一致（否则只是换了
顺序，就会让"条数没变"掩盖"索引变了" ⇒ `auto{j}` 跟着变 ⇒ 跨轮不可比）。

═══ 十、契约 ═══

  · `tests/contract_gate_attribution_test.py`（新，A–H）：A 守恒（归因条数
    == 探针条数）；B **五格都有正例**（量具有区分力，不是"全落一格"）；
    **C 每一格的 0 都能反证**（放开 `assertNotEqual` ⇒ form 减少且确实产出
    ——⛔ 用单参的 `assertTrue` 做反证是**恒假**的，165 已踩过）；D 三个扫描器
    读数**写死**对拍；E 同源恒等式；F 沙箱侧的格不许在抽取层伪造；
    G 真实仓库读数；H 多重集差口径。
  · `tests/contract_unittest_extract_test.py` B5–B7：lambda 同形写法收、
    **带参 lambda 不收**。B5 第一版写错了期望（忘了父包降级会把
    `mi.first([])` 降成 `first([])`）⇒ 先红后绿的价值。

═══ 十一、残留（⛔ 全是削弱结论的方向）═══

  · 每仓库只跑 **10 个 commit**（43/53/50 条断言）。够证明"不是 n=1"，不够
    说"more-itertools 的 F2P 恒为 0" —— 要那个结论得跑全量（40+）。
  · **F2P ≠ 能修好**：boltons 有 22 条 F2P、6 个 commit，但 163 实测只修好
    2 个 ⇒ 判据有鉴别力之后还有**合成层形态门**。本轮没有重跑合成层。
  · `tuple` / `unstable` 两格在三仓库都是 0 ⇒ 165 记的那两条规则在这一批
    样本上**没被触发**，不能说"已验证有效"。
  · attrs 的 form 门 24 条裸 assert 非 `==`、more-itertools 的 truthy/∈/≠
    ⇒ 解它们需要**新的比较语义**（≠ / truthy / ∈），属扩张，未做。
  · 不可序列化（26% / 42%）是**结构性**的（沙箱走 JSON），要动就得改判据
    传参方式 ⇒ 属扩张，未做。
  · `ctx` 里 13 条 `unpassed_on_fixed` 是**上下文门**（157 那道），本轮只是
    归因，没做任何解锁。
  · 仍未跑 held-out 闸（`no_gate` 口径）；`reach` 依旧是事后 oracle。

### 167. ⛔⛔ **整条链条的门分布：头号门是形态（35/40 修好 0），不是判据——这解释了连续三轮「开门收益 0」；并更正 166 一个错误归因（「不可序列化」那格真不可 JSON 的是 0 条）**（2026-10-29）

═══ 一、起点：三轮开门收益都是 0，该怀疑的不是某一道门 ═══

| 轮次 | 开的那道门 | 门开到什么程度 | 端到端修好 |
|---|---|---|---|
| 164 | 装载（整包装载） | 8% → **100%** | **0** |
| 165 | 判据（unittest 抽取） | 0 → **544** 条 | **0** |
| 166 | 形态（form 最大格 `assertRaises`） | 拦 20 → 10 条 | **0** |

三轮都开在**判据侧**。166 结尾还留着一个没量的地方：

    boltons：40 commit → 6/10 个有 F2P → **只修好 2 个**

那 4 个失败在哪，**从没归因过**。⇒ 本轮不接着开第四道门，先把整条链条
（判据侧 + 合成层）量成一张表，找出真正的头号门。

═══ 二、合成层停靠点：六格（`SynthResult.gate`）═══

    S0_blew       缺陷版自己在沙箱里就跑挂 / 候选一条都没跑成
    S1_no_focus   定位器没定位到任何顶层 def/class
    S2_no_cands   定位到了，但该处没有任何可枚举的变异
    S3_no_gain    候选都跑了，没有一个严格优于基线
    S4_partial    找到了但没全绿 ⇒ **凑数解**（161 的 `64e6430`）
    S5_full       找到了且全绿（⛔ 只是**必要**条件）

⛔⛔ **顺序就是诊断顺序，不许重排**：基线跑挂时 `focus` **必然**是空的
⇒ 先判 focus 会把「量具炸了」记成「定位不到」（第 25 条）。契约 B 组用
一个**字段几乎相同、只差 `base_ok`** 的对照钉住它。

⛔ `S0` 收两种同形情形（基线炸 / 候选全炸）：它们不是同一处代码，但
**诊断动作相同**（都是"尺子没量"，得先修量具而不是"世界没有解"）。分开
记会让「修不好」看起来像「搜不到」—— 那正是 `S3` 的含义。

═══ 三、⛔⛔ 读数：整条链条（boltons 全量 40 commit，恰好一格）═══

| 门 | commit 数 | 占比 |
|---|---|---|
| 判据：无鉴别力（`no_f2p`） | 20 | **50%** |
| 判据：抽不到（`no_criteria`） | 9 | 22% |
| 合成：S3_no_gain | 6 | 15% |
| **合成：S5_full（修好）** | **2** | **5%** |
| 合成：S1_no_focus / S2_no_cands / S4_partial | 1 / 1 / 1 | 各 2% |

═══ 四、⛔⛔ 真正的头号门：形态（`reach`），而且它是**硬门** ═══

把事后形态档（166 的尺子：L3=合成层候选集够得到，X=需要 INSERT/DELETE）
铺到**全链条**，而不是只看进合成层的那 11 个：

| reach | commit 数 | **修好** | 明细 |
|---|---|---|---|
| **L3** | 4 | **2** | `c1c25da`、`766b554` 修好；`93185b2`、`64e6430` 卡 `no_f2p` |
| L2 | 1 | 0 | `f5f5cd8` 卡 `no_f2p` |
| **X** | **35** | **0** | 9 `no_criteria` + 17 `no_f2p` + 9 进合成层**全败** |

⇒ 三条结论，每一条都改变下一步：

  1. **形态不可达的 35 个（87.5%），判据侧再顺也是 0**。合成层只做
     「翻转已有节点」，`reach=X` 要 INSERT/DELETE ⇒ **结构上够不到**。
  2. **形态可达的命中率极高**：L3 里进到合成层的 2/2 全修好；把卡判据的
     算进去是 2/4。⇒ 合成层在它的射程内**没有病**。
  3. **判据侧的全部可挖空间 = 3/40**（L3 卡判据的 2 个 + L2 的 1 个）。
     就算判据完美，上限也只是 5% → **10%**。

⛔ 这就是为什么 164/165/166 三轮收益都是 0 —— **不是运气差，是结构性
必然**：那三轮开的门都在「形态不可达的那 35 个」的上游。

⛔ `reach` 是**事后 oracle**（要看 fixed 源码才知道）⇒ 本节是**归因**，
不是预测能力。部署时我们不知道哪个 commit 是 L3。

═══ 五、⛔⛔ 更正 166：那格「不可序列化」里，**真不可 JSON 的是 0 条** ═══

166 记了 attrs 42%、more-itertools 26% 是「不可序列化」，并断言：

    「结构性限制（沙箱走 JSON），要动就得改判据传参方式 ⇒ 属扩张」

**这句是错的**。拆开看那 39 条：

| 仓库 | 该格总数 | (a) 期望真不可 JSON | (b) 期望侧**根本没解出值** |
|---|---|---|---|
| boltons | 7 | **0** | 7 |
| more-itertools | 11 | **0** | 11 |
| attrs | 21 | **0** | 21 |

⇒ **全部是 (b)**。166 把两者记成同一个数 `n_unjsonable`，后果是把
「能修的」记成「不可修」，从而在结论里**永久放弃**了它们。

拆开后还暴露第三种成因：**探针与期望写反了** —— `assert 1 == C().value`
把常量写在左边是合法等价写法，但抽取层按「左探针、右期望」的约定 ⇒
抽成 `call=1`（**恒真**）、`gold=C().value`（解不出）⇒ 这条断言彻底失效。
attrs 一个文件里就有 **49 条**长这样。

修法（都**不扩张**判据形态）：
  · `n_unjsonable` 拆成 `n_gold_unresolved` / `n_gold_unjsonable`
  · 只在「期望侧真的解不出值、而探针侧是常量」时**交换**，记账
    `n_gold_swapped`（两边都能解 ⇒ 不动，否则破坏跨轮可比性）

**重测三仓库**（10 commit / 新增断言口径）：

| 仓库 | | form | unser | tuple | ctx | P2P | **F2P** |
|---|---|---|---|---|---|---|---|
| boltons | 166→167 | 7→7 | 7→7 | 0→0 | 7→7 | 10→10 | **22→22** |
| more-itertools | 166→167 | 10→10 | 11→10 | 0→0 | 16→**17** | 6→6 | **0→0** |
| attrs | 166→167 | 26→26 | 21→**11** | 0→**3** | 3→**10** | 0→0 | **0→0** |

⇒ 10 条从「结构性不可序列化」挪回了「上下文门」（可修的那一类）。
⛔ 但 **F2P 仍然是 0** —— 又一次「修对了量具、收益仍是 0」。
boltons 全量端到端重跑：门分布与交换前**一字不变**（仍是修好 2/40）。

═══ 六、⛔ r83 那个「人工转写后能修」究竟靠的是什么 ═══

`93185b2`（boltons #302）人类修的是 `start < stop` → `start <= stop`
（纯 cmp 翻转，L3）。它 PR 新增的三条断言是：

    assert next(date_range_inclusive) == today
    with pytest.raises(StopIteration): next(date_range)
    with pytest.raises(StopIteration): next(date_range_inclusive)

三条**全含 `date.today()`** ⇒ 被自限 2（不可复现）拒收；⛔ 就算解开，
期望值还是 `date` 对象 ⇒ 进下一道「不可序列化」。

⇒ **人工转写时用的是另一套判据**（固定日期），不是把已有断言物化。
⇒ 所谓「n=1→3」里这一例，价值不在"物化率"，而在**人替机器造了新判据**
（把 `date.today()` 换成 `date(2020,1,1)`）。那属于**判据生成**，不是
判据抽取 ⇒ 是比"提高物化率"大得多的扩张。

⛔ 这条反过来削弱了 161/163 里「人工转写后能修」的分量：它证明的是
「判据到位后合成层能修」，**不是**「自动端到端能修」。

═══ 七、契约 ═══

  · `tests/contract_synth_gate_test.py`（新，A–F）：六格各自可达；
    **顺序不许重排**（B 组用"只差 base_ok 的对照"钉住）；`n_ran==0`
    不许落 S3（C 组）；S5 只是必要条件（D 组，与 161 契约 P3 同款）；
    格名写死对拍（E 组）；真实落盘读数（F 组，缺失则跳过并披露）。
  · `tests/contract_unittest_extract_test.py` **K/L 组**：两格不许合并、
    `n_unjsonable` 已不存在；写反要换回来且**两边都能解时不换**。
  · ⛔ 两组都做了**注入反证**：故意把判定顺序改错 / 去掉交换 / 合并回
    一格 ⇒ 契约分别红 5 项、6 项，还原后全绿。**没这一步，契约是恒真的。**

═══ 八、残留（⛔ 全是削弱结论的方向）═══

  · `reach` 是**事后 oracle**，不能当 predictor ⇒ 第四节是归因不是能力。
  · 每仓库 10 个 commit 的门分布（43/53/50 条）；只有 boltons 跑了全量 40。
  · **合成层只跑了 boltons**。more-itertools / attrs 的 F2P 是 0 ⇒ 它们
    的合成层停靠点**根本没被量过**（本表的六格只在 boltons 上有读数）。
  · 交换救回的 10 条落到 ctx 后**没有**再往下游追（是不是能解锁未知）。
  · `S4_partial` 只有 1 例、`S1`/`S2` 各 1 例 ⇒ 这三格的结论是 n=1。
  · 仍未跑 held-out 闸（`no_gate` 口径）；`S5_full` 只是必要条件。
  · 三个待拍板的扩张项一个没动：`truthy`/`∈`/`≠` 形态、沙箱传参方式、
    **真结构化代码生成**（攻形态门唯一有效的路，r83 已量扩候选集天花板
    仅 4.7% ⇒ 不是扩候选集能解决的）。

═══ 九、下一步（以此为准）═══

判据侧**已经到顶**：可挖空间 3/40，且第三/第四节都证明收益上界有限。
继续在判据侧开门是明确的浪费。三条路，成本递增：

  1. **收口**：把 more-itertools / attrs 也跑全量 40，把「F2P 恒为 0」
     从 n=10 收口到 n=40（当前这两个仓库的合成层读数**完全没有**）。
  2. **量化上界**：对 L3 那 4 个 commit 做「判据侧完美」的上界实验
     （人工给足判据，看是否 4/4 全修好）⇒ 能钉死 10% 这个天花板。
  3. **换架构**（真代码生成）：唯一能越过形态门的路，但属大扩张，
     且 r83 已证明「扩候选集」无效 ⇒ 必须是**生成**而非**枚举**。

### 168. ⛔⛔ **CI 与本地不一致的取证：抓到一个真红，并修正一条被我自己信了很久的错误规则**（2026-10-29）

═══ 一、起因 ═══

搁置项「推 `.github/workflows/verify.yml` 需带 workflow scope 的 PAT」拿到了 token，
`ci-annotation` 分支 cherry-pick 到 main 之上推上去（`1cdfa65`）。顺手查 CI 历史，
发现**最近三次运行全是 failure**，而本地 `aether check` 是 101 份契约全绿。

按第 24 条（某维度恒 0 先怀疑量具）与第 25 条（一个异常两种含义），
「本地绿 / CI 红」这个差本身就是读数 —— 之前它被当背景噪音放过了三次。

═══ 二、取证 ═══

| 读数 | 结果 |
|---|---|
| CI 矩阵 | 3.9 / 3.12 / 3.13 三档 |
| 哪档红 | **只有 3.9 红**，3.12 / 3.13 全绿 |
| 红了几次 | 连续 3 次（`ba94841` / `cd9e90c` / `5ad4553`），无人处理 |
| 红项 | 唯一一条：`contract_pycompat_test.py` **A1b 无法解析的文件数=0** |
| 未解析文件 | `scripts/probe_synth_layer.py:193` `EOL while scanning string literal` |
| 全仓普查 | 220 个 `.py`，**只有这 1 个**在 3.9 下解析不了 |

最小复现（3.9 / 3.13 对拍）：

    print(f"a {x
     if x else 0}")        # 3.9: SyntaxError      3.13: OK

⇒ 根因是 **f-string 跨物理行**。这是 PEP 701（3.12）才放开的写法，
3.9 的 tokenizer 直接断在行尾。**与 V6 的能力无关，是脚本自身的语法。**

═══ 三、⛔⛔ 修正一条我自己信了很久的错误规则 ═══

长期规则里写着「**系统 3.9 会假失败**」。这句话被我用来给每次 3.9 的红背书，
于是连续三次 CI 红被自动归类成噪音。本次证明：**A1b 那条是真红。**

正确表述（两句话不许合并成一句）：

  · `aether check` **全套**在本机系统 3.9 上跑，确实会假失败（依赖 / pyc 之类）；
  · **pycompat 契约这一份**用 3.9 单独跑是有效的，抓的是真违规。

⇒ **不许拿前者的「假」去给后者的「红」背书。** 这两件事共用一个版本号而已。

═══ 四、⛔⛔ 新增规则：跨版本语法违规只能由「真·最低版本解释器」检出 ═══

静态检查很容易让人以为它跨版本通用。实测：

    ast.parse(src, feature_version=(3, 9))     # 用 3.13 跑 ⇒ 抓不到 f-string 跨行

`feature_version` 只管「这个版本有没有这个语法特性」，管不了 **tokenizer 层**
的差异，也不看注解的运行时求值。⇒ 这类违规**只能**靠真 3.9 解释器。

推论（这条比修复本身重要）：

    **CI 矩阵里的 3.9 job 是这条判据本身的一部分，不是多余的档位。**
    删掉它 ⇒ pycompat 的 A 组在剩下的档位上恒绿 ⇒ 判据恒真 ⇒ 契约白写。

═══ 五、落地 ═══

- 修 `scripts/probe_synth_layer.py`：把跨行的条件表达式先落到变量 `found`
  再 print（拼接后输出逐字等价），并把「别跨行」的原因写进注释。
- 验收：`/usr/bin/python3 tests/contract_pycompat_test.py` ⇒ **10/10 全过**
  （A1b 由红转绿）。
- `c24066f` 已推送 origin/main；CI 三档 **check (3.9) / (3.12) / (3.13) 全 success**
  ⇒ 这是三档矩阵**第一次全绿**。

═══ 六、⛔ 待办（安全）═══

PAT 明文出现在了对话里 ⇒ 应去 GitHub Settings 撤销 / 轮换该 token。

### 169. ⛔⛔ **取证基线被外部破坏：本地红了 2 项，但红的不是代码**（2026-10-29）

═══ 一、症状 ═══

推完 168 之后跑本地全量 `aether check` ⇒ **RC=1**，红 2 项，
都出在 `contract_alias_demote_test.py` 的 H 组（真实仓库读数）：

    FAIL H1 ⛔ 真实测试里能自动抽出 ≥12 条判据          0 条
    FAIL H3 ⛔ 其中真有鉴别力（F2P）≥1                  F2P=0

而同一份提交在 CI 上三档**全绿**。

═══ 二、先排除自己 ═══

  · 本轮只改了 `probe_synth_layer.py` 的一处 print 与 README，没碰 `extract.py`；
  · grep 全仓 `rmtree|rm -rf|unlink` ⇒ **没有任何 V6 代码会删 `/tmp/realrepo`**
    （现有的 rmtree 全部指向各自的临时目录）。

═══ 三、根因 ═══

| 读数 | 结果 |
|---|---|
| 三个仓库的 git 可用性 | **只有 boltons 坏**；more-itertools / attrs 完好 |
| `.git` 里剩什么 | `hooks info logs objects refs` |
| 缺什么 | **`HEAD`、`config`、`index`、`packed-refs`、`description`** |
| objects | `objects/pack/pack-a96e18….pack` **完好**（2.1M，1743 个 commit） |
| `refs/heads` | **空**（本地分支引用没了） |
| 工作区 | 缺若干点文件（`.github/…`、`.gitignore`、`.omp/…`） |

⇒ 不是清理工具：那会删整个目录，不会精准地只留骨架。
⇒ **来源仍未定位**，高度疑似 §1 记的那位并发编辑者。

═══ 四、恢复：不重新 clone ═══

⛔ 为什么不用 `git clone` 重来：探针按 **commit sha** 取源码，objects 在 ⇒ 读数本可复现；
   但 `select_commits()` 是 `git log --format=%H` **从 HEAD 往下遍历取前 40 个**，
   重新 clone 会让 HEAD 前移 ⇒ 那 40 个 commit 变了 ⇒ **跨轮不可比**。

  1. `cp -R` 备份到 `/tmp/boltons_broken_backup`（先备份再动）。
  2. 在副本上写回最小 `HEAD`（`ref: refs/heads/master`）与 `config`。
  3. `git cat-file --batch-all-objects --batch-check` 枚举 1743 个 commit，
     按 committer 时间戳取顶端 ⇒ `4e5faa3`（2026-10-22，与原快照一致）。
  4. `git branch -f master 4e5faa3` + `git read-tree HEAD` 重建 index。
  5. 验证：`git log -1` = 4e5faa3；`git show 766b554:tests/test_strutils.py` 可读；
     原先报红的那份契约 **H1/H2/H3 全 PASS**。

═══ 五、残留 ═══

  · `refs/heads` 只剩重建出来的 master（原快照是否还有别的本地分支无从得知）。
  · 工作区仍缺点文件 ⇒ 破坏**不只**动了 `.git`。探针 / 契约只读 objects，不受影响，
    但**任何依赖工作区的工具都要重新核**。
  · `/tmp` 上的东西不在版本控制里 ⇒ 同样的事还会再发生（见下条规则）。

═══ 六、⛔⛔ 新规则 ═══

**取证基线不在版本控制里 ⇒ 会被静默破坏，而且红的不是代码。**

症状是固定的一对：

    CI 绿（基线上游根本不存在 ⇒ H 组 SKIP，not covered ≠ passed）
    本地红（基线坏了 ⇒ 抽到 0 条 ⇒ H1/H3 红）

⇒ **每次跑普查之前，先验基线健康**（`git log -1` 出得来 + 关键 sha 能 `git show`），
   并把健康度本身当成一个读数报出来。否则「判据侧又归零了」和「基线没了」
   会长得一模一样，你又会去改不该改的代码。

### 170. ⛔⛔ **跨三仓库全量收口：119 个真实 commit，全自动端到端修好 0 例**（2026-10-30）

═══ 一、做了什么 ═══

167 只量了 boltons 的全量 40；more-itertools / attrs 停在 166 的 10 commit 样本，
且它们的**合成层停靠点完全没有读数**。本轮补齐：三仓库各跑全量 40。

    python scripts/probe_auto_unlock.py --repo /tmp/realrepo/more-itertools \
        --subdir more_itertools/ --limit 40 --reach --out /tmp/r91_mi.json
    python scripts/probe_auto_unlock.py --repo /tmp/realrepo/attrs \
        --subdir src/attr/ --limit 40 --reach --out /tmp/r91_attrs.json

读数已落盘存档：`scripts/census_mi_40.json`、`scripts/census_attrs_40.json`
（⛔ 169 的教训：读数原来只写在 /tmp，而 /tmp 已被证明会被清 ⇒ 存档进仓库）。

═══ 二、⛔⛔ 核心读数 ═══

| 仓库 | commit | reach=X | L2 | L3 | 判据抽不到 | 判据无鉴别力 | S1 | S2 | S3 | S5 | 端到端修好 |
|---|---|---|---|---|---|---|---|---|---|---|
| boltons | 40 | 35 | 1 | 4 | 9 | 20 | 1 | 1 | 6 | 2 | **0** |
| more-itertools | 39 | 38 | 0 | 1 | 0 | 32 | 1 | 0 | 5 | 1 | **0** |
| attrs | 40 | 37 | 2 | 1 | 22 | 17 | 0 | 1 | 0 | 0 | **0** |
| **合计** | **119** | **110** | **3** | **6** | **31** | **69** | — | — | — | — | **0** |

═══ 三、三条结论 ═══

  1. **形态门跨三仓库一致地是硬门。** L3 占比 boltons 10%（4/40）、
     more-itertools 2.6%（1/39）、attrs 2.5%（1/40）⇒ 不是 boltons
     特有的采样现象，**三个独立仓库都只有个位数**。
     ⇒ 167 的结论（头号门是形态）从 n=1 仓库变成**跨三仓库**。

  2. **判据侧已到顶，且这个"到顶"现在更彻底。** 167 说 boltons 还剩 3/40
     可挖；对 mi / attrs 而言，判据侧再挖出 F2P 也**没用** —— 它们的
     reach 是 X（结构级修复，需要 INSERT/DELETE），合成层在结构上够不到。
     ⇒ 判据侧不是"还剩一点"，而是**在有意义的那些题上等于零**。

  3. **在现有架构上继续投入，期望收益 ≈ 0。** 119 个 commit 里，
     69 个卡在判据无鉴别力、31 个判据抽不到、其余卡在合成层，
     而合成层能进入的前提（reach≥L2）本身只有 9/119。

═══ 四、⛔ 分寸（这条比数字重要）═══

  boltons 那 2 个 `S5_full` 的判定是 **elsewhere** —— 合成层全绿了，
  但改动**没落在人类改的那一行**（一个改在 L122，一个顺手改了 doctest）。
  ⇒ 按 161 的口径，`full_pass` 只是**必要条件**，这两例不算修复。

  所以准确的说法是：**自动抽取判据 + 沙箱验证这一步已经跑通，
  但从"跑通"到"真的修好"之间还隔着形态门，119 例里 0 例跨过去。**
  把它说成"能自动修 bug"是不诚实的。

═══ 五、下一步 ═══

  判据侧与形态门的量化工作到此收口。剩下的路只有一条实质性的：
  **换架构做真的代码生成**（越过形态门的唯一办法），而 r83 已量过
  "扩候选集"的天花板只有 4.7% ⇒ 必须是**生成**，不是枚举。
  这属于大扩张，**等拍板**，我不自己开工。

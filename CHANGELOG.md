# 变更记录

⛔ 这个文件的**结构与判据**（不是随手记的流水账）：

  · **版本号只有一处**：`aether/_version.py` 的 `CODE_VERSION`。
    本文件的小节标题必须与它一致 —— 由 `tests/contract_release_test.py`
    逐个对照（写错版本号 ⇒ 契约转红）。
  · **每一条都要能指到证据**：契约名、提交、或读数命令。
    只写"优化了性能"这类无法证伪的话，等于没写。
  · ⛔ **如实记录"没做"与"没验"**：本项目的纪律是"不可判别 ≠ 有罪"，
    但也绝不许把"没跑过"写成"没问题"。所以下面有独立的"已知边界"一节。

## 0.2.0 — 2026-10-15

> 主题：**从"能跑"补成"可交付、可自动验收、可长期跑"**。
> 内核（判据/闸门/账本纪律）此前已经很硬，缺的是外壳。

### 补上的能力

| 能力 | 落点 | 守它的契约 |
|---|---|---|
| 一条命令的全量验收（聚合退出码 + 五态分类） | `aether/check.py` · `aether check` | `contract_check_test.py` |
| 契约**声明**自己支持哪些模式（未声明 ≠ 只有一种） | 各 `tests/contract_*_test.py` 的 `SUPPORTED_MODES` | 同上 |
| 账本**出处**：谁写的、按哪版格式写 | `aether/provenance.py` · `aether/_version.py` | `contract_provenance_test.py` |
| 可安装的包 + `python -m aether` + LICENSE | `pyproject.toml` · `aether/__main__.py` | `contract_packaging_test.py` |
| "零第三方依赖"从声明变成**可机器验证** | 同上（AST 静态扫 import） | 同上 |
| 静态标注自洽（非法错误码 / 裸抑制） | `contract_static_hygiene_test.py` | 自身 |
| 投影 schema 版本**真比较**（新库拒读） | `aether/store.py` · `doctor` | `contract_store_migration_test.py` |
| 环境变量与 `.env.example` 同源 | `.env.example` | `contract_env_docs_test.py` |
| **种子数据**：`git clone` 后就有可验的事实源 | `data/seed/` | 由 `contract_ledger_test` / `doctor` 间接覆盖 |
| 跨语言一致性成为验收的**显式一步** | `aether.check.cross_language` | `contract_check_test.py` |
| `--root` 在子命令前后都能写 | `aether/entry.py`（幂等） | `contract_serve_test` / CLI 冒烟 |
| 备份 / 恢复 / 体检 / 断点续跑 / 日志分级 | `aether/{backup,doctor,checkpoint,logging_setup}.py` | `contract_ops_test.py` / `contract_logging_test.py` / `contract_repro_test.py` |

### 修掉的**方向错的改动**（注释自信、前提是错的）

  · **账本舍入**：曾按"JS `Math.round` 是 half-up"把 Python 改成
    `floor(x+0.5)`。但与本仓库对拍的是 TS 的 `roundHalfEven`（取偶）
    ⇒ 那改动**制造**了它声称要消除的跨语言漂移。已撤回并加契约。
  · **`Capsule(genes=())` 不得为空**：空 genes 是**生产哨兵**
    （`TemplateProposer` 在"无可用变异"时返回它），该校验会让进化循环
    在最该保守停手时抛异常。已撤回。
  · **投影"伪迁移"**：只插版本号、从不比较 ⇒ 旧结构被按新结构读。
    现在比较并把结论落在可读属性上，库比代码新则拒绝打开。

### 修掉的**假仪表**（有代码、判据没跑）

  · `_coerce_pass(name, bool(p))` —— 调用点先 `bool()` ⇒ 类型闸永不触发；
  · `check_verdict` 对 `checks[*].passed` 严格、对顶层 `passed` 仍 `bool()`
    ⇒ 账本里 `{"passed": "false"}` 被读成 `reward=1.0`；
  · `Verdict(evidence=None)` 的归一化分支在 frozen dataclass 上必抛；
  · `all_of/any_of` 不传 `caliber` ⇒ 指纹退化成空串 ⇒ 口径闸静默失效；
  · `replay.stats` / `replay.base_reward` —— 只写不读的死仪器，
    底下压着两条**非法** `# type: ignore[attr-id]`。

### 修掉的**并发缺陷**

  · `Ledger.append` 的"文件没变就跳过重读"快路径：重载下 24 轮 × 4 进程
    并发 **1 轮分叉**（两个进程都写出指向同一个 `seq=5` 的 `prev_hash`），
    而它只省 **0.068 ms/条** ⇒ 撤掉，锁内无条件重读尾巴。
  · REA 桥的契约在并发下会**假红**（共享外部资源返回 `timeout`）：
    改为超时跳过该断言并写明"未测"。

## 已知边界（**没做**或**没验**的事）

| 项 | 状态 |
|---|---|
| wheel 真的装得上 | **未验**：本机没装 `setuptools` 且离线，`pip wheel . --no-build-isolation` 报 `BackendUnavailable`。判据只到"元数据自洽 + 入口可解析" |
| 真类型检查（mypy/pyright） | **未做**：只守"抑制指令自洽"，不守"注解类型正确" |
| `CliAdapter` 丢弃子进程 stdout 的诊断信封 | **未修**：REA 超时时调用方看不到原因（契约 C2b 故意断言这个坏行为，修好会转红） |
| `_ID_PREFIX` 的一致化 | 5 处已统一为 32，`interop.py` 仍是 `[:16]`（跨语言契约面，动它代价大于收益） |
| `data/` 运行产物是否进版本库 | **不进**（这是刻意的）；只发布 `data/seed/` 种子 |
| 一处未复现的偶发 | 某次全量 `check -j 4` 时 `contract_difficulty_test.py` 红过一次，之后 4 路并发 ×4 + 全量 ×3 均未再现，**根因未定位** |

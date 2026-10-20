"""仓库根与数据布局的**唯一事实源**（r36）。

为什么需要它（2026-10-14 取证）：收口前 "data/ledger.jsonl" 这组路径在
**四处各自手拼**，没有一处是权威的：

    aether/cli.py:49      _paths(root)                 5 个键
    aether/doctor.py:69   _paths(root)                 3 个键（与上面重复 3 个）
    aether/serve.py       342/344/546/568/579          直接 `root / "data" / ...`
    aether/backup.py:52   BACKUP_TARGETS               相对路径字面量
    aether/backup.py:138  `out / "data__ledger.jsonl"` 拷贝后的扁平文件名

⛔ 后果不是"不好看"，是**会出错且无人察觉**：改一次布局要改四处，漏一处
    ⇒ `status` 读的账本和 `backup` 带走的账本**不是同一个文件** —— 备份
    报成功、恢复时才发现少东西（与"备份时链从未验过"是同一类假仪表）。

⛔ 为什么不做成"扫描目录"：布局必须是**声明式**的。目录里有什么就备份什么
    ⇒ 一次误拷进 data/ 的临时文件会被永久带进备份历史；且"它属于事实源
    还是投影"这件事只能由人声明，扫不出来。

⛔ 只放**常量与纯函数**，不 import 任何运行时模块（ledger/store/memory）：
    本模块被入口与库层共同引用，有 import 就会变循环依赖。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

#: 仓库根（= `aether/` 的上一级）。三个入口此前各写一行
#: `Path(__file__).resolve().parent.parent` ⇒ 三处可能漂移。
#:
#: ⛔⛔ r61：这个常量的语义是**「代码在哪」**（包位置），不是**「数据在哪」**。
#:     两者在仓库里恰好相等，装成 wheel 后就分开了 —— `site-packages` 下
#:     既没有 `tasks/` 也没有 `tests/`，往那里写账本意味着：重装即丢、
#:     多项目混库、只读镜像直接崩。要"数据在哪"请用 `resolve_root()`。
#:     ⛔ 不要把这个常量改成函数：11 个模块 import 它取**代码资产**
#:     （`console/`、`arch_baseline`），那些跟着代码走才是对的。
ROOT: Path = Path(__file__).resolve().parent.parent

DATA_DIRNAME = "data"
#: 能力候选档案文件名（见 `paths()` 的 `candidates` 键）。
CANDIDATES_NAME = "candidates.json"
#: 清理提案档案文件名（见 `paths()` 的 `cleanup` 键）。
CLEANUP_NAME = "cleanup_proposals.json"
TASKS_DIRNAME = "tasks"
BACKUPS_DIRNAME = "backups"
CONSOLE_DIRNAME = "console"
#: 外部能力（REA 桥）的全量输出落点。**声明式**：桥脚本只读这里，
#: 不自己拼 `root / "data" / "rea"`（拼了就是布局又多一份）。
REA_DIRNAME = "rea"
#: REA 需要、但本环境缺失的系统工具的**兼容层**落点（编译产物，不入备份）。
#: 目前只有一个 `ps`（本沙箱拒绝对 `/bin/ps` 的 exec，见 `scripts/shims/ps_shim.c`）。
REA_SHIMS_DIRNAME = "rea_shims"

LEDGER_NAME = "ledger.jsonl"
DB_NAME = "aether.db"
MEMORY_NAME = "memory.json"
#: 断点续跑的进度文件（r58 起默认开；见 `EvolutionLoop.write_checkpoint`）。
#: ⛔ 它是**派生量**（可从账本重放得到），不入 `BACKUP_TARGETS`；但它的
#:    **位置**与其它数据文件一样只允许在这里定义一次（第 46 条）。
CHECKPOINT_NAME = "checkpoint.json"
#: 机器自审（r60 F）的快照落点：每次自动落盘前把**原文件整份**备份到这里，
#: 子目录名是 unix 时间戳。⛔ 不入 `BACKUP_TARGETS`（它是回滚用的临时副本，
#: 不是事实源），但**位置**必须在这里定义一次（第 46 条）。
AUTONOMY_DIRNAME = "autonomy"

#: 能力清单的示例文件（UI / 控制台的默认清单）。
CAPS_EXAMPLE = "capabilities.example.json"

#: 架构基线（r59）。⛔ 刻意**不在 data/ 下**：`data/*.json` 被 `.gitignore`
#:    排除（运行产物不该提交），而基线是**代码的属性** —— 放进 data/ ⇒
#:    `git clone` 得到的是「没有基线」的仓库，基线闸从第一天起就不可判别。
#:    位置仍然只在这里定义一次（第 46 条），实现见 `aether/arch_baseline.py`。
ARCH_BASELINE_DIRNAME = "scripts"
ARCH_BASELINE_NAME = "arch_baseline.json"

#: 包内示例任务集的落点（相对**包目录**，不是数据根）。
#:
#: ⛔⛔ 为什么需要它：装成 wheel 之后 `tasks/` 不存在（那是仓库资产，
#:    见 `pyproject.toml`）。没有任务集 ⇒ `synth` / `evolve` 装完即废 ——
#:    实测 `aether synth` 在空目录里抛的是裸 Python 栈。
#:    ⇒ 包内带一份**可跑的最小示例**，由 `aether init` 物化到工作区。
#:
#: ⛔ 它不是数据根的一部分：`looks_like_repo()` 判的是 `pkg_root / tasks`，
#:    而这里是 `aether/seeds/tasks` ⇒ 不会被误判成"仓库态"。
#:    ⛔ 也不参与 `paths()` 的任何键（数据根里没有它）。
SEEDS_DIRNAME = "seeds"
SEED_TASKS_DIRNAME = "tasks"

#: 工作区标记文件名（`aether init` 写的那个，r61）。#: ⛔ 为什么要有**显式标记**而不是"看到 data/ 就当工作区"：后者是"目录里
#:    有什么就认什么"，你在任何目录里 `mkdir data` 都会把数据根拽过去 ——
#:    与 `BACKUP_TARGETS` 拒绝"扫描目录"是同一条理由（必须声明式）。
WORKSPACE_MARKER = ".aether"

#: 显式指定工作区根的环境变量（优先级仅次于 `--root`）。
#: ⛔ 名字里带 AETHER 而不是 V6：`AETHER_ROOT` 是**用户契约**，与仓库代号解耦。
ENV_ROOT = "AETHER_ROOT"

#: 工作区根的来源标签（r61）。⛔ 四种来源必须可分辨：
#:   同一个 `data/ledger.jsonl` 路径，可能是"用户指定的"、"仓库自带的"、
#:   也可能是"兜底目录里刚建的空库" —— 三种情况下"账本里只有 0 条事件"
#:   的**含义完全不同**（前两种是异常，第三种是还没开始）。
ROOT_FROM_FLAG = "flag"              # `--root` 显式
ROOT_FROM_ENV = "env"                # `$AETHER_ROOT`
ROOT_FROM_REPO = "repo"              # 包位置本身是仓库（开发态）
ROOT_FROM_WORKSPACE = "workspace"    # 向上找到 `.aether` 标记
ROOT_FROM_HOME = "home"              # 用户数据目录（安装态兜底）

#: "包位置还是个仓库"的判据：**同时**有这些目录才算。
#: ⛔ 只查 `aether/paths.py` 不够 —— site-packages 里也有它。
#:    必须查**仓库资产**（`tasks/` `tests/`），那些恰恰不进 wheel。
_REPO_SENTINELS: Tuple[str, ...] = ("tasks", "tests")

#: 备份清单：**唯一**被带走的文件（相对 root 的路径，kind）。
#: ⛔ 与 `backup.BACKUP_TARGETS` 是同一个值（那里直接引用本常量），
#:    不在两处各写一份 —— 否则"备份了什么"就有了两个答案。
BACKUP_TARGETS: Tuple[Tuple[str, str], ...] = (
    (f"{DATA_DIRNAME}/{LEDGER_NAME}", "source"),      # 唯一事实源
    (f"{DATA_DIRNAME}/{MEMORY_NAME}", "source"),      # 记忆快照
    (f"{DATA_DIRNAME}/{DB_NAME}", "projection"),      # SQLite 投影（可重建）
)

__all__ = [
    "ROOT", "DATA_DIRNAME", "TASKS_DIRNAME", "BACKUPS_DIRNAME",
    "CONSOLE_DIRNAME", "REA_DIRNAME", "LEDGER_NAME", "DB_NAME",
    "MEMORY_NAME", "CHECKPOINT_NAME", "CAPS_EXAMPLE", "AUTONOMY_DIRNAME",
    "ARCH_BASELINE_DIRNAME", "ARCH_BASELINE_NAME",
    "SEEDS_DIRNAME", "SEED_TASKS_DIRNAME",
    "WORKSPACE_MARKER", "ENV_ROOT",
    "ROOT_FROM_FLAG", "ROOT_FROM_ENV", "ROOT_FROM_REPO",
    "ROOT_FROM_WORKSPACE", "ROOT_FROM_HOME",
    "BACKUP_TARGETS",
    "data_dir", "paths", "flat_name", "BACKUP_FLAT", "seeds_dir",
    "looks_like_repo", "find_workspace", "user_data_dir",
    "resolve_root", "resolve_root_with_source",
]


def data_dir(root: Union[str, Path]) -> Path:
    """`<root>/data`。"""
    return Path(root) / DATA_DIRNAME


def paths(root: Union[str, Path], *, default_env: str = "textscore"
          ) -> Dict[str, Path]:
    """一次算全所有**已知数据文件**的位置。

    ⛔ `arch_baseline` 是唯一的例外（它不在 data/ 下）：它是**代码的
       属性**、必须随代码进版本库，见 `ARCH_BASELINE_DIRNAME` 的注释。
       位置照样在这里定义一次 —— 不放这儿就会有人在别处再拼一遍。

    ⛔ 键名是**接口**：`ledger` / `db` / `memory` / `task` / `tasks_root`。
       调用方按键取值，不要自己再拼一遍（拼了就回到收口前的状态）。
       `task` 是默认环境目录（CLI 的 `synth` 用它）。
    """
    r = Path(root)
    return {
        "ledger": r / DATA_DIRNAME / LEDGER_NAME,
        "db": r / DATA_DIRNAME / DB_NAME,
        "memory": r / DATA_DIRNAME / MEMORY_NAME,
        "task": r / TASKS_DIRNAME / default_env,
        "tasks_root": r / TASKS_DIRNAME,
        "backups": r / BACKUPS_DIRNAME,
        "console": r / CONSOLE_DIRNAME,
        #: 外部能力输出（REA 桥的全量 Evidence 落点）。不入备份清单 ——
        #: 它是**投影**（可从目标重跑得到），不是事实源。
        "rea": r / DATA_DIRNAME / REA_DIRNAME,
        #: 兼容层编译产物（如 `ps`）。同样是投影：缺了就现编。
        "rea_shims": r / DATA_DIRNAME / REA_SHIMS_DIRNAME,
        #: 能力候选档案。⛔ 它不是事实源（事实源是账本里的
        #: `cap.candidate` 事件），只是"当前待办状态"的便携缓存 ——
        #: 但它的**位置**必须和其它数据文件一样只有一处定义：
        #: 调用方手拼 `root / "data" / "x.json"` 会被静态闸拦下
        #: （实测踩过：`cli.py` 里手拼一次就被 `contract_entrypoints` 抓了）。
        "candidates": r / DATA_DIRNAME / CANDIDATES_NAME,
        #: 清理提案档案（同 `candidates`：不是事实源，事实源是账本的
        #: `cleanup.proposal` 事件；但**位置**必须只有一处定义）。
        "cleanup": r / DATA_DIRNAME / CLEANUP_NAME,
        #: 续跑进度（派生量，不是事实源）。默认开 ⇒ 位置必须有唯一定义。
        "checkpoint": r / DATA_DIRNAME / CHECKPOINT_NAME,
        #: 机器自审的落盘前快照（r60 F）。⛔ 派生量：只用于回滚，
        #: 不是事实源；删了不丢证据（账本里记了备份路径与内容指纹）。
        "autonomy": r / DATA_DIRNAME / AUTONOMY_DIRNAME,
        #: 架构基线（代码属性，随代码进版本库 —— 唯一的非 data/ 键）。
        "arch_baseline": r / ARCH_BASELINE_DIRNAME / ARCH_BASELINE_NAME,
    }


def _safe_cwd() -> Optional[Path]:
    """当前目录；站着的目录已被删时返回 `None`。

    ⛔ `Path.cwd()` 在父目录被删后会抛 `OSError`。这里要回答的是"数据根
       在哪"，不该因为进程恰好站在一个被删掉的目录里就让整个 CLI 起不来
       —— 那会把"位置未知"伪装成"程序坏了"（两种故障的下一件事相反）。
    """
    try:
        return Path.cwd()
    except OSError:
        return None


def seeds_dir(pkg_root: Optional[Union[str, Path]] = None) -> Path:
    """包内示例任务集的目录（`<pkg>/seeds/tasks`）。

    ⛔ 位置只在这里推导一次（第 46 条）。`pkg_root` 是可注入的旋钮：
       契约要能模拟"这个包里没有示例"（负结果），否则那条分支永远走不到。
    ⛔ 读不到时**不抛异常**：这是"有没有"的查询，缺了是正常输入 ——
       由调用方决定怎么说（`init` 要说"没有示例可物化"，不是崩）。
    """
    base = Path(pkg_root) if pkg_root is not None else ROOT
    return base / "aether" / SEEDS_DIRNAME / SEED_TASKS_DIRNAME


def looks_like_repo(pkg_root: Union[str, Path]) -> bool:
    """`pkg_root` 是不是一个**仓库**（而不是装进 site-packages 的包）。

    判据只有一条：**仓库资产在不在**。`aether/paths.py` 两边都有，所以它
    不能当判据；`tasks/` 与 `tests/` 恰恰是刻意**不打进 wheel** 的那批
    （见 `pyproject.toml` 的 `package-data` 注释）⇒ 它们的存在等价于
    "这是开发态仓库"。
    """
    r = Path(pkg_root)
    if not (r / "aether" / "paths.py").is_file():
        return False
    return all((r / s).is_dir() for s in _REPO_SENTINELS)


def find_workspace(start: Union[str, Path]) -> Optional[Path]:
    """从 `start` 向上找带 `.aether` 标记的目录（含 `start` 自己）。

    ⛔ 向上找而不是只看当前目录：人在子目录里（`cd data && aether status`）
       敲命令是常态，只看当前层会凭空造出第二个库 —— 而"库里是空的"
       与"你站错地方了"在读数上一模一样（第 97 条同族）。
    """
    cur = Path(start).resolve()
    for p in (cur, *cur.parents):
        try:
            if (p / WORKSPACE_MARKER).is_file():
                return p
        except OSError:
            continue
    return None


def user_data_dir(env: Optional[Dict[str, str]] = None) -> Path:
    """安装态兜底的数据目录（XDG 口径，不引依赖）。

    ⛔ 为什么要有兜底：装成 wheel 后如果既没有 `--root`、也不在仓库、
       也不在工作区里，唯一"错得最少"的落点是用户数据目录 ——
       **绝不能**是 `site-packages`（重装即丢 / 多项目混库 / 只读镜像崩）。
    """
    e = os.environ if env is None else env
    xdg = (e.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "aether"
    return Path.home() / ".local" / "share" / "aether"


def resolve_root_with_source(
        explicit: Optional[Union[str, Path]] = None, *,
        pkg_root: Optional[Union[str, Path]] = None,
        cwd: Optional[Union[str, Path]] = None,
        env: Optional[Dict[str, str]] = None,
) -> Tuple[Path, str]:
    """解析**工作区根**，并返回它的来源标签。五支，顺序固定。

    | # | 判据 | 来源标签 | 什么时候命中 |
    |---|---|---|---|
    | ① | `--root` 显式 | `flag` | 用户说了算 |
    | ② | `$AETHER_ROOT` | `env` | 脚本/CI 说了算 |
    | ③ | 包位置是仓库 | `repo` | **开发态**（行为与 r60 逐字相同） |
    | ④ | 向上找到 `.aether` | `workspace` | `aether init` 过的工作区 |
    | ⑤ | 用户数据目录 | `home` | 安装态兜底 |

    ⛔⛔ ③ 必须排在 ④ 前面，且判据只看"仓库资产在不在"：仓库根**没有**
       `.aether` 标记，若让 ④ 先跑，`cd <仓库>/tasks && aether status`
       会走到 ⑤，于是**同一台机器上两个库**，而两边都可能"看着正常"。
       —— 这是本函数唯一不可调换的一对顺序。

    ⛔ 四个入参都可注入（`pkg_root` / `cwd` / `env` 都是测试用的旋钮）：
       否则"安装态"这件事只能靠真的 `pip install` 一遍才能验，
       而那种验法在契约里既慢又不可重复（第 99 条同族）。
    """
    if explicit:
        return Path(explicit).expanduser(), ROOT_FROM_FLAG
    e = os.environ if env is None else env
    v = (e.get(ENV_ROOT) or "").strip()
    if v:
        return Path(v).expanduser(), ROOT_FROM_ENV
    base = Path(pkg_root) if pkg_root is not None else ROOT
    if looks_like_repo(base):
        return base, ROOT_FROM_REPO
    start = Path(cwd) if cwd is not None else _safe_cwd()
    ws = find_workspace(start) if start is not None else None
    if ws is not None:
        return ws, ROOT_FROM_WORKSPACE
    return user_data_dir(e), ROOT_FROM_HOME


def resolve_root(explicit: Optional[Union[str, Path]] = None, *,
                 pkg_root: Optional[Union[str, Path]] = None,
                 cwd: Optional[Union[str, Path]] = None,
                 env: Optional[Dict[str, str]] = None) -> Path:
    """只要位置（来源见 `resolve_root_with_source`）。

    ⛔ 薄包装，不重写一遍顺序 —— 同一条优先级写两遍，改一处就两处不一致
       （第 97 条：同一条退路写两遍的害处是"下次只改一处"）。
    """
    return resolve_root_with_source(
        explicit, pkg_root=pkg_root, cwd=cwd, env=env)[0]


def flat_name(rel: str) -> str:
    """备份目录里的扁平文件名：`data/ledger.jsonl` → `data__ledger.jsonl`。

    ⛔ 备份刻意**不建子目录**（一份备份 = 一个平铺目录，便于打包/传输）。
       这个映射必须只有一处：`backup.create` 写文件用它，`backup.create`
       验链、以及 `restore` 读文件也用它 —— 三处各拼一次就会对不上。
    """
    return rel.replace("/", "__")


#: 各备份目标在备份目录里的落点（rel → 扁平名）。
BACKUP_FLAT: Dict[str, str] = {rel: flat_name(rel) for rel, _ in BACKUP_TARGETS}

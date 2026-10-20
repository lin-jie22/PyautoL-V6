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

from pathlib import Path
from typing import Dict, Tuple, Union

#: 仓库根（= `aether/` 的上一级）。三个入口此前各写一行
#: `Path(__file__).resolve().parent.parent` ⇒ 三处可能漂移。
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
    "BACKUP_TARGETS",
    "data_dir", "paths", "flat_name", "BACKUP_FLAT",
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


def flat_name(rel: str) -> str:
    """备份目录里的扁平文件名：`data/ledger.jsonl` → `data__ledger.jsonl`。

    ⛔ 备份刻意**不建子目录**（一份备份 = 一个平铺目录，便于打包/传输）。
       这个映射必须只有一处：`backup.create` 写文件用它，`backup.create`
       验链、以及 `restore` 读文件也用它 —— 三处各拼一次就会对不上。
    """
    return rel.replace("/", "__")


#: 各备份目标在备份目录里的落点（rel → 扁平名）。
BACKUP_FLAT: Dict[str, str] = {rel: flat_name(rel) for rel, _ in BACKUP_TARGETS}

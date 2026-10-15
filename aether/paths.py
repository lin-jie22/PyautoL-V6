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

#: 能力清单的示例文件（UI / 控制台的默认清单）。
CAPS_EXAMPLE = "capabilities.example.json"

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
    "MEMORY_NAME", "CAPS_EXAMPLE", "BACKUP_TARGETS", "data_dir", "paths",
    "flat_name", "BACKUP_FLAT",
]


def data_dir(root: Union[str, Path]) -> Path:
    """`<root>/data`。"""
    return Path(root) / DATA_DIRNAME


def paths(root: Union[str, Path], *, default_env: str = "textscore"
          ) -> Dict[str, Path]:
    """一次算全所有**已知数据文件**的位置。

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

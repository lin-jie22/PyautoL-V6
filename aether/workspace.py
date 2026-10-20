"""工作区骨架（r61）：`aether init` 的唯一实现。

为什么单独一个模块、而不是写在 `cli.cmd_init` 里：
    契约要能**直接 import 它**做"幂等 / 不覆盖 / 演练不落盘"的反证，
    而不必为了测一个纯建目录的动作去起子进程（起子进程的失败模式是
    "报错但看不出是它错了还是环境错了"，见第 105 条同族）。

⛔ 为什么需要「工作区」这个概念：装成 wheel 后 `paths.ROOT`（包位置）
   等于 `site-packages` —— 往那里写账本意味着重装即丢、多项目混库、
   只读镜像里直接崩。工作区就是"数据该住的地方"，靠 `.aether` 标记
   **显式声明**（不是"看到 data/ 就当工作区"，那是扫描式判断，
   与 `BACKUP_TARGETS` 拒绝扫描是同一条理由）。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .paths import (BACKUPS_DIRNAME, DATA_DIRNAME, TASKS_DIRNAME,
                    WORKSPACE_MARKER, looks_like_repo, seeds_dir)

#: 标记文件的格式版本（标记本身也要能演进）。
MARKER_VERSION = 1

#: 骨架里要建的目录（相对 root）。
SKELETON_DIRS = (DATA_DIRNAME, TASKS_DIRNAME, BACKUPS_DIRNAME)

#: 一个示例任务集由这两个文件构成（`Environment.from_dir` 只读它们）。
#: ⛔ 与 `pyproject.toml` 的 `package-data` **必须**一致：那边少一个，
#:    装出来的 wheel 里就只有半个示例（而 `init` 会静默物化半份）。
SEED_FILES = ("task.py", "tests.json")

__all__ = ["MARKER_VERSION", "SKELETON_DIRS", "SEED_FILES", "is_workspace",
           "init", "seed_tasks"]


def is_workspace(root: Union[str, Path]) -> bool:
    """`root` 是不是一个已声明的工作区（只看标记，不看目录里有什么）。"""
    try:
        return (Path(root) / WORKSPACE_MARKER).is_file()
    except OSError:
        return False


def seed_tasks(root: Union[str, Path], *, dry_run: bool = False,
               pkg_root: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """把包内的示例任务集物化到 `root/tasks/`。

    为什么是"物化"而不是"直接读包内那份"：
        `synth` / `evolve` 要**改写**任务（变异），改的是包内副本的话，
        wheel 目录会被写脏、且重装即丢。所以包里那份是**模板**，
        工作区那份才是用户自己的 —— 此后它怎么改都与包无关。

    幂等语义（与 `init` 的其余部分一致）：
      · 目标文件已存在 ⇒ **不覆盖**（用户可能已经改过它，覆盖等于吞掉他的工作）
      · `dry_run` ⇒ 一个字节都不落

    ⛔ 包里没有示例 ⇒ `available=False` 且给出原因，**不是**静默成功：
       "没物化任何东西"与"物化成功但本来就是空的"在输出上必须能分开。
    """
    r = Path(root).expanduser()
    src_root = seeds_dir(pkg_root)
    res: Dict[str, Any] = {"available": src_root.is_dir(), "source": str(src_root),
                           "seeded": [], "existing": [], "dry_run": bool(dry_run)}
    if not res["available"]:
        res["reason"] = (f"包里没有示例任务集（{src_root}）⇒ tasks/ 是空的，"
                         f"你仍可以自己往里放 `task.py` + `tests.json`")
        return res

    for env in sorted(p for p in src_root.iterdir() if p.is_dir()):
        for name in SEED_FILES:
            s = env / name
            if not s.is_file():
                continue
            rel = f"{TASKS_DIRNAME}/{env.name}/{name}"
            dst = r / TASKS_DIRNAME / env.name / name
            if dst.exists():
                res["existing"].append(rel)
                continue
            res["seeded"].append(rel)
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, dst)
    return res


def init(root: Union[str, Path], *, dry_run: bool = False,
         now: Optional[float] = None,
         pkg_root: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """在 `root` 建工作区骨架，返回**可打印的裁决**。

    幂等语义（三条，缺一条这个命令就会变成破坏性操作）：
      · 已存在的目录 ⇒ 记进 `existing`，**不动它**
      · 已存在的标记 ⇒ **不重写**（保留最初的 `created_at`）
      · `dry_run` ⇒ 一个字节都不落（演练与真跑在磁盘上必须同形，
        否则"我演练过了"这句话就不成立 —— 第 139 条）

    ⛔ 在 V6 仓库根上跑 ⇒ `noop=True` 且**什么都不建**：仓库自己就是
       数据根（`resolve_root()` 的 `repo` 那一支），在这里再放一个
       `.aether` 反而会造出"同一个目录、两条认路依据"。
    """
    r = Path(root).expanduser()
    res: Dict[str, Any] = {
        "root": str(r.resolve() if r.exists() else r),
        "created": [],
        "existing": [],
        "marker": str(r / WORKSPACE_MARKER),
        "noop": False,
        "dry_run": bool(dry_run),
        "reasons": [],
    }

    if looks_like_repo(r):
        res["noop"] = True
        res["reasons"].append(
            "这是 V6 仓库根（有 tasks/ 与 tests/）⇒ 它自己就是数据根，"
            "不需要 .aether 标记；直接 `aether status` 即可")
        return res

    for name in SKELETON_DIRS:
        d = r / name
        if d.is_dir():
            res["existing"].append(name)
        else:
            res["created"].append(name)
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)

    #: ⛔ 示例任务集必须在**骨架建完之后**物化（`tasks/` 得先存在）。
    #:    顺序反了不会报错 —— `mkdir(parents=True)` 会把 `tasks/` 顺手建出来，
    #:    于是"建骨架"这一步的输出里少了 tasks ⇒ 报告与实际不符。
    res["seeds"] = seed_tasks(r, dry_run=dry_run, pkg_root=pkg_root)

    marker = r / WORKSPACE_MARKER
    if marker.is_file():
        res["existing"].append(WORKSPACE_MARKER)
    else:
        res["created"].append(WORKSPACE_MARKER)
        #: ⛔ 标记里记下"哪来的一份示例"：事后能回答"这个工作区是自己搭的
        #:    还是 `init` 给的"，而不用靠目录里有没有东西去猜。
        seeded = sorted({p.split("/")[1] for p in res["seeds"]["seeded"]})
        if not dry_run:
            marker.write_text(json.dumps({
                "version": MARKER_VERSION,
                "created_at": int(now if now is not None else time.time()),
                "by": "aether init",
                "seeded": seeded,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return res

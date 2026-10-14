#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备份与恢复：`ledger.jsonl` 是唯一事实源，却一直没有任何备份命令。

═══ 为什么这是**缺的功能** ═══

V6 的恢复能力是**单向**的，而且方向正好是反的：

  · `rebuild` 能从账本重建 SQLite 投影（投影丢了能再来）；
  · 但**账本本身**坏了（误删 / 写坏 / 磁盘故障）⇒ 什么都回不来。

也就是说：唯一能被保护的是"可以从账本重建的那个东西"，
而唯一不能重建的那个东西恰恰没人保护。

═══ 设计取舍 ═══

1. ⛔ **备份必须自带校验和**。备份完才发现拷坏了，比没备份更糟
   —— 它会让人**以为**有退路。所以 manifest 记每个文件的 sha256 与
   字节数，`restore` 前逐条复算。

2. ⛔ **备份时账本可能正在被写**（进化循环 / API 同时在跑）。
   这里**不假装**能拿到一致性快照（那需要写侧配合）—— 而是：
   备份后立刻对**副本**跑 `verify_chain`，把结论写进 manifest 的
   `chain_ok`。`chain_ok=False` 的备份**仍然可恢复**（它是完整文件的
   拷贝），但恢复前会明确告知"这份副本的链是断的"。
   ⛔ 这就是为什么不能静默：一个"看起来成功"的坏备份 = 假仪表。

3. ⛔ **恢复前先给当前状态留一份 `.before-restore`**。恢复是覆盖写，
   一旦覆盖错（比如恢复点选错了），当前数据就没了 —— 而当前数据
   恰恰是"还没坏"的那份。留一份的成本是几 MB，不留的代价是全部。

4. ⛔ **恢复是 fail-closed**：manifest 缺失 / hash 不符 / 文件数不对
   ⇒ 一律拒绝，且说清是哪一项。不允许"缺哪个就跳过哪个"。

5. ⛔ 只备份**已知的事实源与投影文件**，不做目录级通配拷贝
   —— 通配会把 100MB 的临时文件一起装进来，也会把不该带走的东西带走。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: ⛔ 备份的**唯一清单**：只有这些文件被带走。加文件必须显式加到这儿
#:    （并说明它属于事实源还是投影），不做目录通配。
BACKUP_TARGETS: Tuple[Tuple[str, str], ...] = (
    ("data/ledger.jsonl", "source"),      # 唯一事实源
    ("data/memory.json", "source"),       # 记忆快照（可从账本部分重建）
    ("data/aether.db", "projection"),     # SQLite 投影（可从账本重建）
)

__all__ = ["BACKUP_TARGETS", "BackupError", "Manifest", "create", "verify",
           "restore", "report", "archive", "unarchive"]


class BackupError(Exception):
    """备份/恢复不可继续。**必须**带人类可读的原因。"""


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass
class Manifest:
    """一份备份的目录。缺任何一条都判不可恢复（不跳过）。"""
    created_at: float
    root: str
    files: List[Dict[str, Any]] = field(default_factory=list)   # rel/size/sha256/kind
    chain_ok: Optional[bool] = None       # 副本的账本链是否完整（None=未测）
    chain_reason: str = ""
    format: int = 1
    #: ⛔ 这份备份**落在哪**（r35）。此前 manifest 记了内容却没记位置
    #:    ⇒ 事后拿到一份 manifest，还要靠人记得它当初拷到哪个目录去了。
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"format": self.format, "created_at": self.created_at,
                "root": self.root, "files": self.files,
                "chain_ok": self.chain_ok, "chain_reason": self.chain_reason,
                "path": self.path}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Manifest":
        if not isinstance(d, dict):
            raise BackupError(f"manifest 必须是对象，实得 {type(d).__name__}")
        if "files" not in d or not isinstance(d["files"], list):
            raise BackupError("manifest 缺 files ⇒ 拒绝恢复（不许跳过缺失项）")
        return cls(created_at=float(d.get("created_at", 0.0)),
                   root=str(d.get("root", "")), files=list(d["files"]),
                   chain_ok=d.get("chain_ok"),
                   chain_reason=str(d.get("chain_reason", "")),
                   format=int(d.get("format", 1)),
                   path=str(d.get("path", "")))


def create(root: str | Path, dest: str | Path,
           note: str = "") -> Manifest:
    """把已知事实源与投影拷到 `dest`（带时间戳子目录）。返回 manifest。

    ⛔ 不存在的文件**跳过并记录**（`missing`），不算失败 —— 首次运行时
    `aether.db` 可能还没建。但 manifest 里必须写明"它不在"，否则恢复时
    "本来就只有 2 个文件"和"少拷了一个"看起来一样。
    """
    root_p = Path(root)
    dest_p = Path(dest)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = dest_p / f"backup-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    files: List[Dict[str, Any]] = []
    for rel, kind in BACKUP_TARGETS:
        src = root_p / rel
        if not src.exists():
            files.append({"rel": rel, "kind": kind, "missing": True,
                          "size": 0, "sha256": ""})
            continue
        dst = out / rel.replace("/", "__")
        shutil.copy2(src, dst)
        files.append({"rel": rel, "kind": kind, "missing": False,
                      "size": dst.stat().st_size, "sha256": _sha256(dst)})
    m = Manifest(created_at=time.time(), root=str(root_p), files=files,
                 path=str(out))
    #: ⛔ 备份完**立刻**验副本的链：不验的话，"拷成功了"会被当成
    #:   "备份可用"，而半截拷贝在恢复的那一刻才发现（假仪表）。
    led_copy = out / "data__ledger.jsonl"
    if led_copy.exists():
        try:
            from .ledger import Ledger
            #: ⛔ 三元组 `(ok, first_bad_seq, reason)` —— 按 2 元组解包会
            #:    ValueError，而 except 会把它记成"未测" ⇒ 备份看起来
            #:    成功了、链却从没验过（假仪表）。判据必须与被测对象同源。
            ok, bad_seq, why = Ledger(led_copy).verify_chain()
            m.chain_ok = bool(ok)
            m.chain_reason = str(why or "") + (
                f"（首个坏点 seq={bad_seq}）" if bad_seq is not None else "")
        except Exception as e:      # noqa: BLE001 —— 验不了就如实记为未验
            m.chain_ok, m.chain_reason = None, f"未测：{type(e).__name__}: {e}"
    payload = m.to_dict()
    if note:
        payload["note"] = note
    (out / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def archive(backup_dir: str | Path) -> Path:
    """把一份备份目录打成**单个** `.tar.gz`（便于传输 / 异地留存）。

    ⛔ 刻意**薄**：解压出来就是原备份目录，`restore` 照常吃。这里不重新
       实现一遍校验 —— 那会造出第二份判据（"打包格式对不对"与
       "备份内容对不对"是两件事，各有一份就够了）。
    """
    d = Path(backup_dir)
    if not d.is_dir():
        raise BackupError(f"不是备份目录：{d}")
    tar_path = d.with_suffix(".tar.gz") if d.suffix != ".tar.gz" else d
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(d, arcname=d.name)
    return tar_path


def unarchive(tar_path: str | Path, dest: str | Path) -> Path:
    """解回目录，返回该目录路径（供 `verify` / `restore` 直接消费）。

    ⛔ 防路径穿越（tar 里可能带 `../`）：解压前逐条检查目标是否仍在
       `dest` 之内 —— 归档文件是**数据**，不是可信代码。
    """
    tp = Path(tar_path)
    out_root = Path(dest)
    out_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tp, "r:gz") as tf:
        for m in tf.getmembers():
            target = (out_root / m.name).resolve()
            if not str(target).startswith(str(out_root.resolve())):
                raise BackupError(f"归档里含越界路径：{m.name}")
        tf.extractall(out_root)
    return out_root / tp.name.replace(".tar.gz", "")


def verify(path: str | Path) -> Tuple[bool, List[str], Optional[Manifest]]:
    """校验一份备份是否**完整且未被改动**。返回 (ok, problems, manifest)。

    ⛔ 三类问题分开报：缺文件 / hash 不符 / manifest 自身坏。
       合成一句"备份不可用"会让人直接删掉它，而"少一个投影文件"其实
       是可以接受的（投影能从账本重建）—— 处置不同，不能混。
    """
    p = Path(path)
    mf = p / "manifest.json"
    if not mf.exists():
        return (False, [f"manifest.json 不存在：{mf}（这不是一份备份）"], None)
    try:
        m = Manifest.from_dict(json.loads(mf.read_text(encoding="utf-8")))
    except (ValueError, BackupError) as e:
        return (False, [f"manifest 不可解析：{e}"], None)
    problems: List[str] = []
    for f in m.files:
        rel = str(f.get("rel", ""))
        if f.get("missing"):
            continue            #: 备份时就不存在 ⇒ 不是"丢了"
        dst = p / rel.replace("/", "__")
        if not dst.exists():
            problems.append(f"缺文件 {rel}（该有的没拷到 ⇒ 不许跳过）")
            continue
        try:
            h = _sha256(dst)
        except OSError as e:
            problems.append(f"{rel} 读不了：{e}")
            continue
        if h != f.get("sha256"):
            problems.append(
                f"{rel} 内容变了（sha256 {h[:12]}… ≠ 记录 "
                f"{str(f.get('sha256'))[:12]}…）⇒ 备份被改动过")
    return (not problems, problems, m)


def restore(path: str | Path, root: str | Path,
            keep_current: bool = True) -> Dict[str, Any]:
    """从备份恢复到 `root`。返回报告。**fail-closed**。

    ⛔ 恢复前先把当前状态另存一份 `.before-restore-<时间戳>`：
       覆盖写是不可逆的，而"当前"这份恰恰是还没坏的。
    """
    ok, problems, m = verify(path)
    if not ok or m is None:
        raise BackupError("备份校验不通过 ⇒ 拒绝恢复：\n  - "
                          + "\n  - ".join(problems))
    root_p = Path(root)
    saved_to = ""
    if keep_current:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        cur = root_p / f".before-restore-{stamp}"
        cur.mkdir(parents=True, exist_ok=True)
        for rel, _kind in BACKUP_TARGETS:
            src = root_p / rel
            if src.exists():
                shutil.copy2(src, cur / rel.replace("/", "__"))
        saved_to = str(cur)
    restored, skipped = [], []
    for f in m.files:
        rel = str(f.get("rel", ""))
        dst = root_p / rel
        src = Path(path) / rel.replace("/", "__")
        if f.get("missing") or not src.exists():
            skipped.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".restoring")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)        #: 原子替换：半截恢复比不恢复更糟
        restored.append(rel)
    return {"restored": restored, "skipped": skipped,
            "saved_current_to": saved_to,
            "chain_ok": m.chain_ok, "chain_reason": m.chain_reason,
            "created_at": m.created_at,
            "problems": problems}


def report(root: str | Path) -> Dict[str, Any]:
    """当前数据面的"可恢复性"读数：每个目标在不在、多大、什么时候改的。"""
    root_p = Path(root)
    items = []
    for rel, kind in BACKUP_TARGETS:
        p = root_p / rel
        items.append({"rel": rel, "kind": kind, "exists": p.exists(),
                      "size": p.stat().st_size if p.exists() else 0,
                      "mtime": (time.strftime(
                          "%Y-%m-%d %H:%M:%S",
                          time.localtime(p.stat().st_mtime))
                          if p.exists() else "")})
    return {"root": str(root_p), "files": items,
            "note": "source=唯一事实源（丢了不可重建）/ "
                    "projection=投影（可从账本 rebuild）"}

#!/usr/bin/env python3
"""r110 / README 189：跨口径存档的 **superseded 登记**（契约①）。

═══ 为什么需要它 ═══

154 以来三份存档的 `caliber` 都钉着 `scripts/probe_context_gate.py` 的
sha256。r110 用户拍板「四条全放宽且默认生效」⇒ 量具字节必变 ⇒ 旧存档里
那个 hash 与磁盘不再相等。按「标准可读不可写」纪律：

    · 旧存档**一个字节不改**（不回写、不删除、旧读数原样保留）；
    · 只在这里登记「旧值 / 新值 / 原因 / 归档章节」；
    · 契约改用 `caliber_matches_superseded()`：只有**已登记且逐字对上**
      的那一处差异可豁免，其余任何差异照旧转红（fail-closed）。

⛔ 绝不是「忽略这个键」的万能豁免。三条硬约束：

    1. 登记的 `old_sha256` 必须等于存档里的值 ⇒ 证明存档没被悄悄改；
    2. 登记的 `new_sha256` 必须等于磁盘当前值 ⇒ 证明没拿旧登记掩盖新改动；
    3. 登记条目之外的任何差异（含多键 / 缺键 / 别的文件变了）一律
       返回差异文件名 ⇒ 闸仍有牙。

⛔ 跨口径读数**不可比**（契约①）：189 之后的读数另存 v2 口径存档，
   与 154/177/180 的读数不得出现在同一张对比表里。

用法（供契约调用，纯只读）：
    caliber_matches_superseded(saved, disk, "scripts/extract_downstream.json")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "caliber_superseded.json"


def load() -> dict:
    """登记簿只读加载；文件缺失直接抛（fail-closed，不许默认空登记）。"""
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def entries_for(archive: str, registry: Optional[dict] = None) -> Dict[str, dict]:
    """某份存档名下的豁免条目，按文件名索引。未登记 ⇒ 空 dict。"""
    reg = load() if registry is None else registry
    return {e["file"]: e for e in reg.get("entries", [])
            if e.get("archive") == archive}


def caliber_matches_superseded(saved: Dict[str, str],
                               disk: Dict[str, str],
                               archive: str,
                               registry: Optional[dict] = None) -> List[str]:
    """返回**差异文件名列表**；空列表 = 通过。

    ⛔ 与产品 `caliber_matches()` 的差别只有一处：若差异文件在本登记簿里
    且 old/new 双向逐字对上，则这一处不算差异。缺键同样算差异（不许用
    默认值兜底，契约①）。
    """
    allowed = entries_for(archive, registry)
    diffs: List[str] = []
    for f in sorted(set(saved) | set(disk)):
        s, d = saved.get(f), disk.get(f)
        if s == d and s is not None:
            continue
        e = allowed.get(f)
        if e and s == e["old_sha256"] and d == e["new_sha256"]:
            continue
        diffs.append(f)
    return diffs

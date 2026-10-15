#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据集变更后的**重新发布**：保留历史前缀、在断点续一条新链。

═══ 为什么需要它（这是"换尺子"的正当路径）═══

`aether/judge.py` 给判据集（尺子）一个内容寻址身份，并让**跨尺子比较**
变成可检测的 `incomparable`。但检测出来之后要有出路 —— 否则：
**判据一改，账本里就永久躺着两把尺子的读数，而没有任何合法的方式收拾它。**

直接删事件不行：账本 append-only，删一条就断链（实测：
丢掉 18 条 `eval.verdict` ⇒ `prev_hash 不连续`，链完整这条判据立刻转红）。
那正是对的行为 —— **不许偷偷改历史**。

所以正路是**重新发布**：
  · 保留**前缀**（判据变更之前的历史原样搬过来，hash 逐条重算）；
  · 在断点写一条 `evolution.judge_changed`，带旧/新判据身份与原因；
  · 后缀**不搬**（那些是用旧尺子量的，搬过来就是"跨尺子同账"）。

⛔ 这**不是**"洗掉不方便的数据"。判据是：
   ① 必须显式给出 `--reason`（谁、为什么换尺子）；
   ② 必须写出旧/新身份（可审计）；
   ③ 默认 **dry-run**，`--apply` 才落盘；
   ④ 落盘前先备份旧账本（不覆盖原文件，另存新文件并告知）。
⛔ 它是"重新发布"，不是"修复"。所以它**不**改任何既有事件的语义。
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether import judge as J                                    # noqa: E402
from aether.ledger import Event, Ledger, content_hash            # noqa: E402

__all__ = ["plan", "relaunch"]

#: 环境标识：这些事件本身**不带**判据身份，但它们记录的是"系统做了什么"，
#: 与尺子无关 ⇒ 前缀里保留（判据变更不影响它们的含义）。
_ALWAYS_KEEP = frozenset({
    "memory.write", "cap.candidate", "cap.invoke", "evolution.run_config",
    "evolution.stopped", "evolution.saturated", "harness.init",
    "harness.commit", "harness.revert", "gate.result", "weakness.mined",
    "gen.probe", "saturation.probe", "aging.trace", "attribution.layer",
    "attribution.step", "evolve.noop",
})


def _load(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:                                      # noqa: BLE001
            continue
    return out


def plan(ledger: Path, *, current: Optional[str] = None
         ) -> Dict[str, Any]:
    """算出"重新发布"的方案（**只读**）。

    切点 = **最后一条用旧尺子量的事件**的位置。切点之前全保留，
    切点之后（含切点那条 verdict 起）属于旧尺子 ⇒ 不搬。
    ⛔ 切点之前的 `eval.verdict` 里若有**别的**尺子身份，也要点出来 ——
       那说明历史上已经混过尺子，不能装作没发生。
    """
    cur = current or J.identity()
    events = _load(ledger)
    #: ⛔⛔ 断点判据：**第一条**用旧尺子量的裁决之后全丢。
    #:    实测踩过一个判据 bug：原先用"**最后一条**旧身份"当断点 ——
    #:    而上一轮重新发布插的 `evolution.judge_changed` 标记在**末尾**，
    #:    它本身就不是"当前身份"的裁决 ⇒ 断点被推到最末 ⇒ 所有实验期的
    #:    旧尺子裁决都被当成"前缀"保留下来（dry-run 显示"保留 2991"，
    #:    而账本里明明躺着 4 种身份）。
    #:    ⇒ 判据改成"第一条旧的之后全丢"，并且**标记事件不参与**这个判断
    #:      （它是留痕，不是量出来的读数）。
    first_bad = -1
    other_versions: Dict[str, int] = {}
    no_id = 0
    for i, e in enumerate(events):
        if e.get("kind") != "eval.verdict":
            continue
        v = str((e.get("payload") or {}).get("judge_version") or "")
        if not v:
            no_id += 1
            continue
        if v != cur:
            other_versions[v[:16]] = other_versions.get(v[:16], 0) + 1
            if first_bad < 0:
                first_bad = i
    keep = events[:first_bad] if first_bad >= 0 else events
    drop = events[first_bad:] if first_bad >= 0 else []
    #: 新链要写进去的"被丢掉的旧身份"（不是前缀里那些 —— 那些还在链上）
    dropped_versions: Dict[str, int] = {}
    for e in drop:
        if e.get("kind") != "eval.verdict":
            continue
        v = str((e.get("payload") or {}).get("judge_version") or "(无身份)")
        dropped_versions[v[:16]] = dropped_versions.get(v[:16], 0) + 1
    return {
        "current_judge": cur,
        "n_events": len(events),
        "n_keep": len(keep),
        "n_drop": len(drop),
        "cut_at_seq": (drop[0].get("seq") if drop else None),
        "other_versions_prefix": other_versions,
        "dropped_versions": dropped_versions,
        "verdicts_without_id": no_id,
        "dropped_kinds": sorted({str(d.get("kind")) for d in drop}),
        "note": ("前缀保留（判据变更前的历史原样重算 hash）；后缀不搬"
                 "（那是用旧尺子量的）。会插一条 evolution.judge_changed 留痕。"),
    }


def relaunch(ledger: Path, out: Path, *, reason: str, by: str,
             apply: bool = False) -> Dict[str, Any]:
    """执行重新发布：写一条**全新的**链到 `out`（不覆盖 `ledger`）。

    ⛔ 三条硬纪律：
       ① `reason` / `by` 必须非空 —— 换尺子必须留痕、必须有人；
       ② 默认 dry-run，`--apply` 才写盘；
       ③ **不覆盖原账本**：写新文件；原文件原样保留（想删自己删）。
    """
    if not str(reason).strip():
        raise ValueError("重新发布必须给出 --reason（换尺子必须留痕）")
    if not str(by).strip():
        raise ValueError("重新发布必须给出 --by（谁批准的必须可查）")
    cur = J.identity()
    pl = plan(ledger, current=cur)
    events = _load(ledger)
    keep = events[:pl["n_keep"]]
    drop = events[pl["n_keep"]:]
    result: Dict[str, Any] = {"plan": pl, "reason": reason, "by": by,
                              "out": str(out), "applied": False}
    if not apply:
        result["note"] = "dry-run：没有写任何文件（加 --apply 才落盘）"
        return result

    if out.exists():
        raise FileExistsError(
            f"{out} 已存在 ⇒ 拒绝覆盖（重新发布要留下旧的那份）")
    out.parent.mkdir(parents=True, exist_ok=True)
    #: ⛔ 逐条**重算 hash** 并把 `seq` 收紧成连续 —— 前缀里被丢掉的那 18 条
    #:    会让 seq 出现空洞，而"seq 严格递增无重复"是一条契约
    #:    （`contract_ledger_test` D 组）。空洞会让它转红。
    prev = Ledger.GENESIS_HASH
    lines: List[str] = []
    for new_seq, e in enumerate(keep, 1):
        ev = Event(seq=new_seq, ts=float(e.get("ts") or 0.0),
                   kind=str(e.get("kind") or ""), actor=str(e.get("actor") or ""),
                   payload=dict(e.get("payload") or {}), prev_hash=prev, hash="")
        ev = Event(**{**asdict(ev), "hash": content_hash(ev.identity())})
        lines.append(ev.to_line())
        prev = ev.hash
    #: 留痕：把"换了尺子"这件事写进新链的第一条（这样它**必然**在链上）
    marker = Event(seq=len(lines) + 1, ts=float(keep[-1].get("ts") or 0.0)
                   if keep else 0.0,
                   kind="evolution.judge_changed", actor=str(by),
                   payload={"from_judge": sorted(pl["dropped_versions"]),
                            "to_judge": cur, "reason": reason,
                            "dropped_events": pl["n_drop"],
                            "dropped_kinds": pl["dropped_kinds"],
                            "cut_at_seq": pl["cut_at_seq"],
                            "sid": ""},
                   prev_hash=prev, hash="")
    marker = Event(**{**asdict(marker), "hash": content_hash(marker.identity())})
    lines.append(marker.to_line())
    out.write_text("".join(lines), encoding="utf-8")
    ok, seq, why = Ledger(out).verify_chain()
    result.update({"applied": True, "n_written": len(lines),
                   "chain_ok": ok, "chain_reason": why,
                   "note": "新链已写出；原账本**原样保留**（没有覆盖）"})
    return result


def _main(argv: Sequence[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="relaunch_ledger",
        description="判据集变更后的重新发布：保留前缀 + 留痕 + 新链")
    ap.add_argument("--ledger", default=str(ROOT / "data" / "ledger.jsonl"))
    ap.add_argument("--out", required=True, help="新账本路径（不许覆盖原文件）")
    ap.add_argument("--reason", required=True, help="为什么要换尺子（必填）")
    ap.add_argument("--by", required=True, help="谁批准的（必填）")
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run）")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    try:
        rep = relaunch(Path(ns.ledger), Path(ns.out), reason=ns.reason,
                       by=ns.by, apply=ns.apply)
    except (ValueError, FileExistsError) as e:
        print(f"❌ {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if ns.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    pl = rep["plan"]
    print(f"当前判据身份 {pl['current_judge'][:16]}；"
          f"共 {pl['n_events']} 条 ⇒ 保留 {pl['n_keep']} / 丢弃 {pl['n_drop']}")
    if pl["other_versions_prefix"]:
        print(f"⛔ 前缀里还有别的尺子身份：{pl['other_versions_prefix']}"
              f"（历史上混过尺子，不假装没发生）")
    print(f"丢弃的事件类型：{pl['dropped_kinds']}")
    print(rep.get("note", ""))
    if rep.get("applied"):
        print(f"新链：{rep['out']}（{rep['n_written']} 条，"
              f"链完整={rep['chain_ok']}）")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

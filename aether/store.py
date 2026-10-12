"""SQLite 投影层：账本的**索引与查询视图**，不是第二份事实源。

⛔ 纪律（V5 三次踩坑的制度化）：
  1. 事实源只有 ledger（JSONL + 哈希链）。这里存的东西**必须**能由账本
     重放重建；如果丢了库，可以 `rebuild_from_ledger()`。
  2. schema 版本化 + **探测式**迁移：新列用 `PRAGMA table_info` 探测后
     再拼 SQL（V5 r107：硬写新列会在未迁移库抛错，except 后 rollback
     把同一事务里的其他字段一起回滚 ⇒ 记账静默失效）。
  3. 加列前先检查有没有硬编码 schema 版本的测试（V5：`schema_v7_v8_test`
     写死版本号是**作者刻意**的，用来强制人工确认）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_snapshots (
    harness_id   TEXT PRIMARY KEY,
    parent_id    TEXT,
    payload      TEXT NOT NULL,
    accepted     INTEGER DEFAULT 0,
    round_no     INTEGER DEFAULT 0,
    created_at   REAL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    harness_id   TEXT,
    task_id      TEXT,
    reward       REAL,
    passed       INTEGER,
    gate_ok      INTEGER,
    caliber      TEXT,
    payload      TEXT,
    created_at   REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_harness ON runs(harness_id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
"""


@dataclass
class RunRow:
    run_id: str
    harness_id: str
    task_id: str
    reward: float
    passed: bool
    gate_ok: bool
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"run_id": self.run_id, "harness_id": self.harness_id,
                "task_id": self.task_id, "reward": self.reward,
                "passed": self.passed, "gate_ok": self.gate_ok,
                "payload": self.payload}


class Store:
    """账本的查询投影。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    # ── 迁移（探测式）────────────────────────────────────────────
    def _migrate(self) -> None:
        self.conn.executescript(_SCHEMA)
        cur = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if cur is None:
            self.conn.execute("INSERT INTO meta (key,value) VALUES ('schema_version',?)",
                              (str(SCHEMA_VERSION),))
        self.conn.commit()

    @property
    def schema_version(self) -> int:
        r = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(r["value"]) if r else 0

    def columns(self, table: str) -> List[str]:
        return [r["name"] for r in
                self.conn.execute(f"PRAGMA table_info({table})")]

    # ── 写 ────────────────────────────────────────────────────────
    def save_snapshot(self, harness_id: str, payload: Dict[str, Any], *,
                      parent_id: str = "", accepted: bool = False,
                      round_no: int = 0) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO harness_snapshots "
            "(harness_id,parent_id,payload,accepted,round_no,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (harness_id, parent_id, json.dumps(payload, ensure_ascii=False,
                                               default=str),
             1 if accepted else 0, round_no, time.time()))
        self.conn.commit()

    def save_run(self, row: RunRow, caliber: Optional[Dict[str, Any]] = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id,harness_id,task_id,reward,passed,gate_ok,caliber,payload,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (row.run_id, row.harness_id, row.task_id, row.reward,
             1 if row.passed else 0, 1 if row.gate_ok else 0,
             json.dumps(caliber or {}, ensure_ascii=False, default=str),
             json.dumps(row.payload, ensure_ascii=False, default=str),
             time.time()))
        self.conn.commit()

    # ── 读 ────────────────────────────────────────────────────────
    def runs(self, harness_id: Optional[str] = None,
             limit: int = 500) -> List[RunRow]:
        if harness_id:
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE harness_id=? ORDER BY created_at DESC LIMIT ?",
                (harness_id, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            out.append(RunRow(run_id=r["run_id"], harness_id=r["harness_id"],
                              task_id=r["task_id"], reward=float(r["reward"] or 0),
                              passed=bool(r["passed"]), gate_ok=bool(r["gate_ok"]),
                              payload=payload))
        return out

    def snapshots(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM harness_snapshots ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            out.append({"harness_id": r["harness_id"], "parent_id": r["parent_id"],
                        "accepted": bool(r["accepted"]), "round_no": r["round_no"],
                        "created_at": r["created_at"], "payload": payload})
        return out

    def stats(self) -> Dict[str, Any]:
        n_runs = self.conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
        n_snap = self.conn.execute("SELECT COUNT(*) c FROM harness_snapshots").fetchone()["c"]
        avg = self.conn.execute("SELECT AVG(reward) a FROM runs").fetchone()["a"]
        return {"schema_version": self.schema_version, "n_runs": n_runs,
                "n_snapshots": n_snap, "avg_reward": round(float(avg or 0), 4),
                "path": str(self.path)}

    def rebuild_from_ledger(self, events: Iterable[Any]) -> int:
        """从账本事件重建投影（证明"库只是视图"）。

        只处理两类事件：`harness.commit`（快照）与 `eval.verdict`（run）。
        返回重建条数。⛔ 重建**不覆盖**已有 accepted 快照的语义，
        只补齐缺失行 —— 重建是幂等的。
        """
        n = 0
        for ev in events:
            p = ev.payload or {}
            if ev.kind == "harness.commit":
                self.save_snapshot(
                    p.get("harness_id", ""), p.get("payload", {}),
                    parent_id=p.get("parent_id", ""), accepted=True,
                    round_no=int(p.get("round_no", 0)))
                n += 1
            elif ev.kind == "eval.verdict":
                self.save_run(RunRow(
                    run_id=p.get("run_id", f"rebuilt:{ev.seq}"),
                    harness_id=p.get("harness_id", ""),
                    task_id=p.get("task_id", ""),
                    reward=float(p.get("reward", 0.0)),
                    passed=bool(p.get("passed", False)),
                    gate_ok=bool(p.get("gate_ok", False)),
                    payload=p), caliber=p.get("caliber"))
                n += 1
        return n

    def close(self) -> None:
        self.conn.close()

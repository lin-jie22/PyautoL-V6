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

    def save_run(self, row: RunRow, caliber: Optional[Dict[str, Any]] = None) -> bool:
        """写入一条 run。返回 True 表示**覆盖了内容不同的同键行**。

        ⛔⛔ 2026-10-13 实测事故：`run_id` 曾由 (harness, task) 拼出 ⇒
        同一组合的多次执行互相覆盖 ⇒ 账本 495 条执行、投影只剩 51 条
        （**89.7% 的执行历史被静默抹掉**），而内存里的 `_runs` 是完整的
        ⇒ 投影与内存账不一致 ⇒ "库只是视图"这句话是假的。

        修法两头做：① `run_id` 唯一化（见 `evolution.evaluate_dev`）；
        ② **覆盖本身必须可见** —— 一旦发生就累加到 `meta.overwrite_count`
        并在 `stats()` / 一致性报告里报出来。静默正确才是这里最大的风险。
        """
        payload_s = json.dumps(row.payload, ensure_ascii=False, default=str)
        caliber_s = json.dumps(caliber or {}, ensure_ascii=False, default=str)
        prev = self.conn.execute(
            "SELECT reward,passed,gate_ok,payload FROM runs WHERE run_id=?",
            (row.run_id,)).fetchone()
        overwrote = False
        if prev is not None:
            same = (abs(float(prev["reward"] or 0) - float(row.reward)) < 1e-9
                    and bool(prev["passed"]) == bool(row.passed)
                    and bool(prev["gate_ok"]) == bool(row.gate_ok)
                    and str(prev["payload"] or "") == payload_s)
            #: 同键同内容 ⇒ 幂等重写，不算事故；同键不同内容 ⇒ 丢历史
            overwrote = not same
        self.conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id,harness_id,task_id,reward,passed,gate_ok,caliber,payload,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (row.run_id, row.harness_id, row.task_id, row.reward,
             1 if row.passed else 0, 1 if row.gate_ok else 0,
             caliber_s, payload_s, time.time()))
        if overwrote:
            self.conn.execute(
                "INSERT INTO meta (key,value) VALUES ('overwrite_count','1') "
                "ON CONFLICT(key) DO UPDATE SET value="
                "CAST(CAST(value AS INTEGER)+1 AS TEXT)")
        self.conn.commit()
        return overwrote

    @property
    def overwrite_count(self) -> int:
        r = self.conn.execute(
            "SELECT value FROM meta WHERE key='overwrite_count'").fetchone()
        return int(r["value"]) if r else 0

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
                #: ⛔ 同键覆盖次数 > 0 ⇒ 投影丢过历史（读数必须能看见事故）
                "overwrite_count": self.overwrite_count,
                "path": str(self.path)}

    def snapshot_ids(self) -> List[str]:
        return [r["harness_id"] for r in self.conn.execute(
            "SELECT harness_id FROM harness_snapshots").fetchall()]

    def run_ids(self, limit: int = 100000) -> List[str]:
        return [r["run_id"] for r in self.conn.execute(
            "SELECT run_id FROM runs LIMIT ?", (limit,)).fetchall()]

    def rebuild_from_ledger(self, events: Iterable[Any]) -> Dict[str, int]:
        """从账本事件重建投影（证明"库只是视图"）。

        处理三类事件：`harness.init`（初始 harness）/ `harness.commit`
        （提交快照）/ `eval.verdict`（run）。

        ⛔⛔ 2026-10-13 实测：这个函数之前**只被测试调用过一次，而那次用的
        是恒真断言**（`n >= 0`）⇒ "库可由账本重建"这句宪法从未被真正验证。
        真跑一遍就露馅：账本 495 条执行、重建与现状**键空间完全不同**
        （账本不带 `run_id`，重建只能造 `rebuilt:{seq}`）⇒ 本可比对却无从
        比对。所以现在 `eval.verdict` **必须**带 `run_id`（见 evolution），
        重建按它对齐；对账本里不带 `run_id` 的历史事件，**如实记为
        `unalignable`，而不是拿假键凑数**。

        缺 `run_id` 的历史事件用**派生键** `rebuilt:{seq}` 写入 —— 它不是
        假键（seq 就是它在账本里的位置，可溯源），只是无法与当年那次运行
        用的键对上。⛔ 但不能让它冒充"对齐"：单独计进 `derived`。

        返回计数（不是"成功与否"）：
          `snapshots` / `runs` / `derived`（用派生键补齐的历史事件）
        """
        n_snap = n_run = n_derived = 0
        for ev in events:
            p = ev.payload or {}
            if ev.kind in ("harness.init", "harness.commit"):
                self.save_snapshot(
                    p.get("harness_id", ""), p.get("payload", {}),
                    parent_id=p.get("parent_id", ""), accepted=True,
                    round_no=int(p.get("round_no", 0)))
                n_snap += 1
            elif ev.kind == "eval.verdict":
                rid = p.get("run_id")
                if not rid:
                    #: 历史事件（本轮改动之前写的）不带 run_id ⇒ 用派生键
                    rid = f"rebuilt:{ev.seq}"
                    n_derived += 1
                self.save_run(RunRow(
                    run_id=str(rid),
                    harness_id=p.get("harness_id", ""),
                    task_id=p.get("task_id", ""),
                    reward=float(p.get("reward", 0.0)),
                    passed=bool(p.get("passed", False)),
                    gate_ok=bool(p.get("gate_ok", False)),
                    payload=p), caliber=p.get("caliber"))
                n_run += 1
        return {"snapshots": n_snap, "runs": n_run, "derived": n_derived}

    # ── 一致性：账本（事实源） vs 投影（视图）─────────────────────
    def consistency_report(self, events: Iterable[Any]) -> Dict[str, Any]:
        """账本与投影是否对得上 —— **可被证伪**的那种对得上。

        ⛔ 这不是日志，是判据：`ok=false` 说明"库只是视图"这句话当前不成立，
        任何基于投影的读数（控制台 / `status` / 监督器）都不可信。

        ⛔ 只拿**账本里有 run_id** 的事件做键比对；缺 run_id 的历史事件单独
        记 `unalignable`，绝不参与 `ok` 的判定 —— 否则"历史遗留"会被算成
        "一致"，这正是上一版静默失败的同形态错误。
        """
        ledger_runs: Dict[str, Dict[str, Any]] = {}
        n_unkeyed = 0
        ledger_snaps: List[str] = []
        n_verdicts = 0
        for ev in events:
            p = ev.payload or {}
            if ev.kind == "eval.verdict":
                n_verdicts += 1
                rid = p.get("run_id")
                if rid:
                    ledger_runs[str(rid)] = p
                else:
                    n_unkeyed += 1
            elif ev.kind in ("harness.init", "harness.commit"):
                hid = p.get("harness_id", "")
                if hid:
                    ledger_snaps.append(hid)

        proj_runs = set(self.run_ids())
        proj_snaps = set(self.snapshot_ids())

        missing = sorted(set(ledger_runs) - proj_runs)      # 账本有、投影没
        #: ⛔ 派生键（历史事件重建出来的 `rebuilt:{seq}`）不是异常：
        #:    把它们算进 extra 会让"补齐历史"看起来像"多出脏数据"。
        extra = sorted(r for r in proj_runs - set(ledger_runs)
                       if not r.startswith("rebuilt:"))
        #: 字段级差异（同键内容不同 ⇒ 投影被别的东西改过）
        drifted: List[str] = []
        proj_rows = {r.run_id: r for r in self.runs(limit=100000)}
        for rid, p in ledger_runs.items():
            row = proj_rows.get(rid)
            if row is None:
                continue
            if (abs(float(p.get("reward", 0.0)) - row.reward) > 1e-9
                    or bool(p.get("passed", False)) != row.passed
                    or str(p.get("harness_id", "")) != row.harness_id):
                drifted.append(rid)

        snap_missing = sorted(set(ledger_snaps) - proj_snaps)
        #: ⛔ 条数比对是**丢历史**的唯一抓法：键比对只能发现"对不上"，
        #:    发现不了"压根没写进来"。实测就是靠这一条抓到 495 → 51。
        count_ok = (len(proj_runs) == n_verdicts)
        ok = (not missing) and (not drifted) and (not snap_missing) and count_ok
        return {
            "ok": ok,
            "ledger_verdicts": n_verdicts,
            "ledger_runs_keyed": len(ledger_runs),
            "unkeyed_events": n_unkeyed,
            "projection_runs": len(proj_runs),
            "count_ok": count_ok,
            "missing_in_projection": missing[:20],
            "missing_count": len(missing),
            "extra_in_projection": extra[:20],
            "extra_count": len(extra),
            "drifted": drifted[:20],
            "drift_count": len(drifted),
            "snapshot_missing": snap_missing,
            "overwrite_count": self.overwrite_count,
            "note": ("" if ok else
                     "投影与账本不一致 ⇒ 基于投影的读数不可信"
                     "（修法：`python -m aether.cli rebuild`）"),
        }

    def close(self) -> None:
        self.conn.close()

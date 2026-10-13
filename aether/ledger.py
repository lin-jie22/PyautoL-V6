"""内容寻址追加账本（Append-only Content-Addressed Ledger）。

这是 V6 的**唯一事实源**，也是跨语言契约：Python 核心写，TypeScript
监督器/控制台读。所有进化事件、验证判定、闸门裁决都进这里，可回放、
可审计、可回滚。

为什么不是"数据库里再记一份"（V5 的教训）：
  V5 把状态散在 memory.db / reflection_rag.db / fix_library.db 三处 + 多个
  JSON，导致"记账方式变了 ⇒ Δ 必须拆记账项/能力项"这类对账灾难。V6 把
  **写入路径收敛成一条**：任何想成为事实的东西必须先 append 到这里；
  SQLite 只做索引与查询视图，是账本的**投影**，不是第二份事实源。

前沿依据（2026）：
  · Evolver 的 GEP 用 `events.jsonl` 做可审计进化史，append-only；
  · Emergent Mind 对 self-harness 的综述把「可回滚 + 爆炸半径」列为
    自进化系统的必备属性 —— 没有 append-only 史，回滚就是空话；
  · Memory governance（Mem0 2026 报告）：审计轨迹与留存策略正在变成
    产品需求（SOC 2 / ISO 42001 已开始问 memory hygiene）。

设计要点：
  1. 每条事件带 `prev_hash` ⇒ 形成哈希链，**中间改一条就会断链**；
  2. `hash` 由**身份字段**决定（seq/ts/kind/actor/payload/prev_hash），
     不是"整行字符串" ⇒ 与 V5 `data-artifact-contract` 里"内容寻址 id
     只由身份字段决定"同源；
  3. 多进程安全：append 走 `fcntl.flock` + `os.replace` 之外的追加语义
     （追加本身在 O_APPEND 下是原子的，锁用于防交叉写坏 JSON 行）；
  4. 只读校验 `verify_chain()` 可在任何时刻证明"这段历史没被篡改过"。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

#: 冷启动只读尾部多少字节来找"最后一条事件"。
#: ⛔ 必须 ≥ 单条事件的最大行长，否则缓冲区里可能一个完整行都没有 ⇒
#:    退回全扫（慢但不出错）。64 KB 对 payload 里带源码片段的事件够了；
#:    真正超长时也不会算错，只是退化到全扫。
HYDRATE_TAIL_BYTES = 64 * 1024

LEDGER_VERSION = 1


def _norm(obj: Any) -> Any:
    """数字规范化：**跨语言序列化一致性的关键**。

    Python 的 `json.dumps(1.0)` 是 `1.0`，JS 的 `JSON.stringify(1.0)` 是 `1`
    ⇒ 同一个 payload 在两端算出**不同的 hash** ⇒ 跨语言链校验全线失败。

    约定：整数值的 float 一律降级为 int（`1.0 → 1`）。
    非整数 float 两边都用"最短往返表示"（Python repr / JS String），通常一致。
    """
    if isinstance(obj, dict):
        return {k: _norm(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_norm(v) for v in obj]
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    return obj


def canonical_json(obj: Any) -> str:
    """确定性序列化：键排序 + 紧凑分隔 + 非 ASCII 不转义 + 数字规范化。

    ⛔ 必须跨语言确定性：TS 监督器（`supervisor/src/ledger.ts`）用**同一套**
    规则重算 hash 来校验链。任何一端格式漂移都会被 `verify_chain` 抓到。
    """
    return json.dumps(_norm(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Event:
    """账本事件。**冻结** —— 入账后不可改，改了就断链。"""
    seq: int
    ts: float
    kind: str                       # e.g. "harness.commit" / "verdict" / "gate"
    actor: str                      # 谁做的：evolution / supervisor / human / env
    payload: Dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""

    def identity(self) -> Dict[str, Any]:
        """身份字段 —— hash 只由这些决定（不含 hash 自身）。

        ⛔ ts 用**毫秒整数**：浮点时间戳在不同语言里序列化不一致
        （`1.0` vs `1`），会直接破坏跨语言链校验。
        """
        return {
            "v": LEDGER_VERSION,
            "seq": self.seq,
            "ts": int(round(self.ts * 1000)),
            "kind": self.kind,
            "actor": self.actor,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }

    def to_line(self) -> str:
        return canonical_json(asdict(self)) + "\n"

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Event":
        return Event(
            seq=int(d.get("seq", 0)),
            ts=float(d.get("ts", 0.0)),
            kind=str(d.get("kind", "")),
            actor=str(d.get("actor", "")),
            payload=d.get("payload") or {},
            prev_hash=str(d.get("prev_hash", "")),
            hash=str(d.get("hash", "")),
        )


class Ledger:
    """JSONL 账本。线程/进程安全追加，只读校验。

    ⛔ 不提供任何 delete / update 接口 —— 这是**故意**的。想"纠正"一条
    事实就 append 一条新事件（例如 `harness.revert`），历史永远是历史。
    """

    GENESIS_HASH = "0" * 64

    def __init__(self, path: str | Path, session: Optional[str] = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._last_hash = self.GENESIS_HASH
        self._seq = 0
        #: ⛔ 会话归属：`round_no` **每次进程启动都从 1 重新数**，
        #:    没有它，"同一轮"会把跨小时、跨运行的事件串成一对 ——
        #:    监督器那条"泛化闸被绕过"的规则曾因此把 8 条合法提交
        #:    全判成 critical（实测）。跨轮/跨会话类判据必须有归属。
        #: ⛔ 用 `is None` 而不是 `or`：显式传空串 = "这条没有会话归属"
        #:    （测试要靠它模拟升级前的事件），不能悄悄补一个 uuid 变成
        #:    "有归属" —— 那等于把缺失伪装成存在。
        self.session = (session if session is not None
                        else uuid.uuid4().hex[:12])
        self._hydrate()

    # ── 内部 ──────────────────────────────────────────────────────
    def _hydrate(self) -> None:
        """冷启动：拿到最新 seq 与 hash。空文件不算错。

        ⛔⛔ 只读**尾部**，不全扫。账本 append-only 且写入在独占锁内串行
        ⇒ 最新事件必然在文件末尾；为了两个字段去全量解析一遍，在
        `/api/status` 上就是每个请求多付一遍 O(n)（2026-10-13 实测：
        一次请求要解析全量 **4.7 遍**，`_hydrate` 是其中一整遍）。

        ⛔ 与全扫**等价**的边界（靠 covered by 测试，不靠信心）：
        · 末尾有坏行（崩出来的半截写入）⇒ 回退到上一个可解析的事件，
          与全扫的 `except: continue` 同果；
        · 缓冲区里一个可解析的都没有 ⇒ **退回全扫**，绝不返回"看起来正常
          的旧 hash"（那是把缺失伪装成存在）；
        · 跨边界截断：偏移非 0 时丢掉第一段（它可能是半截行）。
        """
        last = self._read_tail_event()
        if last is not None:
            self._seq = last.seq
            self._last_hash = last.hash
        elif self.path.exists() and self.path.stat().st_size > 0:
            #: 尾部一个可解析的都没有（整个缓冲区全是坏行）⇒ 退全扫。
            #: 绝不落到"什么都不设" —— 那会把缺失伪装成 genesis。
            self._hydrate_full()

    def _read_tail_event(self) -> Optional[Event]:
        """读**最后一条**可解析事件。读不到 ⇒ None（调用方退回全扫）。"""
        if not self.path.exists():
            return None
        size = self.path.stat().st_size
        if size == 0:
            return None
        offset = max(0, size - HYDRATE_TAIL_BYTES)
        with self.path.open("rb") as f:
            f.seek(offset)
            chunk = f.read(HYDRATE_TAIL_BYTES + (1 if offset else 0))
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:      # 尾巴正好切断多字节字符
            text = chunk.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        if offset:
            lines = lines[1:]           # 第一段可能是半截行
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                return Event.from_dict(json.loads(line))  # type: ignore[return-value]
            except Exception:  # noqa: BLE001 —— 坏行就往回退
                continue
        return None

    def _hydrate_full(self) -> None:
        """全扫版（保底路径）。语义与改造前的 `_hydrate` 逐字相同。"""
        if not self.path.exists():
            return
        last = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = Event.from_dict(json.loads(line))
                except Exception:  # noqa: BLE001 —— 坏行不拖垮冷启动
                    continue
        if last is not None:
            self._seq = last.seq
            self._last_hash = last.hash

    def _acquire(self):
        self._lock_path.touch(exist_ok=True)
        fh = self._lock_path.open("r+")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    # ── 写 ────────────────────────────────────────────────────────
    def append(self, kind: str, payload: Dict[str, Any],
               actor: str = "system", ts: Optional[float] = None) -> Event:
        """追加一条事件。返回带 hash 的 Event。

        ⛔ 先加锁再重算 `_last_hash`：并发会话可能已经在文件里写了新行，
        直接沿用内存里的旧 hash 会造出分叉链（V5 踩过同形态的坑：
        热进程回写覆盖文件修改）。
        """
        fh = self._acquire()
        try:
            self._hydrate()                      # 重读，防并发分叉
            seq = self._seq + 1
            #: 会话归属写进 payload（⛔ 不是新增 Event 字段 —— 那会改跨语言
            #: hash 契约，TS 侧必须同步才能校验链；payload 是本就参与 hash
            #: 的自由区，历史文件缺 `sid` 也不影响链校验）
            body = dict(payload or {})
            body.setdefault("sid", self.session)
            ev = Event(seq=seq, ts=ts if ts is not None else time.time(),
                       kind=kind, actor=actor, payload=body,
                       prev_hash=self._last_hash, hash="")
            ev = Event(**{**asdict(ev), "hash": content_hash(ev.identity())})
            with self.path.open("a", encoding="utf-8") as f:
                f.write(ev.to_line())
                f.flush()
                os.fsync(f.fileno())
            self._seq = ev.seq
            self._last_hash = ev.hash
            return ev
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    # ── 读 ────────────────────────────────────────────────────────
    def events(self) -> List[Event]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Event.from_dict(json.loads(line)))
                except Exception:  # noqa: BLE001
                    continue
        return out

    def iter_lines(self) -> Iterator[str]:
        """给跨语言消费者用：直接吐原始行（TS 侧自己解析）。"""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield line

    def tail(self, n: int = 20) -> List[Event]:
        return self.events()[-n:]

    def head_hash(self) -> str:
        return self._last_hash

    @property
    def seq(self) -> int:
        """当前最新 seq（0 = 空账本）。

        ⛔ 给"需要全局单调锚"的调用方用（例如 run_id 编号）：账本 seq 是
        唯一既单调、又跨运行不重置的东西 —— 用进程内计数器会在第二次
        运行时从头开始 ⇒ 撞键 ⇒ 投影静默覆盖（2026-10-13 实测事故）。
        """
        return self._seq

    # ── 校验 ──────────────────────────────────────────────────────
    def verify_chain(self, events: Optional[List[Event]] = None
                     ) -> Tuple[bool, Optional[int], str]:
        """校验整条哈希链。

        返回 (ok, first_bad_seq, reason)。
        ⛔ 这是**判据**，不是日志：任何消费者（含 TS 监督器）都应该在
        启动与每次快照时跑它，断了就告警而不是继续读。

        ⛔ `events` 用来复用**已经读过**的事件（不传则自行读一遍）。
        2026-10-13 实测：一次 `/api/status` 会走到 **3 遍全量 JSONL 解析**
        （`stats()` 一次 + `stats()` 里的 `verify_chain()` 一次 +
        `_collect` 自己一次）。这不只是浪费 —— 三遍读的是三个不同时刻的
        快照，**同一份响应里三个读数可能互相矛盾**。读完一次喂给所有消费
        者，既省两遍，也消除了这个窗口。
        """
        prev = self.GENESIS_HASH
        for ev in (self.events() if events is None else events):
            if ev.prev_hash != prev:
                return False, ev.seq, f"prev_hash 不连续（期望 {prev[:12]}… 实得 {ev.prev_hash[:12]}…）"
            want = content_hash(ev.identity())
            if ev.hash != want:
                return False, ev.seq, f"hash 不匹配（payload 被改过？实得 {ev.hash[:12]}… 应为 {want[:12]}…）"
            prev = ev.hash
        return True, None, ""

    # ── 回放 ──────────────────────────────────────────────────────
    def replay(self, kinds: Optional[List[str]] = None,
               since_seq: int = 0) -> List[Event]:
        ks = set(kinds) if kinds else None
        return [e for e in self.events()
                if e.seq >= since_seq and (ks is None or e.kind in ks)]

    def stats(self, events: Optional[List[Event]] = None
              ) -> Dict[str, Any]:
        """账本概览 + **当场**的链校验结果。

        `events` 语义同 `verify_chain()`：传进来已读好的事件 ⇒ 不再重读
        文件。⛔ 不传时的默认行为与改动前**完全相同**，改动只发生在
        "调用方手头已经有一份快照"这条路上。
        """
        evs = self.events() if events is None else events
        by_kind: Dict[str, int] = {}
        for e in evs:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        ok, bad, reason = self.verify_chain(evs)
        return {
            "path": str(self.path),
            "count": len(evs),
            "head": self._last_hash[:16],
            "by_kind": by_kind,
            "chain_ok": ok,
            "first_bad_seq": bad,
            "reason": reason,
        }

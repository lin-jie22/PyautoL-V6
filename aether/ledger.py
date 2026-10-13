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
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

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
    def iter_events(self, since_offset: int = 0,
                    progress: Optional[Dict[str, Any]] = None,
                    ) -> Iterator[Event]:
        """**流式**遍历：一次只在内存里留一条事件。

        ⛔ `events()` 物化整份账本 —— 4000 条 / 5 MB 的账本实测峰值
        **8.18 MB**，占一次 `/api/status` 响应的 **99%**。而所有消费者
        （统计 / 链校验 / 一致性 / 记忆对账）**都只是遍历一遍** ⇒ 物化
        出来的那份列表除了占内存没有任何用处。

        ⛔ 与 `events()` **逐条同源**：`events()` 就是 `list(iter_events())`。
        绝不写第二份解析逻辑 —— 两份逻辑会在坏行/编码上漂移，漂移后
        "流式读数"与"全量读数"不一致，而没人会发现。

        ⛔⛔ `since_offset` / `progress` 只服务**增量消费者**（2026-10-13
        第 16 轮 `_deadend_map`）：续读时传上次记下的 `progress["offset"]`，
        就只读新增部分。
          · `progress["offset"]` **只在一条事件被完整解析之后**才推进 ⇒
            offset 永远停在行边界上。
          · 为什么必须这样：并发 append 时最后一行可能**只写了一半**
            （写锁在行写完才释放，但我们可能在写完前读到）。若把半行后的
            位置记成 offset，下次 `seek` 会落在**一行的中间** ⇒ 之后每次
            都从中间读起，错位是永久且静默的（读到的都是垃圾行，而垃圾行
            被 `except` 吞掉 ⇒ 看起来像"账本没新事件"）。
            ⇒ 宁可**重读一条**，不可**错位一行**。
          · 调用方仍需自己校验链的连续性（`prev_hash`）—— 偏移只保证
            "从哪读"，不保证"前面的内容没被改过"。

        ⛔⛔ **为什么这里用二进制打开而不是 `open(..., "r")`**：文本模式下
        `for line in f` 迭代期间调 `f.tell()` 会直接抛
        `OSError: telling position disabled by next() call`（2026-10-13
        实测）。第一版用文本模式 + `except: pass` 兜住 —— 于是位置**永远
        没被记录**（停在 0），每次续读都从头开始，看上去"增量逻辑跑通了"，
        实测 `incremental=0 / full=6`。**把失败吞掉就等于把降级伪装成成功**
        （V5 同款病）⇒ 这里改为二进制读，`tell()` 是精确的字节偏移，
        且**不再吞异常**：记不下位置就让调用方看见。
        """
        if not self.path.exists():
            return
        with self.path.open("rb") as f:
            if since_offset and since_offset > 0:
                f.seek(since_offset)
            for raw in f:
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    #: 坏编码行：与坏 JSON 行同等处理（跳过，不污染整份读）
                    continue
                if not line:
                    continue
                try:
                    ev = Event.from_dict(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
                if progress is not None:
                    progress["offset"] = f.tell()
                yield ev

    def events(self) -> List[Event]:
        return list(self.iter_events())

    def iter_lines(self) -> Iterator[str]:
        """给跨语言消费者用：直接吐原始行（TS 侧自己解析）。"""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield line

    def tail(self, n: int = 20) -> List[Event]:
        """最后 `n` 条事件（正序）。

        ⛔ 不得写成 `self.events()[-n:]` —— 那会**物化整份账本**只为丢掉
        前面全部（与 `/api/events` 曾踩的同一个洞：为取尾部 200 条而解析
        1533 条）。`deque(maxlen=n)` 只留窗口 ⇒ 内存 O(n) 而非 O(全量)。

        ⛔ `n <= 0` ⇒ 空列表（与 `deque(maxlen=0)` 一致，绝不退化成
        "不封顶" —— 见 §18：`limit` 的负数语义必须是"0 条"）。
        """
        return list(deque(self.iter_events(), maxlen=max(0, int(n))))

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
    def verify_chain(self, events: Optional[Iterable[Event]] = None
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

        ⛔ 链推进逻辑在 `_chain_feed`（唯一事实源）。这里保留**提前
        break**（断链后不再校验后续）—— 与改造前逐字同行为；`stats()`
        不 break 是为了把 `by_kind` 数全，**不是**另一套判据。
        """
        state = _new_chain_state()
        for ev in (self.iter_events() if events is None else events):
            _chain_feed(state, ev)
            if state["bad"] is not None:
                break
        return state["bad"] is None, state["bad"], state["reason"]

    # ── 回放 ──────────────────────────────────────────────────────
    def iter_replay(self, kinds: Optional[List[str]] = None,
                    since_seq: int = 0) -> Iterator[Event]:
        """**流式**重放：一次只在内存里留一条事件。

        ⛔ `replay()` 是"全量物化 + 过滤 + 再建一份列表" —— 而 `/api/events`
        只想要**最后 limit 条**。2026-10-13 实测：生产账本 1533 条，为了
        取尾部 200 条，`replay()` 峰值 **6.50 MB**。上一轮把 `/api/status`
        流式化了，这里却是同形态的问题原封不动 —— **只修一半等于没修**。

        ⛔ 与 `replay()` **逐条同源**（`replay()` = `list(iter_replay(...))`）。
        """
        ks = set(kinds) if kinds else None
        for e in self.iter_events():
            if e.seq >= since_seq and (ks is None or e.kind in ks):
                yield e

    def replay(self, kinds: Optional[List[str]] = None,
               since_seq: int = 0) -> List[Event]:
        return list(self.iter_replay(kinds, since_seq))

    def stats_agg(self) -> "LedgerStatsAgg":
        """拿一个**流式**统计聚合器（给一次遍历喂多消费者的调用方）。"""
        return LedgerStatsAgg(path=str(self.path), head=self._last_hash[:16])

    def stats(self, events: Optional[Iterable[Event]] = None
              ) -> Dict[str, Any]:
        """账本概览 + **当场**的链校验结果。

        `events` 语义同 `verify_chain()`：传进来已读好的事件 ⇒ 不再重读
        文件。⛔ 不传时的默认行为与改动前**完全相同**，改动只发生在
        "调用方手头已经有一份快照"这条路上。

        ⛔ 与 `stats_agg()` **同一份**逻辑（本函数就是建 agg → feed →
        result）。调用方要么用这个、要么用 agg，**绝不许再写第三份**
        —— 三份读数里只要有一份漂移，"控制台说没事"就是假没事。
        """
        agg = self.stats_agg()
        for ev in (self.iter_events() if events is None else events):
            agg.feed(ev)
        return agg.result()


def _new_chain_state() -> Dict[str, Any]:
    """链校验游标。"""
    return {"prev": Ledger.GENESIS_HASH, "bad": None, "reason": ""}


def _chain_feed(state: Dict[str, Any], ev: Event) -> None:
    """推进一条事件的链校验 —— **链语义的唯一事实源**。

    `verify_chain()` 与 `LedgerStatsAgg.feed()` 都走这里，绝不各写一份：
    两份实现只要在"断链后是否继续推进 prev"上分歧，同一份账本就会在
    控制台和监督器上给出不同结论，而两边都能自称"链是好的"。

    ⛔ 断链后**不再推进** prev（与改造前 `verify_chain` 的 `return`
    逐字同源）。`stats()` 不 break 只是要把 `by_kind` 数全 —— 断链后
    继续 feed 不会改变 `first_bad_seq`，因为已断就不再改写。
    """
    if state["bad"] is not None:
        return
    if ev.prev_hash != state["prev"]:
        state["bad"] = ev.seq
        state["reason"] = (f"prev_hash 不连续（期望 {state['prev'][:12]}… "
                           f"实得 {ev.prev_hash[:12]}…）")
        return
    want = content_hash(ev.identity())
    if ev.hash != want:
        state["bad"] = ev.seq
        state["reason"] = (f"hash 不匹配（payload 被改过？实得 {ev.hash[:12]}… "
                           f"应为 {want[:12]}…）")
        return
    state["prev"] = ev.hash


class LedgerStatsAgg:
    """`stats()` 的**流式**形态：逐条 `feed()`，最后 `result()`。

    存在的理由：一次 `/api/status` 要同时产出统计 / 链校验 / 一致性 /
    记忆对账 / 尾部窗口五份读数。改造前它们各自要一份**全量事件列表**
    ⇒ 4000 条账本实测峰值 8.27 MB，其中 99% 花在"持有"上。改成一次
    磁盘遍历、五个聚合器**共享同一条流**，峰值只取决于各自要留的
    那点派生量。

    ⛔ 如实：这不等于 O(1)。`by_kind` 随 kind 种类数增长（有界），
    链校验只留 prev + 首个坏点。真正随事件数增长的是别的聚合器
    （一致性要留 run_id 键、记忆对账要留 note_id 键）—— 那些是**短
    字符串**，不再是 Event 对象 + payload 全量。
    """

    def __init__(self, path: str = "", head: str = ""):
        self.path = path
        #: ⛔ `head` 由调用方给（`Ledger._last_hash`），**不在这里重读文件**：
        #:    重读就意味着又开一遍文件、又可能读到不同时刻的末尾。
        self.head = head
        self.n = 0
        self.by_kind: Dict[str, int] = {}
        self._chain = _new_chain_state()

    def feed(self, ev: Event) -> None:
        self.n += 1
        k = ev.kind
        self.by_kind[k] = self.by_kind.get(k, 0) + 1
        #: ⛔ 与 `verify_chain` **同一份**推进逻辑
        _chain_feed(self._chain, ev)

    def result(self) -> Dict[str, Any]:
        bad = self._chain["bad"]
        return {
            "path": self.path,
            "count": self.n,
            "head": self.head,
            "by_kind": self.by_kind,
            "chain_ok": bad is None,
            "first_bad_seq": bad,
            "reason": self._chain["reason"],
        }

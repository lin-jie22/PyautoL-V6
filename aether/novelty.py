"""新颖性守卫：**已经存在的状态不再提交**。

⛔ 为什么需要它（实测，不是推断）：

    data/ledger.jsonl 里 `harness.commit` 共 **120** 条，去重后
    `(parent_id, capsule)` 只有 **12** 个组合 —— 其中 10 个组合重复了
    108 次（最狠的一条：同一个父节点 + 同一个胶囊，被提交了 **57** 遍）。
    去重后的 `harness_id` 只有 **15** 个。

⇒ 账本记的是"提交了 120 次"，事实是"产生了 12 个新状态"。
   读账本的人会以为自己变强了 120 次。这是**读数撒谎**，不是效率问题。

根因不是"提议器太笨"，而是**没有任何一层看"这个状态是不是已经有了"**：
  · `archive.add` 有重复检查 —— 但它在**闸之后**（浪费已经发生），
    而且只去重"已入档的条目"，不是"已存在的状态"；
  · `_in_cooldown` 只管"被拦过的" —— 提交成功的恰恰不在它的视野里；
  · 每次运行都从同一个基线出发（`--warm-start` 之前）⇒ 第二轮算出的
    子状态与第一轮**逐字相同**（harness 内容寻址）⇒ 必然会撞。

⛔⛔ 判据用**内容地址**，不另做指纹：
    `Harness.harness_id` 就是基因组的哈希。同一份基因组 ⇒ 同一个 id
    ⇒ 它就是同一个状态。任何"另算一个签名"的做法都要回答"rationale
    算不算""基因顺序算不算"，而这两个问题的答案都会与内容寻址不一致
    （第 99 条：契约根必须从 `__file__`/单一事实源推导）。
    顺带白捡一个能力：**不同胶囊走到同一个状态**（例如两个都写
    `set routing=...` 的编辑）也会被同一个判据拦住。

⛔ 与"死路冷却"严格分工，不许重叠：
    冷却管 **被拦过的**（可能因为基线变了而值得重试，所以有 probation 出口）；
    本模块管 **已经存在的**（重试的结果由内容寻址决定，恒等 ⇒ 无出口）。
    两者混在一起会出现"已存在的状态也在 probation 轮被反复重试"。

⛔ 事实源是**账本**，不是进程内缓存：换一次运行就丢掉的内存集合，
    挡不住跨运行重复（而 120 次提交里 108 次是跨运行的）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .genome import Capsule, Harness, apply_capsule

__all__ = ["COMMIT_KIND", "NoveltyVerdict", "judge", "SeenStates",
           "blocked_replay"]

#: 状态诞生的**唯一**记账点。改成别的名字必须同时改这里（否则守卫失明）。
COMMIT_KIND = "harness.commit"

#: 守卫的三种判定。
#: `ok`           —— 状态是新的，继续走闸
#: `no_change`    —— 候选执行后与父状态逐字相同（空变异）
#: `exists`       —— 该状态已经在账本里出现过（内容地址相同）
#: `apply_failed` —— 应用失败；**不在这里吞掉**，交回原路径处理
OK = "ok"
NO_CHANGE = "no_change"
EXISTS = "exists"
APPLY_FAILED = "apply_failed"

#: 只有这两种才算"重复"。`apply_failed` 必须回原路径 —— 那里的异常
#: 处理会把 `Candidate.reason` 写成 `apply_failed:<异常名>`，是**另一个
#: 事实**（候选本身坏了，不是重复）。
BLOCKING = frozenset({NO_CHANGE, EXISTS})


@dataclass
class NoveltyVerdict:
    kind: str
    harness_id: str = ""
    harness: Optional[Harness] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == OK

    @property
    def blocks(self) -> bool:
        return self.kind in BLOCKING

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "harness_id": self.harness_id,
                "detail": self.detail}


def judge(h: Harness, cap: Capsule,
          seen: "SeenStates") -> NoveltyVerdict:
    """算出候选状态并判定它是不是新的。

    ⛔ 会真的 `apply_capsule`：这一步**不花评估预算**（纯函数、无 runner、
      无 IO），所以放在闸前是免费的；而它的副产品 `harness` 直接交给
      后续流程复用，避免同一轮算两遍。
    """
    try:
        child = apply_capsule(h, cap)
    except Exception as e:                             # noqa: BLE001
        #: ⛔ 不在这里吞：交回原路径，让 `Candidate.reason` 带上异常名。
        return NoveltyVerdict(APPLY_FAILED, detail=f"{type(e).__name__}: {e}")
    hid = str(child.harness_id)
    if hid == str(h.harness_id):
        return NoveltyVerdict(NO_CHANGE, hid, child,
                              "候选执行后与父状态逐字相同（空变异）")
    if hid in seen:
        return NoveltyVerdict(EXISTS, hid, child,
                              "该状态已存在于账本（内容地址相同）")
    return NoveltyVerdict(OK, hid, child, "")


class SeenStates:
    """账本里出现过的状态 id 集合（**增量**读，与死路账同一套失效判据）。

    ⛔ 增量不是"相信文件只追加"，而是**能证明前面没变**：
      ① 偏移只停在行边界；
      ② 新增第一条的 `prev_hash` 必须等于缓存的链头；
      ③ 一条新增都没有但文件指纹变了 ⇒ 可疑 ⇒ 全量重扫（fail-closed）。

    ⛔ 缓存失效**可观测**：`stats` 里记 `full` / `incremental` / 失效原因。
      否则"每轮全扫"和"真增量"在外部看起来一模一样（假仪表）。
    """

    def __init__(self) -> None:
        self._ids: set = set()
        self._offset: int = 0
        self._head: str = ""
        self._fp: Tuple[int, int] = (0, 0)
        self._loaded: bool = False
        self.stats: Dict[str, Any] = {
            "full": 0, "incremental": 0, "scanned": 0,
            "invalidated": 0,
            "reasons": {"first": 0, "shrunk": 0,
                        "no_new_but_touched": 0, "chain_broken": 0}}

    # ── 读 ────────────────────────────────────────────────────────
    def refresh(self, ledger: Any, events: Optional[Iterable[Any]] = None
                ) -> "SeenStates":
        """把账本里新增的提交并进来。返回自身（便于链式调用）。

        `events` 给定时**不做增量**（调用方手头已有一份快照，语义同
        `Ledger.stats(events=...)`）：直接全量扫一遍那些事件。
        """
        if events is not None:
            n = 0
            for ev in events:
                n += self._feed(ev)
            self.stats["scanned"] += n
            return self

        if not self._loaded:
            self.stats["reasons"]["first"] += 1
            self.stats["full"] += 1
            self._full(ledger)
            return self
        if self._step(ledger):
            return self
        return self

    def add(self, harness_id: str) -> None:
        """把本轮刚提交的状态记进来 —— 否则同一轮里第二个候选可以再撞一次。"""
        if harness_id:
            self._ids.add(str(harness_id))

    def __contains__(self, hid: Any) -> bool:
        return str(hid) in self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def to_report(self) -> Dict[str, Any]:
        return {"n_states": len(self._ids),
                "offset": self._offset,
                "stats": dict(self.stats)}

    # ── 内部 ──────────────────────────────────────────────────────
    def _feed(self, ev: Any) -> int:
        """把一条事件里的"已存在状态"吃进来。返回 1（吃过）。"""
        try:
            kind = ev.kind
        except AttributeError:
            return 0
        if kind != COMMIT_KIND:
            return 0
        p = getattr(ev, "payload", None) or {}
        hid = p.get("harness_id")
        if hid:
            self._ids.add(str(hid))
        #: ⛔ 父状态也是"真实存在过的状态"：一次提交的 `parent_id` 指向的
        #:    harness 一定已经存在。不吃它的话，**回到父状态**（撤销一条
        #:    编辑）会被判成新状态 —— 而它显然是旧的。
        pid = p.get("parent_id")
        if pid:
            self._ids.add(str(pid))
        return 1

    def _fp_of(self, ledger: Any) -> Tuple[int, int]:
        try:
            st = ledger.path.stat()
            return (int(st.st_size), int(st.st_mtime_ns))
        except OSError:
            return (0, 0)

    def _full(self, ledger: Any) -> None:
        prog: Dict[str, Any] = {"offset": 0}
        n = 0
        last = ""
        for ev in ledger.iter_events(progress=prog):
            n += self._feed(ev)
            last = str(getattr(ev, "hash", "") or last)
        self._offset = int(prog.get("offset") or 0)
        self._head = last
        self._fp = self._fp_of(ledger)
        self._loaded = True
        self.stats["scanned"] += n

    def _step(self, ledger: Any) -> bool:
        """增量一步。返回 True（无论成功与否，状态都已是最新）。"""
        fp = self._fp_of(ledger)
        if fp[0] < self._offset:                     # ① 文件变小
            self._invalidate("shrunk")
            self._full(ledger)
            return True
        prog: Dict[str, Any] = {"offset": self._offset}
        new = list(ledger.iter_events(since_offset=self._offset,
                                      progress=prog))
        if not new:
            #: ② 没有新增却被写过 ⇒ 无法证明缓存仍成立
            if fp != self._fp:
                self._invalidate("no_new_but_touched")
                self._full(ledger)
            return True
        #: ③ 链必须接得上
        if str(getattr(new[0], "prev_hash", "")) != self._head:
            self._invalidate("chain_broken")
            self._full(ledger)
            return True
        n = 0
        for ev in new:
            n += self._feed(ev)
        self._offset = int(prog.get("offset") or self._offset)
        self._head = str(getattr(new[-1], "hash", "") or self._head)
        self._fp = fp
        self.stats["incremental"] += 1
        self.stats["scanned"] += n
        return True

    def _invalidate(self, why: str) -> None:
        self.stats["invalidated"] += 1
        r = self.stats["reasons"]
        r[why] = int(r.get(why, 0)) + 1


def blocked_replay(ledger: Any) -> Dict[str, Any]:
    """**纯诊断**：如果从一开始就有守卫，历史上有多少提交会被拦。

    ⛔ 只读。不改账本、不跑评估、不影响任何判据。它的用途是回答
      「这个守卫到底值多少」—— 没有它，收益只能靠讲道理。
    """
    seen: set = set()
    total = 0
    blocked: List[Dict[str, Any]] = []
    for ev in ledger.iter_events():
        if getattr(ev, "kind", "") != COMMIT_KIND:
            continue
        p = getattr(ev, "payload", None) or {}
        hid = str(p.get("harness_id") or "")
        pid = str(p.get("parent_id") or "")
        total += 1
        if hid and hid in seen:
            blocked.append({"seq": int(getattr(ev, "seq", 0) or 0),
                            "harness_id": hid, "parent_id": pid,
                            "capsule": p.get("capsule"),
                            "round_no": p.get("round_no")})
        if hid:
            seen.add(hid)
        if pid:
            seen.add(pid)
    return {"n_commits": total,
            "n_distinct_states": len(seen),
            "n_blocked": len(blocked),
            "blocked_rate": round(len(blocked) / total, 4) if total else 0.0,
            "sample": blocked[:10]}

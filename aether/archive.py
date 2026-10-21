"""分层非支配存档（r62）—— 把「够格但没被选中」的解留下来。

═══ 为什么需要它 ═══

`evolution._run_round` 每轮只留**一个** best（`front = best`），其余
`eligible` 候选在这一轮结束后**被丢弃**。被丢弃的不是垃圾：

    · 它们已经**通过准入闸**（advantage / 能力地板 / 增殖闸全过了）；
    · 它们的 reward 与 blast 都已经**算过**（评估成本已付）；
    · 它们和 best 的差别只是"这一轮评分低一点"，而那把尺子是
      **当时那条基线**的尺子。

下一轮基线变了（提交后 harness_id 变了）⇒ 同一个胶囊在新基线上的收益
要**重测**才知道，而旧代码连"重测的机会"都不给 —— 候选对象随轮次一起
被回收。这就是单线爬坡：**一次只走一条路，走错就丢掉所有旁支**。

前沿（population / archive / island 模型）都在做同一件事：**留下帕累托
前沿，而不是只留下当轮冠军**。

═══ 三层纪律（少一层就退化成"多存几个"）═══

① **分层键 = `blast_layers`**（改动触及哪几层），不是候选名、不是 reward。
   为什么：同一层内的解才可比 —— `["skills"]` 与 `["skills","tools"]`
   的 reward 差异里混着"改动面不同"这个维度，混着排队就等于用一把
   会变形的尺子排序（第 31 条同族）。分层让每层各有一条自己的前沿。

② **组内非支配**：目标向量 `(reward, -size_delta)`（reward 越大越好、
   体积增量越小越好）。被支配的解（另一个解两项都不差、至少一项更好）
   直接不入档。⇒ 一层里存的是**帕累托前沿**，不是 Top-K。
   ⛔ 刻意**不**做加权求和：`size_delta` 的单位是"字节/字符"，`reward`
     是 [0,1] 的比例 —— 加权等于给两者编一个汇率，而那个汇率是所有
     后续结论的隐含前提（第 71 条：代价必须能被单独量）。

③ **存档不参与判据**（默认关）：`resample()` 取出来的解**必须重测**。
   存档里的 `reward` 是**旧基线**下的读数，直接拿来排序就是
   "当时好 ⇒ 现在好"，与"held-out 参与排序"是同一个错误
   （见 `generalization.py` H1 的自指死锁说明）。
   ⇒ `Entry.baseline_reward` / `Entry.harness_id` 存下来，就是为了让
     "这条读数是相对谁量的"可查；`resample()` 返回的每一条都带
     `stale=True`，调用方必须重测后才许进判据。

⛔ 默认关闭的理由与 `--ho-grow` / `--retry-every` 同族：换的是**候选
   来源**，不是判据。默认关 ⇒ 与历史逐字同口径（账本里一条 `archive.*`
   事件都不该有），开了才是新实验口径、不与历史同表比。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .genome import Capsule, Gene

#: 每层前沿的保留上限。⛔ 有上限才叫"存档"，没上限就是"全存"：
#: 全存会让 `resample` 的排序退化成"按旧 reward 全局排序"（见纪律③）。
DEFAULT_CAP = 8

#: 层名分隔符。用 `+` 而不是 `:` —— 层名本身可能含 `:`（`L3.skills.a:b`）。
LAYER_SEP = "+"


# ──────────────────────────────────────────────────────────────────
# Capsule 的可逆快照
# ──────────────────────────────────────────────────────────────────
#: ⛔ `Capsule` 是 frozen dataclass 且没有 to_dict（与 `Harness` 不同）。
#:    存档必须能把它**还原成可执行对象**（否则重测无从谈起），所以这里加
#:    一对纯函数。刻意放在 archive 而不是 genome：genome 的公开面每加一个
#:    符号都要过 architecture 基线（`arch_baseline.py`），而这对函数目前
#:    只有一个消费者。真出现第二个消费者时再上提到 genome（第 103 条）。
def capsule_to_dict(cap: Capsule) -> Dict[str, Any]:
    return {"name": cap.name,
            "genes": [{"op": g.op, "target": g.target, "value": g.value,
                       "rationale": g.rationale} for g in cap.genes],
            "rationale": cap.rationale, "origin": cap.origin}


def capsule_from_dict(d: Dict[str, Any]) -> Capsule:
    genes = tuple(Gene(op=str(g["op"]), target=str(g["target"]),
                       value=g.get("value"), rationale=str(g.get("rationale") or ""))
                  for g in (d.get("genes") or []))
    return Capsule(name=str(d["name"]), genes=genes,
                   rationale=str(d.get("rationale") or ""),
                   origin=str(d.get("origin") or ""))


# ──────────────────────────────────────────────────────────────────
# 取回并装配（r63f 抽出）
# ──────────────────────────────────────────────────────────────────
def reseed(archive: "Archive", harness_id: str, capsules: Sequence[Capsule],
           n_wanted: int, round_no: int
           ) -> Tuple[List[Capsule], Dict[str, Any], List[Dict[str, Any]]]:
    """从存档取回**待重测**的候选，还原成可执行的 `Capsule`。

    ⛔ **为什么抽成独立函数**（不是为了好看）：这段装配原来内联在
       `evolution._run_round` 里，而它的可达条件是**一个合取** ——
       「同一轮里 ≥2 个够格候选」且「其中一条被提交」（提交那条的 harness
       变成当前 harness，被 `exclude_harness` 排除；另一条才算旧基线待重测）。
       实测生产 81 轮里 80 轮只有 1 个候选进闸，四次专门运行 11 次 resample
       全部 `n_picked=0` ⇒ **契约构造不出这个条件，这段代码从没被执行过**。
       抽出后三重排除与非装配情形可以逐个打回去
       （见 `contract_archive_reseed_test`）。

    ⛔ **本函数不碰 ledger**：失败以 `errors` 返回，由调用方落账。
       这样单测不需要账本，账本里也不会出现单测伪造的事件。

    返回 `(restored, payload, errors)`：

    | 项 | 含义 |
    |---|---|
    | `restored` | 还原成功的 `Capsule`，**已去重**（与本轮已有名字冲突的跳过、空壳跳过） |
    | `payload`  | `archive.resample` 事件的字段，调用方原样落账 |
    | `errors`   | `[{kind, payload}]`：`archive.resample_failed` / `archive.restore_failed` |
    """
    have = {c.name for c in capsules}
    errors: List[Dict[str, Any]] = []
    picked: List[Dict[str, Any]] = []
    try:
        picked = archive.resample(
            n_wanted, exclude_harness=(harness_id,),
            exclude_capsules=tuple(have),
            #: ⛔ 与 `exclude_harness` 不是一回事：这个排除的是
            #:    "读数就是在**当前**基线上量的"（无重测价值）。
            exclude_baseline=(harness_id,))
    except Exception as e:                             # noqa: BLE001
        errors.append({"kind": "archive.resample_failed",
                       "payload": {"round_no": round_no,
                                   "error": f"{type(e).__name__}: {e}"}})
    restored: List[Capsule] = []
    for g in picked:
        try:
            cap = capsule_from_dict(g["capsule"])
        except Exception as e:                         # noqa: BLE001
            errors.append({"kind": "archive.restore_failed",
                           "payload": {"round_no": round_no,
                                       "error": f"{type(e).__name__}: {e}"}})
            continue
        if cap.name in have or not cap.genes:
            continue
        restored.append(cap)
        have.add(cap.name)
    payload = {"round_no": round_no, "harness_id": harness_id,
               "n_wanted": n_wanted, "n_picked": len(picked),
               "n_restored": len(restored),
               "capsules": [c.name for c in restored],
               #: ⛔ 打上 stale：这批评分**来自旧基线**，账本读者要先
               #:    看到这个标记，才不会把"存档里当初很好"当成"现在很好"。
               "stale": [{"capsule": g["capsule"].get("name"),
                          "reward_at_store": g.get("reward_at_store"),
                          "baseline_reward": g.get("baseline_reward"),
                          "stored_at_round": g.get("stored_at_round")}
                         for g in picked]}
    return restored, payload, errors


# ──────────────────────────────────────────────────────────────────
# 一条存档记录
# ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Entry:
    """一个**曾经够格**的解，连同它被量时的全部上下文。"""
    capsule: Dict[str, Any]
    harness_id: str
    harness: Dict[str, Any]
    layers: Tuple[str, ...]
    reward: float
    load_gain: float
    size_delta: int
    round_no: int
    #: ⛔ 这条 reward 是相对**哪条基线**量的。缺了它，旧读数会被当成
    #:    新读数用（"当时好"冒充"现在好"）。
    #:    ⛔⛔ 只有数值**不够**（r62 实测）：发展集基线 reward 在"还没提交"
    #:    的那些轮里恒为 `0.0` ⇒ 靠数值判断"新旧"会把所有条目都判成同一个
    #:    基线。必须存**基线的 harness_id**（内容寻址，提交一次就变）。
    baseline_reward: float = 0.0
    baseline_harness_id: str = ""
    env_pair: str = ""
    accepted: bool = False

    @property
    def layer_key(self) -> str:
        return LAYER_SEP.join(self.layers) if self.layers else "none"

    @property
    def polar(self) -> Tuple[float, float]:
        """目标向量：reward ↑、体积增量 ↓。"""
        return (round(float(self.reward), 6), -float(self.size_delta))

    def to_dict(self) -> Dict[str, Any]:
        return {"capsule": self.capsule, "harness_id": self.harness_id,
                "harness": self.harness, "layers": list(self.layers),
                "reward": round(self.reward, 6),
                "load_gain": round(self.load_gain, 6),
                "size_delta": self.size_delta, "round_no": self.round_no,
                "baseline_reward": round(self.baseline_reward, 6),
                "baseline_harness_id": self.baseline_harness_id,
                "env_pair": self.env_pair, "accepted": self.accepted}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Entry":
        return Entry(capsule=d["capsule"], harness_id=str(d["harness_id"]),
                     harness=d.get("harness") or {},
                     layers=tuple(d.get("layers") or ()),
                     reward=float(d.get("reward") or 0.0),
                     load_gain=float(d.get("load_gain") or 0.0),
                     size_delta=int(d.get("size_delta") or 0),
                     round_no=int(d.get("round_no") or 0),
                     baseline_reward=float(d.get("baseline_reward") or 0.0),
                     baseline_harness_id=str(d.get("baseline_harness_id") or ""),
                     env_pair=str(d.get("env_pair") or ""),
                     accepted=bool(d.get("accepted")))


def dominates(a: Entry, b: Entry) -> bool:
    """a 是否支配 b（两项都不差、至少一项严格更好）。"""
    pa, pb = a.polar, b.polar
    return (pa[0] >= pb[0] and pa[1] >= pb[1]) and (pa[0] > pb[0] or pa[1] > pb[1])


# ──────────────────────────────────────────────────────────────────
# 存档本体
# ──────────────────────────────────────────────────────────────────
class Archive:
    """按 `blast_layers` 分层、层内做帕累托过滤的存档。

    ⛔ 落盘是 **append-only JSONL**，载入时**重放**所有行重建前沿。
       不落"当前前沿快照"：快照会与历史行不一致（两个事实源），
       而重放是幂等的 —— 同一份文件读两次得到同一个 front。
    """

    def __init__(self, path: str = "", cap: int = DEFAULT_CAP):
        self.path = path
        self.cap = max(1, int(cap))
        self._fronts: Dict[str, List[Entry]] = {}
        #: 只读统计（进账本用）：被拒/替换各多少条
        self.stats: Dict[str, int] = {"offered": 0, "added": 0,
                                      "dominated": 0, "duplicate": 0,
                                      "evicted": 0}
        self._seen: set = set()

    # ── 载入 / 落盘 ────────────────────────────────────────────
    def load(self) -> "Archive":
        if not self.path or not os.path.exists(self.path):
            return self
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._insert(Entry.from_dict(json.loads(line)),
                                 persist=False)
                except Exception:  # noqa: BLE001
                    #: ⛔ 坏行不许中断载入（存档是**旁路**，不是主账本），
                    #:    但也不许静默：计入 stats 让调用方看得见。
                    self.stats["corrupt"] = self.stats.get("corrupt", 0) + 1
        return self

    def _append_line(self, e: Entry) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")

    # ── 插入（非支配过滤的主逻辑）───────────────────────────────
    @staticmethod
    def _identity(e: Entry) -> Tuple[str, str]:
        """去重键 = (候选内容, 改动后的 harness)。"""
        return (json.dumps(e.capsule, sort_keys=True, ensure_ascii=False),
                e.harness_id)

    def _insert(self, e: Entry, persist: bool = True) -> Dict[str, Any]:
        key = self._identity(e)
        if key in self._seen:
            self.stats["duplicate"] += 1
            return {"added": False, "reason": "duplicate", "evicted": 0}
        front = self._fronts.setdefault(e.layer_key, [])
        for old in front:
            if dominates(old, e):
                self.stats["dominated"] += 1
                return {"added": False,
                        "reason": f"dominated_by({old.capsule.get('name')})",
                        "evicted": 0}
        keep = [o for o in front if not dominates(e, o)]
        evicted = len(front) - len(keep)
        keep.append(e)
        #: ⛔ 超上限时按**前沿内**的 reward 排序截断（只截自己的前沿，
        #:    不与别的层比 —— 跨层比就是拿分数冒充可比性）。
        if len(keep) > self.cap:
            keep.sort(key=lambda x: (x.reward, x.load_gain), reverse=True)
            evicted += len(keep) - self.cap
            keep = keep[:self.cap]
        self._fronts[e.layer_key] = keep
        self._seen.add(key)
        self.stats["added"] += 1
        self.stats["evicted"] += evicted
        if persist:
            self._append_line(e)
        return {"added": True, "reason": "", "evicted": evicted,
                "layer": e.layer_key}

    def add(self, e: Entry) -> Dict[str, Any]:
        self.stats["offered"] += 1
        return self._insert(e)

    # ── 读取 ───────────────────────────────────────────────────
    def layers(self) -> List[str]:
        return sorted(self._fronts)

    def front(self, layer_key: Optional[str] = None) -> List[Entry]:
        if layer_key is not None:
            return list(self._fronts.get(layer_key, ()))
        out: List[Entry] = []
        for k in sorted(self._fronts):
            out.extend(self._fronts[k])
        return out

    def resample(self, n: int, exclude_harness: Sequence[str] = (),
                 exclude_capsules: Sequence[str] = (),
                 exclude_baseline: Sequence[str] = ()) -> List[Dict[str, Any]]:
        """从各层前沿里取至多 n 条**待重测**的候选。

        ⛔ 返回的是**待重测**条目，不是"可提交"条目：`stale=True` 且带
           `baseline_reward` / `baseline_harness_id`，调用方必须在当前基线上
           重算 reward 才能用于任何判据（纪律③）。

        ⛔ 三重排除，各自管一件不同的事：
           `exclude_harness`  —— 改动后的 harness 已在当前谱系上（等价物）。
           `exclude_capsules` —— 候选名这一轮**已经被提过**：同胶囊 + 同基线
                                 ⇒ 结果必然一样，重测纯属浪费。
           `exclude_baseline` —— 这条读数**就是当前基线**上量的：不是"旧读数"，
                                 没有重测的必要（差别就在这一项上 —— 用
                                 `baseline_reward` 数值比会错，因为"还没提交"
                                 的那些轮里它恒为 0.0）。

        ⛔ 取法：**按层轮流**（round-robin），不是全局按 reward 排序。
           全局排序会让"改动面小、reward 略高"的那一层吃掉全部名额 ——
           那正是单线爬坡换个写法（层间可比性不存在）。
        """
        excl_h = set(exclude_harness)
        excl_c = set(exclude_capsules)
        excl_b = set(exclude_baseline)
        pools: List[List[Entry]] = []
        for k in sorted(self._fronts):
            cand = [e for e in self._fronts[k]
                    if e.harness_id not in excl_h
                    and str(e.capsule.get("name")) not in excl_c
                    and e.baseline_harness_id not in excl_b]
            if cand:
                #: 层内按 reward 降序（层内可比 ⇒ 这个排序是合法的）
                cand.sort(key=lambda x: (x.reward, x.load_gain), reverse=True)
                pools.append(cand)
        out: List[Dict[str, Any]] = []
        i = 0
        while len(out) < n and any(pools):
            progressed = False
            for p in pools:
                if i < len(p) and len(out) < n:
                    e = p[i]
                    out.append({"capsule": e.capsule, "layer": e.layer_key,
                                "stale": True,
                                "reward_at_store": e.reward,
                                "baseline_reward": e.baseline_reward,
                                "baseline_harness_id": e.baseline_harness_id,
                                "stored_at_round": e.round_no,
                                "harness_id": e.harness_id})
                    progressed = True
            if not progressed:
                break
            i += 1
        return out

    def to_report(self) -> Dict[str, Any]:
        return {"layers": {k: len(v) for k, v in sorted(self._fronts.items())},
                "total": len(self.front()), "cap": self.cap,
                "stats": dict(self.stats)}

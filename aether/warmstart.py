"""跨运行热启动 —— 让下一次 `aether evolve` 从上一次的**已提交结果**开始。

⛔⛔ 存在理由（2026-10-21 实测，纯诊断、未改任何判据）：

    账本 `data/ledger.jsonl` 里 `harness.commit` 共 **120** 条，其中
      · `parent_id` 只有 6 个不同值，**104 条（87%）**来自同一个父
        （`abcc4158…` 60 条 / `df9bf305…` 44 条）；
      · `round_no` **全部是 1**；
      · 去重 `harness_id` 只有 **15 个**，`capsule` 只有 **6 种**，
        其中 `add_skill:fix_control_flow` 占 94 条（78%）。

    ⇒ 每次调用 `aether evolve` 都从 `cli.py` 里那个裸种子 `Harness()`
      重爬一遍。用户跑一百次与跑一次等价 —— **「累积」是假的**。

    前沿系统的共同结构（DGM / AlphaEvolve / ShinkaEvolve）是 `archive`
    **跨运行持久**：accepted child 进存档，下次可当 parent。V6 已经有
    `harness_snapshots` 表与 `harness.commit` 事件，缺的只是
    「下一次从哪个节点开始」这一步。

⛔ 本模块**只回答「从哪开始」**，刻意不做三件事：

    ① **不改任何判据** —— 非回归闸 / 泛化闸 / 能力地板一条都没动。
       拿回来的 harness 照样要在本轮重新跑冻结基线、重新被闸审。
    ② **不写账本** —— 事件由调用方落（与 `archive.reseed` 同一条纪律：
       单测不该为了测装配而伪造账本事件）。
    ③ **不做串味容忍** —— 卷子口径不同就是不用（fail-closed）。宁可
       回到裸种子，也不许把「另一套 dev/heldout/seed」上的最好结果
       当成这一轮的起点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .genome import Harness

#: 决定「这是哪张卷子」的字段。⛔ 少一项就可能串味：
#:   · `dev_env` / `heldout_env` —— 选择面与考卷的来源环境；
#:   · `seed` —— 任务采样的随机源；
#:   · `envs` —— 世界规模（r47 之后它是实验变量，不是常量）。
CALIBER_KEYS: Tuple[str, ...] = ("dev_env", "heldout_env", "seed", "envs")

#: 需要读的事件种类。⛔ 只读这两种：账本 8.8 MB，全量物化没有理由。
_KINDS = ["evolution.run_config", "harness.commit"]


def _caliber_of_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从一条 `run_config` 里抽出「卷子口径」。

    ⛔ `envs` 缺失时返回 `()` 而不是跳过这个字段 —— 「没记录」与
       「空世界」必须是两个不同的值（前者走 legacy 宽松匹配，
       后者是一个真的空集合）。
    """
    raw_envs = payload.get("envs")
    envs: Tuple[str, ...] = ()
    if isinstance(raw_envs, (list, tuple)):
        envs = tuple(str(x) for x in raw_envs)
    return {
        "dev_env": str(payload.get("dev_env") or ""),
        "heldout_env": str(payload.get("heldout_env") or ""),
        "seed": str(payload.get("seed") or ""),
        "envs": envs,
    }


def _same_caliber(a: Dict[str, Any], b: Dict[str, Any],
                  *, allow_legacy_envs: bool = True) -> Tuple[bool, str]:
    """两套卷子口径是否可比。返回 `(same, 差异说明)`。

    ⛔⛔ `envs` 的**宽松分支是刻意的、也是有代价的**：早期 `run_config`
    没有 `envs` 字段（实测第 1 条就只有 dev/heldout/seed），如果要求
    `envs` 逐字相等，那这些历史运行**永远**匹配不上 ⇒ 新开关一上来就
    在最老的账本上用不了。所以「至少一方没记录」时忽略这一项，并把
    这件事写进 `note`（调用方负责落账）—— **允许，但必须可见**。
    """
    for k in ("dev_env", "heldout_env", "seed"):
        if a.get(k) != b.get(k):
            return False, f"{k} 不同（账本 {a.get(k)!r} ≠ 本次 {b.get(k)!r}）"
    ea, eb = a.get("envs") or (), b.get("envs") or ()
    if ea and eb and tuple(ea) != tuple(eb):
        return False, f"envs 不同（账本 {list(ea)} ≠ 本次 {list(eb)}）"
    if allow_legacy_envs and (not ea or not eb):
        return True, "envs 至少一方未记录 ⇒ 只比对 dev/heldout/seed（legacy）"
    return True, ""


@dataclass
class WarmStart:
    """热启动的结果。**成功与失败都必须可读**，不许用 `None` 两义。"""

    #: 只有 `True` 时 `harness` 才可用
    ok: bool = False
    harness: Optional[Harness] = None
    #: 从哪条 commit 恢复（账本里的 harness_id，原样）
    from_harness: str = ""
    #: 那条 commit 的账本 seq（可用来 `aether show` 直接定位）
    source_seq: int = -1
    #: 为什么没恢复 / 为什么恢复（**人可读**，会进账本）
    reason: str = ""
    #: 扫过的 commit 条数（不是匹配数）—— 「没有可继承的」与「一条都没扫到」
    #: 是两种完全不同的情况，不许混成一个空结果
    n_commits_seen: int = 0
    #: 口径匹配上的条数
    n_matched: int = 0
    #: 恢复出来的 harness 的 id（⛔ 与 `from_harness` 不等时说明往返有损）
    restored_id: str = ""
    #: 宽松匹配 / id 前缀差异这类「允许但要可见」的事
    notes: List[str] = field(default_factory=list)

    def to_payload(self, **extra: Any) -> Dict[str, Any]:
        """给账本用。⛔ 不放 harness 本体 —— 快照表才是事实源。"""
        d: Dict[str, Any] = {
            "ok": self.ok,
            "from_harness": self.from_harness,
            "source_seq": self.source_seq,
            "restored_id": self.restored_id,
            "reason": self.reason,
            "n_commits_seen": self.n_commits_seen,
            "n_matched": self.n_matched,
            "notes": list(self.notes),
        }
        d.update(extra)
        return d

    def summary(self) -> str:
        if self.ok:
            return (f"从 {self.from_harness[:16]} 热启动"
                    f"（seq={self.source_seq}，匹配 {self.n_matched}"
                    f"/{self.n_commits_seen} 条提交）")
        return (f"未能热启动：{self.reason}"
                f"（扫到 {self.n_commits_seen} 条提交）")


def _id_compatible(ledger_id: str, restored_id: str) -> bool:
    """快照 id 与账本 id 是否指向同一个 harness。

    ⛔ 账本里同时存在 16 位与 32 位两种长度（早期 16 位、后期 32 位，
       见 `harness_snapshots` 的 18 行）⇒ **短的那个是长的前缀**才算同一。
       不接受「任意包含」，那会让两个不同的 harness 互相认亲。
    """
    a, b = str(ledger_id or ""), str(restored_id or "")
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def load(
    ledger: Any,
    store: Any,
    *,
    dev_env: str,
    heldout_env: str,
    seed: Any,
    envs: Sequence[str] = (),
    allow_legacy_envs: bool = True,
) -> WarmStart:
    """找**最后一次**在同口径下提交成功的 harness，并从快照表恢复它。

    ⛔ 取「最后一次」而不是「分数最高的一次」：分数跨口径不可比
       （不同 dev 集、不同难度分层、不同题数），而**提交链**是唯一
       与口径绑定的顺序事实。要按分数挑，那是 `archive` 的职责。
    """
    target = {
        "dev_env": str(dev_env or ""),
        "heldout_env": str(heldout_env or ""),
        "seed": str(seed or ""),
        "envs": tuple(str(x) for x in (envs or ())),
    }
    cur: Optional[Dict[str, Any]] = None
    last_seq = -1
    last_hid = ""
    seen = 0
    matched = 0
    legacy_hit = False

    for ev in ledger.iter_replay(_KINDS):
        if ev.kind == "evolution.run_config":
            cur = _caliber_of_run(ev.payload or {})
            continue
        # `harness.commit`
        seen += 1
        if cur is None:
            # ⛔ run_config 之前就出现的提交：口径未知 ⇒ 不认（fail-closed）
            continue
        same, diff = _same_caliber(cur, target,
                                   allow_legacy_envs=allow_legacy_envs)
        if not same:
            continue
        matched += 1
        if diff:
            legacy_hit = True
        hid = str((ev.payload or {}).get("harness_id") or "")
        if hid:
            last_seq, last_hid = int(ev.seq), hid

    notes: List[str] = []
    if legacy_hit:
        notes.append("envs 至少一方未记录 ⇒ 按 legacy 口径只比对 "
                     "dev/heldout/seed")
    if seen == 0:
        return WarmStart(reason="账本里没有任何 `harness.commit` ⇒ "
                                "这是第一次运行，本该从种子开始",
                         n_commits_seen=0, n_matched=0, notes=notes)
    if not last_hid:
        return WarmStart(reason=f"{seen} 条提交里没有一条与本次卷子同口径"
                                f"（dev={target['dev_env']} / "
                                f"heldout={target['heldout_env']} / "
                                f"seed={target['seed']}）⇒ 不继承，避免串味",
                         n_commits_seen=seen, n_matched=matched, notes=notes)

    snaps = store.snapshots(harness_id=last_hid, limit=1)
    if not snaps:
        return WarmStart(from_harness=last_hid, source_seq=last_seq,
                         reason=f"账本记着 {last_hid[:16]}，但快照表里没有它"
                                f" ⇒ 无法恢复（快照是派生量，可能被清理过）",
                         n_commits_seen=seen, n_matched=matched, notes=notes)
    payload = snaps[-1].get("payload") or {}
    try:
        h = Harness.from_dict(payload)
    except Exception as e:                              # noqa: BLE001
        # ⛔ 反序列化失败**必须给出具体异常**：`from_dict` 的纪律是
        #    「类型不对时抛错，不静默修补」⇒ 这里抓到就说明快照真的坏了
        return WarmStart(from_harness=last_hid, source_seq=last_seq,
                         reason=f"快照 {last_hid[:16]} 反序列化失败："
                                f"{type(e).__name__}: {e}",
                         n_commits_seen=seen, n_matched=matched, notes=notes)

    rid = str(getattr(h, "harness_id", "") or "")
    if not _id_compatible(last_hid, rid):
        # ⛔ 能反序列化但 id 对不上 ⇒ 这份快照不是账本记的那一个。
        #    宁可不用 —— 用错起点比不用起点危险得多。
        return WarmStart(from_harness=last_hid, source_seq=last_seq,
                         restored_id=rid,
                         reason=f"快照 id 不一致（账本 {last_hid[:16]} vs "
                                f"恢复 {rid[:16]}）⇒ 拒绝使用，疑为往返有损",
                         n_commits_seen=seen, n_matched=matched, notes=notes)
    if rid != last_hid:
        notes.append(f"id 长度不同但前缀一致（账本 {len(last_hid)} 位 / "
                     f"恢复 {len(rid)} 位）⇒ 视为同一个 harness")

    return WarmStart(ok=True, harness=h, from_harness=last_hid,
                     source_seq=last_seq, restored_id=rid,
                     reason="", n_commits_seen=seen, n_matched=matched,
                     notes=notes)

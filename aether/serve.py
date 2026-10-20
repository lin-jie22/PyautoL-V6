"""极简 HTTP API：把账本与投影暴露给 TypeScript 监督器与控制台。

跨语言契约就一个：**账本 JSONL**。TS 侧既能直接读文件（同机部署），
也能走这个 API（分离部署）。两端用**同一套判据**（链校验、异常检测）。

⛔ 账本侧只读：账本的写只有一条路径 —— `ledger.append`，本服务不提供
   任何写账本的接口。

⛔ 但**接入层有写口子**（2026-10-14 更正，此前这里写的是"不提供任何写
   接口"，与 `POST /api/invoke` 早已存在的事实不符）：
   · `POST /api/invoke` —— 会真的执行能力，能力与清单声明了 `effects` 就
     有副作用；
   · `POST /api/capabilities/reload` —— 运行时替换本进程的能力集
     （**默认关闭**，须 `--caps-hot`）。
   两者都不碰账本，所以"账本只读"仍然成立；但把它们当成"只读服务"
   是错的 —— 分诊时会漏掉这两条攻击面。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import hmac
import threading

#: ⛔ 与 CLI 同一套日志：常驻进程的诊断**必须**带 ts/level/logger，否则
#:    连跑几天的日志分不清哪行是哪次请求、哪个级别（r35）。
from .logging_setup import get_logger, setup as _log_setup

_log = get_logger("aether.serve")      # ⛔ 显式名，理由见 cli.py 同位置
from urllib.parse import parse_qs, urlparse

from .entry import NO_CAPS, add_log_args, add_root_arg, add_serve_args
#: ⛔ `ROOT` 与数据布局来自 `paths`（唯一事实源）—— 与 cli / ui 同源。
#:    此前三个入口各写一行 `Path(__file__).resolve().parent.parent`。
from .paths import ROOT, paths, resolve_root

#: `/api/ledger/raw` 单次响应上限（字节）。账本 append-only ⇒ 无上限读取
#: 会让内存峰值随历史线性增长；超限直接 413，让调用方走分页/同机直读路径。
MAX_RAW_BYTES = 64 * 1024 * 1024

#: `/api/events` 默认返回条数。
DEFAULT_EVENT_TAIL = 500

#: 列表类响应的**硬上限**。
#: ⛔ 为什么必须有：`/api/status?limit=N` 与 `/api/events?limit=N` 的 `N`
#:    此前**完全没有校验**，而 `/api/ledger/raw` 的无上限读取已经修过了 ——
#:    同一个洞换个入口还开着两次。更需要命中的是**负数**：SQLite 里
#:    `LIMIT -1` 就是**无限制**（实测：`runs(limit=-1)` 返回全表 810 行），
#:    所以 `limit=-1` 不是"取 0 条"，是"全部吐出来"。
MAX_LIST_LIMIT = 5000

#: `/api/invoke` 请求体上限（字节）。**第四个人口**：前面三处（raw / events /
#: status 的 limit）都修过，这个是新开的口子，不能又是无上限。
MAX_INVOKE_BODY_BYTES = 1024 * 1024

#: `/api/capabilities/reload` 请求体上限（字节）。这个 body **只需要一个
#: 路径**，给 1MB 没有理由 —— 上限越窄，被塞进来的余地越小。
MAX_RELOAD_BODY_BYTES = 64 * 1024

#: ── 文件浏览与预览（控制台的 Workspace 面板）──────────────────────
#: ⛔ 这两个端点**只读**，且只能看仓库内的东西。它们是"控制台能看文件"
#:    这条需求的实现，不是文件服务器：没有写、没有上传、没有任意路径。

#: `/api/file` 单次读取上限（字节）。超了明确 413 并给出提示 ——
#: 静默截断会让"预览"变成"看半截文件"而不自知。
MAX_FILE_BYTES = 512 * 1024

#: 一次目录列举最多返回多少条目。
MAX_LIST_ENTRIES = 500

#: 列举时**整棵跳过**的目录名。⛔ 声明式：不扫这些目录，否则展开仓库根
#:    会把 `.git` 的上万个对象、`node_modules` 的依赖树全读一遍 ——
#:    一次点击变成几秒的卡顿，而用户只是想看 data/ 里有什么。
FILE_SKIP_DIRS = frozenset({
    ".git", ".venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})

#: 视作**文本**、可直接预览的扩展名。不在名单里的一律当二进制拒绝预览：
#: 把二进制当 UTF-8 解出来是一屏乱码，比明确说"不能预览"更糟。
TEXT_PREVIEW_EXTS = frozenset({
    ".txt", ".md", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh",
    ".cfg", ".csv", ".log", ".sql", ".svg", ".xml",
})

#: 视为"只监听本机"的地址集合。⛔ 只认**字面量**：不要试图解析域名或用
#: `socket` 判断 —— 那会把判断结果押在 DNS 与路由表上（可失真），而这里
#: 需要的是一个**保守的**判定。认不出的地址一律按"非回环"处理
#: ⇒ 没开 token 就拒绝启动（fail-closed，宁可拦错不可放行）。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: 进程启动时刻（`/api/health` 的 uptime 用）。模块级 ⇒ 每个进程一份。
_STARTED = time.time()


def _code_version() -> str:
    """代码版本的**唯一读取处**。

    ⛔ 函数内 import：本模块可能被 `aether` 包在初始化过程中导入，
    顶层 `from . import __version__` 有循环导入风险。
    """
    from . import __version__
    return str(__version__)


def _is_within(child: Path, parent: Path) -> bool:
    """按**路径分量**判断 child 是否在 parent 之下。

    ⛔ 不能用 `str(child).startswith(str(parent))`：前缀判断会把
    `/app/console_backup/x` 当成 `/app/console` 的子路径 ⇒ 路径穿越。
    `Path.relative_to` 按分量比较，不会犯这个错。
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _endpoint_is(path: str, prefix: str) -> bool:
    """端点匹配：要么完全相等，要么后面紧跟 `/`。

    ⛔ `path.startswith("/api/status")` 会让 `/api/status_extra` 误命中。
    """
    return path == prefix or path.startswith(prefix + "/")


class MemoryLedgerAgg:
    """记忆对账的**账本侧**流式聚合：逐条 `feed()`，`result(file_ids)` 出结论。

    ⛔ 与 `memory_report(..., ledger_events=...)` **同一份**逻辑（后者就是
    建 agg → feed → result）。记忆对账是判据（"每条记忆都能在账本里找到
    证据"），判据有两份实现就等于没有判据。

    ⛔ 只留 `note_id` 短串与计数，**不留 payload**。
    """

    def __init__(self) -> None:
        self.n_write_ev = 0
        self.n_backfill = 0
        self.w_ids: set = set()
        self.rejected_by: Dict[str, int] = {}

    def feed(self, ev: Any) -> None:
        if ev.kind != "memory.write":
            return
        self.n_write_ev += 1
        p = ev.payload or {}
        disp = str(p.get("disposition") or "")
        if disp in ("written", "backfill"):
            self.w_ids.add(str(p.get("note_id") or ""))
            if disp == "backfill":
                self.n_backfill += 1
        elif disp.startswith("rejected"):
            self.rejected_by[disp] = self.rejected_by.get(disp, 0) + 1

    def result(self, file_ids: set) -> Dict[str, Any]:
        untraceable = sorted(file_ids - self.w_ids)
        #: ⛔ "账本里一条记忆事件都没有" = 记忆账本化**之前**的存量，
        #:    那是历史遗留（不可溯源），不是"对不上"（有据却矛盾）。
        state = ("pre_ledger" if self.n_write_ev == 0 and file_ids
                 else "partial" if untraceable else "complete")
        return {
            "ledger_write_events": self.n_write_ev,
            "ledger_written_ids": len(self.w_ids),
            "backfilled": self.n_backfill,
            "ledger_rejected": self.rejected_by,
            "untraceable_count": len(untraceable),
            "untraceable_sample": untraceable[:5],
            "ledger_trace": state,
            "note": ("" if state == "complete" else
                     f"记忆早于账本化写入 ⇒ 这 {len(untraceable)} 条不可溯源"
                     f"（重跑一轮即带证据）"
                     if state == "pre_ledger" else
                     "有记忆事件却仍有条目对不上 ⇒ 记忆账本化路径存在漏记"),
        }


class TailWindowAgg:
    """只留**最后** `limit` 条事件的窗口（响应体不随账本变长）。

    ⛔ 用 `deque(maxlen=)` 而不是 `list(iter)[-limit:]` —— 后者会先物化
    一份全量副本，正是这次要消掉的那 8 MB。
    """

    def __init__(self, limit: int = 200):
        self.buf: deque = deque(maxlen=max(0, limit))

    def feed(self, ev: Any) -> None:
        self.buf.append(_event_dict(ev))

    def result(self) -> List[Dict[str, Any]]:
        return list(self.buf)


def _memory_file_report(path: Path) -> Tuple[Dict[str, Any], Optional[set]]:
    """记忆库**文件侧**的静态读数。返回 (报告, 文件里的 note_id 集合)。

    与账本侧分开是因为流式化后两者来源不同：文件一次性读完（它本来就是
    一个 JSON），账本逐条喂。合在一个函数里就没法只跑一半。

    `file_ids=None` 表示**没有可对账的文件**（不存在 / 解析失败）⇒ 调用
    方不得做对账。⛔ 不能用空集合代替：空集合会让"没有记忆库"被算成
    "0 条不可溯源 ⇒ 完全可溯源"，那是把缺失渲染成绿灯。

    ⛔⛔ 2026-10-13 实测：这里原先是硬编码的 `{"tiers": ["L0".."L4"]}` ——
    一个**恒亮绿灯**：库里 0 条也报五层、库被污染也报正常。写入侧刚建好
    污染闸，观测侧却是假表 ⇒ 污染在账本里可见、在控制台上不可见。

    ⛔ 只读服务**不得**重新嵌入（要走网络，且会改写库）。这里只读落盘的
    snapshot 做静态检查：维度是否与库声明一致 / 有没有空向量 / 有没有
    未验证标记 / 有没有继承条。判据复用 `memory.py` 的词表（唯一事实源），
    不在本文件抄一份 —— 抄一份就会漂移，漂移后"控制台说没事"是假没事。
    """
    from .memory import UNVERIFIED_MARKS, PLACEHOLDER_BODIES

    if not path.exists():
        #: ⛔ 明确报"没有记忆库"，不得渲染成"有五层"
        return ({"present": False, "total": 0,
                 "note": "no_snapshot（尚未落盘：本进程内还没跑过，或已被清理）"},
                None)
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        #: 半截 JSON 必须点名（原子写是为了避免它，但读不出来要如实报）
        return ({"present": True, "readable": False, "total": 0,
                 "error": f"{type(e).__name__}: {e}",
                 "note": "snapshot 解析失败（半截写入？）⇒ 记忆不可读"},
                None)

    notes = snap.get("notes") or []
    declared_dim = int(snap.get("dim") or 0)
    declared_emb = str(snap.get("embedder_id") or "")
    dims: Dict[int, int] = {}
    embs: Dict[str, int] = {}
    layers: Dict[str, int] = {}
    n_no_emb = 0
    n_unverified = 0
    n_inherited = 0
    for n in notes:
        v = n.get("embedding") or []
        dims[len(v)] = dims.get(len(v), 0) + 1
        if not v:
            n_no_emb += 1
        e = str(n.get("embedder") or "unknown")
        embs[e] = embs.get(e, 0) + 1
        ly = str(n.get("tier") or "?")
        layers[ly] = layers.get(ly, 0) + 1
        body = str(n.get("content") or "")
        if (body.strip().lower() in PLACEHOLDER_BODIES
                or any(m in body for m in UNVERIFIED_MARKS)):
            n_unverified += 1
        if "inherited" in (n.get("tags") or ()) or n.get("inherited"):
            n_inherited += 1
    #: ⛔ 未声明 embedder_id / dim 的老快照：不能反过来把**所有**条目
    #:    都算成"外来/异维"。两者都要以声明值为前提（对称条件）——
    #:    否则"控制台说库被污染"和"库真的被污染"就混在一起了。
    foreign = sum(c for e, c in embs.items()
                  if declared_emb and e != declared_emb)
    wrong_dim = sum(c for d, c in dims.items()
                    if declared_dim and d != declared_dim)
    out = {
        "present": True, "readable": True, "path": str(path),
        "total": len(notes),
        "layer_sizes": layers,
        "embedder_id": declared_emb, "dim": declared_dim,
        "dim_histogram": dims, "embedder_histogram": embs,
        #: ⛔ 库里出现非声明 embedder / 非声明 dim 的条目 = 混库事故
        "foreign_embedder": foreign, "wrong_dim": wrong_dim,
        #: ⛔ "写入成功 ≠ 可用"：空向量条目永远检索不到
        "no_embedding": n_no_emb, "unverified": n_unverified,
        "inherited": n_inherited,
        "integrity_ok": (foreign == 0 and wrong_dim == 0 and n_no_emb == 0
                         and n_unverified == 0),
    }
    return out, {str(n.get("note_id") or "") for n in notes}


def memory_report(path: Path, ledger_events: Optional[Iterable[Any]] = None,
                  ledger_agg: Optional["MemoryLedgerAgg"] = None
                  ) -> Dict[str, Any]:
    """记忆库的**静态**完整性读数（只读，不重建 embedder）。

    `ledger_events` 给进来时会额外做**记忆对账**：文件里的每条记忆应当
    能在账本里找到一条 `memory.write`（disposition=written）。这是"事实
    只有一份"的另一半 —— 记忆是跨运行继承、直接喂给提议器的东西，不进
    账本就等于因果链上有一环没有证据（2026-10-13 实测：账本里 0 条记忆
    事件，而 `data/memory.json` 里躺着 11 条）。

    `ledger_agg` 是**流式**入口：调用方已经在自己的那一次遍历里喂过
    聚合器了，传进来直接出结果，不再消费第二遍账本（2026-10-13：`cli
    status` 曾问账本三遍 —— 三条判据来自三个不同时刻的快照）。
    两个入口走**同一个** `MemoryLedgerAgg` ⇒ 判据只有一份。

    ⛔ 只读服务**不得**重新嵌入（要走网络，且会改写库）。判据复用
    `memory.py` 的词表（唯一事实源），不在本文件抄一份。
    """
    out, file_ids = _memory_file_report(path)
    if ledger_agg is not None:
        if file_ids is not None:
            out.update(ledger_agg.result(file_ids))
        return out
    if ledger_events is None:
        out["ledger_trace"] = "not_checked（未传入账本事件）"
        return out
    if file_ids is None:
        return out
    agg = MemoryLedgerAgg()
    for ev in ledger_events:
        agg.feed(ev)
    out.update(agg.result(file_ids))
    return out


def _parse_limit(q: Dict[str, List[str]], default: int
                 ) -> Tuple[bool, int, str]:
    """解析并校验 `limit`：**两个列表端点共用**，不许各写一份。

    返回 (ok, value, error_message)。

    ⛔ 为什么**拒绝**负数而不是 clamp：`LIMIT -1` 在 SQLite 里是"无限制"，
    clamp 成 0 会把"全部吐出"悄悄变成"一条不给"，而调用方以为自己拿到了
    想要的东西 —— 两头都是把缺失/溢出伪装成正常。明确 400 + 说清原因。
    ⛔ 为什么**拒绝**超限而不是静默截断：静默截断会让"第 3000 条之后还有
    没有"这个问题失去答案。要全量就走同机直读（那是另一条有出路的路径）。
    """
    raw = q.get("limit", [None])[0]
    if raw is None:
        return True, default, ""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return False, 0, f"limit 必须是整数（实得 {raw!r}）"
    if n < 0:
        return False, 0, (f"limit 不得为负（实得 {n}）："
                          "SQLite 的 LIMIT -1 == 无限制，那会把整张表吐出来")
    if n > MAX_LIST_LIMIT:
        return False, 0, (f"limit 过大（{n} > {MAX_LIST_LIMIT}）："
                          "需要全量请同机直读文件，不要打穿这个端点")
    return True, n, ""


# ── 文件浏览与预览的路径闸（控制台的 Workspace 面板）────────────────
def _safe_repo_path(root: Any, raw: Optional[str]
                    ) -> Tuple[Optional[Path], Optional[str]]:
    """把用户给的 path 解析成**仓库内**的绝对路径。

    ⛔ 三条硬约束，**少一条就变成任意文件读取**：

      1. 只接受相对路径（`/...`、`~`、含盘符的一律拒）—— 绝对路径让
         "限定在仓库内"这件事失去意义；
      2. `resolve()` **之后**再判是否在仓库根内 ⇒ 同时挡住 `..` 与
         **符号链接逃逸**（仓库里一个指向 /etc 的软链本身不是漏洞，
         跟随它才是。先拼后判会漏，先 resolve 后判不会）；
      3. 解析失败（权限、循环链接）同样拒，不"尽力而为"。

    返回 `(path, None)` 或 `(None, 原因)`。原因会原样回给调用方 ——
    被拒绝时必须说清是"路径不合法"还是"逃出了仓库"。
    """
    if raw is None:
        return None, "缺 path 参数"
    raw = (raw or "").strip()
    if raw in ("", ".", "./"):
        raw = "."
    if raw.startswith("/") or raw.startswith("~"):
        return None, f"path 必须是相对仓库根的相对路径，实得 {raw[:60]!r}"
    root_p = Path(root).resolve()
    try:
        target = (root_p / raw).resolve()
    except (OSError, ValueError) as ex:
        return None, f"路径无法解析：{type(ex).__name__}"
    if target != root_p and not target.is_relative_to(root_p):
        return None, "路径逃出仓库根（.. 或符号链接）"
    return target, None


def _rel_to_root(root: Any, target: Path) -> str:
    """绝对路径 → 相对仓库根的 POSIX 路径（前端显示与回传都用这个）。"""
    try:
        return target.resolve().relative_to(
            Path(root).resolve()).as_posix()
    except Exception:                                      # noqa: BLE001
        return target.name


def _event_dict(ev: Any) -> Dict[str, Any]:
    """账本事件 → JSON 友好的 dict。**所有端点共用**，避免两套结构。

    ⛔ 不要直接用 `ev.__dict__`：`@dataclass(slots=True)` 的实例没有
    `__dict__`，端点会当场 500。优先用事件自带的 `to_dict()`，
    退路走 `dataclasses.asdict`。
    """
    d = getattr(ev, "to_dict", None)
    if callable(d):
        return d()
    from dataclasses import asdict, is_dataclass
    if is_dataclass(ev) and not isinstance(ev, type):
        return asdict(ev)
    return dict(getattr(ev, "__dict__", {}))


def _collect(root: Path, limit: int = 200) -> Dict[str, Any]:
    from .ledger import Ledger
    from .store import Store

    #: ⛔ 布局来自 `paths`：手拼 `root / "data" / ...` 是 r36 收口的对象
    #:    （serve 里此前有 3 处，与 cli/doctor/backup 各写一份）。
    p = paths(root)
    led = Ledger(p["ledger"])
    db = p["db"]
    mpath = p["memory"]

    #: ⛔⛔ **一次磁盘遍历，多个消费者共享同一条流**。
    #:    上一轮消掉的是"解析 4.69 遍"（同一份响应里三个读数来自不同时刻
    #:    的快照 ⇒ 自相矛盾）；这一轮消掉的是"把整份账本物化成列表"。
    #:    实测 4000 条 / 5 MB 账本：峰值 **8.27 MB → 0.1 MB 量级**，
    #:    改动前 99% 的峰值花在"持有"上，而所有消费者**都只是遍历一遍**。
    #:    ⛔ 每个聚合器都必须与它的"非流式"入口共用同一份逻辑（见各自的
    #:    docstring）：`stats`↔`stats_agg`、`consistency_report`↔
    #:    `consistency_agg`、`memory_report`↔`MemoryLedgerAgg`。
    stat_agg = led.stats_agg()
    tail_agg = TailWindowAgg(limit)
    mem_agg = MemoryLedgerAgg()
    aggs: List[Any] = [stat_agg, tail_agg, mem_agg]

    store_stats: Dict[str, Any] = {}
    runs: List[Dict[str, Any]] = []
    snaps: List[Dict[str, Any]] = []
    #: ⛔ 无投影库时这两个也得有值（"0 条、没截断"，不是"没这个键"）——
    #:    缺键会让 TS 侧 `?? 0` 兜底，把"没有库"和"有库但 0 条"混成一种。
    snaps_total = 0
    snaps_truncated = False
    consistency: Dict[str, Any] = {}
    s = Store(db) if db.exists() else None
    try:
        if s is not None:
            #: ⛔ 投影侧这些读数是**账本无关**的（SQLite 自己的数据），
            #:    不必等账本遍历 —— 但一致性聚合器要在遍历前拿到投影键，
            #:    所以在这里建。
            store_stats = s.stats()
            runs = [r.to_dict() for r in s.runs(limit=limit)]
            #: ⛔ 谱系是 append-only 的 ⇒ **无界**。必须按 limit 取，并把
            #:    "截断过"如实报出去（控制台才知道自己看到的是不完整的谱系）。
            #:    ⛔ 判据不受影响：一致性走 `snapshot_ids()`（全量）。
            snaps = s.snapshots(limit=max(0, limit))
            snaps_total = int(store_stats.get("n_snapshots", 0))
            snaps_truncated = snaps_total > len(snaps)
            cons_agg = s.consistency_agg()
            aggs.append(cons_agg)
        for ev in led.iter_events():
            for a in aggs:
                a.feed(ev)
        if s is not None:
            #: ⛔ 账本 vs 投影：与 CLI `status` / `rebuild` **同一个**判据。
            #:    控制台不能只报"运行记录 51 条"而不说账本里有 495 条 ——
            #:    那等于把丢历史这件事藏起来（2026-10-13 实测）。
            consistency = cons_agg.result()
    finally:
        #: ⛔ try/finally：任一步抛异常都不能让 SQLite 连接靠 GC 兜底
        #:    （ThreadingHTTPServer 下每请求一线程，堆积很快）。
        if s is not None:
            s.close()

    mem, file_ids = _memory_file_report(mpath)
    #: ⛔ 文件不存在 / 解析失败 ⇒ **不做**对账（与 `memory_report` 的早退
    #:    分支逐字一致）。没有"文件里的条目"就没有可对账的东西；硬凑会
    #:    把"没有记忆库"渲染成"0 条不可溯源 ⇒ 完全可溯源"。
    if file_ids is not None:
        mem.update(mem_agg.result(file_ids))

    return {
        "ledger": stat_agg.result(),
        "events": tail_agg.result(),
        "store": store_stats,
        "consistency": consistency,
        "runs": runs,
        "snapshots": snaps,
        #: ⛔ 切了就要说切了：不报的话"谱系只有 7 个"会被当成全部
        "snapshots_total": snaps_total,
        "snapshots_truncated": snaps_truncated,
        "memory": mem,
        #: ⛔ 版本号**不许再写字面量**：此前 `aether/__init__.py` 与这里
        #:    各写一份（实测已漂移：包是 0.1.0、另一处是 0.2.0）⇒ 同一个
        #:    进程的两个端点能报出两个版本。唯一事实源 = `__version__`。
        "version": _code_version(),
    }


class Handler(BaseHTTPRequestHandler):
    #: ⛔ r61：**占位值**，运行时由 `main()` 用 `Path(args.root)` 覆盖（第 1008 行）。
    #:    占位也必须走 `resolve_root()` —— 写成 `ROOT`（包位置）意味着
    #:    "没走 main 的那条路"会把账本指到 `site-packages`，而它照样能读能写
    #:    （不是崩溃，是**悄悄写到安装目录**，比崩溃更坏）。
    root = resolve_root()
    console_dir: Optional[Path] = None
    #: CORS 白名单（元组）。空 = **不发** CORS 头。
    #: ⛔ 默认不发。`Access-Control-Allow-Origin: *` 会让**任意网页**的 JS
    #:    读到本机账本（含 harness 状态 / 记忆正文 / 失败轨迹）——
    #:    只读 ≠ 无隐私。跨源部署时显式列白名单，或传 "*"（知情选择）。
    cors_origins: tuple = ()
    #: 接入层网关。**None ⇒ 调用端点返回 503 而不是 404**：能力没装载是
    #: 配置问题，不是"端点不存在"，分诊方向必须正确。
    gateway: Any = None
    #: ⛔ 热装载**默认关闭**。理由：热装载 = 运行时改变本进程的能力集，
    #:    比 invoke 更靠近信任边界（清单里的 `python` 形态是任意代码进本
    #:    进程）⇒ 必须 `aether-serve --caps-hot` 显式开启，不是默认能力。
    caps_hot: bool = False
    #: 当前生效清单的路径（热装载读数）。空 = 源码内默认注册表。
    caps_src: str = ""
    #: 热装载次数 / 最后一次装载时刻 —— **可见读数**，否则"服务里跑的到底
    #: 是哪份清单"只能靠猜（与"声明 vs 实际"落差同源）。
    caps_reload_count: int = 0
    caps_last_reload: str = ""
    #: ⛔ ThreadingHTTPServer ⇒ 多线程并发；热装载不成对（计数 +1 与替换
    #:    gateway 之间被另一个 reload 插队）会让读数与实况对不上。
    caps_lock: Any = threading.Lock()
    #: ⛔⛔ API 鉴权令牌。空串 = **不鉴权**（localhost 默认场景，向后兼容）。
    #:    非空 ⇒ 所有请求都必须在 `Authorization: Bearer <token>` 或
    #:    `X-API-Key: <token>` 里带上它。
    #:    ⛔ "不鉴权"只在**监听回环地址**时才可接受 —— 这条约束由 `main()`
    #:    在启动期强制（非 localhost 且无 token ⇒ 拒绝启动），不靠调用方自觉。
    api_token: str = ""

    # ── 鉴权 ──────────────────────────────────────────────────
    def _authorized(self) -> bool:
        """校验本次请求。不通过时**本方法自己发 401**并返回 False。

        ⛔ 为什么要有它：本服务暴露的是账本原文、记忆正文、失败轨迹，
        以及 `POST /api/invoke`（能触发外部能力执行）。此前它零鉴权，
        唯一的保护是"默认绑 127.0.0.1"这条**隐式约定** —— 而 `--host`
        一个开关就能把它拆掉，且拆掉时没有任何提示。

        ⛔ 比较必须用 `hmac.compare_digest`：用 `==` 比字符串会按字节
        提前退出 ⇒ 逐字节试探可还原令牌（时序侧信道）。这是"看起来能
        工作的鉴权"与"真鉴权"的分界。
        """
        if not Handler.api_token:
            return True
        hdr = (self.headers.get("Authorization") or "").strip()
        token = ""
        if hdr.lower().startswith("bearer "):
            token = hdr[7:].strip()
        if not token:
            token = (self.headers.get("X-API-Key") or "").strip()
        if token and hmac.compare_digest(token, Handler.api_token):
            return True
        self._json({"error": "unauthorized",
                    "hint": "请求头带 Authorization: Bearer <token> "
                            "或 X-API-Key: <token>",
                    "note": "本服务暴露账本原文与能力调用 ⇒ 非本机部署"
                            "必须开鉴权（--api-token 或 V6_API_TOKEN）"}, 401)
        return False

    def _cors_headers(self) -> Dict[str, str]:
        if not self.cors_origins:
            return {}
        origin = self.headers.get("Origin")
        if not origin:
            return {}
        if "*" in self.cors_origins or origin in self.cors_origins:
            #: 回显 origin（而不是回 "*"）：与 credentials 兼容，且不扩大暴露面
            return {"Access-Control-Allow-Origin": origin,
                    "Vary": "Origin"}
        return {}

    def _send(self, code: int, body: bytes,
              ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _serve_console_file(self, rel: str) -> None:
        assert self.console_dir is not None
        base = self.console_dir.resolve()
        f = (base / rel).resolve()
        #: ⛔ 按**路径分量**判断，不能用字符串前缀（见 _is_within）。
        if not _is_within(f, base) or not f.is_file():
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if _endpoint_is(u.path, "/api/status"):
                ok, limit, err = _parse_limit(q, 200)
                if not ok:
                    self._json({"error": err}, 400)
                    return
                #: ⛔ 鉴权状态必须**可读**：没这个读数，"服务有没有上锁"
                #:    只能靠翻启动命令 —— 而启动时是从哪台机器起的、
                #:    有没有带 --api-token，事后无从查证（假仪表同族）。
                st = _collect(self.root, limit=limit)
                st["auth"] = {"required": bool(Handler.api_token),
                              "scheme": "bearer|api-key"
                              if Handler.api_token else "none"}
                self._json(st)
            elif _endpoint_is(u.path, "/api/health"):
                #: ⛔ 探活端点必须**真的轻**：`/api/status` 要全量扫账本
                #:    （4 个聚合器），拿它当 liveness 会在账本变大时把探活
                #:    拖成超时 ⇒ 编排层（k8s/docker healthcheck）误判服务死
                #:    了而重启它，而它其实是健康的。这里只做 O(1) 的 stat。
                ledger_p = paths(self.root)["ledger"]
                self._json({
                    "ok": True,
                    "uptime_s": round(time.time() - _STARTED, 3),
                    "pid": os.getpid(),
                    "auth_required": bool(Handler.api_token),
                    "caps_loaded": Handler.gateway is not None,
                    "checks": {
                        "ledger_present": ledger_p.exists(),
                        "ledger_bytes": (ledger_p.stat().st_size
                                         if ledger_p.exists() else 0),
                    },
                    "note": "只做 O(1) 检查 ⇒ 可当探活；"
                            "链完整性请用 verify-chain / status",
                })
            elif _endpoint_is(u.path, "/api/events"):
                from .ledger import Ledger
                ok, limit, err = _parse_limit(q, DEFAULT_EVENT_TAIL)
                if not ok:
                    self._json({"error": err}, 400)
                    return
                kind = q.get("kind", [None])[0]
                led = Ledger(paths(self.root)["ledger"])
                #: ⛔ 流式：只留最后 limit 条，**不物化整份账本**。
                #:    `led.replay()` 曾为了取尾部 200 条而把 1533 条全量解析
                #:    两遍（列表推导再造一份）⇒ 峰值 6.50 MB（实测）。
                tail = TailWindowAgg(limit)
                for e in led.iter_replay(kinds=[kind] if kind else None):
                    tail.feed(e)
                #: ⛔ 与 /api/status.events **同一结构**（都走 _event_dict），
                #:    否则 TS 侧两个端点各拿一套字段，契约两头不对账。
                self._json({"events": tail.result()})
            elif _endpoint_is(u.path, "/api/ledger/raw"):
                path = paths(self.root)["ledger"]
                if not path.exists():
                    self._json({"error": "ledger not found"}, 404)
                    return
                #: ⛔ 无上限读取 ⇒ 内存峰值随账本线性增长。超限明确 413；
                #:    调用方应走 /api/events?limit=N 或同机直读文件。
                size = path.stat().st_size
                if size > MAX_RAW_BYTES:
                    self._json(
                        {"error": "ledger too large for raw dump",
                         "size": size, "limit": MAX_RAW_BYTES,
                         "hint": "use /api/events?limit=N "
                                 "or read the file directly"},
                        413)
                    return
                self._send(200, path.read_bytes(),
                           "application/x-ndjson")
            elif _endpoint_is(u.path, "/api/files"):
                #: 列目录（**只读**）。控制台的 Workspace 树用它。
                target, why = _safe_repo_path(
                    self.root, q.get("path", [""])[0])
                if target is None:
                    self._json({"error": why}, 400)
                    return
                if not target.is_dir():
                    self._json({"error": "不是目录"}, 400)
                    return
                entries: List[Dict[str, Any]] = []
                truncated = False
                try:
                    for child in sorted(
                            target.iterdir(),
                            key=lambda p: (not p.is_dir(), p.name.lower())):
                        if child.name in FILE_SKIP_DIRS:
                            continue
                        if len(entries) >= MAX_LIST_ENTRIES:
                            truncated = True
                            break
                        try:
                            st = child.stat()
                        except OSError:                    # noqa: BLE001
                            continue
                        entries.append({
                            "name": child.name,
                            "kind": "dir" if child.is_dir() else "file",
                            "size": st.st_size if child.is_file() else 0,
                            "mtime": round(st.st_mtime, 3),
                            "ext": child.suffix.lower(),
                        })
                except OSError as ex:                      # noqa: BLE001
                    self._json({"error": f"读取目录失败：{type(ex).__name__}"},
                               500)
                    return
                #: ⛔ `n` 跟随实际条目数（不是"目录里有多少"）：
                #:    超上限时另给 `truncated`，否则前端会把"还有更多"
                #:    读成"就这些"。
                #: ⛔ `skipped_dirs` **由服务端给出**，前端不许自己硬编码一份
                #:   名单：两处各写一份 ⇒ 服务端改了而页面还在说旧名字
                #:   ⇒ "看不见的目录有哪些"这件事变成**假的读数**。
                self._json({"root": str(Path(self.root).resolve()),
                            "path": _rel_to_root(self.root, target),
                            "n": len(entries), "truncated": truncated,
                            "skipped_dirs": sorted(FILE_SKIP_DIRS),
                            "entries": entries})
            elif _endpoint_is(u.path, "/api/file"):
                #: 读单个文本文件（**只读**）。控制台的预览面板用它。
                target, why = _safe_repo_path(
                    self.root, q.get("path", [""])[0])
                if target is None:
                    self._json({"error": why}, 400)
                    return
                if not target.is_file():
                    self._json({"error": "不是文件（或不存在）"}, 404)
                    return
                ext = target.suffix.lower()
                if ext not in TEXT_PREVIEW_EXTS:
                    #: ⛔ 415 而不是"尽力解一下"：把二进制当 UTF-8 吐出来
                    #:    是一屏乱码，比明确说"不能预览"更糟。
                    self._json({"error": "该文件类型不支持预览",
                                "path": _rel_to_root(self.root, target),
                                "ext": ext,
                                "previewable": sorted(TEXT_PREVIEW_EXTS)},
                               415)
                    return
                size = target.stat().st_size
                if size > MAX_FILE_BYTES:
                    self._json({"error": "文件超过预览上限",
                                "size": size, "limit": MAX_FILE_BYTES,
                                "hint": "用本地编辑器打开，或同机直读"},
                               413)
                    return
                blob = target.read_bytes()
                try:
                    text = blob.decode("utf-8")
                except UnicodeDecodeError:
                    self._json({"error": "不是合法 UTF-8 文本（拒绝吐乱码）",
                                "path": _rel_to_root(self.root, target)},
                               415)
                    return
                self._json({"path": _rel_to_root(self.root, target),
                            "bytes": size,
                            "sha256": hashlib.sha256(blob).hexdigest(),
                            "content": text})
            elif _endpoint_is(u.path, "/api/capabilities"):
                if Handler.gateway is None:
                    self._json({"error": "capability gateway 未装载",
                                "hint": "启动时用 --caps <manifest.json>"}, 503)
                    return
                q_kind = q.get("kind", [""])[0]
                caps = [s.to_dict() for s in
                        Handler.gateway.registry.list(kind=q_kind)]
                #: ⛔ `n` 必须**跟随过滤**：此前 `n` 恒为注册表总数，
                #:    实测 `?kind=skill` 返回 `n=4` 而列表只有 2 条 ⇒
                #:    调用方会读成"过滤没生效"。总数另给 `n_total`。
                self._json({
                    "fingerprint": Handler.gateway.registry.fingerprint,
                    "n": len(caps),
                    "n_total": len(Handler.gateway.registry),
                    "capabilities": caps,
                    #: ⛔ 「跑的到底是哪份清单」必须是**读数**而不是靠猜：
                    #:    热装载存在时，磁盘上的清单 ≠ 进程里的清单。
                    "source": Handler.caps_src,
                    "hot_reload": bool(Handler.caps_hot),
                    "reload_count": Handler.caps_reload_count,
                    "last_reload_at": Handler.caps_last_reload,
                })
            elif self.console_dir and (u.path == "/"
                                       or _endpoint_is(u.path, "/console")):
                rel = ("index.html"
                       if u.path in ("/", "/console", "/console/")
                       else u.path[len("/console/"):])
                self._serve_console_file(rel)
            else:
                self._json({"error": "unknown endpoint", "path": u.path}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _read_body(self, limit: int) -> Tuple[Optional[Dict[str, Any]], bool]:
        """读 JSON body。**先按声明长度拒，不要先读再判断**。

        返回 `(body, ok)`：`ok=False` 表示已自行回过错误响应，调用方直接
        return。⛔ 两个 POST 端点共用这一份 —— 长度闸与解析闸各写一遍
        ⇒ 迟早有一个端点漏掉上限。
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json({"error": "bad Content-Length"}, 400)
            return None, False
        if n > limit:
            self._json({"error": "request body too large", "bytes": n,
                        "limit": limit}, 413)
            return None, False
        raw = self.rfile.read(n) if n > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"invalid JSON body: {type(e).__name__}"}, 400)
            return None, False
        if not isinstance(body, dict):
            self._json({"error": "body 必须是 JSON 对象"}, 400)
            return None, False
        return body, True

    # ── POST：热装载能力清单 / 调用已注册的能力 ────────────────
    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        u = urlparse(self.path)
        if _endpoint_is(u.path, "/api/capabilities/reload"):
            self._do_reload()
            return
        if not _endpoint_is(u.path, "/api/invoke"):
            self._json({"error": "unknown endpoint", "path": u.path}, 404)
            return
        if Handler.gateway is None:
            self._json({"error": "capability gateway 未装载",
                        "hint": "启动时用 --caps <manifest.json>，或 "
                                "POST /api/capabilities/reload（须 --caps-hot）"},
                       503)
            return
        body, ok = self._read_body(MAX_INVOKE_BODY_BYTES)
        if not ok:
            return
        cid = str(body.get("capability", "")).strip()
        if not cid:
            self._json({"error": "缺 capability"}, 400)
            return
        #: ⛔⛔ **只允许调用已注册的能力**：body 里出现 impl / argv / code /
        #:    transport 一律拒绝。否则这个端点就是"远程执行任意命令"，
        #:    能力清单就成了摆设 —— 清单的意义正是**事先**决定谁能被调。
        banned = {"impl", "argv", "code", "source", "transport", "target",
                  "adapter", "module"}
        hit = banned & set(body)
        if hit:
            self._json({"error": "请求体不得携带实现描述（只允许引用已注册能力）",
                        "rejected_keys": sorted(hit)}, 400)
            return
        args = body.get("args") or {}
        if not isinstance(args, dict):
            self._json({"error": "args 必须是对象"}, 400)
            return
        try:
            res = Handler.gateway.invoke(
                cid, args, actor=str(body.get("actor", "") or "http"))
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        code = 200 if res.status in ("ok",) else 422
        self._json(res.to_dict(), code)

    # ── 热装载：运行时替换能力集 ──────────────────────────────
    def _do_reload(self) -> None:
        """`POST /api/capabilities/reload` —— 不重启就换掉能力清单。

        ⛔⛔ 三道闸，缺一不可：
        1. **默认关闭**（`--caps-hot` 才开）。热装载 = 运行时改变本进程的
           能力集，比 invoke 更靠近信任边界；默认开着等于把"谁能被调"的
           决定权从启动时的清单，移交给了任何一个能访问端口的人。
        2. **body 只允许给路径**，不接受内联能力描述。否则这个端点就是
           "远程注入任意实现进本进程" —— 与 `/api/invoke` 拒绝 body 携带
           `impl/argv/code` **同一条纪律**（事先决定谁能被调）。
        3. **装载 0 条 ⇒ 不替换**。把一份好清单换成空的是"能力凭空消失"，
           比装载失败更糟：所有调用从 ok 变 unavailable，而没人动过代码。

        ⛔ 返回码必须能反映"部分成功"：只回 200 会让调用方（只看 HTTP 码
        的人）漏掉 skipped ⇒ 与 r29「`status=ok` 掩盖落差」同源 ⇒
        有 skip 时回 **207**，装载 0 条回 **409**。
        """
        if not Handler.caps_hot:
            self._json({"error": "热装载未启用",
                        "hint": "启动时加 --caps-hot（默认关闭：热装载 = "
                                "运行时改变本进程能力集）",
                        "hot_reload": False}, 403)
            return
        body, ok = self._read_body(MAX_RELOAD_BODY_BYTES)
        if not ok:
            return
        banned = {"capabilities", "impl", "argv", "code", "source",
                  "transport", "target", "adapter", "module", "spec"}
        hit = banned & set(body)
        if hit:
            self._json({"error": "热装载只接受清单**路径**，不接受内联能力描述"
                                "（否则等于远程注入实现）",
                        "rejected_keys": sorted(hit)}, 400)
            return
        mpath = str(body.get("manifest", "")).strip() or Handler.caps_src
        if not mpath:
            self._json({"error": "缺 manifest（且启动时没有 --caps 可重载）"},
                       400)
            return
        p = Path(mpath)
        if not p.is_file():
            self._json({"error": "manifest 不是文件", "path": str(p)}, 404)
            return

        from .interop import mount_capabilities
        with Handler.caps_lock:
            #: ⛔ 热装载必须**继承**当前账本：不传 ⇒ 重载后调用能力静默不记账，
            #:    而这是最难察觉的回归（能力在、账没了）。
            _prev_led = (getattr(Handler.gateway, "ledger", None)
                         if Handler.gateway is not None else None)
            gw, rep = mount_capabilities(str(p), ledger=_prev_led)
            if not rep.loaded:
                #: ⛔ 不替换：换空 = 能力凭空消失，比装载失败更难查
                self._json({"error": "清单没装载上任何能力 ⇒ 保持原能力集不变",
                            "path": str(p), "report": rep.to_dict(),
                            "kept": (Handler.gateway.registry.fingerprint
                                     if Handler.gateway is not None else "")},
                           409)
                return
            old = Handler.gateway
            before_fp = (old.registry.fingerprint if old is not None else "")
            before_n = len(old.registry) if old is not None else 0
            Handler.gateway = gw          #: 属性赋值是原子的：in-flight 调用
            Handler.caps_src = str(p)     #: 仍持有旧对象，不会被抽掉
            Handler.caps_reload_count += 1
            Handler.caps_last_reload = time.strftime("%Y-%m-%dT%H:%M:%S")
            after_fp = gw.registry.fingerprint
            after_n = len(gw.registry)
        self._json({"status": "ok" if rep.ok else "partial",
                    "path": str(p),
                    "fingerprint_before": before_fp,
                    "fingerprint_after": after_fp,
                    "n_before": before_n, "n_after": after_n,
                    "changed": before_fp != after_fp,
                    "reload_count": Handler.caps_reload_count,
                    "last_reload_at": Handler.caps_last_reload,
                    "report": rep.to_dict()},
                   200 if rep.ok else 207)

    def log_message(self, fmt: str, *a: Any) -> None:
        sys.stderr.write("[aether] " + (fmt % a) + "\n")


def build_parser() -> argparse.ArgumentParser:
    """serve 的 parser。**单独抽出**有两个理由：

      1. 与 cli / ui 的公共参数可以逐项比对（`tests/contract_entrypoints`
         的 B 组直接遍历 `parser._actions`）；
      2. ⛔⛔ `entry.build_forward()` **以它为准**导出 ui → serve 的透传
         清单 ⇒ ui 不可能"漏传一个开关"（漏了会抛错，见 entry 的说明）。
    """
    ap = argparse.ArgumentParser(prog="aether-serve")
    add_root_arg(ap)
    #: ⛔ serve 默认**不接**外部清单（`NO_CAPS`）：服务是常驻进程，启动时
    #:    挂什么清单必须是显式决定，不能靠"恰好有个示例文件在仓库里"。
    add_serve_args(ap, caps_default=NO_CAPS, with_console=True)
    add_log_args(ap)
    return ap


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    #: ⛔ 先配日志再鉴权 —— 否则「拒绝启动」这条最该被看到的诊断，
    #:    恰恰走的是还没配好的通道。
    try:
        _log_setup(level=args.log_level, json_output=args.log_json)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    #: ── 鉴权（fail-closed）─────────────────────────────────────
    token = (args.api_token or os.environ.get("V6_API_TOKEN") or "").strip()
    if not token and args.host not in _LOOPBACK_HOSTS:
        _log.error("拒绝启动：--host %s 不是回环地址，但没有设置 API 令牌"
                   " ⇒ 账本原文 / 记忆正文 / 能力调用端点将对网络裸奔。"
                   " 出路：加 --api-token <token>（或环境变量 V6_API_TOKEN），"
                   "或改回 --host 127.0.0.1。", args.host)
        return 2
    Handler.api_token = token

    #: ⛔ **与 CLI 同一个装载接线**（`interop.mount_capabilities`）：两边各写
    #:    一遍 `Registry → load_manifest → Gateway` 就会出现"CLI 看得到被拒
    #:    的能力、HTTP 一声不响"的幽灵差异。判据有两份实现 = 没有判据。
    if args.caps:
        from .interop import mount_capabilities
        from .ledger import Ledger
        #: ⛔ 挂账本：调能力必须写 `cap.invoke`（此前不传 ⇒ 账本 0 次）。
        #:    `Ledger.append` 走 `fcntl.flock(LOCK_EX)` + fsync ⇒ 与 evolve
        #:    进程并发写是安全的（不是"HTTP 服务只读"那句能拦的范围：
        #:    那句说的是不提供**写账本端点**，而调用能力是真实事件）。
        _led = Ledger(paths(Path(args.root))["ledger"])
        gw, rep = mount_capabilities(args.caps, ledger=_led)
        Handler.gateway = gw
        Handler.caps_src = str(args.caps)
        print(f"[caps] 装载 {len(rep.loaded)} 个能力：{rep.loaded}")
        #: ⛔ 每条 skip 都打印：能力没挂上却一声不响 = 调用时才发现的 404
        for s in rep.skipped:
            print(f"[caps] ⚠️ 跳过 {s.get('id')}：{s.get('reason')}")

    Handler.caps_hot = bool(args.caps_hot)
    Handler.root = Path(args.root)
    Handler.console_dir = Path(args.console) if args.console else None
    Handler.cors_origins = tuple(
        o.strip() for o in args.cors.split(",") if o.strip())

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    cors_note = (f", cors={','.join(Handler.cors_origins)}"
                 if Handler.cors_origins else "")
    print(f"V6 API on http://{args.host}:{args.port}/api/status "
          f"(root={Handler.root}{cors_note})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

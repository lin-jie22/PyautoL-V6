"""极简 HTTP API：把账本与投影暴露给 TypeScript 监督器与控制台。

跨语言契约就一个：**账本 JSONL**。TS 侧既能直接读文件（同机部署），
也能走这个 API（分离部署）。两端用**同一套判据**（链校验、异常检测）。

⛔ 只读 API：这个服务不提供任何写接口。写只有一条路径 —— ledger.append。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent

#: `/api/ledger/raw` 单次响应上限（字节）。账本 append-only ⇒ 无上限读取
#: 会让内存峰值随历史线性增长；超限直接 413，让调用方走分页/同机直读路径。
MAX_RAW_BYTES = 64 * 1024 * 1024

#: `/api/events` 默认返回条数。
DEFAULT_EVENT_TAIL = 500


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


def memory_report(path: Path, ledger_events: Optional[Iterable[Any]] = None
                  ) -> Dict[str, Any]:
    """记忆库的**静态**完整性读数（只读，不重建 embedder）。

    `ledger_events` 给进来时会额外做**记忆对账**：文件里的每条记忆应当
    能在账本里找到一条 `memory.write`（disposition=written）。这是"事实
    只有一份"的另一半 —— 记忆是跨运行继承、直接喂给提议器的东西，不进
    账本就等于因果链上有一环没有证据（2026-10-13 实测：账本里 0 条记忆
    事件，而 `data/memory.json` 里躺着 11 条）。

    ⛔ 只读服务**不得**重新嵌入（要走网络，且会改写库）。判据复用
    `memory.py` 的词表（唯一事实源），不在本文件抄一份。
    """
    out, file_ids = _memory_file_report(path)
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

    led = Ledger(root / "data" / "ledger.jsonl")
    db = root / "data" / "aether.db"
    mpath = root / "data" / "memory.json"

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
    consistency: Dict[str, Any] = {}
    s = Store(db) if db.exists() else None
    try:
        if s is not None:
            #: ⛔ 投影侧这些读数是**账本无关**的（SQLite 自己的数据），
            #:    不必等账本遍历 —— 但一致性聚合器要在遍历前拿到投影键，
            #:    所以在这里建。
            store_stats = s.stats()
            runs = [r.to_dict() for r in s.runs(limit=limit)]
            snaps = s.snapshots()
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
        "memory": mem,
        "version": "0.1.0",
    }


class Handler(BaseHTTPRequestHandler):
    root = ROOT
    console_dir: Optional[Path] = None
    #: CORS 白名单（元组）。空 = **不发** CORS 头。
    #: ⛔ 默认不发。`Access-Control-Allow-Origin: *` 会让**任意网页**的 JS
    #:    读到本机账本（含 harness 状态 / 记忆正文 / 失败轨迹）——
    #:    只读 ≠ 无隐私。跨源部署时显式列白名单，或传 "*"（知情选择）。
    cors_origins: tuple = ()

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
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if _endpoint_is(u.path, "/api/status"):
                try:
                    limit = int(q.get("limit", ["200"])[0])
                except ValueError:
                    self._json({"error": "limit 必须是整数"}, 400)
                    return
                self._json(_collect(self.root, limit=limit))
            elif _endpoint_is(u.path, "/api/events"):
                from .ledger import Ledger
                try:
                    limit = int(
                        q.get("limit", [str(DEFAULT_EVENT_TAIL)])[0])
                except ValueError:
                    self._json({"error": "limit 必须是整数"}, 400)
                    return
                kind = q.get("kind", [None])[0]
                led = Ledger(self.root / "data" / "ledger.jsonl")
                evs = led.replay(kinds=[kind] if kind else None)
                tail = deque(evs, maxlen=max(0, limit))
                #: ⛔ 与 /api/status.events **同一结构**（都走 _event_dict），
                #:    否则 TS 侧两个端点各拿一套字段，契约两头不对账。
                self._json({"events": [_event_dict(e) for e in tail]})
            elif _endpoint_is(u.path, "/api/ledger/raw"):
                path = self.root / "data" / "ledger.jsonl"
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

    def log_message(self, fmt: str, *a: Any) -> None:
        sys.stderr.write("[aether] " + (fmt % a) + "\n")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="aether-serve")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8611)
    ap.add_argument("--console", default="")
    #: ⛔ 默认**不发** CORS 头：本机账本含 harness/记忆正文，任意网页
    #:    不该能读。跨源部署时显式列出白名单，或传 "*"（知情选择）。
    ap.add_argument("--cors", default="",
                    help="CORS 白名单（逗号分隔 origin；'*' 表示任意）")
    args = ap.parse_args(argv)

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

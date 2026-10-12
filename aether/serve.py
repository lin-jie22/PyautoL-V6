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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent


def _collect(root: Path, limit: int = 200) -> Dict[str, Any]:
    from .ledger import Ledger
    from .store import Store
    from .memory import MemoryOS

    led = Ledger(root / "data" / "ledger.jsonl")
    st = led.stats()
    events = [json.loads(l) for l in list(led.iter_lines())[-limit:]]
    db = root / "data" / "aether.db"
    store_stats: Dict[str, Any] = {}
    runs: list = []
    snaps: list = []
    if db.exists():
        s = Store(db)
        store_stats = s.stats()
        runs = [r.to_dict() for r in s.runs(limit=limit)]
        snaps = s.snapshots()
        s.close()
    return {
        "ledger": st,
        "events": events,
        "store": store_stats,
        "runs": runs,
        "snapshots": snaps,
        "memory": {"tiers": ["L0", "L1", "L2", "L3", "L4"]},
        "version": "0.1.0",
    }


class Handler(BaseHTTPRequestHandler):
    root = ROOT
    console_dir: Optional[Path] = None

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path.startswith("/api/status"):
                limit = int(q.get("limit", ["200"])[0])
                self._json(_collect(self.root, limit=limit))
            elif u.path.startswith("/api/events"):
                from .ledger import Ledger
                led = Ledger(self.root / "data" / "ledger.jsonl")
                kind = q.get("kind", [None])[0]
                evs = led.replay(kinds=[kind] if kind else None)
                self._json({"events": [e.__dict__ for e in evs][-500:]})
            elif u.path.startswith("/api/ledger/raw"):
                from .ledger import Ledger
                led = Ledger(self.root / "data" / "ledger.jsonl")
                self._send(200, "".join(led.iter_lines()).encode("utf-8"),
                           "application/x-ndjson")
            elif self.console_dir and (u.path == "/" or u.path.startswith("/console")):
                rel = "index.html" if u.path in ("/", "/console", "/console/") \
                    else u.path[len("/console/"):]
                f = (self.console_dir / rel).resolve()
                if not str(f).startswith(str(self.console_dir.resolve())) or not f.exists():
                    self._json({"error": "not found"}, 404)
                    return
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                self._send(200, f.read_bytes(), ctype)
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
    args = ap.parse_args(argv)

    Handler.root = Path(args.root)
    Handler.console_dir = Path(args.console) if args.console else None
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"V6 API on http://{args.host}:{args.port}/api/status "
          f"(root={Handler.root})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

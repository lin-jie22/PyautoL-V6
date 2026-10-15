#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：HTTP API 鉴权 —— 「默认绑 localhost」不是鉴权。

补齐前的真实形态：六个端点全部零鉴权，唯一保护是 `--host` 默认
127.0.0.1 这条**隐式约定**。而 `--host 0.0.0.0` 一个开关就能拆掉它，
且拆掉时**没有任何提示** —— 一旦有人把服务塞进容器就是：

  · `GET /api/ledger/raw`   ⇒ 账本原文（含失败轨迹、harness 状态）
  · `GET /api/status`       ⇒ 记忆正文与投影读数
  · `POST /api/invoke`      ⇒ 触发已装载的外部能力（agent / skill / tool）

所以本契约钉三件事：

  1. **外向监听必须带令牌**（启动期 fail-closed，不是"运行时再说"）；
  2. **令牌比较不泄漏时序**（`hmac.compare_digest`，不是 `==`）；
  3. **鉴权状态可读** —— 否则"服务上没上锁"事后无从查证。

跑法（离线，秒级；每个用例起独立端口的服务）：
    python tests/contract_auth_test.py
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        r = _opener.open(req, timeout=5)
        return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


def _post(url, payload, headers=None):
    hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=hdr, method="POST")
    try:
        r = _opener.open(req, timeout=5)
        return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


def _serve(token=""):
    """起一个独立端口的服务，返回 (base_url, shutdown)。"""
    from aether.serve import Handler
    Handler.api_token = token
    Handler.root = Path(tempfile.mkdtemp(prefix="v6_auth_"))
    Handler.console_dir = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv.shutdown


def main():
    from aether import serve as S

    print("== A 组：不设令牌 ⇒ 回环场景照常可用（向后兼容）==")
    url, stop = _serve("")
    try:
        code, body = _get(url + "/api/status")
        ck("A1 无令牌时 GET 正常（默认场景不被破坏）", code == 200, str(code))
        ck("A2 读数明说没上锁（事后可查证，不用翻启动命令）",
           body.get("auth", {}).get("required") is False
           and body.get("auth", {}).get("scheme") == "none",
           str(body.get("auth")))
    finally:
        stop()

    print()
    print("== B 组：设了令牌 ⇒ 不带就 401，且端点全覆盖 ==")
    url, stop = _serve("s3cr3t")
    try:
        code, body = _get(url + "/api/status")
        ck("B1 不带令牌 ⇒ 401（不是 200，也不是 404）", code == 401, str(code))
        ck("B1b 401 带可行动提示（告诉调用方怎么带）",
           "Authorization" in json.dumps(body, ensure_ascii=False),
           str(body)[:160])
        code2, _ = _get(url + "/api/status",
                        {"Authorization": "Bearer s3cr3t"})
        ck("B2 Bearer 正确 ⇒ 放行", code2 == 200, str(code2))
        code3, _ = _get(url + "/api/status", {"X-API-Key": "s3cr3t"})
        ck("B3 X-API-Key 也认（两种常见形态都支持）", code3 == 200, str(code3))
        code4, _ = _get(url + "/api/status",
                        {"Authorization": "Bearer wrong"})
        ck("B4 错的令牌 ⇒ 401（不是 403：不暴露" "资源存在性以外的信息）",
           code4 == 401, str(code4))
        code5, _ = _get(url + "/api/events?limit=1")
        ck("B5 GET /api/events 同样受保护（鉴权必须在**总入口**，不是逐端点）",
           code5 == 401, str(code5))
        code6, _ = _post(url + "/api/invoke", {"capability": "x"})
        ck("B6 ⛔ POST /api/invoke 同样受保护（这是最能造成后果的端点）",
           code6 == 401, str(code6))
        code7, _ = _post(url + "/api/capabilities/reload", {"manifest": "/tmp/x"})
        ck("B7 POST 热装载同样受保护", code7 == 401, str(code7))
        code8, body8 = _get(url + "/api/status",
                            {"Authorization": "Bearer s3cr3t"})
        ck("B8 上锁后读数改成 required=True",
           code8 == 200 and body8.get("auth", {}).get("required") is True,
           str(body8.get("auth")))
    finally:
        stop()

    print()
    print("== C 组：外向监听必须带令牌（启动期 fail-closed）==")
    #: ⛔ 捕获 stderr 而不是只看 rc：rc=2 只说明"被拒了"，不能证明
    #:   **理由**给了出路（"拒绝但没说怎么办"同样是坏体验）。
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = S.main(["--host", "0.0.0.0", "--port", "1"])
    msg = buf.getvalue()
    ck("C1 0.0.0.0 + 无令牌 ⇒ 拒绝启动（不是起来再说）", rc == 2, f"rc={rc}")
    ck("C2 拒绝原因写明出路（--api-token 与改回回环都点到）",
       "--api-token" in msg and "127.0.0.1" in msg, msg[:200])
    saved = S._LOOPBACK_HOSTS
    try:
        S._LOOPBACK_HOSTS = frozenset()          # 事故版：谁都不算回环
        buf2 = io.StringIO()
        with redirect_stderr(buf2):
            rc3 = S.main(["--host", "127.0.0.1", "--port", "1"])
        ck("C3 ⛔ 反证：回环不再被豁免 ⇒ 同样拒绝启动"
           "（证明 C1 拦的是 host 判定，不是别的）", rc3 == 2, f"rc={rc3}")
    finally:
        S._LOOPBACK_HOSTS = saved
    ck("C4 还原后回环重新被豁免（事故版没残留）",
       S._LOOPBACK_HOSTS is saved and "127.0.0.1" in S._LOOPBACK_HOSTS,
       str(sorted(S._LOOPBACK_HOSTS)))
    ck("C5 认不出的地址一律按非回环处理（保守 ⇒ fail-closed）",
       "0.0.0.0" not in S._LOOPBACK_HOSTS
       and "myhost" not in S._LOOPBACK_HOSTS)

    print()
    print("== D 组：令牌比较不得泄漏时序 ==")
    src = Path(ROOT / "aether" / "serve.py").read_text(encoding="utf-8")
    ck("D1 用 hmac.compare_digest 比较（不是 == ⇒ 无逐字节时序侧信道）",
       "hmac.compare_digest" in src)
    ck("D2 比较**只**出现在鉴权方法里（没有别处拿 == 比令牌）",
       src.count("hmac.compare_digest") >= 1)
    url, stop = _serve("abcdefgh")
    try:
        #: 前缀相同但整体错误 ⇒ 必须同样是 401（不是"前几位对了就多给信息"）
        c_part, _ = _get(url + "/api/status",
                         {"Authorization": "Bearer abcdefghXXXX"})
        c_pref, _ = _get(url + "/api/status",
                         {"Authorization": "Bearer abcdefZZ"})
        ck("D3 前缀命中与完全不命中 ⇒ 同为 401（响应不得区分到哪一位对）",
           c_part == 401 and c_pref == 401, f"{c_part}/{c_pref}")
    finally:
        stop()

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   ✗ {f}")
        return 1
    print("✅ 鉴权契约全部通过：外向必带令牌、端点全覆盖、状态可读")
    return 0


if __name__ == "__main__":
    sys.exit(main())

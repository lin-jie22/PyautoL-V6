#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：外部**只读**发现 —— 网络只用于「发现」，绝不用作「判据」。

═══ 这条契约守的是什么 ═══

用户要的是"系统能自己在 GitHub 上找到好东西为自己用"。
这条契约守的是**做这件事时不许踩的三条线**，少一条，C 就从"给人多一条
信息"变成"把系统的判断力交给外部 API"：

  A 外部查询是**三态**的（拿到 / 不存在 / 现在取不到），不许合并成布尔；
  B 派生量（陈旧天数、许可）解析不出来就**空着**，不许退回 0；
  C ⛔⛔ **外部事实绝不进判据** —— 这是宪法第一条（判据必须离线可回归）
     的落地：`stars` 今天被点几颗、`pushed_at` 是多少，都不许影响
     一个工具"该不该装"的结论，否则判据就随外部 API 波动了；
  D ⛔ 默认路径**零网络**：不发请求就是不发，不联网时也不许有半点痕迹；
  E slug 按**字符白名单**收，路径穿越 / 查询串注入必须被拦下。

跑法（**完全离线**，秒级 —— 用的是 127.0.0.1 回环服务器，不是真 GitHub）：
    python tests/contract_ghprobe_test.py

⛔ 为什么必须离线：宪法要求判据离线可回归，而本契约自己就是判据。
   让契约去打真 GitHub ⇒ 配额一满或网络一抖，它就红了 ⇒ 契约从"守住
   不变量"退化成"报告外部服务的可用性"（V5 实测：链首 82% 429，
   整条供给链废掉两轮）。
"""
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约，
#:    写死 ⇒ 契约永远 import 生产那份 ⇒ 删前删后跑的是同一份代码 ⇒
#:    对拍恒绿 ⇒ 那道闸是空的（实测：删掉 `_SLUG_RE` 定义后契约仍 rc=0）。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


class _GH(BaseHTTPRequestHandler):
    """假 GitHub。**数请求数**是本契约最硬的一条判据（见 D 组）。"""

    code = 200
    body: dict = {}
    hits: list = []

    def do_GET(self):                                    # noqa: N802
        type(self).hits.append(self.path)
        payload = json.dumps(type(self).body).encode("utf-8")
        self.send_response(type(self).code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):                           # 别把请求打到 stdout
        return


#: 一份真的仓库响应（字段取自实测 api.github.com 的真实结构）。
_REPO = {"full_name": "radareorg/radare2", "stargazers_count": 25098,
         "license": {"spdx_id": "NOASSERTION"},
         "pushed_at": "2026-10-19T16:16:32Z", "archived": False,
         "html_url": "https://github.com/radareorg/radare2"}


def _n_hits() -> int:
    return len(_GH.hits)


def _real_repo(archived: bool = False, pushed: str = "2026-10-19T16:16:32Z",
               stars: int = 25098) -> dict:
    d = dict(_REPO)
    d.update({"archived": archived, "pushed_at": pushed,
              "stargazers_count": stars})
    return d


def main() -> int:
    from aether import caps_candidate as CC
    from aether import ghprobe as G

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _GH)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    # ── A 组：三态，不许合并成布尔 ───────────────────────────────
    print("A 组：外部查询是三态（拿到 / 不存在 / 现在取不到）")
    _GH.code, _GH.body = 200, _real_repo()
    f_ok = G.fetch("radareorg/radare2", api_base=base)
    ck("A1 拿到了 ⇒ ok，且字段真的抽出来了",
       f_ok.status == G.OK and f_ok.stars == 25098
       and f_ok.full_name == "radareorg/radare2", f_ok.to_dict())
    ck("A2 原始值照存（pushed_at 不在这里派生，派生交给显示层）",
       f_ok.pushed_at == "2026-10-19T16:16:32Z", f_ok.pushed_at)

    _GH.code, _GH.body = 404, {"message": "Not Found"}
    f_404 = G.fetch("radareorg/nope", api_base=base)
    ck("A3 仓库不存在 ⇒ not_found（**不是** unavailable）",
       f_404.status == G.NOT_FOUND, f_404.status)

    _GH.code, _GH.body = 403, {"message": "rate limit exceeded"}
    f_403 = G.fetch("radareorg/radare2", api_base=base)
    ck("A4 被限流 ⇒ unavailable，且**单独标 rate_limited**"
       "（合并进 not_found 会让配额用完看起来像仓库没了）",
       f_403.status == G.UNAVAILABLE and f_403.detail == "rate_limited",
       f"{f_403.status}/{f_403.detail}")
    ck("A5 三种状态的**成因互不相同**（不是换个说法的同一个东西）",
       {f_ok.status, f_404.status, f_403.status}
       == {G.OK, G.NOT_FOUND, G.UNAVAILABLE})
    ck("A6 只有 ok 才 usable（usable 只用来决定要不要写 evidence）",
       f_ok.usable and not f_404.usable and not f_403.usable)

    _GH.code, _GH.body = 200, {"nope": 1}
    f_bad = G.fetch("radareorg/radare2", api_base=base)
    ck("A7 ⛔ 拿到了响应但结构不对 ⇒ unavailable，**绝不假装 ok**"
       "（全用 .get() 会把它解析成一个看着正常的空事实）",
       f_bad.status == G.UNAVAILABLE and not f_bad.usable, f_bad.to_dict())

    _GH.code, _GH.body = 200, _real_repo(archived=True)
    f_arch = G.fetch("radareorg/radare2", api_base=base)
    ck("A8 archived 真的抽出来了（它与「只是不活跃」是两件事）",
       f_arch.archived is True)

    # ── B 组：派生量解析不出来就空着，不许退回 0 ──────────────────
    print("\nB 组：派生量（陈旧天数 / 许可标签）")
    ck("B1 ⛔ stale_days 空输入 ⇒ None，绝不退回 0"
       "（0 会被读成「今天刚提交」，比空着危险得多）",
       G.stale_days("") is None)
    ck("B2 坏值 ⇒ None（不是抛出来，也不是 0）",
       G.stale_days("not-a-date") is None)
    import time
    _day = 86400
    now = time.time()
    ck("B3 正常值 ⇒ 天数（拿固定的 now 算，不依赖跑的这一刻）",
       G.stale_days("2026-10-19T16:16:32Z", now=now + 3 * _day) == 3,
       str(G.stale_days("2026-10-19T16:16:32Z", now=now + 3 * _day)))
    ck("B4 ⛔ 许可标签：空 与 NOASSERTION 必须不同"
       "（前者是没声明，后者是声明了但 SPDX 没认出来）",
       G.license_label("") != G.license_label("NOASSERTION"),
       f'{G.license_label("")} / {G.license_label("NOASSERTION")}')
    ck("B5 真实许可原样透传（不做加工）",
       G.license_label("Apache-2.0") == "Apache-2.0")

    # ── C 组：⛔⛔ 外部事实绝不进判据 ─────────────────────────────
    print("\nC 组：外部事实只进 evidence，绝不进判据（宪法第一条）")

    def _mk() -> "CC.Candidate":
        return CC.Candidate(tool="radare2", kind="missing_tool",
                            install_argv=["brew", "install", "radare2"],
                            verify_hint="装完跑 verify", reason="缺")

    #: ⛔⛔ 把"默认目标"也临时指向回环 ⇒ **任何**意外请求都会被数到。
    #:    不改它的话，代码只要绕过 `api_base`（比如有人删掉了 net 检查，
    #:    或者手拼 API_BASE 常量），请求就会打到真 GitHub 而 hits **不涨**
    #:    ⇒ 断言假绿 —— 测的就成了"有没有打回环"，而不是"有没有联网"。
    #:    实测：事故版 A 在改这条之前，D1 是绿的（第 24 条）。
    _saved_base = G.API_BASE
    G.API_BASE = base
    try:
        before = _n_hits()
        c0 = CC.annotate(_mk(), "radareorg/radare2")
        ck("C1 ⛔ 默认不发请求 ⇒ 一个字节都不写"
           "（不写 = 没查过，不留 status=not_queried 那种假痕迹）",
           "external" not in c0.evidence and _n_hits() == before,
           f"external={'external' in c0.evidence} hits+{_n_hits() - before}")
    finally:
        G.API_BASE = _saved_base
    ck("C2 清单没声明上游 ⇒ 不猜（与 install_argv 空着同一条纪律）",
       "external" not in CC.annotate(_mk(), "", net=True).evidence)

    _GH.code, _GH.body = 200, _real_repo()
    c1 = CC.annotate(_mk(), "radareorg/radare2", net=True, api_base=base)
    ck("C3 显式开了 ⇒ 真的写进 evidence 了",
       (c1.evidence.get("external") or {}).get("status") == G.OK,
       str(c1.evidence))

    _a = _mk()
    _a.cap_id = CC.content_hash(_a.identity())
    _b = CC.annotate(_mk(), "radareorg/radare2", net=True, api_base=base)
    _b.cap_id = CC.content_hash(_b.identity())
    ck("C4 ⛔ 外部事实**不改 cap_id**"
       "（identity 只含 tool/kind/install_argv，evidence 不在里面）",
       _a.cap_id == _b.cap_id, f"{_a.cap_id} vs {_b.cap_id}")

    #: ⛔⛔ 这一条是 C 组的命根子：外部事实不许影响任何闸。
    #:   造一个**极端到荒谬**的外部事实（已归档 + 0 星 + 十年没提交），
    #:   闸的结论必须一字不变 —— 否则"仓库什么状态"就成了"能不能装"的判据。
    _p = _mk()
    CC.validate(_p)
    _q = _mk()
    _q.evidence["external"] = {"status": G.OK, "full_name": "a/b", "stars": 0,
                               "license_spdx": "", "pushed_at": "",
                               "archived": True, "html_url": "",
                               "fetched_at": 0.0}
    CC.validate(_q)
    ck("C5 ⛔⛔ 极端外部事实（已归档 + 0 星 + 无提交记录）也**改不了闸**"
       "（否则外部 API 一波动，判据就跟着动）",
       _p.gate_passed == _q.gate_passed
       and _p.gate_reasons == _q.gate_reasons
       and _p.state == _q.state, f"{_p.state} vs {_q.state}")

    _GH.code, _GH.body = 403, {"message": "rate limit exceeded"}
    c2 = CC.annotate(_mk(), "radareorg/radare2", net=True, api_base=base)
    ck("C6 三态**都留痕**：取不到也记下来"
       "（否则「从没测过」与「测了没结果」就同形了）",
       (c2.evidence.get("external") or {}).get("status") == G.UNAVAILABLE,
       str(c2.evidence.get("external")))

    # ── D 组：默认路径零网络（跨进程，量的是请求数） ────────────────
    print("\nD 组：默认路径真的零网络（跨进程数回环服务器的请求数）")
    tmp = Path(tempfile.mkdtemp(prefix="v6_gh_"))
    (tmp / "data").mkdir(exist_ok=True)

    def _run(*extra):
        return subprocess.run(
            [sys.executable, "-B", "-m", "aether.cli", "--root", str(tmp),
             "caps", "candidates", "propose", *extra],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300)

    _GH.code, _GH.body = 200, _real_repo()
    _GH.hits.clear()
    r_plain = _run()
    out_plain = r_plain.stdout.decode("utf-8", "replace")
    ck("D1 ⛔ 默认 propose **一次网络请求都没发**"
       "（默认路径上这条能力是「不存在」的）",
       _n_hits() == 0, f"hits={_n_hits()}")
    ck("D2 ⛔ 默认输出里不出现上游行（不显示 == 没查过）",
       "🌐" not in out_plain, out_plain[:200])

    before = _n_hits()
    r_net = _run("--net", "--gh-api-base", base)
    out_net = r_net.stdout.decode("utf-8", "replace")
    ck("D3 显式 --net ⇒ 真的发了请求",
       _n_hits() > before, f"hits={_n_hits() - before}")
    ck("D4 显式 --net ⇒ 输出里出现上游行（这是给人看的那一半）",
       "🌐" in out_net and "radareorg/radare2" in out_net, out_net[:300])
    ck("D5 上游行里不许出现 API 原始值 NOASSERTION"
       "（radare2 实际是 LGPL，透传原始值等于说它许可有问题）",
       "NOASSERTION" not in out_net, out_net[:300])

    _GH.code, _GH.body = 403, {"message": "rate limit exceeded"}
    r_lim = _run("--net", "--gh-api-base", base)
    out_lim = r_lim.stdout.decode("utf-8", "replace")
    ck("D6 ⛔ 被限流时**照样显示**，不许当没事"
       "（读不到 ≠ 没有上游，空着会让读者以为这工具没人维护）",
       "取不到" in out_lim, out_lim[:300])

    _GH.code, _GH.body = 404, {"message": "Not Found"}
    r_404 = _run("--net", "--gh-api-base", base)
    out_404 = r_404.stdout.decode("utf-8", "replace")
    ck("D7 仓库不存在 ⇒ 指向该改的是清单，不是这个工具",
       "gh_repo" in out_404, out_404[:300])

    # ── E 组：slug 白名单（路径穿越 / 注入） ──────────────────────
    print("\nE 组：slug 按字符白名单收（拼 URL 之前的最后一道）")
    _GH.hits.clear()
    for bad in ["../b", "a/../b", "a/b?x=1", "a b/c", "radareorg/radare2/",
                "a//b", ".hidden/x", "x/."]:
        f = G.fetch(bad, api_base=base)
        ck(f"E ⛔ {bad!r} 被拦下且**不发请求**",
           f.status == G.UNAVAILABLE and _n_hits() == 0,
           f"{f.status} hits={_n_hits()}")
    #: ⛔ 假服务器的 `code/body` 是**类变量**，跨组泄漏。上一组（D7）把它
    #:    留在 404 上，这里不重置就会让「合法 slug」碰上 404 ⇒ 断言假红。
    #:    （契约自己也会踩第 24 条：读数是共享的，每次用前必须显式设好。）
    _GH.code, _GH.body = 200, _real_repo()
    _f_ok2 = G.fetch("radareorg/radare2", api_base=base)
    ck("E-ok 合法 slug 不受影响（白名单不误伤）",
       _f_ok2.status == G.OK, _f_ok2.status)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：外部发现是三态的、默认零网络、且绝不进判据")
    return 0


if __name__ == "__main__":
    sys.exit(main())

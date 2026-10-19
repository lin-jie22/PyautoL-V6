#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部**只读**事实探针：查一个开源仓库的上游现状（绝不下载、绝不执行）。

═══ 先说清楚这个模块**是什么**，以及它**不是**什么 ═══

它是 C（外部发现器）的落地，但**只取回一半**：

  · ✅ 做的：问"这个仓库还活着吗、多少人用、什么许可、最近一次提交"。
  · ❌ 不做的：下载、clone、装、执行、解析代码、评估"好不好"。

⇒ 它产出的是**事实记录**，不是判据。这一点是硬边界，见下。

═══ ⛔⛔ 硬边界：网络只用于「发现」，绝不用作「判据」═══

本项目的第一条命题是**判据必须离线可回归**：自进化系统的"零新红""
非回归"一旦依赖网络或模型供给，就会随供给波动而失效
（V5 实测：链首供应商 82% 429，整条供给链废掉两轮）。

所以本模块的产出**只能进 `evidence`，绝不能进 `validate()` 的四道闸**：

  · 不许写"stars < N ⇒ 拒绝"这类规则 ⇒ 那会让"仓库今天被点了几个星"
    变成能不能装某个工具的判据（荒谬，且随网络波动）。
  · 不许拿 `pushed_at` 当"过期则拒" ⇒ 一个十年没提交的工具可能恰恰
    是最稳的那个（这正是 `archived` 与 `pushed_at` 要分开记的原因：
    **归档是作者的决定，不活跃只是现状**）。

⇒ 它的消费方是**人**：给人多一条本机给不了的信息，让人自己判断。
   机器负责的是"把事实如实取回来"，包括"取不回来"也要如实。

═══ 三条实测逼出来的约束（不是设计时想到的）═══

1. **用精确仓库接口，不用搜索接口**。
   实测 `GET /rate_limit` ⇒ `core: 60 次/小时`，而 `search: 仅 10 次/分钟`。
   差 6 倍，而且搜索的第一条**不保证是官方仓库**
   （实测搜 `radare2` 返回 665 个，第一条才是 `radareorg/radare2`）。
   ⇒ 工具的上游仓库由清单**显式声明**（`Tool.gh_repo`），
      和 `install_argv` 一个道理：不知道就留空，**不猜**。

2. **必须绕过代理**。
   本机有 `HTTP_PROXY=http://127.0.0.1:61086`。访 localhost 要绕开它
   （否则 502 假故障，与 `interop`/`providers` 同款），而实测**直连**
   api.github.com 是通的（0.6–3.2s）。⇒ 统一用 `ProxyHandler({})`。

3. **三态，不许合并成布尔**。
   "仓库不存在（slug 写错）"与"现在取不到（断网/限流）"处置完全不同：
   前者要改清单，后者**什么都不该做**（更不该当成"这工具没人要"）。
   合成 `ok = 有数据` 一个布尔 ⇒ 排障方向全错（同族：`discover.Probe`
   的 found/missing/error）。

═══ 默认**不发请求** ═══

本模块默认不联网。只有调用方显式传 `net=True`（CLI 的 `--net`）才发。
跟其它"明码标价"的昂贵动作一个处理（r42 的对拍预算）：
**破法存在但默认关**，而不是删掉它 —— 想要的时候有，不想要时零成本。
"""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

__all__ = ["API_BASE", "OK", "NOT_FOUND", "UNAVAILABLE",
           "RepoFact", "fetch", "stale_days", "license_label", "NET_OFF"]

#: 默认 API 根。⛔ **可注入**：契约用 `http://127.0.0.1:<port>` 起本地
#: 回环服务器，⇒ 契约**完全离线**（宪法：判据必须离线可回归）。
API_BASE = "https://api.github.com"

OK = "ok"                # 拿到了，且解析出了仓库事实
NOT_FOUND = "not_found"  # 仓库不存在（清单里的 slug 写错了）
UNAVAILABLE = "unavailable"  # 现在取不到（网络/超时/限流），**不是**它不存在

#: 默认不发请求。想要网络必须显式开（CLI `--net`）。
NET_OFF = False

#: 请求超时（秒）。⛔ 短：这是一次元数据查询，不是下载。
#: 给长了会让 `caps candidates propose --net` 变成一条挂死的命令。
TIMEOUT_S = 8.0

#: ⛔ 这个 UA 是**必须**的：GitHub 会拒绝没有 User-Agent 的请求。
_UA = "V6-ghprobe"

#: ⛔⛔ slug 的形态白名单。别拿 `slug.count("/") != 1` 当形态检查：
#:    `../b` 正好只有一个斜杠 ⇒ 拼出 `…/repos/../b` = `…/b`（路径穿越）。
#:    ⛔ 也别只按"字符集"收：`.` 本身是仓库名的合法字符，于是
#:    `[A-Za-z0-9._-]+/[A-Za-z0-9._-]+` **照样会放行 `../b`**
#:    （owner 段被匹配成 `..`）—— 这是我第一版的实测漏法。
#:    ⇒ 按 **GitHub 自己的命名规则**收：
#:      · owner 段：字母数字**开头**，且**不含点**（GitHub 用户名不含点）
#:        ⇒ `..` / `.hidden` 直接被拒；
#:      · repo 段：字母数字或 `_` 开头，后面才允许 `.` 与 `-`；
#:      · 两段都**不含 `/`**，加上 `\A…\Z` 锚定 ⇒ 拼出来的 URL 里
#:        除了那一个分隔符不会再有第二个 `/` ⇒ 路径穿越无路可走。
_SLUG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_][A-Za-z0-9._-]*\Z")


@dataclass
class RepoFact:
    """一次外部查询的结果。⛔ **三态**（见模块文档约束 3）。

    `status` 才是判据；`full_name` 这些只在 `status == OK` 时有意义。
    """
    slug: str = ""                       # 请求的 owner/name
    status: str = UNAVAILABLE
    #: 取不到时的原因。**三态之上再细分**：`rate_limited` / `timeout` /
    #: `http_<code>` / `network_error` —— 让排障方向一眼可见。
    detail: str = ""
    full_name: str = ""
    stars: int = 0
    license_spdx: str = ""               # 空 = 没声明许可（不是"取不到"）
    pushed_at: str = ""                  # ISO8601，原始值，不做派生
    archived: bool = False
    html_url: str = ""
    fetched_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"slug": self.slug, "status": self.status,
                "detail": self.detail, "full_name": self.full_name,
                "stars": self.stars, "license_spdx": self.license_spdx,
                "pushed_at": self.pushed_at, "archived": self.archived,
                "html_url": self.html_url, "fetched_at": self.fetched_at}

    @property
    def usable(self) -> bool:
        """⛔ 只是"拿到数据了吗"的便捷写法，**不是**"这工具好不好"。

        它只用来决定"要不要把这条写进 evidence"，绝不参与任何闸。
        """
        return self.status == OK


def stale_days(pushed_at: str, *, now: Optional[float] = None) -> Optional[int]:
    """距最近一次提交多少天。**派生量**，不在 `RepoFact` 里存。

    ⛔ 存原始值 + 用时再算，而不是存"天数"：存了天数，读者看到的是
       "3 天前"还是"30 天前"取决于**它被记下来的那一刻**，
       而不是它被显示出来的那一刻 ⇒ 一条旧记录会永远显示"刚更新"。
    ⛔ 解析不出来返回 `None`（**三态的第三态**），不返回 0：
       0 会被读成"今天刚提交"，比空着危险得多。
    """
    if not pushed_at:
        return None
    iso = pushed_at.strip().replace("Z", "+00:00")
    try:
        t = datetime.datetime.fromisoformat(iso).timestamp()
    except Exception:                                       # noqa: BLE001
        return None
    base = now if now is not None else time.time()
    return int(max(0.0, base - t) // 86400)


def license_label(spdx: str) -> str:
    """把 API 的 `license.spdx_id` 翻成人能读的话。

    ⛔ 不许透传原始值：`radare2` 实际是 LGPL，而 GitHub 因为仓库里有多个
       LICENSE 文件报 `NOASSERTION` ⇒ 照原样打印等于告诉读者"这工具
       许可有问题"，而真相是"SPDX 没认出来" —— 两件事。

    ⛔ 空串与 `NOASSERTION` 也是两件事：前者是**真的没有声明**，
       后者是**声明了但没被认出来**。合成一句会把一个有许可的项目
       说成没有许可。
    """
    s = (spdx or "").strip()
    if not s:
        return "未声明许可"
    if s == "NOASSERTION":
        return "非标准许可（SPDX 未识别）"
    return s


def fetch(slug: str, *, api_base: str = API_BASE,
          timeout: float = TIMEOUT_S) -> RepoFact:
    """查一个仓库的元数据。**只读 GET**，绝不下任何东西。

    ⛔ 三态处置：
      · 200 且解析得出来 ⇒ `OK`
      · 404               ⇒ `NOT_FOUND`（**清单写错了**，该改的是清单）
      · 其它              ⇒ `UNAVAILABLE`（**现在取不到**，什么都不该改）
    ⛔ 403/429 单独标 `rate_limited`：那与"仓库不存在"是完全不同的事，
       合并成 UNAVAILABLE 会让"配额用完了"看起来像"网络坏了"。
    """
    out = RepoFact(slug=slug)
    slug = (slug or "").strip()
    if not slug or not _SLUG_RE.match(slug):
        out.status = UNAVAILABLE
        out.detail = "slug 形态不对（只接受 owner/name 且只含安全字符）⇒ 拒绝拼 URL"
        return out

    url = f"{api_base.rstrip('/')}/repos/{slug}"
    #: ⛔ 绕代理是**必须的**（实测：本机 HTTP_PROXY 会让直连变成 502）。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA,
                      "Accept": "application/vnd.github+json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:                     # noqa: BLE001
        code = getattr(e, "code", 0)
        if code == 404:
            out.status = NOT_FOUND
            out.detail = "仓库不存在（清单里的 slug 可能写错了）"
            return out
        if code in (403, 429):
            out.status = UNAVAILABLE
            out.detail = "rate_limited"
            return out
        out.status = UNAVAILABLE
        out.detail = f"http_{code}"
        return out
    except Exception as e:                                  # noqa: BLE001
        name = type(e).__name__
        #: ⛔ `socket.timeout` 在 3.10+ 是 `TimeoutError` 的别名 ⇒ 按类型名
        #:   判比按 `isinstance` 稳（跨版本不同名）。
        out.status = UNAVAILABLE
        out.detail = ("timeout" if "Timeout" in name or "timeout" in name.lower()
                      else f"network_error:{name}")
        return out

    #: ⛔ 拿到了响应但**不是仓库响应** ⇒ 不假装 ok。
    #:    后面全是 `.get(...) or ""`，对 `{"nope": 1}` 这种垃圾会一路
    #:    解析出一个"看起来正常"的空事实（full_name 落到兜底的 slug、
    #:    stars=0、pushed_at=""），然后报 `ok` —— 那比如实说"取不到"
    #:    危险得多：下游会把"什么都没查到"当成"查到了，只是它不活跃"
    #:    （第 24 条：量具会撒谎，而撒谎的方式常常是空值）。
    if not str((data or {}).get("full_name") or ""):
        out.status = UNAVAILABLE
        out.detail = "bad_response: 响应里没有 full_name"
        return out

    try:
        lic = data.get("license") or {}
        out.full_name = str(data.get("full_name") or slug)
        out.stars = int(data.get("stargazers_count") or 0)
        out.license_spdx = str(lic.get("spdx_id") or "")
        out.pushed_at = str(data.get("pushed_at") or "")
        out.archived = bool(data.get("archived"))
        out.html_url = str(data.get("html_url") or "")
    except Exception as e:                                  # noqa: BLE001
        #: ⛔ 拿到了响应但结构不对 ⇒ 仍是 UNAVAILABLE，且**不假装 OK**。
        out.status = UNAVAILABLE
        out.detail = f"bad_response:{type(e).__name__}"
        return out

    out.status = OK
    out.fetched_at = time.time()
    return out

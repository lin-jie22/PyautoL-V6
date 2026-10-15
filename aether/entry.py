"""三个入口（cli / serve / ui）公共参数的**唯一事实源**（r36）。

为什么需要它（2026-10-14 取证）：三个入口各自声明同一批参数，已经出现
**四处漂移**：

  1. `--log-level` 的 choices：cli/serve 用 `LEVELS`，ui **硬编码**
     `["DEBUG","INFO",...]` ⇒ 级别表改了 ui 不会跟着改，而两个入口的
     校验行为就此分叉（同一份配置在 cli 合法、在 ui 报 rc=2）。
  2. `--caps` 的默认值：ui 是示例清单（`capabilities.example.json`），
     cli 的 `caps` / `doctor` 是空（只看内置能力）。差异**是刻意的**
     （命令行默认是"什么都不接"，UI 默认是"开箱能看见"），但必须写在
     同一处并起好名字，否则它看起来就像漏改。
  3. `--port` 默认值 8611 在三个入口各写一遍。
  4. ⛔ 最要紧的一条：`python -m aether.cli ui --log-level DEBUG` 之前
     **静默无效** —— 全局 `--log-level` 在 ui 分支被用来转发给子进程，
     但转发清单是手写的，漏了 log-level / log-json / api-token。
     "设了不生效"且没有任何报错，是标准的假仪表。

⛔ 怎么保证不再漏：**透传清单不手写**，由 `build_forward()` 遍历
   `serve.build_parser()` 的选项**动态导出**。以后给 serve 加参数：
   · ui 的 parser 用同一个 `add_serve_args()` ⇒ 一定跟着长出来；
   · 万一 ui 没这个 dest ⇒ `build_forward()` **抛错**，不是静默跳过
     （静默跳过 = 命令行开关丢了却看不出来）。
"""
from __future__ import annotations

import argparse
from typing import Any, List, Optional, Sequence

from .logging_setup import LEVELS
from .paths import CAPS_EXAMPLE, ROOT

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8611
DEFAULT_LOG_LEVEL = "INFO"

#: 命令行默认**不接**外部能力（只看源码内注册的），UI/控制台默认接示例
#: 清单。⛔ 两个默认值都在这里取名，不散在入口里 —— 散开就看不出是刻意的。
NO_CAPS = ""
DEFAULT_CAPS = CAPS_EXAMPLE

#: `build_forward()` 里**不参与**透传的 dest：
#:   root / port  —— 由 build_forward 显式拼（顺序固定，且一定是这两项）
#:   console      —— 由 ui 自己决定指向仓库的 console/，不接受外部值
#:   help         —— argparse 自带
_NO_FORWARD = frozenset({"help", "root", "port", "console"})

__all__ = [
    "DEFAULT_HOST", "DEFAULT_PORT", "DEFAULT_LOG_LEVEL", "NO_CAPS",
    "DEFAULT_CAPS", "add_root_arg", "add_log_args", "add_serve_args",
    "add_ui_args", "build_forward", "normalize_log_args",
]


def add_ui_args(p: argparse.ArgumentParser) -> None:
    """ui 专有的参数（`--no-open`）。

    ⛔ 它与 `add_serve_args` 分开：前者只影响"怎么打开窗口"，后者决定
       "服务怎么起"。混在一起会让 cli 的 `ui` 子命令冒出它不支持的开关。
    """
    p.add_argument("--no-open", action="store_true",
                   help="只起服务，不开窗口")


def add_root_arg(p: argparse.ArgumentParser, *, suppress: bool = False
                 ) -> None:
    """`--root`（三个入口同名同义同默认）。

    ⛔ `suppress` 的语义与 `add_log_args` 完全一样：子 parser 的默认值会
       盖掉主 parser 已解析的值 ⇒ `aether --root /x ui` 的 /x 会被
       子命令的默认值（仓库根）悄悄换掉。子命令一律用 `suppress=True`。
    """
    #: ⛔ **幂等**：同一个 parser 上重复调用必须无害。
    #:    实测踩过：给每个子命令挂 `--root`（好让 `aether doctor --root X`
    #:    也能用）时，`_sub` 与容器子命令的路径重合 ⇒
    #:    `argparse.ArgumentError: argument --root: conflicting option string`，
    #:    而报错发生在**构造 parser 时** ⇒ 整个 CLI 都起不来。
    #:    幂等比"调用方记得别重复"可靠（同族：`setdefault` 而不是 `[k]=`）。
    if any("--root" in a.option_strings for a in p._actions):
        return
    p.add_argument("--root", default=(argparse.SUPPRESS if suppress
                                      else str(ROOT)), help="V6 根目录")


def add_log_args(p: argparse.ArgumentParser, *, suppress: bool = False
                 ) -> None:
    """`--log-level` / `--log-json`（三个入口同口径）。

    ⛔ choices 走 `LEVELS`，**不**在入口里抄一份列表（r36 前 ui 就是抄的，
       抄完就与 cli 分叉了）。
    ⛔ 默认 INFO 而非 WARNING：改造前这些诊断是 `print(file=sys.stderr)`，
       全部可见；默认 WARNING 会让 💾/ℹ️ 那几条凭空消失 ⇒ 静默改行为。

    ⛔⛔ `suppress=True` 是给**子命令**用的，别乱开：
        `argparse` 在派发子命令时会把子 parser 的 namespace **整个拷回**
        主 namespace —— 包括子 parser 的**默认值**。于是
        `aether --log-level DEBUG ui` 里的 DEBUG 会被 ui 子命令的默认值
        INFO **悄悄盖掉**（用户填的值凭空消失，且无任何提示）。
        用 `SUPPRESS` ⇒ 子 parser 没解析到的键**不进** namespace ⇒
        主 parser 的值保留；用户在子命令后显式写的仍然生效。
        ⛔ 用 SUPPRESS 就必须自己兜底（`getattr(args, ..., DEFAULT)`），
        否则独立入口（如 `python -m aether.ui`）下这个属性压根不存在。
    """
    d: Any = argparse.SUPPRESS if suppress else DEFAULT_LOG_LEVEL
    p.add_argument("--log-level", default=d, choices=list(LEVELS),
                   help="诊断输出级别（默认 INFO = 与改造前可见性一致）")
    p.add_argument("--log-json", action="store_true", default=(
        argparse.SUPPRESS if suppress else False),
                   help="诊断输出改成每行一个 JSON（供脚本消费；"
                        "**结果输出始终走 stdout，不受影响**）")


def normalize_log_args(ns: Any) -> Any:
    """把 SUPPRESS 掉的两个日志参数补回默认值（原地改，返回同一对象）。

    ⛔ 有 SUPPRESS 就必须兜底，否则 `getattr` 静默拿到"不存在"
       ⇒ 日志级别变成"上次配的那个"或干脆不配（不可预测比报错更坏）。
    """
    if not hasattr(ns, "log_level"):
        ns.log_level = DEFAULT_LOG_LEVEL
    if not hasattr(ns, "log_json"):
        ns.log_json = False
    #: `--root` 同样可能是 SUPPRESS 的（cli 的 ui 子命令）⇒ 一并兜底。
    if not hasattr(ns, "root"):
        ns.root = str(ROOT)
    return ns


def add_serve_args(p: argparse.ArgumentParser, *,
                   caps_default: str = NO_CAPS,
                   with_console: bool = True) -> None:
    """serve 的**全部**启动参数。ui 与 cli 的 `ui` 子命令复用同一份。

    ⛔ 为什么 ui 也要有 serve 的参数：它起服务的方式是**开子进程**。参数
       声明只有一份 ⇒ 两边不可能出现"serve 认这个、ui 不认"的差异。
    """
    p.add_argument("--host", default=DEFAULT_HOST, help="监听地址")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="监听端口（被占用时 ui 会自动顺延）")
    #: ⛔ 默认**不发** CORS 头：本机账本含 harness/记忆正文，任意网页
    #:    不该能读。跨源部署时显式列出白名单，或传 "*"（知情选择）。
    p.add_argument("--cors", default="",
                   help="CORS 白名单（逗号分隔 origin；'*' 表示任意）")
    if with_console:
        p.add_argument("--console", default="", help="控制台静态目录")
    p.add_argument("--caps", default=caps_default,
                   help="能力清单 JSON：接入外部 agents/skills（见 "
                        "capabilities.example.json）")
    p.add_argument("--caps-hot", action="store_true",
                   help="允许 POST /api/capabilities/reload 热装载"
                        "（默认关闭：热装载 = 运行时改变本进程能力集）")
    p.add_argument("--api-token", default="",
                   help="API 鉴权令牌。设置后所有请求必须带 "
                        "Authorization: Bearer <token> 或 X-API-Key。\n"
                        "⛔ 监听非回环地址时**必须**设置（否则拒绝启动）："
                        "本服务暴露账本原文与能力调用端点，"
                        "--host 0.0.0.0 + 无 token = 裸奔。\n"
                        "也可用环境变量 V6_API_TOKEN 传（不写进命令行历史）。")


_MISSING = object()


def build_forward(ns: Any, *, parser: argparse.ArgumentParser,
                  console: Optional[str] = None,
                  port: Optional[int] = None,
                  skip: Sequence[str] = ()) -> List[str]:
    """把「本入口的参数」翻译成 **serve 子进程的命令行**。

    ⛔ 清单**不手写**：遍历 `serve.build_parser()` 的选项，逐个从 `ns` 取
       同名属性。这样 serve 新增参数时，只有两条路：ui 的 parser 跟着
       长出同名参数（自动透传），或这里抛错（显式暴露缺失）。
       —— 手写清单的失败模式是第三条：参数丢了但一切正常（r36 前
          `--log-level` 就是这样丢的）。

    ⛔ `getattr(..., _MISSING)`：缺属性 ⇒ **抛 ValueError**，不静默跳过。
       静默跳过 => 命令行开关失效却无人知晓（与"落点要兑现"同一条纪律）。
    """
    skip_set = set(skip) | _NO_FORWARD
    #: ⛔ `port` 可覆盖：ui 会把被占用的端口**顺延**，此时必须传实际端口，
    #:    否则子进程去连一个（可能属于别人的）端口 ⇒ "读数看着正常其实是
    #:    别人家的服务"（与 `_free_port` 的注释同一条纪律）。
    cmd: List[str] = [
        "--root", str(getattr(ns, "root", ROOT)),
        "--port", str(port if port is not None
                      else getattr(ns, "port", DEFAULT_PORT))]
    for act in parser._actions:                       # noqa: SLF001
        dest = act.dest
        if dest in skip_set:
            continue
        v = getattr(ns, dest, _MISSING)
        if v is _MISSING:
            raise ValueError(
                f"入口缺少 serve 参数 --{dest.replace('_', '-')} ⇒ "
                f"透传会把它静默丢掉（ui 的 parser 必须也声明它）")
        if act.nargs == 0:                            # store_true 之类的开关
            if v:
                cmd.append(act.option_strings[0])
            continue
        if v is None or v == "":
            continue
        cmd += [act.option_strings[0], str(v)]
    if console:
        cmd += ["--console", str(console)]
    return cmd

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""能力**发现**：从本机已有的工具自动生成能力清单（不下载、不安装）。

═══ 先说清楚这个模块**不做什么**，以及为什么 ═══

V6 的接入层（`interop.py`）能把外部 agent / skill / 工具挂上并叫得动，
但它的清单只能从**本地文件或 dict** 读（`load_manifest` → `open()`）：
没有任何"去网上找""自动装"的代码。有人会把这当成缺陷 —— 这个模块
**故意不补那一条**，理由是它与本项目的第一条命题直接冲突：

  · 宪法：**判据必须离线可回归**。自进化系统的"零新红""非回归"这些判据
    一旦依赖网络或某个模型的供给，就会随供给波动而失效
    （V5 实测：链首供应商 82% 429，整条供给链废掉两轮）。
  · 沙箱：子进程里 `socket.connect` 等网络动作是**动作级拦下**的
    （`environment.py` 的 `_AUDIT_*`）。也就是说，"不可信代码自己去下载"
    这条路在结构上就是关着的 —— 这不是疏漏，是设计。
  · 信任：`interop._adapter_from` 对 `python` 形态要求清单里显式写
    `trusted: true`，因为那等同任意代码执行。**"自动下载并执行"是这个
    风险的最高形态**：它把"人审过这份清单"这一步整个拿掉。

⇒ 所以这里补的是**另一半**：不是"去网上找"，而是
**"把本机已经有的东西自动认出来"** —— 装机清单（capability manifest）
不用手写，也不用手抄版本号；由探针生成，且每条都带**证据**
（探到的路径 + 版本 + 探针命令）。装上、卸载、换版本，重跑一次就更新。

═══ 与"自动安装"的正确关系 ═══

人装工具（`brew install ghidra` / `pip install x`）—— **人负责**，
因为那会改环境、要许可证、要看供应链。
发现 + 生成清单 + 探到就算、探不到就报缺 —— **机器负责**。
这两件事的边界一旦模糊，系统就会在某个半夜自己去装一个它"觉得需要"的
东西，然后拿它当判据。这是本项目最不想看到的形态。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["Tool", "Probe", "KNOWN_TOOLS", "probe", "discover",
           "to_manifest", "report", "which", "extra_search_dirs",
           "install_roots", "INSTALL_LAYOUTS", "COMMON_BIN_DIRS"]

def extra_search_dirs() -> List[Path]:
    """PATH 之外**项目约定**的可执行文件目录。

    ⛔ 为什么需要：本项目把工具链装在 `~/.workbuddy/binaries/<tool>/versions/
       <ver>/bin/` 下（README 的 `NODE=` 就是这条约定），而那个目录**不在
       PATH 上**。只查 PATH ⇒ `node` 被报成 `missing`，而同一个仓库里的
       `check.find_node()` 却找得到它 ⇒ "有没有装"取决于你问了哪个模块。
       ⇒ 查找口径必须**只有一份**（`check.find_node` 现在复用本函数）。
    ⛔ 只认**版本化的实际路径**，不认 `current` 这类软链：报告里写出真实
       路径，才能回答"到底是哪一版"（软链指来指去正是我们要避免的含糊）。
    """
    out: List[Path] = []
    base = Path.home() / ".workbuddy" / "binaries"
    if not base.is_dir():
        return out
    try:
        for tool in sorted(base.iterdir()):
            vers = tool / "versions"
            if not vers.is_dir():
                continue
            for v in sorted(vers.iterdir()):
                b = v / "bin"
                if b.is_dir():
                    out.append(b)
    except OSError:
        return out
    return out


#: 系统级常见前缀（PATH 缺失时的兜底）。
COMMON_BIN_DIRS: Tuple[Path, ...] = (
    Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"),
)

#: **安装目录约定**：有些工具不装进 PATH，而是解压到某个"安装根"下，
#: 真正的可执行文件在它内部的固定相对路径上。
#: ⛔ 实测（2026-10-15）：本机 `ghidra` 装在
#:    `~/tools/ghidra_12.1.4_PUBLIC/support/analyzeHeadless`，
#:    不在 PATH 上 ⇒ 只查 PATH 会把"装了"报成"没装"，
#:    而 REA 自己的 doctor 明明报 `ghidra: healthy`（两份读数打架）。
#: ⛔ 只列**可执行文件的相对位置**，不猜版本号：目录名里的版本由探针读出来。
INSTALL_LAYOUTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    #: (工具名, 相对于某个安装根目录的候选相对路径)
    ("analyzeHeadless", ("support/analyzeHeadless",)),
    ("r2", ("binr/radare2", "bin/radare2")),
)


def install_roots() -> List[Path]:
    """可能存放"解压式安装"的根目录（逐个扫一层）。

    ⛔ 只扫**用户目录下的约定位置**（`~/tools`、`~/opt`、`~/.local`），
       不扫全盘：全盘扫描是让一个"发现"命令变成几十分钟的典型做法，
       而且会把无关目录里的同名文件当成本工具。
    """
    out: List[Path] = []
    for base in (Path.home() / "tools", Path.home() / "opt",
                 Path.home() / ".local" / "share"):
        if base.is_dir():
            out.append(base)
    return out


def _which_in_install_layouts(exe: str) -> str:
    """在"安装根/某工具目录/相对路径"里找可执行文件（见 `INSTALL_LAYOUTS`）。"""
    rels = dict(INSTALL_LAYOUTS).get(exe)
    if not rels:
        return ""
    for root in install_roots():
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            for rel in rels:
                cand = child / rel
                try:
                    if cand.is_file() and os.access(cand, os.X_OK):
                        return str(cand)
                except OSError:
                    continue
    return ""


def which(exe: str) -> str:
    """按**统一顺序**找一个可执行文件：PATH → 项目约定 → 安装目录约定 → 常见前缀。

    ⛔ 与 `check.find_node` 共用这一份（此前两处各写一遍，结果不一致：
       `node` 在一个模块里是 missing、在另一个模块里找得到）。
    """
    found = shutil.which(exe)
    if found:
        return found
    hit = _which_in_install_layouts(exe)
    if hit:
        return hit
    for d in list(extra_search_dirs()) + list(COMMON_BIN_DIRS):
        cand = d / exe
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except OSError:
            continue
    return ""


#: 探针超时（秒）。⛔ 很短：探针是"这个可执行文件在不在、报什么版本"，
#: 不是"跑一次分析"。给长了会把 `caps discover` 变成一个挂死的命令。
PROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Tool:
    """一个**已知的**外部工具：怎么探、探到之后怎么接入。"""
    name: str
    #: 可执行文件名（走 PATH 查找）。
    exe: str
    #: 探针参数的**候选序列**（按顺序试，第一个给出可用输出的胜出）。
    #: ⛔ 为什么是序列不是单个：同一个工具在不同发行版/版本下"打印版本"的
    #:    参数不一样（`--version` / `-V` / `-v` / `-h`），写死一个就会把
    #:    "参数不对"误报成"工具坏了"（`found` 一列全是空版本）。
    #:    ⛔ 每个候选都必须**只读、只打印版本/用法**，不许触发真分析。
    probe_args: Tuple[Tuple[str, ...], ...] = (("--version",),)
    #: 探到之后生成的清单条目骨架（`impl` 由调用方按需补全）。
    capability: Dict[str, Any] = field(default_factory=dict)
    #: 给人看的一句话：这个工具是干什么的。
    about: str = ""
    #: ⛔ 预置的替代名：同一个工具在不同包管理器下名字不同。
    aliases: Tuple[str, ...] = ()
    #: **建议**安装命令（给人执行的提示，本系统**不执行**它）。
    #: ⛔ 空元组表示"我们不知道该怎么装" ⇒ 宁可空着也不猜：
    #:    猜错会让人装错东西，比不给提示更糟（同族：`version` 抽不到就留空）。
    install_argv: Tuple[str, ...] = ()
    #: 上游仓库（GitHub 的 `owner/name`），供外部**只读**事实探针查证。
    #: ⛔ 空字符串表示"这个工具的上游不在 GitHub / 我们不确定是哪一个"
    #:    ⇒ 留空，**不猜**（与 `install_argv` 同一条纪律）。
    #:    ⛔ 为什么必须**显式声明**而不是让探针去搜：搜索接口
    #:       **只有 10 次/分钟**（core 是 60 次/小时），而且搜索结果的第一条
    #:       **不保证是官方仓库**（实测搜 `radare2` 返回 665 个）。
    #:    ⛔ 这个字段**不参与**任何判据（见 `ghprobe` 模块文档）。
    gh_repo: str = ""


@dataclass
class Probe:
    """一次探针的结果。**三态**：found / missing / error。"""
    tool: str
    status: str                 # found | missing | error
    path: str = ""
    version: str = ""
    detail: str = ""
    #: 生成清单时用的 CLI 形态（found 才有）
    capability: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "status": self.status, "path": self.path,
                "version": self.version, "detail": self.detail,
                "capability_id": (self.capability or {}).get("id")}


#: 已知工具表。⛔ **数据驱动**：新增一个工具只加一行，不改代码路径。
#: ⛔ `probe_args` 必须安全：不许是"跑一次真分析"（那会读用户文件、
#:    也可能联网）。版本探针的语义是"确认这个可执行文件是它、以及哪一版"。
KNOWN_TOOLS: Tuple[Tool, ...] = (
    Tool(name="ghidra", exe="analyzeHeadless",
         probe_args=(("-help",), ("--version",)),
         about="反编译/二进制分析（headless 模式）",
         capability={"id": "tool.ghidra", "kind": "tool"},
         aliases=("ghidra", "ghidraRun"),
         install_argv=("brew", "install", "--cask", "ghidra"),
         gh_repo="NationalSecurityAgency/ghidra"),
    Tool(name="radare2", exe="r2",
         probe_args=(("-v",), ("-V",)),
         about="逆向工程框架（radare2）",
         capability={"id": "tool.radare2", "kind": "tool"},
         aliases=("radare2", "rabin2"),
         install_argv=("brew", "install", "radare2"),
         gh_repo="radareorg/radare2"),
    #: ⛔ `gh_repo` 空着是**事实**不是遗漏：binutils 的上游在 sourceware，
    #:    otool 的上游是 Apple 的 cctools —— 它们**不在 GitHub 上**。
    #:    留空 ⇒ 外部探针跳过（与其去搜一个不存在的官方仓库，不如不查）。
    Tool(name="objdump", exe="objdump", probe_args=(("--version",),),
         about="binutils 反汇编/目标文件检查（上游在 sourceware，不在 GitHub）",
         capability={"id": "tool.objdump", "kind": "tool"}),
    #: ⛔ `otool` **没有** `--version`（Apple 的版本在 Xcode 里）。这里用
    #:    `-h`：它能证明"这个可执行文件在"，但拿不到版本号 —— 那是
    #:    `found` 的一个正常子情形（`version=""`），不是探针失败。
    Tool(name="otool", exe="otool",
         probe_args=(("--version",), ("-h",)),
         about="macOS 目标文件检查（本机自带；无 --version，版本随 Xcode）",
         capability={"id": "tool.otool", "kind": "tool"}),
    Tool(name="git", exe="git", probe_args=(("--version",),),
         about="版本控制（取证/回放常用）",
         capability={"id": "tool.git", "kind": "tool"},
         gh_repo="git/git"),
    Tool(name="node", exe="node", probe_args=(("--version",),),
         about="Node 运行时（跨语言监督器要用 ≥22）",
         capability={"id": "tool.node", "kind": "tool"},
         aliases=("nodejs",),
         gh_repo="nodejs/node"),
    Tool(name="python3", exe="python3", probe_args=(("--version",),),
         about="Python 解释器",
         capability={"id": "tool.python3", "kind": "tool"},
         aliases=("python",),
         gh_repo="python/cpython"),
)


#: 版本号形状（宽松：数字起头，允许 `1.2.3` / `2.36.1-1` / `21.0.0`）。
_VERSION_RE = __import__("re").compile(r"\b\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.\-]+)?")


def _version_line(lines: List[str]) -> Tuple[str, str]:
    """从探针输出里挑"版本行"，并抽出其中的版本号。

    ⛔ 这一步比它看起来重要。实测四种形态，抽错就会得到**看起来像版本号
       的垃圾**（比空着更糟，因为它会被写进清单当版本）：

        objdump --version  → 首行是 `OVERVIEW: llvm object file dumper`
                             （长文帮助，版本号在很后面）
        otool  --version   → 两行 `llvm-otool(1): Apple Inc. version cctools-1040`
        git    --version   → `git version 2.50.1 (Apple Git-155)`
        otool  -h          → 打印**路径** + 用法（抽出来是路径碎片）

    ⇒ 判据按优先级：
       ① 含 "version" 一词且含版本号的行（最可能是版本行）；
       ② 任何含版本号的行；
       ③ 都没有 ⇒ 用第一行非空输出（并让调用方知道它可能不是版本行）。
       返回 `(版本行, 版本号)`，版本号可为空串（**不编造**）。
    """
    hit = [ln for ln in lines if "version" in ln.lower()
           and _VERSION_RE.search(ln)]
    if not hit:
        hit = [ln for ln in lines if _VERSION_RE.search(ln)]
    if hit:
        line = hit[0]
        m = _VERSION_RE.search(line)
        return line[:120], (m.group(0) if m else "")
    return (lines[0][:120] if lines else ""), ""


def probe(tool: Tool) -> Probe:
    """探一个工具：PATH 查找 → 跑一次**版本探针**。

    ⛔ 分三态而不是两态：
        · `found`   —— 找到并且探针成功（带路径与版本）；
        · `missing` —— PATH 里没有（这是**正常**结果，不是错误）；
        · `error`   —— 找到了但探针失败（权限 / 依赖缺失 / 超时）。
       把后两者混成一个，等于把"没装"与"装了但坏了"当成同一件事 ——
       而它们的处置完全不同（一个去装，一个去修）。
    ⛔ 找不到**不抛异常**：这是发现命令，缺工具是它的正常输入。
    """
    path = which(tool.exe)
    if not path:
        for alt in tool.aliases:
            path = which(alt)
            if path:
                break
    if not path:
        return Probe(tool=tool.name, status="missing",
                     detail=f"PATH 里找不到 {tool.exe}"
                            + (f"（别名：{list(tool.aliases)}）"
                               if tool.aliases else ""))
    #: ⛔⛔ 运行期形状校验：`probe_args` 必须是"元组的元组"。
    #:    实测踩过：写成 `("--version",)`（字符串的元组）时，
    #:    `[path, *args]` 会把 **"--version" 展开成单字符**
    #:    ⇒ git 收到 `- - v e r s i o n` ⇒ `unknown option: -`，
    #:    而报错看起来像"环境里 git 坏了"（排障方向完全错）。
    #:    数据类的类型注解拦不住这个（注解不强制），所以在这里显式拦。
    for _a in tool.probe_args:
        if not isinstance(_a, (tuple, list)) or not all(
                isinstance(x, str) for x in _a):
            raise TypeError(
                f"Tool({tool.name!r}).probe_args 的元素必须是字符串元组，"
                f"实得 {_a!r} ⇒ 请写成 (('--version',),) 这种「元组的元组」")
    last_rc: Optional[int] = None
    used: Tuple[str, ...] = ()
    ver = ""
    for args in tool.probe_args:
        used = args
        try:
            p = subprocess.run([path, *args], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               timeout=PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return Probe(tool=tool.name, status="error", path=path,
                         detail=f"探针超过 {PROBE_TIMEOUT_S:.0f}s 未结束"
                                f"（命令：{path} {' '.join(args)}）")
        except OSError as e:
            return Probe(tool=tool.name, status="error", path=path,
                         detail=f"{type(e).__name__}: {e}")
        last_rc = p.returncode
        out_lines = [x.strip() for x in
                     (p.stdout or b"").decode("utf-8", "replace").splitlines()
                     if x.strip()]
        ver_line, ver = _version_line(out_lines)
        #: 拿到**版本号**就收工；只有用法/用法信息就继续试下一个候选。
        if ver:
            break
    #: ⛔ 探针**返回码非零也可能是正常的**（不少工具用非零码打印 --help）。
    #:    所以判定是"有没有输出"，而不是"退出码是不是 0"；但把退出码记进
    #:    detail，供人判断。
    if not ver_line:
        #: 找到了、但**所有**候选参数都没给出可用输出（例：工具只认
        #: 别的参数）。**仍然是 found**（可执行文件确实在），但没有版本、
        #: 也不生成清单条目 —— 比起编一个版本号，空着更诚实。
        return Probe(tool=tool.name, status="found", path=path,
                     detail=f"找到了，但候选探针都没给出可用输出"
                            f"（rc={last_rc}；试过 {list(tool.probe_args)}）",
                     capability=None)
    cap = dict(tool.capability or {})
    if cap:
        #: ⛔ 版本号抽不到就**不写**（写一个从别处切下来的碎片比空着更糟：
        #:    它看起来像个版本号，实际是路径/括注的残渣）。
        #:    ⛔ `probe_output` 保留原始那一行：审计者要能看出
        #:       "这个版本号是从哪句话里抽出来的"。
        cap.setdefault("version", ver)
        cap["probe"] = {"argv": [path, *used], "line": ver_line,
                        "rc": last_rc}
        cap["impl"] = {"transport": "cli", "argv": [path]}
        cap.setdefault("about", tool.about)
    return Probe(tool=tool.name, status="found", path=path, version=ver,
                 detail=f"rc={last_rc}；探针参数 {list(used)}", capability=cap)


def discover(tools: Optional[Tuple[Tool, ...]] = None) -> List[Probe]:
    """逐个探已知工具（顺序稳定 ⇒ 报告可逐次对比）。"""
    return [probe(t) for t in (tools if tools is not None else KNOWN_TOOLS)]


def to_manifest(probes: List[Probe], *, note: str = "") -> Dict[str, Any]:
    """把探到的工具合成一份**可直接被 `load_manifest` 读**的清单。

    ⛔ 只收 `found` 且探出了 capability 的：没有可执行路径就没法接入，
       硬写进去只会在调用时报 `unavailable`（"挂上了但叫不动"）。
    ⛔ 清单里**不写 checker**：`load_manifest` 会 fail-closed 拒绝带
       `checker` 的条目（判据是代码，不可序列化）。这里不制造那种清单。
    """
    items = []
    for p in probes:
        if p.status != "found" or not p.capability:
            continue
        c = dict(p.capability)
        #: ⛔⛔ 必须**显式声明这是个占位**：探针证明的是"这个可执行文件在、
        #:    是哪一版"，所以 `argv` 是**版本探针命令**（`objdump --version`）。
        #:    拿它当能力去调用，只会得到一段版本输出 —— 那不是能力，
        #:    是"这个工具在"的证据。
        #:    不标注的话，"发现 5 个能力"会被读成"这 5 个能力已经能用了"
        #:    （本项目最恨的形态：把"存在"伪造成"可用"）。
        c["status"] = "discovered_unwrapped"
        c["note"] = ("仅证明该工具**存在**（argv 是版本探针命令）。"
                     "要真当能力用，请补一份真正的 argv/参数声明 —— "
                     "发现不等于包装好。")
        items.append(c)
    return {
        "version": 1,
        "generated_by": "aether caps discover",
        "note": note or ("由本机探针生成：只包含**这台机器上确实存在**的工具。"
                         "它不下载、不安装、不改环境。"
                         "⛔ 每条都是 `discovered_unwrapped`：能证明工具在、"
                         "但还没被包装成可用的能力。"),
        "capabilities": items,
    }


def report(probes: List[Probe]) -> Dict[str, Any]:
    """给人/给机器看的发现报告（三态分开计数）。"""
    n = {s: sum(1 for p in probes if p.status == s)
         for s in ("found", "missing", "error")}
    return {
        "n": len(probes),
        "counts": n,
        "probes": [p.to_dict() for p in probes],
        "missing": [p.tool for p in probes if p.status == "missing"],
        "errors": [(p.tool, p.detail) for p in probes if p.status == "error"],
        "note": ("`missing` 是正常结果（这台机器没装）；`error` 是"
                 "『装了但探针失败』—— 两者的处置不同，所以分开计。"
                 "本命令**不下载、不安装**：要装工具请人工装（会改环境、"
                 "涉许可证与供应链），装完重跑本命令即可。"),
    }

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`aether selfcheck`：**装完就能跑的自证**（秒级、离线、不写生产）。

═══ 为什么它不是"再写一套测试" ═══

`aether check` 跑的是 `tests/` 里的 68 份契约，全量 **33–37 分钟**。
那条路的前提是"你手上有这个仓库" —— 而 `pip install aether-v6` 的用户
没有（`tests/` 刻意不打进 wheel，见 `pyproject.toml` 的 package-data 注释）。
于是装完之后，用户唯一能问的问题是"这东西现在是不是好的"，
而系统**回答不了**：`aether check` 会报"0 份契约 ⇒ 判失败"。

⛔ "没验过"与"验过且没问题"在输出上最容易同形 —— 本项目为此立过
   第 14 条（三态分开）与第 75 条（写入成功 ≠ 可用）。所以这里不是
   "把 33 分钟缩短"，而是**补一个原本不存在的裁决**。

═══ 它与契约的关系：判据同源，不是第二份实现 ═══

每一项都调用**生产代码本身**（`paths.resolve_root_with_source` /
`Ledger.append+verify_chain` / `caps_candidate.validate` /
`statem.Rank.move` / `backup.create+restore`），只是把数据根换成临时
目录。它不重新定义"什么算过"，只是**把内核自己声称的不变式，
用内核自己跑一遍**。

⇒ 改判据时不需要改这里（它没有判据）；但这些函数的**行为**一改，
  它就会红 —— 那正是它作为回归闸的用途。

═══ 它继承 V6 的第一条命题，并把它拆成三个可分的项 ═══

    「每一次自我修改都必须能被独立地证伪、回放和撤销」

    `gate.teeth`       —— 可**证伪**：闸喂坏输入必须拒（反证，
                          不是"它没报错"）
    `ledger.chain`     —— 可**回放**：写进账本 → 链校验 → 篡改必须被发现
    `backup.roundtrip` —— 可**撤销**：备份 → 改坏 → 还原 ⇒ 逐字节相同

⛔ 三项里任何一项只报"ok"而不做反证，就等于恒真断言（第 9 条）。
   所以 `gate.teeth` / `ledger.chain` 都是**成对**的：好输入必须放行、
   坏输入必须拒绝；同一份数据改一个字节后必须报断链。

═══ 状态名为什么是 ok / broken / skip，而不是 pass / fail / skip ═══

第 58 条记过一例：`check.FAIL` 与 `doctor.FAIL` **同名不同物**，
合并会让"契约超时"被体检读成"没查"。这里继续**让值本身不同名**：
`check` 用 `pass/fail/timeout/error`，体检用 `ok/fail/skipped`，
自检用 `ok/broken/skip` —— 三个状态机的值域互不重叠，
误合并会在第一行就报错，而不是静默算错。

⛔ `skip ≠ ok`：没跑起来的项**不算通过**，但它也不让整条命令变红
   （"不可判别 ≠ 有罪"，第 67 条）。报告里逐项写明为什么不适用。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["OK", "BROKEN", "SKIP", "Check", "Ctx", "Result", "CHECKS", "run",
           "summarize", "main", "BUDGET_S"]

OK = "ok"
BROKEN = "broken"
SKIP = "skip"

#: 自检的墙钟预算。⛔ 它是一道**判据**：超过它就不该叫 selfcheck
#:    （用户不会等 33 分钟，也不会等 3 分钟）。
BUDGET_S = 60.0


@dataclass
class Check:
    """一项自检。

    `fn(ctx) -> (status, detail)`：**必须**返回三态之一，且 `detail` 在
    非 ok 时写清"为什么不适用 / 哪一步不一致"。⛔ 返回空理由一律当
    broken —— "没说话"不许被读成"没问题"。
    """
    key: str
    what: str
    fn: Callable[["Ctx"], Tuple[str, str]]


@dataclass
class Ctx:
    """一次自检的上下文（临时目录由 `run()` 建，`fn` 只许往里写）。"""
    root: Path            # 工作区根（**只读**：自检不许改生产）
    tmp: Path             # 临时目录（可随便写）
    pkg_root: Path        # 包位置（判"安装态"用）
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class Result:
    key: str
    what: str
    status: str
    detail: str = ""
    seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "what": self.what, "status": self.status,
                "detail": self.detail, "seconds": round(self.seconds, 3)}


# ══════════════════════════════════════════════════════════════════
# 各项自检
# ══════════════════════════════════════════════════════════════════

def _fake_install(tmp: Path) -> Path:
    """造一个"装进 site-packages"的包位置：有 `aether/paths.py`，**没有**
    `tasks/` 与 `tests/`。

    ⛔ 判据必须是"仓库资产在不在"而不是"路径里有没有 site-packages"：
       后者对 `pip install -e` 不成立（装的是仓库本身）。
    """
    sp = tmp / "site-packages"
    (sp / "aether").mkdir(parents=True, exist_ok=True)
    (sp / "aether" / "paths.py").write_text("# fake\n", encoding="utf-8")
    return sp


def _c_root_priority(c: Ctx) -> Tuple[str, str]:
    """五支优先级**逐支**验一遍，且验的是相对顺序（不是各自单独成立）。"""
    from .paths import (ROOT_FROM_ENV, ROOT_FROM_FLAG, ROOT_FROM_HOME,
                        ROOT_FROM_REPO, ROOT_FROM_WORKSPACE, data_dir,
                        paths as build_paths, resolve_root_with_source)

    def same(a: Any, b: Any) -> bool:
        """⛔ 必须比 `resolve()` 之后的值：macOS 的临时目录是
        `/var/folders/...`，而 `find_workspace()` 会 `resolve()` 成
        `/private/var/folders/...` ⇒ 直接比字符串会**永远不等**，
        而报出来的是"工作区没命中"（看起来像真 bug，其实是量具）。
        """
        try:
            return Path(a).resolve() == Path(b).resolve()
        except OSError:
            return Path(a) == Path(b)

    sp = _fake_install(c.tmp / "p1")
    cwd = c.tmp / "p1" / "empty"
    cwd.mkdir(parents=True, exist_ok=True)
    env = {"AETHER_ROOT": str(c.tmp / "from_env")}

    # ① flag 压过 env
    got, src = resolve_root_with_source(str(c.tmp / "from_flag"),
                                        pkg_root=sp, cwd=cwd, env=env)
    if src != ROOT_FROM_FLAG or not same(got, c.tmp / "from_flag"):
        return BROKEN, f"①--root 没赢：拿到 {src} {got}"

    # ② env 压过"安装态兜底"
    got, src = resolve_root_with_source(None, pkg_root=sp, cwd=cwd, env=env)
    if src != ROOT_FROM_ENV or not same(got, c.tmp / "from_env"):
        return BROKEN, f"②$AETHER_ROOT 没赢：拿到 {src} {got}"

    # ③ 仓库态（包位置就是仓库：三样资产都在）
    repo = c.tmp / "p1" / "repo"
    (repo / "aether").mkdir(parents=True, exist_ok=True)
    (repo / "aether" / "paths.py").write_text("# fake\n", encoding="utf-8")
    for name in ("tasks", "tests"):
        (repo / name).mkdir(exist_ok=True)
    got, src = resolve_root_with_source(None, pkg_root=repo, cwd=cwd, env={})
    if src != ROOT_FROM_REPO or not same(got, repo):
        return BROKEN, f"③仓库态没命中：拿到 {src} {got}"

    # ④ 工作区：向上找 `.aether`（站在子目录里也要找得到）
    ws = c.tmp / "p1" / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".aether").write_text("{}", encoding="utf-8")
    deep = data_dir(ws) / "sub"
    deep.mkdir(parents=True, exist_ok=True)
    got, src = resolve_root_with_source(None, pkg_root=sp, cwd=deep, env={})
    if src != ROOT_FROM_WORKSPACE or not same(got, ws):
        return BROKEN, f"④工作区没命中：拿到 {src} {got}（站在 {deep}）"

    # ⑤ 兜底：**绝不能**是包位置
    got, src = resolve_root_with_source(None, pkg_root=sp, cwd=cwd, env={})
    if src != ROOT_FROM_HOME:
        return BROKEN, f"⑤兜底没命中：拿到 {src} {got}"
    if same(got, sp) or sp.resolve() in got.resolve().parents:
        return BROKEN, f"⑤兜底落到了包目录里：{got}"

    # ⛔ ③ 必须排在 ④ 前面：仓库根**没有** `.aether`，若 ④ 先跑，
    #    `cd <仓库>/tasks` 会掉到兜底 ⇒ 同一台机器两个库。
    got2, src2 = resolve_root_with_source(None, pkg_root=repo,
                                          cwd=build_paths(repo)["tasks_root"],
                                          env={})
    if src2 != ROOT_FROM_REPO:
        return BROKEN, f"③④ 顺序错了：在仓库子目录里拿到 {src2} {got2}"

    # ⛔ 唯一的例外（r61 第二块）：`pip install -e .` 装的是仓库本身，
    #    站在**仓库外**的工作区里时必须由那个工作区说了算 ——
    #    否则两个项目混用仓库账本，而两边都"看着正常"。
    got3, src3 = resolve_root_with_source(None, pkg_root=repo, cwd=deep, env={})
    if src3 != ROOT_FROM_WORKSPACE or not same(got3, ws):
        return BROKEN, f"-e 装下工作区被仓库支遮蔽：拿到 {src3} {got3}"

    # ⛔ 而这个例外**只认找得到的 `.aether`**：找不到时仍落 ③ ——
    #    否则在临时目录里 spawn 子进程的契约会集体掉到 ⑤（测试写生产库）。
    bare = c.tmp / "p1" / "bare"
    bare.mkdir(parents=True, exist_ok=True)
    got4, src4 = resolve_root_with_source(None, pkg_root=repo, cwd=bare, env={})
    if src4 != ROOT_FROM_REPO:
        return BROKEN, f"例外放宽成「cwd 在外面就换根」：拿到 {src4} {got4}"
    return OK, ("①flag>②env>③repo>④workspace>⑤home；兜底不落包目录；"
                "cwd 在仓库外的工作区里时工作区优先")


def _c_root_no_pkg_write(c: Ctx) -> Tuple[str, str]:
    """安装态下**一次解析都不许落到包里**，且解析完包目录里不许多出东西。"""
    from .paths import looks_like_repo, resolve_root_with_source

    sp = _fake_install(c.tmp / "p2")
    before = sorted(p.relative_to(sp).as_posix() for p in sp.rglob("*"))
    if looks_like_repo(sp):
        return BROKEN, "装成 wheel 的包位置被判成了仓库 ⇒ 数据会写进 site-packages"
    got, src = resolve_root_with_source(None, pkg_root=sp,
                                        cwd=c.tmp / "p2", env={})
    after = sorted(p.relative_to(sp).as_posix() for p in sp.rglob("*"))
    if after != before:
        extra = [x for x in after if x not in before]
        return BROKEN, f"解析动作往包目录里写了东西：{extra[:4]}"
    if got == sp or sp.resolve() in got.resolve().parents:
        return BROKEN, f"数据根 = {got}（在包目录内，来源 {src}）"
    return OK, f"安装态解析到 {src}，包目录零写入"


def _c_ledger_roundtrip(c: Ctx) -> Tuple[str, str]:
    """写进账本 ⇒ 换一个实例从盘上读回 ⇒ 字段逐字相同。"""
    from .ledger import Ledger

    p = c.tmp / "l1.jsonl"
    led = Ledger(p)
    led.append("selfcheck.ping", {"n": 1, "tag": "甲"})
    led.append("selfcheck.ping", {"n": 2, "tag": "乙"})
    #: ⛔ **换实例**读：同一个对象可能只是把内存里的列表还给你，
    #:    那样"能读回"就与"盘上真写了"无关（第 75 条：写入 ≠ 可用）。
    ev = Ledger(p).events()
    if [e.seq for e in ev] != [1, 2]:
        return BROKEN, f"seq 不连续：{[e.seq for e in ev]}"
    if ev[1].payload.get("n") != 2 or ev[1].payload.get("tag") != "乙":
        return BROKEN, f"内容没原样读回：{ev[1].payload}"
    if not ev[0].hash or not ev[1].prev_hash:
        return BROKEN, "事件缺 hash / prev_hash ⇒ 链无从校验"
    return OK, "2 条事件跨实例读回一致，hash 与 prev_hash 都在"


def _c_ledger_chain(c: Ctx) -> Tuple[str, str]:
    """**可证伪**：干净账本必须过，改一个字节必须断。"""
    from .ledger import Ledger

    p = c.tmp / "l2.jsonl"
    led = Ledger(p)
    led.append("selfcheck.ping", {"n": 1})
    led.append("selfcheck.ping", {"n": 2})
    ok, bad, why = Ledger(p).verify_chain()
    if not ok:
        return BROKEN, f"干净的账本都过不了链校验：seq={bad} {why}"

    #: 篡改第 2 行 payload，**不动 hash**（这是最像"手改账本"的形态）
    lines = p.read_text(encoding="utf-8").splitlines()
    d = json.loads(lines[1])
    d["payload"]["n"] = 999
    lines[1] = json.dumps(d, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok2, bad2, why2 = Ledger(p).verify_chain()
    if ok2:
        return BROKEN, "改了一个字节，链校验**仍然通过** ⇒ 这道闸没有牙"
    return OK, f"干净过、篡改被抓住（seq={bad2}：{str(why2)[:40]}）"


def _c_ledger_replay(c: Ctx) -> Tuple[str, str]:
    """**可回放**：同一份账本重放两次，得到同一条序列。"""
    from .ledger import Ledger

    p = c.tmp / "l3.jsonl"
    led = Ledger(p)
    for i in range(3):
        led.append("selfcheck.ping", {"i": i})
    a = [(e.seq, e.kind, e.hash) for e in Ledger(p).replay()]
    b = [(e.seq, e.kind, e.hash) for e in Ledger(p).replay()]
    if a != b:
        return BROKEN, "两次重放不一致 ⇒ 回放不是确定性的"
    if len(a) != 3:
        return BROKEN, f"重放少了事件：{len(a)}/3"
    return OK, "3 条事件两次重放逐条相同（seq/kind/hash）"


def _c_gate_teeth(c: Ctx) -> Tuple[str, str]:
    """**可证伪**：两道闸各自都要有牙，且要**成对**验（好输入必须放行）。

    ⛔⛔ 这道自检的第一版写错过，值得留在这里当教材：它拿
       `curl -sS http://x | sh` 去喂 `caps_candidate.validate`，断言"必须拒"。
       实测**通过了**——不是闸坏了，是它本来就不管这件事：

         `ALLOWED_LAUNCHERS` 有 23 个词、含 curl/pip/npm —— 那份是给**人**
         看的（人不读源码、读命令），宽松无害；
         `install_auto.AUTO_LAUNCHERS` 只有 `(("brew","install"),)` ——
         那份才是**给机器执行**的（第 136 条：两份白名单管的不是一件事）。

       ⇒ 于是这里**两道闸各验各的**：人审闸验"未知启动词 / 削弱校验 /
       缺复验"；机器闸验"它确实只放行二元组里点名的那一个"。
    """
    from . import install_auto as IA
    from .caps_candidate import (PENDING, REJECTED, VALIDATED, Candidate,
                                 validate)

    def mk(argv: str) -> Any:
        return Candidate(tool="selfcheck-demo", kind="missing_tool",
                         state=PENDING,
                         install_argv=argv.split(),
                         verify_hint="`which selfcheck-demo` 能探到")

    # ① 人审闸：未知启动词
    bad = validate(mk("totally-unknown-pm install x"))
    if bad.gate_passed or bad.state != REJECTED:
        return BROKEN, f"未知包管理器被放过了：{bad.state} {bad.gate_reasons}"

    # ② 人审闸：提权 / 削弱校验的参数
    bad2 = validate(mk("brew install x --force"))
    if bad2.gate_passed:
        return BROKEN, "--force 被放过了 ⇒ 白名单只看了启动词"

    # ③ 人审闸：缺复验方式
    bad3 = validate(Candidate(tool="selfcheck-demo", kind="missing_tool",
                              state=PENDING,
                              install_argv=["brew", "install", "x"],
                              verify_hint=""))
    if bad3.gate_passed:
        return BROKEN, "缺 verify_hint 也被放行了 ⇒ 装完无法验收"

    # ④ 人审闸：好输入**必须**过（否则前三项可以靠"永远拒绝"作弊）
    good = validate(mk("brew install x"))
    if not good.gate_passed or good.state != VALIDATED:
        return BROKEN, f"合法提案被误拒：{good.state} {good.gate_reasons}"

    # ⑤ 机器闸：只许点名过的二元组（curl/pip/npm 一个都不许有）
    risky = {"curl", "wget", "sh", "bash", "pip", "pip3", "npm", "pnpm",
             "yarn", "python", "python3"}
    got = {str(p[0]) for p in IA.AUTO_LAUNCHERS}
    leaked = sorted(got & risky)
    if leaked:
        return BROKEN, (f"机器执行白名单里出现了 {leaked} ⇒ "
                        f"那是任意代码执行（curl … | sh / pip 跑 setup.py）")
    if not got:
        return BROKEN, "机器执行白名单是空的 ⇒ 这条路永远走不到（空集合不撒谎）"
    return OK, (f"人审闸：未知启动词/--force/缺复验全拒、合法 brew 放行；"
                f"机器闸只认 {'、'.join(' '.join(p) for p in IA.AUTO_LAUNCHERS)}")


def _c_state_no_downgrade(c: Ctx) -> Tuple[str, str]:
    """机器不许把「人的裁决」往回推（r60 D 的共用不变式）。

    ⛔ 验的是**生产里那两个 Rank 实例**（caps 与 cleanup），不是本地新建
       一个 —— 自建表只能证明 `Rank` 类没写错，证明不了两条链接对了。
    """
    from . import caps_candidate as CC
    from . import cleanup as CL

    if getattr(CC, "STATE_RANK", None) is None:
        return BROKEN, "caps_candidate 没有 STATE_RANK ⇒ 回退保护无处可接"
    pairs = [("caps", CC.STATE_RANK, CC.APPROVED, CC.PENDING)]
    if getattr(CL, "STATE_RANK", None) is not None and hasattr(CL, "APPLIED"):
        pairs.append(("cleanup", CL.STATE_RANK, CL.APPLIED, CL.PENDING))

    for name, rank, settled, lower in pairs:
        if rank.move(settled, lower) != settled:
            return BROKEN, (f"{name}：{settled} 能被推回 {lower}"
                            f" ⇒ 人的裁决会被机器撤销")
    #: 反向：已裁决**可以继续前进**（否则这道保护把链锁死）
    if CC.STATE_RANK.move(CC.APPROVED, CC.INSTALLED) == CC.APPROVED:
        return BROKEN, "caps：approved 前进不到 installed ⇒ 保护把链锁死了"
    return OK, f"{len(pairs)} 条链：回退被挡、前进放行"


def _c_backup_roundtrip(c: Ctx) -> Tuple[str, str]:
    """**可撤销**：备份 → 改坏 → 还原 ⇒ 逐字节相同。"""
    from . import backup
    from .ledger import Ledger
    from .paths import paths as build_paths

    root = c.tmp / "bk_ws"
    target = build_paths(root)["ledger"]
    target.parent.mkdir(parents=True, exist_ok=True)
    #: ⛔ 用**真账本**而不是手写一行 JSON：`create` 会顺手验副本的链，
    #:    手写行会让 chain_ok=False ⇒ 测的就不是"还原"而是"坏备份"。
    Ledger(target).append("selfcheck.backup", {"n": 1})
    want = hashlib.sha256(target.read_bytes()).hexdigest()

    dest = c.tmp / "bk_dest"
    man = backup.create(root, dest)
    #: 改坏它（真删内容，不是改 mtime）
    target.write_text("BROKEN\n", encoding="utf-8")
    backup.restore(man.path, root)
    got = hashlib.sha256(target.read_bytes()).hexdigest()
    if got != want:
        return BROKEN, f"还原后内容不同：{got[:12]} != {want[:12]}"
    #: ⛔ 备份自己也要能验：不验的话，"备份坏了"会在真正需要它那一刻
    #:    才暴露（那正是最不该发现它的时刻）。
    okv, probs, man2 = backup.verify(man.path)
    if not okv:
        return BROKEN, f"备份自校验失败：{probs[:2]}"
    if man.chain_ok is False:
        return BROKEN, f"副本的账本链断了：{str(man.chain_reason)[:80]}"
    return OK, (f"还原后 sha256 一致；备份自校验通过"
                f"（{len(man2.files) if man2 else 0} 项）")


def _c_seed_tasks(c: Ctx) -> Tuple[str, str]:
    """工作区里的任务集**真能跑一遍**（不是"文件在不在"）。

    ⛔ 没 `init` 过 ⇒ **skip**，不是 ok：没有任务集不等于任务集是好的。
    ⛔ 用 `verify_source` 跑**生产的那条执行链**（沙箱 + 逐条 got/expect），
       不是 `compile()` 一下就算数 —— "能编译"与"判据全过"差得远。
    """
    from .paths import TASKS_DIRNAME

    tasks = c.root / TASKS_DIRNAME
    envs = sorted(p for p in tasks.glob("*") if (p / "tests.json").is_file()) \
        if tasks.is_dir() else []
    if not envs:
        return SKIP, (f"{tasks} 下没有任务集 ⇒ 未验证"
                      "（装完先跑 `aether init` 才有一份可跑的示例）")
    from .environment import Environment, verify_source

    pick = envs[0]
    env = Environment.from_dir(pick)
    v = verify_source(env)
    if not v.passed:
        why = (v.error or "").strip() or "判据未全过"
        return BROKEN, f"{pick.name} 跑不过：{why[:120]}"
    return OK, f"{pick.name}：{len(v.checks)} 条判据全过"


def _c_deps_zero(c: Ctx) -> Tuple[str, str]:
    """**零运行期依赖**：除 `aether` 自己之外，没有第三方包被拉进来。

    ⛔ 这条不是洁癖（`pyproject.toml` 里写过理由）：自进化系统的判据必须
       离线可回归 —— 一旦依赖网络或模型，"零新红""非回归"就会随供给
       波动而失效。
    """
    try:
        import sysconfig
        purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
    except (KeyError, OSError):
        return SKIP, "取不到 site-packages 路径（虚拟环境异常）⇒ 未验证"

    leaked: List[str] = []
    for name, mod in list(sys.modules.items()):
        if name.split(".")[0] == "aether":
            continue
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            p = Path(f).resolve()
        except OSError:
            continue
        if purelib in p.parents:
            leaked.append(name)
    if leaked:
        return BROKEN, f"这些第三方包被拉进来了：{sorted(leaked)[:6]}"
    return OK, f"除 aether 自己外，{purelib.name} 下无第三方模块被加载"


def _c_console_page(c: Ctx) -> Tuple[str, str]:
    """控制台页面在**当前形态**下可达（仓库态 = `console/`，装态 = 包内快照）。

    ⛔ 为什么要单独一条：`console/` 与 `tasks/` 一样是**仓库资产**，
       不会自动进 wheel。r61 实测过后果 —— 装完之后 `aether ui` 直接
       起不来（报 `site-packages/console/index.html` 不存在，而 API 是好的）。
       `tasks/` 那条有 `seed.tasks` 兜着，控制台此前**一条断言都没有**，
       所以缺了也只能等用户撞上。
    ⛔ 只看"目录在不在"不够：真出过"目录在、页面是空的"（打包点错了 glob
       ⇒ 只带进一个空目录条目）⇒ 必须验到 **index.html 还是非空的**。
    """
    from .paths import console_dir

    try:
        d = console_dir(c.pkg_root)
    except OSError as exc:                       # pragma: no cover - 防御
        return BROKEN, f"控制台目录解析失败：{exc}"
    page = d / "index.html"
    if not page.is_file():
        return BROKEN, (f"找不到 {page}（仓库那份与控制台快照都没有）"
                        "⇒ `aether ui` 会起不来，API 虽然还能用")
    try:
        size = page.stat().st_size
    except OSError as exc:                       # pragma: no cover - 防御
        return BROKEN, f"读不到 {page}：{exc}"
    if size < 1024:
        return BROKEN, f"{page} 只有 {size} 字节（空页面 ⟹ 控制台打开是白屏）"
    return OK, f"{page}（{size // 1024} KB）"


#: 检查项。⛔ 顺序固定（报告可逐次对比）；新增项加在末尾。
CHECKS: Tuple[Check, ...] = (
    Check("root.priority", "工作区根的五支优先级按序生效", _c_root_priority),
    Check("root.not_pkg", "安装态不把数据根指回包目录", _c_root_no_pkg_write),
    Check("ledger.roundtrip", "写进账本能跨实例原样读回", _c_ledger_roundtrip),
    Check("ledger.chain", "链校验能发现被篡改的一行（可证伪）", _c_ledger_chain),
    Check("ledger.replay", "两次重放逐条相同（可回放）", _c_ledger_replay),
    Check("gate.teeth", "闸拒绝坏提案、放行好提案（成对）", _c_gate_teeth),
    Check("state.no_downgrade", "机器不许把已裁决的状态往回推",
          _c_state_no_downgrade),
    Check("backup.roundtrip", "备份→改坏→还原逐字节相同（可撤销）",
          _c_backup_roundtrip),
    Check("seed.tasks", "工作区任务集能真跑一遍", _c_seed_tasks),
    Check("console.page", "控制台页面在装出来的包里也可达", _c_console_page),
    Check("deps.zero", "没有第三方依赖被拉进来", _c_deps_zero),
)


# ══════════════════════════════════════════════════════════════════
# 驱动
# ══════════════════════════════════════════════════════════════════

def run(root: Optional[Any] = None, *, only: Optional[str] = None,
        timeout_s: float = BUDGET_S,
        pkg_root: Optional[Any] = None) -> Dict[str, Any]:
    """跑一遍自检。**不写生产**：所有落盘都在临时目录里。

    `root` 只用于**读**（"工作区里有没有任务集"），不改它一个字节。
    `pkg_root` 可注入：契约要能模拟"装进 site-packages"的包位置。
    """
    from .paths import ROOT as PKG_ROOT, resolve_root

    pkg = Path(pkg_root) if pkg_root is not None else PKG_ROOT
    ws = Path(root).expanduser() if root else resolve_root(pkg_root=pkg)
    t0 = time.time()
    results: List[Result] = []
    with tempfile.TemporaryDirectory(prefix="aether-selfcheck-") as td:
        tmp = Path(td)
        for i, chk in enumerate(CHECKS):
            if only and only not in chk.key:
                continue
            ctx = Ctx(root=ws, tmp=tmp / f"c{i:02d}", pkg_root=pkg)
            ctx.tmp.mkdir(parents=True, exist_ok=True)
            t1 = time.time()
            try:
                status, detail = chk.fn(ctx)
            except Exception as e:                       # noqa: BLE001
                status = BROKEN
                detail = (f"{type(e).__name__}: {e} | "
                          + " / ".join(
                              traceback.format_exc().strip().splitlines()[-2:]))
            if status not in (OK, BROKEN, SKIP):
                status, detail = BROKEN, f"自检项返回了不认识的状态 {status!r}"
            if not str(detail or "").strip():
                status, detail = BROKEN, "自检项没有给出理由（沉默 ≠ 通过）"
            results.append(Result(chk.key, chk.what, status, detail,
                                  time.time() - t1))
    secs = time.time() - t0
    n = {k: sum(1 for r in results if r.status == k) for k in (OK, BROKEN, SKIP)}
    over = secs > timeout_s
    return {
        "root": str(ws),
        "pkg_root": str(pkg),
        "n": len(results),
        "counts": n,
        "seconds": round(secs, 2),
        "budget_s": timeout_s,
        "over_budget": over,
        "results": [r.to_dict() for r in results],
        #: ⛔ 三态：**broken 才红**；skip 不红但必须可见；
        #:    超预算也算红 —— 一个 5 分钟的自检没人跑，等于不存在。
        "ok": n[BROKEN] == 0 and not over,
        "exit_code": 0 if (n[BROKEN] == 0 and not over) else 1,
    }


def summarize(rep: Dict[str, Any]) -> str:
    c = rep["counts"]
    head = "✅ 自检通过" if rep["ok"] else "❌ 自检未通过"
    lines = [f"{head}：{rep['n']} 项 ok={c[OK]} broken={c[BROKEN]} "
             f"skip={c[SKIP]}（{rep['seconds']}s / 预算 {rep['budget_s']:.0f}s）"]
    for r in rep["results"]:
        mark = {OK: "✅", BROKEN: "❌", SKIP: "⚠️"}[r["status"]]
        lines.append(f"  {mark} {r['key']:<18} {r['detail']}")
    if rep["over_budget"]:
        lines.append(f"⛔ 超出预算 {rep['budget_s']:.0f}s ⇒ 用户不会跑它，"
                     f"等于没有这道闸")
    if c[SKIP]:
        lines.append(f"ℹ️ {c[SKIP]} 项**未验证**（skip ≠ 通过）")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:       # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(
        prog="aether.selfcheck",
        description="自检：装完就能跑的不变式验收（秒级、离线、不写生产）")
    ap.add_argument("--root", default="", help="工作区根（默认按五支优先级解析）")
    ap.add_argument("--only", default="", help="只跑 key 含该子串的项")
    ap.add_argument("--json", action="store_true", help="输出结构化报告")
    ns = ap.parse_args(argv)
    rep = run(ns.root or None, only=ns.only or None)
    print(json.dumps(rep, ensure_ascii=False, indent=2)
          if ns.json else summarize(rep))
    return int(rep["exit_code"])


if __name__ == "__main__":                              # pragma: no cover
    sys.exit(main())

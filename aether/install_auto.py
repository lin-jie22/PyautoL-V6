#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""机器自审**安装**（r60 G）：把「人点 --yes」换成「可复现的判据 + 自动回滚」。

═══ 它替代的是什么，以及为什么可以替代 ═══

`caps_candidate` 的原始设计写得很硬：**人批准**（`--yes`）、**人执行**安装。
两条理由各自成立与否要分开看 —— 这是本模块存在的全部依据：

  · 「人**执行**」⇒ 现在由本模块执行。理由是执行本身没有任何判断力：
    命令原文是清单里写好的、可枚举的、可回滚的。把人耗在"复制粘贴一条
    已经确定的命令"上，只会让这条链永远停在 approved。
  · 「人**批准**」⇒ 换成**当场重跑的四道闸 + 清单逐字比对 + 可回滚 +
    装后复验**。⛔ 这不是"把闸拆了"，是换一个更细的裁决者：
    原来人只看一眼 JSON 就点 yes，现在要过五道机器判据。

⛔ 第 132 条（机器自审 = 换裁决者，不是删闸）：新拿到的权限必须用
   **等强度的另一个约束**顶上。人审原本挡住的五件事，逐条对应如下：

| 人审原来挡住的 | 机器换成什么 |
|---|---|
| 「要不要装这个」 | 四道闸**当场重跑**（探针复核 / 启动词 / 非回归 / 可复验），不读档案里的旧读数 |
| 「装的是什么命令」 | `install_argv` 必须与 `KNOWN_TOOLS` 的**静态声明逐字相同** |
| 「谁负责」 | `approved_by="auto"`，事后一眼分开机器批的与人批的 |
| 「装错了怎么办」 | 必须能**推导**出卸载命令；装后探不到 ⇒ 自动卸载 + 再探确认 |
| 「会不会跑任意代码」 | 自动执行白名单按 **(启动词, 动词) 二元组逐个点名**，只有 `brew install` |

═══ 为什么「与清单逐字相同」是这套设计的地基 ═══

`data/candidates.json` 是**缓存**（`caps_candidate` 的文档里写死了：
事实源是账本），而缓存文件可以被手改、被别的进程写、被一次坏的合并弄脏。
如果执行的是**档案里的** argv，那么"谁改了那个文件"就等于"谁决定了
系统要跑什么命令" —— 一道闸都没有。

⇒ 执行前必须 `cand.install_argv == list(tool.install_argv)`，
   不一致就拒绝。⛔ 记账时把两份都写进证据：只报"不一致"而不报
   "分别是什么"，等于让人自己去猜差异源（第 45 条）。

⛔⛔ 一条**实测**出来的补充（r60 G 写契约时踩到的）：这道比对是**第二层**，
   第一层在 `cli._mk()` —— 它对 `missing` 的工具会用**清单的 argv
   重建候选**，于是档案里被改过的 argv 在多数情况下**根本到不了**这里。
   ⛔ 但第二层仍然必须有：`merge()` 在"新候选没有 argv"时会**回填**
   档案里的旧 argv（见 `caps_candidate.merge`）—— 对清单里
   **没有** install_argv 的工具（本机 `objdump` / `otool` 就是），
   档案里的 argv 就是唯一来源 ⇒ 那时这行比对是唯一的挡板。
   （实测经过：契约里我原本用"改档案 argv"来验证这道闸，结果 CLI 从清单
   重建了 argv，真去跑了 `brew install radare2`，被 120s 超时杀掉；
   复查环境零改动。⇒ 教训是**验证一道闸之前，先确认它前面没有别的闸
   把它短路掉**，否则你测的其实是另一条路。）

═══ 为什么执行白名单必须比提示白名单窄 ═══

`caps_candidate.ALLOWED_LAUNCHERS` 有 23 个词，含 `curl` / `wget` /
`git` / `python3` / `pip` / `npm`。那份名单管的是**给人看的命令**：
人不看源码，看命令；宽松一点不会造成执行风险。

这份名单管的是**机器真的会跑**什么，所以：

  · `curl ... | sh`、`python3 x.py`、`git clone && make` —— 都是**任意代码执行**；
  · `pip install` 会跑 `setup.py`；`npm install` 会跑 postinstall 脚本；
  · 而 `brew install <formula>` 也跑 formula 的 post-install，但它的
    **名字与版本由 Homebrew 仓库锁定**，且**卸载是干净的**（`brew uninstall`）。

⇒ 只留 `brew install` 一个二元组。⛔ 逐个点名，不用全称量词
   （第 59 条）：写「包管理器都行」会让下一个人以为 `pip` 也在里面。

═══ 三态（与 `autonomy` 同一套词）═══

  `ok`          闸过、装成、复验探到（或本来就在 ⇒ `noop`）
  `rejected`    闸红 / 装失败 / 装完探不到（后者已自动卸载）
  `unverified`  跑不起来（没有 argv / 没有卸载路径 / 包管理器不存在 / 演练）

⛔ `unverified` 绝不并入 `ok`（第 27 条）。「没裁决」与「通过」同形是这个
   项目最贵的那类事故。
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import caps_candidate as CC
from . import discover as D

#: 装成（或本来就在）
OK = "ok"
#: 被拒（闸红 / 与清单不符 / 装失败 / 装完探不到）
REJECTED = "rejected"
#: 没有裁决（跑不起来 / 演练 / 无回滚路径）
UNVERIFIED = "unverified"

#: 机器自审时写进 `approved_by` 的值。
#: ⛔ 刻意**不是**人名：事后要能一眼分开「人批的」与「机器判据批的」，
#:    否则审计时两种责任同形（与 `autonomy.AUTO_BY` 同一字面值，
#:    由契约钉住两者相等，防漂移）。
AUTO_BY = "auto"

#: ⛔⛔ 允许**自动执行**的 (启动词, 动词) 二元组。见模块文档：
#:    这份名单与 `caps_candidate.ALLOWED_LAUNCHERS` 管的不是同一件事，
#:    后者给**人**看，前者给**机器**跑。往这里加一个词 = 给系统加
#:    一种"能自己跑的命令形态"，要连带想清楚三件事：
#:      ① 它会不会执行第三方代码（`pip`/`npm` 会）；
#:      ② 它有没有**干净可推导**的卸载方式（`curl -o` 没有）；
#:      ③ 装完之后本系统的探针能不能找到它（找不到就没法复验 ⇒ 不许自动装）。
AUTO_LAUNCHERS: Tuple[Tuple[str, str], ...] = (
    ("brew", "install"),
)

__all__ = ["OK", "REJECTED", "UNVERIFIED", "AUTO_BY", "AUTO_LAUNCHERS",
           "InstallVerdict", "uninstall_argv", "machine_install",
           "record_verdict"]


@dataclass
class InstallVerdict:
    """一次机器自审安装的完整结论。"""
    state: str
    reasons: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    #: 是否**本来就已经装好**（什么都没做）。⛔ 与 `ok` 分开记：
    #: 「做成了」与「无事可做」在处置上不同（一个是新装、一个去看现状）。
    noop: bool = False
    #: 是否只演练（**命令一条都没执行**）
    dry_run: bool = False
    #: 是否已回滚（True 表示环境已还原到执行前）
    rolled_back: bool = False
    #: 候选的最终状态（ok ⇒ installed；rejected ⇒ rejected；其余 ⇒ 不变）
    cand_state: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "reasons": list(self.reasons),
                "evidence": dict(self.evidence), "noop": self.noop,
                "dry_run": self.dry_run, "rolled_back": self.rolled_back,
                "cand_state": self.cand_state}


def _run(argv: Sequence[str], *, timeout_s: float
         ) -> Tuple[int, str, str, bool]:
    """真正执行子进程的那一层。⛔ 全模块**唯一**的执行点。

    抽成一个模块级函数不是为了好测，是为了让「执行」在代码里只有一处 ——
    只有一处，守卫点才唯一（守卫散在四处时，下次改只会改到看得见的那几处）。

    返回 `(rc, stdout, stderr, timed_out)`；跑不起来 ⇒ rc=127，
    超时 ⇒ rc=124（与 shell 约定同值，避免自己发明一套码）。
    """
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True,
                           errors="replace", timeout=float(timeout_s))
        return int(p.returncode), (p.stdout or ""), (p.stderr or ""), False
    except subprocess.TimeoutExpired as ex:
        out = ex.stdout if isinstance(ex.stdout, str) else ""
        err = ex.stderr if isinstance(ex.stderr, str) else ""
        return 124, out, err, True
    except OSError as ex:                                  # noqa: BLE001
        return 127, "", f"{type(ex).__name__}: {ex}", False


def uninstall_argv(argv: Sequence[str]) -> List[str]:
    """从安装命令**推导**回滚命令；推不出来 ⇒ 空表（**不许自动装**）。

    ⛔ 这条是"回滚能力"的判据，不是顺手写的工具函数：没有卸载路径的
       安装命令，一旦装上就是**不可逆**的环境改动，而环境改动没法像
       源码那样快照（`srcmut` 的做法在这里用不上）。
       ⇒ 推不出来就**不装**，把命令留给人。
    ⛔ 只认**能一一映射**的形态：`brew install [--cask] X` ⇒
       `brew uninstall [--cask] X`。`--cask` 必须原样带过去 ——
       丢掉它会去 core 里找一个同名 formula，找不到就报错，
       而"报错"会被读成"回滚失败"。
    """
    args = [str(a) for a in argv]
    if len(args) < 3 or args[0] != "brew" or args[1] != "install":
        return []
    rest = args[2:]
    flags = [a for a in rest if a.startswith("-")]
    names = [a for a in rest if not a.startswith("-")]
    if len(names) != 1:
        return []
    return ["brew", "uninstall", *flags, names[0]]


def machine_install(cand: Any, tool: Any, *,
                    dry_run: bool = False,
                    timeout_s: float = 1800.0,
                    probe_fn: Optional[Callable[[Any], Any]] = None,
                    run: Optional[Callable[..., Tuple[int, str, str, bool]]] = None
                    ) -> InstallVerdict:
    """对一条候选做**机器自审安装**。返回三态结论（不抛异常）。

    `probe_fn` / `run` 不传 ⇒ 用生产实现。
    ⛔ 两个参数都只给**契约**用（把网络与"真装一次"挡在测试之外），
       CLI **一律不传** —— 传了就等于给外部一个"伪造装成功"的入口。
       同 `autonomy.auto_apply(runner=...)` 的边界。

    ⛔ 判据顺序固定，逐项记因（只报第一个失败项会让人修完一个又冒一个）：
       ① 有没有安装命令  ② 与清单是否逐字相同  ③ 是不是可自动执行的形态
       ④ 有没有回滚路径  ⑤ 四道闸当场重跑  ⑥ 执行 → 装后复验 → 失败回滚
    """
    probe_fn = probe_fn or D.probe
    run = run or _run
    cand_state0 = str(getattr(cand, "state", ""))
    ev: Dict[str, Any] = {"tool": str(getattr(tool, "name", "")),
                          "state_before": cand_state0}

    # ① 有没有安装命令
    argv = [str(a) for a in (getattr(cand, "install_argv", None) or [])]
    ev["install_argv"] = argv
    if not argv:
        return InstallVerdict(UNVERIFIED, [
            "候选没有 install_argv ⇒ 没有可执行的命令",
            "（这类候选是 unwrapped_capability：工具已在，缺的是参数声明，"
            "不是安装）"], ev, cand_state=cand_state0)

    # ② 与清单静态声明逐字比对
    declared = [str(a) for a in (getattr(tool, "install_argv", None) or [])]
    ev["declared_argv"] = declared
    if argv != declared:
        return InstallVerdict(REJECTED, [
            f"安装命令与清单声明不一致：档案={argv}｜清单={declared}",
            "档案是**缓存**（事实源是账本）⇒ 不许它决定系统跑什么命令。"
            "重跑 `propose` 重建档案，或检查清单"],
            ev, cand_state=cand_state0)

    # ③ 可自动执行的形态（二元组逐个点名）
    pair = tuple(argv[:2])
    ev["launcher"] = list(pair)
    if pair not in AUTO_LAUNCHERS:
        allowed = "、".join(" ".join(p) for p in AUTO_LAUNCHERS)
        return InstallVerdict(REJECTED, [
            f"启动形态 {pair[0]!r} {pair[1] if len(pair) > 1 else ''!r} "
            f"不在**自动执行**白名单里（只允许：{allowed}）",
            "注意：`caps_candidate.ALLOWED_LAUNCHERS` 更宽，那份是给**人**看的；"
            "机器执行会跑第三方代码，两份名单管的不是同一件事"],
            ev, cand_state=cand_state0)
    if not shutil.which(pair[0]):
        return InstallVerdict(UNVERIFIED, [
            f"{pair[0]} 不在这台机器上 ⇒ 跑不起来（这不是「该不该装」，"
            f"是「用什么东西装」）"], ev, cand_state=cand_state0)

    # ④ 回滚路径
    un = uninstall_argv(argv)
    ev["uninstall_argv"] = un
    if not un:
        return InstallVerdict(UNVERIFIED, [
            f"从 {argv} 推导不出卸载命令 ⇒ 装上就**不可逆**，"
            f"环境改动没法快照 ⇒ 不许自动装（命令留给人）"],
            ev, cand_state=cand_state0)

    # ⑤ 探针复核 + 四道闸（**当场重跑**，不读档案里的旧读数）
    pr = probe_fn(tool)
    ev["probe_before"] = _probe_dict(pr)
    if str(getattr(pr, "status", "")) == "found":
        #: ⛔ 已经装好 ⇒ 不改环境。但**不许静默返回 ok**：
        #:    「本来就在」与「刚装上」的处置不同，用 `noop` 分开记。
        return InstallVerdict(OK, [
            f"{getattr(tool, 'name', '')} 已经装好"
            f"（{getattr(pr, 'path', '') or '探针 found'}）⇒ 不执行任何命令"],
            ev, noop=True, cand_state=cand_state0)
    if str(getattr(pr, "status", "")) == "error":
        return InstallVerdict(UNVERIFIED, [
            f"探针报 error（{getattr(pr, 'detail', '')}）⇒ 这是「去修」"
            f"不是「去装」：装一遍解决不了它"],
            ev, cand_state=cand_state0)

    cand = CC.validate(cand, probe=pr)
    ev["gate"] = {"passed": bool(getattr(cand, "gate_passed", False)),
                  "reasons": list(getattr(cand, "gate_reasons", []) or [])}
    if not getattr(cand, "gate_passed", False):
        return InstallVerdict(REJECTED, [
            "四道闸未通过 ⇒ 不执行（批一条已知不过闸的提案等于把闸当摆设）"]
            + [f"⇒ {r}" for r in (getattr(cand, "gate_reasons", None) or [])],
            ev, cand_state=str(getattr(cand, "state", "")))

    # ⑥ 执行
    if dry_run:
        ev["dry_run"] = True
        return InstallVerdict(OK, [
            "演练：闸全过、清单一致、可回滚，**命令未执行**（环境零改动）",
            "去掉 --dry-run 就会真装"], ev, dry_run=True,
            cand_state=str(getattr(cand, "state", "")))

    #: ⛔ `yes=True` 在这里等于「调用方（上面那五道判据）已担责」，
    #:    不再是「人点过」。`approved_by="auto"` 把这件事写进账本。
    cand = CC.approve(cand, by=AUTO_BY, yes=True)
    if str(getattr(cand, "state", "")) != CC.APPROVED:
        return InstallVerdict(REJECTED, [
            "机器批准未推进（状态没到 approved）："
            + str((getattr(cand, "gate_reasons", None) or ["?"])[-1])],
            ev, cand_state=str(getattr(cand, "state", "")))

    t0 = time.time()
    rc, out, err, timed = run(argv, timeout_s=timeout_s)
    ev["exec"] = {"rc": rc, "timed_out": bool(timed),
                  "seconds": round(time.time() - t0, 1),
                  "stdout_tail": _tail(out), "stderr_tail": _tail(err),
                  "argv": argv}
    if timed:
        ev["exec"]["note"] = f"超时（{timeout_s}s）⇒ 可能装了一半，必须回滚"

    # ⑦ 装后复验（**只认探针读数**，不认 rc）：rc=0 也可能装到了探不到的地方
    pr2 = probe_fn(tool)
    ev["probe_after"] = _probe_dict(pr2)
    if str(getattr(pr2, "status", "")) == "found":
        cand = CC.verify(cand, pr2)
        return InstallVerdict(OK, [
            f"装成并复验通过：rc={rc}，探到 "
            f"{getattr(pr2, 'path', '')}（{getattr(pr2, 'version', '') or '无版本'}）",
            f"回滚方式（如需）：{' '.join(un)}"],
            ev, cand_state=str(getattr(cand, "state", "")))

    # ⑧ 没装成 ⇒ 回滚，并**再探一次**确认（判据是客观读数，不是 rc）
    urc, uout, uerr, _utimed = run(un, timeout_s=timeout_s)
    pr3 = probe_fn(tool)
    ev["rollback"] = {"rc": urc, "argv": un,
                      "stdout_tail": _tail(uout), "stderr_tail": _tail(uerr),
                      "probe_after_rollback": _probe_dict(pr3)}
    rolled_back = str(getattr(pr3, "status", "")) != "found"
    #: ⛔⛔ 原因必须**按 rc 分叉**（第 45 条：两个证人不一致先找差异源）。
    #:    r60 G 生产实测（2026-10-20 15:17）：`brew install` 在本机的
    #:    执行宿主里被 `sandbox-exec: sandbox_apply: Operation not permitted`
    #:    挡掉，rc=1 且一秒都没装 —— 而当时那句文案写的是「装完仍探不到 ⇒
    #:    这个工具装完本系统的探针找不到它」，把**执行失败**说成了**落点问题**。
    #:    两条路的下一件事完全不同：一条去查执行环境/网络，一条去补
    #:    `discover` 的落点规则。混成一句话，等于让人去修错的地方。
    if rc != 0:
        why = [f"执行失败：rc={rc}{'（超时）' if timed else ''} → 命令没跑成",
               f"stderr 末行：{_last_line(err) or '（空）'}",
               "⛔ 这不是探针落点问题 —— 装都没装上，谈不上探不探得到；"
               "先解决执行环境（权限 / 网络 / 沙箱），或由人在普通终端里跑"]
    else:
        why = [f"装完仍探不到：探针={getattr(pr2, 'status', '')}（但 rc=0）",
               "这个工具装完本系统的探针找不到它 ⇒ 它的自动装不该开放"
               "（要开先让 `discover` 认识它的落点）"]
    if rolled_back:
        #: ⛔ 判据是**探针**，卸载命令的 rc 只作参照：`brew uninstall` 对
        #:    没装上的包返回非 0（"No such keg"），把那个 rc 念成"回滚失败"
        #:    会让一次干净的回滚看起来像事故。
        why.append(f"探针确认环境已还原（卸载命令 `{' '.join(un)}` rc={urc}；"
                   f"rc≠0 通常表示本来就没装上，判据是探针不是 rc）")
    else:
        why.append(f"⛔ 回滚后仍探得到 ⇒ 环境**可能已被改动**，"
                   f"请人工确认：{' '.join(un)}")
    #: ⛔⛔ 「这次没装成」**不是**「这个提案被否决」（第 45 条：两个证人不
    #:    一致先找差异源）。批准说的是"可以装"，不是"装得上"。
    #:    ⇒ 状态**退回 approved**（机器批准仍在，下次可再试），
    #:      这一轮的失败由 `InstallVerdict.state=rejected` 单独记。
    #:    ⛔ 别用 `CC.verify()` 的失败分支：它会把状态打成 `rejected`，
    #:      于是一次网络抖动就吃掉了批准 —— 与 r60 D（过闸降级）同族，
    #:      只是这次的凶手是"复验"。
    cand.state = CC.STATE_RANK.move(cand.state, CC.APPROVED)
    ev["state_after"] = str(cand.state)
    return InstallVerdict(REJECTED, why, ev, rolled_back=rolled_back,
                          cand_state=str(cand.state))


def _probe_dict(pr: Any) -> Dict[str, Any]:
    """探针结果转 dict（兼容没有 `to_dict` 的假探针，契约会用到）。"""
    fn = getattr(pr, "to_dict", None)
    if callable(fn):
        try:
            return dict(fn())
        except Exception:                                  # noqa: BLE001
            pass
    return {k: getattr(pr, k, None)
            for k in ("tool", "status", "path", "version", "detail")}


def _tail(text: str, n: int = 800) -> str:
    """取尾部（安装输出前几千行都是下载进度，有用的错误在最后）。"""
    s = str(text or "")
    return s[-n:] if len(s) > n else s


def _last_line(text: str) -> str:
    """取最后一行非空输出。

    ⛔ 只取一行而不是整段：`brew` 的 stderr 可能有几十行（下载进度 +
    警告 + 错误），而"为什么失败"几乎总在最后一行。把整段塞进 reason
    会让闸的理由变成日志，读者反而找不到那句关键的话。
    """
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def record_verdict(ledger: Any, verdict: InstallVerdict,
                   *, actor: str = "caps") -> Optional[int]:
    """把一次机器自审安装的**结论**记进账本。

    ⛔ 记的是**完整命令原文 + rc + 探针前后读数**：事后要能回答
       「这行命令是谁在什么时候让系统跑的」，而不只是「状态变了」。
    ⛔ 没挂账本 ⇒ 返回 `None` 且**不假装记过**（同 `caps_candidate.record`）。
    """
    if ledger is None:
        return None
    return ledger.append("cap.install_auto", verdict.to_dict(),
                         actor=actor).seq

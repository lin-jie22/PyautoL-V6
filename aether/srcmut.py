#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""源码变异：**带指纹的编辑** + **在副本里对拍**。

═══ 它为什么存在 ═══

"让系统能提议改自己的源码"与"让系统改 harness"是两种风险量级：

    · 改 harness —— 纯数据（frozen + `replace`）⇒ 回滚 = 指针回退，
      而判据住在 harness **之外** ⇒ 裁决独立于被测对象。
    · 改源码   —— 源码里**住着判据层**（`judge.JUDGE_MODULES` 那五个模块）。
      让循环改它自己的判据 ⇒ "非回归闸通过"这个结论不再独立于被测对象
      ⇒ **自证**。而且删错了**不可逆**（账本只能留痕，不能还原）。

⇒ 所以本模块**不做**「自动改生产源码」。它只做两件确定的事：

    ① 把一次编辑表达成**带内容指纹**的结构 —— 行号会漂移，指纹不会；
    ② 在**副本**里应用它，用**冻结的契约**做删前/删后对拍，
       给出"这个编辑是否安全"的裁决。

剩下的一步（真的改源码）由人执行，与 `caps_candidate` 同族。

═══ 三条硬约束（都是**构造上**的，不是「记得别这么做」）═══

    ① **生产根不可写**：`apply_edits` 对生产根**直接拒绝**（不是警告后照做）。
       ⇒ "清理通道"在结构上够不到生产源码，与 `judge` 锁判据同形态。
    ② **指纹不匹配 ⇒ 不许应用**：行号漂了 ⇒ 区间内容变了 ⇒ 这次编辑指向的
       已经不是它当初**证明过**的那段代码 ⇒ fail-closed。
    ③ **没有对拍裁决 ⇒ 不许放行**：`probe` 不输出"我猜没问题"。
       跑不动就是跑不动（记 `unverified`），不冒充"验证通过"。

═══ 判据层排除 ═══

`SOURCE_PROTECTED` 里的模块**扫都不扫**（不是"扫出来再拦掉"）。
⛔ **`srcmut` 自己也在名单里** —— 清理能删掉自己的闸，等于没有闸。
   这与"判据模块不得出现在可变异层"是同一条纪律换了个落点。

⛔ **对拍跑的是冻结契约，不是本模块自己下的结论**：`srcmut` 只负责
   **执行与比对**，它不定义什么是对的 ⇒ 判决权仍在被测对象之外。
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

from .ledger import content_hash
from .paths import ROOT

__all__ = ["SOURCE_PROTECTED", "Edit", "from_lines", "verify", "apply_edits",
           "probe", "select_contracts", "contract_runner", "EDIT_ID_PREFIX"]

#: ⛔ 这些模块**扫都不扫**（不是"扫出来再拦掉"）。
#:    与 `judge.JUDGE_MODULES` 同源，加上它自己与 `judge`/`srcmut`。
SOURCE_PROTECTED = frozenset({
    "verifier",       # 奖励的唯一定义
    "environment",    # 执行判据的落地
    "nongression",    # 准入闸
    "generalization", # 泛化闸
    "repair",         # 难度分层
    "judge",          # 判据集身份（改它 ⇒ 历史裁决不可比）
    #: ⛔⛔ **闸自己**：清理能删掉自己的闸 = 没有闸。
    "srcmut",
    #: ⛔ 判据层的读数来源：改 `selfref` 会让"自指负载"这个**尺子**漂移，
    #:    而减负轴的全部判据都建立在这把尺子上（改尺子 ⇒ Δ 不可比）。
    "selfref",
})

#: 指纹/寻址前缀长度（与 `genome._ID_PREFIX` 同口径）。
EDIT_ID_PREFIX = 32

#: 对拍副本**不复制**的东西。⛔ 不是"省地方"，是**时间与正确性**：
#:   · `.git` / `.venv` —— 几百 MB，复制一次几十秒，而对拍跟它无关；
#:   · `data` / `backups` —— **运行期产物**：两边都复制 ⇒ 两侧内容不同
#:     （删前跑过一次会写数据）⇒ 比对结果被运行污染。
#:     ⛔ 忽略它是安全的：契约用 `tmp_path` 自建数据，不依赖仓库里的产物。
COPY_IGNORE = (".git", ".venv", "__pycache__", ".pytest_cache", "data",
               "backups", ".workbuddy", "*.pyc", "*.db", "*.jsonl")

#: 支持的编辑形态。⛔ 封死：**只许删区间**。
#:    "改源码"这个词听起来宽，但能**证明安全**的只有删除：
#:    插入/改写会改变行为，而"改了但行为等价"需要真正的程序分析。
ALLOWED_OPS = frozenset({"delete_range"})


@dataclass(frozen=True)
class Edit:
    """一次**机器可应用**的源码编辑。

    ⛔ 为什么带 `sha256`：行号会漂（有人在前面插了一行 ⇒ 区间错位），
       而指纹不会。应用时校验指纹 ⇒ 漂了就拒绝，绝不"差不多就应用"。
       ⛔ 指纹取**被删区间原文**，不是整个文件 —— 整个文件的指纹会让
       "文件任何一处变了"都拒绝，那等于只能应用"刚刚生成"的编辑，
       与"提议 → 过闸 → 之后由人执行"这个跨时间流程冲突。
    """
    file: str                   # 相对仓库根的 posix 路径
    start_line: int = 0         # 1-based，含
    end_line: int = 0           # 1-based，含
    sha256: str = ""
    op: str = "delete_range"

    def __post_init__(self) -> None:
        if not str(self.file).strip():
            raise ValueError(f"Edit.file 必须是非空字符串，实得 {self.file!r}")
        if self.op not in ALLOWED_OPS:
            raise ValueError(
                f"不支持的 op={self.op!r}（允许：{sorted(ALLOWED_OPS)}）⇒ "
                f"能**证明安全**的只有删除区间，插入/改写需要程序分析")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError(
                f"非法行区间 {self.start_line}-{self.end_line}"
                f"（要求 1 <= start <= end）")

    def identity(self) -> Dict[str, Any]:
        """只由"删哪儿、删的是什么"决定 ⇒ 同一编辑同 id。"""
        return {"file": self.file, "start_line": self.start_line,
                "end_line": self.end_line, "sha256": self.sha256,
                "op": self.op}

    @property
    def edit_id(self) -> str:
        return content_hash(self.identity())[:EDIT_ID_PREFIX]

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.identity())

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Edit":
        return Edit(file=str(d.get("file") or ""),
                    start_line=int(d.get("start_line") or 0),
                    end_line=int(d.get("end_line") or 0),
                    sha256=str(d.get("sha256") or ""),
                    op=str(d.get("op") or "delete_range"))

    @property
    def n_lines(self) -> int:
        return max(0, self.end_line - self.start_line + 1)

    @property
    def module_stem(self) -> str:
        """被改模块的短名（用于判据层排除与契约映射）。"""
        return Path(str(self.file)).stem


def _sha_lines(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slice_lines(lines: List[str], start: int, end: int) -> str:
    """取出 `[start, end]`（1-based，含）区间的原文。**不做任何归一**。

    ⛔ 不 strip、不统一换行：指纹必须与"文件里真实存在的字节"对应，
       归一一次 ⇒ 生成侧与应用侧可能用不同的归一 ⇒ 指纹永远对不上，
       或者更糟：对上一个**不同**的区间。
    """
    return "".join(lines[start - 1:end])


def from_lines(root: Any, rel: str, start_line: int, end_line: int,
               *, op: str = "delete_range") -> Optional[Edit]:
    """按行区间生成一条带指纹的编辑。**读不到/区间非法 ⇒ None**（不抛）。

    ⛔ 返回 None 而不是抛异常：扫描器会遍历很多文件，一个文件读不动
       不该让整轮扫描崩掉（与 `selfref` 里 `except SyntaxError: continue`
       同纪律）。但**调用方必须处理 None** —— 拿不到指纹就没有编辑。
    """
    try:
        p = Path(root) / str(rel)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)
    except (OSError, ValueError):
        return None
    if start_line < 1 or end_line > len(lines) or end_line < start_line:
        return None
    return Edit(file=str(rel), start_line=int(start_line),
                end_line=int(end_line),
                sha256=_sha_lines(_slice_lines(lines, start_line, end_line)),
                op=op)


def verify(root: Any, e: Edit) -> Tuple[bool, str]:
    """校验：这段区间**现在**还是不是它当初被证明过的那段。

    三道检查，逐项给原因（只报第一个会让人修完一个又冒一个）：
      ① 模块在 `SOURCE_PROTECTED` 里 ⇒ 拒绝（判据层，扫都不该扫到）；
      ② 文件读不到 / 行区间越界 ⇒ 拒绝；
      ③ 指纹不匹配 ⇒ 拒绝（**行号漂了**，这次编辑已经指向别的东西）。
    """
    rel = str(e.file)
    stem = Path(rel).stem
    if stem in SOURCE_PROTECTED:
        return False, (f"模块 {stem} 在判据层排除名单里 ⇒ 源码清理在构造上"
                       f"够不到它（{rel}）")
    try:
        p = Path(root) / rel
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)
    except (OSError, ValueError) as ex:
        return False, f"读不到 {rel}（{type(ex).__name__}）⇒ 无法校验指纹"
    if e.end_line > len(lines) or e.start_line < 1:
        return False, (f"行区间 {e.start_line}-{e.end_line} 越界"
                       f"（文件共 {len(lines)} 行）")
    now = _sha_lines(_slice_lines(lines, e.start_line, e.end_line))
    if now != e.sha256:
        return False, (f"指纹不匹配 ⇒ 行号已漂移（{rel}:{e.start_line}-"
                       f"{e.end_line} 现在的内容不是当初证明过的那段）")
    return True, ""


def _is_production_root(root: Any) -> bool:
    try:
        return Path(root).resolve() == Path(ROOT).resolve()
    except (OSError, ValueError):
        #: 解析不出来 ⇒ **按生产根处理**（fail-closed：宁可拒绝也不冒险）。
        return True


def _apply_now(root: Any, edits: Sequence[Edit] = ()) -> Tuple[bool, str, int]:
    """校验 + 应用（**不判根**）。只许 `apply_edits` / `apply_edits_privileged` 调。

    ⛔ 抽出这一层的理由：生产根的拒绝是**策略**，校验与应用是**机制**。
       两件事写在一个函数里，就会出现「为了让自动落盘能跑，先把拒绝那行注释掉」
       —— 而那正是构造性防护失效的形态（第 95 条：同构代码要抽一份）。
    """
    edits = tuple(edits)
    if not edits:
        return True, "（没有编辑）", 0
    #: ① 先全量校验（全有或全无的前提）
    for e in edits:
        ok, why = verify(root, e)
        if not ok:
            return False, why, 0
    #: ② 按文件分组，**从后往前**删（先删行号大的 ⇒ 不影响前面的行号）
    by_file: Dict[str, List[Edit]] = {}
    for e in edits:
        by_file.setdefault(str(e.file), []).append(e)
    n = 0
    for rel, group in by_file.items():
        p = Path(root) / rel
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)
        drop: set = set()
        for e in group:
            drop.update(range(e.start_line, e.end_line + 1))
        kept = [ln for i, ln in enumerate(lines, start=1) if i not in drop]
        p.write_text("".join(kept), encoding="utf-8")
        n += len(drop)
    return True, "", n


def snapshot(root: Any, edits: Sequence[Edit], dest: Any) -> List[str]:
    """把编辑涉及的**原文件**整份备份到 `dest`，返回相对路径清单。

    ⛔ 整份拷而不是只存被删的那几行：回滚要的是"文件原样"，而只存片段
       在「同一文件有多处编辑 / 后续又被别人改过」时拼不回去。
    """
    dest = Path(dest)
    seen: List[str] = []
    for e in tuple(edits or ()):
        rel = str(e.file)
        if rel in seen:
            continue
        src = Path(root) / rel
        if not src.exists():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        seen.append(rel)
    return seen


def apply_edits(root: Any, edits: Sequence[Edit] = ()) -> Tuple[bool, str, int]:
    """在 `root` 这套源码上应用编辑。⛔ **生产根一律拒绝**。

    ⛔⛔ 为什么拒绝生产根而不是"加个 --yes 就能写"：
       "让系统能提议改自己的源码"里的**提议**二字是边界 ——
       提议 = 产出证据与补丁；执行 = 改字节。后者不可逆，
       而判据层就住在这批字节里。把它做成"需要显式确认"只挡得住误操作，
       挡不住"某天有人把它接进自动循环"。⇒ 在**构造上**够不到。

    ⛔ 生产根的唯一入口是 `apply_edits_privileged`：它**强制要求快照目录**，
       且必须由 `autonomy` 在冻结契约对拍通过之后调用（r60 F）。

    ⛔ **全有或全无**：任一条校验不过 ⇒ 一个字节都不改（否则留下半截
       编辑的源码树，比不改更糟 —— 它编译不过，而没人知道原稿是什么）。

    返回 `(ok, reason, n_applied)`。
    """
    if _is_production_root(root):
        return False, (f"⛔ 拒绝在生产根上应用源码编辑（{root}）⇒ "
                       f"本模块只产出提议与证据；执行由人完成。"
                       f"自动落盘走 `apply_edits_privileged`（要快照 + 对拍）"), 0
    return _apply_now(root, edits)


def apply_edits_privileged(root: Any, edits: Sequence[Edit] = (),
                           *, snapshot_dir: Any
                           ) -> Tuple[bool, str, int, List[str]]:
    """**唯一允许写生产根**的入口：先快照，再应用。

    ⛔ 为什么需要这条：r60 F 把「人点头」换成「机器对拍」之后，删代码仍然
       需要一条能真正写字节的路。原设计的做法是「构造上够不到」（
       `apply_edits` 拒生产根）。现在够得到了，就必须用**同强度的另一个
       约束**顶上：**没有快照就不许写**。
       ⛔ 这两件事不能只做一件：只给权限不给快照 = 不可逆；只留快照不给
          权限 = 那条路永远是死的（第 60 条：图里可达 ≠ 跑到了）。

    ⛔ 调用方**必须**已经拿到 `probe()` 的 `ok=True`（不是 `unverified`）。
       本函数不复验契约 —— 它只保证「要么全写、要么全不写、写坏了能还原」。

    返回 `(ok, reason, n_lines, backups)`。
    """
    edits = tuple(edits or ())
    if not edits:
        return True, "（没有编辑）", 0, []
    if not str(snapshot_dir or "").strip():
        return False, ("⛔ 缺 snapshot_dir ⇒ 拒绝写生产根"
                       "（没有快照的删除不可逆，账本只能留痕不能还原）"), 0, []
    d = Path(snapshot_dir)
    d.mkdir(parents=True, exist_ok=True)
    backups = snapshot(root, edits, d)
    ok, why, n = _apply_now(root, edits)
    if not ok:
        #: 应用失败（指纹漂移等）⇒ 文件还没动，但仍按"无副作用"返回备份清单，
        #: 让调用方能区分「没写」与「写了要回滚」。
        return False, why, 0, backups
    return True, "", n, backups


def rollback(root: Any, backups: Sequence[str], snapshot_dir: Any) -> Tuple[bool, str]:
    """从快照还原。⛔ 只还原**快照里确实有**的文件，缺一个就整体报错。"""
    d = Path(snapshot_dir)
    missing = [r for r in backups if not (d / r).exists()]
    if missing:
        return False, f"快照缺文件 {missing[:3]} ⇒ 拒绝回滚（会造出半新半旧的树）"
    for rel in backups:
        dst = Path(root) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d / rel, dst)
    return True, ""


def probe(src_root: Any, edits: Sequence[Edit] = (),
          *, runner: Optional[Callable[[Any], Dict[str, Any]]] = None,
          workdir: Any = None) -> Dict[str, Any]:
    """删前/删后对拍：**这才是源码清理那道非回归闸的裁决**。

    ⛔⛔ 为什么不能拿 `NonRegressionGate` 顶替：
       那道闸判的是"某个 harness 在冻结任务上有没有退化"，而**删源码不改
        harness** ⇒ 它恒过 ⇒ 那是一个**假裁决**（看着有闸，实际是空闸）。
       源码清理的非回归必须问"删掉这段代码之后，冻结契约还过不过"。

    ⛔ 判据的独立性：`runner` 执行的是**冻结的契约**，本模块只做比对，
       不定义什么是对的 ⇒ 判决权仍在被测对象之外（不自证）。

    ⛔ **跑不动 ⇒ 记 `unverified`，不放行**：三态，不是二态。
       `ok=False`（确实变差了）与 `unverified`（没裁决）必须分开 ——
       把后者当成前者会让人误以为"它不安全"；当成通过则更糟。

    返回 dict：`{"ok", "unverified", "before", "after", "reason", ...}`。
    """
    out: Dict[str, Any] = {
        "ok": False, "unverified": True, "before": None, "after": None,
        "reason": "", "edits": [e.to_dict() for e in tuple(edits or ())],
        "applied_lines": 0,
    }
    edits = tuple(edits or ())
    if runner is None:
        out["reason"] = "没有注入 runner ⇒ 拿不到裁决（不许冒充「已验证」）"
        return out
    if not edits:
        out["reason"] = "没有编辑 ⇒ 无可对拍"
        return out
    #: ① 先校验（指纹漂移 ⇒ 这次编辑证明的不是这段源码 ⇒ 不许对拍）
    for e in edits:
        ok, why = verify(src_root, e)
        if not ok:
            out["reason"] = f"校验未过 ⇒ 拒绝对拍：{why}"
            return out

    #: ② 建**副本**：生产源码一个字节都不动
    import tempfile
    tmp = Path(tempfile.mkdtemp(
        prefix="v6_srcmut_",
        dir=str(workdir) if workdir is not None else None))
    try:
        dst = tmp / "copy"
        shutil.copytree(src_root, dst,
                        ignore=shutil.ignore_patterns(*COPY_IGNORE))
        try:
            before = dict(runner(src_root) or {})
        except Exception as ex:                            # noqa: BLE001
            out["reason"] = f"删前基线跑挂了（{type(ex).__name__}: {ex}）"
            return out
        out["before"] = before
        #: ⛔⛔ **"跑不起来" ≠ "基线红了"** —— 实测踩到：环境没装 pytest
        #:    ⇒ before/after 都是 rc=1 ⇒ 若按"两边一样"判等价，就会得出
        #:    "删掉没问题"这个**完全错误**的结论。
        #:    ⇒ 跑不起来一律 `unverified`（红），且原因里写清楚是环境问题。
        if before.get("error"):
            out["reason"] = ("删前基线**跑不起来** ⇒ 无裁决（不是「删坏了」）"
                             f"：{str(before.get('error'))[:200]}")
            return out
        aok, why, n = apply_edits(dst, edits)
        if not aok:
            out["reason"] = f"应用编辑失败 ⇒ 无可对拍：{why}"
            return out
        out["applied_lines"] = n
        try:
            after = dict(runner(dst) or {})
        except Exception as ex:                            # noqa: BLE001
            out["reason"] = f"删后结果跑挂了（{type(ex).__name__}: {ex}）"
            return out
        out["after"] = after
        if after.get("error"):
            out["reason"] = ("删后结果**跑不起来** ⇒ 无裁决"
                             f"：{str(after.get('error'))[:200]}")
            return out
        out["unverified"] = False
        #: ③ 比对：**冻结契约的结果必须完全一致**（不许"差不多"）
        b_ok, a_ok = bool(before.get("ok")), bool(after.get("ok"))
        b_bad = sorted(map(str, before.get("failed") or []))
        a_bad = sorted(map(str, after.get("failed") or []))
        if not b_ok:
            out["ok"] = False
            out["reason"] = ("删前基线就是红的 ⇒ 对拍无意义"
                             "（先修红，再谈清理）")
        elif a_ok and not a_bad and b_ok and not b_bad:
            out["ok"] = True
            out["reason"] = "删前删后冻结契约全绿 ⇒ 这次删除行为等价"
        else:
            out["ok"] = False
            out["reason"] = (f"删后冻结契约出现差异："
                             f"新增红 {sorted(set(a_bad) - set(b_bad))}，"
                             f"消失 {sorted(set(b_bad) - set(a_bad))}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ──────────────────────────────────────────────────────────────────
# 冻结契约的执行与选择（runner 工厂）
# ──────────────────────────────────────────────────────────────────
def select_contracts(root: Any, rel_files: Sequence[str], *,
                     subdir: str = "tests", prefix: str = "contract_",
                     suffix: str = "_test.py",
                     max_contracts: int = 12) -> Tuple[List[str], str]:
    """挑出**直接消费**被改模块的冻结契约，返回 `(清单, 原因)`。

    ⛔⛔ 判据是 **import 关系**，不是文件名约定 —— 实测踩过：
        `aether/providers.py` 的守护契约叫 `contract_proposer_test.py`
        （提议器就住在 providers.py 里）。按 `contract_{模块名}_test.py`
        映射 ⇒ 找不到 ⇒ 报"没有契约守着" ⇒ **可证明的那一类也过不了闸**。
        文件名是**人起的**，import 关系才是**真的** ⇒ 一律按 import 扫。

    ⛔ 扫不到 ⇒ 返回空 **并给出原因**：没有契约消费它 ⇒ 这次删除
       **没有任何冻结判据验证过** ⇒ 那必须是 `unverified`，不许当通过。

    ⛔ `max_contracts`：对拍要跑**两遍**（删前 + 删后），契约越多越贵。
       超过上限 ⇒ 返回空并说明"需要 N 份，超预算"。
       ⛔ **不静默截断**：只跑前 12 份就宣称"验证通过"，等于拿一把
       断掉一半的尺子量东西（与「跑错对象等于没跑」同族）。

    ⛔ 一律**同时**给出原因：返回空的两条路（"没人守" vs "超预算"）
       修法完全不同 —— 前者要补契约，后者要给预算。
    """
    root = Path(root)
    tdir = root / subdir
    if not tdir.is_dir():
        return [], f"没有 {subdir}/ 目录 ⇒ 找不到冻结契约"
    stems: List[str] = []
    for rel in rel_files:
        s = Path(str(rel)).stem
        if s and s not in stems:
            stems.append(s)
    if not stems:
        return [], "没有给出被改文件 ⇒ 无从选择契约"
    hits: List[str] = []
    for cf in sorted(tdir.glob(f"{prefix}*{suffix}")):
        try:
            tree = ast.parse(cf.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError, SyntaxError):
            continue
        mods: set = set()
        for n in ast.walk(tree):
            #: `from ..providers import X` ⇒ module="providers"
            #: `from .. import providers as P` ⇒ module=None，名字在 alias 里
            if isinstance(n, ast.ImportFrom):
                if n.module:
                    mods.update(str(n.module).split("."))
                for a in n.names:
                    mods.update(str(a.name).split("."))
            elif isinstance(n, ast.Import):
                for a in n.names:
                    mods.update(str(a.name).split("."))
        if mods & set(stems):
            hits.append(str(cf.relative_to(root)))
    if not hits:
        return [], (f"没有任何冻结契约 import 被改模块 {stems} ⇒ "
                    f"这次删除没有冻结判据验证过（要过闸请先补一份契约）")
    if len(hits) > max_contracts:
        return [], (f"需要跑 {len(hits)} 份契约（{hits[:3]}…）> 上限 "
                    f"{max_contracts} ⇒ 超出预算，未验证；"
                    f"要跑请显式放宽 max_contracts")
    return hits, f"按 import 关系选中 {len(hits)} 份契约"


def contract_runner(rel_paths: Sequence[str], *, timeout_s: float = 900.0,
                    python_exe: Optional[str] = None,
                    extra_args: Sequence[str] = ()
                    ) -> Callable[[Any], Dict[str, Any]]:
    """造一个 `runner(root) -> {"ok", "failed", "rc", "error"}`。

    ⛔⛔ **契约是独立脚本，不是 pytest 用例** —— 实测踩过：
        V6 的 `tests/contract_*.py` 自带 `_main(argv)` 与 `ck()` 断言，
        退出码 0 = 全绿。跑 `python -m pytest tests/contract_x_test.py`
        得到的是 `no tests ran`（0 个用例）⇒ 退出码 0 ⇒ **两边都"没有
        跑任何东西"⇒ 对拍恒相等 ⇒ 那道闸变成空闸**（与 r39 里用错
        `probe()` 而不是 `probe_removal()` 完全同形态）。
        ⇒ 一律**当脚本逐个执行**，看退出码。

    ⛔ 契约子集由**调用方**给，本模块**不决定**跑哪些、也不决定口径
       ⇒ 判决权始终在被修改的那批源码之外（不自证）。

    ⛔⛔ 清掉 `PYTHONPATH`：否则副本里的 `aether` 可能被解析到**生产**那份
       ⇒ 对拍实际比的是"生产 vs 生产" ⇒ 恒绿 ⇒ 那道闸又变成空闸。

    ⛔ 跑挂了 / 超时 ⇒ 返回 `ok=False` 且带 `error` ⇒ 调用方要能分辨
       "契约真的红了"（`failed` 非空）与"压根没跑起来"（`error` 非空）。
       把后者当成前者，会让人以为"这段代码删不得"。
    """
    sel = [str(p) for p in (rel_paths or ())]

    def _run(root: Any) -> Dict[str, Any]:
        import subprocess
        if not sel:
            return {"ok": False, "failed": [], "rc": None,
                    "error": "没有选中任何冻结契约 ⇒ 无法对拍"}
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)          #: ⛔ 见 docstring
        failed: List[str] = []
        last_rc = 0
        err = ""
        for rel in sel:
            cmd = [python_exe or sys.executable, rel, *extra_args]
            try:
                cp = subprocess.run(cmd, cwd=str(root), capture_output=True,
                                    text=True, timeout=timeout_s, env=env)
            except Exception as ex:                            # noqa: BLE001
                return {"ok": False, "failed": failed + [rel], "rc": None,
                        "error": f"{type(ex).__name__}: {ex}"}
            last_rc = cp.returncode
            if cp.returncode != 0:
                #: ⛔⛔ **只进 `failed`，绝不写 `error`** —— 实测踩过：
                #:    把"契约红了"也塞进 `error` ⇒ 对拍把它读成"跑不起来"
                #:    ⇒ `unverified` ⇒ 红的原因从"删坏了"变成"没验证"
                #:    ⇒ 一次**真实的破坏**被降级成了"不确定"。
                #:    `error` 只保留给"压根没跑起来"（异常 / 超时 / 没解释器）。
                failed.append(rel)
        return {"ok": not failed, "failed": failed, "rc": last_rc,
                "error": err}
    return _run

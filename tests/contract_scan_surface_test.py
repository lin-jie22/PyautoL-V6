#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约 · 验收静态闸的扫描面必须覆盖 untracked（§185 / 拍板 ⑥）。

═══ 守什么 ═══

`contract_portability_test.py` 的 A1 是一条「零命中」判据。在干净仓库里，
修前（tracked-only）与修后（含 untracked）**都是零命中** ⇒ 只看生产读数
分不出闸修没修好。r101 的 113/113 就是这样假的绿：全量验收跑在提交前，
新加的 4 个文件全是 `??`，而 A1 只扫 `git ls-files`。

⇒ 本契约在**临时 git 仓库**里造「提交前」现场，用同源判据证明：
  1. 旧扫描面看不见 untracked 脏文件（事故现场可复现）；
  2. 新扫描面必须抓到它，且只落在 untracked 账上（两本账分开）；
  3. `--exclude-standard` 不是装饰：去掉它，ignored 运行产物会被误纳入。

═══ 硬纪律 ═══

⛔ 判据全部复用被测对象自己的 `scan_text()` / `read_text()` /
   `scan_surface()` / `probe_scan_surface.measure()`（契约⑥：同源）。
⛔ B7/B8/B9 是**注入事故版**，不是把断言注释掉：撤回只换条件、
   保留 body，跑完 `finally` 恢复，否则后面 C 组读的是被污染的量具。
⛔ 本文件与存档 JSON 都在 A1 的扫描面里 ⇒ 真实家目录只许拼出来，
   绝不写字面量。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

import probe_scan_surface as probe                        # noqa: E402
from aether.check import discover                          # noqa: E402

M = probe.M
CENSUS = json.loads((ROOT / "scripts" / "scan_surface_census.json")
                    .read_text(encoding="utf-8"))
FAILED: list[str] = []

EXPECTED_CHECKS = {
    "old_selfcheck_hit", "old_missed_root_untracked",
    "old_missed_nested_untracked", "new_caught_root_untracked",
    "new_caught_nested_untracked", "tracked_hit_unchanged",
    "ignored_excluded", "redacted_not_hit", "clean_not_hit",
    "untracked_only_leak",
}


def ck(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


def main() -> None:
    print("== A 组：钉死存档读数，并同源重跑一遍（尺子不许漂）==")
    fix, old, new = CENSUS["fixture"], CENSUS["old_surface"], CENSUS["new_surface"]
    ck("A1 夹具 3 tracked / 3 untracked / 6 类样本",
       fix["n_tracked"] == 3 and fix["n_untracked"] == 3
       and fix["n_categories"] == 6, str(fix))
    ck("A2 tracked 清单逐字钉死",
       fix["tracked"] == [".gitignore", "tracked_clean.txt",
                          "tracked_dirty.txt"], str(fix["tracked"]))
    ck("A3 untracked 清单逐字钉死（含子目录）",
       fix["untracked"] == ["nested/untracked_dirty.py",
                            "untracked_dirty.txt",
                            "untracked_redacted.txt"], str(fix["untracked"]))
    ck("A4 ignored 清单逐字钉死",
       fix["ignored"] == ["ignored_dirty.txt"], str(fix["ignored"]))
    ck("A5 旧扫描面 = tracked-only / 3 文件 / 1 命中",
       old["kind"] == "tracked-only (git ls-files)"
       and old["n_files"] == 3
       and old["hits"] == ["tracked_dirty.txt:1"], str(old))
    ck("A6 新扫描面 6 文件 / n_scanned=6",
       new["kind"] == "tracked + untracked-non-ignored"
       and new["n_files"] == 6 and new["n_scanned"] == 6, str(new))
    ck("A7 tracked 账不变（只 1 命中）",
       new["hits_tracked"] == ["tracked_dirty.txt:1"],
       str(new["hits_tracked"]))
    ck("A8 ⛔ untracked 账新增 2 命中（根 + 子目录）",
       new["hits_untracked"] == ["nested/untracked_dirty.py:1",
                                 "untracked_dirty.txt:1"],
       str(new["hits_untracked"]))
    ck("A9 checks 键集合逐字钉死且 10 项全 True",
       set(CENSUS["checks"]) == EXPECTED_CHECKS
       and all(CENSUS["checks"].values()), str(CENSUS["checks"]))
    ck("A10 closure_ok=True", CENSUS["closure_ok"] is True,
       str(CENSUS["closure_ok"]))
    ck("A11 ⛔ 同源重跑 measure() 与存档逐字相等（不是手写死的读数）",
       probe.measure() == CENSUS, "尺子漂了 / 存档被手改过")

    print("== B 组：临时仓库注入事故版 —— 证明每条新判据不是死代码 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        probe.build_fixture(root)
        tracked = M.tracked_files(root) or []
        others = M.untracked_files(root) or []
        cand = M.candidate_files(root) or []
        surf = M.scan_surface(root)
        ck("B0 临时仓库四条查询都跑起来了（None ⇒ 整组空转）",
           bool(tracked) and bool(others) and bool(cand)
           and surf is not None, "git 命令失败")
        if surf is None:
            surf = {"tracked": [], "untracked": [], "files": [], "n_scanned": 0}
        ck("B1 ⛔ 同源自检：拼接样本必须命中 1 行（量具能命中）",
           M.scan_text("p = " + repr(probe.BAD) + "\n") == [1],
           str(M.scan_text("p = " + repr(probe.BAD) + "\n")))
        ck("B2 旧扫描面确实看不见 untracked 脏文件（r101 事故现场）",
           "untracked_dirty.txt" not in tracked
           and "nested/untracked_dirty.py" not in tracked, str(tracked))
        ck("B3 untracked 通道恰含 3 条且不含 ignored",
           sorted(others) == ["nested/untracked_dirty.py",
                              "untracked_dirty.txt",
                              "untracked_redacted.txt"], str(others))
        ck("B4 candidate_files = tracked ∪ untracked（去重后 6 条）",
           cand == sorted(set(tracked) | set(others)) and len(cand) == 6,
           str(cand))
        ck("B5 ⛔ scan_surface 两本账分开：tracked 1 / untracked 2",
           surf["tracked"] == ["tracked_dirty.txt:1"]
           and surf["untracked"] == ["nested/untracked_dirty.py:1",
                                     "untracked_dirty.txt:1"], str(surf))
        ck("B6 旧尺子逐字重放仍只有 1 命中",
           probe.old_surface_hits(root) == ["tracked_dirty.txt:1"],
           str(probe.old_surface_hits(root)))

        #: 事故版①：把候选面退回 tracked-only ⇒ untracked 账必须归零。
        orig_c = M.candidate_files
        try:
            M.candidate_files = lambda root=None: M.tracked_files(root)
            back = M.scan_surface(root)
        finally:
            M.candidate_files = orig_c
        ck("B7 ⛔ 事故版①：扫描面退回 tracked-only ⇒ untracked 账归零"
           "（证明 B5 的 2 条来自 ⑥ 的扩张）",
           back is not None and back["untracked"] == []
           and back["tracked"] == ["tracked_dirty.txt:1"], str(back))

        #: 事故版②：去掉 --exclude-standard ⇒ ignored 运行产物被误纳入。
        orig_u = M.untracked_files
        try:
            M.untracked_files = lambda root=None: M.git_files(
                "--others", root=root)
            noexc = M.scan_surface(root)
        finally:
            M.untracked_files = orig_u
        ck("B8 ⛔ 事故版②：去掉 --exclude-standard ⇒ ignored 脏文件被扫到"
           "（证明它不是装饰、能防每轮假红）",
           noexc is not None
           and "ignored_dirty.txt:1" in noexc["untracked"],
           str(noexc and noexc["untracked"]))

        #: 事故版③：判据恒真 ⇒ 连 tracked 脏文件都不报。
        orig_s = M.scan_text
        try:
            M.scan_text = lambda text: []
            dead = M.scan_surface(root)
        finally:
            M.scan_text = orig_s
        ck("B9 ⛔ 事故版③：scan_text 恒返回空 ⇒ 两本账全空"
           "（证明「零命中」另有含义，A1 不是恒真）",
           dead is not None and dead["tracked"] == []
           and dead["untracked"] == [], str(dead))

    print("== C 组：本机实战与边界（只报读数，不改仓库）==")
    ck("C0 被测对象四个新函数都在（否则本契约空转）",
       all(callable(getattr(M, n, None)) for n in
           ("git_files", "untracked_files", "candidate_files",
            "scan_surface")), "缺函数")
    live = M.scan_surface()
    ck("C1 生产扫描面非 None / ≥200 文本 / 两本账零命中",
       live is not None and live["n_scanned"] >= 200
       and live["tracked"] == [] and live["untracked"] == [],
       str(live and {k: live[k] for k in
                     ("n_scanned", "tracked", "untracked")}))
    t_files, c_files = M.tracked_files() or [], M.candidate_files() or []
    ck("C2 扫描面只扩不缩：candidate ⊇ tracked",
       set(t_files) <= set(c_files) and len(c_files) >= len(t_files),
       f"tracked={len(t_files)} candidate={len(c_files)}")
    ck("C3 ⛔ 本契约文件自身在候选面里（提交前它是 untracked）",
       "tests/" + Path(__file__).name in c_files,
       "新契约文件没进扫描面 ⇒ ⑥ 没生效")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "tests").mkdir()
        (d / "tests" / "contract_probe_tmp_test.py").write_text(
            "# x\n", encoding="utf-8")
        seen = [p.name for p in discover(d)]
        ck("C4 aether.check.discover 是磁盘 glob —— 执行面本就覆盖 untracked"
           "（所以 ⑥ 只需改静态闸，不改流程）",
           seen == ["contract_probe_tmp_test.py"], str(seen))
    with tempfile.TemporaryDirectory() as td:
        ck("C5 非 git 目录 ⇒ git_files 返回 None（跑不了 ≠ 通过）",
           M.git_files(root=Path(td)) is None, "竟然返回了清单")


if __name__ == "__main__":
    main()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
        sys.exit(1)
    print("✅ 全部通过")

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""r106 尺子 · A1 静态闸的 tracked-only 盲区与补法对照（§185 / 拍板 ⑥）。

═══ 为什么需要这把尺子 ═══

`contract_portability_test.py` 的 A1 是一条「零命中」判据。在干净仓库里，
修前（tracked-only）与修后（含 untracked）**都是零命中** ⇒ 光看生产读数
分不出闸有没有被修好。第 133 条：零命中先要证明量具能命中。

⇒ 在临时 git 仓库里造一个「提交前」现场：新文件全是 `??`，其中两条
带真实家目录样式。旧扫描面必须**看不见**，新扫描面必须**抓到**。

═══ 两条硬纪律 ═══

⛔ 判据一律复用被测对象自己的 `scan_text()` / `read_text()` /
   `scan_surface()`（eval-standard-anchoring 契约⑥：判据必须同源）。
   这里唯一「另写」的是**修前的文件清单循环**，那是事故现场的重放，
   不是另一份判据。
⛔ 本文件与落盘 JSON 都在 A1 的扫描面里 ⇒ 真实家目录只许**拼**出来，
   绝不写字面量；JSON 里只存文件名 / 计数 / 布尔，不存样本值。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "scripts" / "scan_surface_census.json"


def load_portability():
    """同源加载被测对象；不 import 生产常量表，避免两边一起改错。"""
    spec = importlib.util.spec_from_file_location(
        "portability", ROOT / "tests" / "contract_portability_test.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load_portability()

#: ⛔ 拼出来，不写字面量 —— 本文件自己也在 A1 的扫描面里。
BAD = M._PREFIXES[0] + "alice" + "/repo/y.py"
REDACTED = "/" + "Users/…/V6"

TRACKED = [".gitignore", "tracked_clean.txt", "tracked_dirty.txt"]
UNTRACKED = ["nested/untracked_dirty.py", "untracked_dirty.txt",
             "untracked_redacted.txt"]
IGNORED = ["ignored_dirty.txt"]


def build_fixture(root: Path) -> None:
    """造一个「全量验收跑在提交前」的现场：6 类样本，一个不少。"""
    (root / ".gitignore").write_text("ignored_dirty.txt\n", encoding="utf-8")
    (root / "tracked_clean.txt").write_text("clean\n", encoding="utf-8")
    for name in ("tracked_dirty.txt", "untracked_dirty.txt",
                 "ignored_dirty.txt"):
        (root / name).write_text("p = " + repr(BAD) + "\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "untracked_dirty.py").write_text(
        "p = " + repr(BAD) + "\n", encoding="utf-8")
    (root / "untracked_redacted.txt").write_text(
        "p = " + repr(REDACTED) + "\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", *TRACKED], cwd=str(root), check=True)


def old_surface_hits(root: Path) -> list[str] | None:
    """修前 A1 的逐字重放：`git ls-files`（tracked-only）+ 同源判据。

    ⛔ 只换文件清单，**不换** `read_text` / `scan_text`。
    """
    files = M.git_files(root=root)
    if files is None:
        return None
    out: list[str] = []
    for rel in files:
        p = root / rel
        if not p.is_file():
            continue
        text = M.read_text(p)
        if text is None:
            continue
        for ln in M.scan_text(text):
            out.append("%s:%d" % (rel, ln))
    return sorted(out)


def measure() -> dict:
    """跑一次完整对照，返回**稳定**读数（相对路径，与临时目录名无关）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_fixture(root)
        tracked = M.tracked_files(root)
        untracked = M.untracked_files(root)
        old = old_surface_hits(root)
        new = M.scan_surface(root)
        assert tracked is not None and untracked is not None
        assert old is not None and new is not None, "git 命令没跑起来 ⇒ 不是通过"
        files = new["files"]
        hits_t, hits_u = new["tracked"], new["untracked"]
        checks = {
            "old_selfcheck_hit": "tracked_dirty.txt:1" in old,
            "old_missed_root_untracked": "untracked_dirty.txt:1" not in old,
            "old_missed_nested_untracked": (
                "nested/untracked_dirty.py:1" not in old),
            "new_caught_root_untracked": "untracked_dirty.txt:1" in hits_u,
            "new_caught_nested_untracked": (
                "nested/untracked_dirty.py:1" in hits_u),
            "tracked_hit_unchanged": hits_t == old,
            "ignored_excluded": all(
                not x.startswith("ignored_dirty") for x in files),
            "redacted_not_hit": all("redacted" not in x for x in hits_u),
            "clean_not_hit": all("clean" not in x for x in hits_t + hits_u),
            "untracked_only_leak": all(x.startswith("untracked")
                                       or x.startswith("nested/")
                                       for x in hits_u),
        }
        return {
            "fixture": {
                "n_tracked": len(tracked),
                "n_untracked": len(untracked),
                "tracked": sorted(tracked),
                "untracked": sorted(untracked),
                "ignored": IGNORED,
                "n_categories": 6,
            },
            "old_surface": {
                "kind": "tracked-only (git ls-files)",
                "n_files": len(tracked),
                "hits": old,
            },
            "new_surface": {
                "kind": "tracked + untracked-non-ignored",
                "n_files": len(files),
                "n_scanned": new["n_scanned"],
                "hits_tracked": hits_t,
                "hits_untracked": hits_u,
            },
            "checks": checks,
            "closure_ok": all(checks.values()),
        }


def main() -> None:
    data = measure()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("old_hits =", data["old_surface"]["hits"])
    print("new_hits_tracked   =", data["new_surface"]["hits_tracked"])
    print("new_hits_untracked =", data["new_surface"]["hits_untracked"])
    print("checks =", data["checks"])
    print("closure_ok =", data["closure_ok"])
    print("WROTE", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()

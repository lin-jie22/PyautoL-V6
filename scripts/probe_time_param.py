#!/usr/bin/env python3
"""⛔ 取证：**把不可离线求值的时间源参数化，到底能不能换来 F2P？**

═══ 为什么要有这个脚本（README 172）═══

`aether/extract.py` 的**自限 2** 规定：探针里含 `date.today()` / `now()`
这类时间源 ⇒ 不可复现 ⇒ 一律拒收（`GATE_UNSTABLE`）。这条自限是**对的**
—— 跨天跨进程会翻的判据 ≡ 随机判据。

但 r92 的上界实验留了一个开口：案例 `93185b2`（boltons 的无限 daterange）
在全自动路径下是 `no_f2p`，而它的人类测试**正好**全用了 `date.today()`
⇒ 看起来是"被自限 2 冤枉了一道"，于是产生了这条设想：

    **把 `today = date.today()` 改写成 `today = date(2020, 1, 1)`，
      让这批探针重新可求值。**

本脚本就是为这条设想做**对照实验**，而且刻意做得很便宜：

    A 组（对照）源仓库里 target commit **原样**
    B 组（实验）同一个 commit，但把测试源码里的一行按 `--map` 改写

两组的**被测源码一字未改**（只 `--amend` 测试文件），所以 `auto_extract`
里 `fixed` 的取值完全一样 ⇒ **唯一的自变量就是那一行替换**。

═══ ⛔ 两条纪律 ═══

  1. **不碰源仓库**。一律先 clone 到 `tempfile.mkdtemp()`，在副本上
     `--amend`；源 HEAD 不变 ⇒ 跨轮仍可比（README 169 的教训）。
  2. **不自带第二份三验**。两侧都走 `probe_auto_unlock.auto_extract`
     （产品层 `extract_criteria` + 沙箱三验），本脚本只负责"组织两次跑"。

读 JSON：`[{"group": "base"|"param", "stats": {...}, "k": int, "rows": [...]}]`
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from probe_auto_unlock import _git, auto_extract  # noqa: E402

_ENV = {"GIT_AUTHOR_NAME": "probe", "GIT_AUTHOR_EMAIL": "probe@local",
        "GIT_COMMITTER_NAME": "probe", "GIT_COMMITTER_EMAIL": "probe@local"}


def _env() -> dict:
    """提交身份：显式给全，免得换台机器没配 git identity 就 amend 失败。"""
    import os
    return {**os.environ, **_ENV}


def _g(repo: Path, *a: str) -> str:
    """跑一条 git 命令并返回 stdout（在 `repo` 目录里跑，失败 ⇒ 空串）。"""
    try:
        return subprocess.run(("git",) + a, cwd=repo, check=True,
                              capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return ""


def g_ok(repo: Path, *a: str) -> bool:
    """⛔ **只看返回码**的命令存在性判定。

    ⛔ 不许用 `_g(...) != ""` 代替：`cat-file -e <sha>` 成功时**什么都不输出**
    ⇒ 空串会被当成"取不到"。这个坑真实踩过一次 —— 脚本报"基线不可用"，
    而手工 `git cat-file -e` 明明 rc=0（第 102 条：判退出码不接管道）。
    """
    try:
        subprocess.run(("git",) + a, cwd=repo, check=True,
                       capture_output=True, text=True)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def health(repo: Path, sha: str, src: str) -> tuple:
    """⛔ README 169：普查/实验之前**先验基线健康**，并把健康度当读数报。"""
    head = _g(repo, "rev-parse", "--short", "HEAD").strip()
    if not head:
        return False, f"{repo} 不是可用的 git 仓库（或 HEAD 丢了）"
    if not g_ok(repo, "cat-file", "-e", sha):
        return False, f"{sha} 在本仓库里取不到"
    if not _git(repo, "show", f"{sha}:{src}"):
        return False, f"{sha}:{src} 读不到源码"
    return True, f"HEAD={head}  {sha} 可读"


def clone_pair(src_repo: Path, root: Path, sha: str, mapping: dict
               ) -> tuple:
    """返回 `(base_repo, param_repo)`，失败 ⇒ `(None, reason)`。"""
    base = root / "base"
    try:
        subprocess.run(["git", "clone", "-q", str(src_repo), str(base)],
                       check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, OSError) as e:
        return None, f"clone 失败：{e}"
    if not _git(base, "rev-parse", "HEAD").strip():
        return None, "clone 出来 HEAD 取不到 ⇒ 源仓库已损坏（README 169）"
    subprocess.run(["git", "checkout", "-q", "-B", "tpbase", sha], cwd=base,
                   check=True, capture_output=True, text=True)
    param = root / "param"
    shutil.copytree(base, param, symlinks=True)
    for tf, (old, new) in mapping.items():
        p = param / tf
        if not p.exists():
            continue
        s = p.read_text(encoding="utf-8")
        p.write_text(s.replace(old, new), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=param, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "--amend", "--no-edit"],
                   cwd=param, check=True, capture_output=True, text=True,
                   env=_env())
    return (base, param), ""


def run_one(repo: Path, src: str, testfs: list) -> dict:
    """在 `repo` 的 HEAD 上跑一次标准 `auto_extract`。"""
    sha = _git(repo, "rev-parse", "HEAD").strip()
    detail: dict = {}
    tests, stx = auto_extract(repo, sha, src, testfs, detail=detail)
    rows = [{"fn": r["key"][0], "stmt": r["key"][1], "call": r["key"][2],
             "verdict": r["verdict"]} for r in detail.get("rows", [])]
    return {"sha": sha[:7], "group": "",
            "stats": {k: v for k, v in sorted(stx.items())
                      if k.startswith("n_")},
            "k": len(tests),
            "verdicts": dict(collections.Counter(
                r["verdict"] for r in detail.get("rows", []))),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/realrepo/boltons")
    ap.add_argument("--sha", default="93185b2")
    ap.add_argument("--src", default="boltons/timeutils.py")
    ap.add_argument("--tests", default="tests/test_timeutils.py")
    ap.add_argument("--map", default="today = date.today()=>"
                                     "today = date(2020, 1, 1)")
    ap.add_argument("--timeout", default="20")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo)
    ok, why = health(repo, args.sha, args.src)
    print("═" * 76)
    print("取证：时间源参数化的对照实验")
    print("═" * 76)
    print(f"  基线健康度：{'OK' if ok else '⛔ ' + why}")
    if not ok:
        print("  ⇒ 基线不可用，本实验结果无意义（README 169）。")
        return 2
    print(f"  commit {args.sha}   src={args.src}")
    print(f"  替换   {args.map}")

    tf_list = [t.strip() for t in args.tests.split(",") if t.strip()]
    mapping = {}
    for part in args.map.split("||"):
        if "=>" not in part:
            continue
        old, new = part.split("=>", 1)
        for tf in tf_list:
            mapping[tf] = (old.strip("\n"), new.strip("\n"))

    root = Path(tempfile.mkdtemp(prefix="timeparam_"))
    out: list = []
    try:
        pair, err = clone_pair(repo, root, args.sha, mapping)
        if pair is None:
            print(f"  ⛔ {err}")
            return 2
        base, param = pair
        for name, rp in (("base(原样)", base), ("param(参数化)", param)):
            r = run_one(rp, args.src, tf_list)
            r["group"] = name
            out.append(r)
            print(f"\n── {name}  sha={r['sha']}")
            print(f"   stats: {r['stats']}")
            print(f"   可用判据 k = {r['k']}   verdict = {r['verdicts']}")
            for x in r["rows"]:
                print(f"      {x['verdict']:<20} {x['call'][:70]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if len(out) == 2:
        b, p = out
        print("\n" + "═" * 76)
        print("差值（param − base）")
        print("═" * 76)
        for key in ("n_unstable", "n_cand", "n_semantics_ok", "n_gold_unresolved"):
            vb, vp = b["stats"].get(key, 0), p["stats"].get(key, 0)
            print(f"   {key:<22} {vb:>3} → {vp:<3}  ({vp - vb:+d})")
        print(f"   可用判据 k            {b['k']:>3} → {p['k']:<3}"
              f"  ({p['k'] - b['k']:+d})")
        nb = collections.Counter(b["verdicts"])
        np_ = collections.Counter(p["verdicts"])
        print(f"   unpassed_on_fixed     {nb.get('unpassed_on_fixed', 0):>3}"
              f" → {np_.get('unpassed_on_fixed', 0):<3}")

    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False,
                                             indent=1), encoding="utf-8")
        print(f"\n  ⇒ 已写 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

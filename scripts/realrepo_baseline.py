#!/usr/bin/env python3
"""取证基线锚：把「119 个真实 commit」的判定口径钉进版本控制。

═══ 为什么有这个文件 ═══

169（2026-10-29）：/tmp/realrepo/boltons 的 .git 骨架被外部破坏 ⇒ 本地
契约 H1/H3 红、CI 三档全绿（基线上游根本不存在 ⇒ H 组 SKIP）。
当时的恢复靠「objects 里的 pack 还完好 ⇒ 原地重建 HEAD/config/index」。

174（2026-11-05）：同样的事第二次发生，而且更彻底 ——
    · 三个仓库 HEAD/config/index 全丢
    · more-itertools / attrs 的 objects 直接空了（pack 也没了）
    · 工作区 0 个文件
⇒ 169 的「靠 objects 原地恢复」对两个仓库已不成立。

根因不是「谁删了文件」，是 **取证基线本身不在版本控制里**：
探针按 `git log --format=%H`（从 HEAD 往下）用固定词表筛前 40 个
（`probe_alias_demote.select_commits`），所以 **HEAD 就是这把尺子的零点**。
零点没了 ⇒ 重新 clone 会拿到远端最新 HEAD ⇒ 那 40 个 commit 全变
⇒ 跨轮读数不可比，而且**不会报错**：照样出一份看起来正常的分布表。

⛔ 标准可读、不可被被测者改写。基线必须：
   ① 内容寻址地钉死在版本控制里；
   ② 验不过时 **fail-closed**（拒绝出读数），不许静默 SKIP 当成通过；
   ③ 任何机器都能按钉死的 sha 重建，而不是按远端最新。

═══ 锚里钉了什么 ═══
  · head（40 位）        ：零点
  · commit_shas          ：同源 `select_commits` 的输出序列（**决定性**判据）
  · extractor_sha256     ：`select_commits` 源码 hash（词表被改也能抓到）
  · n_tracked / smoke    ：抓「index 丢了」「objects 内容被换」

⛔ 锚里**不另写一份 FIX_WORDS** —— 判据必须与被测对象同源。

用法：
    python scripts/realrepo_baseline.py verify
    python scripts/realrepo_baseline.py verify --json
    python scripts/realrepo_baseline.py snapshot
    python scripts/realrepo_baseline.py bootstrap --repo more-itertools
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from probe_alias_demote import select_commits  # noqa: E402

BASELINE = ROOT / "scripts" / "realrepo_baseline.json"
CENSUS_DIR = ROOT / "scripts"
EXTRACTOR = ROOT / "scripts" / "probe_alias_demote.py"

SCHEMA = "realrepo-baseline/v1"
_MISSING = "__MISSING__"
REPOS = ("boltons", "more-itertools", "attrs")
CENSUS_FILE = {
    "boltons": "census_boltons_40.json",
    "more-itertools": "census_mi_40.json",
    "attrs": "census_attrs_40.json",
}
SUBDIR = {
    "boltons": "boltons/",
    "more-itertools": "more_itertools/",
    "attrs": "src/attr/",
}
REMOTE = {
    "boltons": "https://github.com/mahmoud/boltons.git",
    "more-itertools": "https://github.com/more-itertools/more-itertools.git",
    "attrs": "https://github.com/python-attrs/attrs.git",
}

#: 口径签名：缺键用 `_MISSING`，不要用默认值（「没记」和「记了假值」必须可区分）。
CALIBER_KEYS = ("schema", "extractor", "extractor_sha256", "limit")
#: 每个 repo 的必需键；任何一条缺失都直接判不健康并**点名该字段**。
REPO_KEYS = ("path", "remote", "head", "head_short", "subdir", "limit",
             "commit_shas", "census_shas", "n_tracked", "smoke",
             "census_sha256")


def is_subsequence(small: Sequence[str], big: Sequence[str]) -> bool:
    """存档读数必须是原始候选的**保序子序列**，不是「相等」。

    ⛔ 两本账要分开记、分开报：`select_commits` 给的是 40 个**候选**，
       `census_*.json` 给的是探针**跑完**的行数。`probe_auto_unlock` 主循环
       在 `sha^:src` 读不到 / buggy==fixed 时会 `continue` ⇒ 实测
       more-itertools 是 40 选 39（`9f4f31f` 被跳过）。把两本账合并成
       一个布尔，就会在「候选池变了」和「探针跳过一条」之间分不清。
    """
    it = iter(big)
    return all(any(x == y for x in it) for y in small)


# ───────────────────── 基础 ─────────────────────
def _git(repo: Path, *args: str) -> Tuple[int, str]:
    """⛔ 存在性命令（`cat-file -e`）成功时 stdout 为空 ⇒ 只看返回码。"""
    try:
        p = subprocess.run(["git", *args], cwd=str(repo),
                           capture_output=True, text=True)
    except OSError as e:
        return 127, "%s: %s" % (type(e).__name__, e)
    return p.returncode, (p.stdout or "").strip()


def _git_blob(repo: Path, *args: str) -> Tuple[int, bytes]:
    """读 blob 用**原始字节**：`text=True` + `.strip()` 会把尾部换行吃掉，
    那样 `blob_sha256` 量的不是真内容，尾部空白被改也照样验绿。"""
    try:
        p = subprocess.run(["git", *args], cwd=str(repo),
                           capture_output=True)
    except OSError:
        return 127, b""
    return p.returncode, (p.stdout or b"")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def load_baseline(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or BASELINE
    return json.loads(p.read_text(encoding="utf-8"))


def current_caliber() -> Dict[str, Any]:
    """当前代码里那把尺子。⛔ 不 import 常量再抄一遍，直接对源码取 hash。"""
    return {
        "schema": SCHEMA,
        "extractor": "probe_alias_demote.select_commits",
        "extractor_sha256": sha256_file(EXTRACTOR),
        "limit": 40,
    }


def check_caliber(doc: Dict[str, Any], cur: Optional[Dict[str, Any]] = None
                  ) -> List[str]:
    cur = cur or current_caliber()
    diffs: List[str] = []
    for k in CALIBER_KEYS:
        want = doc.get(k, _MISSING)
        got = cur.get(k, _MISSING)
        if want != got:
            diffs.append("caliber.%s: anchor=%s current=%s" % (k, want, got))
    return diffs


# ───────────────────── 单仓库验签 ─────────────────────
def verify_repo(name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """验一个仓库。**每条失败都点名字段**，不合并成一个布尔。"""
    diffs: List[str] = []
    readings: Dict[str, Any] = {}

    for k in REPO_KEYS:
        if k not in cfg or cfg[k] is None:
            diffs.append("%s.%s: %s" % (name, k, _MISSING))
    if diffs:
        return {"ok": False, "diffs": diffs, "readings": readings}

    path = Path(cfg["path"])
    readings["path"] = str(path)
    if not path.is_dir():
        diffs.append("%s.path: 目录不存在 (%s)" % (name, path))
    if not (path / ".git").is_dir():
        diffs.append("%s.path: .git 不是目录" % name)
    if not (path / ".git" / "HEAD").is_file():
        #: 169 的症状：HEAD 丢了 ⇒ 探针抽 0 条，看起来像「判据侧又归零」。
        diffs.append("%s.path: .git/HEAD 缺失" % name)
    if diffs:
        return {"ok": False, "diffs": diffs, "readings": readings}

    head = str(cfg["head"])
    rc, _ = _git(path, "cat-file", "-e", head + "^{commit}")
    if rc != 0:
        #: objects 被清空的情形（174）：ref 还在，commit 对象读不出来。
        diffs.append("%s.head: commit 对象不可读 rc=%d (%s)" % (name, rc, head))
        return {"ok": False, "diffs": diffs, "readings": readings}

    rc, got_head = _git(path, "rev-parse", "HEAD")
    readings["head"] = got_head[:7]
    if rc != 0 or got_head != head:
        diffs.append("%s.head: expected=%s got=%s rc=%d"
                     % (name, head[:7], got_head[:7], rc))

    rc, log = _git(path, "log", "--format=%H")
    if rc != 0 or not log:
        diffs.append("%s.git-log: 不可用 rc=%d" % (name, rc))
        return {"ok": False, "diffs": diffs, "readings": readings}

    rc, tracked = _git(path, "ls-files")
    n_tracked = len([x for x in tracked.splitlines() if x.strip()])
    readings["n_tracked"] = n_tracked
    if n_tracked != int(cfg["n_tracked"]):
        diffs.append("%s.n_tracked: expected=%d got=%d"
                     % (name, int(cfg["n_tracked"]), n_tracked))

    #: ⛔ 决定性判据：复用**被测对象自己**的 select_commits，不另写词表。
    try:
        got = [x[0] for x in select_commits(path, int(cfg["limit"]),
                                            str(cfg["subdir"]))]
    except Exception as e:  # noqa: BLE001 - 抽取器崩了也要落成读数
        diffs.append("%s.select_commits: %s: %s" % (name, type(e).__name__, e))
        return {"ok": False, "diffs": diffs, "readings": readings}
    want = list(cfg["commit_shas"])
    readings["n_commits"] = len(got)
    if got != want:
        first = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                     min(len(got), len(want)))
        diffs.append("%s.commit_shas: 序列不一致 first_diff@%d "
                     "expected=%s got=%s (len %d vs %d)"
                     % (name, first,
                        want[first] if first < len(want) else "<end>",
                        got[first] if first < len(got) else "<end>",
                        len(want), len(got)))

    smoke_ok = 0
    for item in cfg["smoke"]:
        sha, rel = item["sha"], item["path"]
        rc, blob = _git_blob(path, "show", "%s:%s" % (sha, rel))
        if rc != 0:
            diffs.append("%s.smoke: 读不到 %s:%s rc=%d" % (name, sha, rel, rc))
            continue
        if sha256_bytes(blob) != item["blob_sha256"]:
            diffs.append("%s.smoke: 内容 hash 不符 %s:%s" % (name, sha, rel))
            continue
        smoke_ok += 1
    readings["smoke"] = "%d/%d" % (smoke_ok, len(cfg["smoke"]))

    #: ⛔ 两本账分开验：候选池（commit_shas）与探针读数（census_shas）。
    cf = CENSUS_DIR / str(CENSUS_FILE.get(name, ""))
    if name in CENSUS_FILE:
        if not cf.is_file():
            diffs.append("%s.census: 存档缺失 (%s)" % (name, cf.name))
        else:
            if sha256_file(cf) != cfg["census_sha256"]:
                diffs.append("%s.census_sha256: 存档读数被改写 (%s)"
                             % (name, cf.name))
            try:
                census_shas = [r["sha"] for r in
                               json.loads(cf.read_text(encoding="utf-8"))]
            except (OSError, ValueError, KeyError, TypeError) as e:
                diffs.append("%s.census: 读不出来 %s" % (name, e))
            else:
                if census_shas != list(cfg["census_shas"]):
                    diffs.append("%s.census_shas: 与锚不一致 (存档 %d / 锚 %d)"
                                 % (name, len(census_shas),
                                    len(cfg["census_shas"])))
                if not is_subsequence(census_shas, cfg["commit_shas"]):
                    diffs.append("%s.census_shas: 不是 commit_shas 的保序子序列"
                                 " ⇒ 候选池与读数口径混了" % name)
                readings["census"] = "%d/%d" % (len(census_shas),
                                                len(cfg["commit_shas"]))

    return {"ok": not diffs, "diffs": diffs, "readings": readings}


# ───────────────────── 全局验签 ─────────────────────
def verify(baseline: Dict[str, Any], repos: Sequence[str] = ()
           ) -> Dict[str, Any]:
    """验整份锚。**caliber 不成立时连仓库都不用看** ⇒ fail-closed。"""
    cur = current_caliber()
    cal = baseline.get("caliber", {})
    caliber_diffs = check_caliber(cal, cur)
    names = tuple(repos) if repos else REPOS
    out: Dict[str, Any] = {
        "ok": False,
        "schema": baseline.get("schema", _MISSING),
        "caliber_diffs": caliber_diffs,
        "repos": {},
        "diffs": [],
    }
    for name in names:
        cfg = baseline.get("repos", {}).get(name)
        if cfg is None:
            out["repos"][name] = {
                "ok": False,
                "diffs": ["repos.%s: %s" % (name, _MISSING)],
                "readings": {},
            }
        else:
            out["repos"][name] = verify_repo(name, cfg)
    for name, r in out["repos"].items():
        for d in r["diffs"]:
            out["diffs"].append(d)
    out["ok"] = not caliber_diffs and all(
        r["ok"] for r in out["repos"].values())
    return out


def require_baseline(repos: Sequence[str] = (),
                     path: Optional[Path] = None) -> Dict[str, Any]:
    """普查脚本的入口闸。⛔ 不健康 ⇒ 拒绝出读数，而不是 SKIP 后报绿。"""
    try:
        bl = load_baseline(path)
    except (OSError, ValueError) as e:
        print("⛔ 取证基线锚不可读，拒绝出读数: %s" % e, file=sys.stderr)
        raise SystemExit(2)
    rep = verify(bl, repos)
    if not rep["ok"]:
        print("⛔ 取证基线不健康 ⇒ 拒绝出读数（fail-closed）",
              file=sys.stderr)
        for d in rep["caliber_diffs"]:
            print("   " + d, file=sys.stderr)
        for d in rep["diffs"]:
            print("   " + d, file=sys.stderr)
        raise SystemExit(2)
    return rep


# ───────────────────── 生成锚 ─────────────────────
def _smoke_for(name: str, cands: Sequence[Any]) -> List[Dict[str, str]]:
    """从同源抽取器的输出里取 3 条 (sha, src)，记 blob 内容 hash。"""
    path = Path("/tmp/realrepo") / name
    picks = [cands[i] for i in (0, len(cands) // 2, len(cands) - 1)
             if cands]
    out: List[Dict[str, str]] = []
    for c in picks:
        sha, rel = c[0], c[1]
        rc, blob = _git_blob(path, "show", "%s:%s" % (sha, rel))
        if rc != 0:
            raise RuntimeError("smoke 读不到 %s:%s" % (sha, rel))
        out.append({"sha": sha, "path": rel,
                    "blob_sha256": sha256_bytes(blob)})
    return out


def snapshot(allow_supersede: bool = False) -> Dict[str, Any]:
    """从**当前健康**的三仓库生成锚。⛔ 口径变了不许静默覆盖。"""
    cal = current_caliber()
    if BASELINE.is_file():
        try:
            old = load_baseline()
        except (OSError, ValueError):
            old = {}
        diffs = check_caliber(old.get("caliber", {}), cal)
        if diffs:
            if not allow_supersede:
                raise RuntimeError(
                    "口径签名不一致，拒绝覆盖基线（用 --allow-supersede "
                    "显式换口径，旧值会另存）: " + "; ".join(diffs))
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup = BASELINE.with_name(
                "%s_superseded_%s.json" % (BASELINE.stem, stamp))
            shutil.copy2(BASELINE, backup)
            print("旧基线另存: %s" % backup)

    repos: Dict[str, Any] = {}
    for name in REPOS:
        path = Path("/tmp/realrepo") / name
        rc, head = _git(path, "rev-parse", "HEAD")
        if rc != 0 or len(head) != 40:
            raise RuntimeError("%s: HEAD 读不出来 rc=%d got=%s"
                               % (name, rc, head))
        cf = CENSUS_DIR / CENSUS_FILE[name]
        census = json.loads(cf.read_text(encoding="utf-8"))
        want = [r["sha"] for r in census]
        cands = select_commits(path, cal["limit"], SUBDIR[name])
        got = [c[0] for c in cands]
        if not is_subsequence(want, got):
            raise RuntimeError(
                "%s: 存档读数不是候选池的保序子序列 ⇒ 不许生成锚 "
                "(census %d / select %d, census head %s vs select head %s)"
                % (name, len(want), len(got), want[:3], got[:3]))
        rc, tracked = _git(path, "ls-files")
        n_tracked = len([x for x in tracked.splitlines() if x.strip()])
        skipped = [x for x in got if x not in want]
        repos[name] = {
            "path": str(path),
            "remote": REMOTE[name],
            "head": head,
            "head_short": head[:7],
            "head_evidence": ("recovered_from_pack" if name == "boltons"
                              else "inferred_from_census_first_sha"),
            "subdir": SUBDIR[name],
            "limit": cal["limit"],
            "commit_shas": got,
            "census_shas": want,
            "n_skipped_by_probe": len(skipped),
            "skipped_shas": skipped,
            "n_tracked": n_tracked,
            "smoke": _smoke_for(name, cands),
            "census_sha256": sha256_file(cf),
        }
        print("  %-16s head=%s select=%d census=%d skipped=%s tracked=%d"
              % (name, head[:7], len(got), len(want), skipped or "-",
                 n_tracked))

    doc = {"schema": SCHEMA, "caliber": cal, "repos": repos,
           "note": "取证基线锚：HEAD 是 select_commits 尺子的零点，"
                   "重建必须按钉死的 head，不得用远端最新。"}
    #: 原子写，避免半截文件变成一把谁也说不清的尺子。
    tmp = BASELINE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, BASELINE)
    return doc


# ───────────────────── 按钉死的 sha 重建 ─────────────────────
def bootstrap(name: str, force: bool = False) -> int:
    """从远端 clone 后 **checkout 到锚里的 head**，绝不用远端最新。"""
    bl = load_baseline()
    cfg = bl["repos"][name]
    dest = Path(cfg["path"])
    if not force and verify_repo(name, cfg)["ok"]:
        print("%s: 基线已健康，不动（--force 可强制重建）" % name)
        return 0
    tmp_root = Path(tempfile.mkdtemp(prefix="realrepo_bootstrap_"))
    clone = tmp_root / name
    print("%s: clone %s" % (name, cfg["remote"]))
    p = subprocess.run(["git", "clone", "-q", cfg["remote"], str(clone)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print("clone 失败: " + (p.stderr or "").strip(), file=sys.stderr)
        return 1
    p = subprocess.run(["git", "checkout", "-q", "--detach", cfg["head"]],
                       cwd=str(clone), capture_output=True, text=True)
    if p.returncode != 0:
        print("checkout 钉死的 head 失败: " + (p.stderr or "").strip(),
              file=sys.stderr)
        return 1
    trial = dict(cfg, path=str(clone))
    rep = verify_repo(name, trial)
    if not rep["ok"]:
        print("重建后验签不通过，不替换现网基线：", file=sys.stderr)
        for d in rep["diffs"]:
            print("   " + d, file=sys.stderr)
        return 1
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if dest.exists():
        backup = tmp_root / ("%s_broken_%s" % (name, stamp))
        shutil.move(str(dest), str(backup))
        print("旧基线另存: %s" % backup)
    shutil.move(str(clone), str(dest))
    print("%s: 重建完成 head=%s 读数=%s" % (name, cfg["head_short"],
                                            rep["readings"]))
    return 0


# ───────────────────── CLI ─────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("verify", "snapshot", "bootstrap"))
    ap.add_argument("--repo", default="", help="verify/bootstrap 只做某一个仓库")
    ap.add_argument("--baseline", default="", help="verify 用另一份锚（反证用）")
    ap.add_argument("--json", action="store_true", help="verify 输出结构化读数")
    ap.add_argument("--allow-supersede", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.cmd == "snapshot":
        snapshot(args.allow_supersede)
        print("锚已写入 %s" % BASELINE)
        return 0
    if args.cmd == "bootstrap":
        if not args.repo:
            print("bootstrap 必须指定 --repo", file=sys.stderr)
            return 1
        return bootstrap(args.repo, args.force)

    path = Path(args.baseline) if args.baseline else None
    try:
        bl = load_baseline(path)
    except (OSError, ValueError) as e:
        print("⛔ 锚不可读: %s" % e, file=sys.stderr)
        return 1
    rep = verify(bl, (args.repo,) if args.repo else ())
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        for d in rep["caliber_diffs"]:
            print("FAIL " + d)
        for name, r in rep["repos"].items():
            status = "PASS" if r["ok"] else "FAIL"
            print("[%-16s] %s  %s" % (name, status, r["readings"]))
            for d in r["diffs"]:
                print("    ⛔ " + d)
        print("baseline_health=%s  repos=%d/%d  diffs=%d"
              % ("OK" if rep["ok"] else "UNHEALTHY",
                 sum(1 for r in rep["repos"].values() if r["ok"]),
                 len(rep["repos"]), len(rep["diffs"]) + len(rep["caliber_diffs"])))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

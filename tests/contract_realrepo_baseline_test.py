#!/usr/bin/env python3
"""契约：取证基线的「锚」必须可读不可被重建，且验不过时 fail-closed。

═══ 钉死什么 ═══

174（2026-11-05）：/tmp/realrepo 三仓库第二次被静默破坏，这次连
more-itertools / attrs 的 objects 都空了。探针用 `git log --format=%H`
从 HEAD 往下取前 40 个 ⇒ **HEAD 就是尺子的零点**；零点丢了重新 clone
会拿到远端最新 HEAD ⇒ 40 个 commit 全变，而且不报错。

⇒ 必须有三件事，任何一件失效都会让「跨轮读数可比」变成假的：
   A. 锚把 head / 候选序列 / 抽取器 hash 钉进版本控制；
   B. 验证用**同源** `select_commits` 对拍，不是另写一份词表；
   C. 不健康时拒绝出读数（fail-closed），不许 SKIP 后被当成通过。

⛔⛔ B 组反证是必须的：一个恒红的尺子也能让 A 组全绿。
   B0 先证明闸**能绿**，B1–B7 再逐项注入事故，且每条都核对
   「预期变红的**那个字段**」—— 只断言 ok=False 会放过「因为别的
   原因红」这种假反证。

用法：python tests/contract_realrepo_baseline_test.py
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import realrepo_baseline as rb  # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

ANCHOR = ROOT / "scripts" / "realrepo_baseline.json"

PASSES: list = []
FAILS: list = []
SKIPS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        #: ⛔ 放在**前**（contract_assert_wiring 的 B2 用 ck 里第一个
        #:    append 的变量名推断失败列表是谁）。
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


# ══════════════ A 组：锚自身（纯离线，CI 也必跑）══════════════
def group_a():
    print("\n【A】锚自身：结构、口径签名、两本账")
    if not ANCHOR.is_file():
        ck("A0 锚文件存在", False, str(ANCHOR))
        return None
    try:
        bl = json.loads(ANCHOR.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        ck("A0 锚可解析", False, str(e))
        return None

    ck("A1 schema 正确", bl.get("schema") == rb.SCHEMA,
       "实得 %s" % bl.get("schema"))
    cal = bl.get("caliber", {})
    ck("A2 caliber 必需键齐全且无哨兵",
       all(k in cal and cal[k] != rb._MISSING for k in rb.CALIBER_KEYS),
       "实得 %s" % sorted(cal))
    ck("A3 抽取器源码 hash 与当前一致（尺子没被改写）",
       rb.check_caliber(cal, rb.current_caliber()) == [],
       str(rb.check_caliber(cal, rb.current_caliber())))

    repos = bl.get("repos", {})
    ck("A4 三仓库齐全", set(rb.REPOS) <= set(repos),
       "实得 %s" % sorted(repos))
    missing = []
    for name in rb.REPOS:
        cfg = repos.get(name, {})
        for k in rb.REPO_KEYS:
            if k not in cfg or cfg[k] is None:
                missing.append("%s.%s" % (name, k))
    ck("A5 每仓库必需键齐全（缺键必须点名）", not missing, str(missing))

    ok_shape = True
    for name in rb.REPOS:
        cfg = repos.get(name, {})
        head = str(cfg.get("head", ""))
        if len(head) != 40 or cfg.get("head_short") != head[:7]:
            ok_shape = False
    ck("A6 head 是 40 位且 head_short 自洽", ok_shape)

    two_books = True
    for name in rb.REPOS:
        cfg = repos.get(name, {})
        cc, cs = cfg.get("commit_shas", []), cfg.get("census_shas", [])
        if len(cc) != cfg.get("limit") or not rb.is_subsequence(cs, cc):
            two_books = False
        if cfg.get("n_skipped_by_probe") != len(cc) - len(cs):
            two_books = False
    ck("A7 候选池 / 读数两本账分开且自洽（40 选 39 合法）", two_books)

    census_ok = True
    for name in rb.REPOS:
        cfg = repos.get(name, {})
        cf = ROOT / "scripts" / rb.CENSUS_FILE[name]
        if not cf.is_file():
            census_ok = False
            continue
        rows = [r["sha"] for r in json.loads(cf.read_text(encoding="utf-8"))]
        if rows != cfg.get("census_shas"):
            census_ok = False
        if rb.sha256_file(cf) != cfg.get("census_sha256"):
            census_ok = False
    ck("A8 读数存档与锚逐字一致且未被改写", census_ok)

    smoke_ok = all(
        repos.get(n, {}).get("smoke") and all(
            set(("sha", "path", "blob_sha256")) <= set(s)
            and len(s.get("blob_sha256", "")) == 64
            for s in repos[n]["smoke"])
        for n in rb.REPOS)
    ck("A9 每仓库有内容寻址的 smoke 项", smoke_ok)
    return bl


# ══════════════ B 组：注入反证（本地 fixture，不碰 /tmp/realrepo）══════════════
def _run(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


def _fixture(tmp: Path):
    """造一个最小 git 仓库，让同源 select_commits 恰好产出 2 个候选。"""
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "boltons").mkdir()
    (repo / "tests").mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "t@example.com")
    _run(repo, "config", "user.name", "contract")
    src = repo / "boltons" / "x.py"
    tst = repo / "tests" / "test_x.py"
    src.write_text("def f(x):\n    return x < 3\n", encoding="utf-8")
    tst.write_text("def test_x():\n    pass\n", encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "init")
    for i, (body, subj) in enumerate((
            ("def f(x):\n    return x <= 3\n", "fix off-by-one bug"),
            ("def f(x):\n    return x <= 4\n", "fix another boundary bug"))):
        src.write_text(body, encoding="utf-8")
        tst.write_text("def test_x():\n    assert f(%d)\n" % (3 + i),
                       encoding="utf-8")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", subj)
    head = _run(repo, "rev-parse", "HEAD")
    cands = rb.select_commits(repo, 40, "boltons/")
    shas = [c[0] for c in cands]
    rc, blob = rb._git_blob(repo, "show", "%s:boltons/x.py" % shas[0])
    if rc != 0 or len(shas) != 2:
        raise RuntimeError("fixture 不成立: shas=%s rc=%d" % (shas, rc))
    cfg = {
        "path": str(repo), "remote": "file:///none", "head": head,
        "head_short": head[:7], "subdir": "boltons/", "limit": 40,
        "commit_shas": shas, "census_shas": list(shas),
        "n_tracked": len(_run(repo, "ls-files").splitlines()),
        "smoke": [{"sha": shas[0], "path": "boltons/x.py",
                   "blob_sha256": rb.sha256_bytes(blob)}],
        "census_sha256": "unused-for-fixture",
    }
    bl = {"schema": rb.SCHEMA, "caliber": rb.current_caliber(),
          "repos": {"fixture": cfg}}
    return repo, cfg, bl, shas


def _has(diffs, prefix):
    return any(d.startswith(prefix) for d in diffs)


def group_b():
    print("\n【B】注入反证：闸必须能绿，且每种事故都按预期字段转红")
    tmp = Path(tempfile.mkdtemp(prefix="contract_baseline_"))
    try:
        repo, cfg, bl, shas = _fixture(tmp)
    except Exception as e:  # noqa: BLE001
        ck("B0 fixture 可构造", False, str(e))
        return

    #: ⛔ B0 先证明闸**能绿**；没有它，下面全红也可能只是闸恒红。
    base = rb.verify(bl, ("fixture",))
    ck("B0 健康 fixture 验签为绿（闸不是恒红）", base["ok"],
       str(base["diffs"] + base["caliber_diffs"]))

    d = dict(cfg, head=shas[1])
    r = rb.verify(dict(bl, repos={"fixture": d}), ("fixture",))
    ck("B1 HEAD 被换成旧 commit ⇒ 红且点名 head",
       (not r["ok"]) and _has(r["repos"]["fixture"]["diffs"], "fixture.head:"))

    bad = [shas[0] + "x", shas[1]]
    d = dict(cfg, commit_shas=bad)
    r = rb.verify(dict(bl, repos={"fixture": d}), ("fixture",))
    ck("B2 候选序列被篡改 ⇒ 红且点名 commit_shas",
       (not r["ok"]) and _has(r["repos"]["fixture"]["diffs"],
                              "fixture.commit_shas:"))

    d = {k: v for k, v in cfg.items() if k != "smoke"}
    r = rb.verify(dict(bl, repos={"fixture": d}), ("fixture",))
    ck("B3 删必需键 ⇒ 红且点名该键（缺键用哨兵）",
       (not r["ok"]) and any("smoke" in x and rb._MISSING in x
                             for x in r["repos"]["fixture"]["diffs"]))

    cal = dict(bl["caliber"], extractor_sha256="deadbeef")
    r = rb.verify(dict(bl, caliber=cal), ("fixture",))
    ck("B4 抽取器源码 hash 被改 ⇒ caliber 红且点名字段",
       (not r["ok"]) and any("extractor_sha256" in x
                             for x in r["caliber_diffs"]))

    headfile = repo / ".git" / "HEAD"
    saved = headfile.read_text(encoding="utf-8")
    headfile.unlink()
    try:
        r = rb.verify(bl, ("fixture",))
        ck("B5 .git/HEAD 丢失（169 症状）⇒ 红且点名 HEAD",
           (not r["ok"]) and any("HEAD" in x
                                 for x in r["repos"]["fixture"]["diffs"]))
    finally:
        headfile.write_text(saved, encoding="utf-8")

    #: ⛔ 169 的根因变成可回归判据：旧自检只查 `.git/HEAD` 是否存在。
    #:    从被测文件里用 AST 抽出那个函数（**同源**，不另写一份判据）。
    try:
        tree = ast.parse((ROOT / "tests" / "contract_upper_bound_test.py")
                         .read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_realrepo_ok")
        ns = {"Path": Path}
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     "<_realrepo_ok>", "exec"), ns)
        weak = ns["_realrepo_ok"]
    except Exception as e:  # noqa: BLE001
        skip("B6 弱自检同源对照", "抽不出 _realrepo_ok: %s" % e)
    else:
        hollow = tmp / "hollow"
        (hollow / ".git").mkdir(parents=True)
        (hollow / ".git" / "HEAD").write_text("ref: refs/heads/master\n",
                                              encoding="utf-8")
        d = dict(cfg, path=str(hollow))
        strong = rb.verify(dict(bl, repos={"fixture": d}), ("fixture",))
        ck("B6 只有 .git/HEAD、objects 全空时：旧自检放行 / 新闸拒绝",
           weak(str(hollow)) is True and (not strong["ok"])
           and any("HEAD" in x or "commit 对象不可读" in x
                   for x in strong["repos"]["fixture"]["diffs"]))

    #: ⛔ C 组：不健康时**拒绝出读数**，而不是 SKIP 后报绿。
    bad_path = tmp / "bad_anchor.json"
    bad_path.write_text(json.dumps(dict(bl, caliber=dict(
        bl["caliber"], extractor_sha256="deadbeef"))), encoding="utf-8")
    err = io.StringIO()
    code = None
    with contextlib.redirect_stderr(err):
        try:
            rb.require_baseline(repos=("fixture",), path=bad_path)
        except SystemExit as e:
            code = e.code
    ck("B7 坏锚 ⇒ require_baseline 以 2 退出且写明 fail-closed",
       code == 2 and "fail-closed" in err.getvalue(),
       "code=%s stderr=%s" % (code, err.getvalue()[:120]))

    absent = tmp / "no_such_anchor.json"
    code = None
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            rb.require_baseline(path=absent)
        except SystemExit as e:
            code = e.code
    ck("B8 锚缺失 ⇒ 也拒绝出读数（缺失不得读作通过）", code == 2)

    shutil.rmtree(tmp, ignore_errors=True)


# ══════════════ C 组：真实基线 ══════════════
def group_c(bl):
    print("\n【C】真实基线（本机组；CI 不在本机时明确记为未覆盖）")
    if bl is None:
        skip("C1 三仓库验签", "锚不可读")
        return
    if not all((Path("/tmp/realrepo") / n / ".git" / "HEAD").is_file()
               for n in rb.REPOS):
        skip("C1 三仓库验签",
             "取证基线不在本机（未覆盖 ≠ 通过；用 bootstrap 重建）")
        return
    rep = rb.verify(bl)
    ck("C1 三仓库全部验签通过", rep["ok"],
       str(rep["caliber_diffs"] + rep["diffs"])[:300])
    for name, r in rep["repos"].items():
        print("      %-16s %s  %s" % (
            name, "PASS" if r["ok"] else "FAIL", r["readings"]))


def main() -> int:
    bl = group_a()
    group_b()
    group_c(bl)
    print("\npass=%d  fail=%d  skip=%d" %
          (len(PASSES), len(FAILS), len(SKIPS)))
    if SKIPS:
        print("  ⛔ 跳过项（未覆盖 ≠ 通过）：" + ", ".join(SKIPS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

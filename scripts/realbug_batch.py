#!/usr/bin/env python3
"""0/N 实验的**批量驱动**：拿一批真实 bug fix 排队喂给 V6。

单个样本的说明见 `scripts/realbug_experiment.py`。这里只负责三件事：

  1. 从候选 commit 里逐个物化（用时ambari脚本）
  2. 逐个跑 N 轮 EvolutionLoop
  3. 汇总成一张表 —— ⛔ **每条单独记账**，不许只报总数

跑法：
    python scripts/realbug_batch.py \\
        --repo /tmp/realrepo/boltons --module boltons/mathutils.py \\
        --commits 5eb6cc0,efff866,c1c25da --rounds 10 --work /tmp/rbbatch
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--module", default="", help="留空=从 commit 自己改动的文件推断")
    ap.add_argument("--commits", required=True, help="逗号分隔的 bug fix sha")
    ap.add_argument("--rounds", default="10")
    ap.add_argument("--group", default="6")
    ap.add_argument("--work", required=True)
    args = ap.parse_args()

    work = Path(args.work)
    #: ⛔ import 的 `--out-root` 是**工作区根**（它自己会拼 `/tasks/<env>`），
    #:    experiment 的 `--tasks-root` 才是 tasks 目录。两边口径不同，
    #:    传同一个变量会出现 `/tasks/tasks/<env>` 的双层嵌套。
    work.mkdir(parents=True, exist_ok=True)
    tasks_root = work / "tasks"

    rows = []
    for sha in [c.strip() for c in args.commits.split(",") if c.strip()]:
        print(f"\n{'#' * 72}\n# {sha}\n{'#' * 72}", flush=True)
        #: `--module` 为空 ⇒ 从该 commit 自己改动的 `boltons/*.py` 推断
        if args.module:
            module = args.module
        else:
            out = subprocess.run(
                ["git", "show", "--name-only", "--format=", sha],
                cwd=str(args.repo), capture_output=True, text=True).stdout
            srcs = [l.strip() for l in out.splitlines()
                    if l.strip().startswith("boltons/") and l.strip().endswith(".py")]
            if len(srcs) != 1:
                rows.append({"commit": sha, "stage": "import", "ok": False,
                             "why": f"改动的源文件 {len(srcs)} 个 ⇒ 跳过"})
                print(f"  ⚠️ 跳过（改动源文件 {len(srcs)} 个，只收单文件修复）")
                continue
            module = srcs[0]
        env_id = f"real_{Path(args.repo).name}_{Path(module).stem}_{sha[:7]}"

        # 1) 物化
        p = subprocess.run(
            [PY, str(ROOT / "scripts" / "realbug_import.py"),
             "--repo", args.repo, "--commit", sha, "--module", module,
             "--env-id", env_id, "--out-root", str(work)],
            capture_output=True, text=True, cwd=str(ROOT))
        if p.returncode != 0:
            rows.append({"commit": sha, "stage": "import", "ok": False,
                         "why": (p.stdout or "")[-300:]})
            print("  ✗ 物化失败（该 bug 不适合这个判据形态）")
            continue

        # 2) 跑
        p2 = subprocess.run(
            [PY, str(ROOT / "scripts" / "realbug_experiment.py"),
             "--tasks-root", str(tasks_root), "--env", env_id,
             "--rounds", args.rounds, "--group", args.group,
             "--mode", "real", "--root", str(work / f"run_{sha[:7]}")],
            capture_output=True, text=True, cwd=str(ROOT))
        res_file = work / f"run_{sha[:7]}_result.json"
        if res_file.is_file():
            r = json.loads(res_file.read_text(encoding="utf-8"))
            r["stage"] = "run"
            #: ⛔ result.json 里没有 commit（实验脚本不知道 sha）⇒ 这里补上，
            #:    否则汇总表第一列全是 None，读数与哪个 bug 对不上。
            r["commit"] = sha
            rows.append(r)
            ok = r.get("after_n_passed") == r.get("total")
            print(f"  {'✅ 修好' if ok else '❌ 未修好'}: "
                  f"{r.get('before_n_passed')}→{r.get('after_n_passed')}"
                  f"/{r.get('total')}  提交 {r.get('committed')}")
        else:
            rows.append({"commit": sha, "stage": "run", "ok": False,
                         "why": (p2.stdout or p2.stderr or "")[-300:]})
            print("  ✗ 运行失败")

    # 3) 汇总
    print(f"\n{'=' * 72}\n汇总（⛔ 逐条记账，不看总数）\n{'=' * 72}")
    done = [r for r in rows if r.get("stage") == "run"]

    def _readable(r):
        """⛔⛔ `None` 不许流进判据比较：`None == None` 为**真**。

        运行失败的项三个字段全是 None ⇒ 直接 `a == t` 会判成"✅ 修好"。
        与第 135 条同族：None 进 `==` 会把"缺失"变成"合法值"。
        ⇒ 读数不完整的一律按**未修好**计入分母，不计入分子。
        """
        return (isinstance(r.get("after_n_passed"), int)
                and isinstance(r.get("total"), int)
                and r["total"] > 0)

    fixed_n = sum(1 for r in done if _readable(r)
                  and r["after_n_passed"] == r["total"])
    for r in rows:
        if r.get("stage") != "run":
            print(f"  {r.get('commit')}  ⚠️ 未进入评测：{(r.get('why') or '')[:70]}")
            continue
        if not _readable(r):
            print(f"  {r.get('commit')}  ⚠️ 读数不完整"
                  f"（before={r.get('before_n_passed')} "
                  f"after={r.get('after_n_passed')} total={r.get('total')}）"
                  f" ⇒ 按未修好计")
            continue
        b, a, t = r["before_n_passed"], r["after_n_passed"], r["total"]
        print(f"  {r.get('commit')}  {b}→{a}/{t}  "
              f"提交{r.get('committed')}  "
              f"{'✅ 修好' if a == t else '❌ 未修好'}")
    usable = len(done)
    print(f"\n  进入评测 {usable} 个真实 bug，修好 {fixed_n} 个 "
          f"⇒ {fixed_n}/{usable if usable else 1}")
    (work / "summary.json").write_text(
        json.dumps({"rows": rows, "n_usable": usable, "n_fixed": fixed_n},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

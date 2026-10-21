#!/usr/bin/env python3
"""契约：世界规模 ≠ 选择面（r63 缺口 A）。

背景：`--envs auto` 会把 `tasks/` 下全部合法目录读进来（当前 13 个），
但 `--dev-env` / `--heldout-env` 默认仍是 `textscore` / `eventflow` ⇒
另外 11 个环境**只以冻结题身份进入非回归闸**，既不进发展集也不进 held-out。
原实现对此完全静默：读 `n_envs=13` 的人会以为选择面也是 13。

本契约守两件事：
  A 纯函数 `env_roles()` 分得清「参与选择」与「只守不选」，且不吞顺序；
  B 它真的进了 `run_config`（否则等于没有 —— 记账层收不到就等于不存在）。

⛔ 反证不是摆设：A4 用两组不同规模的输入做**结构性对照**，若函数返回常量
   （例如 frozen_only 恒空），A1/A4 必有一条转红；A5 守「两种含义不许共用
   一个字段」（note 空 ⇔ frozen_only 空，不许用空串冒充"已确认没有"）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

#: ⛔ 显式声明（而非靠"没声明就只跑默认模式"兜底）：check 会把未声明当成
#:    一种**未知**，每次全量验收都报一句警告 —— 降噪的办法是声明真实情况，
#:    不是把警告关掉（"没声明 ≠ 只有一种模式"）。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = sys.executable
FAILED = []


def ck(name, ok, detail=""):
    print(("✅ " if ok else "❌ ") + name + ("" if ok else f"  ⟵ {detail}"))
    if not ok:
        FAILED.append(name)


from aether.cli import DEFAULT_ENVS, env_roles  # noqa: E402

print("\nA 组：纯函数 —— 世界多大 / 选择面多大")
names13 = ("textscore", "eventflow", "stateflow", "scheduler",
           "chaos", "core", "encoder", "shlex", "textwrap",
           "token", "utils", "textscore2", "eventflow2")
r13 = env_roles(names13, "textscore", "eventflow")
ck("A1 ⛔ 13 个世界里只有 2 个参与选择，其余 11 个只守不选",
   r13["n_world"] == 13 and r13["n_selection"] == 2
   and r13["n_frozen_only"] == 11, str(r13))
ck("A2 frozen_only 保留传入顺序（discover_envs 的顺序是历史可比性的一部分）",
   r13["frozen_only"] == list(names13[2:]), str(r13["frozen_only"])[:80])
ck("A3 只有 dev/heldout 两个 ⇒ frozen_only 空、note 空（不制造噪音）",
   (lambda r: r["n_frozen_only"] == 0 and r["frozen_only"] == []
    and r["note"] == "")(env_roles(("textscore", "eventflow"),
                                  "textscore", "eventflow")),
   str(env_roles(("textscore", "eventflow"), "textscore", "eventflow")))

#: ⛔ A4 反证：结构性对照。同一函数、两种规模，`n_frozen_only` 必须不同。
#:    若它返回常量（比如永远 0），这里必红 —— 证明 A1 不是恒真。
r4 = env_roles(DEFAULT_ENVS, "textscore", "eventflow")
ck("A4 ⛔ 反证：4 个世界 vs 13 个世界，n_frozen_only 必须不同"
   "（返回常量则此处转红）",
   r4["n_frozen_only"] == 2 and r13["n_frozen_only"] == 11,
   f'{r4["n_frozen_only"]} vs {r13["n_frozen_only"]}')
ck("A5 ⛔ note 空 ⇔ frozen_only 空（不许用空串冒充「已确认没有」）",
   (r4["note"] != "") == (bool(r4["frozen_only"]))
   and (r13["note"] != "") == (bool(r13["frozen_only"])),
   f'{r4["note"]!r} / {r13["note"]!r}')
ck("A6 ⛔ dev 或 heldout 不在名单里时如实记 1（不许四舍五入成 2）",
   env_roles(("textscore", "chaos"), "textscore", "eventflow")["n_selection"] == 1,
   str(env_roles(("textscore", "chaos"), "textscore", "eventflow")))

print("\nB 组：接线 —— 真跑一次，账本里读得到 envs_role")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    (ws / "tasks").symlink_to(ROOT / "tasks")
    (ws / "data").mkdir()
    p = subprocess.run(
        [PY, "-B", "-m", "aether.cli", "evolve", "--root", str(ws),
         "--rounds", "1", "--group", "1", "--max-tasks", "2",
         "--envs", "textscore,eventflow,scheduler"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=540)
    rows = []
    lg = ws / "data" / "ledger.jsonl"
    if lg.exists():
        rows = [json.loads(x) for x in lg.read_text(encoding="utf-8").splitlines()
                if x.strip()]
    rc = [r for r in rows if r["kind"] == "evolution.run_config"]
    ck("B0 这一轮跑成了，且**记在临时库**（生产库零写入）",
       p.returncode == 0 and len(rc) == 1, f"rc={p.returncode} n={len(rc)}")
    pl = rc[0]["payload"] if rc else {}
    ck("B1 ⛔ run_config 里有 envs_role（记账层收不到 = 等于没做）",
       isinstance(pl.get("envs_role"), dict), str(pl.get("envs_role"))[:120])
    role = pl.get("envs_role") or {}
    ck("B2 三个环境里只有 scheduler 是只守不选",
       role.get("frozen_only") == ["scheduler"] and role.get("n_world") == 3
       and role.get("n_selection") == 2, str(role))
    ck("B3 n_envs 与 envs_role.n_world 必须一致（两个字段不许打架）",
       pl.get("n_envs") == role.get("n_world") == 3,
       f'{pl.get("n_envs")} vs {role.get("n_world")}')
    ck("B4 同一份 run_config 里 envs 列表仍是三个（没被删减）",
       pl.get("envs") == ["textscore", "eventflow", "scheduler"],
       str(pl.get("envs")))
    ck("B5 stderr 里明说了「其余只进非回归闸」（人能看见，不只账本能）",
       "只进非回归闸" in (p.stderr or ""), (p.stderr or "")[-200:])

print("\nC 组：反证 —— 默认口径下不该冒出新字段的噪音")
r_def = env_roles(DEFAULT_ENVS, "textscore", "eventflow")
ck("C1 默认四环境仍有 2 个只守不选（默认口径本来就是这样，不许假装没有）",
   r_def["frozen_only"] == ["stateflow", "scheduler"], str(r_def["frozen_only"]))
ck("C2 反证：把 dev/heldout 换成 stateflow/scheduler ⇒ 只守不选的那两个换人",
   (lambda r: r["frozen_only"] == ["textscore", "eventflow"])(
       env_roles(DEFAULT_ENVS, "stateflow", "scheduler")),
   str(env_roles(DEFAULT_ENVS, "stateflow", "scheduler")["frozen_only"]))

print()
if FAILED:
    print(f"❌ {len(FAILED)} 项失败：" + "; ".join(FAILED[:6]))
    raise SystemExit(1)
print("✅ 全部通过")

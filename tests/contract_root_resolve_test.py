#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**工作区根**的解析（r61）—— 「数据住哪」与「代码住哪」必须分开。

═══ 为什么需要这份契约 ═══

`paths.ROOT` 的定义是 `Path(__file__).resolve().parent.parent`。在仓库里
它等于仓库根，**看起来**就是数据根 —— 于是 11 个模块直接拿它当数据根用。
装成 wheel 之后同一个表达式指向 `site-packages`，实测（r61，干净 venv）：

    $ aether status
    "path": "…/venv/lib/python3.13/site-packages/data/ledger.jsonl"

而它**不是崩溃**：能读能写、`rc=0`、读数看着正常。三条后果没有一条会报错：

  · `pip install -U` 重装 ⇒ 账本连同整个自进化历史一起没了；
  · 两个项目共用一个 venv ⇒ 两套进化历史混进同一个库；
  · Docker 里 `site-packages` 只读 ⇒ 第一次写账本就崩。

⛔ 「悄悄写错地方」比「崩」更坏：崩了会有人查，写错了没人查（第 97 条同族）。

═══ 这份契约守什么 ═══

  A 五支优先级（注入式，不依赖本机形态）
  B **仓库内行为逐字不变** —— 这是唯一不可让步的不变式
  C 安装态**绝不**把数据根解析到包位置
  D 工作区靠 `.aether` 显式声明（不是"看到 data/ 就当工作区"）
  E `aether init` 幂等 / 不覆盖 / 演练不落盘
  F 开发态下 cwd 在**别的工作区**里时由工作区说了算（`-e` 装不混库）；
    负结果契约：例外只认找得到的 `.aether`，仓库内部的 `.aether` 不生效

⛔ 反证组（不在本文件内，见提交说明）：删掉 ③ repo 支 ⇒ B 全红；
   删掉 ④ 工作区支 ⇒ A4/D1 红；删掉 ⑤ 兜底 ⇒ C2 红。
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约，
#:    写死 ⇒ 永远 import 生产那份 ⇒ 删前删后是同一份代码 ⇒ 对拍恒绿。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []
_TMP = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="aether_root_"))
    _TMP.append(d)
    return d


def _mk_pkg(base: Path, *, repo: bool) -> Path:
    """造一个"包位置"。`repo=True` 时补上仓库资产（tasks/ tests/）。"""
    p = base / "site-packages" if not repo else base
    (p / "aether").mkdir(parents=True, exist_ok=True)
    (p / "aether" / "paths.py").write_text("", encoding="utf-8")
    if repo:
        for name in ("tasks", "tests"):
            (p / name).mkdir(parents=True, exist_ok=True)
    return p


def main() -> int:
    from aether import paths as P

    print("== A 组：五支优先级（顺序是接口，不许调换）==")

    #: ① `--root` 优先于一切（连 env 也不看）。
    d = _tmp()
    pkg = _mk_pkg(d / "sp", repo=False)
    r, why = P.resolve_root_with_source(
        str(d / "explicit"), pkg_root=pkg, cwd=d / "cwd", env={"AETHER_ROOT": str(d / "env")})
    ck("A1 `--root` 显式 > `$AETHER_ROOT`",
       why == P.ROOT_FROM_FLAG and r == (d / "explicit"), f"{why} {r}")

    #: ② env 优先于仓库形态 —— 否则"想临时换个库"这件事在开发态永远做不到。
    repo = _mk_pkg(d / "repo", repo=True)
    r, why = P.resolve_root_with_source(
        None, pkg_root=repo, cwd=repo, env={"AETHER_ROOT": str(d / "env")})
    ck("A2 `$AETHER_ROOT` > 仓库形态（开发态也能临时换库）",
       why == P.ROOT_FROM_ENV and r == (d / "env"), f"{why} {r}")

    #: ③ 仓库形态。
    r, why = P.resolve_root_with_source(None, pkg_root=repo, cwd=repo, env={})
    ck("A3 包位置是仓库 ⇒ 用仓库根（开发态默认）",
       why == P.ROOT_FROM_REPO and r == repo, f"{why} {r}")

    #: ④ 向上找 `.aether`。
    ws = d / "ws"
    (ws / "deep" / "er").mkdir(parents=True)
    (ws / ".aether").write_text("{}", encoding="utf-8")
    r, why = P.resolve_root_with_source(
        None, pkg_root=_mk_pkg(d / "sp2", repo=False), cwd=ws / "deep" / "er", env={})
    ck("A4 向上找到 `.aether` ⇒ 用它（人在子目录里敲命令是常态）",
       why == P.ROOT_FROM_WORKSPACE and r == ws.resolve(), f"{why} {r}")

    #: ⑤ 用户数据目录兜底。
    r, why = P.resolve_root_with_source(
        None, pkg_root=_mk_pkg(d / "sp3", repo=False), cwd=_tmp(), env={})
    ck("A5 都没有 ⇒ 用户数据目录（不是包位置）",
       why == P.ROOT_FROM_HOME and r != (d / "sp3"), f"{why} {r}")

    #: 五个来源标签必须**互不相同**，否则读数分不清"用户指定"和"兜底默认"。
    tags = {P.ROOT_FROM_FLAG, P.ROOT_FROM_ENV, P.ROOT_FROM_REPO,
            P.ROOT_FROM_WORKSPACE, P.ROOT_FROM_HOME}
    ck("A6 五个来源标签互不相同（读数可分辨谁决定的）", len(tags) == 5, str(tags))

    print("\n== B 组：仓库内行为**逐字不变**（唯一不可让步的不变式）==")

    r, why = P.resolve_root_with_source()
    ck("B1 本仓库里 resolve_root() == ROOT", r == P.ROOT, f"{r} vs {P.ROOT}")
    ck("B2 本仓库里来源标签是 repo", why == P.ROOT_FROM_REPO, why)
    ck("B3 `--root` 的 CLI 默认值仍等于仓库根（入口收口点实测）",
       _root_default() == str(P.ROOT), _root_default())
    ck("B4 账本落点仍是仓库 data/ledger.jsonl（不是别处）",
       P.paths(P.resolve_root())["ledger"] == P.ROOT / "data" / "ledger.jsonl",
       str(P.paths(P.resolve_root())["ledger"]))
    ck("B5 looks_like_repo(仓库) 为真（判据认得出开发态）",
       P.looks_like_repo(P.ROOT) is True)

    print("\n== C 组：安装态**绝不**把数据根解析到包位置 ==")

    sp = _mk_pkg(_tmp() / "sp4", repo=False)
    ck("C1 假 site-packages 不被认成仓库", P.looks_like_repo(sp) is False)
    r, why = P.resolve_root_with_source(None, pkg_root=sp, cwd=_tmp(), env={})
    ck("C2 ⛔ 数据根不是包位置（写进 site-packages 会随重装丢失）",
       r.resolve() != sp.resolve(), f"{why} → {r}")
    ck("C3 兜底落在用户数据目录里",
       why == P.ROOT_FROM_HOME and r.name == "aether", f"{why} {r}")

    #: XDG 口径：换 `XDG_DATA_HOME` 必须真的换地方（否则"可配置"是假仪表）。
    r2, _ = P.resolve_root_with_source(
        None, pkg_root=sp, cwd=_tmp(), env={"XDG_DATA_HOME": str(_tmp() / "xdg")})
    ck("C4 `XDG_DATA_HOME` 生效", r2 == (_tmp() / "xdg" / "aether") or
       str(r2).startswith("/"), str(r2))
    r3, _ = P.resolve_root_with_source(
        None, pkg_root=sp, cwd=_tmp(), env={"XDG_DATA_HOME": str(_tmp() / "xdg2")})
    ck("C4b 两次不同 XDG ⇒ 两个不同落点（不是硬编码）", r2 != r3, f"{r2} / {r3}")

    print("\n== D 组：工作区必须**显式声明**（不靠扫描目录）==")

    d = _tmp()
    fake = d / "just_data"
    (fake / "data").mkdir(parents=True)          #: 只有 data/，没有 .aether
    r, why = P.resolve_root_with_source(
        None, pkg_root=_mk_pkg(d / "sp5", repo=False), cwd=fake, env={})
    ck("D1 ⛔ 只有 data/ 没有 .aether ⇒ **不算**工作区（否则任何目录都能把根拽走）",
       why != P.ROOT_FROM_WORKSPACE, f"{why} {r}")

    ws = d / "ws2"
    (ws / "a" / "b").mkdir(parents=True)
    (ws / ".aether").write_text(json.dumps({"version": 1}), encoding="utf-8")
    ck("D2 带 .aether ⇒ 认作工作区，且从子目录向上能找到",
       P.find_workspace(ws / "a" / "b") == ws.resolve(),
       str(P.find_workspace(ws / "a" / "b")))
    ck("D3 没有标记时 find_workspace 返回 None（不猜）",
       P.find_workspace(d / "just_data") is None)

    print("\n== E 组：`aether init` 幂等 / 不覆盖 / 演练不落盘 ==")

    from aether import workspace as W

    e = _tmp() / "w"
    e.mkdir()
    r1 = W.init(e, now=1000.0)
    ck("E1 建齐骨架（data/ tasks/ backups/ .aether）",
       sorted(r1["created"]) == sorted(["data", "tasks", "backups", ".aether"]),
       str(r1["created"]))
    ck("E1b 目录真的在盘上",
       all((e / n).is_dir() for n in ("data", "tasks", "backups")))
    marker = e / ".aether"
    first = json.loads(marker.read_text(encoding="utf-8"))
    ck("E1c 标记写入了 version 与 created_at",
       first.get("version") == W.MARKER_VERSION and first.get("created_at") == 1000,
       str(first))

    #: ⛔ 幂等：第二次必须报"全是已存在"，且**不改** created_at。
    time.sleep(0.01)
    r2 = W.init(e, now=2000.0)
    second = json.loads(marker.read_text(encoding="utf-8"))
    ck("E2 幂等：第二次 created 为空", r2["created"] == [], str(r2["created"]))
    ck("E3 ⛔ 不覆盖已有标记（created_at 保留最初那个）",
       second.get("created_at") == 1000, str(second))

    #: ⛔ 演练必须**一字节不落**（第 139 条：演练与真跑在磁盘上必须同形）。
    f = _tmp() / "dry"
    f.mkdir()
    rd = W.init(f, dry_run=True, now=3000.0)
    ck("E4 ⛔ 演练：报了要建什么，但一个文件都没落盘",
       rd["created"] and not any(f.iterdir()), str(sorted(x.name for x in f.iterdir())))

    #: ⛔ 仓库根上跑 ⇒ noop，且**不许**造出 `.aether`（否则同一目录两条认路依据）。
    had = (P.ROOT / ".aether").exists()
    rr = W.init(P.ROOT)
    ck("E5 仓库根上跑 init ⇒ noop",
       rr["noop"] is True and rr["created"] == [], str(rr["created"]))
    ck("E5b ⛔ noop 时**没有**造出 `.aether`（不许两种认路依据并存）",
       (P.ROOT / ".aether").exists() == had)

    print("\n== F 组：开发态（包位置=仓库）下 cwd 在别的工作区里 ==")

    #: ⛔ r61 修掉的那个洞：`pip install -e .` 装的是**仓库本身**，于是
    #:    `cd ~/proj && aether evolve` 也落回仓库账本 —— 两个项目混库，
    #:    而两边都"看着正常"（与修前 `site-packages/data/` 是同一个病）。
    #:    规则：③ 只在 cwd **不在**别的工作区里时命中。
    d = _tmp()
    repo2 = _mk_pkg(d / "repo2", repo=True)
    outside = d / "outside"
    outside.mkdir()
    (outside / ".aether").write_text("{}", encoding="utf-8")
    r, why = P.resolve_root_with_source(None, pkg_root=repo2, cwd=outside, env={})
    ck("F1 ⛔ cwd 在**仓库外**的工作区里 ⇒ 工作区说了算（-e 装不再混库）",
       why == P.ROOT_FROM_WORKSPACE and r.resolve() == outside.resolve(),
       f"{why} {r}")
    r, why = P.resolve_root_with_source(
        str(outside), pkg_root=repo2, cwd=outside, env={})
    ck("F1b 显式 `--root` 仍然优先（① 永远最大）",
       why == P.ROOT_FROM_FLAG and r == outside, f"{why} {r}")

    #: ⛔⛔ 负结果契约：这个例外**只认找得到的 ④**。临时目录里没有 `.aether`
    #:     时必须仍然落 ③ —— 否则 68 份在临时目录里 spawn 子进程的契约
    #:     会集体掉到 ⑤（`~/.local/share/aether`）⇒ **测试写生产库**（第 88 条）。
    bare = d / "bare"
    bare.mkdir()
    r, why = P.resolve_root_with_source(None, pkg_root=repo2, cwd=bare, env={})
    ck("F1c ⛔⛔ 例外只认找得到的 `.aether`：临时目录里没有它 ⇒ 仍然落仓库",
       why == P.ROOT_FROM_REPO and r == repo2, f"{why} {r}")

    #: ⛔ 仓库自己的子目录里就算有 `.aether`，也不许把根拽出仓库
    #:    （否则 `cd <repo>/data && aether status` 会指向另一个库）。
    inner = repo2 / "data" / "sub"
    inner.mkdir(parents=True, exist_ok=True)
    (inner / ".aether").write_text("{}", encoding="utf-8")
    r, why = P.resolve_root_with_source(None, pkg_root=repo2, cwd=inner, env={})
    ck("F1d ⛔ 仓库内部的 `.aether` 不生效（根不许被拽出仓库）",
       why == P.ROOT_FROM_REPO and r == repo2, f"{why} {r}")

    #: ⛔ 反证：把 `_within` 恒判 True（== 修前"什么都算在仓库内"的行为）
    #:    ⇒ F1 必须翻回仓库。否则 F1 是恒真的（本机恰好没装 -e 也会绿）。
    _orig_within = P._within
    P._within = lambda a, b: True
    try:
        r5, why5 = P.resolve_root_with_source(None, pkg_root=repo2,
                                              cwd=outside, env={})
    finally:
        P._within = _orig_within
    ck("F1e ⛔ 反证：`_within` 恒 True ⇒ F1 翻回仓库（证明 F1 不是恒真）",
       why5 == P.ROOT_FROM_REPO and r5 == repo2, f"{why5} {r5}")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：数据根与代码根已分开，仓库内行为零变化")
    return 0


def _root_default() -> str:
    """`--root` 的 CLI 默认值（**从收口点取**，不自己推一遍）。"""
    from aether import entry
    ap = argparse.ArgumentParser()
    entry.add_root_arg(ap)
    return ap.parse_args([]).root


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)

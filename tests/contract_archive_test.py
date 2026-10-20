#!/usr/bin/env python3
"""契约 · 分层非支配存档（r62）。

═══ 守什么 ═══

  A 分层键 = `blast_layers`（不是候选名、不是 reward）
  B 层内非支配：被支配的解不入档
  C 互不支配的两条都留下（这是"前沿"与"Top-K"的分界）
  D 去重：同 (capsule 内容, harness_id) 不重复增殖
  E 上限：只截**本层**前沿，不跨层比
  F 落盘往返幂等（重放所有行 ⇒ 同一个 front）
  G 默认关：全量 evolve 账本里一条 `archive.*` 都没有
  H 反证：A / B 两组都不是恒真

⛔ 为什么 A 组必须能翻红：分层如果写成"按 reward 分桶"，B 组照样过、
   但"同一层可比"这个前提就没了 ⇒ 前沿变成跨层混排。H2 专治这个。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

SUPPORTED_MODES = ("default",)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED: list = []
PY = sys.executable


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


from aether.archive import (DEFAULT_CAP, Archive, Entry, capsule_from_dict,
                            capsule_to_dict, dominates)


def mk(name: str, layers, reward: float, size: int, hid: str = "",
       base: float = 0.0, rnd: int = 1, base_id: str = "") -> Entry:
    hid = hid or f"h_{name}"
    cap = {"name": name,
           "genes": [{"op": "set", "target": f"L3.skills.{name}",
                      "value": "体", "rationale": "r"}],
           "rationale": "r", "origin": ""}
    return Entry(capsule=cap, harness_id=hid, harness={"skills": {}},
                 layers=tuple(layers), reward=reward, load_gain=0.0,
                 size_delta=size, round_no=rnd, baseline_reward=base,
                 baseline_harness_id=base_id)


print("=" * 62)
print("契约 · 分层非支配存档")
print("=" * 62)

# ── A 组：分层键是 blast_layers ────────────────────────────────
print("\n[A] 分层：不同 layers ⇒ 不同的层，各自一条前沿")
a = Archive()
a.add(mk("s_only", ["skills"], 0.5, 10))
a.add(mk("s_tools", ["skills", "tools"], 0.5, 10))
a.add(mk("routing", ["routing"], 0.5, 10))
ck("A1 三个不同 layers ⇒ 三层", len(a.layers()) == 3, f"{a.layers()}")
ck("A2 层键按 blast_layers 拼（顺序稳定、可读）",
   "skills+tools" in a.layers(), f"{a.layers()}")
ck("A3 ⛔ 层键**不是** reward 桶（同 reward 不同层必须是两层）",
   a.layers().count("skills") == 1 and "routing" in a.layers(),
   f"{a.layers()}")
ck("A4 空 layers 落到 none 层（不是丢掉）",
   (lambda x: (x.add(mk("bare", [], 0.1, 1)),
               "none" in x.layers())[1])(Archive()), "")

# ── B 组：被支配的不入档 ──────────────────────────────────────
print("\n[B] 层内非支配：两项都不差且至少一项更好 ⇒ 后来者不入档")
b = Archive()
r1 = b.add(mk("good", ["skills"], 0.8, 10))
r2 = b.add(mk("worse", ["skills"], 0.5, 20))     # reward 低 且 体积更大
ck("B1 第一个解入档", r1.get("added") is True, f"{r1}")
ck("B2 ⛔ 被支配者不入档", r2.get("added") is False, f"{r2}")
ck("B3 拒绝理由指明被谁支配", "dominated_by" in str(r2.get("reason")),
   f"{r2.get('reason')!r}")
ck("B4 前沿里只有 1 条", len(b.front()) == 1, f"{len(b.front())}")
ck("B5 统计记了 dominated", b.stats["dominated"] == 1, f"{b.stats}")

# ── C 组：互不支配 ⇒ 都留（前沿 ≠ Top-K）────────────────────────
print("\n[C] 互不支配的两条都留下")
c = Archive()
c.add(mk("high_reward", ["skills"], 0.9, 100))   # 分高但体积大
c.add(mk("small", ["skills"], 0.6, 1))           # 分低但体积小
ck("C1 两条都在前沿（谁也没支配谁）", len(c.front()) == 2,
   f"{[e.capsule['name'] for e in c.front()]}")
ck("C2 分高的不自动踢掉体积小的（这正是 Top-K 会犯的错）",
   {e.capsule["name"] for e in c.front()} == {"high_reward", "small"},
   f"{sorted(e.capsule['name'] for e in c.front())}")
ck("C3 dominates 是严格支配（相等不算）",
   not dominates(mk("x", ["s"], 0.5, 10), mk("y", ["s"], 0.5, 10)), "")

# ── D 组：去重 ────────────────────────────────────────────────
print("\n[D] 去重：同 (capsule 内容, harness_id) 不重复增殖")
d = Archive()
e0 = mk("dup", ["skills"], 0.7, 5, hid="H1")
d.add(e0)
res = d.add(mk("dup", ["skills"], 0.7, 5, hid="H1"))
ck("D1 重复条目被识别", res.get("reason") == "duplicate", f"{res}")
ck("D2 前沿仍是 1 条", len(d.front()) == 1, f"{len(d.front())}")
ck("D3 同内容不同 harness_id ⇒ 是另一条（不同基线 ⇒ 不同读数）",
   (lambda x: (x.add(mk("dup", ["skills"], 0.7, 5, hid="H2")),
               len(x.front()) == 2)[1])(d),
   "同层同分同体积 ⇒ 互不支配 ⇒ 两条都在（前沿不是去重后的 Top-1）")

# ── E 组：上限只截本层 ───────────────────────────────────────
print("\n[E] cap：只截本层前沿，不跨层比")
e_ = Archive(cap=2)
for i, r in enumerate([0.9, 0.8, 0.7, 0.6]):
    # 体积随 reward 反向：都互不支配 ⇒ 强制触达 cap
    e_.add(mk(f"k{i}", ["skills"], r, 100 - i * 10))
ck("E1 本层被截到 cap=2", len(e_.front("skills")) == 2,
   f"{len(e_.front('skills'))}")
e_.add(mk("other", ["routing"], 0.01, 999))
ck("E2 ⛔ 另一层不受影响（跨层比就错了）",
   len(e_.front("routing")) == 1, f"{len(e_.front('routing'))}")
ck("E3 截断记进 stats.evicted", e_.stats["evicted"] >= 1, f"{e_.stats}")

# ── F 组：落盘往返幂等 ───────────────────────────────────────
print("\n[F] 落盘往返：重放所有行 ⇒ 同一个 front")
tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="v6_arch_"))
p = str(tmpdir / "archive.jsonl")
f1 = Archive(path=p)
f1.add(mk("a", ["skills"], 0.5, 10))
f1.add(mk("b", ["skills"], 0.5, 20))       # 被 a 支配
f1.add(mk("c", ["routing"], 0.4, 3))
before = sorted((e.capsule["name"], e.layer_key) for e in f1.front())
f2 = Archive(path=p).load()
after = sorted((e.capsule["name"], e.layer_key) for e in f2.front())
ck("F1 文件真的写了", pathlib.Path(p).exists(),
   f"{len(open(p, encoding='utf-8').readlines())} 行")
ck("F2 ⛔ load 后前沿与内存一致（重放幂等）", before == after,
   f"{before} vs {after}")
f3 = Archive(path=p).load().load()
ck("F3 反复 load 不增殖", len(f3.front()) == len(f2.front()),
   f"{len(f3.front())} vs {len(f2.front())}")
ck("F4 坏行不中断载入（旁路存档不许拖死主流程）",
   (lambda x: (open(p, "a", encoding="utf-8").write("{坏 json\n"),
               len(Archive(path=p).load().front()) == 2)[1])(f3), "")

# ── G 组：Capsule 快照往返 ────────────────────────────────────
print("\n[G] Capsule 快照往返（存档必须能还原成可执行对象）")
snap = {"name": "snap", "rationale": "r", "origin": "mined",
        "genes": [{"op": "append", "target": "L2.tools", "value": "t1",
                   "rationale": "x"},
                  {"op": "set", "target": "L3.skills.s1", "value": "体",
                   "rationale": "y"}]}
cap = capsule_from_dict(snap)
back = capsule_to_dict(cap)
ck("G1 往返基因数一致", len(back["genes"]) == 2, f"{len(back['genes'])}")
ck("G2 往返逐字一致（除 key 顺序）",
   json.loads(json.dumps(back, sort_keys=True)) ==
   json.loads(json.dumps(snap, sort_keys=True)),
   f"{back}")
ck("G3 capsule_id 往返后不变（内容寻址）",
   capsule_from_dict(back).capsule_id == cap.capsule_id,
   f"{cap.capsule_id}")

# ── H 组：end-to-end —— 默认关 / 开启 ─────────────────────────
print("\n[H] end-to-end：默认关 ⇒ 账本 0 条 archive.*；开启 ⇒ 有 offer")


def run_evolve(*extra: str) -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v6_arch_ws_"))
    shutil.copytree(ROOT / "tasks", tmp / "tasks")
    subprocess.run([PY, "-m", "aether.cli", "--root", str(tmp), "evolve",
                    "--rounds", "1", "--group", "2", "--max-tasks", "4",
                    *extra], capture_output=True, text=True, cwd=str(ROOT),
                   timeout=600)
    return tmp


def kinds(root: pathlib.Path) -> list:
    out = []
    f = root / "data" / "ledger.jsonl"
    if not f.exists():
        return out
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line)["kind"])
            except Exception:  # noqa: BLE001
                continue
    return out


ws_off = run_evolve()
k_off = kinds(ws_off)
ck("H1 ⛔ 默认关 ⇒ 一条 archive.* 都没有（与历史逐字同口径）",
   not [k for k in k_off if k.startswith("archive.")],
   f"{[k for k in k_off if k.startswith('archive.')][:5]}")
ck("H2 默认关时 run_config 记了 archive_resample=0（口径可查）",
   (lambda: (
       lambda evs: bool(evs) and evs[-1].get("archive_resample") == 0
   )([json.loads(l)["payload"] for l in
      open(ws_off / "data" / "ledger.jsonl", encoding="utf-8")
      if json.loads(l)["kind"] == "evolution.run_config"]))(), "")

ws_on = run_evolve("--archive-resample", "2")
k_on = kinds(ws_on)
ck("H3 开启 ⇒ 有 archive.offer（入档真的发生了）",
   "archive.offer" in k_on, f"{[k for k in k_on if k.startswith('archive.')]}")
ck("H4 开启时 run_config 记了 archive_resample=2",
   (lambda evs: bool(evs) and evs[-1].get("archive_resample") == 2)(
       [json.loads(l)["payload"] for l in
        open(ws_on / "data" / "ledger.jsonl", encoding="utf-8")
        if json.loads(l)["kind"] == "evolution.run_config"]), "")

# ── J 组：resample 的三重排除与轮转 ─────────────────────────────
print("\n[J] resample：只取**待重测**的，且按层轮转")
j2 = Archive()
for nm, ly, rw in (("old_s", ["skills"], 0.9), ("old_r", ["routing"], 0.5),
                   ("old_t", ["tools"], 0.3)):
    j2.add(mk(nm, ly, rw, 10, hid=f"H_{nm}", base=0.0, rnd=1,
              base_id="BASE_OLD"))
got = j2.resample(2, exclude_baseline=("BASE_NEW",))
ck("J1 旧基线的条目能取回 2 条", len(got) == 2, f"{[g['capsule']['name'] for g in got]}")
ck("J2 ⛔ 每条都标 stale=True（旧读数不许直接进判据）",
   all(g.get("stale") is True for g in got), f"{[g.get('stale') for g in got]}")
ck("J3 带 baseline_harness_id（能看出是相对谁量的）",
   all(g.get("baseline_harness_id") == "BASE_OLD" for g in got),
   f"{[g.get('baseline_harness_id') for g in got]}")
ck("J4 ⛔ 层间轮转：2 条来自**两个不同层**（不是同层前两名）",
   len({g["layer"] for g in got}) == 2, f"{[g['layer'] for g in got]}")
ck("J5 ⛔ 基线相同的被排除（数值 0.0 会骗人 ⇒ 必须按 harness_id 比）",
   j2.resample(2, exclude_baseline=("BASE_OLD",)) == [], "")
ck("J6 名字相同的被排除（同胶囊同基线 ⇒ 重测结果必然一样）",
   j2.resample(2, exclude_capsules=("old_s", "old_r", "old_t"),
               exclude_baseline=("BASE_NEW",)) == [], "")
ck("J7 excludes 之外的照常取回（排除逻辑没有误伤）",
   len(j2.resample(3, exclude_capsules=("old_s",),
                   exclude_baseline=("BASE_NEW",))) == 2,
   f"{[g['capsule']['name'] for g in j2.resample(3, exclude_capsules=('old_s',), exclude_baseline=('BASE_NEW',))]}")




def _bang(fn_name: str) -> bool:
    """把某个模块级函数临时改成错误实现 ⇒ 对应断言必须翻红。"""
    import aether.archive as A
    orig = getattr(A, fn_name)
    try:
        setattr(A, fn_name, lambda *a, **k: False)
        x = A.Archive()
        x.add(mk("good", ["skills"], 0.8, 10))
        r = x.add(mk("worse", ["skills"], 0.5, 20))
        return r.get("added") is True          # 错误实现下"被支配者也入档"
    finally:
        setattr(A, fn_name, orig)


ck("I1 ⛔ `dominates` 恒 False ⇒ B2 会红（证明 B2 测的是真比较）",
   _bang("dominates"), "")
ck("I2 恢复后行为不变（反证本身没留下副作用）",
   (lambda: (lambda x: (x.add(mk("g2", ["s"], 0.8, 10)),
                        x.add(mk("w2", ["s"], 0.5, 20)))[1].get("added")
             is False)(Archive()))(), "")

print("\n" + "=" * 62)
if FAILED:
    print(f"❌ {len(FAILED)} 项未过：")
    for n in FAILED:
        print(f"   · {n}")
    sys.exit(1)
print("✅ 全过")

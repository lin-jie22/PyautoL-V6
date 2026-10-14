"""命令行入口：让整套机制**离线可跑**（自进化系统的判据必须可回归）。

    python -m aether.cli status            账本完整性 + 库统计
    python -m aether.cli synth             合成任务（看 yield，不是看尝试次数）
    python -m aether.cli evolve --rounds 3 跑一轮完整 self-harness 进化
    python -m aether.cli verify-chain       校验账本哈希链（跨语言监督器同款判据）

⛔ 所有命令**默认离线**：不联网、不调模型。要接真模型用 --provider http。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent


#: 自带环境。**四个是刻意的**：两个只能区分"变强"与"讨好一个环境"，
#: 四个才能让 held-out 有难度跨度、且 dev/held-out 可以任意配对。
#: 解法形状刻意不同：数值归一化 / 流式聚合 / 图搜索 / 扫描线+贪心。
DEFAULT_ENVS = ("textscore", "eventflow", "stateflow", "scheduler")

#: 任务类别。**路由按它来**，不按环境名 —— 见 TemplateProposer.route_key。
TASK_CLASS = "repair"


def _paths(root: Path) -> Dict[str, Path]:
    return {
        "ledger": root / "data" / "ledger.jsonl",
        "db": root / "data" / "aether.db",
        "memory": root / "data" / "memory.json",
        "task": root / "tasks" / "textscore",
        "tasks_root": root / "tasks",
    }


# ──────────────────────────────────────────────────────────────────
# 各子命令
# ──────────────────────────────────────────────────────────────────
def cmd_status(args: argparse.Namespace) -> int:
    from .ledger import Ledger
    from .serve import MemoryLedgerAgg, memory_report
    from .store import Store
    p = _paths(Path(args.root))
    led = Ledger(p["ledger"])
    s = Store(p["db"]) if p["db"].exists() else None

    #: ⛔⛔ 一次 `status` 只准**遍历账本一遍**。2026-10-13 实测：旧写法问了
    #:    **3.01 遍**（`stats()` 一遍 + `consistency_report(led.events())`
    #:    一遍 + `memory_report(ledger_events=led.events())` 一遍）。
    #:    ⛔ 比浪费更糟的是正确性：三遍读的是三个**不同时刻**的快照 ⇒ 同一
    #:    份输出里 `chain_ok` / `missing_count` / `untraceable_count` 可能
    #:    互相矛盾而不自知 —— 与 serve 侧 §16 是同一个洞，只是这边从没人
    #:    来数过遍数。三条判据必须来自**同一个**快照。
    #:    ⛔ 三个聚合器与 serve 侧 `_collect` **共用同一份逻辑**，不抄第二套。
    stat_agg = led.stats_agg()
    mem_agg = MemoryLedgerAgg()
    cons_agg = s.consistency_agg() if s is not None else None
    try:
        for ev in led.iter_events():
            stat_agg.feed(ev)
            mem_agg.feed(ev)
            if cons_agg is not None:
                cons_agg.feed(ev)
        st = stat_agg.result()
        cons = cons_agg.result() if cons_agg is not None else None
        print(json.dumps({"ledger": st}, ensure_ascii=False, indent=2))
        chain_ok = st["chain_ok"]
        cons_ok = True
        if s is not None:
            print(json.dumps({"store": s.stats()}, ensure_ascii=False, indent=2))
            #: ⛔ 账本（事实源）与投影（视图）是否对得上 —— 这是**判据**，
            #:    不是附带信息：对不上 ⇒ 控制台/监督器基于投影的读数都不可信。
            print(json.dumps({"consistency": cons}, ensure_ascii=False, indent=2))
            if not cons["ok"]:
                print(f"⚠️ 投影与账本不一致（缺 {cons['missing_count']} / "
                      f"漂移 {cons['drift_count']} / 快照缺 "
                      f"{len(cons['snapshot_missing'])} / "
                      f"账本 {cons['ledger_verdicts']} 条 vs 投影 "
                      f"{cons['projection_runs']} 条）⇒ 基于投影的读数不可信"
                      f"（修法：python -m aether.cli rebuild）", file=sys.stderr)
                cons_ok = False
            if cons["overwrite_count"]:
                print(f"⚠️ 投影发生过 {cons['overwrite_count']} 次同键覆盖"
                      f" ⇒ 执行历史被抹掉过", file=sys.stderr)
                cons_ok = False
        #: ⛔ 两条判据**分开报**：合成一个 ok 再用链的文案输出，等于把"投影
        #:    不一致"报成"账本链断裂" —— 那是另一种假仪表（文案与判据不对应）。
        print("账本链：" + ("✅ 完整" if chain_ok
                        else f"❌ 断裂 @seq={st['first_bad_seq']} {st['reason']}"))
        print("投影一致性：" + ("✅ 与账本一致" if cons_ok else "❌ 与账本不一致"))
        #: 记忆完整性也进 status（与 serve 用**同一个**读数函数，不抄第二份）
        mr = memory_report(p["memory"], ledger_agg=mem_agg)
        print(json.dumps({"memory": mr}, ensure_ascii=False, indent=2))
    finally:
        #: ⛔ try/finally：中途抛异常也不能让 SQLite 连接靠 GC 兜底
        if s is not None:
            s.close()
    if mr.get("present") and not mr.get("integrity_ok"):
        print(f"⚠️ 记忆库不完整：{ {k: v for k, v in mr.items() if k.startswith(('foreign', 'wrong', 'no_embedding', 'unverified')) } }",
              file=sys.stderr)
    #: ⛔ 记忆对账：库里有东西却查不到它是怎么来的 ⇒ 因果链缺一环证据。
    #:    "pre_ledger"（账本化之前的存量）不算事故，但必须说出来。
    if mr.get("ledger_trace") == "partial":
        print(f"⚠️ 记忆对不上账本：{mr.get('untraceable_count')} 条在账本里"
              f"找不到写入事件 ⇒ 记忆账本化路径有漏记", file=sys.stderr)
    elif mr.get("ledger_trace") == "pre_ledger":
        print(f"ℹ️ 记忆有 {mr.get('total')} 条，但账本里 0 条写入事件 ⇒ "
              f"这些是账本化之前的存量（不可溯源，重跑一轮即带证据）",
              file=sys.stderr)
    return 0 if (chain_ok and cons_ok) else 1


def cmd_memory(args: argparse.Namespace) -> int:
    """记忆库：读数（show）与换嵌入后的全量重算（reindex）。

    ⛔ `reindex` 存在的理由：换 embedder 后 `MemoryOS.load` 会**整库拒载**
    （设计正确 —— 异维余弦不可比）。但拒载之后如果没有恢复路径，系统就
    永久停在"读不了"的状态。这里是那条恢复路径，且它只走 `reindex()`。
    """
    from .embeddings import HashingEmbedder, resolve_embedder
    from .memory import MemoryOS
    from .serve import memory_report
    p = _paths(Path(args.root))

    from .ledger import Ledger

    if args.memory_cmd == "show":
        print(json.dumps(
            memory_report(p["memory"],
                          ledger_events=Ledger(p["ledger"]).events()),
            ensure_ascii=False, indent=2))
        return 0

    if args.memory_cmd == "backfill":
        #: ⛔ 这是"记忆对不上账本"的**出口**：账本化之前写入的存量永远
        #:    补不上票 ⇒ 告警会一直挂着 ⇒ 一直挂着的告警会被忽略。
        from .embeddings import HashingEmbedder
        led = Ledger(p["ledger"])
        mem, _ = MemoryOS.load(p["memory"], HashingEmbedder(dim=256),
                               min_content_len=60)
        mem.attach_ledger(led)
        n = mem.backfill_ledger()
        mem.save(p["memory"])
        after = memory_report(p["memory"], ledger_events=Ledger(p["ledger"]).events())
        print(json.dumps({"backfilled": n, "after": after},
                         ensure_ascii=False, indent=2))
        if after.get("ledger_trace") == "complete":
            print(f"✅ 存量记忆已补记 {n} 条账本证据（注意：backfill 只证明"
                  f"「此刻库里有它」，不证明它当初是这么写进来的）")
        return 0 if after.get("ledger_trace") in ("complete", "pre_ledger") else 1

    if args.memory_cmd == "ingest":
        #: ⛔ 外部知识摄入的**唯一 CLI 入口**。它不自己实现去重/判据 —— 那些
        #:    闸在 `MemoryOS._write_raw`，learner 只做编排 + 计量 + 记账 +
        #:    可达性取证（V5 教训：learner 自搞一套 ⇒ 39x 重复且报表说成功）。
        from .embeddings import HashingEmbedder
        from .learner import KnowledgeItem, KnowledgeLearner
        led = Ledger(p["ledger"])
        mem, _ = MemoryOS.load(p["memory"], HashingEmbedder(dim=256),
                               min_content_len=60)
        mem.attach_ledger(led)
        src = Path(getattr(args, "file", "") or "")
        if not src.is_file():
            print(f"[memory] 找不到清单：{src}", file=sys.stderr)
            return 2
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[memory] 清单不是合法 JSON：{e}", file=sys.stderr)
            return 2
        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            print("[memory] 清单需要 items 数组", file=sys.stderr)
            return 2
        lr = KnowledgeLearner(mem, ledger=led,
                              max_items=int(getattr(args, "max_items", 200)))
        rep = lr.ingest([KnowledgeItem(
            content=str(r.get("content", "")),
            source=str(r.get("source", src.name)),
            tier=str(r.get("tier", "L3") or "L3"),
            keywords=tuple(r.get("keywords", ()) or ()),
            tags=tuple(r.get("tags", ()) or ()),
            trust=float(r.get("trust", 0.5))) for r in rows
            if isinstance(r, dict)])
        mem.save(p["memory"])
        #: ⛔⛔ **持久化往返取证**：进程内的 `written / retrievable` 只证明
        #:    "内存里有"，证不了"存下来还能读回来"。实测反例：摄入 2 条短
        #:    知识 ⇒ 进程内 `written=2 / retrievable=True`，而
        #:    `save → load` 后 **2 条全被 `rejected_unverified`（内容过短）
        #:    拒载** ⇒ 知识根本没留下。不看这一段，"摄入成功"就是假仪表。
        _, reload_rep = MemoryOS.load(p["memory"], HashingEmbedder(dim=256),
                                      min_content_len=60)
        out = rep.to_dict()
        out["persist_probe"] = {
            "loaded": int((reload_rep or {}).get("loaded", 0)),
            "rejected_unverified": int(
                (reload_rep or {}).get("rejected_unverified", 0)),
            "notes_after_reload": len(
                MemoryOS.load(p["memory"], HashingEmbedder(dim=256),
                              min_content_len=60)[0].notes),
            "note": "写入侧放行 ≠ 加载侧放行；这里查的是 save→load 之后"
                    "真正还在库里的条数",
        }
        lost = out["persist_probe"]["rejected_unverified"]
        if lost:
            print(f"[memory] ⚠️ {lost} 条写完但重载时被拒（多半是内容过短，"
                  f"见 n_below_min_content={rep.n_below_min_content}）"
                  f"⇒ 这些知识**没有真正留下**", file=sys.stderr)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        #: ⛔ 退出码诚实：被拒 / 被截断 / 检索不到 / 重载后丢了 ⇒ 非 0。
        #:    否则"摄入命令跑成功"会被当成"知识都进去了"。
        return 0 if (rep.ok and rep.retrievable is not False and not lost) else 1

    if args.memory_cmd == "prune":
        #: ⛔⛔ **默认只报数，不删**。剪枝不可逆，而"产物增殖闸"要的是
        #:    **看得见**（库在增殖 ⇒ 有人该决定怎么办），不是自动删。
        #:    真删必须显式 `--apply`，且每条都往账本 append `memory.pruned`
        #:    ⇒ 事后能回答"这条记忆去哪了"。
        from .embeddings import HashingEmbedder
        led = Ledger(p["ledger"])
        mem, _ = MemoryOS.load(p["memory"], HashingEmbedder(dim=256),
                               min_content_len=60)
        mem.attach_ledger(led)
        kw = {"max_notes": int(getattr(args, "max_notes", 10000)),
              "min_trust": float(getattr(args, "min_trust", 0.05)),
              "stale_s": float(getattr(args, "stale_days", 30)) * 86400.0}
        if not getattr(args, "apply", False):
            dry = mem.prune_dryrun(**kw)
            print(json.dumps(dry, ensure_ascii=False, indent=2))
            print(f"ℹ️ dry-run：共 {dry['total']} 条，将剪 {dry['n']} 条"
                  f"（{dry['by_reason']}）⇒ 真删请加 --apply")
            return 0
        victims = mem.prune(actor="cli.memory.prune", **kw)
        mem.save(p["memory"])
        rep = mem.prune_report()
        err = getattr(mem, "_ledger_error", "")
        print(json.dumps({"applied": True, "victims": len(victims), **rep,
                          "ledger_error": err},
                         ensure_ascii=False, indent=2))
        #: ⛔ 剪了却没留痕 ⇒ 明知有事故也要报出来（不是"删成功就完事"）
        if err:
            print(f"❌ 已剪 {len(victims)} 条，但**没留痕**：{err}", file=sys.stderr)
            return 1
        print(f"✅ 已剪 {len(victims)} 条并逐条留痕（remaining={rep['remaining']}）")
        return 0

    # ── reindex ──────────────────────────────────────────────────
    before = memory_report(p["memory"])
    if not before.get("present"):
        print(f"❌ 没有可重算的记忆库（{p['memory']}）", file=sys.stderr)
        return 1
    emb: Any = HashingEmbedder(dim=256)
    if args.to != "hash":
        try:
            cand, probe = resolve_embedder(args.to)
            if probe.get("chosen") != "ollama" and args.to == "ollama":
                print(f"❌ 目标嵌入不可用：{probe}", file=sys.stderr)
                return 1
            emb = cand
        except Exception as e:  # noqa: BLE001
            print(f"❌ 目标嵌入不可用（{type(e).__name__}: {e}）", file=sys.stderr)
            return 1
    mem, rep = MemoryOS.load_for_reindex(p["memory"], emb, min_content_len=60)
    if not rep["loaded"]:
        print(f"❌ 没有可重算的条目：{rep.get('note')}", file=sys.stderr)
        return 1
    r = mem.reindex(emb, from_label=f"{rep.get('from_embedder')}:{rep.get('from_dim')}")
    out = {"before": before, "load": rep, "reindex": r}
    if r.get("rolled_back"):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"❌ reindex 失败已回滚：{r.get('error')}", file=sys.stderr)
        return 1
    #: ⛔ 有条目没算出来 ⇒ 不落盘（落盘 = 把 embed_missing 固化成"正常库"）
    if r.get("failed"):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"❌ {r['failed']} 条重算失败 ⇒ **不落盘**（库保持原样）",
              file=sys.stderr)
        return 1
    mem.save(p["memory"])
    out["after"] = memory_report(p["memory"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = out["after"].get("integrity_ok") and \
        len(out["after"].get("dim_histogram", {})) <= 1
    print("✅ 记忆库已重算并落盘" if ok else "⚠️ 落盘后仍不完整（见 after）")
    return 0 if ok else 1


def cmd_verify_chain(args: argparse.Namespace) -> int:
    from .ledger import Ledger
    led = Ledger(_paths(Path(args.root))["ledger"])
    ok, seq, why = led.verify_chain()
    print("✅ 链完整" if ok else f"❌ 断裂 @seq={seq}: {why}")
    return 0 if ok else 1


def cmd_rebuild(args: argparse.Namespace) -> int:
    """从账本重建 SQLite 投影 —— **「库只是视图」的唯一验证方式**。

    ⛔ 为什么需要这条命令（2026-10-13 实测）：`rebuild_from_ledger` 写好了
    两年多……不，写好之后**只有测试调过一次，而且那次断言是 `n >= 0`**
    —— 恒真。真跑一遍才发现：账本 495 条执行、投影只剩 51 条，且键空间
    完全不同（账本不带 run_id）⇒ "库可由账本重建"其实**从未被验证过**。

    ⛔ 与 `memory reindex` 同一形态的纪律：**一条闸配一条出口**。
    投影被判"不可信"之后必须有恢复动作，否则就是死胡同。

    ⛔ 不删旧库：先改名备份（保留现场），再重建。
    """
    import shutil
    from .ledger import Ledger
    from .store import Store
    p = _paths(Path(args.root))
    led = Ledger(p["ledger"])
    evs = led.events()

    if args.check:
        if not p["db"].exists():
            print(f"❌ 没有投影库可校验（{p['db']}）", file=sys.stderr)
            return 1
        s = Store(p["db"])
        rep = s.consistency_report(evs)
        s.close()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        print("✅ 投影与账本一致" if rep["ok"] else "❌ 投影与账本不一致 ⇒ 需重建")
        return 0 if rep["ok"] else 1

    #: ⛔ 复用上面已读的 `evs`：不传 events 会让 `verify_chain()` 再扫一遍
    #:    文件 ⇒ 两遍读的是两个不同时刻的快照，"链完整"可能是**上一遍**的
    #:    结论而这一遍已经断了（与 `cmd_status` 同一个洞，见上面）。
    ok, seq, why = led.verify_chain(evs)
    if not ok:
        print(f"❌ 账本链已断裂 @seq={seq}: {why} ⇒ 拒绝重建"
              f"（事实源本身不可信，重建只会把错误固化）", file=sys.stderr)
        return 1

    target = Path(args.out) if args.out else p["db"]
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists() and not args.force:
        #: ⛔ 默认**不删**现场：旧库留一份带时间戳的副本，事后还能对账
        backup = target.with_name(target.name + f".bak_{int(time.time())}")
        shutil.copy2(target, backup)
        target.unlink()

    s = Store(target)
    counts = s.rebuild_from_ledger(evs)
    rep = s.consistency_report(evs)
    s.close()
    out = {"rebuilt": counts, "consistency": rep,
           "backup": str(backup) if backup else None, "db": str(target)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if counts["derived"]:
        print(f"⚠️ {counts['derived']} 条历史事件缺 run_id ⇒ 用派生键"
              f" rebuilt:{{seq}} 补齐（可溯源，但与当年那次运行的键对不上）",
              file=sys.stderr)
    print("✅ 已从账本重建且与账本一致" if rep["ok"] else "❌ 重建后仍不一致（见 consistency）")
    return 0 if rep["ok"] else 1


def cmd_synth(args: argparse.Namespace) -> int:
    from .environment import (DEFAULT_MUTATION_KINDS, Environment,
                              synthesize_tasks, verify_source)
    p = _paths(Path(args.root))
    env = Environment.from_dir(p["task"])
    base = verify_source(env)
    print(f"环境 {env.env_id} fingerprint={env.fingerprint} "
          f"基线通过 {base.n_passed}/{len(base.checks)}")
    _kinds: Tuple[str, ...] = tuple(
        x.strip() for x in (getattr(args, "kinds", "") or "").split(",")
        if x.strip()) or DEFAULT_MUTATION_KINDS
    rep = synthesize_tasks(env, max_n=int(args.max),
                           depth=int(getattr(args, "depth", "1") or "1"),
                           kinds=_kinds)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    for t in rep.tasks[:5]:
        print(f"  ✓ {t.task_id}  {t.desc}  F2P={list(t.fail_to_pass)}")
    # ⛔ yield=0 是**失败信号**，不是"这次没合成出来而已"
    if rep.valid == 0:
        print("❌ yield=0：这个环境合成不出有效任务 ⇒ 进化没有燃料")
        return 1
    return 0


def _fact_verifier(note) -> tuple:
    """记忆提升的**对抗验证器**：职责是尽力证伪，不是确认。

    ⛔ 方向不能反：默认"通过"的提升等于没有验证。这里主动找反证
    （内容太短、含占位词、含未验证标记），找到就证伪。

    ⛔ 判据**必须复用** `memory.memory_purity`：跨运行装载的污染闸用同一个
    词表。分成两份就会漂移 —— 写入侧放行、装载侧拦截（或反过来），
    而两边都以为自己在守闸。
    """
    from .memory import memory_purity
    return memory_purity(note)


def _default_runner(envs, memory=None, timeout: float = 10.0):
    """把 harness 变成"能修好环境里的 bug"的执行体（多环境版）。

    ⛔ 执行逻辑现在住在 `agent.HarnessAgent`：harness 的 **5 层都会影响结果**
    （L0 元规则可禁用修法 / L1 路由决定技能可不可达 / L2 工具缺失直接失败 /
    L3 是修法本身 / L4 记忆是第二条通路）。

    ⛔ 三类技能的区别，它把 "reward hacking" 变成可判定的：

        fix_control_flow        —— **保守通用修法**：任何环境都能修，但修不全
        fix_literals            —— **通用字面量修法**：另一个维度（只对齐数值常量）
        patch::<env_id>         —— **定点修法**：只在点名的那个环境生效，修得全

    在**单一环境**的评测里，后者**严格更好**（发展集分数更高）⇒ 它一定会被
    选中 ⇒ 一定会被提交。只有 held-out 能把这两者分开。
    """
    from .agent import HarnessAgent
    return HarnessAgent(envs, memory=memory, timeout=timeout).as_runner()


def _verify_fixed(env, broken_src, fixed_src, timeout: float = 10.0):
    """判定：修好后是否**所有原本通过的测试都回来了**（可执行判据）。"""
    from .environment import verify_source
    from .verifier import Check, Verdict
    v = verify_source(env, source=fixed_src, timeout=timeout)
    n = len(v.checks)
    return Verdict(passed=v.passed, checks=v.checks,
                   evidence={"n_passed": v.n_passed, "n_total": n,
                             "changed": fixed_src != broken_src},
                   env_fingerprint=env.fingerprint, cost_s=v.cost_s)


def cmd_evolve(args: argparse.Namespace) -> int:
    from .environment import (DEFAULT_MUTATION_KINDS, Environment,
                              synthesize_tasks)
    from .evolution import EvolutionConfig, EvolutionLoop
    from .generalization import GeneralizationGate, make_heldout
    from .genome import Harness
    from .ledger import Ledger
    from .nongression import Baseline, FrozenTask, NonRegressionGate
    from .providers import StubProvider, TemplateProposer
    from .store import Store

    p = _paths(Path(args.root))
    envs = {name: Environment.from_dir(p["tasks_root"] / name)
            for name in DEFAULT_ENVS}
    dev_name = args.dev_env
    ho_name = args.heldout_env
    if dev_name not in envs or ho_name not in envs:
        raise SystemExit(f"未知环境：dev={dev_name} heldout={ho_name}，可用 {sorted(envs)}")
    if dev_name == ho_name:
        raise SystemExit("发展环境与 held-out 环境不能是同一个 ⇒ 泛化闸会自我取消")

    led = Ledger(p["ledger"])
    store = Store(p["db"])

    # ── 记忆层：**必须在 runner 之前**建好并注入 ──────────────────
    # ⛔ 2026-10-13 实测：记忆原本建在 loop.run() **之后**，89 次执行里
    #    L4 通路触发 0 次 ⇒ 记忆对 reward 零影响 ⇒ 5 层里的 L4 是装饰。
    #    这里把每个环境的基线指纹写进 L2（curated 通道），L4 才有燃料。
    # ⛔⛔ **不预置**任何环境的修法知识。2026-10-13 实测两种预置都失败：
    #   · 预置"整份基线源码" ⇒ L4 一召回就把 reward 顶到 1.0 ⇒ 基线在天花板
    #     ⇒ 再也挖不到弱点 ⇒ 进化永久空转（reward 满分是最隐蔽的假象）；
    #   · 预置"用 fix_control_flow 修" ⇒ 记忆**复制**了技能的效果 ⇒
    #     加这个技能不再有边际收益 ⇒ 候选增益 0 ⇒ 同样不提交。
    #   共同根因：外部预置的知识**掩盖**了"这个系统没有技能"这个真实弱点。
    #   记忆只能由**执行经验**填充：修好了什么、被什么闸拒了。
    from .embeddings import HashingEmbedder
    from .memory import MemoryOS
    embedder = HashingEmbedder(dim=256)
    embedder_report: Dict[str, Any] = {"chosen": "hashing:256",
                                       "degraded": False, "decisions": []}
    if getattr(args, "embedder", "hash") != "hash":
        # ⛔ 换嵌入**必须过选型闸**：绝对余弦跨 embedder 不可比，
        #    只能比排序判别力（recall@1 / MRR / margin）。
        try:
            from .embeddings import resolve_embedder
            from .retrieval import (BENCH, BENCH_EN, EmbeddingSwitchGate,
                                    evaluate)
            cand, probe = resolve_embedder(args.embedder)
            gate = EmbeddingSwitchGate()
            for name, cases in (("zh", BENCH), ("en", BENCH_EN)):
                cur = evaluate(HashingEmbedder(dim=256), cases)
                new = evaluate(cand, cases)
                v = gate.decide(cur, new)
                embedder_report["decisions"].append(
                    {"bench": name, **v.to_dict()})
            # ⛔ 两套基准**都要**放行才切换（fail-closed）：
            #    换嵌入要 reindex 全量重算，代价真实 ⇒ 一边说行一边说不行时
            #    按"不行"处理（实测：nomic 在英文放行、中文拒绝）。
            decs = embedder_report["decisions"]
            allowed = len(decs) == 2 and all(d.get("allow") for d in decs)
            embedder_report["probe"] = probe
            if allowed:
                embedder = cand
                embedder_report["chosen"] = getattr(
                    cand, "embedder_id", type(cand).__name__)
            else:
                embedder_report["note"] = "选型闸拒绝 ⇒ 保持 hashing（显式记账）"
        except Exception as e:  # noqa: BLE001
            # ⛔ 真嵌入不可用 ⇒ **显式降级并记账**，绝不静默混库
            embedder_report = {"chosen": "hashing:256", "degraded": True,
                               "error": f"{type(e).__name__}: {e}"}
    # ⛔ 跨运行持久化：不落盘的话每次运行都从空记忆开始 ⇒ "经验积累"是假的
    #    （2026-10-13 实测两次运行记忆完全相同）。装载必须过污染闸与
    #    embedder 对齐闸，详见 `MemoryOS.load`。
    mem, mem_load = MemoryOS.load(p["memory"], embedder, min_content_len=60)
    #: ⛔ 记忆必须接进账本：它跨运行继承、直接喂给提议器，是因果链一环。
    #:    不进账本 ⇒ 谁写的、何时写的、被拒了多少都不可溯源（2026-10-13
    #:    实测：宪法说"事实只有一份"，记忆却在账本之外另存一份）。
    mem.attach_ledger(led)
    if mem_load.get("note"):
        print(f"💾 记忆装载：{mem_load['note']}", file=sys.stderr)
    runner = _default_runner(envs, memory=mem)

    # ⛔ 种子 harness **必须先建**：冻结基线要用它跑。
    #    用空 `Harness()` 跑基线 ⇒ 连工具都没有 ⇒ 基线全红 ⇒
    #    非回归闸"没有原本通过的任务"⇒ 对任何候选都放行的空闸（V5 同款陷阱）。
    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5, "min_content_len": 150})

    # 冻结套件：**两个环境**的原始源码都应当全绿
    frozen = [FrozenTask(task_id=f"frozen::{name}",
                         spec={"env_id": name, "broken_source": e.source,
                               "topic": name, "task_class": TASK_CLASS})
              for name, e in envs.items()]
    caliber = {"seed": int(args.seed), "timeout": 10.0, "split": "frozen"}
    baseline = Baseline(
        caliber=caliber,
        verdicts={t.task_id: runner(h0, t.spec) for t in frozen},
        harness_id="seed")
    gate = NonRegressionGate(frozen, baseline)

    from .repair import difficulty_report, grow_for_difficulty

    # held-out：**另一个环境**的合成任务。它不参与候选之间的比较。
    # ⛔ 它必须**先**合成：发展集要照着它的难度层补采样（见下）。
    heldout = None
    rep_ho = None
    diff_report = None
    ho_levels: set = set()
    #: ⛔ held-out 补采样记录。没有它，"泛化闸判 inconclusive"和
    #:    "held-out 采样不够"在输出里长得一模一样。
    grow_ho_report: Dict[str, Any] = {}
    #: ⛔⛔ 变异参数**默认与既有口径完全一致**（depth=1 / kinds=默认三种）。
    #:    `which` 是全局遍历序编号，任何默认行为的改动都会让历史账本
    #:    与基线不可比 ⇒ 新口径只能由命令行**显式**开启，算作新实验。
    _kinds: Tuple[str, ...] = tuple(
        x.strip() for x in (getattr(args, "kinds", "") or "").split(",")
        if x.strip()) or DEFAULT_MUTATION_KINDS
    _depth = int(getattr(args, "depth", "1") or "1")
    if args.heldout == "on":
        rep_ho = synthesize_tasks(envs[ho_name], max_n=int(args.max_tasks),
                                  depth=_depth, kinds=_kinds)
        if rep_ho.tasks:
            def _ho_tasks(src_tasks) -> list:
                return [FrozenTask(task_id=t.task_id,
                                   spec={"env_id": ho_name,
                                         "broken_source": t.broken_source,
                                         "topic": ho_name,
                                         "task_class": TASK_CLASS})
                        for t in src_tasks]

            ho_tasks = _ho_tasks(rep_ho.tasks)
            # ⛔ held-out 也要有**难度跨度**：全是简单题的话，它会放行
            #    "只会修简单题"的候选。难度由"通用修法能否修好"执行判定。
            diff_report = difficulty_report(envs[ho_name], ho_tasks)
            ho_levels = set(diff_report["labels"].values())
            if not diff_report["spread"]:
                # ⛔⛔ 光警告没有出口 ⇒ 泛化闸会把每次放行都降级成
                #    inconclusive ⇒ "永远不提交"，和"候选不好"完全同形。
                #    所以先**补采样**：合成任务来自 AST 变异、与候选无关，
                #    扩大 held-out 采样不给任何候选优待（同 dev 的 grow）。
                #    到顶仍无跨度 ⇒ 才真的报"判别力不足"。
                g_ho = grow_for_difficulty(
                    envs[ho_name],
                    lambda n: synthesize_tasks(envs[ho_name], max_n=n,
                                               depth=_depth,
                                               kinds=_kinds).tasks,
                    ("easy", "hard"), max_n=int(args.max_tasks))
                if g_ho["covered"]:
                    ho_src = g_ho["tasks"]
                    ho_tasks = _ho_tasks(ho_src)
                    diff_report = difficulty_report(envs[ho_name], ho_tasks)
                    ho_levels = set(diff_report["labels"].values())
                    print(f"✅ held-out 判别力自愈：采样 {int(args.max_tasks)}"
                          f"→{g_ho['max_n_used']} 补齐难度跨度"
                          f"（easy {diff_report['n_easy']} / "
                          f"hard {diff_report['n_hard']}）", file=sys.stderr)
                grow_ho_report = {k: v for k, v in g_ho.items() if k != "tasks"}
                if not diff_report["spread"]:
                    print(f"⚠️ held-out 只有单一难度（easy {diff_report['n_easy']}"
                          f" / hard {diff_report['n_hard']}）⇒ 判别力不足，"
                          f"它无法区分「真泛化」与「只会修这类题」"
                          f" ⇒ 泛化闸将把每次放行降级为 inconclusive"
                          f"（补采样到上限仍缺层："
                          f"{'/'.join(g_ho['missing']) or '未知'}）",
                          file=sys.stderr)
        else:
            print(f"⚠️ held-out 环境 {ho_name} 合成 yield=0 ⇒ 泛化闸无从裁决",
                  file=sys.stderr)

    # ── 发展集：**照着 held-out 的难度层补采样** ─────────────────
    # ⛔ 2026-10-13 实测：max_tasks=8 时 dev easy/hard=3/0 而 ho=2/1 ⇒
    #    泛化闸三轮全判 inconclusive（拦截）；max_tasks=12 时 dev=3/2 ⇒
    #    generalize 并提交。缺层是**采样配置**问题，不是候选的错 ——
    #    让候选为采样不足背锅，效果和"永远不提交"完全一样。
    #    补采样只加题不加判据（合成任务与候选无关），所以它不给任何优待。
    grow_report: Dict[str, Any] = {}
    rep_synth = synthesize_tasks(envs[dev_name], max_n=int(args.max_tasks),
                                 depth=_depth, kinds=_kinds)
    dev_src_tasks = rep_synth.tasks
    if ho_levels:
        g = grow_for_difficulty(
            envs[dev_name], lambda n: synthesize_tasks(
                envs[dev_name], max_n=n, depth=_depth, kinds=_kinds).tasks,
            ho_levels, max_n=int(args.max_tasks))
        dev_src_tasks = g["tasks"]
        grow_report = {k: v for k, v in g.items() if k != "tasks"}
        if g["note"]:
            print(("✅ " if g["covered"] else "⚠️ ") + g["note"],
                  file=sys.stderr)
    dev = [FrozenTask(task_id=t.task_id,
                      spec={"env_id": dev_name, "broken_source": t.broken_source,
                            "topic": dev_name, "task_class": TASK_CLASS})
           for t in dev_src_tasks]

    # ⛔ 发展集也要有难度标签：泛化闸要靠它做**同层对照**。
    dev_diff = difficulty_report(envs[dev_name], dev)
    dev_difficulty = dev_diff["labels"]
    if not dev_diff["spread"]:
        print(f"⚠️ 发展集只有单一难度（easy {dev_diff['n_easy']}"
              f" / hard {dev_diff['n_hard']}）⇒ 泛化闸拿不到同层对照",
              file=sys.stderr)

    if rep_ho is not None and rep_ho.tasks and diff_report is not None:
        heldout = GeneralizationGate(
            make_heldout(ho_name, ho_tasks,
                         difficulty=diff_report["labels"]),
            dev_task_ids=[t.task_id for t in dev],
            heldout_floor=float(args.heldout_floor),
            base_at_ceiling=1.0)

    # ⛔ 两类技能刻意并列：通用但保守 vs 定点但只此一地。
    #    在**发展集**上后者严格更好 ⇒ 一定会被选中 ⇒ 一定会被提交，
    #    除非泛化闸拦住它。这就是 reward hacking 的可执行形态。
    pool = {
        "fix_control_flow": "保守通用修法：只恢复控制流（比较/运算），不动字面量",
        "fix_literals": "通用字面量修法：只把数值常量对齐基线，不动结构/字符串",
        "fix_boolops": "通用布尔运算修法：只把 and/or 对齐基线，不动比较/运算/常量",
        f"patch:{dev_name}": f"定点修法：{dev_name} 整个文件换回基线（换环境即失效）",
    }
    #: ⛔ 三类并列，前两类都是**通用**的（不点名环境），第三类是定点的：
    #:    在**发展集**上定点修法严格更好 ⇒ 一定会被选中 ⇒ 一定会被提交，
    #:    除非泛化闸拦住它。这就是 reward hacking 的可执行形态。
    #: ⛔ 为什么要有 `fix_literals`（2026-10-13 实测）：只有
    #:    `fix_control_flow` 时，常量变异题**永远**是 hard，而唯一能修它的
    #:    `patch:*` 必然被 held-out 拦 ⇒ 闭环第一轮之后**注定停滞**
    #:    （实测 round2/3 全 `saturated:stagnated`）。它是第二个**能力维度**，
    #:    不是第二个定点修法。
    if args.capsules == "general":
        pool = {k: v for k, v in pool.items() if not k.startswith("patch:")}
    elif args.capsules == "narrow":
        pool = {f"patch:{dev_name}": pool[f"patch:{dev_name}"]}
    # ⛔ 路由键用**任务类别**而不是环境名：按环境名路由本身就是一种定点化，
    #    换环境就失效。泛化与否在路由层就已经是结构选择了。
    template = TemplateProposer(skills=pool, route_key=TASK_CLASS)
    proposer: Any = template
    if getattr(args, "proposer", "template") == "llm":
        # 真模型提议器。**同协议**替换模板提议器 —— 闸一个不少：
        # 语法闸（L0 禁改/白名单）→ 解析闸 → 非回归闸 → 泛化闸 → 能力地板。
        from .providers import LLMProposer, OllamaProvider
        model = getattr(args, "llm_model", "") or os.environ.get(
            "V6_OLLAMA_MODEL", "Qwen3.5-4B:latest")
        try:
            # ⛔ 接口契约必须注入：不告诉模型"路由键是任务类别 / 技能名只有
            #    这几个"，它就会猜 —— 实测猜错时输出结构完全合法却 reward=0，
            #    看起来像"闸在工作"，实际是 prompt 缺文档（providers 里有实测）。
            #: ⛔⛔ held-out 规则**只讲判据，不讲答案**：
            #    "会在另一个环境上验证"是规则 ⇒ 该说；
            #    "你应该提通用修法"是答案 ⇒ 说了就是把模型该考的试替它考了
            #    （外部预置知识会掩盖真实弱点，与"记忆只能由执行填充"
            #     同一条纪律）。模型能不能自己想到，正是要测的。
            ho_note = ""
            if heldout is not None:
                ho_note = (
                    f"你提的变异会在**另一个环境** `{ho_name}` 上复核："
                    f"只有在发展集和 held-out **都**变好才会被采纳；"
                    f"只针对某一个环境（或某几道特定题）的修法会被判"
                    f" overfit 拦下。提变异时优先考虑**跨环境都成立**的改法。")
            proposer = LLMProposer(
                OllamaProvider(model=model, think=False),
                route_keys=(TASK_CLASS,),
                skill_vocab=tuple(sorted(pool)),
                env_ids=tuple(sorted(envs)),
                heldout_note=ho_note)
            print(f"🧠 proposer=llm（{model}）", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 真模型不可用（{type(e).__name__}: {e}）⇒ 回退模板提议器",
                  file=sys.stderr)
            proposer = template
    # ⛔ 记忆进入因果链：被闸拒的变异记进记忆 ⇒ 下一轮不再重复提。
    #    同协议包装（只筛候选，不动判据），闸一个不少。
    from .providers import MemoryAwareProposer
    # ⛔ dev 与 held-out 的**难度层必须对得上**：held-out 有某层而发展集没有
    #    ⇒ 那一层无从对照 ⇒ 泛化闸会判 inconclusive（拦截）。
    #    不提示的话，"永远不提交"看起来和"候选不好"一模一样。
    if diff_report and dev_difficulty:
        missing = sorted(set(diff_report["labels"].values())
                         - set(dev_difficulty.values()))
        if missing:
            print(f"⚠️ 发展集缺少难度层 {missing}（held-out 有）⇒ 这些层无从对照"
                  f" ⇒ 泛化闸将判 inconclusive 并拦截"
                  f"（提高 --max-tasks 或换环境以获得同层对照）", file=sys.stderr)

    proposer = MemoryAwareProposer(proposer, mem)

    _consolidated_total = 0

    def _remember_round(rep) -> None:
        """把一轮的**执行经验**写进记忆 —— 记忆只能由执行填充。

        ⛔ 与"被闸拒的经验"（MemoryAwareProposer.remember）相对：
        这条记的是**修好了什么**。两者都是 L4 通路的燃料，
        而且都不是预置答案 —— 没有执行过就没有内容。

        ⛔⛔ 巩固的**触发点就在这里**（轮末、循环内），不是跑完再补：
            它是 ALE 所说的"睡眠期"行为，语义上属于这一轮的收尾；
            放在 loop.run() 之后 ⇒ 本轮的教训对下一轮的提议器
            **影响恒为 0**（V5：有机制 ≠ 这条路被走到）。
            同时这里把事件条数写进 `loop.aging_events` ⇒ 老化检测
            的"发生了什么"才有事实源。
        """
        nonlocal _consolidated_total
        if rep.committed:
            best = next((c for c in rep.candidates if c.accepted), None)
            mem.write(
                "L3",
                f"repair_hint:{dev_name} 第 {rep.round_no} 轮：变异 "
                f"{best.capsule_name if best else rep.committed[:8]} 修好了任务，"
                f"基线 reward {rep.baseline_reward:.2f} → "
                f"{(best.reward if best else 0.0):.2f}（全部由执行判据给出）。"
                f"失败模式集中在控制流（比较与运算），保守修法覆盖其中一部分，"
                f"技能不可达时它是第二条通路。",
                channel="curated", source=f"round{rep.round_no}",
                tags=("experience", "exec"))
            return
        #: 未提交 ⇒ 这一轮有失败素材 ⇒ 睡眠期巩固（ALE 的离线巩固语义）
        try:
            made = mem.consolidate(min_access=0)
        except Exception as _e:                        # noqa: BLE001
            made = []
            print(f"⚠️ 巩固失败：{type(_e).__name__}: {_e}", file=sys.stderr)
        _consolidated_total += len(made)
        loop.aging_events["consolidated"] = len(made)
        #: 剪枝**只报数不删** —— 不可逆 ⇒ 该不该剪由人拍板
        try:
            loop.aging_events["pruned"] = len(
                mem.prune_dryrun().get("victims", []) or [])
        except Exception:                              # noqa: BLE001
            pass

    loop = EvolutionLoop(led, gate, dev, runner, proposer, store=store,
                         caliber=caliber, heldout=heldout,
                         dev_difficulty=dev_difficulty,
                         on_round_end=_remember_round,
                         config=EvolutionConfig(
                             group_size=int(args.group),
                             #: ⛔ 默认 0 = 串行（与历史基线同口径）。并行只
                             #:    包住 runner 计算，记账仍串行（见 §23.5）。
                             parallel_workers=int(
                                 getattr(args, "parallel", "0") or 0)))

    #: ⛔ 初始 harness 必须**先进账本**再落投影：只写库的话，从账本重建会
    #:    丢掉它（实测 7 个快照重建只剩 6 个）⇒ "账本是唯一事实源"被违反。
    loop.register_root(h0)
    mem.write("L0", h0.meta_rules, channel="curated", source="seed-harness")

    t0 = time.time()
    final, reports = loop.run(h0, rounds=int(args.rounds))

    for r in reports:
        #: ⛔ L4 是巩固的**唯一输入**。旧写法只记轮次模板（"第 N 轮：基线
        #:    reward=X，候选 M 个，结果=Y"），各轮之间只差数字 ⇒ 巩固归纳
        #:    出来还是模板，没有可沉淀的信息。现在把"这一轮到底为什么没成"
        #:    一并写进去（裁决 / 被拒数 / 分层增益 / 是否空转），巩固才有料。
        _g = r.generalization if isinstance(r.generalization, dict) else {}
        _verdict = str(_g.get("verdict") or "none")
        #: ⛔ `r.candidates` 是 Candidate 对象（不是 dict）—— 判据/取值必须与
        #:    被测对象同源，别照抄 JSON 输出里的 `.get()` 写法。
        _rej = sum(1 for c in r.candidates if not getattr(c, "accepted", False))
        _by = _g.get("dev_by_difficulty") or {}
        _easy = float(_by.get("easy") or 0.0)
        _hard = float(_by.get("hard") or 0.0)
        mem.write("L4",
                  f"第 {r.round_no} 轮进化：基线 reward={r.baseline_reward:.2f}，"
                  f"候选 {len(r.candidates)} 个，被拒 {_rej} 个，"
                  f"结果={'提交' if r.committed else '回滚'}，裁决 {_verdict}，"
                  f"易层增益 {_easy:+.2f}，难层增益 {_hard:+.2f}，"
                  f"空转 {1 if r.idle else 0}",
                  channel="system", source=f"round{r.round_no}")
        if r.weakness:
            mem.write("L3",
                      f"{r.weakness.kind} / {r.weakness.topic}："
                      f"{'; '.join(r.weakness.evidence[:2])}",
                      channel="learner", source=f"round{r.round_no}")

    # 对抗验证演示：verifier 的职责是**证伪**，找不到反证才提升
    promoted = []
    for nid in list(mem.notes)[:3]:
        ok, why = mem.promote(nid, verifier=_fact_verifier, channel="curated")
        promoted.append({"note": nid, "ok": ok, "why": why})
    # fail-closed：无验证器 ⇒ 拒绝提升
    _, no_verifier_why = mem.promote(list(mem.notes)[0], verifier=None) if mem.notes else (False, "")
    #: ⛔ 巩固是"睡眠期"行为 —— 只在**没变好**时才做，而不是无条件每轮跑
    #:    （旧行为跑是跑了，但"产出 0 是因为没有新信息"还是"因为机制坏了"
    #:    完全不可分辨，只能靠猜）。
    #:
    #: ⛔⛔ 触发信号不能只用 `idle`（2026-10-14 实测打脸）：round2/3 是
    #:    `no_candidate_passed` —— **有候选、全被闸拒**，这恰恰是最该沉淀
    #:    教训的场景，而 `idle` 只在"压根没事可做"时才为真 ⇒ 实测三轮
    #:    idle_rounds=0 ⇒ 巩固一次都没被触发，等于把修好的机制又关掉了。
    #:    正确的语义是「这一轮没提交 ⇒ 有失败素材可反思」。
    _idle = [r for r in reports if getattr(r, "idle", False)]
    _uncommitted = [r for r in reports if not getattr(r, "committed", None)]
    #: ⛔ 巩固**不在**这里跑 —— 它已移进 `_remember_round`（轮末、循环内）。
    #:    放在这里 ⇒ 只在全部轮次结束后发生一次 ⇒ 本轮教训对下一轮
    #:    提议器的影响恒为 0（V5：有机制 ≠ 这条路被走到）。
    consolidated = [None] * _consolidated_total
    _cons_rep = mem.consolidation_report()
    _cons_rep["trigger"] = "stagnant" if _uncommitted else "all_committed"
    _cons_rep["idle_rounds"] = len(_idle)
    _cons_rep["uncommitted_rounds"] = len(_uncommitted)
    # ⛔ 提议器诊断必须落盘：看到"0 个候选"时要能分辨
    #    「闸在工作」/「模型没接通」/「输出被截断」—— 三者处置完全不同，
    #    缺了它，进化循环"正确地什么都不做"和"供给根本没通"长得一模一样。
    diag = proposer.diagnostics() if hasattr(proposer, "diagnostics") else \
        {"name": "template", "error": "", "rejects": []}
    if diag.get("error"):
        print(f"ℹ️ proposer 诊断：{diag}", file=sys.stderr)
    out = {
        "dev_env": dev_name,
        "heldout_env": ho_name if heldout is not None else None,
        "proposer": diag,
        "synthesis": rep_synth.to_dict(),
        "synthesis_heldout": rep_ho.to_dict() if rep_ho is not None else None,
        #: ⛔ 发展集缺难度层时的**补采样**记录（没有它，"永远判 inconclusive"
        #:   和"候选不好"在输出里长得一模一样）
        "grow": grow_report or None,
        #: ⛔ held-out 的补采样记录（同上：缺了它，"永远 inconclusive"
        #:   和"held-out 采样不够"在输出里无法区分）
        "grow_heldout": grow_ho_report or None,
        "heldout_difficulty": ({k: v for k, v in diff_report.items()
                                if k != "labels"} if diff_report else None),
        "dev_difficulty": {k: v for k, v in dev_diff.items() if k != "labels"},
        "rounds": [r.to_dict() for r in reports],
        "final_harness_id": final.harness_id,
        "seed_harness_id": h0.harness_id,
        "improved": any(r.improved for r in reports),
        "cost_s": round(time.time() - t0, 2),
        "embedder": embedder_report,
        "ledger": led.stats(),
        "memory": mem.integrity(),
        #: ⛔ 记忆是否真的进了因果链：跳过数 > 0 才证明它被走到过
        "memory_causal": proposer.diagnostics() if hasattr(
            proposer, "diagnostics") else {},
        "memory_promote": promoted,
        "memory_promote_without_verifier": no_verifier_why,
        "memory_consolidated": len(consolidated),
        #: ⛔ 分账（不再只报一个数字）：made / deduped / skipped_* 各自独立，
        #:    否则"没新信息"与"机制坏了"不可分辨。
        "memory_consolidation": _cons_rep,
        #: ⛔ 产物增殖闸的**读数**（2026-10-14 接线）：之前 `prune()` 有实现
        #:    却零调用点 ⇒ 这条闸是装饰。自动路径**只报数不删** —— 剪枝不
        #:    可逆，该不该剪由人决定（`aether.cli memory prune --apply`）。
        #:    于是"库在增殖"这件事第一次变得可见。
        "memory_prune": mem.prune_dryrun(),
        #: ⛔ 跨轮退化（老化）裁决。与"非回归闸"正交：
        #:    闸 = 这一轮有没有变坏；老化 = 进来之后还在不在自己变弱。
        #:    两者合一 ⇒ 一个"放行但正在退化"的系统会同时骗过两件事。
        "aging": loop.aging_report(),
    }
    mem_saved = mem.save(p["memory"])
    out["memory_load"] = mem_load
    out["memory_saved"] = mem_saved
    print(json.dumps(out, ensure_ascii=False, indent=2))
    store.close()
    return 0


def _caps_gateway(path: str):
    """装载能力清单并返回网关。

    ⛔ 直接调 `interop.mount_capabilities` —— 与 HTTP 服务（启动时装载 +
    `/api/capabilities/reload` 热装载）**同一个函数**。两个入口各写一份
    装载接线，就会出现"CLI 能调、HTTP 调不到"的幽灵差异。
    """
    from .interop import mount_capabilities
    return mount_capabilities(path)


def cmd_caps(args) -> int:
    from .interop import load_manifest
    gw, rep = _caps_gateway(getattr(args, "caps", "") or "")
    for s in rep.skipped:
        print(f"[caps] ⚠️ 跳过 {s.get('id')}：{s.get('reason')}", file=sys.stderr)
    reg = gw.registry
    cmd = getattr(args, "caps_cmd", "list")

    if cmd == "list":
        print(json.dumps({"fingerprint": reg.fingerprint, "n": len(reg),
                          "capabilities": reg.manifest()},
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "show":
        spec = reg.get(args.id)
        if spec is None:
            print(f"[caps] 未注册：{args.id}", file=sys.stderr)
            return 2
        print(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "load":
        r2 = load_manifest(reg, args.manifest)
        print(json.dumps(r2.to_dict(), ensure_ascii=False, indent=2))
        return 0 if r2.ok else 1
    if cmd == "invoke":
        try:
            call_args = json.loads(args.json or "{}")
        except Exception as e:  # noqa: BLE001
            print(f"[caps] --json 不是合法 JSON：{e}", file=sys.stderr)
            return 2
        if not isinstance(call_args, dict):
            print("[caps] --json 必须是对象", file=sys.stderr)
            return 2
        res = gw.invoke(args.id, call_args, actor="cli")
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2,
                         default=str))
        #: ⛔ 退出码必须诚实：脚本要靠它判成败，全返回 0 就是把失败藏起来
        return 0 if res.status == "ok" else 1
    print(f"[caps] 未知子命令 {cmd}", file=sys.stderr)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="aether", description="V6 · AETHER CLI")
    ap.add_argument("--root", default=str(ROOT), help="V6 根目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="账本 + 库统计 + 投影一致性")
    sub.add_parser("verify-chain", help="校验账本哈希链")
    rb = sub.add_parser("rebuild", help="从账本重建 SQLite 投影（库只是视图）")
    rb.add_argument("--check", action="store_true",
                    help="只校验投影与账本是否一致，不重建")
    rb.add_argument("--out", default="", help="重建到别的库（默认覆盖 data/aether.db）")
    rb.add_argument("--force", action="store_true",
                    help="（保留位）不备份旧库 —— 默认总是备份")
    s = sub.add_parser("synth", help="合成任务并统计 yield")
    s.add_argument("--max", default="8")
    #: ⛔ 变异参数默认**与既有基线同口径**（depth=1 / 默认三种），
    #:    显式传参才算新实验 —— 否则历史账本不可比。
    s.add_argument("--depth", default="1", choices=["1", "2"],
                   help="变异点数：2 ⇒ 需要【叠加】才能修好")
    s.add_argument("--kinds", default="",
                   help="变异种类，逗号分隔：compare,binop,constant,"
                        "boolop,unaryop")
    m = sub.add_parser("memory", help="记忆库：读数 / 换嵌入后全量重算 / 补记账")
    ms = m.add_subparsers(dest="memory_cmd", required=True)
    ms.add_parser("show", help="打印记忆库完整性读数")
    ms.add_parser("backfill",
                  help="为账本化之前的存量记忆补记账本证据（对账告警的出口）")
    mr = ms.add_parser("reindex", help="换 embedder 后的唯一合法恢复路径")
    mr.add_argument("--to", default="hash", choices=["hash", "ollama"],
                    help="目标嵌入器")
    mi = ms.add_parser("ingest", help="摄入外部知识（learner 通道，只写 L3/L4）")
    mi.add_argument("file", help="知识清单 JSON（数组，或 {items:[...]}）")
    mi.add_argument("--max-items", default="200", help="单次摄入配额")
    mp = ms.add_parser("prune", help="产物增殖闸：默认只报数，加 --apply 才真删")
    mp.add_argument("--apply", action="store_true",
                    help="真的删除（默认 dry-run：只算不删）")
    mp.add_argument("--max-notes", default="10000", help="容量上限")
    mp.add_argument("--min-trust", default="0.05", help="低于此信任度即剪")
    mp.add_argument("--stale-days", default="30", help="未访问超过这么多天且零访问即剪")
    c = sub.add_parser("caps", help="接入层：列出 / 查看 / 调用 agents 与 skills")
    c.add_argument("--caps", default="",
                   help="能力清单 JSON（不传则只显示源码内注册的能力）")
    cs = c.add_subparsers(dest="caps_cmd", required=True)
    cs.add_parser("list", help="列出已注册能力")
    sh = cs.add_parser("show", help="查看一个能力的声明")
    sh.add_argument("id")
    iv = cs.add_parser("invoke", help="调用一个能力")
    iv.add_argument("id")
    iv.add_argument("--json", default="{}", help='参数 JSON，如 \'{"text":"a"}\'')
    ld = cs.add_parser("load", help="装载能力清单并报告每条成败")
    ld.add_argument("manifest")

    e = sub.add_parser("evolve", help="跑 self-harness 进化")
    e.add_argument("--rounds", default="3")
    e.add_argument("--group", default="3")
    e.add_argument("--max-tasks", default="12")
    e.add_argument("--depth", default="1", choices=["1", "2"],
                   help="变异深度：1=单点（默认，与既有基线同口径）/ "
                        "2=双点（需要【叠加】才能修好 ⇒ 抬高天花板）")
    e.add_argument("--kinds", default="",
                   help="变异种类，逗号分隔：compare,binop,constant,boolop,"
                        "unaryop。留空=默认三种（与既有基线同口径）")
    e.add_argument("--capsules", default="both",
                   choices=["both", "general", "narrow"],
                   help="提议池：both=两类都给（定点修法必赢）/ general / narrow")
    e.add_argument("--seed", default="0")
    e.add_argument("--dev-env", default="textscore", help="发展集所在环境")
    e.add_argument("--heldout-env", default="eventflow", help="held-out 环境（不参与选择）")
    e.add_argument("--heldout", default="on", choices=["on", "off"],
                   help="off ⇒ 关闭泛化闸（用于对照：过拟合候选会被提交）")
    e.add_argument("--proposer", default="template", choices=["template", "llm"],
                   help="变异提议器：template（离线确定性）/ llm（真模型）")
    e.add_argument("--llm-model", default="", help="真模型名（默认 Qwen3.5-4B:latest）")
    e.add_argument("--heldout-floor", default="0.0",
                   help="held-out 增益地板（低于它判 overfit）")
    e.add_argument("--parallel", default="0",
                   help="并发评估线程数。0/1=串行（**默认**，与历史基线同口径）。\n"
                        "⛔ 并行只包住 runner 计算，run_id 分配与账本写入仍串行\n"
                        "（顺序漂移 ⇒ 账本不可复现）。⛔ 任务若逼近 timeout，\n"
                        "并行下的资源竞争可能把它挤成 timeout ⇒ 结果会变 ⇒\n"
                        "要可比就用默认串行。")
    e.add_argument("--embedder", default="hash", choices=["hash", "auto", "ollama"],
                   help="嵌入器：hash（默认，离线）/ auto|ollama（须过选型闸）")

    u = sub.add_parser("ui", help="起 API + 控制台，并打开桌面窗口")
    u.add_argument("--port", type=int, default=8611)
    u.add_argument("--caps", default="capabilities.example.json")
    u.add_argument("--caps-hot", action="store_true",
                   help="开能力热装载（默认关 —— 宪法第 52 条）")
    u.add_argument("--no-open", action="store_true", help="只起服务，不开窗口")

    args = ap.parse_args(argv)
    if args.cmd == "ui":
        from .ui import main as ui_main
        return ui_main(["--port", str(args.port),
                        "--root", str(args.root),
                        "--caps", args.caps]
                       + (["--caps-hot"] if args.caps_hot else [])
                       + (["--no-open"] if args.no_open else []))
    return {
        "status": cmd_status,
        "verify-chain": cmd_verify_chain,
        "rebuild": cmd_rebuild,
        "synth": cmd_synth,
        "memory": cmd_memory,
        "evolve": cmd_evolve,
        "caps": cmd_caps,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

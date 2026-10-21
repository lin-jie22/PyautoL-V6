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
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logging_setup import get_logger, setup as _log_setup
#: ⛔ 显式传名，不用 `__name__`：`python -m aether.cli` 时 `__name__` 是
#:    `"__main__"` ⇒ 多进程日志（CLI + 常驻 serve + UI 父进程）里三家的
#:    logger 全叫 __main__，分不清哪行是谁打的（契约 C2 实测抓到）。
_log = get_logger("aether.cli")
from .entry import (DEFAULT_CAPS, NO_CAPS, add_log_args, add_root_arg,
                    add_serve_args, add_ui_args, build_forward,
                    normalize_log_args)
#: ⛔ `ROOT` 与数据布局来自 `paths`（唯一事实源），不在这里再推一遍
#:    —— 三个入口各推一次就会漂移（r36：此前 cli/serve/ui 各写一行）。
from .paths import ROOT, paths as _paths


def _setup_logging(level: str, json_output: bool = False) -> None:
    """⛔ 只由 `main()` 调一次。非法级别在这里就退出（rc=2），
    而不是让它在 `setup()` 里抛栈 —— 命令行参数错是**用法错**，不是崩溃。"""
    try:
        _log_setup(level=level, json_output=json_output)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)   # logger 还没配上，只能用 print
        raise SystemExit(2) from None


#: 自带环境。**四个是刻意的**：两个只能区分"变强"与"讨好一个环境"，
#: 四个才能让 held-out 有难度跨度、且 dev/held-out 可以任意配对。
#: 解法形状刻意不同：数值归一化 / 流式聚合 / 图搜索 / 扫描线+贪心。
#:
#: ⛔⛔ 但"四个"同时也是 **r47 找到的能力天花板的直接来源**：2026-10-17
#:    实测这台引擎走到第 2 代就不再进化 —— 不是机器不行，是**世界只有四个
#:    小函数**（每份 task.py 3–10 条判据），两个胶囊就把 dev 集打满，
#:    轮 3 起 `no_weakness_evidence` 主动停机。⇒ 见下方 `discover_envs`。
DEFAULT_ENVS = ("textscore", "eventflow", "stateflow", "scheduler")


def discover_envs(tasks_root: "Path") -> Tuple[str, ...]:
    """扫出所有合法环境目录（含 `task.py` + `tests.json`）。

    ⛔ **顺序必须稳定且历史可比**：`DEFAULT_ENVS` 里存在的一律排在前，
    其余按名字追加。若按目录 mtime 或直接 `sorted()` 混排，既有
    `run_id` 与冻结基线的对齐会漂移 ⇒ 账本不可复现。

    ⛔ 这个函数**不是**默认口径：`cmd_evolve` 仍默认只用 `DEFAULT_ENVS`
    四个。扩大世界必须显式传 `--envs auto`（见第 103 条：新口径显式开启，
    否则历史对照失效）。
    """
    try:
        subs = [d for d in tasks_root.iterdir() if d.is_dir()]
    except OSError:
        return DEFAULT_ENVS
    found = {d.name for d in subs
             if (d / "task.py").is_file() and (d / "tests.json").is_file()}
    head = tuple(n for n in DEFAULT_ENVS if n in found)
    tail = tuple(sorted(found - set(head)))
    return head + tail

def resume_window(target_rounds: Any, ck_round_no: Any,
                  ) -> Optional[Tuple[int, int]]:
    """算出续跑窗口，返回 `(start_round, remaining)`；无需续跑则返回 None。

    ⛔⛔ 这里承载过一次**一票否决级死锁**（2026-10-17），务必看清：

    旧实现是 `remaining = ck.rounds_total - ck.round_no`，两个数**都来自
    checkpoint**（上次运行的计划轮数与已完成轮数）。而上一次运行**必定**写到
    `round_no == rounds_total` 才结束 ⇒ `remaining` 恒为 0 ⇒ 无论本次命令行
    传 `--rounds` 多少，都判定"已全部跑完"⇒ **一个进化存过断点之后就再也
    续不动了**。

    实测：`--rounds 2` 后再 `--rounds 4 --resume`、`--rounds 6 --resume`，
    两次都直接 return 0，账本零新增事件。这也是生产账本里
    "87 次提交、最长血缘只有 2 代"的直接原因之一。

    ⛔ 正确语义：**`target_rounds` 是本次命令行表达的意图**（目标总轮数），
    checkpoint 只提供"已完成到第几轮"，余量 = 目标 − 已完成。二者谁是权威，
    决定了这个系统能不能跨进程累积 —— 答案必须是人。
    """
    try:
        target = int(target_rounds)
        done = int(ck_round_no)
    except (TypeError, ValueError):          # noqa: BLE001
        return None
    if done < 0 or target < 0:
        return None
    remaining = target - done
    return None if remaining <= 0 else (done, remaining)


#: 任务类别。**路由按它来**，不按环境名 —— 见 TemplateProposer.route_key。
TASK_CLASS = "repair"

#: ⛔ 种子**不许预置修法**（2026-10-17 实测证伪，别再加回来）：给种子装
#:    `fix_control_flow` 能立刻换来闸的判别力，但进化活动直接减半
#:    （3 轮对照：事件 155→70、闸门裁决 10→3、提交 2→1）—— 外部预置的
#:    能力会**掩盖**"系统还没有它"这个真实弱点。判别力应由
#:    `evolution._rebaseline`（基线随 best 升级）取得，不由预置取得。

#: 冻结套件 v2 的合成参数。`max_n` 必须给够：每个环境前几次变异很可能
#: "没让测试变红"（rejected_no_failure），max_n=2 时只有 scheduler 出题
#: ⇒ 会误判成"另外三个环境合成不出任务"（实测：textscore 7 次尝试中
#: 4 次无效、eventflow 6 次中 3 次无效）。
FROZEN_SYNTH_N = 8
#: ⛔ 每环境 1 条而不是 2 条：冻结套件的大小就是**每份契约的付费项**
#:    （闸对**每个候选**都要跑满整套）。12 条时 `evolve --rounds 3`
#:    与相关契约的执行量直接翻倍，而判别力只从 8 涨到 12 ⇒ 不划算。
#:    ⛔ 省成本只能省在这里 —— **不能省在"这条题要不要执行"上**：
#:    跳过"基线没通过"的题会把 `runner 全崩` 变成静默放行（实测被
#:    `contract_nongression_test` C2/G5 当场抓红）。
FROZEN_BROKEN_PER_ENV = 1


# ──────────────────────────────────────────────────────────────────
# 各子命令
# ──────────────────────────────────────────────────────────────────
def cmd_init(args: argparse.Namespace) -> int:
    """`aether init`：建工作区骨架（r61）。

    ⛔ 目标目录只看 `--dir` / 当前目录（**不看 `--root`**，理由见 parser 处
       的注释：`--root` 在安装态的默认值是用户数据目录，拿它当默认目标会把
       工作区建到"家里"而不是"我现在站的地方"）。
    ⛔ `noop`（在 V6 仓库根上跑）返回 **0** 而不是非 0：这不是失败，是
       "本来就是它"。报非 0 会让脚本把正常情况当异常 —— 与"这次没装成 ≠
       提案被否决"是同一条（第 138 条）。
    """
    from .workspace import init as _ws_init
    target = Path(args.dir) if getattr(args, "dir", "") else Path.cwd()
    r = _ws_init(target, dry_run=bool(getattr(args, "dry_run", False)))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r["noop"]:
        why = r["reasons"][0] if r["reasons"] else "不需要建"
        print(f"✅ 无事可做：{why}")
        return 0
    if r["dry_run"]:
        print(f"🧪 演练：以上 {len(r['created'])} 项**都没有落盘**"
              f"（去掉 --dry-run 才真建）")
        return 0
    root = Path(r["root"])
    print(f"✅ 工作区已就绪：{root}")
    #: ⛔ 落点必须从 `_paths` 取，不许手拼 `root / "data" / "ledger.jsonl"`
    #:    —— 本轮实测就被 `contract_entrypoints` 的 E1 静态闸抓了一次
    #:    （第 46 条：布局只有一份。抓到了说明那道闸有牙，不是装饰）。
    print(f"   数据落点：{_paths(root)['ledger']}")
    if r["existing"]:
        print(f"   已存在未动：{', '.join(r['existing'])}")
    #: ⛔ 示例任务的读物必须分开三态（第 14 条）：
    #:    写进去的 / 本来就有的 / 包里根本没有的 —— 合成一句"已就绪"，
    #:    用户跑 `synth` 时才发现 tasks/ 是空的，那才是真的伤信任。
    sd = r.get("seeds") or {}
    if sd.get("seeded"):
        envs = sorted({p.split("/")[1] for p in sd["seeded"]})
        print(f"   示例任务集：{', '.join(envs)}"
              f"（{len(sd['seeded'])} 个文件，来自 {sd.get('source')}）")
    if sd.get("existing"):
        print(f"   示例任务集已存在未覆盖：{len(sd['existing'])} 个文件"
              f"（你自己改过的那份**没有被动**）")
    if not sd.get("available"):
        print(f"⚠️ {sd.get('reason') or '包里没有示例任务集'}")
        print("   要跑合成任务：在 V6 仓库目录里直接跑（仓库自己就是数据根）")
    print("   下一步：aether status / aether selfcheck")
    if sd.get("seeded"):
        #: ⛔ 必须给一个**真能出数**的参数：实测 `--max 2` 时 attempted=2、
        #:    两条变异恰好都是"无行为差异"⇒ yield=0、rc=1，看起来像坏了。
        #:    `--max 24` 实测 attempted=10 / valid=5。让用户第一步就撞红
        #:    是最伤的（"装完就不能用"）。
        print("   跑一次合成任务（出数需要样本量）：aether synth --max 24")
    return 0


def cmd_selfcheck(args: argparse.Namespace) -> int:
    """`aether selfcheck`：秒级自证（r61）。

    ⛔ 与 `check` 是**两件事**，别合并（合并只会得到一个"两边都不像"的命令）：
       · `check`    —— 跑 `tests/` 里的 68 份契约，33–37 分钟，**只在仓库里**；
       · `selfcheck` —— 跑内核自带的不变式，秒级，**装出来的 wheel 里也有**。
       装完之后 `check` 只会说"0 份契约 ⇒ 判失败"（fail-closed 是对的），
       那时用户该敲的命令是这一条。
    ⛔ 只读：自检不写账本、不改工作区、不碰包目录（数据全落临时目录）。
    """
    from .selfcheck import run as _sc_run
    from .selfcheck import summarize as _sc_sum
    rep = _sc_run(Path(args.root) if getattr(args, "root", "") else None,
                  only=(getattr(args, "only", "") or None))
    if getattr(args, "json", False):
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(_sc_sum(rep))
    return int(rep["exit_code"])


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
    #: ⛔ 尺子分区**搭同一次遍历**（`CaliberAgg` 与 `caliber.partition`
    #:    同一份逻辑）。它进 status 不是装饰：`aether caliber --seal` 之后
    #:    这里的 `dropped_sealed` 必然从 0 变正 ⇒ 封存**改变了一个真读数**
    #:    （第 35b 条：处置动作必须有可观测后果）。
    from . import caliber as _C
    cal_agg = _C.CaliberAgg()
    try:
        for ev in led.iter_events():
            stat_agg.feed(ev)
            mem_agg.feed(ev)
            cal_agg.feed(ev)
            if cons_agg is not None:
                cons_agg.feed(ev)
        st = stat_agg.result()
        cons = cons_agg.result() if cons_agg is not None else None
        _cal = cal_agg.result()
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
        #: ⛔ 尺子读数**无条件打印**（第 35 条）：不打印 ⇒ "混了几把尺子"
        #:    这件事在 status（最常用的读数入口）上永远看不见。
        print(json.dumps({"caliber": _cal}, ensure_ascii=False, indent=2))
        print("尺子分区：" + _C.render(_cal).replace("\n", "\n  "))
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
            _log.warning(f"[memory] 找不到清单：{src}")
            return 2
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            _log.warning(f"[memory] 清单不是合法 JSON：{e}")
            return 2
        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            _log.warning("[memory] 清单需要 items 数组")
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
            _log.error(f"❌ 已剪 {len(victims)} 条，但**没留痕**：{err}")
            return 1
        print(f"✅ 已剪 {len(victims)} 条并逐条留痕（remaining={rep['remaining']}）")
        return 0

    # ── reindex ──────────────────────────────────────────────────
    before = memory_report(p["memory"])
    if not before.get("present"):
        _log.error(f"❌ 没有可重算的记忆库（{p['memory']}）")
        return 1
    emb: Any = HashingEmbedder(dim=256)
    if args.to != "hash":
        try:
            cand, probe = resolve_embedder(args.to)
            if probe.get("chosen") != "ollama" and args.to == "ollama":
                _log.error(f"❌ 目标嵌入不可用：{probe}")
                return 1
            emb = cand
        except Exception as e:  # noqa: BLE001
            _log.error(f"❌ 目标嵌入不可用（{type(e).__name__}: {e}）")
            return 1
    mem, rep = MemoryOS.load_for_reindex(p["memory"], emb, min_content_len=60)
    if not rep["loaded"]:
        _log.error(f"❌ 没有可重算的条目：{rep.get('note')}")
        return 1
    r = mem.reindex(emb, from_label=f"{rep.get('from_embedder')}:{rep.get('from_dim')}")
    out = {"before": before, "load": rep, "reindex": r}
    if r.get("rolled_back"):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        _log.error(f"❌ reindex 失败已回滚：{r.get('error')}")
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
            _log.error(f"❌ 没有投影库可校验（{p['db']}）")
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
    #: ⛔ r61：装成 wheel 后**本来就没有任务源**（`tasks/` 是仓库资产，
    #:    刻意不打进包，见 `pyproject.toml` 的 `package-data` 注释）。
    #:    不拦这一下的话用户拿到的是**裸栈**，而栈里那句话（`task.py`
    #:    的 `read_text` 抛 `FileNotFoundError`）既不说是"装出来的包没有
    #:    任务源"，也不说该怎么办 —— 于是"环境坏了"和"这个安装里没有
    #:    燃料"看起来一模一样，而两者的下一件事完全相反（第 108 条）。
    #:    ⛔ 仍然返回 1：没有燃料**就是**失败，不是"这次跳过"。
    task_dir = p["task"]
    if not (task_dir / "task.py").is_file():
        print(f"⛔ 没有任务源：{task_dir}/task.py 不存在", file=sys.stderr)
        print("   原因：V6 自带的任务源随仓库走、**不进 wheel**"
              "（装出来的包里 tasks/ 是空的）。", file=sys.stderr)
        print("   三条出路（都验过）：", file=sys.stderr)
        print("     ① 在仓库目录里跑 —— 仓库自己就是数据根：", file=sys.stderr)
        print("        cd <V6 仓库> && aether synth", file=sys.stderr)
        print(f"     ② 自己放一套任务源进来：{task_dir}/task.py + tests.json",
              file=sys.stderr)
        print("     ③ 指向一个已有任务的工作区：aether --root <工作区> synth",
              file=sys.stderr)
        return 1
    env = Environment.from_dir(task_dir)
    base = verify_source(env)
    print(f"环境 {env.env_id} fingerprint={env.fingerprint} "
          f"基线通过 {base.n_passed}/{len(base.checks)}")
    _kinds: Tuple[str, ...] = tuple(
        x.strip() for x in (getattr(args, "kinds", "") or "").split(",")
        if x.strip()) or DEFAULT_MUTATION_KINDS
    rep = synthesize_tasks(env, max_n=int(args.max),
                           depth=int(getattr(args, "depth", "1") or "1"),
                           kinds=_kinds,
                           balanced=bool(getattr(args, "balanced", False)))
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
        patch:<env_id>          —— **定点修法**：只在点名的那个环境生效，修得全

    在**单一环境**的评测里，后者**严格更好**（发展集分数更高）⇒ 它一定会被
    选中 ⇒ 一定会被提交。只有 held-out 能把这两者分开。
    """
    from .agent import HarnessAgent
    return HarnessAgent(envs, memory=memory, timeout=timeout).as_runner()


def _verify_fixed(env, broken_src, fixed_src, timeout: float = 10.0):
    """判定：修好后是否**所有原本通过的测试都回来了**（可执行判据）。"""
    from .environment import verify_source
    from .verifier import Verdict
    v = verify_source(env, source=fixed_src, timeout=timeout)
    n = len(v.checks)
    return Verdict(passed=v.passed, checks=v.checks,
                   evidence={"n_passed": v.n_passed, "n_total": n,
                             "changed": fixed_src != broken_src},
                   env_fingerprint=env.fingerprint, cost_s=v.cost_s)


def cmd_evolve(args: argparse.Namespace) -> int:
    from .environment import (DEFAULT_MUTATION_KINDS, Environment,
                              synthesize_tasks)
    from .evolution import EvolutionConfig, EvolutionLoop, RunBudget
    from .generalization import GeneralizationGate, make_heldout
    from .genome import Harness
    from .ledger import Ledger
    from .nongression import Baseline, FrozenTask, NonRegressionGate
    from .providers import TemplateProposer
    from .store import Store

    p = _paths(Path(args.root))
    #: ⛔⛔ **世界规模是效用第一的实验变量**（r47）。默认四个 ⇒ 与历史同口径；
    #:    `--envs auto` / 显式名单 ⇒ 新实验，账本里的 `envs` 字段会如实记录。
    _want = str(getattr(args, "envs", "") or "").strip()
    if _want == "auto":
        names = discover_envs(p["tasks_root"])
    elif _want:
        names = tuple(x.strip() for x in _want.split(",") if x.strip())
    else:
        names = DEFAULT_ENVS
    missing = [n for n in names
               if not (p["tasks_root"] / n / "task.py").is_file()]
    if missing:
        raise SystemExit(f"环境目录不存在或缺 task.py：{missing}"
                         f"；可用 {sorted(discover_envs(p['tasks_root']))}")
    envs = {name: Environment.from_dir(p["tasks_root"] / name)
            for name in names}
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
        _log.info(f"💾 记忆装载：{mem_load['note']}")
    runner = _default_runner(envs, memory=mem)

    #: ⛔⛔ 变异参数**默认与既有口径完全一致**（depth=1 / kinds=默认三种）。
    #:    `which` 是全局遍历序编号，任何默认行为的改动都会让历史账本
    #:    与基线不可比 ⇒ 新口径只能由命令行**显式**开启，算作新实验。
    _kinds: Tuple[str, ...] = tuple(
        x.strip() for x in (getattr(args, "kinds", "") or "").split(",")
        if x.strip()) or DEFAULT_MUTATION_KINDS
    _depth = int(getattr(args, "depth", "1") or "1")
    #: ⛔ `require_behavior` **默认关闭**：它改的是"考卷上有哪些题"，
    #:    开了之后任务集就与历史不可比 ⇒ 只能显式开启，算作新实验。
    #:    开着它跑，是为了回答「第 3 代是不是建立在崩溃题上」。
    _require_behavior = bool(getattr(args, "require_behavior", False))
    #: ⛔ `balanced`（按维度轮流取变异点）**默认关闭**，理由同上：它换的是
    #:    "考卷上有哪些题"，开了就与历史不可比。
    #:    ⛔ 它**刻意不作用于冻结套件**：冻结套件是**基线锚点**，非回归闸
    #:    的判据集不能随采样实验漂移（V5 教训：判据不能被退化 run 重建）。
    #:    ⇒ 本轮唯一的变量是 dev / held-out 的采样顺序。
    _balanced = bool(getattr(args, "balanced", False))
    #: ⛔ `ho_grow`（held-out 判别力自愈）**默认关闭**（r53）：它换的是
    #:    held-out 的**题数**（考卷），不是判据 ⇒ 开了就与历史基线不可比。
    _ho_grow = bool(getattr(args, "ho_grow", False))
    _ho_grow_max_n = int(getattr(args, "ho_grow_max_n", "64") or 64)
    _ho_grow_max_tasks = int(getattr(args, "ho_grow_max_tasks", "96") or 96)
    #: ⛔ r55：换采样口径的档数。1 = 只用主口径（与 r53/r54 逐字同口径）。
    _ho_grow_variants = max(1, int(getattr(args, "ho_grow_variants", "1") or 1))
    #: ⛔ `retry_every`（被拒候选的 probation 出口）**默认 0 = 永久不重提**
    #:    （r54）：与 `deadend_probation_every` 同形、同样默认关闭 ⇒ 默认
    #:    口径与历史逐字可比。它不改任何判据，只改"要不要再给一次机会"。
    _retry_every = int(getattr(args, "retry_every", "0") or 0)

    # ⛔ 种子 harness **必须先建**：冻结基线要用它跑。
    #    用空 `Harness()` 跑基线 ⇒ 连工具都没有 ⇒ 基线全红 ⇒
    #    非回归闸"没有原本通过的任务"⇒ 对任何候选都放行的空闸（V5 同款陷阱）。
    #: ⛔⛔ **种子不许预置修法**（2026-10-17 实测证伪）：给种子装
    #:    `fix_control_flow` 确实能让冻结晶线有判别力，但代价是进化活动
    #:    直接减半（同样 3 轮：事件 155→70、闸门裁决 10→3、提交 2→1）——
    #:    与"记忆预置修法 ⇒ 候选增益 0 ⇒ 空转"是同一条纪律：外部预置的
    #:    能力会**掩盖**"这个系统还没有它"这个真实弱点。
    #:    ⇒ 判别力由**基线随 best 升级**取得（见 `evolution._rebaseline`），
    #:    不由预置取得。
    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5, "min_content_len": 150})

    # 冻结套件 v2 = 原始源码（防"读不到/跑不动"这类**粗**回归）
    #              + 真破坏题（防"已具备的修法被顶掉"这类**细**回归）
    #: ⛔ v1 只有原始源码 ⇒ 裸种子的通过集 = {本来就不需要修的题}
    #:    ⇒ 提案器只会装技能/改路由/改元规则，全都碰不到这个集合
    #:    ⇒ 账本 234 条 `gate.result` 全 True、`gate.blocked` 0 次。
    #:    ⛔ 破坏题**不按种子筛**：种子现在修不好，best 装上修法后就修得
    #:    好 —— 基线一升级，它们自动进入"必须守住"的通过集
    #:    （见 `evolution._rebaseline`）。按种子筛会把它们永久筛掉。
    frozen = [FrozenTask(task_id=f"frozen::{name}",
                         spec={"env_id": name, "broken_source": e.source,
                               "topic": name, "task_class": TASK_CLASS})
              for name, e in envs.items()]
    _broken: List[FrozenTask] = []
    for name, e in envs.items():
        rep_f = synthesize_tasks(e, max_n=FROZEN_SYNTH_N, timeout=10.0,
                                 depth=1, kinds=_kinds)
        for i, t in enumerate(rep_f.tasks[:FROZEN_BROKEN_PER_ENV]):
            _broken.append(FrozenTask(
                task_id=f"frozen::{name}::m{i}",
                spec={"env_id": name, "broken_source": t.broken_source,
                      "topic": name, "task_class": TASK_CLASS}))
    frozen = frozen + _broken
    led.append("frozen_suite.built", {
        "suite": "v2", "n_source": len(envs), "n_broken": len(_broken),
        "broken": [t.task_id for t in _broken],
        "note": "v1 只有原始源码 ⇒ 裸种子基线在天花板 ⇒ 闸对真实变异"
                "零判别力（实测 234 条 gate.result 全绿）"},
        actor="cli")
    caliber = {"seed": int(args.seed), "timeout": 10.0, "split": "frozen",
               "frozen_suite": "v2"}
    baseline = Baseline(
        caliber=caliber,
        verdicts={t.task_id: runner(h0, dict(t.spec)) for t in frozen},
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
    if args.heldout == "on":
        rep_ho = synthesize_tasks(envs[ho_name], max_n=int(args.max_tasks),
                                  depth=_depth, kinds=_kinds,
                                  require_behavior=_require_behavior,
                                  balanced=_balanced)
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
                                               kinds=_kinds,
                                               require_behavior=_require_behavior,
                                               balanced=_balanced).tasks,
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
                                 depth=_depth, kinds=_kinds,
                                 require_behavior=_require_behavior,
                                 balanced=_balanced)
    dev_src_tasks = rep_synth.tasks
    if ho_levels:
        g = grow_for_difficulty(
            envs[dev_name], lambda n: synthesize_tasks(
                envs[dev_name], max_n=n, depth=_depth, kinds=_kinds,
                require_behavior=_require_behavior,
                balanced=_balanced).tasks,
            ho_levels, max_n=int(args.max_tasks))
        dev_src_tasks = g["tasks"]
        grow_report = {k: v for k, v in g.items() if k != "tasks"}
        if g["note"]:
            print(("✅ " if g["covered"] else "⚠️ ") + g["note"],
                  file=sys.stderr)
        #: ⛔⛔ 缺层自愈的**第二个出口**：换**变异深度**补层（r51 实测）。
        #:    `grow_for_difficulty` 只会"加大 max_n"，但加大 n 补不出来的是
        #:    **这个口径根本造不出**的那种层 —— 实测 `--depth 2 --balanced`
        #:    下 dev 集 **26 条全是 hard**（双点跨维度叠加 ⇒ 单技能修不好），
        #:    而 held-out 有 easy 层 ⇒ 泛化闸判「dev 没有该层，无从对照」
        #:    ⇒ **6 轮 0 提交**，尽管 dev_gain 高达 +0.69。
        #:    ⛔ 那是 fail-closed 的正确行为，但系统因此**没有任何出口**：
        #:    「明明有增益却永不提交」与「候选不好」长得一模一样。
        #:    ⇒ 出口：降级用**单点**变异补 easy 层（单点题 = easy）。
        #:    ⛔ 只在「缺层 且 depth≥2」时触发 ⇒ 默认口径（depth=1）零影响。
        if not g["covered"] and _depth >= 2:
            g2 = grow_for_difficulty(
                envs[dev_name], lambda n: synthesize_tasks(
                    envs[dev_name], max_n=n, depth=1, kinds=_kinds,
                    require_behavior=_require_behavior,
                    balanced=_balanced).tasks,
                g["missing"], max_n=int(args.max_tasks))
            if g2["tasks"]:
                _seen = {t.task_id for t in dev_src_tasks}
                _added = [t for t in g2["tasks"] if t.task_id not in _seen]
                dev_src_tasks = list(dev_src_tasks) + _added
                grow_report["depth_fallback"] = {
                    "from_depth": _depth, "to_depth": 1,
                    "missing": list(g["missing"]),
                    "covered_after": bool(g2["covered"]),
                    "n_added": len(_added),
                    "n_tasks_after": len(dev_src_tasks),
                    "note": "加大采样仍缺层（该口径造不出这层）⇒ 降级用"
                            "单点变异补齐难度跨度",
                }
                print(f"{'✅' if g2['covered'] else '⚠️ '} 难度层降级自愈："
                      f"depth {_depth}→1 补 {len(_added)} 条题（缺 "
                      f"{'/'.join(g['missing'])}）⇒ dev 共 "
                      f"{len(dev_src_tasks)} 条", file=sys.stderr)
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
        #: ── r53：held-out 判别力自愈的两个注入点 ──────────────────
        #: ⛔ **由 CLI 注入而不是闸自己 import**：合成口径（depth / kinds /
        #:    balanced / require_behavior）是**实验变量**，只有命令行知道
        #:    这次跑的是什么口径。闸自己 import 一份 ⇒ 补出来的题与本次
        #:    实验的题**不同源** ⇒ "补采样"变成悄悄换了一把尺子。
        #: ⛔ 两个函数与上面建 held-out 用的是**同一份** `synthesize_tasks`
        #:    参数 ⇒ 补出来的题与原有题同分布（只差"基线做不对"这一筛选）。
        def _ho_synth_with(balanced: bool):
            """按给定采样口径合成 held-out 候选题（与建卷同源的入参）。"""
            def _fn(n: int):
                return [FrozenTask(
                    task_id=t.task_id,
                    spec={"env_id": ho_name,
                          "broken_source": t.broken_source,
                          "topic": ho_name, "task_class": TASK_CLASS})
                    for t in synthesize_tasks(
                        envs[ho_name], max_n=int(n), depth=_depth,
                        kinds=_kinds, require_behavior=_require_behavior,
                        balanced=balanced).tasks]
            return _fn

        def _ho_synth(n: int):
            return _ho_synth_with(_balanced)(n)

        #: ⛔ r55：**换采样口径**，不是加大采样量。
        #:    实测（probe_headroom_source A/E 段）：单个环境在一种口径下
        #:    题源有硬顶（max_n 64→256、depth 2→4 产出恒定 26 条）；
        #:    而 `balanced` 取反后在同一环境上给出**完全不重叠**的 26 条
        #:    （G 段实测重叠 0 条）。⇒ 撞顶时唯一有效的方向是换口径。
        #:    ⛔ 刻意**不换环境**：held-out 的"另一个环境"是这套闸的定义
        #:    （H1），换环境等于换尺子的单位；换取点顺序不换单位。
        def _ho_synth_alt(n: int):
            return _ho_synth_with(not _balanced)(n)

        def _ho_labels(pending) -> Dict[str, str]:
            return difficulty_report(envs[ho_name], list(pending))["labels"]

        heldout = GeneralizationGate(
            make_heldout(ho_name, ho_tasks,
                         difficulty=diff_report["labels"]),
            dev_task_ids=[t.task_id for t in dev],
            heldout_floor=float(args.heldout_floor),
            base_at_ceiling=1.0,
            #: ⛔ 默认 `ho_grow=False`（CLI 默认关）⇒ 不注入 ⇒ 与历史
            #:    逐字同口径。开了才注入，且进 `run_config`（实验变量）。
            grow_fn=(_ho_synth if _ho_grow else None),
            difficulty_fn=(_ho_labels if _ho_grow else None),
            #: ⛔ 默认 1 档 ⇒ `grow_variant_fns=()` ⇒ 与 r53/r54 逐字同口径。
            grow_variant_fns=((_ho_synth_alt,) if (_ho_grow and
                                                   _ho_grow_variants >= 2)
                              else ()),
            grow_ceiling=int(_ho_grow_max_n),
            grow_max_tasks=int(_ho_grow_max_tasks))

    # ⛔ 两类技能刻意并列：通用但保守 vs 定点但只此一地。
    #    在**发展集**上后者严格更好 ⇒ 一定会被选中 ⇒ 一定会被提交，
    #    除非泛化闸拦住它。这就是 reward hacking 的可执行形态。
    pool = {
        "fix_control_flow": "保守通用修法：只恢复控制流（比较/运算），不动字面量",
        "fix_literals": "通用字面量修法：只把数值常量对齐基线，不动结构/字符串",
        "fix_boolops": "通用布尔运算修法：只把 and/or 对齐基线，不动比较/运算/常量",
        "fix_strings": "通用字符串修法：只把字符串常量对齐基线，不动结构/数值/bool",
        "fix_unaryops": "通用取反修法：只把 not 的增删按树形位置对齐基线，不动其它",
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
    #: ⛔⛔ **供给端必须进账本**（r62）。此前 run_config 只有 `proposer` 一个
    #:    字符串 ⇒ 71 次跑完之后，账本里**读不出用的是哪个模型**（连字段都
    #:    没有，`.get("provider")` 恒 None ⇒ 曾被误读成"provider=None×71"，
    #:    实际是量具的盲区：把"没有这个键"当成了"值是 None"，第 24 条）。
    #:    ⇒ 这里记全三项：提议器种类、provider 实现、**实际生效的模型名**
    #:    （`--llm-model` 留空时取默认值 ⇒ 空串会掩盖"其实跑了 Qwen3.5-4B"）。
    #:    ⛔ 默认 template 也要记（`kind="template"`）：不写 ⇒ 又一次"读不出"，
    #:    而"没接到真模型"与"接上了但没产出"必须能区分。
    _llm_desc: Dict[str, Any] = {"kind": "template", "provider": "",
                                 "model": "", "fallback": ""}
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
                #: ⛔ **只讲逻辑后果，不讲答案**（r62 实验口径，默认关）：
                #:    "`patch:<env>` 只在点名环境生效"这句接口契约本来就在
                #:    prompt 里，但它到"held-out 必然 0 增益"这一步的**推论**
                #:    模型没做（实测它照样提 patch:textscore）。这里只把
                #:    推论写明，仍然不告诉它该改哪一层、该加哪个技能。
                if bool(getattr(args, "ho_implication", False)):
                    ho_note += (
                        f"\n由上面两条可直接推出：只在 `patch:{ho_name}` 或"
                        f"`patch:<发展集环境>` 上生效的改法，在 `{ho_name}`"
                        f" 上**必然**是 0 增益 ⇒ 必然被拦。要让它在两个环境"
                        f"上都起作用，改动必须落在**与环境无关**的层"
                        f"（例如通用技能正文、或把它们接进路由链）。")
            proposer = LLMProposer(
                OllamaProvider(model=model, think=False),
                route_keys=(TASK_CLASS,),
                skill_vocab=tuple(sorted(pool)),
                env_ids=tuple(sorted(envs)),
                heldout_note=ho_note)
            _llm_desc = {"kind": "llm", "provider": "ollama",
                         "model": model, "fallback": "",
                         "think": False, "max_tokens": LLMProposer.max_tokens}
            _log.warning(f"🧠 proposer=llm（{model}）")
        except Exception as e:  # noqa: BLE001
            #: ⛔ 回退必须留痕：`kind` 仍是 llm（用户要求的），但 `fallback`
            #:    写明为什么没成 ⇒ 否则事后看账本会以为真跑了模型。
            _llm_desc = {"kind": "llm", "provider": "ollama",
                         "model": model, "fallback": f"{type(e).__name__}: {e}"}
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

    #: ⛔⛔ ledger 必须**显式注入**（2026-10-17）：以前这里没传 ⇒ 经验闭环
    #:    的三个事件一条都进不了账本 ⇒ 全量扫描显示 `evolution:reject` 写入
    #:    仅 5 条、"因学到而少提了几个候选"完全无数 ⇒ 这条线在读数上是瞎的。默认 None 是 no-op（有机制不等于这条路被走到，第 4 条）。
    #: ⛔ `retry_every` 从 args 直接取（不依赖上面的临时变量）：这个包装器
    #:    是**提议器层的开关**，它必须跟着命令行走，不能因为某次重构挪了
    #:    变量定义位置就悄悄变成默认值 ⇒ 开关的位置与开关的读数必须同处。
    proposer = MemoryAwareProposer(
        proposer, mem, ledger=led,
        retry_every=int(getattr(args, "retry_every", "0") or 0))

    _consolidated_total = 0

    #: ⛔⛔ `KnowledgeLearner` 以前**在进化循环里从未被构造过**（全量账本
    #:    `knowledge.ingested` 0 次）⇒ 它的四道闸（去重 / 最短内容 / 配额
    #:    截断 / 可达性取证）在生产路径上一次都没生效，循环里的经验写都是
    #:    **裸 write** 绕过去的 —— 与 V5「两次 39x 重复」同族。这里把它接
    #:    进循环；⛔ 只接**本就属 learner 通道**的写入（下面 weakness 那条），
    #:    L4/system 的轮次摘要仍走原路（不改通道语义）。
    from .learner import KnowledgeItem, KnowledgeLearner
    _learner = KnowledgeLearner(mem, ledger=led, default_tier="L3",
                                max_items=int(getattr(args, "max_tasks", 6) or 6) * 4)
    #: ⛔ 跨轮**汇总**（单条在 learner 自己的 `knowledge.ingested` 里）：
    #:    只有把每轮报告加起来，才看得出"这几轮到底学进去了多少"。
    _ingest_total = {"input": 0, "written": 0, "deduped": 0, "rejected": 0,
                     "below_min": 0, "truncated": 0, "unretrievable": 0}

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
            _log.warning(f"⚠️ 巩固失败：{type(_e).__name__}: {_e}")
        _consolidated_total += len(made)
        loop.aging_events["consolidated"] = len(made)
        #: 剪枝**只报数不删** —— 不可逆 ⇒ 该不该剪由人拍板。
        #: ⛔ 读 `will_delete` 而**不是**臆造键名：原版写的是
        #:    `.get("victims", [])`，而 `prune_dryrun()` 当时从不返回该键
        #:    ⇒ 表达式恒为 0 ⇒ `aging_events["pruned"]` 恒 0 ⇒ 老化读数
        #:    对"库在增殖"**永久失明**（一个静默归零的读数比没有读数更坏）。
        try:
            dry = mem.prune_dryrun()
            loop.aging_events["pruned"] = int(dry.get("will_delete", 0))
            loop.aging_events["prune_candidates"] = int(dry.get("n", 0))
        except Exception:                              # noqa: BLE001
            pass

    #: ⛔⛔ 断点续跑**默认开**（r58）：第 60 条承诺"中间态可恢复"，而默认
    #:    口径下 `checkpoint_path=None` ⇒ 一次都不写 ⇒ 那条承诺在默认口径下
    #:    **不成立**（r57 实测 `evolution.checkpoint` 0 次、模块覆盖率 0%）。
    #:    ⛔ 第 12 条：默认开的开关必须能**显式关** ⇒ 配 `--no-checkpoint`。
    #:    ⛔ 第 46 条：位置只从 `paths()` 取，不在这里手拼。
    if getattr(args, "no_checkpoint", False):
        cp_path = ""
    else:
        cp_path = str(getattr(args, "checkpoint", "") or ""
                      or _paths(Path(args.root))["checkpoint"])

    def _int_or_none(v):
        v = str(v or "").strip()
        return int(v) if v else None

    def _float_or_none(v):
        v = str(v or "").strip()
        return float(v) if v else None

    budget = RunBudget(max_rounds=_int_or_none(getattr(args, "max_rounds", "")),
                       max_seconds=_float_or_none(
                           getattr(args, "max_seconds", "")),
                       max_propose_calls=_int_or_none(
                           getattr(args, "max_propose", "")),
                       max_eval_runs=_int_or_none(
                           getattr(args, "max_eval_runs", "")))
    #: ⛔ 没设任何一项 ⇒ 传 None（保持旧行为），不是传一个"全空预算"。
    #:    全空预算会让 `budget.limited` 报 False 但每轮多一份读数 ⇒
    #:    "装了止损"和"没装"在输出里长得一样（假仪表）。
    if not budget.limited:
        budget = None

    #: ⛔ 分层非支配存档（r62，默认关）：`--archive-resample 0` ⇒ 不注入
    #:    Archive ⇒ 一条 `archive.*` 事件都不会有 ⇒ 与历史逐字同口径。
    #:    ⛔ 落盘路径随数据根走（不是仓库根）：存档是**这次实验的产物**，
    #:    不是代码资产。
    _arch_n = int(getattr(args, "archive_resample", "0") or 0)
    _archive = None
    if _arch_n > 0:
        from .archive import Archive
        _arch_cap = int(getattr(args, "archive_cap", "8") or 8)
        #: ⛔ 从 `_paths()` 取 `ledger` 的**父目录**，不手拼 "data" ——
        #:    手拼目录名会被 `contract_entrypoints` 的 E 组抓住（r61 console
        #:    同族：路径只许从 paths 派生）。
        _archive = Archive(
            path=os.path.join(
                os.path.dirname(str(_paths(Path(args.root))["ledger"])),
                "archive.jsonl"),
            cap=_arch_cap).load()

    loop = EvolutionLoop(led, gate, dev, runner, proposer, store=store,
                         caliber=caliber, heldout=heldout,
                         dev_difficulty=dev_difficulty,
                         on_round_end=_remember_round,
                         checkpoint_path=cp_path or None,
                         budget=budget,
                         archive=_archive,
                         config=EvolutionConfig(
                             group_size=int(args.group),
                             #: ⛔ 默认 1 = 「best 被拦 ⇒ 整轮结束」（与历史
                             #:    基线逐字同口径）。>1 ⇒ 同轮按发展集排序
                             #:    依次试下一个 —— 闸一条没少，held-out 仍
                             #:    不参与排序（H1 不动）。见 r52。
                             gate_tries=int(
                                 getattr(args, "gate_tries", "1") or 1),
                             #: ⛔ 默认 False ⇒ 与历史基线逐字同口径（r53）。
                             ho_grow=bool(_ho_grow),
                             ho_grow_max_n=int(_ho_grow_max_n),
                             ho_grow_max_tasks=int(_ho_grow_max_tasks),
                             #: ⛔ 默认 0 = 不重采样（与历史基线同口径，r62）。
                             archive_resample=_arch_n,
                             archive_cap=int(
                                 getattr(args, "archive_cap", "8") or 8),
                             #: ⛔ 默认 0 = 串行（与历史基线同口径）。并行只
                             #:    包住 runner 计算，记账仍串行（见 §23.5）。
                             parallel_workers=int(
                                 getattr(args, "parallel", "0") or 0),
                             #: ⛔ 默认 `exact` = 弱点里带具体检查名（历史同
                             #:    口径）。`coarse` 只给失效类别与数量 ——
                             #:    换的是**给模型的视图**，闸与判据不动（r62）。
                             evidence=str(
                                 getattr(args, "evidence", "exact") or "exact"),
                             #: ⛔ 默认 False = 历史逐字同口径（env 专属胶囊
                             #:    在 held-out 上 0 增益仍判 overfit）。
                             allow_scoped_commit=bool(
                                 getattr(args, "allow_scoped_commit", False))))

    #: ── 断点续跑 ──────────────────────────────────────────────
    #: ⛔ resume 时**不**重新 register_root：root 已经在上一次运行里写进
    #:   账本了，再写一条会让账本出现两个 root ⇒ "唯一事实源"自相矛盾。
    start_round = 0
    h_start = h0
    rounds = int(args.rounds)
    resumed_from = None
    did_resume = False
    if getattr(args, "resume", False):
        if not cp_path:
            _log.error("❌ --resume 需要配合 --checkpoint <路径>")
            return 2
        try:
            cpx, h_start = loop.load_checkpoint(cp_path)
        except Exception as e:      # noqa: BLE001 —— 拒绝原因要原样给人看
            _log.error(f"❌ 无法续跑：{e}")
            return 2
        #: ⛔⛔ **这里原先是一票否决级的死锁**（2026-10-17 实测修复）。
        #:    旧代码用 `cpx.rounds_left`（= checkpoint 里存的**上次**
        #:    `rounds_total` − `round_no`）判断要不要续跑。而上一次写完
        #:    checkpoint 时二者必然相等 ⇒ `rounds_left == 0` ⇒ 无论这次
        #:    命令行传 `--rounds` 多少，都打印"已全部跑完 ⇒ 无需续跑"。
        #:    **后果：一个进化一旦存过断点，就再也续不动了。**
        #:    实测 `--rounds 2` 后再 `--rounds 4 --resume` / `--rounds 6
        #:    --resume` ⇒ 两次都直接 return 0，账本零新增事件。
        #:    这正是生产账本里"87 次提交、最长血缘只有 2 代"的根因之一
        #:    （配合单次运行最快在第 4 轮就 idle 停机）。
        #:
        #:    正确语义：`--rounds` 是**本次目标总轮数**（人的意图），
        #:    checkpoint 只提供"已完成到第几轮"。余量 = 目标 − 已完成。
        target = rounds                       # 命令行给的目标总轮数
        win = resume_window(target, cpx.round_no)
        if win is None:
            print(f"✅ checkpoint 已完成第 {cpx.round_no} 轮，"
                  f"本次目标 {target} 轮 ⇒ 无需续跑。")
            return 0
        start_round, rounds = win
        resumed_from = cpx.round_no
        did_resume = True
        print(f"♻️  续跑：从第 {start_round + 1} 轮开始，再跑 {rounds} 轮"
              f"（目标 {target} 轮，已完成 {cpx.round_no} 轮）")
    elif (not getattr(args, "no_continue", False)
          and not getattr(args, "fresh", False)
          and cp_path and os.path.exists(cp_path)):
        #: ── 自动接续（默认行为，r60 F）────────────────────────────
        #: ⛔ 与 `--resume` 的**语义差别**必须钉住，否则两个开关会打架：
        #:    `--resume`：`--rounds` 是**目标总轮数**（人的意图，老语义）；
        #:    自动接续：`--rounds` 是**本次追加**轮数（调度者每 30–80 分钟
        #:    跑一次 `--rounds 1`，它说的当然是"再跑一轮"，不是"总数 1"）。
        #:    ⛔ 混用会把"追加 1 轮"读成"目标 1 轮"⇒ 余量 0 ⇒ 永远不跑。
        try:
            cpx, h_start = loop.load_checkpoint(cp_path)
        except Exception as e:      # noqa: BLE001 —— 拒绝原因要原样给人看
            #: ⛔ 连续性断了（换尺子 / 换配置 / 账本回退）⇒ **从头跑，但必须
            #:    说出来**。静默从头 = 把"断了"伪装成"正常"，而两者在账本
            #:    里长得一样（都是新一轮从 round 1 起）⇒ 第 24/25 条。
            print(f"⚠️  不接续：{e}")
            print("   ⇒ 本次从初始 harness 起跑（本轮结束会覆盖 checkpoint）。"
                  "要留住上次进度，先自行备份该文件。")
            try:
                led.append("evolution.continuity_broken",
                           {"reason": str(e)[:300], "checkpoint": cp_path},
                           actor="evolution")
            except Exception:       # noqa: BLE001 —— 记账失败不拖垮这次运行
                pass
        else:
            start_round = int(cpx.round_no)
            resumed_from = start_round
            did_resume = True
            print(f"♻️  接续：第 {start_round} 轮已完成，本次追加 {rounds} 轮"
                  f"（目标 {start_round + rounds} 轮，"
                  f"harness={cpx.harness_id[:8] or '?'}）")
    if not did_resume:
        #: ⛔ 只在**从头跑**时注册 root：接续时 root 已经在上一次写进账本，
        #:    再写一条会让账本出现两个 root ⇒ 「唯一事实源」自相矛盾。
        #: ⛔ 初始 harness 必须**先进账本**再落投影：只写库的话，从账本重建
        #:    会丢掉它（实测 7 个快照重建只剩 6 个）⇒ 「唯一事实源」被违反。
        loop.register_root(h0)

    #: ⛔ **实验可复现性**（r35）：跑之前先把"这次用的什么参数"写进账本。
    #:    此前命令行参数只活在终端里 —— 一周后回看账本，能看见改了什么、
    #:    却**看不出是用什么口径跑出来的** ⇒ 同样的代码跑出不同结果时
    #:    无从分辨是随机性还是参数变了。账本是唯一事实源，实验配置就该
    #:    是它的一部分（与 harness / 事件同级，不是散落在 shell history）。
    run_config = {
        "seed": args.seed, "rounds": args.rounds, "group": args.group,
        "max_tasks": args.max_tasks, "depth": args.depth,
        "kinds": args.kinds, "capsules": args.capsules,
        "proposer": args.proposer, "dev_env": args.dev_env,
        #: ⛔ 供给端的**实际**配置（不是命令行字符串）：见 `_llm_desc` 的说明。
        #:    `fallback` 非空 ⇒ 用户要了 llm 但回退成 template，必须能读出来。
        "llm": dict(_llm_desc),
        "heldout_env": args.heldout_env, "heldout": args.heldout,
        "heldout_floor": args.heldout_floor,
        #: ⛔ 采样口径必须进账本（r51）：同样的 `max_n` 下，顺序与均衡能差
        #:    37% 的题量与 11 倍的维度覆盖 ⇒ 不记就读不出"换的是口径还是
        #:    换的是世界"（与 `n_envs` 同族：世界规模也是实验变量）。
        "balanced": bool(_balanced),
        "require_behavior": bool(_require_behavior),
        #: ⛔ 判别力自愈是**实验变量**（换了考卷）⇒ 必须进账本，否则
        #:    "这次为什么能多提交一代"事后无从分辨是机器变好还是卷子变了。
        "ho_grow": bool(_ho_grow),
        "ho_grow_max_n": int(_ho_grow_max_n) if _ho_grow else 0,
        "ho_grow_max_tasks": int(_ho_grow_max_tasks) if _ho_grow else 0,
        "ho_grow_variants": int(_ho_grow_variants) if _ho_grow else 1,
        #: ⛔ 换的是"要不要再给一次机会"，不是判据 ⇒ 同样必须进 run_config
        #:    （第 40 条：实验变量必须进账本，否则口径差异不可复现）。
        "retry_every": int(_retry_every),
        #: ⛔ 存档换的是**候选来源**（第三根轴）⇒ 必须进账本：不开就是
        #:    单线爬坡，开了才可能有旁支回来 ⇒ 两者跑出来的轨迹不可比。
        "archive_resample": _arch_n,
        "archive_cap": int(getattr(args, "archive_cap", "8") or 8),
        "archive_path": (_archive.path if _archive is not None else ""),
        #: ⛔ 证据粒度换的是**模型看到的世界**（不是判据）⇒ 必须进账本：
        #:    同一份代码下 `exact` 与 `coarse` 提出来的胶囊完全不同，
        #:    不记 ⇒ "这次为什么没被 overfit 拦"事后无从归因。
        "evidence": str(getattr(args, "evidence", "exact") or "exact"),
        #: ⛔ 它**放开了一道闸的适用范围**（不是关掉闸）⇒ 必须进账本，
        #:    否则"这次为什么能提交"会被读成"模型变强了"。
        "allow_scoped_commit": bool(
            getattr(args, "allow_scoped_commit", False)),
        #: ⛔ 它改的是**prompt 内容**（给模型的规则），不是判据 ⇒ 同样是
        #:    实验变量：开着它跑出来的胶囊不可与历史相比。
        "ho_implication": bool(getattr(args, "ho_implication", False)),
        "parallel": getattr(args, "parallel", "0"),
        "embedder": args.embedder,
        "budget": {k: (getattr(args, k, "") or "")
                   for k in ("max_rounds", "max_seconds", "max_propose",
                             "max_eval_runs")},
        "checkpoint": cp_path or None,
        #: ⛔ **世界规模必须进账本**（r47）：进化走到第几代，天花板在机器
        #:    还是在世界的判定，全靠这个读数。不记 ⇒ "这次是几个环境的
        #:    实验"要靠猜 ⇒ 账本失去可比性。
        "n_envs": len(names),
        "envs": list(names),
        "world": "auto" if _want == "auto" else ("custom" if _want else "default4"),
        #: ⛔ 记 `0` 而不是 `None`：None 分不清「不是续跑」和「忘了记」。
        #:    （与 checkpoint 的 `n_below_min_content` 同源纪律：读数要么
        #:    是确认过的值，要么带原因，不许用空值冒充"已确认没有"。）
        "resumed_from": resumed_from if resumed_from is not None else 0,
        #: ⛔ **走的是哪条连续性路径**必须进账本（r60 F）：`resume` 是显式
        #:    老语义（--rounds 为目标总轮数）、`auto` 是默认接续（追加）、
        #:    `fresh` 是从头。三者跑出来的 `round_no` 序列完全不同，不记
        #:    就分不清「这轮从 seed 起」是配置如此还是接续失败。
        "continuity": ("resume" if getattr(args, "resume", False)
                       else ("auto" if did_resume else "fresh")),
    }
    led.append("evolution.run_config", run_config, actor="cli")

    if resumed_from is None:
        mem.write("L0", h0.meta_rules, channel="curated",
                  source="seed-harness")

    t0 = time.time()
    final, reports = loop.run(h_start, rounds=rounds, start_round=start_round)

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
        #: ⛔ 这条本来就是 **learner 通道**（弱点是"学到的事实"），以前却是
        #:    裸 `mem.write` ⇒ 去重闸 / 最短内容闸 / 配额闸 / 可达性取证
        #:    全没生效，账本里也认不出这是"一次学习"。改走 KnowledgeLearner：
        #:    通道语义不变，四条闸与 `knowledge.ingested` 读数同时上线。
        #:    ⛔ `ingest` 的返回值必须**用起来**（不许只调用不看报告）：
        #:    写入侧放行 ≠ 真的进去了 —— 被去重/过短的条数要能读出来。
        if r.weakness:
            _rep = _learner.ingest([KnowledgeItem(
                content=(f"{r.weakness.kind} / {r.weakness.topic}："
                         f"{'; '.join(r.weakness.evidence[:2])}"),
                source=f"round{r.round_no}", tier="L3",
                tags=("experience", "weakness"), trust=0.5)])
            _ingest_total["input"] += _rep.n_input
            _ingest_total["written"] += _rep.n_written
            _ingest_total["deduped"] += _rep.n_deduped
            _ingest_total["rejected"] += _rep.n_rejected
            _ingest_total["below_min"] += _rep.n_below_min_content
            _ingest_total["truncated"] += _rep.n_truncated
            if _rep.retrievable is False:
                _ingest_total["unretrievable"] += 1

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
    #: ⛔⛔ **意图**与**实际**必须分两条事件记（r62）。`run_config` 写在跑之前，
    #:    它只能记"打算用什么"：端点连不上时它照样写 `kind="llm"` 且
    #:    `fallback=""`（构造 `OllamaProvider` 不校验可达性，第一次真正
    #:    发请求才失败）⇒ 只靠 run_config，「接了模型、跑了、失败了」与
    #:    「接了模型、跑了、成功了但没产出」**逐字相同**。
    #:    ⇒ 这里补一条**运行后**的读数，数据直接取自 `diagnostics()`（同源，
    #:    不另算一遍）：`error` 非空就是"这条链路走到了但失败了"。
    try:
        led.append("evolution.llm_result", {
            "kind": _llm_desc.get("kind", ""),
            "provider": _llm_desc.get("provider", ""),
            "model": _llm_desc.get("model", ""),
            "fallback": _llm_desc.get("fallback", ""),
            "propose_calls": diag.get("propose_calls", 0),
            "error": diag.get("error", ""),
            "retries_used": diag.get("retries_used", 0),
            "raw_len": diag.get("raw_len", 0),
            "rejects": list(diag.get("rejects") or [])[:5],
            "memory_skipped_n": diag.get("memory_skipped_n", 0),
        }, actor="cli")
    except Exception as _e:  # noqa: BLE001
        #: ⛔ 记账失败不许让整轮白跑，但也不许静默。
        _log.warning(f"⚠️ evolution.llm_result 写入失败：{_e}")
    if diag.get("error"):
        _log.info(f"ℹ️ proposer 诊断：{diag}")
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
        #: ⛔ 参数**同时**出现在输出里：账本用于事后审计，输出用于当场
        #:    贴进实验记录。两者同源（就是上面那一份），不各写一遍。
        "run_config": run_config,
        "improved": any(r.improved for r in reports),
        "cost_s": round(time.time() - t0, 2),
        #: ⛔ 续跑读数必须可见：不打印，"开了 --checkpoint"与"进度真的
        #:    存下来了"在外部无从区分（假仪表）。`resumable=False`
        #:    说明已跑完，别再诱导一次无意义的续跑。
        "checkpoint": ({"path": loop.last_checkpoint or cp_path,
                        "round_no": reports[-1].round_no if reports else 0,
                        "rounds_total": start_round + rounds,
                        "resumable": bool(reports)
                        and reports[-1].round_no < start_round + rounds,
                        "resumed_from": resumed_from}
                       if (cp_path or loop.last_checkpoint) else None),
        "embedder": embedder_report,
        "ledger": led.stats(),
        "memory": mem.integrity(),
        #: ⛔ 记忆是否真的进了因果链：跳过数 > 0 才证明它被走到过
        "memory_causal": proposer.diagnostics() if hasattr(
            proposer, "diagnostics") else {},
        #: ⛔⛔ **学习闭环读数**（2026-10-17 新增）。以前经验写进记忆之后
        #:    是否被下一轮用上，只有内存里的列表记得 ⇒ 账本无从查证。这里把
        #:    **写入侧**（摄入/去重/过短/配额/写后读不到）汇总到输出，配合
        #:    `experience_loop` 的**起效侧**读数，闭环两端才第一次同时可见。
        "learning_ingest": dict(_ingest_total),
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


def _caps_gateway(path: str, ledger=None):
    """装载能力清单并返回网关。

    ⛔ 直接调 `interop.mount_capabilities` —— 与 HTTP 服务（启动时装载 +
    `/api/capabilities/reload` 热装载）**同一个函数**。两个入口各写一份
    装载接线，就会出现"CLI 能调、HTTP 调不到"的幽灵差异。

    ⛔ `ledger` 必须传：不传 ⇒ 网关没有账本 ⇒ 调用能力时 `cap.invoke`
       一条也写不出（实测账本 0 次，见 `Gateway.invoke` 的说明）。
    """
    from .interop import mount_capabilities
    return mount_capabilities(path, ledger=ledger)


def cmd_caps(args) -> int:
    from .interop import load_manifest
    from .ledger import Ledger
    #: ⛔ 记账：CLI 调能力必须进账本 —— 否则"执行链不进账"这条缺口永远补不上。
    led = Ledger(_paths(Path(args.root))["ledger"])
    gw, rep = _caps_gateway(getattr(args, "caps", "") or "", ledger=led)
    for s in rep.skipped:
        _log.warning(f"[caps] ⚠️ 跳过 {s.get('id')}：{s.get('reason')}")
    reg = gw.registry
    cmd = getattr(args, "caps_cmd", "list")

    if cmd == "candidates":
        #: ⛔ 这条链**不下载、不安装**：它生成候选、过闸、留痕，并把
        #:    "该怎么装"作为**给人的提示**打印出来。真正的安装由人执行
        #:    （理由见 `aether/caps_candidate.py`）。
        #: ⛔ 状态**落盘**（`data/candidates.json`）：CLI 每次调用都是新进程，
        #:    而"批准"是跨调用动作。不落盘时 `validate` 后跑 `approve`
        #:    会重新生成候选 ⇒ 批准状态丢失 ⇒ `hint` 报 validated。
        from . import caps_candidate as CC
        from . import discover as D
        #: ⛔ `ghprobe` 只 import 标准库 ⇒ 延迟导入这里不会有循环。
        from . import ghprobe as GH
        #: ⛔ 位置**按键取**，不许手拼 `root / "data" / ...`：
        #:    布局的唯一事实源是 `paths()`（静态闸会抓手拼）。
        cpath = _paths(Path(args.root))["candidates"]
        saved = CC.load(cpath)
        sub = getattr(args, "cand_cmd", "propose")

        def _mk(tool_name: str):
            """取一条候选 + 它的探针结果。

            ⛔⛔ 返回的两个 `None` **语义不同**，不许压成一义（r60 E 实测）：

                `(None, None)` —— 工具不在 `KNOWN_TOOLS` 里 ⇒ **真不认识**
                `(None, pr)`   —— 认识，但 `from_probe` 判「不值得提」
                                  （已装且探到了 capability）⇒ 候选从流水线
                                  上消失了，**只能回档案里取**

            ⛔ 原来三个子命令都写 `if c is None: 报「不认识工具」`，把两义
               压成一义。于是工具一装好，`validate` / `approve` / `hint`
               全部退出 2，并打印：

                  不认识工具 'radare2'；已知：['ghidra', 'radare2', …]

               —— **同一句话里自证它认识**。读者会去查工具名写错没有，
               而真正的问题是"装好之后没有退路"（r60 B 那条在 `verify`
               上补了，另外三个出口没补）。
            """
            tool = next((t for t in D.KNOWN_TOOLS if t.name == tool_name), None)
            if tool is None:
                return None, None
            pr = D.probe(tool)
            c = CC.from_probe(pr, install_argv=list(tool.install_argv))
            if c is None:
                #: ⛔ 与 `verify` 子命令同一条退路：没有待办候选是**对的**，
                #:    但档案里那条已批准/已结案的候选必须还能被读到。
                return saved.get(tool_name), pr
            #: 与已存档状态合并：新探针更新事实，但不把已批准的打回 pending
            return CC.merge(c, saved.get(tool_name)), pr

        def _gh_line(c) -> str:
            """外部上游事实的显示。⛔ **没查过就返回空串**（不显示 == 没查过）。

            ⛔ 三态分开显示，合成一句最糟：
              · ok          上游现状（星数 / 许可 / 最近提交）
              · not_found   清单里的 `gh_repo` 可能写错了 ⇒ 该改的是清单
              · unavailable 现在取不到（限流/断网）⇒ 与这个工具本身无关
            ⛔ 把"取不到"显示成空白尤其危险：读者会读成"这工具没有上游"，
               而真相只是我们这一刻问不到（第 51 条：没测过 ≠ 测了没结果）。
            """
            e = (c.evidence or {}).get("external")
            if not e:
                return ""
            st = e.get("status") or ""
            if st == GH.OK:
                age = GH.stale_days(e.get("pushed_at") or "")
                when = "未知" if age is None else f"{age} 天前"
                lic = GH.license_label(e.get("license_spdx") or "")
                out = (f"    🌐 上游 {e.get('full_name')} ★{e.get('stars')}"
                       f"｜{lic}｜最近提交 {when}（本机探不到）")
                if e.get("archived"):
                    #: ⛔ 归档是**作者主动封存**，与"只是不活跃"是两件事，
                    #:    所以它单独占一行，不混进上面那句里。
                    out += "\n    ⛔ 上游已归档 —— 装之前先想清楚"
                return out
            if st == GH.NOT_FOUND:
                return "    🌐 上游查不到 ⇒ 清单里的 gh_repo 可能写错了"
            return (f"    🌐 上游现状取不到（{e.get('detail') or '?'}）"
                    f"⇒ 与本机探针无关，不是这个工具的问题")

        if sub == "propose":
            probes = D.discover()
            found = {t.name: pr for t, pr in zip(D.KNOWN_TOOLS, probes)}
            cands = []
            fresh = []
            for t, pr in zip(D.KNOWN_TOOLS, probes):
                c = CC.from_probe(pr, install_argv=list(t.install_argv))
                if not c:
                    continue
                c = CC.merge(c, saved.get(t.name), probe=pr)
                #: ⛔ 外部上游事实**只在显式 `--net` 时才取**。默认不联网：
                #:    外部 API 有配额且可用性不归我们管，而宪法第一条要求
                #:    判据必须离线可回归 ⇒ 这条路径默认不存在。
                c = CC.annotate(c, t.gh_repo,
                                net=bool(getattr(args, "net", False)),
                                api_base=str(getattr(args, "gh_api_base", "")
                                             or ""))
                cands.append(c)
                #: ⛔ 只记**状态变了**的（r60 B）。原来每轮 propose 都把
                #:    同一条候选重记一遍 —— 实测 radare2 一条在账本里
                #:    占了 170 条，害得"事件数"被读成"候选数"。
                if saved.get(t.name) is None or saved[t.name].state != c.state:
                    fresh.append(c)
            #: ⛔ 装好的工具从上面漏掉了（`from_probe` 对"已探到且已包装"
            #:    返回 None，那是"不值得提"的正确判定）⇒ 由 `seal` 结案，
            #:    否则 `installed` 永远不可达（r60 B 实测）。
            sealed = CC.seal(saved, found)
            #: ⛔ 合并保存：原写法是整表替换，会把刚批准的（甚至刚结案的）
            #:    冲回 pending —— 与 `cleanup` 链当初同一个 bug。
            merged = dict(saved)
            for c in cands:
                merged[c.tool] = c
            for c in sealed:
                merged[c.tool] = c
            CC.save(cpath, merged)
            for c in fresh:
                CC.record(led, c)
            for c in sealed:
                CC.record(led, c)
            if getattr(args, "json", False):
                print(json.dumps([c.to_dict() for c in cands + sealed],
                                 ensure_ascii=False, indent=2))
                return 0
            if not cands and not sealed:
                #: ⛔ 「已有的也都包装了」是**过度声明**（r60 E）：`from_probe`
                #:    认的只是"探针能给出 capability"，而那份 capability 的
                #:    `impl.argv` 仅仅是 `[path]`（**版本探针命令**），
                #:    `to_manifest` 里它一直标着 `discovered_unwrapped`。
                #:    ⇒ 不写全称断言，**逐个点名**（第 59 条：文案里的全称
                #:    量词会失真）。探针 `error` 的也要列出来 —— 按设计它们
                #:    不生成候选（装一遍解决不了"装了但坏了"），不点名就等于
                #:    让它们从所有读数里消失。
                ok = [t.name for t, x in zip(D.KNOWN_TOOLS, probes)
                      if x.status == "found"]
                err = [(t.name, x.detail) for t, x in zip(D.KNOWN_TOOLS, probes)
                       if x.status == "error"]
                print(f"✅ 没有待办候选：{len(ok)} 个已知工具都探到了"
                      f"（{'、'.join(ok) or '无'}）")
                if err:
                    print(f"   ⛔ 其中 {len(err)} 个探针失败："
                          + "；".join(f"{n}（{d}）" for n, d in err))
                    print("      那不是「缺」而是「去修」—— 所以这里不提安装候选"
                          "（装一遍解决不了它）。")
                print("   注意：探到的 argv 是**版本探针命令**，不是任务参数；"
                      "`caps discover` 里仍标 discovered_unwrapped。")
                return 0
            for c in cands:
                mark = {"approved": "🟡 已批准（未安装）",
                        "installed": "✅ 已装（复验过）"}.get(c.state, "· 待过闸")
                print(f"{mark} {c.tool} [{c.kind}] {c.reason}")
                if c.verify_hint:
                    print(f"    ⇒ {c.verify_hint}")
                #: ⛔ 没查过就什么都不打印（不显示 == 没查过，不假装查过）。
                ext = _gh_line(c)
                if ext:
                    print(ext)
            for c in sealed:
                print(f"✅ 已装（复验过） {c.tool} —— 结案"
                      f"（原本这条会直接消失）")
            print(f"\n候选 {len(cands)} 条"
                  + (f"｜结案 {len(sealed)} 条" if sealed else "")
                  + f" → {cpath}")
            if cands:
                print("⛔ 下一步：`aether caps candidates validate --tool X`，"
                      "然后 `approve --tool X --by 你 --yes`")
            print("   装完：`aether caps candidates verify --tool X` 结案"
                  "（propose 也会自动结案）")
            return 0

        if sub == "verify":
            c, pr = _mk(args.tool)
            if pr is None:
                _log.warning(f"[caps] 不认识工具 {args.tool!r}；"
                             f"已知：{[t.name for t in D.KNOWN_TOOLS]}")
                return 2
            if c is None:
                #: ⛔ 走到这里说明 `_mk` 已经把档案也查过了（r60 E：
                #:    那里返回 `saved.get(tool_name)`）⇒ **档案里真的没有它**。
                #:    ⛔ 原来这里还有一句 `c = saved.get(args.tool)`，在
                #:    `_mk` 补上同一条退路之后它就是**死代码**了
                #:    （`c is None` ⇒ 档案里必然也没有）。同一条退路写两遍
                #:    的害处不是重复，是"下次只改一处"（第 95 条同族）。
                if pr.status == "error":
                    _log.warning(f"[caps] {args.tool} 找到了但探针失败"
                                 f"（{pr.detail}）⇒ 那是「去修」，不是「去装」")
                    return 1
                #: ⛔ 别说"先跑 propose"：已装且已探到的工具 `propose` 永远
                #:    不会再提它 ⇒ 那是个死循环式建议（第 35b 条）。
                print(f"✅ {args.tool} 已经装好（{pr.path or pr.status}），"
                      f"档案里从来没有它的候选 ⇒ 没有可复验的东西。")
                return 0
            c = CC.verify(c, pr)
            if c.state == CC.INSTALLED:
                #: 只有结案才落盘。
                saved[c.tool] = c
                CC.save(cpath, saved)
                CC.record(led, c)
                print(json.dumps({"tool": c.tool, "state": c.state,
                                  "approved_by": c.approved_by,
                                  "probe": pr.to_dict()},
                                 ensure_ascii=False, indent=2))
                print(f"✅ 已装（复验过）：{c.tool} @ {pr.path}")
                if not c.approved_by:
                    print("   （⚠️ 这条**未经人批准**：这里记下的是探针事实，"
                          "不等于本系统采纳过它）")
                return 0
            #: ⛔ 没通过就**只留痕、不落盘**：把 approved 打回 rejected
            #:    等于撤销人的批准，而"还没装"和"装了没用"是两件事
            #:    （r60 B）—— 后者才该被看见，前者只是时机不对。
            CC.record(led, c)
            #: ⛔ 别再套一遍"复验未通过"前缀：`verify()` 写的那条原因
            #:    本身就是这句话开头，叠起来变成"复验未通过：复验未通过：…"。
            _log.warning("[caps] "
                         + (c.gate_reasons[-1] if c.gate_reasons else "?"))
            print(f"   探针读数：{pr.status}（{pr.detail}）")
            _o = saved.get(c.tool)
            print(f"   档案里 {c.tool} 仍是 "
                  f"**{_o.state if _o else '（无记录）'}**，"
                  f"你的批准没有被撤销。")
            return 1

        if sub in ("validate", "approve", "hint", "auto"):
            c, pr = _mk(args.tool)
            #: ⛔ 「不认识」只认 `pr is None`（工具名真的不在 `KNOWN_TOOLS`）：
            #:    用 `c is None` 判会让"工具装好了"冒充"工具名写错了"（r60 E）。
            if pr is None:
                _log.warning(f"[caps] 不认识工具 {args.tool!r}；"
                             f"已知：{[t.name for t in D.KNOWN_TOOLS]}")
                return 2
            if c is None:
                #: 工具认识、也探过了，但档案里从来没有它的候选 ⇒ 无事可做。
                #: ⛔ 先分出 `error`：那是「装了但坏了」（去修），
                #:    不是「已经装好」（第 49 条：一个裁决分支的观测
                #:    不许比另一个少 —— 三态都得有话说）。
                if pr.status == "error":
                    _log.warning(f"[caps] {args.tool} 找到了但探针失败"
                                 f"（{pr.detail}）⇒ 那是「去修」，不是「去装」")
                    return 1
                #: ⛔ 别说"先跑 propose"：已装且已探到的工具 **propose 永远
                #:    不会再提它**（`from_probe` 返回 None），那是个死循环式
                #:    建议（第 35b 条：闸 reason 写的「出路」系统必须真有）。
                print(f"✅ {args.tool} 已经装好（{pr.path or pr.status}），"
                      f"档案里没有它的待办候选 ⇒ 没有可 `{sub}` 的东西。")
                return 0
            if sub == "validate":
                c = CC.validate(c, probe=pr)
                saved[c.tool] = c
                CC.save(cpath, saved)
                CC.record(led, c)
                print(json.dumps(c.to_dict(), ensure_ascii=False, indent=2))
                #: ⛔ 已裁决的候选闸现在红了 ⇒ **必须说人话**（r60 D）。
                #:    只打印 JSON 的话，「批准还在」与「闸现在是红的」会挤在
                #:    同一个字典里，读者按先扫到的那个键理解 —— 而这两种
                #:    理解的处置完全相反（不用重批 vs 去看闸为什么红）。
                #: ⛔ 而且 `installed` 与 `approved` 要说**不同的话**（r60 E）：
                #:    installed 的闸红是**提案本身过期**（它说 missing，工具
                #:    已经在）—— 那是结案后的正常形态，不需要处理；
                #:    approved 的闸红才是"要去看为什么红"。
                if c.state == CC.INSTALLED and not c.gate_passed:
                    print(f"✅ {c.tool} 已经结案（装好且复验过）。"
                          f"闸红是因为**提案本身过期**了：")
                    for r in c.gate_reasons:
                        print(f"     ⇒ {r}")
                    print("   不需要处理 —— 这条候选到此为止。")
                elif CC.STATE_RANK.is_settled(c.state) and not c.gate_passed:
                    print(f"⚠️ 这条候选已是 **{c.state}**"
                          f"（人的批准**没有被撤销**），"
                          f"但闸现在的读数是红的：")
                    for r in c.gate_reasons:
                        print(f"     ⇒ {r}")
                return 0 if c.gate_passed else 1
            if sub == "approve":
                #: ⛔ 先过闸再批准：批一条已知不过闸的提案等于把闸当摆设。
                c = CC.validate(c, probe=pr)
                c = CC.approve(c, by=getattr(args, "by", "") or "",
                               yes=bool(getattr(args, "yes", False)))
                saved[c.tool] = c
                CC.save(cpath, saved)
                CC.record(led, c)
                #: ⛔ 已结案的再批一次不是失败，是**无事可做**（r60 E）：
                #:    `approve()` 已经挡住了回退（终态不许退回 approved），
                #:    这里只是把"没推进"说成它真正的原因。
                if c.state == CC.INSTALLED:
                    print(f"✅ {c.tool} 已经装好并结案"
                          + (f"（批准人：{c.approved_by}）"
                             if c.approved_by else "（⚠️ 未经人批准）")
                          + "，不需要再批准 —— 状态没有变动。")
                    return 0
                if c.state != CC.APPROVED:
                    _log.warning("[caps] 未推进："
                                 + (c.gate_reasons[-1] if c.gate_reasons else "?"))
                    return 1
                print(f"✅ 已批准（**仍未安装**）：{c.tool} by {c.approved_by}")
                argv = CC.install_hint(c)
                if argv:
                    print("   安装命令（请**人工**执行）：\n     " + " ".join(argv))
                else:
                    print("   本系统没给出 install_argv：**不猜包管理器**，"
                          "请按该工具的官方文档人工装。")
                #: ⛔ 出路必须真能结案（r60 B）：`caps discover` 只重扫清单，
                #:    **不结案**（不会把 approved 变成 installed，也不记账本）。
                print(f"   装完跑 `aether caps candidates verify "
                      f"--tool {c.tool}` 结案。")
                return 0
            if sub == "auto":
                #: ⛔⛔ **机器自审安装**（r60 G）：人审被拿掉，但替它顶上去的
                #:    是五道判据（清单逐字比对 / 执行白名单 / 可回滚 /
                #:    四道闸当场重跑 / 装后复验），见 `aether/install_auto.py`。
                #: ⛔ 这里**不传** `probe_fn` / `run`：那两个参数只给契约用，
                #:    从 CLI 传进来等于给外部一个"伪造装成功"的入口。
                from . import install_auto as IA
                #: ⛔ `_mk` 里的 Tool 是局部变量，这里按名字重取一次 ——
                #:    不做闭包穿透，也不把 `_mk` 的返回值改成三元组
                #:    （改了就要动四个已稳定的出口）。
                tool = next(t for t in D.KNOWN_TOOLS if t.name == args.tool)
                v = IA.machine_install(c, tool, dry_run=bool(args.dry_run),
                                       timeout_s=float(args.timeout))
                IA.record_verdict(led, v)
                #: 落盘口径（三条，缺一条就会出现「演练与真装同形」）：
                #:   · **演练**：一个字节都不落 —— 演练的语义是"环境零改动"，
                #:     而档案是环境的一部分（E1b 实测抓到过：演练把状态推到
                #:     validated 并写了 approved_by，与真装长得一样）；
                #:   · **本来就在**（noop）：也不落 —— 什么都没做；
                #:   · **装成**：installed + approved_by=auto（机器批准留痕）。
                #:   · **被拒**：落 rejected 状态**只用于"没裁决"以外**的
                #:     真结论；批准本身不退（见 `install_auto` 里那段说明）。
                if not v.dry_run and v.state == IA.OK:
                    if not v.noop:
                        c.state = v.cand_state or c.state
                        c.approved_by = IA.AUTO_BY
                        c.approved_at = time.time()
                        saved[c.tool] = c
                        CC.save(cpath, saved)
                elif not v.dry_run and v.state == IA.REJECTED:
                    c.state = v.cand_state or c.state
                    saved[c.tool] = c
                    CC.save(cpath, saved)
                if not v.dry_run:
                    CC.record(led, c)
                icon = {IA.OK: "✅", IA.REJECTED: "⛔"}.get(v.state, "⚠️")
                #: ⛔ 图标只在 `icon` 里出现一次：两个 dict 都带 ✅ 会打出
                #:    「✅ ✅ 机器自审通过」—— 文案瑕疵，但读者会以为是两层结论。
                head = {IA.OK: ("无事可做（本来就在）" if v.noop
                                else "机器自审通过"),
                        IA.REJECTED: "机器自审拒绝",
                        IA.UNVERIFIED: "无法裁决（没有执行任何命令）"}[v.state]
                print(f"{icon} {head} —— {args.tool}"
                      + ("（演练：命令未执行）" if v.dry_run else ""))
                for r in v.reasons:
                    print(f"   ⇒ {r}")
                e = v.evidence.get("exec") or {}
                if e:
                    print(f"   执行：{' '.join(e.get('argv') or [])}")
                    print(f"        rc={e.get('rc')} 耗时 {e.get('seconds')}s"
                          + ("（超时）" if e.get("timed_out") else ""))
                    if e.get("stderr_tail"):
                        print("        stderr 尾部："
                              + str(e["stderr_tail"]).strip().splitlines()[-1][:160])
                if v.state == IA.OK and not v.noop and not v.dry_run:
                    print(f"   状态：{c.tool} ⇒ {c.state}"
                          f"（approved_by={c.approved_by}）")
                return 0 if v.state == IA.OK else 1
            argv = CC.install_hint(c)
            if not argv:
                #: ⛔ 已结案的不给命令，也**不许**再念一遍"先 validate +
                #:    approve"（那条路对它已经走完了）。这里用探针的**当下
                #:    读数**说事：档案说已装、而现在探不到，是个必须被看见
                #:    的矛盾（第 45 条：两个证人不一致先找差异源）。
                if c.state == CC.INSTALLED:
                    if pr.status != "found":
                        print(f"⚠️ 档案说 {c.tool} 已装（复验过），"
                              f"但现在探到 {pr.status}（{pr.detail}）。")
                        print(f"   跑 `aether caps candidates verify "
                              f"--tool {c.tool}` 复验 —— 卸了就该让它红。")
                        return 1
                    print(f"✅ {c.tool} 已经装好（{pr.path}），不需要再装。")
                    return 0
                print(f"（没有可给的安装命令：候选状态={c.state}。"
                      f"先 validate + approve --yes；或本系统没给出 "
                      f"install_argv —— 我们不猜包管理器。）")
                return 1
            print(" ".join(argv))
            return 0

    if cmd == "cleanup":
        #: ⛔ 本命令**只在显式 approve --yes 之后才给出补丁**，而且
        #:    **从不改源码** —— 删错了是不可逆的，账本只能留痕不能还原。
        from . import cleanup as CL
        #: ⛔ 延迟 import：只有走到 cleanup 这条命令才需要它（它带 subprocess
        #:    与 shutil，是"对拍"这条路上才用的重家伙）。
        from . import srcmut as SM
        src_root = Path(args.root)
        cpath = _paths(src_root)["cleanup"]
        saved = CL.load(cpath)
        sub = getattr(args, "cleanup_cmd", "propose")

        def _save(props):
            CL.save(cpath, props)

        if sub == "propose":
            #: ⛔ 传 `existing` ⇒ 同 id 的**不再重提**。不传的后果是每轮
            #:    把同一批再记一遍账（实测 30 个提案被提了 509 次），
            #:    而且新提案的 pending 会把人刚批准的 applied **冲掉**。
            props = CL.build(limit=int(getattr(args, "limit", 20) or 20),
                             existing=saved)
            for p in props:
                CL.record(led, p)
            #: ⛔ 合并而非整表替换：旧提案带着它的状态（proposed/applied/
            #:    executed）必须留下，否则"批准"这一动作会被下一次 propose
            #:    静默抹掉 —— 看上去是重新扫了一遍，实际是撤销了人的决定。
            merged = dict(saved)
            for p in props:
                merged[p.prop_id] = p
            _save(merged)
            if getattr(args, "json", False):
                print(json.dumps([p.to_dict() for p in props],
                                 ensure_ascii=False, indent=2))
                return 0
            if not props:
                print(f"✅ 没有**新**清理提案（档案里已有 {len(saved)} 条，"
                      "不再重复记账）")
                print(f"   想看它们：直接读 {cpath}")
                return 0
            print(f"新清理提案 {len(props)} 条（已进账本，state=pending）；"
                  f"档案共 {len(merged)} 条 → {cpath}")
            for p in props[:12]:
                print(f"  [{p.kind}] {p.target} @ {p.where}")
            if len(props) > 12:
                print(f"  … 还有 {len(props) - 12} 条")
            print("⛔ 下一步：`aether caps cleanup validate`，"
                  "然后 `approve --id … --by 你 --yes`")
            return 0

        if sub == "validate":
            pid = getattr(args, "pid", "") or ""
            targets = ([saved[pid]] if pid and pid in saved
                       else list(saved.values()))
            if not targets:
                print("（没有可校验的提案：先跑 `caps cleanup propose`）")
                return 2
            #: ⛔ 对拍很贵（每条要复制一份仓库 + 跑一次冻结契约）
            #:    ⇒ 默认只对前 N 条**带可执行编辑**的提案跑，其余记为
            #:      `unverified`。⛔ **unverified 就是红**，不冒充"通过"。
            probe_limit = int(getattr(args, "probe_limit", 3) or 3)
            bad = 0
            probed = 0
            for p in targets:
                verdict: Any = None
                if p.edits and probed < probe_limit:
                    probed += 1
                    eds = [SM.Edit.from_dict(x) for x in p.edits]
                    sel, why = SM.select_contracts(
                        src_root, [e.file for e in eds],
                        max_contracts=int(
                            getattr(args, "max_contracts", 12) or 12))
                    if not sel:
                        p.evidence["nonregression"] = {
                            "unverified": True, "ok": False,
                            "reason": why}
                    else:
                        pr = SM.probe(src_root, eds,
                                      runner=SM.contract_runner(
                                          sel,
                                          python_exe=getattr(args, "python",
                                                             None)))
                        p.evidence["nonregression"] = pr
                        verdict = bool(pr.get("ok")) and not pr.get("unverified")
                elif p.edits:
                    p.evidence["nonregression"] = {
                        "unverified": True, "ok": False,
                        "reason": (f"超出本次对拍预算（--probe-limit "
                                   f"{probe_limit}）⇒ 未验证，不当通过")}
                else:
                    #: ⛔ 前两类（启发式）**拿不到**"删后"的源码
                    #:    ⇒ 无从对拍 ⇒ 它们本来就该红。这不是缺陷，
                    #:       是判据的边界：启发式只撑得住提案，撑不住判定。
                    p.evidence["nonregression"] = {
                        "unverified": True, "ok": False,
                        "reason": ("启发式判定（名字没人用 / 只写不读）⇒ "
                                   "没有可应用的编辑 ⇒ 无法对拍；"
                                   "这一类只能提案，不能过闸")}
                p = CL.validate(p, non_regression_ok=verdict)
                saved[p.prop_id] = p
                CL.record(led, p)
                mark = "✅" if p.gate_passed else "❌"
                print(f"{mark} [{p.kind}] {p.target} @ {p.where} "
                      f"state={p.state} 减负={p.evidence.get('load_delta')}")
                nr = p.evidence.get("nonregression") or {}
                if nr.get("unverified"):
                    print(f"     ⚠️ 未验证：{str(nr.get('reason'))[:90]}")
                elif nr.get("ok"):
                    print("     ✅ 对拍通过：删前删后冻结契约一致")
                for r in p.gate_reasons:
                    print(f"     ⇒ {r}")
                #: ⛔ 同 `caps candidates validate`：已裁决的提案闸红了，
                #:    要明说「批准还在」—— 否则人会以为批准被撤销、
                #:    去重跑一遍 approve，而重跑 approve 也不会修复任何东西。
                if CL.STATE_RANK.is_settled(p.state) and not p.gate_passed:
                    print(f"     ⚠️ 已是 **{p.state}**"
                          f"（人的批准**没有被撤销**），"
                          f"但闸现在的读数是红的")
                bad += 0 if p.gate_passed else 1
            _save(saved)
            return 1 if bad else 0

        if sub == "approve":
            pid = getattr(args, "pid", "")
            p = saved.get(pid)
            if p is None:
                _log.warning(f"[cleanup] 没有提案 {pid!r}（先跑 propose/validate）")
                return 2
            #: ⛔ 复用 `validate` 阶段存下来的裁决，但**必须先验指纹**：
            #:    源码一动 ⇒ 当初那次对拍证明的是**另一段代码** ⇒ 裁决作废。
            #:    直接复用缓存 ⇒ "曾经证明过安全" 会冒充 "现在仍然安全"。
            verdict: Any = None
            cached = p.evidence.get("nonregression") or {}
            if p.edits:
                eds = [SM.Edit.from_dict(x) for x in p.edits]
                stale = [e for e in eds if not SM.verify(src_root, e)[0]]
                if stale:
                    p.evidence["nonregression_stale"] = (
                        f"源码已变（{len(stale)} 条编辑指纹不匹配）⇒ "
                        f"裁决作废，请重跑 `caps cleanup validate`")
                elif cached.get("ok") and not cached.get("unverified"):
                    verdict = True
            p = CL.validate(p, non_regression_ok=verdict)
            p = CL.apply_patch(p, approved_by=getattr(args, "by", "") or "",
                               yes=bool(getattr(args, "yes", False)))
            saved[pid] = p
            _save(saved)
            CL.record(led, p)
            if p.state != CL.APPLIED:
                _log.warning("[cleanup] 未推进："
                             + (p.gate_reasons[-1] if p.gate_reasons else "?"))
                return 1
            print(f"✅ 已批准（**源码仍未改动**）：{p.target} @ {p.where} "
                  f"by {p.evidence.get('approved_by')}")
            print("   补丁（请**人工**执行）：")
            for ln in p.patch.splitlines():
                print("     " + ln)
            return 0

        if sub == "auto":
            #: ⛔⛔ 机器自审（r60 F）：把「人点头」换成「冻结契约对拍」。
            #:    这条路径**新增了写生产根的权限**，所以必须有等强度的约束
            #:    顶上：按 import 选契约 → 副本对拍 → 快照 → 落盘 → 复验 →
            #:    失败原子回滚。少任何一环，它就从"自审"退化成"自动乱改"。
            from . import autonomy as AU
            pid = getattr(args, "pid", "") or ""
            p = saved.get(pid)
            if p is None:
                _log.warning(f"[cleanup] 没有提案 {pid!r}（先跑 propose/validate）")
                return 2
            if CL.STATE_RANK.of(p.state) >= CL.STATE_RANK.of(CL.EXECUTED):
                print(f"✅ {pid} 已是 {p.state}：无需再执行")
                return 0
            dry = bool(getattr(args, "dry_run", False))
            v = AU.auto_apply(
                p, src_root,
                max_contracts=int(getattr(args, "max_contracts", 12) or 12),
                timeout_s=float(getattr(args, "timeout", 900.0) or 900.0),
                verify_after=not dry,
                apply_to_disk=not dry)
            p.evidence["autonomy"] = v.to_dict()
            #: ⛔ 记 `auto` 而**不是**任何人名：审计时要能一眼分开
            #:    「人批的」与「机器对拍过的」，否则两种责任同形。
            p.evidence["approved_by"] = AU.AUTO_BY
            if v.applied and not dry:
                p.state = CL.STATE_RANK.move(p.state, CL.EXECUTED)
            saved[pid] = p
            _save(saved)
            CL.record(led, p)
            if v.state == AU.OK:
                print(f"{'🧪 演练（源码未改动）' if dry else '✅ 机器自审通过'}"
                      f"：{p.target} @ {p.where}")
                for r in v.reasons:
                    print(f"   {r}")
                if not dry:
                    print(f"   删了 {v.evidence.get('applied_lines')} 行"
                          f"｜快照 {v.snapshot_dir}")
                return 0
            if v.state == AU.UNVERIFIED:
                print("⛔ 没有裁决（**不等于**『安全』）：")
            else:
                print("⛔ 对拍发现差异 ⇒ 拒绝落盘：")
            for r in v.reasons:
                print(f"   ⇒ {r}")
            return 1

        if sub == "done":
            #: ⛔ 人执行完补丁之后**回来结账** —— 这是这条链此前缺的最后一环。
            #:    没有它，`APPLIED`（已批准）就是终点，于是账本会说"删了"
            #:    而源码一字未动，提案也永远结不了案。
            pid = getattr(args, "pid", "") or ""
            p = saved.get(pid)
            if p is None:
                print(f"⛔ 没有提案 {pid!r}（先跑 propose → validate → approve）")
                return 2
            p = CL.verify_executed(p, src_root=src_root,
                                   at=int(time.time()))
            saved[pid] = p
            _save(saved)
            CL.record(led, p)
            ex = (p.evidence or {}).get("execution") or {}
            if p.state == CL.EXECUTED:
                print(f"✅ 已结案：{p.target} @ {p.where}"
                      f"（证据级：{ex.get('level')}）")
                print("   `build()` 将不再重提这一条。")
                return 0
            print(f"⛔ 不能结案：{ex.get('reason')}")
            print(f"   （当前状态仍是 {p.state}：补丁还没被真正执行）")
            return 1

        # patch
        p = saved.get(getattr(args, "pid", ""))
        if p is None or p.state != CL.APPLIED:
            print(f"（没有可执行的补丁：提案状态="
                  f"{p.state if p else '不存在'}。先 validate + approve --yes）")
            return 1
        print(p.patch)
        return 0

    if cmd == "discover":
        #: ⛔ 只**发现**，不下载不安装（见 `aether/discover.py` 的说明）。
        #:    探到的每个工具都带"在哪、哪一版、用什么命令探出来的"。
        from . import discover as D
        probes = D.discover()
        man = D.to_manifest(probes)
        if getattr(args, "out", ""):
            outp = Path(args.out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            _log.info(f"[caps] 清单已写：{outp}（{len(man['capabilities'])} 个能力）")
        if getattr(args, "manifest_only", False):
            print(json.dumps(man, ensure_ascii=False, indent=2))
            return 0
        rep_d = D.report(probes)
        if getattr(args, "json", False):
            print(json.dumps({"discover": rep_d, "manifest": man},
                             ensure_ascii=False, indent=2))
        else:
            for pr in probes:
                icon = {"found": "✅", "missing": "⏭️ ", "error": "❌"}[pr.status]
                extra = f"  {pr.version}" if pr.version else f"  {pr.detail}"
                print(f"{icon} {pr.tool:<10} {pr.path or '（PATH 里没有）'}{extra}")
            print(f"\n发现 {rep_d['n']} 个候选："
                  f"{rep_d['counts']['found']} 已装 / "
                  f"{rep_d['counts']['missing']} 未装 / "
                  f"{rep_d['counts']['error']} 探针失败")
            if rep_d["missing"]:
                print(f"⏭️  未装（**这是正常结果**，本命令不会替你装）："
                      f"{'、'.join(rep_d['missing'])}")
            print("ℹ️ 要装工具请**人工**装（会改环境、涉许可证与供应链），"
                  "装完重跑本命令即可。")
        return 0

    if cmd == "list":
        print(json.dumps({"fingerprint": reg.fingerprint, "n": len(reg),
                          "capabilities": reg.manifest()},
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "show":
        spec = reg.get(args.id)
        if spec is None:
            _log.warning(f"[caps] 未注册：{args.id}")
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
            _log.warning(f"[caps] --json 不是合法 JSON：{e}")
            return 2
        if not isinstance(call_args, dict):
            _log.warning("[caps] --json 必须是对象")
            return 2
        res = gw.invoke(args.id, call_args, actor="cli")
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2,
                         default=str))
        #: ⛔ 退出码必须诚实：脚本要靠它判成败，全返回 0 就是把失败藏起来
        return 0 if res.status == "ok" else 1
    _log.warning(f"[caps] 未知子命令 {cmd}")
    return 2


def cmd_backup(args) -> int:
    """备份：把唯一事实源与投影拷走，**带校验和**。"""
    from . import backup as B
    dest = args.to or str(_paths(args.root)["backups"])
    m = B.create(args.root, dest, note=args.note or "")
    out = {"dest": dest, "manifest": m.to_dict(),
           "chain_ok": m.chain_ok, "chain_reason": m.chain_reason}
    #: `--archive`：再打成**单个** .tar.gz（便于传输 / 异地留存）。
    #: ⛔ 顺序：先备份、再打包 —— 打包失败不该把已经拷好的备份说成没有。
    if getattr(args, "archive", False):
        try:
            out["archive"] = str(B.archive(m.path))
        except Exception as e:      # noqa: BLE001
            _log.warning(f"[backup] 打包失败（备份本身已成功）：{e}")
            out["archive"] = ""
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    #: ⛔ 退出码必须诚实：链验出来是断的 ⇒ 这份备份**不可信**，必须报失败
    #:    （否则"备份成功"会被当成"有退路"，而它其实没有）。
    if m.chain_ok is False:
        _log.error(f"❌ 备份副本的账本链是断的：{m.chain_reason}")
        return 1
    return 0


def cmd_restore(args) -> int:
    """恢复：fail-closed，且恢复前先给当前状态留一份。"""
    from . import backup as B
    src = args.src
    #: `--from` 直接指向 `.tar.gz` 也能恢复：先解到一个临时目录，
    #: 之后的流程与"从目录恢复"**完全相同** —— 不另走一条校验路径。
    if str(src).endswith(".tar.gz"):
        tmpd = tempfile.mkdtemp(prefix="v6_unarchive_")
        try:
            src = str(B.unarchive(src, tmpd))
        except B.BackupError as e:
            _log.error(f"❌ 解包失败：{e}")
            return 2
    ok, problems, m = B.verify(src)
    if not ok:
        _log.error("❌ 备份校验不通过 ⇒ 拒绝恢复：")
        for p_ in problems:
            _log.warning(f"   - {p_}")
        return 2
    assert m is not None
    print(f"将用 {src}（{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(m.created_at))}）"
          f" 覆盖 {args.root} 的账本/记忆/投影。")
    if m.chain_ok is False:
        _log.warning(f"⚠️ 这份备份的账本链是断的：{m.chain_reason}")
    #: ⛔ 恢复是不可逆的覆盖写 ⇒ 默认**不**自动执行，必须显式 --yes。
    #:    （"备份时留了当前状态"是第二道保险，不是让人随手执行的理由。）
    if not args.yes:
        print("加 --yes 才真的执行（恢复前会自动把当前状态另存一份）。")
        return 0
    try:
        rep = B.restore(src, args.root)
    except B.BackupError as e:
        _log.error(f"❌ 恢复失败：{e}")
        return 2
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_check(args) -> int:
    """一条命令跑完全部验收，**一个退出码**。

    ⛔ 存在的理由：收口前这件事在 README 里是一段手工 `for` 循环，
       既没有失败聚合（退出码 = 最后一次迭代 ⇒ 中间的红全被吞掉），
       又把 `--e2e` 传给不支持的契约（几百次纯重复）。详见
       `aether/check.py` 的模块 docstring。
    ⛔ check **不修**任何东西、不写账本：它只回答"通过了哪些验收、
       哪些没跑、哪些失败了"。治疗各有命令（同 doctor 的纪律）。
    """
    from . import check as C
    #: ⛔ `--timeout` 的默认值是 0.0（表示"没传"），必须 `or` 落到模块默认 ——
    #:    直接把 0.0 传下去会变成 `subprocess.run(timeout=0)`：每一份契约
    #:    立刻超时，得到"全红但看起来像环境问题"的假故障。
    tmo = float(getattr(args, "timeout", 0.0) or C.DEFAULT_TIMEOUT_S)
    rep = C.run_checks(args.root, only=getattr(args, "only", "") or None,
                       timeout_s=tmo)
    print(json.dumps(rep, ensure_ascii=False, indent=2)
          if getattr(args, "json", False) else C.summarize(rep))
    return int(rep["exit_code"])


def cmd_doctor(args) -> int:
    """一条命令回答"这个系统现在健康吗"。**只读，不修任何东西**。

    ⛔ 每项失败都带 `action`（该跑哪条命令），但 doctor 自己不动手：
    体检恰恰是在数据可疑时跑的，此时自动修改 = 未告知就改数据。
    """
    from . import doctor as D
    rep = D.run(args.root, caps=getattr(args, "caps", "") or "")
    icons = {D.OK: "✅", D.FAIL: "❌", D.SKIPPED: "⏭️ "}
    for c in rep["checks"]:
        line = f"{icons.get(c['status'], '?')} {c['name']}: {c['detail']}"
        if c["status"] == D.SKIPPED and c.get("skipped_reason"):
            line += f"（未查：{c['skipped_reason']}）"
        if c["action"]:
            line += f"\n     ⇒ {c['action']}"
        print(line)
    if rep["note"]:
        print(f"ℹ️  {rep['note']}")
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return rep["exit_code"]


def cmd_caliber(args) -> int:
    """尺子分区：**哪条读数能跟哪条比**。

    ⛔ 它存在的理由（r57 实测）：`doctor` 的 `judge.boundary` 因为账本里
       混了 14 把尺子而 fail，给出的出路是"用同一把尺子重跑一组"，
       而**系统里当时没有这个工具** ⇒ 那条出路是装饰（第 35b 条）。

    ⛔ `--seal` 是**显式**动作，默认不开：封存 = 声明"这段历史退出一切
       跨轮比较"，是不可撤销（只可前推）的语义动作，不该由只读命令顺手做。
    """
    from . import caliber as C
    from .ledger import Ledger
    p = _paths(Path(args.root))
    led = Ledger(p["ledger"])
    evs = list(led.iter_events())
    if getattr(args, "seal", False):
        rep = C.seal(led, reason=getattr(args, "note", "") or "")
        if getattr(args, "json", False):
            print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        if not rep["sealed"]:
            print(f"❌ 未封存：{rep['reason']}")
            return 1
        print(f"✅ 已封存到 seq={rep['upto_seq']}"
              f"（之前 {rep['prev_upto_seq']}）；"
              f"封存段 {rep['dropped_sealed_after']} 条，"
              f"可比 {rep['n_comparable_after']} 条")
        return 0
    view = C.comparable_view(evs)
    if getattr(args, "json", False):
        print(json.dumps(view, ensure_ascii=False, indent=2, default=str))
    else:
        print(C.render(view))
    #: ⛔ 多把尺子 **且** 未封存 ⇒ 退出码非 0：这是"待处置"，不是"报错"。
    #:    不设 ⇒ CI 里永远绿，等于没这个命令（第 9 条：闸恒绿先做反证）。
    return 0 if (view["n_segments"] <= 1 or view["sealed_upto"] is not None) else 1


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 的 parser（**单独抽出**是为了可测）。

    ⛔ 为什么拆：入口参数一致性只能靠"把三个入口的 parser 拿出来逐项比对"
       来验证；parser 埋在 `main()` 里 ⇒ 测试只能 `--help` 抓字符串，
       而字符串比对会把"改了 help 文案"误报成"参数不一致"。
       公共参数（`--root` / `--log-*`）走 `entry.add_*`，不在这里手写。
    """
    ap = argparse.ArgumentParser(prog="aether", description="V6 · AETHER CLI")
    add_root_arg(ap)
    #: ⛔ `--version` 必须有，且只从 `_version` 读。一个系统要被人信任，
    #:    第一件事是它能回答"我是谁、读的是哪版格式"。此前版本号散在
    #:    4 处（含 `serve.py` 的字面量），且没有任何命令行入口能读出来。
    from . import _version as _v
    ap.add_argument("--version", action="version",
                    version=f"V6 · AETHER {_v.CODE_VERSION}"
                            f"（账本格式 v{_v.LEDGER_FORMAT}）")
    #: ⛔ 默认 INFO 而非 WARNING：改造前这些诊断是 `print(file=sys.stderr)`，
    #:    **全部可见**。默认 WARNING 会让 💾/ℹ 那几条凭空消失 ⇒ 静默改了
    #:    用户可见行为。要安静请显式 `--log-level WARNING`。
    add_log_args(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)

    #: ⛔⛔ `--root` 也必须能在**子命令之后**写。此前只加在顶层 ⇒
    #:    `aether --root /x doctor` 可以，而 `aether doctor --root /x`
    #:    直接报 `unrecognized arguments` —— 而后者是所有人的第一直觉
    #:    （也是文档里"参数跟在命令后面"的常规写法）。用户遇到的是一个
    #:    "参数不存在"的报错，而参数**是**存在的，只是位置受限。
    #:    用 `suppress=True` 挂到每个子命令上：不设默认值，就不会盖掉
    #:    顶层已经解析出的值（见 `entry.add_root_arg` 的说明）。
    def _sub(name, **kw):
        sp = sub.add_parser(name, **kw)
        add_root_arg(sp, suppress=True)
        return sp

    _sub("status", help="账本 + 库统计 + 投影一致性")
    #: ⛔ r61：`init` 的目标目录**只看 `--dir` / 当前目录**，不看 `--root`。
    #:    理由：`--root` 在安装态的默认值是用户数据目录（`~/.local/share/aether`），
    #:    若拿它当默认目标，用户在一个空目录里敲 `aether init` 会建到**家里**
    #:    —— 而人的直觉永远是"在我现在站的地方建"。要指定位置请显式 `--dir`。
    ini = _sub("init", help="在当前目录建工作区骨架（幂等，已存在的不动）")
    ini.add_argument("--dir", default="",
                     help="建在这里（默认：当前目录；与 --root 无关）")
    ini.add_argument("--dry-run", action="store_true",
                     help="只报要建什么，一个字节都不落")
    #: ⛔ r61：`selfcheck` 与 `check` 是**两件不同的事**，不许合并 ——
    #:    `check` 跑 `tests/` 里的契约（33–37 分钟，**只有仓库里有**）；
    #:    `selfcheck` 跑内核自带的不变式（秒级，**装出来的包里也有**）。
    #:    装成 wheel 之后 `check` 只会报"0 份契约 ⇒ 判失败"，那时该跑的是这里。
    sc = _sub("selfcheck", help="自检：秒级、离线、不写生产（装完就能跑）")
    sc.add_argument("--only", default="", help="只跑 key 含该子串的项")
    sc.add_argument("--json", action="store_true", help="输出结构化报告")
    _sub("verify-chain", help="校验账本哈希链")
    #: ⛔ `check` 是把"全部验收"收成一条命令的入口：它自己聚合退出码，
    #:    是全仓库**唯一**能回答"这套代码现在算不算通过"的命令。
    ck = _sub("check", help="跑完全部契约验收（一条命令、一个退出码）")
    ck.add_argument("--only", default="",
                    help="只跑文件名含该子串的契约（调试用；报告会标注是子集）")
    ck.add_argument("--json", action="store_true", help="输出结构化报告")
    ck.add_argument("--timeout", type=float, default=0.0,
                    help="单份契约超时秒数（默认 600；超时记 timeout 而不是失败）")
    ck.add_argument("-j", "--jobs", type=int, default=1,
                    help="并行数（契约各用临时目录，互不写生产状态）")
    bk = _sub("backup", help="备份账本/记忆/投影（自带校验和）")
    bk.add_argument("--to", default="", help="备份存放目录（默认 <root>/backups）")
    bk.add_argument("--note", default="", help="给这份备份写一句备注")
    bk.add_argument("--archive", action="store_true",
                    help="顺便打成单个 .tar.gz（便于传输；备份本身照常保留）")
    rs = _sub("restore", help="从备份恢复（默认只演练，--yes 才执行）")
    rs.add_argument("--from", dest="src", required=True, help="备份目录")
    rs.add_argument("--yes", action="store_true",
                    help="真的执行覆盖（恢复前会自动留一份当前状态）")
    cl = _sub("caliber", help="尺子分区：哪段读数能跟哪段比（--seal 才动手）")
    cl.add_argument("--json", action="store_true", help="输出结构化报告")
    cl.add_argument("--seal", action="store_true",
                    help="把当前账本末尾之前的历史封存（只追加事件，不删改）")
    cl.add_argument("--note", default="", help="封存理由（进账本）")
    dc = _sub("doctor", help="一条命令体检（只读，不改任何东西）")
    #: ⛔ 默认**不接**外部清单（`NO_CAPS`）：命令行体检的默认口径是
    #:    "只查仓库自带的"，与 UI 默认挂示例清单（`DEFAULT_CAPS`）不同 ——
    #:    两个默认值都在 `entry` 里命名，散在入口里就看不出是刻意的。
    dc.add_argument("--caps", default=NO_CAPS,
                    help="顺带校验能力清单能否装载（未指定 ⇒ 该项记 skipped）")
    rb = _sub("rebuild", help="从账本重建 SQLite 投影（库只是视图）")
    rb.add_argument("--check", action="store_true",
                    help="只校验投影与账本是否一致，不重建")
    rb.add_argument("--out", default="", help="重建到别的库（默认覆盖 data/aether.db）")
    rb.add_argument("--force", action="store_true",
                    help="（保留位）不备份旧库 —— 默认总是备份")
    s = _sub("synth", help="合成任务并统计 yield")
    s.add_argument("--max", default="8")
    #: ⛔ 变异参数默认**与既有基线同口径**（depth=1 / 默认三种），
    #:    显式传参才算新实验 —— 否则历史账本不可比。
    s.add_argument("--depth", default="1", choices=["1", "2"],
                   help="变异点数：2 ⇒ 需要【叠加】才能修好")
    s.add_argument("--kinds", default="",
                   help="变异种类，逗号分隔：compare,binop,constant,"
                        "boolop,unaryop")
    s.add_argument("--balanced", action="store_true",
                   help="按变异维度轮流取点位（默认：按遍历序取前 N 个）")
    m = _sub("memory", help="记忆库：读数 / 换嵌入后全量重算 / 补记账")
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
    c = _sub("caps", help="接入层：列出 / 查看 / 调用 agents 与 skills")
    c.add_argument("--caps", default=NO_CAPS,
                   help="能力清单 JSON（不传则只显示源码内注册的能力）")
    cs = c.add_subparsers(dest="caps_cmd", required=True)
    #: ⛔ `discover` 是"**发现**"而不是"下载/安装"：它探本机已有工具的
    #:    路径与版本，生成可直接被 `--caps` 读的清单。故意不提供
    #:    "从网上装"—— 那会改环境、涉许可证与供应链，且与本项目
    #:    "判据必须离线可回归"的宪法冲突（详见 `aether/discover.py`）。
    dp = cs.add_parser("discover",
                       help="探本机已装的外部工具并生成能力清单（不下载/不安装）")
    dp.add_argument("--out", default="",
                    help="把生成的清单写到这个路径（不传则只打印报告）")
    dp.add_argument("--json", action="store_true", help="输出结构化发现报告")
    dp.add_argument("--manifest-only", action="store_true",
                    help="只输出清单（便于管进文件），不带报告")
    #: ⛔ 这条链是"发现 → 候选 → 过闸 → 留痕 → **人批准**"，
    #:    它存在的理由见 `aether/caps_candidate.py`：直接"去网上找并装"
    #:    同时踩宪法（判据须离线可回归）、沙箱（网络动作被拦）、
    #:    信任（自动下载并执行 = 跳过人审）。
    cg = cs.add_parser(
        "candidates",
        help="能力候选：生成 / 过闸 / 批准（**不下载不安装**，安装由人做）")
    cgs = cg.add_subparsers(dest="cand_cmd", required=True)
    cgp = cgs.add_parser("propose", help="从本机探针生成候选（缺的工具 / 未包装的）")
    cgp.add_argument("--json", action="store_true", help="结构化输出")
    #: ⛔ 外部上游事实**默认不取**：外部 API 有配额（实测 core 60 次/小时）
    #:    且可用性不归我们管。想要必须显式开 —— 破法存在但默认关，
    #:    而不是删掉它（同 r42 的对拍预算）。
    cgp.add_argument("--net", action="store_true",
                     help="顺便查上游仓库现状（默认不联网）")
    #: ⛔ 只给契约的**回环服务器**用，让契约完全离线（宪法：判据必须离线
    #:    可回归）。不进帮助文档：它不是给人日常用的开关。
    cgp.add_argument("--gh-api-base", default="", help=argparse.SUPPRESS)
    cgv = cgs.add_parser("validate", help="对候选过闸（探针复核 + 白名单 + 非回归 + 可复验）")
    cgv.add_argument("--tool", required=True, help="工具名（如 ghidra）")
    cga = cgs.add_parser("approve", help="**人批准**（没有 --yes 一律不推进）")
    cga.add_argument("--tool", required=True)
    cga.add_argument("--by", default="", help="批准人（必须留名）")
    cga.add_argument("--yes", action="store_true",
                     help="显式批准：本系统不代你决定装什么")
    cgh = cgs.add_parser("hint", help="打印给人执行的安装命令（本命令不执行它）")
    cgh.add_argument("--tool", required=True)
    #: ⛔ **机器自审**（r60 G）：把「人点 --yes + 人执行」换成
    #:    「五道判据 + 自动回滚」。人审被拿掉，替它顶上去的是
    #:    清单逐字比对 / 执行白名单 / 可回滚 / 四道闸当场重跑 / 装后复验。
    #:    理由与边界见 `aether/install_auto.py` 的模块文档。
    cgi = cgs.add_parser(
        "auto",
        help="**机器自审**安装：五道判据全过才执行，装不上自动卸载")
    cgi.add_argument("--tool", required=True)
    #: ⛔ 演练是**默认安全**的那一档：裁决照跑，命令一条不执行。
    cgi.add_argument("--dry-run", action="store_true",
                     help="只裁决不执行（环境零改动）")
    cgi.add_argument("--timeout", type=float, default=1800.0,
                     help="单条命令超时秒数（默认 1800）")
    #: ⛔ 人装完之后**回来结账**的入口（r60 B）。没有它，`installed` 是
    #:    不可达的终态：工具一装好候选就蒸发，账本上永远 0 次。
    cgvf = cgs.add_parser("verify", help="装完后复验并结案（approved ⇒ installed）")
    cgvf.add_argument("--tool", required=True)
    #: ⛔ 清理提案器：让"自己的产出该收拾"变成可被提议、可被裁决的一件事。
    #:    判据不是"通过测试"（删代码不会让 reward 变高 ⇒ 按增益判永远提不出来），
    #:    而是"**减少自指负载**且**不动判据集**"。详见 `aether/cleanup.py`。
    cl = cs.add_parser(
        "cleanup",
        help="清理提案：找零消费方接口/只写不读属性（**只提案，不改源码**）")
    cls_ = cl.add_subparsers(dest="cleanup_cmd", required=True)
    clb = cls_.add_parser("propose", help="扫描并生成清理提案（只读）")
    clb.add_argument("--limit", type=int, default=20)
    clb.add_argument("--json", action="store_true")
    clv = cls_.add_parser(
        "validate",
        help="对提案过闸（不动判据集/减负可测/非回归对拍/可执行）")
    clv.add_argument("--id", dest="pid", default="", help="提案 id（不传则全跑）")
    #: ⛔ 对拍预算：每条 = 复制一份仓库 + 跑一次冻结契约。
    #:    ⛔ **超出预算的提案记为 unverified ⇒ 红**，不是"跳过"。
    #:      跳过的提案会停留在 `pending`，看上去像"待办"，
    #:      而它其实从未被验证 —— 那才是最容易骗人的形态。
    clv.add_argument("--probe-limit", type=int, default=3,
                     help="本次最多对几条提案做删前/删后对拍（默认 3）")
    clv.add_argument("--max-contracts", type=int, default=12,
                     help="单次对拍最多跑几份冻结契约（默认 12；"
                          "超了记为 unverified，不静默截断）")
    clv.add_argument("--python", default=None,
                     help="跑冻结契约用的解释器（默认当前进程；"
                          "契约是**独立脚本**，不需要 pytest）")
    cla = cls_.add_parser("approve", help="**人批准**（没有 --yes 一律不推进）")
    cla.add_argument("--id", dest="pid", required=True)
    cla.add_argument("--by", default="", help="批准人（必须留名）")
    cla.add_argument("--yes", action="store_true",
                     help="显式批准：本系统不代你决定删什么")
    cap_ = cls_.add_parser("patch", help="打印给人执行的补丁（本命令不执行）")
    cap_.add_argument("--id", dest="pid", required=True)
    cd = cls_.add_parser(
        "done",
        help="人执行完补丁后**回来结账**：复验目标真的没了才准结案")
    cd.add_argument("--id", dest="pid", required=True)
    cd.add_argument("--note", default="",
                    help="执行备注（可选，进账本）")
    caa = cls_.add_parser(
        "auto",
        help="**机器自审**：冻结契约对拍通过就自己落盘"
             "（先快照、落盘后复验、失败原子回滚）")
    caa.add_argument("--id", dest="pid", required=True)
    caa.add_argument("--max-contracts", type=int, default=12,
                     help="单次对拍最多跑几份冻结契约（默认 12；"
                          "超了记 unverified，不静默截断）")
    caa.add_argument("--timeout", type=float, default=900.0,
                     help="跑冻结契约的超时秒数")
    caa.add_argument("--dry-run", action="store_true",
                     help="只对拍不落盘：看「这段删除是否等价」的裁决")
    cs.add_parser("list", help="列出已注册能力")
    sh = cs.add_parser("show", help="查看一个能力的声明")
    sh.add_argument("id")
    iv = cs.add_parser("invoke", help="调用一个能力")
    iv.add_argument("id")
    iv.add_argument("--json", default="{}", help='参数 JSON，如 \'{"text":"a"}\'')
    ld = cs.add_parser("load", help="装载能力清单并报告每条成败")
    ld.add_argument("manifest")

    e = _sub("evolve", help="跑 self-harness 进化")
    e.add_argument("--rounds", default="3")
    e.add_argument("--group", default="3")
    e.add_argument("--max-tasks", default="12")
    e.add_argument("--depth", default="1", choices=["1", "2"],
                   help="变异深度：1=单点（默认，与既有基线同口径）/ "
                        "2=双点（需要【叠加】才能修好 ⇒ 抬高天花板）")
    e.add_argument("--kinds", default="",
                   help="变异种类，逗号分隔：compare,binop,constant,boolop,"
                        "unaryop,string。留空=默认三种（与既有基线同口径）"
                        "；string 是后加的算子：真实代码里字符串处理是常态，"
                        "而默认三种打不穿纯字符串处理函数")
    e.add_argument("--balanced", action="store_true",
                   help="⛔ 新实验口径（默认关）：**按变异维度轮流取点位**。"
                        "`which` 是全局遍历序号 ⇒ 数量最多的算子会把前 N 个"
                        "序号占满，后加的维度一条题都进不来（实测 dev 环境"
                        "顺序采样 3 条全是 compare，均衡后 6 条 / 3 个维度）。"
                        "只改取点顺序，尝试次数与判据不变 ⇒ 干净的单变量对照")
    e.add_argument("--require-behavior", action="store_true",
                   help="⛔ 新实验口径（默认关）：**只留真行为变化的题**，"
                        "过滤掉崩溃类（raise / import-failed）。崩溃题任何"
                        "「把源码改回接近基线」的修法都能顺手修好 ⇒ 靠它拿到"
                        "的 reward 提升是假的。开它用来检验既有结论的可信度")
    e.add_argument("--capsules", default="both",
                   choices=["both", "general", "narrow"],
                   help="提议池：both=两类都给（定点修法必赢）/ general / narrow")
    e.add_argument("--seed", default="0")
    e.add_argument("--dev-env", default="textscore", help="发展集所在环境")
    e.add_argument("--heldout-env", default="eventflow", help="held-out 环境（不参与选择）")
    #: ⛔ 世界规模是**实验变量**，不是常量。默认仍是 `DEFAULT_ENVS` 四个
    #:    ⇒ 与既有账本/基线同口径（见第 103 条纪律：新口径必须显式开启）。
    e.add_argument("--envs", default="",
                   help="参与这次进化的环境：`auto`=扫 tasks/ 下全部合法目录，"
                        "或逗号分隔的名字。留空=默认四个（历史口径）")
    e.add_argument("--heldout", default="on", choices=["on", "off"],
                   help="off ⇒ 关闭泛化闸（用于对照：过拟合候选会被提交）")
    e.add_argument("--proposer", default="template", choices=["template", "llm"],
                   help="变异提议器：template（离线确定性）/ llm（真模型）")
    e.add_argument("--llm-model", default="", help="真模型名（默认 Qwen3.5-4B:latest）")
    e.add_argument("--heldout-floor", default="0.0",
                   help="held-out 增益地板（低于它判 overfit）")
    e.add_argument("--ho-grow", action="store_true",
                   help="⛔ 新实验口径（默认关）：**held-out 判别力自愈**。\n"
                        "每轮用当前基线体检 held-out，某层「能涨的题」少于\n"
                        "min_level_headroom 就补采样（只补基线也做不对的题\n"
                        "⇒ 与候选无关 ⇒ 不给任何候选优待，H1 不动）。\n"
                        "它换的是**考卷**不是判据 ⇒ 开了不与历史同表比。")
    e.add_argument("--ho-grow-max-n", default="64",
                   help="补采样的合成 max_n 封顶（合成要真跑变异体 ⇒ 必须封顶）")
    e.add_argument("--ho-grow-max-tasks", default="96",
                   help="补采样后 held-out 的总题数封顶")
    e.add_argument("--ho-grow-variants", default="1",
                   help="r55：补采样可用的**采样口径**档数（默认 1 = 只用主"
                        "口径，与 r53/r54 逐字同口径；≥2 时在主口径题源撞顶"
                        "后换一种取点顺序再取一批题）")
    e.add_argument("--retry-every", default="0",
                   help="⛔ 新实验口径（默认 0 = 现状，即**永久**不重提）：\n"
                        "被闸拒过的候选，每被跳过这么多轮就**放行一次**让闸\n"
                        "重审。0 ⇒ 与历史逐字同口径。\n"
                        "存在理由（r54 实测）：`partial`/`overfit` 的结论依赖\n"
                        "当时的 held-out 卷子，而卷子会随基线长（--ho-grow）\n"
                        "⇒ 旧判定会失效，可这条 skip 没有失效机制；而下游\n"
                        "`deadend_probation_every` 的出口实测 **一次都没被走到**\n"
                        "（13 次 _in_cooldown 判定全 streak=0），因为候选在上游\n"
                        "就被永久 skip 了。它与那个出口同形、同样默认关闭。")
    e.add_argument("--gate-tries", default="1",
                   help="⛔ 新实验口径（默认 1 = 现状）：一轮内最多让几个候选\n"
                        "去闯泛化闸。1 ⇒ best 被拦就整轮作废，下一轮因为排序\n"
                        "不变又会提同一个（实测 6 轮里 4 轮提同一个候选）。\n"
                        ">1 ⇒ 按**发展集**排序依次试下一个 —— 闸一条没少，\n"
                        "held-out 仍不参与排序（H1 不动），只是被拦不再等于\n"
                        "这一轮结束。⛔ 默认 1 ⇒ 与历史基线逐字同口径。")
    e.add_argument("--archive-resample", default="0",
                   help="⛔ 新实验口径（默认 0 = 关，与历史逐字同口径）：\n"
                        "每轮从**分层非支配存档**里取回至多 N 个历轮"
                        "「通过准入闸但没被选中」的解，在当前基线上重测。\n"
                        "0 ⇒ 不入档、不重采样 ⇒ 账本里一条 archive.* 都没有。\n"
                        "⛔ 存档里的 reward 是**旧基线**读数，只用来挑候选；\n"
                        "取回来的条目必须重算 reward 才许进任何判据。")
    e.add_argument("--archive-cap", default="8",
                   help="每层前沿保留上限（默认 8）。⛔ 无上限 = 全存 ⇒ "
                        "重采样会退化成「按旧 reward 全局排序」。")
    e.add_argument("--allow-scoped-commit", action="store_true",
                   help="⛔ 新口径（默认关 = 历史逐字同口径）：允许**只改定点"
                        "技能**（`L3.skills.patch:<env>`）的候选提交。\n"
                        "存在理由（r62 实测）：`patch:<env>` 只对点名的环境生效"
                        "⇒ 它在 held-out 上 0 增益是**构造必然**，旧判据拿这个 0"
                        "判 overfit ⇒ 定点修法永远提交不了（dev 0/5→5/5 仍被拦，"
                        "连续 6 轮空转）。\n"
                        "开了之后这类候选判 `scoped`（放行，**不是** generalize："
                        "没证明泛化，只是没资格用这道闸判），非回归闸与能力地板"
                        "照常生效。\n"
                        "⛔ 沾了路由/工具/通用技能/记忆策略的候选不受影响 ——"
                        "那些层对 held-out 有影响，0 增益就是真没泛化。")
    e.add_argument("--ho-implication", action="store_true",
                   help="⛔ 新实验口径（默认关）：给 LLM 提议器的 held-out 规则里\n"
                        "**补一句逻辑推论** —— 「只在 patch:<环境> 上生效的改法\n"
                        "在该环境上必然 0 增益 ⇒ 必然被 overfit 拦；要跨环境就得\n"
                        "落在与环境无关的层」。\n"
                        "存在理由（r62 实测）：`patch:<env>` 只在点名环境生效这句\n"
                        "接口契约本来就在 prompt 里，但模型没做那一步推论（照样提\n"
                        "patch:textscore）。⛔ 只写推论，不写该改哪一层/该加哪个\n"
                        "技能 —— 那是答案，说了就是替模型考试。")
    e.add_argument("--evidence", choices=("exact", "coarse"), default="exact",
                   help="⛔ 新实验口径（默认 exact = 历史逐字同口径）：喂给"
                        "**提议器**的弱点证据粒度。\n"
                        "exact  ⇒ 带具体检查名（`checks_failed:clamp_low,...`）。\n"
                        "coarse ⇒ 只给失效类别与数量（`behavior_mismatch: 3`、\n"
                        "         `n_failing_checks: 7`），不出现任何检查名。\n"
                        "存在理由（r62 实测）：exact 下 qwen2.5:7b 把 7 个检查名\n"
                        "逐字抄进技能正文 ⇒ dev 0/5→5/5 而 held-out 0.0→0.0 ⇒\n"
                        "被判 overfit。检查名是**发展集**的信息，等于让模型抄答案。\n"
                        "⛔ 只换给模型的视图：`weakness.mined` 账本仍记原文，\n"
                        "   闸与判据一条不动。")
    e.add_argument("--parallel", default="0",
                   help="并发评估线程数。0/1=串行（**默认**，与历史基线同口径）。\n"
                        "⛔ 并行只包住 runner 计算，run_id 分配与账本写入仍串行\n"
                        "（顺序漂移 ⇒ 账本不可复现）。⛔ 任务若逼近 timeout，\n"
                        "并行下的资源竞争可能把它挤成 timeout ⇒ 结果会变 ⇒\n"
                        "要可比就用默认串行。")
    e.add_argument("--embedder", default="hash", choices=["hash", "auto", "ollama"],
                   help="嵌入器：hash（默认，离线）/ auto|ollama（须过选型闸）")
    #: ⛔ 预算四项**默认全空 = 不限**（与历史基线同口径）。接真 LLM 时
    #:    不设就是"把预算交给循环自己"（一次 propose = 一次付费调用）。
    e.add_argument("--max-rounds", default="", help="预算：最多跑几轮")
    e.add_argument("--max-seconds", default="", help="预算：挂钟上限（秒，轮末检查）")
    e.add_argument("--max-propose", default="",
                   help="预算：提议器最多调几次（真 LLM = 付费调用数）")
    e.add_argument("--max-eval-runs", default="",
                   help="预算：发展集最多评估多少次任务")
    e.add_argument("--checkpoint", default="",
                   help="断点续跑：每轮末把进度（第几轮 + 当前 harness）"
                        "原子写到这个文件。不指定 ⇒ 不写 ⇒ 不可续跑。")
    e.add_argument("--no-checkpoint", action="store_true",
                   help="不写续跑进度（默认写 <root>/data/checkpoint.json）"
                        " ⇒ 本次运行不可续跑")
    e.add_argument("--resume", action="store_true",
                   help="从 --checkpoint 记录的位置接着跑，轮号**接续**"
                        "（否则账本里会出现两个「第 3 轮」）。"
                        "⛔ 换尺子 / 换配置 / 账本回退 ⇒ 拒绝并说明是哪一种。"
                        "⛔ 这个开关的语义是「--rounds 为目标**总**轮数」。"
                        "默认已经会自动接续（见 --no-continue），本开关只在"
                        "需要旧语义（给总目标）时用。")
    #: ⛔⛔ r60 F：**默认接着上次的 checkpoint 跑**（追加 --rounds 轮）。
    #:    理由不是"方便"，是实测出来的：生产账本里 119 次 `harness.commit`
    #:    只有 6 个不同技能，`fix_control_flow` 一个占 93 次；38 次
    #:    `baseline.superseded` 全是同一个 4→6。根因是调度者每轮都传
    #:    `--rounds 1` 且**从不传 --resume** ⇒ 每轮从 seed 起 ⇒ 同一件事
    #:    重学 N 遍。机制（`run(start_round=...)` + `rounds_total =
    #:    start_round + rounds`）早就在，缺的只是"默认走它"。
    e.add_argument("--no-continue", action="store_true",
                   help="⛔ 不接续：每次从初始 harness 起（旧行为）。"
                        "默认**接续**：checkpoint 校验通过就接着跑，"
                        "追加 --rounds 轮，轮号连续。")
    e.add_argument("--fresh", action="store_true",
                   help="从初始 harness 起，并重置轮号（checkpoint 会被覆盖）。"
                        "与 --no-continue 的区别：那个只影响本轮，这个明确"
                        "表示「上一次的进度我不要了」。")

    u = _sub("ui", help="起 API + 控制台，并打开桌面窗口")
    #: ⛔ 与 `aether.ui` **同一份**参数声明（`entry.add_serve_args`）。
    #:    手写一遍就必然漂移 —— 漂移的表现是"CLI 认这个开关、UI 不认"，
    #:    而两边的 help 都还写着"支持"。
    add_root_arg(u, suppress=True)
    add_serve_args(u, caps_default=DEFAULT_CAPS, with_console=False)
    add_ui_args(u)
    #: ⛔ `suppress=True`：子 parser 的默认值会**盖掉**主 parser 已解析的值
    #:    （见 `entry.add_log_args` 的说明）⇒ `aether --log-level DEBUG ui`
    #:    会静默退回 INFO。SUPPRESS 让"没在子命令后写"时保留全局值。
    add_log_args(u, suppress=True)

    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd == "ui":
        from .ui import build_parser as ui_parser
        from .ui import main as ui_main
        #: ⛔ 透传清单**不手写**，且以**接收方的 parser** 为准导出
        #:    （这里的目标是 ui，不是 serve —— 三层包装里每一层都只认
        #:    下一层的参数。此前手写清单漏了 --log-level / --log-json /
        #:    --api-token ⇒ "设了不生效"且没有任何报错）。
        #:    ⛔ `--console` 不传：那是 ui 自己决定的（它知道仓库里的
        #:    console/ 在哪），传过去会被子 parser 当成未知参数 ⇒ rc=2。
        return ui_main(build_forward(normalize_log_args(args),
                                     parser=ui_parser()))

    #: ⛔ `ui` 会把控制权交给子进程（它自己会再 setup 一次），这里不重复配。
    _setup_logging(getattr(args, "log_level", "INFO"),
                   getattr(args, "log_json", False))
    return {
        "status": cmd_status,
        "verify-chain": cmd_verify_chain,
        "rebuild": cmd_rebuild,
        "synth": cmd_synth,
        "memory": cmd_memory,
        "evolve": cmd_evolve,
        "caps": cmd_caps,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "check": cmd_check,
        "doctor": cmd_doctor,
        "caliber": cmd_caliber,
        "init": cmd_init,
        "selfcheck": cmd_selfcheck,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

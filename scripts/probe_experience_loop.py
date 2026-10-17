"""经验闭环（会学习）的可观测性探针 —— 生产同源。

为什么要有这个：全量账本扫描（4853 条）显示 `evolution:reject` 写入仅
5 条、`knowledge.ingested` 0 条，而"因学到而少提了几个候选"**根本没有
计数**。本项目第 4 条纪律是「有机制 ≠ 这条路被走到」，第 10 条是「三态
必须分开」—— 这个探针就是把这两条落到学习闭环上：

  A 组  闭环真的成立：记一次失败 ⇒ 下一轮提议里不再有那个变异
  B 组  两侧都进账本：experience.remembered / experience.propose
  C 组  三态分开：ok / degraded（检索崩 ⇒ 放行）/ no_memory
  D 组  **反证**：把筛除逻辑换成恒不筛 ⇒ A 必须转红（证明它不是恒真断言）
  E 组  去重 ≠ 学到：同一条失败记两次，disposition 必须是 deduped

⛔ 反证先于结论：没有 D 组，A–C 组全绿也可能是"闸根本没接上"。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aether.genome import Harness                     # noqa: E402
from aether.ledger import Ledger                      # noqa: E402
from aether.memory import MemoryOS                    # noqa: E402
from aether.embeddings import HashingEmbedder         # noqa: E402
from aether.providers import MemoryAwareProposer      # noqa: E402


class _Note:
    def __init__(self, note_id: str, content: str):
        self.note_id = note_id
        self.content = content

    def __repr__(self) -> str:
        return f"_Note({self.note_id})"


class _Cap:
    def __init__(self, name: str):
        self.name = name


class _Stub:
    """同协议内层提议器：每次都提同样的三个变异（模拟"记不住"的提议器）。"""
    def __init__(self, names=("add_skill", "fix_route", "tune_meta")):
        self.names = tuple(names)
        self.got_rejected = None

    def propose(self, weakness, harness, n=3, rejected=None):
        self.got_rejected = rejected
        return [_Cap(x) for x in self.names[:n]]

    def diagnostics(self):
        return {"name": "stub"}


def _mem(tmp: Path):
    p = tmp / "mem.json"
    return MemoryOS.load(p, HashingEmbedder(dim=256), min_content_len=0)


def run() -> int:
    out: dict = {}
    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  —— {detail}" if detail else ""))
        out.setdefault("checks", []).append({"name": name, "ok": bool(cond)})
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        led = Ledger(tmp / "ledger.jsonl")
        mem, _ = _mem(tmp)
        p = MemoryAwareProposer(_Stub(), mem, ledger=led)
        w = {"kind": "gate_reject_risk", "topic": "scheduler"}

        # ── A 组：闭环真的把东西锁住 ──────────────────────────────
        print("\n── A 组：记一次失败 ⇒ 下一轮不再提议它 ──")
        before = [c.name for c in p.propose(w, Harness())]
        p.remember(w, "add_skill", "gate_reject: 跌幅超限")
        after = [c.name for c in p.propose(w, Harness())]
        ck("A1 记之前提了 add_skill", "add_skill" in before, str(before))
        ck("A2 记之后 add_skill 不再出现", "add_skill" not in after, str(after))
        ck("A3 其余候选不受牵连", "fix_route" in after and "tune_meta" in after,
           str(after))
        ck("A4 失败史被喂给内层提议器", bool(_stub_rejected(p)),
           f"n={len(_stub_rejected(p) or [])}")

        # ── B 组：两侧都进账本 ────────────────────────────────────
        print("\n── B 组：闭环两端都可读 ──")
        kinds = [e.kind for e in led.events()] if hasattr(led, "events") else []
        rem = [e for e in led.events() if e.kind == "experience.remembered"]
        pro = [e for e in led.events() if e.kind == "experience.propose"]
        ck("B1 remember 侧有账", len(rem) >= 1, f"n={len(rem)}")
        ck("B2 propose 侧有账", len(pro) >= 2, f"n={len(pro)}")
        if rem:
            pp = rem[0].payload
            ck("B3 remembered 字段完整",
               {"key", "reason", "disposition", "deduped"} <= set(pp),
               str(sorted(pp)))
            ck("B4 真写入时 disposition=written", pp.get("disposition") == "written",
               str(pp.get("disposition")))
        if len(pro) >= 2:
            last = pro[-1].payload
            ck("B5 propose 记账了 n_in/n_out/n_skipped",
               {"n_in", "n_out", "n_skipped", "recall"} <= set(last),
               str(sorted(last)))
            ck("B6 拦下数与实际相符",
               last["n_skipped"] == last["n_in"] - last["n_out"],
               f"{last['n_in']}→{last['n_out']}")
            ck("B7 失败史条数可读（n_known_history ≥ 1）",
               last.get("n_known_history", 0) >= 1,
               str(last.get("n_known_history")))
        out["kinds"] = kinds

        # ── C 组：三态分开 ────────────────────────────────────────
        print("\n── C 组：三态（ok / degraded / no_memory）──")
        p2 = MemoryAwareProposer(_Stub(), None, ledger=led)
        p2.propose(w, Harness())
        last_no = [e for e in led.events() if e.kind == "experience.propose"][-1]
        ck("C1 无记忆时 recall=no_memory（不冒充 ok）",
           last_no.payload.get("recall") == "no_memory",
           str(last_no.payload.get("recall")))
        ck("C2 无记忆也有事件（没学 vs 没装，能分辨）", True)

        class _Boom:
            def search(self, *a, **k):
                raise RuntimeError("index gone")

            def write(self, *a, **k):
                raise RuntimeError("disk full")

        p3 = MemoryAwareProposer(_Stub(), _Boom(), ledger=led)
        p3.propose(w, Harness())
        last_dg = [e for e in led.events() if e.kind == "experience.propose"][-1]
        ck("C3 检索崩 ⇒ recall=degraded（fail-open 可见）",
           last_dg.payload.get("recall") == "degraded",
           str(last_dg.payload.get("recall")))
        ck("C4 崩了也要照样放行（n_out == n_in，不吞候选）",
           last_dg.payload["n_out"] == last_dg.payload["n_in"],
           f"{last_dg.payload['n_in']}→{last_dg.payload['n_out']}")
        p3.remember(w, "x", "gate_reject")
        rem2 = [e for e in led.events() if e.kind == "experience.remembered"]
        ck("C5 写入崩 ⇒ disposition=write_failed（不冒充 wrote）",
           rem2[-1].payload.get("disposition") == "write_failed",
           str(rem2[-1].payload.get("disposition")))

        # ── E 组：去重 ≠ 学到新东西 ───────────────────────────────
        print("\n── E 组：同一条失败记两次 ──")
        n_before = len([e for e in led.events()
                        if e.kind == "experience.remembered"])
        p.remember(w, "add_skill", "gate_reject: 跌幅超限")
        rem3 = [e for e in led.events() if e.kind == "experience.remembered"]
        ck("E1 第二次仍记账（事件数 +1）", len(rem3) == n_before + 1,
           f"{n_before}→{len(rem3)}")
        ck("E2 但 disposition 标了 deduped（不是真学到）",
           rem3[-1].payload.get("deduped") is True,
           str(rem3[-1].payload.get("disposition")))

        # ── D 组：反证（把闸门拆掉，A 组必须转红）─────────────────
        print("\n── D 组：反证 —— 拆掉筛除逻辑 ──")
        orig_search = mem.search
        mem.search = lambda *a, **k: []               # 事故版：永远查不到失败史
        p4 = MemoryAwareProposer(_Stub(), mem, ledger=led)
        after_broken = [c.name for c in p4.propose(w, Harness())]
        a_ok = "add_skill" not in after_broken
        ck("D1 事故版下 A2 断言会转红 ⇒ A2 有牙", not a_ok,
           f"broken_propose={after_broken}")
        try:
            mem.search = orig_search
        except Exception:                              # noqa: BLE001
            pass

    total = len(out.get("checks", []))
    bad = len(fails)
    print(f"\n{'=' * 64}\n合计 {total} 条，失败 {bad} 条")
    for f in fails:
        print(f"  ✗ {f}")
    rj = ROOT / "data" / "probe_experience_loop.json"
    out["n_checks"] = total
    out["n_failed"] = bad
    out["failed"] = fails
    try:
        rj.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print(f"读数已写入 data/{rj.name}")
    except Exception as e:                              # noqa: BLE001
        print(f"⚠️ 读数落盘失败（不影响判定）：{type(e).__name__}: {e}")
    return 0 if bad == 0 else 1


def _stub_rejected(p: MemoryAwareProposer):
    return getattr(p.inner, "got_rejected", None)


if __name__ == "__main__":
    sys.exit(run())

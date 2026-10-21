#!/usr/bin/env python3
"""契约：跨运行热启动（`--warm-start`，r63i）。

═══ 为什么需要它（实测，不是设计推演）═══

账本 `data/ledger.jsonl` 里 `harness.commit` 共 **120** 条：

| 读数 | 实测 |
|---|---|
| `parent_id` 不同值 | **6 个**（`abcc4158…` 60 条 / `df9bf305…` 44 条） |
| 来自同一个父的占比 | **104/120 = 87%** |
| `round_no` | **全是 1** |
| 去重 `harness_id` | **15 个** |
| `capsule` 种类 | **6 种**，`add_skill:fix_control_flow` 占 94 条（78%） |

⇒ 每次 `aether evolve` 都从 `cli.py` 里那个裸种子 `Harness()` 重爬，
「用一百次」与「用一次」等价。本契约守的是**起点正确性**，不是判据：
热启动不豁免任何闸，只决定「这一轮从哪个节点长出来」。

═══ 反证的做法 ═══

· C 组每个字段一条：换掉那个字段**必须**从「匹配」翻成「拒绝」，
  且 G1 用**正对照**证明同口径时它确实是 True（否则 C 组全绿也可能
  只是永远返回 False）。
· E2 用 spy 证明 `load()` **不写账本**：它的 `append` 一被调用就抛。
· B4 证明「口径未知的提交」不被当成起点 —— 这是 fail-closed 的那一侧。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []

#: ⛔ 必须声明：不声明 ⇒ `check` 会报「未声明支持的模式」。
SUPPORTED_MODES = ("default",)

sys.path.insert(0, str(ROOT))

from aether import warmstart as W                      # noqa: E402
from aether.genome import Harness                      # noqa: E402
from aether.ledger import Ledger                       # noqa: E402
from aether.store import Store                         # noqa: E402


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


#: 判断标准卷子：与 CLI 默认同口径（四个环境 / textscore dev / eventflow ho）
CAL = {"dev_env": "textscore", "heldout_env": "eventflow", "seed": "0",
       "envs": ["textscore", "eventflow", "stateflow", "scheduler"]}


def mk_harness(skill: str = "fix_control_flow",
               route: str = "repair") -> Harness:
    """一个**真的装过东西**的 harness —— 裸种子不该被当成热启动的证据。"""
    return Harness(
        meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
        tools=("read_file", "write_file", "python_exec"),
        memory_policy={"recall_topk": 5, "min_content_len": 150},
        routing={route: skill},
        skills={skill: f"保守通用修法：{skill}"},
    )


class Ctx:
    """一次性临时账本 + 快照库。每个用例一份，互不污染。"""

    def __init__(self, prefix: str = "v6_ws_"):
        self.dir = Path(tempfile.mkdtemp(prefix=prefix))
        self.led = Ledger(self.dir / "ledger.jsonl")
        self.store = Store(self.dir / "store.db")

    def run(self, payload: dict) -> None:
        self.led.append("evolution.run_config", dict(payload), actor="test")

    def commit(self, h: Harness, *, hid: str = "", snapshot: bool = True,
               payload_override: dict = None) -> str:
        """落一条 `harness.commit`，并按需落快照。返回账本里记的 id。"""
        real = h.harness_id
        ledger_id = hid or real
        if snapshot:
            self.store.save_snapshot(real, payload_override
                                     if payload_override is not None
                                     else h.to_dict(), accepted=True)
        self.led.append("harness.commit",
                        {"harness_id": ledger_id, "parent_id": "root",
                         "capsule": "test", "round_no": 1}, actor="test")
        return ledger_id

    def load(self, **over):
        kw = dict(CAL)
        kw.update(over)
        return W.load(self.led, self.store, dev_env=kw["dev_env"],
                      heldout_env=kw["heldout_env"], seed=kw["seed"],
                      envs=kw["envs"])


def main() -> int:
    print("=" * 62)
    print("A 组 · 真能恢复（存在性 + 内容正确）")
    print("=" * 62)
    c = Ctx()
    c.run(CAL)
    h = mk_harness()
    c.commit(h)
    ws = c.load()
    ck("A1 同口径有一条提交 ⇒ ok=True", ws.ok, ws.reason)
    ck("A2 恢复出的 harness 与写进去的**逐字相等**",
       ws.harness is not None and ws.harness.to_dict() == h.to_dict(),
       "" if ws.harness is None else f"id={ws.harness.harness_id[:12]}")
    ck("A3 ⛔ 恢复的不是裸种子：技能与路由都在（否则等于没热启动）",
       ws.harness is not None
       and bool(ws.harness.skills) and bool(ws.harness.routing),
       "" if ws.harness is None else f"skills={list(ws.harness.skills)}")
    ck("A4 空账本 ⇒ ok=False，且理由是「第一次运行」",
       (lambda e: (not e.ok) and "第一次运行" in e.reason)(Ctx().load()),
       "")

    print()
    print("=" * 62)
    print("B 组 · 取的是**最后一条**，不是第一条")
    print("=" * 62)
    c = Ctx()
    c.run(CAL)
    h1, h2 = mk_harness("fix_control_flow"), mk_harness("fix_literals")
    c.commit(h1)
    c.commit(h2)
    ws = c.load()
    ck("B1 两条提交 ⇒ 恢复**后一条**",
       ws.ok and ws.harness is not None
       and ws.harness.harness_id == h2.harness_id,
       "" if ws.harness is None else ws.harness.harness_id[:12])

    c = Ctx()
    c.run(CAL)
    c.commit(h1)
    c.run({**CAL, "dev_env": "shlex"})          # 另一套卷子的提交
    c.commit(mk_harness("fix_boolops"))
    c.run(CAL)                                   # 再回到标准卷子（无新提交）
    ws = c.load()
    ck("B2 中间夹了**别的卷子**的提交 ⇒ 仍取本卷子那条（不串味）",
       ws.ok and ws.harness is not None
       and ws.harness.harness_id == h1.harness_id,
       "" if ws.harness is None else ws.harness.harness_id[:12])
    ck("B3 `n_matched` 只数匹配的，`n_commits_seen` 数全部",
       ws.n_commits_seen == 2 and ws.n_matched == 1,
       f"seen={ws.n_commits_seen} matched={ws.n_matched}")

    c = Ctx()
    c.commit(h1)                    # ⛔ 没有任何 run_config ⇒ 口径未知
    ws = c.load()
    ck("B4 ⛔ 口径未知的提交**不**当起点（run_config 之前的一律 fail-closed）",
       (not ws.ok) and ws.n_commits_seen == 1,
       ws.reason)

    print()
    print("=" * 62)
    print("C 组 · 口径不同必须拒绝（防串味）—— 每个字段一条")
    print("=" * 62)
    for tag, over in (
        ("C1 dev_env", {"dev_env": "shlex"}),
        ("C2 heldout_env", {"heldout_env": "stateflow"}),
        ("C3 seed", {"seed": "7"}),
        ("C4 envs", {"envs": ["textscore", "eventflow"]}),
    ):
        c = Ctx()
        c.run(CAL)
        c.commit(h)
        ws = c.load(**over)
        ck(f"{tag} 不同 ⇒ 拒绝", (not ws.ok) and ws.n_matched == 0,
           ws.reason[:60])

    c = Ctx()
    c.run({k: v for k, v in CAL.items() if k != "envs"})   # legacy：没有 envs
    c.commit(h)
    ws = c.load()
    ck("C5 envs 一方未记录 ⇒ **接受**（legacy），但 notes 必须说出口",
       ws.ok and bool(ws.notes), f"notes={ws.notes}")

    print()
    print("=" * 62)
    print("D 组 · 失败面：每种成因一条，且 reason 各不相同")
    print("=" * 62)
    c = Ctx()
    c.run(CAL)
    c.commit(h, snapshot=False)
    ws = c.load()
    ck("D1 账本记着但快照表没有 ⇒ ok=False 且理由提到快照",
       (not ws.ok) and "快照" in ws.reason, ws.reason[:60])

    c = Ctx()
    c.run(CAL)
    c.commit(h, payload_override={"tools": "not-a-list"})   # 类型错 ⇒ 抛
    ws = c.load()
    ck("D2 快照 payload 坏 ⇒ ok=False 且理由带异常类名",
       (not ws.ok) and "ValueError" in ws.reason, ws.reason[:70])

    c = Ctx()
    c.run(CAL)
    #: ⛔ D3 要的是「**有**快照、但快照内容指向另一个 harness」——
    #:    第一版图省事只把账本 id 换掉，结果快照列也是旧 id ⇒ 走的是
    #:    D1（快照缺失）那条分支，D3 根本没被测到。快照**列**必须等于
    #:    账本 id，而 payload 必须是另一个 harness 的内容。
    other = mk_harness("fix_literals")
    c.store.save_snapshot("deadbeefdeadbeef", other.to_dict(), accepted=True)
    c.led.append("harness.commit",
                 {"harness_id": "deadbeefdeadbeef", "parent_id": "root",
                  "capsule": "test", "round_no": 1}, actor="test")
    ws = c.load()
    ck("D3 快照 id 与账本 id 不一致 ⇒ 拒绝（用错起点比不用更危险）",
       (not ws.ok) and "id" in ws.reason, ws.reason[:70])

    def _no_snapshot():
        x = Ctx()
        x.run(CAL)
        x.commit(h, snapshot=False)
        return x.load()

    def _id_mismatch():
        x = Ctx()
        x.run(CAL)
        x.store.save_snapshot("deadbeefdeadbeef",
                              mk_harness("fix_boolops").to_dict(),
                              accepted=True)
        x.led.append("harness.commit",
                     {"harness_id": "deadbeefdeadbeef", "parent_id": "root",
                      "capsule": "test", "round_no": 1}, actor="test")
        return x.load()

    def _other_caliber():
        x = Ctx()
        x.run({**CAL, "seed": "9"})
        x.commit(h)
        return x.load()

    reasons = {Ctx().load().reason, _no_snapshot().reason,
               _id_mismatch().reason, _other_caliber().reason}
    ck("D4 ⛔ 四种成因给出**四个不同**的 reason（不是同一个兜底）",
       len(reasons) == 4, f"n_distinct={len(reasons)}")

    print()
    print("=" * 62)
    print("E 组 · 纪律：不改判据、不写账本、id 前缀兼容")
    print("=" * 62)
    src = (ROOT / "aether" / "warmstart.py").read_text(encoding="utf-8")
    ck("E1 ⛔ 模块不 import 任何闸（热启动只换起点，不碰判据）",
       "nongression" not in src and "generalization" not in src
       and "from .gate" not in src, "")
    #: ⛔ 只认「账本对象的 append」，不认所有 `.append(` —— 第一版用
    #:    `".append("` 把 `notes.append(...)` 也算了进去 ⇒ 假红。
    #:    源码字符串检查必须**指向具体接收者**，否则它在测别的东西。
    ck("E2 ⛔ 模块不写账本（事件由调用方落 ⇒ 单测不必伪造事件）",
       "ledger.append(" not in src and "led.append(" not in src, "")

    class _Spy:
        def iter_replay(self, kinds=None, since_seq=0):
            return iter(())

        def append(self, *a, **k):
            raise AssertionError("warmstart.load 不该写账本")

    ok_spy = True
    try:
        W.load(_Spy(), Store(Ctx().dir / "s2.db"), dev_env="textscore",
               heldout_env="eventflow", seed="0", envs=())
    except AssertionError:
        ok_spy = False
    ck("E3 spy 账本上 load() 不触发 append（运行期证明，不看源码）", ok_spy, "")

    real = mk_harness().harness_id
    ck("E4 16 位与 32 位前缀视为同一个 harness",
       W._id_compatible(real[:16], real), f"{real[:16]} ⊂ {real}")
    ck("E5 ⛔ 不同 harness 不许认亲（只认前缀，不认包含）",
       not W._id_compatible("aaaaaaaaaaaaaaaa", real), "")

    print()
    print("=" * 62)
    print("F 组 · 真的接回了 CLI（不是只在模块里躺着）")
    print("=" * 62)
    cli = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("F1 cli 落了 `evolution.warm_start` 事件（成功失败都可见）",
       '"evolution.warm_start"' in cli, "")
    ck("F2 ⛔ 成功时**真的换了起点**：`h0 = _ws.harness`",
       "h0 = _ws.harness" in cli, "")
    ck("F3 默认关（第 103 条纪律：新口径必须显式开启）",
       '"--warm-start", action="store_true"' in cli, "")
    ck("F4 失败打到 stderr 而不是静默回退",
       "本轮从裸种子开始" in cli, "")

    print()
    print("=" * 62)
    print("G 组 · 正对照（证明 C 组的「拒绝」是被那条字段拦的）")
    print("=" * 62)
    a = {"dev_env": "textscore", "heldout_env": "eventflow", "seed": "0",
         "envs": ("textscore", "eventflow", "stateflow", "scheduler")}
    ck("G1 同口径 ⇒ `_same_caliber` 为 True（C 组不是永远 False）",
       W._same_caliber(a, a)[0] is True, "")
    ck("G2 换一个字段 ⇒ 立刻翻 False（四个字段各管一处）",
       all(W._same_caliber(a, {**a, k: ["x"] if k == "envs" else "x"})[0]
           is False for k in ("dev_env", "heldout_env", "seed", "envs")), "")

    print()
    print("=" * 62)
    if FAILS:
        print(f"❌ 共 {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

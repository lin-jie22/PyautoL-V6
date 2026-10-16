#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：自指负载探针必须是**只读**的、且它的判据不许是恒真的。

═══ 为什么这条要守 ═══

`scripts/probe_self_reference.py` 回答的是"这套系统有多少产出在给自己造
机器"。这类探针有个特殊风险：**它的读数会被当成结论引用**。所以：

  · 扫错了（假阳）⇒ 会把正常接口报成死代码，人会去删有用的东西；
  · 扫空了（假阴）⇒ 会给出"很健康"的假读数，比没有读数更坏；
  · 它自己**不许**改仓库 —— 探针改状态就不再是取证工具。

本文件守五条：
  A **只读**：探针的源码里没有任何写仓库/写账本/跑进化的动作；
  A **判据只有一份**：扫描逻辑不许同时住在 `aether/selfref.py` 与
    `scripts/probe_self_reference.py` 里（两份 ⇒ 改一处漏一处，
    而漏的那处不报错）；
  B **判据会响**：注入一个人造的死接口 ⇒ 必须被抓到（反证）；
  C **判据不冤报**：被真实调用的接口 / 协议方法 / CLI 入口 ⇒ 不许报死；
  D 账本的机制/增益分类**与账本的真实事件名一致**（实测踩过两次：
    先写裸名而账本是 `eval.verdict`、改成全名后归一化又把它去掉了
    ⇒ 增益恒 0；一个"不报错但永远指向同一结论"的假仪表）。

⛔⛔ 本文件自己踩过的坑（2026-10-16 修，务必别再犯）：
   扫描判据 2026-10-15 从 `scripts/probe_self_reference.py` 搬进了
   `aether/selfref.py`，而本文件的加载目标**没跟着搬** ⇒ 它一直在测
   那个 25 行的 CLI 薄壳：A1/A2 对着空壳扫"没有写动作"恒真（绿），
   B/C/D 三组在第 93 行 **AttributeError 整组崩掉**（红）。
   ⇒ 一个"48 条契约全绿"的报告里，藏着一份只有 2 条恒真断言在跑的契约。
   落点必须与被测对象同源 —— 这是本文件存在的理由。

跑法（离线，秒级）：
    python tests/contract_self_reference_probe_test.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: ⛔⛔ **判据落点必须是 `aether/selfref.py`，不是 `scripts/` 里的薄壳**。
#:    2026-10-15 扫描判据从 `scripts/probe_self_reference.py` 搬进了
#:    `aether/selfref.py`（别的模块要拿它做判据 —— 见 `aether/cleanup.py`，
#:    "交付物住在 scripts/ 里、由脚本反向提供 API"是依赖方向反了）。
#:    搬完之后 `scripts/` 那边只剩一个 25 行的 CLI 薄壳，而本文件的
#:    `_load_probe()` **没跟着改** ⇒ 它加载的是那个薄壳：
#:      · `SOURCE_DIRS` / `CALL_SITE_DIRS` / `scan_dead_api` 全部取不到
#:        ⇒ B/C/D 三组在第 93 行就 AttributeError 崩掉（整组跑不到）；
#:      · 更糟的是**崩之前 A 组是绿的** —— A1/A2 扫一个 25 行空壳，
#:        "没有任何写动作"当然成立 ⇒ 恒真。
#:      ⇒ 一个"看起来有 20 条断言"的契约，实际只有 2 条恒真的在跑。
PROBE_PATH = ROOT / "aether" / "selfref.py"


def _load_probe():
    """把探针当模块加载（**独立于包路径**，便于 B 组改它的 ROOT）。

    ⛔ 为什么用 importlib 而不是 `import aether.selfref`：B 组要往
       `P.ROOT` 上**注入临时目录**做反证。直接 import 包内模块的话，
       改的是 `aether.selfref.ROOT` —— 而 `aether/cleanup.py` 持有的是
       同一个对象 ⇒ 反证会污染真实调用方（测试不得改写生产状态）。
    """
    spec = importlib.util.spec_from_file_location(
        "probe_self_reference_under_test", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    P = _load_probe()
    src = PROBE_PATH.read_text(encoding="utf-8")

    # ── A 组：只读 ───────────────────────────────────────────────
    print("A 组：探针只读（不改仓库、不写账本、不跑进化）")
    banned = ["ledger.append", ".write_text(", "os.replace", "shutil.move",
              "cli_main(", "cmd_evolve", "subprocess.run", "unlink(",
              "mkdir(", "store.save_run"]
    hit = [b for b in banned if b in src.split("def _main")[0]]
    ck("A1 探针主体没有任何写动作（append/write/replace/mkdir/subprocess…）",
       not hit, f"命中：{hit}")
    ck("A2 它读账本用的是**只读**文本读（不是 Ledger 对象）",
       "read_text(encoding=\"utf-8\"" in src and "Ledger(" not in src,
       "出现 Ledger( 说明可能顺手写了账")

    #: A3 判据只有一份。`aether/cleanup.py` 拿 `selfref` 当判据用 ⇒
    #:    scripts 那边再长出一整套 ⇒ 改一处漏一处，而漏的那处不报错。
    #:    ⛔ 判据读的是**函数名/常量名**，不是文件长度：薄壳"很薄"是结果，
    #:       不是判据 —— 扫长度的话，有人往薄壳里塞 200 行注释它照样绿。
    _shell = ROOT / "scripts" / "probe_self_reference.py"
    _MARKERS = ("SOURCE_DIRS", "CALL_SITE_DIRS", "DYNAMIC_ALLOW",
                "def scan_dead_api", "def scan_write_only",
                "def mechanism_vs_gain", "MECHANISM_KINDS", "GAIN_KINDS")
    _shell_src = _shell.read_text(encoding="utf-8") if _shell.is_file() else ""
    _dup = [k for k in _MARKERS if k in _shell_src]
    ck("A3b 薄壳文件真的在（A3 扫的是真文件，不是空串 ⇒ A3 不是恒真）",
       bool(_shell.is_file()) and bool(_shell_src), str(_shell))
    ck("A3 ⛔ 判据只有一份：scripts 薄壳里没有第二套扫描逻辑"
       "（两份 ⇒ 改一处漏一处，而漏的那处不报错）",
       not _dup, f"scripts 里又长出了：{_dup}")
    ck("A3x ⛔ 反证：往薄壳里注入一套扫描逻辑 ⇒ A3 转红（A3 不是恒真）",
       [k for k in _MARKERS if k in (_shell_src + "def scan_dead_api")]
       == ["def scan_dead_api"],
       "注入后判据竟没抓到")

    # ── B 组：判据会响（反证）────────────────────────────────────
    print("B 组：反证 —— 注入人造死接口必须被抓到")
    with tempfile.TemporaryDirectory() as td:
        fake_root = Path(td)
        (fake_root / "aether").mkdir()
        (fake_root / "tests").mkdir()
        (fake_root / "scripts").mkdir()
        #: 一个**确定零调用方**的公开函数 + 一个被调用的对照
        (fake_root / "aether" / "mod.py").write_text(
            "def totally_uncalled_api():\n"
            "    return 1\n"
            "\n"
            "def called_api():\n"
            "    return 2\n"
            "\n"
            "def _private_helper():\n"
            "    return 3\n", encoding="utf-8")
        (fake_root / "aether" / "user.py").write_text(
            "from .mod import called_api\n"
            "\n"
            "def go():\n"
            "    return called_api()\n", encoding="utf-8")
        old_root = P.ROOT
        old_dirs = (P.SOURCE_DIRS, P.CALL_SITE_DIRS)
        try:
            P.ROOT = fake_root
            P.SOURCE_DIRS = ("aether",)
            P.CALL_SITE_DIRS = ("aether", "tests", "scripts")
            got = P.scan_dead_api()
        finally:
            P.ROOT = old_root
            P.SOURCE_DIRS, P.CALL_SITE_DIRS = old_dirs
        names = {d["name"] for d in got["dead_api"]}
        ck("B1 ⛔ 零调用方的公开函数被抓到（判据不是恒真）",
           "totally_uncalled_api" in names, str(sorted(names)))
        ck("B2 ⛔ 被真实调用的函数不冤报",
           "called_api" not in names, str(sorted(names)))
        ck("B3 下划线私有函数不进这份报告（口径是「公开接口」）",
           "_private_helper" not in names, str(sorted(names)))

    # ── C 组：真实仓库上不许冤报关键入口 ─────────────────────────
    print("C 组：真实仓库上，已知被调用/协议的东西不许被报为死")
    real = P.scan_dead_api()
    real_names = {d["name"] for d in real["dead_api"]}
    for must_not in ("to_dict", "from_dict", "main", "identity", "fingerprint",
                     "probe", "run", "report"):
        ck(f"C 不冤报 {must_not}()", must_not not in real_names)
    ck("C5 报告给出了「这是下界」的口径说明（读数不许被当成全量）",
       "下界" in real["note"], real["note"][:60])

    # ── D 组：账本分类与真实事件名一致 ───────────────────────────
    print("D 组：机制/增益分类与账本真实事件名一致（假仪表的守门）")
    ck("D1 `_bare()` 会去掉前缀", P._bare("eval.verdict") == "verdict",
       P._bare("eval.verdict"))
    ck("D2 增益表认得 `eval.verdict`（归一化**之后**）",
       P._bare("eval.verdict") in P.GAIN_KINDS)
    ck("D2x ⛔ 反证：不归一化就认不得（归一化是承重的，不是装饰）",
       "eval.verdict" not in P.GAIN_KINDS, f"{sorted(P.GAIN_KINDS)}")
    ck("D3 机制表认得 `harness.commit` / `cap.candidate`（归一化之后）",
       P._bare("harness.commit") in P.MECHANISM_KINDS
       and P._bare("cap.candidate") in P.MECHANISM_KINDS)
    ck("D3x ⛔ 反证：不归一化 ⇒ 机制侧也认不得（两侧同一条纪律）",
       "harness.commit" not in P.MECHANISM_KINDS)

    #: ⛔⛔ **合成账本，不读生产账本**。
    #:    原实现读 `ROOT / "data" / "ledger.jsonl"` 并对它的形状下断言
    #:    （"增益数 > 0""按会话分组 ≥3 组"）。那有两个问题：
    #:      ① 契约变成非确定的 —— 生产账本的形状会随每次 evolve 漂移，
    #:         于是"今天绿"与"这个契约在守什么"是两件事；
    #:      ② 更糟的是原实现**根本没跑到那里**（薄壳上没有 `mechanism_vs_gain`
    #:         ⇒ 第 93 行就崩了），所以这两个耦合从未真正生效过 —— 一旦
    #:         修好加载目标，它们立刻会变成"看生产账本心情"的红灯。
    #:    ⇒ 合成一个**形状已知**的账本：断言针对探针的判据，不针对数据的运气。
    def _write_ledger(path, events):
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n"
                                for e in events), encoding="utf-8")
        return path

    def _ev(kind, **payload):
        return {"kind": kind, "payload": payload}

    with tempfile.TemporaryDirectory() as td:
        lg_path = Path(td) / "ledger.jsonl"
        events = []
        for sid in ("s1", "s2", "s3"):
            for _ in range(2):
                events.append(_ev("eval.verdict", sid=sid, reward=1.0))
        #: ⛔ 每条都带 sid（"全带 sid ⇒ 3 组"与 8b 的"缺 sid ⇒ 单独成桶"
        #:    是两件事，不许混在同一个夹具里 —— 混了就分不清红的是哪一条）。
        events += [_ev("harness.commit", gain=0.5, sid="s1"),
                   _ev("harness.commit", gain=1.0, sid="s1"),
                   _ev("harness.revert", sid="s2"),
                   _ev("evolve.noop", sid="s2"),
                   _ev("gen.probe", verdict="generalize", sid="s3")]
        lg = P.mechanism_vs_gain(_write_ledger(lg_path, events))
        ck("D4 ⛔ 增益侧真的数到了事件（判据没被架空）",
           lg.get("gain_total", 0) == 6, f"gain_total={lg.get('gain_total')}")
        ck("D5 机制侧真的数到了事件（两侧用的是**同一套**归一化）",
           lg.get("mech_total", 0) == 5, f"mech_total={lg.get('mech_total')}")
        ck("D6 ⛔ `commit_yield` 被标记为**废读数**"
           "（revert 无 gain 键 ⇒ 它恒等于 1.0，是循环论证）",
           lg.get("commit_yield_is_vacuous") is True,
           str(lg.get("commit_yield")))
        ck("D7 ⛔ 账本上无法把增益与机制对齐到同一轮次 ⇒ 必须**如实说**",
           lg.get("verdict_has_round_no") is False
           and "无法回答" in lg.get("join_note", ""),
           lg.get("join_note", "")[:60])
        ck("D8 按会话分组的比值有读数（能算的那种近似）",
           len(lg.get("by_sid") or []) == 3
           and lg.get("mech_gain_ratio") is not None,
           f"by_sid={len(lg.get('by_sid') or [])} "
           f"ratio={lg.get('mech_gain_ratio')}")

        #: ⛔ 无 sid 的事件必须**单独成桶**，不许被并进某个具名会话：
        #:    并进去 ⇒ 那个会话的机制/增益比值被**别人的**事件污染，
        #:    而污染在输出上完全看不出来（与"缺会话归属 ⇒ 跨运行串味"
        #:    是同一个坑，只是这次发生在读数侧）。
        lg_nosid = P.mechanism_vs_gain(_write_ledger(
            Path(td) / "no_sid.jsonl",
            [_ev("eval.verdict", sid="s1", reward=1.0),
             _ev("harness.commit", gain=1.0)]))
        grp = {d["sid"]: d for d in (lg_nosid.get("by_sid") or [])}
        ck("D8b ⛔ 无 sid 的事件单独成桶（不并进具名会话 ⇒ 不污染它的比值）",
           set(grp) == {"s1", "(no-sid)"}
           and grp["s1"]["mech"] == 0 and grp["s1"]["gain"] == 1,
           f"{lg_nosid.get('by_sid')}")

        #: 反证 1：账本上只有机制事件 ⇒ 增益侧必须是 **0 条**，且比值必须是
        #:         None —— **不是 0.0**：0.0 会伪装成"有读数，只是很小"。
        lg_nogain = P.mechanism_vs_gain(_write_ledger(
            Path(td) / "no_gain.jsonl",
            [_ev("harness.commit", gain=0.5), _ev("gen.probe",
                                                  verdict="overfit")]))
        ck("D9 ⛔ 反证：没有增益事件 ⇒ gain_total=0（D4 不是恒真）",
           lg_nogain.get("gain_total") == 0,
           f"gain_total={lg_nogain.get('gain_total')}")
        ck("D9b ⛔ 反证：没有增益 ⇒ 比值是 **None 而不是 0.0**"
           "（0.0 会伪装成有读数）",
           lg_nogain.get("mech_gain_ratio") is None,
           f"ratio={lg_nogain.get('mech_gain_ratio')!r}")

        #: 反证 2：`commit_yield_is_vacuous` 这个"废读数"标记本身要有判别力 ——
        #:        账本上出现一条**没有 gain 键**的 commit ⇒ 它就不该再被标废。
        lg_nogainfield = P.mechanism_vs_gain(_write_ledger(
            Path(td) / "no_field.jsonl",
            [_ev("harness.commit", gain=0.5), _ev("harness.commit")]))
        ck("D10 ⛔ 反证：出现无 gain 键的 commit ⇒ 不再判为废读数"
           "（D6 因此不是恒真）",
           lg_nogainfield.get("commit_yield_is_vacuous") is False,
           f"vacuous={lg_nogainfield.get('commit_yield_is_vacuous')}")

        #: 反证 3：账本不存在 ⇒ present=False，**不许假通过**（原实现在这里
        #:        写了一句 `ck(..., True, ...)` ⇒ 账本没了它也绿）。
        lg_missing = P.mechanism_vs_gain(Path(td) / "nope.jsonl")
        ck("D11 ⛔ 账本不存在 ⇒ present=False 并给出说明（不假装通过）",
           lg_missing.get("present") is False
           and bool(lg_missing.get("note")), str(lg_missing))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：探针只读、判据会响、不冤报、分类与账本同源")
    return 0


if __name__ == "__main__":
    sys.exit(main())

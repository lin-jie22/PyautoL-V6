#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**机器自审安装**（r60 G）—— 人审拿掉之后，替它顶上去的是什么。

`caps_candidate` 原本写着「人批准（`--yes`）+ 人执行安装」。这次把两步都
交给机器，本文件守的**不是"能不能装"**，而是「换裁决者时有没有把闸一起
丢掉」（第 132 条）：

  A 回滚能力   —— 推不出卸载命令的命令，**不许**自动执行
  B 五道判据   —— 无 argv / 与清单不符 / 形态不许 / 探针坏 / 闸红 / 已装
  C 执行与回滚 —— rc 不是判据，**探针读数**才是；装不上必须还原
  D 档案被改   —— 档案是缓存，不许它决定系统跑什么命令
  E CLI 出口   —— 拒绝/演练/不认识 三种出口可分辨，且 CLI 不能喂假探针
  F 零影响     —— 装工具不许影响任何冻结判据

⛔ 全部离线：不跑真的 `brew install`，`run` / `probe_fn` 全部用假实现注入。
   「真跑过一次」留给生产读数，不留给契约（契约要能在没网的机器上复现）。

用法：
    python tests/contract_install_auto_test.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


class Script:
    """假探针 / 假执行器：按**第几次调用**返回不同读数。

    ⛔ 用"第几次"而不是"看点什么"：被测代码的判据顺序是本契约要钉住的
       东西，用序号才能精确证明"它在哪一步读了探针"。
    """

    def __init__(self, probes, results=None):
        self.probes = list(probes)
        self.results = list(results or [])
        self.n_probe = 0
        self.calls = []

    # 探针：第 n 次调用返回 probes[min(n-1, len-1)]
    def probe(self, tool):
        from aether import discover as D
        i = min(self.n_probe, len(self.probes) - 1)
        self.n_probe += 1
        st = self.probes[i]
        if isinstance(st, D.Probe):
            return st
        #: ⛔ 这里必须**先分 str**：`"missing"[0]` 是 `"m"`，
        #:    会把状态悄悄变成一个不存在的三态值，于是闸红，
        #:    而读数是"探针复核不一致"—— 看着像被测代码的问题（第 24 条：
        #:    某维度异常先怀疑量具）。
        if isinstance(st, str):
            return D.Probe(tool=getattr(tool, "name", "?"), status=st,
                           detail="（契约假读数）")
        return D.Probe(tool=getattr(tool, "name", "?"), status=st[0],
                       path=st[1] if len(st) > 1 else "",
                       detail=st[2] if len(st) > 2 else "")

    # 执行器：第 n 次调用返回 results[min(n-1, len-1)]
    def run(self, argv, *, timeout_s):
        self.calls.append(list(argv))
        i = min(len(self.calls) - 1, len(self.results) - 1)
        r = self.results[i] if self.results else (0, "ok", "", False)
        return r


def main():
    from aether import caps_candidate as CC
    from aether import discover as D
    from aether import install_auto as IA

    T_R2 = next(t for t in D.KNOWN_TOOLS if t.name == "radare2")

    def mk_cand(tool=None, install_argv=None, verify_hint=None,
                kind="missing_tool"):
        tool = tool or T_R2
        pr = D.Probe(tool=tool.name, status="missing", detail="PATH 里没有")
        c = CC.from_probe(pr, install_argv=(list(install_argv)
                                           if install_argv is not None
                                           else list(tool.install_argv)))
        assert c is not None, "构造失败：missing 应当产出候选"
        if verify_hint is not None:
            c.verify_hint = verify_hint
        c.kind = kind
        return c

    # ── A 组：回滚能力（推不出卸载 ⇒ 不许自动装）───────────────────
    print("A 组：回滚能力 —— 没有卸载路径的命令不许自动执行")
    ck("A1 brew install X ⇒ brew uninstall X",
       IA.uninstall_argv(["brew", "install", "radare2"])
       == ["brew", "uninstall", "radare2"],
       str(IA.uninstall_argv(["brew", "install", "radare2"])))
    #: ⛔ `--cask` 丢了会去 core 里找同名 formula ⇒ 找不到 ⇒ 报错
    #:    会被读成"回滚失败"（而那其实是"回滚命令写错了"）。
    ck("A2 `--cask` 原样带过去（丢了会找错名字）",
       IA.uninstall_argv(["brew", "install", "--cask", "ghidra"])
       == ["brew", "uninstall", "--cask", "ghidra"],
       str(IA.uninstall_argv(["brew", "install", "--cask", "ghidra"])))
    ck("A3 pip install X ⇒ 推不出来（会跑 setup.py，且不是一一映射）",
       IA.uninstall_argv(["pip", "install", "x"]) == [])
    ck("A4 brew install a b ⇒ 推不出来（两个包名，不知道卸哪个）",
       IA.uninstall_argv(["brew", "install", "a", "b"]) == [])
    ck("A5 curl 下载类 ⇒ 推不出来", IA.uninstall_argv(["curl", "-o", "x"]) == [])

    # ── B 组：五道判据 ─────────────────────────────────────────────
    print("B 组：五道判据 —— 逐条独立触发，每条都必须能单独拒绝")
    s = Script(["missing"])
    v = IA.machine_install(mk_cand(install_argv=[]), T_R2,
                           run=s.run, probe_fn=s.probe)
    ck("B1 没有 install_argv ⇒ unverified（不是 rejected：没东西可执行）",
       v.state == IA.UNVERIFIED and not s.calls, f"{v.state} calls={s.calls}")

    #: ⛔ 这条是整套设计的地基：档案是**缓存**，可被手改。
    #:    执行档案里的 argv = 谁改了文件谁决定系统跑什么。
    bad = mk_cand(install_argv=["brew", "install", "radare2", "--HEAD"])
    s = Script(["missing"])
    v = IA.machine_install(bad, T_R2, run=s.run, probe_fn=s.probe)
    ck("B2 与清单声明不一致 ⇒ rejected，且两份命令都写进理由",
       v.state == IA.REJECTED
       and any("不一致" in r for r in v.reasons)
       and any("--HEAD" in r for r in v.reasons),
       f"{v.state} {v.reasons[:2]}")
    ck("B2b 不一致时**一条命令都没执行**", not s.calls, str(s.calls))

    pip_tool = D.Tool(name="radare2", exe="r2",
                      install_argv=("pip", "install", "radare2"))
    s = Script(["missing"])
    v = IA.machine_install(mk_cand(install_argv=["pip", "install", "radare2"]),
                           pip_tool, run=s.run, probe_fn=s.probe)
    ck("B3 `pip install` 在提示白名单里，但**不在自动执行名单** ⇒ rejected",
       v.state == IA.REJECTED
       and any("自动执行" in r for r in v.reasons), f"{v.state} {v.reasons[:1]}")
    ck("B3b 形态被拒时零执行", not s.calls, str(s.calls))

    heads = {p[0] for p in IA.AUTO_LAUNCHERS}
    wide = {"pip", "curl", "git", "python3", "npm"}
    ck("B4 两份白名单管的不是同一件事（提示白名单 ⊃ 自动执行白名单）",
       heads < set(CC.ALLOWED_LAUNCHERS) and wide <= set(CC.ALLOWED_LAUNCHERS)
       and not (wide & heads),
       f"heads={heads}")

    s = Script([("error", "", "权限不足")])
    v = IA.machine_install(mk_cand(), T_R2, run=s.run, probe_fn=s.probe)
    ck("B5 探针 error ⇒ unverified（那是「去修」不是「去装」）",
       v.state == IA.UNVERIFIED and not s.calls, f"{v.state}")

    s = Script([("found", "/opt/homebrew/bin/r2", "")])
    v = IA.machine_install(mk_cand(), T_R2, run=s.run, probe_fn=s.probe)
    ck("B6 本来就在 ⇒ ok + noop，且**零执行**",
       v.state == IA.OK and v.noop and not s.calls, f"{v.state} noop={v.noop}")
    ck("B6b 「无事可做」与「刚装上」可分辨（noop 单独记）", v.noop is True)

    s = Script(["missing"])
    v = IA.machine_install(mk_cand(verify_hint=""), T_R2,
                           run=s.run, probe_fn=s.probe)
    ck("B7 四道闸未过 ⇒ rejected 且零执行（闸不是摆设）",
       v.state == IA.REJECTED and not s.calls
       and any("未通过" in r for r in v.reasons), f"{v.state} {v.reasons[:1]}")

    # ── C 组：执行与回滚 ───────────────────────────────────────────
    print("C 组：执行与回滚 —— rc 不是判据，探针读数才是")
    s = Script(["missing", ("found", "/opt/homebrew/bin/r2", "")],
               [(0, "==> Pouring radare2", "", False)])
    cand = mk_cand()
    v = IA.machine_install(cand, T_R2, run=s.run, probe_fn=s.probe)
    ck("C1 装成 + 复验探到 ⇒ ok，状态 installed，approved_by=auto",
       v.state == IA.OK and cand.state == CC.INSTALLED
       and cand.approved_by == IA.AUTO_BY,
       f"{v.state} {cand.state} {cand.approved_by}")
    ck("C1b 装了 1 次（不含卸载）", len(s.calls) == 1, str(s.calls))
    ck("C1c 命令原文进证据（可复核「谁让它跑了什么」）",
       v.evidence.get("exec", {}).get("argv") == ["brew", "install", "radare2"],
       str(v.evidence.get("exec")))

    s = Script(["missing", "missing", "missing"],
               [(0, "", "", False), (0, "Uninstalling", "", False)])
    cand = mk_cand()
    v = IA.machine_install(cand, T_R2, run=s.run, probe_fn=s.probe)
    ck("C2 rc=0 但探不到 ⇒ rejected（rc 不是判据）且已自动卸载",
       v.state == IA.REJECTED and v.rolled_back
       and s.calls[-1] == ["brew", "uninstall", "radare2"],
       f"{v.state} rb={v.rolled_back} calls={s.calls}")
    #: ⛔⛔ 与 r60 D 同族的一条：**「这次没装成」不许撤销批准**。
    #:    批准说的是"可以装"，不是"装得上"；一次网络抖动就打回 rejected，
    #:    等于把批准吃掉，而界面上和"从没批过"长得一样。
    ck("C2b 装失败 ⇒ 状态退回 approved（批准仍在），不是 rejected",
       cand.state == CC.APPROVED and cand.approved_by == IA.AUTO_BY,
       f"{cand.state} {cand.approved_by}")

    s = Script(["missing", "missing", "missing"],
               [(1, "", "Error: failed", False), (0, "", "", False)])
    v = IA.machine_install(mk_cand(), T_R2, run=s.run, probe_fn=s.probe)
    ck("C3 装失败也清一遍（防留半截），并记 stderr 尾部",
       v.state == IA.REJECTED and len(s.calls) == 2
       and "failed" in (v.evidence["exec"]["stderr_tail"] or ""),
       f"{v.state} calls={len(s.calls)}")

    s = Script(["missing", "missing", ("found", "/x/r2", "")],
               [(0, "", "", False), (1, "", "No such keg", False)])
    v = IA.machine_install(mk_cand(), T_R2, run=s.run, probe_fn=s.probe)
    ck("C4 卸载后仍探得到 ⇒ rolled_back=False 且明说「可能已被改动」",
       v.state == IA.REJECTED and not v.rolled_back
       and any("可能已被改动" in r for r in v.reasons),
       f"{v.state} rb={v.rolled_back}")

    s = Script(["missing", "missing", "missing"],
               [(124, "", "", True), (0, "", "", False)])
    v = IA.machine_install(mk_cand(), T_R2, run=s.run, probe_fn=s.probe)
    ck("C5 超时 ⇒ 仍走复验+回滚，timed_out 进证据",
       v.state == IA.REJECTED and v.rolled_back
       and v.evidence["exec"]["timed_out"] is True,
       f"{v.state} rb={v.rolled_back}")

    s = Script(["missing"])
    v = IA.machine_install(mk_cand(), T_R2, dry_run=True,
                           run=s.run, probe_fn=s.probe)
    ck("C6 演练 ⇒ ok + dry_run，**零执行**（环境零改动）",
       v.state == IA.OK and v.dry_run and not s.calls,
       f"{v.state} calls={s.calls}")

    # ── D 组：档案被改（地基）──────────────────────────────────────
    print("D 组：档案被改 —— 缓存不许决定系统跑什么命令")
    #: ⛔⛔ 这条断言的第一版写的是 `curl -sS http://…`，**测错了位置**：
    #:    curl 形态本来就会被 ③（执行白名单）拒掉 ⇒ 删掉 ② 它也照样红
    #:    不了，于是"以为测了 ②，其实测的是 ③"（第 130 条的配套：
    #:    判据别写弱，否则说得通但不真的答案能通关）。
    #:    真正只可能被 ② 兜住的形态是：**同样以 brew install 开头，但包名
    #:    换了** —— 那才是一个被篡改的缓存最可能的形态（同名工具、不同包），
    #:    而且 ③④ 都会放它过去。
    evil = mk_cand(install_argv=["brew", "install", "evilformula"])
    s = Script(["missing"])
    v = IA.machine_install(evil, T_R2, run=s.run, probe_fn=s.probe)
    ck("D1 档案被改成装另一个包 ⇒ rejected（只有 ② 能拦住它）",
       v.state == IA.REJECTED and not s.calls
       and any("evilformula" in r for r in v.reasons),
       f"{v.state} {v.reasons[:1]}")

    s = Script(["missing", ("found", "/opt/homebrew/bin/r2", "")],
               [(0, "", "", False)])
    v = IA.machine_install(mk_cand(), T_R2, run=s.run, probe_fn=s.probe)
    ck("D2 一致时能走到执行（正向对照：不是「一律拒绝」）",
       v.state == IA.OK and len(s.calls) == 1, f"{v.state} calls={len(s.calls)}")

    #: ⛔⛔ 第 ② 道闸**真正**唯一的用武之地：清单里**没有** argv 的工具
    #:    （本机 `objdump` / `otool`），`merge()` 会把档案里的旧 argv
    #:    回填进来 —— 那时它是唯一挡板。没有这条，第 ② 道闸就只是
    #:    "清单自己跟自己比"，永远恒真（第 50 条：反证不许同义反复）。
    t_nogr = next(t for t in D.KNOWN_TOOLS if t.name == "objdump")
    s = Script(["missing"])
    v = IA.machine_install(mk_cand(tool=t_nogr,
                                   install_argv=["brew", "install", "binutils"]),
                           t_nogr, run=s.run, probe_fn=s.probe)
    ck("D3 清单没声明 argv 而档案有 ⇒ rejected（回填路径被这道闸堵住）",
       v.state == IA.REJECTED and not s.calls
       and any("不一致" in r for r in v.reasons),
       f"{v.state} {v.reasons[:1]}")

    #: ⛔⛔ 实测账（写在这里，别再犯）：这一组原本用「改档案 argv」来验第 ②
    #:    道闸，结果 CLI 走的是 `_mk()` —— 它对 missing 的工具**用清单重建
    #:    候选**，档案 argv 被整个丢掉 ⇒ 契约实际触发了**真的** `brew install
    #:    radare2`（被本文件的 120s 超时杀掉，复查环境零改动）。
    #:    ⇒ 教训（第 60 条的同族）：**验证一道闸之前，先确认它前面没有别的闸
    #:    把它短路掉**，否则你测的是另一条路。
    #:    ⇒ 所以：API 层才验"档案被改"（D1/D3），CLI 层**一律只走演练**。

    # ── E 组：CLI 出口 ────────────────────────────────────────────
    print("E 组：CLI 出口 —— 演练 / 不认识 / 记账 三种可分辨")
    tmp = Path(tempfile.mkdtemp(prefix="v6_ia_"))
    try:
        (tmp / "data").mkdir(parents=True, exist_ok=True)
        cand = mk_cand()
        CC.save((tmp / "data" / "candidates.json"), {cand.tool: cand})
        env = {**__import__("os").environ, "PYTHONPATH": str(ROOT)}

        def cli(*extra):
            return subprocess.run(
                [sys.executable, "-B", "-m", "aether.cli", "--root", str(tmp),
                 "caps", "candidates", "auto", *extra],
                capture_output=True, text=True, cwd=str(ROOT), env=env,
                timeout=120)

        #: ⛔ 只跑 `--dry-run`：契约**不许**在这台机器上真装一次东西。
        p = cli("--tool", cand.tool, "--dry-run")
        out = (p.stdout or "") + (p.stderr or "")
        ck("E1 演练 rc=0，且输出里**没有**「执行：」行（命令没跑）",
           p.returncode == 0 and "执行：" not in out,
           f"rc={p.returncode} {out[-200:]}")
        after = json.loads((tmp / "data" / "candidates.json").read_text("utf-8"))
        st = {c["tool"]: c["state"] for c in after["candidates"]}
        ck("E1b 演练后档案状态不变（没裁决就不许改状态、不许写 approved_by）",
           st.get(cand.tool) == cand.state
           and not [c for c in after["candidates"] if c["approved_by"]],
           str([(c["tool"], c["state"], c["approved_by"])
                for c in after["candidates"]]))

        p2 = cli("--tool", "no_such_tool_xyz")
        ck("E2 不认识的工具 ⇒ rc=2（与「被拒」分开）", p2.returncode == 2,
           f"rc={p2.returncode}")

        #: ⛔ 记账可复核：账本里要有 `cap.install_auto`，且带着**完整结论**
        #:    （命令原文 / 探针前后读数）—— 只记"状态变了"等于事后无从归因。
        from aether.ledger import Ledger
        from aether.paths import paths as _paths
        evs = [e for e in Ledger(_paths(tmp)["ledger"]).events()
               if e.kind == "cap.install_auto"]
        pay = evs[-1].payload if evs else {}
        ck("E3 账本记了 cap.install_auto，且带命令原文与探针读数",
           bool(evs) and pay.get("evidence", {}).get("install_argv")
           and "probe_before" in pay.get("evidence", {}),
           f"n={len(evs)} keys={sorted(pay)[:6]}")

        #: ⛔ 形自证：CLI **不许**传 `probe_fn` / `run` —— 从外部喂一个
        #:    假探针等于给系统一个"伪造装成功"的入口。
        src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
        i = src.find("machine_install(c, tool")
        seg = src[max(0, i - 200):i + 300] if i >= 0 else ""
        ck("E4 CLI 调用 machine_install 时不传 probe_fn / run（形自证）",
           bool(seg) and "probe_fn" not in seg and " run=" not in seg,
           seg[-120:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── F 组：零影响与身份 ────────────────────────────────────────
    print("F 组：零影响 —— 装工具不许影响任何冻结判据")
    from aether import autonomy as AU
    ck("F1 两份「机器批准」的字面值不许漂移（AUTO_BY 同源）",
       IA.AUTO_BY == AU.AUTO_BY, f"{IA.AUTO_BY} vs {AU.AUTO_BY}")
    for name, why in (("nongression.py", "冻结判据不读它"),
                      ("evolution.py", "自演化循环不读它")):
        txt = (ROOT / "aether" / name).read_text(encoding="utf-8")
        ck(f"F2 {name} 不 import install_auto（{why}）",
           "install_auto" not in txt)
    ck("F3 install_auto 不 import evolution / nongression（单向依赖）",
       "import evolution" not in (ROOT / "aether" / "install_auto.py")
       .read_text(encoding="utf-8"))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：机器能自己装，但装什么由清单定、装错能退回去")
    return 0


if __name__ == "__main__":
    sys.exit(main())

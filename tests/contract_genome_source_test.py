#!/usr/bin/env python3
"""契约 · 基因 target 路径的**唯一来源**（r35）。

背景（2026-10-14 实测取证）：
    `providers.TemplateProposer` 手搓 `Gene(op=..., target=f"L3.skills.{n}")`，
    而 `genome.gene_add_skill` 等四个构造器写了**同形**的一份。两处互不相干：
    改层名 / 改路径格式时必漏一处，且漏的那处**不报错** —— `apply_gene`
    对未知层抛错，但对「已知层的错误子键」放行 ⇒ 产物落在没人看的地方。

本契约守三件事：
    A 组  静态闸：target 字面量只准出现在 genome 的 `_t_*` 里（AST 扫描，
                  **跳过 docstring 与注释** —— 只断言行，不断言文案）。
    B 组  行为闸：提议器产出的 target 与构造器产出的**逐字相同**。
    C 组  ⛔ 反证：短路 `_t_skill` ⇒ 提议器产物必须跟着变（证明真同源，
                  不是「两边恰好写得一样」）。
    D 组  语义闸：`gene_route`(set) 与 `gene_route_append`(append) 不许互相
                  替换 —— 路由是链，set 覆盖会顶掉旧能力。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aether import genome as G                                    # noqa: E402
from aether.genome import Harness, apply_gene, parse_route        # noqa: E402
from aether.providers import TemplateProposer                     # noqa: E402

FAILED: list = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


AETHER = pathlib.Path(__file__).resolve().parent.parent / "aether"
PREFIXES = ("L3.skills.", "L1.routing.", "L4.memory_policy.")
EXACT = ("L2.tools",)


def _const_str(node) -> "str | None":
    """把字符串常量 / f-string 的**静态部分**取出。f-string 的插值位记
    为 `\\x00`，这样 `f"L3.skills.{n}"` 仍能匹配前缀。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("\x00")
        return "".join(parts)
    return None


def scan_target_literals():
    """返回 [(文件名, 行号, 所在函数名, 字面量)] —— 只扫**代码里的字符串**，
    注释不进 AST，docstring 显式跳过。"""
    hits = []
    for f in sorted(AETHER.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = fn.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)):
                body = body[1:]          # 去掉 docstring
            for n in ast.walk(ast.Module(body=body, type_ignores=[])):
                s = _const_str(n)
                if s is None:
                    continue
                s0 = s.split("\x00")[0]
                if s0.startswith(PREFIXES) or s in EXACT:
                    hits.append((f.name, n.lineno, fn.name, s))
    return hits


#: ⛔ 造数据也要守被测对象的闸：`TemplateProposer.skills` 默认是**空字典**，
#:    不传技能 ⇒ `missing_skill` 分支的 for 循环一次都不跑 ⇒ 产物空集
#:    ⇒ 断言会变成空集恒真（本项目已三次栽在「空集 ⇒ 恒真」上）。
SKILLS = {"s_ctrl": "def s_ctrl(): pass", "s_lit": "def s_lit(): pass"}


def _proposer() -> "TemplateProposer":
    return TemplateProposer(skills=dict(SKILLS), route_key="repair")


def _proposed_genes(kind: str, **wk):
    w = {"kind": kind}
    w.update(wk)
    caps = _proposer().propose(w, Harness(), n=2)
    return [(g.op, g.target, g.value) for c in caps for g in c.genes]


print("=" * 62)
print("契约 · 基因 target 唯一来源")
print("=" * 62)

# ── A 组：静态闸 ────────────────────────────────────────────────
print("\n[A] 静态闸：target 字面量的落点")
hits = scan_target_literals()
files = sorted({h[0] for h in hits})
fns = sorted({f"{h[0]}:{h[2]}" for h in hits})
ck("A1 target 字面量只出现在 genome.py（提议器等不得自带一份）",
   files == ["genome.py"], str(files))
ck("A2 且只出现在 `_t_*` 这四个构造点里",
   all(h[2].startswith("_t_") for h in hits), str(fns))
ck("A3 ⛔ 反证用：扫描器确实扫到了东西（不是空集 ⇒ A1/A2 不是恒真）",
   len(hits) >= 4 and len(fns) == 4, f"hits={len(hits)} fns={fns}")
ck("A4 四个 `_t_*` 齐备（少一个 ⇒ 有人绕过它另写了一份）",
   fns == ["genome.py:_t_policy", "genome.py:_t_route",
           "genome.py:_t_skill", "genome.py:_t_tools"], str(fns))

# ── B 组：行为同源 ──────────────────────────────────────────────
print("\n[B] 行为闸：提议器产物 == 构造器产物（逐字）")
sk = _proposed_genes("missing_skill", topic="control_flow")
ck("B1 提议器产出了技能基因（否则 B2 是空集恒真）", bool(sk), str(sk[:2]))
if sk:
    op, tgt, _v = sk[0]
    ck("B2 技能 target 与 gene_add_skill 逐字相同（同一个构造点）",
       tgt == G.gene_add_skill("s_ctrl", SKILLS["s_ctrl"]).target, f"{tgt!r}")
    ck("B2b 且落在 L3.skills.* 层（set）",
       tgt.startswith("L3.skills.") and op == "set", f"{op} {tgt}")
    rt = [g for g in sk if g[1].startswith("L1.routing.")]
    ck("B2c 路由 target 与 gene_route_append 逐字相同",
       bool(rt) and rt[0][1] == G.gene_route_append("repair", "s_ctrl").target,
       str(rt[:1]))
tl = _proposed_genes("missing_tool", tool="read_file")
ck("B3 工具 target 与 gene_tool 逐字相同",
   bool(tl) and tl[0][1] == G.gene_tool("read_file").target, str(tl))
rc = _proposed_genes("recall_miss")
ck("B4 调参 target 与 gene_tune 逐字相同",
   bool(rc) and rc[0][1] == G.gene_tune("recall_topk", 2).target, str(rc[:1]))

# ── C 组：反证（真同源，不是恰好写得一样）─────────────────────
print("\n[C] ⛔ 反证：短路 `_t_skill` ⇒ 提议器必须跟着变")
base = _proposed_genes("missing_skill", topic="control_flow")
orig = G._t_skill
try:
    G._t_skill = lambda name: f"L9.HIJACKED.{name}"      # 事故版
    broken = _proposed_genes("missing_skill", topic="control_flow")
    ck("C1 改 genome 一处 ⇒ 提议器产物跟着变（证明是同一份代码）",
       bool(broken) and broken != base and "HIJACKED" in broken[0][1],
       str(broken[:1]))
finally:
    G._t_skill = orig
ck("C2 还原后产物回到原样（事故版没残留）",
   _proposed_genes("missing_skill", topic="control_flow") == base)

# ── D 组：set / append 不许互相替换 ─────────────────────────────
print("\n[D] 语义闸：路由是链，set 覆盖 ⇒ 顶掉旧能力")
ck("D1 gene_route 是 set，gene_route_append 是 append（两者不可互换）",
   G.gene_route("repair", "s2").op == "set"
   and G.gene_route_append("repair", "s2").op == "append",
   f"{G.gene_route('repair','s2').op} / {G.gene_route_append('repair','s2').op}")
ck("D1b 两者 target 相同（差在 op，不差在落点）",
   G.gene_route("repair", "s2").target == G.gene_route_append("repair", "s2").target)

h0 = Harness()
h1 = apply_gene(h0, G.gene_route("repair", "sA"))
h2 = apply_gene(h1, G.gene_route_append("repair", "sB"))
h3 = apply_gene(h2, G.gene_route_append("repair", "sC"))
chain_app = parse_route(h3.routing.get("repair", ""))
h2b = apply_gene(h1, G.gene_route("repair", "sB"))     # set 覆盖
chain_set = parse_route(h2b.routing.get("repair", ""))
ck("D2 append 可叠加（sA;sB;sC 都在链上）",
   chain_app == ("sA", "sB", "sC"), str(chain_app))
ck("D3 ⛔ 反证：set 覆盖 ⇒ 旧能力被顶掉（只剩最后一条）",
   chain_set == ("sB",), str(chain_set))
ck("D4 提议器用的必须是 append（否则闭环停滞 —— 实测 easy +1.0→−1.0）",
   any(g.op == "append" and g.target.startswith("L1.routing.")
       for g in _proposer().propose(
           {"kind": "missing_skill", "topic": "t"}, Harness(), n=1)[0].genes))

# ── E 组：构造器自身的闸仍在 ───────────────────────────────────
print("\n[E] 构造器原有闸未被动过")
try:
    G.gene_tune("k", "not-a-number")
    ck("E1 gene_tune 对非数字 delta 仍然抛错", False, "没抛")
except ValueError as e:
    ck("E1 gene_tune 对非数字 delta 仍然抛错", "数字" in str(e), str(e)[:50])
ck("E2 gene_tune 接受 int（未被误伤）", G.gene_tune("k", 2).value == 2)
ck("E3 四个构造器都能产出可应用的基因（apply_gene 不抛）",
   all(apply_gene(Harness(), g) is not None
       for g in (G.gene_add_skill("s", "def s(): pass"),
                 G.gene_route("repair", "s"),
                 G.gene_route_append("repair", "s"),
                 G.gene_tool("read_file"), G.gene_tune("recall_topk", 1))))

print("\n" + "=" * 62)
if FAILED:
    print(f"❌ {len(FAILED)} 项未通过：" + "; ".join(FAILED))
    sys.exit(1)
print("✅ 全部通过")

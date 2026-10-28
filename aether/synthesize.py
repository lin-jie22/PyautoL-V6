"""⛔⛔ **判据驱动生成补丁层** —— V6 此前**只有「对齐」，没有「合成」**。

═══ 这一层为什么必须存在（README 158 的取证结论）═══

五个 `revert_*(broken, base)` 的语义是「按位置**对齐**回 base」：

    base == broken  ⇒  五个修法**全部逐字返回输入**（恒等）
    base = 错的     ⇒  修法跟着产出那个错的值   （盲从）

⇒ base 里有什么，修法就能产出什么；base 里没有的，它**一个字节也造不出来**。
真实任务域里 `env.source` 是「修复后源码」= **人类答案**，所以「第 1 轮修好
真实 bug」的准确含义是：**定位（真能力，靠判据）+ 改成什么抄自基线（假能力）**。

本模块是补缺口的那一层。

═══ ⛔⛔ 它与 `revert_*` 的结构性区别：签名里没有 base ═══

    revert_*(broken_src, base_src)      ← 答案来自 base
    synthesize_patch(broken_src, tests) ← 答案来自**判据反馈**

这不是"换个写法"，是**信息源换了**：base 是答案，判据只是尺子。
把 base 从签名里拿掉，是为了让「有没有偷看答案」这个问题**结构上可判定**：
本模块里没有 `env.source` 这个词，也没有任何 `base` 参数 ⇒ 想偷看都偷不到。

═══ ⛔ 它是**枚举式程序合成**，不是"模型凭空造代码"═══

搜索空间由 `CANDIDATE_KINDS` 给定（比较算子翻转 / 整数常量 ±1 / 加减翻转）。
覆盖不到的形态它一个字节也造不出来 —— 而 README 153 普查过：真实修复里
**84.9% 是结构级**，真算子级只有 **5.5%** ⇒ 本层的射程上限就在这 5.5% 里。
谁把它当成"会修 bug 了"，谁就是没读这个模块头。

═══ ⛔⛔ 本层的固有风险：它选出的补丁必然在发展集判据上更优 ═══

筛选用的是**发展集判据** ⇒ 选出来的必然是"发展集上更绿"的那个
⇒ 这就是 overfit 的形状。held-out 闸照常要过，**本层不声称泛化**。
（与 `revert_*` 的差别不是"更安全"，是"overfit 的方式不同"：
revert 抄答案，本层搜答案 —— 两者都要由闸裁决。）

⛔⛔ **这个风险已经实测为真，不是理论担心**（`scripts/probe_synth_overfit.py`，
真实 bug boltons `c1c25da`，25 条判据，每组 5 个随机子集）：

| 给的判据数 k | 与人类修复一致 | **未见判据**通过率 |
|---|---|---|
| **k=1** | **0/5** | **0.25** ← 5/5 全错：搜出 `cmp@L160`，满足给它那 1 条、破坏其余 18 条 |
| k=2 | 4/5 | 0.843 |
| **k=3** | 5/5 | **1.0** ← 收敛 |
| k=5 / 10 / 25 | 5/5 | 1.0 |

⇒ 两条必读推论：
  1. **判据必须够密**：这条 bug 上 k≥3 才收敛。判据稀疏时本层产出的是
     "拟合判据"的凑数解，不是修复。
  2. ⛔ **159 报的"修好"是在 k=25（最宽松）下取得的** —— 谁引用那个读数，
     必须同时引用这张表。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from aether.environment import run_in_sandbox
from aether.repair import render_patch

__all__ = [
    "SynthResult",
    "score_source",
    "locate_focus",
    "synth_candidates",
    "synthesize_patch",
]

#: ⛔⛔ 合成层这道门的**停靠点**，按串联顺序。命名即口径，不许另起一套。
#: 判据侧已经量过三道（装载/判据/形态），但「判据有鉴别力之后为什么还
#: 修不好」从没被归过因 —— 没有这张表，`found=False` 只是一个数，看不出
#: 该往哪使劲（第 24 条：一个维度恒 0 先怀疑量具，而不是继续加大搜索）。
GATE_BLEW = "S0_blew"          #: 缺陷版自己在沙箱里就跑挂 ⇒ 本层无从判断
GATE_NO_FOCUS = "S1_no_focus"  #: 定位器没定位到任何顶层 def/class
GATE_NO_CANDS = "S2_no_cands"  #: 定位到了，但该处没有任何可枚举的变异
GATE_NO_GAIN = "S3_no_gain"    #: 候选都跑了，没有一个严格优于基线
GATE_PARTIAL = "S4_partial"    #: 找到了，但没全绿 ⇒ 凑数解（161 的 64e6430）
GATE_FULL = "S5_full"          #: 找到了且全绿（⛔ 只是必要，仍要过 held-out）


#: 比较算子的候选替换集。⛔ 刻意**不含** `is`/`in` 之外的语义跳跃 ——
#: 每多一类候选，搜索空间与 overfit 风险同步放大（第 71 条：量代价）。
_CMP_ALTS: Dict[type, List[type]] = {
    ast.Gt: [ast.GtE, ast.Lt, ast.LtE],
    ast.GtE: [ast.Gt, ast.Lt, ast.LtE],
    ast.Lt: [ast.LtE, ast.Gt, ast.GtE],
    ast.LtE: [ast.Lt, ast.Gt, ast.GtE],
    ast.Eq: [ast.NotEq],
    ast.NotEq: [ast.Eq],
    ast.In: [ast.NotIn],
    ast.NotIn: [ast.In],
    ast.Is: [ast.IsNot],
    ast.IsNot: [ast.Is],
}


@dataclass
class SynthResult:
    """一次合成的结果。⛔ `n_ran` / `n_blew` 必须记账 —— 没有它们，
    「0 个候选有效」与「量具没跑起来」长得一模一样（第 24/25 条）。"""
    patch: Optional[str] = None
    desc: str = ""
    base_ok: int = 0
    new_ok: int = 0
    n_tests: int = 0
    focus: Tuple[str, ...] = ()
    n_cands: int = 0
    n_ran: int = 0
    n_blew: int = 0
    render_modes: Dict[str, int] = field(default_factory=dict)

    @property
    def gain(self) -> int:
        return self.new_ok - self.base_ok

    @property
    def found(self) -> bool:
        return self.patch is not None

    @property
    def full_pass(self) -> bool:
        """⛔⛔ 找到的补丁让**全部**判据通过了吗（而不只是「比基线好」）。

        ⛔ `found` 有两个含义，只看它会读错：
            真的找到了    vs    找到个**凑数的**。

        README 161 的 `64e6430` 那一例：只有 2 条 F2P、且其中一条恒失败
        （我探针写成了 SyntaxError）⇒ 「全绿」根本不可能达到 ⇒ 正确答案
        与一个改在不相干位置（`len(ded) > 384` → `<`，L122 vs 人类 L155）
        的凑数解并列 6/7 ⇒ 本层按「通过数最多、先到先得」选中了凑数解。
        `found=True`、`gain>0`，可它跟那个 bug 毫无关系。

        ⇒ **全绿是天然的分水岭**，必须显式可查，不该让调用方自己算。

        ⛔ 但它只是**必要**条件，不是充分条件：存在「另一种改法也能全绿」
        的情形（见契约 P3）⇒ 全绿之后仍要过 held-out 闸。
        """
        return self.patch is not None and self.n_tests > 0 \
            and self.new_ok >= self.n_tests

    @property
    def gate(self) -> str:
        """⛔ 停在合成层的**哪一格**（串联，先到的赢）。

        ⛔⛔ 判据顺序就是**诊断顺序**，不许重排：`n_blew` 得先看 —— 基线
        跑挂时 `focus` 必然是空的，先判 focus 会把「量具炸了」误记成
        「定位不到」（第 25 条：一个 0 有两种含义，这里必须分开）。

        ⛔ 只依赖**本层的字段**，不看 `base`、不看人类修复 ⇒ 可离线回归。
        「候选集里究竟有没有正解」是另一件事（事后 oracle），由调用方
        单独记账，不混进这张表。

        ⛔ `S0` 收两种同形情形：**基线跑挂** 与 **候选全跑挂**。它们不是
        同一处代码，但**诊断动作相同**（都是"尺子没量"，得先修量具/环境，
        不是"世界没有解"）⇒ 合并。分开记会让「修不好」看起来像「搜不到」。
        """
        if self.base_ok < 0:
            return GATE_BLEW
        if not self.focus:
            return GATE_NO_FOCUS
        if self.n_cands == 0:
            return GATE_NO_CANDS
        if self.n_ran == 0:            #: 候选一条都没跑成 ⇒ 同 S0
            return GATE_BLEW
        if not self.found:
            return GATE_NO_GAIN
        return GATE_FULL if self.full_pass else GATE_PARTIAL


# ── 判据跑分 ────────────────────────────────────────────────────────
def score_source(source: str, tests: List[Dict[str, Any]],
                 timeout: float = 20.0,
                 pkg: Optional[Dict[str, Any]] = None
                 ) -> Tuple[int, List[str], bool]:
    """返回 (通过条数, 失败判据名, **是否炸掉**)。

    ⛔ 第三位 True = 这条读数**不成立**（跑了但没跑成）。调用方必须单独记账，
    静默跳过会让「搜不出来」看起来像「世界没有」，而实际是「尺子没量」。
    """
    if not isinstance(source, str):
        return -1, [f"source 不是 str：{type(source).__name__}"], True
    try:
        v = run_in_sandbox(source, tests, timeout=timeout, pkg=pkg)
    except Exception as e:                                     # noqa: BLE001
        return -1, [f"runner error: {type(e).__name__}: {e}"], True
    if getattr(v, "import_error", None):
        return -1, [f"import error: {v.import_error[:80]}"], True
    ok, bad = 0, []
    for t, r in zip(tests, v.results):
        if r.get("passed"):
            ok += 1
        else:
            bad.append(t.get("name", "?"))
    return ok, bad, False


# ── 定位 ────────────────────────────────────────────────────────────
def _walk(n: ast.AST):
    for c in ast.iter_child_nodes(n):
        yield c
        yield from _walk(c)


def _idents(text: str) -> List[str]:
    """粗抽标识符：不解析表达式（判据名可能是半个表达式），只取连续标识字符。"""
    out, cur = [], ""
    for ch in text:
        if ch.isalnum() or ch == "_":
            cur += ch
        else:
            if cur and not cur[0].isdigit():
                out.append(cur)
            cur = ""
    if cur and not cur[0].isdigit():
        out.append(cur)
    return out


def top_defs(tree: ast.Module) -> Dict[str, ast.AST]:
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def locate_focus(source: str, texts: List[str]) -> List[str]:
    """从失败判据的**文本**里抽标识符 ⇒ 映射到源码里的**顶层**定义名。

    ⛔ `texts` 必须是「判据名 + 调用表达式」拼在一起的文本：只看判据名会
    漏 —— 判据名常常是 `t1` / `p3` 这种**不含任何标识符**的编号，而
    `call` 里才有真正的调用目标（`f(10)` ⇒ `f`）。开发时实踩：只看名字
    时量具自检搜不出修复，看起来像"这一层不行"，实际是定位没开工。

    ⛔ 这是"够用即可"的定位，不是缺陷定位研究：
       · 只认顶层 `def`/`class`（嵌套定义、别名导入、`pytest.fixture`
         注入的对象都不在射程内 ⇒ 真实定位成功率只会更低不会更高）
       · 抽不出 ⇒ 返回空 ⇒ 调用方**必须**停机而不是全文件搜索
         （全文件搜索 = 搜索空间爆炸 + 无法说明改动与判据相关）
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:                                        # noqa: BLE001
        return []
    defs = top_defs(tree)
    hits, seen = [], set()
    for nm in texts:
        for tok in _idents(str(nm)):
            if tok in defs and tok not in seen:
                seen.add(tok)
                hits.append(tok)
    return hits


def _find_node(tree: ast.AST, ref: ast.AST) -> Optional[ast.AST]:
    """按 (类型, lineno, col_offset) 在**另一棵**树里找对应节点。

    ⛔ 为什么不用 id：候选树是新 parse 出来的，id 完全无关。
    """
    for n in _walk(tree):
        if type(n) is type(ref) \
           and getattr(n, "lineno", None) == getattr(ref, "lineno", None) \
           and getattr(n, "col_offset", None) == getattr(ref, "col_offset", None):
            return n
    return None


# ── 候选枚举 ────────────────────────────────────────────────────────
def synth_candidates(source: str, focus: List[str],
                     max_cands: int = 400) -> List[Tuple[str, str, str]]:
    """产出 [(描述, 候选源码, 渲染模式)]。

    ⛔ 严格限定在 `focus` 指定的顶层定义内部：射程外的一概不动。
    ⛔ 渲染走 `render_patch`（最小编辑）；`fallback` 的**照收但记账** ——
       宁可丢格式不可丢语义（与 154 同一条纪律）。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:                                        # noqa: BLE001
        return []
    defs = top_defs(tree)
    scopes = [defs[f] for f in focus if f in defs]
    if not scopes:
        return []

    points: List[Tuple[str, Any, Optional[type], ast.AST]] = []
    for sc in scopes:
        for n in _walk(sc):
            if isinstance(n, ast.Compare):
                for op in n.ops:
                    for alt in _CMP_ALTS.get(type(op), []):
                        points.append(("cmp", None, alt, n))
            elif isinstance(n, ast.Constant) and isinstance(n.value, int) \
                    and not isinstance(n.value, bool):
                points.append(("int+1", None, None, n))
                points.append(("int-1", None, None, n))
            elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
                points.append(("binop-flip", None, None, n))

    out: List[Tuple[str, str, str]] = []
    for kind, _k, alt, node in points[:max_cands]:
        # ⛔ render_patch 的回调签名是 mutate(b, g) —— **两个**参数。
        #   写成单参数 ⇒ 每次 TypeError ⇒ 被静默吞掉 ⇒ 全部 "unchanged"
        #   ⇒ 看起来像"搜不出修复"（本模块开发时实踩，勿删此注）。
        def mut(t, _base=None, kind=kind, node=node, alt=alt):
            tgt = _find_node(t, node)
            if tgt is None:
                return t
            if kind == "cmp":
                tgt.ops = [alt() for _ in tgt.ops]
            elif kind in ("int+1", "int-1"):
                tgt.value = tgt.value + (1 if kind.endswith("+1") else -1)
            elif kind == "binop-flip":
                tgt.op = ast.Sub() if isinstance(tgt.op, ast.Add) else ast.Add()
            return t

        try:
            cand, mode = render_patch(source, source, mut)
        except Exception:                                      # noqa: BLE001
            continue
        if cand and cand != source:
            out.append((f"{kind}@L{node.lineno}", cand, mode))
    return out


# ── 主入口 ──────────────────────────────────────────────────────────
def synthesize_patch(broken_src: str,
                     tests: List[Dict[str, Any]],
                     *,
                     max_cands: int = 400,
                     max_runs: int = 200,
                     timeout: float = 20.0,
                     min_gain: int = 1,
                     pkg: Optional[Dict[str, Any]] = None,
                     ) -> SynthResult:
    """**不看 base**，只用判据反馈，在缺陷源码上搜一个更绿的补丁。

    ⛔ 参数里没有 `base` / `env.source` —— 这是本层与 `revert_*` 的
    结构性区别，也是「有没有偷看答案」的可判定边界。

    `min_gain=1` ⇒ 只接受**严格优于基线**的候选（绝不劣化）。
    `max_runs` ⇒ 沙箱跑分预算上限（每一次跑分都是真代价，第 71 条）。

    `pkg`（可选，README 164）：整包装载描述，**原样透传给沙箱**。
    ⛔ 它不是答案、不含修复后源码 —— 只是让包内相对导入能成立的
    装载上下文（与 `base` 是两回事，别混）。
    """
    res = SynthResult(n_tests=len(tests))
    base_ok, bad, blew = score_source(broken_src, tests, timeout=timeout,
                                      pkg=pkg)
    res.base_ok = base_ok if not blew else -1
    if blew:
        res.n_blew = 1
        return res                          #: 基线都跑不起来 ⇒ 本层无从判断

    #: ⛔ 定位文本 = 判据名 **+ call 表达式**：只给名字会漏（名字可能是编号）
    bad_set = set(bad)
    texts = [f"{t.get('name', '')} {t.get('call', '')}"
             for t in tests if t.get("name") in bad_set]
    focus = locate_focus(broken_src, texts)
    res.focus = tuple(focus)
    if not focus:
        return res                          #: 定位失败 ⇒ 停机，不做全文件搜索

    cands = synth_candidates(broken_src, focus, max_cands=max_cands)
    res.n_cands = len(cands)
    for _d, _c, mode in cands:
        res.render_modes[mode] = res.render_modes.get(mode, 0) + 1

    best: Optional[Tuple[int, str, str]] = None
    for desc, cand, _mode in cands[:max_runs]:
        ok, _bad, blew2 = score_source(cand, tests, timeout=timeout, pkg=pkg)
        if blew2:
            res.n_blew += 1
            continue
        res.n_ran += 1
        if ok - base_ok >= min_gain and (best is None or ok > best[0]):
            best = (ok, desc, cand)

    if best is not None:
        res.new_ok, res.desc, res.patch = best
    return res

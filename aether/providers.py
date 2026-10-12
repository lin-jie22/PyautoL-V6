"""LLM / Embedding 供给：可插拔，**默认离线确定性**。

为什么要一个 stub provider（而不是直接接真模型）：
  1. **自进化系统必须能被离线回归**。依赖网络/模型 ⇒ 每次跑出来的进化
     轨迹都不同 ⇒ "零新红""非回归"这些判据全部失效（V5 的教训：
     链首 zhipu 82% 429 直接让整条供给链崩掉两轮）。
  2. 判据必须与被测对象同源：stub 让"进化循环本身的正确性"和
     "模型好不好"解耦 —— 前者能被确定性测试，后者只能被评测。
  3. 真模型通过同一个 Protocol 注入，**接口不变**。

⛔ 供给失败必须**显式**：stub 不会因为"没配 key"就返回空字符串让下游
   以为成功。真 provider 的任何异常都向上抛并由调用方记账（fail-closed）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from .genome import Capsule, Gene, Harness
from .runtime import Trajectory


def _post_json(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """⛔ 访问 localhost 必须绕过代理（本机有 HTTP_PROXY ⇒ 否则 502 假故障）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class LLMProvider(Protocol):
    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str: ...


class Proposer(Protocol):
    """变异提议器：看弱点 → 产出候选 capsule。"""
    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3) -> List[Capsule]: ...


class StubProvider:
    """确定性 provider：同一输入永远同一输出。

    ⛔ 它**不假装自己是模型**：`complete()` 返回的是结构化的"我不做生成"
    标记，下游 proposer 看到它就必须走模板化路径，而不是把空字符串
    当成"模型说不用改"。
    """
    STUB_MARK = "[[STUB:no-generation]]"

    def __init__(self, echoes: Optional[Dict[str, str]] = None):
        self.echoes = echoes or {}
        self.calls = 0

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        self.calls += 1
        for k, v in self.echoes.items():
            if k in prompt:
                return v
        return f"{self.STUB_MARK} prompt_len={len(prompt)}"


class HTTPProvider:
    """OpenAI 兼容 Chat Completions。**显式失败**，不吞异常。"""

    def __init__(self, base_url: str = "", model: str = "", api_key: str = "",
                 timeout: float = 60.0):
        self.base_url = base_url or os.environ.get("V6_LLM_BASE_URL", "")
        self.model = model or os.environ.get("V6_LLM_MODEL", "")
        self.api_key = api_key or os.environ.get("V6_LLM_API_KEY", "")
        self.timeout = timeout
        if not (self.base_url and self.model):
            raise ValueError("HTTPProvider 需要 base_url 与 model（或环境变量）")

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:200]!r}") from e
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM 调用失败：{type(e).__name__}: {e}") from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM 响应结构异常：{data}") from e


class OllamaProvider:
    """本机 Ollama（走**原生** `/api/chat`）。**显式失败**。

    ⛔ 为什么不用 OpenAI 兼容的 `/v1/chat/completions`：thinking 模型
    （Qwen3.5）在那里关不掉思考，token 预算被 reasoning 吃光 ⇒ `content`
    空字符串 ⇒ 下游"解析失败"，而日志上只看到"模型没输出"。实测同一请求：
    走 /v1 时 41s、finish_reason=length（内容被思考挤掉）；
    走 /api/chat + `think:false` 时 **3.2s** 且内容完整 —— 慢 13 倍还拿不到东西。

    ⛔ 访问 localhost 必须绕过代理（本机有 HTTP_PROXY，否则 502 假故障）。
    """

    def __init__(self, model: str = "", base_url: str = "http://127.0.0.1:11434",
                 timeout: float = 0.0, think: bool = False):
        self.model = model or os.environ.get("V6_OLLAMA_MODEL", "")
        self.base_url = base_url
        #: 本机小模型在长 prompt 下首 token 很慢（实测 >180s 超时过），
        #: 所以超时可配（V6_OLLAMA_TIMEOUT），默认 180s。
        self.timeout = float(timeout or os.environ.get("V6_OLLAMA_TIMEOUT", 180))
        self.think = think
        if not self.model:
            raise ValueError("OllamaProvider 需要 model（或 V6_OLLAMA_MODEL）")
        self.calls = 0
        self.last_thinking_len = 0

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        self.calls += 1
        msgs = ([{"role": "system", "content": system}] if system else []) \
            + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": msgs, "stream": False,
                "think": bool(self.think),
                "options": {"num_predict": max_tokens, "temperature": temperature}}
        data = _post_json(self.base_url.rstrip("/") + "/api/chat", body,
                          self.timeout)
        msg = data.get("message") or {}
        self.last_thinking_len = len(msg.get("thinking") or "")
        content = msg.get("content") or ""
        if not content and self.last_thinking_len:
            # ⛔ 显式记账：这是"预算被思考吃光"，与"模型没说话"不是一回事
            raise RuntimeError(
                f"empty_content（thinking 占 {self.last_thinking_len} 字，"
                f"content 空 ⇒ 提高 max_tokens 或 think=False）")
        return content


@dataclass
class TemplateProposer:
    """模板化变异提议器（stub 的默认搭档）。

    它**不是**"假装是 LLM"，而是把"弱点 → 编辑"的映射显式写成规则：
      · 缺技能 ⇒ 加技能 + 加路由
      · 工具不够 ⇒ 开放工具
      · 记忆召回差 ⇒ 调 memory_policy
    这样进化循环的**机制正确性**可以脱离模型被验证（V5 的纪律：
    先证明机制跑得通，再谈模型好不好）。
    """
    name: str = "template"
    skills: Dict[str, str] = field(default_factory=dict)
    #: 路由键。**默认按任务类别**，不按环境名（topic）。
    #: ⛔ 按环境名路由（`textscore → 修法`）本身就是一种定点化：换一个环境
    #:    这条路由立刻失效，于是"技能加了"却"在别处用不上"。
    #:    按任务类别路由（`repair → 修法`）才是通用写法。
    route_key: str = "repair"

    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3) -> List[Capsule]:
        kind = str(weakness.get("kind", ""))
        topic = str(weakness.get("topic", "general"))
        out: List[Capsule] = []

        if kind in ("missing_skill", "unknown"):
            for i, (sname, body) in enumerate(list(self.skills.items())[:n]):
                # ⛔ 胶囊（不是单基因）：技能 + 路由**必须一起改**。
                #    只加技能 ⇒ 路由没接通 ⇒ 技能不可达 ⇒ reward 不变
                #    ⇒ 过不了能力地板。这正是 Capsule 存在的理由，
                #    由 tests/contract_agent_test.py 的 L1 组实测守住。
                out.append(Capsule(
                    name=f"add_skill:{sname}",
                    genes=(Gene(op="set", target=f"L3.skills.{sname}",
                                value=body, rationale=f"弱点={kind}"),
                           Gene(op="set", target=f"L1.routing.{self.route_key}",
                                value=sname,
                                rationale=f"新技能需可达（按任务类别"
                                          f"{self.route_key} 路由，不按环境名）")),
                    rationale=f"补技能 {sname} 并接通路由", origin="mined"))

        if kind == "missing_tool":
            tool = str(weakness.get("tool", "read_file"))
            out.append(Capsule(name=f"tool:{tool}",
                               genes=(Gene(op="append", target="L2.tools",
                                           value=tool, rationale="工具不可达"),),
                               rationale=f"开放工具 {tool}", origin="mined"))

        if kind == "recall_miss":
            out.append(Capsule(
                name="tune:recall",
                genes=(Gene(op="tune", target="L4.memory_policy.recall_topk",
                            value=2, rationale="召回不足 ⇒ 扩大 topk"),
                       Gene(op="tune", target="L4.memory_policy.min_content_len",
                            value=-20, rationale="下限过高挡住了短知识")),
                rationale="调记忆召回参数", origin="mined"))

        if not out:
            out.append(Capsule(name="noop", genes=(), rationale="无可用变异",
                               origin="mined"))
        return out[:max(1, n)]


# ──────────────────────────────────────────────────────────────────
# 真模型提议器
# ──────────────────────────────────────────────────────────────────
#: 允许的操作。⛔ 白名单，不是黑名单：模型能发明的东西必须落在集合内。
ALLOWED_OPS: Tuple[str, ...] = ("set", "append", "tune", "remove")

#: 允许被模型改的层。⛔ `meta_rules`（L0）**不在里面** —— 元规则是宪法，
#: 自动改自己宪法的系统是失控的捷径（V6 第 1 条：改 L0 需人工审批）。
ALLOWED_TARGET_LAYERS: Tuple[str, ...] = ("routing", "tools", "skills",
                                          "memory_policy", "prompts")

#: value 允许的标量类型。⛔ 不接受 dict/list：模型往 value 里塞结构化内容
#: （嵌套指令、伪基因、超长文本）是注入面，不是"更丰富的表达"。
_SCALAR = (str, int, float, bool)

MAX_VALUE_LEN = 400
MAX_TARGET_LEN = 80


def validate_gene(d: Any, *, allow_protected: bool = False) -> Tuple[bool, str]:
    """校验**模型产出的一条基因**。返回 (ok, 拒因)。

    ⛔ 模型输出是**不可信输入**，和"用户提交的表单"同级。
    它后面还要过非回归闸与泛化闸，但**语法闸必须先过**：
    一个非法 target 到了 `apply_gene` 会抛异常，那已经太晚了 ——
    它会在进化循环里变成"候选评估崩溃"，而不是"候选被拒"。
    """
    if not isinstance(d, dict):
        return False, f"gene 不是对象：{type(d).__name__}"
    op = str(d.get("op", "")).strip()
    if op not in ALLOWED_OPS:
        return False, f"op 不在白名单：{op!r}"
    target = str(d.get("target", "")).strip()
    if not target or len(target) > MAX_TARGET_LEN:
        return False, f"target 缺失或过长：{target[:40]!r}"
    parts = target.split(".")
    if parts and parts[0].startswith("L") and parts[0][1:].isdigit() \
            and len(parts) > 1:
        parts = parts[1:]
    layer = parts[0] if parts else ""
    if layer == "meta_rules" and not allow_protected:
        # ⛔ 第一道锁（独立检查）：模型不得自动改 L0 元规则
        return False, "⛔ 模型不得自动改 L0 元规则（需人工审批）"
    allowed = ALLOWED_TARGET_LAYERS + (("meta_rules",) if allow_protected else ())
    # 第二道锁（白名单）：即使上面放行，层不在白名单里仍然拒
    if layer not in allowed:
        return False, f"target 层不在白名单：{layer!r}"
    if "value" not in d:
        return False, "缺 value"
    v = d["value"]
    if isinstance(v, bool) or not isinstance(v, _SCALAR):
        return False, f"value 必须是标量，实得 {type(v).__name__}"
    if isinstance(v, str) and len(v) > MAX_VALUE_LEN:
        return False, f"value 过长（{len(v)} > {MAX_VALUE_LEN}）"
    return True, ""


def parse_capsules(raw: str, *, allow_protected: bool = False,
                   max_genes: int = 4) -> Tuple[List[Capsule], List[str]]:
    """从模型的自由文本里解析出 capsule 列表。

    ⛔ **解析失败返回空列表，不返回"部分成功"的东西**：
    一条非法基因混在合法基因里，会让整个 capsule 在 `apply_gene` 时崩溃，
    而崩溃会被进化循环记成"这个候选 reward 0" —— 于是模型的一个语法错误
    被伪装成"这个变异没用"。宁可这一轮什么都不做（并记账），
    也不要让"没改"和"改了但崩了"无法区分。
    """
    rejects: List[str] = []
    text = (raw or "").strip()
    if not text:
        return [], ["empty_response"]
    # 1) 优先在 ```json 代码块里找
    start = -1
    m = re.search(r"```(?:json)?\s*", text)
    if m:
        start = text.find("{", m.end())
    # 2) 否则从全文第一个 `{` 开始
    if start < 0:
        start = text.find("{")
    if start < 0:
        return [], ["no_json_object"]
    blob = _scan_json_object(text, start)
    if blob is None:
        # 真模型会漏掉最后的右括号（实测 Qwen3.5-4B 上稳定复现一次）。
        # ⛔ 只做**语法**修复：补闭合括号，绝不改内容；且必须记账
        #    （能修 ≠ 没问题 —— 补过的输出要能被看出来）。
        fixed = _repair_missing_braces(text, start)
        if fixed is None:
            return [], ["unbalanced_braces（输出不完整，还差 "
                        f"{_brace_depth(text, start)} 层括号未闭合）"]
        blob = fixed
        rejects.append("repaired:missing_closing_braces")
    try:
        data = json.loads(blob)
    except Exception as e:  # noqa: BLE001
        return [], [f"json_parse_error:{type(e).__name__}"]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("capsules") or [data]
    else:
        return [], [f"unexpected_root:{type(data).__name__}"]
    if not isinstance(items, list):
        return [], ["capsules_not_a_list"]

    out: List[Capsule] = []
    for it in items:
        if not isinstance(it, dict):
            rejects.append("capsule_not_object")
            continue
        genes_raw = it.get("genes") or []
        if not isinstance(genes_raw, list) or not genes_raw:
            rejects.append(f"capsule_empty_genes:{it.get('name')}")
            continue
        genes: List[Gene] = []
        bad = False
        for g in genes_raw[:max_genes]:
            ok, why = validate_gene(g, allow_protected=allow_protected)
            if not ok:
                rejects.append(f"gene_rejected:{why}")
                bad = True
                break
            genes.append(Gene(op=str(g["op"]), target=str(g["target"]),
                              value=g["value"],
                              rationale=str(g.get("rationale", ""))[:200]))
        if bad or not genes:
            continue
        out.append(Capsule(name=str(it.get("name", "llm"))[:60] or "llm",
                           genes=tuple(genes),
                           rationale=str(it.get("rationale", ""))[:300],
                           origin="llm"))
    return out, rejects


def _scan_json_object(text: str, start: int) -> Optional[str]:
    """从 start 处扫描一个**完整**的 JSON 对象，返回其文本。

    ⛔ 不能用正则 `\\{.*?\\}`：非贪婪会在**第一个** `}` 处停下，
    于是任何带嵌套的 JSON（capsule 里必然有 genes 数组）都会被截成半截
    ⇒ json_parse_error。这个 bug 在"模型不加 fence"时碰巧不出现，
    一旦模型用 ```json 包裹就必现 —— 典型的只在真模型上暴露的缺陷。

    ⛔ 必须跳过**字符串内部**的括号（`"a{b"` 不能算开括号）。
    """
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair_missing_braces(text: str, start: int) -> Optional[str]:
    """只补**缺失的右括号**，补完必须能 parse，否则返回 None。

    ⛔ 边界：绝不改内容、绝不猜字段。若模型是被**截断在字符串中间**
    （引号未闭合），补括号后 `json.loads` 必然失败 ⇒ 走 unbalanced 分支。
    所以它只能救"漏了尾巴上的括号"，救不了"输出被切断"。
    """
    tail = text[start:]
    need = _brace_depth(tail, 0)
    if need <= 0:
        return None
    cand = tail.rstrip() + ("}" * need)
    try:
        json.loads(cand)
    except Exception:  # noqa: BLE001
        return None
    return cand


def _brace_depth(text: str, start: int) -> int:
    depth = 0
    for c in text[start:]:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
    return depth
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("capsules") or [data]
    else:
        return [], [f"unexpected_root:{type(data).__name__}"]
    if not isinstance(items, list):
        return [], ["capsules_not_a_list"]

    out: List[Capsule] = []
    for it in items:
        if not isinstance(it, dict):
            rejects.append("capsule_not_object")
            continue
        genes_raw = it.get("genes") or []
        if not isinstance(genes_raw, list) or not genes_raw:
            rejects.append(f"capsule_empty_genes:{it.get('name')}")
            continue
        genes: List[Gene] = []
        bad = False
        for g in genes_raw[:max_genes]:
            ok, why = validate_gene(g, allow_protected=allow_protected)
            if not ok:
                rejects.append(f"gene_rejected:{why}")
                bad = True
                break
            genes.append(Gene(op=str(g["op"]), target=str(g["target"]),
                              value=g["value"],
                              rationale=str(g.get("rationale", ""))[:200]))
        if bad or not genes:
            continue
        out.append(Capsule(name=str(it.get("name", "llm"))[:60] or "llm",
                           genes=tuple(genes),
                           rationale=str(it.get("rationale", ""))[:300],
                           origin="llm"))
    return out, rejects


SYSTEM_PROMPT = """\
你是一个自进化 agent 系统的变异提议器。你只输出 JSON，不输出任何解释文字。

可编辑的层（target 必须以这些开头）：
  L1.routing.<键>          任务类别 → 技能名的路由
  L2.tools                 工具名（用 op=append，value 是工具名字符串）
  L3.skills.<技能名>       技能正文（op=set）
  L4.memory_policy.<键>    记忆策略参数（op=tune 时 value 是数字增量）
  prompts.<名字>           提示词片段（op=set）
op 只能是 set / append / tune / remove 之一。
value 只能是字符串、数字或布尔值，不能是对象或数组。
⛔ 不要输出 meta_rules 相关的 target —— 元规则层不允许自动修改。

输出格式：
{"capsules":[{"name":"简短名字","rationale":"为什么这样改","genes":[{"op":"set","target":"L3.skills.fix_x","value":"...","rationale":"..."}]}]}
"""


def build_prompt(weakness: Dict[str, Any], harness: Harness, n: int = 3) -> str:
    """把"当前 harness + 弱点"讲清楚，让模型有依据而不是瞎猜。"""
    return (
        "当前 harness 状态（JSON）：\n"
        f"{json.dumps(harness.to_dict(), ensure_ascii=False)[:1500]}\n\n"
        "观测到的弱点：\n"
        f"{json.dumps(weakness, ensure_ascii=False)[:800]}\n\n"
        f"请提出最多 {n} 个候选 capsule，每个 capsule 是一组**必须一起生效**的编辑"
        "（例如：加技能必须同时加路由，否则技能不可达）。\n"
        "只输出 JSON。"
    )


@dataclass
class LLMProposer:
    """真模型变异提议器，与 `TemplateProposer` **同协议**（可互换）。

    ⛔⛔ 模型输出是不可信输入。三道闸按顺序：
      ① 语法闸 `validate_gene`  —— target/op/value 白名单，L0 禁止自动改
      ② 解析闸 `parse_capsules` —— 解析失败 ⇒ 空列表（**不**返回半成品）
      ③ 语义闸 —— 非回归闸 / 泛化闸 / 能力地板，在进化循环里（与模板提议器完全相同）

    于是"模型好不好"与"进化机制对不对"是**两个独立问题**：
    模型再差，也只是提不出可用变异（正确地什么都不做并记账）；
    它不可能绕过闸把系统改坏。这正是先做 TemplateProposer 的理由。
    """

    llm: Any
    name: str = "llm"
    allow_protected: bool = False
    max_genes: int = 4
    temperature: float = 0.0
    #: ⛔ 真模型会被自己的输出长度截断（实测：1024 预算下出现半截 JSON）
    max_tokens: int = 2048
    #: 截断后重试次数。⛔ 重试**必须被记账**（否则"多试几次"会掩盖真实失败率）
    retries: int = 1
    #: 最近一轮的记账（⛔ 失败必须可见，不能只是"返回空列表"）
    last_error: str = ""
    last_rejects: List[str] = field(default_factory=list)
    last_raw_len: int = 0
    last_raw_tail: str = ""
    n_retries_used: int = 0

    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3) -> List[Capsule]:
        self.last_error = ""
        self.last_rejects = []
        self.last_raw_len = 0
        self.last_raw_tail = ""
        self.n_retries_used = 0
        prompt = build_prompt(weakness, harness, n)
        for attempt in range(self.retries + 1):
            if attempt:
                self.n_retries_used += 1
                self.last_rejects.append(f"retry#{attempt}")
            try:
                raw = self.llm.complete(prompt, system=SYSTEM_PROMPT,
                                        max_tokens=self.max_tokens,
                                        temperature=self.temperature)
            except Exception as e:  # noqa: BLE001
                # ⛔ 显式记账后返回空：供给失败 ≠ "模型认为无需修改"
                self.last_error = f"{type(e).__name__}: {e}"
                return []
            self.last_raw_len = len(raw or "")
            self.last_raw_tail = (raw or "")[-120:]
            if hasattr(self.llm, "STUB_MARK") and self.llm.STUB_MARK in (raw or ""):
                # stub provider 明确说"我不做生成" ⇒ 不是解析失败，是没接真模型
                self.last_error = "stub_provider（未接真模型，不产出变异）"
                return []
            caps, rejects = parse_capsules(
                raw, allow_protected=self.allow_protected,
                max_genes=self.max_genes)
            self.last_rejects = [r for r in self.last_rejects
                                 if not r.startswith("retry#")] + rejects
            if caps:
                return caps[:max(1, n)]
        if not self.last_rejects:
            self.last_error = "empty_model_output"
        else:
            self.last_error = f"no_valid_capsule（{'; '.join(self.last_rejects[:3])}）"
        return []

    def diagnostics(self) -> Dict[str, Any]:
        """把"这一轮为什么没产出变异"讲清楚。

        ⛔ 没有它，跑完一轮看到"0 个候选"时无法区分：
           模型挂了 / 输出被截断 / 输出合法但全被闸拒 / 没接真模型。
        这四种的处置完全不同 —— 可观测性缺口会让"闸在工作"和
        "供给根本没通"看起来一模一样。
        """
        return {"name": self.name, "error": self.last_error,
                "rejects": list(self.last_rejects),
                "raw_len": self.last_raw_len,
                "raw_tail": self.last_raw_tail,
                "retries_used": self.n_retries_used}

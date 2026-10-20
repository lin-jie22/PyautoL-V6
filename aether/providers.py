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

from dataclasses import dataclass, field, replace
from typing import (Any, Callable, Dict, List, Optional, Protocol, Sequence,
                    Tuple)

from .genome import (Capsule, Gene, Harness, gene_add_skill, gene_route_append,
                     gene_tool, gene_tune, parse_route)
from .runtime import Trajectory
#: ⛔ 清理提案器需要它。依赖方向：`providers → cleanup → agent → genome`，
#:    链条上没有回指 providers 的边（有则会循环导入）。
from . import cleanup as _cleanup


def _accepts(fn: Any, arg: str) -> bool:
    """内层提议器是否接受某个关键字参数（鸭子探测，不强制协议）。"""
    try:
        import inspect
        return arg in inspect.signature(fn).parameters
    except Exception:  # noqa: BLE001
        return False


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

    def __init__(self, model: str = "", base_url: str = "", timeout: float = 0.0,
                 think: bool = False):
        self.model = model or os.environ.get("V6_OLLAMA_MODEL", "")
        #: ⛔ 端点必须可覆盖（r62）：写死 11434 时，契约**无法注入**可控的
        #:    假端点 ⇒ 「真模型链路跑通了吗」只能靠"本机恰好装了 ollama"来
        #:    证明 ⇒ 在没装 ollama 的机器上永远红、在装了的机器上永远绿，
        #:    两种都不是断言。默认值不变 ⇒ 生产行为逐字不变。
        self.base_url = (base_url or os.environ.get("V6_OLLAMA_BASE_URL", "")
                         or "http://127.0.0.1:11434")
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


class MemoryAwareProposer:
    """给任意提议器套一层**跨轮经验记忆**：试过并被闸拒的变异不再重复提。

    ⛔ 存在理由（2026-10-13 实测）：记忆层在端到端进化里**一次都没被走到**
    （89 次执行中 L4 通路触发 0 次）—— 记忆只在 `loop.run()` 之后写，
    对 reward 零影响，是纯装饰。这个包装器让记忆**进入因果链**：
    下一轮的候选集由上一轮的失败记忆决定。

    ⛔ 只跳过**被闸拒绝**的变异（`gate_reject` / `overfit` / `partial` /
    `blocked`），不跳过"没优势"（`no_advantage`）：后者是"这次没用"，
    前者是"这条路被证明有害"。记错对象 ⇒ 系统会停止探索有用但不稳定的变异。

    ⛔ 与内层提议器**同协议**可互换：闸一个不少（它只筛候选，不改判据）。
    """

    #: ⛔⛔ 明星 mechanism ≠ 这条路被走到（第 4 条）。这个类曾只在**内存**
    #:    里维护 `skipped` ⇒ 「这一轮的候选集是不是真被上一轮的失败改变
    #:    过」在账本里**读不出来**：2026-10-17 全量账本扫描，`evolution:reject`
    #:    写入仅 5 条、`knowledge.ingested` 0 条，而"因学到而少提了几个候选"
    #:    根本没有数 ⇒ 闭环两侧都是盲的。以下三个事件补齐两侧读数。
    #: ⛔ r58 更正：r57 审计曾判定「PROPOSE 一个事件都没有」—— **那是量具
    #:    的盲区**（审计只扫字面量，扫不到 `self.EVENT_PROPOSE` 这种常量
    #:    别名 + `_emit()` 中转），生产账本里其实有 21 条。真缺口小得多：
    #:    记了 n_in/n_out，却**没记提名名单** ⇒ "这轮池子里有谁、什么顺序"
    #:    仍然读不出来 ⇒ 提名名单已补齐（第 24 条第七次命中，先修量具）。
    EVENT_REMEMBERED = "experience.remembered"
    EVENT_PROPOSE = "experience.propose"
    #: 名单只做**取证**：有界窗口，不承担计数职责（计数在 `stats` 里）
    EVIDENCE_WINDOW = 20

    def __init__(self, inner: Any, memory: Any, marker: str = "tried",
                 ledger: Any = None, retry_every: int = 0):
        self.inner = inner
        self.memory = memory
        self.marker = marker
        self.skipped: List[str] = []
        self.asked = 0
        #: ⛔⛔ **被跳过的候选必须能重审**（r54 实测缺口）。
        #:   `0` ⇒ 与历史逐字同口径（**永久**不重提）；`>0` ⇒ 每被跳过
        #:   `retry_every` 轮，放行一次（与 `EvolutionConfig.
        #:   deadend_probation_every` 完全同形）。
        #:
        #:   为什么必须给它出口（三条实测证据）：
        #:   ① `partial` / `overfit` 的结论依赖**当时的 held-out 卷子**，
        #:      而 r53 起卷子会随基线长（`--ho-grow`）⇒ 旧判定会失效，
        #:      可这条 skip **没有失效机制**（与第 35c 条同构：随被测对象
        #:      变化的量，不许只量一次）。
        #:   ② `deadend_probation_every` 出口的存在，说明**设计者自己承认**
        #:      「被拦 N 次后情况可能变化」—— 但那套出口在**下游**
        #:      （`_in_cooldown`），而本层的 skip 在**上游**先触发
        #:      ⇒ 实测 13 次 `_in_cooldown` 判定**全部 streak=0**，
        #:      冷却机制与其出口**一次都没被走到**（第 4 条）。
        #:   ③ 损失可量化：轮 2 的 `fix_control_flow` rew=0.7143（基线
        #:      0.381，+0.33）被判 `partial` ⇒ 从第 3 轮起永久消失。
        #:
        #:   ⛔ 更正确的做法是「**卷子指纹变了就重审**」（因果维度），
        #:      但需要跨层把卷子身份传到提议器 —— 本轮不做，先给时间
        #:      维度的出口（保守、可验证、默认关闭），缺口如实记录。
        self.retry_every = int(retry_every or 0)
        #: 每个 key 被连续跳过了多少轮（只对 retry_every > 0 有意义）。
        self._skip_age: Dict[str, int] = {}
        #: 本轮因 probation 而**重新放行**的候选（读数，不是判据）。
        self.probation_released: List[str] = []
        #: 类级注入点（与 `MemoryOS.ledger` 同形态）：None ⇒ no-op。
        #: ⛔ 构造函数里 `self.ledger = ledger` 会**覆盖类级注入**
        #:    ⇒ 显式传 None 时不能写（第 7 条：默认开的开关不能靠"不传"关）。
        if ledger is not None:
            self.ledger = ledger
        #: ⛔ 计数与 evidence 分开存（第 14 条）：`skipped` 列表会被下一个
        #:    例子在这儿开始挤掉样本，但**总数**必须不丢。
        self.stats: Dict[str, int] = {
            "remembered": 0, "asked": 0, "skipped": 0, "recall_failed": 0,
            #: ⛔ 单独计数（第 16 条）：「因 probation 重审」与「被 skip」
            #:    是两件事，合并成一个数 ⇒ 出口到底走了几次，永远读不出来。
            "probation_released": 0}
        #: 上一次 `_rejected_for` 是否因检索崩溃而返回空 —— 见该方法注释
        self._last_recall_ok: bool = True

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        """记一条经验闭环事件。⛔ 记账失败**不许**拖垮提议（但要留痕）。"""
        led = getattr(self, "ledger", None)
        if led is None:
            return
        try:
            led.append(kind, payload, actor="experience")
        except Exception as e:                              # noqa: BLE001
            #: 没有 ledger_error 字段可读 ⇒ 静默丢事件会被读成"没发生"。
            try:
                led.append("experience.ledger_failed",
                           {"kind": kind,
                            "error": f"{type(e).__name__}: {e}"},
                           actor="experience")
            except Exception:                               # noqa: BLE001
                pass

    @staticmethod
    def _key(weakness: Dict[str, Any], cap_name: str) -> str:
        return (f"{weakness.get('kind', '')}|{weakness.get('topic', '')}"
                f"|{cap_name}")

    def remember(self, weakness: Dict[str, Any], cap_name: str,
                 reason: str) -> None:
        """把一次**失败**记进记忆。只有被闸拒的才记（见类 docstring）。"""
        if self.memory is None:
            return
        bad_marks = ("gate_reject", "overfit", "partial", "blocked",
                     "apply_failed")
        if not any(m in (reason or "") for m in bad_marks):
            return
        key = self._key(weakness, cap_name)
        #: ⛔ 写入 ≠ 记进去。`memory.write` 返回的 disposition 可能是
        #:    `deduped`（同一条失败被反复记）⇒ 那时**没有新增知识**，
        #:    而记账仍会写事件 ⇒ 读数会把「记了 1 条」读成「学了 N 次」
        #:    （V5 第 39x 重复同族）。三种处置必须分开（第 10 条）。
        disp = "unknown"
        note_id = ""
        try:
            note, disp = self.memory.write(
                "L4",
                f"{self.marker}:{key} —— 上一轮该变异被拒（{reason[:80]}），"
                f"本轮不再重复提议，避免把算力反复花在同一条死路上。",
                channel="system", source="evolution:reject",
                tags=("experience", "exec"))
            note_id = getattr(note, "note_id", "") or ""
        except Exception as e:                              # noqa: BLE001
            #: ⛔ 写失败 ≠ 没写过。沉默会让"记忆崩了"和"没有失败可记"
            #:    在读数里长得一模一样（fail-open 的第二种形态）。
            self.stats["recall_failed"] += 1
            self._emit(self.EVENT_REMEMBERED, {
                "key": key, "reason": (reason or "")[:80],
                "disposition": "write_failed",
                "error": f"{type(e).__name__}: {e}",
                "note_id": "", "deduped": False})
            return
        self.stats["remembered"] += 1
        self._emit(self.EVENT_REMEMBERED, {
            "key": key, "reason": (reason or "")[:80],
            "disposition": str(disp), "note_id": note_id,
            "deduped": bool(disp != "written")})

    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3) -> List[Capsule]:
        self.asked += 1
        self.stats["asked"] += 1
        #: ⛔ 每轮清空：`probation_released` 是**本轮**的读数，跨轮累积会让
        #:    "第 3 轮到底放行了谁"在账本里读不出来（与 `skipped` 的区别：
        #:    那个是有界窗口的取证名单，这个必须是本轮切片）。
        self.probation_released = []
        #: ⛔ 先把失败史**交给模型**，再筛。顺序有讲究：
        #    对 LLM 提议器来说，只筛不说是浪费 —— 模型白跑一次推理，
        #    产出的还是同一条死路（下一轮继续撞）。说清楚"这些方案试过、
        #    被判 XX 拦下"，模型才知道该往哪个方向改。
        rejected = self._rejected_for(weakness)
        if rejected and _accepts(self.inner.propose, "rejected"):
            caps = list(self.inner.propose(weakness, harness, n,
                                           rejected=rejected))
        else:
            caps = list(self.inner.propose(weakness, harness, n))
        if self.memory is None:
            #: ⛔⛔ **没有记忆也必须记账**：不记 ⇒ "这一轮没学到东西"
            #:    和"根本没装记忆"在账本里都是"没有事件"，无法分辨
            #:    （这正是本 r45 之前的原状）。
            self._emit(self.EVENT_PROPOSE, {
                "asked": self.asked, "recall": "no_memory",
                "n_in": len(caps), "n_out": len(caps), "n_skipped": 0,
                "n_known_history": 0, "skipped_sample": [],
                #: ⛔ 提名名单**必须进账**（r58 实测缺口，见类注释 §r58）：
                #:    没有它 ⇒ "这一轮池子里到底有哪些候选、以什么顺序"
                #:    在账本里读不出来 ⇒ 候选挤压（r52）只能靠外部探针猜。
                "proposed": [c.name for c in caps],
                "accepted": [c.name for c in caps]})
            return caps
        out = []
        for c in caps:
            key = self._key(weakness, c.name)
            ok = True
            try:
                hits = self.memory.search(f"{self.marker}:{key}", top_k=3)
            except Exception:  # noqa: BLE001 —— 记忆不可用 ⇒ 不筛，照原样提
                #: ⛔⛔ fail-open 必须**可见**：这里放行是有意为之（一个读
                #:    失败不该让整轮停摆），但它和"查了、没有这条失败史"
                #:    的后果完全不同 —— 前者意味着本轮的重复提议**没被拦**。
                hits = []
                ok = False
                self.stats["recall_failed"] += 1
            if ok and any(f"{self.marker}:{key}" in (x.content or "")
                          for x in hits):
                age = self._skip_age.get(key, 0) + 1
                self._skip_age[key] = age
                #: ⛔ probation 出口：每被跳过 `retry_every` 轮，放行一次
                #:    让闸**重新审**这个旧判定（理由见 `__init__` 的注释）。
                #:    ⛔ 不清除记忆条目：记忆记的是"曾经被拒"这个事实，
                #:      它没变；变的是"这张卷子还作不作数"。清掉记忆 ⇒
                #:      把「学过」这件事也一起删了（第 17 条：能力验收看
                #:      账本里有没有它的读数）。
                if self.retry_every > 0 and age % self.retry_every == 0:
                    self.stats["probation_released"] += 1
                    self.probation_released.append(c.name)
                    out.append(c)
                    continue
                self.skipped.append(c.name)
                self.stats["skipped"] += 1
                continue
            #: 已不在失败史里 ⇒ 它已被重新采纳 ⇒ 重审计数归零。
            self._skip_age.pop(key, None)
            out.append(c)
        #: ⛔ 三态分开（第 10 条）：`ok` / `degraded`（读崩 ⇒ 放行）/
        #:    `no_memory`（压根没记忆）。合成一个 bool 就会把「拦不住」读成
        #:    「没撞上」。
        recall = "ok" if self._last_recall_ok else "degraded"
        #: ⛔ 第一版这里写成 `len(self.skipped) - _skipped_before`，理由是
        #:    "probation 放行的候选会漏算" —— **那个理由是错的**：被放行的
        #:    候选也在 `out` 里 ⇒ `len(caps) - len(out)` 恒等于本轮 skip 数
        #:    （既有契约 `contract_experience_loop_test` 的 B6 正是断言此式）。
        #:    ⇒ 撤回：不为一句想错的理由引入与既有断言冲突的第二种算法。
        #:    ⛔ 真正需要的独立读数是 **probation_released**（下面），
        #:      因为"放行过谁"确实无法从两个长度里推出来。
        n_skipped = len(caps) - len(out)
        self._emit(self.EVENT_PROPOSE, {
            "asked": self.asked,
            "recall": recall,
            "n_in": len(caps), "n_out": len(out),
            "n_skipped": n_skipped,
            "n_known_history": len(rejected),
            #: ⛔ 出口走了几次必须出得来（第 35d 条）：只活在内存里 ⇒
            #:    "这个候选为什么这轮又出现了"在账本里查不到。
            "retry_every": self.retry_every,
            "probation_released": list(self.probation_released),
            "skipped_sample": [c.name for c in caps
                               if c not in out][:self.EVIDENCE_WINDOW],
            #: ⛔ 两个名单**都要**（r58）：
            #:    `proposed` = 池子原样（含顺序）⇒ 能回答"这轮考虑了谁"；
            #:    `accepted` = 过筛后的名单 ⇒ 能回答"谁真的进了闸"。
            #:    只记后者 ⇒ "被永久跳过的候选"在账本里从来没出现过
            #:    （r54 的故障正是这样被藏住的）。
            "proposed": [c.name for c in caps],
            "accepted": [c.name for c in out]})
        return out

    def _rejected_for(self, weakness: Dict[str, Any]) -> List[Tuple[str, str]]:
        """取出与本轮弱点相关的失败史（变异名 + 被拒原因）。"""
        self._last_recall_ok = True
        if self.memory is None:
            return []
        try:
            notes = self.memory.search(
                f"{self.marker} 被拒 overfit partial 变异", top_k=8)
        except Exception:  # noqa: BLE001
            #: ⛔ 以前这里静默 `return []` ⇒ "检索崩了"和"没有失败史"在后续
            #:    完全不可分辨 ⇒ 本轮照提重复候选，而账本显示绿色运行。
            self._last_recall_ok = False
            return []
        out: List[Tuple[str, str]] = []
        for nt in notes or []:
            body = nt.content or ""
            if f"{self.marker}:" not in body:
                continue
            key = body.split(f"{self.marker}:", 1)[1].split(" ", 1)[0]
            cap = key.rsplit("|", 1)[-1]
            why = body.split("被拒（", 1)[-1].rstrip("）。") \
                if "被拒（" in body else "被闸拒"
            if cap and (cap, why) not in out:
                out.append((cap, why))
        return out

    def diagnostics(self) -> Dict[str, Any]:
        inner = self.inner.diagnostics() if hasattr(self.inner, "diagnostics") \
            else {"name": getattr(self.inner, "name", "unknown")}
        return {**inner, "memory_skipped": list(self.skipped),
                "memory_skipped_n": len(self.skipped),
                "propose_calls": self.asked}


def _missing_tool_of(weakness: Dict[str, Any]) -> str:
    """从失败证据里解析**真正缺的**工具名。

    ⛔ 不能写死一个默认工具名：那会让"缺 python_exec"的失败去开
       `read_file` ⇒ 提议与病因错配 ⇒ 候选永远修不好，但**看起来在动**
       （最难发现的一种空转）。
    生产者：`agent.require_tools` 闸 ⇒ `PermissionError: 缺工具 ['read_file']`。
    """
    for ev in (weakness.get("evidence") or []):
        m = re.search(r"缺工具\s*\[([^\]]*)\]", str(ev))
        if m:
            names = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
            if names:
                return names[0]
    #: 兜底：调用方显式给出的工具名（手工构造的 weakness / 旧路径）。
    return str(weakness.get("tool") or "")


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
            #: ⛔⛔ 路由是**链**：新技能 `append` 到现有链尾，不是 `set` 覆盖。
            #:    2026-10-14 实测：set 覆盖 ⇒ 装一个新技能就顶掉旧的 ⇒
            #:    两个通用能力维度（控制流 / 字面量）互斥 ⇒ dev 分层的
            #:    easy 从 +1.0 掉到 −1.0 ⇒ 候选永远过不了闸 ⇒ 闭环停滞
            #:    （旧读数：round2/3 `no_candidate_passed`）。
            #:    append 之后能力**可叠加**，旧的仍在链上。
            cur = parse_route(harness.routing.get(self.route_key, ""))
            # ⛔ 已在链上的技能不再是"弱点" ⇒ 不再重复提（否则提议器会一直
            #    提同一个东西，进化循环看起来在动，实际原地踏步）。
            for sname, body in [(s, b) for s, b in self.skills.items()
                                if s not in cur][:n]:
                # ⛔ 胶囊（不是单基因）：技能 + 路由**必须一起改**。
                #    只加技能 ⇒ 路由没接通 ⇒ 技能不可达 ⇒ reward 不变
                #    ⇒ 过不了能力地板。这正是 Capsule 存在的理由，
                #    由 tests/contract_agent_test.py 的 L1 组实测守住。
                #: ⛔ target 路径**不在这里写**（2026-10-14 收口）：此前这里
                #:    手写 f"L3.skills.{sname}"，与 genome 的构造器各写一份。
                #:    两处同形却互不相干 ⇒ 改格式必漏一处，且漏的那处不报错。
                out.append(Capsule(
                    name=f"add_skill:{sname}",
                    genes=(gene_add_skill(sname, body, rationale=f"弱点={kind}"),
                           gene_route_append(
                               self.route_key, sname,
                               rationale=f"新技能**追加**到现有链 {list(cur)} 尾"
                                         f"（按任务类别 {self.route_key} 路由，"
                                         f"不按环境名；不覆盖已有能力）")),
                    rationale=f"补技能 {sname} 并接通路由", origin="mined"))

        if kind == "missing_tool":
            #: ⛔ 工具名必须从**失败证据**里取真正缺的那个（见 `_missing_tool_of`）。
            #:    取不到 ⇒ 不提（走 noop 哨兵）而不是开一个可能不需要的工具：
            #:    "开错工具"会把 L2 层的爆炸半径花在一个不解决病因的改动上。
            tool = _missing_tool_of(weakness)
            if tool:
                out.append(Capsule(
                    name=f"tool:{tool}",
                    genes=(gene_tool(tool,
                                     rationale="工具不可达（L2 闸拦下）"),),
                    rationale=f"开放工具 {tool}", origin="mined"))

        if kind == "recall_miss":
            #: ⛔⛔ 当前**没有自动分类路径**能产出这个 kind（见
            #:    `evolution._ERROR_TO_KIND` 上方的纪律说明）：L4 通路只在
            #:    "链上无技能"时才走，而那时真正的缺口是**技能**（加技能是更
            #:    根本的修法）。这里保留为**显式接口** —— 供未来的 LLM 提议器
            #:    或人工干预直接指定 `kind="recall_miss"`。它不是假仪表：
            #:    入口存在，且 contract_evolution_test 的 J 组做反证
            #:    （指定它 ⇒ 真的产出含 memory_policy 的 L4 变异）。
            out.append(Capsule(
                name="tune:recall",
                genes=(gene_tune("recall_topk", 2,
                                 rationale="召回不足 ⇒ 扩大 topk"),
                       gene_tune("min_content_len", -20,
                                 rationale="下限过高挡住了短知识")),
                rationale="调记忆召回参数", origin="mined"))

        if not out:
            out.append(Capsule(name="noop", genes=(), rationale="无可用变异",
                               origin="mined"))
        return out[:max(1, n)]


# ──────────────────────────────────────────────────────────────────
# 清理提案器（常设，不看 weakness）
# ──────────────────────────────────────────────────────────────────
@dataclass
class CleanupProposer:
    """清理提案器：把"收拾自己的产出"变成**可被提议的变异**。

    ═══ 它与其它提议器最不一样的一点：**它不看 weakness** ═══

        · `TemplateProposer` / `LLMProposer` 是 **weakness-driven**：
          有失败证据 ⇒ 补能力。dev 全绿 ⇒ 它们无事可做。
        · 码健康是**维护轴**，不是失败信号：它不会因为 dev 全绿就消失。
          ⇒ 本提案器是**常设**的（standing proposer），**每轮都跑**。

    ⛔ 存在理由（努力分配）：
       进化循环每轮都在**评估候选**，而没有任何东西在自己的产出上花一点力。
       机制上是因为**判据只认增益、不认减负** —— 删掉一个死条目 reward
       不变 ⇒ 按现有判据它永远提不出来 ⇒ "通过测试但不产生增益"的机制
       只增不减。本提案器把"减负"变成候选，但**能不能提交仍由闸决定**。

    ⛔⛔ 它**不自己判**，候选走**同一套闸**（由 `evolution` 保证）：
         · 非回归闸 —— 删错了东西，冻结套件会响；
         · 泛化闸的**退化面** —— held-out 不许变差（不要求涨）；
         · 两轴准入 —— reward 涨 **或** 自指负载降，且不许伴随 reward 下降。
       ⇒ 它不是"绕过闸的清理通道"。想靠它把有用的东西删掉，闸照样会响。

    ⛔ 保守：扫描挂在 `cleanup.genome_dead_entries`（与 `agent` 词汇表同源），
       扫不到就**不提**（返回空列表）—— 不提 ≠ 没问题，调用方要能分辨。
    """
    name: str = "cleanup"

    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 1) -> List[Capsule]:
        #: ⛔ 显式忽略 weakness。形参保留是为了与其它提议器**同协议**
        #:    （可互换：`MemoryAwareProposer` 包它、evolution 统一调
        #:    `propose(w.to_dict(), h, n=...)`）。不看 weakness 是特性。
        del weakness
        try:
            scan = _cleanup.genome_dead_entries(harness)
        except Exception:                              # noqa: BLE001
            #: ⛔ 扫描挂了 ⇒ 这一轮不提清理。**不抛** —— 编码层的故障
            #:    不该让进化循环在最需要保守停手的那一刻整体炸掉。
            return []
        out: List[Capsule] = []
        for entry in (scan.get("dead_entries") or [])[:max(1, n)]:
            genes = _cleanup.cleanup_genes(harness, entry)
            if not genes:
                continue
            out.append(Capsule(
                name=f"cleanup:{entry.get('kind')}:{entry.get('name')}",
                genes=genes,
                rationale=str(entry.get("reason") or "")[:300],
                origin="cleanup"))
        return out

    def diagnostics(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": "standing（不看 weakness）"}


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
            #: ⛔⛔ 2026-10-13 实测：模型在第 3 个 capsule 里塞了一整段源码
            #    ⇒ 超预算被截断 ⇒ 整个 JSON 作废 ⇒ **前两个合法的 capsule
            #    一起被丢掉**（"一个坏 capsule 拖垮整批"）。
            #    整批作废会让"模型啰嗦"被记成"模型没产出" —— 两者处置不同。
            #    降级为逐对象扫描：完整的保留，截断的丢弃并记账。
            salvaged = _salvage_objects(text, start, max_genes=max_genes)
            if salvaged:
                rejects.append(
                    f"partial_parse:truncated_tail（整批被截断 ⇒ 逐对象抢救，"
                    f"保留 {len(salvaged)} 个完整 capsule，丢弃 1 个截断的）")
                return salvaged, rejects
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


def _salvage_objects(text: str, start: int, max_genes: int = 4) -> List[Capsule]:
    """整体 JSON 被截断时，逐对象抢救出**完整的** capsule。

    ⛔ 纪律不松动：每个被保留的 capsule 自身都是完整解析 + 过语法闸的。
    这里做的是**隔离**（把坏的那个单独丢掉），不是"返回半成品" ——
    宁可少一个候选，也不让一条截断的基因混进合法候选里伪装成"试过了没用"。

    ⛔ 抢救必须**显式记账**：调用方要能看出"这一批丢了多少个"，
    否则"模型只提了 2 个"和"模型提了 3 个、1 个被截断丢掉"无法区分。
    """
    #: 定位到数组起点（"capsules": [ 之后），逐个平衡扫描子对象
    m = re.search(r'"capsules"\s*:\s*\[', text[start:start + 200])
    scan_from = start + m.end() if m else start
    out: List[Capsule] = []
    dropped = 0
    i = scan_from
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        blob = _scan_json_object(text, i)
        if blob is None:
            dropped += 1          #: 截断在这个对象里 ⇒ 它和后面的都救不了
            break
        try:
            it = json.loads(blob)
        except Exception:  # noqa: BLE001
            dropped += 1
            i += 1
            continue
        cap = _capsule_from_item(it, max_genes=max_genes)
        if cap is None:
            dropped += 1
        else:
            out.append(cap)
        i += len(blob)
    if not out:
        return []
    #: ⛔ 记账写进 capsule 自身（frozen ⇒ 用 replace）：丢弃数必须跟着
    #   候选走到账本里，否则"这一批丢了多少"在账本上查不到。
    return [replace(c, rationale=(c.rationale or "")
                    + f" [salvaged:kept={len(out)},dropped={dropped}]")
            for c in out]


def _capsule_from_item(it: Any, max_genes: int = 4) -> Optional[Any]:
    """把一个已解析的 capsule 字典过语法闸（与整批路径**同一套判据**）。"""
    if not isinstance(it, dict):
        return None
    from .genome import Capsule, Gene
    name = str(it.get("name") or "").strip()
    genes_raw = it.get("genes")
    if not name or not isinstance(genes_raw, list) or not genes_raw:
        return None
    genes: List[Any] = []
    for g in genes_raw:
        ok, why = validate_gene(g)
        if not ok:
            return None        #: ⛔ 一条非法 ⇒ 整个 capsule 丢（不返回半成品）
        genes.append(Gene(op=str(g.get("op")), target=str(g.get("target")),
                          value=g.get("value"),
                          rationale=str(g.get("rationale") or "")))
    if len(genes) > max_genes:
        return None
    return Capsule(name=name, genes=tuple(genes),
                   rationale=str(it.get("rationale") or ""),
                   origin=str(it.get("origin") or "llm"))


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
  L1.routing.<键>          任务类别 → 技能名（或 `技能A|技能B` 表示按顺序叠加）
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


def build_prompt(weakness: Dict[str, Any], harness: Harness, n: int = 3,
                 route_keys: Sequence[str] = (),
                 skill_vocab: Sequence[str] = (),
                 env_ids: Sequence[str] = (),
                 rejected: Sequence[Tuple[str, str]] = (),
                 heldout_note: str = "") -> str:
    """把"当前 harness + 弱点 + **接口契约**"讲清楚，让模型有依据而不是瞎猜。

    ⛔⛔ 2026-10-13 实测：不给契约时，Qwen3.5-4B 输出**结构完全合法**的 capsule
        （甚至自己在 rationale 里写了"需同步添加路由"），但两处猜错：

          · 路由键写成技能名 `L1.routing.textscore_eval`
            —— 真实键是**任务类别**（如 `repair`）；
          · 技能正文写自然语言描述 `L3.skills.xxx`
            —— 真实技能名只有 `fix_control_flow` / `patch::<env_id>`。

        结果：过闸 reward=0 ⇒ `no_advantage`，系统看起来"闸在工作"，
        实际是**模型不知道接口**。这不是模型能力问题，是 prompt 缺 API 文档。

    ⛔ 契约**由调用方注入**，不在这里硬编码：proposers 是通用模块，
       环境 / 任务类别 / 可用技能是部署的事。硬编码 ⇒ 换个环境就骗模型。
    """
    contract: List[str] = []
    if route_keys:
        contract.append(
            "路由键必须是**任务类别**，不是技能名。本系统当前的任务类别："
            + "、".join(f"`{k}`" for k in route_keys)
            + "。写成别的键 ⇒ 技能永不可达。")
    if skill_vocab:
        contract.append(
            "可用的技能名**只有**这些（写别的名字 ⇒ 空转，reward 为 0）："
            + "、".join(f"`{s}`" for s in skill_vocab) + "。")
    if env_ids:
        contract.append("已知环境 id：" + "、".join(f"`{e}`" for e in env_ids)
                        + "（`patch::<env_id>` 只在点名的环境生效）。")
    #: ⛔⛔ 评估规则 vs 解法：这里只能讲**规则**，绝不能讲解法。
    #    讲"要在另一个环境上验证、只对当前环境有效的会被拦"是规则；
    #    讲"你应该提通用修法 fix_control_flow"是**预置答案** ——
    #    那会把"这个系统还不会修"这个真实弱点掩盖掉（与 V6 那条
    #    "记忆只能由执行填充"是同一条纪律：外部预置的知识会让 reward
    #    顶到天花板 ⇒ 进化永久空转）。模型能不能自己想到，是它要考的试。
    rules: List[str] = list(contract)
    if heldout_note:
        rules.append(heldout_note)
    if rejected:
        rules.append(
            "这些变异**已经试过并被闸拦下**，不要重复提（提了也会被拦，"
            "等于浪费一次机会）：\n  "
            + "\n  ".join(f"· `{name}` —— {why}" for name, why in rejected[:8]))
    return (
        "当前 harness 状态（JSON）：\n"
        f"{json.dumps(harness.to_dict(), ensure_ascii=False)[:1500]}\n\n"
        "观测到的弱点：\n"
        f"{json.dumps(weakness, ensure_ascii=False)[:800]}\n\n"
        + ("⚠️ 规则与接口（违反 ⇒ 该 capsule 无效或被拦）：\n- "
           + "\n- ".join(rules) + "\n\n" if rules else "")
        + f"请提出最多 {n} 个候选 capsule，每个 capsule 是一组**必须一起生效**的编辑"
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
    #: 完整原始输出（⛔ 只记账不参与判定；没有它，"模型猜错接口"和
    #:   "模型没输出"在诊断里长得一样）
    last_raw: str = ""
    #: 接口契约，**由调用方注入**（见 `build_prompt` 的实测注释）
    route_keys: Tuple[str, ...] = ()
    skill_vocab: Tuple[str, ...] = ()
    env_ids: Tuple[str, ...] = ()
    #: ⛔ 评估规则**只讲判据**，不讲答案（见 `build_prompt` 的纪律注释）
    heldout_note: str = ""

    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3, rejected: Sequence[Tuple[str, str]] = ()
                ) -> List[Capsule]:
        self.last_error = ""
        self.last_rejects = []
        self.last_raw_len = 0
        self.last_raw_tail = ""
        self.last_raw = ""
        self.n_retries_used = 0
        self.last_rejected = list(rejected)
        prompt = build_prompt(weakness, harness, n,
                              route_keys=self.route_keys,
                              rejected=rejected,
                              heldout_note=self.heldout_note,
                              skill_vocab=self.skill_vocab,
                              env_ids=self.env_ids)
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
            self.last_raw = raw or ""
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
                "raw": self.last_raw[:600],
                "retries_used": self.n_retries_used,
                #: ⛔ 注入了什么必须可查：否则"模型没听进去"和
                #    "根本没告诉它"在诊断里长得一样
                "rejected_shown": [n for n, _ in getattr(self,
                                                         "last_rejected", ())],
                "heldout_note_shown": bool(self.heldout_note)}

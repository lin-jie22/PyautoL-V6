"""示例环境：文本打分工具（V6 自带的可验证任务源）。

为什么自带一个 toy 环境而不是直接接 SWE-bench：
    SWE-smith 的核心洞察是**先建共享环境、再合成任务**。这个 toy 环境
    就是那套结构的缩小版 —— 它能在秒级完成"变异 → 执行 → F2P 筛选"，
    于是进化循环可以离线回归，不用等 Docker。

它同时也是 V5 血脉的延续：`is_hollow()` 就是 V5 的 `artifact_is_hollow`
（写入端"降级成功 ≠ 可用"那条纪律）的可执行形态。
"""


def clamp(x, lo=0.0, hi=1.0):
    """把 x 夹到 [lo, hi]。"""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def normalize_scores(scores):
    """归一化为概率分布；总和 <= 0 ⇒ 空字典（不是除零崩溃）。"""
    total = sum(scores.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in scores.items()}


def top_n(pairs, n=3):
    """取分数最高的 n 个 (key, score) 对，稳定降序。"""
    ranked = sorted(pairs, key=lambda kv: kv[1], reverse=True)
    return ranked[:n]


def is_hollow(text):
    """产物是不是"空壳"：空 或 只有占位词。

    V5 血泪的浓缩：`.txt 文件被错判为成功`、空 content 被当成交付。
    ⛔ 这个判据必须**可执行**——否则它就只是文档里的一句口号。
    """
    t = (text or "").strip()
    if not t:
        return True
    return t.lower() in {"pass", "ok", "done", "success", "n/a", "todo"}


def merge_keywords(*groups):
    """合并多组关键词，去重且保持首次出现顺序。"""
    out = []
    seen = set()
    for g in groups:
        for k in g or ():
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out

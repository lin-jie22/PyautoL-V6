"""示例环境 2：事件流聚合（与 textscore **不同任务族**的可验证任务源）。

为什么必须有第二个环境：
    单个环境上的"进步"无法区分两件事 ——
        (a) harness 真的变强了；
        (b) harness 只是学会了讨好这一个环境的判据（reward hacking 的温和形态）。
    只有把候选放到**没参与过选择**的另一个环境上，这两件事才能被分开。

这是 V6 泛化闸（generalization.py）的燃料：held-out 环境**从不参与**
候选之间的比较，只在裁决时用一次。
"""


def sessionize(events, gap=30):
    """按时间间隔切分会话：相邻事件间隔 > gap 则断开。

    返回按事件 id 组成的会话列表（空输入 ⇒ 空列表，不是崩溃）。
    """
    sessions = []
    cur = []
    prev_ts = None
    for e in sorted(events, key=lambda e: e["ts"]):
        if prev_ts is not None and e["ts"] - prev_ts > gap:
            sessions.append(cur)
            cur = []
        cur.append(e["id"])
        prev_ts = e["ts"]
    if cur:
        sessions.append(cur)
    return sessions


def dedup_stream(events):
    """按 event id 去重，保留首次出现的顺序。"""
    out = []
    seen = set()
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e["id"])
    return out


def rolling_count(events, window=60):
    """对每个事件时刻，返回 [t-window, t] 窗口内（含自身）的事件数。"""
    ts = sorted(e["ts"] for e in events)
    counts = []
    for t in ts:
        counts.append(sum(1 for x in ts if t - window <= x <= t))
    return counts


def pct(values, q=50):
    """线性插值分位数；空输入 ⇒ 0.0（不是除零崩溃）。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)

"""示例环境 4：区间调度（冲突检测 / 最大重叠 / 贪心分配 / 区间合并）。

与前三者的差别不只是"领域不同"，而是**解法形状**不同：
    textscore   数值归一化（算术）
    eventflow   流式聚合（遍历 + 窗口）
    stateflow   离散图搜索（BFS）
    scheduler   扫描线 + 贪心（排序 + 计数）

变异（比较符 / 运算符 / 常量）在这四种形状上造成的失败模式不同，
于是同一个"修法"在四个环境上的表现差异才是真信号，而不是噪声。
"""


def _norm(intervals):
    """把 [start, end] 归一化成元组并排序。空输入 ⇒ 空。"""
    return sorted((int(a), int(b)) for a, b in intervals)


def has_conflict(intervals):
    """是否存在任意两个区间重叠（相邻不算重叠：[1,2] 与 [2,3] 不冲突）。"""
    iv = _norm(intervals)
    for i in range(1, len(iv)):
        if iv[i][0] < iv[i - 1][1]:
            return True
    return False


def max_overlap(intervals):
    """任一时刻的最大并发数（扫描线）。空 ⇒ 0。"""
    events = []
    for a, b in intervals:
        if b <= a:
            continue
        events.append((a, 1))
        events.append((b, -1))
    events.sort()
    cur = best = 0
    for _t, d in events:
        cur += d
        if cur > best:
            best = cur
    return best


def min_rooms(intervals):
    """最少需要几个资源（= 最大并发）。"""
    return max_overlap(intervals)


def can_schedule(intervals, capacity):
    """给定容量能否全部安排下。容量 <= 0 且区间非空 ⇒ False。"""
    if not intervals:
        return True
    if capacity <= 0:
        return False
    return max_overlap(intervals) <= capacity


def merge_intervals(intervals):
    """合并所有重叠区间（相邻也合并：[1,2] 与 [2,3] ⇒ [1,3]）。"""
    iv = _norm(intervals)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return out


def total_busy(intervals):
    """合并后的总占用时长（重叠只算一次）。"""
    return sum(b - a for a, b in merge_intervals(intervals))


def utilization(intervals, horizon, capacity=1):
    """资源利用率 = 总占用 / (容量 × 时间窗)。horizon<=0 ⇒ 0.0。"""
    if horizon <= 0 or capacity <= 0:
        return 0.0
    return total_busy(intervals) / (capacity * horizon)


def greedy_select(intervals):
    """按结束时间贪心选出**互不重叠**的最大集合，返回原索引（升序）。

    经典活动选择：按结束时间升序，能选就选。
    """
    if not intervals:
        return []
    order = sorted(range(len(intervals)), key=lambda i: intervals[i][1])
    chosen = []
    last_end = None
    for i in order:
        a, b = intervals[i]
        if last_end is None or a >= last_end:
            chosen.append(i)
            last_end = b
    return sorted(chosen)


def free_slots(intervals, horizon, slot=1):
    """在 [0, horizon) 内找出长度 >= slot 的空闲时段数量。"""
    busy = merge_intervals(intervals)
    gaps = []
    cursor = 0
    for a, b in busy:
        if a > cursor:
            gaps.append((cursor, a))
        if b > cursor:
            cursor = b
    if cursor < horizon:
        gaps.append((cursor, horizon))
    return sum(1 for a, b in gaps if b - a >= slot)

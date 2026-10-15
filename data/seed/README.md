# 种子数据（随仓库发布的最小现场）

⛔ **为什么需要它**：`.gitignore` 排除了 `data/*.jsonl` / `data/*.db` /
`data/*.json`（账本 / 投影 / 记忆都是运行产物，不该进版本库）。
后果是 **`git clone` 得到的是一个"没有事实源"的系统**：
`doctor` 会报"账本文件不存在"、demo 跑不起来、README 里引用的读数也不存在。

这份种子给新克隆者一个**能立刻跑通**的最小现场，而且它自身**有出处**
（每条账本的第一条事件带 `src_version` / `format_version`），
所以 `provenance` 能判读它、`doctor` 能对它做体检。

## 里面有什么

| 文件 | 是什么 |
|---|---|
| `data/ledger.jsonl` | 4 条事件（`harness.init` + `eval.verdict` + 2 条 `memory.write`），哈希链完整、带出处 |
| `data/memory.json` | 2 条够长的 L3 记忆，**有账本痕迹**（所以 `memory show` 报 `complete` 而不是 `pre_ledger`） |

**没有投影库**（`aether.db`）—— 这是刻意的：库只是视图，用下面这条命令
从账本重放出来，顺便证明"库可由账本重建"这件事在种子上也成立。

## 怎么用

```bash
PY=/path/to/python3          # 见根 README 的说明（只用 stdlib）

# ① 直接体检这个现场（注意：`--root` 在子命令前后都可以写）
$PY -m aether --root data/seed doctor
$PY -m aether doctor --root data/seed

# ② 从账本重建投影（唯一正路：库只是视图）
$PY -m aether --root data/seed rebuild

# ③ 校验链与出处
$PY -m aether --root data/seed verify-chain
$PY -m aether --root data/seed status        # 里面有 ledger.provenance 读数

# ④ 想在自己那儿开一个新现场：把这份 layout 复制过去再跑 rebuild
```

## 已知读数（不要当"系统有问题"）

在**没有备份目录**的情况下，`doctor` 会报 `backup.present: fail`
（"没有任何备份（唯一事实源无退路）"）—— 对种子数据这是**正确**的，
它正是 doctor 该报的：备份平时用不上，等要用时才发现从来没做过。
真现场里跑一次 `aether backup` 就会转绿。

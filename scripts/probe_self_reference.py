#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自指负载探针 —— **`aether.selfref` 的 CLI 薄壳**。

⛔ 扫描判据只有一份（在 `aether/selfref.py`）：`aether/cleanup.py` 也要用它
   做"这次清理到底减少了多少自指负载"的判据。脚本自己再写一份就是
   第二个事实源（同族教训：`run_contracts.py` 与 `aether.check` 收敛过一次）。

用法：
    python scripts/probe_self_reference.py            # 人读表
    python scripts/probe_self_reference.py --json     # 结构化
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.selfref import _main                          # noqa: E402

if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

#!/usr/bin/env python3
"""示例外部工具（CLI 形态）：stdin 收 JSON，stdout 吐 JSON。

这是接入层的最小契约 —— **任何语言写的**可执行文件，只要遵守
"stdin JSON args → stdout JSON output" 就能被 V6 调用：

    echo '{"text":"abc"}' | python3 scripts/ext_reverse.py
    → {"out": "cba"}
"""
import json
import sys


def main() -> int:
    args = json.loads(sys.stdin.read() or "{}")
    print(json.dumps({"out": str(args.get("text", ""))[::-1]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

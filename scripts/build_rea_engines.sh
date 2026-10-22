#!/bin/sh
# 构建 REA 所需的 Ghidra **原生反编译器**（macOS / Apple Silicon）。
#
# 背景（2026-10-15 实测取证，详见 docs/ARCHITECTURE.md §34.10）：
#
#   ⛔⛔ Ghidra 12.1.4 的官方发布包 **不含** macOS arm64 的原生反编译器
#      （`Ghidra/Features/Decompiler/os/` 下只有 `linux_x86_64` 和 `win_x86_64`）。
#      但 `DecompInterface.openProgram()` 一上来就要跟原生 `decompile`
#      子进程握手 ⇒ 没有它，REA 的 ghidra 通道 100% 挂死在
#      `DecompileProcess.readToBurst`，表现为 `provider_timeout`。
#
#   ⛔⛔ `src/decompile/cpp/` 里有**两个 main()**，编错目标不会报错，只会静默挂死：
#        consolemain.cc   → decomp_opt / decomp_dbg   ← 独立**交互控制台**（错的）
#        ghidra_process.cc → ghidra_opt / ghidra_dbg   ← **Ghidra 管道协议**（对的）
#      ⛔ 判据不是"能不能编过"，而是**安装目标**：
#        `cp ghidra_opt $(GHIDRA_BIN)/Ghidra/Features/Decompiler/os/$(OSDIR)/decompile`
#
# 用法：
#     sh scripts/build_rea_engines.sh
#
# 产物安装到两个候选位置（REA 两个都认，见 §34.10）：
#     <install>/Ghidra/Features/Decompiler/os/mac_arm_64/decompile
#     <install>/Ghidra/Features/Decompiler/build/os/mac_arm_64/decompile

set -eu

#: ⛔ 不写死安装路径（r67）：写死 ⇒ 别人 clone 下来跑这个脚本，得到的是
#:    "找不到 Ghidra/Features/..."，看起来像脚本坏了。
#:    顺序：环境变量 → 家目录下的 ghidra_* 目录（**只认目录**，`ghidra_*.zip`
#:    在字典序上更大，不过滤就会把压缩包当安装目录）。
INSTALL_DIR="${GHIDRA_INSTALL_DIR:-}"
if [ -z "$INSTALL_DIR" ]; then
  INSTALL_DIR=$(ls -d "$HOME"/tools/ghidra_*/ 2>/dev/null | head -1 || true)
  INSTALL_DIR="${INSTALL_DIR%/}"
fi
if [ ! -d "$INSTALL_DIR" ]; then
  echo "build_rea_engines: 找不到 Ghidra 安装目录 —— 设 GHIDRA_INSTALL_DIR" >&2
  exit 2
fi
CPP_DIR="$INSTALL_DIR/Ghidra/Features/Decompiler/src/decompile/cpp"
NATIVE_DIR="mac_arm_64"
BUILT="$CPP_DIR/ghidra_opt"

if [ ! -d "$CPP_DIR" ]; then
    echo "!! 找不到 Ghidra 源码树：$CPP_DIR" >&2
    echo "   先下载 Ghidra 12.1.4 发布包并解压（发布包自带 src/decompile/cpp）。" >&2
    exit 2
fi

cd "$CPP_DIR"

echo "=== 编译 ghidra_opt（目标 arm64） ==="
# ⛔ 为什么必须覆盖这三个变量：官方 Makefile 的 Darwin 分支硬编码了
#      ARCH_TYPE=-arch x86_64
#      OSDIR=mac_x86_64
#      ADDITIONAL_FLAGS=-mmacosx-version-min=10.6 -w
#    且分支上方挂着原作者 TODO，明说不支持 arm64/aarch64：
#      "# TODO: need to revise to support arm64/aarch64 arch - improve on both OS and arch detection"
#    ⇒ 不覆盖就会静默产出 x86_64 产物并装进 mac_x86_64/。
#    ⇒ 且 10.6 这个最低版本在 arm64 上不合法，必须抬到 11.0。
make ghidra_opt \
    ARCH_TYPE="-arch arm64" \
    OSDIR="$NATIVE_DIR" \
    ADDITIONAL_FLAGS="-mmacosx-version-min=11.0 -w"

if [ ! -x "$BUILT" ]; then
    echo "!! 未产出 $BUILT" >&2
    exit 1
fi

echo
echo "=== 产物自检 ==="
file "$BUILT"

if command -v lipo >/dev/null 2>&1; then
    arch="$(lipo -archs "$BUILT" 2>/dev/null || echo unknown)"
    echo "archs: $arch"
    case "$arch" in
        *arm64*) ;;
        *) echo "!! 产物不是 arm64：$arch" >&2; exit 1 ;;
    esac
fi

echo
echo "=== 安装 ==="
DEC="Ghidra/Features/Decompiler"
install -d "$INSTALL_DIR/$DEC/os/$NATIVE_DIR"
install -d "$INSTALL_DIR/$DEC/build/os/$NATIVE_DIR"
install -m 755 "$BUILT" "$INSTALL_DIR/$DEC/os/$NATIVE_DIR/decompile"
install -m 755 "$BUILT" "$INSTALL_DIR/$DEC/build/os/$NATIVE_DIR/decompile"

echo "  -> $INSTALL_DIR/$DEC/os/$NATIVE_DIR/decompile"
echo "  -> $INSTALL_DIR/$DEC/build/os/$NATIVE_DIR/decompile"
echo
echo "=== 冒烟（空 stdin 应立即退出，不应打印 '[decomp]> ' 提示符）==="
out="$(: | "$INSTALL_DIR/$DEC/os/$NATIVE_DIR/decompile" 2>&1 || true)"
case "$out" in
    *"decomp]"*) echo "!! 拿到了控制台提示符 ⇒ 编的仍是 consolemain 版" >&2; exit 1 ;;
    *) echo "ok：无控制台提示符" ;;
esac
echo "done."

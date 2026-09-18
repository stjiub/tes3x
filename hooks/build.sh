#!/usr/bin/env sh
# Build the injected payload and patch it into a copy of the retail XBE.
#
# The payload is linked at the exact VA the new XBE section will land at, so absolute
# references resolve without relocations. Ask the injector for that VA first.
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
OUT=${OUT:-$ROOT/build/hooks}
CLANG=${CLANG:-/c/msys64/mingw64/bin/clang.exe}
LLD=${LLD:-/c/msys64/mingw64/bin/lld-link.exe}
SRCS=${SRCS:-"tes3xhook.c tes3xlog.c"}

XBE=${1:?usage: build.sh <input.xbe> [output.xbe]}
DEST=${2:-$OUT/morrowind.xbe}

mkdir -p "$OUT"

VA=$(python "$ROOT/tools/tes3x_inject.py" "$XBE" --next-va | tail -1)
ENTRY=$(python - "$XBE" <<'PY'
import sys, struct
d = open(sys.argv[1], 'rb').read()
print("0x%08X" % (struct.unpack_from('<I', d, 0x128)[0] ^ 0xA8FC57AB))
PY
)

echo "section VA $VA   original entry $ENTRY"

python "$ROOT/tools/tes3x_inject.py" "$XBE" --dump-thunks "$HERE/tes3x_thunks.h" >/dev/null

# The archive hook stands in for Archive::Load at its one call site, so it needs that
# function's address; read it out of the binary rather than hardcoding it here.
INJECT_EXTRA=""
case " $SRCS " in
*" tes3xarch.c "*)
    ARCH_SITE=${ARCH_SITE:-0x000D4E06}
    ARCH_LOAD=$(python "$ROOT/tools/tes3x_inject.py" "$XBE" --print-call "$ARCH_SITE" | tail -1)
    echo "archive hook: call site $ARCH_SITE -> Archive::Load $ARCH_LOAD"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_ARCHIVE_LOAD=$ARCH_LOAD"
    ;;
esac

# The script hook stands in front of Script::RunFunction at all three of its call sites, so it
# needs that function's address. tes3x_patch.py locates it by signature - the same search the
# patcher uses, so the payload and the patch cannot disagree about which function it is.
case " $SRCS " in
*" tes3xscript.c "*)
    RUN_FUNCTION=$(python "$ROOT/tools/tes3x_patch.py" "$XBE" --locate run-function | tail -1)
    COMMAND_TABLE=$(python "$ROOT/tools/tes3x_patch.py" "$XBE" --locate command-table | tail -1)
    OPCODE_BASE=$(sed -n 's/^#define TES3X_OPCODE_BASE  *//p' "$HERE/tes3xscript.c")
    OPCODE_CEIL=$(sed -n 's/^#define TES3X_OPCODE_CEIL  *//p' "$HERE/tes3xscript.c")
    echo "script hook: RunFunction $RUN_FUNCTION, table $COMMAND_TABLE, opcodes [$OPCODE_BASE, $OPCODE_CEIL)"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_RUN_FUNCTION=$RUN_FUNCTION"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_COMMAND_TABLE=$COMMAND_TABLE"
    ;;
esac

# The console hook replaces the engine's own action check at the one call site that gates
# Console::Toggle, and reads its combo from the ini through the engine's own reader.
case " $SRCS " in
*" tes3xconsole.c "*)
    INI_GET=${INI_GET:-0x001933E0}
    INI_PATH=${INI_PATH:-0x0035E364}
    CONSOLE_SITE=${CONSOLE_SITE:-0x00098430}
    echo "console hook: gate $CONSOLE_SITE, ini reader $INI_GET, ini path $INI_PATH"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_INI_GET_STRING=$INI_GET -DTES3X_INI_PATH=$INI_PATH"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_FIND_MENU=${FIND_MENU:-0x001AD340}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_OPEN_VK=${OPEN_VK:-0x0022D210}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_CONSOLE_MENU_ID=${CONSOLE_MENU_ID:-0x003D816C}"
    ;;
esac

WOUT=$(cygpath -w "$OUT")
OBJS=""
for src in $SRCS; do
    obj=$(basename "$src" .c).obj
    "$CLANG" -target i386-pc-win32 -march=pentium3 -Os -ffreestanding -nostdlib \
        -fno-builtin -fno-stack-protector -fno-asynchronous-unwind-tables \
        -DTES3X_ORIG_ENTRY="$ENTRY" $EXTRA_CFLAGS -I"$HERE" \
        -c "$HERE/$src" -o "$OUT/$obj"
    OBJS="$OBJS $WOUT\\$obj"
done

# lld-link is a native binary, so hand it Windows paths and stop MSYS rewriting the
# /flags; the python invocations above still want the normal conversion
MSYS2_ARG_CONV_EXCL='*' "$LLD" /nologo /subsystem:native /entry:tes3x_entry /fixed \
    /nodefaultlib /base:"$VA" /map:"$WOUT\tes3xhook.map" /out:"$WOUT\tes3xhook.pe" $OBJS

# lld-link writes a symbol address as the second-to-last field of its map line, 16 hex digits.
sym_va() {
    awk -v want="$1" '$2 == want { print $(NF - 1) }' "$OUT/tes3xhook.map"
}

HOOKS=""
if [ -n "$ARCH_LOAD" ]; then
    HOOK=$(sym_va _tes3x_archive_hook)
    [ -n "$HOOK" ] || { echo "could not find _tes3x_archive_hook in the link map" >&2; exit 1; }
    INJECT_EXTRA="--patch-call $ARCH_SITE=0x$HOOK"
    HOOKS="$HOOKS archive_load=0x$HOOK"
fi
if [ -n "$RUN_FUNCTION" ]; then
    SHOOK=$(sym_va _tes3x_script_hook)
    [ -n "$SHOOK" ] || { echo "could not find _tes3x_script_hook in the link map" >&2; exit 1; }
    HOOKS="$HOOKS script_dispatch=0x$SHOOK opcode_base=$OPCODE_BASE opcode_ceil=$OPCODE_CEIL"
fi
if [ -n "$CONSOLE_SITE" ]; then
    CHOOK=$(sym_va @tes3x_console_hook@12)
    [ -n "$CHOOK" ] || CHOOK=$(sym_va _tes3x_console_hook)
    [ -n "$CHOOK" ] || { echo "could not find tes3x_console_hook in the link map" >&2; exit 1; }
    HOOKS="$HOOKS console_gate=0x$CHOOK"
fi

# Hook addresses beside the blob, so tes3x_patch.py can apply it without a toolchain or a map.
python - "$OUT/tes3xhook.json" "$VA" $HOOKS <<'MANIFEST'
import json, sys
out, base = sys.argv[1], sys.argv[2]
doc = {"base": base, "hooks": dict(a.split("=", 1) for a in sys.argv[3:])}
json.dump(doc, open(out, "w", encoding="utf-8"), indent=2)
MANIFEST

python "$ROOT/tools/tes3x_inject.py" "$XBE" \
    --payload "$OUT/tes3xhook.pe" --hook-entry $INJECT_EXTRA --out "$DEST"

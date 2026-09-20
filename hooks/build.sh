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
PYTHON=${PYTHON:-python}
SRCS=${SRCS:-"tes3xhook.c tes3xlog.c tes3xdiag.c"}

XBE=${1:?usage: build.sh <input.xbe> [output.xbe]}
DEST=${2:-$OUT/morrowind.xbe}

mkdir -p "$OUT"

VA=$($PYTHON "$ROOT/tools/tes3x_inject.py" "$XBE" --next-va | tail -1)
ENTRY=$($PYTHON - "$XBE" <<'PY'
import sys, struct
d = open(sys.argv[1], 'rb').read()
print("0x%08X" % (struct.unpack_from('<I', d, 0x128)[0] ^ 0xA8FC57AB))
PY
)

echo "section VA $VA   original entry $ENTRY"

$PYTHON "$ROOT/tools/tes3x_inject.py" "$XBE" --dump-thunks "$HERE/tes3x_thunks.h" >/dev/null

BUILD_ID=$($PYTHON - "$HERE" "$EXTRA_CFLAGS" $SRCS <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
flags = sys.argv[2]
names = set(sys.argv[3:] + ["build.sh", "tes3xdiag.h", "tes3xlog.h", "tes3xnt.h",
                             "tes3x_thunks.h"])
h = hashlib.sha256()
h.update(b"flags\0" + flags.encode("utf-8") + b"\0")
for name in sorted(names):
    path = root / name
    if path.exists():
        h.update(name.encode("ascii") + b"\0" + path.read_bytes())
print("0x" + h.hexdigest()[:8])
PY
)
echo "payload build id $BUILD_ID"
EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_BUILD_ID=$BUILD_ID"

# The archive hook stands in for Archive::Load at its one call site, so it needs that
# function's address; read it out of the binary rather than hardcoding it here.
INJECT_EXTRA=""
case " $SRCS " in
*" tes3xarch.c "*)
    ARCH_SITE=${ARCH_SITE:-0x000D4E06}
    ARCH_LOAD=$($PYTHON "$ROOT/tools/tes3x_inject.py" "$XBE" --print-call "$ARCH_SITE" | tail -1)
    echo "archive hook: call site $ARCH_SITE -> Archive::Load $ARCH_LOAD"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_ARCHIVE_LOAD=$ARCH_LOAD"
    ;;
esac

# The script hook stands in front of Script::RunFunction at all three of its call sites, so it
# needs that function's address. tes3x_patch.py locates it by signature - the same search the
# patcher uses, so the payload and the patch cannot disagree about which function it is.
case " $SRCS " in
*" tes3xscript.c "*)
    RUN_FUNCTION=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate run-function | tail -1)
    COMMAND_TABLE=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate command-table | tail -1)
    OPCODE_BASE=$(sed -n 's/^#define TES3X_OPCODE_BASE  *//p' "$HERE/tes3xscript.c")
    OPCODE_CEIL=$(sed -n 's/^#define TES3X_OPCODE_CEIL  *//p' "$HERE/tes3xscript.c")
    echo "script hook: RunFunction $RUN_FUNCTION, table $COMMAND_TABLE, opcodes [$OPCODE_BASE, $OPCODE_CEIL)"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_RUN_FUNCTION=$RUN_FUNCTION"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_COMMAND_TABLE=$COMMAND_TABLE"
    ;;
esac

# Settings come from Morrowind.ini through the engine's own reader, so any hook that takes
# one needs its address and the filename string.
case " $SRCS " in
*" tes3xconsole.c "*|*" tes3xrefs.c "*|*" tes3xdiag.c "*|*" tes3xsaves.c "*)
    INI_GET=${INI_GET:-0x001933E0}
    INI_PATH=${INI_PATH:-0x0035E364}
    echo "ini reader $INI_GET, ini path $INI_PATH"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_INI_GET_STRING=$INI_GET -DTES3X_INI_PATH=$INI_PATH"
    ;;
esac

# The autosave hook replaces the three automatic-save calls and invokes the original routine.
case " $SRCS " in
*" tes3xsaves.c "*)
    SAVE_GAME=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate save-game | tail -1)
    echo "autosave hook: SaveGame $SAVE_GAME"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_SAVE_GAME=$SAVE_GAME"
    ;;
esac

# Diagnostics stands in for the sole call to the once-per-frame update function.
case " $SRCS " in
*" tes3xdiag.c "*)
    DIAG_UPDATE=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate diagnostics-update | tail -1)
    echo "diagnostics hook: Game::Update $DIAG_UPDATE"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_DIAGNOSTICS -DTES3X_DIAG_UPDATE=$DIAG_UPDATE"
    ;;
esac

# MCP id=1 lands on the restamp fallback all four paths share. Both addresses come from the
# same signature search the patcher uses, so the payload and the patch cannot disagree.
case " $SRCS " in
*" tes3xrefs.c "*)
    REF_LOAD=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate ref-load | tail -1)
    REF_SKIP=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate ref-skip | tail -1)
    REF_RESUME=$(printf '0x%08X' $((REF_LOAD + 6)))
    echo "refs hook: fallback $REF_LOAD, resume $REF_RESUME, skip $REF_SKIP"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_REF_RESUME=$REF_RESUME -DTES3X_REF_SKIP=$REF_SKIP"
    ;;
esac

# MCP id=97 replaces one six-byte cursor update in Script::ReplaceGlobalsInData.
case " $SRCS " in
*" tes3xmcp97.c "*)
    MCP97_SCAN=$($PYTHON "$ROOT/tools/tes3x_patch.py" "$XBE" --locate mcp-97-scan | tail -1)
    MCP97_RESUME=$(printf '0x%08X' $((MCP97_SCAN + 6)))
    echo "mcp-97 hook: scan $MCP97_SCAN, resume $MCP97_RESUME"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_MCP97_RESUME=$MCP97_RESUME"
    ;;
esac

# The console hook replaces the engine's own action check at the one call site that gates
# Console::Toggle.
case " $SRCS " in
*" tes3xconsole.c "*)
    CONSOLE_SITE=${CONSOLE_SITE:-0x00098430}
    echo "console hook: gate $CONSOLE_SITE"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_FIND_MENU=${FIND_MENU:-0x001AD340}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_OPEN_VK=${OPEN_VK:-0x0022D210}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_CONSOLE_MENU_ID=${CONSOLE_MENU_ID:-0x003D816C}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_GET_PROP=${GET_PROP:-0x0019A770}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_COMPILE_RUN=${COMPILE_RUN:-0x0014B3C0}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_VK_MENU_ID=${VK_MENU_ID:-0x003DC710}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_VK_TEXT_ID=${VK_TEXT_ID:-0x003DC75C}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_GAME_PTR=${GAME_PTR:-0x003CB5F4}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_WIDGET_TEXT=${WIDGET_TEXT:-0x0019B350}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_WIDGET_SET_TEXT=${WIDGET_SET_TEXT:-0x0012F3A0}"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -DTES3X_WIDGET_DIRTY=${WIDGET_DIRTY:-0x00199660}"
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
if [ -n "$REF_LOAD" ]; then
    RHOOK=$(sym_va _tes3x_ref_load_hook)
    [ -n "$RHOOK" ] || { echo "could not find _tes3x_ref_load_hook in the link map" >&2; exit 1; }
    HOOKS="$HOOKS ref_load=0x$RHOOK"
fi
if [ -n "$MCP97_SCAN" ]; then
    M97HOOK=$(sym_va _tes3x_mcp97_scan_hook)
    [ -n "$M97HOOK" ] || { echo "could not find _tes3x_mcp97_scan_hook in the link map" >&2; exit 1; }
    HOOKS="$HOOKS mcp97_scan=0x$M97HOOK"
fi
if [ -n "$CONSOLE_SITE" ]; then
    CHOOK=$(sym_va @tes3x_console_hook@12)
    [ -n "$CHOOK" ] || CHOOK=$(sym_va _tes3x_console_hook)
    [ -n "$CHOOK" ] || { echo "could not find tes3x_console_hook in the link map" >&2; exit 1; }
    HOOKS="$HOOKS console_gate=0x$CHOOK"
fi
if [ -n "$DIAG_UPDATE" ]; then
    DHOOK=$(sym_va _tes3x_diag_update_hook)
    [ -n "$DHOOK" ] || { echo "could not find _tes3x_diag_update_hook in the link map" >&2; exit 1; }
    DFLAG=$(sym_va _tes3x_diag_installed)
    [ -n "$DFLAG" ] || { echo "could not find _tes3x_diag_installed in the link map" >&2; exit 1; }
    DMASK=$(sym_va _tes3x_patch_mask)
    [ -n "$DMASK" ] || { echo "could not find _tes3x_patch_mask in the link map" >&2; exit 1; }
    HOOKS="$HOOKS diagnostics_update=0x$DHOOK diagnostics_flag=0x$DFLAG patch_mask=0x$DMASK"
fi
if [ -n "$SAVE_GAME" ]; then
    AHOOK=$(sym_va @tes3x_autosave_hook@12)
    [ -n "$AHOOK" ] || AHOOK=$(sym_va _tes3x_autosave_hook)
    [ -n "$AHOOK" ] || { echo "could not find tes3x_autosave_hook in the link map" >&2; exit 1; }
    HOOKS="$HOOKS autosave=0x$AHOOK"
fi

# Hook addresses beside the blob, so tes3x_patch.py can apply it without a toolchain or a map.
$PYTHON - "$OUT/tes3xhook.json" "$VA" $HOOKS <<'MANIFEST'
import json, sys
out, base = sys.argv[1], sys.argv[2]
doc = {"base": base, "hooks": dict(a.split("=", 1) for a in sys.argv[3:])}
json.dump(doc, open(out, "w", encoding="utf-8"), indent=2)
MANIFEST

$PYTHON "$ROOT/tools/tes3x_inject.py" "$XBE" \
    --payload "$OUT/tes3xhook.pe" --hook-entry $INJECT_EXTRA --out "$DEST"

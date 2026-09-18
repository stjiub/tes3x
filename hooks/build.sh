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

if [ -n "$ARCH_LOAD" ]; then
    HOOK=$(awk '$2 == "_tes3x_archive_hook" { print $(NF - 1) }' "$OUT/tes3xhook.map")
    [ -n "$HOOK" ] || { echo "could not find _tes3x_archive_hook in the link map" >&2; exit 1; }
    INJECT_EXTRA="--patch-call $ARCH_SITE=0x$HOOK"
fi

# Hook addresses beside the blob, so tes3x_patch.py can apply it without a toolchain or a map.
python - "$OUT/tes3xhook.json" "$VA" "$HOOK" <<'PY'
import json, sys
out, base, hook = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""
doc = {"base": base, "hooks": {}}
if hook:
    doc["hooks"]["archive_load"] = "0x" + hook
json.dump(doc, open(out, "w", encoding="utf-8"), indent=2)
PY

python "$ROOT/tools/tes3x_inject.py" "$XBE" \
    --payload "$OUT/tes3xhook.pe" --hook-entry $INJECT_EXTRA --out "$DEST"

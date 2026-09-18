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
SRCS="tes3xhook.c tes3xlog.c"

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

WOUT=$(cygpath -w "$OUT")
OBJS=""
for src in $SRCS; do
    obj=$(basename "$src" .c).obj
    "$CLANG" -target i386-pc-win32 -march=pentium3 -Os -ffreestanding -nostdlib \
        -fno-builtin -fno-stack-protector -fno-asynchronous-unwind-tables \
        -DTES3X_ORIG_ENTRY="$ENTRY" -I"$HERE" \
        -c "$HERE/$src" -o "$OUT/$obj"
    OBJS="$OBJS $WOUT\\$obj"
done

# lld-link is a native binary, so hand it Windows paths and stop MSYS rewriting the
# /flags; the python invocations above still want the normal conversion
MSYS2_ARG_CONV_EXCL='*' "$LLD" /nologo /subsystem:native /entry:tes3x_entry /fixed \
    /nodefaultlib /base:"$VA" /out:"$WOUT\tes3xhook.pe" $OBJS

python "$ROOT/tools/tes3x_inject.py" "$XBE" \
    --payload "$OUT/tes3xhook.pe" --hook-entry --out "$DEST"

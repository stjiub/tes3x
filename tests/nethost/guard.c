/* The Windows parts of the harness, kept apart from host.c, which cannot include windows.h beside
 * the payload's own NT definitions: a buffer whose end touches an unreadable page, so a read past
 * a frame faults, and stdin in binary mode. */

#include <fcntl.h>
#include <io.h>
#include <stdio.h>
#include <windows.h>

void binary_stdin(void)
{
    _setmode(_fileno(stdin), _O_BINARY);
}

unsigned char *guard_end(void)
{
    SYSTEM_INFO info;
    DWORD old;
    unsigned char *base;

    GetSystemInfo(&info);
    base = VirtualAlloc(0, 2 * info.dwPageSize, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!base || !VirtualProtect(base + info.dwPageSize, info.dwPageSize, PAGE_NOACCESS, &old))
        return 0;
    return base + info.dwPageSize;
}

/* [Xbox] settings. Those that belong to a console rather than a build (NetAgent, OverlayBase,
 * the network keys) live in E:\TES3X\console.ini, which is read ahead of Morrowind.ini so a
 * build's files stay the same on every console.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)

typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *, char *, int,
                                        const char *);

static char console_path[] = "\\Device\\Harddisk0\\Partition1\\TES3X\\console.ini";
static char console_text[2048];
static volatile int console_loaded;

static int same(const char *a, const char *b)
{
    while (*b)
        if ((*a++ | 0x20) != (*b++ | 0x20))
            return 0;
    return 1;
}

int tes3x_ini_line(char *line, int *in_xbox, const char *key, char *out, u32 size)
{
    u32 n = tes3x_strlen(key), i;
    char *v;

    while (*line == ' ' || *line == '\t')
        line++;
    if (*line == '[') {
        *in_xbox = same(line, "[xbox]");
        return 0;
    }
    if (!*in_xbox || !same(line, key))
        return 0;
    for (v = line + n; *v == ' ' || *v == '\t'; v++)
        ;
    if (*v++ != '=')
        return 0;
    while (*v == ' ' || *v == '\t')
        v++;
    for (i = 0; v[i] && i < size - 1; i++)
        out[i] = v[i];
    if (v[i])
        return 0;
    while (i && (out[i - 1] == ' ' || out[i - 1] == '\t'))
        i--;
    out[i] = 0;
    return 1;
}

/* Read once: the file is small, and modules ask for keys at different times. */
static void console_load(void)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 offset = 0;
    void *h = 0;

    tes3x_object_attributes(&oa, &name, console_path);
    if (NtCreateFile(&h, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                     FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT) == 0) {
        if (NtReadFile(h, 0, 0, 0, &iosb, console_text, sizeof(console_text) - 1, &offset) == 0
            && iosb.Information < sizeof(console_text))
            console_text[iosb.Information] = 0;
        NtClose(h);
    }
    console_loaded = 1;
}

int tes3x_console_ini(const char *key, char *out, u32 size)
{
    char line[300];
    const char *p;
    u32 n = 0;
    int in_xbox = 0;

    if (!console_loaded)
        console_load();
    for (p = console_text;; p++) {
        if (*p == '\n' || *p == '\r' || !*p) {
            line[n] = 0;
            if (tes3x_ini_line(line, &in_xbox, key, out, size))
                return 1;
            n = 0;
            if (!*p)
                return 0;
        } else if (n < sizeof(line) - 1) {
            line[n++] = *p;
        }
    }
}

void tes3x_ini_xbox(const char *key, const char *dflt, char *out, u32 size)
{
    u32 i;

    for (i = 0; i < size; i++)
        out[i] = 0;
    if (tes3x_console_ini(key, out, size))
        return;
#ifdef TES3X_INI_GET_STRING
    ((fn_ini_get_string)TES3X_INI_GET_STRING)("Xbox", key, dflt, out, (int)size - 1,
                                              (const char *)TES3X_INI_PATH);
#else
    for (i = 0; dflt[i] && i < size - 1; i++)
        out[i] = dflt[i];
#endif
}

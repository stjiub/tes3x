#include <assert.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mgr.h"

static const char *input;
static char written[8192], staged_path[PATH_MAX_MGR + 8];
static size_t written_n;
static int fail_write, deleted, moved;

struct allocation { size_t size; unsigned char guard[32]; };

static void *checked_malloc(size_t size)
{
    struct allocation *a = malloc(sizeof(*a) + size + 32);
    assert(a);
    a->size = size;
    memset(a->guard, 0xa5, 32);
    memset((unsigned char *)(a + 1) + size, 0xa5, 32);
    return a + 1;
}

static void checked_free(void *ptr)
{
    struct allocation *a;
    size_t i;
    if (!ptr)
        return;
    a = (struct allocation *)ptr - 1;
    for (i = 0; i < 32; i++) {
        assert(a->guard[i] == 0xa5);
        assert(((unsigned char *)ptr)[a->size + i] == 0xa5);
    }
    free(a);
}

int read_file(const char *path, unsigned char **data, size_t *n)
{
    (void)path;
    *n = strlen(input);
    *data = checked_malloc(*n + 1);
    memcpy(*data, input, *n + 1);
    return 0;
}

int write_flushed(const char *path, const void *data, size_t n)
{
    assert(n < sizeof(written));
    assert(strlen(path) < sizeof(staged_path));
    memcpy(written, data, n);
    written[n] = 0;
    written_n = n;
    strcpy(staged_path, path);
    return fail_write;
}

int name_cmp_n(const char *a, const char *b, size_t n)
{
    while (n--) {
        int diff = tolower((unsigned char)*a++) - tolower((unsigned char)*b++);
        if (diff)
            return diff;
    }
    return 0;
}

int name_cmp(const char *a, const char *b)
{
    return strlen(a) != strlen(b) || name_cmp_n(a, b, strlen(a));
}

#define ERROR_ALREADY_EXISTS 183
#define FILE_ATTRIBUTE_DIRECTORY 16
#define INVALID_HANDLE_VALUE NULL
typedef void *HANDLE;
typedef struct { unsigned dwFileAttributes; char cFileName[260]; } WIN32_FIND_DATAA;
static HANDLE FindFirstFileA(const char *p, WIN32_FIND_DATAA *d)
{ (void)p; (void)d; return INVALID_HANDLE_VALUE; }
static int FindNextFileA(HANDLE h, WIN32_FIND_DATAA *d) { (void)h; (void)d; return 0; }
static int FindClose(HANDLE h) { (void)h; return 1; }
static int CreateDirectoryA(const char *p, void *s) { (void)p; (void)s; return 1; }
static unsigned GetLastError(void) { return 0; }
static int DeleteFileA(const char *p) { (void)p; deleted++; return 1; }
static int MoveFileA(const char *a, const char *b) { (void)a; (void)b; moved++; return 1; }

#define malloc checked_malloc
#define free checked_free
#include "servers-under-test.c"
#undef malloc
#undef free

static void maximum(char *out, size_t size, char c)
{
    memset(out, c, size - 1);
    out[size - 1] = 0;
}

int main(void)
{
    struct server s = {0};
    char expected[8192], key1[65], key2[65], old[8192], lf[2048];
    size_t n, i;

    maximum(s.name, sizeof(s.name), 'n');
    maximum(s.host, sizeof(s.host), 'h');
    maximum(s.file, sizeof(s.file), 'f');
    s.file[1] = ':', s.file[2] = '\\';
    maximum(s.password, sizeof(s.password), 'p');
    maximum(s.fingerprint, sizeof(s.fingerprint), 'a');
    maximum(s.character, sizeof(s.character), 'c');
    s.port = 65535;
    s.has_server_key = s.has_client_key = 1;
    memset(s.server_key, 0xab, sizeof(s.server_key));
    memset(s.client_key, 0xcd, sizeof(s.client_key));
    for (i = 0; i < 32; i++)
        key1[2*i] = 'a', key1[2*i+1] = 'b', key2[2*i] = 'c', key2[2*i+1] = 'd';
    key1[64] = key2[64] = 0;

    /* Enough LF lines to fill the retained-line allowance after CRLF expansion. */
    for (i = 0; i < sizeof(lf) - 2; i += 2)
        lf[i] = 'x', lf[i + 1] = '\n';
    lf[i] = 0;
    for (int scenario = 0; scenario < 3; scenario++) {
        snprintf(old, sizeof(old), "[other]\nclient_key=keep\n[%s]\npassword=old\n"
                 "[after]\nserver_key=keep-too\n%s", s.name, scenario == 2 ? lf : "");
        input = scenario == 0 ? "" : old;
        deleted = moved = 0;
        assert(rewrite(&s, 1) == 0);
        assert(deleted == 1 && moved == 1);
        assert(strlen(staged_path) == strlen(s.file) + 4);
        assert(!memcmp(staged_path, s.file, strlen(s.file)));
        assert(!strcmp(staged_path + strlen(s.file), ".new"));
        n = (size_t)snprintf(expected, sizeof(expected), "[%s]\r\nserver_key=%s\r\n"
                            "client_key=%s\r\npassword=%s\r\n", s.name, key1, key2, s.password);
        assert(written_n >= n && !memcmp(written + written_n - n, expected, n));
        assert(!strstr(written, "password=old"));
        if (scenario) {
            assert(!strncmp(written, "[other]\r\nclient_key=keep\r\n", 26));
            assert(strstr(written, "[after]\r\nserver_key=keep-too\r\n"));
        } else
            assert(written_n == n);
        assert(rewrite(&s, 0) == 0);
        assert(!strstr(written, s.name) && !strstr(written, s.password));
    }
    fail_write = 1;
    deleted = moved = 0;
    assert(rewrite(&s, 1) == -1);
    assert(!deleted && !moved);
    puts("maximum fields, LF expansion, replacement, removal and write failure passed");
    return 0;
}

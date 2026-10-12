#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "sha256.h"

#define HEAD_MAX 8192
#define GENERIC_READ 1
#define GENERIC_WRITE 2
#define OPEN_ALWAYS 1
#define FILE_ATTRIBUTE_NORMAL 0
#define FILE_BEGIN SEEK_SET
#define INVALID_HANDLE_VALUE NULL
typedef uint32_t DWORD;
typedef FILE *HANDLE;
typedef struct { long long QuadPart; } LARGE_INTEGER;
typedef int (*progress_fn)(const char *, unsigned long long, unsigned long long);
static DWORD KeTickCount;
static unsigned char data[900123];
static size_t stop_at;
static int drops, ignore_range, bad_range, calls, cancelled, speed_seen;
static unsigned long long last_offset;
struct conn { size_t offset; int fail; };

static const char *host_path(const char *path)
{
    static char converted[512];
    size_t i;
    for (i = 0; path[i] && i + 1 < sizeof(converted); i++)
        converted[i] = path[i] == '\\' ? '/' : path[i];
    converted[i] = 0;
    return converted;
}

static HANDLE CreateFileA(const char *path, int access, int share, void *security,
                          int mode, int attrs, void *template)
{
    FILE *f = fopen(host_path(path), "r+b");
    (void)access; (void)share; (void)security; (void)mode; (void)attrs; (void)template;
    return f ? f : fopen(host_path(path), "w+b");
}
static int GetFileSizeEx(HANDLE f, LARGE_INTEGER *size)
{
    long pos = ftell(f);
    fseek(f, 0, SEEK_END); size->QuadPart = ftell(f); fseek(f, pos, SEEK_SET);
    return 1;
}
static int SetFilePointerEx(HANDLE f, LARGE_INTEGER pos, void *out, int origin)
{
    (void)out;
    return !fseek(f, (long)pos.QuadPart, origin);
}
static int SetEndOfFile(HANDLE f)
{
    /* Tests restart only at zero, and overwrite the whole original file. */
    return ftell(f) == 0;
}
static int ReadFile(HANDLE f, void *buf, DWORD n, DWORD *got, void *overlapped)
{
    (void)overlapped; *got = (DWORD)fread(buf, 1, n, f); return !ferror(f);
}
static int WriteFile(HANDLE f, const void *buf, DWORD n, DWORD *put, void *overlapped)
{
    (void)overlapped; fseek(f, 0, SEEK_CUR);
    *put = (DWORD)fwrite(buf, 1, n, f); return *put == n;
}
static void CloseHandle(HANDLE f) { fclose(f); }
static void DeleteFileA(const char *path) { remove(host_path(path)); }
static void flush_path(const char *path) { (void)path; }
static void Sleep(int ms) { KeTickCount += ms; }
static void mgr_progress_title(const char *title) { (void)title; }
static void mgr_progress_speed(unsigned long long speed) { speed_seen |= speed > 0; }
static void mgr_log(const char *format, ...) { (void)format; }
static int name_cmp_n(const char *a, const char *b, size_t n) { return strncmp(a, b, n); }

static const char *open_get(const char *url, struct conn *c, char *head, size_t *at,
                            size_t *have, unsigned long long offset, int *status)
{
    (void)url;
    calls++; last_offset = offset;
    c->offset = ignore_range ? 0 : (size_t)offset;
    c->fail = drops > 0;
    if (drops > 0) drops--;
    *status = offset && !ignore_range ? 206 : 200;
    snprintf(head, HEAD_MAX, "HTTP/1.1 %d OK\r\nContent-Length: %zu\r\n"
             "Content-Range: bytes %zu-%zu/%zu\r\n\r\n", *status,
             sizeof(data) - c->offset, c->offset + bad_range,
             sizeof(data) - 1, sizeof(data));
    *at = strlen(head); *have = 13;
    if (*have > sizeof(data) - c->offset) *have = sizeof(data) - c->offset;
    if (c->fail && c->offset >= stop_at) *have = 0;
    memcpy(head + *at, data + c->offset, *have); c->offset += *have;
    return NULL;
}
static int conn_read(struct conn *c, unsigned char *out, size_t n)
{
    KeTickCount += 350;
    if (c->fail && c->offset >= stop_at) return -1;
    if (n > 73001) n = 73001;
    if (n > sizeof(data) - c->offset) n = sizeof(data) - c->offset;
    if (c->fail && n > stop_at - c->offset) n = stop_at - c->offset;
    memcpy(out, data + c->offset, n); c->offset += n;
    return (int)n;
}
static void conn_close(struct conn *c) { (void)c; }
static int progress(const char *label, unsigned long long done, unsigned long long total)
{
    (void)label; assert(done <= total);
    return cancelled && done >= 262144;
}

#include "receiver.c"

static void reset(void)
{
    drops = ignore_range = bad_range = calls = cancelled = speed_seen = 0;
    stop_at = 400001; last_offset = 0;
    remove("download.new");
}
static void check_file(void)
{
    unsigned char actual[sizeof(data)];
    FILE *f = fopen("download.new", "rb");
    assert(f); assert(fread(actual, 1, sizeof(actual), f) == sizeof(actual));
    assert(fgetc(f) == EOF); fclose(f); assert(!memcmp(actual, data, sizeof(data)));
}
int main(void)
{
    struct sha256 hash;
    unsigned char digest[32];
    char hex[65];
    unsigned long long done;
    const char *error;
    size_t i;
    FILE *f;

    for (i = 0; i < sizeof(data); i++) data[i] = (unsigned char)(i * 17);
    sha256_init(&hash); sha256_update(&hash, data, sizeof(data));
    sha256_final(&hash, digest); sha256_hex(digest, hex);
    reset(); drops = 1; done = 0;
    assert(!http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress));
    assert(calls == 2 && last_offset == stop_at && done == sizeof(data) && speed_seen);
    check_file();

    reset(); drops = 20; done = 0;
    error = http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress);
    assert(error && !strcmp(error, "the download was cut short"));
    assert(calls == 6 && done == stop_at);
    drops = 0; calls = 0; done = 0;
    assert(!http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress));
    assert(calls == 1 && last_offset == stop_at && done == sizeof(data)); check_file();

    reset(); cancelled = 1; done = 0;
    error = http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress);
    assert(error && !strcmp(error, "cancelled"));
    cancelled = 0; done = 0;
    assert(!http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress)); check_file();

    reset(); drops = 1; ignore_range = 1; done = 0;
    assert(!http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress));
    assert(done == sizeof(data)); check_file();

    reset(); drops = 1; bad_range = 1; done = 0;
    error = http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress);
    assert(error && !strcmp(error, "the server resumed at the wrong position"));
    assert(!(f = fopen("download.new", "rb")));

    reset(); f = fopen("download.new", "wb"); assert(f);
    fwrite("corrupt", 1, 7, f); fclose(f); done = 0;
    error = http_save("url", "download.new", sizeof(data), hex, "archive", &done,
                      sizeof(data), progress);
    assert(error && !strcmp(error, "the file does not match the manifest"));
    assert(!(f = fopen("download.new", "rb")));
    return 0;
}

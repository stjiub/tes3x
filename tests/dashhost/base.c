#define main receiver_tests
#include "http.c"
#undef main
#include "json.h"
#ifdef _WIN32
#include <direct.h>
#define make_dir(path) _mkdir(path)
#else
#include <sys/stat.h>
#define make_dir(path) mkdir(path, 0700)
#endif

#define PATH_MAX_MGR 256
#define MANIFEST "tes3xbuild.json"
#define SPACE_MARGIN (8ull << 20)
typedef struct { unsigned long long QuadPart; } ULARGE_INTEGER;
enum { KEEP, FETCH, COPY, REBUILD };
struct item {
    char path[PATH_MAX_MGR], sha[65];
    unsigned long long size;
    int action, xbe, retail, ready;
    char staged[PATH_MAX_MGR];
};
struct base { int unused; };
static int selected;
static int name_cmp(const char *a, const char *b) { return strcmp(a, b); }
static void xboxini_get(const char *key, char *out, size_t n)
{
    (void)key; snprintf(out, n, "base");
}
static int xboxini_set(const char *key, const char *value)
{
    (void)key; assert(!strcmp(value, "base")); selected++; return 0;
}
static const char *check_base(const char *path, struct base *base)
{
    (void)path; (void)base; return "missing";
}
static void join_path(char *out, size_t n, const char *root, const char *relative)
{
    size_t i;
    snprintf(out, n, "%s\\%s", root, relative);
    for (i = 0; out[i]; i++) if (out[i] == '/') out[i] = '\\';
}
static int read_file(const char *path, unsigned char **out, size_t *n)
{
    FILE *f = fopen(host_path(path), "rb");
    long size;
    if (!f) return -1;
    fseek(f, 0, SEEK_END); size = ftell(f); rewind(f);
    *out = malloc((size_t)size + 1); assert(*out);
    *n = fread(*out, 1, (size_t)size, f); (*out)[*n] = 0; fclose(f); return 0;
}
static unsigned long long file_size(const char *path)
{
    FILE *f = fopen(host_path(path), "rb");
    unsigned long long n;
    if (!f) return ~0ull;
    fseek(f, 0, SEEK_END); n = (unsigned long long)ftell(f); fclose(f); return n;
}
static int safe_path(const char *path)
{
    return *path && !strchr(path, ':') && !strstr(path, "..");
}
static void make_parents(const char *path)
{
    char parent[512];
    size_t i;
    snprintf(parent, sizeof(parent), "%s", host_path(path));
    for (i = 0; parent[i]; i++) if (parent[i] == '/') {
        parent[i] = 0; make_dir(parent); parent[i] = '/';
    }
}
static void url_path(char *out, size_t n, const char *root, const char *kind, const char *path)
{
    snprintf(out, n, "%s%s%s", root, kind, path);
}
static int GetDiskFreeSpaceExA(const char *path, void *a, void *b, void *c)
{
    (void)path; (void)a; (void)b; (void)c; return 0;
}
static int MoveFileA(const char *from, const char *to)
{
    char saved[512];
    snprintf(saved, sizeof(saved), "%s", host_path(from));
    return !rename(saved, host_path(to));
}
static const char *write_manifest(const char *folder, const char *text, size_t n,
                                  const char *server)
{
    char path[512]; FILE *f;
    (void)server;
    snprintf(path, sizeof(path), "%s/%s", folder, MANIFEST);
    f = fopen(path, "wb"); assert(f); fwrite(text, 1, n, f); fclose(f); return NULL;
}
static int mgr_hash_file(const char *path, unsigned long long size, char hex[65],
                         unsigned long long *done, unsigned long long total, const char *name,
                         progress_fn progress)
{
    unsigned char *bytes, digest[32]; size_t n; struct sha256 hash;
    (void)total; (void)name; (void)progress;
    if (read_file(path, &bytes, &n)) return -1;
    sha256_init(&hash); sha256_update(&hash, bytes, n); sha256_final(&hash, digest);
    sha256_hex(digest, hex); free(bytes); *done += n;
    return n == size ? 0 : 1;
}

#include "base-under-test.c"

int main(void)
{
    struct sha256 hash; unsigned char digest[32]; char hex[65], text[2048];
    struct json build = {0}; const char *error; FILE *f; size_t i;
    for (i = 0; i < sizeof(data); i++) data[i] = (unsigned char)(i * 17);
    sha256_init(&hash); sha256_update(&hash, data, sizeof(data));
    sha256_final(&hash, digest); sha256_hex(digest, hex);
    snprintf(text, sizeof(text), "{\"retail_base\":{\"install_layout\":\"retail-base\","
             "\"files\":{\"Data Files/Morrowind.bsa\":{\"size\":%zu,\"sha256\":\"%s\"},"
             "\"morrowind.xbe\":{\"size\":%zu,\"sha256\":\"%s\"}}}}",
             sizeof(data), hex, sizeof(data), hex);
    assert(json_parse(&build, text, strlen(text)) >= 0);
    reset(); drops = 20;
    error = install_base(&build, "url/", "profile", progress);
    assert(error && selected == 0);
    assert(file_size("base/Data Files/Morrowind.bsa") == ~0ull);
    assert(file_size("base/Data Files/~t3x") == ~0ull);
    drops = 0; calls = 0;
    assert(!install_base(&build, "url/", "profile", progress));
    assert(selected == 1 && calls == 2);
    assert(file_size("base/Data Files/Morrowind.bsa") == sizeof(data));
    calls = 0;
    assert(!install_base(&build, "url/", "profile", progress));
    assert(calls == 0);
    remove("base/tes3xbuild.json");
    f = fopen("base/Data Files/Morrowind.bsa", "wb"); assert(f);
    fwrite("modded", 1, 6, f); fclose(f); selected = 0;
    error = install_base(&build, "url/", "profile", progress);
    assert(error && strstr(error, "differs") && selected == 0);
    assert(file_size("base/Data Files/Morrowind.bsa") == 6);
    json_free(&build);
    return 0;
}

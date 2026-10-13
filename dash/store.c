/* The shared archive store (tes3x.store): whole-mod archives in E:\TES3X\lib that several builds
 * list in their tes3xarch.txt. Each is named by the first 32 digits of its SHA-256, which a
 * FATX name holds, and has a JSON sidecar written once it is verified; the sidecar is trusted
 * afterwards, since hashing a large archive on this CPU takes minutes. */

#include "mgr.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define STORE_DIR "E:\\TES3X\\lib"
#define STORE_SPACE_MARGIN (8ull << 20)

static void store_path(char *out, size_t n, const char *sha, const char *ext)
{
    snprintf(out, n, STORE_DIR "\\%.32s%s", sha, ext);
}

static unsigned long long size_of(const char *path)
{
    WIN32_FILE_ATTRIBUTE_DATA a;

    if (!GetFileAttributesExA(path, GetFileExInfoStandard, &a))
        return ~0ull;
    return (unsigned long long)a.nFileSizeHigh << 32 | a.nFileSizeLow;
}

static int is_digest(const char *sha)
{
    return strlen(sha) == 64 && strspn(sha, "0123456789abcdef") == 64;
}

/* The sidecar's entry: 0 when it names this digest and the archive has its size. */
static int read_sidecar(const char *json_path, struct store_item *out)
{
    unsigned char *text;
    size_t n;
    struct json j = {0};
    char bsa[PATH_MAX_MGR];
    int ok = -1;

    memset(out, 0, sizeof(*out));
    if (read_file(json_path, &text, &n))
        return -1;
    if (json_parse(&j, (const char *)text, n) >= 0 && j.t[0].type == JSON_OBJECT
        && json_string(&j, json_get(&j, 0, "sha256"), out->sha, sizeof(out->sha)) == 64
        && is_digest(out->sha)) {
        json_string(&j, json_get(&j, 0, "name"), out->name, sizeof(out->name));
        out->size = (unsigned long long)json_number(&j, json_get(&j, 0, "size"));
        out->tier = (int)json_number(&j, json_get(&j, 0, "tier"));
        store_path(bsa, sizeof(bsa), out->sha, ".bsa");
        ok = out->size && size_of(bsa) == out->size ? 0 : -1;
    }
    json_free(&j);
    free(text);
    return ok;
}

int store_present(const char *sha, unsigned long long size)
{
    char path[PATH_MAX_MGR];
    struct store_item item;

    store_path(path, sizeof(path), sha, ".json");
    return !read_sidecar(path, &item) && !strcmp(item.sha, sha) && item.size == size;
}

const char *store_install(const struct json *j, const char *url_base, int *reused,
                          int *fetched, progress_fn progress)
{
    static char reason[160];
    char sha[65], name[48], url[PATH_MAX_MGR + 128], staged[PATH_MAX_MGR], path[PATH_MAX_MGR];
    char text[256];
    unsigned long long size, need = 0, done = 0;
    ULARGE_INTEGER free_bytes, total_bytes;
    const char *err;
    int list = json_get(j, 0, "store"), i, pass, tier;
    size_t k;

    *reused = *fetched = 0;
    if (list < 0)
        return NULL;
    if (j->t[list].type != JSON_ARRAY)
        return "The manifest's store list is invalid.";
    for (pass = 0; pass < 2; pass++) {
        if (pass && need) {
            CreateDirectoryA("E:\\TES3X", NULL);
            CreateDirectoryA(STORE_DIR, NULL);
            if (GetDiskFreeSpaceExA("E:\\", &free_bytes, &total_bytes, NULL)
                && free_bytes.QuadPart < need + STORE_SPACE_MARGIN) {
                snprintf(reason, sizeof(reason), "E:\\ needs %llu MB more for shared archives.",
                         (need + STORE_SPACE_MARGIN - free_bytes.QuadPart + (1 << 20) - 1) >> 20);
                return reason;
            }
        }
        for (i = json_child(j, list); i >= 0; i = json_sibling(j, list, i)) {
            if (json_string(j, json_get(j, i, "sha256"), sha, sizeof(sha)) != 64
                || !is_digest(sha)
                || !(size = (unsigned long long)json_number(j, json_get(j, i, "size"))))
                return "The manifest names an invalid shared archive.";
            if (store_present(sha, size)) {
                *reused += pass;
                continue;
            }
            if (!pass) {
                need += size;
                continue;
            }
            name[0] = 0;
            json_string(j, json_get(j, i, "name"), name, sizeof(name));
            for (k = 0; name[k]; k++)
                if (name[k] == '"' || name[k] == '\\' || (unsigned char)name[k] < 32)
                    name[k] = '?';
            tier = (int)json_number(j, json_get(j, i, "tier"));
            store_path(staged, sizeof(staged), sha, ".new");
            store_path(path, sizeof(path), sha, ".bsa");
            snprintf(url, sizeof(url), "%sstore/%s", url_base, sha);
            mgr_progress_title("Downloading shared archive");
            if ((err = http_save(url, staged, size, sha, name[0] ? name : sha, &done, need,
                                 progress))) {
                snprintf(reason, sizeof(reason), "Shared archive %.40s: %s",
                         name[0] ? name : sha, err);
                return !strcmp(err, "cancelled") ? "Cancelled." : reason;
            }
            DeleteFileA(path);
            if (!MoveFileA(staged, path))
                return "Could not rename a shared archive in.";
            flush_path(path);
            snprintf(text, sizeof(text),
                     "{\"sha256\": \"%s\", \"size\": %llu, \"name\": \"%s\", \"tier\": %d}",
                     sha, size, name, tier);
            store_path(path, sizeof(path), sha, ".json");
            if (write_flushed(path, text, strlen(text)))
                return "Could not record a shared archive.";
            mgr_log("store: %s %.16s, %llu bytes\n", name, sha, size);
            (*fetched)++;
        }
    }
    return NULL;
}

int store_check(const struct json *j, int *missing)
{
    char sha[65];
    int list = json_get(j, 0, "store"), i, count = 0;

    *missing = 0;
    if (list < 0 || j->t[list].type != JSON_ARRAY)
        return 0;
    for (i = json_child(j, list); i >= 0; i = json_sibling(j, list, i), count++)
        if (json_string(j, json_get(j, i, "sha256"), sha, sizeof(sha)) != 64
            || !store_present(sha, (unsigned long long)json_number(j, json_get(j, i, "size"))))
            (*missing)++;
    return count;
}

int store_scan(struct store_item *out, int max, const struct build *builds, int build_count)
{
    WIN32_FIND_DATAA f;
    /* nxdk matches only a bare "*", so the sidecars are picked out by name */
    HANDLE h = FindFirstFileA(STORE_DIR "\\*", &f);
    char path[PATH_MAX_MGR], sha[65], *text;
    struct json j;
    int count = 0, b, i, k, list;

    if (h == INVALID_HANDLE_VALUE)
        return 0;
    do {
        if (strlen(f.cFileName) != 37 || name_cmp(f.cFileName + 32, ".json"))
            continue;
        snprintf(path, sizeof(path), STORE_DIR "\\%s", f.cFileName);
        if (count < max && !read_sidecar(path, &out[count])
            && !name_cmp_n(f.cFileName, out[count].sha, 32))
            count++;
    } while (FindNextFileA(h, &f));
    FindClose(h);
    for (b = 0; b < build_count; b++) {
        if (manifest_load(&builds[b], &text, &j))
            continue;
        list = json_get(&j, 0, "store");
        for (i = list < 0 ? -1 : json_child(&j, list); i >= 0; i = json_sibling(&j, list, i))
            if (json_string(&j, json_get(&j, i, "sha256"), sha, sizeof(sha)) == 64)
                for (k = 0; k < count; k++)
                    out[k].users += !strcmp(out[k].sha, sha);
        manifest_free(text, &j);
    }
    return count;
}

int store_delete_unused(const struct store_item *items, int count, unsigned long long *bytes)
{
    char path[PATH_MAX_MGR];
    int i, deleted = 0;

    *bytes = 0;
    for (i = 0; i < count; i++) {
        if (items[i].users)
            continue;
        /* the sidecar first: an archive without one is downloaded again, never trusted */
        store_path(path, sizeof(path), items[i].sha, ".json");
        DeleteFileA(path);
        store_path(path, sizeof(path), items[i].sha, ".bsa");
        if (DeleteFileA(path)) {
            deleted++;
            *bytes += items[i].size;
            mgr_log("store: deleted %s %.16s\n", items[i].name, items[i].sha);
        }
    }
    return deleted;
}

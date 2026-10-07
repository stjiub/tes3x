/* A server's build installed into a folder of its own. Every file arrives under a staging name
 * and is checked against the manifest's SHA-256, whose own hash came through the server's
 * session; only when all are in are they renamed into place, so an interrupted update leaves the
 * old build runnable. Files a retail copy holds come from the retail base, XBEs are rebuilt from
 * their delta against it, and only the rest is downloaded. */

#include "mgr.h"
#include "sha256.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

const char INSTALL_CONFIRM[] = "The folder holds something else.";

#define MANIFEST_MAX (16u << 20)
#define DELTA_MAX (16u << 20)
#define SPACE_MARGIN (8ull << 20)
#define COPY_CHUNK (256 * 1024)
#define FATX_NAME 42
#define MAX_BUILDS 64
/* plugins' modified times set the load order: one a minute from 2010-01-01 */
#define LOAD_ORDER_EPOCH 129067776000000000ull
#define LOAD_ORDER_STEP 600000000ull

enum { KEEP, FETCH, COPY, REBUILD };

struct item {
    char path[PATH_MAX_MGR]; /* as the manifest spells it */
    char sha[65];
    unsigned long long size;
    int action, xbe;
    char staged[PATH_MAX_MGR]; /* set once the staged file exists */
};

struct known {
    char *path; /* lower case */
    char sha[65];
    unsigned long long size;
};

static void lower(char *s)
{
    for (; *s; s++)
        if (*s >= 'A' && *s <= 'Z')
            *s += 32;
}

static int known_cmp(const void *a, const void *b)
{
    return strcmp(((const struct known *)a)->path, ((const struct known *)b)->path);
}

/* The files of a manifest, sorted by lower-case path for lookups. */
static struct known *index_files(const struct json *j, int *count)
{
    struct known *k;
    char path[PATH_MAX_MGR];
    int files = json_get(j, 0, "files"), i, n = 0;

    *count = 0;
    if (files < 0 || j->t[files].type != JSON_OBJECT)
        return NULL;
    if (!(k = calloc((size_t)j->t[files].count + 1, sizeof(*k))))
        return NULL;
    for (i = json_child(j, files); i >= 0; i = json_sibling(j, files, i)) {
        json_string(j, i, path, sizeof(path));
        lower(path);
        if (!(k[n].path = strdup(path)))
            break;
        json_string(j, json_get(j, j->t[i].next, "sha256"), k[n].sha, sizeof(k[n].sha));
        k[n].size = (unsigned long long)json_number(j, json_get(j, j->t[i].next, "size"));
        n++;
    }
    qsort(k, (size_t)n, sizeof(*k), known_cmp);
    *count = n;
    return k;
}

static void index_free(struct known *k, int n)
{
    while (n-- > 0)
        free(k[n].path);
    free(k);
}

static const struct known *lookup(const struct known *k, int n, const char *path)
{
    struct known key;
    char low[PATH_MAX_MGR];
    size_t i;

    snprintf(low, sizeof(low), "%s", path);
    for (i = 0; low[i]; i++)
        if (low[i] == '\\')
            low[i] = '/';
    lower(low);
    key.path = low;
    return k ? bsearch(&key, k, (size_t)n, sizeof(*k), known_cmp) : NULL;
}

static unsigned long long file_size(const char *path)
{
    WIN32_FILE_ATTRIBUTE_DATA a;

    if (!GetFileAttributesExA(path, GetFileExInfoStandard, &a)
        || (a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
        return ~0ull;
    return (unsigned long long)a.nFileSizeHigh << 32 | a.nFileSizeLow;
}

static void make_parents(const char *path)
{
    char part[PATH_MAX_MGR];
    size_t i;

    snprintf(part, sizeof(part), "%s", path);
    for (i = 3; part[i]; i++)
        if (part[i] == '\\') {
            part[i] = 0;
            CreateDirectoryA(part, NULL);
            part[i] = '\\';
        }
}

/* A path a manifest may name: relative, inside the folder, FATX-sized names. */
static int safe_path(const char *p)
{
    const char *part;
    size_t n;

    if (!*p || *p == '/' || *p == '\\' || strchr(p, ':'))
        return 0;
    for (part = p; *part; part += n + (part[n] ? 1 : 0)) {
        n = strcspn(part, "/\\");
        if (!n || n > FATX_NAME || (n <= 2 && part[0] == '.' && (n == 1 || part[1] == '.')))
            return 0;
    }
    return 1;
}

static void url_path(char *out, size_t n, const char *base, const char *what, const char *path)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t o = (size_t)snprintf(out, n, "%s%s", base, what);
    unsigned char c;

    for (; *path && o + 4 < n; path++) {
        c = (unsigned char)*path;
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
            || strchr("-._~/", c)) {
            out[o++] = (char)c;
        } else {
            out[o++] = '%', out[o++] = hex[c >> 4], out[o++] = hex[c & 15];
        }
    }
    out[o] = 0;
}

/* Copies a base file, checking it against the manifest as it goes. */
static const char *copy_checked(const char *from, const char *to, const struct item *it,
                                unsigned long long *done, unsigned long long total,
                                progress_fn progress)
{
    static unsigned char *buf;
    struct sha256 s;
    unsigned char digest[32];
    char hex[65];
    unsigned long long seen = 0;
    DWORD got, put;
    HANDLE in, out;
    const char *err = NULL;

    if (!buf && !(buf = malloc(COPY_CHUNK)))
        return "out of memory";
    in = CreateFileA(from, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                     FILE_ATTRIBUTE_NORMAL, NULL);
    if (in == INVALID_HANDLE_VALUE)
        return "not in the retail base";
    out = CreateFileA(to, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (out == INVALID_HANDLE_VALUE) {
        CloseHandle(in);
        return "could not create the file";
    }
    sha256_init(&s);
    while (!err && ReadFile(in, buf, COPY_CHUNK, &got, NULL) && got) {
        sha256_update(&s, buf, got);
        if (!WriteFile(out, buf, got, &put, NULL) || put != got)
            err = "could not write the file";
        seen += got, *done += got;
        if (progress && progress(it->path, *done, total))
            err = "cancelled";
    }
    CloseHandle(in);
    CloseHandle(out);
    if (!err) {
        sha256_final(&s, digest);
        sha256_hex(digest, hex);
        if (seen != it->size || strcmp(hex, it->sha))
            err = "the retail base's copy differs";
    }
    if (err)
        DeleteFileA(to);
    return err;
}

static int find_recipe(const struct json *j, const char *path)
{
    char name[PATH_MAX_MGR];
    int list = json_get(j, 0, "xbe"), i;

    for (i = json_child(j, list); i >= 0; i = json_sibling(j, list, i))
        if (json_string(j, json_get(j, i, "path"), name, sizeof(name)) >= 0
            && !name_cmp(name, path))
            return i;
    return -1;
}

/* Rebuilds an XBE from the delta the server serves, against the retail base. */
static const char *rebuild(const struct json *j, const struct item *it, const char *url_base,
                           const char *to)
{
    char want_retail[65], want_delta[65], hex[65], url[PATH_MAX_MGR + 128];
    unsigned char *patch = NULL, *out = NULL, digest[32];
    size_t patch_n, out_n;
    struct sha256 s;
    const char *err;
    int r = find_recipe(j, it->path);

    if (r < 0 || json_string(j, json_get(j, json_get(j, r, "delta"), "sha256"), want_delta,
                             sizeof(want_delta)) != 64)
        return "the manifest has no delta for it";
    json_string(j, json_get(j, r, "retail_digest"), want_retail, sizeof(want_retail));
    url_path(url, sizeof(url), url_base, "delta/", want_delta);
    if ((err = http_get(url, &patch, &patch_n, DELTA_MAX, NULL)))
        return err;
    sha256_init(&s);
    sha256_update(&s, patch, patch_n);
    sha256_final(&s, digest);
    sha256_hex(digest, hex);
    if (strcmp(hex, want_delta))
        err = "the delta is not the one the manifest names";
    else if (!(err = xbe_decode(want_retail, patch, patch_n, it->sha, &out, &out_n))
             && write_flushed(to, out, out_n))
        err = "could not write the XBE";
    free(patch);
    free(out);
    return err;
}

/* The folder's files, relative and with '\', for the orphan pass. */
static int walk(const char *root, const char *rel, char (*out)[PATH_MAX_MGR], int n, int max)
{
    WIN32_FIND_DATAA fd;
    char pattern[PATH_MAX_MGR], sub[PATH_MAX_MGR];
    HANDLE h;

    snprintf(pattern, sizeof(pattern), "%s%s%s\\*", root, *rel ? "\\" : "", rel);
    if ((h = FindFirstFileA(pattern, &fd)) == INVALID_HANDLE_VALUE)
        return n;
    do {
        if (fd.cFileName[0] == '.')
            continue;
        snprintf(sub, sizeof(sub), "%s%s%s", rel, *rel ? "\\" : "", fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            n = walk(root, sub, out, n, max);
        else if (n < max)
            snprintf(out[n++], PATH_MAX_MGR, "%s", sub);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return n;
}

static int staging_name(const char *rel)
{
    const char *name = strrchr(rel, '\\') ? strrchr(rel, '\\') + 1 : rel;

    return !strncmp(name, "~t3x", 4) && strlen(name) > 8
           && !name_cmp(name + strlen(name) - 4, ".new");
}

/* Where the build goes: the build the manager installed from this server before, else the
 * manifest's folder name under the first Games folder there is. */
static const char *choose_folder(const struct server *s, const struct json *j, char *out,
                                 size_t n)
{
    static struct build builds[MAX_BUILDS];
    static const char drives[] = "FGEC";
    char name[64], games[16];
    int count = scan_builds(builds, MAX_BUILDS), i;

    for (i = 0; i < count; i++)
        if (!strcmp(builds[i].server, s->name)) {
            snprintf(out, n, "%s", builds[i].path);
            return NULL;
        }
    if (json_string(j, json_get(j, 0, "folder"), name, sizeof(name)) <= 0
        && json_string(j, json_get(j, 0, "profile"), name, sizeof(name)) <= 0)
        return "The build names no folder.";
    if (!safe_path(name) || strpbrk(name, "/\\"))
        return "The build's folder name is not one FATX takes.";
    for (i = 0; drives[i]; i++) {
        snprintf(games, sizeof(games), "%c:\\Games", drives[i]);
        if (GetFileAttributesA(games) != INVALID_FILE_ATTRIBUTES) {
            join_path(out, n, games, name);
            return NULL;
        }
    }
    return "No Games folder on C, E, F or G.";
}

static void stamp_load_order(const struct json *j, const char *folder)
{
    char name[PATH_MAX_MGR], rel[PATH_MAX_MGR], path[PATH_MAX_MGR];
    unsigned long long t;
    FILETIME ft;
    HANDLE h;
    int list = json_get(j, 0, "plugins"), i, k = 0;

    for (i = json_child(j, list); i >= 0; i = json_sibling(j, list, i), k++) {
        json_string(j, i, name, sizeof(name));
        snprintf(rel, sizeof(rel), "Data Files\\%s", name);
        join_path(path, sizeof(path), folder, rel);
        h = CreateFileA(path, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE)
            continue;
        t = LOAD_ORDER_EPOCH + (unsigned long long)k * LOAD_ORDER_STEP;
        ft.dwLowDateTime = (DWORD)t, ft.dwHighDateTime = (DWORD)(t >> 32);
        SetFileTime(h, NULL, NULL, &ft);
        CloseHandle(h);
    }
}

/* The server's manifest as received, with where it came from and when added at the front. */
static const char *write_manifest(const char *folder, const char *text, size_t n,
                                  const char *server)
{
    char head[192], name[80], path[PATH_MAX_MGR], staged[PATH_MAX_MGR];
    LARGE_INTEGER now;
    TIME_FIELDS tf;
    char *out;
    size_t i, k;
    int r;

    for (i = k = 0; server[i] && k + 1 < sizeof(name); i++)
        name[k++] = server[i] == '"' || server[i] == '\\' ? '?' : server[i];
    name[k] = 0;
    KeQuerySystemTime(&now);
    RtlTimeToTimeFields(&now, &tf);
    snprintf(head, sizeof(head),
             "{\n \"server\": \"%s\",\n \"deployed\": \"%04d-%02d-%02dT%02d:%02d:%02dZ\",", name,
             tf.Year, tf.Month, tf.Day, tf.Hour, tf.Minute, tf.Second);
    for (i = 0; i < n && text[i] != '{'; i++)
        ;
    if (i == n || !(out = malloc(strlen(head) + n)))
        return "could not write the manifest";
    k = strlen(head);
    memcpy(out, head, k);
    memcpy(out + k, text + i + 1, n - i - 1);
    join_path(path, sizeof(path), folder, MANIFEST);
    snprintf(staged, sizeof(staged), "%s.new", path);
    r = write_flushed(staged, out, k + n - i - 1);
    free(out);
    if (r)
        return "could not write the manifest";
    DeleteFileA(path);
    return MoveFileA(staged, path) ? NULL : "could not write the manifest";
}

const char *server_install(struct server *s, int replace, char *folder, size_t folder_n,
                           char *summary, size_t summary_n, progress_fn progress)
{
    static char (*files)[PATH_MAX_MGR];
    static char reason[160];
    struct ticket t;
    struct json j = {0}, old = {0};
    struct item *items = NULL;
    struct known *had = NULL, *want = NULL;
    const struct known *k;
    char url_base[96], url[PATH_MAX_MGR + 128], base[PATH_MAX_MGR];
    char path[PATH_MAX_MGR], from[PATH_MAX_MGR], prev[PATH_MAX_MGR], origin[16], drive[4];
    unsigned char *text = NULL, *old_text = NULL, digest[32];
    unsigned long long need = 0, done = 0;
    ULARGE_INTEGER free_bytes, total_bytes;
    unsigned staged_n = 0;
    size_t text_n, old_n;
    DWORD start = KeTickCount;
    struct sha256 sh;
    const char *err = NULL;
    int n = 0, had_n = 0, want_n = 0, i, e, count[4] = {0}, owned, others = 0, removed = 0, no_base = 0;

    folder[0] = summary[0] = 0;
    if ((err = server_ticket(s, &t, progress)))
        return err;
    snprintf(url_base, sizeof(url_base), "http://%u.%u.%u.%u:%u/", t.addr >> 24,
             t.addr >> 16 & 255, t.addr >> 8 & 255, t.addr & 255, t.port);
    for (i = 0; i < 16; i++)
        snprintf(url_base + strlen(url_base), 3, "%02x", t.token[i]);
    strcat(url_base, "/");
    if (progress)
        progress("Reading the build's manifest", 0, 0);
    snprintf(url, sizeof(url), "%smanifest", url_base);
    if ((err = http_get(url, &text, &text_n, MANIFEST_MAX, NULL)))
        return err;
    sha256_init(&sh);
    sha256_update(&sh, text, text_n);
    sha256_final(&sh, digest);
    if (text_n != t.size || memcmp(digest, t.sha256, 32)) {
        err = "The manifest is not the one the server named.";
        goto done;
    }
    if (json_parse(&j, (const char *)text, text_n) < 0 || j.t[0].type != JSON_OBJECT
        || !(want = index_files(&j, &want_n))) {
        err = "The server's manifest is unreadable.";
        goto done;
    }
    if (json_number(&j, json_get(&j, 0, "format")) > MANIFEST_FORMAT
        || json_number(&j, json_get(&j, 0, "manager")) > MANAGER_LEVEL) {
        err = "The build needs a newer manager.";
        goto done;
    }
    if ((err = choose_folder(s, &j, folder, folder_n)))
        goto done;

    /* what the folder holds now; a folder without a TES3X manifest is not ours to write */
    join_path(path, sizeof(path), folder, MANIFEST);
    owned = !read_file(path, &old_text, &old_n) && json_parse(&old, (char *)old_text, old_n) >= 0
            && old.t[0].type == JSON_OBJECT;
    if (owned)
        had = index_files(&old, &had_n);
    if (!files && !(files = malloc(sizeof(*files) * 16384))) {
        err = "Out of memory.";
        goto done;
    }
    n = walk(folder, "", files, 0, 16384);
    for (i = 0; i < n; i++)
        others += !staging_name(files[i]);
    if (!owned && others && !replace) {
        err = INSTALL_CONFIRM;
        goto done;
    }

    if (!(items = calloc((size_t)want_n + 1, sizeof(*items)))) {
        err = "Out of memory.";
        goto done;
    }
    console_get("OverlayBase", base, sizeof(base));
    n = 0;
    for (i = json_child(&j, json_get(&j, 0, "files")); i >= 0;
         i = json_sibling(&j, json_get(&j, 0, "files"), i)) {
        struct item *it = &items[n++];

        e = j.t[i].next;
        json_string(&j, i, it->path, sizeof(it->path));
        json_string(&j, json_get(&j, e, "sha256"), it->sha, sizeof(it->sha));
        json_string(&j, json_get(&j, e, "origin"), origin, sizeof(origin));
        it->size = (unsigned long long)json_number(&j, json_get(&j, e, "size"));
        if (!safe_path(it->path) || !name_cmp(it->path, MANIFEST)) {
            snprintf(reason, sizeof(reason), "The manifest names a path it may not: %.100s",
                     it->path);
            err = reason;
            goto done;
        }
        join_path(path, sizeof(path), folder, it->path);
        k = lookup(had, had_n, it->path);
        it->xbe = !strcmp(origin, "xbe");
        if (k && !strcmp(k->sha, it->sha) && file_size(path) == it->size)
            it->action = KEEP;
        else if (it->xbe)
            it->action = REBUILD;
        else if (!strcmp(origin, "retail") && base[0])
            it->action = COPY;
        else
            it->action = FETCH;
        if (it->action == FETCH && !strcmp(origin, "retail"))
            no_base++;
        count[it->action]++;
        if (it->action != KEEP)
            need += it->size;
    }
    mgr_log("install %s into %s: %d kept, %d to download, %d from the base, %d XBEs, %llu bytes\n",
            s->name, folder, count[KEEP], count[FETCH], count[COPY], count[REBUILD], need);
    if (no_base) {
        snprintf(reason, sizeof(reason),
                 "%d files come from your retail copy. Choose a retail base in Settings first.",
                 no_base);
        err = reason;
        goto done;
    }

    snprintf(drive, sizeof(drive), "%.2s\\", folder);
    if (GetDiskFreeSpaceExA(drive, &free_bytes, &total_bytes, NULL)
        && free_bytes.QuadPart < need + SPACE_MARGIN) {
        snprintf(reason, sizeof(reason), "%s needs %llu MB more than it has free.", drive,
                 (need + SPACE_MARGIN - free_bytes.QuadPart + (1 << 20) - 1) >> 20);
        err = reason;
        goto done;
    }

    for (i = 0; i < n && !err; i++) {
        struct item *it = &items[i];
        const char *why = NULL;
        char *slash;

        if (it->action == KEEP)
            continue;
        join_path(path, sizeof(path), folder, it->path);
        make_parents(path);
        slash = strrchr(path, '\\');
        snprintf(it->staged, sizeof(it->staged), "%.*s\\~t3x%u.new", (int)(slash - path), path,
                 staged_n++);
        if (it->action == REBUILD) {
            mgr_progress_title("Rebuilding");
            if (progress)
                progress(it->path, done, need);
            why = rebuild(&j, it, url_base, it->staged);
            if (!why)
                done += it->size;
            else
                mgr_log("install: %s not rebuilt: %s\n", it->path, why);
        } else if (it->action == COPY) {
            join_path(from, sizeof(from), base, it->path);
            mgr_progress_title("Copying");
            why = copy_checked(from, it->staged, it, &done, need, progress);
            if (why)
                mgr_log("install: %s not copied from the base: %s\n", it->path, why);
        }
        if (why && !strcmp(why, "cancelled")) {
            err = "Cancelled.";
        } else if (it->action == FETCH || why) {
            url_path(url, sizeof(url), url_base, "file/", it->path);
            mgr_progress_title("Downloading");
            if ((err = http_save(url, it->staged, it->size, it->sha, it->path, &done, need,
                                 progress))) {
                snprintf(reason, sizeof(reason), "%.80s: %s%s%s", it->path,
                         why ? why : err, why ? ", and the server: " : "", why ? err : "");
                err = !strcmp(err, "cancelled") ? "Cancelled." : reason;
            }
        }
        if (err)
            it->staged[0] = 0;
    }

    /* all here: rename them in, keeping the XBEs they replace */
    for (i = 0; i < n && !err; i++) {
        struct item *it = &items[i];

        if (it->action == KEEP)
            continue;
        join_path(path, sizeof(path), folder, it->path);
        if (it->xbe && file_size(path) != ~0ull) {
            snprintf(prev, sizeof(prev), "%s.prev", path);
            DeleteFileA(prev);
            MoveFileA(path, prev);
        }
        DeleteFileA(path);
        if (!MoveFileA(it->staged, path)) {
            snprintf(reason, sizeof(reason), "Could not rename %.100s in.", it->path);
            err = reason;
            break;
        }
        flush_path(path);
        it->staged[0] = 0;
    }
    if (err)
        goto done;

    /* what no longer belongs: staging leftovers, and files the build dropped; a file no manifest
     * listed is not the build's to remove */
    n = walk(folder, "", files, 0, 16384);
    for (i = 0; i < n; i++)
        if (staging_name(files[i])
            || (lookup(had, had_n, files[i]) && !lookup(want, want_n, files[i]))) {
            join_path(path, sizeof(path), folder, files[i]);
            removed += DeleteFileA(path) != 0;
            mgr_log("install: removed %s\n", files[i]);
        }
    stamp_load_order(&j, folder);
    err = write_manifest(folder, (const char *)text, text_n, s->name);
    snprintf(summary, summary_n, "%d downloaded, %d from the base, %d XBEs rebuilt, %d kept%s",
             count[FETCH], count[COPY], count[REBUILD], count[KEEP],
             removed ? ", old files removed" : "");
done:
    mgr_progress_title(NULL);
    /* a failed update leaves the old build as it was */
    for (i = 0; items && i < want_n; i++)
        if (items[i].staged[0])
            DeleteFileA(items[i].staged);
    mgr_log("install %s: %s, %lu ms\n", s->name, err ? err : summary, KeTickCount - start);
    free(items);
    index_free(had, had_n);
    index_free(want, want_n);
    json_free(&j);
    json_free(&old);
    free(text);
    free(old_text);
    return err;
}

/* Self-update. The manager folder holds the launcher as default.xbe and the manager in slots a and
 * b; slots.ini names the active slot and a pending one, which the launcher tries twice before
 * going back. A release arrives in E:\TES3X\update, signed with the project's release key: the
 * manager fetches it from the update feed, the PC writes it there through the agent, or anyone
 * by FTP or USB. A release replaces the manager
 * and with it the embedded key, so a key changes by shipping a manager that holds the next one. */

#include "mgr.h"
#include "sha256.h"
#include "monocypher-ed25519.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#ifndef RELEASE_PUB
#error RELEASE_PUB, the release key in hex, comes from keys/release.pub
#endif

#define INBOX "E:\\TES3X\\update"
#define RELEASE_NAME "release.json"
#define RELEASE INBOX "\\" RELEASE_NAME
#define PRODUCT "tes3x-manager"
#define RELEASE_FORMAT 1

/* tes3x_release.py reads a release's version from here */
__attribute__((used)) const char mgr_version_tag[] = "TES3X-MANAGER-VERSION " MGR_VERSION;

static char root[PATH_MAX_MGR]; /* the manager folder, empty when not installed on a disk */
static char slot[2];            /* "a" or "b", empty for a folder from before slots */

struct slots {
    char active[4], pending[4], failed[4];
    int tries;
};

static void slot_get(const char *text, const char *key, char *out, size_t n)
{
    size_t k = strlen(key), len;
    const char *line;

    for (line = text; *line; line += strcspn(line, "\n"), line += *line == '\n') {
        if (strncmp(line, key, k) || line[k] != '=')
            continue;
        len = strcspn(line + k + 1, "\r\n");
        if (len < n) {
            memcpy(out, line + k + 1, len);
            out[len] = 0;
        }
        return;
    }
}

static void slots_read(struct slots *s)
{
    char path[PATH_MAX_MGR], tries[8] = "0";
    unsigned char *data;
    size_t n;

    memset(s, 0, sizeof(*s));
    join_path(path, sizeof(path), root, "slots.ini");
    if (read_file(path, &data, &n))
        return;
    slot_get((char *)data, "active", s->active, sizeof(s->active));
    slot_get((char *)data, "pending", s->pending, sizeof(s->pending));
    slot_get((char *)data, "failed", s->failed, sizeof(s->failed));
    slot_get((char *)data, "tries", tries, sizeof(tries));
    s->tries = atoi(tries);
    free(data);
}

static int slots_write(const struct slots *s)
{
    char path[PATH_MAX_MGR], staged[PATH_MAX_MGR], text[96];
    int n;

    join_path(path, sizeof(path), root, "slots.ini");
    snprintf(staged, sizeof(staged), "%s.new", path);
    n = snprintf(text, sizeof(text), "active=%s\r\npending=%s\r\ntries=%d\r\nfailed=%s\r\n",
                 s->active, s->pending, s->tries, s->failed);
    if (write_flushed(staged, text, (size_t)n))
        return -1;
    DeleteFileA(path);
    if (!MoveFileA(staged, path))
        return -1;
    return flush_path(path);
}

static int version_cmp(const char *a, const char *b)
{
    long x, y;
    char *end;

    while (*a || *b) {
        x = strtol(a, &end, 10), a = *end == '.' ? end + 1 : end;
        y = strtol(b, &end, 10), b = *end == '.' ? end + 1 : end;
        if (x != y)
            return x < y ? -1 : 1;
    }
    return 0;
}

void update_locate(void)
{
    char path[PATH_MAX_MGR], *last;

    if (nt_to_drive(XeImageFileName->Buffer, XeImageFileName->Length, path, sizeof(path))
        || !(last = strrchr(path, '\\'))) {
        mgr_log("update: not started from a disk; self-update is off\n");
        return;
    }
    *last = 0;
    last = strrchr(path, '\\');
    if (last && (!name_cmp(last + 1, "a") || !name_cmp(last + 1, "b"))) {
        slot[0] = (char)(last[1] | 0x20);
        *last = 0;
    }
    snprintf(root, sizeof(root), "%s", path);
    mgr_log("update: folder %s, slot %s\n", root, slot[0] ? slot : "none");
}

const char *update_confirm(void)
{
    static char news[64];
    struct slots s;

    if (!root[0] || !slot[0])
        return NULL;
#ifdef MGR_TEST_NO_CONFIRM
    /* a test build that behaves as an update failing before its main screen */
    join_path(news, sizeof(news), root, "default.xbe");
    mgr_log("update: test build, not confirming slot %s\n", slot);
    mgr_launch_xbe(news);
#endif
    slots_read(&s);
    news[0] = 0;
    if (!strcmp(s.pending, slot)) {
        snprintf(s.active, sizeof(s.active), "%s", slot);
        s.pending[0] = 0;
        s.tries = 0;
        mgr_log("update: slot %s confirmed\n", slot);
        snprintf(news, sizeof(news), "Updated to manager %s.", MGR_VERSION);
    } else if (s.failed[0]) {
        mgr_log("update: slot %s failed to start; running slot %s\n", s.failed, slot);
        snprintf(news, sizeof(news), "An update did not start; kept manager %s.", MGR_VERSION);
        s.failed[0] = 0;
    } else {
        return NULL;
    }
    if (slots_write(&s))
        mgr_log("update: could not write slots.ini\n");
    return news;
}

static void clear_inbox(void)
{
    char pattern[PATH_MAX_MGR], path[PATH_MAX_MGR];
    WIN32_FIND_DATAA fd;
    HANDLE h;

    snprintf(pattern, sizeof(pattern), INBOX "\\*");
    if ((h = FindFirstFileA(pattern, &fd)) == INVALID_HANDLE_VALUE)
        return;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
            join_path(path, sizeof(path), INBOX, fd.cFileName);
            DeleteFileA(path);
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

static int hex_key(unsigned char key[32])
{
    const char *hex = RELEASE_PUB;
    unsigned v;
    int i;

    if (strlen(hex) != 64)
        return -1;
    for (i = 0; i < 32; i++) {
        if (sscanf(hex + 2 * i, "%2x", &v) != 1)
            return -1;
        key[i] = (unsigned char)v;
    }
    return 0;
}

/* Whether the release's text is signed with the release key and is a manager release; its
 * version goes into `version`. Why not, or NULL. */
static const char *release_check(const unsigned char *text, size_t n, const unsigned char *sig,
                                 size_t sig_n, struct json *j, char *version, size_t version_n)
{
    unsigned char key[32];

    if (!sig || sig_n != 64)
        return "the release is not signed";
    if (hex_key(key) || crypto_ed25519_check(sig, key, text, n))
        return "the release's signature is not the project's";
    if (json_parse(j, (const char *)text, n) < 0 || j->t[0].type != JSON_OBJECT
        || json_number(j, json_get(j, 0, "format")) != RELEASE_FORMAT
        || !json_equals(j, json_get(j, 0, "product"), PRODUCT)
        || json_string(j, json_get(j, 0, "version"), version, version_n) <= 0)
        return "the release is not a manager release this manager reads";
    return NULL;
}

/* Whether d is the file the release lists under `name`. */
static const char *entry_check(const struct json *j, const char *name, const unsigned char *d,
                               size_t n)
{
    char want[65], got[65];
    unsigned char digest[32];
    struct sha256 h;
    int e = json_get(j, json_get(j, 0, "files"), name);

    if (e < 0 || json_string(j, json_get(j, e, "sha256"), want, sizeof(want)) != 64)
        return "the release does not list its files";
    sha256_init(&h);
    sha256_update(&h, d, n);
    sha256_final(&h, digest);
    sha256_hex(digest, got);
    if (strcmp(got, want) || (long long)n != json_number(j, json_get(j, e, "size")))
        return "a file of the release does not match it";
    return NULL;
}

/* A file of the release from the inbox, checked, or NULL with *why set. */
static unsigned char *release_file(const struct json *j, const char *name, size_t *n,
                                   const char **why)
{
    char path[PATH_MAX_MGR];
    unsigned char *data;

    join_path(path, sizeof(path), INBOX, name);
    if (read_file(path, &data, n)) {
        *why = "a file of the release is missing";
        return NULL;
    }
    if ((*why = entry_check(j, name, data, *n))) {
        free(data);
        return NULL;
    }
    return data;
}

static int same_file(const char *path, const unsigned char *d, size_t n)
{
    unsigned char *data;
    size_t size;
    int same;

    if (read_file(path, &data, &size))
        return 0;
    same = size == n && !memcmp(data, d, n);
    free(data);
    return same;
}

const char *update_apply(char *version, size_t version_n, char *launcher, size_t launcher_n)
{
    unsigned char *text = NULL, *sig = NULL, *manager = NULL, *boot = NULL;
    char target[PATH_MAX_MGR], folder[PATH_MAX_MGR];
    const char *why;
    size_t n, sig_n = 0, manager_n, boot_n;
    struct json j = {0};
    struct slots s;
    int discard = 1;

    /* a manager run from elsewhere, a disc say, leaves the release to the installed one */
    if (!root[0] || read_file(RELEASE, &text, &n))
        return NULL;
    mgr_log("update: release in " INBOX "\n");
    read_file(RELEASE ".sig", &sig, &sig_n);
    if ((why = release_check(text, n, sig, sig_n, &j, version, version_n)))
        goto done;
    if (version_cmp(version, MGR_VERSION) <= 0) {
        why = "the release is not newer than this manager";
        goto done;
    }
    /* a release still arriving lacks or mismatches a file; keep it for the next start */
    discard = 0;
    if (!(manager = release_file(&j, "manager.xbe", &manager_n, &why))
        || !(boot = release_file(&j, "launcher.xbe", &boot_n, &why)))
        goto done;
    discard = 1;
    slots_read(&s);
    if (!s.active[0])
        snprintf(s.active, sizeof(s.active), "%s", slot);
    snprintf(s.pending, sizeof(s.pending), "%s", s.active[0] == 'a' ? "b" : "a");
    join_path(folder, sizeof(folder), root, s.pending);
    CreateDirectoryA(folder, NULL);
    join_path(target, sizeof(target), folder, "default.xbe");
    if ((why = replace_file(target, manager, manager_n)))
        goto done;
    join_path(launcher, launcher_n, root, "default.xbe");
    if (!same_file(launcher, boot, boot_n) && (why = replace_file(launcher, boot, boot_n)))
        goto done;
    s.tries = 0;
    s.failed[0] = 0;
    if (slots_write(&s))
        why = "could not write slots.ini";
    else
        mgr_log("update: manager %s in slot %s, pending\n", version, s.pending);
done:
    if (why)
        mgr_log("update: refused, %s\n", why);
    if (!why || discard)
        clear_inbox();
    json_free(&j);
    free(text);
    free(sig);
    free(manager);
    free(boot);
    return why ? why : "";
}

static int write_inbox(const char *name, const unsigned char *d, size_t n)
{
    char path[PATH_MAX_MGR];
    FILE *f;
    size_t put;

    CreateDirectoryA("E:\\TES3X", NULL);
    CreateDirectoryA(INBOX, NULL);
    join_path(path, sizeof(path), INBOX, name);
    if (!(f = fopen(path, "wb")))
        return -1;
    put = fwrite(d, 1, n, f);
    return fclose(f) || put != n ? -1 : 0;
}

const char *update_fetch(const char *feed, progress_fn progress, char *version, size_t version_n,
                         int *newer)
{
    static const char *const files[] = {"manager.xbe", "launcher.xbe"};
    static char message[96];
    char base[512], url[640];
    unsigned char *text = NULL, *sig = NULL, *file = NULL;
    size_t n = 0, sig_n = 0, file_n;
    const char *why;
    struct json j = {0};
    unsigned i;

    *newer = 0;
    if (!root[0])
        return "this manager is not installed on a disk";
    if (feed && *feed)
        snprintf(base, sizeof(base), "%s", feed);
    else if (!console_get("UpdateFeed", base, sizeof(base)))
        snprintf(base, sizeof(base), "%s", MGR_FEED);
    if (base[0] && base[strlen(base) - 1] != '/')
        strncat(base, "/", sizeof(base) - strlen(base) - 1);
    mgr_log("update: checking %s\n", base);
    snprintf(url, sizeof(url), "%s" "release.json", base);
    if (!(why = http_get(url, &text, &n, 64 * 1024, progress))) {
        snprintf(url, sizeof(url), "%s" "release.json.sig", base);
        why = http_get(url, &sig, &sig_n, 1024, progress);
    }
    if (!why)
        why = release_check(text, n, sig, sig_n, &j, version, version_n);
    if (!why && version_cmp(version, MGR_VERSION) <= 0) {
        snprintf(message, sizeof(message), "Manager %s is the newest; the feed has %s.",
                 MGR_VERSION, version);
        why = message;
    }
    for (i = 0; !why && i < sizeof(files) / sizeof(*files); i++) {
        snprintf(url, sizeof(url), "%s%s", base, files[i]);
        if (!(why = http_get(url, &file, &file_n, 16 << 20, progress))
            && !(why = entry_check(&j, files[i], file, file_n))
            && write_inbox(files[i], file, file_n))
            why = "could not write " INBOX;
        free(file);
        file = NULL;
    }
    /* release.json last, as the PC sends it: the manager keeps a release still arriving */
    if (!why && (write_inbox(RELEASE_NAME ".sig", sig, sig_n) || write_inbox(RELEASE_NAME, text, n)))
        why = "could not write " INBOX;
    if (!why) {
        *newer = 1;
        mgr_log("update: manager %s fetched\n", version);
    } else {
        mgr_log("update: fetch: %s\n", why);
    }
    json_free(&j);
    free(text);
    free(sig);
    return why;
}

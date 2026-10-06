/* Build folders: finding them by their manifest, checking them against it, starting them. */

#include "mgr.h"
#include "sha256.h"

#include <ctype.h>
#include <hal/xbox.h>
#include <nxdk/mount.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#define LAUNCH_PAGE 0x1000
#define CHUNK (256 * 1024)
#define MAX_BASES 8
#define BASE_LAYOUT "retail-base"
#define MANAGER_LAYOUT "manager"
#define RETAIL_TITLE_ID 0x42530005u

/* Game partitions in the order they are scanned, and the kernel device each drive letter is. */
static const struct {
    char letter;
    const char *device;
} drives[] = {
    {'F', "\\Device\\Harddisk0\\Partition6"},
    {'G', "\\Device\\Harddisk0\\Partition7"},
    {'E', "\\Device\\Harddisk0\\Partition1"},
    {'C', "\\Device\\Harddisk0\\Partition2"},
};

void mount_drives(void)
{
    char device[64];
    size_t i;

    for (i = 0; i < sizeof(drives) / sizeof(*drives); i++) {
        if (nxIsDriveMounted(drives[i].letter))
            continue;
        snprintf(device, sizeof(device), "%s\\", drives[i].device);
        nxMountDrive(drives[i].letter, device);
    }
}

/* The kernel caches writes; an update's files must be on the disk before a restart or power
 * loss can find them half there. */
int flush_path(const char *path)
{
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    IO_STATUS_BLOCK io;
    NTSTATUS r;

    if (h == INVALID_HANDLE_VALUE)
        return -1;
    r = NtFlushBuffersFile(h, &io);
    CloseHandle(h);
    return NT_SUCCESS(r) ? 0 : -1;
}

int write_flushed(const char *path, const void *d, size_t n)
{
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    IO_STATUS_BLOCK io;
    DWORD put = 0;
    int ok;

    if (h == INVALID_HANDLE_VALUE)
        return -1;
    ok = WriteFile(h, d, (DWORD)n, &put, NULL) && put == n && NT_SUCCESS(NtFlushBuffersFile(h, &io));
    CloseHandle(h);
    return ok ? 0 : -1;
}

int nt_to_drive(const char *nt, size_t len, char *out, size_t n)
{
    size_t i, k;

    for (i = 0; i < sizeof(drives) / sizeof(*drives); i++) {
        k = strlen(drives[i].device);
        if (len > k && nt[k] == '\\' && !name_cmp_n(nt, drives[i].device, k)
            && len - k + 3 <= n) {
            snprintf(out, n, "%c:%.*s", drives[i].letter, (int)(len - k), nt + k);
            return 0;
        }
    }
    return -1;
}

int read_file(const char *path, unsigned char **data, size_t *n)
{
    FILE *f = fopen(path, "rb");
    long size;

    *data = NULL;
    if (!f)
        return -1;
    if (fseek(f, 0, SEEK_END) || (size = ftell(f)) < 0 || fseek(f, 0, SEEK_SET)
        || !(*data = malloc((size_t)size + 1))
        || fread(*data, 1, (size_t)size, f) != (size_t)size) {
        fclose(f);
        free(*data);
        *data = NULL;
        return -1;
    }
    fclose(f);
    (*data)[size] = 0;
    *n = (size_t)size;
    return 0;
}

int name_cmp(const char *a, const char *b)
{
    for (; *a && tolower((unsigned char)*a) == tolower((unsigned char)*b); a++, b++)
        ;
    return tolower((unsigned char)*a) - tolower((unsigned char)*b);
}

int name_cmp_n(const char *a, const char *b, size_t n)
{
    for (; n && *a && tolower((unsigned char)*a) == tolower((unsigned char)*b); a++, b++, n--)
        ;
    return n ? tolower((unsigned char)*a) - tolower((unsigned char)*b) : 0;
}

void join_path(char *out, size_t n, const char *folder, const char *relative)
{
    size_t i;

    snprintf(out, n, "%s\\%s", folder, relative);
    for (i = strlen(folder); out[i]; i++)
        if (out[i] == '/')
            out[i] = '\\';
}

int manifest_load(const struct build *b, char **text, struct json *j)
{
    char path[PATH_MAX_MGR];
    size_t n;

    join_path(path, sizeof(path), b->path, MANIFEST);
    if (read_file(path, (unsigned char **)text, &n))
        return -1;
    if (json_parse(j, *text, n) < 0 || j->t[0].type != JSON_OBJECT) {
        free(*text);
        *text = NULL;
        return -1;
    }
    return 0;
}

void manifest_free(char *text, struct json *j)
{
    json_free(j);
    free(text);
}

static void summarize(struct build *b)
{
    struct json j;
    char *text;
    int files, i;

    if (manifest_load(b, &text, &j)) {
        snprintf(b->error, sizeof(b->error), "manifest unreadable");
        return;
    }
    if (json_number(&j, json_get(&j, 0, "format")) > MANIFEST_FORMAT
        || json_number(&j, json_get(&j, 0, "manager")) > MANAGER_LEVEL)
        snprintf(b->error, sizeof(b->error), "needs a newer manager");
    files = json_get(&j, 0, "files");
    if (files < 0 || j.t[files].type != JSON_OBJECT)
        snprintf(b->error, sizeof(b->error), "manifest has no file list");
    json_string(&j, json_get(&j, 0, "profile"), b->profile, sizeof(b->profile));
    json_string(&j, json_get(&j, 0, "install_layout"), b->layout, sizeof(b->layout));
    json_string(&j, json_get(&j, 0, "deployed"), b->deployed, sizeof(b->deployed));
    json_string(&j, json_get(&j, 0, "server"), b->server, sizeof(b->server));
    b->files = files < 0 ? 0 : j.t[files].count;
    for (i = json_child(&j, files); i >= 0; i = json_sibling(&j, files, i))
        b->bytes += (unsigned long long)json_number(&j, json_get(&j, j.t[i].next, "size"));
    i = json_get(&j, 0, "plugins");
    b->plugins = i < 0 ? 0 : j.t[i].count;
    i = json_get(&j, 0, "xbe");
    b->xbes = i < 0 ? 0 : j.t[i].count;
    manifest_free(text, &j);
}

/* Retail bases the last scan_builds saw by their manifest. */
static char installed[MAX_BASES][PATH_MAX_MGR];
static int installed_count;

static int scan_folder(const char *root, struct build *out, int n, int max)
{
    WIN32_FIND_DATAA fd;
    char pattern[PATH_MAX_MGR], manifest[PATH_MAX_MGR];
    HANDLE h;
    struct build *b;

    snprintf(pattern, sizeof(pattern), "%s\\*", root);
    if ((h = FindFirstFileA(pattern, &fd)) == INVALID_HANDLE_VALUE)
        return n;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.')
            continue;
        snprintf(manifest, sizeof(manifest), "%s\\%s\\" MANIFEST, root, fd.cFileName);
        if (GetFileAttributesA(manifest) == INVALID_FILE_ATTRIBUTES || n >= max)
            continue;
        b = &out[n++];
        memset(b, 0, sizeof(*b));
        snprintf(b->path, sizeof(b->path), "%s\\%s", root, fd.cFileName);
        snprintf(b->name, sizeof(b->name), "%s", fd.cFileName);
        summarize(b);
        /* a retail base is listed apart from the builds */
        if (!strcmp(b->layout, BASE_LAYOUT)) {
            if (installed_count < MAX_BASES)
                snprintf(installed[installed_count++], PATH_MAX_MGR, "%s", b->path);
            n--;
        } else if (!strcmp(b->layout, MANAGER_LAYOUT)) {
            n--;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return n;
}

int scan_builds(struct build *out, int max)
{
    char root[16];
    size_t i;
    int n = 0;

    installed_count = 0;
    for (i = 0; i < sizeof(drives) / sizeof(*drives); i++) {
        snprintf(root, sizeof(root), "%c:\\Games", drives[i].letter);
        n = scan_folder(root, out, n, max);
    }
    return n;
}

static int exists(const char *folder, const char *relative)
{
    char path[PATH_MAX_MGR];

    join_path(path, sizeof(path), folder, relative);
    return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
}

static int add_base(struct base *out, int n, int max, const char *path, int installed_base,
                    int has_xbe)
{
    if (n >= max)
        return n;
    memset(&out[n], 0, sizeof(*out));
    snprintf(out[n].path, sizeof(out[n].path), "%s", path);
    out[n].installed = installed_base;
    out[n].has_xbe = has_xbe;
    return n + 1;
}

/* Bases installed by TES3X, as the last scan_builds found them, then folders without a manifest
 * whose morrowind.xbe is a known retail image with the game's data beside it. The second kind is
 * only as clean as whoever copied it, which the user confirms. */
int find_bases(struct base *out, int max, progress_fn progress)
{
    WIN32_FIND_DATAA fd;
    char pattern[PATH_MAX_MGR], folder[PATH_MAX_MGR], xbe[PATH_MAX_MGR];
    unsigned title_id;
    HANDLE h;
    size_t d;
    int i, n = 0;

    for (i = 0; i < installed_count; i++) {
        join_path(xbe, sizeof(xbe), installed[i], "morrowind.xbe");
        n = add_base(out, n, max, installed[i], 1, xbe_known_retail(xbe));
    }
    for (d = 0; d < sizeof(drives) / sizeof(*drives); d++) {
        snprintf(pattern, sizeof(pattern), "%c:\\Games\\*", drives[d].letter);
        if ((h = FindFirstFileA(pattern, &fd)) == INVALID_HANDLE_VALUE)
            continue;
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.')
                continue;
            snprintf(folder, sizeof(folder), "%c:\\Games\\%s", drives[d].letter, fd.cFileName);
            join_path(xbe, sizeof(xbe), folder, "morrowind.xbe");
            if (exists(folder, MANIFEST) || xbe_title_id(xbe, &title_id)
                || title_id != RETAIL_TITLE_ID || !exists(folder, "Data Files\\Morrowind.esm"))
                continue;
            if (progress)
                progress(folder, 0, 0);
            if (xbe_known_retail(xbe))
                n = add_base(out, n, max, folder, 0, 1);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    return n;
}

const char *check_base(const char *folder, struct base *out)
{
    static struct build b;
    char xbe[PATH_MAX_MGR];
    unsigned title_id;
    int installed_base = 0;

    if (exists(folder, MANIFEST)) {
        memset(&b, 0, sizeof(b));
        snprintf(b.path, sizeof(b.path), "%s", folder);
        summarize(&b);
        if (strcmp(b.layout, BASE_LAYOUT))
            return "It is a TES3X build, not a retail copy.";
        installed_base = 1;
    }
    join_path(xbe, sizeof(xbe), folder, "morrowind.xbe");
    if (xbe_title_id(xbe, &title_id))
        return "It has no morrowind.xbe.";
    if (title_id != RETAIL_TITLE_ID)
        return "Its morrowind.xbe is not Morrowind.";
    if (!exists(folder, "Data Files\\Morrowind.esm"))
        return "It has no Data Files\\Morrowind.esm.";
    if (!xbe_known_retail(xbe))
        return "Its morrowind.xbe is not a retail image this manager knows.";
    add_base(out, 0, 1, folder, installed_base, 1);
    return NULL;
}

static int folder_cmp(const void *a, const void *b)
{
    return name_cmp(((const struct folder *)a)->name, ((const struct folder *)b)->name);
}

int list_folders(const char *path, struct folder *out, int max)
{
    WIN32_FIND_DATAA fd;
    char pattern[PATH_MAX_MGR];
    HANDLE h;
    size_t d;
    int n = 0;

    if (!path[0]) {
        for (d = 0; d < sizeof(drives) / sizeof(*drives) && n < max; d++, n++) {
            snprintf(out[n].name, sizeof(out[n].name), "%c:", drives[d].letter);
            out[n].game = 0;
        }
    } else {
        snprintf(pattern, sizeof(pattern), "%s\\*", path);
        if ((h = FindFirstFileA(pattern, &fd)) == INVALID_HANDLE_VALUE)
            return 0;
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.'
                || n >= max)
                continue;
            snprintf(out[n].name, sizeof(out[n].name), "%s", fd.cFileName);
            snprintf(pattern, sizeof(pattern), "%s\\%s", path, fd.cFileName);
            out[n++].game = exists(pattern, "morrowind.xbe");
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    qsort(out, n, sizeof(*out), folder_cmp);
    return n;
}

static int hash_file(const char *path, unsigned long long size, char hex[65],
                     unsigned long long *done, unsigned long long total, const char *name,
                     progress_fn progress)
{
    static unsigned char *buf;
    struct sha256 s;
    unsigned char digest[32];
    unsigned long long seen = 0;
    size_t got;
    FILE *f;

    if (!buf && !(buf = malloc(CHUNK)))
        return -1;
    if (!(f = fopen(path, "rb")))
        return -1;
    sha256_init(&s);
    while ((got = fread(buf, 1, CHUNK, f)) > 0) {
        sha256_update(&s, buf, got);
        seen += got;
        *done += got;
        if (progress && progress(name, *done, total)) {
            fclose(f);
            return -2;
        }
    }
    fclose(f);
    sha256_final(&s, digest);
    sha256_hex(digest, hex);
    return seen == size ? 0 : 1;
}

int verify_build(const struct build *b, struct verify *v, progress_fn progress)
{
    struct json j;
    char *text, name[PATH_MAX_MGR], path[PATH_MAX_MGR], want[65], got[65];
    unsigned long long done = 0;
    DWORD start = KeTickCount;
    int files, i, e, r;

    memset(v, 0, sizeof(*v));
    if (manifest_load(b, &text, &j))
        return -1;
    files = json_get(&j, 0, "files");
    mgr_log("verify %s: %d files, %llu bytes\n", b->path, b->files, b->bytes);
    for (i = json_child(&j, files); i >= 0; i = json_sibling(&j, files, i)) {
        e = j.t[i].next;
        json_string(&j, i, name, sizeof(name));
        json_string(&j, json_get(&j, e, "sha256"), want, sizeof(want));
        join_path(path, sizeof(path), b->path, name);
        r = hash_file(path, (unsigned long long)json_number(&j, json_get(&j, e, "size")), got,
                      &done, b->bytes, name, progress);
        if (r == -2) {
            v->cancelled = 1;
            break;
        }
        if (r == -1) {
            v->missing++;
            mgr_log("  missing: %s\n", name);
        } else if (r || strcmp(got, want)) {
            v->changed++;
            mgr_log("  changed: %s\n", name);
        } else {
            v->ok++;
        }
    }
    v->bytes = done;
    mgr_log("verify %s: %d ok, %d missing, %d changed%s, %lu ms\n", b->path, v->ok,
            v->missing, v->changed, v->cancelled ? ", cancelled" : "", KeTickCount - start);
    manifest_free(text, &j);
    return 0;
}

const char *launch_build(const struct build *b, void (*before)(void))
{
    char xbe[PATH_MAX_MGR];

    join_path(xbe, sizeof(xbe), b->path, "default.xbe");
    return launch_xbe(xbe, before);
}

static const char *launch_with(const char *xbe, const unsigned char *data, size_t n,
                               void (*before)(void));

const char *launch_xbe(const char *xbe, void (*before)(void))
{
    return launch_with(xbe, NULL, 0, before);
}

/* The engine's relaunch data (tes3xmulti.c): magic, -, -, mode, then a join after the save path. */
#define BXWM_MAGIC 0x4D575842u
#define BXWM_NEW_GAME 0u
#define JOIN_MAGIC 0x4A4D3354u
#define JOIN_AT 0x110u
#define JOIN_NAME 79u /* within the game's, as servers.ini names it */

const char *join_server(const char *folder, const char *server, void (*before)(void))
{
    static unsigned char data[0xC00];
    char xbe[PATH_MAX_MGR];

    if (strlen(server) > JOIN_NAME)
        return "the server's name is too long";
    memset(data, 0, sizeof(data));
    memcpy(data, &(unsigned){BXWM_MAGIC}, 4);
    memcpy(data + 0xC, &(unsigned){BXWM_NEW_GAME}, 4);
    memcpy(data + JOIN_AT, &(unsigned){JOIN_MAGIC}, 4);
    memcpy(data + JOIN_AT + 4, server, strlen(server));
    /* the engine itself: a full layout's default.xbe is the retail launcher */
    join_path(xbe, sizeof(xbe), folder, "morrowind.xbe");
    return launch_with(xbe, data, sizeof(data), before);
}

static const char *launch_with(const char *xbe, const unsigned char *data, size_t n,
                               void (*before)(void))
{
    PLAUNCH_DATA_PAGE page = LaunchDataPage;
    const char *slash = strrchr(xbe, '\\');
    unsigned title_id;
    size_t i;

    if (xbe_title_id(xbe, &title_id))
        return "the XBE is missing or not an XBE";
    for (i = 0; i < sizeof(drives) / sizeof(*drives) && drives[i].letter != xbe[0]; i++)
        ;
    if (i == sizeof(drives) / sizeof(*drives) || !slash || slash < xbe + 2)
        return "not on a known partition";
    if (!page && !(page = MmAllocateContiguousMemory(LAUNCH_PAGE)))
        return "no memory for the launch page";
    LaunchDataPage = page;
    MmPersistContiguousMemory(page, LAUNCH_PAGE, TRUE);
    memset(page, 0, LAUNCH_PAGE);
    page->Header.dwLaunchDataType = LDT_TITLE;
    /* XAPI's XGetLaunchInfo hands a title only data carrying its own title ID */
    page->Header.dwTitleId = title_id;
    /* the kernel takes "folder;file" and maps D: to the folder */
    snprintf(page->Header.szLaunchPath, sizeof(page->Header.szLaunchPath), "%s%.*s;%s",
             drives[i].device, (int)(slash - xbe - 2), xbe + 2, slash + 1);
    if (n)
        memcpy(page->LaunchData, data, n < sizeof(page->LaunchData) ? n : sizeof(page->LaunchData));
    mgr_log("launch %s title %08X\n", page->Header.szLaunchPath, title_id);
    if (before)
        before();
    HalReturnToFirmware(HalQuickRebootRoutine);
    return "the launch returned";
}

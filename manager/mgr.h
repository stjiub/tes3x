#ifndef MGR_H
#define MGR_H

#include <stddef.h>

#include "json.h"

#ifndef MGR_VERSION
#define MGR_VERSION "0.2"
#endif
#define MANIFEST "tes3xbuild.json"
/* The newest build manifest format this manager reads (tes3x_manifest.FORMAT). */
#define MANIFEST_FORMAT 1
#define MANAGER_LEVEL 1
#define PATH_MAX_MGR 260

struct build {
    char path[PATH_MAX_MGR]; /* "F:\Games\MorrowindNet" */
    char name[48];
    char profile[64];
    char layout[16];
    char deployed[24];
    int files, plugins, xbes;
    unsigned long long bytes;
    char error[64]; /* why the manifest cannot be used; empty when it can */
};

struct verify {
    int ok, missing, changed;
    unsigned long long bytes;
    int cancelled;
};

/* Called as work proceeds; returns nonzero to cancel. */
typedef int (*progress_fn)(const char *what, unsigned long long done, unsigned long long total);

void mgr_log(const char *format, ...) __attribute__((format(printf, 1, 2)));

void mount_drives(void);
int scan_builds(struct build *out, int max);
/* The manifest's text and its tokens; free both with manifest_free. */
int manifest_load(const struct build *b, char **text, struct json *j);
void manifest_free(char *text, struct json *j);
int verify_build(const struct build *b, struct verify *v, progress_fn progress);
/* Starts the build's default.xbe; returns only on failure, with a reason. */
const char *launch_build(const struct build *b, void (*before)(void));
const char *launch_xbe(const char *xbe, void (*before)(void));
/* main.c: stops the screen and starts an XBE; returns only on failure. */
void mgr_launch_xbe(const char *xbe);

/* console.c: [Xbox] keys of E:\TES3X\console.ini. Getters return the value's length, 0 when
 * absent; console_set keeps the file's other keys. */
#define CONSOLE_INI "E:\\TES3X\\console.ini"
int ini_get(const char *text, const char *key, char *out, size_t n);
int console_get(const char *key, char *out, size_t n);
int console_set(const char *key, const char *value);

/* Retail bases (builds.c): folders holding a clean retail copy, the overlay's OverlayBase and the
 * reference for XBE deltas. */
struct base {
    char path[PATH_MAX_MGR];
    int installed; /* by TES3X, with a retail-base manifest; otherwise found by its XBE alone */
    int has_xbe;   /* morrowind.xbe present with a known retail digest */
};
int find_bases(struct base *out, int max, progress_fn progress);

/* The agent (agent.c), on when E:\TES3X\console.ini sets NetAgent. */
void agent_start(void);
void agent_poll(void);
void agent_goodbye(void);
const char *agent_status(void);

/* XBE rebuild from the delta in a manifest's xbe entry (xbe.c), against OverlayBase's
 * morrowind.xbe. */
int xbe_title_id(const char *path, unsigned *title_id);
/* Whether the XBE at path is a retail image this manager knows, by its scene-form digest. */
int xbe_known_retail(const char *path);
const char *rebuild_xbe(const struct build *b, const char *xbe, const char *delta,
                        progress_fn progress);
/* Writes target as target.new, then renames it in, keeping the old file as target.prev. */
const char *replace_file(const char *target, const unsigned char *d, size_t n);

/* Self-update (update.c). update_locate finds the manager folder and slot it runs from;
 * update_confirm marks a pending slot good and returns news for the screen, or NULL;
 * update_apply installs a signed release from E:\TES3X\update into the other slot and returns
 * NULL when there is none, "" when the launcher it names should start it, or why not. */
void update_locate(void);
/* "a" or "b", empty when not running from a slot. */
const char *update_slot(void);
const char *update_confirm(void);
const char *update_apply(char *version, size_t version_n, char *launcher, size_t launcher_n);
/* Fetches the feed's release into E:\TES3X\update when it is newer (*newer set), else says why
 * not. The feed is `feed`, else console.ini's UpdateFeed, else MGR_FEED. */
const char *update_fetch(const char *feed, progress_fn progress, char *version, size_t version_n,
                         int *newer);

/* Where releases are published; a fork points this at its own. */
#ifndef MGR_FEED
#define MGR_FEED "https://github.com/stjiub/tes3x/releases/latest/download/"
#endif

/* agent.c: starts the network once, from console.ini's network keys; 0 when it is up. */
int net_up(void);
/* http.c: GET url (http or https, redirects followed) into a malloc'd *body of at most max
 * bytes, NUL-terminated; NULL, or why it failed. */
const char *http_get(const char *url, unsigned char **body, size_t *n, size_t max,
                     progress_fn progress);

int read_file(const char *path, unsigned char **data, size_t *n);
/* Writes a file and flushes it to the disk; flush_path flushes one already written. */
int write_flushed(const char *path, const void *d, size_t n);
int flush_path(const char *path);
/* "\Device\Harddisk0\Partition6\x" as "F:\x"; -1 off the partitions the manager knows. */
int nt_to_drive(const char *nt, size_t len, char *out, size_t n);
int name_cmp(const char *a, const char *b);
int name_cmp_n(const char *a, const char *b, size_t n);
void join_path(char *out, size_t n, const char *folder, const char *relative);

#endif

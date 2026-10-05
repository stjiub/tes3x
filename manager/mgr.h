#ifndef MGR_H
#define MGR_H

#include <stddef.h>

#include "json.h"

#define MGR_VERSION "0.1"
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

/* XBE rebuild from the delta in a manifest's xbe entry (xbe.c). */
int xbe_title_id(const char *path, unsigned *title_id);
const char *rebuild_xbe(const struct build *b, const char *xbe, const char *retail,
                        const char *delta, progress_fn progress);

int read_file(const char *path, unsigned char **data, size_t *n);
int name_cmp(const char *a, const char *b);
void join_path(char *out, size_t n, const char *folder, const char *relative);

#endif

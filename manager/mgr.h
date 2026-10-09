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
    char server[80]; /* the server the manager installed it from, as servers.ini names it */
    char error[64];  /* why the manifest cannot be used; empty when it can */
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
/* Deletes a build's folder and everything in it; NULL, or why not. */
const char *delete_build(const struct build *b, progress_fn progress);
const char *launch_xbe(const char *xbe, void (*before)(void));
/* main.c: stops the screen and starts an XBE; returns only on failure. */
void mgr_launch_xbe(const char *xbe);
/* The progress dialog's title until changed; NULL is "Working". */
void mgr_progress_title(const char *title);

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
/* Whether a folder the user chose can be the base, as find_bases judges a copy: NULL and *out
 * filled in if it can, else the reason. */
const char *check_base(const char *folder, struct base *out);

/* A subfolder, for choosing a base anywhere on the disk. */
#define FOLDER_NAME 43 /* FATX's 42 characters */
struct folder {
    char name[FOLDER_NAME];
    int game; /* holds morrowind.xbe */
};
/* The subfolders of `path` sorted by name, or the drives when it is empty. */
int list_folders(const char *path, struct folder *out, int max);

/* The agent (agent.c), on when E:\TES3X\console.ini sets NetAgent. */
void agent_start(void);
void agent_poll(void);
void agent_goodbye(void);
const char *agent_status(void);

/* XBE rebuild from the delta in a manifest's xbe entry (xbe.c), against the XBE in OverlayBase
 * that has the recipe's retail digest. */
int xbe_title_id(const char *path, unsigned *title_id);
/* Whether the XBE at path is a retail image this manager knows, by its scene-form digest. */
int xbe_known_retail(const char *path);
const char *rebuild_xbe(const struct build *b, const char *xbe, const char *delta,
                        progress_fn progress);
/* The output of a delta checked against its recipe's hashes, malloc'd into *out. */
const char *xbe_decode(const char *want_retail, const unsigned char *patch, size_t patch_n,
                       const char *want_out, unsigned char **out, size_t *out_n);
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
/* Stops the NIC before another title starts; net_up fails after it. */
void net_down(void);
/* http.c: GET url (http or https, redirects followed) into a malloc'd *body of at most max
 * bytes, NUL-terminated; NULL, or why it failed. */
const char *http_get(const char *url, unsigned char **body, size_t *n, size_t max,
                     progress_fn progress);

/* http.c: GET url into a file, checked against size and SHA-256 (hex) as it arrives; progress
 * counts from *done towards total. */
const char *http_save(const char *url, const char *path, unsigned long long size,
                      const char *sha256, const char *label, unsigned long long *done,
                      unsigned long long total, progress_fn progress);

/* Multiplayer servers (servers.c): those in each save pool's U:\TES3X\servers.ini, which the
 * game writes on joining one. */
struct server {
    char name[80]; /* the section, "host[:port]" as NetServer names it */
    char host[64];
    unsigned port;
    char file[PATH_MAX_MGR]; /* the servers.ini it is in */
    int has_server_key, has_client_key;
    unsigned char server_key[32], client_key[32];
    char password[65];
    char fingerprint[33]; /* of the pinned server key */
    int online; /* -1 not asked, 0 no answer, 1 answered, 2 manager update, 3 server update */
    char character[48]; /* the character this console last played there, from the server */
};
/* What a server's BUILD hands the manager: its manifest's hash and size, and a ticket for the
 * HTTP side at addr:port. */
struct ticket {
    unsigned char sha256[32], token[16];
    unsigned size, port, addr;
};
int servers_load(struct server *out, int max);
int server_add(const char *name, struct server *s);
int server_save(const struct server *s);
/* Drops the server's section from its servers.ini; the keys go with it. */
int server_remove(const struct server *s);
void server_fingerprint(const unsigned char key[32], char hex[33]);
/* Pins the server's key on first contact and keeps a new identity key; NULL, or why not:
 * SERVER_PASSWORD when the password is missing or wrong. */
extern const char SERVER_PASSWORD[];
extern const char SERVER_UPDATE[];
/* Whether the server answers a handshake; pins and changes nothing. */
int server_probe(const struct server *s);
const char *server_ticket(struct server *s, struct ticket *t, progress_fn progress);

/* install.c: the server's build into its folder, staged and hash-checked, the XBEs rebuilt from
 * the retail base. NULL and a summary, INSTALL_CONFIRM when the folder holds something else
 * (again with replace set to go ahead), or why not. The folder is filled in either way. */
extern const char INSTALL_CONFIRM[];
const char *server_install(struct server *s, int replace, char *folder, size_t folder_n,
                           char *summary, size_t summary_n, progress_fn progress);
/* Starts a build's engine with New Game and the server to join (the game's JOIN_MAGIC). */
const char *join_server(const char *folder, const char *server, void (*before)(void));

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

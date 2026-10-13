/* Content libraries: signed catalogs and tickets with a pinned server key, over HTTPS whose
 * certificate the signed replies name. A private library lists builds only to approved Dashes;
 * a multiplayer world's authorization reaches the one revision it requires. */

#include "mgr.h"
#include "monocypher-ed25519.h"
#include "../hooks/monocypher.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define CONTENT_FILE "E:\\TES3X\\content.ini"
#define CONTENT_ID_FILE "E:\\TES3X\\content-id.key"
#define CONTENT_RESULTS_FILE "E:\\TES3X\\content-results.txt"
#define CONTENT_RESULTS_MAX 16
#define CONTENT_ATTEMPTS_FILE "E:\\TES3X\\content-attempts.txt"
#define CONTENT_ATTEMPTS_MAX 16
#define CONTENT_MAX (64 * 1024 + 96)
#define CONTENT_PORT 26510

const char CONTENT_KEY_CHANGED[] = "The content server's key has changed.";
const char CONTENT_BUILD_CHANGED[] = "The offered build has changed.";
const char CONTENT_PENDING[] = "The library has not approved this Xbox.";
static unsigned char content_secret[64], content_public[32];
static int content_identity_loaded;

struct pending_result {
    char host[64], stage[10];
    unsigned port;
    unsigned char token[16], key[32];
};

struct pending_attempt {
    char host[64], entry[43], build[65], revision[65];
    unsigned port;
    unsigned char key[32], attempt[8];
};

static void retry_results(const struct content_host *host);

static int digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
           : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static int unhex(const char *text, unsigned char *out, size_t n)
{
    size_t i;
    int hi, lo;

    if (strlen(text) != n * 2)
        return -1;
    for (i = 0; i < n; i++) {
        hi = digit(text[i * 2]), lo = digit(text[i * 2 + 1]);
        if (hi < 0 || lo < 0)
            return -1;
        out[i] = (unsigned char)(hi << 4 | lo);
    }
    return 0;
}

static void hex(char *out, const unsigned char *data, size_t n)
{
    static const char abc[] = "0123456789abcdef";
    size_t i;
    for (i = 0; i < n; i++)
        out[i * 2] = abc[data[i] >> 4], out[i * 2 + 1] = abc[data[i] & 15];
    out[n * 2] = 0;
}

static int content_identity(void)
{
    unsigned char *saved = NULL, seed[32];
    size_t n = 0;
    int created = 0;

    if (content_identity_loaded)
        return 0;
    if (!read_file(CONTENT_ID_FILE, &saved, &n)) {
        if (n != sizeof(seed)) {
            free(saved);
            return -1;
        }
        memcpy(seed, saved, sizeof(seed));
        free(saved);
    } else {
        if (server_random(seed, sizeof(seed)))
            return -1;
        created = 1;
    }
    if (created) {
        CreateDirectoryA("E:\\TES3X", NULL);
        if (write_flushed(CONTENT_ID_FILE, seed, sizeof(seed))) {
            crypto_wipe(seed, sizeof(seed));
            return -1;
        }
    }
    crypto_ed25519_key_pair(content_secret, content_public, seed);
    crypto_wipe(seed, sizeof(seed));
    content_identity_loaded = 1;
    return 0;
}

static int load_results(struct pending_result rows[CONTENT_RESULTS_MAX])
{
    unsigned char *data = NULL;
    size_t size;
    char *line, *next, token[33], key[65], extra;
    int count = 0;

    if (read_file(CONTENT_RESULTS_FILE, &data, &size))
        return 0;
    if (size > 4096) {
        free(data);
        return -1;
    }
    for (line = (char *)data; *line && count < CONTENT_RESULTS_MAX; line = next) {
        next = strpbrk(line, "\r\n");
        if (next) {
            *next++ = 0;
            next += strspn(next, "\r\n");
        } else {
            next = line + strlen(line);
        }
        if (sscanf(line, "%63s %u %32s %64s %9s %c", rows[count].host,
                   &rows[count].port, token, key, rows[count].stage, &extra) != 5
            || !rows[count].port || rows[count].port > 65535
            || unhex(token, rows[count].token, 16) || unhex(key, rows[count].key, 32)
            || (strcmp(rows[count].stage, "complete")
                && strcmp(rows[count].stage, "failed")
                && strcmp(rows[count].stage, "cancelled")))
            continue;
        count++;
    }
    free(data);
    return count;
}

static int save_results(const struct pending_result *rows, int count)
{
    char text[4096], token[33], key[65], staged[sizeof CONTENT_RESULTS_FILE + 8];
    size_t used = 0;
    int i, n;

    if (!count) {
        DeleteFileA(CONTENT_RESULTS_FILE);
        return 0;
    }
    for (i = 0; i < count; i++) {
        hex(token, rows[i].token, 16);
        hex(key, rows[i].key, 32);
        n = snprintf(text + used, sizeof(text) - used, "%s %u %s %s %s\n",
                     rows[i].host, rows[i].port, token, key, rows[i].stage);
        if (n < 0 || (size_t)n >= sizeof(text) - used)
            return -1;
        used += (size_t)n;
    }
    CreateDirectoryA("E:\\TES3X", NULL);
    snprintf(staged, sizeof(staged), "%s.new", CONTENT_RESULTS_FILE);
    if (write_flushed(staged, text, used))
        return -1;
    DeleteFileA(CONTENT_RESULTS_FILE);
    return MoveFileA(staged, CONTENT_RESULTS_FILE) ? 0 : -1;
}

static int queue_result(const struct content_host *host, const struct ticket *ticket,
                        const char *stage)
{
    struct pending_result rows[CONTENT_RESULTS_MAX], *row;
    int count = load_results(rows), i;

    if (count < 0 || !host->has_key)
        return -1;
    for (i = 0; i < count; i++)
        if (!memcmp(rows[i].token, ticket->token, 16) && !memcmp(rows[i].key, host->key, 32))
            break;
    if (i == count) {
        if (count == CONTENT_RESULTS_MAX) {
            memmove(rows, rows + 1, sizeof(rows[0]) * (CONTENT_RESULTS_MAX - 1));
            i = count - 1;
        } else {
            count++;
        }
    }
    row = &rows[i];
    memset(row, 0, sizeof(*row));
    snprintf(row->host, sizeof(row->host), "%s", host->host);
    snprintf(row->stage, sizeof(row->stage), "%s", stage);
    row->port = host->port;
    memcpy(row->token, ticket->token, 16);
    memcpy(row->key, host->key, 32);
    return save_results(rows, count);
}

static void remove_result(const struct content_host *host, const struct ticket *ticket)
{
    struct pending_result rows[CONTENT_RESULTS_MAX];
    int count = load_results(rows), i, kept = 0;

    if (count < 0)
        return;
    for (i = 0; i < count; i++)
        if (memcmp(rows[i].token, ticket->token, 16) || memcmp(rows[i].key, host->key, 32))
            rows[kept++] = rows[i];
    if (kept != count && save_results(rows, kept))
        mgr_log("content: could not clear an acknowledged result\n");
}

static int split(struct content_host *host)
{
    const char *colon = strrchr(host->name, ':');
    size_t n = colon ? (size_t)(colon - host->name) : strlen(host->name);
    unsigned port = CONTENT_PORT;
    const char *p;

    if (!n || n >= sizeof(host->host))
        return -1;
    for (p = host->name; *p && p < host->name + n; p++)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z')
              || (*p >= '0' && *p <= '9') || *p == '.' || *p == '-'))
            return -1;
    if (colon) {
        if (!colon[1])
            return -1;
        for (p = colon + 1; *p; p++)
            if (*p < '0' || *p > '9')
                return -1;
        port = (unsigned)atoi(colon + 1);
    }
    if (!port || port > 65535)
        return -1;
    memcpy(host->host, host->name, n);
    host->host[n] = 0;
    host->port = port;
    return 0;
}

static void fingerprint(const unsigned char key[32], char out[33])
{
    server_fingerprint(key, out);
}

int content_load(struct content_host *out, int max)
{
    unsigned char *data;
    size_t size;
    char *line, *next, *space, *tls;
    int count = 0;

    if (read_file(CONTENT_FILE, &data, &size))
        return 0;
    for (line = (char *)data; *line && count < max; line = next) {
        next = strpbrk(line, "\r\n");
        if (next) {
            *next++ = 0;
            next += strspn(next, "\r\n");
        } else {
            next = line + strlen(line);
        }
        space = strchr(line, ' ');
        if (space)
            *space++ = 0;
        tls = space ? strchr(space, ' ') : NULL;
        if (tls)
            *tls++ = 0;
        if (strlen(line) >= sizeof(out[count].name))
            continue;
        memset(&out[count], 0, sizeof(out[count]));
        snprintf(out[count].name, sizeof(out[count].name), "%s", line);
        if (split(&out[count]))
            continue;
        if (space && !unhex(space, out[count].key, 32)) {
            out[count].has_key = 1;
            fingerprint(out[count].key, out[count].fingerprint);
            out[count].has_tls = tls && !unhex(tls, out[count].tls, 32);
        }
        count++;
    }
    free(data);
    return count;
}

static int save_host(const struct content_host *host, int remove)
{
    struct content_host hosts[32];
    char text[12288], key[65], tls[66], staged[sizeof CONTENT_FILE + 8];
    int count = content_load(hosts, 32), i, found = 0;
    size_t n = 0;

    for (i = 0; i < count; i++)
        if (!name_cmp(hosts[i].name, host->name)) {
            hosts[i] = *host;
            found = 1;
            break;
        }
    if (!found && !remove && count < 32)
        hosts[count++] = *host;
    else if (!found && !remove)
        return -1;
    for (i = 0; i < count; i++) {
        if (remove && !name_cmp(hosts[i].name, host->name))
            continue;
        key[0] = tls[0] = 0;
        if (hosts[i].has_key)
            hex(key, hosts[i].key, 32);
        if (hosts[i].has_key && hosts[i].has_tls) {
            tls[0] = ' ';
            hex(tls + 1, hosts[i].tls, 32);
        }
        n += (size_t)snprintf(text + n, sizeof(text) - n, "%s%s%s%s\n", hosts[i].name,
                              key[0] ? " " : "", key, tls);
        if (n >= sizeof(text))
            return -1;
    }
    CreateDirectoryA("E:\\TES3X", NULL);
    snprintf(staged, sizeof(staged), "%s.new", CONTENT_FILE);
    if (write_flushed(staged, text, n))
        return -1;
    DeleteFileA(CONTENT_FILE);
    return MoveFileA(staged, CONTENT_FILE) ? 0 : -1;
}

int content_add(const char *name, struct content_host *out)
{
    struct content_host existing[32];
    int count = content_load(existing, 32), i;

    for (i = 0; i < count; i++)
        if (!name_cmp(existing[i].name, name)) {
            *out = existing[i];
            return 0;
        }
    memset(out, 0, sizeof(*out));
    if (strlen(name) >= sizeof(out->name))
        return -1;
    snprintf(out->name, sizeof(out->name), "%s", name);
    return split(out) || save_host(out, 0) ? -1 : 0;
}

static int valid_id(const char *id)
{
    const char *p;

    if (!*id || !((*id >= 'a' && *id <= 'z') || (*id >= 'A' && *id <= 'Z')
                  || (*id >= '0' && *id <= '9')))
        return 0;
    for (p = id; *p; p++)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z')
              || (*p >= '0' && *p <= '9') || *p == '.' || *p == '-' || *p == '_'))
            return 0;
    return 1;
}

static int load_attempts(struct pending_attempt rows[CONTENT_ATTEMPTS_MAX])
{
    unsigned char *data = NULL;
    size_t size;
    char *line, *next, key[65], attempt[17], extra;
    int count = 0;

    if (read_file(CONTENT_ATTEMPTS_FILE, &data, &size))
        return 0;
    if (size > 8192) {
        free(data);
        return -1;
    }
    for (line = (char *)data; *line && count < CONTENT_ATTEMPTS_MAX; line = next) {
        next = strpbrk(line, "\r\n");
        if (next) {
            *next++ = 0;
            next += strspn(next, "\r\n");
        } else {
            next = line + strlen(line);
        }
        if (sscanf(line, "%63s %u %64s %42s %64s %64s %16s %c", rows[count].host,
                   &rows[count].port, key, rows[count].entry, rows[count].build,
                   rows[count].revision, attempt, &extra) != 7
            || !rows[count].port || rows[count].port > 65535
            || !valid_id(rows[count].entry) || strlen(rows[count].build) != 64
            || strlen(rows[count].revision) != 64
            || unhex(rows[count].build, (unsigned char[32]){0}, 32)
            || unhex(rows[count].revision, (unsigned char[32]){0}, 32)
            || unhex(key, rows[count].key, 32)
            || unhex(attempt, rows[count].attempt, 8))
            continue;
        count++;
    }
    free(data);
    return count;
}

static int save_attempts(const struct pending_attempt *rows, int count)
{
    char text[8192], key[65], attempt[17], staged[sizeof CONTENT_ATTEMPTS_FILE + 8];
    size_t used = 0;
    int i, n;

    if (!count) {
        DeleteFileA(CONTENT_ATTEMPTS_FILE);
        return 0;
    }
    for (i = 0; i < count; i++) {
        hex(key, rows[i].key, 32);
        hex(attempt, rows[i].attempt, 8);
        n = snprintf(text + used, sizeof(text) - used, "%s %u %s %s %s %s %s\n",
                     rows[i].host, rows[i].port, key, rows[i].entry, rows[i].build,
                     rows[i].revision, attempt);
        if (n < 0 || (size_t)n >= sizeof(text) - used)
            return -1;
        used += (size_t)n;
    }
    CreateDirectoryA("E:\\TES3X", NULL);
    snprintf(staged, sizeof(staged), "%s.new", CONTENT_ATTEMPTS_FILE);
    if (write_flushed(staged, text, used))
        return -1;
    DeleteFileA(CONTENT_ATTEMPTS_FILE);
    return MoveFileA(staged, CONTENT_ATTEMPTS_FILE) ? 0 : -1;
}

static int find_attempt(const struct pending_attempt *rows, int count,
                        const struct content_host *host, const struct content_entry *entry)
{
    int i;
    for (i = 0; i < count; i++)
        if (!strcmp(rows[i].host, host->host) && rows[i].port == host->port
            && !memcmp(rows[i].key, host->key, 32)
            && !strcmp(rows[i].entry, entry->id)
            && !strcmp(rows[i].build, entry->build)
            && !strcmp(rows[i].revision, entry->revision))
            return i;
    return -1;
}

static int get_attempt(const struct content_host *host, const struct content_entry *entry,
                       unsigned char attempt[8])
{
    struct pending_attempt rows[CONTENT_ATTEMPTS_MAX], *row;
    int count = load_attempts(rows), i;

    if (count < 0 || !host->has_key)
        return -1;
    i = find_attempt(rows, count, host, entry);
    if (i >= 0) {
        memcpy(attempt, rows[i].attempt, 8);
        return 0;
    }
    if (server_random(attempt, 8))
        return -1;
    if (count == CONTENT_ATTEMPTS_MAX) {
        memmove(rows, rows + 1, sizeof(rows[0]) * (CONTENT_ATTEMPTS_MAX - 1));
        i = count - 1;
    } else {
        i = count++;
    }
    row = &rows[i];
    memset(row, 0, sizeof(*row));
    snprintf(row->host, sizeof(row->host), "%s", host->host);
    snprintf(row->entry, sizeof(row->entry), "%s", entry->id);
    snprintf(row->build, sizeof(row->build), "%s", entry->build);
    snprintf(row->revision, sizeof(row->revision), "%s", entry->revision);
    row->port = host->port;
    memcpy(row->key, host->key, 32);
    memcpy(row->attempt, attempt, 8);
    return save_attempts(rows, count);
}

void content_attempt_done(const struct content_host *host, const struct content_entry *entry,
                          const struct ticket *ticket)
{
    struct pending_attempt rows[CONTENT_ATTEMPTS_MAX];
    int count = load_attempts(rows), i, kept = 0;

    if (count < 0)
        return;
    for (i = 0; i < count; i++)
        if (find_attempt(rows + i, 1, host, entry) < 0
            || memcmp(rows[i].attempt, ticket->attempt, 8))
            rows[kept++] = rows[i];
    if (kept != count && save_attempts(rows, kept))
        mgr_log("content: could not clear a finished install attempt\n");
}

int content_remove(const struct content_host *host)
{
    return save_host(host, 1);
}

int content_accept_key(struct content_host *host)
{
    if (!host->has_pending)
        return -1;
    memcpy(host->key, host->pending_key, 32);
    host->has_key = 1;
    host->has_pending = host->has_tls = 0;
    fingerprint(host->key, host->fingerprint);
    return save_host(host, 0);
}

/* A signed reply over HTTPS. Pinned: the connection must present the certificate the last
 * signed reply named, as before anything that carries a ticket. The reply's own "tls" must name
 * the certificate this connection presented. */
static const char *get_signed(struct content_host *host, const char *path, int pinned,
                              struct json *j, unsigned char **body)
{
    char url[1280], named_hex[65];
    unsigned char seen[32], named[32];
    size_t size;
    const char *error;
    int dirty = 0;

    if (pinned && !host->has_tls)
        return "The content server's certificate is not known yet.";
    snprintf(url, sizeof(url), "https://%s:%u%s", host->host, host->port, path);
    http_pin(host->host, host->port, pinned ? host->tls : NULL);
    error = http_get(url, body, &size, CONTENT_MAX, NULL);
    http_pin(host->host, host->port, NULL);
    if (error)
        return error;
    if (http_last_cert(seen))
        return "The content server did not offer a secure connection.";
    if (size < 98 || crypto_ed25519_check(*body + 32, *body, *body + 96, size - 96))
        return "The content response has an invalid signature.";
    if (host->has_key && memcmp(host->key, *body, 32)) {
        memcpy(host->pending_key, *body, 32);
        fingerprint(host->pending_key, host->pending_fingerprint);
        host->has_pending = 1;
        return CONTENT_KEY_CHANGED;
    }
    if (json_parse(j, (const char *)*body + 96, size - 96) < 0
        || j->t[0].type != JSON_OBJECT
        || json_number(j, json_get(j, 0, "format")) != 1)
        return "The content response is unreadable.";
    if (json_string(j, json_get(j, 0, "tls"), named_hex, sizeof(named_hex)) != 64
        || unhex(named_hex, named, 32) || crypto_verify32(named, seen))
        return "The content server's certificate does not match its key.";
    if (!host->has_key) {
        memcpy(host->key, *body, 32);
        host->has_key = 1;
        fingerprint(host->key, host->fingerprint);
        dirty = 1;
    }
    if (!host->has_tls || crypto_verify32(host->tls, named)) {
        memcpy(host->tls, named, 32);
        host->has_tls = dirty = 1;
    }
    if (dirty && save_host(host, 0))
        return "Could not keep the content server's key.";
    retry_results(host);
    return NULL;
}

const char *content_fingerprint(void)
{
    static char fp[33];

    if (content_identity())
        return "?";
    fingerprint(content_public, fp);
    return fp;
}

/* The catalog request, signed by this Dash for the certificate its library last named. */
static const char *catalog_request(struct content_host *host, struct json *j,
                                   unsigned char **body)
{
    char path[320], pub[65], signature[129], tls[65], message[80];
    unsigned char sig[64];

    if (!host->has_tls || content_identity())
        return get_signed(host, "/v1/catalog", 0, j, body);
    hex(pub, content_public, 32);
    hex(tls, host->tls, 32);
    snprintf(message, sizeof(message), "catalog:%s", tls);
    crypto_ed25519_sign(sig, content_secret, (const unsigned char *)message, strlen(message));
    hex(signature, sig, 64);
    snprintf(path, sizeof(path), "/v1/catalog?client=%s&sig=%s", pub, signature);
    return get_signed(host, path, 1, j, body);
}

const char *content_catalog(struct content_host *host, struct content_entry *out, int max,
                            int *count)
{
    struct json j = {0};
    unsigned char *body = NULL;
    const char *err;
    char access[16];
    int list, i, value, signed_request = host->has_tls;

    *count = 0;
    err = catalog_request(host, &j, &body);
    if (!err && json_string(&j, json_get(&j, 0, "access"), access, sizeof(access)) > 0
        && !strcmp(access, "private") && !signed_request) {
        /* the first reply named the certificate; ask again as this Dash */
        json_free(&j);
        free(body);
        body = NULL;
        err = catalog_request(host, &j, &body);
    }
    if (err)
        goto done;
    if (json_string(&j, json_get(&j, 0, "access"), access, sizeof(access)) <= 0
        || (strcmp(access, "public") && strcmp(access, "approved"))) {
        err = CONTENT_PENDING;
        goto done;
    }
    list = json_get(&j, 0, "entries");
    if (list < 0 || j.t[list].type != JSON_ARRAY || j.t[list].count > max) {
        err = "The content catalog is too large or invalid.";
        goto done;
    }
    for (i = json_child(&j, list); i >= 0; i = json_sibling(&j, list, i)) {
        struct content_entry *entry = &out[*count];
        memset(entry, 0, sizeof(*entry));
        if (json_string(&j, json_get(&j, i, "id"), entry->id, sizeof(entry->id)) <= 0
            || !valid_id(entry->id)
            || json_string(&j, json_get(&j, i, "name"), entry->name, sizeof(entry->name)) <= 0
            || json_string(&j, json_get(&j, i, "build"), entry->build, sizeof(entry->build)) != 64
            || json_string(&j, json_get(&j, i, "revision"), entry->revision,
                           sizeof(entry->revision)) != 64
            || json_string(&j, json_get(&j, i, "layout"), entry->layout,
                           sizeof(entry->layout)) <= 0) {
            err = "The content catalog has an invalid build.";
            goto done;
        }
        value = json_get(&j, i, "bytes");
        entry->bytes = (unsigned long long)json_number(&j, value);
        value = json_get(&j, i, "latest");
        if (value < 0 || j.t[value].type != JSON_NUMBER
            || (json_number(&j, value) != 0 && json_number(&j, value) != 1)) {
            err = "The content catalog has an invalid revision.";
            goto done;
        }
        entry->latest = (int)json_number(&j, value);
        (*count)++;
    }
done:
    json_free(&j);
    free(body);
    return err;
}

const char *content_ticket(struct content_host *host, const struct content_entry *entry,
                           const struct content_auth *auth, struct ticket *out)
{
    struct json j = {0};
    unsigned char *body = NULL;
    char path[1024], hex_sha[65], hex_token[33], hex_attempt[17], build[65], revision[65];
    char pub[65], signature[129], message[320], tls[65], world[256] = "";
    unsigned char attempt[8], sig[64];
    const char *err;

    memset(out, 0, sizeof(*out));
    if (content_identity())
        return "Could not load the Dash content identity.";
    if (!host->has_tls)
        return "Browse the content library first.";
    if (get_attempt(host, entry, attempt))
        return "Could not keep the content install attempt.";
    hex(pub, content_public, 32);
    hex(hex_attempt, attempt, 8);
    hex(tls, host->tls, 32);
    snprintf(message, sizeof(message), "ticket:%s:%s:%s:%s:%d:%s", entry->id, entry->build,
             entry->revision, hex_attempt, entry->latest, tls);
    crypto_ed25519_sign(sig, content_secret, (const unsigned char *)message, strlen(message));
    hex(signature, sig, 64);
    if (auth)
        snprintf(world, sizeof(world), "&world=%s&expires=%llu&wsig=%s", auth->world,
                 auth->expires, auth->signature);
    snprintf(path, sizeof(path),
             "/v1/ticket/%s?client=%s&build=%s&revision=%s&attempt=%s&latest=%d&sig=%s%s",
             entry->id, pub, entry->build, entry->revision, hex_attempt,
             entry->latest, signature, world);
    err = get_signed(host, path, 1, &j, &body);
    if (err)
        goto done;
    if (json_string(&j, json_get(&j, 0, "build"), build, sizeof(build)) != 64
        || json_string(&j, json_get(&j, 0, "revision"), revision,
                       sizeof(revision)) != 64) {
        err = "The content ticket is invalid.";
        goto done;
    }
    if (strcmp(build, entry->build) || strcmp(revision, entry->revision)) {
        err = CONTENT_BUILD_CHANGED;
        goto done;
    }
    if (json_string(&j, json_get(&j, 0, "sha256"), hex_sha, sizeof(hex_sha)) != 64
        || json_string(&j, json_get(&j, 0, "token"), hex_token, sizeof(hex_token)) != 32
        || json_string(&j, json_get(&j, 0, "attempt"), hex_attempt,
                       sizeof(hex_attempt)) != 16
        || unhex(hex_sha, out->sha256, 32) || unhex(hex_token, out->token, 16)
        || unhex(hex_attempt, out->attempt, 8) || memcmp(out->attempt, attempt, 8)) {
        err = "The content ticket is invalid.";
        goto done;
    }
    out->size = (unsigned)json_number(&j, json_get(&j, 0, "size"));
    out->port = host->port;
    memcpy(out->tls, host->tls, 32);
    out->has_tls = 1;
    snprintf(out->revision, sizeof(out->revision), "%s", revision);
    if (!out->size || out->size > 16u << 20)
        err = "The content ticket has an invalid manifest size.";
done:
    json_free(&j);
    free(body);
    return err;
}

static int send_report(const struct content_host *host, const struct ticket *ticket,
                       const char *stage, unsigned long long done, unsigned long long total)
{
    char token[33], signature[129], message[128], url[512];
    unsigned char sig[64];
    unsigned char *body = NULL;
    size_t n;
    const char *err;

    hex(token, ticket->token, 16);
    snprintf(message, sizeof(message), "%s:%s:%llu:%llu", token, stage, done, total);
    crypto_ed25519_sign(sig, content_secret, (const unsigned char *)message, strlen(message));
    hex(signature, sig, sizeof(sig));
    if (!host->has_tls)
        return -1;
    snprintf(url, sizeof(url),
             "https://%s:%u/v1/report/%s?stage=%s&done=%llu&total=%llu&sig=%s",
             host->host, host->port, token, stage, done, total, signature);
    http_pin(host->host, host->port, host->tls);
    err = http_get(url, &body, &n, 128, NULL);
    http_pin(host->host, host->port, NULL);
    free(body);
    return err ? -1 : 0;
}

int content_report(const struct content_host *host, const struct ticket *ticket,
                   const char *stage, unsigned long long done, unsigned long long total)
{
    int terminal = !strcmp(stage, "complete") || !strcmp(stage, "failed")
                   || !strcmp(stage, "cancelled");
    int queued = 0, result;

    if (terminal && (queued = queue_result(host, ticket, stage)))
        mgr_log("content: could not keep the final install result\n");
    result = send_report(host, ticket, stage, done, total);
    if (terminal && !result && !queued)
        remove_result(host, ticket);
    return result;
}

static void retry_results(const struct content_host *host)
{
    struct pending_result rows[CONTENT_RESULTS_MAX], keep[CONTENT_RESULTS_MAX];
    struct ticket ticket = {0};
    int count, i, kept = 0;

    if (!host->has_key || content_identity() || (count = load_results(rows)) <= 0)
        return;
    for (i = 0; i < count; i++) {
        if (!strcmp(rows[i].host, host->host) && rows[i].port == host->port
            && !memcmp(rows[i].key, host->key, 32)) {
            memcpy(ticket.token, rows[i].token, 16);
            if (!send_report(host, &ticket, rows[i].stage, 0, 0))
                continue;
        }
        keep[kept++] = rows[i];
    }
    if (kept != count && save_results(keep, kept))
        mgr_log("content: could not clear acknowledged install results\n");
}

/* Standalone content libraries: signed catalogs and tickets with a pinned server key. */

#include "mgr.h"
#include "monocypher-ed25519.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define CONTENT_FILE "E:\\TES3X\\content.ini"
#define CONTENT_MAX (64 * 1024 + 96)
#define CONTENT_PORT 26510

const char CONTENT_KEY_CHANGED[] = "The content server's key has changed.";
const char CONTENT_BUILD_CHANGED[] = "The offered build has changed.";

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
    char *line, *next, *space;
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
        if (strlen(line) >= sizeof(out[count].name))
            continue;
        memset(&out[count], 0, sizeof(out[count]));
        snprintf(out[count].name, sizeof(out[count].name), "%s", line);
        if (split(&out[count]))
            continue;
        if (space && !unhex(space, out[count].key, 32)) {
            out[count].has_key = 1;
            fingerprint(out[count].key, out[count].fingerprint);
        }
        count++;
    }
    free(data);
    return count;
}

static int save_host(const struct content_host *host, int remove)
{
    struct content_host hosts[32];
    char text[8192], key[65], staged[sizeof CONTENT_FILE + 8];
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
        if (hosts[i].has_key)
            hex(key, hosts[i].key, 32);
        else
            key[0] = 0;
        n += (size_t)snprintf(text + n, sizeof(text) - n, "%s%s%s\n", hosts[i].name,
                              key[0] ? " " : "", key);
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
    host->has_pending = 0;
    fingerprint(host->key, host->fingerprint);
    return save_host(host, 0);
}

static const char *get_signed(struct content_host *host, const char *path, struct json *j,
                              unsigned char **body)
{
    char url[256];
    size_t size;
    const char *error;

    snprintf(url, sizeof(url), "http://%s:%u%s", host->host, host->port, path);
    if ((error = http_get(url, body, &size, CONTENT_MAX, NULL)))
        return error;
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
    if (!host->has_key) {
        memcpy(host->key, *body, 32);
        host->has_key = 1;
        fingerprint(host->key, host->fingerprint);
        if (save_host(host, 0))
            return "Could not keep the content server's key.";
    }
    return NULL;
}

const char *content_catalog(struct content_host *host, struct content_entry *out, int max,
                            int *count)
{
    struct json j = {0};
    unsigned char *body = NULL;
    const char *err;
    int list, i, value;

    *count = 0;
    err = get_signed(host, "/v1/catalog", &j, &body);
    if (err)
        goto done;
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
            || json_string(&j, json_get(&j, i, "layout"), entry->layout,
                           sizeof(entry->layout)) <= 0) {
            err = "The content catalog has an invalid build.";
            goto done;
        }
        value = json_get(&j, i, "bytes");
        entry->bytes = (unsigned long long)json_number(&j, value);
        (*count)++;
    }
done:
    json_free(&j);
    free(body);
    return err;
}

const char *content_ticket(struct content_host *host, const struct content_entry *entry,
                           struct ticket *out)
{
    struct json j = {0};
    unsigned char *body = NULL;
    char path[96], hex_sha[65], hex_token[33], build[65];
    const char *err;

    snprintf(path, sizeof(path), "/v1/ticket/%s", entry->id);
    err = get_signed(host, path, &j, &body);
    if (err)
        goto done;
    if (json_string(&j, json_get(&j, 0, "build"), build, sizeof(build)) != 64) {
        err = "The content ticket is invalid.";
        goto done;
    }
    if (strcmp(build, entry->build)) {
        err = CONTENT_BUILD_CHANGED;
        goto done;
    }
    if (json_string(&j, json_get(&j, 0, "sha256"), hex_sha, sizeof(hex_sha)) != 64
        || json_string(&j, json_get(&j, 0, "token"), hex_token, sizeof(hex_token)) != 32
        || unhex(hex_sha, out->sha256, 32) || unhex(hex_token, out->token, 16)) {
        err = "The content ticket is invalid.";
        goto done;
    }
    out->size = (unsigned)json_number(&j, json_get(&j, 0, "size"));
    out->port = host->port;
    if (!out->size || out->size > 16u << 20)
        err = "The content ticket has an invalid manifest size.";
done:
    json_free(&j);
    free(body);
    return err;
}

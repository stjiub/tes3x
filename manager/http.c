/* HTTP and HTTPS GET into memory or a file, following redirects: the update feed and servers'
 * builds. TLS is mbedTLS (tls_config.h) without certificate checks: what is fetched is trusted by
 * a signature or by a hash that came through the server's session. */

#include "mgr.h"
#include "sha256.h"

#include <lwip/netdb.h>
#include <lwip/sockets.h>
#include <mbedtls/ssl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#include "../hooks/monocypher.h"

#define MAX_REDIRECTS 5
#define TIMEOUT_MS 20000
#define HEAD_MAX 8192
#define URL_MAX 2048 /* GitHub's download redirects carry signed queries of about 1 KB */

struct conn {
    int sock, tls;
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
};

/* Session keys for public downloads need not be secret, only fresh: BLAKE2b over the cycle
 * counter, sampled as network waits make it vary. */
static unsigned char pool[64];

static void stir(void)
{
    unsigned lo, hi;
    unsigned char sample[12];

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    memcpy(sample, &lo, 4), memcpy(sample + 4, &hi, 4);
    memcpy(sample + 8, (const void *)&KeTickCount, 4);
    crypto_blake2b_keyed(pool, sizeof(pool), pool, sizeof(pool), sample, sizeof(sample));
}

static int rng(void *unused, unsigned char *out, size_t n)
{
    unsigned char block[64];
    size_t k;

    (void)unused;
    for (; n; n -= k, out += k) {
        stir();
        crypto_blake2b_keyed(block, sizeof(block), pool, sizeof(pool), (const unsigned char *)"out", 3);
        k = n < 32 ? n : 32;
        memcpy(out, block, k);
    }
    return 0;
}

static int wait_readable(int sock)
{
    fd_set readable;
    struct timeval wait = {TIMEOUT_MS / 1000, 0};

    FD_ZERO(&readable);
    FD_SET(sock, &readable);
    return select(sock + 1, &readable, NULL, NULL, &wait) > 0;
}

static int bio_send(void *ctx, const unsigned char *d, size_t n)
{
    int r = send(*(int *)ctx, d, n, 0);

    return r < 0 ? MBEDTLS_ERR_SSL_INTERNAL_ERROR : r;
}

static int bio_recv(void *ctx, unsigned char *d, size_t n)
{
    int r;

    if (!wait_readable(*(int *)ctx))
        return MBEDTLS_ERR_SSL_TIMEOUT;
    stir();
    r = recv(*(int *)ctx, d, n, 0);
    return r < 0 ? MBEDTLS_ERR_SSL_INTERNAL_ERROR : r;
}

static int conn_write(struct conn *c, const char *d, size_t n)
{
    int r;

    while (n) {
        r = c->tls ? mbedtls_ssl_write(&c->ssl, (const unsigned char *)d, n)
                   : send(c->sock, d, n, 0);
        if (r == MBEDTLS_ERR_SSL_WANT_WRITE)
            continue;
        if (r <= 0)
            return -1;
        d += r, n -= (size_t)r;
    }
    return 0;
}

/* Bytes read, 0 at the end of the stream, -1 on an error. */
static int conn_read(struct conn *c, unsigned char *d, size_t n)
{
    int r;

    if (!c->tls)
        return wait_readable(c->sock) ? recv(c->sock, d, n, 0) : -1;
    do
        r = mbedtls_ssl_read(&c->ssl, d, n);
    while (r == MBEDTLS_ERR_SSL_WANT_READ);
    return r == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY ? 0 : r;
}

static void conn_close(struct conn *c)
{
    if (c->tls) {
        mbedtls_ssl_free(&c->ssl);
        mbedtls_ssl_config_free(&c->conf);
    }
    if (c->sock >= 0)
        closesocket(c->sock);
    c->sock = -1;
}

static const char *conn_open(struct conn *c, const char *host, const char *port, int tls)
{
    struct addrinfo hints = {0}, *res = NULL;
    int r, tries;

    memset(c, 0, sizeof(*c));
    c->sock = -1;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    /* a DHCP lease may still be on its way */
    for (tries = 0; (r = getaddrinfo(host, port, &hints, &res)) != 0 && tries < 10; tries++)
        Sleep(1000);
    if (r != 0 || !res)
        return "the server's name was not found";
    c->sock = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    r = c->sock < 0 ? -1 : connect(c->sock, res->ai_addr, res->ai_addrlen);
    freeaddrinfo(res);
    if (r < 0) {
        conn_close(c);
        return "could not connect to the server";
    }
    if (!(c->tls = tls))
        return NULL;
    mbedtls_ssl_init(&c->ssl);
    mbedtls_ssl_config_init(&c->conf);
    if (mbedtls_ssl_config_defaults(&c->conf, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM,
                                    MBEDTLS_SSL_PRESET_DEFAULT)) {
        conn_close(c);
        return "TLS setup failed";
    }
    mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_NONE);
    mbedtls_ssl_conf_rng(&c->conf, rng, NULL);
    if (mbedtls_ssl_setup(&c->ssl, &c->conf) || mbedtls_ssl_set_hostname(&c->ssl, host)) {
        conn_close(c);
        return "TLS setup failed";
    }
    mbedtls_ssl_set_bio(&c->ssl, &c->sock, bio_send, bio_recv, NULL);
    while ((r = mbedtls_ssl_handshake(&c->ssl)) != 0)
        if (r != MBEDTLS_ERR_SSL_WANT_READ && r != MBEDTLS_ERR_SSL_WANT_WRITE) {
            mgr_log("http: TLS handshake with %s: -0x%04X\n", host, (unsigned)-r);
            conn_close(c);
            return "the secure connection failed";
        }
    return NULL;
}

/* scheme://host[:port]/path into its parts; nonzero if not http or https. */
static int split_url(const char *url, int *tls, char *host, size_t host_n, char *port,
                     char *path, size_t path_n)
{
    const char *p, *slash, *colon;
    size_t len;

    if (!strncmp(url, "https://", 8))
        *tls = 1, p = url + 8;
    else if (!strncmp(url, "http://", 7))
        *tls = 0, p = url + 7;
    else
        return -1;
    slash = p + strcspn(p, "/");
    colon = memchr(p, ':', (size_t)(slash - p));
    len = (size_t)((colon ? colon : slash) - p);
    if (!len || len >= host_n)
        return -1;
    memcpy(host, p, len);
    host[len] = 0;
    snprintf(port, 8, "%.*s", colon ? (int)(slash - colon - 1) : 3,
             colon ? colon + 1 : *tls ? "443" : "80");
    snprintf(path, path_n, "%s", *slash ? slash : "/");
    return 0;
}

static const char *header(const char *head, const char *name, char *out, size_t n)
{
    const char *line;
    size_t k = strlen(name), len;

    for (line = strstr(head, "\r\n"); line && line[2]; line = strstr(line + 2, "\r\n")) {
        if (name_cmp_n(line + 2, name, k) || line[2 + k] != ':')
            continue;
        line += 3 + k;
        while (*line == ' ')
            line++;
        len = strcspn(line, "\r\n");
        if (len >= n)
            return NULL;
        memcpy(out, line, len);
        out[len] = 0;
        return out;
    }
    return NULL;
}

/* A chunked body, read to its end, decoded in place. */
static int dechunk(unsigned char *d, size_t *n)
{
    size_t in = 0, out = 0, k;
    unsigned char *eol;

    for (;;) {
        if (in >= *n || !(eol = memchr(d + in, '\n', *n - in)))
            return -1;
        k = strtoul((char *)d + in, NULL, 16);
        in = (size_t)(eol - d) + 1;
        if (!k) {
            *n = out;
            return 0;
        }
        if (k > *n - in)
            return -1;
        memmove(d + out, d + in, k);
        out += k, in += k + 2;
    }
}

static int grow(unsigned char **body, size_t *cap, size_t need, size_t max)
{
    unsigned char *p;
    size_t to = *cap ? *cap : 65536;

    if (need > max)
        return -1;
    while (to < need)
        to *= 2;
    if (to > max)
        to = max;
    if (to <= *cap)
        return 0;
    if (!(p = realloc(*body, to)))
        return -1;
    *body = p, *cap = to;
    return 0;
}

/* Reads the body after the head; `have` bytes of it already arrived in `buf`. */
static const char *read_body(struct conn *c, const char *head, unsigned char *buf, size_t have,
                             unsigned char **body, size_t *n, size_t max, const char *label,
                             progress_fn progress)
{
    char value[32];
    unsigned char chunk[4096];
    size_t cap = 0, total, want;
    int r, sized = header(head, "Content-Length", value, sizeof(value)) != NULL;
    long long length = sized ? atoll(value) : -1;

    if (header(head, "Transfer-Encoding", value, sizeof(value)) && !name_cmp(value, "chunked"))
        sized = 0, length = -1;
    if (length > (long long)max)
        return "the download is larger than expected";
    *n = 0;
    total = sized ? (size_t)length : 0;
    if (grow(body, &cap, sized ? total + 1 : have + 1, max + 1))
        return "out of memory";
    memcpy(*body, buf, have);
    *n = have;
    while (!sized || *n < total) {
        want = sizeof(chunk);
        r = conn_read(c, chunk, want);
        if (r == 0 && !sized)
            break;
        if (r <= 0)
            return "the download was cut short";
        if (grow(body, &cap, *n + (size_t)r + 1, max + 1))
            return sized ? "out of memory" : "the download is larger than expected";
        memcpy(*body + *n, chunk, (size_t)r);
        *n += (size_t)r;
        if (progress && progress(label, *n, total))
            return "cancelled";
    }
    if (sized && *n > total)
        *n = total;
    if (header(head, "Transfer-Encoding", value, sizeof(value)) && !name_cmp(value, "chunked")
        && dechunk(*body, n))
        return "the server's chunked reply was not understood";
    (*body)[*n] = 0;
    return NULL;
}

/* Sends GET for url, following redirects, and reads the head of a 200 reply into head; the
 * body's first `*have` bytes follow it at head + *body_at. */
static const char *open_get(const char *url, struct conn *c, char *head, size_t *body_at,
                            size_t *have)
{
    static char path[URL_MAX], location[URL_MAX], current[URL_MAX];
    static char request[URL_MAX + 256];
    char host[128], port[8];
    const char *err;
    int tls, redirects, status, r, usual;
    size_t got;
    char *blank;

    if (net_up())
        return "the network did not start";
    snprintf(current, sizeof(current), "%s", url);
    for (redirects = 0; redirects <= MAX_REDIRECTS; redirects++) {
        if (split_url(current, &tls, host, sizeof(host), port, path, sizeof(path)))
            return "not an http or https address";
        mgr_log("http: GET %s\n", current);
        if ((err = conn_open(c, host, port, tls)))
            return err;
        usual = !strcmp(port, tls ? "443" : "80");
        snprintf(request, sizeof(request),
                 "GET %s HTTP/1.1\r\nHost: %s%s%s\r\nUser-Agent: tes3x-manager/" MGR_VERSION
                 "\r\nAccept: */*\r\nConnection: close\r\n\r\n", path, host,
                 usual ? "" : ":", usual ? "" : port);
        if (conn_write(c, request, strlen(request))) {
            conn_close(c);
            return "the request could not be sent";
        }
        for (got = 0, blank = NULL; !blank && got < HEAD_MAX; got += (size_t)r) {
            r = conn_read(c, (unsigned char *)head + got, HEAD_MAX - got);
            if (r <= 0)
                break;
            head[got + (size_t)r] = 0;
            blank = strstr(head, "\r\n\r\n");
        }
        if (!blank || sscanf(head, "HTTP/1.%*d %d", &status) != 1) {
            conn_close(c);
            return "the server's reply was not understood";
        }
        *body_at = (size_t)(blank - head) + 4;
        *have = got - *body_at;
        blank[2] = 0;
        mgr_log("http: %d\n", status);
        if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
            conn_close(c);
            if (!header(head, "Location", location, sizeof(location)))
                return "the server redirected nowhere";
            if (location[0] == '/')
                snprintf(current, sizeof(current), "%s://%s:%s%s", tls ? "https" : "http", host,
                         port, location);
            else
                snprintf(current, sizeof(current), "%s", location);
            continue;
        }
        if (status != 200) {
            conn_close(c);
            return status == 404 ? "not found on the server" : "the server refused the request";
        }
        return NULL;
    }
    return "too many redirects";
}

const char *http_get(const char *url, unsigned char **body, size_t *n, size_t max,
                     progress_fn progress)
{
    static char head[HEAD_MAX + 1];
    const char *err, *label = strrchr(url, '/') ? strrchr(url, '/') + 1 : url;
    struct conn c;
    size_t at, have;

    *body = NULL;
    *n = 0;
    if ((err = open_get(url, &c, head, &at, &have)))
        return err;
    err = read_body(&c, head, (unsigned char *)head + at, have, body, n, max, label, progress);
    conn_close(&c);
    if (err) {
        free(*body);
        *body = NULL;
        *n = 0;
    }
    return err;
}

#define SAVE_BUFFER (256 * 1024)

const char *http_save(const char *url, const char *path, unsigned long long size,
                      const char *sha256, const char *label, unsigned long long *done,
                      unsigned long long total, progress_fn progress)
{
    static char head[HEAD_MAX + 1];
    static unsigned char *buf;
    struct conn c;
    struct sha256 s;
    unsigned char digest[32];
    char value[32], hex[65];
    unsigned long long seen = 0;
    size_t at, have, fill;
    const char *err;
    DWORD put;
    HANDLE f;
    int r;

    if (!buf && !(buf = malloc(SAVE_BUFFER)))
        return "out of memory";
    if ((err = open_get(url, &c, head, &at, &have)))
        return err;
    if (!header(head, "Content-Length", value, sizeof(value))
        || strtoull(value, NULL, 10) != size) {
        conn_close(&c);
        return "the server's file is not the size the manifest gives";
    }
    f = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) {
        conn_close(&c);
        return "could not create the file";
    }
    sha256_init(&s);
    memcpy(buf, head + at, have);
    fill = have;
    for (;;) {
        if (fill == SAVE_BUFFER || seen + fill >= size) {
            if (seen + fill > size) {
                err = "the server sent more than the file";
                break;
            }
            sha256_update(&s, buf, fill);
            if (!WriteFile(f, buf, (DWORD)fill, &put, NULL) || put != fill) {
                err = "could not write the file";
                break;
            }
            seen += fill, *done += fill, fill = 0;
            if (progress && progress(label, *done, total)) {
                err = "cancelled";
                break;
            }
            if (seen == size)
                break;
        }
        r = conn_read(&c, buf + fill, SAVE_BUFFER - fill);
        if (r <= 0) {
            err = "the download was cut short";
            break;
        }
        fill += (size_t)r;
    }
    conn_close(&c);
    CloseHandle(f);
    if (!err) {
        sha256_final(&s, digest);
        sha256_hex(digest, hex);
        if (strcmp(hex, sha256))
            err = "the file does not match the manifest";
    }
    if (err)
        DeleteFileA(path);
    return err;
}

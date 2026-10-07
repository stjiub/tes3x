/* Multiplayer servers: the ones servers.ini knows, and the session that asks one for its build.
 * The session is the game's (tes3xmulti.c): Noise XX with this console's key for that server,
 * the server's key pinned on first contact, and a HELLO flagged MANAGER, answered with BUILD
 * (the manifest's SHA-256 and a ticket for its HTTP side) instead of a place in the world. */

#include "mgr.h"

#include <lwip/netdb.h>
#include <lwip/sockets.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#include "../hooks/monocypher.h"
#include "../hooks/tes3xnoise.h"

#define UDATA "E:\\UDATA"
/* the game's own pool, where a server added here goes */
#define GAME_POOL UDATA "\\42530005\\TES3X"
#define TRUST_MAX 4096
#define SERVER_PORT 26500

#define T3MP_VERSION 18
#define OUTER 16
#define INNER 16
enum { HELLO = 1, REFUSE = 9, BUILD = 12, HANDSHAKE1 = 20, HANDSHAKE2, HANDSHAKE3, SEALED };
#define HANDSHAKE_PAD 128
#define HELLO_BYTES 74 /* MAC, XBE build, load order, plugin count, clock, manifest build */
#define MANAGER_FLAG 0x40000000u
#define BUILD_BYTES 56
#define RETRY_MS 500
#define TRIES 12
#define ENTROPY_SAMPLES 256

static const unsigned char prologue[] = "TES3X T3MP 11";

static unsigned get32(const unsigned char *p)
{
    return p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}

static void put32(unsigned char *p, unsigned v)
{
    p[0] = (unsigned char)v, p[1] = (unsigned char)(v >> 8);
    p[2] = (unsigned char)(v >> 16), p[3] = (unsigned char)(v >> 24);
}

/* --- servers.ini --- */

static int hex_digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
           : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static int hex_read(const char *text, size_t n, unsigned char *out, size_t bytes)
{
    size_t i;
    int hi, lo;

    if (n < 2 * bytes)
        return 0;
    for (i = 0; i < bytes; i++) {
        if ((hi = hex_digit(text[2 * i])) < 0 || (lo = hex_digit(text[2 * i + 1])) < 0)
            return 0;
        out[i] = (unsigned char)(hi << 4 | lo);
    }
    return 1;
}

static void hex_write(char *out, const unsigned char *p, size_t n)
{
    static const char digits[] = "0123456789abcdef";
    size_t i;

    for (i = 0; i < n; i++)
        out[2 * i] = digits[p[i] >> 4], out[2 * i + 1] = digits[p[i] & 15];
    out[2 * n] = 0;
}

static size_t line_len(const char *p)
{
    return strcspn(p, "\r\n");
}

/* "host[:port]" into its parts. */
static int split_name(struct server *s)
{
    const char *colon = strrchr(s->name, ':');
    size_t n = colon ? (size_t)(colon - s->name) : strlen(s->name);

    if (!n || n >= sizeof(s->host))
        return 0;
    memcpy(s->host, s->name, n);
    s->host[n] = 0;
    s->port = colon ? (unsigned)atoi(colon + 1) : SERVER_PORT;
    return s->port > 0 && s->port < 65536;
}

void server_fingerprint(const unsigned char key[32], char hex[33])
{
    unsigned char fp[16];

    crypto_blake2b(fp, sizeof(fp), key, 32);
    hex_write(hex, fp, sizeof(fp));
}

static int read_ini(const char *file, struct server *out, int n, int max)
{
    unsigned char *data;
    const char *p;
    struct server *s = NULL;
    size_t len, size;
    int i;

    if (read_file(file, &data, &size))
        return n;
    for (p = (const char *)data; *p; p += len, p += strspn(p, "\r\n")) {
        len = line_len(p);
        if (p[0] == '[' && len > 2 && p[len - 1] == ']') {
            s = NULL;
            if (len - 2 >= sizeof(out->name))
                continue;
            /* a name in two pools is one server; the later file's keys are not merged */
            for (i = 0; i < n && name_cmp_n(out[i].name, p + 1, len - 2); i++)
                ;
            if (i < n && strlen(out[i].name) == len - 2)
                continue;
            if (n >= max)
                break;
            s = &out[n];
            memset(s, 0, sizeof(*s));
            memcpy(s->name, p + 1, len - 2);
            snprintf(s->file, sizeof(s->file), "%s", file);
            if (!split_name(s))
                s = NULL;
            else
                n++;
        } else if (s && len > 11 && !strncmp(p, "server_key=", 11)) {
            s->has_server_key = hex_read(p + 11, len - 11, s->server_key, 32);
        } else if (s && len > 11 && !strncmp(p, "client_key=", 11)) {
            s->has_client_key = hex_read(p + 11, len - 11, s->client_key, 32);
        } else if (s && len >= 9 && !strncmp(p, "password=", 9) && len - 9 < sizeof(s->password)) {
            memcpy(s->password, p + 9, len - 9);
            s->password[len - 9] = 0;
        }
    }
    free(data);
    return n;
}

/* Every save pool's servers.ini: the game keeps one per title, under U:\TES3X. */
int servers_load(struct server *out, int max)
{
    WIN32_FIND_DATAA fd;
    char file[PATH_MAX_MGR];
    HANDLE h;
    int n = 0, i;

    n = read_ini(GAME_POOL "\\servers.ini", out, n, max);
    if ((h = FindFirstFileA(UDATA "\\*", &fd)) != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.'
                || !name_cmp(fd.cFileName, "42530005"))
                continue;
            snprintf(file, sizeof(file), UDATA "\\%s\\TES3X\\servers.ini", fd.cFileName);
            n = read_ini(file, out, n, max);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    for (i = 0; i < n; i++)
        if (out[i].has_server_key)
            server_fingerprint(out[i].server_key, out[i].fingerprint);
    return n;
}

static int make_folder(const char *path)
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
    return CreateDirectoryA(part, NULL) || GetLastError() == ERROR_ALREADY_EXISTS ? 0 : -1;
}

/* The file with the server's section rewritten last, as the game's trust_save writes it; the
 * other sections are kept. Through a new file, so a failed write never loses
 * the keys there. */
int server_save(const struct server *s)
{
    unsigned char *data = NULL;
    char *out, key[65], folder[PATH_MAX_MGR], staged[PATH_MAX_MGR + 8], *slash;
    const char *p;
    size_t len, size = 0, o = 0;
    int inside = 0, r;

    read_file(s->file, &data, &size);
    if (!(out = malloc(size + 512))) {
        free(data);
        return -1;
    }
    for (p = data ? (const char *)data : ""; *p; p += len, p += strspn(p, "\r\n")) {
        len = line_len(p);
        if (p[0] == '[')
            inside = len == strlen(s->name) + 2 && !name_cmp_n(p + 1, s->name, len - 2);
        if (inside || !len || o + len + 2 > size + 256)
            continue;
        memcpy(out + o, p, len);
        o += len;
        out[o++] = '\r', out[o++] = '\n';
    }
    o += (size_t)sprintf(out + o, "[%s]\r\n", s->name);
    if (s->has_server_key) {
        hex_write(key, s->server_key, 32);
        o += (size_t)sprintf(out + o, "server_key=%s\r\n", key);
    }
    if (s->has_client_key) {
        hex_write(key, s->client_key, 32);
        o += (size_t)sprintf(out + o, "client_key=%s\r\n", key);
        crypto_wipe(key, sizeof(key));
    }
    if (s->password[0])
        o += (size_t)sprintf(out + o, "password=%s\r\n", s->password);
    free(data);
    snprintf(folder, sizeof(folder), "%s", s->file);
    if ((slash = strrchr(folder, '\\')))
        *slash = 0;
    make_folder(folder);
    snprintf(staged, sizeof(staged), "%s.new", s->file);
    r = write_flushed(staged, out, o);
    crypto_wipe(out, o);
    free(out);
    if (r)
        return -1;
    DeleteFileA(s->file);
    if (!MoveFileA(staged, s->file))
        return -1;
    return 0;
}

/* A server for the game's pool, from "host[:port]"; nonzero if the name is not one. */
int server_add(const char *name, struct server *s)
{
    memset(s, 0, sizeof(*s));
    if (!name[0] || strlen(name) >= sizeof(s->name) || strpbrk(name, "[]\r\n#"))
        return -1;
    snprintf(s->name, sizeof(s->name), "%s", name);
    snprintf(s->file, sizeof(s->file), GAME_POOL "\\servers.ini");
    return split_name(s) ? server_save(s) : -1;
}

/* --- the session --- */

static unsigned char pool[64];
static unsigned mixed;

static void stir(void)
{
    unsigned lo, hi;
    unsigned char sample[12];

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    put32(sample, lo), put32(sample + 4, hi), put32(sample + 8, KeTickCount);
    crypto_blake2b_keyed(pool, sizeof(pool), pool, sizeof(pool), sample, sizeof(sample));
    mixed++;
}

/* The cycle counter's jitter across timer interrupts and network waits, hashed: the kernel has no
 * random source, and this makes the console's identity key. */
static void random_bytes(unsigned char *out, size_t n, int sock)
{
    unsigned char block[64];
    struct timeval wait = {0, 1000};
    fd_set readable;

    while (mixed < ENTROPY_SAMPLES) {
        FD_ZERO(&readable);
        FD_SET(sock, &readable);
        select(sock + 1, &readable, NULL, NULL, &wait);
        stir();
    }
    stir();
    crypto_blake2b_keyed(block, sizeof(block), pool, sizeof(pool), (const unsigned char *)"out", 3);
    memcpy(out, block, n);
    crypto_blake2b_keyed(pool, sizeof(pool), pool, sizeof(pool), (const unsigned char *)"next", 4);
    crypto_wipe(block, sizeof(block));
}

static void outer(unsigned char *p, int type, unsigned session, unsigned seq)
{
    memcpy(p, "T3MP", 4);
    p[4] = T3MP_VERSION, p[5] = (unsigned char)type, p[6] = p[7] = 0;
    put32(p + 8, session);
    put32(p + 12, seq);
}

const char SERVER_PASSWORD[] = "The server wants a password.";

static const char *refusal(unsigned reason)
{
    switch (reason) {
    case 2: return "The server is full.";
    case 3: return SERVER_PASSWORD;
    case 5: return "This console is banned there.";
    default: return "The server refused this console.";
    }
}

const char *server_ticket(struct server *s, struct ticket *t, progress_fn progress)
{
    static unsigned char packet[1500], plain[1500];
    static struct noise hs;
    struct addrinfo hints = {0}, *res = NULL;
    struct sockaddr_in to, from;
    socklen_t from_n;
    unsigned char e[32], id[4], hello[HELLO_BYTES + 64], none[1], send_key[32], receive_key[32];
    unsigned char sent[HANDSHAKE_PAD + 128];
    char port[8], fp[33];
    unsigned session, seq;
    size_t sent_n, pw;
    DWORD at;
    const char *err = NULL;
    int sock = -1, tries, got, phase = 1, dirty = 0;

    if (net_up())
        return "The network did not start.";
    if (progress)
        progress("Finding the server", 0, 0);
    snprintf(port, sizeof(port), "%u", s->port);
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    /* a DHCP lease may still be on its way */
    for (tries = 0; getaddrinfo(s->host, port, &hints, &res) != 0 && tries < 50; tries++) {
        if (progress && progress("Finding the server", 0, 0))
            return "Cancelled.";
        Sleep(200);
    }
    if (!res)
        return "The server's name was not found.";
    memcpy(&to, res->ai_addr, sizeof(to));
    freeaddrinfo(res);
    t->addr = ntohl(to.sin_addr.s_addr);
    if ((sock = socket(AF_INET, SOCK_DGRAM, 0)) < 0)
        return "No socket for the session.";
    if (!s->has_client_key) {
        random_bytes(s->client_key, 32, sock);
        s->has_client_key = dirty = 1;
    }
    random_bytes(e, sizeof(e), sock);
    random_bytes(id, sizeof(id), sock);
    session = get32(id) | 1;
    noise_start(&hs, prologue, sizeof(prologue) - 1, s->client_key, e);
    crypto_wipe(e, sizeof(e));
    memset(sent, 0, sizeof(sent));
    outer(sent, HANDSHAKE1, session, 0);
    noise_write1(&hs, sent + OUTER, NULL, 0);
    sent_n = HANDSHAKE_PAD;
    if (progress)
        progress("Asking the server for its build", 0, 0);
    for (tries = 0, at = 0; phase && !err;) {
        if (!at || KeTickCount - at >= RETRY_MS) {
            if (tries++ >= TRIES) {
                err = phase == 1 ? "The server did not answer." : "The server stopped answering.";
                break;
            }
            sendto(sock, sent, sent_n, 0, (struct sockaddr *)&to, sizeof(to));
            at = KeTickCount;
        }
        if (progress && progress("Asking the server for its build", 0, 0)) {
            err = "Cancelled.";
            break;
        }
        {
            struct timeval wait = {0, 50000};
            fd_set readable;

            FD_ZERO(&readable);
            FD_SET(sock, &readable);
            if (select(sock + 1, &readable, NULL, NULL, &wait) <= 0)
                continue;
        }
        stir();
        from_n = sizeof(from);
        got = recvfrom(sock, packet, sizeof(packet), 0, (struct sockaddr *)&from, &from_n);
        if (got < OUTER || memcmp(packet, "T3MP", 4) || packet[4] != T3MP_VERSION
            || get32(packet + 8) != session || from.sin_addr.s_addr != to.sin_addr.s_addr)
            continue;
        if (phase == 1 && packet[5] == HANDSHAKE2 && got == OUTER + (int)NOISE_MSG2) {
            if (noise_read2(&hs, packet + OUTER, NOISE_MSG2, none) != 0)
                continue;
            server_fingerprint(hs.rs, fp);
            if (s->has_server_key && crypto_verify32(hs.rs, s->server_key)) {
                mgr_log("server %s: key %s is not the one pinned (%s)\n", s->name, fp,
                        s->fingerprint);
                err = "The server's key has changed since this console first met it.";
                break;
            }
            if (!s->has_server_key) {
                memcpy(s->server_key, hs.rs, 32);
                memcpy(s->fingerprint, fp, sizeof(fp));
                s->has_server_key = dirty = 1;
                mgr_log("server %s: pinned key %s\n", s->name, fp);
            }
            memset(hello, 0, sizeof(hello));
            put32(hello + 14, MANAGER_FLAG);
            pw = strlen(s->password);
            memcpy(hello + HELLO_BYTES, s->password, pw);
            outer(sent, HANDSHAKE3, session, 0);
            noise_write3(&hs, sent + OUTER, hello, HELLO_BYTES + (unsigned)pw);
            sent_n = OUTER + NOISE_MSG3 + HELLO_BYTES + pw;
            noise_split(&hs, send_key, receive_key);
            phase = 3, tries = 0, at = 0;
        } else if (phase == 3 && packet[5] == SEALED && got >= OUTER + INNER + (int)NOISE_TAG) {
            seq = get32(packet + 12);
            if (noise_open(receive_key, seq, packet, OUTER, packet + OUTER, (unsigned)got - OUTER,
                           plain))
                continue;
            got -= OUTER + NOISE_TAG;
            if (plain[0] == REFUSE && got >= INNER + 12) {
                err = refusal(get32(plain + INNER + 8));
            } else if (plain[0] == BUILD && got >= INNER + BUILD_BYTES) {
                memcpy(t->sha256, plain + INNER, 32);
                t->size = get32(plain + INNER + 32);
                t->port = plain[INNER + 36] | (unsigned)plain[INNER + 37] << 8;
                memcpy(t->token, plain + INNER + 40, 16);
                phase = 0;
                if (!t->size)
                    err = "The server hands out no build.";
            }
        }
    }
    closesocket(sock);
    crypto_wipe(&hs, sizeof(hs));
    crypto_wipe(send_key, sizeof(send_key));
    crypto_wipe(receive_key, sizeof(receive_key));
    if (dirty && server_save(s))
        mgr_log("server %s: could not write %s\n", s->name, s->file);
    mgr_log("server %s: %s\n", s->name, err ? err : "ticket");
    return err;
}

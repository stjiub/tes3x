/* The manager's side of the TES3X agent: the in-game agent's sealed protocol (tes3xagent.c), with
 * file operations and launch added. NetAgent and the network keys come from E:\TES3X\console.ini. */

#include "mgr.h"

#include <hal/xbox.h>
#include <lwip/netif.h>
#include <lwip/dhcp.h>
#include <lwip/sockets.h>
#include <nxdk/net.h>
#include <nvnetdrv.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#include "../hooks/monocypher.h"
#include "../hooks/tes3xnoise.h"

#define AGENT_PORT 26501
#define AGENT_VERSION 1
#define AGENT_HEADER 16
#define AGENT_MAX 1400
#define AGENT_FINGERPRINT 16
#define RETRY_MS 500
#define RETRIES 5
#define SILENCE_MS 6000
#define ENTROPY_NEEDED 256

enum { HANDSHAKE1 = 1, HANDSHAKE2, HANDSHAKE3, SEALED };
enum { WELCOME, HEARTBEAT, LOG, GOODBYE, REPLY, REQUEST };
enum { OP_CONSOLE = 1, OP_READ, OP_REBOOT, OP_WRITE, OP_LIST, OP_DELETE, OP_RENAME, OP_MKDIR,
       OP_LAUNCH, OP_TIME, OP_SPACE };
enum { ST_OK, ST_BUSY, ST_UNSUPPORTED, ST_FAILED, ST_BAD };
enum { OFF, NET_WAIT, WANT, SENT1, SENT3, CONNECTED, UNTRUSTED };

#define READ_MAX 1024
#define LIST_BUDGET 1200
#define DONE_IDS 64
#define FILE_IDLE_MS 2000
#define WRITE_BUFFER 65536

static const unsigned char prologue[] = "TES3X in-game agent v1";
static const unsigned char hello[8] = {0, 0, 0, 0, 'm', 'g', 'r', '1'};

extern struct netif *g_pnetif;

static int phase, sock = -1, tries;
static unsigned target, target_port, session, outgoing, incoming, has_incoming;
static DWORD sent_at, heard_at, beat_at;
static unsigned char pinned[AGENT_FINGERPRINT], send_key[32], receive_key[32];
static struct noise handshake;
static unsigned char hs_packet[AGENT_HEADER + NOISE_MSG3 + sizeof(hello)];
static unsigned hs_n;
static CRITICAL_SECTION lock;
static char status_text[64] = "off";
static nx_net_parameters_t net;

static struct {
    unsigned mixed, drawn;
    unsigned char pool[64];
} entropy;

/* Ids of requests that change something, so a retry is answered, not run again. */
static struct {
    unsigned next, id[DONE_IDS];
    unsigned char status[DONE_IDS];
} done;

static struct {
    HANDLE h;
    char path[PATH_MAX_MGR];
    int writing;
    DWORD used;
    /* contiguous writes held back: the disk takes about 1 ms for each small write */
    unsigned start, fill;
} file = {INVALID_HANDLE_VALUE};
/* One buffer fills from requests while the writer thread puts the other on disk. */
static unsigned char held[2][WRITE_BUFFER];
static int filling;
static struct {
    HANDLE h;
    unsigned start, fill;
    const unsigned char *data;
} job;
static HANDLE job_ready, writer_idle;
/* a held write that later failed; the next request that changes something reports it */
static volatile int write_failed;
static volatile unsigned failed_at;

/* Where a session's time went, in CPU cycles; logged when it ends. */
static struct {
    unsigned long long open, op, seal, send;
    unsigned requests, idle;
} cost;

static char launch_path[PATH_MAX_MGR];
static volatile DWORD launch_at;

static unsigned get32(const unsigned char *p)
{
    return p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}

static void put32(unsigned char *p, unsigned v)
{
    p[0] = (unsigned char)v, p[1] = (unsigned char)(v >> 8);
    p[2] = (unsigned char)(v >> 16), p[3] = (unsigned char)(v >> 24);
}

static unsigned long long cycles(void)
{
    unsigned lo, hi;

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return (unsigned long long)hi << 32 | lo;
}

static void cost_log(void)
{
    if (cost.requests)
        mgr_log("agent: %u requests; us in open %u, ops %u, seal %u, send %u; %u idle sleeps\n",
                cost.requests, (unsigned)(cost.open / 733), (unsigned)(cost.op / 733),
                (unsigned)(cost.seal / 733), (unsigned)(cost.send / 733), cost.idle);
    memset(&cost, 0, sizeof(cost));
}

static void entropy_add(void)
{
    crypto_blake2b_ctx ctx;
    unsigned lo, hi;
    unsigned char sample[8];

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    put32(sample, lo);
    put32(sample + 4, KeTickCount);
    crypto_blake2b_init(&ctx, sizeof(entropy.pool));
    crypto_blake2b_update(&ctx, entropy.pool, sizeof(entropy.pool));
    crypto_blake2b_update(&ctx, sample, sizeof(sample));
    crypto_blake2b_final(&ctx, entropy.pool);
    entropy.mixed++;
}

static int random_bytes(unsigned char *out, unsigned n)
{
    unsigned char block[64], label[8] = {0, 0, 0, 0, 'o', 'u', 't', 0};

    entropy_add();
    if (entropy.mixed < ENTROPY_NEEDED || n > sizeof(block))
        return 0;
    put32(label, ++entropy.drawn);
    crypto_blake2b_keyed(block, sizeof(block), entropy.pool, sizeof(entropy.pool), label, 8);
    memcpy(out, block, n);
    memcpy(label + 4, "next", 4);
    crypto_blake2b_keyed(entropy.pool, sizeof(entropy.pool), entropy.pool, sizeof(entropy.pool),
                         label, 8);
    crypto_wipe(block, sizeof(block));
    return 1;
}

static void header(unsigned char *p, int kind, unsigned body, unsigned sequence)
{
    memcpy(p, "T3AG", 4);
    p[4] = AGENT_VERSION, p[5] = (unsigned char)kind;
    p[6] = (unsigned char)body, p[7] = (unsigned char)(body >> 8);
    put32(p + 8, session);
    put32(p + 12, sequence);
}

static void transmit(const unsigned char *p, unsigned n)
{
    struct sockaddr_in to;

    memset(&to, 0, sizeof(to));
    to.sin_family = AF_INET;
    to.sin_port = htons((unsigned short)target_port);
    to.sin_addr.s_addr = htonl(target);
    sendto(sock, p, n, 0, (struct sockaddr *)&to, sizeof(to));
}

static void sealed_send(int kind, const unsigned char *body, unsigned n)
{
    static unsigned char packet[AGENT_MAX], plain[AGENT_MAX];
    unsigned sequence;
    unsigned long long t;

    if (phase != CONNECTED || AGENT_HEADER + n + 1 + NOISE_TAG > AGENT_MAX)
        return;
    sequence = outgoing++;
    plain[0] = (unsigned char)kind;
    memcpy(plain + 1, body, n);
    header(packet, SEALED, n + 1 + NOISE_TAG, sequence);
    t = cycles();
    noise_seal(send_key, sequence, packet, AGENT_HEADER, plain, n + 1, packet + AGENT_HEADER);
    cost.seal += cycles() - t;
    t = cycles();
    transmit(packet, AGENT_HEADER + n + 1 + NOISE_TAG);
    cost.send += cycles() - t;
}

static void reply(unsigned id, int status, const unsigned char *payload, unsigned n)
{
    static unsigned char body[AGENT_MAX];

    put32(body, id);
    body[4] = (unsigned char)status;
    if (n)
        memcpy(body + 5, payload, n);
    sealed_send(REPLY, body, 5 + n);
}

static const char *address(const char *p, unsigned *out)
{
    unsigned value = 0, part, i;

    for (i = 0; i < 4; i++) {
        if (*p < '0' || *p > '9')
            return NULL;
        for (part = 0; *p >= '0' && *p <= '9'; p++)
            if ((part = part * 10 + (unsigned)(*p - '0')) > 255)
                return NULL;
        value = value << 8 | part;
        if (i != 3 && *p++ != '.')
            return NULL;
    }
    *out = value;
    return p;
}

static int hex_byte(const char *p)
{
    int v = 0, i, c;

    for (i = 0; i < 2; i++) {
        c = p[i];
        if (c >= '0' && c <= '9')
            v = v * 16 + c - '0';
        else if ((c | 0x20) >= 'a' && (c | 0x20) <= 'f')
            v = v * 16 + (c | 0x20) - 'a' + 10;
        else
            return -1;
    }
    return v;
}

/* NetAgent=IP[:PORT]#FINGERPRINT, as the payload reads it. */
static int parse_agent(const char *spec)
{
    const char *p = address(spec, &target);
    int i, b;

    if (!p)
        return 0;
    target_port = AGENT_PORT;
    if (*p == ':')
        for (target_port = 0, p++; *p >= '0' && *p <= '9'; p++)
            if ((target_port = target_port * 10 + (unsigned)(*p - '0')) > 65535)
                return 0;
    if (!target_port || *p++ != '#')
        return 0;
    for (i = 0; i < AGENT_FINGERPRINT; i++, p += 2) {
        if ((b = hex_byte(p)) < 0)
            return 0;
        pinned[i] = (unsigned char)b;
    }
    return *p == 0;
}

/* NetAddress: dhcp, or A.B.C.D[/BITS]; absent, the dashboard's network settings. */
static int parse_network(const char *text)
{
    char value[32];
    const char *p;
    unsigned ip, bits = 24, gw = 0, dns;

    memset(&net, 0, sizeof(net));
    net.ipv4_mode = NX_NET_AUTO;
    net.ipv6_mode = NX_NET_AUTO;
    if (!ini_get(text, "NetAddress", value, sizeof(value)))
        return 1;
    if (!name_cmp(value, "dhcp")) {
        net.ipv4_mode = NX_NET_DHCP;
        return 1;
    }
    if (!(p = address(value, &ip)))
        return 0;
    if (*p == '/')
        bits = (unsigned)atoi(p + 1);
    if (ini_get(text, "NetGateway", value, sizeof(value)) && !address(value, &gw))
        return 0;
    /* NetDns as the game reads it: the gateway when absent */
    dns = gw;
    if (ini_get(text, "NetDns", value, sizeof(value)) && !address(value, &dns))
        return 0;
    net.ipv4_dns1 = htonl(dns);
    /* nxdk wants each address with its first octet in the low byte */
    net.ipv4_mode = NX_NET_STATIC;
    net.ipv4_ip = htonl(ip);
    net.ipv4_gateway = htonl(gw);
    net.ipv4_netmask = htonl(bits ? 0xFFFFFFFFu << (32 - bits) : 0);
    return 1;
}

static volatile LONG net_state; /* 0 down, 1 starting, 2 started, 3 stopped */
static int net_result, net_parsed;

int net_up(void)
{
    unsigned char *data = NULL;
    size_t n;

    if (InterlockedCompareExchange(&net_state, 1, 0) == 0) {
        if (!net_parsed && (read_file(CONSOLE_INI, &data, &n) || !parse_network((char *)data))) {
            if (data)
                mgr_log("network: bad NetAddress or NetGateway; using the dashboard's\n");
            parse_network("");
        }
        free(data);
        net_result = nxNetInit(&net);
        mgr_log("network: init %d\n", net_result);
        net_state = 2;
    }
    while (net_state == 1)
        Sleep(10);
    if (net_state == 3)
        return -1;
    /* -2 is DHCP still waiting; its lease may come later */
    return net_result == 0 || net_result == -2 ? 0 : -1;
}

void net_down(void)
{
    /* nxNetShutdown is a stub; the NIC would keep writing its rings into the next title */
    while (net_state == 1)
        Sleep(10);
    if (net_state == 2 && (net_result == 0 || net_result == -2))
        nvnetdrv_stop();
    net_state = 3;
}

static DWORD WINAPI agent_thread(LPVOID unused);
static DWORD WINAPI writer_thread(LPVOID unused);

void agent_start(void)
{
    unsigned char *data;
    char spec[96];
    size_t n;

    if (read_file(CONSOLE_INI, &data, &n))
        return;
    if (!ini_get((char *)data, "NetAgent", spec, sizeof(spec))) {
        free(data);
        return;
    }
    if (!parse_agent(spec) || !parse_network((char *)data)) {
        free(data);
        snprintf(status_text, sizeof(status_text), "bad NetAgent or NetAddress");
        mgr_log("agent: %s\n", status_text);
        return;
    }
    free(data);
    net_parsed = 1;
    phase = NET_WAIT;
    snprintf(status_text, sizeof(status_text), "starting network");
    mgr_log("agent: target %u.%u.%u.%u:%u\n", target >> 24, target >> 16 & 255, target >> 8 & 255,
            target & 255, target_port);
    InitializeCriticalSection(&lock);
    job_ready = CreateEvent(NULL, FALSE, FALSE, NULL);
    writer_idle = CreateEvent(NULL, FALSE, TRUE, NULL);
    CreateThread(NULL, 0, writer_thread, NULL, 0, NULL);
    CreateThread(NULL, 0, agent_thread, NULL, 0, NULL);
}

/* --- requests --- */

static DWORD WINAPI writer_thread(LPVOID unused)
{
    DWORD put;

    (void)unused;
    for (;;) {
        WaitForSingleObject(job_ready, INFINITE);
        if (SetFilePointer(job.h, (LONG)job.start, NULL, FILE_BEGIN) == INVALID_SET_FILE_POINTER
            || !WriteFile(job.h, job.data, job.fill, &put, NULL) || put != job.fill) {
            failed_at = job.start;
            write_failed = 1;
        }
        SetEvent(writer_idle);
    }
}

/* Hands the held writes to the writer thread once it is free. */
static void file_flush(void)
{
    if (!file.fill)
        return;
    WaitForSingleObject(writer_idle, INFINITE);
    job.h = file.h, job.start = file.start, job.fill = file.fill, job.data = held[filling];
    filling ^= 1;
    file.fill = 0;
    SetEvent(job_ready);
}

/* Returns once everything held is on disk; write_failed then says whether it all went. */
static void file_sync(void)
{
    file_flush();
    WaitForSingleObject(writer_idle, INFINITE);
    SetEvent(writer_idle);
    if (write_failed)
        mgr_log("agent: write %s at %u failed\n", file.path, failed_at);
}

static void file_close(void)
{
    file_sync();
    if (file.h != INVALID_HANDLE_VALUE)
        CloseHandle(file.h);
    file.h = INVALID_HANDLE_VALUE;
    file.path[0] = 0;
}

/* A request's path as the manager opens it: C/E/F/G/X/Y/Z, or D: for the manager's own folder. */
static int take_path(const unsigned char *p, unsigned n, char *out)
{
    unsigned i;
    char letter;

    if (n < 3 || n >= PATH_MAX_MGR || p[1] != ':' || (p[2] != '\\' && p[2] != '/'))
        return 0;
    letter = (char)(p[0] & ~0x20);
    if (!strchr("CDEFGXYZ", letter))
        return 0;
    for (i = 0; i < n; i++) {
        if (!p[i] || (i > 2 && p[i] == '.' && p[i - 1] == '.'))
            return 0;
        out[i] = p[i] == '/' ? '\\' : (char)p[i];
    }
    out[0] = letter;
    out[n] = 0;
    return 1;
}

static int file_open(const char *path, int writing, int truncate)
{
    if (file.h != INVALID_HANDLE_VALUE && file.writing == writing && !truncate
        && !strcmp(file.path, path))
        return 1;
    file_close();
    file.h = writing ? CreateFileA(path, GENERIC_WRITE, 0, NULL,
                                   truncate ? CREATE_ALWAYS : OPEN_EXISTING,
                                   FILE_ATTRIBUTE_NORMAL, NULL)
                     : CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                                   OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file.h == INVALID_HANDLE_VALUE)
        return 0;
    snprintf(file.path, sizeof(file.path), "%s", path);
    file.writing = writing;
    file.used = KeTickCount;
    return 1;
}

static int done_status(unsigned id)
{
    int i;

    for (i = 0; i < DONE_IDS; i++)
        if (done.id[i] == id)
            return done.status[i];
    return -1;
}

static void finish(unsigned id, int status)
{
    done.status[done.next % DONE_IDS] = (unsigned char)status;
    done.id[done.next++ % DONE_IDS] = id;
    reply(id, status, NULL, 0);
}

static void op_read(unsigned id, const unsigned char *a, unsigned n)
{
    static unsigned char out[8 + READ_MAX];
    char path[PATH_MAX_MGR];
    unsigned offset, want;
    DWORD size, got = 0;

    if (n < 7 || !take_path(a + 6, n - 6, path)) {
        reply(id, ST_BAD, NULL, 0);
        return;
    }
    offset = get32(a);
    want = a[4] | (unsigned)a[5] << 8;
    if (want > READ_MAX)
        want = READ_MAX;
    if (!file_open(path, 0, 0)) {
        reply(id, ST_FAILED, NULL, 0);
        return;
    }
    file.used = KeTickCount;
    size = GetFileSize(file.h, NULL);
    if (offset < size) {
        if (want > size - offset)
            want = size - offset;
        if (SetFilePointer(file.h, (LONG)offset, NULL, FILE_BEGIN) == INVALID_SET_FILE_POINTER
            || !ReadFile(file.h, out + 8, want, &got, NULL)) {
            file_close();
            reply(id, ST_FAILED, NULL, 0);
            return;
        }
    }
    put32(out, size);
    put32(out + 4, offset);
    reply(id, ST_OK, out, 8 + got);
}

/* offset, path length, path, data. Offset 0 creates or truncates the file; no data writes out
 * what is held, so its answer covers every earlier write. */
static void op_write(unsigned id, const unsigned char *a, unsigned n)
{
    char path[PATH_MAX_MGR];
    unsigned offset, path_n;

    if (n < 6 || (path_n = a[4]) + 5 > n || !take_path(a + 5, path_n, path)) {
        reply(id, ST_BAD, NULL, 0);
        return;
    }
    offset = get32(a);
    a += 5 + path_n, n -= 5 + path_n;
    if (!file_open(path, 1, offset == 0)) {
        finish(id, ST_FAILED);
        return;
    }
    file.used = KeTickCount;
    if (file.fill && (offset != file.start + file.fill || file.fill + n > WRITE_BUFFER))
        file_flush();
    if (!file.fill)
        file.start = offset;
    memcpy(held[filling] + file.fill, a, n);
    file.fill += n;
    if (!n)
        file_sync();
    else if (file.fill == WRITE_BUFFER)
        file_flush();
    finish(id, write_failed ? ST_FAILED : ST_OK);
    write_failed = 0;
}

/* start, path. Answers the total and as many entries from `start` as fit: attributes, size,
 * name length, name. */
static void op_list(unsigned id, const unsigned char *a, unsigned n)
{
    static unsigned char out[LIST_BUDGET + 64];
    char path[PATH_MAX_MGR], pattern[PATH_MAX_MGR + 2];
    WIN32_FIND_DATAA fd;
    HANDLE h;
    unsigned start, index = 0, count = 0, at = 6, k;

    if (n < 5 || !take_path(a + 4, n - 4, path)) {
        reply(id, ST_BAD, NULL, 0);
        return;
    }
    start = get32(a);
    snprintf(pattern, sizeof(pattern), "%s%s*", path, path[strlen(path) - 1] == '\\' ? "" : "\\");
    if ((h = FindFirstFileA(pattern, &fd)) == INVALID_HANDLE_VALUE) {
        reply(id, GetFileAttributesA(path) == INVALID_FILE_ATTRIBUTES ? ST_FAILED : ST_OK,
              (const unsigned char *)"\0\0\0\0\0\0", 6);
        return;
    }
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, ".."))
            continue;
        k = (unsigned)strlen(fd.cFileName);
        if (index++ < start || at + 6 + k > LIST_BUDGET)
            continue;
        out[at] = (unsigned char)(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY ? 1 : 0);
        put32(out + at + 1, fd.nFileSizeLow);
        out[at + 5] = (unsigned char)k;
        memcpy(out + at + 6, fd.cFileName, k);
        at += 6 + k;
        count++;
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    put32(out, index);
    out[4] = (unsigned char)count, out[5] = (unsigned char)(count >> 8);
    reply(id, ST_OK, out, at);
}

static void op_path(unsigned id, int op, const unsigned char *a, unsigned n)
{
    char path[PATH_MAX_MGR], to[PATH_MAX_MGR];
    unsigned from_n;
    int ok;
    DWORD attrs;

    file_close();
    if (op == OP_RENAME) {
        if (n < 2 || (from_n = a[0]) + 1 >= n || !take_path(a + 1, from_n, path)
            || !take_path(a + 1 + from_n, n - 1 - from_n, to)) {
            reply(id, ST_BAD, NULL, 0);
            return;
        }
        ok = MoveFileA(path, to);
    } else if (!take_path(a, n, path)) {
        reply(id, ST_BAD, NULL, 0);
        return;
    } else if (op == OP_MKDIR) {
        attrs = GetFileAttributesA(path);
        ok = (attrs != INVALID_FILE_ATTRIBUTES && attrs & FILE_ATTRIBUTE_DIRECTORY)
             || CreateDirectoryA(path, NULL);
    } else {
        attrs = GetFileAttributesA(path);
        ok = attrs != INVALID_FILE_ATTRIBUTES
             && (attrs & FILE_ATTRIBUTE_DIRECTORY ? RemoveDirectoryA(path) : DeleteFileA(path));
    }
    mgr_log("agent: op %d %s%s%s: %s\n", op, path, op == OP_RENAME ? " -> " : "",
            op == OP_RENAME ? to : "", ok ? "ok" : "failed");
    finish(id, ok ? ST_OK : ST_FAILED);
}

/* last-write time (FILETIME, UTC), path: plugin load order follows it */
static void op_time(unsigned id, const unsigned char *a, unsigned n)
{
    char path[PATH_MAX_MGR];
    FILETIME t;
    HANDLE h;
    int ok;

    if (n < 9 || !take_path(a + 8, n - 8, path)) {
        reply(id, ST_BAD, NULL, 0);
        return;
    }
    file_close();
    t.dwLowDateTime = get32(a);
    t.dwHighDateTime = get32(a + 4);
    h = CreateFileA(path, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    ok = h != INVALID_HANDLE_VALUE && SetFileTime(h, NULL, NULL, &t);
    if (h != INVALID_HANDLE_VALUE)
        CloseHandle(h);
    finish(id, ok ? ST_OK : ST_FAILED);
}

/* a folder's drive: free and total bytes */
static void op_space(unsigned id, const unsigned char *a, unsigned n)
{
    unsigned char out[16];
    char path[PATH_MAX_MGR];
    ULARGE_INTEGER free_bytes, total;

    if (!take_path(a, n, path)) {
        reply(id, ST_BAD, NULL, 0);
        return;
    }
    path[3] = 0;
    if (!GetDiskFreeSpaceExA(path, &free_bytes, &total, NULL)) {
        reply(id, ST_FAILED, NULL, 0);
        return;
    }
    put32(out, free_bytes.LowPart);
    put32(out + 4, free_bytes.HighPart);
    put32(out + 8, total.LowPart);
    put32(out + 12, total.HighPart);
    reply(id, ST_OK, out, sizeof(out));
}

static void op_launch(unsigned id, const unsigned char *a, unsigned n)
{
    char path[PATH_MAX_MGR];
    unsigned title_id;

    if (!take_path(a, n, path) || path[0] == 'D') {
        reply(id, ST_BAD, NULL, 0);
        return;
    }
    if (xbe_title_id(path, &title_id)) {
        finish(id, ST_FAILED);
        return;
    }
    file_close();
    finish(id, ST_OK);
    /* after the reply and its retries have had a moment to leave */
    snprintf(launch_path, sizeof(launch_path), "%s", path);
    launch_at = KeTickCount + 300;
}

static void request(const unsigned char *body, unsigned n)
{
    unsigned id = get32(body);
    int op = body[4], status;

    body += 5, n -= 5;
    if (op == OP_READ) {
        op_read(id, body, n);
        return;
    }
    if (op == OP_LIST) {
        op_list(id, body, n);
        return;
    }
    if (op == OP_SPACE) {
        op_space(id, body, n);
        return;
    }
    if ((status = done_status(id)) >= 0) {
        reply(id, status, NULL, 0);
        return;
    }
    if (op != OP_WRITE && (file_sync(), write_failed)) {
        write_failed = 0;
        finish(id, ST_FAILED);
        return;
    }
    switch (op) {
    case OP_WRITE:
        op_write(id, body, n);
        break;
    case OP_DELETE:
    case OP_RENAME:
    case OP_MKDIR:
        op_path(id, op, body, n);
        break;
    case OP_LAUNCH:
        op_launch(id, body, n);
        break;
    case OP_TIME:
        op_time(id, body, n);
        break;
    case OP_REBOOT:
        finish(id, ST_OK);
        mgr_log("agent: reboot\n");
        Sleep(200);
        HalReturnToFirmware(HalRebootRoutine);
        break;
    case OP_CONSOLE:
        reply(id, ST_UNSUPPORTED, NULL, 0);
        break;
    default:
        reply(id, ST_BAD, NULL, 0);
    }
}

/* --- session --- */

static void handshake_start(void)
{
    unsigned char secrets[64], id[4];

    if (!random_bytes(secrets, sizeof(secrets)) || !random_bytes(id, sizeof(id)))
        return;
    session = get32(id) | 1;
    noise_start(&handshake, prologue, sizeof(prologue) - 1, secrets, secrets + 32);
    crypto_wipe(secrets, sizeof(secrets));
    header(hs_packet, HANDSHAKE1, NOISE_MSG1, 0);
    noise_write1(&handshake, hs_packet + AGENT_HEADER, NULL, 0);
    hs_n = AGENT_HEADER + NOISE_MSG1;
    phase = SENT1, tries = 0, sent_at = KeTickCount;
    transmit(hs_packet, hs_n);
}

static void handshake_finish(const unsigned char *msg2)
{
    unsigned char fingerprint[AGENT_FINGERPRINT], none[1];

    if (noise_read2(&handshake, msg2, NOISE_MSG2, none) != 0) {
        phase = WANT;
        return;
    }
    crypto_blake2b(fingerprint, sizeof(fingerprint), handshake.rs, 32);
    if (crypto_verify16(fingerprint, pinned)) {
        crypto_wipe(&handshake, sizeof(handshake));
        phase = UNTRUSTED;
        snprintf(status_text, sizeof(status_text), "PC key does not match NetAgent");
        mgr_log("agent: untrusted\n");
        return;
    }
    noise_write3(&handshake, hs_packet + AGENT_HEADER, hello, sizeof(hello));
    noise_split(&handshake, send_key, receive_key);
    header(hs_packet, HANDSHAKE3, NOISE_MSG3 + sizeof(hello), 0);
    hs_n = AGENT_HEADER + NOISE_MSG3 + sizeof(hello);
    phase = SENT3, tries = 0, outgoing = 0, has_incoming = 0, sent_at = KeTickCount;
    transmit(hs_packet, hs_n);
}

static void receive(const unsigned char *p, unsigned n)
{
    static unsigned char plain[AGENT_MAX];
    unsigned size, sequence, length;
    unsigned long long t;
    int opened;

    if (n < AGENT_HEADER || memcmp(p, "T3AG", 4) || p[4] != AGENT_VERSION
        || (size = p[6] | (unsigned)p[7] << 8) != n - AGENT_HEADER || get32(p + 8) != session)
        return;
    sequence = get32(p + 12);
    if (p[5] == HANDSHAKE2 && phase == SENT1 && !sequence && size == NOISE_MSG2) {
        handshake_finish(p + AGENT_HEADER);
        return;
    }
    if (p[5] != SEALED || phase < SENT3 || size < NOISE_TAG + 1
        || (has_incoming && (int)(sequence - incoming) <= 0))
        return;
    t = cycles();
    opened = noise_open(receive_key, sequence, p, AGENT_HEADER, p + AGENT_HEADER, size, plain);
    cost.open += cycles() - t;
    if (opened)
        return;
    /* A request can overtake the welcome; it is retried, and must not make the welcome look
     * like a replay. */
    if (phase == SENT3 && plain[0] != WELCOME)
        return;
    incoming = sequence, has_incoming = 1, heard_at = KeTickCount;
    length = size - NOISE_TAG - 1;
    if (plain[0] == WELCOME && phase == SENT3) {
        phase = CONNECTED;
        beat_at = 0;
        memset(&done, 0, sizeof(done));
        snprintf(status_text, sizeof(status_text), "connected to the PC");
        mgr_log("agent: connected, session %08X\n", session);
    } else if (plain[0] == REQUEST && phase == CONNECTED && length > 4) {
        t = cycles();
        request(plain + 1, length);
        cost.op += cycles() - t;
        cost.requests++;
    }
}

static unsigned free_kb(void)
{
    MM_STATISTICS st;

    st.Length = sizeof(st);
    return MmQueryStatistics(&st) == 0 ? st.AvailablePages * 4 : 0;
}

static void open_socket(void)
{
    struct sockaddr_in any;
    const ip4_addr_t *ip = netif_ip4_addr(g_pnetif);

    if (!g_pnetif || ip4_addr_isany(ip))
        return;
    if ((sock = socket(AF_INET, SOCK_DGRAM, 0)) < 0)
        return;
    memset(&any, 0, sizeof(any));
    any.sin_family = AF_INET;
    any.sin_port = htons(AGENT_PORT);
    if (bind(sock, (struct sockaddr *)&any, sizeof(any))) {
        closesocket(sock);
        sock = -1;
        return;
    }
    mgr_log("agent: address %s\n", ip4addr_ntoa(ip));
    snprintf(status_text, sizeof(status_text), "%s, pairing", ip4addr_ntoa(ip));
    phase = WANT;
}

static void serve(void)
{
    unsigned char beat[16];
    DWORD now = KeTickCount;

    if (phase == WANT) {
        handshake_start();
    } else if (phase == SENT1 || phase == SENT3) {
        if (now - sent_at >= RETRY_MS) {
            sent_at = now;
            if (++tries >= RETRIES)
                phase = WANT;
            else
                transmit(hs_packet, hs_n);
        }
    } else if (phase == CONNECTED) {
        if (now - heard_at > SILENCE_MS) {
            mgr_log("agent: PC silent, pairing again\n");
            snprintf(status_text, sizeof(status_text), "PC silent, pairing again");
            cost_log();
            phase = WANT;
            file_close();
            return;
        }
        if (!beat_at || now - beat_at >= 1000) {
            put32(beat, now * 1000u);
            put32(beat + 4, 0);
            put32(beat + 8, free_kb());
            put32(beat + 12, 0);
            sealed_send(HEARTBEAT, beat, sizeof(beat));
            beat_at = now;
        }
        if (file.h != INVALID_HANDLE_VALUE && now - file.used > FILE_IDLE_MS)
            file_close();
    }
}

/* Sleep(1) here cost each request about 2 ms; select wakes on the packet. The timeout keeps
 * heartbeats and retries going. */
static void wait_readable(void)
{
    struct timeval wait = {0, 20000};
    fd_set readable;

    FD_ZERO(&readable);
    FD_SET(sock, &readable);
    select(sock + 1, &readable, NULL, NULL, &wait);
}

/* Requests are answered as they arrive: a reply waits on nothing the UI does. */
static DWORD WINAPI agent_thread(LPVOID unused)
{
    static unsigned char packet[AGENT_MAX + 1];
    struct sockaddr_in from;
    socklen_t from_n;
    int got, heard;

    (void)unused;
    if (net_up())
        return 0;
    while (sock < 0) {
        entropy_add();
        open_socket();
        if (sock < 0)
            Sleep(100);
    }
    for (;;) {
        for (heard = 0;; heard = 1) {
            from_n = sizeof(from);
            got = recvfrom(sock, packet, sizeof(packet), MSG_DONTWAIT, (struct sockaddr *)&from,
                           &from_n);
            if (got <= 0)
                break;
            EnterCriticalSection(&lock);
            entropy_add();
            if (ntohl(from.sin_addr.s_addr) == target && ntohs(from.sin_port) == target_port
                && got <= AGENT_MAX && phase != OFF)
                receive(packet, (unsigned)got);
            LeaveCriticalSection(&lock);
        }
        EnterCriticalSection(&lock);
        entropy_add();
        if (phase == OFF || phase == UNTRUSTED) {
            LeaveCriticalSection(&lock);
            return 0;
        }
        serve();
        LeaveCriticalSection(&lock);
        if (!heard) {
            cost.idle++;
            wait_readable();
        }
    }
}

void agent_poll(void)
{
    if (launch_at && (int)(KeTickCount - launch_at) >= 0) {
        launch_at = 0;
        mgr_launch_xbe(launch_path);
    }
}

const char *agent_status(void)
{
    return status_text;
}

void agent_goodbye(void)
{
    if (phase == OFF)
        return;
    EnterCriticalSection(&lock);
    file_close();
    sealed_send(GOODBYE, NULL, 0);
    cost_log();
    phase = OFF;
    LeaveCriticalSection(&lock);
}

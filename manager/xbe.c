/* XBEs: the title ID a launch needs, and rebuilding a build's XBE from the player's retail image
 * and the delta its manifest names. */

#include "mgr.h"
#include "sha256.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#include "zstd/zstd.h"

#define XBE_BASE 0x104
#define XBE_CERT 0x118
#define CERT_TITLE_ID 8

/* The scene's edits, as tes3x_patch.scene_form makes them: deltas are made against the image
 * with these done, so retail and scene copies rebuild alike. */
#define CERT_ALLOWED_MEDIA 0x220
#define CERT_GAME_REGION 0x224
#define MEDIA_ANY 0xC00001FFu
#define REGION_ANY 0x00000007u

static const char *const asset_paths[] = {
    "Data Files\\Fonts",
    "Data Files\\",
    "Data Files\\%s",
    "Data Files\\Morrowind.bsa",
    "Data Files\\Meshes",
    "Data Files\\morrowind.esm.map",
};
/* mov dword ptr [esp+0xC], "Z:\" */
static const unsigned char inline_drive[] = {0xC7, 0x44, 0x24, 0x0C};

int xbe_title_id(const char *path, unsigned *title_id)
{
    unsigned char head[0x1000];
    unsigned base, cert;
    size_t n;
    FILE *f = fopen(path, "rb");

    if (!f)
        return -1;
    n = fread(head, 1, sizeof(head), f);
    fclose(f);
    if (n < 0x178 || memcmp(head, "XBEH", 4))
        return -1;
    memcpy(&base, head + XBE_BASE, 4);
    memcpy(&cert, head + XBE_CERT, 4);
    cert -= base;
    if (cert + CERT_TITLE_ID + 4 > n)
        return -1;
    memcpy(title_id, head + cert + CERT_TITLE_ID, 4);
    return 0;
}

static int path_at(const unsigned char *d, size_t n, size_t i, const char *tail)
{
    size_t k = strlen(tail);

    return i + 3 + k < n && isalpha(d[i]) && d[i + 1] == ':' && d[i + 2] == '\\'
           && !memcmp(d + i + 3, tail, k) && d[i + 3 + k] == 0;
}

/* Every asset path's drive set to D:, or nothing changed if one of them is missing. */
static void drive_letters(unsigned char *d, size_t n)
{
    size_t i, k, store = 0;
    int stores = 0, found;

    for (k = 0; k < sizeof(asset_paths) / sizeof(*asset_paths); k++) {
        for (found = 0, i = 0; i < n && !found; i++)
            found = path_at(d, n, i, asset_paths[k]);
        if (!found)
            return;
    }
    for (i = 0; i + 8 <= n; i++)
        if (!memcmp(d + i, inline_drive, 4) && isalpha(d[i + 4]) && d[i + 5] == ':'
            && d[i + 6] == '\\' && d[i + 7] == 0)
            stores++, store = i + 4;
    if (stores != 1)
        return;
    for (k = 0; k < sizeof(asset_paths) / sizeof(*asset_paths); k++)
        for (i = 0; i < n; i++)
            if (path_at(d, n, i, asset_paths[k]))
                d[i] = 'D';
    d[store] = 'D';
}

static void scene_form(unsigned char *d, size_t n)
{
    unsigned media = MEDIA_ANY, region = REGION_ANY;

    if (n < CERT_GAME_REGION + 4)
        return;
    memcpy(d + CERT_ALLOWED_MEDIA, &media, 4);
    memcpy(d + CERT_GAME_REGION, &region, 4);
    drive_letters(d, n);
}

static void digest_hex(const unsigned char *d, size_t n, char hex[65])
{
    struct sha256 s;
    unsigned char digest[32];

    sha256_init(&s);
    sha256_update(&s, d, n);
    sha256_final(&s, digest);
    sha256_hex(digest, hex);
}

/* tes3x_patch.retail_digest of each supported image: GOTY USA morrowind.xbe */
static const char *const retail_digests[] = {
    "fd4cf820663f004437ee7ec1077889713c55c7ca6b2db8badfabdb352216dfed",
};

int xbe_known_retail(const char *path)
{
    unsigned char *d;
    char hex[65];
    size_t n, i;

    if (read_file(path, &d, &n))
        return 0;
    if (n < 4 || memcmp(d, "XBEH", 4)) {
        free(d);
        return 0;
    }
    scene_form(d, n);
    digest_hex(d, n, hex);
    free(d);
    for (i = 0; i < sizeof(retail_digests) / sizeof(*retail_digests); i++)
        if (!strcmp(hex, retail_digests[i]))
            return 1;
    mgr_log("%s: not a known retail image (%.16s)\n", path, hex);
    return 0;
}

/* The new XBE goes in under a staging name and replaces the old one, kept as .prev, only once
 * complete. */
const char *replace_file(const char *target, const unsigned char *d, size_t n)
{
    char staged[PATH_MAX_MGR], prev[PATH_MAX_MGR];

    snprintf(staged, sizeof(staged), "%s.new", target);
    snprintf(prev, sizeof(prev), "%s.prev", target);
    if (write_flushed(staged, d, n)) {
        DeleteFileA(staged);
        return "could not write the new XBE";
    }
    DeleteFileA(prev);
    if (GetFileAttributesA(target) != INVALID_FILE_ATTRIBUTES && !MoveFileA(target, prev))
        return "could not set the old XBE aside";
    if (!MoveFileA(staged, target)) {
        MoveFileA(prev, target);
        return "could not rename the new XBE in";
    }
    flush_path(target);
    return NULL;
}

static int find_recipe(const struct json *j, const char *xbe)
{
    char path[64];
    int list = json_get(j, 0, "xbe"), i;

    for (i = json_child(j, list); i >= 0; i = json_sibling(j, list, i))
        if (json_string(j, json_get(j, i, "path"), path, sizeof(path)) >= 0
            && !name_cmp(path, xbe))
            return i;
    return -1;
}

const char *rebuild_xbe(const struct build *b, const char *xbe, const char *delta,
                        progress_fn progress)
{
    static char reason[160];
    struct json j;
    char *text, want_retail[65], want_delta[65], want_out[65], hex[65], target[PATH_MAX_MGR];
    char base[PATH_MAX_MGR], retail[PATH_MAX_MGR];
    unsigned char *ref = NULL, *patch = NULL, *out = NULL;
    size_t ref_n, patch_n, out_n, got;
    unsigned long long size;
    ZSTD_DCtx *dctx = NULL;
    DWORD start = KeTickCount;
    const char *err = NULL;
    int recipe;

    /* the reference whatever the build's layout: a full build's XBE is a delta too */
    if (!console_get("OverlayBase", base, sizeof(base)))
        return "no retail base set";
    join_path(retail, sizeof(retail), base, "morrowind.xbe");
    if (manifest_load(b, &text, &j))
        return "manifest unreadable";
    if ((recipe = find_recipe(&j, xbe)) < 0) {
        manifest_free(text, &j);
        return "the manifest has no recipe for that XBE";
    }
    json_string(&j, json_get(&j, recipe, "retail_digest"), want_retail, sizeof(want_retail));
    json_string(&j, json_get(&j, recipe, "sha256"), want_out, sizeof(want_out));
    if (json_string(&j, json_get(&j, json_get(&j, recipe, "delta"), "sha256"), want_delta,
                    sizeof(want_delta)) < 0)
        err = "the manifest has no delta for that XBE";
    manifest_free(text, &j);
    if (err)
        return err;

    if (progress)
        progress("retail XBE", 0, 3);
    if (read_file(retail, &ref, &ref_n))
        return "the retail base has no morrowind.xbe";
    if (ref_n < 4 || memcmp(ref, "XBEH", 4)) {
        err = "the retail file is not an XBE";
        goto done;
    }
    scene_form(ref, ref_n);
    digest_hex(ref, ref_n, hex);
    mgr_log("rebuild %s\\%s: retail digest %s\n", b->path, xbe, hex);
    if (strcmp(hex, want_retail)) {
        snprintf(reason, sizeof(reason), "unknown retail image %.16s", hex);
        err = reason;
        goto done;
    }
    if (progress)
        progress("delta", 1, 3);
    if (read_file(delta, &patch, &patch_n)) {
        err = "cannot read the delta";
        goto done;
    }
    digest_hex(patch, patch_n, hex);
    if (strcmp(hex, want_delta)) {
        err = "the delta is not the one the manifest names";
        goto done;
    }
    size = ZSTD_getFrameContentSize(patch, patch_n);
    if (size == ZSTD_CONTENTSIZE_UNKNOWN || size == ZSTD_CONTENTSIZE_ERROR || size > 64u << 20) {
        err = "the delta does not give its output size";
        goto done;
    }
    out_n = (size_t)size;
    if (!(out = malloc(out_n ? out_n : 1)) || !(dctx = ZSTD_createDCtx())) {
        err = "not enough memory to rebuild the XBE";
        goto done;
    }
    if (progress)
        progress("decoding", 2, 3);
    ZSTD_DCtx_setParameter(dctx, ZSTD_d_windowLogMax, 30);
    if (ZSTD_isError(ZSTD_DCtx_refPrefix(dctx, ref, ref_n))) {
        err = "the decoder refused the retail XBE";
        goto done;
    }
    got = ZSTD_decompressDCtx(dctx, out, out_n, patch, patch_n);
    if (ZSTD_isError(got) || got != out_n) {
        mgr_log("rebuild: zstd error %s\n", ZSTD_isError(got) ? "yes" : "short");
        err = "the delta does not decode against this retail XBE";
        goto done;
    }
    digest_hex(out, out_n, hex);
    if (strcmp(hex, want_out)) {
        err = "the rebuilt XBE does not match the manifest";
        goto done;
    }
    join_path(target, sizeof(target), b->path, xbe);
    err = replace_file(target, out, out_n);
    mgr_log("rebuild %s: %s, %lu ms\n", target, err ? err : "done", KeTickCount - start);
done:
    ZSTD_freeDCtx(dctx);
    free(out);
    free(patch);
    free(ref);
    return err;
}

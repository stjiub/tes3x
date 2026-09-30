/* Noise_XX_25519_ChaChaPoly_BLAKE2b for the console, which always initiates. See tes3xnoise.h. */

#include "monocypher.h"
#include "tes3xnoise.h"

typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;

static const char protocol[] = "Noise_XX_25519_ChaChaPoly_BLAKE2b";

void noise_wipe(void *p, unsigned int n)
{
    crypto_wipe(p, n);
}

void noise_public(unsigned char public_key[32], const unsigned char secret[32])
{
    crypto_x25519_public_key(public_key, secret);
}

static void nonce(u8 out[12], u64 counter)
{
    u32 i;

    for (i = 0; i < 4; i++)
        out[i] = 0;
    for (i = 0; i < 8; i++)
        out[4 + i] = (u8)(counter >> (8 * i));
}

void noise_seal(const unsigned char key[32], unsigned long long counter, const unsigned char *ad,
                unsigned int ad_n, const unsigned char *in, unsigned int n, unsigned char *out)
{
    crypto_aead_ctx ctx;
    u8 iv[12];

    nonce(iv, counter);
    crypto_aead_init_ietf(&ctx, key, iv);
    crypto_aead_write(&ctx, out, out + n, ad, ad_n, in, n);
    crypto_wipe(&ctx, sizeof(ctx));
}

int noise_open(const unsigned char key[32], unsigned long long counter, const unsigned char *ad,
               unsigned int ad_n, const unsigned char *in, unsigned int n, unsigned char *out)
{
    crypto_aead_ctx ctx;
    u8 iv[12];
    int bad;

    if (n < NOISE_TAG)
        return -1;
    nonce(iv, counter);
    crypto_aead_init_ietf(&ctx, key, iv);
    bad = crypto_aead_read(&ctx, out, in + n - NOISE_TAG, ad, ad_n, in, n - NOISE_TAG);
    crypto_wipe(&ctx, sizeof(ctx));
    return bad;
}

/* HMAC-BLAKE2b over a then b; keys are at most NOISE_HASH bytes. */
static void hmac(u8 out[NOISE_HASH], const u8 *key, u32 key_n, const u8 *a, u32 a_n,
                 const u8 *b, u32 b_n)
{
    crypto_blake2b_ctx ctx;
    u8 pad[128], inner[NOISE_HASH];
    u32 i;

    for (i = 0; i < sizeof(pad); i++)
        pad[i] = (u8)((i < key_n ? key[i] : 0) ^ 0x36);
    crypto_blake2b_init(&ctx, NOISE_HASH);
    crypto_blake2b_update(&ctx, pad, sizeof(pad));
    crypto_blake2b_update(&ctx, a, a_n);
    crypto_blake2b_update(&ctx, b, b_n);
    crypto_blake2b_final(&ctx, inner);
    for (i = 0; i < sizeof(pad); i++)
        pad[i] ^= 0x36 ^ 0x5C;
    crypto_blake2b_init(&ctx, NOISE_HASH);
    crypto_blake2b_update(&ctx, pad, sizeof(pad));
    crypto_blake2b_update(&ctx, inner, sizeof(inner));
    crypto_blake2b_final(&ctx, out);
    crypto_wipe(pad, sizeof(pad));
    crypto_wipe(inner, sizeof(inner));
}

/* HKDF with the chaining key: two outputs. */
static void hkdf(const u8 ck[NOISE_HASH], const u8 *ikm, u32 ikm_n, u8 out1[NOISE_HASH],
                 u8 out2[NOISE_HASH])
{
    static const u8 one = 1, two = 2;
    u8 temp[NOISE_HASH];

    hmac(temp, ck, NOISE_HASH, ikm, ikm_n, 0, 0);
    hmac(out1, temp, NOISE_HASH, &one, 1, 0, 0);
    hmac(out2, temp, NOISE_HASH, out1, NOISE_HASH, &two, 1);
    crypto_wipe(temp, sizeof(temp));
}

static void mix_hash(struct noise *st, const u8 *data, u32 n)
{
    crypto_blake2b_ctx ctx;

    crypto_blake2b_init(&ctx, NOISE_HASH);
    crypto_blake2b_update(&ctx, st->h, NOISE_HASH);
    crypto_blake2b_update(&ctx, data, n);
    crypto_blake2b_final(&ctx, st->h);
}

static void mix_key(struct noise *st, const u8 *ikm, u32 n)
{
    u8 ck[NOISE_HASH], temp[NOISE_HASH];
    u32 i;

    hkdf(st->ck, ikm, n, ck, temp);
    for (i = 0; i < NOISE_HASH; i++)
        st->ck[i] = ck[i];
    for (i = 0; i < NOISE_KEY; i++)
        st->k[i] = temp[i];
    st->n = 0;
    st->has_k = 1;
    crypto_wipe(ck, sizeof(ck));
    crypto_wipe(temp, sizeof(temp));
}

/* 0 unless the shared secret is all zeros (a low-order public key). */
static int mix_dh(struct noise *st, const u8 secret[NOISE_KEY], const u8 public_key[NOISE_KEY])
{
    u8 shared[NOISE_KEY], any = 0;
    u32 i;

    crypto_x25519(shared, secret, public_key);
    for (i = 0; i < NOISE_KEY; i++)
        any |= shared[i];
    mix_key(st, shared, NOISE_KEY);
    crypto_wipe(shared, sizeof(shared));
    return any ? 0 : -1;
}

static void encrypt_and_hash(struct noise *st, const u8 *in, u32 n, u8 *out)
{
    u32 i;

    if (st->has_k) {
        noise_seal(st->k, st->n++, st->h, NOISE_HASH, in, n, out);
        n += NOISE_TAG;
    } else {
        for (i = 0; i < n; i++)
            out[i] = in[i];
    }
    mix_hash(st, out, n);
}

static int decrypt_and_hash(struct noise *st, const u8 *in, u32 n, u8 *out)
{
    if (!st->has_k || noise_open(st->k, st->n++, st->h, NOISE_HASH, in, n, out))
        return -1;
    mix_hash(st, in, n);
    return 0;
}

void noise_start(struct noise *st, const unsigned char *prologue, unsigned int prologue_n,
                 const unsigned char s_secret[32], const unsigned char e_secret[32])
{
    u32 i;

    crypto_wipe(st, sizeof(*st));
    for (i = 0; protocol[i]; i++)
        st->h[i] = (u8)protocol[i];
    for (i = 0; i < NOISE_HASH; i++)
        st->ck[i] = st->h[i];
    mix_hash(st, prologue, prologue_n);
    for (i = 0; i < NOISE_KEY; i++) {
        st->s_secret[i] = s_secret[i];
        st->e_secret[i] = e_secret[i];
    }
    crypto_x25519_public_key(st->s_public, st->s_secret);
    crypto_x25519_public_key(st->e_public, st->e_secret);
}

void noise_write1(struct noise *st, unsigned char *out, const unsigned char *payload,
                  unsigned int payload_n)
{
    u32 i;

    for (i = 0; i < NOISE_KEY; i++)
        out[i] = st->e_public[i];
    mix_hash(st, st->e_public, NOISE_KEY);
    encrypt_and_hash(st, payload, payload_n, out + NOISE_KEY);
}

int noise_read2(struct noise *st, const unsigned char *in, unsigned int n, unsigned char *payload)
{
    u32 i;

    if (n < NOISE_MSG2)
        return -1;
    for (i = 0; i < NOISE_KEY; i++)
        st->re[i] = in[i];
    mix_hash(st, st->re, NOISE_KEY);
    if (mix_dh(st, st->e_secret, st->re) ||
        decrypt_and_hash(st, in + NOISE_KEY, NOISE_KEY + NOISE_TAG, st->rs) ||
        mix_dh(st, st->e_secret, st->rs) ||
        decrypt_and_hash(st, in + 2 * NOISE_KEY + NOISE_TAG, n - 2 * NOISE_KEY - NOISE_TAG,
                         payload))
        return -1;
    return (int)(n - NOISE_MSG2);
}

void noise_write3(struct noise *st, unsigned char *out, const unsigned char *payload,
                  unsigned int payload_n)
{
    encrypt_and_hash(st, st->s_public, NOISE_KEY, out);
    mix_dh(st, st->s_secret, st->re);
    encrypt_and_hash(st, payload, payload_n, out + NOISE_KEY + NOISE_TAG);
}

void noise_split(struct noise *st, unsigned char send[32], unsigned char receive[32])
{
    u8 k1[NOISE_HASH], k2[NOISE_HASH];
    u32 i;

    hkdf(st->ck, 0, 0, k1, k2);
    for (i = 0; i < NOISE_KEY; i++) {
        send[i] = k1[i];
        receive[i] = k2[i];
    }
    crypto_wipe(k1, sizeof(k1));
    crypto_wipe(k2, sizeof(k2));
    crypto_wipe(st, sizeof(*st));
}

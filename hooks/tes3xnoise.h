#ifndef TES3X_NOISE_H
#define TES3X_NOISE_H

/* Noise_XX_25519_ChaChaPoly_BLAKE2b, the initiator's side, and the ChaCha20-Poly1305 (RFC 8439)
 * packets sealed with the keys it ends in. No kernel calls, so it also builds on the PC. */

#define NOISE_KEY 32u
#define NOISE_HASH 64u
#define NOISE_TAG 16u
/* Each message's size without its payload. */
#define NOISE_MSG1 NOISE_KEY                             /* e, then the payload in the clear */
#define NOISE_MSG2 (NOISE_KEY + NOISE_KEY + 2 * NOISE_TAG) /* e, s sealed, the payload sealed */
#define NOISE_MSG3 (NOISE_KEY + 2 * NOISE_TAG)           /* s sealed, the payload sealed */

struct noise {
    unsigned char ck[NOISE_HASH], h[NOISE_HASH], k[NOISE_KEY];
    unsigned long long n;
    unsigned int has_k;
    unsigned char s_secret[NOISE_KEY], s_public[NOISE_KEY];
    unsigned char e_secret[NOISE_KEY], e_public[NOISE_KEY];
    unsigned char re[NOISE_KEY], rs[NOISE_KEY];
};

/* The static and ephemeral secrets come from the caller's random source. */
void noise_start(struct noise *st, const unsigned char *prologue, unsigned int prologue_n,
                 const unsigned char s_secret[32], const unsigned char e_secret[32]);
/* -> e, payload. Writes NOISE_MSG1 + payload_n bytes. */
void noise_write1(struct noise *st, unsigned char *out, const unsigned char *payload,
                  unsigned int payload_n);
/* <- e, ee, s, es, payload. The payload's length (n - NOISE_MSG2 bytes into payload), or -1;
 * the server's static key is then in st->rs. */
int noise_read2(struct noise *st, const unsigned char *in, unsigned int n, unsigned char *payload);
/* -> s, se, payload. Writes NOISE_MSG3 + payload_n bytes. */
void noise_write3(struct noise *st, unsigned char *out, const unsigned char *payload,
                  unsigned int payload_n);
/* The transport keys: send is the initiator's, receive the responder's. Wipes the state, rs
 * too. */
void noise_split(struct noise *st, unsigned char send[32], unsigned char receive[32]);

/* out = ciphertext then tag (n + NOISE_TAG bytes); counter is the nonce. */
void noise_seal(const unsigned char key[32], unsigned long long counter, const unsigned char *ad,
                unsigned int ad_n, const unsigned char *in, unsigned int n, unsigned char *out);
/* in is ciphertext then tag; 0 if authentic, with n - NOISE_TAG bytes of plaintext in out. */
int noise_open(const unsigned char key[32], unsigned long long counter, const unsigned char *ad,
               unsigned int ad_n, const unsigned char *in, unsigned int n, unsigned char *out);

void noise_public(unsigned char public_key[32], const unsigned char secret[32]);
void noise_wipe(void *p, unsigned int n);

#endif

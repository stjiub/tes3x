#ifndef MGR_SHA256_H
#define MGR_SHA256_H

#include <stddef.h>
#include <stdint.h>

struct sha256 {
    uint32_t h[8];
    uint64_t n;
    unsigned char block[64];
};

void sha256_init(struct sha256 *s);
void sha256_update(struct sha256 *s, const void *data, size_t n);
void sha256_final(struct sha256 *s, unsigned char out[32]);
void sha256_hex(const unsigned char digest[32], char out[65]);

#endif

#ifndef TES3X_LOG_H
#define TES3X_LOG_H

typedef unsigned char u8;
typedef unsigned long u32;
typedef unsigned long long u64;

/* Appends to E:\tes3xlog.txt. The first call sets the time origin. */
void tes3x_log_prepare(void);
void tes3x_log(const char *tag, u32 value);
void tes3x_log_hex(const char *tag, u32 value);
void tes3x_log_raw(const char *buf, u32 len);

#endif

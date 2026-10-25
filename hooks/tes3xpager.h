#ifndef TES3X_PAGER_H
#define TES3X_PAGER_H

#include "tes3xlog.h"

int tes3x_pager_command(const char *text);
int tes3x_pager_init(void);
int tes3x_pager_contains(const void *ptr);
int tes3x_pager_set_group(void *base, u32 size, u32 group);
void *tes3x_pager_take(u32 bytes);

/* Allocation from the region's base stays below this; tes3x_pager_take claims above it. */
extern u32 tes3x_pager_limit;

#endif

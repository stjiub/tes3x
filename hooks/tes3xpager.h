#ifndef TES3X_PAGER_H
#define TES3X_PAGER_H

#include "tes3xlog.h"

int tes3x_pager_command(const char *text);
int tes3x_pager_set_group(void *base, u32 size, u32 group);

#endif

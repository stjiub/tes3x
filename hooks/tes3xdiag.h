#ifndef TES3X_DIAG_H
#define TES3X_DIAG_H

#include "tes3xlog.h"

enum {
    TES3X_DIAG_NOTE_NONE = 0,
    TES3X_DIAG_NOTE_CONSOLE = 1,
    TES3X_DIAG_NOTE_SCRIPT = 2,
    TES3X_DIAG_NOTE_ARCHIVE = 3,
    TES3X_DIAG_NOTE_REFERENCE = 4,
};

extern volatile u32 tes3x_diag_installed;
extern volatile u32 tes3x_patch_mask;

void tes3x_diag_init(void);
void tes3x_diag_tick(void);
void tes3x_diag_note(u32 code, u32 value);
void tes3x_diag_snapshot(void);
int tes3x_diag_command(const char *text);

#endif

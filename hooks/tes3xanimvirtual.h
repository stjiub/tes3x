#ifndef TES3X_ANIM_VIRTUAL_H
#define TES3X_ANIM_VIRTUAL_H

#include "tes3xlog.h"

int tes3x_animation_virtual_play(const u8 *ref, u8 *animation, float start, float stop);
int tes3x_animation_virtual_restore(void *animation);

#endif

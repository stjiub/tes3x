/* Select a Can't Touch This clip by number from the console test command. */

#include "tes3xlog.h"

int tes3x_ctt_clip_range(unsigned int clip, float *start, float *stop);
void tes3x_mcp132_test_set_range(float start, float stop);

int tes3x_ctt_test_select_clip(unsigned int clip)
{
    float start, stop;
    if (!tes3x_ctt_clip_range(clip, &start, &stop))
        return 0;
    tes3x_mcp132_test_set_range(start, stop);
    tes3x_log("ctt.test.clip", clip);
    return 1;
}

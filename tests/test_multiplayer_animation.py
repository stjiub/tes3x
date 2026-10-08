"""First-person animation phases must retain the engine's semantic key slots."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from tes3x_payload import find_tool


class AnimationPhaseTests(unittest.TestCase):
    def test_sparse_attack_keys_and_idle_loop(self):
        source = (ROOT / 'hooks/tes3xmulti.c').read_text()
        start = source.index('static const float *anim_keys(')
        end = source.index('static void player_anim_capture(', start)
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define plausible(p) ((p) != 0)
#define ANIM_GROUP_COUNT 150u
#define ANIM_GROUP_OBJECTS 0x68
#define GROUP_KEY_COUNT 0x14
#define GROUP_KEY_TIMES 0x1C
#define ANIM_LAYERS 3u
static u32 group_types[150];
#define TES3X_NET_ANIM_GROUP_TYPES group_types
static u8 wet;
static u8 underwater(const void *mobile) { return wet; }
#define TES3X_NET_BASE_UNDERWATER underwater
static u32 now_us(void) { return 250000; }
static void copy(u8 *to, const u8 *from, u32 n) { memcpy(to, from, n); }
'''
        harness += source[start:end]
        apply_start = source.index('static void anim_apply(')
        apply_end = source.index('/* Idle on every layer', apply_start)
        harness += r'''
#define ANIM_GROUP 0x38
#define ANIM_KEY 0x3C
#define ANIM_LOOPS 0x48
#define ANIM_TIMING 0x58
#define ANIM_SEQUENCES 0x2C4
#define ANIM_SEQUENCE_LAYERS 0x308
#define SEQUENCE_TIME_OFFSET 0x54
#define GROUP_NONE 255u
#define GROUP_LOOPS 100000
#define MOBILE_FLAGS 0x10
#define MOBILE_SCRIPTED 0x10000000u
#define ANIM_TIME_LIMIT 100000.0f
static u32 refused_anims, started;
static u8 *ref_animation(u8 *ref) { return ref; }
static u8 *ref_mobile(u8 *ref) { return 0; }
static int float_within(const u8 *bytes, u32 count, float limit) {
    float value; memcpy(&value, bytes, 4);
    return isfinite(value) && fabsf(value) <= limit;
}
typedef u8 (*fn_has_group)(void *, int);
typedef void (*fn_play_group)(void *, int, int, int, int);
static u8 has_group(void *a, int group) { return 1; }
/* Model the native acceptance gates, independently of the phase-to-mode mapping. */
static void play_group(void *a, int group, int layer, int mode, int loops) {
    u32 category = group_types[group];
    if (category == 7 && (mode < 3 || mode > 7)) return;
    if (category == 6 && mode != 3 && mode != 4 && mode != 9 && mode != 10 && mode != 11) return;
    ((u8 *)a)[ANIM_GROUP + layer] = group;
    started++;
}
#define TES3X_NET_ANIM_HAS_GROUP has_group
#define TES3X_NET_ANIM_PLAY_GROUP play_group
'''
        harness += source[apply_start:apply_end]
        harness += r'''
static void table(u8 *a, u8 *group, const float *keys, u32 n) {
    *(u8 **)(a + ANIM_GROUP_OBJECTS) = group;
    *(u32 *)(group + GROUP_KEY_COUNT) = n;
    *(const float **)(group + GROUP_KEY_TIMES) = keys;
}
static void phase(u8 *from, u8 *to, u8 slot, float time, float expected) {
    u8 key = slot;
    assert(anim_retime(from, to, 0, &key, &time));
    assert(key == slot);
    assert(fabsf(time - expected) < 0.001f);
}
int main(void) {
    u8 from[2048] = {0}, to[2048] = {0}, fg[64] = {0}, tg[64] = {0};
    float fk[39] = {0}, tk[39] = {0};
    table(from, fg, fk, 39);
    table(to, tg, tk, 15);
    /* Captured strong first-person chop: stale light key 22 while heavy phase is active. */
    u8 actor[256] = {0}, controller[16] = {0}, captured[20] = {0};
    actor[0xDD] = 7; actor[0xE0] = 2; controller[8] = 137;
    group_types[137] = 7;
    captured[0] = 0; captured[1] = 137; captured[2] = 137;
    captured[4] = 3; captured[5] = 22; captured[6] = 22;
    anim_follow_key(actor, controller, captured);
    assert(captured[4] == 3 && captured[5] == 27 && captured[6] == 27);
    fk[26] = 182.6666717529297f; fk[27] = 183.00001525878906f;
    tk[26] = 304.20001220703125f; tk[27] = 304.4000244140625f;
    *(u32 *)(tg + GROUP_KEY_COUNT) = 39;
    phase(from, to, captured[5], (fk[26]+fk[27])*0.5f, (tk[26]+tk[27])*0.5f);
    for (u32 state = 5; state <= 7; state++) {
        for (u32 attack = 1; attack <= 3; attack++) {
            actor[0xDD] = state; actor[0xE0] = attack;
            anim_follow_key(actor, controller, captured);
            assert(captured[5] == 12 + 11*(attack-1) + 2*(state-5));
        }
    }
    actor[0xDD] = 2; captured[5] = 19;
    anim_follow_key(actor, controller, captured);
    assert(captured[5] == 19); /* Hold remains at its native maximum wind-up. */
    *(u32 *)(tg + GROUP_KEY_COUNT) = 15;
    /* Native start modes differ from semantic action keys, including late swing/cast slots. */
    group_types[137] = 7;
    for (u32 k = 0; k < 39; k++)
        assert(anim_start_mode(137, k) == (k < 3 ? 3 : k < 6 ? 4 : k < 17 ? 5 : k < 28 ? 6 : 7));
    group_types[128] = 6;
    for (u32 k = 0; k < 15; k++)
        assert(anim_start_mode(128, k) == (k < 3 ? 3 : k < 6 ? 4 : k < 9 ? 9 : k < 12 ? 10 : 11));
    group_types[138] = 3;
    assert(anim_start_mode(138, 9) == 5);
    group_types[145] = 5;
    assert(anim_start_mode(145, 7) == 1);
    assert(anim_start_mode(0, 3) == 1);
    /* Slots before this swing describe draw/unequip on other parts of the timeline. */
    fk[0] = 1; fk[3] = 90; fk[5] = 120;
    fk[8] = 10; fk[9] = 20;
    tk[8] = 300; tk[9] = 320;
    phase(from, to, 9, 15, 310);
    phase(from, to, 9, 25, 320);
    phase(from, to, 9, 5, 300);
    /* Idle slots are start, stop, loop start, loop stop: not sorted. */
    fk[0] = 10; fk[1] = 30; fk[2] = 15; fk[3] = 25;
    tk[0] = 100; tk[1] = 160; tk[2] = 120; tk[3] = 150;
    phase(from, to, 3, 20, 135);
    phase(from, to, 0, 10, 100);
    /* An unavailable semantic key cannot be replaced by an unrelated one. */
    u8 key = 20;
    float time = 12;
    assert(!anim_retime(from, to, 0, &key, &time));
    /* Swimming requires water, uses loop slots, and leaves attack layers for retiming. */
    u8 mobile[32] = {0}, out[20] = {0}, before[20];
    *(uint16_t *)(mobile + MOBILE_MOVEMENT) = MOVE_SWIM | 1;
    out[1] = 141;
    out[5] = 9;
    float attack_time = 15;
    memcpy(out + 12, &attack_time, 4);
    memcpy(before, out, sizeof(out));
    assert(locomotion_capture(mobile, to, out) == 0);
    assert(memcmp(out, before, sizeof(out)) == 0);
    *(u8 **)(to + ANIM_GROUP_OBJECTS + GROUP_SWIM_WALK*4) = tg;
    tk[0] = 0; tk[1] = 100; tk[2] = 10; tk[3] = 20;
    wet = 1;
    assert(locomotion_capture(mobile, to, out) == 5);
    assert(out[0] == GROUP_SWIM_WALK && out[2] == GROUP_SWIM_WALK);
    assert(out[1] == 141 && out[5] == 9);
    float loop_time;
    memcpy(&loop_time, out + 8, 4);
    assert(fabsf(loop_time - 10.25f) < 0.001f);
    memcpy(&attack_time, out + 12, 4);
    assert(attack_time == 15);
    /* First-person idle must become crouch without replacing an ongoing attack. */
    *(uint16_t *)(mobile + MOBILE_MOVEMENT) = MOVE_SNEAK;
    *(u8 **)(to + ANIM_GROUP_OBJECTS + GROUP_IDLE_SNEAK*4) = tg;
    out[0] = 14; out[2] = 14;
    wet = 0;
    assert(locomotion_capture(mobile, to, out) == 5);
    assert(out[0] == GROUP_IDLE_SNEAK && out[2] == GROUP_IDLE_SNEAK);
    assert(out[1] == 141 && out[5] == 9);
    for (u32 direction = 0; direction < 4; direction++) {
        *(uint16_t *)(mobile + MOBILE_MOVEMENT) = MOVE_SNEAK | (1u << direction);
        *(u8 **)(to + ANIM_GROUP_OBJECTS + (GROUP_SNEAK_WALK + direction)*4) = tg;
        out[0] = 53; out[2] = 53;
        assert(locomotion_capture(mobile, to, out) == 5);
        assert(out[0] == GROUP_SNEAK_WALK + direction);
        assert(out[1] == 141 && out[5] == 9);
    }
    /* A late swing/cast arriving from idle must actually pass native playback gates. */
    u8 pose[20] = {0}, old[20] = {0};
    pose[0] = GROUP_NONE; pose[1] = 137; pose[2] = 137;
    pose[5] = 18; pose[6] = 22;
    for (u32 layer = 0; layer < 3; layer++) {
        to[ANIM_GROUP + layer] = 0;
        *(int *)(to + ANIM_SEQUENCE_LAYERS + 4*layer) = -1;
    }
    *(u8 **)(to + ANIM_GROUP_OBJECTS + 137*4) = tg;
    *(u32 *)(tg + GROUP_KEY_COUNT) = 39;
    anim_apply(to, old, pose, 1);
    assert(started == 2 && refused_anims == 0);
    assert(to[ANIM_GROUP+1] == 137 && to[ANIM_GROUP+2] == 137);
    assert(*(u32 *)(to+ANIM_KEY+4) == 18);
    pose[1] = 128; pose[2] = 128; pose[5] = 7; pose[6] = 13;
    *(u8 **)(to + ANIM_GROUP_OBJECTS + 128*4) = tg;
    anim_apply(to, old, pose, 1);
    assert(started == 4 && refused_anims == 0);
    assert(to[ANIM_GROUP+1] == 128 && to[ANIM_GROUP+2] == 128);
    return 0;
}
'''
        try:
            clang = find_tool('clang')
        except Exception as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            c = folder / 'phase.c'
            exe = folder / ('phase.exe' if sys.platform == 'win32' else 'phase')
            c.write_text(harness)
            build = subprocess.run([clang, str(c), '-o', str(exe)], capture_output=True,
                                   text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(exe)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()

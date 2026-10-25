/* Test-only commands for lazy-dialogue: compare stubs with the engine's chains, build every
 * topic, test the filter summary, count heard responses, and digest every topic's responses so control and patched builds
 * can be compared. */

#include "tes3xlog.h"

#define RECORDS_DIALOGUES 0x48 /* list: head +0x8; node: next +0x4, dialogue +0x8 */
#define DIAL_TYPE 0x14
#define DIAL_INFO 0x18
#define INFO_FLAGS 0x08
#define INFO_SOURCE 0x0C
#define INFO_NEXT 0x1C
#define INFO_CONDITIONS 0x20
#define INFO_OFFSET 0x24
#define INFO_HEARD 0x28
#define FILE_NAME 0x0C
#define FILTER_SOUND 6
#define DIGEST_FLAGS 0x2369 /* master, linked, deleted, quest bits, result text */

typedef const char *(__thiscall *fn_object_id)(const void *);

TES3X_OPTIONAL u32 tes3x_lazy_dialogue_check(void);
TES3X_OPTIONAL u32 tes3x_lazy_dialogue_build_all(void);
TES3X_OPTIONAL u32 tes3x_lazy_dialogue_summary_check(void);
TES3X_OPTIONAL u32 tes3x_lazy_dialogue_summary_sweep(u32 stride);

static const char COMMAND[] = "tes3xlazy";

static u32 mix(u32 h, u32 v)
{
    u32 i;

    for (i = 0; i < 4; i++, v >>= 8)
        h = (h ^ (v & 0xFF)) * 0x01000193u;
    return h;
}

static u32 mix_text(u32 h, const char *s)
{
    for (; s && *s; s++) {
        u8 c = (u8)*s;
        h = (h ^ (c >= 'A' && c <= 'Z' ? c + 32 : c)) * 0x01000193u;
    }
    return h * 0x01000193u;
}

static u32 digest_info(const u8 *info)
{
    const u8 *source = *(const u8 *const *)(info + INFO_SOURCE);
    const u8 *node;
    u32 h = 0x811C9DC5u;

    h = mix_text(h, source ? (const char *)source + FILE_NAME : 0);
    h = mix(h, *(const u32 *)(info + INFO_OFFSET));
    h = mix(h, *(const u32 *)(info + INFO_FLAGS) & DIGEST_FLAGS);
    h = mix(h, *(const u32 *)(info + 0x14));
    h = mix(h, *(const u32 *)(info + 0x18));
    for (node = *(const u8 *const *)(info + INFO_CONDITIONS); node;
         node = *(const u8 *const *)(node + 4)) {
        u32 tag = *(const u32 *)node, value = *(const u32 *)(node + 8);

        h = mix(h, tag);
        if (tag < FILTER_SOUND && value > 0x10000 && value != 0xFFFFFFFFu)
            h = mix_text(h, (*(fn_object_id *)(*(const u8 *const *)value + 0x20))(
                                (const void *)value));
    }
    return h;
}

static void digest(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_VALUE(LAZY_DATA_HANDLER);
    const u8 *records = handler ? *(const u8 *const *)handler : 0;
    const u8 *list = records ? *(const u8 *const *)(records + RECORDS_DIALOGUES) : 0;
    const u8 *node;
    u32 sums[5] = {0}, infos[5] = {0}, topics[5] = {0};
    u32 i;

    for (node = list ? *(const u8 *const *)(list + 8) : 0; node;
         node = *(const u8 *const *)(node + 4)) {
        const u8 *topic = *(const u8 *const *)(node + 8);
        const u8 *info;
        u32 type, h = 0x811C9DC5u;

        if (!topic || (type = topic[DIAL_TYPE]) > 4)
            continue;
        topics[type]++;
        for (info = *(const u8 *const *)(topic + DIAL_INFO); info;
             info = *(const u8 *const *)(info + INFO_NEXT)) {
            h = mix(h, digest_info(info));
            infos[type]++;
        }
        /* Order-independent across topics, order-dependent within one. */
        sums[type] += mix_text(h, *(const char *const *)(topic + 0x10));
    }
    for (i = 0; i < 5; i++) {
        tes3x_log_hex3("lazy.digest", i, infos[i], sums[i]);
        if (i) {
            topics[0] += topics[i];
            infos[0] += infos[i];
            sums[0] = mix(sums[0], sums[i]);
        }
    }
    tes3x_log("lazy.digest_topics", topics[0]);
    tes3x_log("lazy.digest_infos", infos[0]);
    tes3x_log("lazy.digest_sum", sums[0]);
}

/* Heard responses per dialogue type, which a save must carry across a reload. */
static void heard(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_VALUE(LAZY_DATA_HANDLER);
    const u8 *records = handler ? *(const u8 *const *)handler : 0;
    const u8 *list = records ? *(const u8 *const *)(records + RECORDS_DIALOGUES) : 0;
    const u8 *node;
    u32 counts[5] = {0}, i;

    for (node = list ? *(const u8 *const *)(list + 8) : 0; node;
         node = *(const u8 *const *)(node + 4)) {
        const u8 *topic = *(const u8 *const *)(node + 8);
        const u8 *info;

        if (!topic || topic[DIAL_TYPE] > 4)
            continue;
        for (info = *(const u8 *const *)(topic + DIAL_INFO); info;
             info = *(const u8 *const *)(info + INFO_NEXT))
            if (*(const u32 *)(info + INFO_HEARD))
                counts[topic[DIAL_TYPE]]++;
    }
    for (i = 0; i < 5; i++)
        tes3x_log_hex3("lazy.heard", i, counts[i], 0);
}

int tes3x_lazy_dialogue_test_command(const char *text)
{
    u32 i;

    for (i = 0; COMMAND[i]; i++)
        if ((text[i] | 0x20) != COMMAND[i])
            return 0;
    if (text[i] != ' ' && text[i])
        return 0;
    for (; text[i] == ' '; i++)
        ;
    if ((text[i] | 0x20) == 'c') {
        if (tes3x_lazy_dialogue_check)
            tes3x_lazy_dialogue_check();
    } else if ((text[i] | 0x20) == 's' && (text[i + 1] | 0x20) == 'w') {
        u32 stride = 0;

        for (; text[i] && text[i] != ' '; i++)
            ;
        for (; text[i] == ' '; i++)
            ;
        for (; text[i] >= '0' && text[i] <= '9'; i++)
            stride = stride * 10 + (u32)(text[i] - '0');
        if (tes3x_lazy_dialogue_summary_sweep)
            tes3x_lazy_dialogue_summary_sweep(stride ? stride : 1);
    } else if ((text[i] | 0x20) == 's') {
        if (tes3x_lazy_dialogue_summary_check)
            tes3x_lazy_dialogue_summary_check();
    } else if ((text[i] | 0x20) == 'h') {
        heard();
    } else if ((text[i] | 0x20) == 'b') {
        if (tes3x_lazy_dialogue_build_all)
            tes3x_lazy_dialogue_build_all();
    } else {
        digest();
    }
    return 1;
}

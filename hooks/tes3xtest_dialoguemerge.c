/* Test-only command counting the INFO chain of one dialogue topic. */

#include "tes3xlog.h"

#ifndef TES3X_DIALMERGE_DATA_HANDLER
#error "define TES3X_DIALMERGE_DATA_HANDLER to the DataHandler pointer"
#endif

#define RECORDS_DIALOGUES 0x48 /* list: head +0x8; node: next +0x4, dialogue +0x8 */
#define DIALOGUE_NAME 0x10
#define DIALOGUE_INFO 0x18
#define INFO_NEXT 0x1C
#define CHAIN_LIMIT 200000u

static const char COMMAND[] = "tes3xdial ";

static int lower(int c)
{
    return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c;
}

static int name_equal(const char *a, const char *b)
{
    while (*a && lower(*a) == lower(*b)) {
        a++;
        b++;
    }
    return !*a && !*b;
}

int tes3x_dialmerge_test_command(const char *text)
{
    const u8 *handler, *records, *list, *node, *topic = 0, *info;
    const char *name;
    u32 i, count = 0;

    for (i = 0; COMMAND[i]; i++)
        if (lower(text[i]) != COMMAND[i])
            return 0;
    name = text + i;
    handler = *(const u8 *const *)TES3X_DIALMERGE_DATA_HANDLER;
    records = handler ? *(const u8 *const *)handler : 0;
    list = records ? *(const u8 *const *)(records + RECORDS_DIALOGUES) : 0;
    for (node = list ? *(const u8 *const *)(list + 8) : 0; node;
         node = *(const u8 *const *)(node + 4)) {
        const u8 *dialogue = *(const u8 *const *)(node + 8);
        const char *id = dialogue ? *(const char *const *)(dialogue + DIALOGUE_NAME) : 0;

        if (id && name_equal(id, name)) {
            topic = dialogue;
            break;
        }
    }
    if (!topic) {
        tes3x_log("dialmerge.missing", 0);
        return 1;
    }
    for (info = *(const u8 *const *)(topic + DIALOGUE_INFO); info && count < CHAIN_LIMIT;
         info = *(const u8 *const *)(info + INFO_NEXT))
        count++;
    tes3x_log("dialmerge.infos", count);
    return 1;
}

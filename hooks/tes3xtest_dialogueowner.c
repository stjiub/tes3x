/* Test-only command checking findDialogue against the chain each INFO sits in. */

#include "tes3xlog.h"

#define RECORDS_DIALOGUES 0x48 /* list: head +0x8; node: next +0x4, dialogue +0x8 */
#define DIALOGUE_INFO 0x18
#define INFO_LINK 0x10
#define INFO_NEXT 0x1C

typedef const u8 *(__cdecl *fn_find_dialogue)(const u8 *info);

static const char COMMAND[] = "tes3xowner";

int tes3x_dialogue_owner_test_command(const char *text)
{
    const u8 *handler, *records, *list, *node;
    u32 i, stride = 0, index = 0, checked = 0, mismatched = 0, stored = 0;

    for (i = 0; COMMAND[i]; i++)
        if ((text[i] | 0x20) != COMMAND[i])
            return 0;
    if (text[i] != ' ' && text[i])
        return 0;
    for (; text[i] == ' '; i++)
        ;
    for (; text[i] >= '0' && text[i] <= '9'; i++)
        stride = stride * 10 + (u32)(text[i] - '0');
    if (!stride)
        stride = 1;
    handler = *(const u8 *const *)TES3X_VALUE(OWNER_DATA_HANDLER);
    records = handler ? *(const u8 *const *)handler : 0;
    list = records ? *(const u8 *const *)(records + RECORDS_DIALOGUES) : 0;
    for (node = list ? *(const u8 *const *)(list + 8) : 0; node;
         node = *(const u8 *const *)(node + 4)) {
        const u8 *topic = *(const u8 *const *)(node + 8);
        const u8 *info;

        for (info = topic ? *(const u8 *const *)(topic + DIALOGUE_INFO) : 0; info;
             info = *(const u8 *const *)(info + INFO_NEXT)) {
            if (index++ % stride)
                continue;
            checked++;
            if (((fn_find_dialogue)TES3X_VALUE(OWNER_FIND))(info) != topic)
                mismatched++;
            if (*(const u8 *const *)(info + INFO_LINK) == topic)
                stored++;
        }
    }
    tes3x_log("owner.chained", index);
    tes3x_log("owner.checked", checked);
    tes3x_log("owner.mismatch", mismatched);
    tes3x_log("owner.unstored", checked - stored);
    return 1;
}

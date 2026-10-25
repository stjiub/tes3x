/* Answer DialogueInfo::findDialogue from an owner pointer instead of walking every topic.
 *
 * After loading, INFO +0x10 (the load-link names) is null and unused, so it holds the owning
 * topic. Owners are recorded on the first lookup once every chained INFO's +0x10 is null; until
 * then, and for an INFO in no chain, the original walk answers. The INFO destructor frees +0x10,
 * so its read is guarded: a pointer whose first word is the topic vtable is an owner.
 */

#include "tes3xlog.h"

#define RECORDS_DIALOGUES 0x48 /* list: head +0x8; node: next +0x4, dialogue +0x8 */
#define DIALOGUE_INFO 0x18
#define INFO_LINK 0x10
#define INFO_NEXT 0x1C

u32 tes3x_dialogue_vtable;

static const u8 *first_dialogue(const u8 **node)
{
    const u8 *handler = *(const u8 *const *)TES3X_VALUE(OWNER_DATA_HANDLER);
    const u8 *records = handler ? *(const u8 *const *)handler : 0;
    const u8 *list = records ? *(const u8 *const *)(records + RECORDS_DIALOGUES) : 0;

    *node = list ? *(const u8 *const *)(list + 8) : 0;
    return *node ? *(const u8 *const *)(*node + 8) : 0;
}

static const u8 *next_dialogue(const u8 **node)
{
    *node = *(const u8 *const *)(*node + 4);
    return *node ? *(const u8 *const *)(*node + 8) : 0;
}

static void record_owners(void)
{
    const u8 *node, *topic;
    u8 *info;
    u32 topics = 0, infos = 0, vtable = 0;

    for (topic = first_dialogue(&node); node; topic = next_dialogue(&node)) {
        if (!topic)
            continue;
        vtable = *(const u32 *)topic;
        for (info = *(u8 **)(topic + DIALOGUE_INFO); info; info = *(u8 **)(info + INFO_NEXT))
            if (*(u32 *)(info + INFO_LINK))
                return;
    }
    if (!vtable)
        return;
    for (topic = first_dialogue(&node); node; topic = next_dialogue(&node)) {
        if (!topic)
            continue;
        topics++;
        for (info = *(u8 **)(topic + DIALOGUE_INFO); info; info = *(u8 **)(info + INFO_NEXT)) {
            *(const u8 **)(info + INFO_LINK) = topic;
            infos++;
        }
    }
    tes3x_dialogue_vtable = vtable;
    tes3x_log("owner.topics", topics);
    tes3x_log("owner.infos", infos);
}

static __attribute__((used, noinline)) const u8 *owner_of(const u8 *info)
{
    const u8 *owner;

    if (!info)
        return 0;
    if (!tes3x_dialogue_vtable)
        record_owners();
    owner = *(const u8 *const *)(info + INFO_LINK);
    if (tes3x_dialogue_vtable && owner && *(const u32 *)owner == tes3x_dialogue_vtable)
        return owner;
    return 0;
}

/* Replaces findDialogue's first instruction, mov eax, [DataHandler]. */
__attribute__((naked)) void tes3x_dialogue_owner_find(void)
{
    __asm__ volatile(
        "pushl 4(%esp)\n\t"
        "call _owner_of\n\t"
        "addl $4, %esp\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "retl\n\t"
        "1:\n\t"
        "movl " TES3X_SYM(OWNER_DATA_HANDLER) ", %eax\n\t"
        "pushl $" TES3X_SYM(OWNER_FIND_RESUME) "\n\t"
        "retl\n\t");
}

/* Replaces the destructor's mov eax, [edi+0x10]; cmp eax, ebp (ebp is 0 there). */
__attribute__((naked)) void tes3x_dialogue_owner_dtor(void)
{
    __asm__ volatile(
        "movl 0x10(%edi), %eax\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "pushl %ecx\n\t"
        "movl (%eax), %ecx\n\t"
        "cmpl _tes3x_dialogue_vtable, %ecx\n\t"
        "popl %ecx\n\t"
        "jne 1f\n\t"
        "cmpl $0, _tes3x_dialogue_vtable\n\t"
        "je 1f\n\t"
        "movl %ebp, 0x10(%edi)\n\t"
        "movl %ebp, %eax\n\t"
        "1:\n\t"
        "cmpl %ebp, %eax\n\t"
        "retl\n\t");
}

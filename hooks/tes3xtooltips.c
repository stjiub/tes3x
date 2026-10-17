/* Extra object information on the existing help-layer tooltip. */
#include "tes3xlog.h"

#define REF 0x52464552u
#define NPC 0x5F43504Eu
#define FACT 0x54434146u
#define WEAP 0x50414557u
#define MISC 0x4353494Du
#define ARMO 0x4F4D5241u
#define CLOT 0x544F4C43u
#define BOOK 0x4B4F4F42u
#define CREATURE 0x41455243u
#define TOOLTIP_ID 0xFFFF8105u
#define LABEL_ID 0xFFFF80B4u

typedef void(__attribute__((stdcall)) *fn_display)(u8 *, u8 *, int);
typedef u32(__attribute__((thiscall)) *fn_type)(const void *);
typedef u8 *(__attribute__((thiscall)) *fn_item_data)(u8 *);
typedef u8 *(__attribute__((thiscall)) *fn_player)(u8 *);
typedef u8 *(__cdecl *fn_find_help)(u32);
typedef u8 *(__attribute__((thiscall)) *fn_find_child)(u8 *, u32);
typedef u8 *(__attribute__((thiscall)) *fn_create_label)(u8 *, u32, const char *, int, int);
typedef u8 *(__attribute__((thiscall)) *fn_enchantment)(u8 *);

static u32 kind(const u8 *record)
{
    return ((fn_type)TES3X_TIP_TYPE)(record + 4);
}

static int plausible(const u8 *ptr)
{
    return (u32)ptr >= 0x10000u && (u32)ptr < 0x80000000u && !((u32)ptr & 3);
}

static int theft(u8 *data, u8 *owner, u32 owner_type)
{
    u8 *world = *(u8 **)TES3X_TIP_WORLD;
    u8 *mobile, *player_ref, *player_base, *required;

    if (!plausible(world))
        return 0;
    mobile = ((fn_player)TES3X_TIP_PLAYER)(world);
    player_ref = plausible(mobile) ? *(u8 **)(mobile + 0x14) : 0;
    player_base = plausible(player_ref) ? *(u8 **)(player_ref + 0x28) : 0;
    if (owner_type == NPC) {
        if (!plausible(player_base) || owner == *(u8 **)(player_base + 0x6c))
            return 0;
        required = *(u8 **)(data + 8);
        return !required || (plausible(required) && *(float *)(required + 0x34) == 0.0f);
    }
    if (owner_type == FACT)
        return !(*(u32 *)(owner + 0x29c) & 1u) ||
               *(int *)(owner + 0x294) < *(int *)(data + 8);
    return 0;
}

static char *put(char *at, char *end, const char *s)
{
    if (!s)
        return at;
    while (*s && at + 1 < end)
        *at++ = *s++;
    *at = 0;
    return at;
}

static char *number(char *at, char *end, unsigned n)
{
    char digits[11];
    unsigned count = 0;
    do {
        digits[count++] = (char)('0' + n % 10);
        n /= 10;
    } while (n && count < sizeof(digits));
    while (count && at + 1 < end)
        *at++ = digits[--count];
    *at = 0;
    return at;
}

static char *decimal(char *at, char *end, float value)
{
    unsigned scaled;
    if (value < 0 || value > 100)
        return put(at, end, "?");
    scaled = (unsigned)(value * 100.0f + 0.5f);
    at = number(at, end, scaled / 100);
    at = put(at, end, ".");
    if (scaled % 100 < 10)
        at = put(at, end, "0");
    return number(at, end, scaled % 100);
}

static void label(u8 *body, const char *text, int warning)
{
    u8 *widget = ((fn_create_label)TES3X_TIP_CREATE_LABEL)(body, LABEL_ID, text, 0, 0);
    if (warning && plausible(widget)) {
        float *colour = (float *)(widget + 0x154);
        colour[0] = 1.0f;
        colour[1] = 0.2f;
        colour[2] = 0.2f;
        colour[3] = 1.0f;
        widget[0x7f] = 1;
    }
}

void __attribute__((stdcall)) tes3x_tooltip_hook(u8 *object, u8 *data, int count)
{
    u8 *world, *menu, *body, *item, *owner, *soul, *enchant;
    u32 object_type, item_type, owner_type;
    char line[96], *at;
    int in_menu;

    ((fn_display)TES3X_TIP_ORIGINAL)(object, data, count);
    if (!plausible(object))
        return;
    world = *(u8 **)TES3X_TIP_WORLD;
    if (!plausible(world))
        return;
    menu = ((fn_find_help)TES3X_TIP_FIND_HELP)(TOOLTIP_ID);
    body = plausible(menu) ? ((fn_find_child)TES3X_TIP_FIND_CHILD)(
        menu, *(unsigned short *)TES3X_TIP_CONTENT_ID) : 0;
    if (!plausible(body))
        return;
    in_menu = world[0xd2] != 0;
    object_type = kind(object);
    item = object;
    if (object_type == REF) {
        item = *(u8 **)(object + 0x28);
        data = ((fn_item_data)TES3X_TIP_ITEM_DATA)(object);
    }
    if (!plausible(item))
        return;
    item_type = kind(item);
    owner = plausible(data) ? *(u8 **)(data + 4) : 0;
    owner_type = plausible(owner) ? kind(owner) : 0;
    if (!in_menu) {
        if (object_type == REF && owner && theft(data, owner, owner_type))
            label(body, "Steal", 1);
        return;
    }
    if (owner_type == NPC || owner_type == FACT) {
        const char *name = owner_type == NPC ? *(const char **)(owner + 0x70) :
                                                (const char *)(owner + 0x30);
        at = put(line, line + sizeof(line), owner_type == NPC ? "Owner: " : "Faction: ");
        at = put(at, line + sizeof(line), name);
        if (owner_type == FACT) {
            int rank = *(int *)(data + 8);
            if (rank >= 0 && rank < 10) {
                at = put(at, line + sizeof(line), " (rank ");
                at = number(at, line + sizeof(line), (unsigned)rank + 1);
                put(at, line + sizeof(line), ")");
            }
        }
        label(body, line, 0);
    }
    if (item_type == WEAP) {
        int weapon_type = item[0x5c];
        at = put(line, line + sizeof(line), "Speed ");
        at = decimal(at, line + sizeof(line), *(float *)(item + 0x60));
        at = put(at, line + sizeof(line), "   Reach ");
        decimal(at, line + sizeof(line), *(float *)(item + 0x64));
        if (weapon_type < 12)
            label(body, line, 0);
        else if (weapon_type <= 13) {
            at = put(line, line + sizeof(line), "Projectile damage ");
            at = number(at, line + sizeof(line), item[0x6a]);
            at = put(at, line + sizeof(line), "-");
            number(at, line + sizeof(line), item[0x6b]);
            label(body, line, 0);
        }
    }
    if (item_type == WEAP || item_type == ARMO || item_type == CLOT ||
        item_type == BOOK || item_type == MISC) {
        u8 *physical = item + 4;
        u32 *vtable = *(u32 **)physical;
        enchant = plausible((u8 *)vtable) && plausible((u8 *)vtable[0xd0 / 4]) ?
            ((fn_enchantment)vtable[0xd0 / 4])(physical) : 0;
        if (plausible(enchant)) {
            static const char *const modes[] = {
                "Cast once", "On strike", "On use", "Constant effect"
            };
            unsigned mode = *(unsigned short *)(enchant + 0x2c);
            if (mode < 4) {
                at = put(line, line + sizeof(line), "Enchantment: ");
                at = put(at, line + sizeof(line), modes[mode]);
                if (mode != 3 && plausible(data)) {
                    float charge = *(float *)(data + 0x10);
                    unsigned maximum = *(unsigned short *)(enchant + 0x30);
                    if (charge >= 0 && charge <= maximum) {
                        at = put(at, line + sizeof(line), "  Charge ");
                        at = number(at, line + sizeof(line), (unsigned)charge);
                        at = put(at, line + sizeof(line), "/");
                        number(at, line + sizeof(line), maximum);
                    }
                }
                label(body, line, 0);
            }
        }
    }
    if (item_type == MISC && plausible(data)) {
        soul = *(u8 **)(data + 0x10);
        if (plausible(soul) && (kind(soul) == CREATURE || kind(soul) == NPC)) {
            at = put(line, line + sizeof(line), "Soul: ");
            put(at, line + sizeof(line), *(const char **)(soul + 0x70));
            label(body, line, 0);
        }
    }
}

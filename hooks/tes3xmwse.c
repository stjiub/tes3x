/* Legacy MWSE 0.9.4 stack-machine compatibility. */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_SCRIPT_DECODE
#error "define TES3X_SCRIPT_DECODE to the VA of Script::Decode"
#endif
#ifndef TES3X_SCRIPT_IP
#error "define TES3X_SCRIPT_IP to the VA of the script instruction pointer"
#endif
#ifndef TES3X_SCRIPT_OPCODE
#error "define TES3X_SCRIPT_OPCODE to the VA of the decoded script opcode"
#endif
#ifndef TES3X_GAME_INSTANCE
#error "define TES3X_GAME_INSTANCE to the VA of the Game instance pointer"
#endif

#define TES3X_MWSE_STACK_WORDS 256
#define TES3X_MWSE_REGS 16
#define TES3X_MWSE_TARGET_REF   (TES3X_SCRIPT_IP + 0x3C)
#define TES3X_MWSE_TARGET_TEMPL (TES3X_SCRIPT_IP + 0x44)
#define TES3X_MWSE_LOCAL_VARS   (TES3X_SCRIPT_IP + 0x48)

typedef signed char s8;
typedef signed short s16;
typedef signed long s32;
typedef union { u32 word; float real; } tes3x_mwse_float;
typedef void (__attribute__((thiscall)) *tes3x_decode_fn)(void *, u32);

enum {
    TES3X_MWSE_ZERO = 1,
    TES3X_MWSE_POSITIVE = 2,
};

static void *tes3x_mwse_script;
static u32 tes3x_mwse_stack[TES3X_MWSE_STACK_WORDS];
static u32 tes3x_mwse_sp;
static u32 tes3x_mwse_regs[TES3X_MWSE_REGS];
static u32 tes3x_mwse_flags;
static u32 tes3x_mwse_calls;
static u32 tes3x_mwse_unsupported;
static u32 tes3x_mwse_fixup_calls;
static u32 tes3x_mwse_fixup_opcodes;
static u32 tes3x_mwse_target_calls;
static u32 tes3x_mwse_value_sets;

/* Inline operand bytes; -1 is not a legacy instruction. */
static int tes3x_mwse_operand_width(u32 opcode)
{
    switch (opcode) {
    case 0x3801: case 0x3809: case 0x380B: case 0x380D:
    case 0x3811: case 0x3816: case 0x3818:
        return 4;
    case 0x3802: case 0x380A: case 0x380C: case 0x380E:
    case 0x380F: case 0x3813: case 0x3817: case 0x3819:
        return 2;
    case 0x3804: case 0x3805: case 0x3806: case 0x3807:
    case 0x3808: case 0x3810: case 0x3812: case 0x3814: case 0x3815:
        return 1;
    default:
        break;
    }
    if (opcode == 0x3803 || (opcode >= 0x3820 && opcode <= 0x3824)
            || (opcode >= 0x3828 && opcode <= 0x382D)
            || (opcode >= 0x3830 && opcode <= 0x3839))
        return 0;
    if ((opcode >= 0x3C00 && opcode <= 0x3C09)
            || (opcode >= 0x3C10 && opcode <= 0x3C15)
            || opcode == 0x3C18 || (opcode >= 0x3C1A && opcode <= 0x3C22)
            || (opcode >= 0x3C28 && opcode <= 0x3C34)
            || (opcode >= 0x3E61 && opcode <= 0x3E68)
            || (opcode >= 0x3F00 && opcode <= 0x3F06)
            || (opcode >= 0x3F08 && opcode <= 0x3F12)
            || (opcode >= 0x3F21 && opcode <= 0x3F26)
            || (opcode >= 0x3F31 && opcode <= 0x3F38)
            || opcode == 0x3F3A || opcode == 0x3F3F || opcode == 0x3F5E
            || (opcode >= 0x3F61 && opcode <= 0x3F69)
            || opcode == 0x3F6E || opcode == 0x3F6F
            || opcode == 0x3F7C || opcode == 0x3F7E
            || opcode == 0x3FA0 || opcode == 0x3FA1)
        return 0;
    return -1;
}

static int tes3x_mwse_should_log(u32 n)
{
    u32 p;
    if (n <= 4)
        return 1;
    for (p = 10; p <= 1000000; p *= 10)
        if (n == p)
            return 1;
    return 0;
}

static void tes3x_mwse_set_flags(u32 value)
{
    s32 signed_value = (s32)value;
    tes3x_mwse_flags = value ? (signed_value > 0 ? TES3X_MWSE_POSITIVE : 0)
                             : TES3X_MWSE_ZERO | TES3X_MWSE_POSITIVE;
}

static int tes3x_mwse_push(u32 value)
{
    if (tes3x_mwse_sp >= TES3X_MWSE_STACK_WORDS)
        return 0;
    tes3x_mwse_stack[tes3x_mwse_sp++] = value;
    tes3x_mwse_set_flags(value);
    return 1;
}

static int tes3x_mwse_pop(u32 *value)
{
    if (!tes3x_mwse_sp)
        return 0;
    *value = tes3x_mwse_stack[--tes3x_mwse_sp];
    return 1;
}

static void tes3x_mwse_change_script(void *script)
{
    u32 i;
    if (script == tes3x_mwse_script)
        return;
    tes3x_mwse_script = script;
    tes3x_mwse_sp = 0;
    tes3x_mwse_flags = TES3X_MWSE_ZERO | TES3X_MWSE_POSITIVE;
    for (i = 0; i < TES3X_MWSE_REGS; i++)
        tes3x_mwse_regs[i] = 0;
}

static u32 tes3x_mwse_local_count(void *script, u32 type)
{
    if (type == 's')
        return *(u32 *)((u8 *)script + 0x30);
    if (type == 'l')
        return *(u32 *)((u8 *)script + 0x34);
    if (type == 'f')
        return *(u32 *)((u8 *)script + 0x38);
    return 0;
}

static int tes3x_mwse_local(void *script, u32 opcode)
{
    u32 type, index, value = 0;
    u32 **vars = *(u32 ***)TES3X_MWSE_LOCAL_VARS;
    if (!vars || !tes3x_mwse_pop(&type) || !tes3x_mwse_pop(&index)
            || index >= tes3x_mwse_local_count(script, type))
        return 0;
    if (opcode == 0x3C00) {
        if (type == 's')
            value = (u32)(s32)((s16 *)vars[0])[index];
        else if (type == 'l')
            value = vars[1][index];
        else if (type == 'f')
            value = vars[2][index];
        else
            return 0;
        return tes3x_mwse_push(value);
    }
    if (!tes3x_mwse_pop(&value))
        return 0;
    if (type == 's')
        ((s16 *)vars[0])[index] = (s16)value;
    else if (type == 'l')
        vars[1][index] = value;
    else if (type == 'f')
        vars[2][index] = value;
    else
        return 0;
    return 1;
}

static void *tes3x_mwse_target(void)
{
    return *(void **)TES3X_MWSE_TARGET_REF;
}

static void *tes3x_mwse_pc_target(void)
{
    u8 *game = *(u8 **)TES3X_GAME_INSTANCE;
    return game ? *(void **)(game + 0xD0) : 0;
}

static void *tes3x_mwse_template(void *target)
{
    return target ? *(void **)((u8 *)target + 0x28) : 0;
}

static int tes3x_mwse_set_ref(void *target)
{
    void *templ = tes3x_mwse_template(target);
    *(void **)TES3X_MWSE_TARGET_REF = target;
    *(void **)TES3X_MWSE_TARGET_TEMPL = templ;
    tes3x_mwse_set_flags((u32)target);
    return !target || templ != 0;
}

static u32 tes3x_mwse_value_offset(u32 type)
{
    switch (type) {
    case 0x4353494D: /* MISC */
    case 0x4B4F4F42: /* BOOK */
    case 0x48434C41: /* ALCH */
    case 0x50414557: /* WEAP */
        return 0x16;
    case 0x4847494C: /* LIGH */
        return 0x17;
    case 0x52474E49: /* INGR */
    case 0x4B434F4C: /* LOCK */
    case 0x424F5250: /* PROB */
    case 0x41504552: /* REPA */
        return 0x2B;
    case 0x4F4D5241: /* ARMO */
    case 0x544F4C43: /* CLOT */
        return 0x2C;
    case 0x41505041: /* APPA */
        return 0x2D;
    default:
        return 0;
    }
}

static u32 tes3x_mwse_weight_offset(u32 type)
{
    u32 offset = tes3x_mwse_value_offset(type);
    if (type == 0x544E4F43) /* CONT */
        return 0x1E;
    return offset ? offset - 1 : 0;
}

static int tes3x_mwse_can_set_weight(u32 type)
{
    return type == 0x4353494D || type == 0x544F4C43 || type == 0x50414557
           || type == 0x4F4D5241 || type == 0x4B4F4F42 || type == 0x48434C41
           || type == 0x41505041 || type == 0x52474E49 || type == 0x424F5250
           || type == 0x4B434950 || type == 0x41504552;
}

static u32 tes3x_mwse_quality_offset(u32 type)
{
    if (type == 0x424F5250 || type == 0x4B434950) /* PROB, PICK */
        return 0x2C;
    if (type == 0x41504552) /* REPA */
        return 0x2D;
    return 0;
}

static u32 tes3x_mwse_condition_offset(u32 type)
{
    if (type == 0x4B434F4C || type == 0x424F5250 || type == 0x4F4D5241)
        return 0x2D; /* LOCK, PROB, ARMO */
    if (type == 0x41504552) /* REPA */
        return 0x2C;
    if (type == 0x50414557) /* WEAP */
        return 0x17;
    return 0;
}

static int tes3x_mwse_is_gold(void *templ)
{
    const char *id = templ ? *(const char **)((u8 *)templ + 0x2C) : 0;
    return id && (id[0] == 'g' || id[0] == 'G')
           && (id[1] == 'o' || id[1] == 'O')
           && (id[2] == 'l' || id[2] == 'L')
           && (id[3] == 'd' || id[3] == 'D') && id[4] == '_';
}

static u32 *tes3x_mwse_attachment(void *target, u32 type)
{
    u8 *node = target ? *(u8 **)((u8 *)target + 0x44) : 0;
    while (node) {
        if (*(u32 *)node == type)
            return *(u32 **)(node + 8);
        node = *(u8 **)(node + 4);
    }
    return 0;
}

static u32 tes3x_mwse_item_count(void *target)
{
    u32 *data = tes3x_mwse_attachment(target, 6);
    return data ? data[0] : 1;
}

static u32 tes3x_mwse_max_condition(void *templ, u32 type)
{
    u32 offset = tes3x_mwse_condition_offset(type);
    u32 value = offset && templ ? *((u32 *)templ + offset) : 0;
    return type == 0x50414557 ? value >> 16 : value;
}

static u32 *tes3x_mwse_enchantment(void *templ, u32 type)
{
    u32 offset;
    u32 *ench;
    if (type == 0x4F4D5241) /* ARMO */
        offset = 0x30;
    else if (type == 0x544F4C43) /* CLOT */
        offset = 0x2D;
    else if (type == 0x50414557) /* WEAP */
        offset = 0x1D;
    else
        return 0;
    ench = templ ? *((u32 **)templ + offset) : 0;
    return ench && (ench[0x10] & 0xFF) ? ench : 0;
}

static u32 tes3x_mwse_max_charge(void *templ, u32 type)
{
    u32 *ench = tes3x_mwse_enchantment(templ, type);
    return ench ? ench[0x0C] : 0;
}

static u32 tes3x_mwse_min_float(u32 value, u32 maximum)
{
    tes3x_mwse_float a, b;
    a.word = value;
    b.real = (float)maximum;
    return a.real < b.real ? a.word : b.word;
}

static int tes3x_mwse_jump(u32 opcode, const u8 *operand, u32 length)
{
    int jump = opcode == 0x3809 || opcode == 0x380A;
    u32 target;
    if (opcode == 0x380B || opcode == 0x380C)
        jump = (tes3x_mwse_flags & TES3X_MWSE_ZERO) != 0;
    else if (opcode == 0x380D || opcode == 0x380E)
        jump = (tes3x_mwse_flags & TES3X_MWSE_ZERO) == 0;
    else if (opcode == 0x3816 || opcode == 0x3817)
        jump = (tes3x_mwse_flags & TES3X_MWSE_POSITIVE) != 0;
    else if (opcode == 0x3818 || opcode == 0x3819)
        jump = (tes3x_mwse_flags & TES3X_MWSE_POSITIVE) == 0;
    target = (opcode == 0x3809 || opcode == 0x380B || opcode == 0x380D
              || opcode == 0x3816 || opcode == 0x3818)
             ? *(const u32 *)operand : (u32)*(const unsigned short *)operand;
    if (jump) {
        if (target > length)
            return 0;
        *(u32 *)TES3X_SCRIPT_IP = target;
    }
    return 1;
}

static int tes3x_mwse_execute(void *script, u32 opcode, const u8 *operand, u32 length)
{
    u32 a, b, value, offset, type;
    u32 *attached, *ench;
    tes3x_mwse_float number;
    void *target, *templ;

    switch (opcode) {
    case 0x380F: /* Pop */
        a = *(const unsigned short *)operand / 4;
        tes3x_mwse_sp = a > tes3x_mwse_sp ? 0 : tes3x_mwse_sp - a;
        return 1;
    case 0x3810: /* PopReg */
        if (operand[0] >= TES3X_MWSE_REGS || !tes3x_mwse_pop(&value))
            return 0;
        tes3x_mwse_regs[operand[0]] = value;
        tes3x_mwse_set_flags(value);
        return 1;
    case 0x3811: /* Push */
        return tes3x_mwse_push(*(const u32 *)operand);
    case 0x3812: /* PushB */
        return tes3x_mwse_push((u32)(s32)*(const s8 *)operand);
    case 0x3813: /* PushS */
        return tes3x_mwse_push((u32)(s32)*(const s16 *)operand);
    case 0x3814: /* PushReg */
        return operand[0] < TES3X_MWSE_REGS
               && tes3x_mwse_push(tes3x_mwse_regs[operand[0]]);
    case 0x3820: case 0x3821: case 0x3822: case 0x3823: case 0x3824:
        if (!tes3x_mwse_pop(&a) || !tes3x_mwse_pop(&b))
            return 0;
        if (opcode == 0x3820)
            value = a + b;
        else if (opcode == 0x3821)
            value = a - b;
        else if (opcode == 0x3822)
            value = a * b;
        else if (!(s32)b)
            value = 0;
        else if (a == 0x80000000 && b == 0xFFFFFFFF)
            value = opcode == 0x3823 ? 0x80000000 : 0;
        else if (opcode == 0x3823)
            value = (u32)((s32)a / (s32)b);
        else
            value = (u32)((s32)a % (s32)b);
        return tes3x_mwse_push(value);
    case 0x3C00: case 0x3C02: /* GetLocal, SetLocal */
        return tes3x_mwse_local(script, opcode);
    case 0x3C06: /* RefPCTarget */
        return tes3x_mwse_set_ref(tes3x_mwse_pc_target());
    case 0x3C07: /* XGetPCTarget */
        target = tes3x_mwse_pc_target();
        tes3x_mwse_target_calls++;
        if (tes3x_mwse_should_log(tes3x_mwse_target_calls))
            tes3x_log("mwse.pc_target", (u32)target);
        return tes3x_mwse_push((u32)target);
    case 0x3C18: /* SetRef */
        return tes3x_mwse_pop(&value) && tes3x_mwse_set_ref((void *)value);
    case 0x3C08: /* XRefType */
        templ = tes3x_mwse_template(tes3x_mwse_target());
        return tes3x_mwse_push(templ ? *(u32 *)((u8 *)templ + 4) : 0);
    case 0x3F61: /* XGetValue */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        offset = tes3x_mwse_value_offset(type);
        value = offset && templ ? *((u32 *)templ + offset) : 0;
        if (type == 0x544F4C43)
            value &= 0xFFFF;
        if (tes3x_mwse_is_gold(templ))
            value = 1;
        if (target)
            value *= tes3x_mwse_item_count(target);
        return tes3x_mwse_push(value);
    case 0x3E61: /* XSetValue */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        offset = tes3x_mwse_value_offset(type);
        if (!tes3x_mwse_pop(&value) || !offset || !templ || tes3x_mwse_is_gold(templ))
            return tes3x_mwse_push(0);
        if (type == 0x544F4C43)
            *((u32 *)templ + offset) = (*((u32 *)templ + offset) & 0xFFFF0000)
                                       | (value & 0xFFFF);
        else
            *((u32 *)templ + offset) = value;
        tes3x_mwse_value_sets++;
        if (tes3x_mwse_should_log(tes3x_mwse_value_sets))
            tes3x_log("mwse.set_value", value);
        return tes3x_mwse_push(1);
    case 0x3E62: /* XSetWeight */
    case 0x3E63: /* XSetQuality */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        offset = opcode == 0x3E62 ? tes3x_mwse_weight_offset(type)
                                  : tes3x_mwse_quality_offset(type);
        if (opcode == 0x3E62 && !tes3x_mwse_can_set_weight(type))
            offset = 0;
        if (!tes3x_mwse_pop(&value) || !offset || !templ)
            return tes3x_mwse_push(0);
        *((u32 *)templ + offset) = value;
        return tes3x_mwse_push(1);
    case 0x3E64: /* XSetCondition */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        attached = tes3x_mwse_attachment(target, 6);
        if (!tes3x_mwse_pop(&value) || !attached
                || (type != 0x50414557 && type != 0x4F4D5241))
            return tes3x_mwse_push(0);
        a = tes3x_mwse_max_condition(templ, type);
        attached[3] = value < a ? value : a;
        return tes3x_mwse_push(1);
    case 0x3E65: /* XSetMaxCondition */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        offset = tes3x_mwse_condition_offset(type);
        if (!tes3x_mwse_pop(&value) || !templ || !offset
                || (type != 0x50414557 && type != 0x4F4D5241))
            return tes3x_mwse_push(0);
        attached = tes3x_mwse_attachment(target, 6);
        if (attached && attached[3] > value)
            attached[3] = value;
        if (type == 0x50414557)
            *((u32 *)templ + offset) = (value << 16) | (*((u32 *)templ + offset) & 0xFFFF);
        else
            *((u32 *)templ + offset) = value;
        return tes3x_mwse_push(1);
    case 0x3E66: /* XSetCharge */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        attached = tes3x_mwse_attachment(target, 6);
        if (!tes3x_mwse_pop(&value) || !attached)
            return tes3x_mwse_push(0);
        attached[4] = tes3x_mwse_min_float(value, tes3x_mwse_max_charge(templ, type));
        return tes3x_mwse_push(1);
    case 0x3E67: /* XSetMaxCharge */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        ench = tes3x_mwse_enchantment(templ, type);
        if (!tes3x_mwse_pop(&value) || !ench)
            return tes3x_mwse_push(0);
        attached = tes3x_mwse_attachment(target, 6);
        number.word = value;
        if (attached) {
            tes3x_mwse_float charge;
            charge.word = attached[4];
            if (charge.real >= number.real)
                attached[4] = value;
        }
        ench[0x0C] = (u32)(s32)number.real;
        return tes3x_mwse_push(1);
    case 0x3F63: /* XGetWeight */
    case 0x3F69: /* XGetQuality */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        offset = opcode == 0x3F63 ? tes3x_mwse_weight_offset(type)
                                  : tes3x_mwse_quality_offset(type);
        number.word = offset && templ ? *((u32 *)templ + offset) : 0;
        if (opcode == 0x3F63)
            number.real *= (float)tes3x_mwse_item_count(target);
        return tes3x_mwse_push(number.word);
    case 0x3F65: /* XGetCondition */
    case 0x3F66: /* XGetMaxCondition */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        return tes3x_mwse_push(tes3x_mwse_max_condition(templ, type));
    case 0x3F67: /* XGetCharge */
    case 0x3F68: /* XGetMaxCharge */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        type = templ ? *(u32 *)((u8 *)templ + 4) : 0;
        value = tes3x_mwse_max_charge(templ, type);
        number.real = (float)value;
        attached = tes3x_mwse_attachment(target, 6);
        if (opcode == 0x3F67 && attached)
            number.word = attached[4];
        return tes3x_mwse_push(number.word);
    default:
        break;
    }
    if ((opcode >= 0x3809 && opcode <= 0x380E)
            || (opcode >= 0x3816 && opcode <= 0x3819))
        return tes3x_mwse_jump(opcode, operand, length);
    return -1;
}

int tes3x_mwse_run(void *script, u32 opcode)
{
    int width = tes3x_mwse_operand_width(opcode);
    u32 *ip = (u32 *)TES3X_SCRIPT_IP;
    u32 length = *(u32 *)((u8 *)script + 0x3C);
    const u8 *data = *(const u8 **)((u8 *)script + 0x58);
    int handled = -1;

    tes3x_mwse_calls++;
    tes3x_mwse_change_script(script);
    if (width < 0) {
        tes3x_log("mwse.unknown_opcode", opcode);
        return 0;
    }
    if (!data || *ip > length || (u32)width > length - *ip) {
        tes3x_log("mwse.run_bounds", opcode);
        return 0;
    }
    data += *ip;
    *ip += (u32)width;
    handled = tes3x_mwse_execute(script, opcode, data, length);
    if (handled < 0) {
        tes3x_mwse_unsupported++;
        if (tes3x_mwse_should_log(tes3x_mwse_unsupported))
            tes3x_log("mwse.unsupported_opcode", opcode);
    } else if (!handled) {
        tes3x_log("mwse.execution_error", opcode);
    }
    return handled > 0;
}

/* Leave one VM opcode for the retail fixup loop, but skip its inline operand. */
void __attribute__((thiscall)) tes3x_mwse_fixup_hook(void *script, u32 with_info)
{
    tes3x_decode_fn decode = (tes3x_decode_fn)TES3X_SCRIPT_DECODE;
    u32 *ip = (u32 *)TES3X_SCRIPT_IP;
    u32 *opcode = (u32 *)TES3X_SCRIPT_OPCODE;
    u32 length = *(u32 *)((u8 *)script + 0x3C);
    int width;

    tes3x_mwse_fixup_calls++;
    if (tes3x_mwse_should_log(tes3x_mwse_fixup_calls))
        tes3x_log("mwse.fixup_call", tes3x_mwse_fixup_calls);
    decode(script, with_info);
    width = tes3x_mwse_operand_width(*opcode);
    if (width < 0)
        return;
    tes3x_mwse_fixup_opcodes++;
    if (tes3x_mwse_should_log(tes3x_mwse_fixup_opcodes))
        tes3x_log("mwse.fixup_opcode", *opcode);
    if (*ip > length || (u32)width > length - *ip) {
        tes3x_log("mwse.fixup_bounds", *opcode);
        return;
    }
    *ip += (u32)width;
}

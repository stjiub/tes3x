/* Legacy MWSE 0.9.4 stack-machine compatibility. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
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
#ifndef TES3X_RUN_FUNCTION
#error "define TES3X_RUN_FUNCTION to the VA of Script::RunFunction"
#endif
#ifndef TES3X_GAME_INSTANCE
#error "define TES3X_GAME_INSTANCE to the VA of the Game instance pointer"
#endif
#ifndef TES3X_MWSE_WORLD
#error "define TES3X_MWSE_WORLD to the WorldController pointer"
#endif
#ifndef TES3X_MWSE_DATA_HANDLER
#error "define TES3X_MWSE_DATA_HANDLER to the DataHandler pointer"
#endif
#ifndef TES3X_MWSE_FIND_REFERENCE
#error "define TES3X_MWSE_FIND_REFERENCE to the reference lookup function"
#endif
#if !defined(TES3X_MWSE_RESOLVE_OBJECT) || !defined(TES3X_MWSE_INVENTORY_ADD) || \
    !defined(TES3X_MWSE_INVENTORY_REMOVE) || \
    !defined(TES3X_MWSE_INVENTORY_ITEM_DATA) || !defined(TES3X_MWSE_ACTOR_EQUIPPED) || \
    !defined(TES3X_MWSE_WEAR_ITEM) || !defined(TES3X_MWSE_DROP_ITEM) || \
    !defined(TES3X_MWSE_START_COMBAT) || !defined(TES3X_MWSE_GET_SPELL_LIST) || \
    !defined(TES3X_MWSE_SPELL_ADD_ID) || !defined(TES3X_MWSE_SPELL_REMOVE) || \
    !defined(TES3X_MWSE_FORCE_CAST) || !defined(TES3X_MWSE_CREATE_REFERENCE) || \
    !defined(TES3X_MWSE_CELL_INSERT) || !defined(TES3X_MWSE_CELL_NODE) || \
    !defined(TES3X_MWSE_CELL_ACTIVATORS) || !defined(TES3X_MWSE_ATTACH_SCENE) || \
    !defined(TES3X_MWSE_REF_MODIFIED) || !defined(TES3X_MWSE_UPDATE_LIGHTING) || \
    !defined(TES3X_MWSE_ADD_MOB)
#error "define the legacy MWSE inventory functions"
#endif

#define TES3X_MWSE_STACK_WORDS 256
#define TES3X_MWSE_REGS 16
#define TES3X_MWSE_STRINGS 16
#define TES3X_MWSE_STRING_SIZE 2049
#define TES3X_MWSE_FILES 16
#define TES3X_MWSE_FILENAME_SIZE 62
#define TES3X_MWSE_PATH_SIZE 96
#define TES3X_MWSE_TARGET_REF   (TES3X_SCRIPT_IP + 0x3C)
#define TES3X_MWSE_TARGET_TEMPL (TES3X_SCRIPT_IP + 0x44)
#define TES3X_MWSE_LOCAL_VARS   (TES3X_SCRIPT_IP + 0x48)
#define TES3X_MWSE_SECOND_OBJECT (TES3X_SCRIPT_IP - 0x4B8)
#define TES3X_MWSE_VAR_INDEX     (TES3X_SCRIPT_IP + 4)
#define TES3X_MWSE_DESTINATION   (TES3X_SCRIPT_IP - 0x168)
#define TES3X_MWSE_TARGET_ROT    (TES3X_SCRIPT_IP - 0xA8)

typedef signed char s8;
typedef signed short s16;
typedef signed long s32;
typedef unsigned short u16;
typedef union { u32 word; float real; } tes3x_mwse_float;
typedef void (__attribute__((thiscall)) *tes3x_decode_fn)(void *, u32);
typedef void *(__attribute__((thiscall)) *tes3x_find_reference_fn)(void *, const char *);
typedef void *(__attribute__((thiscall)) *tes3x_resolve_object_fn)(void *, const char *);
typedef void (__attribute__((thiscall)) *tes3x_inventory_add_fn)(
    void *, void *, void *, int, int, u8 **);
typedef void (__attribute__((thiscall)) *tes3x_inventory_remove_fn)(
    void *, void *, void *, int, void *, int);
typedef void *(__attribute__((thiscall)) *tes3x_inventory_item_data_fn)(
    void *, void *, u32);
typedef void *(__attribute__((thiscall)) *tes3x_actor_equipped_fn)(void *, void *);
typedef void (__attribute__((thiscall)) *tes3x_wear_item_fn)(
    void *, void *, void *, int, int);
typedef u32 (__attribute__((thiscall)) *tes3x_drop_item_fn)(
    void *, void *, void *, int, int);
typedef void (__attribute__((thiscall)) *tes3x_start_combat_fn)(void *, void *);
typedef void *(__attribute__((thiscall)) *tes3x_get_spell_list_fn)(void *);
typedef u8 (__attribute__((thiscall)) *tes3x_spell_add_id_fn)(void *, const char *);
typedef void (__attribute__((thiscall)) *tes3x_spell_remove_fn)(void *, void *);
typedef void (__attribute__((thiscall)) *tes3x_force_cast_fn)(void *, void *);
typedef float (__attribute__((thiscall)) *tes3x_run_function_fn)(
    void *, u32, u32, u32);
typedef u8 *(__attribute__((thiscall)) *tes3x_create_reference_fn)(
    void *, void *, const float *, const float *, int, void *);
typedef void (__attribute__((thiscall)) *tes3x_cell_insert_fn)(void *, void *);
typedef void *(__attribute__((thiscall)) *tes3x_cell_part_fn)(void *);
typedef void (__attribute__((thiscall)) *tes3x_attach_scene_fn)(
    void *, void *, void *, void *, int);
typedef void (__attribute__((thiscall)) *tes3x_ref_modified_fn)(void *, int);
typedef void (__attribute__((thiscall)) *tes3x_update_lighting_fn)(void *, void *);
typedef void (__attribute__((thiscall)) *tes3x_add_mob_fn)(void *, void *);
#ifdef TES3X_CONSOLE
int tes3x_console_text_begin(const char *initial);
int tes3x_console_text_poll(char *out, u32 size);
#endif

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtSetInformationFile KFN(THUNK_NtSetInformationFile, fn_NtSetInformationFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define FILE_DIRECTORY_FILE 0x01u

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
static char tes3x_mwse_strings[TES3X_MWSE_STRINGS][TES3X_MWSE_STRING_SIZE];
static u32 tes3x_mwse_string_next;
typedef struct {
    char name[TES3X_MWSE_FILENAME_SIZE];
    u32 position;
} tes3x_mwse_file;
static tes3x_mwse_file tes3x_mwse_files[TES3X_MWSE_FILES];
static u32 tes3x_mwse_file_next;
static int tes3x_mwse_file_dir_ready;
static u32 tes3x_mwse_file_values[TES3X_MWSE_STACK_WORDS];
static u32 tes3x_mwse_native_a2, tes3x_mwse_native_a3;
static char tes3x_mwse_input_buffer[TES3X_MWSE_STRING_SIZE];
static int tes3x_mwse_input_active;

static int tes3x_mwse_push(u32 value);
static int tes3x_mwse_pop(u32 *value);
static u32 tes3x_mwse_strlen(const char *s);
static char *tes3x_mwse_save_string(const char *s);
static const char *tes3x_mwse_string(void *script, u32 value);

static float tes3x_mwse_native(void *script, u32 opcode)
{
    return ((tes3x_run_function_fn)TES3X_RUN_FUNCTION)(
        script, opcode, tes3x_mwse_native_a2, tes3x_mwse_native_a3);
}

static int tes3x_mwse_text_input(void *script)
{
    const char *initial, *result;
    u32 value, stopcode = 13, i;
    int status;
    if (!tes3x_mwse_pop(&value) || value < 32768
            || !(initial = tes3x_mwse_string(script, value)))
        return 0;
    if (tes3x_mwse_sp)
        tes3x_mwse_pop(&stopcode);
    (void)stopcode; /* The Xbox keyboard's Done button is the platform stop key. */
    if (!tes3x_mwse_input_active || initial != tes3x_mwse_input_buffer) {
        for (i = 0; initial[i] && i < TES3X_MWSE_STRING_SIZE - 1; i++)
            tes3x_mwse_input_buffer[i] = initial[i];
        tes3x_mwse_input_buffer[i] = 0;
#ifdef TES3X_CONSOLE
        if (!tes3x_console_text_begin(tes3x_mwse_input_buffer))
            return 0;
        tes3x_mwse_input_active = 1;
#else
        tes3x_mwse_input_active = 0;
#endif
        return tes3x_mwse_push((u32)tes3x_mwse_input_buffer)
               && tes3x_mwse_push(0);
    }
#ifdef TES3X_CONSOLE
    status = tes3x_console_text_poll(
        tes3x_mwse_input_buffer, sizeof(tes3x_mwse_input_buffer));
    if (!status)
        return tes3x_mwse_push((u32)tes3x_mwse_input_buffer)
               && tes3x_mwse_push(0);
    if (status == -2)
        return 0;
#else
    status = 1;
#endif
    tes3x_mwse_input_active = 0;
    result = tes3x_mwse_save_string(tes3x_mwse_input_buffer);
    return tes3x_mwse_push((u32)result)
           && tes3x_mwse_push(tes3x_mwse_strlen(tes3x_mwse_input_buffer) + 1);
}

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

static int tes3x_mwse_drop(u32 bytes)
{
    u32 words;
    if (bytes & 3)
        return 0;
    words = bytes / 4;
    if (words > tes3x_mwse_sp)
        return 0;
    tes3x_mwse_sp -= words;
    return 1;
}

static int tes3x_mwse_declare(u32 bytes)
{
    u32 words, i;
    if ((bytes & 3) || bytes / 4 > TES3X_MWSE_STACK_WORDS - tes3x_mwse_sp)
        return 0;
    words = bytes / 4;
    for (i = 0; i < words; i++)
        tes3x_mwse_stack[tes3x_mwse_sp++] = 0;
    return 1;
}

static int tes3x_mwse_stack_index(s32 byte_offset, u32 *index)
{
    s32 word_offset;
    s32 at;
    if ((byte_offset & 3) || !tes3x_mwse_sp)
        return 0;
    word_offset = byte_offset / 4;
    at = (s32)tes3x_mwse_sp - 1 - word_offset;
    if (at < 0 || at >= (s32)tes3x_mwse_sp)
        return 0;
    *index = (u32)at;
    return 1;
}

static void tes3x_mwse_change_script(void *script)
{
    u32 i;
    if (script == tes3x_mwse_script)
        return;
    tes3x_mwse_script = script;
    tes3x_mwse_sp = 0;
    tes3x_mwse_string_next = 0;
    tes3x_mwse_flags = TES3X_MWSE_ZERO | TES3X_MWSE_POSITIVE;
    for (i = 0; i < TES3X_MWSE_REGS; i++)
        tes3x_mwse_regs[i] = 0;
}

static u32 tes3x_mwse_strlen(const char *s)
{
    u32 n = 0;
    if (s)
        while (s[n])
            n++;
    return n;
}

static int tes3x_mwse_strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return (int)(unsigned char)*a - (int)(unsigned char)*b;
}

static char *tes3x_mwse_string_slot(void)
{
    char *out = tes3x_mwse_strings[tes3x_mwse_string_next++ % TES3X_MWSE_STRINGS];
    out[0] = 0;
    return out;
}

static char *tes3x_mwse_save_string(const char *s)
{
    char *out;
    u32 n = 0;
    if (!s)
        return 0;
    out = tes3x_mwse_string_slot();
    while (s[n] && n < TES3X_MWSE_STRING_SIZE - 1) {
        out[n] = s[n];
        n++;
    }
    out[n] = 0;
    return out;
}

static const char *tes3x_mwse_string(void *script, u32 value)
{
    const u8 *data;
    u32 length, n, i;
    char *out;
    if (!value)
        return 0;
    if (value >= 32768)
        return (const char *)value;
    data = *(const u8 **)((u8 *)script + 0x58);
    length = *(u32 *)((u8 *)script + 0x3C);
    if (!data || value >= length)
        return 0;
    n = data[value];
    if (n > length - value - 1)
        return 0;
    if (n >= TES3X_MWSE_STRING_SIZE)
        n = TES3X_MWSE_STRING_SIZE - 1;
    out = tes3x_mwse_string_slot();
    for (i = 0; i < n; i++)
        out[i] = (char)data[value + 1 + i];
    out[n] = 0;
    return out;
}

static int tes3x_mwse_append(char *out, u32 *at, const char *s)
{
    if (!s)
        s = "null";
    while (*s && *at < TES3X_MWSE_STRING_SIZE - 1)
        out[(*at)++] = *s++;
    out[*at] = 0;
    return *s == 0;
}

static void tes3x_mwse_append_char(char *out, u32 *at, u32 size, char c)
{
    if (*at + 1 < size)
        out[(*at)++] = c;
}

static void tes3x_mwse_append_uint(char *out, u32 *at, u32 size,
                                   u32 value, u32 base)
{
    static const char digits[] = "0123456789abcdef";
    char reversed[16];
    u32 n = 0;
    do {
        reversed[n++] = digits[value % base];
        value /= base;
    } while (value && n < sizeof(reversed));
    while (n)
        tes3x_mwse_append_char(out, at, size, reversed[--n]);
}

static void tes3x_mwse_append_int(char *out, u32 *at, u32 size, s32 value)
{
    u32 magnitude;
    if (value < 0) {
        tes3x_mwse_append_char(out, at, size, '-');
        magnitude = 0u - (u32)value;
    } else {
        magnitude = (u32)value;
    }
    tes3x_mwse_append_uint(out, at, size, magnitude, 10);
}

static void tes3x_mwse_append_float(char *out, u32 *at, u32 size,
                                    float value, int precision)
{
    u32 whole, fraction = 0, scale = 1, i;
    if (precision < 0)
        precision = 6;
    if (precision > 9)
        precision = 9;
    if (value < 0.0f) {
        tes3x_mwse_append_char(out, at, size, '-');
        value = -value;
    }
    whole = (u32)value;
    for (i = 0; i < (u32)precision; i++)
        scale *= 10;
    if (precision) {
        fraction = (u32)((value - (float)whole) * (float)scale + 0.5f);
        if (fraction >= scale) {
            whole++;
            fraction -= scale;
        }
    }
    tes3x_mwse_append_uint(out, at, size, whole, 10);
    if (precision) {
        tes3x_mwse_append_char(out, at, size, '.');
        for (i = scale / 10; i; i /= 10)
            tes3x_mwse_append_char(out, at, size,
                                   (char)('0' + (fraction / i) % 10));
    }
}

/* Returns the substitution count; a negative count marks a trailing percent. */
static int tes3x_mwse_interpolate(void *script, const char *format,
                                  char *out, u32 size)
{
    u32 value, at = 0, skip;
    int precision, substitutions = 0;
    const char *string;
    tes3x_mwse_float number;
    if (!size)
        return 0;
    while (*format && at + 1 < size) {
        if (*format++ != '%') {
            tes3x_mwse_append_char(out, &at, size, format[-1]);
            continue;
        }
        if (*format == '%') {
            tes3x_mwse_append_char(out, &at, size, *format++);
            continue;
        }
        if (!*format) {
            substitutions++;
            substitutions = -substitutions;
            break;
        }
        skip = 0;
        precision = -1;
        while (*format >= '0' && *format <= '9')
            skip = skip * 10 + (u32)(*format++ - '0');
        if (*format == '.') {
            format++;
            if (*format < '0' || *format > '9') {
                tes3x_mwse_append_char(out, &at, size, '%');
                tes3x_mwse_append_char(out, &at, size, '.');
                continue;
            }
            precision = 0;
            while (*format >= '0' && *format <= '9')
                precision = precision * 10 + (*format++ - '0');
        }
        if (*format == 'n' || *format == 'N') {
            format++;
            substitutions++;
            tes3x_mwse_append_char(out, &at, size, '\r');
            tes3x_mwse_append_char(out, &at, size, '\n');
            continue;
        }
        if (*format == 'q' || *format == 'Q') {
            format++;
            substitutions++;
            tes3x_mwse_append_char(out, &at, size, '"');
            continue;
        }
        if (*format == 'l' || *format == 'L') {
            format++;
            if (!tes3x_mwse_pop(&value))
                return (-2147483647 - 1);
            substitutions++;
            for (skip = 0; skip < 4 && ((char *)&value)[skip]; skip++)
                tes3x_mwse_append_char(out, &at, size, ((char *)&value)[skip]);
            continue;
        }
        if (*format == 'd' || *format == 'D' || *format == 'h'
                || *format == 'H' || *format == 'f' || *format == 'F') {
            char kind = *format++;
            if (!tes3x_mwse_pop(&value))
                return (-2147483647 - 1);
            substitutions++;
            if (kind == 'd' || kind == 'D')
                tes3x_mwse_append_int(out, &at, size, (s32)value);
            else if (kind == 'h' || kind == 'H')
                tes3x_mwse_append_uint(out, &at, size, value, 16);
            else {
                number.word = value;
                tes3x_mwse_append_float(out, &at, size, number.real, precision);
            }
            continue;
        }
        if (*format == 's' || *format == 'S') {
            format++;
            if (!tes3x_mwse_pop(&value)
                    || !(string = tes3x_mwse_string(script, value)))
                return (-2147483647 - 1);
            substitutions++;
            while (*string && skip)
                string++, skip--;
            while (*string && at + 1 < size && precision != 0) {
                tes3x_mwse_append_char(out, &at, size, *string++);
                if (precision > 0)
                    precision--;
            }
            continue;
        }
        tes3x_mwse_append_char(out, &at, size, '%');
    }
    out[at] = 0;
    return substitutions;
}

static int tes3x_mwse_string_build(void *script)
{
    u32 value;
    int substitutions;
    const char *format;
    char *out;
    if (!tes3x_mwse_pop(&value) || !(format = tes3x_mwse_string(script, value)))
        return 0;
    out = tes3x_mwse_string_slot();
    substitutions = tes3x_mwse_interpolate(
        script, format, out, TES3X_MWSE_STRING_SIZE);
    if (substitutions == (-2147483647 - 1))
        return 0;
    if (!out[0]) {
        u32 at = 0;
        tes3x_mwse_append(out, &at, "null");
    }
    return tes3x_mwse_push((u32)out);
}

static int tes3x_mwse_message_fix(void *script, u32 offset, u32 length)
{
    u8 *data = *(u8 **)((u8 *)script + 0x58);
    const char *format, *built;
    u32 value, field, bytes, i, buttons;
    int trailing;
    if (!data || offset > length || length - offset < 4
            || *(u16 *)(data + offset) != 0x1000)
        return 0;
    field = *(u16 *)(data + offset + 2);
    offset += 4;
    if (field > length - offset || !tes3x_mwse_sp)
        return 0;
    value = tes3x_mwse_stack[tes3x_mwse_sp - 1];
    format = tes3x_mwse_string(script, value);
    trailing = format && *format
               && format[tes3x_mwse_strlen(format) - 1] == '%';
    if (!tes3x_mwse_string_build(script) || !tes3x_mwse_pop(&value)
            || !(built = tes3x_mwse_string(script, value)))
        return 0;
    bytes = tes3x_mwse_strlen(built) + (trailing ? 0 : 1);
    if (bytes > field)
        bytes = field;
    for (i = 0; i < bytes; i++)
        data[offset + i] = (u8)built[i];
    offset += field;
    if (offset >= length || data[offset++] != 0 || offset >= length)
        return 0;
    buttons = data[offset++];
    while (buttons--) {
        if (offset >= length)
            return 0;
        field = data[offset++];
        if (field > length - offset || !tes3x_mwse_string_build(script)
                || !tes3x_mwse_pop(&value)
                || !(built = tes3x_mwse_string(script, value)))
            return 0;
        bytes = tes3x_mwse_strlen(built) + 1;
        if (bytes > field)
            bytes = field;
        for (i = 0; i < bytes; i++)
            data[offset + i] = (u8)built[i];
        offset += field;
    }
    return 1;
}

#define TES3X_MWSE_REGEX_CODE 512
#define TES3X_MWSE_REGEX_STATES 256
#define TES3X_MWSE_REGEX_PATCHES 512
#define TES3X_MWSE_REGEX_CLASSES 16
enum {
    TES3X_RE_ANY = 256,
    TES3X_RE_BOL,
    TES3X_RE_EOL,
    TES3X_RE_CLASS,
    TES3X_RE_SPLIT,
    TES3X_RE_MATCH,
    TES3X_RE_CAT,
    TES3X_RE_ALT,
    TES3X_RE_STAR,
    TES3X_RE_PLUS,
    TES3X_RE_QUESTION,
    TES3X_RE_CLASS_TOKEN = 512,
};

typedef struct {
    short type, out, out1, class_index;
} tes3x_regex_state;
typedef struct {
    short state, next;
    signed char which;
} tes3x_regex_patch;
typedef struct {
    short start, patches;
} tes3x_regex_fragment;

static unsigned short tes3x_regex_code[TES3X_MWSE_REGEX_CODE];
static u8 tes3x_regex_classes[TES3X_MWSE_REGEX_CLASSES][32];
static tes3x_regex_state tes3x_regex_states[TES3X_MWSE_REGEX_STATES];
static tes3x_regex_patch tes3x_regex_patches[TES3X_MWSE_REGEX_PATCHES];
static tes3x_regex_fragment tes3x_regex_fragments[TES3X_MWSE_REGEX_STATES];
static short tes3x_regex_list_a[TES3X_MWSE_REGEX_STATES];
static short tes3x_regex_list_b[TES3X_MWSE_REGEX_STATES];
static u16 tes3x_regex_seen[TES3X_MWSE_REGEX_STATES];
static u16 tes3x_regex_generation;
static int tes3x_regex_code_count, tes3x_regex_class_count;
static int tes3x_regex_state_count, tes3x_regex_patch_count;

static void tes3x_regex_class_set(int index, u8 c)
{
    tes3x_regex_classes[index][c >> 3] |= (u8)(1u << (c & 7));
}

static int tes3x_regex_emit(int token)
{
    if (tes3x_regex_code_count >= TES3X_MWSE_REGEX_CODE)
        return 0;
    tes3x_regex_code[tes3x_regex_code_count++] = (unsigned short)token;
    return 1;
}

static int tes3x_regex_postfix(const char *pattern)
{
    struct { short alternatives, atoms; } parens[32];
    int alternatives = 0, atoms = 0, depth = 0, token, i, inverse;
    const u8 *p = (const u8 *)pattern;
    tes3x_regex_code_count = tes3x_regex_class_count = 0;
    while (*p) {
        token = *p++;
        if (token == '(') {
            if (atoms > 1) {
                atoms--;
                if (!tes3x_regex_emit(TES3X_RE_CAT))
                    return 0;
            }
            if (depth >= (int)(sizeof(parens) / sizeof(parens[0])))
                return 0;
            parens[depth].alternatives = (short)alternatives;
            parens[depth++].atoms = (short)atoms;
            alternatives = atoms = 0;
            continue;
        }
        if (token == '|') {
            if (!atoms)
                return 0;
            while (--atoms > 0)
                if (!tes3x_regex_emit(TES3X_RE_CAT))
                    return 0;
            alternatives++;
            continue;
        }
        if (token == ')') {
            if (!depth || !atoms)
                return 0;
            while (--atoms > 0)
                if (!tes3x_regex_emit(TES3X_RE_CAT))
                    return 0;
            while (alternatives-- > 0)
                if (!tes3x_regex_emit(TES3X_RE_ALT))
                    return 0;
            depth--;
            alternatives = parens[depth].alternatives;
            atoms = parens[depth].atoms + 1;
            continue;
        }
        if (token == '*' || token == '+' || token == '?') {
            if (!atoms || !tes3x_regex_emit(token == '*' ? TES3X_RE_STAR
                                            : token == '+' ? TES3X_RE_PLUS
                                                           : TES3X_RE_QUESTION))
                return 0;
            continue;
        }
        if (atoms > 1) {
            atoms--;
            if (!tes3x_regex_emit(TES3X_RE_CAT))
                return 0;
        }
        if (token == '\\') {
            if (!*p)
                return 0;
            token = *p++;
        } else if (token == '.') {
            token = TES3X_RE_ANY;
        } else if (token == '^') {
            token = TES3X_RE_BOL;
        } else if (token == '$') {
            token = TES3X_RE_EOL;
        } else if (token == '[') {
            int first = 1, previous = -1;
            if (tes3x_regex_class_count >= TES3X_MWSE_REGEX_CLASSES)
                return 0;
            i = tes3x_regex_class_count++;
            for (token = 0; token < 32; token++)
                tes3x_regex_classes[i][token] = 0;
            inverse = *p == '^';
            if (inverse)
                p++;
            while (*p && (*p != ']' || first)) {
                int c = *p++;
                first = 0;
                if (c == '\\' && *p)
                    c = *p++;
                if (c == '-' && previous >= 0 && *p && *p != ']') {
                    int end = *p++;
                    if (end == '\\' && *p)
                        end = *p++;
                    if (end < previous)
                        return 0;
                    while (++previous <= end)
                        tes3x_regex_class_set(i, (u8)previous);
                    previous = -1;
                } else {
                    tes3x_regex_class_set(i, (u8)c);
                    previous = c;
                }
            }
            if (*p++ != ']')
                return 0;
            if (inverse)
                for (token = 0; token < 32; token++)
                    tes3x_regex_classes[i][token] ^= 0xFF;
            token = TES3X_RE_CLASS_TOKEN + i;
        }
        if (!tes3x_regex_emit(token))
            return 0;
        atoms++;
    }
    if (depth || !atoms)
        return 0;
    while (--atoms > 0)
        if (!tes3x_regex_emit(TES3X_RE_CAT))
            return 0;
    while (alternatives-- > 0)
        if (!tes3x_regex_emit(TES3X_RE_ALT))
            return 0;
    return 1;
}

static int tes3x_regex_state_new(int type, int out, int out1, int class_index)
{
    tes3x_regex_state *state;
    if (tes3x_regex_state_count >= TES3X_MWSE_REGEX_STATES)
        return -1;
    state = &tes3x_regex_states[tes3x_regex_state_count];
    state->type = (short)type;
    state->out = (short)out;
    state->out1 = (short)out1;
    state->class_index = (short)class_index;
    return tes3x_regex_state_count++;
}

static int tes3x_regex_patch_new(int state, int which)
{
    tes3x_regex_patch *patch;
    if (tes3x_regex_patch_count >= TES3X_MWSE_REGEX_PATCHES)
        return -1;
    patch = &tes3x_regex_patches[tes3x_regex_patch_count];
    patch->state = (short)state;
    patch->which = (signed char)which;
    patch->next = -1;
    return tes3x_regex_patch_count++;
}

static int tes3x_regex_patch_append(int first, int second)
{
    int at;
    if (first < 0)
        return second;
    for (at = first; tes3x_regex_patches[at].next >= 0;
         at = tes3x_regex_patches[at].next)
        ;
    tes3x_regex_patches[at].next = (short)second;
    return first;
}

static void tes3x_regex_patch_apply(int patch, int target)
{
    while (patch >= 0) {
        tes3x_regex_patch *p = &tes3x_regex_patches[patch];
        if (p->which)
            tes3x_regex_states[p->state].out1 = (short)target;
        else
            tes3x_regex_states[p->state].out = (short)target;
        patch = p->next;
    }
}

static int tes3x_regex_compile(const char *pattern)
{
    int i, token, state, patch, fragments = 0;
    tes3x_regex_fragment a, b, out;
    if (!tes3x_regex_postfix(pattern))
        return -1;
    tes3x_regex_state_count = tes3x_regex_patch_count = 0;
    for (i = 0; i < tes3x_regex_code_count; i++) {
        token = tes3x_regex_code[i];
        if (token == TES3X_RE_CAT) {
            if (fragments < 2)
                return -1;
            b = tes3x_regex_fragments[--fragments];
            a = tes3x_regex_fragments[--fragments];
            tes3x_regex_patch_apply(a.patches, b.start);
            out.start = a.start;
            out.patches = b.patches;
        } else if (token == TES3X_RE_ALT) {
            if (fragments < 2)
                return -1;
            b = tes3x_regex_fragments[--fragments];
            a = tes3x_regex_fragments[--fragments];
            state = tes3x_regex_state_new(TES3X_RE_SPLIT, a.start, b.start, 0);
            if (state < 0)
                return -1;
            out.start = (short)state;
            out.patches = (short)tes3x_regex_patch_append(a.patches, b.patches);
        } else if (token == TES3X_RE_STAR || token == TES3X_RE_PLUS
                   || token == TES3X_RE_QUESTION) {
            if (!fragments)
                return -1;
            a = tes3x_regex_fragments[--fragments];
            state = tes3x_regex_state_new(TES3X_RE_SPLIT, a.start, -1, 0);
            if (state < 0 || (patch = tes3x_regex_patch_new(state, 1)) < 0)
                return -1;
            if (token == TES3X_RE_STAR) {
                tes3x_regex_patch_apply(a.patches, state);
                out.start = (short)state;
                out.patches = (short)patch;
            } else if (token == TES3X_RE_PLUS) {
                tes3x_regex_patch_apply(a.patches, state);
                out.start = a.start;
                out.patches = (short)patch;
            } else {
                out.start = (short)state;
                out.patches = (short)tes3x_regex_patch_append(a.patches, patch);
            }
        } else {
            int type = token, class_index = 0;
            if (token >= TES3X_RE_CLASS_TOKEN) {
                type = TES3X_RE_CLASS;
                class_index = token - TES3X_RE_CLASS_TOKEN;
            }
            state = tes3x_regex_state_new(type, -1, -1, class_index);
            if (state < 0 || (patch = tes3x_regex_patch_new(state, 0)) < 0)
                return -1;
            out.start = (short)state;
            out.patches = (short)patch;
        }
        if (fragments >= TES3X_MWSE_REGEX_STATES)
            return -1;
        tes3x_regex_fragments[fragments++] = out;
    }
    if (fragments != 1)
        return -1;
    state = tes3x_regex_state_new(TES3X_RE_MATCH, -1, -1, 0);
    if (state < 0)
        return -1;
    tes3x_regex_patch_apply(tes3x_regex_fragments[0].patches, state);
    return tes3x_regex_fragments[0].start;
}

static void tes3x_regex_add(short *list, int *count, int state, const char *at,
                            const char *begin, const char *end)
{
    tes3x_regex_state *s;
    if (state < 0 || state >= tes3x_regex_state_count
            || tes3x_regex_seen[state] == tes3x_regex_generation)
        return;
    tes3x_regex_seen[state] = tes3x_regex_generation;
    s = &tes3x_regex_states[state];
    if (s->type == TES3X_RE_SPLIT) {
        tes3x_regex_add(list, count, s->out, at, begin, end);
        tes3x_regex_add(list, count, s->out1, at, begin, end);
    } else if (s->type == TES3X_RE_BOL) {
        if (at == begin)
            tes3x_regex_add(list, count, s->out, at, begin, end);
    } else if (s->type == TES3X_RE_EOL) {
        if (at == end)
            tes3x_regex_add(list, count, s->out, at, begin, end);
    } else if (*count < TES3X_MWSE_REGEX_STATES) {
        list[(*count)++] = (short)state;
    }
}

static int tes3x_regex_list_matches(const short *list, int count)
{
    int i;
    for (i = 0; i < count; i++)
        if (tes3x_regex_states[list[i]].type == TES3X_RE_MATCH)
            return 1;
    return 0;
}

static int tes3x_regex_match(const char *text, const char *pattern)
{
    const char *begin = text, *end = text + tes3x_mwse_strlen(text), *start, *at;
    short *current, *next, *swap;
    int entry = tes3x_regex_compile(pattern), current_count, next_count, i, type;
    u8 c;
    if (entry < 0)
        return 0;
    for (start = begin; start <= end; start++) {
        current = tes3x_regex_list_a;
        next = tes3x_regex_list_b;
        current_count = 0;
        if (++tes3x_regex_generation == 0) {
            for (i = 0; i < TES3X_MWSE_REGEX_STATES; i++)
                tes3x_regex_seen[i] = 0;
            tes3x_regex_generation = 1;
        }
        tes3x_regex_add(current, &current_count, entry, start, begin, end);
        if (tes3x_regex_list_matches(current, current_count))
            return 1;
        for (at = start; at < end && current_count; at++) {
            c = (u8)*at;
            next_count = 0;
            if (++tes3x_regex_generation == 0)
                tes3x_regex_generation = 1;
            for (i = 0; i < current_count; i++) {
                tes3x_regex_state *state = &tes3x_regex_states[current[i]];
                type = state->type;
                if (type == c || type == TES3X_RE_ANY
                        || (type == TES3X_RE_CLASS
                            && (tes3x_regex_classes[state->class_index][c >> 3]
                                & (1u << (c & 7)))))
                    tes3x_regex_add(next, &next_count, state->out, at + 1, begin, end);
            }
            swap = current;
            current = next;
            next = swap;
            current_count = next_count;
            if (tes3x_regex_list_matches(current, current_count))
                return 1;
        }
        if (at == end && current_count) /* Boost match_partial compatibility. */
            return 1;
    }
    return 0;
}

static int tes3x_mwse_same_text(const char *a, const char *b);

static int tes3x_mwse_valid_filename(const char *filename)
{
    u32 n = 0;
    if (!filename || *filename == '|')
        return 0;
    while (filename[n]) {
        char c = filename[n];
        if (n >= 42 || !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                || (c >= '0' && c <= '9') || c == '_' || c == '.'))
            return 0;
        n++;
    }
    return n >= 5;
}

static int tes3x_mwse_prepare_file_dir(void)
{
    static char path[] = "D:\\Data Files\\MWSE";
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    void *handle = 0;
    if (tes3x_mwse_file_dir_ready)
        return 1;
    tes3x_dos_attributes(&oa, &name, path);
    if (NtCreateFile(&handle, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN_IF,
                     FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return 0;
    NtClose(handle);
    tes3x_mwse_file_dir_ready = 1;
    return 1;
}

static tes3x_mwse_file *tes3x_mwse_file_state(const char *filename)
{
    tes3x_mwse_file *file = 0;
    u32 i, n;
    if (!tes3x_mwse_valid_filename(filename))
        return 0;
    for (i = 0; i < TES3X_MWSE_FILES; i++) {
        if (tes3x_mwse_files[i].name[0]
                && tes3x_mwse_same_text(tes3x_mwse_files[i].name, filename))
            return &tes3x_mwse_files[i];
        if (!file && !tes3x_mwse_files[i].name[0])
            file = &tes3x_mwse_files[i];
    }
    if (!file)
        file = &tes3x_mwse_files[tes3x_mwse_file_next++ % TES3X_MWSE_FILES];
    for (n = 0; filename[n] && n < TES3X_MWSE_FILENAME_SIZE - 1; n++)
        file->name[n] = filename[n];
    file->name[n] = 0;
    file->position = 0;
    return file;
}

static int tes3x_mwse_open_file(tes3x_mwse_file *file, void **handle)
{
    static const char prefix[] = "D:\\Data Files\\MWSE\\";
    char path[TES3X_MWSE_PATH_SIZE];
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u32 i = 0, n = 0;
    if (!file || !tes3x_mwse_prepare_file_dir())
        return 0;
    while (prefix[i] && i < sizeof(path) - 1) {
        path[i] = prefix[i];
        i++;
    }
    while (file->name[n] && i < sizeof(path) - 1)
        path[i++] = file->name[n++];
    path[i] = 0;
    tes3x_dos_attributes(&oa, &name, path);
    return NtCreateFile(handle, GENERIC_READ | GENERIC_WRITE | SYNCHRONIZE,
                        &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ,
                        FILE_OPEN_IF, FILE_SYNCHRONOUS_IO_NONALERT) == 0;
}

static u32 tes3x_mwse_file_read(const char *filename, void *data, u32 size)
{
    tes3x_mwse_file *file = tes3x_mwse_file_state(filename);
    IO_STATUS_BLOCK iosb;
    u64 offset;
    void *handle = 0;
    u32 read = 0;
    if (!tes3x_mwse_open_file(file, &handle))
        return 0;
    offset = file->position;
    if (NtReadFile(handle, 0, 0, 0, &iosb, data, size, &offset) == 0) {
        read = iosb.Information;
        file->position += read;
    }
    NtClose(handle);
    return read;
}

static u32 tes3x_mwse_file_write(const char *filename, const void *data, u32 size)
{
    tes3x_mwse_file *file = tes3x_mwse_file_state(filename);
    IO_STATUS_BLOCK iosb;
    u64 offset, end;
    void *handle = 0;
    u32 written = 0;
    if (!tes3x_mwse_open_file(file, &handle))
        return 0;
    offset = file->position;
    if (NtWriteFile(handle, 0, 0, 0, &iosb, data, size, &offset) == 0) {
        written = iosb.Information;
        file->position += written;
        end = file->position;
        NtSetInformationFile(handle, &iosb, &end, sizeof(end),
                             FileEndOfFileInformation);
    }
    NtClose(handle);
    return written;
}

static u32 tes3x_mwse_file_read_string(const char *filename, char *out, u32 size,
                                        int line)
{
    u32 n = 0;
    while (n + 1 < size) {
        char c;
        if (tes3x_mwse_file_read(filename, &c, 1) != 1) {
            out[n++] = 0;
            break;
        }
        if (!c || (line && c == '\n')) {
            out[n++] = 0;
            break;
        }
        out[n++] = c;
    }
    if (!n || out[n - 1])
        out[n++] = 0;
    if (line && n > 1 && out[n - 2] == '\r')
        out[n - 2] = 0;
    return n;
}

static u32 tes3x_mwse_format_count(const char *format, int *line)
{
    u32 count = 0;
    *line = 0;
    while (*format) {
        if (*format++ != '%')
            continue;
        while (*format >= '0' && *format <= '9')
            format++;
        if (*format == '.') {
            format++;
            if (*format < '0' || *format > '9')
                continue;
            while (*format >= '0' && *format <= '9')
                format++;
        }
        if (!*format) {
            *line = 1;
            break;
        }
        if (*format == 'l' || *format == 'L' || *format == 'd'
                || *format == 'D' || *format == 'h' || *format == 'H'
                || *format == 'f' || *format == 'F' || *format == 's'
                || *format == 'S')
            count++;
        format++;
    }
    return count;
}

static u32 tes3x_mwse_parse_text(const char *format, const char *string,
                                 u32 *results, u32 maximum)
{
    u32 count = 1, skip, width, value;
    int sign;
    char buffer[TES3X_MWSE_STRING_SIZE];
    tes3x_mwse_float number;
    while (*format && *string && count < maximum) {
        if (*format != '%') {
            if (*format++ != *string++)
                format = 0;
            continue;
        }
        format++;
        skip = 0;
        width = 0xFFFFFFFF;
        while (*format >= '0' && *format <= '9')
            skip = skip * 10 + (u32)(*format++ - '0');
        if (*format == '.') {
            format++;
            if (*format < '0' || *format > '9') {
                if (*string == '%' && string[1] == '.')
                    string += 2;
                else
                    format = 0;
                continue;
            }
            width = 0;
            while (*format >= '0' && *format <= '9')
                width = width * 10 + (u32)(*format++ - '0');
        }
        if (*format == 'n' || *format == 'N') {
            format++;
            if (*string == '\r')
                string++;
            if (*string == '\n')
                string++;
            else
                format = 0;
        } else if (*format == 'q' || *format == 'Q') {
            format++;
            if (*string == '"')
                string++;
            else
                format = 0;
        } else if (*format == 'l' || *format == 'L') {
            u32 i;
            format++;
            value = 0;
            for (i = 0; i < 4 && *string; i++)
                ((char *)&value)[i] = *string++;
            results[count++] = value;
        } else if (*format == 'd' || *format == 'D') {
            format++;
            value = 0;
            sign = 1;
            if (*string == '-') {
                string++;
                sign = -1;
            }
            while (*string >= '0' && *string <= '9')
                value = value * 10 + (u32)(*string++ - '0');
            results[count++] = sign < 0 ? 0u - value : value;
        } else if (*format == 'h' || *format == 'H') {
            u32 digits = 0, digit;
            format++;
            value = 0;
            while (digits++ < 8) {
                if (*string >= '0' && *string <= '9')
                    digit = (u32)(*string - '0');
                else if (*string >= 'a' && *string <= 'f')
                    digit = (u32)(*string - 'a' + 10);
                else if (*string >= 'A' && *string <= 'F')
                    digit = (u32)(*string - 'A' + 10);
                else
                    break;
                value = value * 16 + digit;
                string++;
            }
            results[count++] = value;
        } else if (*format == 'f' || *format == 'F') {
            float divisor = 10.0f;
            format++;
            number.real = 0.0f;
            sign = 1;
            if (*string == '-') {
                string++;
                sign = -1;
            }
            while (*string >= '0' && *string <= '9')
                number.real = number.real * 10.0f + (float)(*string++ - '0');
            if (*string == '.') {
                string++;
                while (*string >= '0' && *string <= '9') {
                    number.real += (float)(*string++ - '0') / divisor;
                    divisor *= 10.0f;
                }
            }
            if (sign < 0)
                number.real = -number.real;
            results[count++] = number.word;
        } else if (*format == 's' || *format == 'S') {
            u32 at = 0;
            char stop;
            format++;
            while (skip && *string)
                skip--, string++;
            if (width != 0) {
                while (width && *string && at + 1 < sizeof(buffer)) {
                    buffer[at++] = *string++;
                    width--;
                }
            } else {
                stop = *format;
                if (stop == '%' && format[1] != '%') {
                    stop = format[1];
                    if (stop == 'n' || stop == 'N')
                        stop = '\r';
                    else if (stop == 'q' || stop == 'Q')
                        stop = '"';
                    else if (stop == 'd' || stop == 'D' || stop == 'f'
                             || stop == 'F')
                        stop = 0;
                    else
                        stop = *string;
                }
                while (*string && at + 1 < sizeof(buffer)) {
                    if (*string == stop || (!stop && (*string == '-'
                            || (*string >= '0' && *string <= '9'))))
                        break;
                    buffer[at++] = *string++;
                }
            }
            buffer[at] = 0;
            results[count++] = (u32)tes3x_mwse_save_string(buffer);
        } else if (*format == '%') {
            format++;
            if (*string == '%')
                string++;
            else
                format = 0;
        } else if (*format) {
            if (*string == '%' && string[1] == *format)
                string += 2, format++;
            else
                format = 0;
        }
    }
    results[0] = count - 1;
    if (!*string && (!format || !*format || (*format == '%' && !format[1])))
        results[0] = count;
    while (count < maximum)
        results[count++] = 0;
    return results[0];
}

static int tes3x_mwse_file_command(void *script, u32 opcode)
{
    tes3x_mwse_file *file;
    const char *filename;
    const char *string;
    u32 name_value, value, count, bytes, i, result_count;
    int substitutions;
    char input[TES3X_MWSE_STRING_SIZE];
    if (!tes3x_mwse_pop(&name_value)
            || !(filename = tes3x_mwse_string(script, name_value)))
        return 0;
    if (opcode == 0x3C10) { /* XFileRewind */
        file = tes3x_mwse_file_state(filename);
        if (!file)
            return 0;
        file->position = 0;
        return 1;
    }
    if (opcode == 0x3C15) { /* XFileSeek */
        if (!tes3x_mwse_pop(&value)
                || !(file = tes3x_mwse_file_state(filename)))
            return 0;
        file->position = value;
        return 1;
    }
    if (opcode == 0x3C14) { /* XFileReadString */
        count = tes3x_mwse_file_read_string(filename, input, sizeof(input), 0);
        string = count > 1 ? tes3x_mwse_save_string(input) : "null";
        return tes3x_mwse_push((u32)string);
    }
    if (opcode == 0x3F09) { /* XFileWriteText */
        if (!tes3x_mwse_pop(&value)
                || !(string = tes3x_mwse_string(script, value)))
            return 0;
        substitutions = tes3x_mwse_interpolate(
            script, string, input, sizeof(input));
        if (substitutions == (-2147483647 - 1))
            return 0;
        if (input[0])
            string = input;
        bytes = tes3x_mwse_strlen(string) + (substitutions < 0 ? 0 : 1);
        return tes3x_mwse_file_write(filename, string, bytes) == bytes;
    }
    if (opcode == 0x3F08) { /* XFileReadText */
        if (!tes3x_mwse_pop(&value)
                || !(string = tes3x_mwse_string(script, value)))
            return 0;
        result_count = tes3x_mwse_format_count(string, &substitutions) + 1;
        if (result_count > TES3X_MWSE_STACK_WORDS
                || result_count > TES3X_MWSE_STACK_WORDS - tes3x_mwse_sp)
            return 0;
        count = tes3x_mwse_file_read_string(
            filename, input, sizeof(input), substitutions);
        for (i = 0; i < result_count; i++)
            tes3x_mwse_file_values[i] = 0;
        if (count)
            tes3x_mwse_parse_text(
                string, input, tes3x_mwse_file_values, result_count);
        for (i = result_count; i; i--)
            if (!tes3x_mwse_push(tes3x_mwse_file_values[i - 1]))
                return 0;
        return 1;
    }
    if (opcode >= 0x3C11 && opcode <= 0x3C13) {
        if (!tes3x_mwse_pop(&count) || count >= TES3X_MWSE_STACK_WORDS
                || count + 1 > TES3X_MWSE_STACK_WORDS - tes3x_mwse_sp)
            return 0;
        bytes = opcode == 0x3C11 ? count * 2 : count * 4;
        bytes = tes3x_mwse_file_read(filename, tes3x_mwse_file_values, bytes);
        count = opcode == 0x3C11 ? bytes / 2 : bytes / 4;
        if (opcode == 0x3C11) {
            s16 *shorts = (s16 *)tes3x_mwse_file_values;
            for (i = count; i; i--)
                tes3x_mwse_file_values[i - 1] = (u32)(s32)shorts[i - 1];
        }
        for (i = count; i; i--)
            if (!tes3x_mwse_push(tes3x_mwse_file_values[i - 1]))
                return 0;
        return tes3x_mwse_push(count);
    }
    if (!tes3x_mwse_pop(&value))
        return 0;
    if (opcode == 0x3C31) { /* XFileWriteShort */
        s16 short_value = (s16)value;
        return tes3x_mwse_file_write(filename, &short_value,
                                     sizeof(short_value)) == sizeof(short_value);
    }
    if (opcode == 0x3C32 || opcode == 0x3C33) /* XFileWriteLong/Float */
        return tes3x_mwse_file_write(filename, &value, sizeof(value)) == sizeof(value);
    if (opcode == 0x3C34) { /* XFileWriteString */
        string = tes3x_mwse_string(script, value);
        if (!string)
            return 0;
        bytes = tes3x_mwse_strlen(string) + 1;
        return tes3x_mwse_file_write(filename, string, bytes) == bytes;
    }
    return 0;
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

static void *tes3x_mwse_target(void);
static u32 *tes3x_mwse_attachment(void *target, u32 type);

static int tes3x_mwse_local(void *script, u32 opcode)
{
    u32 type, index, value = 0;
    u32 **vars;
    u32 *holder;
    int foreign = opcode == 0x3C01 || opcode == 0x3C03;
    if (foreign) {
        holder = tes3x_mwse_attachment(tes3x_mwse_target(), 6);
        vars = holder ? *(u32 ***)(holder + 6) : 0;
    } else {
        vars = *(u32 ***)TES3X_MWSE_LOCAL_VARS;
    }
    if (!vars || !tes3x_mwse_pop(&type) || !tes3x_mwse_pop(&index)
            || (!foreign && index >= tes3x_mwse_local_count(script, type)))
        return 0;
    if (opcode == 0x3C00 || opcode == 0x3C01) {
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

static void *tes3x_mwse_base(void *templ)
{
    void *base;
    if (!templ || !(base = *(void **)((u8 *)templ + 0x6C)))
        return 0;
    return *(u32 *)((u8 *)base + 4) == *(u32 *)((u8 *)templ + 4) ? base : 0;
}

static const char *tes3x_mwse_id(void *object)
{
    return object ? *(const char **)((u8 *)object + 0x2C) : 0;
}

static char *tes3x_mwse_name(void *templ)
{
    u32 type;
    void *base;
    if (!templ)
        return 0;
    type = *(u32 *)((u8 *)templ + 4);
    base = tes3x_mwse_base(templ);
    if (type == 0x5F43504E || type == 0x41455243) /* NPC_, CREA */
        return *(char **)((u8 *)(base ? base : templ) + 0x70);
    if (type == 0x544E4F43) /* CONT */
        return *(char **)((u8 *)templ + 0x6C);
    if (type == 0x4847494C) /* LIGH */
        return *(char **)((u8 *)templ + 0x48);
    if (type == 0x544F4C43 || type == 0x4F4D5241 || type == 0x50414557
            || type == 0x4353494D || type == 0x4B4F4F42 || type == 0x48434C41)
        return *(char **)((u8 *)templ + 0x44);
    if (type == 0x49544341) /* ACTI */
        return *(char **)((u8 *)templ + 0x38);
    if (type == 0x524F4F44) /* DOOR */
        return (char *)templ + 0x34;
    if (type == 0x41505041) /* APPA */
        return (char *)templ + 0x64;
    if (type == 0x52474E49 || type == 0x41504552 || type == 0x424F5250
            || type == 0x4B434950) /* INGR, REPA, PROB, PICK */
        return (char *)templ + 0x44;
    return 0;
}

static int tes3x_mwse_same_text(const char *a, const char *b)
{
    while (*a && *b) {
        char ca = *a++, cb = *b++;
        if (ca >= 'A' && ca <= 'Z')
            ca += 'a' - 'A';
        if (cb >= 'A' && cb <= 'Z')
            cb += 'a' - 'A';
        if (ca != cb)
            return 0;
    }
    return *a == *b;
}

static void *tes3x_mwse_player(void)
{
    const u8 *world = *(const u8 **)TES3X_MWSE_WORLD;
    const u8 *mobs, *mobile, *const *list;
    if (!world || !(mobs = *(const u8 **)(world + 0x5C))
            || !(list = *(const u8 *const **)(mobs + 0x24)) || !(mobile = *list))
        return 0;
    return *(void **)(mobile + 0x14);
}

static const char *tes3x_mwse_cell_id(void *ref, const char *fallback)
{
    const u8 *list = ref ? *(const u8 **)((u8 *)ref + 0x14) : 0;
    const u8 *cell = list ? *(const u8 **)(list + 0x0C) : 0;
    const char *name = cell ? *(const char **)(cell + 0x14) : 0;
    return name && *name ? name : fallback;
}

static short *tes3x_mwse_input(void)
{
    const u8 *world = *(const u8 **)TES3X_MWSE_WORLD;
    u8 *controller;
    int port;
    if (!world || !(controller = *(u8 **)(world + 0x4C)))
        return 0;
    port = *(int *)(controller + 0x804);
    if (port < 0 || port > 3)
        return 0;
    return (short *)(controller + port * 0x200 + 0x16);
}

static int tes3x_mwse_key_pressed(u32 code)
{
    static const u8 virtual_keys[] = {191, 13, 27, 8, 32, 16};
    static const u8 xbox_keys[] = {9, 10, 11, 12, 14, 15};
    short *input = tes3x_mwse_input();
    u32 i;
    if (!input)
        return tes3x_mwse_push(0);
    if (code) {
        for (i = 0; i < sizeof(virtual_keys); i++)
            if (virtual_keys[i] == code)
                return tes3x_mwse_push(input[xbox_keys[i]] ? 2 : 0);
        return tes3x_mwse_push(0);
    }
    for (i = 0; i < sizeof(virtual_keys); i++)
        if (input[xbox_keys[i]])
            return tes3x_mwse_push(virtual_keys[i]);
    return tes3x_mwse_push(0);
}

static u32 tes3x_mwse_ref_kind;

static int tes3x_mwse_active_cell(const u8 *handler, const u8 *cell)
{
    int dx, dy;
    if (!handler || !cell || !(*(const u32 *)(cell + 0x18) & 0x10))
        return 0;
    if (*(const u32 *)(cell + 0x18) & 1)
        return *(const u8 *const *)(handler + 0xAC) == cell;
    if (*(const u8 *const *)(handler + 0xAC))
        return 0;
    dx = *(const int *)(cell + 0x24) - *(const int *)(handler + 0xA0);
    dy = *(const int *)(cell + 0x28) - *(const int *)(handler + 0xA4);
    return dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1;
}

static int tes3x_mwse_ref_matches(const u8 *ref, u32 kind)
{
    const u8 *object = ref ? *(const u8 *const *)(ref + 0x28) : 0;
    u32 type = object ? *(const u32 *)(object + 4) : 0;
    if (kind == 1)
        return type == 0x5F43504E || type == 0x41455243; /* NPC_, CREA */
    if (kind == 2)
        return type == 0x54415453; /* STAT */
    return object && type != 0x5F43504E && type != 0x41455243 && type != 0x54415453;
}

static void *tes3x_mwse_scan_refs(u32 kind, void *after)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *handler = *(const u8 **)TES3X_MWSE_DATA_HANDLER;
    const u8 *records, *cells, *cell_node, *cell, *temporary;
    u8 *ref;
    u32 c, l, guard;
    int seen = after == 0;
    if (!handler || !(records = *(const u8 **)handler)
            || !(cells = *(const u8 **)(records + 0xB270)))
        return 0;
    cell_node = *(const u8 **)(cells + 4);
    for (c = 0; cell_node && c < 65535; c++, cell_node = *(const u8 **)(cell_node + 8)) {
        cell = *(const u8 *const *)cell_node;
        if (!tes3x_mwse_active_cell(handler, cell))
            continue;
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cell + lists[l] + 4);
            else if ((temporary = *(const u8 *const *)(cell + 0x10)))
                ref = *(u8 *const *)(temporary + 8 + 4);
            else
                continue;
            for (guard = 0; ref && guard < 4096; guard++, ref = *(u8 **)(ref + 0x20)) {
                if (!seen) {
                    if (ref == after)
                        seen = 1;
                    continue;
                }
                if (ref != after && tes3x_mwse_ref_matches(ref, kind))
                    return ref;
            }
        }
    }
    return 0;
}

static void *tes3x_mwse_find_reference(const char *id)
{
    const u8 *handler = *(const u8 **)TES3X_MWSE_DATA_HANDLER;
    void *records;
    if (!id)
        return 0;
    if (tes3x_mwse_same_text(id, "player") || tes3x_mwse_same_text(id, "playersavegame"))
        return tes3x_mwse_player();
    if (!handler || !(records = *(void **)handler))
        return 0;
    return ((tes3x_find_reference_fn)TES3X_MWSE_FIND_REFERENCE)(records, id);
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

static void *tes3x_mwse_resolve_object(const char *id)
{
    const u8 *handler = *(const u8 **)TES3X_MWSE_DATA_HANDLER;
    void *records;
    if (!id || !handler || !(records = *(void **)handler))
        return 0;
    return ((tes3x_resolve_object_fn)TES3X_MWSE_RESOLVE_OBJECT)(records, id);
}

static int tes3x_mwse_content_list(void)
{
    u8 *node = 0, *stack = 0, *item = 0;
    void *target;
    const char *id = 0;
    u32 id_value = 0, count = 0, type = 0, value = 0, weight = 0;
    u32 name_value = 0, next = 0, offset;
    if (TES3X_MWSE_STACK_WORDS - tes3x_mwse_sp < 7)
        return 0;
    if (tes3x_mwse_sp)
        tes3x_mwse_pop((u32 *)&node);
    if (!node) {
        target = tes3x_mwse_target();
        item = (u8 *)tes3x_mwse_template(target);
        if (item)
            node = *(u8 **)(item + 0x3C + 0x0C);
    }
    if (node && (stack = *(u8 **)(node + 8)) && (item = *(u8 **)(stack + 4))) {
        id = tes3x_mwse_id(item);
        if (id) {
            id_value = (u32)tes3x_mwse_save_string(id);
            count = *(u32 *)stack;
            type = *(u32 *)(item + 4);
            offset = tes3x_mwse_value_offset(type);
            value = offset ? *((u32 *)item + offset) : 0;
            if (type == 0x544F4C43) /* CLOT */
                value &= 0xFFFF;
            if (tes3x_mwse_is_gold(item))
                value = 1;
            offset = tes3x_mwse_weight_offset(type);
            weight = offset ? *((u32 *)item + offset) : 0;
            name_value = (u32)tes3x_mwse_save_string(tes3x_mwse_name(item));
            next = *(u32 *)(node + 4);
        }
    }
    return tes3x_mwse_push(next) && tes3x_mwse_push(name_value)
           && tes3x_mwse_push(weight) && tes3x_mwse_push(value)
           && tes3x_mwse_push(type) && tes3x_mwse_push(count)
           && tes3x_mwse_push(id_value);
}

static int tes3x_mwse_inventory_list(int next_stack)
{
    u8 *object = (u8 *)tes3x_mwse_template(tes3x_mwse_target());
    u8 *node = 0, *stack, *item;
    u32 id = (u32)"null", count = 0, next = 0, value;
    if (TES3X_MWSE_STACK_WORDS - tes3x_mwse_sp < (next_stack ? 2 : 3))
        return 0;
    if (next_stack) {
        if (!tes3x_mwse_pop(&value))
            return 0;
        node = (u8 *)value;
    } else if (object) {
        node = *(u8 **)(object + 0x3C + 0x0C);
    }
    if (node && (stack = *(u8 **)(node + 8)) && (item = *(u8 **)(stack + 4))
            && tes3x_mwse_id(item)) {
        id = (u32)tes3x_mwse_save_string(tes3x_mwse_id(item));
        count = *(u32 *)stack;
        next = *(u32 *)(node + 4);
    }
    return tes3x_mwse_push(next) && tes3x_mwse_push(count) && tes3x_mwse_push(id);
}

static int tes3x_mwse_inventory_command(void *script, u32 opcode)
{
    void *target = tes3x_mwse_target();
    u8 *object = (u8 *)tes3x_mwse_template(target);
    void *mobile = tes3x_mwse_attachment(target, 8);
    void *item, *data;
    const char *id;
    u32 id_value, count;
    if (opcode == 0x3F03) /* XContentList */
        return tes3x_mwse_content_list();
    if (opcode == 0x3C2A || opcode == 0x3C2B) /* XInventory, XNextStack */
        return tes3x_mwse_inventory_list(opcode == 0x3C2B);
    if (!tes3x_mwse_pop(&id_value)
            || !(id = tes3x_mwse_string(script, id_value))
            || !(item = tes3x_mwse_resolve_object(id)))
        return 0;
    if (opcode == 0x3C30) /* XHasItemEquipped */
        return tes3x_mwse_push(object &&
            ((tes3x_actor_equipped_fn)TES3X_MWSE_ACTOR_EQUIPPED)(object, item) != 0);
    if (opcode == 0x3F0E) { /* XEquip */
        if (!object || !mobile)
            return 0;
        data = ((tes3x_inventory_item_data_fn)TES3X_MWSE_INVENTORY_ITEM_DATA)(
            object + 0x3C, item, 0);
        ((tes3x_wear_item_fn)TES3X_MWSE_WEAR_ITEM)(mobile, item, data, 1, 0);
        return 1;
    }
    if (!tes3x_mwse_pop(&count))
        return 0;
    if (opcode == 0x3C28) { /* XAddItem */
        u8 *added = 0;
        if (!object)
            return 0;
        ((tes3x_inventory_add_fn)TES3X_MWSE_INVENTORY_ADD)(
            object + 0x3C, mobile, item, (int)count, 0, &added);
        return 1;
    }
    if (opcode == 0x3C29) { /* XRemoveItem */
        if (!object)
            return 0;
        ((tes3x_inventory_remove_fn)TES3X_MWSE_INVENTORY_REMOVE)(
            object + 0x3C, mobile, item, (int)count, 0, 1);
        return 1;
    }
    if (opcode == 0x3F0D) { /* XDrop */
        if (!mobile)
            return 0;
        ((tes3x_drop_item_fn)TES3X_MWSE_DROP_ITEM)(mobile, item, 0, (int)count, 1);
        return 1;
    }
    return 0;
}

static u8 *tes3x_mwse_actor_base(void *object)
{
    void *base = tes3x_mwse_base(object);
    return (u8 *)(base ? base : object);
}

static u32 tes3x_mwse_service_flags(void *object, int include_class)
{
    u8 *base = tes3x_mwse_actor_base(object);
    u8 *class_object;
    u32 flags = 0;
    if (!base || *(u32 *)(base + 4) != 0x5F43504E) /* NPC_ */
        return 0;
    flags = *(u32 *)(base + 0x3A * 4);
    class_object = *(u8 **)(base + 0x2D * 4);
    if (include_class && class_object && *(u32 *)(class_object + 4) == 0x53414C43) /* CLAS */
        flags |= *(u32 *)(class_object + 0x22 * 4);
    return flags;
}

static u32 tes3x_mwse_base_gold(void *object)
{
    u8 *base = tes3x_mwse_actor_base(object);
    u32 type = base ? *(u32 *)(base + 4) : 0;
    if (type == 0x5F43504E) /* NPC_ */
        return *(u32 *)(base + 0x2B * 4) & 0xFFFF;
    if (type == 0x41455243) /* CREA */
        return *(u32 *)(base + 0x36 * 4) & 0xFFFF;
    return 0;
}

static int tes3x_mwse_set_base_gold(void *object, u32 gold)
{
    u8 *base = tes3x_mwse_actor_base(object);
    u32 type = base ? *(u32 *)(base + 4) : 0;
    u32 *field;
    if (type == 0x5F43504E) /* NPC_ */
        field = (u32 *)(base + 0x2B * 4);
    else if (type == 0x41455243) /* CREA */
        field = (u32 *)(base + 0x36 * 4);
    else
        return 0;
    *field = (*field & 0xFFFF0000) | (gold & 0xFFFF);
    return 1;
}

static u32 tes3x_mwse_encumbrance(void *target)
{
    u8 *object = (u8 *)tes3x_mwse_template(target);
    u8 *node, *stack, *item;
    tes3x_mwse_float total, weight;
    u32 offset, guard;
    int count, leveled = 0;
    total.real = 0.0f;
    if (!object)
        return total.word;
    node = *(u8 **)(object + 0x3C + 0x0C);
    for (guard = 0; node && guard < 512; guard++, node = *(u8 **)(node + 4)) {
        stack = *(u8 **)(node + 8);
        item = stack ? *(u8 **)(stack + 4) : 0;
        if (!item)
            continue;
        offset = tes3x_mwse_weight_offset(*(u32 *)(item + 4));
        if (offset) {
            weight.word = *((u32 *)item + offset);
            count = *(int *)stack;
            if (count < 0)
                count = -count;
            total.real += weight.real * (float)count;
        } else if (*(u32 *)(item + 4) == 0x4956454C) { /* LEVI */
            total.real += 0.000001f;
            leveled = 1;
        }
    }
    if (leveled)
        total.real = -total.real;
    return total.word;
}

static float tes3x_mwse_sqrt(float value)
{
    __asm__ volatile("fsqrt" : "+t"(value));
    return value;
}

static float tes3x_mwse_sin(float value)
{
    __asm__ volatile("fsin" : "+t"(value));
    return value;
}

static float tes3x_mwse_cos(float value)
{
    __asm__ volatile("fcos" : "+t"(value));
    return value;
}

static float tes3x_mwse_atan(float value)
{
    float result;
    __asm__ volatile("fpatan" : "=t"(result) : "0"(1.0f), "u"(value) : "st(1)");
    return result;
}

static int tes3x_mwse_math(u32 opcode)
{
    const float pi = 3.14159265358979323846f;
    tes3x_mwse_float a, b;
    if (!tes3x_mwse_pop(&a.word))
        return 0;
    switch (opcode) {
    case 0x3830: /* XTan */
        a.real = tes3x_mwse_sin(a.real) / tes3x_mwse_cos(a.real);
        break;
    case 0x3831: /* XSin */
        a.real = tes3x_mwse_sin(a.real);
        break;
    case 0x3832: /* XCos */
        a.real = tes3x_mwse_cos(a.real);
        break;
    case 0x3833: /* XArcTan */
        a.real = tes3x_mwse_atan(a.real);
        break;
    case 0x3834: /* XArcSin */
        a.real = tes3x_mwse_atan(a.real / tes3x_mwse_sqrt(1.0f - a.real * a.real));
        break;
    case 0x3835: /* XArcCos */
        a.real = pi * 0.5f
                 - tes3x_mwse_atan(a.real / tes3x_mwse_sqrt(1.0f - a.real * a.real));
        break;
    case 0x3836: /* XDegRad */
        a.real *= pi / 180.0f;
        break;
    case 0x3837: /* XRadDeg */
        a.real *= 180.0f / pi;
        break;
    case 0x3838: /* XSqrt */
        a.real = tes3x_mwse_sqrt(a.real);
        break;
    case 0x3839: /* XHypot */
        if (!tes3x_mwse_pop(&b.word))
            return 0;
        a.real = tes3x_mwse_sqrt(a.real * a.real + b.real * b.real);
        break;
    default:
        return 0;
    }
    return tes3x_mwse_push(a.word);
}

static void *tes3x_mwse_place(const char *id)
{
    u8 *handler = *(u8 **)TES3X_MWSE_DATA_HANDLER;
    u8 *world = *(u8 **)TES3X_MWSE_WORLD;
    u8 *player = (u8 *)tes3x_mwse_player();
    u8 *list, *cell, *object, *ref, *mobs;
    float position[3], rotation[3], angle;
    u32 type, i;
    if (!handler || !player || !id
            || !(object = (u8 *)tes3x_mwse_resolve_object(id))
            || !(list = *(u8 **)(player + 0x14))
            || !(cell = *(u8 **)(list + 0x0C)))
        return 0;
    for (i = 0; i < 3; i++) {
        position[i] = ((float *)(player + 0x38))[i];
        rotation[i] = ((float *)(player + 0x2C))[i];
    }
    angle = rotation[2];
    position[0] += tes3x_mwse_sin(angle) * 256.0f;
    position[1] += tes3x_mwse_cos(angle) * 256.0f;
    ref = ((tes3x_create_reference_fn)TES3X_MWSE_CREATE_REFERENCE)(
        *(void **)handler, object, position, rotation, 0, 0);
    if (!ref)
        return 0;
    ((tes3x_cell_insert_fn)TES3X_MWSE_CELL_INSERT)(cell, ref);
    ((tes3x_attach_scene_fn)TES3X_MWSE_ATTACH_SCENE)(
        handler, ref,
        ((tes3x_cell_part_fn)TES3X_MWSE_CELL_NODE)(cell),
        ((tes3x_cell_part_fn)TES3X_MWSE_CELL_ACTIVATORS)(cell), 0);
    ((tes3x_ref_modified_fn)TES3X_MWSE_REF_MODIFIED)(ref, 1);
    ((tes3x_update_lighting_fn)TES3X_MWSE_UPDATE_LIGHTING)(handler, ref);
    type = *(u32 *)(object + 4);
    mobs = world ? *(u8 **)(world + 0x5C) : 0;
    if (mobs && (type == 0x5F43504E || type == 0x41455243))
        ((tes3x_add_mob_fn)TES3X_MWSE_ADD_MOB)(mobs, ref);
    return ref;
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
    u32 a, b, value, offset, type, count, i;
    u32 *attached, *ench;
    tes3x_mwse_float number;
    tes3x_mwse_float number2;
    void *target, *templ;

    switch (opcode) {
    case 0x3801: /* Call */
        value = *(const u32 *)operand;
        if (value > length || !tes3x_mwse_push(*(u32 *)TES3X_SCRIPT_IP))
            return 0;
        *(u32 *)TES3X_SCRIPT_IP = value;
        return 1;
    case 0x3802: /* CallShort */
        value = (u32)(s32)*(const s16 *)operand;
        if (value > length || !tes3x_mwse_push(*(u32 *)TES3X_SCRIPT_IP))
            return 0;
        *(u32 *)TES3X_SCRIPT_IP = value;
        return 1;
    case 0x3803: /* Return */
        if (!tes3x_mwse_sp || (value = tes3x_mwse_stack[tes3x_mwse_sp - 1]) > length)
            return 0;
        tes3x_mwse_sp--;
        *(u32 *)TES3X_SCRIPT_IP = value;
        return 1;
    case 0x3804: /* ReturnP */
        a = operand[0] / 4;
        if ((operand[0] & 3) || tes3x_mwse_sp < a + 1
                || (value = tes3x_mwse_stack[tes3x_mwse_sp - 1]) > length)
            return 0;
        tes3x_mwse_sp -= a + 1;
        *(u32 *)TES3X_SCRIPT_IP = value;
        return 1;
    case 0x3805: /* ReturnVP */
        b = operand[0] / 4;
        if ((operand[0] & 3) || tes3x_mwse_sp < b + 2)
            return 0;
        a = tes3x_mwse_stack[tes3x_mwse_sp - 1];
        value = tes3x_mwse_stack[tes3x_mwse_sp - 2];
        if (value > length)
            return 0;
        tes3x_mwse_sp -= b + 2;
        if (!tes3x_mwse_push(a))
            return 0;
        *(u32 *)TES3X_SCRIPT_IP = value;
        return 1;
    case 0x3806: /* CopyReg */
        a = operand[0] & 0x0F;
        b = operand[0] >> 4;
        if (a >= TES3X_MWSE_REGS || b >= TES3X_MWSE_REGS)
            return 0;
        tes3x_mwse_regs[b] = tes3x_mwse_regs[a];
        tes3x_mwse_set_flags(tes3x_mwse_regs[b]);
        return 1;
    case 0x3807: /* CopyFromStack */
        return tes3x_mwse_stack_index((s32)(s8)operand[0], &a)
               && tes3x_mwse_push(tes3x_mwse_stack[a]);
    case 0x3808: /* CopyToStack */
        if (!tes3x_mwse_stack_index((s32)(s8)operand[0], &a))
            return 0;
        tes3x_mwse_stack[a] = tes3x_mwse_stack[tes3x_mwse_sp - 1];
        return 1;
    case 0x380F: /* Pop */
        return tes3x_mwse_drop(*(const unsigned short *)operand);
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
    case 0x3815: /* DeclareLocal */
        return tes3x_mwse_declare(operand[0]);
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
    case 0x3828: /* IntToFloat */
        if (!tes3x_mwse_pop(&value))
            return 0;
        number.real = (float)(s32)value;
        return tes3x_mwse_push(number.word);
    case 0x3829: /* FloatToInt */
        if (!tes3x_mwse_pop(&number.word))
            return 0;
        return tes3x_mwse_push((u32)(s32)number.real);
    case 0x382A: case 0x382B: case 0x382C: case 0x382D:
        if (!tes3x_mwse_pop(&number.word) || !tes3x_mwse_pop(&number2.word))
            return 0;
        if (opcode == 0x382A)
            number.real += number2.real;
        else if (opcode == 0x382B)
            number.real -= number2.real;
        else if (opcode == 0x382C)
            number.real *= number2.real;
        else
            number.real /= number2.real;
        return tes3x_mwse_push(number.word);
    case 0x3830: case 0x3831: case 0x3832: case 0x3833: case 0x3834:
    case 0x3835: case 0x3836: case 0x3837: case 0x3838: case 0x3839:
        return tes3x_mwse_math(opcode);
    case 0x3C00: case 0x3C01: case 0x3C02: case 0x3C03:
        return tes3x_mwse_local(script, opcode);
    case 0x3C04: /* XAITravel */
        if (tes3x_mwse_sp < 3)
            return 0;
        for (i = 0; i < 3; i++)
            tes3x_mwse_pop(&((u32 *)TES3X_MWSE_DESTINATION)[i]);
        tes3x_mwse_native(script, 0x10F8);
        return 1;
    case 0x3C05: /* XPosition */
        if (tes3x_mwse_sp < 4)
            return 0;
        for (i = 0; i < 3; i++)
            tes3x_mwse_pop(&((u32 *)TES3X_MWSE_DESTINATION)[i]);
        ((u32 *)TES3X_MWSE_TARGET_ROT)[0] = 0;
        ((u32 *)TES3X_MWSE_TARGET_ROT)[1] = 0;
        tes3x_mwse_pop(&((u32 *)TES3X_MWSE_TARGET_ROT)[2]);
        tes3x_mwse_native(script, 0x1004);
        return 1;
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
    case 0x3C1A: /* XFirstNPC */
        tes3x_mwse_ref_kind = 1;
        return tes3x_mwse_push((u32)tes3x_mwse_scan_refs(1, 0));
    case 0x3C1B: /* XNextRef */
        if (!tes3x_mwse_pop(&value))
            return 0;
        return tes3x_mwse_push((u32)tes3x_mwse_scan_refs(
            tes3x_mwse_ref_kind, (void *)value));
    case 0x3C1E: /* XFirstItem */
        tes3x_mwse_ref_kind = 3;
        return tes3x_mwse_push((u32)tes3x_mwse_scan_refs(3, 0));
    case 0x3C1F: /* XFirstStatic */
        tes3x_mwse_ref_kind = 2;
        return tes3x_mwse_push((u32)tes3x_mwse_scan_refs(2, 0));
    case 0x3C20: /* XGetCombat */
        attached = tes3x_mwse_attachment(tes3x_mwse_target(), 8);
        target = attached ? *(void **)((u8 *)attached + 0xEC) : 0;
        return tes3x_mwse_push(target ? *(u32 *)((u8 *)target + 0x14) : 0);
    case 0x3C21: /* XStartCombat */
        if (!tes3x_mwse_pop(&value))
            return 0;
        attached = tes3x_mwse_attachment(tes3x_mwse_target(), 8);
        target = tes3x_mwse_attachment((void *)value, 8);
        if (!attached || !target)
            return 0;
        ((tes3x_start_combat_fn)TES3X_MWSE_START_COMBAT)(attached, target);
        return 1;
    case 0x3C08: /* XRefType */
        templ = tes3x_mwse_template(tes3x_mwse_target());
        return tes3x_mwse_push(templ ? *(u32 *)((u8 *)templ + 4) : 0);
    case 0x3C09: /* XLogMessage */
        if (!tes3x_mwse_string_build(script) || !tes3x_mwse_pop(&value))
            return 0;
        {
            const char *message = tes3x_mwse_string(script, value);
            if (!message)
                return 0;
            tes3x_log_raw("mwse: ", 6);
            tes3x_log_raw(message, tes3x_mwse_strlen(message));
            tes3x_log_raw("\n", 1);
            return 1;
        }
    case 0x3C10: case 0x3C11: case 0x3C12: case 0x3C13: case 0x3C14:
    case 0x3C15: case 0x3C31: case 0x3C32: case 0x3C33: case 0x3C34:
    case 0x3F08: case 0x3F09:
        return tes3x_mwse_file_command(script, opcode);
    case 0x3C1C: /* XRefID */
        templ = tes3x_mwse_template(tes3x_mwse_target());
        return tes3x_mwse_push((u32)tes3x_mwse_save_string(tes3x_mwse_id(templ)));
    case 0x3C1D: /* XGetRef */
        if (!tes3x_mwse_pop(&value))
            return 0;
        return tes3x_mwse_push((u32)tes3x_mwse_find_reference(
            tes3x_mwse_string(script, value)));
    case 0x3C22: /* XDistance */
        if (!tes3x_mwse_pop(&value) || !(target = tes3x_mwse_target()) || !value)
            return 0;
        {
            const float *from = (const float *)((u8 *)target + 0x38);
            const float *to = (const float *)((u8 *)value + 0x38);
            float dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2];
            number.real = tes3x_mwse_sqrt(dx * dx + dy * dy + dz * dz);
            return tes3x_mwse_push(number.word);
        }
    case 0x3C28: /* XAddItem */
    case 0x3C29: /* XRemoveItem */
    case 0x3C2A: /* XInventory */
    case 0x3C2B: /* XNextStack */
    case 0x3C30: /* XHasItemEquipped */
    case 0x3F03: /* XContentList */
    case 0x3F0D: /* XDrop */
    case 0x3F0E: /* XEquip */
        return tes3x_mwse_inventory_command(script, opcode);
    case 0x3C2D: /* XPCCellID */
        return tes3x_mwse_push((u32)tes3x_mwse_save_string(
            tes3x_mwse_cell_id(tes3x_mwse_player(), "Wilderness")));
    case 0x3C2C: /* XPositionCell */
        if (tes3x_mwse_sp < 5)
            return 0;
        for (i = 0; i < 3; i++)
            tes3x_mwse_pop(&((u32 *)TES3X_MWSE_DESTINATION)[i]);
        ((u32 *)TES3X_MWSE_TARGET_ROT)[0] = 0;
        ((u32 *)TES3X_MWSE_TARGET_ROT)[1] = 0;
        tes3x_mwse_pop(&((u32 *)TES3X_MWSE_TARGET_ROT)[2]);
        tes3x_mwse_pop(&value);
        {
            const char *cell = tes3x_mwse_string(script, value);
            char *destination = (char *)TES3X_MWSE_SECOND_OBJECT;
            if (!cell)
                return 0;
            for (i = 0; cell[i] && i < 255; i++)
                destination[i] = cell[i];
            destination[i] = 0;
        }
        tes3x_mwse_native(script, 0x1005);
        return 1;
    case 0x3C2E: /* XPlace */
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *id = tes3x_mwse_string(script, value);
            return id && tes3x_mwse_push((u32)tes3x_mwse_place(id));
        }
    case 0x3C2F: /* XStringCompare */
        if (!tes3x_mwse_pop(&a) || !tes3x_mwse_pop(&b))
            return 0;
        {
            const char *sa = tes3x_mwse_string(script, a);
            const char *sb = tes3x_mwse_string(script, b);
            if (!sa || !sb)
                return 0;
            return tes3x_mwse_push((u32)(s32)tes3x_mwse_strcmp(sa, sb));
        }
    case 0x3E68: /* XStringMatch */
        if (!tes3x_mwse_pop(&a) || !tes3x_mwse_pop(&b))
            return 0;
        {
            const char *string = tes3x_mwse_string(script, a);
            const char *pattern = tes3x_mwse_string(script, b);
            return string && pattern
                   && tes3x_mwse_push(tes3x_regex_match(string, pattern));
        }
    case 0x3F00: /* XKeyPressed */
        value = 0;
        if (tes3x_mwse_sp)
            tes3x_mwse_pop(&value);
        return tes3x_mwse_key_pressed(value);
    case 0x3F01: /* XTextInput */
    case 0x3F02: /* XTextInputAlt */
        return tes3x_mwse_text_input(script);
    case 0x3F04: /* XGetService */
        value = 0x0003FFFF;
        if (tes3x_mwse_sp)
            tes3x_mwse_pop(&value);
        templ = tes3x_mwse_template(tes3x_mwse_target());
        return tes3x_mwse_push(tes3x_mwse_service_flags(templ, 0)
                               & (value & 0x0003FFFF));
    case 0x3F05: /* XSetService */
    case 0x3F06: /* XModService */
        if (!tes3x_mwse_pop(&value))
            return 0;
        templ = tes3x_mwse_template(tes3x_mwse_target());
        target = tes3x_mwse_actor_base(templ);
        if (!target || *(u32 *)((u8 *)target + 4) != 0x5F43504E)
            return 0;
        attached = (u32 *)((u8 *)target + 0x3A * 4);
        if (opcode == 0x3F05)
            *attached = value & 0x0003FFFF;
        else if ((s32)value < 0)
            *attached = tes3x_mwse_service_flags(templ, 1) & ~(u32)(-(s32)value);
        else
            *attached = tes3x_mwse_service_flags(templ, 1) | (value & 0x0003FFFF);
        return 1;
    case 0x3F0A: /* XStringLength */
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *s = tes3x_mwse_string(script, value);
            return s && tes3x_mwse_push(tes3x_mwse_strlen(s));
        }
    case 0x3F0B: /* XStringBuild */
        return tes3x_mwse_string_build(script);
    case 0x3F0C: /* XStringParse */
        if (!tes3x_mwse_pop(&a))
            return 0;
        {
            const char *format = tes3x_mwse_string(script, a);
            const char *string;
            int line;
            if (!format || !tes3x_mwse_pop(&b)
                    || !(string = tes3x_mwse_string(script, b)))
                return 0;
            count = tes3x_mwse_format_count(format, &line) + 1;
            if (count > TES3X_MWSE_STACK_WORDS
                    || count > TES3X_MWSE_STACK_WORDS - tes3x_mwse_sp)
                return 0;
            tes3x_mwse_parse_text(format, string, tes3x_mwse_file_values, count);
            for (i = count; i; i--)
                if (!tes3x_mwse_push(tes3x_mwse_file_values[i - 1]))
                    return 0;
            return 1;
        }
    case 0x3F0F: /* XSetName */
        target = tes3x_mwse_target();
        templ = tes3x_mwse_template(target);
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *source = tes3x_mwse_string(script, value);
            char *name = tes3x_mwse_name(templ);
            u32 i = 0;
            if (!source || !name)
                return 0;
            while (source[i] && i < 127) {
                name[i] = source[i];
                i++;
            }
            while (i < 128)
                name[i++] = 0;
            return 1;
        }
    case 0x3F10: /* XGetSpellEffects */
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *id = tes3x_mwse_string(script, value);
            float result;
            target = id ? tes3x_mwse_resolve_object(id) : 0;
            if (!target || *(u32 *)((u8 *)target + 4) != 0x4C455053)
                return 0;
            *(void **)TES3X_MWSE_SECOND_OBJECT = target;
            result = tes3x_mwse_native(script, 0x1121);
            return tes3x_mwse_push((u32)(s32)result);
        }
    case 0x3F11: /* XSetOwner */
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *id = tes3x_mwse_string(script, value);
            attached = tes3x_mwse_attachment(tes3x_mwse_target(), 6);
            if (!id || !attached)
                return 0;
            attached[1] = (u32)tes3x_mwse_resolve_object(id);
            return 1;
        }
    case 0x3F12: /* XCast */
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *id = tes3x_mwse_string(script, value);
            void *spell = id ? tes3x_mwse_resolve_object(id) : 0;
            attached = tes3x_mwse_attachment(tes3x_mwse_target(), 8);
            if (!spell || *(u32 *)((u8 *)spell + 4) != 0x4C455053 || !attached)
                return 0;
            ((tes3x_force_cast_fn)TES3X_MWSE_FORCE_CAST)(attached, spell);
            return 1;
        }
    case 0x3F22: /* XMyCellID */
        return tes3x_mwse_push((u32)tes3x_mwse_save_string(
            tes3x_mwse_cell_id(tes3x_mwse_target(), 0)));
    case 0x3F21: /* XIsFemale */
        templ = tes3x_mwse_template(tes3x_mwse_target());
        return tes3x_mwse_push(templ && *(u32 *)((u8 *)templ + 4) == 0x5F43504E
                               ? (*((u32 *)templ + 0x0D) & 1) : 0);
    case 0x3F23: /* XGetBaseGold */
        return tes3x_mwse_push(tes3x_mwse_base_gold(
            tes3x_mwse_template(tes3x_mwse_target())));
    case 0x3F24: /* XGetGold */
        target = tes3x_mwse_target();
        attached = tes3x_mwse_attachment(target, 8);
        return tes3x_mwse_push(attached ? attached[0xD8]
                                        : tes3x_mwse_base_gold(tes3x_mwse_template(target)));
    case 0x3F25: /* XSetBaseGold */
        if (!tes3x_mwse_pop(&value))
            return 0;
        tes3x_mwse_set_base_gold(tes3x_mwse_template(tes3x_mwse_target()), value);
        return tes3x_mwse_push(value);
    case 0x3F26: /* XSetGold */
        target = tes3x_mwse_target();
        attached = tes3x_mwse_attachment(target, 8);
        if (!tes3x_mwse_pop(&value) || !attached)
            return 0;
        attached[0xD8] = value;
        return 1;
    case 0x3F31: case 0x3F32: case 0x3F33: case 0x3F34:
    case 0x3F35: case 0x3F36: case 0x3F37: case 0x3F38:
        attached = tes3x_mwse_attachment(tes3x_mwse_target(), 8);
        number.real = -1.0f;
        if (attached)
            number.word = attached[0x96 + (opcode - 0x3F31) * 3];
        return tes3x_mwse_push(number.word);
    case 0x3F3A: /* XIsTrader */
        return tes3x_mwse_push(tes3x_mwse_service_flags(
            tes3x_mwse_template(tes3x_mwse_target()), 1) & 0x000037FF);
    case 0x3F3F: /* XMemLook */
        tes3x_log_hex3("mwse.memlook", (u32)tes3x_mwse_target(),
                       (u32)tes3x_mwse_template(tes3x_mwse_target()), tes3x_mwse_sp);
        return 1;
    case 0x3F5E: /* XIsTrainer */
        return tes3x_mwse_push((tes3x_mwse_service_flags(
            tes3x_mwse_template(tes3x_mwse_target()), 1) & 0x00004000) != 0);
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
    case 0x3F62: /* XGetOwner */
        attached = tes3x_mwse_attachment(tes3x_mwse_target(), 6);
        templ = attached ? (void *)attached[1] : 0;
        return tes3x_mwse_push((u32)tes3x_mwse_save_string(
            templ ? tes3x_mwse_id(templ) : 0));
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
    case 0x3F64: /* XGetEncumb */
        return tes3x_mwse_push(tes3x_mwse_encumbrance(tes3x_mwse_target()));
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
    case 0x3F6E: /* XGetName */
        templ = tes3x_mwse_template(tes3x_mwse_target());
        return tes3x_mwse_push((u32)tes3x_mwse_save_string(tes3x_mwse_name(templ)));
    case 0x3F6F: /* XGetBaseID */
        templ = tes3x_mwse_template(tes3x_mwse_target());
        target = tes3x_mwse_base(templ);
        return tes3x_mwse_push((u32)tes3x_mwse_save_string(
            tes3x_mwse_id(target ? target : templ)));
    case 0x3F7E: /* XMessageFix */
        return tes3x_mwse_message_fix(script, *(u32 *)TES3X_SCRIPT_IP, length);
    case 0x3F7C: /* XIsProvider */
        return tes3x_mwse_push(tes3x_mwse_service_flags(
            tes3x_mwse_template(tes3x_mwse_target()), 1) & 0x00038800);
    case 0x3FA0: /* XAddSpell */
    case 0x3FA1: /* XRemoveSpell */
        if (!tes3x_mwse_pop(&value))
            return 0;
        {
            const char *id = tes3x_mwse_string(script, value);
            void *spell_list;
            attached = tes3x_mwse_attachment(tes3x_mwse_target(), 8);
            spell_list = attached
                ? ((tes3x_get_spell_list_fn)TES3X_MWSE_GET_SPELL_LIST)(attached)
                : 0;
            if (!id || !spell_list)
                return 0;
            if (opcode == 0x3FA0)
                return ((tes3x_spell_add_id_fn)TES3X_MWSE_SPELL_ADD_ID)(
                    spell_list, id) != 0;
            target = tes3x_mwse_resolve_object(id);
            if (!target || *(u32 *)((u8 *)target + 4) != 0x4C455053)
                return 0;
            ((tes3x_spell_remove_fn)TES3X_MWSE_SPELL_REMOVE)(spell_list, target);
            return 1;
        }
    default:
        break;
    }
    if ((opcode >= 0x3809 && opcode <= 0x380E)
            || (opcode >= 0x3816 && opcode <= 0x3819))
        return tes3x_mwse_jump(opcode, operand, length);
    return -1;
}

int tes3x_mwse_run(void *script, u32 opcode, u32 a2, u32 a3)
{
    int width = tes3x_mwse_operand_width(opcode);
    u32 *ip = (u32 *)TES3X_SCRIPT_IP;
    u32 length = *(u32 *)((u8 *)script + 0x3C);
    const u8 *data = *(const u8 **)((u8 *)script + 0x58);
    int handled = -1;

    tes3x_mwse_calls++;
    if (tes3x_mwse_should_log(tes3x_mwse_calls))
        tes3x_log("mwse.run_opcode", opcode);
    tes3x_mwse_native_a2 = a2;
    tes3x_mwse_native_a3 = a3;
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

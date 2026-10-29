/* Time BaseObject::loadFromFile's per-type loadObjectSpecific call, binned by vtable.
 *
 * The call-site profiler cannot time that vtable call by record type. This replaces
 * loadFromFile with the same steps around two RDTSC reads, and keeps calls and cycles per vtable
 * pointer. The profiler's dump (and every 16,384 loads) logs one vtprof.bin line per
 * vtable: the vtable VA, the calls, and the cycles in units of 1024.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_VTPROF_SET_SOURCE
#error "define TES3X_VTPROF_SET_SOURCE to BaseObject::setSourceMod"
#endif
#ifndef TES3X_VTPROF_IS_MASTER
#error "define TES3X_VTPROF_IS_MASTER to TES3File::isMaster"
#endif
#ifndef TES3X_VTPROF_SET_MASTER
#error "define TES3X_VTPROF_SET_MASTER to BaseObject::setFromMaster"
#endif
#ifndef TES3X_VTPROF_CHANGED
#error "define TES3X_VTPROF_CHANGED to the file check loadFromFile ends with"
#endif

#define VT_BINS 256u
#define VT_LOAD 4u
#define VT_MODIFIED 0x14u
#define OBJ_FLAGS 8u
#define VT_RESOLVE 0xE0u

typedef u32(__attribute__((thiscall)) *fn_load)(void *obj, void *file);
typedef void(__attribute__((thiscall)) *fn_file)(void *obj, void *arg);
typedef u32(__attribute__((thiscall)) *fn_check)(void *file);

typedef struct {
    u32 vtable, calls;
    u64 cycles;
} bin;

static bin vt_bins[VT_BINS];
static u32 vt_lost, vt_total;

void tes3x_vtprof_dump(void);

static inline u64 vt_tsc(void)
{
    u32 lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return ((u64)hi << 32) | lo;
}

static void vt_add(u32 vtable, u64 cycles)
{
    u32 i = (vtable >> 2) % VT_BINS, n;

    for (n = 0; n < VT_BINS; n++, i = (i + 1) % VT_BINS) {
        if (vt_bins[i].vtable == vtable || !vt_bins[i].vtable) {
            vt_bins[i].vtable = vtable;
            vt_bins[i].calls++;
            vt_bins[i].cycles += cycles;
            return;
        }
    }
    vt_lost++;
}

/* Replaces BaseObject::loadFromFile(object, file), which pops both arguments. */
u32 __stdcall tes3x_vtprof_load(void *object, void *file)
{
    u32 *vt = *(u32 **)object;
    u32 flags = *(u8 *)((u32)object + OBJ_FLAGS) & 1;
    u32 master;
    u64 t0;
    u32 ok;

    t0 = vt_tsc();
    ok = ((fn_load)vt[VT_LOAD / 4])(object, file) & 0xFF;
    vt_add((u32)vt, vt_tsc() - t0);
    if (!(++vt_total & 0x3FFF))
        tes3x_vtprof_dump();
    ((fn_file)TES3X_VTPROF_SET_SOURCE)(object, file);
    master = flags ? 1 : ((fn_check)TES3X_VTPROF_IS_MASTER)(file) & 0xFF;
    ((fn_file)TES3X_VTPROF_SET_MASTER)(object, (void *)master);
    if (((fn_check)TES3X_VTPROF_CHANGED)(file) & 0xFF)
        ((fn_file)(*(u32 **)object)[VT_MODIFIED / 4])(object, (void *)1);
    return ok;
}

/* The two object passes of loadAllFiles call slot 0xE0 of each object's vtable with the data
 * handler. They are binned under the vtable pointer with the pass number (1 or 2) in its low
 * bits. */
void __stdcall tes3x_vtprof_pass(void *object, void *arg, u32 pass)
{
    u32 *vt = *(u32 **)object;
    u64 t0 = vt_tsc();

    ((fn_file)vt[VT_RESOLVE / 4])(object, arg);
    vt_add((u32)vt | pass, vt_tsc() - t0);
}

/* Entered by call in place of `call [vtable+0xE0]`: the object is in ECX and the argument is on
 * the stack, which the callee pops. */
#define PASS_STUB(name, pass)                                                                    \
    __attribute__((naked)) void name(void)                                                      \
    {                                                                                            \
        __asm__ volatile("movl 4(%esp), %eax\n\t"                                               \
                         "pushl $" #pass "\n\t"                                                  \
                         "pushl %eax\n\t"                                                        \
                         "pushl %ecx\n\t"                                                        \
                         "call _tes3x_vtprof_pass@12\n\t"                                        \
                         "ret $4\n\t");                                                          \
    }

PASS_STUB(tes3x_vtprof_pass1, 1)
PASS_STUB(tes3x_vtprof_pass2, 2)

void tes3x_vtprof_dump(void)
{
    u32 i;

    for (i = 0; i < VT_BINS; i++) {
        if (vt_bins[i].vtable)
            tes3x_log_hex3("vtprof.bin", vt_bins[i].vtable, vt_bins[i].calls,
                           (u32)(vt_bins[i].cycles >> 10));
    }
    tes3x_log("vtprof.lost", vt_lost);
}

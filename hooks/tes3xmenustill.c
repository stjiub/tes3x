/* Show a still image behind the engine's main menu instead of playing the logo movie.
 *
 * The retail launcher draws its menu over a looping movie and hands the engine that frame. A
 * boot without the launcher has none, and the engine clears the colour buffer every menu frame.
 * This paints the still into both buffers when the main menu opens and drops the colour clear
 * until a game starts or loads.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_STILL_OPEN
#error "define TES3X_STILL_OPEN to LoadScreenManager::open"
#endif
#ifndef TES3X_STILL_CLEAR
#error "define TES3X_STILL_CLEAR to NiXBoxRenderer::clear"
#endif
#ifndef TES3X_STILL_DISPLAY
#error "define TES3X_STILL_DISPLAY to NiXBoxRenderer::display"
#endif

#ifndef TES3X_STILL_FENCE
#error "define TES3X_STILL_FENCE to D3D_SetFence"
#endif
#ifndef TES3X_STILL_BLOCK
#error "define TES3X_STILL_BLOCK to D3D_BlockOnTime"
#endif


#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)

#define STILL_PATH "D:\\Data Files\\Textures\\tes3x_menu_still.raw"
#define STILL_W 640
#define STILL_H 480
#define STILL_ROW (STILL_W * 4)

/* NiXBoxRenderer: +0x58 holds the target size (dwords 0 and 1) and the colour surface (dword 5).
 * The surface's Data (dword 1) is the physical address of the current back buffer, which flips
 * on each display; dword 4 packs the pitch / 64 - 1 into its top byte.  The format is X8R8G8B8. */
#define RENDERER_TARGET 0x58
#define TARGET_WIDTH 0
#define TARGET_HEIGHT 1
#define TARGET_COLOUR 5
#define SURFACE_DATA 1
#define SURFACE_SIZE 4

typedef void(__attribute__((thiscall)) *fn_display)(void *renderer);
typedef u32(__stdcall *fn_set_fence)(u32 flags);
typedef void(__stdcall *fn_block_on)(u32 fence, u32 flag);

u32 tes3x_still_active;
u32 tes3x_still_dirty; /* frames still to repaint, one for each buffer */
void *tes3x_still_renderer;

static u32 src_row[STILL_W];

/* D3D queues clears and draws; the GPU would run the ones already queued over what is painted. */
static void gpu_idle(void)
{
    u32 fence = ((fn_set_fence)TES3X_STILL_FENCE)(0);

    ((fn_block_on)TES3X_STILL_BLOCK)(fence, 1);
}

static u32 *back_buffer(u32 *surface)
{
    return (u32 *)(surface[SURFACE_DATA] | 0x80000000u);
}

static u32 read_row(void *file, u32 y)
{
    IO_STATUS_BLOCK iosb;
    u64 offset = (u64)y * STILL_ROW;

    iosb.Information = 0;
    if (NtReadFile(file, 0, 0, 0, &iosb, src_row, STILL_ROW, &offset) != 0)
        return 0;
    return iosb.Information == STILL_ROW;
}

/* Fit the image to the target's height and centre it, black beside it; nearest neighbour. */
static u32 paint(u32 *dst, u32 pitch, u32 width, u32 height, void *file)
{
    u32 dw = height * STILL_W / STILL_H, dh = height, x, y, last = (u32)-1, left;
    u32 *row;

    if (dw > width) {
        dw = width;
        dh = width * STILL_H / STILL_W;
    }
    left = (width - dw) / 2;
    for (y = 0; y < height; y++) {
        row = (u32 *)((char *)dst + y * pitch);
        for (x = 0; x < width; x++)
            row[x] = 0xFF000000u;
    }
    for (y = 0; y < dh; y++) {
        u32 sy = y * STILL_H / dh;

        row = (u32 *)((char *)dst + ((height - dh) / 2 + y) * pitch);
        if (sy != last) {
            if (!read_row(file, sy))
                return 0;
            last = sy;
        }
        for (x = 0; x < dw; x++)
            row[left + x] = src_row[x * STILL_W / dw] | 0xFF000000u;
    }
    return 1;
}

/* The still into the buffer the next frame draws in, behind what a menu hid. */
void tes3x_still_redraw(void)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    static char path[] = STILL_PATH;
    void *file = 0;
    u32 *target, *surface, pitch;

    if (!tes3x_still_renderer || !tes3x_still_dirty)
        return;
    tes3x_still_dirty--;
    target = *(u32 **)((char *)tes3x_still_renderer + RENDERER_TARGET);
    surface = (u32 *)target[TARGET_COLOUR];
    pitch = (((surface[SURFACE_SIZE] >> 24) & 0xFF) + 1) * 64;
    tes3x_dos_attributes(&oa, &name, path);
    if (NtCreateFile(&file, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                     FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return;
    gpu_idle();
    paint(back_buffer(surface), pitch, target[TARGET_WIDTH], target[TARGET_HEIGHT], file);
    NtClose(file);
}

/* A menu showed or hid something: with no colour clear, what it hid stays on screen. */
void tes3x_still_refresh(void)
{
    if (tes3x_still_active)
        tes3x_still_dirty = 3;
}

/* Returns 1 when the still is up, 0 to let the movie play. */
u32 tes3x_still_paint(void)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    static char path[] = STILL_PATH;
    void *file = 0;
    u32 *target, *surface, *first, *second, pitch, width, height, ok, i, status;

    if (!tes3x_still_renderer)
        return 0;
    target = *(u32 **)((char *)tes3x_still_renderer + RENDERER_TARGET);
    surface = (u32 *)target[TARGET_COLOUR];
    width = target[TARGET_WIDTH];
    height = target[TARGET_HEIGHT];
    pitch = (((surface[SURFACE_SIZE] >> 24) & 0xFF) + 1) * 64;
    tes3x_dos_attributes(&oa, &name, path);
    status = NtCreateFile(&file, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                          FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT);
    if (status != 0) {
        tes3x_log("still.open_fail", status);
        return 0;
    }
    gpu_idle();
    first = back_buffer(surface);
    ok = paint(first, pitch, width, height, file);
    NtClose(file);
    if (!ok) {
        tes3x_log("still.read_fail", 0);
        return 0;
    }
    ((fn_display)TES3X_STILL_DISPLAY)(tes3x_still_renderer);
    second = back_buffer(surface);
    gpu_idle();
    if (second != first)
        for (i = 0; i < pitch * height / 4; i++)
            second[i] = first[i];
    ((fn_display)TES3X_STILL_DISPLAY)(tes3x_still_renderer);
    tes3x_still_active = 1;
    tes3x_log_hex3("still.painted", width, height, pitch);
    return 1;
}

/* Replaces MenuOptions::open's call to LoadScreenManager::open (thiscall, seven stack arguments).
 * Without a still it plays the movie as before. */
__attribute__((naked)) void tes3x_still_open_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "call _tes3x_still_paint\n\t"
        "movl %eax, 28(%esp)\n\t"
        "popal\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "ret $0x1c\n"
        "1:\n\t"
        "pushl $" TES3X_SYM(STILL_OPEN) "\n\t"
        "ret\n\t");
}

/* Replaces the other callers of LoadScreenManager::open: a game is starting or loading. */
void tes3x_still_end(void)
{
    if (tes3x_still_active)
        tes3x_log("still.end", 0);
    tes3x_still_active = 0;
}

__attribute__((naked)) void tes3x_still_end_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "call _tes3x_still_end\n\t"
        "popal\n\t"
        "pushl $" TES3X_SYM(STILL_OPEN) "\n\t"
        "ret\n\t");
}

/* Replaces NiXBoxRenderer::clear in the renderer's vtable (thiscall, flags last): remembers the
 * renderer and keeps the colour buffer while the still is up. */
__attribute__((naked)) void tes3x_still_clear_hook(void)
{
    __asm__ volatile(
        "movl %ecx, _tes3x_still_renderer\n\t"
        "cmpl $0, _tes3x_still_active\n\t"
        "je 1f\n\t"
        "cmpl $0, _tes3x_still_dirty\n\t"
        "je 2f\n\t"
        "testb $1, 8(%esp)\n\t"
        "je 2f\n\t"
        "pushal\n\t"
        "call _tes3x_still_redraw\n\t"
        "popal\n"
        "2:\n\t"
        "andl $0xfffffffe, 8(%esp)\n"
        "1:\n\t"
        "pushl $" TES3X_SYM(STILL_CLEAR) "\n\t"
        "ret\n\t");
}

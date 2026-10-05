/* Render at 1280x720 and output 720p when the dashboard and AV pack allow it.  Retail always
 * creates its renderer at 640x480, which D3D outputs as 480p or 480i.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_VIDEO_RENDERER_CREATE
#error "define TES3X_VIDEO_RENDERER_CREATE to NiXBoxRenderer::create"
#endif
#ifndef TES3X_VIDEO_CREATE_DEVICE
#error "define TES3X_VIDEO_CREATE_DEVICE to Direct3D_CreateDevice"
#endif
#ifndef TES3X_VIDEO_MODE_COUNT
#error "define TES3X_VIDEO_MODE_COUNT to Direct3D_GetAdapterModeCount"
#endif
#ifndef TES3X_VIDEO_ENUM_MODES
#error "define TES3X_VIDEO_ENUM_MODES to Direct3D_EnumAdapterModes"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define KFN(slot, type) (*(type *)(slot))

typedef long(__stdcall *fn_AvSendTVEncoderOption)(void *, u32, u32, u32 *);
typedef u32(__cdecl *fn_mode_count)(void);
typedef long(__stdcall *fn_enum_modes)(u32, u32 *);

#define AV_QUERY_ENCODER_SETTINGS 6

#define HD_WIDTH 1280
#define HD_HEIGHT 720

/* Game renderer settings, D3DPRESENT_PARAMETERS and D3DDISPLAYMODE, as dword indices */
#define SETTINGS_WIDTH 2
#define SETTINGS_HEIGHT 3
#define PP_WIDTH 0
#define PP_HEIGHT 1
#define PP_FLAGS 10
#define MODE_WIDTH 0
#define MODE_HEIGHT 1
#define MODE_FLAGS 3

#define PRESENT_LOCKABLE 0x01
#define PRESENT_PROGRESSIVE 0x40

static u32 hd_flags;

/* D3D lists only the modes the encoder offers, so 720p appears only on an HD AV pack with the
 * dashboard's 720p setting on.
 */
static u32 find_hd_mode(void)
{
    fn_mode_count count_modes = (fn_mode_count)TES3X_VIDEO_MODE_COUNT;
    fn_enum_modes enum_modes = (fn_enum_modes)TES3X_VIDEO_ENUM_MODES;
    u32 mode[5], count, i;

    count = count_modes();
    for (i = 0; i < count; i++) {
        if (enum_modes(i, mode) != 0)
            continue;
        if (mode[MODE_WIDTH] == HD_WIDTH && mode[MODE_HEIGHT] == HD_HEIGHT &&
            (mode[MODE_FLAGS] & PRESENT_PROGRESSIVE))
            return mode[MODE_FLAGS];
    }
    return 0;
}

void __stdcall tes3x_video_settings(u32 *settings)
{
    u32 encoder = 0;

    KFN(THUNK_AvSendTVEncoderOption, fn_AvSendTVEncoderOption)(0, AV_QUERY_ENCODER_SETTINGS, 0,
                                                                &encoder);
    hd_flags = find_hd_mode();
    tes3x_log_hex3("video.encoder", encoder, hd_flags, 0);
    if (hd_flags) {
        settings[SETTINGS_WIDTH] = HD_WIDTH;
        settings[SETTINGS_HEIGHT] = HD_HEIGHT;
    }
}

void __stdcall tes3x_video_present(u32 *pp)
{
    if (hd_flags && pp[PP_WIDTH] == HD_WIDTH && pp[PP_HEIGHT] == HD_HEIGHT)
        pp[PP_FLAGS] = (pp[PP_FLAGS] & PRESENT_LOCKABLE) | hd_flags;
    tes3x_log_hex3("video.mode", pp[PP_WIDTH], pp[PP_HEIGHT], pp[PP_FLAGS]);
}

/* Replaces Game::createRenderer's call to NiXBoxRenderer::create (cdecl, width and height
 * first), with the settings in esi.  The call clobbers eax, ecx and edx anyway.
 */
__attribute__((naked)) void tes3x_video_renderer_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl %esi\n\t"
        "call _tes3x_video_settings@4\n\t"
        "popal\n\t"
        "movl 8(%esi), %eax\n\t"
        "movl %eax, 4(%esp)\n\t"
        "movl 12(%esi), %eax\n\t"
        "movl %eax, 8(%esp)\n\t"
        "pushl $" TES3X_STR(TES3X_VIDEO_RENDERER_CREATE) "\n\t"
        "ret\n\t");
}

/* Replaces the call to Direct3D_CreateDevice, whose LTCG form takes the present parameters on
 * the stack and the device slot and behaviour flags in ecx and eax.
 */
__attribute__((naked)) void tes3x_video_create_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 36(%esp)\n\t"
        "call _tes3x_video_present@4\n\t"
        "popal\n\t"
        "pushl $" TES3X_STR(TES3X_VIDEO_CREATE_DEVICE) "\n\t"
        "ret\n\t");
}

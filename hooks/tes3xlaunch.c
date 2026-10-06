/* Cross-folder launch. The kernel reads "folder;file" from the persisted launch data page and
 * maps D: to the folder. XGetLaunchInfo may have freed the old page, so a new one is taken. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlaunch.h"

#define MmAllocateContiguousMemory \
    KFN(THUNK_MmAllocateContiguousMemory, fn_MmAllocateContiguousMemory)
typedef void *(__stdcall *fn_MmAllocateContiguousMemory)(u32 bytes);
#define MmPersistContiguousMemory \
    KFN(THUNK_MmPersistContiguousMemory, fn_MmPersistContiguousMemory)
typedef void(__stdcall *fn_MmPersistContiguousMemory)(void *base, u32 bytes, int persist);
#define HalReturnToFirmware KFN(THUNK_HalReturnToFirmware, fn_HalReturnToFirmware)
typedef void(__stdcall *fn_HalReturnToFirmware)(u32 routine);

#define LAUNCH_PAGE 0x1000
#define LAUNCH_PATH 8
#define LAUNCH_PATH_MAX 520
#define LAUNCH_DATA 0x400 /* the title's own data, after the header */
#define LDT_TITLE 0
#define XBE_CERT_PTR 0x00010118
#define HAL_QUICK_REBOOT_ROUTINE 2

void tes3x_launch(const char *path)
{
    tes3x_launch_data(path, 0, 0);
}

void tes3x_launch_data(const char *path, const void *data, u32 size)
{
    static const char letters[] = "CEFG", partitions[] = "2167";
    static const char prefix[] = "\\Device\\Harddisk0\\Partition";
    void **page_var = *(void ***)THUNK_LaunchDataPage;
    unsigned char *page = MmAllocateContiguousMemory(LAUNCH_PAGE), *out;
    u32 i, n = 0, slash = 0;

    if (!page) {
        tes3x_log("launch.no_page", 0);
        return;
    }
    for (i = 0; i < LAUNCH_PAGE; i++)
        page[i] = 0;
    for (i = 0; i < size && i < LAUNCH_PAGE - LAUNCH_DATA; i++)
        page[LAUNCH_DATA + i] = ((const unsigned char *)data)[i];
    /* the running title's ID: the engine accepts only its own, and the manager takes any */
    ((u32 *)page)[0] = LDT_TITLE;
    ((u32 *)page)[1] = *(u32 *)(*(u32 *)XBE_CERT_PTR + 8);
    out = page + LAUNCH_PATH;
    if (path[0] && path[1] == ':') {
        for (i = 0; letters[i] && letters[i] != (path[0] & ~0x20); i++)
            ;
        if (!letters[i]) {
            tes3x_log("launch.bad_drive", (u8)path[0]);
            return;
        }
        for (; prefix[n]; n++)
            out[n] = (u8)prefix[n];
        out[n++] = (u8)partitions[i];
        path += 2;
    }
    for (i = 0; path[i] && n < LAUNCH_PATH_MAX - 1; i++, n++)
        if ((out[n] = (u8)(path[i] == '/' ? '\\' : path[i])) == '\\')
            slash = n;
    if (!slash) {
        tes3x_log("launch.bad_path", n);
        return;
    }
    out[slash] = ';';
    MmPersistContiguousMemory(page, LAUNCH_PAGE, 1);
    *page_var = page;
    tes3x_log("launch", n);
    HalReturnToFirmware(HAL_QUICK_REBOOT_ROUTINE);
}

/* Cross-folder launch probe: logs the launch data it was started with to E:\tes3xmgr.txt, then
 * starts TARGET with the engine's New Game launch data and JOIN_MAGIC naming SERVER. On its
 * VISITS-th start it turns the console off instead, which ends an unattended run. */

#include <hal/xbox.h>
#include <nxdk/mount.h>
#include <stdio.h>
#include <string.h>
#include <xboxkrnl/xboxkrnl.h>

#ifndef TARGET
#define TARGET "\\Device\\CdRom0\\default.xbe"
#endif
#ifndef SERVER
#define SERVER "10.0.2.2"
#endif
#ifndef TARGET_TITLE
#define TARGET_TITLE 0x42530005u
#endif
#ifndef VISITS
#define VISITS 2
#endif

#define LOG_PATH "E:\\tes3xmgr.txt"
#define LAUNCH_PAGE 0x1000

/* The engine's relaunch data (tes3xmulti.c): magic, pad port, -, mode, save path, then a join. */
#define BXWM_MAGIC 0x4D575842u
#define BXWM_NEW_GAME 0u
#define JOIN_MAGIC 0x4A4D3354u
#define JOIN_AT 0x110u

static FILE *out;

static int visits(void)
{
    char line[160];
    int n = 0;
    FILE *f = fopen(LOG_PATH, "r");

    if (!f)
        return 0;
    while (fgets(line, sizeof(line), f))
        n += strncmp(line, "visit ", 6) == 0;
    fclose(f);
    return n;
}

static void log_page(const LAUNCH_DATA_PAGE *page)
{
    const unsigned char *d;
    int i;

    fprintf(out, "image %.*s\n", XeImageFileName->Length, XeImageFileName->Buffer);
    if (!page) {
        fprintf(out, "page none\n");
        return;
    }
    fprintf(out, "page type %lu title %08lX flags %08lX path '%.520s'\n",
            page->Header.dwLaunchDataType, page->Header.dwTitleId, page->Header.dwFlags,
            page->Header.szLaunchPath);
    d = page->LaunchData;
    fprintf(out, "data");
    for (i = 0; i < 32; i++)
        fprintf(out, " %02X", d[i]);
    fprintf(out, "\n");
}

static void launch(const char *path, const unsigned char *data, unsigned n)
{
    PLAUNCH_DATA_PAGE page = LaunchDataPage;
    char *slash;

    if (!page && !(page = MmAllocateContiguousMemory(LAUNCH_PAGE)))
        return;
    LaunchDataPage = page;
    MmPersistContiguousMemory(page, LAUNCH_PAGE, TRUE);
    memset(page, 0, LAUNCH_PAGE);
    page->Header.dwLaunchDataType = LDT_TITLE;
    /* XAPI's XGetLaunchInfo hands a title only data carrying its own title ID */
    page->Header.dwTitleId = TARGET_TITLE;
    strncpy(page->Header.szLaunchPath, path, sizeof(page->Header.szLaunchPath) - 1);
    /* the kernel takes "folder;file" and maps D: to the folder */
    if ((slash = strrchr(page->Header.szLaunchPath, '\\')))
        *slash = ';';
    memcpy(page->LaunchData, data, n);
    HalReturnToFirmware(HalQuickRebootRoutine);
}

int main(void)
{
    static unsigned char data[0xC00];
    int n;

    nxMountDrive('E', "\\Device\\Harddisk0\\Partition1\\");
    n = visits() + 1;
    out = fopen(LOG_PATH, "a");
    if (!out)
        return 1;
    fprintf(out, "visit %d\n", n);
    log_page(LaunchDataPage);
    if (n >= VISITS) {
        fprintf(out, "shutdown\n");
        fclose(out);
        HalInitiateShutdown();
        return 0;
    }
    ((unsigned *)data)[0] = BXWM_MAGIC;
    ((unsigned *)data)[3] = BXWM_NEW_GAME;
    *(unsigned *)(data + JOIN_AT) = JOIN_MAGIC;
    strcpy((char *)data + JOIN_AT + 4, SERVER);
    fprintf(out, "launch %s join %s\n", TARGET, SERVER);
    fclose(out);
    launch(TARGET, data, sizeof(data));
    return 1;
}

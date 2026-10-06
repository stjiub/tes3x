/* The manager folder's default.xbe: starts the manager in slot a or b as D:\slots.ini says. A
 * pending slot, a self-update not yet confirmed, is tried twice; after that the launcher goes back
 * to the active slot and names the failed one, which the manager reports. Kept small and rarely
 * changed, since it is the one file an update cannot roll back. */

#include <hal/xbox.h>
#include <nxdk/mount.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#define LOG_PATH "E:\\tes3xmgr.txt"
#define SLOTS "D:\\slots.ini"
#define MAX_TRIES 2
#define LAUNCH_PAGE 0x1000

static void say(const char *format, ...)
{
    va_list args;
    FILE *f = fopen(LOG_PATH, "a");

    if (!f)
        return;
    fputs("launcher: ", f);
    va_start(args, format);
    vfprintf(f, format, args);
    va_end(args);
    fclose(f);
}

static int get(const char *text, const char *key, char *out, size_t n)
{
    size_t k = strlen(key), len;
    const char *line;

    for (line = text; *line; line += strcspn(line, "\n"), line += *line == '\n') {
        if (strncmp(line, key, k) || line[k] != '=')
            continue;
        len = strcspn(line + k + 1, "\r\n");
        if (len >= n)
            return 0;
        memcpy(out, line + k + 1, len);
        out[len] = 0;
        return (int)len;
    }
    return 0;
}

static int is_slot(const char *s)
{
    return (s[0] == 'a' || s[0] == 'b') && !s[1];
}

static int slot_present(const char *slot)
{
    char path[32];

    snprintf(path, sizeof(path), "D:\\%s\\default.xbe", slot);
    return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
}

/* Flushed: the kernel caches writes, and a count of tries lost to a power cut would retry a
 * broken update forever. */
static int flush(const char *path, const char *text)
{
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL, text ? CREATE_ALWAYS : OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    IO_STATUS_BLOCK io;
    DWORD put = 0;
    int ok;

    if (h == INVALID_HANDLE_VALUE)
        return 0;
    ok = (!text || (WriteFile(h, text, (DWORD)strlen(text), &put, NULL) && put == strlen(text)))
         && NT_SUCCESS(NtFlushBuffersFile(h, &io));
    CloseHandle(h);
    return ok;
}

static void write_slots(const char *active, const char *pending, int tries, const char *failed)
{
    char text[96];

    snprintf(text, sizeof(text), "active=%s\r\npending=%s\r\ntries=%d\r\nfailed=%s\r\n", active,
             pending, tries, failed);
    if (!flush(SLOTS ".new", text))
        return;
    DeleteFileA(SLOTS);
    if (MoveFileA(SLOTS ".new", SLOTS))
        flush(SLOTS, NULL);
}

static unsigned title_id(const char *slot)
{
    char path[32];
    unsigned char head[0x200];
    unsigned base, cert, id = 0;
    FILE *f;

    snprintf(path, sizeof(path), "D:\\%s\\default.xbe", slot);
    if (!(f = fopen(path, "rb")))
        return 0;
    if (fread(head, 1, sizeof(head), f) == sizeof(head) && !memcmp(head, "XBEH", 4)) {
        memcpy(&base, head + 0x104, 4);
        memcpy(&cert, head + 0x118, 4);
        if (!fseek(f, (long)(cert - base + 8), SEEK_SET) && fread(&id, 4, 1, f) != 1)
            id = 0;
    }
    fclose(f);
    return id;
}

static void launch(const char *slot)
{
    PLAUNCH_DATA_PAGE page = LaunchDataPage;
    static BYTE data[sizeof(page->LaunchData)];
    char folder[MAX_PATH];
    /* what the title that started the manager handed it, such as the game's stale-build note */
    int pass = page && page->Header.dwLaunchDataType == LDT_TITLE;
    size_t len = XeImageFileName->Length;
    char *slash;

    if (len >= sizeof(folder))
        return;
    memcpy(folder, XeImageFileName->Buffer, len);
    folder[len] = 0;
    if (!(slash = strrchr(folder, '\\')))
        return;
    *slash = 0;
    if (pass)
        memcpy(data, page->LaunchData, sizeof(data));
    if (!page && !(page = MmAllocateContiguousMemory(LAUNCH_PAGE)))
        return;
    LaunchDataPage = page;
    MmPersistContiguousMemory(page, LAUNCH_PAGE, TRUE);
    memset(page, 0, LAUNCH_PAGE);
    if (pass)
        memcpy(page->LaunchData, data, sizeof(data));
    page->Header.dwLaunchDataType = LDT_TITLE;
    page->Header.dwTitleId = title_id(slot);
    snprintf(page->Header.szLaunchPath, sizeof(page->Header.szLaunchPath), "%s\\%s;default.xbe",
             folder, slot);
    say("start %s\n", page->Header.szLaunchPath);
    HalReturnToFirmware(HalQuickRebootRoutine);
}

int main(void)
{
    char text[256] = "", active[4] = "a", pending[4] = "", failed[4] = "", tries[8] = "0";
    const char *slot;
    FILE *f;
    size_t n;
    int count;

    nxMountDrive('E', "\\Device\\Harddisk0\\Partition1\\");
    /* D: is the launch folder already; mapped here anyway so slots never depend on the caller */
    if (XeImageFileName->Length < sizeof(text)) {
        memcpy(text, XeImageFileName->Buffer, XeImageFileName->Length);
        text[XeImageFileName->Length] = 0;
        if (strrchr(text, '\\'))
            strrchr(text, '\\')[1] = 0;
        if (nxIsDriveMounted('D'))
            nxUnmountDrive('D');
        nxMountDrive('D', text);
        text[0] = 0;
    }
    if ((f = fopen(SLOTS, "rb"))) {
        n = fread(text, 1, sizeof(text) - 1, f);
        text[n] = 0;
        fclose(f);
        get(text, "active", active, sizeof(active));
        get(text, "pending", pending, sizeof(pending));
        get(text, "failed", failed, sizeof(failed));
        get(text, "tries", tries, sizeof(tries));
    }
    count = atoi(tries);
    slot = is_slot(active) ? active : "a";
    if (is_slot(pending) && slot_present(pending)) {
        if (count < MAX_TRIES) {
            write_slots(slot, pending, count + 1, failed);
            say("pending slot %s, try %d\n", pending, count + 1);
            slot = pending;
        } else {
            write_slots(slot, "", 0, pending);
            say("slot %s did not start %d times; back to %s\n", pending, count, slot);
        }
    }
    if (!slot_present(slot))
        slot = slot[0] == 'a' ? "b" : "a";
    launch(slot);
    say("no manager to start\n");
    Sleep(3000);
    return 1;
}

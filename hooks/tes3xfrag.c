/* Measure FATX allocation hints and overwrite fragmentation.
 * Writes mirror CopyFileEx; extents are counted offline.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtSetInformationFile KFN(THUNK_NtSetInformationFile, fn_NtSetInformationFile)
#define NtFlushBuffersFile KFN(THUNK_NtFlushBuffersFile, fn_NtFlushBuffersFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define COPY_CHUNK 0x10000u
#define CREATE_OPTIONS 0x64u
#define CLUSTER 16384u

#define ROUNDS 12
#define ESS_BYTES 1540096u /* ~1.47 MB, just under the console's measured saves */
#define VV_BYTES 4096u
#define IMG_BYTES 20480u

static char path_hint[] = "\\Device\\Harddisk0\\Partition1\\tes3xf_hint.bin";
static char path_nohint[] = "\\Device\\Harddisk0\\Partition1\\tes3xf_nohint.bin";
static char path_ess[] = "\\Device\\Harddisk0\\Partition1\\tes3xf_cycle.ess";
static char path_vv[] = "\\Device\\Harddisk0\\Partition1\\tes3xf_cycle_vv.dat";
static char path_img[] = "\\Device\\Harddisk0\\Partition1\\tes3xf_cycle_img.xbx";

static void *copy_buf;

/* CopyFileEx-shaped replacement with an optional AllocationSize hint. */
static u32 write_file(char *path, u32 nbytes, int hint)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 alloc = nbytes;
    u64 eof = nbytes;
    void *h = 0;
    u32 status;
    u32 left;

    tes3x_object_attributes(&oa, &name, path);
    status = NtCreateFile(&h, GENERIC_WRITE | SYNCHRONIZE, &oa, &iosb,
                          hint ? &alloc : 0, FILE_ATTRIBUTE_NORMAL, 0,
                          FILE_OVERWRITE_IF, CREATE_OPTIONS);
    if (status != 0)
        return status;

    if (hint)
        NtSetInformationFile(h, &iosb, &eof, sizeof(eof), FileEndOfFileInformation);

    for (left = nbytes; left; ) {
        u32 n = left > COPY_CHUNK ? COPY_CHUNK : left;
        status = NtWriteFile(h, 0, 0, 0, &iosb, copy_buf, n, 0);
        if (status != 0)
            break;
        left -= n;
    }

    NtFlushBuffersFile(h, &iosb);
    NtClose(h);
    return status;
}

void tes3x_frag_probe(void)
{
    u32 i;

    copy_buf = MmAllocateSystemMemory(COPY_CHUNK, PAGE_READWRITE);
    if (!copy_buf) {
        tes3x_log("frag.buf_fail", 0);
        return;
    }
    for (i = 0; i < COPY_CHUNK; i++)
        ((u8 *)copy_buf)[i] = (u8)i;

    tes3x_log("frag.hint", write_file(path_hint, ESS_BYTES, 1));
    tes3x_log("frag.nohint", write_file(path_nohint, ESS_BYTES, 0));

    /* Grow the .ess each round to prevent exact chain reuse. */
    for (i = 0; i < ROUNDS; i++) {
        u32 status = write_file(path_vv, VV_BYTES, 1);
        if (!status)
            status = write_file(path_img, IMG_BYTES, 1);
        if (!status)
            status = write_file(path_ess, ESS_BYTES + i * CLUSTER, 1);
        if (status) {
            tes3x_log("frag.cycle_fail", status);
            break;
        }
    }
    tes3x_log("frag.rounds", i);

    MmFreeSystemMemory(copy_buf, COPY_CHUNK);
    tes3x_log("frag.done", 0);
}

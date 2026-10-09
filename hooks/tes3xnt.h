#ifndef TES3X_NT_H
#define TES3X_NT_H

#include "tes3xlog.h"

typedef struct {
    unsigned short Length;
    unsigned short MaximumLength;
    char *Buffer;
} ANSI_STRING;

/* Xbox OBJECT_ATTRIBUTES has no Length field, unlike desktop NT. */
typedef struct {
    void *RootDirectory;
    ANSI_STRING *ObjectName;
    u32 Attributes;
} OBJECT_ATTRIBUTES;

typedef struct {
    u32 Status;
    u32 Information;
} IO_STATUS_BLOCK;

typedef u32(__stdcall *fn_NtCreateFile)(void **, u32, OBJECT_ATTRIBUTES *, IO_STATUS_BLOCK *,
                                        u64 *, u32, u32, u32, u32);
typedef u32(__stdcall *fn_NtWriteFile)(void *, void *, void *, void *, IO_STATUS_BLOCK *,
                                       const void *, u32, u64 *);
typedef u32(__stdcall *fn_NtReadFile)(void *, void *, void *, void *, IO_STATUS_BLOCK *,
                                      void *, u32, u64 *);
typedef u32(__stdcall *fn_NtQueryInformationFile)(void *, IO_STATUS_BLOCK *, void *, u32, u32);
typedef u32(__stdcall *fn_NtSetInformationFile)(void *, IO_STATUS_BLOCK *, void *, u32, u32);
typedef u32(__stdcall *fn_NtFlushBuffersFile)(void *, IO_STATUS_BLOCK *);
typedef u32(__stdcall *fn_NtClose)(void *);
typedef void *(__stdcall *fn_MmAllocateSystemMemory)(u32, u32);
typedef void(__stdcall *fn_MmFreeSystemMemory)(void *, u32);
typedef void(__stdcall *fn_KeQuerySystemTime)(u64 *);

#define GENERIC_READ 0x80000000u
#define GENERIC_WRITE 0x40000000u
#define SYNCHRONIZE 0x00100000u
#define FILE_APPEND_DATA 0x0004u
#define FILE_ATTRIBUTE_NORMAL 0x80u
#define FILE_SHARE_READ 0x01u
#define FILE_SHARE_WRITE 0x02u
#define FILE_OPEN 1u
#define FILE_OPEN_IF 3u
#define FILE_OVERWRITE_IF 5u
#define FILE_SYNCHRONOUS_IO_NONALERT 0x20u
#define OBJ_CASE_INSENSITIVE 0x40u
#define PAGE_READWRITE 0x04u
#define OB_DOS_DEVICES ((void *)-3) /* ObpDosDevicesDirectoryObject, as XAPI uses it */
#define FileEndOfFileInformation 20u
#define FileDispositionInformation 13u
#define FileNetworkOpenInformation 34u
#define FILE_WRITE_TO_END_OF_FILE 0xFFFFFFFFFFFFFFFFull

/* FATX rejects FileStandardInformation with STATUS_INVALID_PARAMETER; XAPI sizes files this way. */
typedef struct {
    u64 CreationTime;
    u64 LastAccessTime;
    u64 LastWriteTime;
    u64 ChangeTime;
    u64 AllocationSize;
    u64 EndOfFile;
    u32 FileAttributes;
    u32 Pad;
} FILE_NETWORK_OPEN_INFORMATION;

/* A thunk slot holds the resolved function pointer once the kernel has fixed up imports. */
#define KFN(slot, type) (*(type *)(slot))

static inline u32 tes3x_strlen(const char *s)
{
    u32 n = 0;
    while (s[n])
        n++;
    return n;
}

/* RootDirectory NULL means the name is an absolute object-manager path, so only
 * \Device\... form resolves. */
static inline void tes3x_object_attributes(OBJECT_ATTRIBUTES *oa, ANSI_STRING *name, char *path)
{
    name->Buffer = path;
    name->Length = (unsigned short)tes3x_strlen(path);
    name->MaximumLength = name->Length;
    oa->RootDirectory = 0;
    oa->ObjectName = name;
    oa->Attributes = OBJ_CASE_INSENSITIVE;
}

/* Drive-letter paths resolve relative to the DOS devices directory. */
static inline void tes3x_dos_attributes(OBJECT_ATTRIBUTES *oa, ANSI_STRING *name, char *path)
{
    tes3x_object_attributes(oa, name, path);
    oa->RootDirectory = OB_DOS_DEVICES;
}

#endif

/* tes3x-cli.exe: run the tes3x command in this console with the Python beside it, or with the one
 * on PATH, in the current folder; its exit code is the command's. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlwapi.h>
#include <stdio.h>
#include <wchar.h>

/* Ctrl+C reaches the command too, which may save before it stops: wait for it. A handler, unlike
 * SetConsoleCtrlHandler(NULL, TRUE), is not inherited by the command. */
static BOOL WINAPI wait_for_child(DWORD event)
{
    return event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT;
}

int wmain(void)
{
    wchar_t dir[MAX_PATH], python[MAX_PATH], command[32768];
    STARTUPINFOW startup = {sizeof(startup)};
    PROCESS_INFORMATION process;
    DWORD code = 1;

    if (!GetModuleFileNameW(NULL, dir, MAX_PATH) || !PathRemoveFileSpecW(dir))
        return 1;
    PathCombineW(python, dir, L"python\\python.exe");
    if (!PathFileExistsW(python))
        wcscpy(python, L"python.exe");
    _snwprintf(command, 32768, L"\"%ls\" -m tes3x %ls", python, PathGetArgsW(GetCommandLineW()));
    command[32767] = 0;
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process)) {
        fwprintf(stderr, L"tes3x-cli: could not start %ls; keep tes3x-cli.exe in the TES3X "
                 L"folder.\n", python);
        return 1;
    }
    SetConsoleCtrlHandler(wait_for_child, TRUE);
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)code;
}

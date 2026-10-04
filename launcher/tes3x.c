/* TES3X.exe: start the GUI with the Python beside it, or the one on PATH in a checkout. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlwapi.h>
#include <wchar.h>

static void fail(const wchar_t *message)
{
    MessageBoxW(NULL, message, L"TES3X", MB_OK | MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show)
{
    wchar_t dir[MAX_PATH], python[MAX_PATH], script[MAX_PATH], command[32768];
    STARTUPINFOW startup = {sizeof(startup)};
    PROCESS_INFORMATION process;
    (void)instance; (void)previous; (void)show;

    if (!GetModuleFileNameW(NULL, dir, MAX_PATH) || !PathRemoveFileSpecW(dir))
        return 1;
    PathCombineW(script, dir, L"tools\\tes3x_gui.py");
    if (!PathFileExistsW(script)) {
        fail(L"tools\\tes3x_gui.py is missing; keep TES3X.exe in the TES3X folder.");
        return 1;
    }
    PathCombineW(python, dir, L"python\\pythonw.exe");
    if (!PathFileExistsW(python))
        wcscpy(python, L"pythonw.exe");
    _snwprintf(command, 32768, L"\"%ls\" \"%ls\" %ls", python, script, args ? args : L"");
    command[32767] = 0;
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, dir, &startup, &process)) {
        fail(L"Could not start Python. Use a TES3X release, or install Python 3.12 and "
             L"requirements-gui.txt.");
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}

"""Ghidra decompiler behind tes3x_sym.py.

One headless Ghidra process keeps the analysed project open and answers on a loopback port, so a
request costs well under a second instead of Ghidra's start-up. It starts on first use and exits
after it has been idle. The project lives in build/ghidra under the working folder.
"""
import json
import os
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_DIR = Path.cwd() / 'build' / 'ghidra'
PROJECT = 'tes3x'
PROGRAMS = PROJECT_DIR / 'programs.json'
SERVER = PROJECT_DIR / 'server.json'
IDLE = 1800
START_TIMEOUT = 300


def ghidra_home():
    home = os.environ.get('GHIDRA_INSTALL_DIR')
    local = Path.cwd() / 'tes3x.local.toml'
    if not home and local.exists():
        home = tomllib.loads(local.read_text(encoding='utf-8')).get('paths', {}).get('ghidra')
    if not home:
        raise SystemExit('set GHIDRA_INSTALL_DIR or [paths] ghidra in tes3x.local.toml')
    return Path(home)


def headless():
    name = 'analyzeHeadless.bat' if os.name == 'nt' else 'analyzeHeadless'
    path = ghidra_home() / 'support' / name
    if not path.exists():
        raise SystemExit(f'{path} not found')
    return str(path)


def setup(images):
    """Import and analyse each (tag, path); takes minutes per image."""
    stop()
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    progs = json.loads(PROGRAMS.read_text()) if PROGRAMS.exists() else {}
    for tag, path in images:
        print(f'importing {tag}: {path}', flush=True)
        t = time.time()
        r = subprocess.run([headless(), str(PROJECT_DIR), PROJECT, '-import', path, '-overwrite'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           errors='replace')
        (PROJECT_DIR / f'import-{tag}.log').write_text(r.stdout, encoding='utf-8')
        if r.returncode:
            raise SystemExit(f'import failed; see {PROJECT_DIR / f"import-{tag}.log"}')
        progs[tag] = Path(path).name
        print(f'  done in {time.time() - t:.0f} s', flush=True)
    PROGRAMS.write_text(json.dumps(progs, indent=1))


def _alive(pid):
    if os.name == 'nt':
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _connect(port):
    s = socket.create_connection(('127.0.0.1', port), timeout=5)
    s.settimeout(None)
    return s


def _start():
    if not PROGRAMS.exists():
        raise SystemExit('no Ghidra project yet; run `tes3x_sym.py ghidra-setup` first')
    progs = json.loads(PROGRAMS.read_text())
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    first = progs.get('xbe') or next(iter(progs.values()))
    cmd = [headless(), str(PROJECT_DIR), PROJECT, '-process', first, '-noanalysis', '-readOnly',
           '-scriptPath', str(HERE / 'ghidra'), '-postScript', 'Tes3xServe.java', str(port),
           str(IDLE)] + [f'{t}:{n}' for t, n in progs.items()]  # cmd.exe splits on =
    log = open(PROJECT_DIR / 'serve.log', 'w')
    flags = 0x00000008 | 0x00000200 if os.name == 'nt' else 0  # detached, new process group
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=flags)
    return port, p.pid


def _running():
    """(port, pid) of a server already serving this project, found by its command line."""
    if os.name == 'nt':
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | "
              "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, errors='replace').stdout
    else:
        out = subprocess.run(['ps', '-eo', 'pid=,args='], capture_output=True, text=True).stdout
    project = str(PROJECT_DIR).replace('\\', '/').lower()
    for line in out.splitlines():
        pid, _, cl = line.strip().partition(' ')
        args = [a.strip('"') for a in cl.split()]
        if 'Tes3xServe.java' not in args or project not in cl.replace('\\', '/').lower():
            continue
        i = args.index('Tes3xServe.java')
        if i + 1 < len(args) and args[i + 1].isdigit() and pid.isdigit():
            return int(args[i + 1]), int(pid)
    return None


def _server():
    """Socket to the running server, starting one when none answers."""
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(SERVER, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = json.loads(SERVER.read_text() or '{}')
            if info.get('pid') and _alive(info['pid']):
                break
            SERVER.unlink(missing_ok=True)
            continue
        # A lost server.json must not start a second Ghidra: the project lock would stop it.
        found = _running()
        if found:
            port, pid = found
            print(f'using the running Ghidra (pid {pid})', file=sys.stderr)
        else:
            port, pid = _start()
            print(f'starting Ghidra (pid {pid}); the first request waits for it',
                  file=sys.stderr)
        with os.fdopen(fd, 'w') as f:
            json.dump({'port': port, 'pid': pid}, f)
        break
    info = json.loads(SERVER.read_text())
    deadline = time.time() + START_TIMEOUT
    while True:
        try:
            return _connect(info['port'])
        except OSError:
            if not _alive(info['pid']):
                SERVER.unlink(missing_ok=True)
                hint = ''
                if any(PROJECT_DIR.glob(f'{PROJECT}.lock*')):
                    hint = '; the project is locked, probably by a Ghidra GUI'
                raise SystemExit(f'Ghidra exited; see {PROJECT_DIR / "serve.log"}{hint}')
            if time.time() > deadline:
                raise SystemExit('Ghidra did not start in time')
            time.sleep(1)


class Client:
    def __init__(self):
        self.sock = _server()
        self.f = self.sock.makefile('rw', encoding='utf-8', newline='\n')

    def call(self, op, **kw):
        self.f.write(json.dumps({'op': op, **kw}) + '\n')
        self.f.flush()
        line = self.f.readline()
        if not line:
            raise SystemExit('Ghidra closed the connection')
        res = json.loads(line)
        if not res.pop('ok'):
            raise SystemExit(f"Ghidra: {res['error']}")
        return res

    def close(self):
        self.sock.close()


def stop():
    if SERVER.exists():
        info = json.loads(SERVER.read_text() or '{}')
    elif found := _running():
        info = {'port': found[0], 'pid': found[1]}
    else:
        return False
    try:
        s = _connect(info['port'])
        s.sendall(b'{"op": "stop"}\n')
        s.recv(4096)
        s.close()
    except (OSError, KeyError):
        pass
    # The project stays locked until the process is gone.
    deadline = time.time() + 60
    while info.get('pid') and _alive(info['pid']) and time.time() < deadline:
        time.sleep(1)
    SERVER.unlink(missing_ok=True)
    return True

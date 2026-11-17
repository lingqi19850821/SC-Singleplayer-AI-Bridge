"""One cursor-scheme reset after the bound game process exits.

The waiting helper owns only a query/synchronize handle. It outlives an API
connection, sends no input, and exits after one reset attempt. Import is inert.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import threading

GAME = ntpath.normcase(r'D:\Starcraft\starcraft.exe')
WAIT_OBJECT_0, WAIT_TIMEOUT, INFINITE = 0, 258, 0xffffffff
_start_lock = threading.Lock()
_last_watch = None


def identity(value):
    pid, creation = value.get('pid'), value.get('creation_time_filetime')
    if (type(pid) is not int or not 0 < pid < 2**32 or
            type(creation) is not int or not 0 < creation < 2**64 or
            ntpath.normcase(ntpath.normpath(str(value.get('path', '')))) != GAME):
        raise ValueError('exact game PID, FILETIME and path required')
    return pid, creation


class Windows:
    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError('cursor recovery requires Windows')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'GetProcessTimes': ([W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
            'QueryFullProcessImageNameW': ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
            'CreateMutexW': ([C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
            'ReleaseMutex': ([W.HANDLE], W.BOOL),
            'CreateEventW': ([C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE),
            'SetEvent': ([W.HANDLE], W.BOOL),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result

    @staticmethod
    def checked(value):
        if not value:
            raise C.WinError(C.get_last_error())
        return value

    def open_process(self, pid):
        return self.checked(self.k.OpenProcess(0x101000, False, pid))

    def metadata(self, handle, pid):
        times = [W.FILETIME() for _ in range(4)]
        self.checked(self.k.GetProcessTimes(handle, *(C.byref(t) for t in times)))
        buffer, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        self.checked(self.k.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(size)))
        return {'pid': pid, 'creation_time_filetime': times[0].dwLowDateTime | (times[0].dwHighDateTime << 32),
                'path': buffer.value}

    def mutex(self, name):
        C.set_last_error(0)
        handle = self.checked(self.k.CreateMutexW(None, True, name))
        return handle, C.get_last_error() == 183

    def event(self, name):
        return self.checked(self.k.CreateEventW(None, True, False, name))

    def wait(self, handle, timeout):
        result = self.k.WaitForSingleObject(handle, timeout)
        if result == 0xffffffff:
            raise C.WinError(C.get_last_error())
        return result

    def release(self, handle):
        self.checked(self.k.ReleaseMutex(handle))

    def close(self, handle):
        self.k.CloseHandle(handle)

    def reset(self):
        user32 = C.WinDLL('user32', use_last_error=True)
        fn = user32.SystemParametersInfoW
        fn.argtypes, fn.restype = [W.UINT, W.UINT, C.c_void_p, W.UINT], W.BOOL
        ok = bool(fn(0x0057, 0, None, 0))
        return {'api_success': ok, 'win32_error': 0 if ok else C.get_last_error()}


def watch_game_exit(expected, *, platform, ready, record):
    """No memory access, retries, process discovery, or reset on API shutdown."""
    pid, creation = identity(expected)
    handle = platform.open_process(pid)
    mutex, owned = None, False
    try:
        if identity(platform.metadata(handle, pid)) != (pid, creation):
            raise ValueError('game process identity changed')
        mutex, exists = platform.mutex(f'Local\\SCAPI.CursorReset.{pid}.{creation}')
        owned = not exists
        if exists:
            return {'status': 'already_watching'}
        # A late API instance must not reset again after the original exit.
        state = platform.wait(handle, 0)
        if state == WAIT_OBJECT_0:
            return {'status': 'exited_before_arm'}
        if state != WAIT_TIMEOUT:
            raise RuntimeError('game exit wait unconfirmed')
        record({'status': 'watching'})
        ready()
        if platform.wait(handle, INFINITE) != WAIT_OBJECT_0:
            raise RuntimeError('game exit wait unconfirmed')
        result = dict(platform.reset(), status='reset_attempted', game_exit_observed=True,
                      reset_attempts=1, operation='SystemParametersInfoW(SPI_SETCURSORS, 0, NULL, 0)',
                      user_visible_recovery='not verified')
        record(result)
        return result
    finally:
        try:
            if owned:
                platform.release(mutex)
        finally:
            if mutex:
                platform.close(mutex)
            platform.close(handle)


def arm_game_exit_cursor_reset(expected):
    """Called after an API client has verified its exact game connection."""
    global _last_watch
    key = identity(expected)
    with _start_lock:
        if _last_watch is not None:
            old_key, old_platform, owner, receipt, _child = _last_watch
            if old_key == key and old_platform.wait(owner, 0) == WAIT_TIMEOUT:
                return dict(receipt)
            old_platform.close(owner)
            _last_watch = None
        platform = Windows()
        name = f'Local\\SCAPI.CursorResetReady.{key[0]}.{key[1]}'
        directory = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'SCAPI/cursor-recovery'
        directory.mkdir(parents=True, exist_ok=True)
        evidence = directory / f'{key[0]}-{key[1]}.json'
        event = platform.event(name)
        child, owner = None, None
        try:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--pid', str(key[0]),
                '--creation', str(key[1]), '--ready-event', name, '--evidence', str(evidence)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            if platform.wait(event, 5000) != WAIT_OBJECT_0:
                raise RuntimeError('game-exit cursor reset helper readiness unconfirmed; see ' + str(evidence))
            # The signaled event may belong to an existing watcher. Pin that
            # actual owner's handle, never the short-lived duplicate child.
            row = json.loads(evidence.read_text(encoding='utf-8'))
            if (row.get('status') != 'watching' or row.get('pid') != key[0] or
                    row.get('creation_time_filetime') != str(key[1])):
                raise RuntimeError('game-exit cursor reset owner receipt unconfirmed')
            owner_pid = row['helper_pid']
            owner = platform.open_process(owner_pid)
            actual = platform.metadata(owner, owner_pid)
            if (str(actual['creation_time_filetime']) != row.get('helper_creation_time_filetime') or
                    platform.wait(owner, 0) != WAIT_TIMEOUT):
                raise RuntimeError('game-exit cursor reset owner identity changed or exited')
            result = {'status': 'watching', 'helper_pid': owner_pid, 'evidence_path': str(evidence)}
            _last_watch = key, platform, owner, result, child
            owner = None  # The cache owns this handle until the owner exits.
            return dict(result)
        except BaseException:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            raise
        finally:
            if owner:
                platform.close(owner)
            platform.close(event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--creation', type=int, required=True)
    parser.add_argument('--ready-event', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    expected = {'pid': args.pid, 'creation_time_filetime': args.creation, 'path': GAME}
    event, owner_creation = None, None
    def record(value):
        value = {'at_utc': datetime.now(timezone.utc).isoformat(),
                 'pid': args.pid, 'creation_time_filetime': str(args.creation),
                 'helper_pid': os.getpid(), 'helper_creation_time_filetime': owner_creation, **value}
        temporary = args.evidence.with_suffix('.' + str(os.getpid()) + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(args.evidence)
    try:
        platform = Windows()
        own_handle = platform.open_process(os.getpid())
        try:
            owner_creation = str(platform.metadata(own_handle, os.getpid())['creation_time_filetime'])
        finally:
            platform.close(own_handle)
        # Keep the shared ready event alive with its owner, including when later
        # API clients launch a duplicate helper and the first parent has exited.
        event = platform.event(args.ready_event)
        result = watch_game_exit(expected, platform=platform,
            ready=lambda: platform.checked(platform.k.SetEvent(event)), record=record)
        return 0 if result.get('api_success', True) else 1
    except Exception as error:
        args.evidence = args.evidence.with_suffix('.failed.' + str(os.getpid()) + '.json')
        record({'status': 'watch_failed', 'error_type': type(error).__name__, 'error': str(error)})
        return 1
    finally:
        if event:
            platform.close(event)


if __name__ == '__main__':
    raise SystemExit(main())

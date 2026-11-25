"""Pinned, per-attempt native loader copies outside the Low workspace.

Import is inert. The sole process probe has no arguments and is created
suspended; its entry point is never resumed. This module neither opens the
game nor changes privileges, ACLs, the source image, or the DLL candidate.
"""
from pathlib import Path, PureWindowsPath
import ctypes as C
from ctypes import wintypes as W
import os
import subprocess
import uuid
from tools.native_episode_artifacts import approved_episode_loader


ROOT = Path(__file__).resolve().parents[1]
APPROVED_LOADERS = (
    ROOT / 'build/stage5/stage5_loader_v53_stability.exe',
    ROOT / 'build/stage5/stage5_loader_v53_next_episode.exe',
    ROOT / 'build/stage5/stage5_loader_v52_cargo_stationary.exe',
    ROOT / 'build/stage5/stage5_loader_v52_next_episode.exe',
    ROOT / 'build/stage5/stage5_loader_v51_perception_fixes.exe',
    ROOT / 'build/stage5/stage5_loader_v51_next_episode.exe',
    ROOT / 'build/stage5/stage5_loader_v49_minimap_ping.exe',
    ROOT / 'build/stage5/stage5_loader_v49_next_episode.exe',
)
MEDIUM_RID = 0x2000
INVALID_HANDLE = C.c_void_p(-1).value


def _key(path):
    return str(PureWindowsPath(path)).casefold()


class NativeLoaderRuntimeError(RuntimeError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


class _GUID(C.Structure):
    _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', C.c_ubyte * 8)]


class _FILE_INFO(C.Structure):
    _fields_ = [('attributes', W.DWORD), ('created', W.FILETIME),
               ('accessed', W.FILETIME), ('written', W.FILETIME),
               ('volume', W.DWORD), ('size_high', W.DWORD), ('size_low', W.DWORD),
               ('links', W.DWORD), ('index_high', W.DWORD), ('index_low', W.DWORD)]


class _ACL_INFO(C.Structure):
    _fields_ = [('count', W.DWORD), ('used', W.DWORD), ('free', W.DWORD)]


class _SID_ATTR(C.Structure):
    _fields_ = [('sid', C.c_void_p), ('attributes', W.DWORD)]


class _LUID(C.Structure):
    _fields_ = [('low', W.DWORD), ('high', W.LONG)]


class _LUID_ATTR(C.Structure):
    _fields_ = [('luid', _LUID), ('attributes', W.DWORD)]


class _OVERLAPPED(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('internal_high', C.c_size_t),
               ('offset', W.DWORD), ('offset_high', W.DWORD), ('event', W.HANDLE)]


class _STARTUPINFO(C.Structure):
    _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR),
               ('title', W.LPWSTR), ('x', W.DWORD), ('y', W.DWORD),
               ('xsize', W.DWORD), ('ysize', W.DWORD), ('xchars', W.DWORD),
               ('ychars', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD),
               ('show', W.WORD), ('reserved_size', W.WORD),
               ('reserved_bytes', C.POINTER(C.c_ubyte)), ('stdin', W.HANDLE),
               ('stdout', W.HANDLE), ('stderr', W.HANDLE)]


class _PROCESS_INFO(C.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE),
               ('pid', W.DWORD), ('tid', W.DWORD)]


class _Windows:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('native loader runtime requires Windows')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        self.s = C.WinDLL('shell32', use_last_error=True)
        self.o = C.WinDLL('ole32', use_last_error=True)
        definitions = (
            (self.k, 'CreateFileW', W.HANDLE, [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]),
            (self.k, 'CloseHandle', W.BOOL, [W.HANDLE]),
            (self.k, 'GetFileInformationByHandle', W.BOOL, [W.HANDLE, C.POINTER(_FILE_INFO)]),
            (self.k, 'GetFinalPathNameByHandleW', W.DWORD, [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]),
            (self.k, 'GetLongPathNameW', W.DWORD, [W.LPCWSTR, W.LPWSTR, W.DWORD]),
            (self.k, 'ReadFile', W.BOOL, [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]),
            (self.k, 'WriteFile', W.BOOL, [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]),
            (self.k, 'SetFilePointerEx', W.BOOL, [W.HANDLE, C.c_longlong, C.c_void_p, W.DWORD]),
            (self.k, 'FlushFileBuffers', W.BOOL, [W.HANDLE]),
            (self.k, 'LockFileEx', W.BOOL, [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD, C.POINTER(_OVERLAPPED)]),
            (self.k, 'UnlockFileEx', W.BOOL, [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.POINTER(_OVERLAPPED)]),
            (self.k, 'LocalFree', C.c_void_p, [C.c_void_p]),
            (self.k, 'GetCurrentProcess', W.HANDLE, []),
            (self.k, 'CreateProcessW', W.BOOL, [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
                                              C.c_void_p, W.LPCWSTR, C.POINTER(_STARTUPINFO), C.POINTER(_PROCESS_INFO)]),
            (self.k, 'TerminateProcess', W.BOOL, [W.HANDLE, W.UINT]),
            (self.k, 'WaitForSingleObject', W.DWORD, [W.HANDLE, W.DWORD]),
            (self.k, 'GetExitCodeProcess', W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
            (self.k, 'QueryFullProcessImageNameW', W.BOOL, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]),
            (self.k, 'GetProcessTimes', W.BOOL, [W.HANDLE, C.POINTER(W.FILETIME), C.POINTER(W.FILETIME),
                                               C.POINTER(W.FILETIME), C.POINTER(W.FILETIME)]),
            (self.a, 'GetSecurityInfo', W.DWORD, [W.HANDLE, W.DWORD, W.DWORD, C.c_void_p, C.c_void_p,
                                                C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)]),
            (self.a, 'GetAclInformation', W.BOOL, [C.c_void_p, C.c_void_p, W.DWORD, W.DWORD]),
            (self.a, 'GetAce', W.BOOL, [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]),
            (self.a, 'GetSidSubAuthorityCount', C.POINTER(C.c_ubyte), [C.c_void_p]),
            (self.a, 'GetSidSubAuthority', C.POINTER(W.DWORD), [C.c_void_p, W.DWORD]),
            (self.a, 'IsValidSid', W.BOOL, [C.c_void_p]),
            (self.a, 'OpenProcessToken', W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
            (self.a, 'GetTokenInformation', W.BOOL, [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]),
            (self.a, 'LookupPrivilegeValueW', W.BOOL, [W.LPCWSTR, W.LPCWSTR, C.POINTER(_LUID)]),
            (self.s, 'SHGetKnownFolderPath', C.c_long, [C.POINTER(_GUID), W.DWORD, W.HANDLE, C.POINTER(C.c_void_p)]),
            (self.o, 'CoTaskMemFree', None, [C.c_void_p]),
        )
        for lib, name, result, args in definitions:
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args

    @staticmethod
    def check(ok, operation):
        if not ok:
            raise C.WinError(C.get_last_error(), operation)

    def close(self, handle):
        if handle not in (None, 0, INVALID_HANDLE):
            self.check(self.k.CloseHandle(handle), 'CloseHandle')

    def open(self, path, *, write=False, create=False, directory=False, share=1):
        # OPEN_REPARSE_POINT makes the following attribute check inspect the
        # object requested, rather than silently following a final symlink.
        access = 0x20000 | (0x80 if directory else 0x80000000)
        if write:
            access |= 0x40000000
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = self.k.CreateFileW(str(path), access, share, None, 1 if create else 3, flags, None)
        if handle == INVALID_HANDLE:
            raise C.WinError(C.get_last_error(), 'CreateFileW: ' + str(path))
        try:
            info = _FILE_INFO()
            self.check(self.k.GetFileInformationByHandle(handle, C.byref(info)), 'GetFileInformationByHandle')
            if info.attributes & 0x400:
                raise ValueError('reparse point is not a native runtime boundary: ' + str(path))
            if bool(info.attributes & 0x10) != directory:
                raise ValueError('unexpected runtime object type: ' + str(path))
            buffer = C.create_unicode_buffer(32768)
            count = self.k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if not count or count >= len(buffer):
                raise ValueError('runtime final path unavailable')
            final = buffer.value
            if final.startswith('\\\\?\\'):
                final = final[4:]
            # Expand legitimate DOS 8.3 names without resolving junctions.
            # Unlike Path.resolve(), this retains the requested directory path
            # so a parent reparse redirect still differs from the handle path.
            requested = C.create_unicode_buffer(32768)
            count = self.k.GetLongPathNameW(str(path), requested, len(requested))
            if not count or count >= len(requested):
                raise ValueError('runtime requested long path unavailable')
            if _key(final) != _key(requested.value):
                raise ValueError('runtime path redirected: ' + str(path))
            return handle
        except BaseException:
            self.close(handle)
            raise

    def local_app_data(self):
        value = uuid.UUID('F1B32785-6FBA-4FCF-9D55-7B8E7F157091')
        folder = _GUID.from_buffer_copy(value.bytes_le)
        result = C.c_void_p()
        hr = self.s.SHGetKnownFolderPath(C.byref(folder), 0, None, C.byref(result))
        if hr < 0:
            raise OSError('SHGetKnownFolderPath failed: ' + hex(hr & 0xffffffff))
        try:
            path = Path(C.wstring_at(result.value))
        finally:
            self.o.CoTaskMemFree(result)
        if not path.is_absolute() or str(path).startswith('\\\\'):
            raise ValueError('local native runtime known folder required')
        return path

    def sid_rid(self, sid):
        if not self.a.IsValidSid(sid):
            raise ValueError('invalid integrity SID')
        count = self.a.GetSidSubAuthorityCount(sid)[0]
        if not count:
            raise ValueError('empty integrity SID')
        return self.a.GetSidSubAuthority(sid, count - 1)[0]

    def label(self, handle):
        sacl, descriptor = C.c_void_p(), C.c_void_p()
        # LABEL_SECURITY_INFORMATION needs READ_CONTROL, not SeSecurityPrivilege.
        error = self.a.GetSecurityInfo(handle, 1, 0x10, None, None, None, C.byref(sacl), C.byref(descriptor))
        if error:
            raise C.WinError(error, 'GetSecurityInfo label')
        try:
            labels = []
            if sacl.value:
                info = _ACL_INFO()
                self.check(self.a.GetAclInformation(sacl, C.byref(info), C.sizeof(info), 2), 'GetAclInformation')
                for index in range(info.count):
                    ace = C.c_void_p()
                    self.check(self.a.GetAce(sacl, index, C.byref(ace)), 'GetAce')
                    if C.c_ubyte.from_address(ace.value).value == 0x11:
                        flags = C.c_ubyte.from_address(ace.value + 1).value
                        if flags & 0x08:  # INHERIT_ONLY_ACE does not label this object.
                            continue
                        labels.append((self.sid_rid(ace.value + 8), W.DWORD.from_address(ace.value + 4).value))
            if len(labels) > 1:
                raise ValueError('ambiguous mandatory integrity labels')
            rid, policy = labels[0] if labels else (MEDIUM_RID, 1)
            return {'integrity_rid': rid, 'no_write_up': bool(policy & 1), 'explicit': bool(labels)}
        finally:
            if descriptor.value:
                self.k.LocalFree(descriptor)

    def token(self, process):
        token = W.HANDLE()
        self.check(self.a.OpenProcessToken(process, 8, C.byref(token)), 'OpenProcessToken')
        try:
            def information(kind):
                needed = W.DWORD()
                self.a.GetTokenInformation(token, kind, None, 0, C.byref(needed))
                if not 0 < needed.value <= 65536:
                    raise ValueError('token information length unavailable')
                buffer = C.create_string_buffer(needed.value)
                self.check(self.a.GetTokenInformation(token, kind, buffer, len(buffer), C.byref(needed)), 'GetTokenInformation')
                return buffer
            label = information(25)
            rid = self.sid_rid(_SID_ATTR.from_buffer(label).sid)
            privileges = information(3)
            count = W.DWORD.from_buffer(privileges).value
            if 4 + count * C.sizeof(_LUID_ATTR) > len(privileges):
                raise ValueError('invalid token privileges length')
            debug = _LUID()
            self.check(self.a.LookupPrivilegeValueW(None, 'SeDebugPrivilege', C.byref(debug)), 'LookupPrivilegeValueW')
            enabled = False
            for index in range(count):
                item = _LUID_ATTR.from_buffer(privileges, 4 + index * C.sizeof(_LUID_ATTR))
                if (item.luid.low, item.luid.high) == (debug.low, debug.high):
                    enabled = bool(item.attributes & 2)
            return {'integrity_rid': rid, 'integrity_sid': 'S-1-16-' + str(rid), 'se_debug_enabled': enabled}
        finally:
            self.close(token)

    def copy_and_lock(self, source, target):
        writer = self.open(target, write=True, create=True)
        guard, final = None, None
        overlap, locked = _OVERLAPPED(), False
        try:
            self.check(self.k.SetFilePointerEx(source, 0, None, 0), 'source seek')
            buffer, count = C.create_string_buffer(65536), W.DWORD()
            total = 0
            while True:
                self.check(self.k.ReadFile(source, buffer, len(buffer), C.byref(count), None), 'source ReadFile')
                if not count.value:
                    break
                written = W.DWORD()
                self.check(self.k.WriteFile(writer, buffer, count.value, C.byref(written), None), 'runtime WriteFile')
                if written.value != count.value:
                    raise OSError('short native loader write')
                total += count.value
            if not total:
                raise ValueError('empty native loader')
            self.check(self.k.FlushFileBuffers(writer), 'FlushFileBuffers')
            # A no-delete read guard spans the transition from the exclusive
            # writer to the executable read lock. The range lock rejects normal
            # concurrent writes during this brief handle transition.
            guard = self.open(target, share=3)
            self.check(self.k.LockFileEx(guard, 3, 0, 0xffffffff, 0xffffffff, C.byref(overlap)), 'LockFileEx')
            locked = True
            self.close(writer)
            writer = None
            final = self.open(target)
            self.check(self.k.UnlockFileEx(guard, 0, 0xffffffff, 0xffffffff, C.byref(overlap)), 'UnlockFileEx')
            locked = False
            self.close(guard)
            guard = None
            self.check(self.k.SetFilePointerEx(source, 0, None, 0), 'source compare seek')
            left, right = C.create_string_buffer(65536), C.create_string_buffer(65536)
            while True:
                a, b = W.DWORD(), W.DWORD()
                self.check(self.k.ReadFile(source, left, len(left), C.byref(a), None), 'source compare read')
                self.check(self.k.ReadFile(final, right, len(right), C.byref(b), None), 'runtime compare read')
                if a.value != b.value or left.raw[:a.value] != right.raw[:b.value]:
                    raise ValueError('runtime loader differs from pinned source')
                if not a.value:
                    break
            result, final = final, None
            return result, total
        finally:
            if locked and guard:
                self.k.UnlockFileEx(guard, 0, 0xffffffff, 0xffffffff, C.byref(overlap))
            for handle in (writer, guard, final):
                self.close(handle)

    def probe(self, path, receipt):
        receipt.update(argv=[str(path)], creation_flags=['CREATE_SUSPENDED', 'CREATE_NO_WINDOW'],
                       entry_resumed=False, game_access_attempted=False, exited=False)
        startup, info = _STARTUPINFO(), _PROCESS_INFO()
        startup.cb = C.sizeof(startup)
        command = C.create_unicode_buffer(subprocess.list2cmdline([str(path)]))
        self.check(self.k.CreateProcessW(str(path), command, None, None, False, 0x08000004,
                                        None, str(path.parent), C.byref(startup), C.byref(info)), 'suspended loader CreateProcessW')
        receipt['pid'] = info.pid
        error = None
        try:
            created, exited, kernel, user = W.FILETIME(), W.FILETIME(), W.FILETIME(), W.FILETIME()
            self.check(self.k.GetProcessTimes(info.process, C.byref(created), C.byref(exited),
                                             C.byref(kernel), C.byref(user)), 'GetProcessTimes')
            receipt['creation_time_filetime'] = (created.dwHighDateTime << 32) | created.dwLowDateTime
            image, length = C.create_unicode_buffer(32768), W.DWORD(32768)
            self.check(self.k.QueryFullProcessImageNameW(info.process, 0, image, C.byref(length)), 'QueryFullProcessImageNameW')
            receipt['image_path'] = image.value
            if _key(image.value) != _key(path):
                raise ValueError('suspended loader image changed')
            receipt['parent_token'] = self.token(self.k.GetCurrentProcess())
            receipt['child_token'] = self.token(info.process)
            parent = receipt['parent_token']['integrity_rid']
            child = receipt['child_token']['integrity_rid']
            if child < max(MEDIUM_RID, parent):
                raise ValueError('native loader child integrity is below parent or Medium')
        except BaseException as caught:
            error = caught
        finally:
            try:
                if self.k.WaitForSingleObject(info.process, 0) != 0:
                    self.check(self.k.TerminateProcess(info.process, 0x53434150), 'TerminateProcess own suspended probe')
                if self.k.WaitForSingleObject(info.process, 5000) != 0:
                    raise RuntimeError('suspended loader probe exit not confirmed')
                code = W.DWORD()
                self.check(self.k.GetExitCodeProcess(info.process, C.byref(code)), 'GetExitCodeProcess')
                receipt.update(exited=True, exit_code=code.value)
            except BaseException as cleanup_error:
                receipt['exit_error'] = str(cleanup_error)
                if error is None:
                    error = cleanup_error
            finally:
                for handle in (info.thread, info.process):
                    try:
                        self.close(handle)
                    except BaseException as close_error:
                        receipt.setdefault('handle_close_errors', []).append(str(close_error))
                        if error is None:
                            error = close_error
        if error is not None:
            raise error


class LoaderRuntime:
    def __init__(self, windows, source):
        self.windows = windows
        self.source = Path(source)
        self.execution_path = None
        self.directory = None
        self.handles = []
        self.execution_started = False
        self.closed = False
        self.receipt = {'source_loader': str(self.source), 'status': 'preparing',
                        'game_access_verified': False, 'privileges_changed': False, 'acl_changed': False}

    def mark_execution_started(self):
        if self.closed:
            raise RuntimeError('native loader runtime already closed')
        self.execution_started = True

    def close(self, *, completed=False):
        """Release handles; never let file cleanup replace installation ownership.

        A thrown/unknown execution retains its unique copy. Only a returned
        process result or a never-started attempt permits removal.
        """
        if self.closed:
            return self.receipt.get('cleanup', {})
        self.closed = True
        errors = list(self.receipt.get('probe', {}).get('handle_close_errors', []))
        for handle in reversed(self.handles):
            try:
                self.windows.close(handle)
            except BaseException as error:
                errors.append(str(error))
        self.handles.clear()
        probe = self.receipt.get('probe', {})
        allowed = (not self.execution_started or completed) and (not probe.get('pid') or probe.get('exited') is True)
        removed = False
        if allowed and not errors and self.directory is not None:
            try:
                if self.execution_path is not None and self.execution_path.exists():
                    self.execution_path.unlink()
                self.directory.rmdir()  # Only the current UUID directory, never recursive.
                removed = True
            except BaseException as error:
                errors.append(str(error))
        cleanup = {'actual_execution_started': self.execution_started, 'actual_execution_exit_confirmed': bool(completed),
                   'copy_removed': removed, 'copy_retained': bool(self.directory and not removed), 'errors': errors}
        self.receipt['cleanup'] = cleanup
        return cleanup


def prepare_loader_runtime(source_loader):
    """Pin a fixed or exact recipe-bound episode loader and inspect its child."""
    source = next((path for path in APPROVED_LOADERS if _key(path) == _key(source_loader)), None)
    if source is None:
        source = approved_episode_loader(source_loader)
    if source is None:
        raise ValueError('unapproved native loader source')
    runtime = None
    stage = 'windows_api'
    try:
        windows = _Windows()
        runtime = LoaderRuntime(windows, source)
        stage = 'pin_source'
        source_handle = windows.open(source)
        runtime.handles.append(source_handle)
        runtime.receipt['source_label'] = windows.label(source_handle)
        stage = 'runtime_boundary'
        local = windows.local_app_data()
        boundary = local / 'SCAPI-Alpha' / 'native-runtime'
        chain = list(reversed(local.parents)) + [local, local / 'SCAPI-Alpha', boundary]
        runtime.receipt['directory_labels'] = []
        for directory in chain:
            if directory in (local / 'SCAPI-Alpha', boundary):
                directory.mkdir(exist_ok=True)
            handle = windows.open(directory, directory=True, share=3)
            runtime.handles.append(handle)
            label = windows.label(handle)
            runtime.receipt['directory_labels'].append(dict(path=str(directory), **label))
            if label['integrity_rid'] < MEDIUM_RID or not label['no_write_up']:
                raise ValueError('native runtime directory permits Low integrity writes: ' + str(directory))
        directory = boundary / str(uuid.uuid4())
        directory.mkdir()
        runtime.directory = directory
        runtime.execution_path = directory / source.name
        handle = windows.open(directory, directory=True, share=3)
        runtime.handles.append(handle)
        label = windows.label(handle)
        runtime.receipt['directory_labels'].append(dict(path=str(directory), **label))
        if label['integrity_rid'] < MEDIUM_RID or not label['no_write_up']:
            raise ValueError('new native runtime directory permits Low integrity writes')
        runtime.receipt['execution_loader'] = str(runtime.execution_path)
        stage = 'copy_and_compare'
        handle, count = windows.copy_and_lock(source_handle, runtime.execution_path)
        runtime.handles.append(handle)
        label = windows.label(handle)
        runtime.receipt.update(execution_label=label, copied_bytes=count, byte_for_byte_equal=True)
        if label['integrity_rid'] < MEDIUM_RID or not label['no_write_up']:
            raise ValueError('native runtime image permits Low integrity writes')
        stage = 'suspended_token_probe'
        runtime.receipt['probe'] = {}
        windows.probe(runtime.execution_path, runtime.receipt['probe'])
        runtime.receipt.update(status='ready', stage='ready', game_access_verified=False)
        return runtime
    except BaseException as error:
        receipt = runtime.receipt if runtime is not None else {'source_loader': str(source)}
        receipt.update(status='failed', stage=stage, error=str(error), game_access_verified=False)
        if runtime is not None:
            runtime.close()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise NativeLoaderRuntimeError('native loader runtime preparation failed: ' + str(error), receipt) from error

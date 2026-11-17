"""Windows process primitives for the API transport; import performs no IO.

Modified 2026-11-16: retain original primitives, omit research profile probing.
This internal module is not the player-facing observation interface.
"""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct
import sys

TARGET = Path('D:/Starcraft/starcraft.exe')
READ_ACCESS = 0x0400 | 0x0010
MAX32_END = 1 << 32

class ProbeError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def record(self):
        return {"code": self.code, "message": str(self), **self.details}


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def checked_range(address, size):
    if (type(address) is not int or type(size) is not int or address <= 0
            or size <= 0 or address >= MAX32_END or size > MAX32_END - address):
        raise ProbeError("invalid_32bit_range", "Invalid non-null x86 address range",
                         address=address, size=size)
    return address + size


def path_key(path):
    return str(Path(path).resolve()).casefold()


def module_address(module, rva, size):
    if rva < 0 or size <= 0 or rva + size > module["size"]:
        raise ProbeError("outside_module", "Candidate RVA exceeds actual module",
                         rva=hex(rva), size=size)
    address = module["base"] + rva
    checked_range(address, size)
    return address


class PROCESSENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD),
                ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", C.c_size_t),
                ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", W.LONG),
                ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]


class MODULEENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD),
                ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                ("ProccntUsage", W.DWORD), ("modBaseAddr", C.c_void_p),
                ("modBaseSize", W.DWORD), ("hModule", W.HMODULE),
                ("szModule", W.WCHAR * 256), ("szExePath", W.WCHAR * 260)]


class MEMORY_BASIC_INFORMATION(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("PartitionId", W.WORD),
                ("RegionSize", C.c_size_t), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD)]


class WinAPI:
    def __init__(self):
        if sys.platform != "win32" or C.sizeof(C.c_void_p) != 8:
            raise ProbeError("unsupported_host", "Use 64-bit Python on Windows")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            "CloseHandle": ([W.HANDLE], W.BOOL),
            "QueryFullProcessImageNameW": ([W.HANDLE, W.DWORD, W.LPWSTR,
                                           C.POINTER(W.DWORD)], W.BOOL),
            "GetProcessTimes": ([W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
            "CreateToolhelp32Snapshot": ([W.DWORD, W.DWORD], W.HANDLE),
            "Process32FirstW": ([W.HANDLE, C.POINTER(PROCESSENTRY32W)], W.BOOL),
            "Process32NextW": ([W.HANDLE, C.POINTER(PROCESSENTRY32W)], W.BOOL),
            "Module32FirstW": ([W.HANDLE, C.POINTER(MODULEENTRY32W)], W.BOOL),
            "Module32NextW": ([W.HANDLE, C.POINTER(MODULEENTRY32W)], W.BOOL),
            "VirtualQueryEx": ([W.HANDLE, C.c_void_p,
                                C.POINTER(MEMORY_BASIC_INFORMATION), C.c_size_t], C.c_size_t),
            "ReadProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t,
                                   C.POINTER(C.c_size_t)], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result

    def failure(self, operation, **details):
        return ProbeError("win32_error", operation + " failed",
                          winerror=C.get_last_error(), operation=operation, **details)

    def image_path(self, handle):
        buffer = C.create_unicode_buffer(32768)
        size = W.DWORD(len(buffer))
        if not self.k.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(size)):
            raise self.failure("QueryFullProcessImageNameW")
        return buffer.value

    def snapshot(self, flags, pid):
        handle = self.k.CreateToolhelp32Snapshot(flags, pid)
        if handle == C.c_void_p(-1).value:
            raise self.failure("CreateToolhelp32Snapshot", pid=pid)
        return handle

    def processes(self):
        results = []
        handle = self.snapshot(0x00000002, 0)
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = C.sizeof(entry)
            ok = self.k.Process32FirstW(handle, C.byref(entry))
            while ok:
                if entry.szExeFile.casefold() == TARGET.name.casefold():
                    row = {"pid": entry.th32ProcessID, "exe_name": entry.szExeFile,
                           "exact_target": False}
                    query = self.k.OpenProcess(0x1000, False, entry.th32ProcessID)
                    if query:
                        try:
                            row["path"] = self.image_path(query)
                            row["exact_target"] = path_key(row["path"]) == path_key(TARGET)
                        except ProbeError as exc:
                            row["error"] = exc.record()
                        finally:
                            self.k.CloseHandle(query)
                    else:
                        row["error"] = self.failure("OpenProcess(query)").record()
                    results.append(row)
                ok = self.k.Process32NextW(handle, C.byref(entry))
            if C.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise self.failure("Process32 enumeration")
        finally:
            self.k.CloseHandle(handle)
        return results

    def module(self, pid):
        handle = self.snapshot(0x00000008 | 0x00000010, pid)
        found = []
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = C.sizeof(entry)
            ok = self.k.Module32FirstW(handle, C.byref(entry))
            while ok:
                if path_key(entry.szExePath) == path_key(TARGET):
                    found.append({"base": entry.modBaseAddr, "size": entry.modBaseSize,
                                  "path": entry.szExePath})
                ok = self.k.Module32NextW(handle, C.byref(entry))
            if C.get_last_error() != 18:
                raise self.failure("Module32 enumeration")
        finally:
            self.k.CloseHandle(handle)
        # On this WOW64 installation SNAPMODULE|SNAPMODULE32 returned the same
        # main image twice. Merge only identical mappings, never distinct bases.
        entries = len(found)
        found = list({(path_key(row["path"]), row["base"], row["size"]): row
                      for row in found}.values())
        if len(found) != 1:
            raise ProbeError("module_not_unique", "Exact target module not uniquely found",
                             count=len(found), enumeration_entries=entries)
        checked_range(found[0]["base"], found[0]["size"])
        return {**found[0], "enumeration_entries_for_mapping": entries}


class WinReader:
    def __init__(self, api, pid):
        self.api, self.pid = api, pid
        self.handle = api.k.OpenProcess(READ_ACCESS, False, pid)
        if not self.handle:
            raise api.failure("OpenProcess(read-only)", pid=pid, access=hex(READ_ACCESS))

    def close(self):
        if self.handle:
            self.api.k.CloseHandle(self.handle)
            self.handle = None

    def metadata(self):
        times = [W.FILETIME() for _ in range(4)]
        path = self.api.image_path(self.handle)
        if not self.api.k.GetProcessTimes(self.handle, *(C.byref(t) for t in times)):
            raise self.api.failure("GetProcessTimes")
        creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return {"pid": self.pid, "path": path, "creation_time_filetime": creation,
                "memory_handle_access": hex(READ_ACCESS)}

    def validate_range(self, address, size):
        end = checked_range(address, size)
        cursor, regions = address, 0
        while cursor < end:
            mbi = MEMORY_BASIC_INFORMATION()
            got = self.api.k.VirtualQueryEx(self.handle, cursor, C.byref(mbi), C.sizeof(mbi))
            if got != C.sizeof(mbi):
                raise self.api.failure("VirtualQueryEx", address=hex(cursor), returned=got)
            base = mbi.BaseAddress or 0
            region_end = base + mbi.RegionSize
            if not base <= cursor < region_end or region_end > MAX32_END:
                raise ProbeError("invalid_region", "Invalid VirtualQueryEx region",
                                 address=hex(cursor), region_base=hex(base), size=mbi.RegionSize)
            if (mbi.State != 0x1000 or mbi.Protect & (0x100 | 0x01)
                    or (mbi.Protect & 0xff) not in {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}):
                raise ProbeError("unreadable_region", "Range includes uncommitted or unreadable memory",
                                 address=hex(cursor), state=hex(mbi.State), protect=hex(mbi.Protect))
            cursor, regions = min(region_end, end), regions + 1
        return {"address": hex(address), "size": size, "regions_checked": regions}

    def read(self, address, size):
        self.validate_range(address, size)
        buffer = (C.c_ubyte * size)()
        count = C.c_size_t()
        ok = self.api.k.ReadProcessMemory(self.handle, address, buffer, size, C.byref(count))
        if not ok or count.value != size:
            raise self.api.failure("ReadProcessMemory", address=hex(address), requested=size,
                                   received=count.value, api_success=bool(ok))
        return bytes(buffer)


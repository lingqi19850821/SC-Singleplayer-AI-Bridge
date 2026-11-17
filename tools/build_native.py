"""Build the native bridge and bounded loader; never run either artifact.

SPDX-License-Identifier: AGPL-3.0-only
"""
from pathlib import Path, PureWindowsPath
import argparse
import json
import os
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def pe32(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('compiler did not produce a PE executable')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if (data[offset:offset + 4] != b'PE\0\0' or
            struct.unpack_from('<H', data, offset + 4)[0] != 0x14c or
            struct.unpack_from('<H', data, offset + 24)[0] != 0x10b):
        raise ValueError('32-bit x86 TinyCC is required')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tcc', type=Path, required=True, help='existing 32-bit TinyCC executable')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/stage5')
    parser.add_argument('--visible-phase-diagnostic', action='store_true', help='optional v50/ABI28 source candidate; default v49/ABI27')
    args = parser.parse_args(argv)
    compiler = args.tcc.resolve(strict=True)
    output = args.output_dir.resolve()
    if not compiler.is_file():
        parser.error('--tcc must identify a file')
    label = 'v50_visible_phase_diagnostic' if args.visible_phase_diagnostic else 'v49_minimap_ping'
    dll = output / ('scapi_stage5_' + label + '.dll')
    loader = output / ('stage5_loader_' + label + '.exe')
    if dll.exists() or loader.exists():
        parser.error('output already exists; never overwrite a possibly resident image')
    # Reserve this build directory against simultaneous builders. No stale lock is deleted.
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.native-build.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        if dll.exists() or loader.exists():
            parser.error('output appeared while acquiring build ownership')
        literal = str(PureWindowsPath(dll)).replace('\\', '\\\\')
        if len(str(PureWindowsPath(dll))) >= 260:
            parser.error('exact loader DLL path must fit the existing Win32 MAX_PATH boundary')
        define = '-DSCAPI_STAGE5_DLL_PATH=L"' + literal + '"'
        flags = ['-Wall', '-Werror']
        if args.visible_phase_diagnostic:
            flags.append('-DSCAPI_VISIBLE_PHASE_CANDIDATE')
        commands = [
            [str(compiler), *flags, '-shared', str(ROOT / 'native/stage5_bridge.c'), '-o', str(dll), '-luser32'],
            [str(compiler), *flags, define, str(ROOT / 'native/stage5_loader.c'), '-o', str(loader), '-luser32'],
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout or 'native compilation failed')
        pe32(dll)
        pe32(loader)
        encoded = (str(PureWindowsPath(dll)) + '\0').encode('utf-16le')
        if encoded not in loader.read_bytes():
            raise RuntimeError('loader is missing the exact DLL path allowlist')
        print(json.dumps({'dll': str(dll), 'loader': str(loader),
                          'protocol_version': 28 if args.visible_phase_diagnostic else 27,
                          'compiled_only': True, 'game_started': False}, indent=2))
    finally:
        os.close(descriptor)
        lock.unlink()


if __name__ == '__main__':
    main()

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


def compile_native(compiler, arguments):
    result = subprocess.run([str(compiler), *arguments],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or 'native compilation failed')


def require_max_path(targets, parser):
    for target in targets:
        if len(str(PureWindowsPath(target))) >= 260:
            parser.error('exact loader DLL path must fit the existing Win32 MAX_PATH boundary')


def refuse_existing(targets, parser):
    if any(target.exists() or target.is_symlink() for target in targets):
        parser.error('output already exists; never overwrite a possibly resident image')


def verify_loader_allowlist(loader, dll):
    encoded = (str(PureWindowsPath(dll)) + '\0').encode('utf-16le')
    if encoded not in loader.read_bytes():
        raise RuntimeError('loader is missing the exact DLL path allowlist')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tcc', type=Path, required=True, help='existing 32-bit TinyCC executable')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/stage5')
    parser.add_argument('--visible-phase-diagnostic', action='store_true',
                        help='optional v50/ABI28 diagnostic source candidate; default v53/ABI27')
    args = parser.parse_args(argv)
    compiler = args.tcc.resolve(strict=True)
    output = args.output_dir.resolve()
    if not compiler.is_file():
        parser.error('--tcc must identify a file')

    stable = not args.visible_phase_diagnostic
    if stable:
        dll = output / 'scapi_stage5_v53_stability.dll'
        next_dll = output / 'scapi_stage5_v53_next_episode.dll'
        loader = output / 'stage5_loader_v53_stability.exe'
        next_loader = output / 'stage5_loader_v53_next_episode.exe'
        targets = (dll, next_dll, loader, next_loader)
    else:
        dll = output / 'scapi_stage5_v50_visible_phase_diagnostic.dll'
        next_dll = next_loader = None
        loader = output / 'stage5_loader_v50_visible_phase_diagnostic.exe'
        targets = (dll, loader)

    require_max_path(targets, parser)
    refuse_existing(targets, parser)
    # Reserve this build directory against simultaneous builders. No stale lock is deleted.
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.native-build.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        refuse_existing(targets, parser)
        flags = ['-Wall', '-Werror']
        if args.visible_phase_diagnostic:
            flags.append('-DSCAPI_VISIBLE_PHASE_CANDIDATE')
        compile_native(compiler, [*flags, '-shared',
                                  str(ROOT / 'native/stage5_bridge.c'),
                                  '-o', str(dll), '-luser32'])
        pe32(dll)
        pairs = [(dll, loader)]
        if stable:
            # Copy after the bridge compiles, but create the destination exclusively:
            # an existing next-episode image is never overwritten.
            try:
                with next_dll.open('xb') as stream:
                    stream.write(dll.read_bytes())
            except FileExistsError:
                parser.error('output already exists; never overwrite a possibly resident image')
            pe32(next_dll)
            pairs.append((next_dll, next_loader))
        for built_dll, built_loader in pairs:
            literal = str(PureWindowsPath(built_dll)).replace('\\', '\\\\')
            define = '-DSCAPI_STAGE5_DLL_PATH=L"' + literal + '"'
            compile_native(compiler, [*flags, define,
                                      str(ROOT / 'native/stage5_loader.c'),
                                      '-o', str(built_loader), '-luser32'])
            pe32(built_loader)
            verify_loader_allowlist(built_loader, built_dll)
        payload = {'dll': str(dll), 'loader': str(loader)}
        if stable:
            payload.update({'next_episode_dll': str(next_dll),
                            'next_episode_loader': str(next_loader)})
        payload.update({'protocol_version': 28 if args.visible_phase_diagnostic else 27,
                        'compiled_only': True, 'game_started': False})
        print(json.dumps(payload, indent=2))
    finally:
        os.close(descriptor)
        lock.unlink()


if __name__ == '__main__':
    main()

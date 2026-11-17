"""Episode-specific immutable v49 image identities. Import performs no work.

The bridge exports a per-image mailbox and uses per-image private buffers. It
has no named shared mapping. The process/creation writer mutex deliberately
remains shared so two clients cannot submit concurrently to the same game.
"""
from pathlib import Path, PureWindowsPath
import json
import os
import re
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EPISODE_ROOT = ROOT / 'build/stage5/episodes'
TEMPLATE = ROOT / 'build/stage5/scapi_stage5_v49_minimap_ping.dll'
SOURCE = ROOT / 'native/stage5_loader.c'
SCHEMA = 'scapi.native-episode-artifact.v1'


def _key(path):
    return str(PureWindowsPath(path)).casefold()


def _plain_chain(path):
    # Inspect lexical ancestors before resolve: resolving first hides junctions.
    path = Path(os.path.abspath(path))
    for item in (*reversed(path.parents), path):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('episode artifact reparse path forbidden')
    return path


def _paths(token):
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('invalid episode artifact identity')
    directory = EPISODE_ROOT / token
    return directory, directory / ('scapi_stage5_v49_episode_' + token + '.dll'), directory / ('stage5_loader_v49_episode_' + token + '.exe')


def episode_spec(candidate):
    """Validate only the exact host recipe, not an arbitrary DLL/EXE directory."""
    path = Path(os.path.abspath(candidate))
    token = path.parent.name
    directory, dll, loader = _paths(token)
    if _key(path) != _key(dll):
        raise ValueError('unapproved episode candidate path')
    _plain_chain(directory); _plain_chain(dll); _plain_chain(loader)
    descriptor_path = directory / 'artifact.json'
    _plain_chain(descriptor_path)
    if descriptor_path.stat().st_size > 4096:
        raise ValueError('episode artifact descriptor too large')
    descriptor = json.loads(descriptor_path.read_text(encoding='utf-8'))
    expected = {'schema': SCHEMA, 'identity': token, 'candidate': str(dll), 'loader': str(loader),
                'source_candidate': str(TEMPLATE), 'loader_source': str(SOURCE)}
    if descriptor != expected:
        raise ValueError('episode artifact recipe changed')
    _plain_chain(TEMPLATE)
    if dll.stat().st_size != TEMPLATE.stat().st_size or dll.read_bytes() != TEMPLATE.read_bytes():
        raise ValueError('episode candidate differs from fixed v49 source')
    # Literal Win32 allowlist is compiled into this episode's loader. The trusted
    # source directory and compiler recipe are the same boundary as fixed builds.
    if loader.stat().st_size > 2 * 1024 * 1024 or (str(PureWindowsPath(dll)) + '\0').encode('utf-16le') not in loader.read_bytes():
        raise ValueError('episode loader exact candidate allowlist missing')
    return str(dll), loader


def approved_episode_candidate(candidate):
    try:
        episode_spec(candidate)
        return True
    except (OSError, ValueError, TypeError):
        return False


def approved_episode_loader(loader):
    try:
        path = Path(os.path.abspath(loader))
        _, dll, expected = _paths(path.parent.name)
        if _key(path) != _key(expected):
            return None
        episode_spec(dll)
        return expected
    except (OSError, ValueError, TypeError):
        return None


def build_episode_candidate(*, compiler=None):
    """Copy unchanged bridge bytes and compile one exact-path loader, once.

    No game/process discovery or injection occurs here. Old images are never
    overwritten or deleted; their private memory remains until ordinary game exit.
    """
    # Modified 2026-11-16: external compiler, no bundled development environment.
    configured = compiler or os.environ.get('SCAPI_TCC')
    if not configured:
        raise ValueError('explicit compiler or SCAPI_TCC required')
    compiler = _plain_chain(configured).resolve(strict=True)
    _plain_chain(EPISODE_ROOT); _plain_chain(TEMPLATE); _plain_chain(compiler); _plain_chain(SOURCE)
    EPISODE_ROOT.mkdir(parents=True, exist_ok=True)
    directory, dll, loader = _paths(secrets.token_hex(16))
    directory.mkdir(exist_ok=False)
    with dll.open('xb') as stream:
        stream.write(TEMPLATE.read_bytes())
    define = '-DSCAPI_STAGE5_DLL_PATH=L"' + str(PureWindowsPath(dll)).replace('\\', '\\\\') + '"'
    result = subprocess.run([str(compiler), '-Wall', '-Werror', define, str(SOURCE), '-o', str(loader), '-luser32'],
                            capture_output=True, text=True, timeout=30)
    (directory / 'build-result.json').write_text(json.dumps({'returncode': result.returncode,
        'stdout': result.stdout, 'stderr': result.stderr}), encoding='utf-8')
    if result.returncode:
        raise ValueError('episode loader compilation failed; artifacts retained, no install')
    descriptor = {'schema': SCHEMA, 'identity': directory.name, 'candidate': str(dll), 'loader': str(loader),
                  'source_candidate': str(TEMPLATE), 'loader_source': str(SOURCE)}
    with (directory / 'artifact.json').open('x', encoding='utf-8') as stream:
        json.dump(descriptor, stream)
    return episode_spec(dll)

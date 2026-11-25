"""Offline role experience: one JSON request in, one JSON result out.

This tool never attaches to a game and never defaults to a production
knowledge directory. In official_standard mode the caller supplies an explicit
absolute shared-experience directory; each role still owns its own notebook.
"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scapi.experience import (AUTHOR_ROLES, ExperienceStore,
                              execute_role_experience)


def _read_stdin():
    stream = getattr(sys.stdin, 'buffer', None)
    if stream is not None:
        return stream.read().decode('utf-8-sig')
    return sys.stdin.read()


def _emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--role', required=True, choices=sorted(AUTHOR_ROLES))
    value.add_argument('--knowledge-mode',
                       choices=('official_standard', 'isolated_scope'),
                       default='official_standard')
    value.add_argument('--shared-experience-dir')
    value.add_argument('--run-ref', default='offline')
    return value


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    argument_parser = parser()
    options = argument_parser.parse_args(argv)
    shared_path = None
    if options.knowledge_mode == 'official_standard':
        if not options.shared_experience_dir:
            argument_parser.error('--shared-experience-dir is required when '
                                  '--knowledge-mode is official_standard')
        base = Path(options.shared_experience_dir)
        if not base.is_absolute():
            argument_parser.error('--shared-experience-dir must be absolute')
        shared_path = str((base / (options.role + '.sqlite3')).resolve())
    try:
        request = json.loads(_read_stdin().lstrip('\ufeff'))
    except (TypeError, ValueError, UnicodeError):
        _emit({'status': 'rejected',
               'reason': 'invalid_role_experience_arguments',
               'detail': 'stdin must contain one JSON object',
               'automatic_retry': False, 'command_sent': False})
        return 1
    if not isinstance(request, dict):
        _emit({'status': 'rejected',
               'reason': 'invalid_role_experience_arguments',
               'detail': 'stdin must contain one JSON object',
               'automatic_retry': False, 'command_sent': False})
        return 1
    store = None
    try:
        store = ExperienceStore(':memory:', profile_id='offline',
            shared_store=shared_path, shared_profile_id='official_standard')
        result = execute_role_experience(store, role=options.role,
            args=request, runtime_run_ref=options.run_ref, offline=True)
    except Exception:
        # Never expose SQL, database paths or tracebacks in the one result line.
        result = {'status': 'unavailable',
                  'reason': 'role_experience_storage_unavailable',
                  'detail': 'role experience storage is unavailable',
                  'automatic_retry': False, 'command_sent': False}
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
    _emit(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

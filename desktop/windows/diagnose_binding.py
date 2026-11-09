"""Read-only process/file-use diagnostics. Never captures keys or opens a DB."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import re
import subprocess


def file_users(files):
    """Query Restart Manager only; never requests shutdown or restart."""
    if not files:
        return []

    class Unique(C.Structure):
        _fields_ = [('pid', W.DWORD), ('start', W.FILETIME)]

    class Info(C.Structure):
        _fields_ = [('process', Unique), ('name', W.WCHAR * 256), ('service', W.WCHAR * 64),
                    ('kind', W.DWORD), ('status', W.DWORD), ('session', W.DWORD), ('restartable', W.BOOL)]

    dll = C.WinDLL('rstrtmgr', use_last_error=True)
    dll.RmStartSession.argtypes = [C.POINTER(W.DWORD), W.DWORD, W.LPWSTR]
    dll.RmRegisterResources.argtypes = [W.DWORD, W.UINT, C.POINTER(W.LPCWSTR), W.UINT,
                                      C.c_void_p, W.UINT, C.c_void_p]
    dll.RmGetList.argtypes = [W.DWORD, C.POINTER(W.UINT), C.POINTER(W.UINT),
                             C.POINTER(Info), C.POINTER(W.DWORD)]
    dll.RmEndSession.argtypes = [W.DWORD]
    for name in ('RmStartSession', 'RmRegisterResources', 'RmGetList', 'RmEndSession'):
        getattr(dll, name).restype = W.DWORD
    session, key = W.DWORD(), C.create_unicode_buffer(33)
    rc = dll.RmStartSession(C.byref(session), 0, key)
    if rc:
        raise RuntimeError(f'RmStartSession: Windows error {rc}')
    try:
        paths = (W.LPCWSTR * len(files))(*(str(path) for path in files))
        rc = dll.RmRegisterResources(session, len(files), paths, 0, None, 0, None)
        if rc:
            raise RuntimeError(f'RmRegisterResources: Windows error {rc}')
        capacity = 64
        for _ in range(3):
            infos = (Info * capacity)()
            needed, count, reasons = W.UINT(), W.UINT(capacity), W.DWORD()
            rc = dll.RmGetList(session, C.byref(needed), C.byref(count), infos, C.byref(reasons))
            if rc == 234 and capacity < needed.value <= 65536:
                capacity = needed.value
                continue
            if rc:
                raise RuntimeError(f'RmGetList: Windows error {rc}')
            if count.value > capacity:
                raise RuntimeError('RmGetList: invalid result size')
            return sorted({infos[i].process.pid for i in range(count.value)})
        raise RuntimeError('RmGetList: process list changed repeatedly')
    finally:
        dll.RmEndSession(session)


def diagnose(executable, base):
    from chat_rescue.windows_backend import main_processes
    from chat_rescue.shards import database_rels, check_paths
    executable = Path(executable).resolve(strict=True)
    if Path(base).is_symlink():
        raise ValueError('Directory symlinks are unsupported')
    base = Path(base).resolve(strict=True)
    if not executable.is_file() or executable.name.casefold() not in ('weixin.exe', 'wechat.exe'):
        raise ValueError('Select the original Weixin.exe / WeChat.exe from preparation step')
    if not base.is_dir() or base.is_symlink() or base.name != 'xwechat_files':
        raise ValueError('Select only the xwechat_files directory, not a drive or backup')
    main = set(main_processes(executable))
    result = {'main_pids': sorted(main), 'accounts': []}
    for index, root in enumerate(sorted(base.glob('*/db_storage')), 1):
        # Never print account usernames, nicknames, message data or key paths.
        suffix = re.search(r'_([0-9a-fA-F]{4})$', root.parent.name)
        entry = {'directory': index, 'suffix': suffix[1] if suffix else 'unknown'}
        try:
            if root.is_symlink() or root.parent.is_symlink():
                raise ValueError('Account symlinks are unsupported')
            rels = database_rels(root)
            check_paths(root, rels)
            groups = {}
            for label, extension in (('db', ''), ('wal', '-wal'), ('shm', '-shm')):
                paths = [root / (rel + extension) for rel in rels]
                if any(path.is_symlink() for path in paths):
                    raise ValueError('Database/sidecar symlinks are unsupported')
                existing = [path for path in paths if path.is_file()]
                users = file_users(existing)
                groups[label] = {'files': len(existing), 'pids': users,
                                 'matched_main_pids': sorted(main.intersection(users))}
            entry['groups'] = groups
        except (ValueError, OSError, RuntimeError) as error:
            # Avoid printing exception messages that may embed account paths.
            entry['error'] = type(error).__name__
            if isinstance(error, RuntimeError) and str(error).startswith('Rm'):
                entry['stage'] = str(error)
        result['accounts'].append(entry)
    return result


def main():
    print('Chat Rescue: read-only process binding diagnostics')
    print('No keys, message contents, account labels, shutdown or restart operations.\n')
    executable = input('Original WeChat EXE path (preparation step): ').strip().strip('"')
    base = input('xwechat_files directory path: ').strip().strip('"')
    try:
        result = diagnose(executable, base)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('Diagnostic failed: ' + type(error).__name__)
        code = getattr(error, 'returncode', getattr(error, 'winerror', None))
        if code is not None:
            print('Error code: ' + str(code))
        return 1
    print('\nCOPY THE FOLLOWING RESULT:\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

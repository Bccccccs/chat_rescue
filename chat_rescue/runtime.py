"""Host services for offline recovery. Never starts or terminates WeChat."""
from contextlib import contextmanager
import csv
import ctypes as C
from functools import lru_cache
import io
import os
from pathlib import Path
import platform
import subprocess


def supported_host():
    if platform.system() == 'Windows' and platform.machine().lower() in ('amd64', 'x86_64', 'arm64'):
        return
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        return
    raise RuntimeError('支持 macOS Apple Silicon 或 Windows 64 位；Windows 为离线实验支持')


def ensure_closed():
    if os.name == 'nt':
        # CSV has no localized headings. A failed query must block writes.
        result = subprocess.run(['tasklist.exe', '/FO', 'CSV', '/NH'],
                                check=True, capture_output=True, encoding='utf-8', errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW)
        rows = list(csv.reader(io.StringIO(result.stdout)))
        if not rows or any(len(row) < 2 or not row[1].isdigit() for row in rows):
            raise RuntimeError('无法核对 Windows 进程清单，拒绝继续')
        running = any(row[0].casefold() in ('wechat.exe', 'weixin.exe', '微信.exe',
                                          'wechatappex.exe', 'weixinappex.exe') for row in rows)
        instruction = '请从微信菜单正常退出所有微信（关闭窗口可能仍驻留托盘）后重试'
    else:
        rows = subprocess.run(['/bin/ps', '-axo', 'comm='], check=True,
                              capture_output=True, text=True).stdout.splitlines()
        running = any(Path(r.strip()).name in ('WeChat', 'Weixin', '微信') for r in rows)
        instruction = '请用 ⌘Q 正常退出原版及副本微信后重试'
    if running:
        raise RuntimeError(instruction)


@contextmanager
def exclusive_lock(path):
    with Path(path).open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            # Lock an existing byte; append mode must not move the lock offset.
            if lock.seek(0, 2) == 0:
                lock.write(b'\0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield


@lru_cache(maxsize=1)
def current_sid():
    result = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                            check=True, capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = list(csv.reader(io.StringIO(result.stdout)))
    sid = rows[0][1] if len(rows) == 1 and len(rows[0]) == 2 else ''
    if not sid.startswith('S-1-') or not all(p.isdigit() for p in sid[2:].split('-')):
        raise RuntimeError('无法核对当前 Windows 用户 SID')
    return sid


def make_private(path):
    path = Path(path)
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    grant = '*' + current_sid() + (':(OI)(CI)F' if path.is_dir() else ':F')
    subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r', grant],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    require_private(path)


def require_private(path):
    if os.name != 'nt':
        if Path(path).stat().st_mode & 0o077:
            raise RuntimeError('key file must be private (chmod 600)')
        return
    # Read the DACL through Win32, independent of localized icacls output.
    from ctypes import wintypes as W
    adv = C.WinDLL('advapi32', use_last_error=True)
    kernel = C.WinDLL('kernel32', use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [W.LPWSTR, W.DWORD, W.DWORD,
                                        C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p),
                                        C.c_void_p, C.POINTER(C.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = W.DWORD
    adv.GetAce.argtypes = [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]
    adv.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    acl, descriptor, owner = C.c_void_p(), C.c_void_p(), C.c_void_p()
    rc = adv.GetNamedSecurityInfoW(str(Path(path).resolve()), 1, 5,
                                  C.byref(owner), None, C.byref(acl), None, C.byref(descriptor))
    if rc:
        raise OSError(rc, '无法读取私有资料的 Windows ACL')
    try:
        if not acl:
            raise RuntimeError('Windows 私有资料不能使用空 DACL')
        count = C.c_ushort.from_address(acl.value + 4).value
        allowed = {current_sid(), 'S-1-5-18', 'S-1-5-32-544'}
        owner_text = W.LPWSTR()
        if not adv.ConvertSidToStringSidW(owner, C.byref(owner_text)):
            raise RuntimeError('无法核对 Windows 文件所有者')
        try:
            if owner_text.value in allowed:
                allowed.add('S-1-3-4')  # OWNER RIGHTS, used by Python's mode 0700
        finally:
            kernel.LocalFree(C.cast(owner_text, C.c_void_p))
        for index in range(count):
            ace = C.c_void_p()
            if not adv.GetAce(acl, index, C.byref(ace)):
                raise RuntimeError('无法检查 Windows ACL 条目')
            kind = C.c_ubyte.from_address(ace.value).value
            flags = C.c_ubyte.from_address(ace.value + 1).value
            if flags & 8 or kind == 1:  # inherit-only, or deny
                continue
            if kind != 0:
                raise RuntimeError('Windows ACL 含不受支持的授权条目')
            sid = W.LPWSTR()
            if not adv.ConvertSidToStringSidW(C.c_void_p(ace.value + 8), C.byref(sid)):
                raise RuntimeError('无法解析 Windows ACL SID')
            try:
                if sid.value not in allowed:
                    raise RuntimeError('Windows 资料可被其他用户访问；请放入仅本人可访问的私有目录')
            finally:
                kernel.LocalFree(C.cast(sid, C.c_void_p))
    finally:
        kernel.LocalFree(descriptor)

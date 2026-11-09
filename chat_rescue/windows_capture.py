"""Experimental, explicitly scoped Windows 4.2 x64 read-only capture.

Config.Cipher layout/mask adapted from TANGandXUE/wcdb-key-tool at
79f1b5b92e12c66aa281b4a60a3c478b5f547dfa (MIT).
Copyright (c) 2025 CloudDreamAI / TANGandXUE. See THIRD_PARTY_NOTICES.md.
No injection, debug privileges, process launches, memory dumps or termination.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time
from .runtime import current_sid, require_private
from .workflow import digest, private_json
from .operations import ALL, check_owner
from .windows_version import is_windows_42_version
from .shards import database_rels, check_paths
from .vendor.capture_keys import verified

NEEDLE = b'com.Tencent.WCDB.Config.Cipher'
MASK = bytes.fromhex('d2c7442458020000004889442450488b450048844c2448488944254048584c24')
LIMIT = 0x800000000000


class ScanFailure(RuntimeError):
    """Counts and canonical relative database names only; never key material."""
    def __init__(self, reason, pages, found, counts):
        required=list(pages)
        verified_rels=[rel for rel in required if rel in found]
        missing=[rel for rel in required if rel not in found]
        self.diagnostic={'version':1,'state':'failed','phase':'key-scan','reason':reason,
                         'required_count':len(required),'verified_count':len(verified_rels),
                         'required_databases':required,'verified_databases':verified_rels,
                         'missing_databases':missing,'counts':dict(counts),'keys_saved':False}
        explanation={'timeout':'扫描超时','feature-not-found':'未找到已适配的 WCDB 特征',
                     'incomplete-hmac':'本次扫描未覆盖全部所选数据库',
                     'no-selected-salt-match':'已解析密钥候选，但候选附带盐值与所选库均不匹配，逐库 HMAC 也未通过；请核对当前微信登录账号与所选目录，仍不匹配时需继续检查该完整版本兼容性'}[reason]
        names='、'.join(missing[:6])+('等' if len(missing)>6 else '')
        super().__init__(f'读取未完成：HMAC 已验证 {len(verified_rels)}/{len(required)} 个库；{explanation}。'
                         f'未通过：{names}。没有保存部分密钥，尚未读取本人昵称。')


def app_info(executable):
    if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError('Windows 实验取钥需要 64 位 Windows Python')
    executable = Path(executable).resolve(strict=True)
    if executable.name.casefold() not in ('weixin.exe', 'wechat.exe'):
        raise ValueError('请明确选择原版 Weixin.exe 或 WeChat.exe')
    with executable.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('不是 Windows PE 程序')
        stream.seek(0x3c); offset_bytes = stream.read(4)
        if len(offset_bytes) != 4:
            raise ValueError('PE 程序头不完整')
        offset = struct.unpack('<I', offset_bytes)[0]
        stream.seek(offset)
        if stream.read(6) != b'PE\0\0\x64\x86':
            raise ValueError('实验布局仅适配 x64 微信程序')
    version = C.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [W.LPCWSTR, C.POINTER(W.DWORD)]
    version.GetFileVersionInfoW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p]
    version.VerQueryValueW.argtypes = [C.c_void_p, W.LPCWSTR, C.POINTER(C.c_void_p), C.POINTER(W.UINT)]
    ignored = W.DWORD()
    size = version.GetFileVersionInfoSizeW(str(executable), C.byref(ignored))
    if not size:
        raise ValueError('微信程序缺少版本资源')
    blob = C.create_string_buffer(size)
    pointer, length = C.c_void_p(), W.UINT()
    if not version.GetFileVersionInfoW(str(executable), 0, size, blob) or not version.VerQueryValueW(blob, '\\', C.byref(pointer), C.byref(length)) or length.value < 52:
        raise ValueError('无法读取微信版本资源')
    fields = C.cast(pointer, C.POINTER(W.DWORD))
    if fields[0] != 0xFEEF04BD:
        raise ValueError('版本资源无效')
    parts = (fields[2] >> 16, fields[2] & 0xffff, fields[3] >> 16, fields[3] & 0xffff)
    if not is_windows_42_version('.'.join(map(str, parts))):
        raise ValueError('Windows 实验取钥目前仅接受 4.2.x.x；不能据此宣称已实机支持')
    env = dict(os.environ, CHAT_RESCUE_INSPECT_EXE=str(executable))
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
              "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop; "
              "$s=Get-AuthenticodeSignature -LiteralPath $env:CHAT_RESCUE_INSPECT_EXE; "
              "@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject}|ConvertTo-Json -Compress")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                env=env, check=True, capture_output=True, encoding='utf-8',
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.CalledProcessError:
        raise RuntimeError('Windows 原生签名检查未完成；请核对系统 PowerShell Security 模块，不绕过签名验证') from None
    signature = json.loads(result.stdout)
    if signature.get('status') != 'Valid' or not re.search('Tencent|腾讯', signature.get('subject', ''), re.I):
        raise ValueError('请使用签名有效的腾讯原版微信程序')
    return {'executable': str(executable), 'file_version': '.'.join(map(str, parts)),
            'sha256': digest(executable), 'signature': 'Valid', 'validation': 'experimental_unverified_client'}


class Process:
    """Read only one explicit PID, after matching image path and token user."""
    def __init__(self, pid, executable):
        self.handle = None
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        k = self.kernel
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; k.OpenProcess.restype = W.HANDLE
        k.CloseHandle.argtypes = [W.HANDLE]
        k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t]
        k.VirtualQueryEx.restype = C.c_size_t
        self.handle = k.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ
        if not self.handle:
            raise RuntimeError('无法只读打开指定 PID；不自动提权，请核对本人微信进程')
        try:
            name, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not k.QueryFullProcessImageNameW(self.handle, 0, name, C.byref(size)) or Path(name.value).resolve() != Path(executable).resolve():
                raise ValueError('指定 PID 与本次原版微信程序不一致')
            self._check_user()
        except BaseException:
            self.close(); raise

    def _check_user(self):
        adv = C.WinDLL('advapi32', use_last_error=True)
        adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
        adv.GetTokenInformation.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
        adv.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
        self.kernel.LocalFree.argtypes = [C.c_void_p]
        token, length = W.HANDLE(), W.DWORD()
        if not adv.OpenProcessToken(self.handle, 8, C.byref(token)):
            raise RuntimeError('无法核对指定微信进程的用户')
        try:
            adv.GetTokenInformation(token, 1, None, 0, C.byref(length))
            if not 0 < length.value < 65536:
                raise RuntimeError('进程用户信息无效')
            data = C.create_string_buffer(length.value)
            if not adv.GetTokenInformation(token, 1, data, length, C.byref(length)):
                raise RuntimeError('无法读取进程用户')
            sid = W.LPWSTR()
            if not adv.ConvertSidToStringSidW(C.cast(data, C.POINTER(C.c_void_p))[0], C.byref(sid)):
                raise RuntimeError('无法核对进程用户 SID')
            try:
                if sid.value != current_sid():
                    raise ValueError('只允许读取当前 Windows 用户自己的微信进程')
            finally:
                self.kernel.LocalFree(C.cast(sid, C.c_void_p))
        finally:
            self.kernel.CloseHandle(token)

    def regions(self):
        class Info(C.Structure):
            _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p), ('allocation_protect', W.DWORD),
                        ('partition', W.WORD), ('size', C.c_size_t), ('state', W.DWORD),
                        ('protect', W.DWORD), ('kind', W.DWORD)]
        address = 0
        while address < LIMIT:
            info = Info()
            if not self.kernel.VirtualQueryEx(self.handle, C.c_void_p(address), C.byref(info), C.sizeof(info)):
                break
            base = info.base or 0
            if not info.size or base + info.size <= address:
                raise RuntimeError('进程内存区间无效')
            if info.state == 0x1000 and not info.protect & 0x101 and info.protect & 0xEE:
                yield base, info.size
            address = base + info.size

    def read(self, address, size):
        if not 0x10000 <= address < LIMIT or not 0 < size <= 2 * 1024 * 1024:
            return b''
        data, count = C.create_string_buffer(size), C.c_size_t()
        if not self.kernel.ReadProcessMemory(self.handle, C.c_void_p(address), data, size, C.byref(count)) or count.value != size:
            return b''
        return data.raw

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle); self.handle = None


def scan(process, pages, deadline):
    """Resolve Config.Cipher objects; retain only keys verified for all selected pages."""
    found={}
    counts={'read_regions':0,'read_chunks':0,'feature_locations':0,'config_objects':0,
            'object_read_failures':0,'invalid_blob_lengths':0,'blob_read_failures':0,
            'key_only_literals':0,'salted_key_literals':0,'hmac_checks':0,
            'salted_literals_without_selected_salt':0,'fallback_hmac_checks':0}
    checked=set()
    def check_time():
        if time.monotonic() >= deadline:
            raise ScanFailure('timeout',pages,found,counts)

    def chunks():
        for base, size in process.regions():
            counts['read_regions']+=1
            tail = b''
            for offset in range(0, size, 1024 * 1024):
                check_time()
                data = process.read(base + offset, min(1024 * 1024, size - offset))
                counts['read_chunks']+=1
                if data:
                    yield base + offset - len(tail), tail + data
                    tail = data[-127:]
                else:
                    tail = b''  # never join across an unreadable gap

    needles = set()
    for base, data in chunks():
        start = 0
        while (pos := data.find(NEEDLE, start)) >= 0:
            needles.add(base + pos); start = pos + 1
            if len(needles) > 256:
                raise ValueError('Config.Cipher 特征过多，拒绝无界扫描')
    counts['feature_locations']=len(needles)
    if not needles:
        raise ScanFailure('feature-not-found',pages,found,counts)
    def u64(data, offset):
        return struct.unpack_from('<Q', data, offset)[0] if len(data) >= offset + 8 else 0
    seen = set()
    for base, data in chunks():
        for address in needles:
            pattern = struct.pack('<QQ', address, len(NEEDLE))
            start = 0
            while (pos := data.find(pattern, start)) >= 0:
                check_time(); start = pos + 1
                node = process.read(base + pos - 0x10, 0x50)
                if u64(node, 0x10) != address or u64(node, 0x18) != len(NEEDLE):
                    continue
                pointer = u64(node, 0x28)
                if not 0x10000 <= pointer < LIMIT or pointer in seen:
                    continue
                seen.add(pointer)
                counts['config_objects']+=1
                obj = process.read(pointer + 0x88, 0x28)
                if len(obj)!=0x28:
                    counts['object_read_failures']+=1;continue
                length = u64(obj, 0x10)
                if not 0 < length <= 1024:
                    counts['invalid_blob_lengths']+=1
                    continue
                blob = process.read(u64(obj, 8), length)
                if len(blob)!=length:
                    counts['blob_read_failures']+=1;continue
                decoded = bytes(value ^ MASK[i % len(MASK)] for i, value in enumerate(blob))
                for match in re.finditer(rb"[xX]'([0-9a-fA-F]{64})([0-9a-fA-F]{32})?'", decoded):
                    key=bytes.fromhex(match[1].decode())
                    salt=bytes.fromhex(match[2].decode()) if match[2] is not None else None
                    counts['key_only_literals' if salt is None else 'salted_key_literals']+=1
                    if salt is not None and not any(page[:16]==salt for page in pages.values()):
                        counts['salted_literals_without_selected_salt']+=1
                    # The embedded salt prioritizes work, but does not prove or
                    # disprove a raw key. Only the selected page's HMAC may accept
                    # a key; never skip all verification on metadata mismatch.
                    ordered=sorted(pages.items(),key=lambda item: salt is not None and item[1][:16]!=salt)
                    for rel, page in ordered:
                        if (key,rel) in checked:continue
                        check_time();counts['hmac_checks']+=1
                        checked.add((key,rel))
                        if salt is not None and page[:16]!=salt:counts['fallback_hmac_checks']+=1
                        if verified(key, page):
                            if rel in found and found[rel] != key.hex():
                                raise ValueError('必要库密钥出现歧义，拒绝保存')
                            found[rel] = key.hex()
                if set(found) == set(pages):
                    return found
    reason='incomplete-hmac'
    if (not found and counts['salted_key_literals']>0 and counts['key_only_literals']==0
            and counts['salted_literals_without_selected_salt']==counts['salted_key_literals']):
        reason='no-selected-salt-match'
    raise ScanFailure(reason,pages,found,counts)


def capture(pid, executable, root, owner, output, timeout=120, *, include_voice=False):
    if not 1 <= pid <= 0xffffffff or not 10 <= timeout <= 300:
        raise ValueError('PID 或超时参数无效；超时范围 10–300 秒')
    metadata = app_info(executable)
    root, output = Path(root).resolve(strict=True), Path(output).resolve()
    check_owner(root, owner)
    from .operations import inspection_files
    from .voice import single_media
    if include_voice:
        single_media(root)
    rels = inspection_files(root, include_voice)
    check_paths(root, [r + s for r in rels for s in ('', '-wal', '-shm')])
    if output.exists() or output.with_suffix('.capture.json').exists() or output.with_suffix('.capture-failed.json').exists():
        raise FileExistsError('输出或回执已存在，请保留现场并使用新文件名')
    if output.is_relative_to(root):
        raise ValueError('密钥输出必须位于微信目录之外')
    require_private(output.parent)
    pages = {}
    for rel in rels:
        with (root / rel).open('rb') as stream:
            page = stream.read(4096)
        if len(page) != 4096 or page.startswith(b'SQLite format 3\0'):
            raise ValueError('必要库不是已知 SQLCipher 4 首页：' + rel)
        pages[rel] = page
    process = Process(pid, metadata['executable'])
    try:
        keys = scan(process, pages, time.monotonic() + timeout)
    except ScanFailure as error:
        failure_path=output.with_suffix('.capture-failed.json')
        report={**error.diagnostic,'file_version':metadata.get('file_version','unknown'),'executable_sha256':metadata['sha256']}
        try:private_json(failure_path,report)
        except OSError:
            raise RuntimeError(str(error)+' 诊断报告写入失败，请保留当前任务目录。') from None
        raise RuntimeError(str(error)+' 诊断报告：'+str(failure_path)) from None
    finally:
        process.close()
    if (set(keys) != set(rels) or inspection_files(root, include_voice) != rels
            or digest(metadata['executable']) != metadata['sha256']):
        raise RuntimeError('四库覆盖不足或程序文件改变，拒绝保存')
    for rel, page in pages.items():
        with (root / rel).open('rb') as stream:
            if stream.read(16) != page[:16]:
                raise RuntimeError('取钥期间数据库 salt 改变，拒绝保存')
    # Verify full database integrity with the acquired keys before committing.
    from .vendor.sqlcipher_probe import DB
    for rel in rels:
        with DB(root / rel, keys[rel]) as database:
            if database.query('PRAGMA integrity_check;') != [{'integrity_check': 'ok'}]:
                raise RuntimeError('四库完整性验证未通过，拒绝保存')
    if include_voice:
        single_media(root)
    if inspection_files(root, include_voice) != rels:
        raise RuntimeError('验证期间分片清单改变，拒绝保存')
    private_json(output, {'keys': {rel: {'enc_key': keys[rel]} for rel in rels}})
    receipt = output.with_suffix('.capture.json')
    private_json(receipt, {**metadata, 'state': 'verified', 'verified_required_databases': len(rels),
                           'verified_databases': list(rels),
                           'owner': owner, 'database_root': str(root), 'pid': pid})
    return {'keys': str(output), 'receipt': str(receipt), 'verified_required_databases': len(rels),
            'instruction': '本次所有消息分片及必要辅助库验证通过；请本人正常退出微信，随后运行 verify-keys 与 export-db。客户端显示仍未验收。'}

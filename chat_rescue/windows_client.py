"""Verify an explicitly reviewed, complete official client payload; never run it."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import urlsplit
from .windows_capture import app_info


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or str(PurePosixPath(name)) != name
            or any(p in ('.', '..') or p.endswith((' ', '.')) for p in PurePosixPath(name).parts)):
        raise ValueError('固定微信清单路径无效')
    if any(p.casefold() in ('xwechat_files', 'db_storage', 'app_data', 'msg', 'keys.json')
           for p in PurePosixPath(name).parts) or re.search(r'\.db(?:-(?:wal|shm))?$', name, re.I):
        raise ValueError('固定微信载荷不得包含账号资料')
    return name


def manifest(path, expected=None):
    path = Path(path)
    if path.is_symlink() or path.is_junction() or not path.is_file() or path.stat().st_size > 4*1024*1024:
        raise ValueError('固定微信清单不是受支持的普通文件')
    if expected is not None and (not re.fullmatch('[0-9a-f]{64}', expected) or digest(path) != expected):
        raise ValueError('固定微信清单哈希不匹配')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('version') != 1 or not re.fullmatch(r'4\.2\.[0-9]+\.[0-9]+', value.get('file_version', '')):
        raise ValueError('固定微信清单版本不受支持')
    url = urlsplit(value.get('source_url', ''))
    if url.scheme != 'https' or url.hostname not in ('dldir1.qq.com', 'dldir1v6.qq.com', 'pc.weixin.qq.com', 'weixin.qq.com'):
        raise ValueError('固定微信须记录腾讯官方来源')
    files = value.get('files')
    if not isinstance(files, dict) or not 2 <= len(files) <= 20000:
        raise ValueError('固定微信须包含完整程序依赖清单')
    seen = set()
    for name, hash_value in files.items():
        relative(name)
        if name.casefold() in seen or not isinstance(hash_value, str) or not re.fullmatch('[0-9a-f]{64}', hash_value):
            raise ValueError('固定微信清单重复路径或哈希无效')
        seen.add(name.casefold())
    exe = relative(value.get('executable'))
    if exe not in files or PurePosixPath(exe).name.casefold() not in ('weixin.exe', 'wechat.exe'):
        raise ValueError('固定微信清单缺少明确的主程序')
    return value


def validate(root, manifest_path, expected=None):
    root = Path(root)
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError('固定微信目录不能使用链接')
    value = manifest(manifest_path, expected)
    observed = {}
    # Walk without following any link, including directory junctions.
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            if path.is_symlink() or path.is_junction():
                raise ValueError('固定微信文件或目录不能使用链接')
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                name = relative(path.relative_to(root).as_posix())
                if name not in value['files']:
                    raise ValueError('固定微信目录含清单外文件')
                observed[name] = digest(path)
            else:
                raise ValueError('固定微信仅允许普通文件')
    if observed != value['files']:
        raise ValueError('固定微信文件缺失或哈希改变')
    result = app_info(root / value['executable'])
    if result.get('signature') != 'Valid' or result.get('file_version') != value['file_version']:
        raise ValueError('固定微信版本或腾讯签名不匹配')
    return {**result, 'executable':str((root / value['executable']).resolve()), 'pinned_client':True,
            'data_isolation_verified':False}


def install(source, manifest_path, payload):
    validate(source, manifest_path)
    source, payload = Path(source), Path(payload)
    value = manifest(manifest_path)
    target = payload / 'wechat'
    target.mkdir(exist_ok=False)
    for name in value['files']:
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, out)
    installed_manifest = payload / 'wechat-manifest.json'
    with installed_manifest.open('xb') as stream:
        stream.write(Path(manifest_path).read_bytes())
    validate(target, installed_manifest)
    return {'executable':'wechat/' + value['executable'], 'client_manifest':'wechat-manifest.json',
            'client_manifest_sha256':digest(installed_manifest)}

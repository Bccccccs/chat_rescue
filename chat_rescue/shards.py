"""Inventory message databases without opening live chat contents."""
from pathlib import Path
import re


def message_shards(root):
    root = Path(root)
    directory = root / 'message'
    if root.is_symlink() or directory.is_symlink():
        raise ValueError('消息分片目录不能使用符号链接')
    found = []
    for path in directory.glob('message_*'):
        match = re.fullmatch(r'message_(\d+)\.db(?:-wal|-shm)?', path.name)
        if not match:
            continue
        number = match[1]
        if number != str(int(number)) or path.is_symlink() or not path.is_file():
            raise ValueError('消息分片路径不受支持：' + path.name)
        database = directory / ('message_' + number + '.db')
        if not database.is_file():
            raise ValueError('消息分片仅有 WAL/SHM，缺少数据库：' + database.name)
        if path == database:
            found.append((int(number), 'message/' + path.name))
    found.sort()
    if not found or found[0][0] != 0:
        raise ValueError('消息目录缺少 message_0.db；请选择账号的 db_storage 根目录')
    return tuple(rel for _, rel in found)

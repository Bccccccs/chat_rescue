"""One validated file inventory for message shards and their companion databases."""
from pathlib import Path
import re

COMPANIONS = ('message/message_resource.db', 'session/session.db', 'contact/contact.db')
MESSAGE_NAME = re.compile(r'message_(0|[1-9][0-9]*)\.db')
SIDECAR_NAME = re.compile(r'(message_[0-9]+\.db)-(wal|shm)')


def message_rels(root):
    root = Path(root)
    directory = root / 'message'
    if directory.is_symlink():
        raise ValueError('message directory must not be a symbolic link')
    names = []
    for path in directory.glob('message_*'):
        match = MESSAGE_NAME.fullmatch(path.name)
        if match:
            if path.is_symlink() or not path.is_file():
                raise ValueError('message shard must be a regular file')
            names.append(path.name)
        elif re.fullmatch(r'message_[0-9]+\.db', path.name):
            raise ValueError('noncanonical message shard filename')
        elif (sidecar := SIDECAR_NAME.fullmatch(path.name)):
            if path.is_symlink() or not (directory / sidecar[1]).is_file():
                raise ValueError('orphan or symbolic message shard sidecar')
    if 'message_0.db' not in names:
        raise ValueError('missing required message/message_0.db')
    return tuple('message/' + name for name in sorted(names, key=lambda n: int(MESSAGE_NAME.fullmatch(n)[1])))


def mutable_rels(root):
    return (*message_rels(root), *COMPANIONS[:2])


def database_rels(root):
    return (*message_rels(root), *COMPANIONS)


def check_paths(root, rels):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('database inventory root must not be a symbolic link')
    for rel in rels:
        path = root / rel
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
            raise ValueError('database or sidecar path must not be a symbolic link')


def validate_plan_files(root, plan):
    """Validate exact sets before any caller uses a manifest path for file I/O."""
    rels = mutable_rels(root)
    allowed = {r + suffix for r in database_rels(root) for suffix in ('', '-wal', '-shm')}
    if plan.get('version') != 1 or set(plan.get('candidates', {})) != set(rels):
        raise ValueError('计划候选分片清单与目标不匹配')
    if set(plan.get('originals', {})) != allowed:
        raise ValueError('计划原件分片与 DB/WAL/SHM 清单不完整或已改变')
    check_paths(root, allowed)
    return rels


def receipt_covers(root, receipt):
    rels = database_rels(root)
    if receipt.get('verified_required_databases') != len(rels):
        return False
    recorded = receipt.get('verified_databases')
    # Old receipts explicitly verified exactly the original four databases.
    return (len(rels) == 4 if recorded is None else
            isinstance(recorded, list) and len(recorded) == len(rels) and set(recorded) == set(rels))

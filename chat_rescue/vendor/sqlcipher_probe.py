# Copyright (c) 2026 Contributors; MIT. Adapted from pinned acmerfight Gist.
#!/usr/bin/env python3
"""SQLCipher 4 read-only probe. No key extraction; no plaintext export.

Keys enter through a mode-0600 file, never command-line arguments.
Production opens are SQLITE_OPEN_READONLY; only self-test creates toy databases.
"""
import argparse
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


def library():
    candidates = [os.environ.get('SQLCIPHER_LIBRARY'),
                  '/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib',
                  '/usr/local/opt/sqlcipher/lib/libsqlcipher.dylib',
                  ctypes.util.find_library('sqlcipher')]
    name = next((x for x in candidates if x and (not x.startswith('/') or Path(x).exists())), None)
    if not name:
        raise RuntimeError('SQLCipher library missing; install with brew install sqlcipher')
    lib = C.CDLL(name)
    lib.sqlite3_open_v2.argtypes = [C.c_char_p, C.POINTER(C.c_void_p), C.c_int, C.c_char_p]
    lib.sqlite3_open_v2.restype = C.c_int
    lib.sqlite3_close.argtypes = [C.c_void_p]
    lib.sqlite3_key.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
    lib.sqlite3_key.restype = C.c_int
    lib.sqlite3_exec.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p]
    lib.sqlite3_exec.restype = C.c_int
    lib.sqlite3_busy_timeout.argtypes = [C.c_void_p, C.c_int]
    return lib


class DB:
    def __init__(self, path, key, *, fixture_write=False):
        if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
            raise ValueError('expected 64 hex characters for raw SQLCipher key')
        self.lib = library()
        self.ptr = C.c_void_p()
        flags = 6 if fixture_write else 1  # READWRITE|CREATE, or READONLY
        rc = self.lib.sqlite3_open_v2(os.fsencode(path), C.byref(self.ptr), flags, None)
        if rc:
            self.close()
            raise RuntimeError(f'SQLCipher open failed (code {rc})')
        try:
            key_arg = ("x'" + key + "'").encode('ascii')
            rc = self.lib.sqlite3_key(self.ptr, key_arg, len(key_arg))
            if rc:
                raise RuntimeError(f'SQLCipher key setup failed (code {rc})')
            self.query('PRAGMA cipher_compatibility=4;')
            self.lib.sqlite3_busy_timeout(self.ptr, 2000)
            if not fixture_write:
                self.query('PRAGMA query_only=ON;')
            self.query('SELECT count(*) FROM sqlite_master;')
        except BaseException:
            self.close()
            raise

    def query(self, sql):
        rows = []
        callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int,
                                   C.POINTER(C.c_char_p), C.POINTER(C.c_char_p))
        def collect(_, n, values, names):
            rows.append({names[i].decode(): values[i].decode('utf-8', 'replace')
                         if values[i] is not None else None for i in range(n)})
            return 0
        callback = callback_type(collect)
        rc = self.lib.sqlite3_exec(self.ptr, sql.encode(), callback, None, None)
        if rc:
            # No raw SQL or native error strings, since those may reveal secrets/content.
            raise RuntimeError(f'SQLCipher query failed (code {rc})')
        return rows

    def close(self):
        if self.ptr:
            self.lib.sqlite3_close(self.ptr)
            self.ptr = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def load_key(path, db_path):
    p = Path(path)
    if p.stat().st_mode & 0o077:
        raise RuntimeError('key file must be private (chmod 600)')
    data = json.loads(p.read_text())
    if isinstance(data, dict) and isinstance(data.get('keys'), dict):
        data = data['keys']
    if not isinstance(data, dict):
        raise RuntimeError('key manifest must be an object')
    target = Path(db_path).resolve()
    with target.open('rb') as f:
        salt = f.read(16).hex()
    entries = []
    for name, value in data.items():
        normalized = name.replace('\\', '/')
        absolute_match = normalized.startswith('/') and Path(normalized).resolve() == target
        if normalized == salt or str(target).endswith('/' + normalized) or absolute_match:
            if isinstance(value, dict):
                value = value.get('enc_key', value.get('key'))
            if isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value):
                entries.append(value.lower())
    if len(set(entries)) != 1:
        raise RuntimeError('missing or ambiguous key for this database')
    return entries[0]

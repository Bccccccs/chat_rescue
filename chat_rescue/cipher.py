"""SQLCipher copies with explicit preservation of implicit mapping rowids."""
import ctypes as C
import os
from pathlib import Path
from .vendor.sqlcipher_probe import DB, library, load_key
from .workflow import ident, digest


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _open(path):
    d = DB.__new__(DB)
    d.lib, d.ptr = library(), C.c_void_p()
    if d.lib.sqlite3_open_v2(os.fsencode(path), C.byref(d.ptr), 6 | 64, None):
        d.close()
        raise RuntimeError('cannot create SQLCipher destination')
    return d


def _copy(source, output, source_key=None, destination_key=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError('destination already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    with _open(output) as d:
        if destination_key:
            d.query('PRAGMA key=' + literal("x'" + destination_key + "'") + ';PRAGMA cipher_compatibility=4;')
        uri = Path(source).resolve().as_uri() + '?mode=ro'
        d.query('ATTACH DATABASE ' + literal(uri) + ' AS original KEY ' + literal("x'" + source_key + "'" if source_key else '') + ';')
        if source_key:
            d.query('PRAGMA original.cipher_compatibility=4;')
        d.query("SELECT sqlcipher_export('main','original');")
        # Mapping rowids are foreign identities. Export normally renumbers them.
        # Virtual/FTS shadow tables must remain under virtual-table control.
        tables = d.query('PRAGMA original.table_list;')
        for t in tables:
            name = t['name']
            if t['type'] != 'table' or t['wr'] != '0' or name.startswith('sqlite_'):
                continue
            columns = d.query('PRAGMA original.table_info(' + ident(name) + ');')
            if any(c['pk'] != '0' and c['type'].upper() == 'INTEGER' for c in columns):
                continue
            names = ','.join(ident(c['name']) for c in columns)
            d.query('DELETE FROM main.' + ident(name) + ';INSERT INTO main.' + ident(name) + '(rowid,' + names + ') SELECT rowid,' + names + ' FROM original.' + ident(name) + ';')
            mismatch = d.query('SELECT count(*) AS n FROM (SELECT rowid,' + names + ' FROM original.' + ident(name) + ' EXCEPT SELECT rowid,' + names + ' FROM main.' + ident(name) + ');')[0]['n']
            if mismatch != '0':
                raise RuntimeError('implicit rowid validation failed')
        for pragma in ('user_version', 'application_id'):
            value = next(iter(d.query('PRAGMA original.' + pragma + ';')[0].values()))
            d.query('PRAGMA main.' + pragma + '=' + str(int(value)) + ';')
        for t in tables:
            if t['name'].startswith('sqlite_') or t['type'] not in ('table', 'virtual'):
                continue
            name = ident(t['name'])
            if d.query('SELECT count(*) AS n FROM original.' + name) != d.query('SELECT count(*) AS n FROM main.' + name):
                raise RuntimeError('table count validation failed')
        if d.query('PRAGMA main.integrity_check;') != [{'integrity_check': 'ok'}]:
            raise RuntimeError('destination integrity check failed')
        d.query('DETACH DATABASE original;')
    os.chmod(output, 0o600)


def export_database(source, output, keys):
    source = Path(source)
    before = {str(p): digest(p) for p in (source, Path(str(source) + '-wal')) if p.exists()}
    _copy(source, output, source_key=load_key(keys, source))
    if any(not Path(p).exists() or digest(p) != h for p, h in before.items()):
        raise RuntimeError('source changed during export')


def encrypt_database(plain, output, template, keys):
    template = Path(template)
    salt = template.read_bytes()[:16]
    _copy(plain, output, destination_key=load_key(keys, template) + salt.hex())
    if Path(output).read_bytes()[:16] != salt:
        raise RuntimeError('encrypted candidate salt changed')
    with DB(output, load_key(keys, template)) as d:
        if d.query('PRAGMA integrity_check;') != [{'integrity_check': 'ok'}]:
            raise RuntimeError('encrypted candidate integrity check failed')

"""Windows B-to-A conversation rebuild, prepared entirely in private copies."""
import json
import platform
import shutil
from pathlib import Path
from . import operations as ops
from .cipher import encrypt_database
from .workflow import (connect, write_connect, ident, table_for, digest, private_json,
                       plain_profile, WINDOWS_PROFILES, save_chat, prepare_plain_restore)
from .shards import database_rels, message_rels, mutable_rels

MODE = 'replace-a-from-b'


def replacement_base(exported, output, owner, peer):
    """Clear only the selected chat in a new derived copy; retain immutable export."""
    output = ops.fresh(output)
    for rel in database_rels(exported):
        dest = output / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(exported) / rel, dest)
    removed = 0
    table = table_for(peer)
    for rel in message_rels(output):
        with write_connect(output / rel) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                removed += c.execute('SELECT count(*) FROM ' + ident(table)).fetchone()[0]
                c.execute('DROP TABLE ' + ident(table))
    with write_connect(output / 'message/message_resource.db') as c:
        c.execute('DELETE FROM MessageResourceDetail WHERE message_id IN '
                  '(SELECT r.message_id FROM MessageResourceInfo r JOIN ChatName2Id ch '
                  'ON ch.rowid=r.chat_id WHERE ch.user_name=?)', (peer,))
        c.execute('DELETE FROM MessageResourceInfo WHERE chat_id IN '
                  '(SELECT rowid FROM ChatName2Id WHERE user_name=?)', (peer,))
    # Keep A's draft while allowing the source's last imported message to rebuild
    # the visible preview. Never copy B's draft into A.
    with write_connect(output / 'session/session.db') as c:
        old = c.execute('SELECT draft FROM SessionTable WHERE username=?', (peer,)).fetchone()
        c.execute('DELETE FROM SessionTable WHERE username=?', (peer,))
    private_json(output / 'direction-profile.json', {'version': 1, 'direction_profile': plain_profile(exported)})
    return removed, old[0] if old else ''


def make_replace_plan(a, b, output):
    if platform.system() != 'Windows':
        raise ValueError('单会话替换预览目前仅提供 Windows 实验版')
    ops.supported_host(); ops.ensure_closed()
    if a['owner'] == b['owner'] or Path(a['root']).resolve() == Path(b['root']).resolve():
        raise ValueError('A 和 B 必须是两个不同账号及独立目录')
    output = Path(output).resolve()
    accounts = {'A': a, 'B': b}
    for item in accounts.values():
        ops.check_owner(item['root'], item['owner'])
        root = Path(item['root']).resolve(strict=True)
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('计划目录必须位于微信数据库目录之外')
    output = ops.fresh(output)
    reports, nicknames = {}, {}
    # B is the sole message source. Never validate A as an outgoing bundle.
    for slot in ('B', 'A'):
        item = accounts[slot]
        plain = output / (slot + '-plain')
        ops.export_root(item['root'], item['keys'], plain)
        reports[slot] = json.loads((plain / 'export-report.json').read_text(encoding='utf-8'))
        if plain_profile(plain) not in WINDOWS_PROFILES:
            raise ValueError('单向重建需要双方配套 Windows 取钥回执，不能使用 legacy 配置')
        with connect(plain / 'contact/contact.db') as c:
            row = c.execute('SELECT nick_name FROM contact WHERE username=?', (item['owner'],)).fetchone()
            if row is None:
                raise ValueError('无法核对本次账号身份')
            nicknames[slot] = row[0] or '昵称未设置'
    source = save_chat(output / 'B-plain', b['owner'], a['owner'], output / 'B-chat', exclude_system_messages=True)
    removed, draft = replacement_base(output / 'A-plain', output / 'replacement-base', a['owner'], b['owner'])
    directory = ops.fresh(output / 'A-plan')
    result = prepare_plain_restore(output / 'B-chat', output / 'replacement-base', a['owner'], directory / 'recovered-plain')
    with write_connect(directory / 'recovered-plain/session/session.db') as c:
        c.execute('UPDATE SessionTable SET draft=? WHERE username=?', (draft, b['owner']))
        if source['excluded_system_messages']:
            c.execute("UPDATE SessionTable SET summary='' WHERE username=?", (b['owner'],))
    result.update(mode=MODE, removed=removed, excluded_system_messages=source['excluded_system_messages'])
    for rel in mutable_rels(a['root']):
        with connect(directory / 'recovered-plain' / rel) as c:
            if [tuple(r) for r in c.execute('PRAGMA integrity_check')] != [('ok',)]:
                raise ValueError('替换候选完整性检查失败')
        encrypt_database(directory / 'recovered-plain' / rel, directory / 'candidates' / rel,
                         Path(a['root']) / rel, a['keys'])
    plan = {'version': 1, 'target_root': str(Path(a['root']).resolve()), 'target_owner': a['owner'],
            'originals': reports['A']['originals'], 'candidates': {r: digest(directory / 'candidates' / r)
            for r in mutable_rels(a['root'])}, 'report': result}
    private_json(directory / 'plan.json', plan)
    private_json(directory / 'preview.json', result)
    ops.ensure_closed()
    for slot, item in accounts.items():
        if ops.state(item['root']) != reports[slot]['originals']:
            raise ValueError('预览期间账号库发生变化，请保留任务并重新生成')
    # A new coordinator version makes older desktop builds refuse this destructive
    # mode instead of displaying it as an ordinary additive sync preview.
    manifest = {'version': 4, 'mode': MODE, 'capabilities': ['windows-single-chat-replacement'], 'accounts': {s: {'owner': v['owner'], 'root': str(Path(v['root']).resolve()),
                'nickname': nicknames[s]} for s, v in accounts.items()}, 'originals': {s: r['originals'] for s, r in reports.items()},
                'directions': {'A': {**result, 'state': 'ready', 'plan': str(directory)},
                               'B': {'added': 0, 'skipped': 0, 'state': 'source_only', 'plan': None}}}
    private_json(output / 'sync-plan.json', manifest)
    return {'plan': str(output), **manifest}

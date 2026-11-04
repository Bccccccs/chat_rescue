"""Read-only text-direction evidence from immutable A/B exports. No key reads."""
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import uuid
from chat_rescue.workflow import connect, digest, ident, table_for, readvar, plain_profile, private_json
from chat_rescue.shards import database_rels, message_rels, check_paths
from chat_rescue.runtime import require_private


def packed_fields(data, sender_id):
    if not isinstance(data, bytes):
        return None, False
    found, pos = {}, 0
    try:
        while pos < len(data):
            tag, pos = readvar(data, pos)
            field, wire = tag >> 3, tag & 7
            value = None
            if wire == 0:
                value, pos = readvar(data, pos)
            elif wire in (1, 5):
                pos += 8 if wire == 1 else 4
            elif wire == 2:
                size, pos = readvar(data, pos)
                pos += size
            else:
                return None, False
            if pos > len(data) or field == 0:
                return None, False
            if field in (1, 2):
                if field in found or wire != 0:
                    return None, False
                found[field] = value
    except ValueError:
        return None, False
    return found.get(2), set(found) == {1, 2} and found[1] == sender_id


def read_side(root, owner, peer):
    root = Path(root)
    report = json.loads((root/'export-report.json').read_text(encoding='utf-8'))
    account = report.get('account_directory', '')
    if account != owner and not account.startswith(owner + '_'):
        raise ValueError('导出身份不匹配')
    rels = database_rels(root)
    check_paths(root, rels)
    hashes = report.get('exported', {})
    if set(hashes) != set(rels) or any(digest(root/r) != hashes[r] for r in rels):
        raise ValueError('导出清单或哈希不匹配')
    rows, groups, collisions = {}, Counter(), set()
    table = table_for(peer)
    for rel in message_rels(root):
        with connect(root/rel) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            names = {r['rowid']: r['user_name'] for r in c.execute('SELECT rowid,user_name FROM Name2Id WHERE user_name IN (?,?)', (owner, peer))}
            for r in c.execute('SELECT server_id,real_sender_id,packed_info_data,status,origin_source,message_content,compress_content FROM '+ident(table)+' WHERE local_type=1'):
                sender = names.get(r['real_sender_id'])
                role = 'self' if sender == owner else 'peer' if sender == peer else 'outside'
                flag, valid = packed_fields(r['packed_info_data'], r['real_sender_id'])
                status = r['status'] if type(r['status']) is int else None
                origin = r['origin_source'] if type(r['origin_source']) is int else None
                reference = ('self' if names.get(flag) == owner else
                             'peer' if names.get(flag) == peer else 'not_a_participant')
                groups[(role, flag, valid, status, origin, rel,
                        flag == r['real_sender_id'], reference)] += 1
                sid = r['server_id']
                if type(sid) is not int or sid <= 0:
                    continue
                row = (sender, flag, valid, status, r['message_content'], r['compress_content'])
                if sid in rows and rows[sid] != row:
                    collisions.add(sid)
                rows[sid] = row
    for sid in collisions:
        rows.pop(sid, None)
    if any(digest(root/r) != hashes[r] for r in rels):
        raise ValueError('诊断期间导出发生变化')
    summary = {'profile': plain_profile(root), 'text_rows': sum(groups.values()),
               'conflicting_ids_not_paired': len(collisions),
               'groups': [{'sender': k[0], 'field2': k[1], 'sender_field_valid': k[2],
                           'status': k[3], 'origin_source': k[4], 'message_shard': k[5],
                           'field2_equals_real_sender_id': k[6],
                           'field2_Name2Id_role': k[7], 'count': v}
                          for k, v in sorted(groups.items(), key=lambda item: repr(item[0]))]}
    return summary, rows


def diagnose(a_plain, b_plain, owner_a, owner_b):
    if owner_a == owner_b or '@' in owner_a or '@' in owner_b:
        raise ValueError('需要两个不同的已核对单聊身份')
    a, ar = read_side(a_plain, owner_a, owner_b)
    b, br = read_side(b_plain, owner_b, owner_a)
    pairs, different = Counter(), 0
    for sid in ar.keys() & br.keys():
        x, y = ar[sid], br[sid]
        if x[0] not in (owner_a, owner_b) or x[0] != y[0] or x[4:] != y[4:] or not (x[2] and y[2]):
            different += 1
            continue
        actor = 'B_sent_A_received' if x[0] == owner_b else 'A_sent_B_received'
        pairs[(actor, x[1], y[1], x[3], y[3])] += 1
    return {'version': 2, 'diagnostic_only': True, 'live_databases_opened': False,
            'A': a, 'B': b, 'unmatched_content_or_sender_pairs': different,
            'verified_pairs': [{'direction': k[0], 'A_field2': k[1], 'B_field2': k[2],
                                'A_status': k[3], 'B_status': k[4], 'count': v}
                               for k, v in sorted(pairs.items(), key=lambda item: repr(item[0]))]}


def main():
    workspace = Path(os.environ.get('LOCALAPPDATA', ''))/'ChatRescue/Desktop'
    settings_path = workspace/'settings.json'
    require_private(settings_path)
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    a, b = settings['accounts']
    # Select an existing export by the currently selected account directories,
    # never choose an account by recency or open a live database.
    candidates = []
    for task in (workspace/'jobs').glob('*/sync'):
        paths = [task/(s+'-plain')/'export-report.json' for s in ('A', 'B')]
        if all(p.is_file() for p in paths):
            reports = [json.loads(p.read_text(encoding='utf-8')) for p in paths]
            if all(r.get('account_directory') == Path(item['root']).parent.name for r, item in zip(reports, (a, b))):
                candidates.append((max(p.stat().st_mtime_ns for p in paths), task))
    if not candidates:
        raise ValueError('未找到与当前 A、B 目录匹配的完整导出，请保留失败任务')
    task = max(candidates, key=lambda item: item[0])[1]
    result = diagnose(task/'A-plain', task/'B-plain', a['owner'], b['owner'])
    output = workspace/('direction-diagnostic-'+uuid.uuid4().hex+'.json')
    private_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('\n只读诊断完成。摘要没有正文、密钥、账号标识、真实编号或路径。')
    print('结果文件名：'+output.name)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print('只读诊断未完成；错误类别：'+type(error).__name__+'。未修改任何聊天库。')
        raise SystemExit(1)

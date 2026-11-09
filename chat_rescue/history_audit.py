"""Compare private source, candidate and current-A snapshots without applying changes."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from chat_rescue.workflow import connect, ident, table_for, digest, private_json, pack
from chat_rescue.voice import single_media
from chat_rescue.shards import message_rels, database_rels, mutable_rels, check_paths
from chat_rescue.operations import fresh, export_root, check_owner
from chat_rescue.runtime import require_private, ensure_closed
from chat_rescue.schema_audit import verify_snapshot

TZ = timezone(timedelta(hours=8))
DEFERRED = {10000, 10002}


def snapshot_options(manifest):
    if manifest.get('version')==4:return {}
    if manifest.get('version')==5:
        from chat_rescue.media import windows_options
        caps=manifest.get('capabilities',[])
        if caps[-2:]!=['windows-single-chat-replacement','windows-verified-shard-routing']:
            raise ValueError('unknown replacement media capabilities')
        return windows_options({'version':5,'capabilities':caps[:-2]})
    raise ValueError('unsupported replacement audit version')


def error_details(error, stage):
    """Explain known guard failures without exposing arbitrary paths or key data."""
    known = {
        '请从微信菜单正常退出所有微信（关闭窗口可能仍驻留托盘）后重试':
            ('wechat_running','微信或微信子进程仍在运行','请从微信菜单正常退出所有微信，包括托盘中的微信，再运行检查。'),
        '请用 ⌘Q 正常退出原版及副本微信后重试':
            ('wechat_running','微信仍在运行','请正常退出原版及副本微信后再运行检查。'),
        '无法核对 Windows 进程清单，拒绝继续':
            ('process_check_failed','无法核对 Windows 进程清单','请提供此报告；当前库快照未执行。'),
        'Windows 资料可被其他用户访问；请放入仅本人可访问的私有目录':
            ('private_acl_failed','设置或密钥文件可被其他用户访问','请提供此报告以定位文件权限；不要关闭权限检查。'),
        'Windows 私有资料不能使用空 DACL':
            ('private_acl_failed','私有文件权限为空','请提供此报告以定位文件权限；不要关闭权限检查。'),
        'key file must be private (chmod 600)':
            ('private_acl_failed','文件访问权限不符合私有资料要求','请提供此报告以定位文件权限。'),
        'no matching saved replacement task':
            ('matching_task_missing','当前所选账号与已有替换任务不匹配','请提供此报告，保留已有任务，不重新应用恢复。'),
    }
    code,reason,action=known.get(str(error),(
        'unclassified_error','该步骤未完成：'+type(error).__name__,
        '请提供 history-audit.json 和 progress.log，保留已有任务，不重新应用恢复。'))
    if isinstance(error,FileNotFoundError):
        code,reason='local_file_missing','此步骤需要的本机文件不存在'
    return {'stage':stage,'code':code,'reason':reason,'next_action':action}


class Progress:
    def __init__(self, output):
        self.output = output
        self.started = time.monotonic()
        self.lock = threading.Lock()

    def say(self, message):
        line = '[%ds] %s' % (time.monotonic() - self.started, message)
        with self.lock:
            print(line, flush=True)
            with (self.output/'progress.log').open('a', encoding='utf-8') as stream:
                stream.write(line+'\n')

    @contextmanager
    def stage(self, message):
        self.say(message)
        stopped = threading.Event()
        def heartbeat():
            while not stopped.wait(10):
                self.say('仍在处理：'+message)
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stopped.set()
            worker.join()


def save_report(output, report):
    """Persist a valid partial report before expensive later stages."""
    temporary = output/('report-'+uuid.uuid4().hex+'.tmp')
    private_json(temporary, report)
    temporary.replace(output/'history-audit.json')


def diagnostic_conversation(root, owner, peer, output):
    """One scan of selected-chat rows; no resource joins or restore bundle."""
    root = Path(root)
    rels = message_rels(root)
    check_paths(root, rels)
    hashes = {rel:digest(root/rel) for rel in rels}
    messages = []
    for rel in rels:
        with connect(root/rel) as c:
            names = {r['rowid']:r['user_name'] for r in c.execute(
                'SELECT rowid,user_name FROM Name2Id WHERE user_name IN (?,?)', (owner,peer))}
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_for(peer),)).fetchone():
                continue
            for row in c.execute('SELECT * FROM '+ident(table_for(peer))):
                fields = dict(row)
                messages.append({'source_shard':rel, 'sender':names.get(fields.get('real_sender_id')),
                                 'fields':pack(fields)})
    messages.sort(key=lambda m:(m['fields'].get('create_time') or 0,
                                m['fields'].get('sort_seq') or 0,
                                m['source_shard'],m['fields'].get('local_id') or 0))
    if any(digest(root/rel)!=value for rel,value in hashes.items()):
        raise ValueError('snapshot changed during diagnostic JSON export')
    output = fresh(output)
    private_json(output/'conversation.json', {'diagnostic_only':True, 'not_a_restore_bundle':True,
        'resources_omitted':True, 'owner':owner, 'peer':peer, 'messages':messages})
    return len(messages)


def month(value):
    try:
        return datetime.fromtimestamp(value, TZ).strftime('%Y-%m')
    except (ValueError, OSError, OverflowError, TypeError):
        return 'invalid_timestamp'


def summarize(records):
    months = Counter(month(row[1]) for row in records)
    dates = [row[1] for row in records if month(row[1]) != 'invalid_timestamp']
    return {'rows': len(records), 'unique_positive_ids': len({r[0] for r in records if type(r[0]) is int and r[0] > 0}),
            'first_date': datetime.fromtimestamp(min(dates), TZ).strftime('%Y-%m-%d') if dates else None,
            'last_date': datetime.fromtimestamp(max(dates), TZ).strftime('%Y-%m-%d') if dates else None,
            'months': dict(sorted(months.items()))}


def read_chat(root, peer, *, verify_export=False):
    root = Path(root)
    rels = database_rels(root)
    check_paths(root, rels)
    hashes = verify_snapshot(root) if verify_export else {r:digest(root/r) for r in rels}
    rows, shards = [], {}
    for rel in message_rels(root):
        with connect(root/rel) as c:
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_for(peer),)).fetchone()
            part = [tuple(r) for r in c.execute('SELECT server_id,create_time,local_type FROM '+ident(table_for(peer)))] if exists else []
            session = c.execute('SELECT is_session FROM Name2Id WHERE user_name=?', (peer,)).fetchone()
        rows.extend(part)
        shards[rel] = {**summarize(part), 'conversation_table_present':bool(exists),
                       'peer_is_session':session[0] if session else None}
    if any(digest(root/r) != value for r, value in hashes.items()):
        raise ValueError('saved database changed during read-only audit')
    return {**summarize(rows), 'shards': shards}, rows


def missing_by_month(source, target):
    present = {r[0] for r in target if type(r[0]) is int and r[0] > 0}
    unique = {r[0]: r for r in source if type(r[0]) is int and r[0] > 0}
    return dict(sorted(Counter(month(row[1]) for sid, row in unique.items() if sid not in present).items()))


def changed_times(source, target):
    values={r[0]:r[1] for r in target if type(r[0]) is int and r[0]>0}
    counts=Counter((month(r[1]),month(values[r[0]])) for r in source
                   if r[0] in values and r[1]!=values[r[0]])
    return [{'source_month':k[0],'target_month':k[1],'count':v} for k,v in sorted(counts.items())]


def audit_task(task):
    task = Path(task)
    manifest = json.loads((task/'sync-plan.json').read_text(encoding='utf-8'))
    if manifest.get('mode') != 'replace-a-from-b':
        raise ValueError('not a single-conversation replacement plan')
    options=snapshot_options(manifest)
    a, b = manifest['accounts']['A'], manifest['accounts']['B']
    for slot,item in (('A',a),('B',b)):
        exported=json.loads((task/(slot+'-plain')/'export-report.json').read_text(encoding='utf-8'))
        if exported.get('account_directory')!=Path(item['root']).parent.name:
            raise ValueError('saved export account does not match application plan')
    source, source_rows = read_chat(task/'B-plain', a['owner'], verify_export=True)
    original_a, _ = read_chat(task/'A-plain', b['owner'], verify_export=True)
    source_eligible = [r for r in source_rows if r[2] not in DEFERRED]
    bundle = task/'B-chat'
    bm = json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if bm.get('sha256') != digest(bundle/'conversation.json'):
        raise ValueError('bundle hash mismatch')
    data = json.loads((bundle/'conversation.json').read_text(encoding='utf-8'))
    if data['owner'] != b['owner'] or data['peer'] != a['owner']:
        raise ValueError('bundle participants do not match the selected plan')
    bundle_rows = [(m['fields']['server_id'],m['fields']['create_time'],m['fields']['local_type']) for m in data['messages']]
    if bm['message_count'] != len(bundle_rows):
        raise ValueError('bundle count mismatch')
    candidate, candidate_rows = read_chat(task/'A-plan/recovered-plain', b['owner'])
    plan = json.loads((task/'A-plan/plan.json').read_text(encoding='utf-8'))
    rels = (*mutable_rels(task/'A-plan/recovered-plain'),
            *(single_media(task/'A-plan/recovered-plain') if options.get('include_voice') else ()))
    if set(plan['candidates']) != set(rels):
        raise ValueError('candidate shard inventory mismatch')
    check_paths(task/'A-plan/candidates', rels)
    hashes_valid = all(digest(task/'A-plan/candidates'/r) == plan['candidates'][r] for r in rels)
    result_path = task/'sync-result.json'
    result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    applied = result.get('accounts', {}).get('A', {})
    report = {'version':1, 'audit_only':True, 'live_databases_modified':False, 'task_id':task.parent.name,
              'snapshot_options':options,'diagnostic_scope':'message_ids_dates_and_database_hashes',
              'client_media_playback_checked':False,
              'application_state':result.get('state','not_applied'),
              'A_application_state':applied.get('state','not_applied'),
              'encrypted_candidates_match_plan':hashes_valid,
              'applied_A_hashes_match_candidates': applied.get('state')=='applied' and
                  all(applied.get('after',{}).get(r) == h for r,h in plan['candidates'].items()),
              'B_source_all':source, 'B_source_eligible':summarize(source_eligible),
              'B_bundle':summarize(bundle_rows), 'A_before_replacement':original_a,
              'A_candidate':candidate,
              'source_eligible_missing_from_bundle':missing_by_month(source_eligible,bundle_rows),
              'bundle_missing_from_candidate':missing_by_month(bundle_rows,candidate_rows),
              'bundle_timestamps_changed_in_candidate':changed_times(bundle_rows,candidate_rows)}
    return report, bundle_rows


def selected_task(workspace, accounts):
    choices=[]
    for task in (workspace/'jobs').glob('*/sync'):
        path=task/'sync-plan.json'; receipt=task/'sync-result.json'
        if not path.is_file():continue
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('mode')!='replace-a-from-b':continue
        if all(value['accounts'][slot]['owner']==item['owner'] and
               Path(value['accounts'][slot]['root']).resolve()==Path(item['root']).resolve()
               for slot,item in zip(('A','B'),accounts)):
            applied=receipt.is_file() and json.loads(receipt.read_text(encoding='utf-8')).get('state')=='applied'
            choices.append((applied,receipt.stat().st_mtime_ns if receipt.exists() else path.stat().st_mtime_ns,task))
    if not choices:raise ValueError('no matching saved replacement task')
    return max(choices,key=lambda item:item[:2])[2]


def main():
    print('History Audit v3 starting / 历史记录检查启动', flush=True)
    workspace=Path(os.environ['LOCALAPPDATA'])/'ChatRescue/Desktop'
    output=fresh(workspace/('history-audit-'+uuid.uuid4().hex))
    progress=Progress(output)
    progress.say('输出目录：'+str(output))
    report={'version':3,'audit_only':True,'live_databases_modified':False,
            'audit_state':'running','stage':'startup'}
    save_report(output,report)
    try:
        # Saved snapshots can be audited while a client is open. The live export
        # below still requires normal client exit and all original guard checks.
        progress.say('1/4 读取已有任务设置（不访问当前微信库）')
        report['stage']='load_settings';save_report(output,report)
        settings=workspace/'settings.json';require_private(settings)
        accounts=json.loads(settings.read_text(encoding='utf-8'))['accounts']
        report['stage']='select_task';save_report(output,report)
        task=selected_task(workspace,accounts)
        a,b=accounts
        report['stage']='saved_task';save_report(output,report)
        with progress.stage('2/4 比较 B 导出、A 候选及应用回执'):
            saved, source_rows=audit_task(task)
            report.update(saved)
        report.update(version=3,audit_state='running',stage='current_A_snapshot',A_current_export_state='pending')
        save_report(output,report)
        progress.say('已有任务的月份报告已保存：history-audit.json')
        library=Path(sys.executable).parent/'e_sqlcipher.dll'
        if library.is_file():os.environ['SQLCIPHER_LIBRARY']=str(library)
        current_available=False
        try:
            ensure_closed()
            check_owner(a['root'],a['owner']);require_private(a['keys'])
            with progress.stage('3/4 只读复制并导出当前 A（大数据库可能需要数分钟）'):
                export_root(a['root'],a['keys'],output/'A-current-plain',**report.get('snapshot_options',{}))
                current, current_rows=read_chat(output/'A-current-plain',b['owner'],verify_export=True)
            report['A_current_snapshot']=current
            report['bundle_missing_from_current_A']=missing_by_month(source_rows,current_rows)
            report['bundle_timestamps_changed_in_current_A']=changed_times(source_rows,current_rows)
            report['A_current_export_state']='verified'
            current_available=True
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as error:
            report['A_current_export_state']='failed'
            report['A_current_export_error_class']=type(error).__name__
            report['A_current_export_error']=error_details(error,'current_A_snapshot')
            progress.say('当前 A 快照未完成：'+report['A_current_export_error']['reason'])
            progress.say(report['A_current_export_error']['next_action'])
        report['stage']='local_json';save_report(output,report)
        # Fast raw diagnostic JSONs are local viewing files, never restore bundles.
        with progress.stage('4/4 生成仅供本机查看的 A 对话 JSON'):
            sources=[('candidate',task/'A-plan/recovered-plain')]
            if current_available:sources.append(('current',output/'A-current-plain'))
            for label,root in sources:
                try:
                    folder='A-'+label+'-chat'
                    count=diagnostic_conversation(root,a['owner'],b['owner'],output/folder)
                    report['A_'+label+'_conversation_json']=folder+'/conversation.json'
                    progress.say('A '+label+' JSON 已保存，条数：'+str(count))
                except (OSError,ValueError,sqlite3.Error) as error:
                    report['A_'+label+'_json_error_class']=type(error).__name__
                    report['A_'+label+'_json_error']=error_details(error,'A_'+label+'_json')
                save_report(output,report)
        errors=any(key.endswith('_error_class') for key in report)
        report.update(audit_state='partial' if errors else 'completed',stage='finished')
        save_report(output,report)
        progress.say('检查结束；汇总报告：'+str(output/'history-audit.json'))
        print('请只分享 history-audit.json。conversation.json 含正文，仅留本机查看。',flush=True)
        return 2 if errors else 0
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
        report.update(audit_state='failed',error_class=type(error).__name__)
        report['error']=error_details(error,report['stage'])
        save_report(output,report)
        progress.say('检查未完成：'+report['error']['reason'])
        progress.say(report['error']['next_action'])
        return 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
        print('工具启动失败：'+type(error).__name__+'。请保留窗口和已有任务，反馈显示的错误类型。',flush=True)
        raise SystemExit(1)

import sqlite3, tempfile, unittest, hashlib
from pathlib import Path
from chat_rescue.workflow import save_chat, prepare_plain_restore, ClosingConnection
def db(path): return sqlite3.connect(path, factory=ClosingConnection)
A='wxid_receiver_demo'
B='wxid_partner_demo'
FIELDS='local_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,status INTEGER,upload_status INTEGER,download_status INTEGER,server_seq INTEGER,origin_source INTEGER,source TEXT,message_content TEXT,compress_content TEXT,packed_info_data BLOB,WCDB_CT_message_content INTEGER,WCDB_CT_source INTEGER'
def fixture(root, source):
 root.mkdir()
 for d in ['contact','session','message']: (root/d).mkdir()
 with db(root/'contact/contact.db') as c:
  c.execute('CREATE TABLE contact(username TEXT PRIMARY KEY,nick_name TEXT,remark TEXT,local_type INTEGER)')
  c.executemany('INSERT INTO contact VALUES(?,?,?,1)',[(A,'Receiver Demo',''),(B,'Partner Demo','')])
 with db(root/'message/message_0.db') as c:
  c.executescript('CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY,is_session INTEGER); CREATE TABLE DeleteInfo(chat_name_id INTEGER,delete_table_name TEXT);')
  c.executemany('INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(?,?,?)',[(7,A,1),(9,B,0)] if source else [(4,A,0),(301,'wxid_unrelated_demo',1)])
  if source:
   t='Msg_'+hashlib.md5(A.encode()).hexdigest();c.execute('CREATE TABLE '+t+'('+FIELDS+')')
   for suffix,col in [('SENDERID','real_sender_id'),('SERVERID','server_id'),('SORTSEQ','sort_seq'),('TYPE_SEQ','local_type,sort_seq')]:c.execute('CREATE INDEX '+t+'_'+suffix+' ON '+t+'('+col+')')
   values=[(1,101,25769803825,1700000000000,7,1700000000,4,0,0,0,6,None,b'<msg><appmsg><type>6</type></appmsg></msg>',None,bytes.fromhex('080710023a0178'),0,0),(2,102,42,1700000001000,9,1700000001,2,0,0,0,6,None,b'<msg nickname="Synthetic card"/>',None,bytes.fromhex('08091001'),0,0)]
   c.executemany('INSERT INTO '+t+' VALUES('+','.join('?' for _ in values[0])+')',values)
 with db(root/'message/message_resource.db') as c:
  c.executescript('CREATE TABLE ChatName2Id(user_name TEXT PRIMARY KEY,update_time INTEGER);CREATE TABLE SenderName2Id(user_name TEXT PRIMARY KEY,update_time INTEGER);CREATE TABLE MessageResourceInfo(message_id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,sender_id INTEGER,message_local_type INTEGER,message_create_time INTEGER,message_local_id INTEGER,message_svr_id INTEGER,message_origin_source INTEGER,packed_info BLOB);CREATE TABLE MessageResourceDetail(resource_id INTEGER PRIMARY KEY AUTOINCREMENT,message_id INTEGER,type INTEGER,size INTEGER,create_time INTEGER,access_time INTEGER,status INTEGER,data_index TEXT,packed_info BLOB);')
  if source:
   c.execute('INSERT INTO ChatName2Id(rowid,user_name,update_time) VALUES(11,?,0)',(A,));c.execute('INSERT INTO SenderName2Id(rowid,user_name,update_time) VALUES(13,?,0)',(A,))
   c.execute('INSERT INTO MessageResourceInfo VALUES(8,11,13,25769803825,1700000000,1,101,6,?)',(b'opaque metadata',));c.execute("INSERT INTO MessageResourceDetail VALUES(16,8,1,10,1700000000,0,0,'synthetic.dat',NULL)")
 with db(root/'session/session.db') as c:
  c.executescript('CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);CREATE TABLE SessionDeleteTable(username TEXT PRIMARY KEY,delete_time INTEGER);CREATE TABLE SessionTable(username TEXT PRIMARY KEY,type INTEGER,unread_count INTEGER,unread_first_msg_srv_id INTEGER,unread_first_pat_msg_local_id INTEGER,unread_first_pat_msg_sort_seq INTEGER,is_hidden INTEGER,summary TEXT,draft TEXT,status INTEGER,last_timestamp INTEGER,sort_timestamp INTEGER,last_clear_unread_timestamp INTEGER,last_msg_locald_id INTEGER,last_msg_type INTEGER,last_msg_sub_type INTEGER,last_msg_sender TEXT,last_sender_display_name TEXT,last_msg_ext_type INTEGER);')
  if source:c.execute('INSERT INTO SessionTable VALUES(?,0,0,0,0,0,0,?,NULL,0,1700000001,1700000001,0,2,42,0,?,NULL,0)',(A,'[名片]',B))
class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'source';self.target=self.root/'target';fixture(self.source,True);fixture(self.target,False);self.bundle=self.root/'bundle'
 def tearDown(self):self.tmp.cleanup()
 def save(self):return save_chat(self.source,B,A,self.bundle)
 def test_restore_without_optional_delete_index(self):
  self.save()
  with db(self.target/'message/message_0.db') as c:c.execute('DROP TABLE DeleteInfo')
  r=prepare_plain_restore(self.bundle,self.target,A,self.root/'result')
  self.assertEqual(r['added'],2)
  with db(self.root/'result/message/message_0.db') as c:
   self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
   self.assertEqual(c.execute('SELECT count(*) FROM '+r['table']).fetchone()[0],2)
   self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='DeleteInfo'").fetchone())
 def test_foreign_chat_resource_same_id_is_preserved(self):
  self.save()
  with db(self.target/'message/message_resource.db') as c:
   c.execute('INSERT INTO ChatName2Id(rowid,user_name,update_time) VALUES(11,?,0)',('wxid_unrelated_demo',))
   c.execute('INSERT INTO SenderName2Id(rowid,user_name,update_time) VALUES(13,?,0)',(A,))
   c.execute('INSERT INTO MessageResourceInfo VALUES(8,11,13,25769803825,1700000000,90,101,6,?)',(b'foreign cache',))
   c.execute("INSERT INTO MessageResourceDetail VALUES(16,8,1,10,1700000000,0,0,'foreign.dat',NULL)")
   original=c.execute('SELECT * FROM MessageResourceInfo WHERE message_id=8').fetchone()
   detail=c.execute('SELECT * FROM MessageResourceDetail WHERE resource_id=16').fetchone()
  r=prepare_plain_restore(self.bundle,self.target,A,self.root/'result')
  self.assertEqual(r['added'],2)
  with db(self.root/'result/message/message_resource.db') as c:
   self.assertEqual(c.execute('SELECT * FROM MessageResourceInfo WHERE message_id=8').fetchone(),original)
   self.assertEqual(c.execute('SELECT * FROM MessageResourceDetail WHERE resource_id=16').fetchone(),detail)
   added=c.execute('SELECT r.message_id,ch.user_name,s.user_name FROM MessageResourceInfo r JOIN ChatName2Id ch ON ch.rowid=r.chat_id JOIN SenderName2Id s ON s.rowid=r.sender_id WHERE r.message_id!=8 AND r.message_svr_id=101').fetchall()
   self.assertEqual(len(added),1);self.assertEqual(added[0][1:],(B,A))
 def test_existing_target_chat_resource_still_refused(self):
  self.save()
  with db(self.target/'message/message_resource.db') as c:
   c.execute('INSERT INTO ChatName2Id(rowid,user_name,update_time) VALUES(11,?,0)',(B,))
   c.execute('INSERT INTO SenderName2Id(rowid,user_name,update_time) VALUES(13,?,0)',(A,))
   c.execute('INSERT INTO MessageResourceInfo VALUES(8,11,13,25769803825,1700000000,90,101,6,?)',(b'conflicting cache',))
  with self.assertRaisesRegex(ValueError,'resource identity collision'):
   prepare_plain_restore(self.bundle,self.target,A,self.root/'result')
 def test_resource_without_chat_identity_still_refused(self):
  self.save()
  with db(self.target/'message/message_resource.db') as c:
   c.execute('INSERT INTO MessageResourceInfo VALUES(8,99,13,25769803825,1700000000,90,101,6,NULL)')
  with self.assertRaisesRegex(ValueError,'resource identity collision'):
   prepare_plain_restore(self.bundle,self.target,A,self.root/'result')
 def change(self,sql,args=()):
  with db(self.source/'message/message_0.db') as c:
   t=c.execute("SELECT name FROM sqlite_master WHERE name LIKE 'Msg_%' AND type='table'").fetchone()[0];c.execute(sql.format(table=t),args)
 def test_restores_real_ids_blob_direction_and_resources(self):
  self.save();r=prepare_plain_restore(self.bundle,self.target,A,self.root/'result');self.assertEqual(r['added'],2)
  with db(self.root/'result/message/message_0.db') as c:
   rows=c.execute('SELECT server_id,real_sender_id,hex(packed_info_data),message_content FROM '+r['table']+' ORDER BY sort_seq').fetchall();self.assertEqual(rows[0],(101,4,'080410013A0178',b'<msg><appmsg><type>6</type></appmsg></msg>'));self.assertEqual(rows[1][:3],(102,302,'08AE021002'));self.assertEqual(c.execute('SELECT user_name FROM Name2Id WHERE rowid=301').fetchone()[0],'wxid_unrelated_demo');self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
  with db(self.root/'result/message/message_resource.db') as c:
   info=c.execute('SELECT message_id,chat_id,sender_id,message_local_id FROM MessageResourceInfo').fetchone();self.assertEqual(c.execute('SELECT user_name FROM ChatName2Id WHERE rowid=?',(info[1],)).fetchone()[0],B);self.assertEqual(c.execute('SELECT user_name FROM SenderName2Id WHERE rowid=?',(info[2],)).fetchone()[0],A);self.assertEqual(c.execute('SELECT message_id FROM MessageResourceDetail').fetchone()[0],info[0])
  with db(self.root/'result/session/session.db') as c:self.assertEqual(c.execute('SELECT username,last_msg_sender FROM SessionTable').fetchone(),(B,B))
 def test_repeated_restore_deduplicates(self):
  self.save();prepare_plain_restore(self.bundle,self.target,A,self.root/'first');r=prepare_plain_restore(self.bundle,self.root/'first',A,self.root/'second');self.assertEqual((r['added'],r['skipped']),(0,2))
 def test_dedup_restores_hidden_session(self):
  self.save();prepare_plain_restore(self.bundle,self.target,A,self.root/'first')
  with db(self.root/'first/session/session.db') as c:
   c.execute('UPDATE SessionTable SET is_hidden=1');c.execute('INSERT INTO SessionDeleteTable VALUES(?,0)',(B,))
  r=prepare_plain_restore(self.bundle,self.root/'first',A,self.root/'second');self.assertEqual(r['added'],0)
  with db(self.root/'second/session/session.db') as c:
   self.assertEqual(c.execute('SELECT is_hidden FROM SessionTable').fetchone()[0],0);self.assertEqual(c.execute('SELECT count(*) FROM SessionDeleteTable').fetchone()[0],0)
 def test_newer_target_preview_and_draft_preserved_but_unhidden(self):
  self.save()
  with db(self.source/'session/session.db') as c:row=list(c.execute('SELECT * FROM SessionTable').fetchone())
  row[0]=B;row[6]=1;row[7]='newer';row[8]='saved draft';row[10]=1800000000
  with db(self.target/'session/session.db') as c:
   c.execute('INSERT INTO SessionTable VALUES('+','.join('?' for _ in row)+')',row);c.execute('INSERT INTO SessionDeleteTable VALUES(?,0)',(B,))
  r=prepare_plain_restore(self.bundle,self.target,A,self.root/'result');self.assertEqual(r['added'],2)
  with db(self.root/'result/session/session.db') as c:
   self.assertEqual(c.execute('SELECT is_hidden,summary,draft,last_timestamp FROM SessionTable').fetchone(),(0,'newer','saved draft',1800000000));self.assertEqual(c.execute('SELECT count(*) FROM SessionDeleteTable').fetchone()[0],0)
 def test_last_sender_display_name_uses_target_owner(self):
  self.change('UPDATE {table} SET real_sender_id=7,packed_info_data=? WHERE local_id=2',(bytes.fromhex('08071002'),));self.save();prepare_plain_restore(self.bundle,self.target,A,self.root/'result')
  with db(self.root/'result/session/session.db') as c:self.assertEqual(c.execute('SELECT last_msg_sender,last_sender_display_name FROM SessionTable').fetchone(),(A,'Receiver Demo'))
 def test_wrong_owner_rejected(self):
  self.save()
  with self.assertRaises(ValueError):prepare_plain_restore(self.bundle,self.target,B,self.root/'wrong')
 def test_zero_server_id_rejected(self):
  self.change('UPDATE {table} SET server_id=0 WHERE local_id=1')
  with self.assertRaises(ValueError):self.save()
 def test_unknown_type_rejected(self):
  self.change('UPDATE {table} SET local_type=999 WHERE local_id=1')
  with self.assertRaises(ValueError):self.save()
 def test_conflicting_id_no_overwrite(self):
  self.save();r=prepare_plain_restore(self.bundle,self.target,A,self.root/'first')
  with db(self.root/'first/message/message_0.db') as c:c.execute('UPDATE '+r['table']+' SET message_content=? WHERE server_id=101',(b'changed',))
  with self.assertRaises(ValueError):prepare_plain_restore(self.bundle,self.root/'first',A,self.root/'second')
 def test_tampered_bundle_rejected(self):
  self.save();p=self.bundle/'conversation.json';p.write_text(p.read_text()+' ')
  with self.assertRaises(ValueError):prepare_plain_restore(self.bundle,self.target,A,self.root/'tampered')
 def test_group_rejected(self):
  with self.assertRaises(ValueError):save_chat(self.source,B,'example@chatroom',self.bundle)
if __name__=='__main__':unittest.main()

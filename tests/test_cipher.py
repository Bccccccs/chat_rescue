import os,tempfile,unittest,sqlite3,json
from pathlib import Path
from chat_rescue.cipher import export_database,encrypt_database
from chat_rescue.vendor.sqlcipher_probe import DB,library
from chat_rescue.workflow import ClosingConnection,digest
class CipherTests(unittest.TestCase):
 def setUp(self):
  try: library()
  except RuntimeError:self.skipTest('SQLCipher library unavailable; install sqlcipher to run integration tests')
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.key=os.urandom(32).hex();self.source=self.root/'encrypted.db';self.keys=self.root/'keys.json';self.keys.write_text(json.dumps({str(self.source):{'enc_key':self.key}}));self.keys.chmod(0o600)
 def tearDown(self):
  if hasattr(self,'tmp'):self.tmp.cleanup()
 def test_export_keeps_sparse_implicit_rowids_and_committed_wal(self):
  with DB(self.source,self.key,fixture_write=True) as w:
   w.query("PRAGMA journal_mode=WAL;PRAGMA wal_autocheckpoint=0;CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);CREATE TABLE messages(id INTEGER PRIMARY KEY,content BLOB);INSERT INTO Name2Id(rowid,user_name) VALUES(7,'owner'),(301,'peer');PRAGMA wal_checkpoint(TRUNCATE);INSERT INTO messages VALUES(1,X'00ff');")
   before=digest(self.source);wal=digest(Path(str(self.source)+'-wal'));plain=self.root/'plain.db';export_database(self.source,plain,self.keys)
   with sqlite3.connect(plain,factory=ClosingConnection) as c:
    self.assertEqual(c.execute('SELECT rowid,user_name FROM Name2Id ORDER BY rowid').fetchall(),[(7,'owner'),(301,'peer')]);self.assertEqual(c.execute('SELECT content FROM messages').fetchone()[0],b'\x00\xff')
   self.assertEqual(digest(self.source),before);self.assertEqual(digest(Path(str(self.source)+'-wal')),wal)
 def test_reencrypt_preserves_template_salt_and_sparse_maps(self):
  with DB(self.source,self.key,fixture_write=True) as w:w.query("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);INSERT INTO Name2Id(rowid,user_name) VALUES(301,'peer');")
  plain=self.root/'plain.db';export_database(self.source,plain,self.keys);out=self.root/'candidate.db';encrypt_database(plain,out,self.source,self.keys)
  self.assertEqual(out.read_bytes()[:16],self.source.read_bytes()[:16])
  with DB(out,self.key) as d:self.assertEqual(d.query('SELECT rowid AS id FROM Name2Id')[0]['id'],'301')
if __name__=='__main__':unittest.main()

"""Full synthetic encrypted B -> bundle -> encrypted A integration, no real account."""
import json,os,tempfile,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
from chat_rescue.cipher import encrypt_database,export_database
from chat_rescue.vendor.sqlcipher_probe import DB,library
from chat_rescue.operations import export_root,make_plan,apply_plan,ALL
from chat_rescue.workflow import digest,private_json,connect
from test_workflow import fixture,A,B
class CLITests(unittest.TestCase):
 def setUp(self):
  try:library()
  except (OSError,RuntimeError):self.skipTest('SQLCipher library required for integration')
  gate=patch('chat_rescue.operations.mac_only');gate.start();self.addCleanup(gate.stop)
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.source=self.encrypted(B,True);self.target=self.encrypted(A,False)
 def tearDown(self):
  if hasattr(self,'tmp'):self.tmp.cleanup()
 def encrypted(self,owner,source):
  plain=self.root/(owner+'-plain');fixture(plain,source)
  live=self.root/owner/'db_storage';keys=self.root/(owner+'-keys.json');manifest={}
  for rel in ALL:
   path=live/rel;path.parent.mkdir(parents=True,exist_ok=True);key=os.urandom(32).hex();manifest[rel]={'enc_key':key}
   with DB(path,key,fixture_write=True) as d:d.query('CREATE TABLE template(id INTEGER PRIMARY KEY);')
  private_json(keys,{'keys':manifest})
  for rel in ALL:
   candidate=Path(str(live/rel)+'.candidate');encrypt_database(plain/rel,candidate,live/rel,keys);os.replace(candidate,live/rel)
  return live,keys
 def cli(self,*args):
  p=subprocess.run([sys.executable,'-m','chat_rescue',*map(str,args)],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
 def test_complete_encrypted_recovery_and_cli_identity_query(self):
  live,keys=self.source;target,target_keys=self.target
  with patch('chat_rescue.operations.ensure_closed'):
   export_root(live,keys,self.root/'export')
   found=self.cli('contacts','--plain-root',self.root/'export','--query','Receiver Demo');self.assertEqual(found['contacts'][0]['username'],A)
   self.cli('save-chat','--plain-root',self.root/'export','--owner',B,'--peer',A,'--output',self.root/'bundle')
   original={r:digest(target/r) for r in ALL}
   result=make_plan(self.root/'bundle',target,target_keys,A,self.root/'plan');self.assertEqual(result['added'],2)
   self.assertEqual({r:digest(target/r) for r in ALL},original)
   applied=apply_plan(self.root/'plan');self.assertEqual(applied['state'],'applied')
  restored=self.root/'restored.db';export_database(target/ALL[0],restored,target_keys)
  with connect(restored) as c:
   table=result['table'];rows=c.execute('SELECT server_id,real_sender_id,hex(packed_info_data) FROM '+table+' ORDER BY server_id').fetchall();self.assertEqual([tuple(r) for r in rows],[(101,4,'080410013A0178'),(102,302,'08AE021002')])
  self.assertEqual(digest(target/'contact/contact.db'),original['contact/contact.db'])
 def test_cli_wrong_owner_refused(self):
  live,keys=self.source
  with patch('chat_rescue.operations.ensure_closed'):export_root(live,keys,self.root/'export')
  p=subprocess.run([sys.executable,'-m','chat_rescue','save-chat','--plain-root',str(self.root/'export'),'--owner',A,'--peer',B,'--output',str(self.root/'bundle')],capture_output=True,text=True)
  self.assertEqual(p.returncode,2);self.assertFalse((self.root/'bundle').exists())

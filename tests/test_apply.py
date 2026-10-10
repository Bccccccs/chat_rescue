import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chat_rescue.operations import apply_plan,ALL
from chat_rescue.workflow import RELS,digest,private_json
class ApplyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.live=self.root/'synthetic'/'db_storage';self.plan=self.root/'plan';self.plan.mkdir();self.originals={}
  for rel in RELS:
   p=self.live/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'old');Path(str(p)+'-wal').write_bytes(b'wal');q=self.plan/'candidates'/rel;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b'new')
  for rel in ALL:
   for suffix in ('','-wal','-shm'):
    p=self.live/(rel+suffix);self.originals[rel+suffix]=digest(p) if p.exists() else None
  private_json(self.plan/'plan.json',{'version':1,'target_root':str(self.live),'originals':self.originals,'candidates':{r:digest(self.plan/'candidates'/r) for r in RELS},'report':{'added':2},'target_owner':'synthetic'})
 def tearDown(self):self.tmp.cleanup()
 def run_apply(self):
  with patch('chat_rescue.operations.ensure_closed'),patch('chat_rescue.operations.mac_only'):return apply_plan(self.plan)
 def test_apply_keeps_originals_and_rejects_repeat(self):
  self.run_apply()
  for rel in RELS:self.assertEqual((self.live/rel).read_bytes(),b'new');self.assertEqual((self.plan/'originals'/rel).read_bytes(),b'old');self.assertFalse(Path(str(self.live/rel)+'-wal').exists())
  with self.assertRaises(ValueError):self.run_apply()
 def test_incomplete_sidecar_manifest_rejected(self):
  p=self.plan/'plan.json';data=json.loads(p.read_text());data['originals'].pop(RELS[0]+'-wal');p.write_text(json.dumps(data))
  with self.assertRaises(ValueError):self.run_apply()
  self.assertEqual(Path(str(self.live/RELS[0])+'-wal').read_bytes(),b'wal')
 def test_wrong_target_owner_rejected(self):
  p=self.plan/'plan.json';data=json.loads(p.read_text());data['target_owner']='different';p.write_text(json.dumps(data))
  with self.assertRaises(ValueError):self.run_apply()
 def test_changed_live_rejected_before_write(self):
  (self.live/RELS[0]).write_bytes(b'changed')
  with self.assertRaises(ValueError):self.run_apply()
  self.assertEqual((self.live/RELS[1]).read_bytes(),b'old')
 def test_changed_candidate_rejected(self):
  (self.plan/'candidates'/RELS[0]).write_bytes(b'changed')
  with self.assertRaises(ValueError):self.run_apply()
 def test_added_wal_after_plan_rejected(self):
  Path(str(self.live/RELS[0])+'-shm').write_bytes(b'new shm')
  with self.assertRaises(ValueError):self.run_apply()
 def test_partial_replace_failure_rolls_back_db_and_wal(self):
  import os
  real=os.replace;count=0
  def fail_once(a,b):
   nonlocal count
   if str(a).endswith('.chat-rescue-stage'):
    count+=1
    if count==2:raise OSError('synthetic failure')
   return real(a,b)
  with patch('chat_rescue.operations.os.replace',side_effect=fail_once):
   with self.assertRaises(OSError):self.run_apply()
  for rel in RELS:self.assertEqual((self.live/rel).read_bytes(),b'old');self.assertEqual(Path(str(self.live/rel)+'-wal').read_bytes(),b'wal')

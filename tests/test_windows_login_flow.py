"""Exercise the actual C# import gate without opening WPF or WeChat."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import windows_backend as backend


class SourceImportBackendTests(unittest.TestCase):
    def test_b_read_is_blocked_until_desktop_history_is_confirmed(self):
        selected = {'root': 'synthetic', 'owner': 'synthetic_owner', 'keys': 'synthetic.json'}
        with patch.object(backend, 'account', return_value=selected), \
                patch.object(backend, 'main_processes', return_value=[]), \
                patch.object(backend, 'ensure_closed'), patch.object(backend, 'require_private'), \
                patch.object(backend.cli, 'verify_keys'), patch.object(backend, 'account_nickname', return_value='Synthetic'):
            with self.assertRaisesRegex(ValueError, '导入|记录'):
                backend.dispatch({'action': 'capture', 'slot': 'B', 'account': {},
                                  'identity_confirmed': True, 'executable': 'synthetic.exe'})

    def test_plan_is_blocked_without_b_import_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backend, 'ensure_closed'), \
                patch.object(backend, 'account', return_value={'keys': 'synthetic.json'}), \
                patch.object(backend, 'require_private'), patch.object(backend, 'job', return_value=Path(directory)), \
                patch.object(backend, 'make_sync_plan'), patch.object(backend, 'plan_view', return_value={}):
            with self.assertRaisesRegex(ValueError, '导入|记录'):
                backend.dispatch({'action': 'plan', 'a': {}, 'b': {}, 'identity_confirmed': True})


@unittest.skipUnless(os.name == 'nt', 'Windows .NET Framework login state')
class SourceImportFlowTests(unittest.TestCase):
    def test_fresh_account_choices_are_independent_in_real_wizard(self):
        compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            self.skipTest('.NET Framework compiler unavailable')
        production = Path(__file__).resolve().parents[1] / 'desktop/windows'
        framework = compiler.parent / 'WPF'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'backend-config.json').write_text('{}', encoding='utf-8')
            probe = root / 'Probe.cs'
            probe.write_text('''using System;
using System.Collections.Generic;
using System.Reflection;
using System.IO;
using System.Windows.Controls;
using System.Windows.Threading;
using System.Threading;
using ChatRescueWindows;
class FakeWizard : Wizard {
 public string SettingsPath;
 public bool FailCapture;
 protected override Dictionary<string,object> Backend(Dictionary<string,object> request) {
  if((string)request["action"]=="capture" && FailCapture)throw new Exception("synthetic HMAC 0/4");
  if((string)request["action"]=="save-settings")File.WriteAllText(SettingsPath,"overwritten");
  return new Dictionary<string,object>();
 }
}
class Probe {
 static bool HasButton(System.Windows.DependencyObject node,string label){
  var button=node as Button;if(button!=null&&Convert.ToString(button.Content)==label)return true;
  foreach(var value in System.Windows.LogicalTreeHelper.GetChildren(node)){var child=value as System.Windows.DependencyObject;if(child!=null&&HasButton(child,label))return true;}return false;
 }
 static object Field(Wizard wizard, string name) {
  return typeof(Wizard).GetField(name, BindingFlags.Instance|BindingFlags.NonPublic).GetValue(wizard);
 }
 [STAThread] static int Main() { try {
  // Construct controls without showing a window or firing its Loaded backend call.
  var wizard = new FakeWizard();
  if(((Button[])Field(wizard,"steps")).Length!=3)return 20;
  var stale=new Dictionary<string,object>{{"root","old-root"},{"owner","old-owner"},{"keys","old-keys"},{"nickname","old-nickname"}};
  var cached=new Dictionary<string,object>{{"accounts",new object[]{stale,stale}}};
  var status=new Dictionary<string,object>{{"settings",cached}};
  typeof(Wizard).GetMethod("AcceptStatus",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{status});
  var accounts=(Dictionary<string,object>[])Field(wizard,"accounts");
  if((string)accounts[0]["root"]!="" || (string)accounts[1]["root"]!="") return 3;
  wizard.SettingsPath=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"synthetic-settings.txt");
  File.WriteAllText(wizard.SettingsPath,"prior-choice");
  SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
  var request=new Dictionary<string,object>{{"action","status"}};
  typeof(Wizard).GetMethod("Run",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{"refresh",request,new Action<Dictionary<string,object>>(value=>{})});
  var frame=new DispatcherFrame();var deadline=DateTime.UtcNow.AddSeconds(5);
  var timer=new DispatcherTimer{Interval=TimeSpan.FromMilliseconds(10)};
  timer.Tick+=delegate{if(!(bool)Field(wizard,"busy") || DateTime.UtcNow>deadline)frame.Continue=false;};
  timer.Start();Dispatcher.PushFrame(frame);timer.Stop();
  if((bool)Field(wizard,"busy"))return 4;
  if(File.ReadAllText(wizard.SettingsPath)!="prior-choice")return 5;
  var captured=(bool[])Field(wizard,"accountVerified");captured[0]=captured[1]=true;
  wizard.FailCapture=true;
  request=new Dictionary<string,object>{{"action","capture"},{"slot","A"}};
  typeof(Wizard).GetMethod("Run",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{"read",request,new Action<Dictionary<string,object>>(value=>{})});
  frame=new DispatcherFrame();deadline=DateTime.UtcNow.AddSeconds(5);
  timer.Start();Dispatcher.PushFrame(frame);timer.Stop();
  if((bool)Field(wizard,"busy"))return 6;
  if(captured[0] || !captured[1] || !((string[])Field(wizard,"accountReadErrors"))[0].Contains("0/4"))return 7;
  wizard.FailCapture=false;
  typeof(Wizard).GetMethod("Run",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{"read",request,new Action<Dictionary<string,object>>(value=>{})});
  frame=new DispatcherFrame();deadline=DateTime.UtcNow.AddSeconds(5);
  timer.Start();Dispatcher.PushFrame(frame);timer.Stop();
  if(((string[])Field(wizard,"accountReadErrors"))[0]!="")return 8;
  var verified = (bool[])Field(wizard, "accountVerified");
  accounts[0]["root"]="known-root";accounts[0]["nickname"]="已识别昵称";verified[0]=true;
  var namedPanel=new StackPanel();
  typeof(Wizard).GetMethod("AccountCard",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{0,namedPanel});
  var namedCard=(StackPanel)((Border)namedPanel.Children[0]).Child;
  bool named=false;
  foreach(var child in namedCard.Children){var c=child as ComboBox;if(c!=null && c.SelectedItem!=null && Convert.ToString(((ComboBoxItem)c.SelectedItem).Content).Contains("已识别昵称"))named=true;}
  if(!named)return 21;
  var migrated=new Dictionary<string,object>{{"id","synthetic-task"},{"accounts",new object[]{accounts[0],accounts[1]}},{"relocated",true}};
  typeof(Wizard).GetMethod("AcceptTask",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{migrated});
  if(verified[0] || verified[1])return 22;
  var historyTask=new Dictionary<string,object>{{"id","task-y"},{"accounts",new object[]{new Dictionary<string,object>{{"root","y-root"},{"owner","y-owner"},{"keys",""}},new Dictionary<string,object>()}}};
  var historyResult=new Dictionary<string,object>{{"task",historyTask},{"view",new Dictionary<string,object>{{"plan","y-plan"},{"state","ready"}}}};
  typeof(Wizard).GetMethod("AcceptHistoricalPlan",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{historyResult});
  var snapshot=(Dictionary<string,object>)typeof(Wizard).GetMethod("TaskSnapshot",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[0]);
  if((string)snapshot["task_id"]!="task-y" || (string)accounts[0]["root"]!="y-root" || (string)accounts[0]["keys"]!="")return 23;
  var direction=new Dictionary<string,object>{{"structure_check",new Dictionary<string,object>{{"state","matched"}}},{"target_shard_imports",new object[]{new Dictionary<string,object>()}}};
  var preview=new Dictionary<string,object>{{"state","ready"},{"mode","replace-a-from-b"},{"directions",new Dictionary<string,object>{{"A",direction}}}};
  typeof(Wizard).GetField("plan",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(wizard,preview);
  direction["media_experimental"]=true;direction["attachment_state_skipped"]=1;
  direction["attachment_state_skip_details"]=new object[]{new Dictionary<string,object>{{"side","source"},{"kind",(6L<<32)|49L},{"status",27},{"upload_status",8},{"count",1}}};
  string mediaText=Convert.ToString(typeof(Wizard).GetMethod("MediaSummary",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[0]));
  if(!mediaText.Contains("来源文件")||!mediaText.Contains("status=27")||!mediaText.Contains("跳过 1 条"))return 35;
  typeof(Wizard).GetField("step",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(wizard,1);
  typeof(Wizard).GetMethod("Render",BindingFlags.Instance|BindingFlags.NonPublic,null,Type.EmptyTypes,null).Invoke(wizard,new object[0]);
  if(HasButton((StackPanel)Field(wizard,"page"),"确认应用这份替换预览") || !HasButton((StackPanel)Field(wizard,"page"),"预览已核对，下一步确认应用 →"))return 24;
  typeof(Wizard).GetField("step",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(wizard,2);
  typeof(Wizard).GetMethod("Render",BindingFlags.Instance|BindingFlags.NonPublic,null,Type.EmptyTypes,null).Invoke(wizard,new object[0]);
  if(!HasButton((StackPanel)Field(wizard,"page"),"确认应用这份替换预览"))return 25;
  typeof(Wizard).GetField("plan",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(wizard,null);
  typeof(Wizard).GetField("step",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(wizard,0);
  accounts[0]["root"]="";accounts[0]["nickname"]="";
  var candidates = (List<Dictionary<string,object>>)Field(wizard, "candidates");
  candidates.Clear();candidates.Add(new Dictionary<string,object>{{"root","only-root"},{"owner","only-owner"},{"label","Only"},{"complete",true}});
  accounts[0]["root"]="";accounts[1]["root"]="";verified[0]=verified[1]=false;
  var uniquePanel=new StackPanel();
  typeof(Wizard).GetMethod("AccountCard",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{0,uniquePanel});
  if((string)accounts[0]["root"]!="" || verified[0])return 26;
  uniquePanel=new StackPanel();typeof(Wizard).GetMethod("AccountCard",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{1,uniquePanel});
  if((string)accounts[1]["root"]!="")return 27;
  candidates.Add(new Dictionary<string,object>{{"root","second-root"},{"owner","second-owner"},{"label","Second"},{"complete",true}});
  accounts[0]["root"]="";uniquePanel=new StackPanel();
  typeof(Wizard).GetMethod("AccountCard",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{0,uniquePanel});
  if((string)accounts[0]["root"]!="")return 28;
  var historical=new Dictionary<string,object>{{"root","history-root"},{"owner","history-owner"},{"nickname","历史合成昵称"},{"label","历史合成昵称 · 上次识别"},{"complete",true},{"state","historical"}};
  typeof(Wizard).GetMethod("SelectRoot",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{1,historical});
  if((string)accounts[1]["nickname"]!="历史合成昵称" || verified[1])return 29;
  accounts[0]["root"]="target-root";accounts[0]["owner"]="target-owner";accounts[0]["nickname"]="目标合成昵称";verified[0]=true;
  var refreshStatus=new Dictionary<string,object>{{"settings",cached},{"candidates",new object[]{historical,new Dictionary<string,object>{{"root","target-root"},{"owner","target-owner"},{"complete",true},{"nickname","目标合成昵称"}}}}};
  typeof(Wizard).GetMethod("AcceptStatus",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{refreshStatus});
  if((string)accounts[0]["root"]!="target-root" || !verified[0] || (string)accounts[1]["root"]!="history-root" || verified[1])return 30;
  if(File.ReadAllText(wizard.SettingsPath)!="overwritten")return 31;
  var missing=new Dictionary<string,object>{{"root","history-root"},{"owner","history-owner"},{"nickname","历史合成昵称"},{"complete",false},{"state","missing_databases"},{"missing_databases",new object[]{"contact/contact.db"}}};
  refreshStatus["candidates"]=new object[]{missing};
  typeof(Wizard).GetMethod("AcceptStatus",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{refreshStatus});
  if((string)accounts[1]["root"]!="history-root")return 32;
  if(!Convert.ToString(typeof(Wizard).GetMethod("AccountDirectoryStatus",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{1})).Contains("contact/contact.db"))return 33;
  accounts[0]["root"]="";accounts[1]["root"]="";
  candidates.Clear();
  candidates.Add(new Dictionary<string,object>{{"root","synthetic-b"},{"owner","synthetic-b"},{"label","B"}});
  verified[0] = verified[1] = true;
  var panel = new StackPanel();
  typeof(Wizard).GetMethod("AccountCard",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{1,panel});
  var card = (StackPanel)((Border)panel.Children[0]).Child;
  foreach(var child in card.Children) {
   var combo = child as ComboBox;
   if(combo != null) { combo.SelectedIndex = 1; break; }
  }
  if(!verified[0] || verified[1]) return 1;
  verified[0] = verified[1] = true;
  panel = new StackPanel();
  typeof(Wizard).GetMethod("AccountCard",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(wizard,new object[]{0,panel});
  card = (StackPanel)((Border)panel.Children[0]).Child;
  foreach(var child in card.Children) {
   var combo = child as ComboBox;
   if(combo != null) { combo.SelectedIndex = 1; break; }
  }
  if(verified[0] || !verified[1])return 2;
  foreach(var child in card.Children){var combo=child as ComboBox;if(combo!=null){combo.SelectedIndex=0;break;}}
  if((string)accounts[0]["root"]!="" || (string)accounts[0]["nickname"]!="" || !verified[1])return 34;
  return 0;
 }catch(Exception error){Console.Error.WriteLine(error);return 99;} }
}''', encoding='utf-8-sig')
            executable = root / 'Probe.exe'
            references = ['System.Xaml.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll',
                          *(str(framework / name) for name in
                            ('PresentationFramework.dll', 'PresentationCore.dll', 'WindowsBase.dll'))]
            result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/main:Probe',
                                     '/out:' + str(executable), *('/reference:' + ref for ref in references),
                                     str(probe), *(str(production / name) for name in
                                                   ('ChatRescue.cs', 'BackendTransport.cs', 'SourceImportFlow.cs'))],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', 'replace'))
            result = subprocess.run([str(executable)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, 'directory selection invalidation stage ' + str(result.returncode) + result.stderr.decode('utf-8','replace'))

    def test_import_reminder_timing_and_read_gate(self):
        compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            self.skipTest('.NET Framework compiler unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = root/'Probe.cs'
            probe.write_text('''using System;
using ChatRescueWindows;
class Probe {
 static int Main() {
  var flow = new SourceImportFlow();
  if(flow.Reminder("A", false) != "" || flow.Reminder("B", true) != "") return 1;
  if(!flow.Reminder("B", false).Contains("来源账号的手机")) return 2;
  if(!flow.Reminder("B", false).Contains("来源账号的电脑微信")) return 3;
  if(flow.CanRead("B", true) || !flow.CanRead("A", false)) return 4;
  flow.Confirm(true);
  if(!flow.CanRead("B", false) || !flow.CanRead("B", true)) return 5;
  flow.Reset();
  if(flow.CanRead("B", true)) return 6;
  flow.Confirm(true); flow.Confirm(false);
  if(flow.CanRead("B", true) || flow.CanRead("unknown", true)) return 7;
  return 0;
 }
}''', encoding='utf-8-sig')
            executable = root/'Probe.exe'
            production = Path(__file__).resolve().parents[1]/'desktop/windows/SourceImportFlow.cs'
            result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:'+str(executable),
                                     str(probe), str(production)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', 'replace'))
            result = subprocess.run([str(executable)], capture_output=True)
            self.assertEqual(result.returncode, 0, 'source import gate failed stage ' + str(result.returncode))

"""Compile the real desktop transport and exercise GBK -> UTF-8 pipes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows .NET Framework pipe encoding')
class WindowsTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            raise unittest.SkipTest('.NET Framework compiler unavailable')
        cls.tmp = tempfile.TemporaryDirectory(prefix='chat-rescue-transport-')
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        source = root / 'Probe.cs'
        source.write_text(r'''
using System;
using System.Diagnostics;
using System.Text;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using ChatRescueWindows;
public class Probe {
 public static int Main(string[] args) {
  if(args[0]=="path") { Console.OutputEncoding=new UTF8Encoding(false); Console.Write(BackendTransport.ResolveConfiguredPath(args[1],args[2])); return 0; }
  Console.InputEncoding=Encoding.GetEncoding(936);
  Console.OutputEncoding=Encoding.GetEncoding(936);
  var serializer=new JavaScriptSerializer();
  var payload=serializer.Serialize(new Dictionary<string,object>{
   {"nickname","测试账号🙂"},{"path",@"C:\测试目录\聊天.json"},{"text","引号\"、反斜杠\\与换行\n"}});
  var start=new ProcessStartInfo(args[0],"-c \"import json,sys; sys.stdin.reconfigure(encoding='utf-8'); print(json.dumps(json.load(sys.stdin),ensure_ascii=False))\"") {
   UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,
   StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
  start.EnvironmentVariables["PYTHONUTF8"]="1";
  using(var process=Process.Start(start)) {
   var errors=process.StandardError.ReadToEndAsync();
   process.StandardInput.Write(args[1]=="legacy"?payload:BackendTransport.EscapeJsonForPipe(payload));
   process.StandardInput.Close();
   var output=process.StandardOutput.ReadToEnd();process.WaitForExit();errors.Wait();
   Console.OutputEncoding=new UTF8Encoding(false);Console.Write(output);return process.ExitCode;
  }
 }
}
''', encoding='utf-8-sig')
        cls.executable = root / 'Probe.exe'
        transport = Path(__file__).resolve().parents[1] / 'desktop/windows/BackendTransport.cs'
        result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64',
                                 '/reference:System.Web.Extensions.dll', '/out:' + str(cls.executable),
                                 str(source), str(transport)], capture_output=True)
        if result.returncode:
            raise AssertionError('Transport regression harness did not compile')

    def run_probe(self, mode):
        return subprocess.run([str(self.executable), sys.executable, mode], capture_output=True, encoding='utf-8-sig')

    def test_legacy_transport_reproduces_chinese_request_failure(self):
        self.assertNotEqual(self.run_probe('legacy').returncode, 0)

    def test_chinese_emoji_path_and_escapes_roundtrip_without_loss(self):
        result = self.run_probe('fixed')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['nickname'], '测试账号🙂')
        self.assertEqual(data['path'], 'C:\\测试目录\\聊天.json')
        self.assertEqual(data['text'], '引号"、反斜杠\\与换行\n')

    def test_runtime_paths_follow_relocated_installation(self):
        directory = Path(self.tmp.name) / '另一台电脑上的安装目录'
        result = subprocess.run([str(self.executable), 'path', str(directory), r'runtime\python.exe'],
                                capture_output=True, encoding='utf-8-sig', check=True)
        self.assertEqual(Path(result.stdout).resolve(), (directory / 'runtime/python.exe').resolve())
        absolute = Path(self.tmp.name) / 'custom/python.exe'
        result = subprocess.run([str(self.executable), 'path', str(directory), str(absolute)],
                                capture_output=True, encoding='utf-8-sig', check=True)
        self.assertEqual(Path(result.stdout).resolve(), absolute.resolve())

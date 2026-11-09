// Native WPF shell. Database operations stay in the Python backend.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Markup;
using Microsoft.Win32;

namespace ChatRescueWindows {
public class Wizard : Window {
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16000000 };
    readonly string[] titles = { "准备并读取账号", "检查恢复预览", "应用与结果" };
    readonly string[] subtitles = { "检查本机微信，任意顺序读取来源与目标", "统一检查消息、分片和媒体，核对具体替换范围", "确认应用后分别核对数据库回执与微信显示" };
    readonly Brush blue = ColorBrush("#2463EB"), ink = ColorBrush("#17263D"), muted = ColorBrush("#6E7D92"), teal = ColorBrush("#158A78");
    Dictionary<string, object> config, environment, plan, simulation, previousSettings;
    readonly Dictionary<string, object>[] accounts = { Account(), Account() };
    readonly List<Dictionary<string, object>> candidates = new List<Dictionary<string, object>>();
    StackPanel nav, page; Grid shell; TextBlock title, subtitle, stepLabel, status, topStatus;
    ProgressBar progress; Button[] steps = new Button[3];
    string executable = "", work = "", taskId = "", historyPlan = "", message = "先检查本机微信，再读取来源与目标账号。";
    int step; int readingIndex=-1; bool busy, prepared, identityConfirmed, previewConfirmed, clientConfirmed, loadedSettings;
    readonly bool[] accountVerified = { false, false };
    readonly string[] accountReadErrors = { "", "" };
    readonly SourceImportFlow sourceImport = new SourceImportFlow();
    bool includeVoice, includeAttachments;

    static Brush ColorBrush(string hex) { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)); }
    static Dictionary<string, object> Account() { return D("owner", "", "root", "", "keys", "", "nickname", ""); }
    static Dictionary<string, object> D(params object[] pairs) { var result = new Dictionary<string, object>(); for (int i=0; i<pairs.Length; i+=2) result[(string)pairs[i]]=pairs[i+1]; return result; }
    static Dictionary<string, object> Obj(object value) { return value as Dictionary<string, object> ?? new Dictionary<string, object>(); }
    static object Get(Dictionary<string, object> d, string key) { object v; return d != null && d.TryGetValue(key,out v) ? v : null; }
    static string S(Dictionary<string, object> d, string key) { return Convert.ToString(Get(d,key)) ?? ""; }
    static bool B(Dictionary<string, object> d, string key) { return Get(d,key) is bool && (bool)Get(d,key); }
    static int N(Dictionary<string, object> d, string key) { object v=Get(d,key); return v==null ? 0 : Convert.ToInt32(v); }
    static object[] A(object v) { return v as object[] ?? new object[0]; }
    string AccountName(int i) {var c=SelectedDirectory(i);string role=i==0?"目标账号":"来源账号";if(!accountVerified[i]&&A(Get(c,"nickname_hints")).Length>1)return role;string name=!accountVerified[i]?S(c,"nickname"):"";if(name.Length==0)name=S(accounts[i],"nickname");return name.Length>0?name:role;}

    public Wizard() {
        Title="Chat Rescue · Windows 实验版"; Width=1180; Height=850; MinWidth=1030; MinHeight=740;
        WindowStartupLocation=WindowStartupLocation.CenterScreen;
        FontFamily=new FontFamily("Microsoft YaHei UI"); FontSize=14; Foreground=ink; Background=ColorBrush("#F7F9FC");
        config=Obj(json.DeserializeObject(File.ReadAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"backend-config.json"),Encoding.UTF8)));
        foreach(string key in new[]{"python","sqlcipher","repository","workspace","client_manifest"})
            if(S(config,key).Length>0)config[key]=BackendTransport.ResolveConfiguredPath(AppDomain.CurrentDomain.BaseDirectory,S(config,key));
        executable=S(config,"executable");
        if(S(config,"client_manifest").Length>0)executable=BackendTransport.ResolveConfiguredPath(AppDomain.CurrentDomain.BaseDirectory,executable);
        Resources.MergedDictionaries.Add((ResourceDictionary)XamlReader.Parse(@"<ResourceDictionary xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml'>
          <Style TargetType='Button'><Setter Property='Padding' Value='18,11'/><Setter Property='Cursor' Value='Hand'/><Setter Property='Background' Value='#EDF2F8'/><Setter Property='Foreground' Value='#263C58'/><Setter Property='BorderThickness' Value='0'/><Setter Property='Margin' Value='0,0,10,0'/><Setter Property='Template'><Setter.Value><ControlTemplate TargetType='Button'><Border x:Name='B' Background='{TemplateBinding Background}' Padding='{TemplateBinding Padding}' CornerRadius='9'><ContentPresenter HorizontalAlignment='Center' VerticalAlignment='Center'/></Border><ControlTemplate.Triggers><Trigger Property='IsMouseOver' Value='True'><Setter TargetName='B' Property='Opacity' Value='.82'/></Trigger><Trigger Property='IsEnabled' Value='False'><Setter TargetName='B' Property='Opacity' Value='.42'/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
          <Style TargetType='TextBox'><Setter Property='Padding' Value='11,9'/><Setter Property='BorderBrush' Value='#DCE4EE'/><Setter Property='BorderThickness' Value='1'/><Setter Property='VerticalContentAlignment' Value='Center'/></Style>
          <Style TargetType='ComboBox'><Setter Property='Padding' Value='9'/><Setter Property='MinHeight' Value='38'/><Setter Property='HorizontalContentAlignment' Value='Stretch'/></Style>
        </ResourceDictionary>"));
        BuildShell(); Render();
        Loaded += delegate { Run("正在检查本机环境…",D("action","status"),AcceptStatus); };
        Closing += delegate(object sender,System.ComponentModel.CancelEventArgs e) { if(busy) { e.Cancel=true; MessageBox.Show(this,"当前本机操作尚未结束，请等待回执后关闭窗口。","操作进行中"); } };
    }

    void BuildShell() {
        shell=new Grid(); shell.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(235)}); shell.ColumnDefinitions.Add(new ColumnDefinition()); Content=shell;
        var side=new DockPanel{Background=Brushes.White,Margin=new Thickness(0,0,1,0)}; shell.Children.Add(side);
        var brand=new StackPanel{Margin=new Thickness(23,30,15,30)};
        brand.Children.Add(Text("↔  Chat Rescue",21,ink,true)); brand.Children.Add(Text("从来源账号恢复到目标账号",12,muted)); DockPanel.SetDock(brand,Dock.Top);side.Children.Add(brand);
        var bottom=new StackPanel{Margin=new Thickness(23,20,15,25)};
        bottom.Children.Add(Link("一步一步使用指南",ShowGuide));
        bottom.Children.Add(Link("打开私有工作区",delegate{OpenDirectory(work);}));
        bottom.Children.Add(Link("新建恢复任务",NewTask));
        bottom.Children.Add(Link("继续本机任务",LoadLocalTask));
        bottom.Children.Add(Link("导出接续任务",ExportTask));
        bottom.Children.Add(Link("载入接续任务",ImportTask));
        bottom.Children.Add(Link("载入隔离测试报告",LoadSimulation));
        bottom.Children.Add(Text(S(config,"build_label").Length>0?S(config,"build_label"):"0.6.0 · Windows 实验版",11,muted));
        bottom.Children.Add(Link("开源许可",delegate{OpenDirectory(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"licenses"));}));DockPanel.SetDock(bottom,Dock.Bottom); side.Children.Add(bottom);
        nav=new StackPanel{Margin=new Thickness(12,0,12,0)}; side.Children.Add(nav);
        for(int i=0;i<3;i++){int index=i; steps[i]=Button((i+1)+"   "+titles[i],delegate{step=index;Render();});steps[i].HorizontalContentAlignment=HorizontalAlignment.Left;steps[i].Margin=new Thickness(0,0,0,10);steps[i].Padding=new Thickness(16,15,8,15); nav.Children.Add(steps[i]);}
        var right=new DockPanel(); Grid.SetColumn(right,1);shell.Children.Add(right);
        var top=new DockPanel{Margin=new Thickness(32,21,27,19)}; DockPanel.SetDock(top,Dock.Top);right.Children.Add(top);
        var refresh=Link("刷新状态",delegate{Run("正在刷新…",D("action","status"),AcceptStatus);});DockPanel.SetDock(refresh,Dock.Right);top.Children.Add(refresh);
        var history=Link("历史任务",LoadPlan);DockPanel.SetDock(history,Dock.Right);top.Children.Add(history);
        topStatus=Text("○ 正在检查环境",12,muted);top.Children.Add(topStatus);
        var foot=new StackPanel{Margin=new Thickness(32,10,32,22)};DockPanel.SetDock(foot,Dock.Bottom);right.Children.Add(foot);
        progress=new ProgressBar{Height=3,IsIndeterminate=true,Visibility=Visibility.Collapsed,Margin=new Thickness(0,0,0,10)};foot.Children.Add(progress);
        status=Text(message,13,muted);foot.Children.Add(status);
        var scroll=new ScrollViewer{VerticalScrollBarVisibility=ScrollBarVisibility.Auto,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled};right.Children.Add(scroll);
        var body=new StackPanel{Margin=new Thickness(32,13,32,20)};scroll.Content=body;
        stepLabel=Text("",12,blue,true);body.Children.Add(stepLabel);title=Text("",30,ink,true);title.Margin=new Thickness(0,10,0,10);body.Children.Add(title);subtitle=Text("",14,muted);subtitle.Margin=new Thickness(0,0,0,24);body.Children.Add(subtitle);
        page=new StackPanel();body.Children.Add(page);
    }

    TextBlock Text(string value,double size,Brush color,bool bold=false) { return new TextBlock{Text=value,FontSize=size,Foreground=color,FontWeight=bold?FontWeights.SemiBold:FontWeights.Normal,TextWrapping=TextWrapping.Wrap,LineHeight=size*1.6,Margin=new Thickness(0,0,0,7)}; }
    Button Button(string label,Action action,bool primary=false) { var b=new Button{Content=label,Background=primary?blue:ColorBrush("#EDF2F8"),Foreground=primary?Brushes.White:ink}; b.Click+=delegate{action();};return b; }
    Button Link(string label,Action action) { var b=Button(label,action);b.Background=Brushes.Transparent;b.Foreground=muted;b.Padding=new Thickness(0,7,15,7);b.HorizontalAlignment=HorizontalAlignment.Left;return b; }
    StackPanel Card(Panel parent) { var content=new StackPanel();var border=new Border{Background=Brushes.White,BorderBrush=ColorBrush("#E2E8F1"),BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(13),Padding=new Thickness(24),Margin=new Thickness(0,0,0,18),Child=content};parent.Children.Add(border);return content; }
    void Para(Panel p,string text){p.Children.Add(Text(text,14,muted));}
    void Heading(Panel p,string text){p.Children.Add(Text(text,18,ink,true));}
    StackPanel Row(Panel p){var r=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,12,0,3)};p.Children.Add(r);return r;}
    void InvalidatePlan(){plan=null;previewConfirmed=false;clientConfirmed=false;simulation=null;}
    void InvalidateAccount(int index){accountVerified[index]=false;accountReadErrors[index]="";sourceImport.Reset();identityConfirmed=false;InvalidatePlan();}
    void LoadPreviousAccounts(){var values=A(Get(previousSettings,"accounts"));if(values.Length!=2){SetStatus("没有上次账号选择，请选择本次来源与目标。");return;}for(int i=0;i<2;i++)accounts[i]=new Dictionary<string,object>(Obj(values[i]));accountReadErrors[0]=accountReadErrors[1]="";accountVerified[0]=accountVerified[1]=false;sourceImport.Reset();identityConfirmed=false;InvalidatePlan();SetStatus("已载入上次选择的路径；请重新核对本次来源和目标，读取后才可生成预览。");Render();}
    void SetStatus(string value,bool error=false){message=value;status.Text=value;status.Foreground=error?ColorBrush("#B84C45"):muted;}

    void ShowGuide(){MessageBox.Show(this,"1. 使用本机腾讯官方微信，无需重装；程序包不带微信。检查环境后，选择目标和来源，任意顺序登录并读取。登录成功不等于数据库已核验。\n2. 来源所需聊天应在电脑完整可见。双方读取通过后，正常退出微信并生成预览，核对移除/导入数量和媒体跳过原因。\n3. 确认具体计划后应用，保留原件并在微信核对。\n\n换电脑：导出接续任务，私密复制整套程序和接续目录；在新电脑载入，重新定位并核验双方数据库，生成新预览。接续目录包含私人资料和密钥，只用于同一授权操作者换机。\n\n完整离线教程位于程序目录 docs/windows-beginner-guide.md。","操作指南");}
    Dictionary<string,object> SelectedDirectory(int index){foreach(var c in candidates)if(String.Equals(S(c,"root"),S(accounts[index],"root"),StringComparison.OrdinalIgnoreCase))return c;return D();}
    bool DirectoryReady(int index){var c=SelectedDirectory(index);return !c.ContainsKey("complete")||B(c,"complete");}
    string AccountDirectoryStatus(int index){var c=SelectedDirectory(index);string state=S(c,"state");if(state=="missing_directory")return "路径已失效：请重新定位账号的 db_storage，历史昵称仅供参考。";if(state=="missing_databases"){var names=new List<string>();foreach(var value in A(Get(c,"missing_databases")))names.Add(Convert.ToString(value));return "目录缺库："+String.Join("、",names.ToArray())+"。请核对完整 db_storage，不会跳过缺库。";}if(state=="invalid_inventory")return "分片清单需检查："+S(c,"inventory_error");if(accountVerified[index])return "✓ 本次全部所需数据库与本人昵称已核验";if(A(Get(c,"nickname_hints")).Length>1)return "历史昵称提示不一致，请在详情核对路径并读取验证。";if(S(c,"nickname").Length>0||S(accounts[index],"nickname").Length>0)return "历史已识别，待本次核验；历史昵称不证明当前登录账号。";return S(accounts[index],"root").Length==0?"尚未选择账号。请按昵称提示和目录详情明确选择来源或目标。":"首次未识别：当前无法可靠地从登录状态直接得知此目录昵称，请核对路径后读取验证。";}
    void SyncLabels(){for(int i=0;i<2;i++){string root=S(accounts[i],"root");if(root.Length==0)continue;var found=SelectedDirectory(i);if(found.Count==0){found=D("root",root,"owner",S(accounts[i],"owner"),"label","保留的账号路径 · 待核验","state","not_discovered","nickname",S(accounts[i],"nickname"));candidates.Add(found);}if(accountVerified[i]&&S(accounts[i],"nickname").Length>0){found["nickname"]=S(accounts[i],"nickname");found["nickname_hints"]=new object[]{S(accounts[i],"nickname")};found["label"]=S(accounts[i],"nickname")+" · 上次识别";}}}
    void ShowDirectoryGuide(){MessageBox.Show(this,"手动查找账号目录：\n1. 在本人已登录的微信中打开设置，查看文件/存储位置，以实际显示为准（不同版本名称可能不同）。\n2. 也可在资源管理器地址栏输入 shell:Personal 打开当前文档位置，寻找 xwechat_files。自定义目录请以微信设置为准。\n3. 进入本次账号目录，再选择 db_storage。\n\n常见层级：文档\\xwechat_files\\某账号目录\\db_storage\n应同时包含 message、session、contact；message 内有 message_0.db 和 message_resource.db。\n选到 message 子目录会明确返回父级 db_storage；缺库仍会列出，不会忽略。\n多个目录不能按编号、修改时间、昵称相似度或对外微信号猜账号。若设置只显示基础目录而不能确认子目录，当前无法可靠地从登录状态自动配对；不按时间或编号猜测。","手动去哪里找");}

    void AcceptTask(Dictionary<string,object> value){taskId=S(value,"id");historyPlan=S(value,"history_plan");if(historyPlan.Length==0)historyPlan=S(value,"plan");var values=A(Get(value,"accounts"));for(int i=0;i<2;i++){accounts[i]=values.Length==2?new Dictionary<string,object>(Obj(values[i])):Account();accountVerified[i]=false;var errors=A(Get(value,"errors"));accountReadErrors[i]=errors.Length==2?Convert.ToString(errors[i]):"";var reports=A(Get(value,"failure_reports"));if(reports.Length==2&&Convert.ToString(reports[i]).Length>0)accountReadErrors[i]+="\n当前诊断报告："+Convert.ToString(reports[i]);}includeVoice=B(value,"include_voice");includeAttachments=B(value,"include_attachments");sourceImport.Reset();identityConfirmed=false;InvalidatePlan();SyncLabels();step=0;SetStatus("已载入任务 "+taskId+"。旧回执保留；请重新定位并读取双方账号后生成新预览。私有目录："+work);Render();}
    void LoadLocalTask(){var f=new OpenFileDialog{Title="选择当前工作区 tasks 下的 task.json",Filter="任务索引|task.json",InitialDirectory=Path.Combine(work,"tasks")};if(f.ShowDialog(this)!=true)return;string id=new DirectoryInfo(Path.GetDirectoryName(f.FileName)).Name;Run("正在载入任务进度…",D("action","task-load","task_id",id),AcceptTask);}
    void ChooseRoot(int index){var dialog=new System.Windows.Forms.FolderBrowserDialog{Description="选择 文档/xwechat_files/本次账号/db_storage（message 子目录会返回父级并检查缺库）"};if(dialog.ShowDialog()!=System.Windows.Forms.DialogResult.OK)return;Run("正在检查账号目录层级和必要库…",D("action","inspect-account-directory","root",dialog.SelectedPath),delegate(Dictionary<string,object> value){SelectRoot(index,value);SetStatus(S(value,"normalization").Length>0?S(value,"normalization")+"；"+AccountDirectoryStatus(index):AccountDirectoryStatus(index),!DirectoryReady(index));Render();});}
    void SelectRoot(int index,Dictionary<string,object> value){bool same=S(accounts[index],"owner")==S(value,"owner");accounts[index]["root"]=S(value,"root");accounts[index]["owner"]=S(value,"owner");if(!same)accounts[index]["keys"]="";accounts[index]["nickname"]=S(value,"nickname");InvalidateAccount(index);bool found=false;for(int n=0;n<candidates.Count;n++)if(String.Equals(S(candidates[n],"root"),S(value,"root"),StringComparison.OrdinalIgnoreCase)){candidates[n]=new Dictionary<string,object>(value);found=true;break;}if(!found)candidates.Add(new Dictionary<string,object>(value));SyncLabels();}

    void NewTask(){Run("正在建立独立任务…",D("action","task-new"),AcceptTask);}
    Dictionary<string,object> TaskSnapshot(){return D("action","task-save","task_id",taskId,"accounts",accounts,"errors",accountReadErrors,"include_voice",includeVoice,"include_attachments",includeAttachments);}
    void ExportTask(){if(taskId.Length==0){SetStatus("先读取一个账号或载入已有任务，才有可接续资料。",true);return;}if(MessageBox.Show(this,"接续目录可能包含双方密钥、聊天和整库原件，仅用于同一授权操作者换机，请私密保管。\n\n是否准备导出？","私人任务接续",MessageBoxButton.OKCancel)!=MessageBoxResult.OK)return;var dialog=new System.Windows.Forms.FolderBrowserDialog{Description="选择保存位置，工具会在其中新建一个独立接续目录"};if(dialog.ShowDialog()!=System.Windows.Forms.DialogResult.OK)return;Run("正在核对并复制当前任务…",D("action","task-export","task_id",taskId,"output",Path.Combine(dialog.SelectedPath,"ChatRescue-Task-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,6))),delegate(Dictionary<string,object> result){SetStatus("接续目录已生成："+S(result,"path")+"。请私密复制整个目录；换机后双方实库仍需重新核验。");OpenDirectory(S(result,"path"));});}
    void ImportTask(){var dialog=new System.Windows.Forms.FolderBrowserDialog{Description="选择含 transfer.json 的私人接续目录"};if(dialog.ShowDialog()!=System.Windows.Forms.DialogResult.OK)return;Run("正在核对接续目录的完整清单…",D("action","task-import","path",dialog.SelectedPath),AcceptTask);}

    void AcceptStatus(Dictionary<string,object> value) {
        environment=Obj(Get(value,"doctor"));work=S(value,"workspace");
        if(!loadedSettings){loadedSettings=true;previousSettings=Obj(Get(value,"settings"));identityConfirmed=false;}
        if(executable.Length==0)executable=S(value,"executable");
        candidates.Clear();foreach(var c in A(Get(value,"candidates")))candidates.Add(Obj(c));
        SyncLabels();for(int i=0;i<2;i++)if(!DirectoryReady(i)&&accountVerified[i]){accountVerified[i]=false;InvalidatePlan();}
        topStatus.Text=B(environment,"ready")?"● 本机运行环境已就绪":"○ 还有环境项目需要准备";topStatus.Foreground=B(environment,"ready")?teal:muted;
        SetStatus("账号目录与历史昵称提示已刷新；当前选择保留，历史提示不代表本次核验通过。");Render();
    }

    void Render() {
        stepLabel.Text="第 "+(step+1)+" 步 / 共 3 步";title.Text=titles[step];subtitle.Text=subtitles[step];
        for(int i=0;i<3;i++){steps[i].Background=i==step?ColorBrush("#EAF0FF"):Brushes.Transparent;steps[i].Foreground=i==step?blue:muted;}
        page.Children.Clear();
        if(step==0){Preparation();Login();}else if(step==1){if(plan==null&&simulation==null)Reading();else Preview();}else{if(plan!=null&&S(plan,"state")=="ready")Preview();else Result();}
    }

    void Preparation(){
        if(prepared){var ready=Card(page);Heading(ready,"✓ 本机环境与官方微信已检查");Para(ready,"微信程序："+executable+"\n私有工作区："+work+"\n当前任务："+(taskId.Length>0?taskId:"首次读取时创建"));var readyRow=Row(ready);readyRow.Children.Add(Button("重新检查软件",delegate{prepared=false;Render();}));if(historyPlan.Length>0&&File.Exists(Path.Combine(historyPlan,"sync-plan.json")))readyRow.Children.Add(Button("查看已有预览与回执",delegate{Run("正在查看回执…",D("action","resume","plan",historyPlan),delegate(Dictionary<string,object> result){plan=result;step=1;previewConfirmed=false;Render();});}));return;}
        bool pinned=S(config,"client_manifest").Length>0;
        var card=Card(page);Heading(card,prepared?"✓ 本机准备已完成":"先让软件完成准备");Para(card,"检查 Python、SQLCipher 和原版微信。所有任务与原件保存在本机私有目录。");
        var checks=Obj(Get(environment??D(),"checks"));var row=Row(card);foreach(var key in new[]{"supported_host","python_3_14","sqlcipher"})row.Children.Add(Text((B(checks,key)?"✓ ":"○ ")+new Dictionary<string,string>{{"supported_host","Windows 64 位"},{"python_3_14","Python 3.14"},{"sqlcipher","SQLCipher"}}[key]+"    ",13,B(checks,key)?teal:muted));
        Para(card,pinned?"包内固定腾讯微信（核对完整版本和文件清单；不代表账号数据已隔离）":"腾讯原版微信程序");var path=new TextBox{Text=executable,MinWidth=400,IsReadOnly=pinned};path.TextChanged+=delegate{if(executable!=path.Text){executable=path.Text;prepared=false;InvalidatePlan();}};card.Children.Add(path);
        var buttons=Row(card);buttons.Children.Add(Button("选择微信程序",delegate{var f=new OpenFileDialog{Filter="微信程序|Weixin.exe;WeChat.exe"};if(f.ShowDialog(this)==true){executable=f.FileName;prepared=false;InvalidatePlan();Render();}}));
        buttons.Children[0].IsEnabled=!pinned;
        buttons.Children.Add(Button(prepared?"重新检查":"自动检查并准备",delegate{Run("正在验证版本与腾讯签名…",D("action","prepare","executable",executable),delegate(Dictionary<string,object> r){prepared=B(Obj(Get(r,"doctor")),"ready");SetStatus(prepared?"准备完成。原版微信签名有效，下一步登录两个账号。":"环境依赖未就绪，请查看检查项目。",!prepared);Render();});},true));
        var next=Button("环境已检查，请在下方读取账号",delegate{step=0;Render();},true);next.IsEnabled=prepared;Row(card).Children.Add(next);
        var note=Card(page);Heading(note,"本次任务");if(historyPlan.Length>0&&File.Exists(Path.Combine(historyPlan,"sync-plan.json")))Row(note).Children.Add(Button("查看已有预览与回执",delegate{Run("正在查看历史回执…",D("action","resume","plan",historyPlan),delegate(Dictionary<string,object> result){plan=result;step=1;previewConfirmed=false;Render();});}));Para(note,"任务："+(taskId.Length>0?taskId:"首次读取时创建")+"；私有工作区："+work);Para(note,"选择本次要恢复到的目标账号和提供记录的来源账号，可按任意顺序登录并读取。每次只打开一个微信；扫码与手机确认由本人完成。");Para(note,"当前取钥接受 4.2.x.x x64，保留逐条方向校验；实机证据仍仅覆盖 4.2.0.12。完全空白账号缺少消息身份映射时会停止生成计划，不删除记录，也不会显示为恢复成功。");
    }

    void AccountCard(int index,Panel parent){
        SyncLabels();
        bool hasHint=AccountName(index)!=(index==0?"目标账号":"来源账号");var card=Card(parent);Heading(card,(index==0?"目标账号 · 要恢复到这里":"来源账号 · 提供聊天记录"));card.Children.Add(Text(!accountVerified[index]&&hasHint?"上次识别（待核验）："+AccountName(index):AccountName(index),22,accountVerified[index]?(index==0?blue:teal):muted,true));
        Para(card,readingIndex==index?"读取中：正在验证全部必要数据库，完成后识别本人昵称…":AccountDirectoryStatus(index));
        if(accountReadErrors[index].Length>0)card.Children.Add(Text("读取未完成："+accountReadErrors[index],12,ColorBrush("#B84C45")));
        var combo=new ComboBox{Margin=new Thickness(0,8,0,8)};combo.Items.Add(new ComboBoxItem{Content="选择本次账号目录…",Tag=null});combo.SelectedIndex=0;
        foreach(var candidate in candidates){string label=S(candidate,"label");if(S(candidate,"root")==S(accounts[index],"root")&&hasHint&&DirectoryReady(index))label=AccountName(index)+(accountVerified[index]?" · 本次已核验":" · 待重新核验");var item=new ComboBoxItem{Content=label,Tag=candidate,ToolTip=S(candidate,"root")+"\n"+S(candidate,"owner")};combo.Items.Add(item);if(S(candidate,"root")==S(accounts[index],"root"))combo.SelectedItem=item;}
        combo.SelectionChanged+=delegate{var item=combo.SelectedItem as ComboBoxItem;if(item!=null&&item.Tag==null&&S(accounts[index],"root").Length>0){accounts[index]=Account();InvalidateAccount(index);Render();return;}if(item!=null&&item.Tag!=null){var selected=Obj(item.Tag);if(S(accounts[index],"root")!=S(selected,"root")){SelectRoot(index,selected);Render();}}};card.Children.Add(combo);
        var folderActions=Row(card);folderActions.Children.Add(Button("手动选择账号目录",delegate{ChooseRoot(index);}));folderActions.Children.Add(Button("去哪里找？",ShowDirectoryGuide));
        if(S(accounts[index],"root").Length>0){var details=new Expander{Header="账号详情（可选）",Margin=new Thickness(0,5,0,4)};details.Content=Text("昵称："+AccountName(index)+"\n状态："+AccountDirectoryStatus(index)+"\n目录身份候选（核验后确认）："+S(accounts[index],"owner")+"\n所选 db_storage："+S(accounts[index],"root"),11,muted);card.Children.Add(details);}
        string slot=index==0?"A":"B";
        var launch=Button("打开微信并登录账号",delegate{Run("正在打开账号 "+slot+" 的原版微信…",D("action","launch","slot",slot,"a",accounts[0],"executable",executable),delegate(Dictionary<string,object> r){accountVerified[index]=false;sourceImport.Reset();InvalidatePlan();SetStatus(S(r,"instruction"));Render();});},true);launch.IsEnabled=prepared;Row(card).Children.Add(launch);
        if(index==1)Para(card,"可先读取来源或目标；来源账号需先确认所需聊天完整。");
        if(index==1) {
            Para(card,sourceImport.Reminder("B",false));
            var history=new CheckBox{Content="我已核对来源账号中与目标账号的聊天完整，必要时已从来源账号手机导入",IsChecked=sourceImport.Confirmed,Margin=new Thickness(0,10,0,10)};
            history.Click+=delegate{sourceImport.Confirm(history.IsChecked==true);InvalidatePlan();Render();};card.Children.Add(history);
        }
        var actions=Row(card);actions.Children.Add(Button("导入已有密钥",delegate{var f=new OpenFileDialog{Filter="私有密钥清单|*.json"};if(f.ShowDialog(this)==true){accounts[index]["keys"]=f.FileName;InvalidateAccount(index);Render();}}));
        Para(card,"导入已有密钥时，请保留同目录的配套取钥回执，例如 keys.json 与 keys.capture.json；缺少回执可能导致预览方向校验失败。");
        var read=Button((index==0?"读取目标账号":"读取来源账号"),delegate{Run("正在核验账号 "+slot+"：已登录时取钥，已退出时验证已有密钥…",D("action","capture","slot",slot,"account",accounts[index],"identity_confirmed",identityConfirmed,"source_history_confirmed",sourceImport.Confirmed,"executable",executable,"include_voice",includeVoice,"include_attachments",includeAttachments),delegate(Dictionary<string,object> r){accounts[index]=Obj(Get(r,"account"));accountVerified[index]=true;SyncLabels();InvalidatePlan();SetStatus((index==0?"目标账号已核验：":"来源账号已核验：")+AccountName(index)+"。可继续读取另一账号；双方完成后正常退出微信，再生成来源到目标的预览。");Render();});},true);read.IsEnabled=prepared && DirectoryReady(index) && identityConfirmed && S(accounts[index],"root").Length>0 && sourceImport.CanRead(slot,accountVerified[0]);actions.Children.Add(read);
    }

    void Login(){
        MediaOptions();
        var card=Card(page);Heading(card,accountVerified[0]&&accountVerified[1]?"双方账号本次核验已完成":"选择本次要恢复到的账号与记录来源");Para(card,"目标账号是要写入恢复结果的账号；来源账号提供保留的聊天。\n两边均由你选择，可按任意顺序登录并读取；更换登录账号前正常退出当前微信。来源账号需从手机导入或核对所需聊天完整。已有密钥且微信已退出时，可直接读取验证。新会话不会自动套用上次的账号选择，可显式载入历史任务。");
        var check=new CheckBox{Content="我确认所选来源和目标是本次双方授权的两个不同账号",IsChecked=identityConfirmed,Margin=new Thickness(0,12,0,12)};check.Click+=delegate{identityConfirmed=check.IsChecked==true;InvalidatePlan();Render();};card.Children.Add(check);
        Para(card,"已识别账号可按昵称选择；同名请核对路径。历史昵称仅是提示，不代表本次核验。首次未知目录请点“去哪里找？”，不会按目录顺序或修改时间自动配对。");
        var buttons=Row(card);
        buttons.Children.Add(Button("刷新账号目录",delegate{Run("正在查找本机账号目录…",D("action","status"),AcceptStatus);}));
        buttons.Children.Add(Button("载入已有账号任务",ImportAccounts));
        buttons.Children.Add(Button("载入上次账号选择",LoadPreviousAccounts));
        var row=new Grid();row.ColumnDefinitions.Add(new ColumnDefinition());row.ColumnDefinitions.Add(new ColumnDefinition());page.Children.Add(row);
        for(int i=0;i<2;i++){var p=new StackPanel{Margin=new Thickness(i==0?0:9,0,i==0?9:0,0)};Grid.SetColumn(p,i);row.Children.Add(p);AccountCard(i,p);}
        var footer=Card(page);
        var next=Button("下一步：检查恢复预览 →",delegate{step=1;Render();},true);next.IsEnabled=identityConfirmed&&accountVerified[0]&&accountVerified[1]&&sourceImport.Confirmed;Row(footer).Children.Add(next);
    }

    void Reading(){
        var card=Card(page);Heading(card,"验证、备份与单向替换预览");Para(card,"本次用来源账号重建目标账号中的这一段会话。目标原会话仅在候选中移除，确认具体预览后才应用。系统类型 10000、10002 暂不导入并统计，其他未知方向拒绝。");Para(card,"1   选择要恢复到的目标账号与提供记录的来源账号，按任意顺序登录并读取核验。");Para(card,"2   核对来源账号所需聊天完整，双方核验完成后正常退出微信。已有密钥且微信已退出时可直接验证。");Para(card,"3   点击生成预览。软件检查退出状态、保存 DB/WAL/SHM 原件，再建立恢复候选。");
        Para(card,"目标："+AccountName(0)+"；来源："+AccountName(1)+"。如需调整，请返回第一步。\n私有工作区："+work);
        var generate=Button("微信已退出，生成来源 → 目标替换预览",delegate{InvalidatePlan();Run("正在备份、读取并生成预览，请不要启动微信…",D("action","plan","a",accounts[0],"b",accounts[1],"identity_confirmed",identityConfirmed,"source_history_confirmed",sourceImport.Confirmed,"include_voice",includeVoice,"include_attachments",includeAttachments),delegate(Dictionary<string,object> r){plan=r;historyPlan=S(r,"plan");simulation=null;step=1;SetStatus("候选已生成。请核对身份与数量，确认后才会应用。");Render();});},true);
        generate.IsEnabled=identityConfirmed && accountVerified[0] && accountVerified[1] && sourceImport.Confirmed;Row(card).Children.Add(generate);
        Row(card).Children.Add(Button("载入上次隔离测试报告",LoadSimulation));
    }

    string Summary(){if(plan==null)return "";var names=Obj(Get(plan,"accounts"));var dirs=Obj(Get(plan,"directions"));if(S(plan,"mode")=="replace-a-from-b"){var d=Obj(Get(dirs,"A"));int excluded=0;foreach(var value in Obj(Get(d,"excluded_system_messages")).Values)excluded+=Convert.ToInt32(value);return "目标账号："+S(Obj(Get(names,"A")),"nickname")+"\n来源账号："+S(Obj(Get(names,"B")),"nickname")+"\nA 中此会话将移除 "+N(d,"removed")+" 条，从 B 导入 "+N(d,"added")+" 条\n暂不导入系统条目 "+excluded+" 条；B 只读，不应用写入。";}var s=new StringBuilder();for(int i=0;i<2;i++){string slot=i==0?"A":"B";var d=Obj(Get(dirs,slot));s.AppendLine(S(Obj(Get(names,slot)),"nickname")+"：新增 "+N(d,"added")+"，去重 "+N(d,"skipped"));}return s.ToString();}
    bool RoutedPlan(){var d=Obj(Get(Obj(Get(plan,"directions")),"A"));return S(Obj(Get(d,"structure_check")),"state")=="matched"&&A(Get(d,"target_shard_imports")).Length>0;}
    void MediaOptions(){
        var card=Card(page);Heading(card,"媒体本体恢复 · 实验功能");
        var voice=new CheckBox{Content="尝试恢复原语音（缺库或缺音频时跳过本体）",IsChecked=includeVoice,Margin=new Thickness(0,6,0,8)};
        voice.Click+=delegate{includeVoice=voice.IsChecked==true;accountVerified[0]=false;accountVerified[1]=false;InvalidatePlan();SetStatus("语音选项已改变，请重新核验双方账号及已有媒体库密钥；顺序不限。");Render();};card.Children.Add(voice);
        var attachments=new CheckBox{Content="同时恢复图片、视频和文件本体",IsChecked=includeAttachments,Margin=new Thickness(0,6,0,8)};
        attachments.Click+=delegate{includeAttachments=attachments.IsChecked==true;InvalidatePlan();Render();};card.Children.Add(attachments);
        Para(card,"有可验证条件的媒体正常恢复。图片/视频/文件消息状态未适配时跳过本体，保留消息并按状态计数；身份与结构保护不变。缺语音库、音频、附件或图片解密证据时保留消息条目、跳过本体，预览按原因计数；身份/关联冲突、数据库完整性及哈希异常仍停止。先通过离线检查，再由本人在 A 微信逐项打开或播放验收，手机迁移另行验收。");
    }
    string AttachmentStateSummary(Dictionary<string,object> d){var text=new StringBuilder("因未适配附件状态跳过本体："+N(d,"attachment_state_skipped")+" 条；对应消息条目保留，未恢复这些媒体本体。\n");foreach(var value in A(Get(d,"attachment_state_skip_details"))){var item=Obj(value);string kind=S(item,"kind")=="3"?"图片":S(item,"kind")=="43"?"视频":"文件";text.AppendLine((S(item,"side")=="source"?"来源":"目标")+kind+"：status="+S(item,"status")+"，upload_status="+S(item,"upload_status")+"，跳过 "+N(item,"count")+" 条");}return text.ToString();}
    string MediaSummary(){
        var d=Obj(Get(Obj(Get(plan,"directions")),"A"));
        if(!B(d,"media_experimental"))return "本次仅导入消息条目，未启用媒体本体恢复。";
        return AttachmentStateSummary(d)+"媒体候选：图片 "+(N(d,"image_added")+N(d,"image_repaired"))+"，语音 "+(N(d,"voice_added")+N(d,"voice_repaired"))+"，视频 "+(N(d,"video_added")+N(d,"video_repaired"))+"，文件 "+(N(d,"file_added")+N(d,"file_repaired"))+"。\n因格式不支持跳过本体：图片 "+N(d,"image_unsupported")+"，视频 "+N(d,"video_unsupported")+"；对应消息条目仍导入。\n因条件缺失跳过本体：语音来源 "+(N(d,"voice_source_unavailable"))+"，语音目标 "+N(d,"voice_target_unavailable")+"；图片来源 "+N(d,"image_source_unavailable")+"、图片解密证据 "+N(d,"image_key_unavailable")+"、图片目标证据 "+N(d,"image_target_unavailable")+"；视频 "+N(d,"video_source_unavailable")+"，文件 "+N(d,"file_source_unavailable")+"。\n媒体原件：A-plan/original-assets；全部语音库原件：A-plan/originals/message/media_N.db。\n离线候选已核验；微信内打开、播放及手机迁移尚未验收。";
    }
    string RoutingSummary(){var d=Obj(Get(Obj(Get(plan,"directions")),"A"));if(!RoutedPlan())return "此旧计划缺少结构与分片验证，请用新版重新生成预览。";var text=new StringBuilder("A/B 必需结构及候选会话字段、索引核对通过。\n");foreach(var value in A(Get(d,"target_shard_imports"))){var item=Obj(value);var epoch=new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc);text.AppendLine(S(item,"path")+"：导入 "+N(item,"imported")+" 条，"+epoch.AddSeconds(Convert.ToDouble(Get(item,"first_timestamp"))).ToLocalTime().ToString("yyyy-MM-dd")+" 至 "+epoch.AddSeconds(Convert.ToDouble(Get(item,"last_timestamp"))).ToLocalTime().ToString("yyyy-MM-dd"));}text.Append("仅清理此会话失效的搜索范围；微信搜索重建与实际显示仍待验收。");return text.ToString();}
    string StateLabel(string value){switch(value){case "ready":return "候选已验证，等待本人确认";case "applied":return "应用完成";case "partial":return "部分完成，请查看各侧回执";case "interrupted":return "可能中断，禁止重复应用";case "failed":return "未完成，请保留回执";case "undone":return "已撤回";default:return "需要核对本机回执";}}
    void Preview(){
        if(simulation!=null){var c=Card(page);Heading(c,"隔离模拟报告 · 不应用真实账号");Para(c,"这是此前实验的回执，不能作为真实账号的应用预览。");var r=Row(c);r.Children.Add(Text(N(simulation,"added")+" 条恢复       "+N(simulation,"skipped")+" 条重复去重",25,blue,true));Para(c,"原始空白目标："+(B(simulation,"empty_target_uninitialized")?"缺少消息身份映射，直接恢复被拒绝":"请查看私有报告"));Para(c,"实验副本完成初始化后验证通过。"+(B(simulation,"originals_unchanged")?"报告中的原库哈希核对一致。":"原库哈希尚未验证，请查看私有报告。")+"电脑显示与手机迁移未验收。");Row(c).Children.Add(Button("返回读取真实账号",delegate{simulation=null;step=0;Render();},true));return;}
        if(plan==null){var c=Card(page);Heading(c,"还没有可应用的恢复预览");Para(c,"先读取两个账号并生成候选，或载入已有的任务目录。");Row(c).Children.Add(Button("返回读取聊天",delegate{step=0;Render();},true));Row(c).Children.Add(Button("载入历史任务",LoadPlan));return;}
        var card=Card(page);Heading(card,"来源 → 目标单会话替换预览");card.Children.Add(Text(Summary(),22,blue,true));Para(card,S(plan,"mode")=="replace-a-from-b"?"确认后将替换 A 中与 B 的整段会话，A 独有的消息也会移出当前会话；其他会话保留，B 不写入。旧会话完整原件保存在任务目录，真实消息编号与收发身份按 A 视角转换。":"这是旧版双向任务，仅供查看；本版请重新生成 B → A 替换预览。");Para(card,"当前任务状态："+StateLabel(S(plan,"state")));if(B(plan,"history_only"))Para(card,"这是迁入的历史预览，仅供查看；请返回第一步重新核验，生成新预览。旧应用或中断任务应保留并新建任务。");Para(card,"任务与原件目录："+S(plan,"plan"));
        if(step==1){
            Para(card,RoutingSummary());Para(card,MediaSummary());
            if(S(plan,"state")=="ready"&&!B(plan,"history_only"))Row(card).Children.Add(Button("预览已核对，下一步确认应用 →",delegate{step=2;Render();},true));
            else if(S(plan,"state")!="ready")Row(card).Children.Add(Button("查看结果回执",delegate{step=2;Render();},true));
            Row(card).Children.Add(Button("打开任务与原件目录",delegate{OpenDirectory(S(plan,"plan"));}));
            return;
        }
        Para(card,"这是应用前的最终确认。请保持微信退出，确认后才修改目标账号；来源账号不写入。详细分片和媒体检查可返回第二步查看。");
        var check=new CheckBox{Content="已核对双方昵称、数量、时间范围、收发方向，并同意应用本次计划",IsChecked=previewConfirmed,Margin=new Thickness(0,14,0,12)};
        var apply=Button("确认应用这份替换预览",Apply,true);apply.IsEnabled=!B(plan,"history_only") && previewConfirmed && S(plan,"state")=="ready" && S(plan,"mode")=="replace-a-from-b"&&RoutedPlan();
        check.Click+=delegate{previewConfirmed=check.IsChecked==true;apply.IsEnabled=!B(plan,"history_only")&&previewConfirmed&&S(plan,"state")=="ready"&&S(plan,"mode")=="replace-a-from-b"&&RoutedPlan();};card.Children.Add(check);Row(card).Children.Add(apply);Row(card).Children.Add(Button("打开任务目录",delegate{OpenDirectory(S(plan,"plan"));}));
        if(S(plan,"state")!="ready")Row(card).Children.Add(Button("查看结果回执",delegate{step=2;Render();},true));
    }

    void Apply(){
        if(plan==null || B(plan,"history_only") || !previewConfirmed || S(plan,"state")!="ready" || S(plan,"mode")!="replace-a-from-b"||!RoutedPlan())return;
        string text=Summary()+"\n原件保留在："+S(plan,"plan")+"\n\n请保持所有微信退出。确认应用这份计划？";
        if(MessageBox.Show(this,text,"确认本次具体恢复计划",MessageBoxButton.OKCancel,MessageBoxImage.Question)!=MessageBoxResult.OK)return;
        Run("正在核对原库与候选并应用，请等待回执…",D("action","apply","plan",S(plan,"plan"),"plan_hash",S(plan,"plan_hash"),"confirmed",true),delegate(Dictionary<string,object> result){plan["state"]=S(result,"state");step=2;SetStatus(S(result,"state")=="applied"?"数据库应用完成。请本人打开原版微信验收显示。":"请查看各侧回执，不能视为双方全部成功。");Render();});
    }

    void Result(){
        var card=Card(page);Heading(card,"分阶段核对结果");
        Para(card,plan==null?"数据库：尚未载入真实任务回执":"数据库状态："+StateLabel(S(plan,"state")));Para(card,clientConfirmed?"电脑显示：本人已确认":"电脑显示：尚未由本人确认");Para(card,"手机迁移：未验收 · 不会自动同步到手机");
        Para(card,"请在原版微信核对联系人、收发方向、时间、正文或卡片，以及已有消息。启用媒体模式时，还请逐项打开图片、视频和文件并播放语音；缺失或不能打开时保留原件与回执。");
        var confirm=Button("我已在电脑微信逐项核对",delegate{if(plan==null)return;Run("正在记录本人的电脑验收…",D("action","confirm-client","plan",S(plan,"plan"),"plan_hash",S(plan,"plan_hash"),"confirmed",true),delegate{clientConfirmed=true;SetStatus("已记录本人电脑显示确认；手机迁移仍未验收。");Render();});},true);confirm.IsEnabled=plan!=null&&!B(plan,"history_only")&&S(plan,"state")=="applied"&&!clientConfirmed;Row(card).Children.Add(confirm);
        if(plan!=null)Row(card).Children.Add(Button("打开原件与报告目录",delegate{OpenDirectory(S(plan,"plan"));}));
        if(plan!=null && !B(plan,"history_only") && S(plan,"state")=="applied")Row(card).Children.Add(Button("撤回本次替换",delegate{if(MessageBox.Show(this,"仅在两个账号都没有新消息或数据库变化时允许撤回。撤回前还会保存当前原件。\n\n确认撤回这份任务？\n"+Summary(),"确认撤回",MessageBoxButton.OKCancel,MessageBoxImage.Question)!=MessageBoxResult.OK)return;Run("正在核对后续变化并撤回…",D("action","undo","plan",S(plan,"plan"),"plan_hash",S(plan,"plan_hash"),"confirmed",true),delegate(Dictionary<string,object> result){plan["state"]=S(result,"state");clientConfirmed=false;SetStatus("已撤回本次替换，请保留撤回前原件并检查微信。");Render();});}));
    }

    void ImportAccounts(){var f=new OpenFileDialog{Filter="已验证账号任务|verified-accounts.json|JSON 文件|*.json"};if(f.ShowDialog(this)!=true)return;Run("正在核对账号取钥回执…",D("action","import-accounts","path",f.FileName),delegate(Dictionary<string,object> result){var values=A(Get(result,"accounts"));for(int i=0;i<2;i++){accounts[i]=Obj(values[i]);bool exists=false;foreach(var c in candidates)if(S(c,"root")==S(accounts[i],"root"))exists=true;if(!exists)candidates.Add(D("root",S(accounts[i],"root"),"owner",S(accounts[i],"owner"),"label",AccountName(i)));}accountVerified[0]=accountVerified[1]=false;sourceImport.Reset();identityConfirmed=false;InvalidatePlan();step=0;SetStatus(S(result,"instruction"));Render();});}
    void LoadSimulation(){var f=new OpenFileDialog{Filter="隔离模拟回执|simulation-report.json|JSON 文件|*.json"};if(f.ShowDialog(this)!=true)return;Run("正在读取隔离模拟报告…",D("action","simulation-report","path",f.FileName),delegate(Dictionary<string,object> result){simulation=result;plan=null;step=1;SetStatus("当前为隔离测试结果，不能应用到真实账号。");Render();});}
    void AcceptHistoricalPlan(Dictionary<string,object> result){var task=Obj(Get(result,"task"));AcceptTask(task);plan=Obj(Get(result,"view"));historyPlan=S(plan,"plan");simulation=null;previewConfirmed=false;clientConfirmed=B(plan,"client_confirmed");step=1;SetStatus(taskId.Length>0?"已载入历史预览并关联独立任务，请核对回执状态。":"当前查看工作区外的独立历史预览；导出接续暂不可用，请通过原工作区的任务索引继续。");Render();}
    void LoadPlan(){var dialog=new System.Windows.Forms.FolderBrowserDialog{Description="选择含 sync-plan.json 的任务目录"};if(dialog.ShowDialog()!=System.Windows.Forms.DialogResult.OK)return;Run("正在载入历史回执…",D("action","task-from-plan","plan",dialog.SelectedPath),AcceptHistoricalPlan);}

    void OpenDirectory(string path){if(Directory.Exists(path))Process.Start(new ProcessStartInfo(path){UseShellExecute=true});}

    async void Run(string label,Dictionary<string,object> request,Action<Dictionary<string,object>> complete){
        if(busy)return;busy=true;shell.IsEnabled=false;progress.Visibility=Visibility.Visible;SetStatus(label);if(S(request,"action")=="capture"){readingIndex=S(request,"slot")=="B"?1:0;Render();}
        try{
            string action=S(request,"action");
            if((action=="capture"||action=="plan")&&taskId.Length==0){var created=await Task.Run(delegate{return Backend(D("action","task-new"));});taskId=S(created,"id");}
            if(taskId.Length>0&&(action=="capture"||action=="plan"||action=="task-export")){request["task_id"]=taskId;await Task.Run(delegate{return Backend(TaskSnapshot());});}
            var result=await Task.Run(delegate{return Backend(request);});if(S(request,"action")=="capture"){readingIndex=-1;accountReadErrors[S(request,"slot")=="B"?1:0]="";}complete(result);
            if(S(request,"action")=="capture" || S(request,"action")=="import-accounts"){
                try{await Task.Run(delegate{return Backend(D("action","save-settings","executable",executable,"accounts",accounts));});}
                catch{SetStatus(message+"（账号路径设置暂未保存，任务回执保留。）",true);}
            }
        }
        catch(Exception error){SetStatus(error.Message,true);readingIndex=-1;if(S(request,"action")=="capture"){int index=S(request,"slot")=="B"?1:0;accountReadErrors[index]=error.Message;accountVerified[index]=false;InvalidatePlan();Render();}if(plan!=null&&S(request,"action")=="apply"){plan["state"]="needs_receipt_review";step=2;Render();}}
        {
            if(taskId.Length>0&&(S(request,"action")=="capture"||S(request,"action")=="plan")){
                try{await Task.Run(delegate{return Backend(TaskSnapshot());});}catch{SetStatus(message+"（任务索引保存失败，已有回执保留，请打开工作区。）",true);}
            }
            busy=false;shell.IsEnabled=true;progress.Visibility=Visibility.Collapsed;
        }
    }

    protected virtual Dictionary<string,object> Backend(Dictionary<string,object> request){
        var start=new ProcessStartInfo(S(config,"python"),"-B -m chat_rescue.windows_backend"){WorkingDirectory=S(config,"repository"),UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        start.EnvironmentVariables["PYTHONUTF8"]="1";start.EnvironmentVariables["SQLCIPHER_LIBRARY"]=S(config,"sqlcipher");
        if(S(config,"client_manifest").Length>0){start.EnvironmentVariables["CHAT_RESCUE_CLIENT_MANIFEST"]=S(config,"client_manifest");start.EnvironmentVariables["CHAT_RESCUE_CLIENT_SHA256"]=S(config,"client_manifest_sha256");}
        if(S(config,"workspace").Length>0)start.EnvironmentVariables["CHAT_RESCUE_WORKSPACE"]=S(config,"workspace");
        using(var process=Process.Start(start)){
            var stderr=process.StandardError.ReadToEndAsync();process.StandardInput.Write(BackendTransport.EscapeJsonForPipe(json.Serialize(request)));process.StandardInput.Close();string output=process.StandardOutput.ReadToEnd();process.WaitForExit();stderr.Wait();
            Dictionary<string,object> response;try{response=Obj(json.DeserializeObject(output));}catch{throw new Exception("后端没有返回有效回执，请保留本机任务目录；不要重复应用。");}
            if(!B(response,"ok"))throw new Exception(S(response,"error"));return Obj(Get(response,"result"));
        }
    }

    [STAThread] public static void Main(){try{new Application().Run(new Wizard());}catch(Exception error){MessageBox.Show("Chat Rescue 无法启动："+error.Message,"启动失败");}}
}}

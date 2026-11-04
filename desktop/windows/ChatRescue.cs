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
    readonly string[] titles = { "准备软件", "登录两个账号", "读取双方聊天", "确认补齐预览", "检查恢复结果" };
    readonly string[] subtitles = { "检查环境，准备本机恢复工作区", "先登录并核验 A，退出后再处理 B", "验证身份与全部消息分片，保留原件并生成候选", "核对双方昵称、收发方向和新增数量", "分别确认数据库、电脑显示和手机结果" };
    readonly Brush blue = ColorBrush("#2463EB"), ink = ColorBrush("#17263D"), muted = ColorBrush("#6E7D92"), teal = ColorBrush("#158A78");
    Dictionary<string, object> config, environment, plan, simulation;
    readonly Dictionary<string, object>[] accounts = { Account(), Account() };
    readonly List<Dictionary<string, object>> candidates = new List<Dictionary<string, object>>();
    StackPanel nav, page; Grid shell; TextBlock title, subtitle, stepLabel, status, topStatus;
    ProgressBar progress; Button[] steps = new Button[5];
    string executable = "", work = "", message = "先完成环境检查，再按左侧五步继续。";
    int step; bool busy, prepared, identityConfirmed, previewConfirmed, clientConfirmed, loadedSettings;
    readonly bool[] accountVerified = { false, false };
    readonly SourceImportFlow sourceImport = new SourceImportFlow();

    static Brush ColorBrush(string hex) { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)); }
    static Dictionary<string, object> Account() { return D("owner", "", "root", "", "keys", "", "nickname", ""); }
    static Dictionary<string, object> D(params object[] pairs) { var result = new Dictionary<string, object>(); for (int i=0; i<pairs.Length; i+=2) result[(string)pairs[i]]=pairs[i+1]; return result; }
    static Dictionary<string, object> Obj(object value) { return value as Dictionary<string, object> ?? new Dictionary<string, object>(); }
    static object Get(Dictionary<string, object> d, string key) { object v; return d != null && d.TryGetValue(key,out v) ? v : null; }
    static string S(Dictionary<string, object> d, string key) { return Convert.ToString(Get(d,key)) ?? ""; }
    static bool B(Dictionary<string, object> d, string key) { return Get(d,key) is bool && (bool)Get(d,key); }
    static int N(Dictionary<string, object> d, string key) { object v=Get(d,key); return v==null ? 0 : Convert.ToInt32(v); }
    static object[] A(object v) { return v as object[] ?? new object[0]; }
    string AccountName(int i) { string name=S(accounts[i],"nickname"); return name.Length>0 ? name : "微信 " + (i==0 ? "A" : "B"); }

    public Wizard() {
        Title="Chat Rescue · Windows 实验版"; Width=1180; Height=850; MinWidth=1030; MinHeight=740;
        WindowStartupLocation=WindowStartupLocation.CenterScreen;
        FontFamily=new FontFamily("Microsoft YaHei UI"); FontSize=14; Foreground=ink; Background=ColorBrush("#F7F9FC");
        config=Obj(json.DeserializeObject(File.ReadAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"backend-config.json"),Encoding.UTF8)));
        foreach(string key in new[]{"python","sqlcipher","repository","workspace"})
            if(S(config,key).Length>0)config[key]=BackendTransport.ResolveConfiguredPath(AppDomain.CurrentDomain.BaseDirectory,S(config,key));
        executable=S(config,"executable");
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
        brand.Children.Add(Text("↔  Chat Rescue",21,ink,true)); brand.Children.Add(Text("双方聊天互相补齐",12,muted)); DockPanel.SetDock(brand,Dock.Top);side.Children.Add(brand);
        var bottom=new StackPanel{Margin=new Thickness(23,20,15,25)};
        bottom.Children.Add(Link("一步一步使用指南",delegate { MessageBox.Show(this,"1. 检查环境并选择腾讯原版微信。\n2. 先打开 A，本人登录并读取核验。正常退出微信后再打开 B，登录后将手机 B 中与 A 的聊天导入电脑 B（已完整存在则直接核对），确认后读取 B。\n3. 两个账号核验完成、微信正常退出后生成预览。已有密钥可退出后直接读取验证。\n4. 核对昵称、数量、方向，确认后才应用。\n5. 在原版微信核对显示；手机迁移单独验收。\n\n遇到冲突或空白账号未初始化时停止，保留原件，不删除聊天。","使用指南"); }));
        bottom.Children.Add(Link("载入隔离测试报告",LoadSimulation));
        bottom.Children.Add(Text("0.3.6 · Windows 实验版",11,muted));
        bottom.Children.Add(Link("开源许可",delegate{OpenDirectory(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"licenses"));}));DockPanel.SetDock(bottom,Dock.Bottom); side.Children.Add(bottom);
        nav=new StackPanel{Margin=new Thickness(12,0,12,0)}; side.Children.Add(nav);
        for(int i=0;i<5;i++){int index=i; steps[i]=Button((i+1)+"   "+titles[i],delegate{step=index;Render();});steps[i].HorizontalContentAlignment=HorizontalAlignment.Left;steps[i].Margin=new Thickness(0,0,0,10);steps[i].Padding=new Thickness(16,15,8,15); nav.Children.Add(steps[i]);}
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
    void SetStatus(string value,bool error=false){message=value;status.Text=value;status.Foreground=error?ColorBrush("#B84C45"):muted;}

    void AcceptStatus(Dictionary<string,object> value) {
        environment=Obj(Get(value,"doctor"));work=S(value,"workspace");
        if(!loadedSettings){loadedSettings=true;var saved=Obj(Get(value,"settings"));if(S(saved,"executable").Length>0)executable=S(saved,"executable");var values=A(Get(saved,"accounts"));if(values.Length==2){for(int i=0;i<2;i++)accounts[i]=Obj(values[i]);}identityConfirmed=false;}
        if(executable.Length==0)executable=S(value,"executable");
        candidates.Clear();foreach(var c in A(Get(value,"candidates")))candidates.Add(Obj(c));
        topStatus.Text=B(environment,"ready")?"● 本机运行环境已就绪":"○ 还有环境项目需要准备";topStatus.Foreground=B(environment,"ready")?teal:muted;
        SetStatus("环境状态已更新。准备完成后，点击下一步登录两个账号。");Render();
    }

    void Render() {
        stepLabel.Text="第 "+(step+1)+" 步 / 共 5 步";title.Text=titles[step];subtitle.Text=subtitles[step];
        for(int i=0;i<5;i++){steps[i].Background=i==step?ColorBrush("#EAF0FF"):Brushes.Transparent;steps[i].Foreground=i==step?blue:muted;}
        page.Children.Clear();
        if(step==0)Preparation();else if(step==1)Login();else if(step==2)Reading();else if(step==3)Preview();else Result();
    }

    void Preparation(){
        var card=Card(page);Heading(card,prepared?"✓ 本机准备已完成":"先让软件完成准备");Para(card,"检查 Python、SQLCipher 和原版微信。所有任务与原件保存在本机私有目录。");
        var checks=Obj(Get(environment??D(),"checks"));var row=Row(card);foreach(var key in new[]{"supported_host","python_3_14","sqlcipher"})row.Children.Add(Text((B(checks,key)?"✓ ":"○ ")+new Dictionary<string,string>{{"supported_host","Windows 64 位"},{"python_3_14","Python 3.14"},{"sqlcipher","SQLCipher"}}[key]+"    ",13,B(checks,key)?teal:muted));
        Para(card,"腾讯原版微信程序");var path=new TextBox{Text=executable,MinWidth=400};path.TextChanged+=delegate{if(executable!=path.Text){executable=path.Text;prepared=false;InvalidatePlan();}};card.Children.Add(path);
        var buttons=Row(card);buttons.Children.Add(Button("选择微信程序",delegate{var f=new OpenFileDialog{Filter="微信程序|Weixin.exe;WeChat.exe"};if(f.ShowDialog(this)==true){executable=f.FileName;prepared=false;InvalidatePlan();Render();}}));
        buttons.Children.Add(Button(prepared?"重新检查":"自动检查并准备",delegate{Run("正在验证版本与腾讯签名…",D("action","prepare","executable",executable),delegate(Dictionary<string,object> r){prepared=B(Obj(Get(r,"doctor")),"ready");SetStatus(prepared?"准备完成。原版微信签名有效，下一步登录两个账号。":"环境依赖未就绪，请查看检查项目。",!prepared);Render();});},true));
        var next=Button("下一步：登录两个账号 →",delegate{step=1;Render();},true);next.IsEnabled=prepared;Row(card).Children.Add(next);
        var note=Card(page);Heading(note,"与 Mac 相同的五步流程");Para(note,"Windows 每次只打开一个原版微信：先 A，核验并正常退出后再 B。扫码与手机确认由本人完成；读取后显示真实昵称，确认预览后才应用。");Para(note,"当前取钥面向 4.2.0.x x64。完全空白账号缺少消息身份映射时会停止生成计划，不删除记录，也不会显示为恢复成功。");
    }

    void AccountCard(int index,Panel parent){
        var card=Card(parent);Heading(card,(index==0?"A · 目标账号 / 误删方":"B · 源账号 / 保留方"));card.Children.Add(Text(AccountName(index),22,index==0?blue:teal,true));
        Para(card,accountVerified[index]?"✓ 本次全部消息分片与本人昵称已核验":S(accounts[index],"keys").Length>0?"已关联密钥文件 · 请点击读取重新核验":"登录并读取后显示本人昵称");
        var combo=new ComboBox{Margin=new Thickness(0,8,0,8)};combo.Items.Add(new ComboBoxItem{Content="选择本次账号目录…",Tag=null});combo.SelectedIndex=0;
        foreach(var candidate in candidates){var item=new ComboBoxItem{Content=S(candidate,"label"),Tag=candidate};combo.Items.Add(item);if(S(candidate,"root")==S(accounts[index],"root"))combo.SelectedItem=item;}
        combo.SelectionChanged+=delegate{var item=combo.SelectedItem as ComboBoxItem;if(item!=null && item.Tag!=null){var selected=Obj(item.Tag);if(S(accounts[index],"root")!=S(selected,"root")){accounts[index]["root"]=S(selected,"root");accounts[index]["owner"]=S(selected,"owner");accounts[index]["keys"]="";accounts[index]["nickname"]="";accountVerified[0]=accountVerified[1]=false;sourceImport.Reset();identityConfirmed=false;InvalidatePlan();Render();}}};card.Children.Add(combo);
        if(S(accounts[index],"root").Length>0){var details=new Expander{Header="账号详情（可选）",Margin=new Thickness(0,5,0,4)};details.Content=Text("内部身份："+S(accounts[index],"owner")+"\n所选目录："+S(accounts[index],"root"),11,muted);card.Children.Add(details);}
        string slot=index==0?"A":"B";
        var launch=Button("打开微信 "+slot,delegate{Run("正在打开账号 "+slot+" 的原版微信…",D("action","launch","slot",slot,"a",accounts[0],"executable",executable),delegate(Dictionary<string,object> r){accountVerified[index]=false;if(index==0)accountVerified[1]=false;sourceImport.Reset();InvalidatePlan();SetStatus(S(r,"instruction"));Render();});},true);launch.IsEnabled=index==0||accountVerified[0];Row(card).Children.Add(launch);
        if(index==1&&!accountVerified[0])Para(card,"先完成左侧 A 的读取核验，再打开 B。");
        if(index==1 && accountVerified[0] && !accountVerified[1]) {
            Para(card,sourceImport.Reminder("B",false));
            var history=new CheckBox{Content="我已完成导入或确认电脑 B 已有完整记录，并在电脑 B 核对了与 A 的聊天",IsChecked=sourceImport.Confirmed,Margin=new Thickness(0,10,0,10)};
            history.Click+=delegate{sourceImport.Confirm(history.IsChecked==true);InvalidatePlan();Render();};card.Children.Add(history);
        }
        var actions=Row(card);actions.Children.Add(Button("导入已有密钥",delegate{var f=new OpenFileDialog{Filter="私有密钥清单|*.json"};if(f.ShowDialog(this)==true){accounts[index]["keys"]=f.FileName;accountVerified[0]=accountVerified[1]=false;sourceImport.Reset();InvalidatePlan();Render();}}));
        var read=Button("读取账号 "+slot,delegate{Run("正在核验账号 "+slot+"：已登录时取钥，已退出时验证已有密钥…",D("action","capture","slot",slot,"account",accounts[index],"identity_confirmed",identityConfirmed,"source_history_confirmed",sourceImport.Confirmed,"executable",executable),delegate(Dictionary<string,object> r){accounts[index]=Obj(Get(r,"account"));accountVerified[index]=true;if(index==0){accountVerified[1]=false;sourceImport.Reset();}InvalidatePlan();SetStatus(index==0?"A 已核验："+AccountName(0)+"。请从微信菜单正常退出，再打开 B，导入并核对手机 B 中与 A 的聊天。":"B 已核验："+AccountName(1)+"。请正常退出微信，再生成双方补齐预览。");Render();});},true);read.IsEnabled=identityConfirmed && S(accounts[index],"root").Length>0 && sourceImport.CanRead(slot,accountVerified[0]);actions.Children.Add(read);
    }

    void Login(){
        var card=Card(page);Heading(card,accountVerified[1]?"A、B 本次核验已完成":accountVerified[0]?"接下来处理 B · "+AccountName(1):"当前先处理 A · "+AccountName(0));Para(card,"A 是希望恢复记录的目标账号；B 是提供保留记录的源账号。\n1   打开 A → 本人登录 → 核对账号并点击“读取账号 A”。\n2   从微信菜单正常退出整个程序 → 打开 B → 登录 → 将手机 B 中与 A 的聊天导入电脑 B → 核对完整后读取 B。\n每次只打开一个微信。如果登录页记住的是另一账号，请本人切换到本次账号。已有密钥且微信已退出时，可直接读取验证，无需重复登录。");
        var check=new CheckBox{Content="我确认所选 A、B 是本次双方授权的两个账号",IsChecked=identityConfirmed,Margin=new Thickness(0,12,0,12)};check.Click+=delegate{identityConfirmed=check.IsChecked==true;InvalidatePlan();Render();};card.Children.Add(check);
        var buttons=Row(card);
        buttons.Children.Add(Button("刷新账号目录",delegate{Run("正在查找本机账号目录…",D("action","status"),AcceptStatus);}));
        buttons.Children.Add(Button("载入已有账号任务",ImportAccounts));
        var row=new Grid();row.ColumnDefinitions.Add(new ColumnDefinition());row.ColumnDefinitions.Add(new ColumnDefinition());page.Children.Add(row);
        for(int i=0;i<2;i++){var p=new StackPanel{Margin=new Thickness(i==0?0:9,0,i==0?9:0,0)};Grid.SetColumn(p,i);row.Children.Add(p);AccountCard(i,p);}
        var footer=Card(page);
        var next=Button("下一步：读取双方聊天 →",delegate{step=2;Render();},true);next.IsEnabled=identityConfirmed&&accountVerified[0]&&accountVerified[1]&&sourceImport.Confirmed;Row(footer).Children.Add(next);
    }

    void Reading(){
        var card=Card(page);Heading(card,"验证、备份与预览");Para(card,"1   先打开 A，登录并读取 A；正常退出后再打开 B，导入并核对手机 B 中与 A 的聊天后读取 B。");Para(card,"2   B 核验完成后，从微信菜单正常退出整个程序。已有密钥且微信已退出时可直接读取验证。");Para(card,"3   点击生成预览。软件检查退出状态、保存 DB/WAL/SHM 原件，再建立恢复候选。");
        var check=new CheckBox{Content="我已核对两个账号及双方授权",IsChecked=identityConfirmed,Margin=new Thickness(0,10,0,12)};check.Click+=delegate{identityConfirmed=check.IsChecked==true;InvalidatePlan();Render();};card.Children.Add(check);
        var blocks=new Grid();blocks.ColumnDefinitions.Add(new ColumnDefinition());blocks.ColumnDefinitions.Add(new ColumnDefinition());card.Children.Add(blocks);
        for(int i=0;i<2;i++){var p=new StackPanel{Margin=new Thickness(0,8,10,0)};Grid.SetColumn(p,i);blocks.Children.Add(p);AccountCard(i,p);}
        var generate=Button("微信已退出，生成双方补齐预览",delegate{InvalidatePlan();Run("正在备份、读取并生成预览，请不要启动微信…",D("action","plan","a",accounts[0],"b",accounts[1],"identity_confirmed",identityConfirmed,"source_history_confirmed",sourceImport.Confirmed),delegate(Dictionary<string,object> r){plan=r;simulation=null;step=3;SetStatus("候选已生成。请核对身份与数量，确认后才会应用。");Render();});},true);
        generate.IsEnabled=identityConfirmed && accountVerified[0] && accountVerified[1] && sourceImport.Confirmed;Row(card).Children.Add(generate);
        Row(card).Children.Add(Button("载入上次隔离测试报告",LoadSimulation));
    }

    string Summary(){if(plan==null)return "";var names=Obj(Get(plan,"accounts"));var dirs=Obj(Get(plan,"directions"));var s=new StringBuilder();for(int i=0;i<2;i++){string slot=i==0?"A":"B";var d=Obj(Get(dirs,slot));s.AppendLine(S(Obj(Get(names,slot)),"nickname")+"：新增 "+N(d,"added")+"，去重 "+N(d,"skipped"));}return s.ToString();}
    string StateLabel(string value){switch(value){case "ready":return "候选已验证，等待本人确认";case "applied":return "应用完成";case "partial":return "部分完成，请查看各侧回执";case "interrupted":return "可能中断，禁止重复应用";case "failed":return "未完成，请保留回执";case "undone":return "已撤回";default:return "需要核对本机回执";}}
    void Preview(){
        if(simulation!=null){var c=Card(page);Heading(c,"隔离模拟报告 · 不应用真实账号");Para(c,"这是此前实验的回执，不能作为真实账号的应用预览。");var r=Row(c);r.Children.Add(Text(N(simulation,"added")+" 条恢复       "+N(simulation,"skipped")+" 条重复去重",25,blue,true));Para(c,"原始空白目标："+(B(simulation,"empty_target_uninitialized")?"缺少消息身份映射，直接恢复被拒绝":"请查看私有报告"));Para(c,"实验副本完成初始化后验证通过。"+(B(simulation,"originals_unchanged")?"报告中的原库哈希核对一致。":"原库哈希尚未验证，请查看私有报告。")+"电脑显示与手机迁移未验收。");Row(c).Children.Add(Button("返回读取真实账号",delegate{simulation=null;step=2;Render();},true));return;}
        if(plan==null){var c=Card(page);Heading(c,"还没有可应用的恢复预览");Para(c,"先读取两个账号并生成候选，或载入已有的双向任务目录。");Row(c).Children.Add(Button("返回读取聊天",delegate{step=2;Render();},true));Row(c).Children.Add(Button("载入历史任务",LoadPlan));return;}
        var card=Card(page);Heading(card,"双方补齐数量");card.Children.Add(Text(Summary(),22,blue,true));Para(card,"A 接收来自 B 的保留记录；B 接收来自 A 的保留记录。已有消息去重，冲突停止，真实消息编号保留。");Para(card,"当前任务状态："+StateLabel(S(plan,"state")));Para(card,"任务与原件目录："+S(plan,"plan"));
        var check=new CheckBox{Content="已核对双方昵称、数量、收发方向，并同意应用本次计划",IsChecked=previewConfirmed,Margin=new Thickness(0,14,0,12)};
        var apply=Button("确认并补齐双方聊天",Apply,true);apply.IsEnabled=previewConfirmed && S(plan,"state")=="ready";
        check.Click+=delegate{previewConfirmed=check.IsChecked==true;apply.IsEnabled=previewConfirmed&&S(plan,"state")=="ready";};card.Children.Add(check);Row(card).Children.Add(apply);Row(card).Children.Add(Button("打开任务目录",delegate{OpenDirectory(S(plan,"plan"));}));
        if(S(plan,"state")!="ready")Row(card).Children.Add(Button("查看结果回执",delegate{step=4;Render();},true));
    }

    void Apply(){
        if(plan==null || !previewConfirmed || S(plan,"state")!="ready")return;
        string text=Summary()+"\n原件保留在："+S(plan,"plan")+"\n\n请保持所有微信退出。确认应用这份计划？";
        if(MessageBox.Show(this,text,"确认本次具体恢复计划",MessageBoxButton.OKCancel,MessageBoxImage.Question)!=MessageBoxResult.OK)return;
        Run("正在核对原库与候选并应用，请等待回执…",D("action","apply","plan",S(plan,"plan"),"plan_hash",S(plan,"plan_hash"),"confirmed",true),delegate(Dictionary<string,object> result){plan["state"]=S(result,"state");step=4;SetStatus(S(result,"state")=="applied"?"数据库应用完成。请本人打开原版微信验收显示。":"请查看各侧回执，不能视为双方全部成功。");Render();});
    }

    void Result(){
        var card=Card(page);Heading(card,"分阶段核对结果");
        Para(card,plan==null?"数据库：尚未载入真实任务回执":"数据库状态："+StateLabel(S(plan,"state")));Para(card,clientConfirmed?"电脑显示：本人已确认":"电脑显示：尚未由本人确认");Para(card,"手机迁移：未验收 · 不会自动同步到手机");
        Para(card,"请在原版微信核对联系人、收发方向、时间、正文或卡片，以及已有消息。消息条目恢复不代表附件完整。");
        var confirm=Button("我已在电脑微信逐项核对",delegate{if(plan==null)return;Run("正在记录本人的电脑验收…",D("action","confirm-client","plan",S(plan,"plan"),"plan_hash",S(plan,"plan_hash"),"confirmed",true),delegate{clientConfirmed=true;SetStatus("已记录本人电脑显示确认；手机迁移仍未验收。");Render();});},true);confirm.IsEnabled=plan!=null&&S(plan,"state")=="applied"&&!clientConfirmed;Row(card).Children.Add(confirm);
        if(plan!=null)Row(card).Children.Add(Button("打开原件与报告目录",delegate{OpenDirectory(S(plan,"plan"));}));
        if(plan!=null && S(plan,"state")=="applied")Row(card).Children.Add(Button("撤回本次补齐",delegate{if(MessageBox.Show(this,"仅在两个账号都没有新消息或数据库变化时允许撤回。撤回前还会保存当前原件。\n\n确认撤回这份任务？\n"+Summary(),"确认撤回",MessageBoxButton.OKCancel,MessageBoxImage.Question)!=MessageBoxResult.OK)return;Run("正在核对后续变化并撤回…",D("action","undo","plan",S(plan,"plan"),"plan_hash",S(plan,"plan_hash"),"confirmed",true),delegate(Dictionary<string,object> result){plan["state"]=S(result,"state");clientConfirmed=false;SetStatus("已撤回本次补齐，请保留撤回前原件并检查微信。");Render();});}));
    }

    void ImportAccounts(){var f=new OpenFileDialog{Filter="已验证账号任务|verified-accounts.json|JSON 文件|*.json"};if(f.ShowDialog(this)!=true)return;Run("正在核对账号取钥回执…",D("action","import-accounts","path",f.FileName),delegate(Dictionary<string,object> result){var values=A(Get(result,"accounts"));for(int i=0;i<2;i++){accounts[i]=Obj(values[i]);bool exists=false;foreach(var c in candidates)if(S(c,"root")==S(accounts[i],"root"))exists=true;if(!exists)candidates.Add(D("root",S(accounts[i],"root"),"owner",S(accounts[i],"owner"),"label",AccountName(i)));}accountVerified[0]=accountVerified[1]=false;sourceImport.Reset();identityConfirmed=false;InvalidatePlan();step=1;SetStatus(S(result,"instruction"));Render();});}
    void LoadSimulation(){var f=new OpenFileDialog{Filter="隔离模拟回执|simulation-report.json|JSON 文件|*.json"};if(f.ShowDialog(this)!=true)return;Run("正在读取隔离模拟报告…",D("action","simulation-report","path",f.FileName),delegate(Dictionary<string,object> result){simulation=result;plan=null;step=3;SetStatus("当前为隔离测试结果，不能应用到真实账号。");Render();});}
    void LoadPlan(){var dialog=new System.Windows.Forms.FolderBrowserDialog{Description="选择含 sync-plan.json 的双向任务目录"};if(dialog.ShowDialog()!=System.Windows.Forms.DialogResult.OK)return;Run("正在载入历史回执…",D("action","resume","plan",dialog.SelectedPath),delegate(Dictionary<string,object> result){plan=result;simulation=null;previewConfirmed=false;clientConfirmed=B(result,"client_confirmed");step=3;SetStatus("已载入历史任务，请重新核对预览与回执状态。");Render();});}
    void OpenDirectory(string path){if(Directory.Exists(path))Process.Start(new ProcessStartInfo(path){UseShellExecute=true});}

    async void Run(string label,Dictionary<string,object> request,Action<Dictionary<string,object>> complete){
        if(busy)return;busy=true;shell.IsEnabled=false;progress.Visibility=Visibility.Visible;SetStatus(label);
        try{var result=await Task.Run(delegate{return Backend(request);});complete(result);try{await Task.Run(delegate{return Backend(D("action","save-settings","executable",executable,"accounts",accounts));});}catch{SetStatus(message+"（账号路径设置暂未保存，任务回执保留。）",true);}}
        catch(Exception error){SetStatus(error.Message,true);if(plan!=null&&S(request,"action")=="apply"){plan["state"]="needs_receipt_review";step=4;Render();}}
        finally{busy=false;shell.IsEnabled=true;progress.Visibility=Visibility.Collapsed;}
    }

    Dictionary<string,object> Backend(Dictionary<string,object> request){
        var start=new ProcessStartInfo(S(config,"python"),"-B -m chat_rescue.windows_backend"){WorkingDirectory=S(config,"repository"),UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        start.EnvironmentVariables["PYTHONUTF8"]="1";start.EnvironmentVariables["SQLCIPHER_LIBRARY"]=S(config,"sqlcipher");
        if(S(config,"workspace").Length>0)start.EnvironmentVariables["CHAT_RESCUE_WORKSPACE"]=S(config,"workspace");
        using(var process=Process.Start(start)){
            var stderr=process.StandardError.ReadToEndAsync();process.StandardInput.Write(BackendTransport.EscapeJsonForPipe(json.Serialize(request)));process.StandardInput.Close();string output=process.StandardOutput.ReadToEnd();process.WaitForExit();stderr.Wait();
            Dictionary<string,object> response;try{response=Obj(json.DeserializeObject(output));}catch{throw new Exception("后端没有返回有效回执，请保留本机任务目录；不要重复应用。");}
            if(!B(response,"ok"))throw new Exception(S(response,"error"));return Obj(Get(response,"result"));
        }
    }

    [STAThread] public static void Main(){try{new Application().Run(new Wizard());}catch(Exception error){MessageBox.Show("Chat Rescue 无法启动："+error.Message,"启动失败");}}
}}

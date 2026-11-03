# Windows 安装包与跨电脑部署

0.3.6 提供 Windows x64 安装包和便携 ZIP。Python 3.14.10、SQLCipher 4.5.2 community DLL、后端源码及第三方许可一起打包，使用安装目录内的相对路径。使用者不需要安装 Python、配置 SQLCipher、修改 PATH 或保留开发者的源码目录。

## 新电脑使用

1. 从项目 [0.3.6 Release](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.3.6) 下载 `Chat-Rescue-0.3.6-Windows-x64-Setup.exe`，可使用同页 `SHA256SUMS.txt` 核对文件。
2. 双击安装，默认仅安装到当前用户的 `%LOCALAPPDATA%\Programs\ChatRescue`，提供开始菜单和可选桌面快捷方式。需要 Windows 10 build 19041+ / Windows 11 x64，以及系统 .NET Framework 4.8+；安装器检测 .NET，缺失时显示官方安装/修复提示并停止，不静默提权。其他 Windows 版本、ARM64 和干净虚拟机的完整兼容性尚未验收。
3. 启动 Chat Rescue，点击“自动检查并准备”。已安装的原版微信可通过系统信息发现，发现失败时由本人选择程序。微信本体、账号登录和手机确认由本人完成。
4. 先打开 A（目标账号）并读取核验，正常退出微信后再打开 B（源账号）。核对当前目录与身份，双方读取成功且微信退出后生成预览。当前仅核验微信 **4.2.0.12 的文本方向**；不因安装成功就保证其他版本或消息类型可恢复。
5. 对本次具体预览确认后应用；电脑显示与手机迁移分别验收。详细步骤见 [桌面向导](windows-desktop.md)。

运行依赖已经随包提供，安装后无需联网下载它们。下载发布文件需要网络，微信自身的登录与同步另有其网络需求。便携 ZIP 解压后运行 `ChatRescue.exe`，必须保留同目录的 `runtime`、`app`、`backend-config.json` 和许可文件，不能只复制 EXE。

当前安装包尚未进行项目代码签名，Windows 可能显示未知发布者或阻止运行；不建议关闭安全功能。普通用户可等待签名版本，开发验证者自行核对仓库、发布来源与校验文件。打包不包含任何账号、密钥、数据库或开发者机器绝对路径。

## 自动配置的边界

| 项目 | 安装包的处理 |
| --- | --- |
| 没有 Python / 没有 SQLCipher | 使用包内固定版本，安装时自动展开 |
| 已有其他 Python / SQLCipher | 仍使用包内运行时，避免修改全局环境 |
| .NET Framework 4.8+ | 使用系统运行时；缺失时在安装阶段提示修复，不把 SDK 安装到用户电脑 |
| 首次账号、密钥、历史任务 | 从当前用户私有工作目录开始；不从其他电脑或旧实验借用 |
| 原版微信与账号 | 本人安装、登录、授权和核对，不分发或修改微信本体 |
| 升级与卸载 | 程序文件与用户资料分离；卸载保留私有任务、密钥、备份和微信记录 |

用户资料默认位于 `%LOCALAPPDATA%\ChatRescue\Desktop`，其中密钥和任务使用私有 ACL。不要把该目录作为普通安装包传给别人。跨电脑分享只使用经双方授权和校验的单聊 bundle；B 导出给 A 的流程见 [Windows CLI](windows.md)，各电脑分别定位本人的库和密钥。安装包可以部署到多台电脑，不代表不同电脑间会自动同步聊天。

## 维护者构建

在 Windows x64 构建机安装 Python 3.14+ 与 Inno Setup 6.7.4。固定源码提交后，在仓库根目录执行：

```powershell
py -3.14 desktop/windows/package.py --iscc 'C:\你的InnoSetup目录\ISCC.exe' --output 'dist\windows-release-0.3.6'
```

构建器从官方 Python 和固定 NuGet 来源获取依赖并核对写在源码中的 SHA256；也接受 `--python-archive`、`--cipher-archive` 使用本地同哈希缓存。Inno Setup 从[官方页面](https://jrsoftware.org/isdl.php)取得并验证签名，仅作为构建工具，不安装到最终用户电脑。`--output` 必须是尚不存在的新目录，失败现场保留；`--allow-dirty` 仅供本地构建试验，公开发布使用已提交的干净源码。

产物为安装 EXE、便携 ZIP、`SHA256SUMS.txt`，以及包含文件清单与源码提交标识的 `portable/release-manifest.json`。仅 Git 索引内允许的源码、文档、许可进入包；忽略缓存、数据库、密钥和私人截图。包内 Python 用独立 `._pth`，不引用源码工作区或系统 PYTHONPATH。维护者运行打包后的 doctor 和空白设置检查，再验证安装、启动、移动目录及卸载后资料保留。

Python 嵌入式发行的隔离机制依据 [Python 官方文档](https://docs.python.org/3.14/using/windows.html#the-embeddable-package)。.NET Framework 的系统前置条件见 [Microsoft 文档](https://learn.microsoft.com/en-us/dotnet/framework/install/on-windows-and-server)。SQLCipher 的分发和声明要求见 [上游许可](https://www.zetetic.net/sqlcipher/license/)；包内附原文和相关组件声明。

## 当前验证

本机已完成：嵌入式运行时 doctor、空白账号设置、中文目录实际安装、安装后的 GUI 启动、卸载与用户资料保留。恢复合成集和中文管道测试另见 [测试记录](testing.md)。安装器验证使用隔离目录，没有替用户继续操作微信、取钥或修改真实聊天库。未把本机安装验证写成干净新电脑、真实 GUI 恢复、微信显示或手机迁移验收。

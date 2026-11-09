# Windows 离线实验版

0.5.6 同时提供默认关闭的 Windows 媒体本体实验开关：原语音及图片/视频/文件可独立启用，语音须补齐媒体库密钥，附件须本地文件和可验证目标证据。媒体仅有合成离线与加密应用/撤回验证，真实 Windows 结构、打开/播放与手机迁移未验收；缺证据停止。详见 [Windows 媒体实验](windows-media.md)。

0.5.6 修复原生辅助表兼容误报：只对已观察的 MessageGroupTimeInfo 可选布局、未读列表与 biz_info 的字段顺序差异按明确结构核验；会话主表与未知结构检查保留。A 的数据与物理字段顺序保持，分片分配规则沿用 0.5.5。见 [0.5.6 说明](releases/v0.5.6.md)。

0.5.5 新增 A/B 必需结构与候选索引核对；通过未改动会话的独立原生范围证据识别分片，按时间分配源消息，证据不足即停止。仅在候选中清理所选会话失效的搜索范围，预览显示每片数量与日期；真实微信显示、搜索重建与手机迁移仍需本人验收。见 [0.5.5 说明](releases/v0.5.5.md)。

0.5.4 修正当前 Windows 单聊的 packed 字段含义：字段 1 为发送者、字段 2 为接收者，均按各消息分片的 Name2Id 校验并映射到目标分片，不能当固定 3/2 方向值。4.2.0.13 回执导出使用独立 participant-ids 配置；本版 B→A 模式可复用已验证的旧 Windows 回执，逐条执行发送/接收双方身份检查，不改旧报告或伪造版本。已有的 0.5.3 失败任务保留，重新生成预览。真实证据为所提供账号对的文本元数据配对，不能外推客户端、手机或所有非文本已验收。

0.5.4 Windows 桌面版只生成 **B → A 单会话替换**：B 是唯一聊天来源，A 原会话在候选副本中移除后重建，其他会话和 A 草稿保留，B 不写入。系统类型 10000、10002 暂不导入并按类型计数；不把其他未知方向或发送者错误当作中立条目忽略。未知普通方向显示实际值、预期值和类型并停止。替换会移除 A 独有的此会话消息，必须先核对具体移除/导入数量与原件，再确认应用；不要人工删除真实聊天。CLI 原有双向模式保留，不能将其说明当作本版桌面默认行为。

0.5.4 的[Windows 安装包与跨电脑部署](windows-deployment.md)已内置运行依赖。Windows CLI 可以使用双方授权的已有密钥完成四库验证、导出、单聊 bundle 交换、视角转换、候选生成、应用和本机双向补齐。另有 [Windows 五步桌面向导](windows-desktop.md) 的本机构建，沿用 Mac 操作顺序。新增面向 **微信 4.2.x.x x64** 的只读实验取钥命令；Mac 原生桌面与取钥流程保留。本机 4.2.0.12 两个账号已通过真实取钥与四库导出；完全空白账号缺少 Name2Id 的恢复初始化尚未适配，真实 GUI 应用、客户端显示和手机迁移未验收，不能把本版称为开箱即用的 Windows 微信恢复软件。

## 输入和边界

需要 64 位 Windows、Python 3.14+、与 Python 架构一致的 SQLCipher 4 DLL，以及本人授权提供的每个账号独立密钥文件。Windows 不使用 Mac 的 LLDB、`.app` 副本或重签流程；`prepare-key-app` / `capture-keys` 在 Windows 拒绝执行。实验取钥使用单独的 `capture-windows-keys`，要求 x64 微信 4.2.x.x、有效腾讯签名和当前 Windows 用户自己的明确 PID，不启动、注入、终止进程或自动提权。

接受现有引擎兼容的全部 `message/message_N.db` 编号分片（必须含 `message_0.db`），以及 `message/message_resource.db`、`session/session.db`、`contact/contact.db` 三个辅助库，使用 SQLCipher 4 默认参数与 32 字节原始密钥。目录类似不代表结构兼容。旧版 Windows 的 `Msg/MicroMsg.db` 等结构、其他加密参数与群聊不支持；不要改名、删库或删消息来绕过检查。Windows 与 Mac 之间可传同一格式的 bundle，实际恢复仍以目标结构验证为准，不代表任意跨版本互通。

本机 **4.2.0.12 的文本消息**另有已观察的方向配置：packed 字段 2 的发送值为 3、接收值为 2，目标发送 status=2、接收 status=3。源码导出只从匹配账号目录、owner、全部消息分片及辅助库验证成功的 `.capture.json` 回执选择 `windows-4.2.0.12-structured` 配置，并写入导出报告与 bundle；不根据运行系统猜测。没有回执的旧输入保持 `legacy-v1` 的发送值 1；回执完整版本须为 4.2.x.x；其他 4.2 版本沿用 structured 配置并保留逐条校验，实机兼容性尚未验证。非 4.2 版本、格式不完整的版本及实际方向冲突仍拒绝。新结构配置对所有正 int64 类型做同一明确 packed 结构的发送者/方向转换（含图片、语音、视频、表情、位置、名片和 49 的各子类型），不再按类型白名单拒绝；缺少真实正 server_id 或方向结构的条目仍拒绝，不能伪造字段。类型值、正文、压缩内容与不透明元数据保留；非文本保留原 status/upload/download 状态，避免套用文本状态，资源索引重建；附件内容不搬运。旧 `windows-4.2.0.12-text` 配置继续限文本。全类型转换目前仅有合成证据，不能写成各类型客户端已验收。跨平台转换分别使用源与目标配置，不把 1 和 3 当作任意可互换的标记。

同一真实编号的 Windows 文本在本机两端出现过本地时间相差一秒、sort_seq 同差 1000 的情况。仅对类型 1 文本且双方均为 Windows 配置，正文、压缩内容、类型与发送者均相同，时间差恰好一秒且排序差完全对应时去重，保留各端原记录并报告 `timestamp_variants_preserved` 数量。其他差异仍按冲突拒绝；此例外不用于旧配置或跨平台去重。客户端显示与手机迁移尚未因此得到验证。

## 环境和私有工作目录

以下在仓库根目录的 PowerShell 执行。替换所有 `D:\你的…`、内部 username 和密钥文件路径；示例不能直接作为真实账号参数。

```powershell
$env:SQLCIPHER_LIBRARY = 'D:\你的依赖\sqlcipher.dll'
py -3.14 -m chat_rescue doctor
py -3.14 -m chat_rescue init-work --output "$env:LOCALAPPDATA\ChatRescue\source-job"
py -3.14 -m chat_rescue roots
py -3.14 -m chat_rescue roots --search-path 'D:\你的微信数据\xwechat_files'
```

`roots` 搜索系统 Documents 对应的 `xwechat_files`（包括重定向的 Documents），可重复传 `--search-path` 指定自定义的 `xwechat_files` 或确切 `db_storage`。不扫描整盘、不自动按时间选择账号。账号目录必须匹配本人核对的内部 username。官方 Backup 不能作为输入。

`init-work` 只创建新目录，拒绝覆盖，Windows 设置并检查 NTFS ACL。把合法取得的本账号密钥清单复制到该目录，确认它继承了私有权限。Windows 不把 `chmod 600` 当作访问控制；密钥 ACL 只接受当前用户、SYSTEM、Administrators 以及所有者为上述主体时的 OWNER RIGHTS。FAT/exFAT 或不能设置/验证 ACL 的位置会失败。

2026-11-08 SID 兼容修复（源码及本地测试包，尚未更新公开安装包）：当前用户 SID 改为通过 `OpenProcessToken` / `GetTokenInformation(TokenUser)` 读取本进程令牌，再由 `ConvertSidToStringSidW` 转为字符串，不再解析 `whoami` 文本，不依赖中文用户名或电脑名的命令输出编码。系统接口失败仍停止，并报告接口名及 Windows 错误码；不使用环境变量猜测 SID、不放宽 ACL、不自动提权。出现“无法核对当前 Windows 用户 SID”不等于缺少微信数据库或 SQLCipher。终端输出正常不能证明桌面后台收到的原始输出正常，不能仅凭中文名称认定根因。接口契约见 [Microsoft GetTokenInformation](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation)。

密钥清单为 UTF-8 JSON（接受 BOM），结构是 `{"keys":{"message/message_0.db":{"enc_key":"<64位十六进制原始密钥>"}, ...}}`，四个库分别配置。也支持 Windows 绝对路径或数据库前 16 字节 salt 的十六进制作为索引。不在终端输入、打印或向 agent 发送真实密钥。

SQLCipher DLL 及依赖需从可信来源提供，普通 SQLite DLL 不适用；`SQLCIPHER_LIBRARY` 使用绝对路径。DLL 依赖可放在同目录。运行依赖就绪仅代表 `doctor` 通过，不代表密钥、身份或微信结构已经验证。SQLCipher 配置依据 [官方 API](https://www.zetetic.net/sqlcipher/sqlcipher-api/)。Python 可从 [官方 Windows 下载页](https://www.python.org/downloads/windows/)安装。

## 微信 4.2.x.x 实验取钥

这部分仅在用户明确要求实际取钥时执行；代码维护任务不得启动真实微信或扫描真实进程。用户自己在原版微信登录本次账号，在任务管理器“详细信息”中核对主进程 PID 和程序路径；同时核对本次账号的实际 db_storage。工具不自动从多个进程或账号中挑选。

```powershell
py -3.14 -m chat_rescue inspect-windows-app --exe 'C:\你的微信安装目录\Weixin.exe'
# 以下 PID 1234、账号和目录必须替换为已核对的本次实际值：
py -3.14 -m chat_rescue capture-windows-keys --pid 1234 --exe 'C:\你的微信安装目录\Weixin.exe' --db-root 'D:\你的微信数据\xwechat_files\wxid_B_后缀\db_storage' --owner 'wxid_B' --output "$env:LOCALAPPDATA\ChatRescue\source-job\source-keys.json" --timeout 120
```

`inspect-windows-app` 读取 PE 架构、完整文件版本、SHA256 和 Authenticode 签名，不启动微信。取钥仅只读访问指定 PID，核对进程路径和 Windows 用户，定位已知 Config.Cipher 对象并验证所选账号全部消息分片及辅助库首页 HMAC；再通过 SQLCipher 检查所有这些库的完整性，全部通过才写私有密钥文件与同名 `.capture.json` 回执。超时范围为 10–300 秒，不保存部分结果，也不输出候选密钥、passphrase、内存或聊天正文。回执含程序完整版本与哈希、PID、内部 owner、目录及精确的 `verified_databases` 清单；实际库数为分片数 + 3，旧四库回执只适用于单分片账号，留在私有工作目录。

`verify-keys` 应在生成恢复计划前完成。本机观察到，只读完整性查询也会更新 SQLite 的 SHM 共享索引；若计划后再次打开原库，可能使完整 DB/WAL/SHM 哈希检查拒绝应用。保留旧任务，退出微信后重新备份并生成候选；不修改旧计划哈希，不删除 sidecar。四库验证成功不代表原库此后一直未变化。

对象布局参考 [固定上游实现](https://github.com/TANGandXUE/wcdb-key-tool/blob/79f1b5b92e12c66aa281b4a60a3c478b5f547dfa/wcdb_key_tool_windows.py)，属于 4.1+ 路线向 4.2.0 的实验移植。本机两个真实 4.2.0.12 账号已通过取钥与四库完整性验证；其他完整版本仍需独立验证，版本识别通过不能证明布局适用。未找到特征、权限不足、四库不齐或完整性失败时停止，不能改偏移猜值或绕过验证。

取钥期间微信保持运行，命令不自动退出它。完成后由本人正常退出所有微信，再运行 `verify-keys`、导出及恢复步骤；运行中数据库完整性检查可能因忙碌失败，应保留现场定位。已有输出不覆盖；有密钥而缺失回执时先检查本机文件并用 verify-keys 验证，不宣称取钥命令成功。

## B 导出给 A

由 B 在原版微信核对本机记录，然后从菜单正常退出微信，不能只关窗口。命令不会启动或终止微信。

```powershell
$work = "$env:LOCALAPPDATA\ChatRescue\source-job"
$sourceDb = 'D:\你的微信数据\xwechat_files\wxid_B_后缀\db_storage'
py -3.14 -m chat_rescue verify-keys --db-root "$sourceDb" --keys "$work\source-keys.json"
py -3.14 -m chat_rescue export-db --db-root "$sourceDb" --keys "$work\source-keys.json" --output "$work\source-plain"
py -3.14 -m chat_rescue contacts --plain-root "$work\source-plain" --query '对方准确昵称或备注'
py -3.14 -m chat_rescue contacts --plain-root "$work\source-plain" --query '本人准确昵称或内部username'
py -3.14 -m chat_rescue save-chat --plain-root "$work\source-plain" --owner 'wxid_B' --peer 'wxid_A' --output "$work\chat"
```

全部分片及辅助库完整性检查通过后，本人仍须确认 owner=B、peer=A。只把 `chat` bundle 经双方约定的私密渠道交给 A；它是明文，压缩不等于加密，不交付密钥或整库。

## A 核对预览并恢复

A 独立创建 `target-job` 私有目录，准备自己的密钥、核对 B 的联系人并退出微信，把 bundle 放在该目录的 `chat`。不得使用 B 的库或密钥代替 A。

```powershell
$work = "$env:LOCALAPPDATA\ChatRescue\target-job"
$targetDb = 'D:\你的微信数据\xwechat_files\wxid_A_后缀\db_storage'
py -3.14 -m chat_rescue verify-keys --db-root "$targetDb" --keys "$work\target-keys.json"
py -3.14 -m chat_rescue plan-restore --source "$work\chat" --target-db-root "$targetDb" --target-keys "$work\target-keys.json" --target-owner 'wxid_A' --output "$work\restore-plan"
```

生成计划不修改原库。核对 `preview.json` 的身份、新增/去重条数，以及本机 `recovered-plain` 的方向、时间、内容；它是候选，不是微信验收。用户确认本次具体计划后，保持所有微信退出，再执行：

```powershell
py -3.14 -m chat_rescue apply --plan "$work\restore-plan"
```

只有返回成功且 `applied.json` 为 `applied` 才算数据库应用完成。原件在 `restore-plan/originals`，包括同一时刻的 DB/WAL/SHM。然后由 A 打开原版微信人工验收；手机迁移另行验收，不保证附件可打开。失败、库变化、仅有 `apply-started.json` 或 `rollback_failed` 时依照故障文档保留现场，不重复应用、不自行拼接原件。

## 同机双向补齐

双方账号的本机目录和已有密钥都可用时：

```powershell
py -3.14 -m chat_rescue plan-sync --a-root 'D:\你的A目录\db_storage' --a-keys 'D:\你的A私有目录\keys.json' --a-owner 'wxid_A' --b-root 'D:\你的B目录\db_storage' --b-keys 'D:\你的B私有目录\keys.json' --b-owner 'wxid_B' --output 'D:\你的私有目录\sync-plan'
# 核对 sync-plan.json 中双方身份、directions 的 added/skipped 和各侧候选，确认后：
py -3.14 -m chat_rescue apply-sync --plan 'D:\你的私有目录\sync-plan'
```

双向应用包含两次写入，不是跨账号原子事务。看 `sync-result.json` 的整体状态与各侧回执；`partial` 不能报告双方成功。`undo-sync --plan <目录>` 仅适用于完整应用且双方数据库均未变化的任务，会保留撤回前原件；出现新消息或 sidecar 变化时拒绝。单向恢复没有 rollback 子命令。

## 开发验证

使用合成数据运行 `py -3.14 -W error::ResourceWarning -m unittest discover -s tests -v`。测试包括加密 B → bundle → A、双向补齐、去重、方向、真实编号保留、稀疏 rowid、WAL 备份、变化拒绝、失败回撤、中文路径、Windows 文件锁与 ACL。Mac 原生签名/启动测试在 Windows 跳过。

本机验证依赖：Python 3.14.0 x64；SQLCipher 4.5.2 community DLL 来自 SQLitePCLRaw.lib.e_sqlcipher 2.1.11，仅作为测试依赖，不提交或打包其二进制。真实取钥、结构观察与隔离试验见 [证据与边界](evidence-and-support.md)。电脑显示、独立手机迁移尚无验收证据。

2026-11-03 本机完整测试集共 114 项：104 项通过，10 项跳过（9 项 Mac 专用，1 项本机没有符号链接权限）。加密导出、候选生成、应用及回撤测试实际执行通过；尚未运行本次远程 CI，也未重跑 Mac 实机测试。

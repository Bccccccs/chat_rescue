# 发布版本对照

截至 2026-11-09，Mac 当前安装包为 **0.5.0**，Windows 当前安装包为 **0.6.0**。所有已发布下载均标记为 GitHub Pre-release（实验预发布）；目前没有正式稳定版。

## 各个版本是什么

| 工具版本 | 平台 | 发布状态 | 内容与用途 |
| --- | --- | --- | --- |
| [0.6.0](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.6.0) | Windows x64 | 当前 Windows 下载版 | 三步流程、历史昵称与目录选择、账号重新核验、同一操作者任务接续及媒体分类跳过；不附微信，实际媒体与换机仍需验收。 |
| [0.5.6](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.6) | Windows x64 | 历史实验版 | 修复已知辅助表布局误报；保留分片/身份保护，加入默认关闭的原语音与图片、视频、文件本体实验；真实打开未验收。 |
| [0.5.5](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.5) | Windows x64 | 历史实验版 | B→A 替换；核对必需结构、按原生证据分片分配，列出每片数量与日期；未知或歧义停止，客户端显示仍待验收。 |
| [0.5.4](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.4) | Windows x64 | 历史实验版 | B → A 单会话替换；预览 A 将移除和从 B 导入的数量，B 只读。4.2.x.x 版本准入与多分片处理保留。提供安装 EXE 和便携 ZIP；不搬运附件本体。此 Release 没有 Mac 安装包。 |
| [0.5.3](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.3) | Windows x64 | 历史实验版 | B→A 单会话替换；旧字段 2 判断未适配当前历史记录。 |
| [0.5.2](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.2) | Windows x64 | 历史实验版 | 放宽 4.2.x.x 版本准入，保留双向补齐。 |
| [0.5.1](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.1) | Windows x64 | 历史实验版 | 多分片与 structured 方向转换；版本准入仍限 4.2.0.12。 |
| [0.5.0](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.5.0) | Mac Apple Silicon | 当前 Mac 下载版 | 原语音、图片、视频、文件附件实验功能；五步向导和 CLI；实际恢复写入仍限单个 message_0.db。此 Release 没有 Windows 安装包。 |
| 0.4.0 | Mac 开发阶段 | 未单独发布 GitHub 安装包 | 原语音离线实验阶段，功能已并入 0.5.0；无需寻找 0.4.0 下载。 |
| [0.3.7](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.3.7) | Mac Apple Silicon | 历史实验版 | 顺序登录 A、B 的五步向导；基础消息恢复，不含 0.5.0 的原语音和附件恢复实验功能。 |
| [0.3.6](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.3.6) | Windows x64 | 历史实验版 | 首个带 Python / SQLCipher 运行时的安装包和便携包；旧的单消息分片流程，不含 0.5.4 的多分片及 B 导入确认更新。 |
| [0.3.5](https://github.com/Bccccccs/chat_rescue/releases/tag/v0.3.5) | Mac Apple Silicon | 历史实验版 | 首个原生五步桌面向导，早期基础消息恢复。 |

历史版本为复现与回退保留，不能按较新的源码说明推断旧安装包的功能。

## 下载哪个文件

| 使用环境 | 文件 |
| --- | --- |
| Mac，Apple 芯片 M 系列 | [Chat-Rescue-0.5.0-macOS-arm64.zip](https://github.com/Bccccccs/chat_rescue/releases/download/v0.5.0/Chat-Rescue-0.5.0-macOS-arm64.zip) |
| Windows x64，安装使用 | [Chat-Rescue-0.6.0-Windows-x64-Setup.exe](https://github.com/Bccccccs/chat_rescue/releases/download/v0.6.0/Chat-Rescue-0.6.0-Windows-x64-Setup.exe) |
| Windows x64，解压使用 | [Chat-Rescue-0.6.0-Windows-x64-Portable.zip](https://github.com/Bccccccs/chat_rescue/releases/download/v0.6.0/Chat-Rescue-0.6.0-Windows-x64-Portable.zip) |

`SHA256SUMS.txt` 是所在 Release 安装包的校验文件，不能混用其他版本的校验值。`SOURCE_COMMIT.txt`（Mac 0.5.0）记录源码提交；GitHub 自动生成的 `Source code` 是源码快照，不是可直接打开的桌面安装包。Windows 安装包与便携包是同一版本的两种分发形式。

## 工具版本、源码版本与微信版本

- 0.5.0 / 0.5.4 是 Chat Rescue 的工具版本；4.1.13 / 4.2.0.12 是微信版本。
- Mac 0.5.0 适配微信 4.1.13；Windows 实验取钥面向 4.2.x.x x64，现有实机依据为 4.2.0.12，其他完整版本不能据此视为已验证。
- main 当前源码版本为 0.6.0，合并了 Mac 媒体实验代码与 Windows 更新。源码版本不等于两个平台都已发布同号安装包；Mac 官方下载仍是 0.5.0。
- 0.5.4 的 Windows 非文本明确结构转换有合成验证，不等于语音可播放或图片、视频、文件附件可打开；Windows 不处理 Mac 媒体 v2/v3 bundle。
- Mac 媒体查看/播放/打开、Windows 真实 GUI 恢复和各类型显示、手机迁移仍须独立验收。所有版本均不自动同步到微信服务器或手机。

[Mac 安装说明](../macos-deployment.md) · [Windows 安装说明](../windows-deployment.md) · [逐版更新记录](../../CHANGELOG.md)

## 维护记录

2026-11-04：按用户要求统一 README、平台安装说明、媒体说明与 GitHub Release 标题/版本导航。核对各 Release 实际附件与标签；仅整理说明，不重打包、不改变现有标签和安装文件。多个 agent 接续发布时应同时更新本表、首页平台入口及受影响 Release 的当前下载导航，区分源码、安装包与实机验收状态。

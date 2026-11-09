# Windows 固定客户端与媒体扩展实施计划

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 支持固定完整微信载荷的分发与验证，修复读取流程，扩展明确的音视频容器和既有多库链路。

**Architecture:** 独立客户端清单模块校验受信固定载荷，桌面绑定该载荷路径。独立容器识别模块供语音和附件共用；Windows 新能力拒绝旧清单，Mac 范围保持。

**Tech Stack:** Python 3.14、Windows Win32 / WPF、SQLCipher、unittest。

**Spec:** [已确认设计](../2026-11-09-windows-client-and-media-design.md)。用户已于本次会话确认按此实施。

## Global Constraints

- 不启动微信，不实际取钥或读写真实库；只用合成文件。
- 完整微信程序必须固定版本、签名和逐文件哈希；不声称复制程序即隔离数据。
- 多库依然逐片验证与精确关联；缺证据不猜测，不建片。
- 不提交、发布或重打 SID 专包。保留所有已有未提交改动。

## Review Focus

- 固定客户端清单路径越界、链接、额外文件、篡改与版本漂移必须拒绝。
- 已有设置不能静默覆盖包内固定微信路径。
- 音视频扩展不能把缩略图、截断数据或未知格式当主要内容。
- 新旧能力不混用，Mac 边界保持。
- 生成候选、客户端打开和手机迁移分别记录。

## Task 1：读取与文档

- [x] WAL/SHM 进程匹配 RED→GREEN。
- [x] 实际 WPF 选 B 不清除 A，选 A 清除双方，RED→GREEN。
- [x] README 与 Windows 零基础教程。
- [x] 现有 SID 6 项及基线完整 306 项验证通过（45 跳过）。

## Task 2：容器扩展

Files: 新增 `chat_rescue/media_containers.py`、`tests/test_media_containers.py`；修改 `voice.py`、`attachments.py`、`media.py`。

- [x] 标准 SILK、PCM WAV、AMR-NB、MOV/AVI、MKV/WebM、TIFF/JPEG XL 测试 RED→GREEN。
- [x] 按内容识别、长度/块校验与 Windows 能力协商，legacy WAV伪装拒绝；Mac 旧语音/分片边界保持。
- [x] WAV语音与MOV视频经过合成加密候选；既有多媒体库应用、撤回、失败回撤回归继续运行。
- [x] 用户追加格式跳过要求：未知图片/视频与未适配BMP跳过本体、保留消息、计数，源哈希仍绑定；已识别损坏不吞。

## Task 3：完整固定客户端

Files: 新增 `chat_rescue/windows_client.py`、`tests/test_windows_client.py`；修改 `windows_backend.py`、`desktop/windows/ChatRescue.cs`、`desktop/windows/package.py`。

- [x] 固定载荷清单：路径/链接/额外文件/哈希/版本不匹配拒绝。
- [x] 打包接受已审核完整载荷与清单，相对配置，不用账号目录作输入；脚本隔离导入路径RED→GREEN。
- [x] prepare/launch/capture 绑定包内清单，旧设置不得改用其他 EXE。
- [x] 用户要求尝试免安装副本：官方安装器只解压，两层解包后31文件943501283字节，主程序/核心DLL4.2.0.13签名Valid。
- [x] 本地便携包编译、后台检查、完整回归328项（283通过/45跳过）与ZIP CRC/全部文件哈希校验；微信启动/登录/导入与播放未验收。

## 执行记录

后续本人确认：缺少媒体条件也跳过本体继续；取消账号占用PID对应，不做互换功能，来源和目标自行选择；最后选择先交付普通模式，不创建Windows用户/沙盒。已实现非零/空语音库、源/目标媒体缺失原因计数、DAT原因拆分与V1证据一致性、图片后缀链、任意顺序核验、新会话空白选择与历史不被启动刷新覆盖。完整340项（295通过/45跳过），当前WPF4项补充通过。系统级微信缓存隔离不在本次已完成范围，文档与包标记保持未验收。

Ruling: 在现有目录新建 `codex/windows-client-media-next` 分支继续维护 — 工作区已有跨模块未提交依赖，迁移到另一 worktree 会丢失这些前提 — 全程保留原修改，不覆盖和提交。

审查：独立审查发现4项（打包导入路径、未适配BMP不能跳过、legacy语音能力漏限、未知视频伴随文件漏检），均已修复并复核无新阻断；专项16项通过。未访问真实账号资料。

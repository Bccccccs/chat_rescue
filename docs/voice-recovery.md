# 原语音恢复实验模式（本地开发版 0.4.0）

已随 Mac 0.5.0 媒体实验版发布，旧版下载包没有此功能。本轮已实现离线链路和桌面入口；真实微信播放、时长、重启后播放及手机迁移仍待本人分项验收，不能据此宣称全面语音恢复成功。

## 范围

沿用 Mac Apple Silicon / 微信 4.1.13、双方授权单聊、单个 message_0.db；另需双方已有且结构兼容的 message/media_0.db 和各自密钥。Windows 语音、其他消息或媒体分片、群聊、图片、视频和文件附件仍不支持。关闭语音开关时保留原来的类型限制，不静默跳过语音。

首轮仅接受精确的 VoiceInfo / Name2Id 列结构及已观察唯一索引；每条音频须按本媒体库的会话 username 和真实 server_id 唯一匹配，时间和 local_id 与消息一致，data_index 为文本 0，音频有腾讯 SILK v3 头。消息 status=3、upload_status=0、download_status 属于 0/1/4/5，方向必须符合现有严格适配。其他状态或字段结构停止。文件头检查不等于解码或播放验证。

音频不转写、不转码。真实发送者 username、真实 server_id、时间、正文及语音状态原样保留；只转换已适配的收发方向、本地发送者映射、目标 local_id 和媒体/资源会话关联。两个库中数值相同的 ID 不代表同一身份。

## 桌面使用

1. 按原五步向导准备、依次登录 A/B 并核对双方身份及 B 的旧记录。
2. 在“读取双方聊天”页勾选“同时恢复原语音（实验功能）”，再开始读取。普通模式仍按四库完成；语音模式必须完成五库密钥和完整性验证。已有密钥覆盖媒体库时复用，缺少时进入新的取钥任务，不覆盖旧密钥文件。
3. 核对双方昵称、消息数量和各侧语音新增/已有消息补音频/音频已完整数量。有缺失、冲突或未知结构时停止并给出错误原因，不生成可应用的完整双向计划。成功预览的 voice_blocked 为 0。
4. 确认具体预览后应用；先备份，再协调替换本次写入集合。纯音频修复不重复插入消息，也不覆盖原有正文、播放/下载状态、较新会话摘要或草稿。
5. 在原版微信分别核对自己发出和对方发来的语音：头像、方向、时间、时长、完整播放；重启后再次播放。需要手机时由本人使用微信官方迁移入口，手机上再次分项验收。电脑结果不能代替手机结果。

## CLI

沿用 [SOP](recovery-sop.md) 已核对的角色、路径和本人确认。工作目录使用独立新路径，位于仓库和真实微信数据目录外。以下变量均为本人已核对的本机值，不是默认账号。

在原 capture-keys 命令末尾加 `--include-voice`，完成条件会从四库变为五库；已有合法媒体密钥时无需重新取钥。之后：

```sh
python3.14 -m chat_rescue verify-keys \
  --db-root "$SOURCE_DB" --keys "$WORK/source-keys.json" --include-voice
python3.14 -m chat_rescue export-db \
  --db-root "$SOURCE_DB" --keys "$WORK/source-keys.json" \
  --output "$WORK/source-plain" --include-voice
python3.14 -m chat_rescue save-chat \
  --plain-root "$WORK/source-plain" --owner "$OWNER_B" --peer "$OWNER_A" \
  --output "$WORK/chat" --include-voice
```

只私密传递单聊 chat bundle，不传整库或密钥。A 使用自己的五库密钥，退出微信后：

```sh
python3.14 -m chat_rescue plan-restore \
  --source "$WORK/chat" --target-db-root "$TARGET_DB" \
  --target-keys "$WORK/target-keys.json" --target-owner "$OWNER_A" \
  --output "$WORK/restore-plan" --include-voice
```

核对具体候选并获本人确认后，沿用 `apply --plan`。双向 `plan-sync` 在原参数末尾加 `--include-voice`，后续 `apply-sync`、`undo-sync` 从计划读取能力，无需另加开关。单向没有自动 undo 命令；按故障文档保留协调原件处理，不能只回填消息库。

语音模式可以读取 v1 基础类型 bundle，但不能把语音塞进 v1。v2 必须显式启用语音能力，旧版本拒绝读取。仅修复音频时 write_set 可以只有 media_0.db；所有五库及 sidecar 仍参与一致性检查。

## 缺失、去重与冲突

- 目标缺消息且源音频完整：新增消息和原音频，映射目标本地索引。
- 消息一致且目标音频完整：原样保留；同一真实消息双方不同非空音频则停止。
- 消息一致但目标音频为空/缺行：只补音频和所需资源，不重复插入消息。
- 单向公开导出遇到源缺音频：停止，不导出完整成功清单。
- 双向模式允许私人中间 bundle 记录缺音频，以另一侧的不可变原件检查/补齐；双方都缺失则停止，不以新写入另一侧的库充当来源。
- 目标无媒体库、关联不一致、多行、非默认 data_index、未适配状态或非 SILK 头：停止，保留现场；不能删消息、改编号或拿另一账号整库覆盖来绕过。

每条音频最多 64 MiB，单 bundle 音频总量最多 512 MiB；超出停止。清单绑定相对文件路径、大小和 SHA-256，拒绝越界、重复路径、符号链接、篡改和消息清单不匹配。导出的 voice/ 音频与聊天 JSON 一样属于明文私密资料，文件名不代表去身份化。

## 原件、回执与中断

v2 单向计划固定 read_set 为四个基础库加 media_0.db；write_set 只能来自消息、资源、会话和媒体四个已适配路径。原件包括五库及每个 DB/WAL/SHM 的存在状态和哈希。候选按目标账号对应密钥和原盐重新加密，保留隐式 rowid。不能直接把 B 的加密媒体库放进 A。

应用前检查全部依赖、候选及媒体分片清单；先保存全部原件，再写入。apply-journal.jsonl 持久记录每个库的 replace_intent / replace_done，不宣称多文件整体原子替换。正常异常会尝试协调回撤，rollback_failed 明确列出未恢复路径；只有工具报告 rolled_back 才能称回撤完成。

只有 apply-started.json、缺少 applied.json 或明确回撤结果时，按可能中断处理：保持微信退出，保留五库原件、日志、候选及现场，不能自动重跑 apply。双向未全部完成不能自动 undo；完整应用后媒体库或任何依赖产生新变化时拒绝用旧快照覆盖。人工处理必须使用同一时刻的整套相关 DB/WAL/SHM，不能只恢复 media_0.db 或消息库。

实现依据与合成验证见[实现记录](voice-recovery-progress.md)；原始[设计稿](voice-recovery-design.md)记录预期与待实机确认项。

# Windows 媒体本体恢复实验模式（0.6.0）

0.6.0 已纳入下述2026-11-07/09扩展；“下一版/未发布”标题保留开发记录，旧0.5.6下载不包含这些新增能力。最终范围见[0.6.0发布说明](releases/v0.6.0.md)。

## 2026-11-09 本人追加：未适配附件状态跳过本体

Windows 图片/视频/文件的消息status或upload_status未在附件本体规则中适配时，不再中断整段恢复：保留消息条目和原始状态，跳过本条媒体本体，继续其他支持内容。报告增加 `attachment_state_skipped`、各类型的 `*_source_state_unsupported` / `*_target_state_unsupported`，以及仅含侧别、类型、状态和计数的 `attachment_state_skip_details`；桌面预览显示数量及状态组合，不把跳过记成恢复成功。

来源跳过项必须有精确原因 `source-state-unsupported`、空文件列表和新v5能力 `windows-skip-unsupported-attachment-state-v1`，状态/原因不符或伪造载荷拒绝。旧媒体预览需重新生成。目标状态分支适用于仍存在对应消息的增量路径；桌面整段替换先清理候选旧会话，再按来源生成，不能据此宣称恢复了A原有媒体。

本规则仅扩展附件消息状态；未知语音库表/字段结构、身份/方向/资源关联冲突、库HMAC/完整性、路径、哈希和非空内容冲突仍停止。Mac/legacy规则保持。

2026-11-09 实际目录截图定位：Windows 规范 `media_N.db-first.material`、`-incremental.material`、`-last.material` 是 WCDB 元数据备份，旧代码误报未知 sidecar；现按明确名称保留，不送入媒体库解密，也不进入 DB/WAL/SHM 写入集合。真正的未知/孤立 sidecar 仍拒绝，报具体文件名，不要求删除。依据 [Tencent WCDB 官方说明](https://github.com/Tencent/wcdb/wiki/C---%E6%8D%9F%E5%9D%8F%E3%80%81%E5%A4%87%E4%BB%BD%E3%80%81%E4%BF%AE%E5%A4%8D)。

本模式为 B→A 单会话替换增加图片、原语音、视频和文件本体候选。两个媒体开关默认关闭，B 是唯一来源且不应用写入。0.5.6 安装包与便携包包含本模式，能力与验收边界见[版本说明](releases/v0.5.6.md)。

**目前只有合成资料的离线、加密应用和撤回测试，没有真实 Windows 微信打开、语音播放或手机迁移验收。** 能生成候选不表示所有 Windows 微信媒体格式都已适配。遇到下面未适配条件会停止，不用空壳消息或错误密文冒充完整恢复。

## 2026-11-09 截图阻断修复：缺少媒体条件也跳过

本人已进一步确认：缺少媒体条件时，保留消息条目、跳过对应本体，继续其余内容。此规则纳入 Windows 0.6.0，不改变 Mac/legacy 或已发布 0.5.6 包。

- 语音核验全部实际存在的规范 `media_N.db`，允许只有非零编号库；没有任何媒体库时仍核验消息及辅助库，不创建空媒体库。现有媒体库一个也不能漏掉密钥或完整性验证。
- 源库/音频缺失或未适配音频容器计 `voice_source_unavailable`；目标无已有媒体库或缺少唯一原有关联计 `voice_target_unavailable`。消息仍导入，未能恢复的音频本体明确未恢复。重复、身份/时间冲突和未知数据库结构继续拒绝，不按消息片编号或时间猜媒体片。
- 图片 DAT 不再把缺图片密钥和未知格式合为一条错误。源图无可验证解密参数计 `image_key_unavailable`，缺本地文件或资源计 `image_source_unavailable`；A 无可验证重加密证据计 `image_target_unavailable`。已定位的 B 密文仍绑定哈希；不复制 B 密文冒充 A 可读图片，目标也不留下单条消息的部分图片候选。
- 视频/文件缺本地内容分别计 `video_source_unavailable`、`file_source_unavailable`；缺格式支持沿用格式计数。跳过原因受 v5 固定白名单验证，旧版本不能读取。
- 新能力为 `windows-voice-nonzero-inventory-v1` 和 `windows-skip-unavailable-media-v1`，旧媒体预览须保留并重新生成。库完整性、身份、非空内容冲突、未知结构、路径安全与哈希变化不被吞成媒体跳过。
- DAT 的 AES 区段与 XOR 区段均为空时，不无故要求图片密钥。V1 的 TIFF/JPEG XL 目标样本仍不作为可验证 XOR 证据。图片格式扩展可到达大小写及 BMP/AVIF/TIFF/JPEG XL 等原始后缀，不再被旧文件名白名单提前拦截；目录、令牌、资源大小与内容检查保留。

**图片恢复边界仍需如实说明：数据库密钥不是图片 AES 密钥。Windows DAT V2 独立图片密钥的自动获取尚无本项目实机适配与验收；没有可验证参数时，本轮解决的是跳过该图并继续其余恢复，不将其记作图片恢复成功。**

## 2026-11-09 下一版源码：常见容器与格式跳过

用户明确要求不支持的图片/视频格式跳过媒体本体，保留消息条目，不因此放弃其余支持的内容。Windows 0.6.0 增加：

- 语音：Windows v5 新能力下识别腾讯/标准 SILK v3、明确 PCM WAV、逐帧校验的 AMR-NB，原字节保留，不转码。Mac/legacy 仍只接受原腾讯 SILK 契约；不能把 WAV/AMR 装进旧 v2/v3 SILK 包。
- 视频：MP4、MOV、M4V、AVI、MKV、WebM、3GP、3G2 的明确容器布局，检查块边界、品牌/DocType、主要内容及扩展名一致性。没有实现全部视频编解码器，容器识别不代表微信可播放；其他格式按下项跳过。
- 图片：既有 JPEG、PNG、GIF、WebP、明确 24/32 位 BI_RGB BMP、HEIF/AVIF、WXGF，补充 TIFF 的 IFD/strip/tile 范围与盒装 JPEG XL 的完整码流块检查。不同编码布局或品牌未适配时跳过，不宣称任意同名格式均能解码。
- 格式跳过：仅 Windows 媒体包可记 `skip_reason=unsupported-format`，该媒体本体无候选，对应消息仍导入；预览分别显示 `image_unsupported`、`video_unsupported`，并纳入 `attachments_skipped`。被跳过的 B 源文件也绑定哈希，应用前变化仍拦截。已知格式损坏、文件未下载、身份或资源冲突、图片密钥证据缺失不冒充“格式不支持”跳过。
- Windows v5 新增 `windows-media-containers-v1`；附件再加 `windows-skip-unsupported-media-v1`。旧客户端拒绝新能力，当前源码拒绝旧媒体能力清单，须保留旧任务并重新生成预览。

多消息片与多媒体片规则见下面补充，均不按编号猜测或自动建库。此段取代下文旧版“未知格式一律停止”和“语音只接受 SILK”的源码说明；已发布 0.5.6 包行为不变。

容器检查依据：[AMR 存储格式 RFC 4867](https://www.rfc-editor.org/rfc/rfc4867)、[RIFF/WAV](https://learn.microsoft.com/en-us/windows/win32/xaudio2/resource-interchange-file-format--riff-) / [AVI](https://learn.microsoft.com/en-us/windows/win32/directshow/avi-riff-file-reference)、[Matroska 元素](https://www.matroska.org/technical/elements.html)、[EBML RFC 8794](https://www.rfc-editor.org/rfc/rfc8794)、[TIFF 6.0](https://www.itu.int/itudoc/itu-t/com16/tiff-fx/docs/tiff6.pdf) 和 [JPEG XL](https://jpeg.org/jpegxl/index.html)。这些是格式依据，均不作为微信播放验收。

## 2026-11-07 源码补充（未发布）

以下新增行为仅在当前源码中，已下载的 0.5.6 包不会自动获得：

- Windows 枚举全部已有规范编号 `message/media_N.db`（必须有第零片，允许编号不连续），逐片核验密钥、完整性、结构和独立 Name2Id，保存全部 DB/WAL/SHM。跨片重复语音关联停止，不以消息分片编号推定媒体编号。
- 多目标媒体库仅按 A 原有 VoiceInfo 的会话、真实消息编号和时间唯一关联定位：在清理候选前保存关联，导入时重建新的本地编号。缺失、跨片重复或时间冲突即停止，不按文件编号或其他语音样本的最早/最晚时间猜测，不自动新建媒体片。没有已验证的原生媒体范围契约，不能类推消息 FtsRange。候选中清理所选会话语音，其他会话保留。若目标只有一个媒体库，源可来自多个媒体片；若目标多个媒体库且原有关联已丢失，本轮仍无法自动分配。
- 新语音清单仍用 v5，但额外要求 `windows-voice-shards-v1`；旧客户端拒绝新能力，当前源码拒绝缺少此能力的旧 Windows 语音预览。保留旧任务，重新生成具体预览，不能手改清单。
- 图片新增 Windows BITMAPINFOHEADER / BI_RGB 的 24/32 位 BMP 明确布局与 HEIF/AVIF 兼容品牌识别，检查长度/偏移/容器边界；旧 XOR 可识别这些容器及 WXGF。保持原图载荷，不转码。DAT V1 的 BMP、WXGF、HEIF 尾部缺少足以区分 XOR 的校验时仍拒绝。
- 旧 XOR 图片重新加密必须有 A 的可验证旧 XOR 样本，使用 A 的参数并把样本哈希纳入依赖；不能沿用 B 的参数。DAT V2 密钥元数据和目标样本要求不变。

Mac/legacy 的单消息片、单媒体库恢复边界保持。Windows 图片原本已随多消息片的 B→A 替换处理，图片不存放在 VoiceInfo 中。本轮新增的是多媒体库语音链路和图片容器兼容，不代表任意 Windows 媒体布局已适配。

图片容器字段参考 [Microsoft 位图布局](https://learn.microsoft.com/en-us/windows/win32/gdi/bitmap-storage)与 [AVIF 格式规范](https://aomediacodec.github.io/av1-avif/)。这些规范只用于容器识别，不证明微信的真实存储格式或客户端解码能力。

## 桌面操作

1. 在登录或读取页显式勾选“同时恢复原语音”及/或“同时恢复图片、视频和文件本体”。改变语音开关后，需要按 A、B 顺序重新读取账号，核验媒体库密钥。设置只保存账号路径，不自动记住媒体应用授权。
2. 按五步流程先 A 核验并正常退出，再 B。B 需从手机导入所需单聊，或核对电脑已有记录及本地下载内容完整；导入消息不保证图片、视频、文件均已下载。
3. 语音模式需双方全部消息分片、三个辅助库及媒体库的密钥和完整性验证：已发布 0.5.6 包接受单个 `message/media_0.db`，当前未发布源码核验全部已有规范编号 `message/media_N.db`。四库回执不能提前完成。没有媒体库密钥时，应在本人核对登录账号、原版微信程序及进程后重新读取，不能修改旧回执或伪造覆盖条数。
4. 正常退出微信后生成具体 B→A 替换预览。核对 A 移除、B 导入、暂不导入条数、每片日期范围、各类媒体候选及原件目录。生成阶段只修改私有候选，不清理真实 A 会话或文件。
5. 本人确认具体计划后才应用。A 原数据库与 DB/WAL/SHM 在 `A-plan/originals/`，已有附件依赖在 `A-plan/original-assets/`；新文件的原状态记为不存在。B 原始快照在 `B-plain/_encrypted_snapshot/`。没有对应候选的目录可以不存在。
6. 数据库和文件应用完成后，请本人在 A 原版微信逐项核对方向、日期和内容，打开图片、播放语音/视频、打开文件；异常时保留任务和原件。电脑显示、媒体打开/播放、手机迁移是独立验收，不能把其中一项当作其余项通过。

## 当前接受的布局与停止条件

- 方向使用账号绑定的 Windows 配置，每条消息保留真实正 `server_id`、时间、类型、正文与非文本状态；参与者按所属消息分片独立映射。系统类型 10000/10002 仍暂不导入并计数，其他未知身份或方向拒绝。
- 现有多消息分片沿用 0.5.5 的原生 `FtsRange` 和至少两个独立未改动会话证据，不能按文件编号猜测。已发布 0.5.6 包仅接受一个已有 `media_0.db`；当前源码的多媒体片证据规则见上方未发布补充，均不自动新建库。
- 语音仅接受明确的 `Name2Id`/`VoiceInfo` 列、默认值与两种索引布局；A/B 定义需一致，未知表、约束、索引、外键或触发器拒绝。按媒体库自己的会话身份、真实消息编号、时间及源本地编号核对，目标关联使用恢复后的本地编号。只接受 `data_index='0'`、已适配状态及 SILK v3 头，不转码。此布局复用了 Mac 的保守离线契约，**尚无 Windows 实机结构和播放证明**。
- 图片、视频只从唯一匹配的资源索引、消息身份、时间、本地编号、资源令牌和大小定位；文件还须匹配 XML 的名称、大小和 MD5。仅缩略图、封面、缺少主要内容、未下载、大小匹配多义、状态/路径未知或目标不同非空内容会停止。
- 文件路径白名单为该账号 `msg/attach/会话哈希/月份/Img/`、`msg/video/月份/`、`msg/file/月份/`。Windows 真实路径/资源布局尚待样本核验，其他布局不能猜测搬运。拒绝路径穿越、符号链接、媒体路径 junction、Windows 保留文件名、尾随点/空格及大小写路径冲突。
- 图片保留原始图像载荷，支持既有容器校验、旧 XOR 和 DAT V1/V2 保守处理。DAT V2 只有兼容的本地密钥元数据及 A 可验证的现有图片样本齐备时才重加密；V1/XOR 同样依赖可核对的目标证据。没有目标证据就停止，不能复制 B 的账号密文当作 A 可打开的图片。Windows 使用系统 CNG AES，不读取微信进程取图片密钥；数据库密钥不等于图片密钥。
- 单附件上限 256 MiB、单 bundle 附件总量 2 GiB，语音单条 64 MiB、总量 512 MiB。月份规则暂按 Asia/Shanghai；保留源载荷，不自动向服务器下载缺失内容，不保证源端未下载的高清原图。
- A 的其他会话、草稿和其他会话语音保留。选中会话的旧语音行仅在候选媒体库内清理，外部附件不批量删除，避免误删共享文件。未启用的媒体类型仍只有消息条目能力。

## 清单、命令与回执

Windows 媒体导出、bundle、A 应用计划及协调计划使用 v5；能力为 `windows-media-layout-v1`，按开关加 `windows-voice-silk-layout-v1`、`windows-attachments-layout-v1`。协调清单另需单会话替换和已验证分片能力。0.5.5 及更早客户端应拒绝，不能修改版本号绕过。Mac v2/v3 媒体包不作为 Windows 媒体包使用。未开启媒体时保持 v4 协调计划与既有行为。

CLI 在本人已经核对身份、路径、授权密钥及配套回执后使用；所有参数为本机已核对的绝对路径/身份，不把密钥值放到命令行。语音验证和导出支持 `--include-voice`，附件导出支持 `--include-attachments`。Windows 媒体协调恢复使用新增 `plan-replace`，不通过旧双向 `plan-sync` 回写 B：

```powershell
python -m chat_rescue verify-keys --db-root "$A_DB" --keys "$A_KEYS" --include-voice
python -m chat_rescue verify-keys --db-root "$B_DB" --keys "$B_KEYS" --include-voice
python -m chat_rescue plan-replace --a-root "$A_DB" --a-keys "$A_KEYS" --a-owner "$A_OWNER" --b-root "$B_DB" --b-keys "$B_KEYS" --b-owner "$B_OWNER" --output "$PLAN" --include-voice --include-attachments
# 本人已核对并确认这份具体预览、全部微信退出后：
python -m chat_rescue apply-sync --plan "$PLAN"
```

两开关可独立使用；不选语音时不要求媒体库。已有四库密钥不能保证语音模式可用。跨电脑 CLI 的 `export-db`/`save-chat` 支持 v5 媒体 bundle；直接 `plan-restore` 仅允许目标单消息分片，多分片须用本机可验证的 B→A 协调替换，不走默认末片分配。

预览新增 `media_experimental=true`、`client_media_verified=false`、`phone_verified=false`、各类 `*_added`/`*_repaired`/跳过条数及附件候选/依赖哈希；多分片聚合统计保留。实际文件完整性并不自动把客户端验收字段改为成功。

应用前检查 A/B 数据库清单及 DB/WAL/SHM、B 附件原件、A 附件依赖和全部候选哈希。数据库与附件一起备份、暂存、记录逐文件意图并替换；普通写入失败尝试协调回撤，不能宣称跨文件整体原子。成功必须以 `sync-result.json` 和 `A-plan/applied.json` 为准。失败查看 `A-plan/rollback.json`；`rollback_failed` 不是回撤成功。

只有开始日志而无明确完成/回撤回执时，保持微信退出，保留现场，不重跑应用，不拼凑单库或单文件恢复。`undo-sync` 只允许本次完整应用且 A/B 数据库与附件均未变化时撤回；有新消息、媒体变化或已完成手机迁移时不得擅自覆盖。完整失败规则见[故障处理](troubleshooting.md)。

## 开发验证记录

2026-11-05：新增 Windows 合成加密媒体专项，覆盖四类媒体混合候选、媒体独立 Name2Id、其他会话/草稿与 B 保留、多消息分片不同参与者编号、跨账号 DAT V2 目标证据重加密、标准 AES 向量、媒体取钥完成条件、缺失/未知/冲突拒绝、源文件/媒体 WAL 变动拦截、第二附件替换失败的数据库/文件协调回撤、应用后文件变化拒绝撤回、能力协商及桌面参数转发。既有语音与附件保守测试继续执行。测试只有随机密钥、虚构身份与合成媒体载荷，绝不作为实际应用计划或微信可打开的证据。

本轮不启动微信、不取真实密钥、不修改真实聊天库。联合回归、最终 0.5.6 安装包/便携包及校验文件以发布记录为准。

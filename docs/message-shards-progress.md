# Mac 全消息分片检查

2026-11-04。用户要求先修改 Mac，检查所有消息分片。本任务修改 `shards.py`、Mac 取钥/验证/导出、桌面会话统计及相关测试。保留已有语音、附件开发改动，不接管附件实现。

## 范围和接口

- `message_shards(root)` 数字排序枚举 `message_N.db`，要求存在 0 号库，允许编号间隔，拒绝符号链接、非规范编号及孤立 WAL/SHM。资源库、全文索引和业务消息库不当作普通消息分片。
- `inspection_files(root, include_voice=False)` 在 Mac 返回所有消息分片及公共依赖库；Windows 保持原支持边界。
- Mac 取钥每个候选账号使用独立完成清单；所有分片均需密钥和完整性验证，验证前后清单必须一致。回执增加 `verified_databases`（账号根目录到必要库清单）。
- 导出与原件快照包含全部分片及 WAL/SHM；报告保留逐文件哈希，检查期间新增/减少分片会拒绝。桌面会话按联系人跨库累加原始条目数，不将其声明为去重消息数。
- `inspect` 返回 `message_shards`、`recovery_supported`。读取双方后，多分片任务明确停止自动生成恢复计划。恢复写入和 bundle 仍保留单分片限制，不静默只恢复 0 号库。

## 验证

只操作合成库，不启动微信、不取真实密钥、不写真实聊天库。

`python3.14 -m unittest discover -s tests -q`：157 项，144 通过、13 项平台限制跳过；新增 9 项全部通过。覆盖数字排序、非连续编号、辅助库排除、符号链接/孤立 sidecar 拒绝、真实 SQLCipher 多分片验证与快照导出、跨库会话统计、额外密钥缺失、取钥逐账号完成清单、回执后新增分片、导出中新增分片拒绝，以及 Windows 原限制保留。缺少新增分片密钥时，桌面请求重新取钥，不复用不完整清单。

`xcrun swiftc -typecheck desktop/LoginFlow.swift desktop/ChatRescue.swift` 与 `git diff --check` 通过。未重新打包发布，也未使用真实多分片账号做客户端验收。已有其他开发改动保持在原工作区，未提交或推送。

附件实现方协调：已读取并保留本任务的 inspection_files/message_shards、回执与桌面跨分片检查。附件实现正在同一工作区为导出/计划/桌面接入 include_attachments 与v3计划；请避免整文件覆盖附件改动，以 docs/attachment-recovery-progress.md 记录接口为准。附件恢复写入仍限单分片，本轮不提交或推送。

最新协调：用户已在附件任务明确要求“做完推送”。附件实现方将在最终回归后统一提交本次关联开发改动（含已完成的分片只读检查），中文提交信息；不包含私有资料和构建产物。

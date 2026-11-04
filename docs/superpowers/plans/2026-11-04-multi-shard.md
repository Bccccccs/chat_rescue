# 多消息分片修复 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 和 TDD 在本会话执行。

**Goal:** 完整识别现有消息分片，消除单聊漏查，并让导出、恢复、应用与回撤覆盖全部原件。
**Architecture:** 新增统一分片清单；沿用现有 bundle 格式并附带来源分片信息。所有分片独立解释 Name2Id，跨分片检查 server_id。目标新增消息写入已有会话的最大编号分片，无会话则选择现有最大编号分片；不创建新分片。
**Tech Stack:** Python 3.14、sqlite3、SQLCipher 4、unittest。
**Spec:** 用户已选择完整方案，依据 `docs/multi-shard-investigation.md`。

## Global Constraints

- 不启动微信、不取真实密钥、不修改真实数据库，只使用合成资料。
- 不扩大消息类型范围，不伪造 server_id，不绕过账号、完整性与碰撞检查。
- 兼容现有单分片 bundle 和计划；多分片计划必须绑定精确文件集合与 DB/WAL/SHM。
- 不自动创建新分片；客户端显示与手机迁移须单独验收。

## Review Focus

- 其他分片的 Name2Id/本地消息编号与第零分片相同但身份不同：必须按各库解析。
- 计划后新增/删除分片、孤立 WAL/SHM：应用前拒绝。
- 非零分片中的相同 server_id：必须去重或拒绝冲突，不重复插入。
- 候选路径与原件清单不完整或含路径穿越：必须在写入前拒绝。
- 大量消息：按分片/会话批量查询，不逐消息全库扫描。

## Task 1：统一清单与合成分片回归

Files: `chat_rescue/shards.py`, `tests/test_shards.py`。
Interfaces: `message_rels(root) -> tuple[str,...]`, `database_rels(root)`, `mutable_rels(root)`, `validate_plan_files(root, plan)`。
- [x] 增加数字排序、独立身份映射、非零分片会话、快照覆盖等失败用例并确认 RED。
- [x] 实现分片枚举与严格路径/文件验证，保持必须存在第零分片与三个辅助库。
- [x] 运行相应测试确认 GREEN。

## Task 2：跨分片单聊导出与恢复

Files: `workflow.py`, `sync.py`, `desktop_backend.py`, `tests/test_shards.py`。
- [x] 增加跨分片 sender/local_id、去重和冲突、资源/会话关联、单分片旧格式兼容测试并确认 RED。
- [x] 逐分片读取，按本库身份映射归一化；严格拒绝不兼容表结构。
- [x] 按批次跨所有 Msg 表查 server_id，按目标本库映射比较；保留各分片旧记录。
- [x] 分片内新增 local_id 避免与同会话其他分片编号重复；报告列出来源和目标分片。
- [x] 完整性检查和桌面计数覆盖所有分片，运行测试确认 GREEN。

## Task 3：密钥、快照、候选与应用全链路

Files: `operations.py`, `__main__.py`, `windows_capture.py`, `windows_backend.py`, `sync.py`, 加密/应用测试。
- [x] 增加多分片加密端到端、缺密钥拒绝、原件集合变化、回撤测试并确认 RED。
- [x] 动态清单贯通全部流程；回执保存精确已验证数据库路径，旧四库回执仅匹配单分片。
- [x] 应用/双向预检严格验证完整清单并比较当前状态；中途失败恢复整组原件。
- [x] 运行全套测试并记录跳过项。

## Task 4：文档与独立审查

- [x] 同步 AGENTS/README/SOP/CLI/Windows/证据边界，明确仅合成验证。
- [x] 独立审查完整 diff，修复重要发现，并运行全套测试和 diff 检查。

## 执行记录

- 用户已批准完整方案。实现由本会话执行，不再重复请求方案确认。
- 原有工作保存在既有 stash，本次不混入；新建 `codex/multi-message-shards` 分支，保留可直接审查的工作区改动，不自动提交或推送。
- 目标新增消息路由为实现决策；不推断微信的自动滚动建库规则，不宣称实机客户端已验证。

- 用户追加 Windows 全类型方向转换与 B 导入提醒：保留旧配置的限制，新增 structured 配置；非文本保留原本地状态，未知方向仍拒绝。仅文本允许已观察的一秒接收延迟去重。
- 参考远端 `f5153a1`（已 fetch）：Mac 媒体本体采用独立模式与状态验证，多分片恢复写入仍拒绝；本分支不直接套用 Mac 媒体路径。当前修复基于 `1215d16`，远端新提交作为参考，未合并其 Mac 媒体模块。
- 独立审查已复现并修复：撤回 manifest 路径穿越/不完整清单；目标相同消息跨分片重复时应按各库身份验证并逻辑去重一次。两项回归均已观察 RED→GREEN。
- Windows B 导入提醒仅在 A 核验后显示，要求本人在读取 B 和生成计划前确认；更换账号/密钥或重新登录清除。实际 C# 状态机回归与 WPF 编译通过。

- 最终验证：完整 153 项，143 通过、10 项 Mac/Swift 平台跳过；加密集成已实际运行。完整 WPF 编译、实际 C# 状态门槛及 git diff --check 通过。未提交、未推送，保留工作区供审查。

- 后续发布授权：用户明确要求推送与发布；整合远端 f5153a1 的 Mac 媒体模块，保留 Mac 媒体单分片边界。发布版本 0.5.1，本机完整 218 项中 178 通过、40 平台跳过；正式打包壳编译通过。Windows 分片回归迁至 tests/test_windows_shards.py，待远端 CI 构建安装 EXE 与便携 ZIP。

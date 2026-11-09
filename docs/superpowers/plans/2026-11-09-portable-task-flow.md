# Windows 任务接续与三步流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 简化 Windows 普通模式为三个主步骤，提供可核验的同操作者换机任务接续。

**Architecture:** 独立 portable_task 模块管理任务索引与相对文件清单；windows_backend 负责身份和重新验证，WPF 仅保存当前任务标识、显示阶段和调用桥接。原恢复/应用引擎保持，迁入的旧预览只能查看。

**Tech Stack:** Python 3.14 标准库、SQLCipher、WPF/.NET Framework。

**Spec:** docs/superpowers/specs/2026-11-09-portable-task-flow-design.md（用户已批准直接实施）。

## Global Constraints

- 普通模式，不创建用户或沙盒；代码维护不启动微信、不取真实密钥、不访问真实聊天库。
- 保留已有未提交改动，不提交、不正式发布。
- 换机重新验证双方实库，原回执不改写；已应用/中断历史不能恢复为可应用。
- 媒体缺失按已确认规则跳过，数据库/身份/哈希冲突仍拒绝。

## Review Focus

- 相对路径越界、链接、大小写冲突与未列出的文件必须拒绝。
- 导出只覆盖当前任务显式索引，不能误带其他任务或真实微信目录。
- 单侧读取/失败/未生成计划也能保存接续阶段。
- 新电脑旧路径失效不能导致静默选账号或把旧验证当新验证。
- 任何历史应用/中断标记只能查看，不能借迁移重复应用。

### Task 1：接续资料与任务索引

Files: 新增 chat_rescue/portable_task.py、tests/test_portable_task.py。
Interfaces: create(workspace)->id；update(workspace,id,**fields)->index；export_task(workspace,id,output)->summary；import_task(workspace,path)->index；load(workspace,id)->index。
- [x] 写合成测试：单侧+失败导出、任务隔离、原回执不改写、篡改/链接/路径/额外文件拒绝、私有目录、重复导入新ID。
- [x] 运行 unittest discover 对应文件确认缺模块失败；实现并通过。

### Task 2：后端任务生命周期与离线重新核验

Files: windows_backend.py、operations.py（仅如必要）、tests/test_windows_backend.py。
Interfaces: task-new/task-load/task-export/task-import；capture、plan 传 task_id 自动关联当前阶段；reverify-account 生成独立绑定新根的回执，保留历史。
- [x] 测试独立新索引、成功/失败记录、非法跨任务路径、迁入只读历史、重新验证失败不保存有效资料。
- [x] 沿用 verify_keys、account_nickname、capture_direction_profile；不复写旧回执，不降低完整性检查。

### Task 3：三步原生界面与接续入口

Files: desktop/windows/ChatRescue.cs、tests/test_windows_login_flow.py。
- [x] 写WPF行为测试：三个导航、单候选预选、多候选不猜、读取失败后另一侧保留、迁入未核验、旧预览无应用按钮。
- [x] 合并准备/登录与读取/预览；结果为第三步，保留高级操作和帮助；添加当前任务、导出/载入和工作区入口。
- [x] 运行原生编译和WPF测试通过。

### Task 4：文档、回归、整合包

Files: README.md、docs/windows-beginner-guide.md、docs/windows-deployment.md、docs/windows-desktop.md、docs/testing.md、CHANGELOG.md、本地 build 脚本。
- [x] 更新三步指南、私有迁移材料说明、双方实库要求和普通模式限制。
- [x] 运行完整合成回归，最终独立审查；必要修正后定向重验。
- [x] 新建唯一整合包，不覆盖旧包，检查包内源码一致、离线帮助、相对运行时、ZIP CRC/逐文件哈希，记录未实机验收边界。

## 执行方式

用户已明确“按设计直接实施”；主 agent 在现有 codex/windows-client-media-next 分支直接执行，不重复索要流程批准。现有工作区包含跨模块未提交依赖，继续维护并完整保留。最后按 executing-plans 要求做独立只读审查。


## 执行记录

Task 1: complete — portable_task 的索引、私有复制、清单哈希、失败报告、越界/链接/额外文件、导入中断保护与账号变更混入拒绝，9项定向通过。只复制当前索引资料，不复制真实微信目录。
Task 2: complete — 后端任务入口、旧候选只读、记录成功/失败、换目录HMAC/完整性与独立回执、每账号当前密钥路径引用，13项任务桥接与25项既有后端定向通过；其中一个测试使用合成加密四库及含空格新路径，不改旧回执。
Task 3: complete — WPF原生编译及4项测试通过，实际构造控件验证三导航、第二步无应用/第三步确认、昵称标签、唯一候选仅预选/多候选不猜、失败不清另一侧、历史X→Y快照与迁入待核验。
Ruling: 最新用户指示覆盖固定微信方案 — 不附微信、不要求重装，使用本机官方微信；保留已有可选固定载荷源码作历史能力，不用于最终整合包。
Ruling: 普通选择可按身份与目录复用当前工作区已有密钥材料 — 用户明确要求账号配套保存；角色不绑定，材料复用仍须HMAC、完整性和本人身份重新核验，不把仅登录当取钥成功。
Ruling: 实际目录证据包含WCDB三种material元数据备份 — 核对Tencent官方文档后，仅Windows明确名称放行且不进入DB/WAL/SHM读写集合；未知/孤立sidecar继续拒绝。
Ruling: 工作区外的独立历史预览保留查看，不自动复制进接续 — 无可靠任务索引时不能误混入其他任务；UI清空旧taskId并说明，工作区内历史可关联新独立索引。
审查：独立审查发现导入marker写入时机、WPF变量冲突、历史任务混用快照、失败报告未复制4项，已修复并定向复核；新增换账号后旧预览混入拒绝。
构建初检：包内无微信EXE/无固定载荷配置，运行时相对路径；源码逐文件匹配、README/指南内部链接和中文空格新目录doctor检查通过。最终ZIP及完整回归结果见后续记录。

Task 4: complete — 完整回归368项（323通过/45跳过），审查后82项当前源码定向全部通过；原生编译、源码哈希匹配、指南链接、中文空格路径doctor与打包后端任务往返通过。独立复核无新阻断。最终包不带微信、不要求重装，无真实微信/密钥/库操作。ZIP CRC与逐文件哈希在最终封包脚本核对，产物留在dist，不提交或推送。

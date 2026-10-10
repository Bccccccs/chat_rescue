# 命令说明

所有命令在仓库根目录执行，无需 pip 安装：

```sh
python3.14 -m chat_rescue --help
python3.14 -m chat_rescue doctor
```

完整的 A/B 操作顺序见 [恢复 SOP](recovery-sop.md)。命令不会自动完成登录、身份确认或手机迁移。

| 命令 | 用途 | 微信状态 |
| --- | --- | --- |
| `roots` | 列出本机 `db_storage` 目录，仍需核对所属账号 | 建议退出后选目录 |
| `contacts` | 按查询词列出明文 contact 库的 username/nick_name/remark | 输入为明文副本 |
| `doctor` | 检查环境，提供处理建议，不读取聊天正文 | 无需为环境检查登录 |
| `prepare-key-app` | 复制并重签专用微信副本，拒绝覆盖 | 所有微信进程退出 |
| `capture-keys` | 用固定上游捕获代码启动专用副本，保存经过匹配验证的密钥 | 先退出原版，再按提示登录副本 |
| `export-db` | 复制 DB/WAL/SHM，解密副本并报告 | 所有微信进程退出 |
| `save-chat` | 从明文库导出指定双方的单聊 bundle | 输入为明文副本 |
| `plan-restore` | 备份目标库，生成三库候选与恢复预览 | 所有微信进程退出 |
| `apply` | 校验并应用已检查的计划，保留原件 | 所有微信进程退出 |

## 取钥

```sh
python3.14 -m chat_rescue prepare-key-app \
  --app '/Applications/微信.app' --output "$WORK/WeChat-debug.app"
python3.14 -m chat_rescue capture-keys \
  --app "$WORK/WeChat-debug.app" --db-root "$SOURCE_DB" \
  --output "$WORK/source-keys.json" \
  --original-app '/Applications/微信.app'
```

`--original-app` 用于核对原版签名未改变；取钥结束不自动重启原版，需要时手动正常打开。保留专用副本的固定位置以保留已授予的权限。`capture-keys` 使用 Apple `/usr/bin/python3` 和 LLDB；项目其他命令使用 Python 3.14。不要用 Homebrew Python 替换取钥内部使用的系统 Python。保存的密钥必须匹配指定数据库的盐值并通过 HMAC 检查；账号身份仍需人工确认。

## 导出与会话保存

```sh
python3.14 -m chat_rescue export-db \
  --db-root "$SOURCE_DB" --keys "$WORK/source-keys.json" \
  --output "$WORK/source-plain"
python3.14 -m chat_rescue save-chat \
  --plain-root "$WORK/source-plain" --owner "$OWNER_B" --peer "$PEER_A" \
  --output "$WORK/chat"
```

导出保留 `Name2Id` 等表的隐式 rowid，不修改原库。只导出 `message/message_0.db`、`message/message_resource.db`、`session/session.db`、`contact/contact.db` 四库；输出沿用这些相对路径，`_encrypted_snapshot/` 保留加密原件和存在的 WAL/SHM，`export-report.json` 绑定账号父目录和四库哈希。`save-chat` 校验该绑定，不接受任意拼装或改写的明文库。`--owner` 是源库所属账号 B 的内部 wxid，`--peer` 是 A 的内部 wxid。仅支持指定双方的单聊导出，不把整库当作分享产物。

bundle 包含原始消息字段、BLOB 的 base64 表示、参与者映射、会话摘要和资源元数据，不包含密钥。base64 是编码，不是加密。消息中的文件卡片、名片或资源路径不能证明附件内容完整，工具不自动下载附件。

## 目录与身份查询

```sh
python3.14 -m chat_rescue roots
python3.14 -m chat_rescue contacts --plain-root "$WORK/source-plain" \
  --query '替换为联系人昵称、备注或内部wxid'
```

`contacts` 输出 `username`、`nick_name`、`remark`。命令参数使用精确的 `username`，不能使用昵称或备注替代。账号 `db_storage` 父目录必须等于 owner，或以 owner 加 `_` 开头；同名联系人仍需本人核对。

首版只接受单个 `message_0.db`，有其他编号分片时拒绝。仅支持类型 1、42 与 49 子类型 6；会话存在其他类型时整段拒绝，不会自动删掉或跳过它们。

## 预览与应用

```sh
python3.14 -m chat_rescue plan-restore \
  --source "$WORK/chat" --target-db-root "$TARGET_DB" \
  --target-keys "$WORK/target-keys.json" --target-owner "$OWNER_A" \
  --output "$WORK/restore-plan"
python3.14 -m chat_rescue apply --plan "$WORK/restore-plan"
```

`plan-restore` 的目标是 A 的实际数据库根目录与 A 的密钥。它保留真实 `server_id`，翻转本地视角字段，同编号同消息去重，对编号碰撞与不支持类型拒绝自动覆盖或恢复。此阶段校验明文完整性和加密候选，只生成候选，不写入 live 数据库。输出 `target-plain/`（含快照）、`recovered-plain/`、`candidates/`、`preview.json` 和 `plan.json`。`preview.json` 仅含数量与映射，逐条内容需在本机查看 `recovered-plain`，可执行步骤见 SOP。

`apply` 会比对计划记录的原库（含 WAL/SHM）和候选哈希；身份、明文完整性与加密验证在计划阶段完成，应用不再次解密验证。原库变化、计划重复应用或验证失败时不能继续。不要编辑计划或候选文件来绕过检查。应用的 `originals/` 保存三库和存在的 contact 原件及 WAL/SHM。`applied.json` 为成功回执，`rollback.json` 为回撤回执；`apply-started.json` 表示已尝试或中断，不能重复应用。部分失败尝试回撤；回撤成功与否必须查看实际报告，不能因命令退出就推断成功。

## 输出与隐私

以每次命令给出的报告路径和状态为准。不要把密钥、明文数据库、bundle、候选或原件的完整内容贴到公开问题单。若要反馈错误，提供经过脱敏的命令名、环境版本、错误类别与最小复现步骤；不要提供真实路径、账号标识或聊天正文。

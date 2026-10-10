# 双账号恢复 SOP

A = 误删方、目标账号；B = 保留方、源账号。以下先在 B 的电脑执行，再在 A 的电脑执行；同一台 Mac 也可以，但必须严格切换账号与工作目录。微信号、手机号和真实身份不会因恢复视角改变；改变的是本地数据库映射和消息方向。

## 0. 环境与原件

在仓库根目录运行：

```sh
python3.14 -m chat_rescue doctor
```

环境安装见 [README](../README.md)。双方各自保留手机记录、Mac 原始数据库与已有微信备份。不要删除现有会话、消息或手机数据来准备恢复。

取钥需要运行专用微信副本。退出所有微信进程后复制、重签；只有取钥阶段按工具提示启动副本。取钥结束后回到原版，导出、预览和应用都要求微信退出。不要同时运行两个微信副本。

## 1. 确认实际数据库目录与账号

B 在原版微信登录 B，把手机上保留的记录导入 B 的 Mac，确认待恢复消息在 B 的原版电脑微信中可见。A 在开始自己的阶段时，登录 A 的原账号，确认 B 的联系人身份和目标会话。无法确认身份时先停止，不能靠显示昵称推断。

退出微信后，先列出本机数据库根目录：

```sh
python3.14 -m chat_rescue roots
```

查找结果可能有多个账号目录，也可能因权限不足而没有结果。使用 Finder 查看微信数据目录并与刚登录账号对应；不要选微信的 `Backup` 备份包、旧账号或工具输出目录。`--db-root` 指向实际数据库根目录，消息库及联系人、会话库应属于同一个账号。若 `message_0.db` 在 `db_storage/message/` 下，根目录是该 `db_storage`，不是 `message` 子目录。当前根目录必须有 `message/message_0.db`、`message/message_resource.db`、`session/session.db`、`contact/contact.db`。首版只允许一个 `message_0.db` 分片；发现 `message_1.db` 等会拒绝，不要删除其他分片来绕过检查。

记下双方的真实内部账号标识（wxid）。`db_storage` 的父目录必须等于所属账号的内部 username，或以该 username 加 `_` 开头。昵称、手机号、对外展示微信号不能直接当作内部 wxid。B 可在明文导出后使用下面的 `contacts` 查询内部 username，并与账号目录及双方身份交叉核对；不确定时不得继续应用。不要把查找结果、wxid 或数据库路径公开贴到 Issue。

## 2. B：创建私有工作目录与取钥副本

以下示例中的路径和 `wxid_...` 必须替换为 B、A 的实际值；保留引号，避免路径中空格导致错误。

```sh
umask 077
WORK="$(mktemp -d "$HOME/chat-rescue-B.XXXXXX")"
chmod 700 "$WORK"
SOURCE_DB='/替换为B的实际数据库根目录'
OWNER_B='wxid_替换为B的内部账号标识'
PEER_A='wxid_替换为A的内部账号标识'
ORIGINAL_APP='/Applications/微信.app'
python3.14 -m chat_rescue prepare-key-app \
  --app "$ORIGINAL_APP" --output "$WORK/WeChat-debug.app"
```

微信安装名称不同则修改 `ORIGINAL_APP`。专用副本路径必须是新路径；工具拒绝覆盖。不要把副本放进原版应用位置。

在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」添加本次 `WeChat-debug.app` 副本。系统可能还要求运行命令的 Terminal 获得「开发者工具」或「完全磁盘访问权限」；按实际报错处理。授权副本后重新运行取钥命令，不要关闭 SIP。

```sh
python3.14 -m chat_rescue capture-keys \
  --app "$WORK/WeChat-debug.app" \
  --db-root "$SOURCE_DB" \
  --output "$WORK/source-keys.json" \
  --original-app "$ORIGINAL_APP"
```

工具使用 Apple `/usr/bin/python3` 与 LLDB 启动自己的专用副本。按提示在副本中登录 **B**，确认账号。工具只保存与指定数据库盐值匹配且 HMAC 验证通过的密钥。匹配验证不能代替人的账号身份核对。

完成后按提示退出副本。工具保留固定副本位置与权限，并验证原版签名未改变，不自动重启原版；需要使用原版时手动正常打开。此处保持所有微信进程退出，再执行下一步。密钥文件只留在 B 本机：

```sh
chmod 600 "$WORK/source-keys.json"
```

## 3. B：导出并仅分享单聊 bundle

```sh
python3.14 -m chat_rescue export-db \
  --db-root "$SOURCE_DB" \
  --keys "$WORK/source-keys.json" \
  --output "$WORK/source-plain"
```

导出完成后查询 A、B 的联系人信息：

```sh
python3.14 -m chat_rescue contacts --plain-root "$WORK/source-plain" \
  --query '替换为A的昵称、备注或内部wxid'
python3.14 -m chat_rescue contacts --plain-root "$WORK/source-plain" \
  --query '替换为B的昵称、备注或内部wxid'
```

输出字段 `username` 才是 `--owner` / `--peer` 需要的内部账号标识；`nick_name`、`remark` 仅辅助查找。同名结果必须进一步核对，不能直接选择第一项。将核对过的 username 填回变量：

```sh
OWNER_B='wxid_替换为B的内部username'
PEER_A='wxid_替换为A的内部username'
python3.14 -m chat_rescue save-chat \
  --plain-root "$WORK/source-plain" \
  --owner "$OWNER_B" --peer "$PEER_A" \
  --output "$WORK/chat"
```

`source-plain/export-report.json` 记录账号目录和四库哈希；`save-chat` 校验该绑定与哈希，因此不要编辑明文库或报告。四库直接位于 `source-plain/message/`、`session/`、`contact/`；`_encrypted_snapshot/` 保存对应加密原件及存在的 WAL/SHM。先检查导出报告和会话参与者映射，再检查 bundle 中确实是 B 与 A 的授权单聊。全文明文库可能含其他联系人和会话，不能分享。bundle 不包含密钥，但消息正文、参与者信息和资源元数据仍是敏感数据。

如需打包，只打包 `chat` 目录：

```sh
tar -czf "$WORK/chat-bundle.tar.gz" -C "$WORK" chat
chmod 600 "$WORK/chat-bundle.tar.gz"
```

通过双方同意的私密渠道把这个包传给 A。不要传 `source-keys.json`、`source-plain`、整个工作目录或微信数据目录。名片与文件消息的条目不等于完整附件；工具不会自动下载或补齐附件内容。

## 4. A：切换原账号，创建独立工作目录

A 在原版微信登录 **A 原来的账号**，确认 B 的真实身份和会话。保留现有消息。然后退出所有微信进程，重新确定 A 当前账号的实际数据库目录。

同一 Mac 上切换时，B 的数据库路径不能沿用为 A 的路径，B 的密钥也不能用于 A。重新创建 A 的工作目录和副本：

```sh
umask 077
WORK="$(mktemp -d "$HOME/chat-rescue-A.XXXXXX")"
chmod 700 "$WORK"
TARGET_DB='/替换为A的实际数据库根目录'
OWNER_A='wxid_替换为A的内部账号标识'
ORIGINAL_APP='/Applications/微信.app'
python3.14 -m chat_rescue prepare-key-app \
  --app "$ORIGINAL_APP" --output "$WORK/WeChat-debug.app"
```

为 A 的新副本授予完全磁盘访问权限，再取钥；副本里必须登录 **A**：

```sh
python3.14 -m chat_rescue capture-keys \
  --app "$WORK/WeChat-debug.app" \
  --db-root "$TARGET_DB" \
  --output "$WORK/target-keys.json" \
  --original-app "$ORIGINAL_APP"
chmod 600 "$WORK/target-keys.json"
```

按提示退出副本，保留其固定位置；原版不会自动重启。确认所有微信进程退出。把已知来自 B 的单聊包复制到 A 的工作目录，在本机解包：

```sh
tar -xzf "$WORK/chat-bundle.tar.gz" -C "$WORK"
```

此命令仅用于双方确认过的本项目 bundle，不执行包内文件。若收到目录形式的 bundle，将整个 `chat` 目录放到 `$WORK/chat` 即可。

在本机查看 bundle 的账号标识：

```sh
python3.14 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("B/owner:",d["owner"]); print("A/peer:",d["peer"])' \
  "$WORK/chat/conversation.json"
```

A 必须核对 `peer` 是自己，把已确认的值填入 `OWNER_A`，与 B 再次确认 `owner` 是 B；并确认 A 的 `db_storage` 父目录等于 `OWNER_A` 或以 `OWNER_A` 加 `_` 开头。目录匹配只是约束，不能替代本人确认。

## 5. A：预览并核对

```sh
python3.14 -m chat_rescue plan-restore \
  --source "$WORK/chat" \
  --target-db-root "$TARGET_DB" \
  --target-keys "$WORK/target-keys.json" \
  --target-owner "$OWNER_A" \
  --output "$WORK/restore-plan"
```

这一步保存目标四库及存在的 DB/WAL/SHM 快照，建立三库候选与报告，不修改原库。输出包括：

- `target-plain/`：目标四库明文、`export-report.json` 与 `_encrypted_snapshot/`。
- `recovered-plain/`：恢复后的明文四库，供本机逐条检查。
- `candidates/`：消息、资源、会话三库的加密候选，不改 contact 库。
- `preview.json`：数量与发送者映射；`plan.json`：原件/候选哈希和目标路径。

先查看数量/映射预览，再检查消息；预览不包含逐条正文：

```sh
python3.14 -m json.tool "$WORK/restore-plan/preview.json"
```

下面只读打开 `recovered-plain` 的指定会话表，按时间顺序显示真实编号、发送者与内容字段。压缩或 BLOB 字段可能显示为原始字节，不能据此推断附件完整。此步骤只读取候选明文，不改数据库：

```sh
python3.14 - "$WORK/restore-plan" <<'PYCODE'
import json, sqlite3, sys
from contextlib import closing
from pathlib import Path
root = Path(sys.argv[1]).resolve()
preview = json.loads((root / 'preview.json').read_text())
table = '"' + preview['table'].replace('"', '""') + '"'
uri = (root / 'recovered-plain/message/message_0.db').as_uri() + '?mode=ro'
with closing(sqlite3.connect(uri, uri=True)) as db:
    query = ('SELECT m.server_id,m.create_time,n.user_name,m.local_type,'
             'm.message_content,m.compress_content FROM ' + table + ' AS m '
             'LEFT JOIN Name2Id AS n ON n.rowid=m.real_sender_id '
             'ORDER BY m.sort_seq,m.local_id')
    for row in db.execute(query):
        print(row)
PYCODE
```

只在本机检查，禁止将这些文件或终端正文公开分享。核对：

- 源账号是 B，目标账号是 A，单聊另一方是 B；不要只看昵称。
- 从 B 的视角到 A 的视角后，B 发出的消息在 A 端应显示为收到，A 发出的消息仍属于 A。
- 时间、原始消息字段和真实 `server_id` 来自源记录；不能用虚构编号补缺口。
- 同一编号且同一消息应去重；编号碰撞和不支持的消息类型不能自动覆盖。
- 计划生成时的明文完整性与加密验证通过，备份位置明确。命令成功生成完整计划；发现冲突或不支持类型会拒绝，而不是自动跳过该部分。

任何身份不符、方向不确定、编号碰撞或验证失败都先停止。若期间启动了微信或原库发生变化，旧计划不能继续应用；重新退出微信并生成新计划。

## 6. A：应用、电脑验收与可选手机验收

确认预览符合预期且所有微信进程退出后执行：

```sh
python3.14 -m chat_rescue apply --plan "$WORK/restore-plan"
```

计划阶段校验身份、明文完整性与加密候选；`apply` 比对原库（含 WAL/SHM）与候选哈希，不重新解密做完整性验证。它应用消息、资源、会话三库，`originals/` 保留这三库以及存在的 contact 原件和 WAL/SHM；部分失败会尝试回撤。它不自动发送消息，也不修改微信原版签名。`applied.json` 是成功回执，`rollback.json` 记录回撤结果。`apply-started.json` 表示已经开始尝试或中断，不能对同一个计划重复 `apply`；先检查回执与原件。

应用成功后启动 **原版微信**，逐条检查联系人、发送方向、时间、文本/消息条目以及原有记录。保留原件、计划和报告，直到验收完成。如果异常，按 [失败处理](troubleshooting.md)停止后续写入。

如需迁移到手机，使用原版微信的电脑到手机聊天迁移入口手动操作，先阅读当前界面的说明，保留手机原有记录。入口名称可能因版本变化。迁移后逐条检查方向、内容、时间与已有消息；电脑可见不能证明手机可迁移。本项目没有对真实跨账号两条消息完成独立的手机分项验收，见 [证据说明](evidence-and-support.md)。

## 7. 结束与资料保管

双方确认结果前不要清理任何原始数据库或备份。密钥、明文库、bundle、计划和报告都按私人聊天资料保管；不要提交 Git 或同步到公开服务。验收后按双方约定处理私有临时资料，并可撤销专用副本的系统权限。删除临时副本不能代替保留可回撤的原件。

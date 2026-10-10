# 公开测试

在仓库根目录安装 Python 3.14 和 SQLCipher 后运行：

```sh
python3.14 -W error::ResourceWarning -m unittest discover -s tests -v
```

所有账号、消息正文、消息编号和文件元数据来自代码生成的合成数据。测试在临时目录生成数据库和随机密钥，结束后自动清理，不打开或修改真实微信。加密集成测试会用本机 SQLCipher 库；没有库时这些测试会跳过，所以不能把跳过解释为通过验证。

测试覆盖：发送者与 packed 方向映射、真实编号保留与碰撞拒绝、去重、资源关联与会话索引、稀疏隐式 rowid、已提交 WAL 读出、原盐再加密、原库变更拒绝、候选变更拒绝、写入第二库失败时 DB/WAL 回撤，以及加密 B 库→CLI 单聊导出→A 候选→应用→重新解密比对。

合成测试会替代平台/微信进程检查，不能证明另一平台可用，也不涵盖 LLDB 取钥、微信客户端显示或手机迁移。这些需按 SOP 在受支持环境人工验收。

GitHub Actions 的 macOS 任务安装 SQLCipher 后执行相同测试，先要求依赖存在以避免集成测试静默跳过；工作流使用 [setup-python](https://github.com/actions/setup-python) 安装指定 Python。仓库首次发布时，以本机测试回执为已知结果，远程 CI 结果应查看 Actions 的实际运行状态。

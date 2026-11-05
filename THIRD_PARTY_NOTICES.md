# 第三方代码与工具

Windows 0.5.6 内置 Python tzdata 2025.2（IANA 时区数据），由 Python Software Foundation 发布，Apache-2.0；用于隔离运行时的媒体月份路径。固定分发包 SHA256 为 `1a403fada01ff9221ca8044d701868fa132215d84beb92242d9acd2147f667a8`。来源：https://pypi.org/project/tzdata/2025.2/ 。分发许可与 Apache 2.0 全文分别随包保存在 `licenses/tzdata-LICENSE.txt`、`licenses/tzdata-Apache-2.0.txt`，数据包自身许可文件亦保留。

## 固定上游取钥代码

本项目取钥捕获代码使用 acmerfight 发布的 Gist 固定副本：

- 来源：[Gist 0a01249ef72970d07a2603dbb629e80f](https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f)
- 固定提交：`c98dbb10cf23c30e83d5d744905101b0880887a0`
- 许可证：MIT
- 原版权声明：`Copyright (c) 2026 Contributors`

本项目在固定副本上增加数据库完成分组校验及启动工作目录说明，未更换上游基线。保留随固定副本提供的许可证和版权声明。本项目的封装、中文文档及自有代码按根目录 MIT License 提供；它们不替代上游版权声明。下面保留上游 MIT 许可文本：

```text
MIT License

Copyright (c) 2026 Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 外部运行环境

Windows 合成测试及 0.3.6 安装包使用 NuGet 的 [SQLitePCLRaw.lib.e_sqlcipher 2.1.11](https://www.nuget.org/packages/SQLitePCLRaw.lib.e_sqlcipher/2.1.11) 中 win-x64 DLL（报告 SQLCipher 4.5.2 community，密码提供者为 libtomcrypt）。构建固定下载并核对包 SHA256，二进制不提交 Git；安装包附 SQLitePCLRaw Apache-2.0 许可及 NOTICE、SQLCipher BSD-style、SQLite 和 LibTomCrypt 许可。该 DLL 是上游社区非官方构建，不是 Zetetic 商业二进制。SQLCipher 加载与密钥路径适配在本地固定副本上修改，未更换前述取钥上游提交与版权。

Windows 安装包包含官方 Python 3.14.10 x64 嵌入式发行，保留完整 `LICENSE.txt` 并复制至安装目录 `licenses/Python-LICENSE.txt`。Python 原始 ZIP 的 SHA256 固定在打包脚本中；使用隔离 `._pth`，不附加开发者路径。Inno Setup 6.7.4 仅用于构建安装器，不要求用户安装编译器；中文翻译来自其固定版本 `Files/Languages/Unofficial/ChineseSimplified.isl`，保留原维护者声明。上述许可可通过应用“开源许可”入口查看。所有这些产品均不以本项目根目录 MIT 取代其上游许可。

微信应用、Apple LLDB/Xcode Command Line Tools、Homebrew 由用户自行安装；源码运行所需 Python、SQLCipher 由用户安装，桌面包包含其运行时，按各自许可证或使用条款提供；本项目 MIT License 不适用于这些产品。Python 与 SQLCipher 的许可证以打包运行时及各项目上游许可为准。本项目不分发微信应用本体，专用副本由用户在自己的 Mac 上从已安装的应用创建。

## Windows Config.Cipher 布局适配

Windows 实验取钥的对象布局、XOR mask 和查找思路改编自 [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool/blob/79f1b5b92e12c66aa281b4a60a3c478b5f547dfa/wcdb_key_tool_windows.py)，固定提交 `79f1b5b92e12c66aa281b4a60a3c478b5f547dfa`，MIT。该上游描述的是 Windows 4.1+ 路线，不构成本项目 4.2.0 已实机验证的证据。本地封装增加显式 PID、当前用户、程序路径/版本/签名验证、四库限定、全库验证与私有输出，不包含其自动选择进程、提权建议或其他平台实现。原 Mac 上游固定提交及 `Copyright (c) 2026 Contributors` 保留不变。以下保留此新增来源许可：

```text
MIT License

Copyright (c) 2025 CloudDreamAI / TANGandXUE

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

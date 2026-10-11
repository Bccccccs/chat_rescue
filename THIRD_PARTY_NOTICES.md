# 第三方代码与工具

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

微信应用、Apple LLDB/Xcode Command Line Tools、Homebrew 由用户自行安装；源码运行所需 Python、SQLCipher 由用户安装，桌面包包含其运行时，按各自许可证或使用条款提供；本项目 MIT License 不适用于这些产品。Python 与 SQLCipher 的许可证以打包运行时及各项目上游许可为准。本项目不分发微信应用本体，专用副本由用户在自己的 Mac 上从已安装的应用创建。

# 第三方来源与许可

## MemoTrace / WeChatMsg

原作者 SiYuan / Shuaikang Zhou。发布仓库中复用的 4.x 解析代码使用 MIT 许可证，副本位于 `源码/MemoTrace/LICENSE`。

- https://github.com/shixiaogaoya/MemoTrace ，提交 `6923dc94a7d14f6b86a9649a2c727b42ebcbd0e7`。
- https://github.com/tqjason/WeChatMsg ，提交 `14b466dcc4c60b43d9d79301e489c8d6029daed4`。

本地修改集中在 `wxManager/db_v4/hardlink.py` 和 `wxManager/parser/wechat_v4.py`。融合版的读取、归档和报告入口位于 `工具/`。

## 留痕 2.0.5 桌面源码和 PyQt5

历史桌面源码来自作者 SiYuan 的提交 `fc1e2fa7a54a0e80fc0e12f2adb56479de9e477c`，GPLv3。许可证位于 `源码/WeChatMsg-2.0.5/LICENSE`。本地融合版复用该版本的主窗口、会话表单、头像和气泡组件。 [历史提交](https://github.com/tqjason/WeChatMsg/tree/fc1e2fa7a54a0e80fc0e12f2adb56479de9e477c)

PyQt5 5.15.11 安装于项目独立运行环境，使用 PyPI 提供的 GPLv3 版本。 [PyQt5 发布页](https://pypi.org/project/PyQt5/5.15.11/)

## jiatj/wechatapi

Apache License 2.0。参考提交 `04ef8cbde3862cff90b5f6b42c9ebfcea44ef48d`：

- https://github.com/jiatj/wechatapi/blob/04ef8cbde3862cff90b5f6b42c9ebfcea44ef48d/wechatauto/db.py
- https://github.com/jiatj/wechatapi/blob/04ef8cbde3862cff90b5f6b42c9ebfcea44ef48d/wechatauto/media.py

`工具/local_crypto.py` 的 Config.Cipher 适配和 `工具/local_media.py` 的图片格式与参数验证参考了该项目。Apache-2.0 许可证副本位于 `LICENSES/Apache-2.0.txt`。

## APSW

APSW 提供 SQLite 的 Python 接口及自定义 VFS。使用其公开 API 构建按需只读解密视图。

https://rogerbinns.github.io/apsw/vfs.html

Python 依赖各自的许可证保留在“运行环境/Lib/site-packages”中。

# MemoTrace 本地恢复版

基于留痕 2.0.5 的 Windows 桌面界面，接入微信 4.x 本地记录读取、富媒体浏览、多格式导出和年度报告。适合整理本人账号在当前电脑上已同步的聊天记录。电脑版微信 4.2.0.13 的会话读取、头像、离线快照与导出已实测。微信更新后，数据库结构或读取方式可能变化。

## 项目从哪里来

| 来源 | 用到的部分 |
| --- | --- |
| [LC044 / WeChatMsg 历史提交](https://github.com/tqjason/WeChatMsg/tree/fc1e2fa7a54a0e80fc0e12f2adb56479de9e477c) | 原作者 SiYuan 的 PyQt5 主窗体、会话表单、聊天气泡、图标和停用词。源码中的版本字段为 2.0.5。[官方标签归档](https://archive.softwareheritage.org/api/1/snapshot/3972103583ab39c7afb06c9f171e5f12f3029a53/?branches_from=refs%2Ftags%2Fv2.1.1&branches_count=1) |
| [shixiaogaoya / MemoTrace](https://github.com/shixiaogaoya/MemoTrace/tree/6923dc94a7d14f6b86a9649a2c727b42ebcbd0e7) | 微信 4.x 消息模型、类型解析、附件路径和导出素材。另用 [tqjason / WeChatMsg](https://github.com/tqjason/WeChatMsg/tree/14b466dcc4c60b43d9d79301e489c8d6029daed4) 核对上游结构。 |
| [jiatj / wechatapi](https://github.com/jiatj/wechatapi/tree/04ef8cbde3862cff90b5f6b42c9ebfcea44ef48d) | 参考微信 4.x 数据库适配和图片容器处理。 |
| [APSW](https://rogerbinns.github.io/apsw/vfs.html) 与 [jieba](https://pypi.org/project/jieba/0.42.1/) | 分别提供 SQLite 只读视图的接口和年报中文分词。 |

历史桌面代码、解析核心与参考实现适用各自的许可证。版权、固定提交和本地改动说明见 [第三方来源与许可](THIRD_PARTY_NOTICES.md)。

## 实现与主要改动

1. **微信 4.x 读取。** 验证数据库页，使用 APSW 的只读 SQLite 虚拟文件系统按需访问数据，并在内存中叠加已提交的 WAL。会话索引包含普通消息和公众号分库，点开会话时再解析消息。
2. **桌面接入。** 将 2.0.5 的 PyQt5 窗体和消息气泡接到新的会话后端，增加实时会话与本地快照切换、分页搜索、消息类型筛选、头像和本机媒体预览。两处本地解析修正位于 4.x 核心的 hardlink.py 与 wechat_v4.py。
3. **归档和导出。** 可保存单个会话的离线快照。完整归档保留原始记录、时间线、资源索引和本机附件。格式导出支持 CSV、Word、HTML、TXT、Markdown、AI 对话 TXT、JSON、PDF 和 Excel，可选日期和消息类型。
4. **群成员与年报。** 群成员从本机联系人和群成员表读取。会话年报支持群聊与私聊，包含活跃日历、发言排行和常用词。「我的年度聊天报告」可按账号汇总本机分库或已保存快照。两种年报均生成独立 HTML。年度统计按北京时间的自然年计算。

## 安装与运行

需要 Windows 64 位和 Python 3.12。将仓库克隆到可写目录，在仓库根目录运行：

    py -3.12 -m venv 运行环境
    .\运行环境\Scripts\python.exe -m pip install -r requirements.txt
    Copy-Item -LiteralPath 本地配置.example.json -Destination 本地配置.json

编辑 **本地配置.json**，填写本机 Weixin.exe 的完整路径、包含 db_storage 的微信账号目录以及自己的内部微信用户名。default_chat 可留空，也可填入优先打开的会话名称。Windows 路径在 JSON 中可用正斜杠。

双击 **启动恢复版.cmd**。保持微信登录后，点击「读取全部微信会话」。对已保存的会话，可在「本地快照」中离线浏览。导出和会话年报默认保存在项目同级的群聊或个人目录。个人总览保存在同级的 个人/年度报告。

    .\运行环境\Scripts\python.exe .\工具\memotrace_local.py refresh --chat "会话名称"
    .\运行环境\Scripts\python.exe .\工具\memotrace_local.py export --chat "会话名称"
    .\运行环境\Scripts\python.exe .\工具\memotrace_local.py members --chat "群名称"
    .\运行环境\Scripts\python.exe .\工具\test_recovery.py

## 代码入口和适用范围

- **工具/** 存放恢复引擎、会话快照、导出和年报。
- **源码/MemoTrace/** 保留实际用到的 4.x 解析核心和媒体资源。
- **源码/WeChatMsg-2.0.5/** 保留实际用到的历史桌面组件和融合版入口。主窗体控制器为 app/ui/recovery.py，聊天组件为 app/ui/chat/recovery_view.py。
- 此版本覆盖电脑已同步的聊天记录。过期或已清理的媒体可能只剩缩略图，也可能没有本机文件。原版 AI 小助手尚未接入。

# YouTube Digest

把每个 YouTube 视频变成一份可以深入学习的资料。

这是基于[张咋啦原版](https://github.com/zarazhangrui/youtube-digest) v1.2.0 的 Fork 改进版，保留原版 MIT 许可证。源码维护在[本仓库](https://github.com/bangzhu-commit/youtube-digest)，密钥保存在 Chrome 本地；不附带服务商账号或 API 额度。

## 本地改动

- 默认直接读取当前 YouTube 页面的字幕，无需 Supadata Key。借鉴 Obsidian Web Clipper 使用的 Defuddle 0.19.4，兼容新旧文字记录面板、JSON3/XML 字幕，并在原地址失效时向 YouTube 播放器接口请求新字幕地址；必要时打开自带文字记录面板。
- Supadata 改为可选备用。可选“直接读取”“直接读取失败后用 Supadata”“仅 Supadata”。只有主动选择备用或 Supadata 模式才会消耗其额度。
- 模型平台支持 OpenRouter（默认）、302.ai 和 DeepSeek，模型 ID 可编辑；不同平台的 Key 分开保存，切换时不混用。
- 原文字幕无需模型 Key。中文翻译、概览、解释与润色才调用选定的模型平台。
- 保留原版侧栏、双语对照、时间戳跳转、搜索和本地缓存。
- 小宇宙单集页使用中文阅读侧栏：读取已有全文、搜索、回听、按原句批注、恢复未提交感想，并归档到 Obsidian。

## 小宇宙与 Obsidian

在小宇宙单集页点击插件图标，即可打开阅读侧栏。优先读取 Obsidian `知识库/原始素材库` 中链接到这一集且含“转写全文”标题的 Markdown；也支持导入 Markdown、TXT、SRT、VTT 和分段 JSON。网页中只有明确的全文文本才会被读取，节目简介和转写文件引用不算全文。本版没有自动音频转写，读取和批注无需模型 Key；只有主动点击“生成 AI 概览”或“解释原句”才调用配置的模型。

选中原句，或点击段落旁“批注”，输入自己的感想并保存草稿。草稿按节目 ID 保存在当前 Chrome 本地，关闭侧栏后可恢复。点击“保存到 Obsidian”会创建 `知识库/阅读工作台/小宇宙-节目ID-阅读批注.md`，嵌入原始素材并附上原句、时间戳和感想。已存在的原始稿不会被覆盖；重复保存同一批注不会重复追加。已有批注被手动修改时，以 Obsidian 内容为准；遇到冲突会提示重新读取。首次导入的文稿还会保留原文件文本附件。连接不可用时仍可导入全文、写草稿和导出 Markdown。

直接读取和归档需要 Python 3 与本机 Native Messaging 连接。macOS 下，在 `chrome://extensions` 的插件详情页复制其 ID，然后从此源码目录运行一次：

```sh
python3 native/install_host.py --extension-id YOUR_EXTENSION_ID --vault "/path/to/your/vault"
```

安装器仅登记这个扩展 ID 和选定 Vault；桥接程序只接受按节目 ID 读取原始素材或保存阅读批注，不提供任意文件操作、网络请求或命令执行。配置位于用户的 Application Support 目录，不进入源码或安装包。源码目录需要长期保留。其他系统需要显式提供 `--config-dir` 和 `--host-dir`，尚未做浏览器实测。

升级本版后请重新加载扩展，再刷新小宇宙页面，让新的页面脚本生效。新权限是小宇宙单集页访问和 `nativeMessaging`，用于上述本机连接。网页回听依赖当前页的音频播放器；播放器尚未加载时需先在节目页点击播放。

## 安装与配置

长期保留此源代码文件夹。在 Chrome 打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择含 `manifest.json` 的这个文件夹。移动或删除文件夹会使扩展失效。此副本不会自动更新；改代码后点“重新加载”，再刷新 YouTube。

在插件 Settings 中选择 OpenRouter，直接在那里输入自己的 Key，不要发到聊天或写入源码。默认模型 ID 为 `deepseek/deepseek-v4-flash`，可替换为该平台可用且支持 JSON 输出的文字聊天模型。保存设置不调用模型。OpenRouter 的默认 DeepSeek V4 Flash 使用该平台的 `reasoning.enabled=false` 关闭思考，以减少翻译和摘要的等待时间；其他模型不追加此参数。302.ai 使用自己的模型 ID，预设为 `gpt-4o-mini`。DeepSeek 专用的非思考参数和空 JSON 重试只用于 DeepSeek。

字幕默认选“直接读取 YouTube 页面”。此模式完全不调用 Supadata。需要备用时自行选择相应模式并输入 Supadata Key；其请求限定 `mode=native`，不会自动购买语音转录。实际费用看各平台实时价格和用量。

## 翻译质量

默认使用“精校”：借鉴宝玉 baoyu-translate 的语境分析、术语统一和审校规则，初译后用独立的一次模型调用对照英文检查并修订中文。每批调用模型两次，费用和等待时间相应增加；这是适配字幕的精校流程，并未完整执行文章翻译 Skill 的所有阶段。“快速”模式调用一次，可在设置中切换后重新打开侧栏。新版及不同模式使用独立的译文缓存，原文和时间戳仍逐段对应。原始自动字幕有误时，译文仍可能受影响。来源与许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 支持范围与验收

支持 Chrome 116+、带原生字幕的普通公开 `youtube.com/watch` 页面。播放器备用请求在当前 YouTube 页面的 MAIN world 同源执行，不新增代理或浏览器权限，不需要 Defuddle 云服务或 API Key。YouTube 页面与字幕接口仍可能变化；全部原生路径失败时可刷新重试，或选择 Supadata 备用。无字幕视频需要额外语音转录，本版没有实现。Shorts、直播和受限视频未验证。

验证命令：`npm test`、`npm run check`、`npm run package`。测试通过只能说明本地逻辑通过；真实 Key、额度、视频页面仍要实测。安装后用真实视频检查字幕、时间戳跳转和双语翻译。

完整数据流见 [PRIVACY.md](PRIVACY.md)，密钥与安装说明见 [SECURITY.md](SECURITY.md)。

## 后续维护

用户要求修改本项目后，编程 Agent 在验证通过后自动提交并推送到这个 Fork。[AGENTS.md](AGENTS.md) 记录了这项持续授权。源码同步不会自动重载已安装的扩展，也不会创建后台同步任务。

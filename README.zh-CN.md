# YouTube Digest

把每个 YouTube 视频变成一份可以深入学习的资料。

这是基于[张咋啦原版](https://github.com/zarazhangrui/youtube-digest) v1.2.0 的 Fork 改进版，保留原版 MIT 许可证。源码维护在[本仓库](https://github.com/bangzhu-commit/youtube-digest)，密钥保存在 Chrome 本地；不附带服务商账号或 API 额度。

## 本地改动

- 默认直接读取当前 YouTube 页面的字幕，必要时打开 YouTube 自带文字记录面板，无需 Supadata Key。
- Supadata 改为可选备用。可选“直接读取”“直接读取失败后用 Supadata”“仅 Supadata”。只有主动选择备用或 Supadata 模式才会消耗其额度。
- 模型平台支持 OpenRouter（默认）、302.ai 和 DeepSeek，模型 ID 可编辑；不同平台的 Key 分开保存，切换时不混用。
- 原文字幕无需模型 Key。中文翻译、概览、解释与润色才调用选定的模型平台。
- 保留原版侧栏、双语对照、时间戳跳转、搜索和本地缓存。

## 安装与配置

长期保留此源代码文件夹。在 Chrome 打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择含 `manifest.json` 的这个文件夹。移动或删除文件夹会使扩展失效。此副本不会自动更新；改代码后点“重新加载”，再刷新 YouTube。

在插件 Settings 中选择 OpenRouter，直接在那里输入自己的 Key，不要发到聊天或写入源码。默认模型 ID 为 `deepseek/deepseek-v4-flash`，可替换为该平台可用且支持 JSON 输出的文字聊天模型。保存设置不调用模型。OpenRouter 的默认 DeepSeek V4 Flash 使用该平台的 `reasoning.enabled=false` 关闭思考，以减少翻译和摘要的等待时间；其他模型不追加此参数。302.ai 使用自己的模型 ID，预设为 `gpt-4o-mini`。DeepSeek 专用的非思考参数和空 JSON 重试只用于 DeepSeek。

字幕默认选“直接读取 YouTube 页面”。此模式完全不调用 Supadata。需要备用时自行选择相应模式并输入 Supadata Key；其请求限定 `mode=native`，不会自动购买语音转录。实际费用看各平台实时价格和用量。

## 支持范围与验收

支持 Chrome 116+、带原生字幕的普通公开 `youtube.com/watch` 页面。YouTube 页面与字幕接口可能变化；直接获取失败时，先打开“显示文字记录”再重试，或选择 Supadata 备用。无字幕视频需要额外语音转录，本版没有实现。Shorts、直播和受限视频未验证。

验证命令：`npm test`、`npm run check`、`npm run package`。测试通过只能说明本地逻辑通过；真实 Key、额度、视频页面仍要实测。安装后用真实视频检查字幕、时间戳跳转和双语翻译。

完整数据流见 [PRIVACY.md](PRIVACY.md)，密钥与安装说明见 [SECURITY.md](SECURITY.md)。

## 后续维护

用户要求修改本项目后，编程 Agent 在验证通过后自动提交并推送到这个 Fork。[AGENTS.md](AGENTS.md) 记录了这项持续授权。源码同步不会自动重载已安装的扩展，也不会创建后台同步任务。

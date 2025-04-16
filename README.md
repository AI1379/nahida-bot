# Nahida Bot

> ![Avatar](assets/NahidaAvatar1.jpg)
>
> 这是**摩诃善法大吉祥智慧主**，智慧之神**布耶尔**，须弥的**草神大人**，你敢和她对视五秒吗？

这是一个应群友要求做的 QQ 机器人，基于 [nonebot2](https://nonebot.dev)

## 功能

- [x] 基础功能
- [x] 自动批准加好友/加群申请
    - [ ] 引入[`nonebot-plugin-add-friends`](https://github.com/hakunomiko/nonebot-plugin-add-friends)
- [x] pixiv搜索
    - [x] AI 设置
    - [x] Token Pool
- [ ] 漫画搜索下载
- [x] 基于DeepSeek-R1/SiliconFlow的角色扮演
    - [x] 基础功能
    - [x] 持续化记忆
    - [ ] 长期记忆筛选
- [x] 权限控制
    - [ ] Bug: 权限查询失败
- [x] 心跳包
- [x] 并发处理
    - [x] Pixiv 异步下载
    - [x] OpenAI API 异步处理
- [ ] Bug: 日志处理
- [ ] 基于 StableDiffusion 的图像生成
- [ ] 搜图

## 配置

由于 nonebot 不知为何加载 `.env` 文件有延迟，因此我们选择使用一个额外的 `config.json` 文件来配置。这个文件的路径可以在
`.env` 文件中配置。

### PixivBot

你需要通过 `gppt` 包来获取 pixiv 的 `refresh_token`，这个包需要使用 `ChromeDriver`。所以请确保你已经安装了 `Chrome`。

由于 pixiv 可能会有 reCAPTCHA 验证，因此你需要修改 `gppt` 包中的一个 `timeout`
参数。具体参考 [gppt issue #183](https://github.com/eggplants/get-pixivpy-token/issues/183)。

此外，你也可以使用根目录的 `get_token.py` 来获取 `token`。

## License

DO WHAT THE FUCK YOU WANT TO.

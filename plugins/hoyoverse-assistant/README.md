# HoYoverse Assistant

基于 [`genshin.py`](https://github.com/seriaati/genshin.py) 的多游戏查询插件，
目前覆盖《原神》《崩坏：星穹铁道》和《绝区零》。

## 安装

在 Nahida Bot 仓库中执行：

```powershell
uv sync --extra hoyoverse-assistant
```

在 `config.yaml` 顶层启用插件：

```yaml
hoyoverse-assistant:
  enabled: true
  region: cn
  language: zh-cn
  request_timeout_seconds: 20
  max_concurrency: 2
  qr_login_ttl_seconds: 180
  include_real_time_notes: true
```

然后私聊机器人发送 `/米游社登录`，使用米游社 App 扫描机器人返回的二维码并确认，
再发送 `/米游社登录确认`。不要在任何命令中粘贴 Cookies；命令文本可能进入运行日志。

登录凭据绑定到稳定的渠道用户账号（例如 `milky:user:<QQ号>`），不绑定到当前群聊地址。
因此同一用户可以私聊登录后在不同群中查询，但不同群成员不会共享登录态。凭据进入
插件隔离的 opaque secret store；普通 `plugin_data` 只保存非敏感的 UID 绑定。

## 命令

```text
/米游社登录               # 仅私聊
/米游社登录确认           # 仅私聊
/米游社退出               # 仅私聊
/米游社绑定 <原神|铁道|绝区零> <UID>
/米游社解绑 [原神|铁道|绝区零]
/我的米游社

/原神状态
/铁道状态
/绝区零状态

/原神深渊 [本期|上期]
/铁道挑战 <混沌|虚构|末日> [本期|上期]
/绝区零挑战 <式舆|危局|湮灭> [本期|上期]

/原石月报 [1-12]
/星琼月报 [1-12]
/菲林月报 [1-12|YYYYMM]
```

扫码登录成功后会发现《原神》《崩坏：星穹铁道》《绝区零》账号，并为每个游戏自动绑定
等级最高的角色。手动 `/米游社绑定` 也会校验 UID 确实属于当前用户的米游社登录。

## 当前边界

- 当前版本先提供跨渠道一致的文本报告；图片卡片渲染可作为后续独立表现层加入。
- `genshin.py` 当前只提供米游社国服二维码登录，因此 `region: os` 暂不支持登录；
  海外服需要后续接入 HoYoLAB 的安全授权流程。
- opaque secret store 不提供列举接口，但当前与 Provider 凭据一样以明文保存在 SQLite；
  部署者必须用文件系统权限保护数据库和备份。后续可接操作系统密钥环或专用 vault。
- 暂不提供自动签到；签到属于写操作，还需要独立的授权、风控和 Geetest 处理。

## 开发验证

插件测试与主项目测试隔离，安装可选依赖后显式运行：

```powershell
uv sync --dev --extra hoyoverse-assistant
uv run pytest plugins/hoyoverse-assistant/tests
```

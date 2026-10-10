# Code Quality Review

## Summary

本轮将 ASR 的模型接口、HTTP 传输和部署位置分开，并为 Gateway 接入真实回复及现有 TTS；主要检查取消竞态、音频资源边界与被打断回复的上下文处理。

## Overall Assessment

| Metric | Score |
|--------|-------|
| Overall | 82.37/100（507 文件扫描快照，含既有代码） |
| Files Analyzed | 全仓 507；最终 voice 模块定向重扫 8 |
| Critical Issues | 人工确认尚未解决：0；自动结果仍有正则解析误报 |
| Voice runtime after refactor | 86.17/100；最大圈复杂度 29 → 11 |

工具为 fuck-u-code 2.2.2。环境缺失 Tree-sitter WASM，回退到正则解析；报告未提供 locations。
Vue 面板不在工具覆盖内。对函数边界另以 TypeScript AST 核对，指标不是完整安全审计结论。

## Key Issues (sorted by severity)

- **VoiceLabRuntime.handle（L78）**：新增转写分支后最大圈复杂度达到 29，已拆分为路由、
  `_handle_turn_event`（L98）、`_transcript`（L119）、`_finish_playback`（L144）。重扫最大值 11。
- **VoiceLabClient**：正则工具把多个方法误算为一个 231 行、CC=55 的函数。AST 核实最长方法
  `onMessage`（L215–253）为 39 行，`onAudio` 为 36 行。ASR、音频解码、设备及协议已有独立模块。
- **Asr host 的 native inference**：HTTP 取消不会终止原生推理。线程锁的释放放在实际推理的
  finally 中，防止旧任务未完成就启动第二个推理；客户端只重试 429 busy，受总超时和取消控制。
- **VoiceArtifactPlayback.play**：完整文件解码是异步的；generation 在解码返回和每次喂入前校验。
  用迟到解码与定时喂入取消测试验证，避免旧音频恢复播放。
- **错误处理指标**：工具不能识别消息回调、路由及 provider 的跨方法异常边界。已人工核对
  ASR 超时/取消、401/422/429、转写失效、模型/TTS 失败、解码失败、断线和资源释放路径。

## Refactoring Plan

1. 已完成：拆分 VoiceLabRuntime 的转写和播放确认分支，重新运行相关测试。
2. 已完成：将本地停播安排在 ASR 适配器开轮次之前，保持媒体急停的优先级。
3. 已完成：独立 ASR token、部署地址、批量能力声明；保留未来流式 partial 回调。
4. 后续：真实中文、真人麦克风、生产 TTS 及 Tauri 声卡验收；不把当前软件延迟当作声学延迟。

## Security Concerns

未发现本次范围内尚未处理的明确安全问题。ASR 默认仅绑定 loopback，强制独立随机 token，
读取请求体时限制大小，只接受 16kHz mono PCM16 WAV 和有限语言参数。远端部署需运营者配置
HTTPS。客户端禁止 HTTP 重定向，不向 ASR 发送 Gateway 凭据。模型只能收到转写文字，不能
修改 ASR 地址或指定 TTS 文件路径。

Gateway 真实模式拒绝麦克风 PCM，只接受轮次事件和转写。音频容器最大 8MiB、客户端解码后
最多 60 秒；ASR 单段最多 30 秒。通话历史只在内存中保留最多 12 条消息，assistant 内容仅在
确认播完后追加。本轮尚未接工具、KB 或持久 spoken-memory，不进行自动部署。

验证结果：后端相关 66 个测试、Desktop 291 个测试、类型检查和 Vite 构建通过；拆分后又通过
16 个相关后端测试。独立 uv ASR 环境实际启动成功，以 tiny CPU 模型通过 HTTP 正确识别英文
合成样本，单次耗时约 1.5 秒。该结果不覆盖中文质量或生产 Gateway 的真实 TTS 服务。

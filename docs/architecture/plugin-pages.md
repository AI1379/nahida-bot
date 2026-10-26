# 插件管理页与当前轮次媒体

管理页由插件在 `contributes.pages` 中声明，HTML 文件随插件发布。核心负责读取
声明的页面、WebUI 管理员认证和沙箱通信；插件负责数据模型、管理操作与持久化。
表情包库的页面和全部业务代码位于 `nahida_bot/plugins/meme_library/`。

## 页面操作

Python 插件为自己声明的 `webui.admin` 页面注册处理函数：

```python
async def on_load(self):
    self.api.register_page_handler("gallery", self._handle_page_action)

async def _handle_page_action(self, action: str, payload: dict) -> dict:
    # 插件显式分派允许的 action，并验证 payload。
    ...
```

HTML 页面通过宿主注入的接口调用：

```javascript
const result = await window.nahidaPlugin.request("list", { query: "开心" });
```

宿主只接受当前 iframe 的消息，检查页面会话标识、操作名称、请求大小和并发数，
再使用宿主的认证访问 `POST /api/plugins/{plugin_id}/pages/{page_id}/actions`。
插件/页面 ID 来自宿主绑定，不由 iframe 提供。认证凭据不会传给插件页面。
后端只分派已启用插件、已声明管理页的处理函数；停用立即停止分派，卸载清除注册。

页面仍使用 opaque iframe，CSP 保持 `connect-src 'none'`、`form-action 'none'`。
原生表单提交不可用，应使用按钮/键盘事件调用 `nahidaPlugin.request`。
图片预览可由插件返回 `data:` URL，不需要公开图片文件或开放任意 URL 代理。

## 当前轮次附件

`api.get_current_attachments()` 返回当前 Agent 轮次实际收到的规范化附件元组，
没有活动会话时为空。`await api.resolve_current_attachment(index)` 使用零基附件索引，
返回已缓存文件或调用该轮次来源通道的 `download_media`，需要 `temp` 读取权限。
接口不接受任意本地路径、URL 或历史媒体 ID。

插件自行决定如何使用媒体。例如 `meme_collect` 将图片索引转换为真实附件索引，
限制来源会话、图片大小、收藏冷却和容量，再复制原图到插件图库并记录来源。
通用媒体接口不承担表情识别或收藏策略。

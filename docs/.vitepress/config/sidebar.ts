import type { DefaultTheme } from "vitepress";

export const sidebar: DefaultTheme.Sidebar = {
  "/guide/": [
    {
      text: "指南",
      items: [
        { text: "快速开始", link: "/guide/getting-started" },
        { text: "插件 API 参考", link: "/guide/plugin-api" },
        { text: "Workspace 文件", link: "/guide/workspace-files" },
        { text: "开发规范", link: "/guide/development" },
        { text: "配置参考", link: "/guide/configuration" },
      ],
    },
  ],
  "/architecture/": [
    {
      text: "概览",
      link: "/architecture/",
    },
    {
      text: "核心",
      items: [
        {
          text: "目录结构",
          link: "/architecture/directory-structure",
        },
        { text: "运行时流程", link: "/architecture/runtime-flows" },
        { text: "事件系统", link: "/architecture/event-system" },
        { text: "数据与状态", link: "/architecture/data-and-state" },
        { text: "配置类", link: "/architecture/config-classes" },
        {
          text: "优先级与策略",
          link: "/architecture/priorities-and-strategy",
        },
      ],
    },
    {
      text: "Agent 与 Workspace",
      items: [
        {
          text: "Provider 架构",
          link: "/architecture/provider-architecture",
        },
        { text: "Agent 编排", link: "/architecture/agent-orchestration" },
        { text: "沙箱安全", link: "/architecture/sandbox-security" },
        { text: "模型上下文预算", link: "/architecture/model-context-budget" },
        { text: "模型路由", link: "/architecture/model-routing" },
      ],
    },
    {
      text: "插件与 Channel",
      items: [
        { text: "插件系统", link: "/architecture/plugin-system" },
        { text: "Channel 插件", link: "/architecture/channel-plugin" },
        {
          text: "安全与可观测性",
          link: "/architecture/security-observability",
        },
      ],
    },
  ],
  "/design/": [
    {
      text: "设计文档",
      items: [
        { text: "WebUI 设计", link: "/design/webui-design" },
        { text: "OneBot Channel", link: "/design/onebot-channel" },
        { text: "Agent Core", link: "/design/agent-core" },
        { text: "记忆系统", link: "/design/memory-system" },
        { text: "记忆作用域", link: "/design/memory-scoping" },
        {
          text: "Cron 与 WebAPI 优化",
          link: "/design/cron-and-webapi-optimization",
        },
        {
          text: "聊天地址与会话 ID",
          link: "/design/chat-address-and-session-id",
        },
        {
          text: "跨会话消息",
          link: "/design/cross-session-messaging",
        },
        { text: "运行时设置", link: "/design/runtime-settings" },
        {
          text: "工具产出的图片/媒体",
          link: "/design/tool-produced-image-media-design",
        },
      ],
    },
  ],
};

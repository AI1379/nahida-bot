import type { DefaultTheme } from "vitepress";

export const sidebar: DefaultTheme.Sidebar = {
  "/guide/": [
    {
      text: "指南",
      items: [
        { text: "快速开始", link: "/guide/getting-started" },
        { text: "Workspace 文件", link: "/guide/workspace-files" },
        { text: "开发规范", link: "/guide/development" },
        { text: "配置参考", link: "/guide/configuration" },
      ],
    },
  ],
  "/plugin-api/": [
    {
      text: "插件 API",
      items: [
        { text: "概览", link: "/plugin-api/" },
        { text: "教程", link: "/plugin-api/tutorial" },
        { text: "API 参考", link: "/plugin-api/reference" },
      ],
    },
    {
      text: "Nahida Bot SDK API",
      collapsed: true,
      items: [
        { text: "总览", link: "/plugin-api/auto/" },
        { text: "BotAPI 协议", link: "/plugin-api/auto/api" },
        { text: "消息类型", link: "/plugin-api/auto/messaging" },
        { text: "事件系统", link: "/plugin-api/auto/events" },
        { text: "命令相关", link: "/plugin-api/auto/commands" },
        { text: "Plugin 基类", link: "/plugin-api/auto/plugin" },
        { text: "Manifest 清单", link: "/plugin-api/auto/manifest" },
        { text: "聊天地址与会话", link: "/plugin-api/auto/chat-address" },
        { text: "测试工具", link: "/plugin-api/auto/testing" },
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
        {
          text: "Agent Loop 审计（#21 / #24）",
          link: "/architecture/agent-loop-context-audit",
        },
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
        {
          text: "主动入话题",
          link: "/design/conversation-joiner",
        },
        { text: "运行时设置", link: "/design/runtime-settings" },
        {
          text: "工具产出的图片/媒体",
          link: "/design/tool-produced-image-media-design",
        },
        {
          text: "GPT-SoVITS 语音输出",
          link: "/design/gpt-sovits-voice",
        },
        {
          text: "Live2D 动作智能层",
          link: "/design/live2d-motion-intelligence",
        },
        {
          text: "AgentLoop 改造计划",
          link: "/design/agent-loop-repair-plan",
        },
      ],
    },
  ],
};

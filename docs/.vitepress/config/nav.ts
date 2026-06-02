import type { DefaultTheme } from "vitepress";

export const nav: DefaultTheme.NavItem[] = [
  { text: "指南", link: "/guide/getting-started", activeMatch: "/guide/" },
  {
    text: "插件 API",
    link: "/plugin-api/",
    activeMatch: "/plugin-api/",
  },
  {
    text: "架构",
    link: "/architecture/",
    activeMatch: "/architecture/",
  },
  {
    text: "设计文档",
    link: "/design/onebot-channel",
    activeMatch: "/design/",
  },
  { text: "路线图", link: "/ROADMAP" },
  {
    text: "更多",
    items: [
      { text: "配置参考", link: "/guide/configuration" },
    ],
  },
];

import { defineConfig } from "vitepress";
import { nav } from "./config/nav";
import { sidebar } from "./config/sidebar";
import taskCheckbox from "markdown-it-task-checkbox";

export default defineConfig({
  lang: "zh-CN",
  title: "Nahida Bot",
  description: "Agent-first Python LLM chatbot framework",

  head: [["link", { rel: "icon", href: "/assets/NahidaAvatar3.png" }]],

  lastUpdated: true,
  cleanUrls: true,

  themeConfig: {
    logo: "/assets/NahidaAvatar3.png",
    siteTitle: "Nahida Bot",

    nav,
    sidebar,

    socialLinks: [
      {
        icon: "github",
        link: "https://github.com/AI1379/nahida-bot",
      },
    ],

    search: {
      provider: "local",
    },

    editLink: {
      pattern:
        "https://github.com/AI1379/nahida-bot/edit/main/docs/:path",
      text: "在 GitHub 上编辑此页",
    },

    docFooter: {
      prev: "上一页",
      next: "下一页",
    },

    outline: {
      label: "本页目录",
      level: [2, 3],
    },

    lastUpdated: {
      text: "最后更新于",
    },

    returnToTopLabel: "回到顶部",
    sidebarMenuLabel: "菜单",
    darkModeSwitchLabel: "主题",
    lightModeSwitchTitle: "切换到浅色模式",
    darkModeSwitchTitle: "切换到深色模式",
  },

  markdown: {
    lineNumbers: true,
    config: (md) => {
      md.use(taskCheckbox, { disabled: true });
    },
  },
});

import type { PluginSurfaceContribution } from "@/domain/pluginSurface";

const commonView = {
  text: "",
  status: "",
  detail: "",
  expiresAt: "",
  progress: null,
  items: [],
  tone: "neutral" as const,
};

/** Development-only fixtures for checking every host slot without a Gateway. */
export function createPluginSurfacePreview(): PluginSurfaceContribution[] {
  return [
    {
      ownerPluginId: "preview.schedule",
      id: "today",
      target: "desktop.home",
      kind: "card",
      priority: 20,
      source: "local",
      view: {
        ...commonView,
        title: "今日安排",
        text: "上午整理插件协议，下午完成桌面端验收。",
        tone: "info",
      },
    },
    {
      ownerPluginId: "preview.ledger",
      id: "month",
      target: "desktop.sidebar",
      kind: "progress",
      priority: 10,
      source: "local",
      view: {
        ...commonView,
        title: "本月预算",
        text: "已使用 42%",
        progress: 0.42,
        tone: "success",
      },
    },
    {
      ownerPluginId: "preview.focus",
      id: "timer",
      target: "pet.overlay",
      kind: "countdown",
      priority: 50,
      source: "local",
      view: {
        ...commonView,
        title: "专注",
        status: "进行中",
        detail: "1/4",
        expiresAt: new Date(Date.now() + 25 * 60_000).toISOString(),
        tone: "info",
      },
    },
    {
      ownerPluginId: "preview.schedule",
      id: "next",
      target: "pet.drawer",
      kind: "list",
      priority: 20,
      source: "local",
      view: {
        ...commonView,
        title: "接下来",
        items: [
          { text: "整理账本", detail: "14:00", completed: false },
          { text: "散步", detail: "18:30", completed: false },
        ],
      },
    },
  ];
}

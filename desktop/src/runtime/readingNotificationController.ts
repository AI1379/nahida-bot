import { watch } from "vue";
import type { useDesktopStore } from "@/stores/desktop";
import { acceptReadingNotification, readNotificationInbox } from "@/services/readingNotifications";

/** Reading notifications never enter PresentationPlanner or speech playback. */
export function useReadingNotifications(store: ReturnType<typeof useDesktopStore>) {
  function scopeKey() {
    const { gatewayWsUrl, nodeId, nodeToken } = store.gatewayConnection;
    return JSON.stringify([gatewayWsUrl, nodeId, nodeToken]);
  }

  watch(() => store.connected, async (connected) => {
    store.readingBubble = null;
    store.readingInbox = [];
    if (!connected) return;
    const scope = scopeKey();
    try {
      const inbox = await readNotificationInbox(scope);
      if (store.connected && scope === scopeKey()) {
        const merged = new Map([...inbox, ...store.readingInbox].map((item) => [item.id, item]));
        store.readingInbox = [...merged.values()].slice(-200);
      }
    } catch (error) { store.persistenceError = `通知缓存读取失败: ${String(error)}`; }
  });

  return async (args: Record<string, unknown>) => {
    const scope = scopeKey();
    const result = await acceptReadingNotification(args, scope, (notification) => {
      if (store.connected && scopeKey() === scope) store.openReadingBubble(notification);
    });
    return { ok: true as const, result };
  };
}

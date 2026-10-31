import { invoke, isTauri } from "@tauri-apps/api/core";

/** The native connection supplies identity; arguments cannot select another user. */
export async function callPluginService(
  service: string, operation: string, args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (!isTauri()) throw new Error("请连接 Desktop 后操作服务器任务。");
  const response = await invoke<{
    ok: boolean; payload?: Record<string, unknown>; error?: { message: string };
  }>("gateway_node_plugin_call", {
    payload: { service, operation, arguments: args },
  });
  if (!response.ok) throw new Error(response.error?.message ?? "插件操作失败");
  return response.payload ?? {};
}

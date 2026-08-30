import type { CapabilityExecutionResult } from "@/domain/runtime";
import type { DesktopPluginHandler } from "./desktopPluginContract";

export function validateHandlerSet(
  pluginId: string,
  kind: string,
  declared: string[],
  handlers: Record<string, DesktopPluginHandler>,
): void {
  const declaredNames = new Set(declared);
  for (const name of Object.keys(handlers)) {
    if (!declaredNames.has(name)) {
      throw new Error(`Plugin ${pluginId} registered undeclared ${kind} ${name}`);
    }
  }
  for (const name of declaredNames) {
    if (!handlers[name]) {
      throw new Error(`Plugin ${pluginId} did not register declared ${kind} ${name}`);
    }
  }
}

export function pluginError(
  code: string,
  message: string,
): CapabilityExecutionResult {
  return {
    ok: false,
    error: { code, message, retryable: false },
  };
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

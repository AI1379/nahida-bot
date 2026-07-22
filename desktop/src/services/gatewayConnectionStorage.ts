import { createTypedStorage } from "@/utils/storage";
import { sanitizeGatewayConnectionSettings } from "@/domain/gatewayConnection";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";

const storage = createTypedStorage<GatewayConnectionSettings>(
  "nahida.desktop.gateway.connection.v1",
  sanitizeGatewayConnectionSettings,
);

export const readPersistedGatewayConnection = storage.read;
export function writePersistedGatewayConnection(
  settings: GatewayConnectionSettings,
): void {
  storage.write({
    ...settings,
    nodeToken: "",
    adminBearerToken: "",
  });
}
export const clearPersistedGatewayConnection = storage.clear;

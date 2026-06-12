import { isTauri } from "@tauri-apps/api/core";
import { emitTo, listen, type UnlistenFn } from "@tauri-apps/api/event";

import type {
  DesktopRuntimeSnapshot,
  PetWindowCommand,
} from "@/domain/desktopWindowProtocol";

const runtimeSnapshotEvent = "nahida://desktop/runtime-snapshot";
const petCommandEvent = "nahida://desktop/pet-command";

export async function listenForPetCommands(
  handler: (command: PetWindowCommand) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<PetWindowCommand>(petCommandEvent, (event) => {
    handler(event.payload);
  });
}

export async function listenForRuntimeSnapshots(
  handler: (snapshot: DesktopRuntimeSnapshot) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<DesktopRuntimeSnapshot>(runtimeSnapshotEvent, (event) => {
    handler(event.payload);
  });
}

export async function publishRuntimeSnapshot(
  snapshot: DesktopRuntimeSnapshot,
): Promise<void> {
  if (!isTauri()) return;
  await emitTo("pet", runtimeSnapshotEvent, snapshot);
}

export async function sendPetWindowCommand(
  command: PetWindowCommand,
): Promise<void> {
  if (!isTauri()) return;
  await emitTo("main", petCommandEvent, command);
}

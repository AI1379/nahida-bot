import { isTauri } from "@tauri-apps/api/core";
import { emitTo, listen, type UnlistenFn } from "@tauri-apps/api/event";

import type {
  DesktopRuntimeSnapshot,
  PetWindowCommand,
} from "@/domain/desktopWindowProtocol";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";

const runtimeSnapshotEvent = "nahida://desktop/runtime-snapshot";
const petCommandEvent = "nahida://desktop/pet-command";
const lipSyncEnergyEvent = "nahida://desktop/lip-sync-energy";
const motionPlaybackEvent = "nahida://desktop/motion-playback";

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

export async function publishLipSyncEnergy(energy: number): Promise<void> {
  if (!isTauri()) return;
  await emitTo("pet", lipSyncEnergyEvent, Math.max(0, Math.min(1, energy)));
}

export async function listenForLipSyncEnergy(
  handler: (energy: number) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<number>(lipSyncEnergyEvent, (event) => {
    handler(Math.max(0, Math.min(1, event.payload)));
  });
}

export async function publishMotionPlayback(
  playback: MotionPlaybackSummary,
): Promise<void> {
  if (!isTauri()) return;
  await emitTo("main", motionPlaybackEvent, playback);
}

export async function listenForMotionPlaybacks(
  handler: (playback: MotionPlaybackSummary) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<MotionPlaybackSummary>(motionPlaybackEvent, (event) => {
    handler(event.payload);
  });
}

export async function sendPetWindowCommand(
  command: PetWindowCommand,
): Promise<void> {
  if (!isTauri()) return;
  await emitTo("main", petCommandEvent, command);
}

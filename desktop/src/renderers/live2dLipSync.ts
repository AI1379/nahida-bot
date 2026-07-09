import { live2dRuntimeDefaults } from "@/config/desktopRuntimeDefaults";
import type { Live2DModelManifest } from "@/domain/live2d";
import { commonLive2DParameterIds } from "@/domain/live2dBaseMotion";

export function lipSyncParameterIdsForManifest(
  manifest: Live2DModelManifest,
): string[] {
  return manifest.lipSync.parameterIds.length
    ? manifest.lipSync.parameterIds
    : commonLive2DParameterIds.mouthOpen;
}

export function lipSyncValueForSpeakingPulse(now: number): number {
  const pulse =
    (Math.sin(now / live2dRuntimeDefaults.lipSync.pulsePeriodMs) + 1) / 2;
  return (
    live2dRuntimeDefaults.lipSync.minimumOpen +
    pulse * live2dRuntimeDefaults.lipSync.openRange
  );
}

export function scaleLipSyncParameterValue(
  parameterId: string,
  value: number,
): number {
  return /form/i.test(parameterId)
    ? value * live2dRuntimeDefaults.lipSync.mouthFormScale
    : value;
}

import type { RenderMode } from "@/domain/runtime";

export type Live2DRendererProfile = "pet" | "preview";

export const live2dRuntimeDefaults = {
  fpsByMode: {
    suspended: 0,
    idle: 15,
    speaking: 30,
    active: 60,
  } satisfies Record<RenderMode, number>,
  canvas: {
    maxDevicePixelRatio: 1.5,
    antialias: false,
    powerPreference: "low-power" as const,
  },
  layout: {
    anchorX: 0.5,
    anchorY: 0.5,
    fitScale: 0.82,
    positionXRatio: 0.5,
    positionYRatio: 0.58,
  },
  motion: {
    boostTailMs: 180,
  },
  lipSync: {
    pulsePeriodMs: 150,
    minimumOpen: 0.12,
    openRange: 0.32,
    mouthFormScale: 0.25,
  },
} as const;

export const live2dRendererProfiles = {
  pet: {
    maxDevicePixelRatio: live2dRuntimeDefaults.canvas.maxDevicePixelRatio,
    fpsByMode: live2dRuntimeDefaults.fpsByMode,
  },
  preview: {
    maxDevicePixelRatio: 1,
    fpsByMode: {
      suspended: 0,
      idle: 10,
      speaking: 24,
      active: 30,
    },
  },
} as const satisfies Record<
  Live2DRendererProfile,
  {
    maxDevicePixelRatio: number;
    fpsByMode: Record<RenderMode, number>;
  }
>;

export function resolveLive2DTargetFps(
  profile: Live2DRendererProfile,
  mode: RenderMode,
  motionBoosted: boolean,
): number {
  if (mode === "suspended") return 0;
  const fpsByMode = live2dRendererProfiles[profile].fpsByMode;
  return motionBoosted ? fpsByMode.active : fpsByMode[mode];
}

export const presentationTimingDefaults = {
  minimumSegmentDurationMs: 1400,
  millisecondsPerCharacter: 85,
} as const;

export const desktopWindowDefaults = {
  width: 420,
  height: 620,
  edge: "right" as const,
  exposedPx: 42,
  alwaysOnTop: true,
  clickThrough: true,
  interactionMode: "click_through" as const,
  performanceMode: "balanced" as const,
  /** Duration of the emerge/retreat window slide animation. */
  slideDurationMs: 320,
  /**
   * Fallback for advancing emerging/retreating when the pet window never
   * reports `transition_done` (e.g. browser dev mode without Tauri).
   */
  transitionFallbackMs: 1200,
  autoRetreatMs: 8000,
  errorRetreatMs: 10000,
  /** Exit chat (and click-through again) after this much inactivity. */
  chatIdleTimeoutMs: 45000,
} as const;

export const ttsDefaults = {
  language: "zh-CN",
  voiceUri: "",
  preferFemale: true,
  rate: 1,
  pitch: 0,
  volume: 1,
} as const;

export const petProximityDefaults = {
  pollIntervalMs: 250,
  wakeDistancePx: 96,
  hideDistancePx: 220,
  /** Minimum gap between pointer_activity commands sent to the main window. */
  activityThrottleMs: 2000,
} as const;

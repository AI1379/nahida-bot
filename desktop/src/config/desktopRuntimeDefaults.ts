import type { RenderMode } from "@/domain/runtime";

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
} as const;

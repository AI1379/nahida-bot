import type { VoicePlan } from "@/domain/displayPlan";

export interface SpeechArtifactRef {
  artifactId: string;
  downloadUrl: string;
  mimeType: string;
  durationMs?: number;
  sizeBytes?: number;
  expiresAt?: string;
}

export interface AudioPlaybackRequest {
  text: string;
  voice?: VoicePlan;
  artifact?: SpeechArtifactRef;
}

export interface PreloadedAudioHandle {
  /** Provider-reported artifact duration, used to time motion planning. */
  durationMs?: number;
  play(signal: AbortSignal): Promise<void>;
  dispose(): void;
}

export interface AudioPlaybackAdapter {
  isAvailable(): boolean;
  play(request: AudioPlaybackRequest, signal: AbortSignal): Promise<void>;
  stop(): void;
  /**
   * Fetch audio without playing it. Returns a handle that can be played
   * later via {@link PreloadedAudioHandle.play}. The caller may fetch the next
   * segment while the current handle is playing.
   */
  fetch(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<PreloadedAudioHandle>;
}

export class AudioPlaybackAbortedError extends Error {
  constructor() {
    super("Audio playback was aborted.");
    this.name = "AudioPlaybackAbortedError";
  }
}

export function isAudioPlaybackAborted(error: unknown): boolean {
  return error instanceof AudioPlaybackAbortedError;
}

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

export interface AudioPlaybackAdapter {
  isAvailable(): boolean;
  play(request: AudioPlaybackRequest, signal: AbortSignal): Promise<void>;
  stop(): void;
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

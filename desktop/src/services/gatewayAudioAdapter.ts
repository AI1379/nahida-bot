/**
 * Gateway TTS adapter that calls POST /api/speech/jobs on the Nahida Gateway
 * and streams back the cached audio via /api/media/speech/{artifact_id}.
 *
 * Implements the ``AudioPlaybackAdapter`` contract so the
 * ``SpeechPlaybackCoordinator`` can switch between system Web Speech and
 * remote GPT-SoVITS transparently.
 */

import type {
  AudioPlaybackAdapter,
  AudioPlaybackRequest,
  PreloadedAudioHandle,
} from "@/services/audioPlaybackAdapter";
import { AudioPlaybackAbortedError } from "@/services/audioPlaybackAdapter";
import { gatewayWsUrlToHttpBase } from "@/domain/gatewayConnection";
import type { TtsSettings } from "@/domain/config";
import {
  MediaElementEnergyMonitor,
  type AudioEnergyListener,
} from "@/services/audioEnergyEnvelope";

type AdminBearerProvider = () => string;
type TtsSettingsProvider = () => TtsSettings;

interface SpeechJobResponse {
  artifact_id: string;
  download_url: string;
  mime_type: string;
  duration_ms: number;
}

interface ActivePlayback {
  audio: HTMLAudioElement;
  abortController: AbortController;
  energyMonitor: MediaElementEnergyMonitor | null;
}

export class GatewayAudioAdapter implements AudioPlaybackAdapter {
  private readonly getAdminBearer: AdminBearerProvider;
  private readonly getSettings: TtsSettingsProvider;
  private readonly gatewayWsUrl: () => string;
  private readonly onEnergy: AudioEnergyListener;
  private active: ActivePlayback | null = null;
  /** In-memory blob-URL cache keyed by artifact_id. */
  private readonly blobCache = new Map<string, string>();

  constructor(
    getAdminBearer: AdminBearerProvider,
    getSettings: TtsSettingsProvider,
    gatewayWsUrl: () => string,
    onEnergy: AudioEnergyListener = () => {},
  ) {
    this.getAdminBearer = getAdminBearer;
    this.getSettings = getSettings;
    this.gatewayWsUrl = gatewayWsUrl;
    this.onEnergy = onEnergy;
  }

  isAvailable(): boolean {
    const bearer = this.getAdminBearer();
    return Boolean(bearer);
  }

  async play(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<void> {
    const handle = await this.fetch(request, signal);
    await handle.play(signal);
  }

  async fetch(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<PreloadedAudioHandle> {
    const bearer = this.getAdminBearer();
    if (!bearer) {
      throw new Error("Admin API token is required for gateway TTS.");
    }
    if (signal.aborted) throw new AudioPlaybackAbortedError();

    const httpBase = gatewayWsUrlToHttpBase(this.gatewayWsUrl());
    if (!httpBase) {
      throw new Error("Gateway URL is not configured.");
    }

    const settings = this.getSettings();
    const jobResp = await this.requestSpeechJob(
      httpBase,
      bearer,
      request,
      settings,
      signal,
    );

    const audioBlob = await this.fetchAudio(
      httpBase,
      bearer,
      jobResp.download_url,
      signal,
    );
    const blobUrl = URL.createObjectURL(audioBlob);
    this.blobCache.set(jobResp.artifact_id, blobUrl);

    const audio = new Audio(blobUrl);
    let disposed = false;
    let energyMonitor: MediaElementEnergyMonitor | null = null;

    return {
      durationMs: jobResp.duration_ms,
      play: async (playSignal: AbortSignal) => {
        if (disposed) throw new AudioPlaybackAbortedError();
        if (playSignal.aborted) throw new AudioPlaybackAbortedError();
        this.stop();
        const abortController = new AbortController();
        energyMonitor = this.createEnergyMonitor(audio);
        this.active = { audio, abortController, energyMonitor };
        const linkedSignal = this.linkedAbort(playSignal, abortController.signal);
        try {
          await energyMonitor?.start();
          await this.playAudio(audio, linkedSignal, jobResp.duration_ms);
        } finally {
          energyMonitor?.stop();
          if (this.active?.audio === audio) this.active = null;
        }
      },
      dispose: () => {
        if (disposed) return;
        disposed = true;
        void energyMonitor?.dispose().catch(() => undefined);
        energyMonitor = null;
        audio.src = "";
        URL.revokeObjectURL(blobUrl);
        this.blobCache.delete(jobResp.artifact_id);
      },
    };
  }

  stop(): void {
    const prev = this.active;
    this.active = null;
    if (prev) {
      prev.abortController.abort();
      prev.energyMonitor?.stop();
      prev.audio.pause();
      prev.audio.src = "";
    }
  }

  dispose(): void {
    this.stop();
    for (const blobUrl of this.blobCache.values()) {
      URL.revokeObjectURL(blobUrl);
    }
    this.blobCache.clear();
  }

  private async requestSpeechJob(
    httpBase: string,
    bearer: string,
    request: AudioPlaybackRequest,
    _settings: TtsSettings,
    signal: AbortSignal,
  ): Promise<SpeechJobResponse> {
    const body: Record<string, unknown> = {
      text: request.text,
    };
    if (request.voice?.style) body.style = request.voice.style;
    if (request.voice?.speed) body.speed = request.voice.speed;
    if (request.voice?.pitch) body.pitch = request.voice.pitch;
    // TODO: voice selection is currently hardcoded to the gateway default.
    // When multi-voice / persona-bound voice routing is implemented, pass the
    // resolved voice name from the DisplayPlan segment here (e.g. via
    // request.voice.name). The gateway SpeechService.resolve_voice will then
    // pick the matching provider voice config.
    // Voice is intentionally omitted — let the gateway use its default_voice.
    // Desktop voice field maps to DisplayPlan keyword (not TTS voice name).

    let response: Response;
    try {
      response = await fetch(`${httpBase}/api/speech/jobs`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${bearer}`,
        },
        body: JSON.stringify(body),
        signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new AudioPlaybackAbortedError();
      }
      throw new Error(`TTS gateway unreachable: ${String(error)}`);
    }

    if (!response.ok) {
      const detail = await this.readErrorDetail(response);
      throw new Error(
        `TTS synthesis failed (HTTP ${response.status}): ${detail}`,
      );
    }

    return (await response.json()) as SpeechJobResponse;
  }

  private async fetchAudio(
    httpBase: string,
    bearer: string,
    downloadUrl: string,
    signal: AbortSignal,
  ): Promise<Blob> {
    let response: Response;
    try {
      response = await fetch(`${httpBase}${downloadUrl}`, {
        headers: { authorization: `Bearer ${bearer}` },
        signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new AudioPlaybackAbortedError();
      }
      throw new Error(`TTS audio download failed: ${String(error)}`);
    }

    if (!response.ok) {
      throw new Error(
        `TTS audio download failed (HTTP ${response.status})`,
      );
    }

    return response.blob();
  }

  private async playAudio(
    audio: HTMLAudioElement,
    signal: AbortSignal,
    durationMs: number,
  ): Promise<void> {
    const timeoutMs = Math.max(durationMs + 5000, 30000);
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const timer = window.setTimeout(() => {
        finish(() => reject(new Error("TTS playback timed out.")));
      }, timeoutMs);
      const cleanup = () => {
        signal.removeEventListener("abort", onAbort);
        audio.removeEventListener("ended", onEnd);
        audio.removeEventListener("error", onError);
        window.clearTimeout(timer);
      };
      const finish = (settle: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        settle();
      };
      const onAbort = () => {
        this.stop();
        finish(() => reject(new AudioPlaybackAbortedError()));
      };
      const onEnd = () => finish(resolve);
      const onError = () =>
        finish(() => reject(new Error("Audio playback error.")));
      signal.addEventListener("abort", onAbort, { once: true });
      audio.addEventListener("ended", onEnd, { once: true });
      audio.addEventListener("error", onError, { once: true });
      audio.play().catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        finish(() => reject(new Error(`Audio play() rejected: ${String(error)}`)));
      });
    });
  }

  private linkedAbort(
    ...signals: AbortSignal[]
  ): AbortSignal {
    const controller = new AbortController();
    for (const signal of signals) {
      if (signal.aborted) {
        controller.abort(signal.reason);
        break;
      }
      signal.addEventListener(
        "abort",
        () => controller.abort(signal.reason),
        { once: true },
      );
    }
    return controller.signal;
  }

  private async readErrorDetail(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as Record<string, unknown>;
      if (body && typeof body === "object" && "detail" in body) {
        const detail = body.detail;
        if (typeof detail === "string") return detail;
        if (detail && typeof detail === "object" && "message" in detail) {
          return String(
            (detail as Record<string, unknown>).message ?? detail,
          );
        }
        return String(detail);
      }
    } catch {
      // Fall through to status text.
    }
    return response.statusText || `HTTP ${response.status}`;
  }

  private createEnergyMonitor(
    audio: HTMLAudioElement,
  ): MediaElementEnergyMonitor | null {
    if (typeof AudioContext === "undefined") return null;
    try {
      return new MediaElementEnergyMonitor(audio, this.onEnergy);
    } catch {
      return null;
    }
  }
}

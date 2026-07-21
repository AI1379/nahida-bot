import type {
  AudioPlaybackAdapter,
  AudioPlaybackRequest,
  PreloadedAudioHandle,
} from "@/services/audioPlaybackAdapter";
import { AudioPlaybackAbortedError } from "@/services/audioPlaybackAdapter";
import type { TtsSettings } from "@/domain/config";
import { sanitizeTtsSettings } from "@/services/ttsSettingsStorage";

type UtteranceFactory = (text: string) => SpeechSynthesisUtterance;
type SettingsProvider = () => TtsSettings;

const likelyFemaleVoicePattern =
  /xiaoxiao|xiaoyi|huihui|yaoyao|hsiaochen|hiumaan|ting-ting|hanhan|meijia|sinji|yating|tracy|elsa|zira|samantha|victoria|karen|susan|hazel|catherine|female|woman|女/i;

function defaultSpeechSynthesis(): SpeechSynthesis | null {
  return typeof globalThis.speechSynthesis === "undefined"
    ? null
    : globalThis.speechSynthesis;
}

function defaultUtteranceFactory(): UtteranceFactory | null {
  if (typeof globalThis.SpeechSynthesisUtterance === "undefined") return null;
  return (text) => new globalThis.SpeechSynthesisUtterance(text);
}

function webSpeechPitch(semitones: number | undefined): number {
  const pitch = 2 ** ((semitones ?? 0) / 12);
  return Math.max(0.5, Math.min(2, pitch));
}

function normalizedLanguage(language: string): string {
  return language.trim().replace("_", "-").toLowerCase();
}

function languageMatches(voiceLanguage: string, requestedLanguage: string): boolean {
  const voice = normalizedLanguage(voiceLanguage);
  const requested = normalizedLanguage(requestedLanguage);
  if (!requested) return true;
  return (
    voice === requested ||
    voice.split("-")[0] === requested.split("-")[0]
  );
}

export function selectSystemSpeechVoice(
  voices: SpeechSynthesisVoice[],
  settings: TtsSettings,
): SpeechSynthesisVoice | null {
  const selected = voices.find((voice) => voice.voiceURI === settings.voiceUri);
  if (selected) return selected;

  const languageVoices = voices.filter((voice) =>
    languageMatches(voice.lang, settings.language),
  );
  const candidates = languageVoices.length ? languageVoices : voices;
  if (!candidates.length) return null;

  return [...candidates].sort((left, right) => {
    const femaleDifference =
      Number(likelyFemaleVoicePattern.test(right.name)) -
      Number(likelyFemaleVoicePattern.test(left.name));
    if (settings.preferFemale && femaleDifference) return femaleDifference;
    const defaultDifference = Number(right.default) - Number(left.default);
    if (defaultDifference) return defaultDifference;
    return left.name.localeCompare(right.name);
  })[0] ?? null;
}

export class SystemSpeechAdapter implements AudioPlaybackAdapter {
  private readonly synthesis: SpeechSynthesis | null;
  private readonly createUtterance: UtteranceFactory | null;
  private readonly getSettings: SettingsProvider;
  private cancelActive: (() => void) | null = null;

  constructor(
    synthesis = defaultSpeechSynthesis(),
    createUtterance = defaultUtteranceFactory(),
    getSettings: SettingsProvider = () => sanitizeTtsSettings(null),
  ) {
    this.synthesis = synthesis;
    this.createUtterance = createUtterance;
    this.getSettings = getSettings;
  }

  isAvailable(): boolean {
    return this.synthesis !== null && this.createUtterance !== null;
  }

  async play(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<void> {
    return this.playInternal(request, signal);
  }

  async fetch(
    request: AudioPlaybackRequest,
    _signal: AbortSignal,
  ): Promise<PreloadedAudioHandle> {
    const adapter = this;
    return {
      play(playSignal: AbortSignal): Promise<void> {
        return adapter.playInternal(request, playSignal);
      },
      dispose(): void {},
    };
  }

  private async playInternal(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<void> {
    this.stop();
    const synthesis = this.synthesis;
    const createUtterance = this.createUtterance;
    if (!synthesis || !createUtterance) {
      throw new Error("System speech synthesis is unavailable.");
    }
    if (signal.aborted) throw new AudioPlaybackAbortedError();

    const utterance = createUtterance(request.text);
    const settings = sanitizeTtsSettings(this.getSettings());
    const selectedVoice = selectSystemSpeechVoice(
      synthesis.getVoices(),
      settings,
    );
    utterance.voice = selectedVoice;
    utterance.lang = selectedVoice?.lang || settings.language;
    utterance.rate = request.voice?.speed ?? settings.rate;
    utterance.pitch = webSpeechPitch(
      request.voice?.pitch ?? settings.pitch,
    );
    utterance.volume = settings.volume;

    await new Promise<void>((resolve, reject) => {
      let settled = false;

      const settle = (error?: Error) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", abort);
        utterance.onend = null;
        utterance.onerror = null;
        this.cancelActive = null;
        if (error) {
          reject(error);
        } else {
          resolve();
        }
      };

      const abort = () => {
        synthesis.cancel();
        settle(new AudioPlaybackAbortedError());
      };

      this.cancelActive = () => settle(new AudioPlaybackAbortedError());
      signal.addEventListener("abort", abort, { once: true });
      utterance.onend = () => settle();
      utterance.onerror = (event) => {
        settle(new Error(`System speech failed: ${event.error || "unknown"}`));
      };

      synthesis.speak(utterance);
    });
  }

  async fetch(
    _request: AudioPlaybackRequest,
    _signal: AbortSignal,
  ): Promise<PreloadedAudioHandle> {
    return {
      play: (playSignal) => Promise.resolve(),
      dispose: () => {},
    };
  }

  stop(): void {
    const cancelActive = this.cancelActive;
    this.cancelActive = null;
    this.synthesis?.cancel();
    cancelActive?.();
  }
}

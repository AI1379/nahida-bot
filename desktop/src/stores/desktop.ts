import { defineStore } from "pinia";

import type {
  DisplayEmotion,
  DisplayMotion,
  DisplayPlan,
} from "@/domain/displayPlan";
import { availableModelManifests, mockModelManifest } from "@/domain/live2d";
import type { Live2DModelManifest } from "@/domain/live2d";
import { mockBackend } from "@/services/mockBackend";
import type { MockGatewayEvent } from "@/services/mockBackend";

export interface TranscriptEntry {
  id: string;
  role: "system" | "user" | "assistant";
  text: string;
  at: string;
  displayPlan?: DisplayPlan;
}

export const useDesktopStore = defineStore("desktop", {
  state: () => ({
    connected: false,
    gatewayUrl: "mock://backend",
    sessionId: "desktop:private:mock-user",
    currentEmotion: "neutral" as DisplayEmotion,
    currentMotion: "idle" as DisplayMotion,
    speaking: false,
    activePlan: null as DisplayPlan | null,
    currentSegmentIndex: 0,
    models: availableModelManifests as Live2DModelManifest[],
    selectedModelId: mockModelManifest.id,
    model: mockModelManifest as Live2DModelManifest,
    transcript: [] as TranscriptEntry[],
    unsubscribe: null as (() => void) | null,
  }),
  actions: {
    startMockBackend() {
      if (this.unsubscribe) return;
      this.unsubscribe = mockBackend.subscribe((event) =>
        this.applyGatewayEvent(event),
      );
      mockBackend.connect();
    },
    stopMockBackend() {
      mockBackend.disconnect();
      this.unsubscribe?.();
      this.unsubscribe = null;
    },
    submitUserMessage(text: string) {
      const trimmed = text.trim();
      if (!trimmed) return;
      this.transcript.unshift({
        id: crypto.randomUUID(),
        role: "user",
        text: trimmed,
        at: new Date().toISOString(),
      });
      mockBackend.submitUserMessage(trimmed);
    },
    setSegment(index: number) {
      if (!this.activePlan) return;
      const segment = this.activePlan.segments[index];
      if (!segment) return;
      this.currentSegmentIndex = index;
      this.currentEmotion = segment.emotion ?? "neutral";
      this.currentMotion = segment.motion ?? "speaking";
      this.speaking = true;
    },
    finishSpeaking() {
      this.speaking = false;
      this.currentMotion = "idle";
    },
    selectModel(modelId: string) {
      const model = this.models.find((candidate) => candidate.id === modelId);
      if (!model || model.id === this.selectedModelId) return;
      this.selectedModelId = model.id;
      this.model = model;
      this.currentMotion = "idle";
      this.speaking = false;
    },
    applyGatewayEvent(event: MockGatewayEvent) {
      switch (event.type) {
        case "gateway.connected":
          this.connected = true;
          this.currentEmotion = "happy";
          this.currentMotion = "wave";
          this.transcript.unshift({
            id: crypto.randomUUID(),
            role: "system",
            text: "Mock backend connected.",
            at: event.at,
          });
          break;
        case "gateway.disconnected":
          this.connected = false;
          this.currentEmotion = "offline";
          this.currentMotion = "idle";
          this.speaking = false;
          break;
        case "agent.message.started":
          this.currentEmotion = "thinking";
          this.currentMotion = "idle";
          this.speaking = false;
          break;
        case "agent.message.completed":
          this.activePlan = event.displayPlan;
          this.currentSegmentIndex = 0;
          this.transcript.unshift({
            id: crypto.randomUUID(),
            role: "assistant",
            text: event.displayPlan.text,
            at: event.at,
            displayPlan: event.displayPlan,
          });
          this.setSegment(0);
          break;
        case "plugin.error":
          this.currentEmotion = "error";
          this.currentMotion = "notify";
          this.transcript.unshift({
            id: crypto.randomUUID(),
            role: "system",
            text: event.message,
            at: event.at,
          });
          break;
      }
    },
  },
});

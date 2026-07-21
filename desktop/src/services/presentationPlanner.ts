import { planFromText } from "@/domain/displayPlan";
import type { DesktopEvent, PresentationPlan } from "@/domain/runtime";

let presentationCounter = 0;
function nextPresentationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `presentation-${++presentationCounter}`;
}

export function presentationPlanFromDesktopEvent(
  event: DesktopEvent,
): PresentationPlan | null {
  if (event.type === "message.completed") {
    return {
      id: nextPresentationId(),
      source: event.source,
      targetSessionId: event.sessionId,
      displayPlan: event.displayPlan,
      bubbleText: event.displayPlan.text,
      ttsEnabled: event.displayPlan.segments.some((segment) => Boolean(segment.voice)),
      interruption: "replace",
      createdAt: event.at,
    };
  }

  if (event.type === "notification.error") {
    const displayPlan = planFromText(event.message, "error");
    return {
      id: nextPresentationId(),
      source: event.source,
      targetSessionId: event.sessionId,
      displayPlan,
      bubbleText: displayPlan.text,
      ttsEnabled: false,
      interruption: "replace",
      createdAt: event.at,
    };
  }

  if (event.type === "notification.reminder") {
    const displayPlan = planFromText(event.message, "neutral");
    return {
      id: nextPresentationId(),
      source: event.source,
      targetSessionId: event.sessionId,
      displayPlan,
      bubbleText: displayPlan.text,
      ttsEnabled: false,
      interruption: "queue",
      dedupeKey: event.dedupeKey,
      createdAt: event.at,
    };
  }

  return null;
}

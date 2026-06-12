import { describe, expect, it } from "vitest";

import { createInitialPetRuntimeState } from "./runtime";
import { transitionPetRuntime } from "./petRuntimeMachine";

describe("transitionPetRuntime", () => {
  it("keeps illegal transitions unchanged", () => {
    const hidden = createInitialPetRuntimeState();

    expect(transitionPetRuntime(hidden, "speak", "balanced")).toBe(hidden);
  });

  it("does not pop the window out of hiding on failure", () => {
    const hidden = createInitialPetRuntimeState();
    const peek = transitionPetRuntime(hidden, "peek", "balanced");

    expect(transitionPetRuntime(hidden, "fail", "balanced")).toBe(hidden);
    expect(transitionPetRuntime(peek, "fail", "balanced")).toBe(peek);
  });

  it("moves through emerge and speaking states", () => {
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "balanced");
    const emerged = transitionPetRuntime(emerging, "emerged", "balanced");
    const speaking = transitionPetRuntime(emerged, "speak", "balanced");

    expect(emerging.status).toBe("emerging");
    expect(emerging.motion).toBe("emerge");
    expect(emerged.status).toBe("emerged");
    expect(speaking).toMatchObject({
      status: "speaking",
      renderMode: "speaking",
      speaking: true,
      interactionMode: "click_through",
      clickThrough: true,
    });
  });

  it("makes chat interactive and hiding click-through", () => {
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "active");
    const emerged = transitionPetRuntime(emerging, "emerged", "active");
    const chat = transitionPetRuntime(emerged, "enter_chat", "active");
    const retreating = transitionPetRuntime(chat, "retreat", "active");
    const hiddenAgain = transitionPetRuntime(
      retreating,
      "hide",
      "active",
    );

    expect(chat).toMatchObject({
      status: "chat",
      renderMode: "active",
      interactionMode: "interactive",
      clickThrough: false,
    });
    expect(retreating.motion).toBe("retreat");
    expect(hiddenAgain).toMatchObject({
      status: "hidden",
      renderMode: "suspended",
      interactionMode: "click_through",
      clickThrough: true,
    });
  });

  it("keeps chat interactive while a reply is spoken", () => {
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "balanced");
    const emerged = transitionPetRuntime(emerging, "emerged", "balanced");
    const chat = transitionPetRuntime(emerged, "enter_chat", "balanced");
    const speakingInChat = transitionPetRuntime(chat, "speak", "balanced");
    const finished = transitionPetRuntime(
      speakingInChat,
      "finish_speaking",
      "balanced",
    );

    expect(speakingInChat).toMatchObject({
      status: "chat",
      renderMode: "speaking",
      speaking: true,
      interactionMode: "interactive",
      clickThrough: false,
    });
    expect(finished).toMatchObject({
      status: "chat",
      speaking: false,
      interactionMode: "interactive",
      clickThrough: false,
    });
  });

  it("returns non-chat speech to the emerged click-through state", () => {
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "balanced");
    const emerged = transitionPetRuntime(emerging, "emerged", "balanced");
    const speaking = transitionPetRuntime(emerged, "speak", "balanced");
    const finished = transitionPetRuntime(
      speaking,
      "finish_speaking",
      "balanced",
    );

    expect(finished).toMatchObject({
      status: "emerged",
      speaking: false,
      interactionMode: "click_through",
      clickThrough: true,
    });
  });

  it("keeps a visible pet animating in power saver mode", () => {
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "power_saver");
    const emerged = transitionPetRuntime(emerging, "emerged", "power_saver");

    expect(emerged.renderMode).toBe("idle");
  });

  it("keeps emerging as an animation boundary", () => {
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "balanced");

    for (const signal of ["speak", "enter_chat", "fail"] as const) {
      expect(transitionPetRuntime(emerging, signal, "balanced")).toBe(
        emerging,
      );
    }

    const emerged = transitionPetRuntime(emerging, "emerged", "balanced");
    const failed = transitionPetRuntime(emerged, "fail", "balanced");
    expect(transitionPetRuntime(failed, "enter_chat", "balanced")).toBe(
      failed,
    );
  });
});

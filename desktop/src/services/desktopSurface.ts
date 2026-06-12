import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

export type DesktopSurface = "main" | "pet";

export function currentDesktopSurface(): DesktopSurface {
  if (isTauri()) {
    return getCurrentWindow().label === "pet" ? "pet" : "main";
  }

  return new URLSearchParams(window.location.search).get("window") === "pet"
    ? "pet"
    : "main";
}

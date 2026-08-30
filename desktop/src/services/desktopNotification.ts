import { isTauri } from "@tauri-apps/api/core";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";

export interface DesktopNotification {
  title: string;
  body: string;
}

/** Show a system notification when running in the native desktop shell. */
export async function showDesktopNotification(
  notification: DesktopNotification,
): Promise<boolean> {
  if (!isTauri()) return false;

  try {
    let granted = await isPermissionGranted();
    if (!granted) {
      granted = (await requestPermission()) === "granted";
    }
    if (!granted) return false;

    sendNotification(notification);
    return true;
  } catch (error) {
    console.warn("Unable to show a native desktop notification", error);
    return false;
  }
}

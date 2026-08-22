export type TurnStatus =
  | "submitting"
  | "accepted"
  | "generating"
  | "synthesizing"
  | "playing"
  | "completed"
  | "failed";

/**
 * A durable, user-facing conversation unit. Unlike the diagnostic transcript,
 * a turn remains visible while the Gateway and speech pipeline advance it.
 */
export interface TurnRecord {
  id: string;
  sessionId: string;
  userText: string;
  assistantText: string;
  status: TurnStatus;
  createdAt: string;
  updatedAt: string;
  presentationId?: string;
  error?: string;
}

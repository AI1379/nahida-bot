export const mockDesktopDefaults: {
  gatewayUrl: string;
  sessionId: string;
  delays: {
    messageResponseMs: number;
    llmResultMs: number;
    demoStartedMs: number;
    demoCompletedMs: number;
  };
} = {
  gatewayUrl: "mock://backend",
  sessionId: "desktop:private:mock-user",
  delays: {
    messageResponseMs: 900,
    llmResultMs: 180,
    demoStartedMs: 600,
    demoCompletedMs: 1500,
  },
};

export const mockControlDefaults: {
  message: string;
  llmResult: string;
} = {
  message: "演示一条带 TTS 和 Live2D 表现计划的回复。",
  llmResult: `{
  "text": "今天的计划已经整理好了。先处理配置问题，然后再看桌宠协议。",
  "segments": [
    {
      "text": "今天的计划已经整理好了。",
      "emotion": "happy",
      "motion": "nod",
      "pause_after_ms": 250
    },
    {
      "text": "先处理配置问题，然后再看桌宠协议。",
      "emotion": "thinking",
      "expression": "star",
      "motion": "point"
    }
  ]
}`,
};

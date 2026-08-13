import type { MotionIntentName } from "@/domain/motionIntent";

export interface MotionBenchmarkScenario {
  id: string;
  assistantText: string;
  expectedIntent: MotionIntentName;
  displayEmotion?: string;
}

/** Fixed semantic baseline; extend this set before comparing a learned planner. */
export const motionBenchmarkScenarios: MotionBenchmarkScenario[] = [
  { id: "greet-zh-1", assistantText: "你好，很高兴见到你。", expectedIntent: "greet" },
  { id: "greet-zh-2", assistantText: "嗨，今天过得怎么样？", expectedIntent: "greet" },
  { id: "greet-zh-3", assistantText: "早上好，我们开始吧。", expectedIntent: "greet" },
  { id: "greet-en-1", assistantText: "Hello, nice to meet you.", expectedIntent: "greet" },
  { id: "greet-en-2", assistantText: "Hi! What can I help with?", expectedIntent: "greet" },
  { id: "apology-zh-1", assistantText: "抱歉，我刚才理解错了。", expectedIntent: "apology" },
  { id: "apology-zh-2", assistantText: "对不起，这次没有处理好。", expectedIntent: "apology" },
  { id: "apology-zh-3", assistantText: "不好意思，让你久等了。", expectedIntent: "apology" },
  { id: "apology-en-1", assistantText: "Sorry, I made a mistake.", expectedIntent: "apology" },
  { id: "apology-en-2", assistantText: "I apologize for the delay.", expectedIntent: "apology" },
  { id: "deny-zh-1", assistantText: "不是这个答案。", expectedIntent: "deny" },
  { id: "deny-zh-2", assistantText: "不对，这里需要修正。", expectedIntent: "deny" },
  { id: "deny-zh-3", assistantText: "现在不能这样做。", expectedIntent: "deny" },
  { id: "deny-en-1", assistantText: "No, that result is wrong.", expectedIntent: "deny" },
  { id: "deny-en-2", assistantText: "I cannot confirm that claim.", expectedIntent: "deny" },
  { id: "agree-zh-1", assistantText: "好的，我们就这样做。", expectedIntent: "agree" },
  { id: "agree-zh-2", assistantText: "没错，这个结论成立。", expectedIntent: "agree" },
  { id: "agree-zh-3", assistantText: "当然可以。", expectedIntent: "agree" },
  { id: "agree-en-1", assistantText: "Yes, that is correct.", expectedIntent: "agree" },
  { id: "agree-en-2", assistantText: "Exactly, the values match.", expectedIntent: "agree" },
  { id: "surprise-zh-1", assistantText: "居然一次就通过了！", expectedIntent: "surprised" },
  { id: "surprise-zh-2", assistantText: "竟然还有这种情况。", expectedIntent: "surprised" },
  { id: "surprise-zh-3", assistantText: "哇，这个结果很特别。", expectedIntent: "surprised" },
  { id: "surprise-en-1", assistantText: "Wow, I did not expect that.", expectedIntent: "surprised" },
  { id: "surprise-en-2", assistantText: "That is a surprising result.", expectedIntent: "surprised" },
  { id: "celebrate-zh-1", assistantText: "完成了，所有测试都通过了！", expectedIntent: "celebrate" },
  { id: "celebrate-zh-2", assistantText: "成功了，这次构建很稳定。", expectedIntent: "celebrate" },
  { id: "celebrate-zh-3", assistantText: "太好了，任务结束。", expectedIntent: "celebrate" },
  { id: "celebrate-en-1", assistantText: "Success! The build is green.", expectedIntent: "celebrate" },
  { id: "celebrate-en-2", assistantText: "Congratulations, it is complete.", expectedIntent: "celebrate" },
  { id: "concern-zh-1", assistantText: "小心，这个操作不可撤销。", expectedIntent: "concerned" },
  { id: "concern-zh-2", assistantText: "注意这里有数据丢失风险。", expectedIntent: "concerned" },
  { id: "concern-zh-3", assistantText: "我有点担心这个结果。", expectedIntent: "concerned" },
  { id: "concern-en-1", assistantText: "Careful, this may remove data.", expectedIntent: "concerned" },
  { id: "concern-en-2", assistantText: "There is a serious risk here.", expectedIntent: "concerned" },
  { id: "thinking-zh-1", assistantText: "让我想一想。", expectedIntent: "thinking" },
  { id: "thinking-zh-2", assistantText: "我先分析一下原因。", expectedIntent: "thinking" },
  { id: "thinking-zh-3", assistantText: "让我看看现有记录。", expectedIntent: "thinking" },
  { id: "thinking-en-1", assistantText: "Let me think about it.", expectedIntent: "thinking" },
  { id: "thinking-en-2", assistantText: "I will search the available records.", expectedIntent: "thinking" },
  { id: "explain-zh-1", assistantText: "这个模块负责把输入转换成时间线。", expectedIntent: "explain" },
  { id: "explain-zh-2", assistantText: "第一步读取配置，第二步创建连接。", expectedIntent: "explain" },
  { id: "explain-zh-3", assistantText: "结果由三个部分组成。", expectedIntent: "explain" },
  { id: "explain-en-1", assistantText: "The module converts input into a timeline.", expectedIntent: "explain" },
  { id: "explain-en-2", assistantText: "There are three parts in the result.", expectedIntent: "explain" },
  { id: "emotion-error", assistantText: "请求已经结束。", displayEmotion: "error", expectedIntent: "error" },
  { id: "emotion-worried", assistantText: "我会继续陪着你。", displayEmotion: "worried", expectedIntent: "concerned" },
  { id: "emotion-surprised", assistantText: "结果已经返回。", displayEmotion: "surprised", expectedIntent: "surprised" },
  { id: "emotion-thinking", assistantText: "稍等片刻。", displayEmotion: "thinking", expectedIntent: "thinking" },
  { id: "empty-idle", assistantText: "", expectedIntent: "idle" },
];

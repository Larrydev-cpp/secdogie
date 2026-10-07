import { ConservativeRouteAnalyzer, fetchPublicSource, type PublicFetchPolicy } from "../src/public-source.js";
import type { PublicFetchTask } from "../src/protocol.js";

const analyzer = new ConservativeRouteAnalyzer();

type AnalyzeMessage = { type: "analyze"; task: PublicFetchTask; policy: PublicFetchPolicy };

self.onmessage = async (event: MessageEvent<AnalyzeMessage>) => {
  if (event.data?.type !== "analyze") return;
  const task = event.data.task;
  try {
    const fetched = await fetchPublicSource(task, event.data.policy);
    const routes = analyzer.analyze(fetched.body, fetched.hash);
    self.postMessage({ type: "analyzed", taskId: task.taskId, url: fetched.url, sourceHash: fetched.hash, routes });
  } catch (error) {
    self.postMessage({
      type: "analyzed",
      taskId: task.taskId,
      url: task.url,
      sourceHash: "",
      routes: [],
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

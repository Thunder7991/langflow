import { ReadableStream } from "node:stream/web";
import { TextDecoder, TextEncoder } from "node:util";
import useAuthStore from "@/stores/authStore";
import { streamPlatformPlayground } from "../platform-stream";

const encode = new TextEncoder();
const options = {
  flowId: "flow",
  version: "revision",
  mode: "draft" as const,
  input: "你好",
  runId: "run",
  signal: new AbortController().signal,
};
beforeEach(() => {
  Object.assign(global, { TextDecoder });
  useAuthStore.setState({ platformWorkflowActions: ["read", "dry_run"] });
});
afterEach(() => jest.restoreAllMocks());
function response(parts: string[]) {
  return {
    ok: true,
    headers: new Headers(),
    body: new ReadableStream({
      start(controller) {
        for (const part of parts) controller.enqueue(encode.encode(part));
        controller.close();
      },
    }),
  } as unknown as Response;
}
it("decodes fragmented SSE frames and posts only chat input with version and credentials", async () => {
  const fetcher = jest
    .spyOn(global, "fetch")
    .mockResolvedValue(
      response([
        ': heartbeat\r\n\r\ndata: {"type":"run_started","run_id":"run",',
        '"version_token":"revision"}\r\n\r\ndata: {"type":"run_finished","run_id":"run",',
        '"version_token":"revision","outputs":[{"node_id":"out","text":"你好"}]}\n\n',
      ]),
    );
  const received: unknown[] = [];
  await streamPlatformPlayground(options, (event) => received.push(event));
  expect(received).toHaveLength(2);
  expect(received[1]).toMatchObject({ outputs: [{ text: "你好" }] });
  expect(fetcher).toHaveBeenCalledWith(
    expect.stringContaining("/platform-playground/flow"),
    expect.objectContaining({
      credentials: "include",
      headers: expect.objectContaining({ "If-Match": "revision" }),
      body: JSON.stringify({ mode: "draft", input: "你好", run_id: "run" }),
    }),
  );
});
it("rejects an interrupted stream and mismatched run identity without delivering results", async () => {
  jest
    .spyOn(global, "fetch")
    .mockResolvedValueOnce(
      response([
        'data: {"type":"run_started","run_id":"run","version_token":"revision"}\n\n',
      ]),
    )
    .mockResolvedValueOnce(
      response([
        'data: {"type":"run_finished","run_id":"another","version_token":"revision","outputs":[]}\n\n',
      ]),
    );
  await expect(streamPlatformPlayground(options, jest.fn())).rejects.toThrow(
    "连接已中断",
  );
  const receive = jest.fn();
  await expect(streamPlatformPlayground(options, receive)).rejects.toThrow(
    "运行标识",
  );
  expect(receive).not.toHaveBeenCalled();
});
it("tightens capabilities and notifies the host on revocation inside the stream", async () => {
  jest
    .spyOn(global, "fetch")
    .mockResolvedValue(
      response([
        'data: {"type":"run_started","run_id":"run","version_token":"revision"}\n\n',
        'data: {"type":"run_error","run_id":"run","status_code":401,"code":"PLATFORM_SSO_REQUIRED","hostOrigin":"http://localhost:3000","detail":"expired"}\n\n',
      ]),
    );
  const post = jest.spyOn(window.parent, "postMessage");
  await streamPlatformPlayground(options, jest.fn());
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([]);
  expect(post).toHaveBeenCalledWith(
    { channel: "workflow-sso", status: "expired" },
    "http://localhost:3000",
  );
});

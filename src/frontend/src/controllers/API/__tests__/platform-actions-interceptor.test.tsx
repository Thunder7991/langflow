import { act, render } from "@testing-library/react";
import { AxiosError, AxiosHeaders } from "axios";
import useAuthStore from "@/stores/authStore";
import { ApiInterceptor, api } from "../api";

const mockRefresh = jest.fn();
const mockLogout = jest.fn();
jest.mock("../queries/auth", () => ({
  useLogout: () => ({ mutate: mockLogout }),
  useRefreshAccessToken: () => ({ mutateAsync: mockRefresh }),
}));
jest.mock("fetch-intercept", () => ({
  register: () => jest.fn(),
}));

afterEach(() => {
  useAuthStore.setState({ platformWorkflowActions: null });
  jest.restoreAllMocks();
  jest.clearAllMocks();
});

it.each([
  ["/api/v2/platform-files", false],
  ["/api/v2/platform-files", true],
  ["/api/v2/platform-workflow/flows/flow-id/run", false],
  ["/api/v2/platform-workflow/flows/flow-id/run", true],
])(
  "preserves cancellation for %s (already aborted: %s)",
  async (url, alreadyAborted) => {
    useAuthStore.setState({ platformWorkflowActions: ["read", "run"] });
    const { unmount } = render(<ApiInterceptor />);
    const controller = new AbortController();
    if (alreadyAborted) controller.abort();
    let transportAborted = false;
    let transportStarted = false;
    const request = api
      .post(url as string, "payload", {
        signal: controller.signal,
        adapter: (config) =>
          new Promise((_resolve, reject) => {
            transportStarted = true;
            config.signal?.addEventListener?.("abort", () => {
              transportAborted = true;
              reject(new AxiosError("cancelled", "ERR_CANCELED", config));
            });
          }),
      })
      .catch((error) => error);
    // 等待请求拦截器运行，验证真正交给传输层的 signal，而不是组件自己的状态。
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    if (!alreadyAborted) controller.abort();
    await expect(request).resolves.toMatchObject({ code: "ERR_CANCELED" });
    expect(transportStarted).toBe(!alreadyAborted);
    if (!alreadyAborted) expect(transportAborted).toBe(true);
    expect(useAuthStore.getState().platformWorkflowActions).toEqual([
      "read",
      "run",
    ]);
    unmount();
  },
);

it.each([403, 503])(
  "rejects platform %s without native refresh or logout",
  async (status) => {
    const installed = jest.spyOn(api.interceptors.response, "use");
    const { unmount } = render(<ApiInterceptor />);
    const rejectResponse = installed.mock.calls[0][1]!;
    const config = {
      url: "/api/v1/flows/flow-id",
      headers: new AxiosHeaders(),
    };
    const error = new AxiosError("refused", undefined, config, undefined, {
      data: { detail: "refused" },
      status,
      statusText: "refused",
      config,
      headers: { "x-workflow-actions": status === 403 ? "read" : "" },
    });
    await act(async () => {
      await expect(rejectResponse(error)).rejects.toBe(error);
    });
    expect(mockRefresh).not.toHaveBeenCalled();
    expect(mockLogout).not.toHaveBeenCalled();
    expect(useAuthStore.getState().platformWorkflowActions).toEqual(
      status === 403 ? ["read"] : [],
    );
    unmount();
  },
);

it("does not refresh or logout when a proxy rejects a platform request without capability headers", async () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  const installed = jest.spyOn(api.interceptors.response, "use");
  const { unmount } = render(<ApiInterceptor />);
  const rejectResponse = installed.mock.calls[0][1]!;
  const config = { url: "/api/v1/flows/flow-id", headers: new AxiosHeaders() };
  const error = new AxiosError("proxy denied", undefined, config, undefined, {
    data: {},
    status: 403,
    statusText: "Forbidden",
    config,
    headers: {},
  });
  await act(async () => {
    await expect(rejectResponse(error)).rejects.toBe(error);
  });
  expect(mockRefresh).not.toHaveBeenCalled();
  expect(mockLogout).not.toHaveBeenCalled();
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([]);
  unmount();
});

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

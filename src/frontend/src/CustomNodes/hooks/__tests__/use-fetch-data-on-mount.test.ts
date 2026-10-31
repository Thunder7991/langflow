import { renderHook } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import type { APIClassType } from "@/types/api";

const mockMutateTemplate = jest.fn();

jest.mock("../../helpers/mutate-template", () => ({
  mutateTemplate: (...args: unknown[]) => mockMutateTemplate(...args),
}));

jest.mock("../../../stores/alertStore", () => ({
  __esModule: true,
  default: (selector: (state: unknown) => unknown) =>
    selector({ setErrorData: jest.fn() }),
}));

import useFetchDataOnMount from "../use-fetch-data-on-mount";

const nodeWithRefreshableField = (): APIClassType =>
  ({
    template: {
      model_name: {
        real_time_refresh: true,
        options: [],
        value: "",
      },
    },
  }) as unknown as APIClassType;

describe("useFetchDataOnMount", () => {
  beforeEach(() => {
    mockMutateTemplate.mockClear();
    useAuthStore.setState({ platformWorkflowActions: null });
  });
  afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

  it("does not enqueue initialization for a read-only platform flow", () => {
    useAuthStore.setState({ platformWorkflowActions: ["read"] });
    renderHook(() =>
      useFetchDataOnMount(
        nodeWithRefreshableField(),
        "node-1",
        jest.fn(),
        "model_name",
        {} as never,
      ),
    );
    expect(mockMutateTemplate).not.toHaveBeenCalled();
  });

  it("refreshes the template on mount", () => {
    renderHook(() =>
      useFetchDataOnMount(
        nodeWithRefreshableField(),
        "node-1",
        jest.fn(),
        "model_name",
        {} as never,
      ),
    );

    expect(mockMutateTemplate).toHaveBeenCalledTimes(1);
  });

  // Whether that refresh saves the flow is decided by the setter the caller
  // hands in - NodeInputField passes the non-saving one (#8995), covered in
  // use-handle-node-class.test.ts. This hook must pass it through untouched.
  it("applies the refresh through the setter it was given", () => {
    const setNodeClass = jest.fn();

    renderHook(() =>
      useFetchDataOnMount(
        nodeWithRefreshableField(),
        "node-1",
        setNodeClass,
        "model_name",
        {} as never,
      ),
    );

    expect(mockMutateTemplate.mock.calls[0][3]).toBe(setNodeClass);
  });
});

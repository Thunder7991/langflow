import { act, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DashboardWrapperPage } from "..";

jest.mock(
  "@/components/core/appHeaderComponent/components/FlowMenu",
  () => () => null,
);
jest.mock("@/contexts/permissionsContext", () => ({
  PermissionsProvider: ({ children }) => children,
}));
jest.mock("@/customization/hooks/use-custom-theme", () => ({
  __esModule: true,
  default: () => ({}),
}));
jest.mock("@/stores/flowStore", () => ({
  __esModule: true,
  default: (selector) => selector({}),
}));

const parentWindow = { postMessage: jest.fn() };
const originalParent = window.parent;

beforeEach(() => {
  jest.useFakeTimers();
  Object.defineProperty(window, "parent", {
    configurable: true,
    value: parentWindow,
  });
});
afterEach(() => {
  Object.defineProperty(window, "parent", {
    configurable: true,
    value: originalParent,
  });
  jest.useRealTimers();
  jest.clearAllMocks();
});

function requestReady(source = parentWindow) {
  window.dispatchEvent(
    new MessageEvent("message", {
      source: source as unknown as Window,
      origin: "https://ai.example.com",
      data: {
        channel: "workflow-ui",
        status: "check-ready",
        requestId: "navigation-1",
      },
    }),
  );
}

it("reports UI readiness after the authenticated layout has had a paint opportunity", () => {
  render(
    <MemoryRouter>
      <DashboardWrapperPage />
    </MemoryRouter>,
  );
  requestReady();
  expect(parentWindow.postMessage).not.toHaveBeenCalled();
  act(() => jest.advanceTimersByTime(50));
  expect(parentWindow.postMessage).toHaveBeenCalledWith(
    { channel: "workflow-ui", status: "ready", requestId: "navigation-1" },
    "https://ai.example.com",
  );
});

it("ignores other windows and cancels pending readiness on unmount", () => {
  const view = render(
    <MemoryRouter>
      <DashboardWrapperPage />
    </MemoryRouter>,
  );
  requestReady({ postMessage: jest.fn() });
  act(() => jest.advanceTimersByTime(50));
  expect(parentWindow.postMessage).not.toHaveBeenCalled();
  requestReady();
  view.unmount();
  act(() => jest.advanceTimersByTime(50));
  expect(parentWindow.postMessage).not.toHaveBeenCalled();
});

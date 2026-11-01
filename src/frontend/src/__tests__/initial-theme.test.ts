import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const html = readFileSync(resolve(__dirname, "../../index.html"), "utf8");
const originalStorage = window.localStorage;
jest.mock("@/controllers/API", () => ({}));

afterEach(() => {
  document.body.className = "";
  document.body.removeAttribute("id");
  Object.defineProperty(window, "localStorage", {
    value: originalStorage,
    configurable: true,
  });
  jest.restoreAllMocks();
});

it.each([
  ["light", "true", true, false],
  ["dark", "false", false, true],
  ["system", "true", false, false],
  [null, "false", true, true],
])(
  "applies preference %s before React, ignoring a stale cached isDark",
  (preference, cached, systemDark, expected) => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) =>
          key === "themePreference"
            ? preference
            : key === "isDark"
              ? cached
              : null,
      },
    });
    jest
      .spyOn(window, "matchMedia")
      .mockReturnValue({ matches: systemDark } as MediaQueryList);
    const parsed = new DOMParser().parseFromString(html, "text/html");
    document.body.id = "body";
    document.body.className = parsed.body.className;
    const bootstrap =
      parsed.querySelector("#bootstrap-theme")?.textContent ?? "";
    // 执行真实 HTML 的同步脚本，验证 React 尚未挂载时的主题。
    new Function(bootstrap)();
    expect(document.body.classList.contains("dark")).toBe(expected);
    jest.isolateModules(() => {
      const { useDarkStore } = jest.requireActual("../stores/darkStore");
      expect(useDarkStore.getState().dark).toBe(expected);
    });
  },
);

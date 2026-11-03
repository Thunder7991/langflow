import { extractApiErrorMessages } from "../apiError";

it("explains a file transaction conflict without showing driver data", () => {
  const messages = extractApiErrorMessages({
    platformWorkflow: true,
    response: {
      status: 503,
      data: {
        detail: {
          code: "platform_file_operation_busy",
          message: "SQL parameters must not be displayed",
        },
      },
    },
  });
  expect(messages.join(" ")).toMatch(/文件.*稍后重试/);
  expect(messages.join(" ")).not.toContain("SQL");
});

describe("extractApiErrorMessages", () => {
  it("explains network uncertainty without falsely claiming a write failed or succeeded", () => {
    expect(
      extractApiErrorMessages({
        platformWorkflow: true,
        message: "Network Error",
      })[0],
    ).toContain("操作结果尚未确认");
  });
  it.each([
    [401, "PLATFORM_SSO_REQUIRED", /登录.*失效/],
    [403, "PLATFORM_SSO_ERROR", /权限/],
    [503, "PLATFORM_SSO_ERROR", /服务.*不可用/],
    [409, "flow_version_conflict", /版本.*冲突/],
  ])(
    "explains platform status %s without exposing raw backend detail",
    (status, code, expected) => {
      const messages = extractApiErrorMessages({
        response: {
          status,
          headers: { "x-workflow-actions": "" },
          data: { code, detail: { code, message: "raw internal detail" } },
        },
      });
      expect(messages).toHaveLength(1);
      expect(messages[0]).toMatch(expected);
      expect(messages[0]).not.toContain("raw internal detail");
    },
  );
  it("returns a default message for non-object errors", () => {
    expect(extractApiErrorMessages(null)).toEqual([
      "An unknown error occurred",
    ]);
    expect(extractApiErrorMessages(undefined)).toEqual([
      "An unknown error occurred",
    ]);
    expect(extractApiErrorMessages("boom")).toEqual([
      "An unknown error occurred",
    ]);
    expect(extractApiErrorMessages(123)).toEqual(["An unknown error occurred"]);
  });

  it("prefers response.data.detail when it is a string", () => {
    const error = {
      response: { data: { detail: "Server not found" } },
      message: "Network Error",
    };

    expect(extractApiErrorMessages(error)).toEqual(["Server not found"]);
  });

  it("extracts msg fields from validation-error arrays", () => {
    const error = {
      response: {
        data: {
          detail: [{ msg: "Field 'name' is required" }, { msg: "Bad input" }],
        },
      },
    };

    expect(extractApiErrorMessages(error)).toEqual([
      "Field 'name' is required",
      "Bad input",
    ]);
  });

  it("supports arrays mixing strings and objects", () => {
    const error = {
      response: {
        data: {
          detail: ["First", { msg: "Second" }],
        },
      },
    };

    expect(extractApiErrorMessages(error)).toEqual(["First", "Second"]);
  });

  it("JSON-stringifies array objects without a msg field", () => {
    const error = {
      response: {
        data: {
          detail: [{ code: 500, info: "crash" }],
        },
      },
    };

    expect(extractApiErrorMessages(error)).toEqual([
      '{"code":500,"info":"crash"}',
    ]);
  });

  it("falls back to error.message when array detail has no usable messages", () => {
    const error = {
      response: {
        data: {
          detail: ["", { msg: "" }],
        },
      },
      message: "Request failed",
    };

    expect(extractApiErrorMessages(error)).toEqual(["Request failed"]);
  });

  it("falls back to error.message when detail is an empty array", () => {
    const error = {
      response: {
        data: {
          detail: [],
        },
      },
      message: "Request failed",
    };

    expect(extractApiErrorMessages(error)).toEqual(["Request failed"]);
  });

  it("falls back to error.message when no detail is present", () => {
    expect(extractApiErrorMessages({ message: "Network Error" })).toEqual([
      "Network Error",
    ]);
  });

  it("always returns at least one message", () => {
    const msgs = extractApiErrorMessages({});
    expect(Array.isArray(msgs)).toBe(true);
    expect(msgs.length).toBeGreaterThan(0);
  });

  it("surfaces the message of a structured detail object", () => {
    const error = {
      response: {
        data: {
          detail: {
            error_code: "tier_limit_reached",
            message: "Your plan allows 3 projects.",
            resource: "projects",
            limit: 3,
            current: 3,
            tier: "trial",
          },
        },
      },
      message: "Request failed with status code 403",
    };

    expect(extractApiErrorMessages(error)).toEqual([
      "Your plan allows 3 projects.",
    ]);
  });

  it("falls back to error.message when a detail object has no message", () => {
    const error = {
      response: { data: { detail: { error_code: "tier_limit_reached" } } },
      message: "Request failed with status code 403",
    };

    expect(extractApiErrorMessages(error)).toEqual([
      "Request failed with status code 403",
    ]);
  });
});

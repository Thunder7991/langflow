import { handlePlatformSsoError } from "../platform-sso-error";

describe("platform SSO failure recovery", () => {
  it("notifies only the configured host for an expired platform-linked session", () => {
    const post = jest
      .spyOn(window.parent, "postMessage")
      .mockImplementation(() => {});
    expect(
      handlePlatformSsoError({
        code: "PLATFORM_SSO_REQUIRED",
        hostOrigin: "https://ai.example.com",
      }),
    ).toBe(true);
    expect(post).toHaveBeenCalledWith(
      { channel: "workflow-sso", status: "expired" },
      "https://ai.example.com",
    );
    post.mockRestore();
  });
  it("never triggers re-login for readonly denial or backend failure", () => {
    const post = jest
      .spyOn(window.parent, "postMessage")
      .mockImplementation(() => {});
    expect(
      handlePlatformSsoError({
        code: "PLATFORM_SSO_ERROR",
        hostOrigin: "https://ai.example.com",
      }),
    ).toBe(true);
    expect(post).not.toHaveBeenCalled();
    expect(handlePlatformSsoError({ detail: "native auth" })).toBe(false);
    post.mockRestore();
  });
});

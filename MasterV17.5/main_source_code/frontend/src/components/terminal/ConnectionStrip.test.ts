import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { describeBackendError } from "./ConnectionStrip";

describe("describeBackendError", () => {
  it("distinguishes a missing session from a rejected access key", () => {
    expect(describeBackendError(new ApiError(401, { detail: "SESSION_REQUIRED" })))
      .toContain("session missing or expired");
    expect(describeBackendError(new ApiError(401, { detail: "INVALID_CREDENTIAL" })))
      .toContain("access key rejected");
  });

  it("explains an unconfigured backend access key", () => {
    expect(describeBackendError(new ApiError(503, { detail: "AUTH_NOT_CONFIGURED" })))
      .toContain("BACKEND_API_KEY");
  });
});

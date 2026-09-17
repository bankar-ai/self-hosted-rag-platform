import { describe, expect, it } from "vitest";
import { decodeAccessTokenPayload } from "./jwt";

function fakeToken(payload: Record<string, unknown>): string {
  const base64url = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${base64url({ alg: "HS256" })}.${base64url(payload)}.fake-signature`;
}

describe("decodeAccessTokenPayload", () => {
  it("decodes sub and role from a well-formed token", () => {
    const token = fakeToken({ sub: "11111111-1111-1111-1111-111111111111", role: "user" });

    expect(decodeAccessTokenPayload(token)).toEqual({
      sub: "11111111-1111-1111-1111-111111111111",
      role: "user",
    });
  });

  it("returns null for a malformed token", () => {
    expect(decodeAccessTokenPayload("not-a-jwt")).toBeNull();
  });

  it("returns null when required claims are missing", () => {
    const token = fakeToken({ role: "user" });

    expect(decodeAccessTokenPayload(token)).toBeNull();
  });
});

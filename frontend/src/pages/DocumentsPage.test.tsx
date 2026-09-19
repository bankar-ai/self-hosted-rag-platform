import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as apiClient from "../lib/apiClient";
import { AuthProvider } from "../lib/AuthContext";
import { setTokens } from "../lib/tokenStorage";
import DocumentsPage from "./DocumentsPage";

// `decodeAccessTokenPayload` (see ../lib/jwt.ts) requires a real header.payload.signature shape
// to resolve a `sub` claim -- DocumentsPage reads `userId` from that claim and renders nothing
// until it's present, so a plain non-JWT string like "a" would leave the page permanently blank
// in this test. Build a decodable fake token the same way ../lib/jwt.test.ts does.
function fakeToken(payload: Record<string, unknown>): string {
  const base64url = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${base64url({ alg: "HS256" })}.${base64url(payload)}.fake-signature`;
}

function stubAuthAndEmptyDocuments(extra?: (url: string, init?: RequestInit) => Response | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.includes("/auth/me")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ id: "u1", email: "u@example.com", role: "user", is_active: true }),
            { status: 200 }
          )
        );
      }
      const extraResponse = extra?.(url, init);
      if (extraResponse) return Promise.resolve(extraResponse);
      if (url.endsWith("/documents")) {
        return Promise.resolve(new Response(JSON.stringify({ documents: [] }), { status: 200 }));
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    })
  );
}

describe("DocumentsPage", () => {
  beforeEach(() => {
    localStorage.clear();
    setTokens({
      accessToken: fakeToken({ sub: "u1", role: "user" }),
      refreshToken: "b",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("stages a selected file without uploading it until Upload is clicked", async () => {
    stubAuthAndEmptyDocuments();
    const uploadSpy = vi
      .spyOn(apiClient, "uploadWithProgress")
      .mockResolvedValue({ ok: true, status: 202, body: { job_id: "job-1" } });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);

    expect(screen.getByText("test.pdf")).toBeInTheDocument();
    expect(uploadSpy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));
  });

  it("removes a staged file when its remove button is clicked", async () => {
    stubAuthAndEmptyDocuments();

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "remove-me.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    expect(screen.getByText("remove-me.pdf")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText(/remove remove-me\.pdf/i));

    expect(screen.queryByText("remove-me.pdf")).not.toBeInTheDocument();
  });

  it("shows a failed state and lets the user retry when the upload transfer itself fails", async () => {
    stubAuthAndEmptyDocuments((url) => {
      if (url.includes("/ingestion/jobs/job-y")) {
        return new Response(
          JSON.stringify({ status: "done", result: { document_id: "d1", chunks: [] } }),
          { status: 200 }
        );
      }
      return null;
    });
    const uploadSpy = vi
      .spyOn(apiClient, "uploadWithProgress")
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ ok: true, status: 202, body: { job_id: "job-y" } });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "flaky.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(screen.getByText(/upload failed/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(2));
  });
});

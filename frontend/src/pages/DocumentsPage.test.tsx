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

  it("shows a confirmation before deleting, and does nothing if declined", async () => {
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [{ document_id: "d1", filename: "existing.pdf", created_at: "2026-01-01T00:00:00Z" }],
          }),
          { status: 200 }
        );
      }
      return null;
    });
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("existing.pdf"));

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(screen.getByText("existing.pdf")).toBeInTheDocument();
  });

  it("deletes a document when the confirmation is accepted", async () => {
    const deleteCalls: string[] = [];
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/documents/") && init?.method === "DELETE") {
        deleteCalls.push(url);
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [{ document_id: "d1", filename: "existing.pdf", created_at: "2026-01-01T00:00:00Z" }],
          }),
          { status: 200 }
        );
      }
      return null;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("existing.pdf"));

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteCalls).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("existing.pdf")).not.toBeInTheDocument());
  });

  it("deletes multiple selected documents via bulk delete", async () => {
    const deleteCalls: string[] = [];
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/documents/") && init?.method === "DELETE") {
        deleteCalls.push(url);
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [
              { document_id: "d1", filename: "one.pdf", created_at: "2026-01-01T00:00:00Z" },
              { document_id: "d2", filename: "two.pdf", created_at: "2026-01-01T00:00:00Z" },
            ],
          }),
          { status: 200 }
        );
      }
      return null;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("one.pdf"));

    await userEvent.click(screen.getByLabelText(/select all/i));
    await userEvent.click(screen.getByRole("button", { name: /delete selected \(2\)/i }));

    await waitFor(() => expect(deleteCalls).toHaveLength(2));
    expect(deleteCalls.some((url) => url.endsWith("/documents/d1"))).toBe(true);
    expect(deleteCalls.some((url) => url.endsWith("/documents/d2"))).toBe(true);
  });

  it("calls the delete-job endpoint when a failed upload is dismissed", async () => {
    let deleteJobCalled = false;
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/ingestion/jobs/job-x") && init?.method === "DELETE") {
        deleteJobCalled = true;
        return new Response(null, { status: 204 });
      }
      if (url.includes("/ingestion/jobs/job-x")) {
        return new Response(JSON.stringify({ status: "failed", error: "boom" }), { status: 200 });
      }
      return null;
    });
    vi.spyOn(apiClient, "uploadWithProgress").mockResolvedValue({
      ok: true,
      status: 202,
      body: { job_id: "job-x" },
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "broken.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() => expect(deleteJobCalled).toBe(true));
  });
});

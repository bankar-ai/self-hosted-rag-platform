import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as apiClient from "../lib/apiClient";
import { AuthProvider } from "../lib/AuthContext";
import { getDocumentsStore } from "../lib/documentsStore";
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
      if (url.endsWith("/ingestion/jobs")) {
        return Promise.resolve(new Response(JSON.stringify({ jobs: [] }), { status: 200 }));
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

  it("shows a parsing-confidence badge for each document (ERP-076)", async () => {
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [
              {
                document_id: "d1",
                filename: "clean.pdf",
                created_at: "2026-01-01T00:00:00Z",
                parsing_confidence: "high",
              },
              {
                document_id: "d2",
                filename: "scanned.pdf",
                created_at: "2026-01-01T00:00:00Z",
                parsing_confidence: "poor",
              },
            ],
          }),
          { status: 200 }
        );
      }
      return null;
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("clean.pdf"));

    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("poor")).toBeInTheDocument();
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

  it("does not call the delete-job endpoint when retrying a failed job (Finding 1 regression guard)", async () => {
    let deleteJobCalled = false;
    let retryCalled = false;
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/ingestion/jobs/job-x") && init?.method === "DELETE") {
        deleteJobCalled = true;
        return new Response(null, { status: 204 });
      }
      if (url.includes("/ingestion/jobs/job-x/retry") && init?.method === "POST") {
        retryCalled = true;
        return new Response(JSON.stringify({ job_id: "job-x-2" }), { status: 200 });
      }
      if (url.includes("/ingestion/jobs/job-x-2")) {
        // Keep the retried job pending so the test doesn't race a poll-driven cleanup.
        return new Response(JSON.stringify({ status: "pending" }), { status: 200 });
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

    await userEvent.click(screen.getByRole("button", { name: /^retry$/i }));

    await waitFor(() => expect(retryCalled).toBe(true));
    // Give any accidental DELETE call a chance to fire before asserting it never did.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(deleteJobCalled).toBe(false);
  });

  it("keeps a document visible and its Delete button enabled when the delete request fails", async () => {
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/documents/") && init?.method === "DELETE") {
        return new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 });
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

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Delete" })).not.toBeDisabled()
    );
    expect(screen.getByText("existing.pdf")).toBeInTheDocument();
  });

  it("shows an in-progress job from another device via GET /ingestion/jobs (ERP-095)", async () => {
    // Nothing in localStorage for this job -- it's discoverable purely because the server
    // reports it as one of this account's active jobs, simulating a second device/session.
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/ingestion/jobs")) {
        return new Response(
          JSON.stringify({
            jobs: [{ job_id: "job-other-device", filename: "from-phone.pdf", status: "processing", error: null }],
          }),
          { status: 200 }
        );
      }
      if (url.includes("/ingestion/jobs/job-other-device")) {
        // Stays "processing" on every poll -- the point of this test is that the entry is
        // discoverable and rendered at all, not the full lifecycle to completion.
        return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
      }
      return null;
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByText("from-phone.pdf")).toBeInTheDocument());
    expect(screen.getByText("processing")).toBeInTheDocument();
  });

  it("shows a cold-start hint for a job stuck processing past the threshold (ERP-091)", async () => {
    getDocumentsStore("u1").upsert({
      id: "job-slow",
      title: "scanned.pdf",
      status: "processing",
      lastUpdated: Date.now(),
      startedAt: Date.now() - 25_000,
    });
    // The server must also report this job as still active (ERP-099 reconciliation removes a
    // locally-seeded "processing" job the server no longer lists) -- a genuinely-stuck job is
    // still active server-side too, unlike a stale/orphaned local entry.
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/ingestion/jobs")) {
        return new Response(
          JSON.stringify({
            jobs: [{ job_id: "job-slow", filename: "scanned.pdf", status: "processing", error: null }],
          }),
          { status: 200 }
        );
      }
      if (url.includes("/ingestion/jobs/job-slow")) {
        return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
      }
      return null;
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );

    expect(await screen.findByText("scanned.pdf")).toBeInTheDocument();
    expect(screen.getByText(/complex documents may need extra processing time/i)).toBeInTheDocument();
  });

  it("does not show a cold-start hint for a job still within the normal processing window", async () => {
    getDocumentsStore("u1").upsert({
      id: "job-fast",
      title: "quick.pdf",
      status: "processing",
      lastUpdated: Date.now(),
      startedAt: Date.now() - 2_000,
    });
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/ingestion/jobs")) {
        return new Response(
          JSON.stringify({
            jobs: [{ job_id: "job-fast", filename: "quick.pdf", status: "processing", error: null }],
          }),
          { status: 200 }
        );
      }
      if (url.includes("/ingestion/jobs/job-fast")) {
        return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
      }
      return null;
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );

    expect(await screen.findByText("quick.pdf")).toBeInTheDocument();
    expect(screen.queryByText(/complex documents may need extra processing time/i)).not.toBeInTheDocument();
  });

  it("removes a locally-seeded job the server no longer reports as active (ERP-099)", async () => {
    // Simulates a job that finished (or was removed) while this device wasn't polling it --
    // e.g. it completed on another device, or this tab was closed mid-poll. The server's
    // active-jobs list is authoritative and no longer includes it, so the stale local entry
    // must be reconciled away instead of showing "processing" forever.
    getDocumentsStore("u1").upsert({
      id: "job-stale",
      title: "stale.pdf",
      status: "processing",
      lastUpdated: Date.now(),
      startedAt: Date.now() - 60_000,
    });
    stubAuthAndEmptyDocuments();

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.queryByText("stale.pdf")).not.toBeInTheDocument());
  });

  it("updates a locally-seeded job to failed when the server reports it as failed (ERP-099)", async () => {
    getDocumentsStore("u1").upsert({
      id: "job-now-failed",
      title: "broken.pdf",
      status: "processing",
      lastUpdated: Date.now(),
      startedAt: Date.now() - 60_000,
    });
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/ingestion/jobs")) {
        return new Response(
          JSON.stringify({
            jobs: [
              {
                job_id: "job-now-failed",
                filename: "broken.pdf",
                status: "failed",
                error: "parse error",
              },
            ],
          }),
          { status: 200 }
        );
      }
      return null;
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByText("broken.pdf")).toBeInTheDocument());
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("shows a dismiss button on a failed upload-transfer entry that removes it from the list", async () => {
    stubAuthAndEmptyDocuments();
    vi.spyOn(apiClient, "uploadWithProgress").mockRejectedValue(new Error("network down"));

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "gone.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(screen.getByText(/upload failed/i)).toBeInTheDocument());

    await userEvent.click(screen.getByLabelText(/remove gone\.pdf from uploads/i));

    expect(screen.queryByText(/upload failed/i)).not.toBeInTheDocument();
    expect(screen.queryByText("gone.pdf")).not.toBeInTheDocument();
  });

  it("shows the server's detail message instead of a generic failure when the upload is rejected (e.g. 429)", async () => {
    stubAuthAndEmptyDocuments();
    vi.spyOn(apiClient, "uploadWithProgress").mockResolvedValue({
      ok: false,
      status: 429,
      body: { detail: "You already have 5 documents processing. Wait for one to finish before uploading more." },
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "sixth.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() =>
      expect(
        screen.getByText(
          /you already have 5 documents processing\. wait for one to finish before uploading more\./i
        )
      ).toBeInTheDocument()
    );
    expect(screen.queryByText(/^upload failed\.$/i)).not.toBeInTheDocument();
  });

  it("rejects a selection that would exceed the 5-file upload cap", async () => {
    stubAuthAndEmptyDocuments();

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const files = Array.from({ length: 6 }, (_, i) =>
      new File([new Uint8Array(10)], `doc${i}.pdf`, { type: "application/pdf" })
    );
    const input = screen.getByLabelText(/choose pdf files/i);
    await userEvent.upload(input, files);

    expect(
      screen.getByText(/only 5 files can be uploaded at a time/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/ready to upload/i)).not.toBeInTheDocument();
  });
});

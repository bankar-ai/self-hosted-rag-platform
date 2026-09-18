import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";
import { getDocumentsStore, type RecentDocument } from "../lib/documentsStore";
import type { DocumentListResponse, DocumentSummary, JobStatusResponse } from "../lib/types";

const POLL_INTERVAL_MS = 2000;

const IN_PROGRESS_STATUS_STYLES: Record<"pending" | "processing" | "failed", string> = {
  pending: "bg-amber-100 text-amber-700",
  processing: "bg-amber-100 text-amber-700",
  failed: "bg-red-100 text-red-700",
};

export default function DocumentsPage() {
  const { userId } = useAuth();
  // In-flight uploads only (pending/processing/failed) -- still tracked client-side, since a
  // job that hasn't finished (or never will) has no backend row to read back. A job that
  // reaches "done" is removed from here as soon as it's confirmed in `serverDocuments`.
  const [inProgress, setInProgress] = useState<RecentDocument[]>(() =>
    userId ? getDocumentsStore(userId).list().filter((doc) => doc.status !== "done") : []
  );
  const [serverDocuments, setServerDocuments] = useState<DocumentSummary[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function refreshServerDocuments(): Promise<void> {
    const response = await apiFetch("/documents");
    if (!response.ok) return;
    const body = (await response.json()) as DocumentListResponse;
    setServerDocuments(body.documents);
  }

  useEffect(() => {
    void refreshServerDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AuthGuard already guarantees a valid token (and therefore a decodable userId) before this
  // page can render at all -- this is a type-safety formality, not a loading state to wait out.
  if (!userId) {
    return null;
  }
  const scopedUserId = userId;

  function updateInProgress(doc: RecentDocument): void {
    const store = getDocumentsStore(scopedUserId);
    store.upsert(doc);
    setInProgress(store.list().filter((d) => d.status !== "done"));
  }

  function dismissInProgress(jobId: string): void {
    const store = getDocumentsStore(scopedUserId);
    store.remove(jobId);
    setInProgress(store.list().filter((d) => d.status !== "done"));
  }

  async function pollJob(jobId: string, filename: string): Promise<void> {
    const response = await apiFetch(`/ingestion/jobs/${jobId}`);
    const job = (await response.json()) as JobStatusResponse;

    if (job.status === "pending" || job.status === "processing") {
      updateInProgress({ id: jobId, title: filename, lastUpdated: Date.now(), status: job.status });
      setTimeout(() => void pollJob(jobId, filename), POLL_INTERVAL_MS);
      return;
    }

    if (job.status === "failed") {
      updateInProgress({
        id: jobId,
        title: filename,
        lastUpdated: Date.now(),
        status: "failed",
        error: job.error ?? undefined,
      });
      return;
    }

    // Done: the document now has its own row on the backend, so it no longer needs to be
    // tracked as a local in-flight job -- drop it here and let `serverDocuments` show it.
    getDocumentsStore(scopedUserId).remove(jobId);
    setInProgress(getDocumentsStore(scopedUserId).list().filter((d) => d.status !== "done"));
    await refreshServerDocuments();
  }

  async function handleUpload(): Promise<void> {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append("file", file);

    const response = await apiFetch("/ingestion/pdf", { method: "POST", body: formData });
    setIsUploading(false);

    if (!response.ok) return;

    const { job_id: jobId } = (await response.json()) as { job_id: string };
    updateInProgress({ id: jobId, title: file.name, lastUpdated: Date.now(), status: "pending" });
    void pollJob(jobId, file.name);

    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleDelete(documentId: string): Promise<void> {
    setDeletingId(documentId);
    const response = await apiFetch(`/documents/${documentId}`, { method: "DELETE" });
    setDeletingId(null);
    if (response.ok) {
      setServerDocuments((prev) => prev.filter((doc) => doc.document_id !== documentId));
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="mb-1 text-2xl font-semibold text-slate-900">Documents</h1>
      <p className="mb-6 text-sm text-slate-500">
        Upload a PDF to make it searchable in Chat. Ingestion runs in the background.
      </p>
      <div className="mb-8 flex items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4">
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          className="flex-1 text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white"
        />
        <Button onClick={() => void handleUpload()} disabled={isUploading}>
          {isUploading ? "Uploading..." : "Upload"}
        </Button>
      </div>

      {inProgress.length > 0 && (
        <>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            In-progress uploads
          </p>
          <ul className="mb-8 flex flex-col gap-2">
            {inProgress.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center justify-between rounded-xl border border-slate-200 p-4"
              >
                <div>
                  <p className="font-medium text-slate-900">{doc.title}</p>
                  {doc.error && <p className="mt-1 text-sm text-red-600">{doc.error}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-medium ${IN_PROGRESS_STATUS_STYLES[doc.status as "pending" | "processing" | "failed"]}`}
                  >
                    {doc.status}
                  </span>
                  {doc.status === "failed" && (
                    <Button
                      className="bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100"
                      onClick={() => dismissInProgress(doc.id)}
                    >
                      Dismiss
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
        Your documents
      </p>
      {serverDocuments.length === 0 ? (
        <p className="text-sm text-slate-400">No documents uploaded yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {serverDocuments.map((doc) => (
            <li
              key={doc.document_id}
              className="flex items-center justify-between rounded-xl border border-slate-200 p-4"
            >
              <p className="font-medium text-slate-900">{doc.filename}</p>
              <Button
                className="bg-white text-red-600 ring-1 ring-red-200 hover:bg-red-50"
                onClick={() => void handleDelete(doc.document_id)}
                disabled={deletingId === doc.document_id}
              >
                {deletingId === doc.document_id ? "Deleting..." : "Delete"}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

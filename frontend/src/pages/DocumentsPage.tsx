import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch, uploadWithProgress } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";
import { getDocumentsStore, type RecentDocument } from "../lib/documentsStore";
import type { DocumentListResponse, DocumentSummary, JobStatusResponse } from "../lib/types";

const POLL_INTERVAL_MS = 2000;

// Must match the live `INGESTION_MAX_UPLOAD_SIZE_BYTES` backend setting (ERP-051) -- there is
// no settings-introspection endpoint, so this is a matching constant, not a fetched value.
const MAX_UPLOAD_SIZE_BYTES = 20_000_000;
const MAX_UPLOAD_SIZE_LABEL = "20 MB";

const IN_PROGRESS_STATUS_STYLES: Record<"pending" | "processing" | "failed", string> = {
  pending: "bg-amber-100 text-amber-700",
  processing: "bg-amber-100 text-amber-700",
  failed: "bg-red-100 text-red-700",
};

interface UploadInFlight {
  id: string;
  name: string;
  progress: number;
}

export default function DocumentsPage() {
  const { userId } = useAuth();
  // In-flight uploads only (pending/processing/failed) -- still tracked client-side, since a
  // job that hasn't finished (or never will) has no backend row to read back. A job that
  // reaches "done" is removed from here as soon as it's confirmed in `serverDocuments`.
  const [inProgress, setInProgress] = useState<RecentDocument[]>(() =>
    userId ? getDocumentsStore(userId).list().filter((doc) => doc.status !== "done") : []
  );
  const [serverDocuments, setServerDocuments] = useState<DocumentSummary[]>([]);
  // Byte-transfer progress only, for files still being sent -- separate from `inProgress`,
  // which starts only once the backend has accepted the file and created a job (ERP-054).
  const [uploadsInFlight, setUploadsInFlight] = useState<UploadInFlight[]>([]);
  const [rejectedFiles, setRejectedFiles] = useState<string[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
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

  async function uploadOne(file: File): Promise<void> {
    const uploadId = crypto.randomUUID();
    setUploadsInFlight((prev) => [...prev, { id: uploadId, name: file.name, progress: 0 }]);

    const result = await uploadWithProgress("/ingestion/pdf", file, (fraction) => {
      setUploadsInFlight((prev) =>
        prev.map((u) => (u.id === uploadId ? { ...u, progress: fraction } : u))
      );
    }).catch(() => null);

    setUploadsInFlight((prev) => prev.filter((u) => u.id !== uploadId));
    if (!result || !result.ok) return;

    const { job_id: jobId } = result.body as { job_id: string };
    updateInProgress({ id: jobId, title: file.name, lastUpdated: Date.now(), status: "pending" });
    void pollJob(jobId, file.name);
  }

  async function handleFiles(files: File[]): Promise<void> {
    const accepted: File[] = [];
    const rejected: string[] = [];
    for (const file of files) {
      if (file.size > MAX_UPLOAD_SIZE_BYTES) {
        rejected.push(file.name);
      } else {
        accepted.push(file);
      }
    }
    setRejectedFiles(rejected);
    await Promise.all(accepted.map((file) => uploadOne(file)));
  }

  function handleFileInputChange(): void {
    const files = fileInputRef.current?.files;
    if (!files || files.length === 0) return;
    void handleFiles(Array.from(files));
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setIsDragging(false);
    const files = Array.from(event.dataTransfer.files).filter(
      (file) => file.type === "application/pdf"
    );
    if (files.length > 0) void handleFiles(files);
  }

  async function handleDelete(documentId: string): Promise<void> {
    setDeletingId(documentId);
    const response = await apiFetch(`/documents/${documentId}`, { method: "DELETE" });
    setDeletingId(null);
    if (response.ok) {
      setServerDocuments((prev) => prev.filter((doc) => doc.document_id !== documentId));
    }
  }

  async function handleRetry(jobId: string, filename: string): Promise<void> {
    setRetryingId(jobId);
    const response = await apiFetch(`/ingestion/jobs/${jobId}/retry`, { method: "POST" });
    setRetryingId(null);
    if (!response.ok) return;

    const { job_id: newJobId } = (await response.json()) as { job_id: string };
    dismissInProgress(jobId);
    updateInProgress({ id: newJobId, title: filename, lastUpdated: Date.now(), status: "pending" });
    void pollJob(newJobId, filename);
  }

  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="mb-1 text-2xl font-semibold text-slate-900">Documents</h1>
      <p className="mb-6 text-sm text-slate-500">
        Upload one or more PDFs to make them searchable in Chat. Ingestion runs in the
        background.
      </p>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`mb-2 flex flex-col items-center gap-3 rounded-xl border border-dashed p-6 text-center transition-colors ${
          isDragging ? "border-slate-500 bg-slate-100" : "border-slate-300 bg-slate-50"
        }`}
      >
        <p className="text-sm text-slate-600">Drag and drop PDFs here, or</p>
        <div className="flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            multiple
            onChange={handleFileInputChange}
            className="text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white"
          />
        </div>
      </div>
      <p className="mb-6 text-xs text-slate-400">Maximum file size: {MAX_UPLOAD_SIZE_LABEL} per PDF.</p>

      {rejectedFiles.length > 0 && (
        <p className="mb-6 text-sm text-red-600">
          Too large (max {MAX_UPLOAD_SIZE_LABEL}), not uploaded: {rejectedFiles.join(", ")}
        </p>
      )}

      {uploadsInFlight.length > 0 && (
        <ul className="mb-8 flex flex-col gap-2">
          {uploadsInFlight.map((upload) => (
            <li key={upload.id} className="rounded-xl border border-slate-200 p-4">
              <p className="mb-2 truncate text-sm font-medium text-slate-900">{upload.name}</p>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                <div
                  className="h-full rounded-full bg-slate-900 transition-all"
                  style={{ width: `${Math.round(upload.progress * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}

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
                    <>
                      <Button
                        className="bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100"
                        onClick={() => void handleRetry(doc.id, doc.title)}
                        disabled={retryingId === doc.id}
                      >
                        {retryingId === doc.id ? "Retrying..." : "Retry"}
                      </Button>
                      <Button
                        className="bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100"
                        onClick={() => dismissInProgress(doc.id)}
                      >
                        Dismiss
                      </Button>
                    </>
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

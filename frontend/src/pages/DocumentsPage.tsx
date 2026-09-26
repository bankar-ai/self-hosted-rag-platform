import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import ConfidenceBadge from "../components/ConfidenceBadge";
import { apiFetch, uploadWithProgress } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";
import { getDocumentsStore, type RecentDocument } from "../lib/documentsStore";
import type {
  DocumentListResponse,
  DocumentSummary,
  JobListResponse,
  JobStatusResponse,
} from "../lib/types";

const POLL_INTERVAL_MS = 2000;

// ERP-091: how long a job can sit in "processing" before showing a cold-start hint -- the
// OCR-fallback path (Cloud Run docling, scale-to-zero) has a documented 1-2 minute cold start
// (ERP-086), well above ordinary fast-path processing time, so a job still processing past this
// threshold is likely mid-cold-start rather than just slow.
const PROCESSING_HINT_DELAY_MS = 20_000;

// Must match the live `INGESTION_MAX_UPLOAD_SIZE_BYTES` backend setting (ERP-051) -- there is
// no settings-introspection endpoint, so this is a matching constant, not a fetched value.
const MAX_UPLOAD_SIZE_BYTES = 20_000_000;
const MAX_UPLOAD_SIZE_LABEL = "20 MB";

// Must match the live `INGESTION_MAX_ACTIVE_JOBS_PER_USER` backend setting (ERP-094).
const MAX_FILES_PER_UPLOAD = 5;

const IN_PROGRESS_STATUS_STYLES: Record<"pending" | "processing" | "failed", string> = {
  pending: "bg-amber-100 text-amber-700",
  processing: "bg-amber-100 text-amber-700",
  failed: "bg-red-100 text-red-700",
};

interface UploadInFlight {
  id: string;
  file: File;
  progress: number;
  status: "uploading" | "failed";
  errorMessage?: string;
}

export default function DocumentsPage() {
  const { userId } = useAuth();
  // In-flight uploads only (pending/processing/failed). Seeded from localStorage for a fast
  // first paint, then reconciled against `GET /ingestion/jobs` (ERP-095) so a job started from
  // another device/session -- which has no entry in this browser's localStorage -- still shows
  // up here instead of only being visible to whichever browser initiated it. A job that reaches
  // "done" is removed from here as soon as it's confirmed in `serverDocuments`.
  const [inProgress, setInProgress] = useState<RecentDocument[]>(() =>
    userId ? getDocumentsStore(userId).list().filter((doc) => doc.status !== "done") : []
  );
  const [serverDocuments, setServerDocuments] = useState<DocumentSummary[]>([]);
  // Files chosen or dropped but not yet uploaded (ERP-070) -- upload only starts once the
  // user explicitly clicks "Upload", not on selection/drop.
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  // Byte-transfer progress/failure, for files currently being sent -- separate from
  // `inProgress`, which starts only once the backend has accepted the file and created a job.
  const [uploadsInFlight, setUploadsInFlight] = useState<UploadInFlight[]>([]);
  const [rejectedFiles, setRejectedFiles] = useState<string[]>([]);
  const [tooManyFilesMessage, setTooManyFilesMessage] = useState<string | null>(null);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(new Set());
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Tracks which job IDs already have a `pollJob` recursive-timeout loop running, so hydrating
  // active jobs from the server (ERP-095) never starts a second, redundant loop for a job this
  // same device already kicked off (and is therefore already polling) itself.
  const pollingJobIdsRef = useRef<Set<string>>(new Set());

  async function refreshServerDocuments(): Promise<void> {
    const response = await apiFetch("/documents");
    if (!response.ok) return;
    const body = (await response.json()) as DocumentListResponse;
    setServerDocuments(body.documents);
  }

  // Reads back the account's active (pending/processing/failed) jobs from the server (ERP-095)
  // -- not just this browser's own `localStorage` record -- so a job started on another device
  // (or a prior session on this one) still shows up here instead of only being visible to
  // whichever browser happened to initiate the upload.
  //
  // Also reconciles the other direction (ERP-099): a job seeded from this browser's own
  // `localStorage` as "pending"/"processing" that the server no longer reports as active has
  // reached a terminal state this device never observed (e.g. it finished, or was dismissed,
  // while this tab wasn't open/polling) -- the server's active-jobs list is authoritative, so a
  // locally-seeded job missing from it is treated as done, exactly like `pollJob`'s own "done"
  // branch, rather than being left showing "processing" forever on just this device.
  async function hydrateActiveJobsFromServer(): Promise<void> {
    const response = await apiFetch("/ingestion/jobs");
    if (!response.ok) return;
    const body = (await response.json()) as JobListResponse;
    const activeJobIds = new Set(body.jobs.map((job) => job.job_id));
    for (const job of body.jobs) {
      updateInProgress({
        id: job.job_id,
        title: job.filename,
        lastUpdated: Date.now(),
        status: job.status,
        error: job.error ?? undefined,
      });
      if (job.status === "pending" || job.status === "processing") {
        ensurePolling(job.job_id, job.filename);
      }
    }

    const store = getDocumentsStore(scopedUserId);
    const staleLocalJobs = store
      .list()
      .filter((doc) => (doc.status === "pending" || doc.status === "processing") && !activeJobIds.has(doc.id));
    if (staleLocalJobs.length === 0) return;
    for (const doc of staleLocalJobs) {
      store.remove(doc.id);
    }
    setInProgress(store.list().filter((d) => d.status !== "done"));
    await refreshServerDocuments();
  }

  useEffect(() => {
    void refreshServerDocuments();
    void hydrateActiveJobsFromServer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AuthGuard already guarantees a valid token (and therefore a decodable userId) before this
  // page can render at all -- this is a type-safety formality, not a loading state to wait out.
  if (!userId) {
    return null;
  }
  const scopedUserId = userId;

  // Preserves `startedAt` across repeated polls of the same job (ERP-091) -- carrying it
  // forward here, rather than requiring every call site to know/pass it, is what keeps it a
  // stable "first seen" timestamp instead of drifting forward on every 2s poll tick the way
  // `lastUpdated` deliberately does.
  function updateInProgress(doc: RecentDocument): void {
    const store = getDocumentsStore(scopedUserId);
    const existing = store.list().find((d) => d.id === doc.id);
    store.upsert({ ...doc, startedAt: existing?.startedAt ?? doc.startedAt ?? Date.now() });
    setInProgress(store.list().filter((d) => d.status !== "done"));
  }

  // Local-only cleanup: removes a job from the local documentsStore/`inProgress` state without
  // touching the backend. Safe to call when a retry is starting, since the retried job (see
  // `retry_job` in app/ingestion/jobs.py) deliberately reuses the SAME `pdf_path` as the
  // original -- the backend DELETE below unlinks that shared file, so it must never fire as a
  // side effect of retrying (Finding 1).
  function clearInProgressLocally(jobId: string): void {
    const store = getDocumentsStore(scopedUserId);
    store.remove(jobId);
    setInProgress(store.list().filter((d) => d.status !== "done"));
  }

  // Full dismiss: the user is walking away from this job entirely, so it's safe to delete the
  // job's uploaded file server-side too. Only ever wire this to an explicit "Dismiss" click.
  async function dismissInProgress(jobId: string): Promise<void> {
    pollingJobIdsRef.current.delete(jobId);
    await apiFetch(`/ingestion/jobs/${jobId}`, { method: "DELETE" }).catch(() => null);
    clearInProgressLocally(jobId);
  }

  async function pollJob(jobId: string, filename: string): Promise<void> {
    const response = await apiFetch(`/ingestion/jobs/${jobId}`);
    const job = (await response.json()) as JobStatusResponse;

    if (job.status === "pending" || job.status === "processing") {
      updateInProgress({ id: jobId, title: filename, lastUpdated: Date.now(), status: job.status });
      setTimeout(() => void pollJob(jobId, filename), POLL_INTERVAL_MS);
      return;
    }

    pollingJobIdsRef.current.delete(jobId);

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

  /** Starts a `pollJob` loop for `jobId` unless one is already running -- guards against
   * hydrating the same active job from the server (ERP-095) while this device is already
   * polling it because it was the one that started the upload. */
  function ensurePolling(jobId: string, filename: string): void {
    if (pollingJobIdsRef.current.has(jobId)) return;
    pollingJobIdsRef.current.add(jobId);
    void pollJob(jobId, filename);
  }

  async function uploadOne(file: File, existingId?: string): Promise<void> {
    const uploadId = existingId ?? crypto.randomUUID();
    setUploadsInFlight((prev) => [
      ...prev.filter((u) => u.id !== uploadId),
      { id: uploadId, file, progress: 0, status: "uploading" },
    ]);

    const result = await uploadWithProgress("/ingestion/pdf", file, (fraction) => {
      setUploadsInFlight((prev) =>
        prev.map((u) => (u.id === uploadId ? { ...u, progress: fraction } : u))
      );
    }).catch(() => null);

    if (!result || !result.ok) {
      // Prefer the backend's own explanation (e.g. the 429 "too many active jobs" message from
      // ERP-094) over a generic "Upload failed." -- a network-level failure (`result` is null)
      // or a response with no parseable `detail` still falls back to the generic text.
      const detail =
        result && typeof result.body === "object" && result.body !== null && "detail" in result.body
          ? (result.body as { detail?: unknown }).detail
          : undefined;
      const errorMessage = typeof detail === "string" ? detail : undefined;
      setUploadsInFlight((prev) =>
        prev.map((u) => (u.id === uploadId ? { ...u, status: "failed", errorMessage } : u))
      );
      return;
    }

    setUploadsInFlight((prev) => prev.filter((u) => u.id !== uploadId));
    const { job_id: jobId } = result.body as { job_id: string };
    updateInProgress({ id: jobId, title: file.name, lastUpdated: Date.now(), status: "pending" });
    ensurePolling(jobId, file.name);
  }

  function retryUpload(uploadId: string): void {
    const upload = uploadsInFlight.find((u) => u.id === uploadId);
    if (!upload) return;
    void uploadOne(upload.file, uploadId);
  }

  // Purely client-side: a failed upload transfer never created a backend job, so there's
  // nothing to clean up server-side -- just drop it from local state.
  function removeFailedUpload(uploadId: string): void {
    setUploadsInFlight((prev) => prev.filter((u) => u.id !== uploadId));
  }

  async function handleFiles(files: File[]): Promise<void> {
    await Promise.all(files.map((file) => uploadOne(file)));
  }

  function stageFiles(files: File[]): void {
    if (stagedFiles.length + files.length > MAX_FILES_PER_UPLOAD) {
      setTooManyFilesMessage(
        `Only ${MAX_FILES_PER_UPLOAD} files can be uploaded at a time. Remove some and try again.`
      );
      return;
    }
    setTooManyFilesMessage(null);
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
    setStagedFiles((prev) => [...prev, ...accepted]);
  }

  function removeStagedFile(index: number): void {
    setStagedFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function startStagedUpload(): Promise<void> {
    const files = stagedFiles;
    setStagedFiles([]);
    await handleFiles(files);
  }

  function handleFileInputChange(): void {
    const files = fileInputRef.current?.files;
    if (!files || files.length === 0) return;
    stageFiles(Array.from(files));
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setIsDragging(false);
    const files = Array.from(event.dataTransfer.files).filter(
      (file) => file.type === "application/pdf"
    );
    if (files.length > 0) stageFiles(files);
  }

  async function deleteDocuments(documentIds: string[]): Promise<void> {
    const names = serverDocuments
      .filter((doc) => documentIds.includes(doc.document_id))
      .map((doc) => doc.filename);
    const confirmed = window.confirm(
      names.length === 1
        ? `This will permanently delete "${names[0]}". This cannot be undone. Continue?`
        : `This will permanently delete ${names.length} files (${names.join(", ")}). This cannot be undone. Continue?`
    );
    if (!confirmed) return;

    setDeletingIds((prev) => new Set([...prev, ...documentIds]));
    // Each delete is caught individually so a network-level rejection on one document can never
    // stop `Promise.all` from resolving -- otherwise the cleanup below would never run, leaving
    // every attempted row stuck on "Deleting..." forever (Finding 3). A caught failure is treated
    // the same as a non-ok response: the id is left in place instead of removed (Finding 2).
    const results = await Promise.all(
      documentIds.map(async (id) => {
        const ok = await apiFetch(`/documents/${id}`, { method: "DELETE" })
          .then((response) => response.ok)
          .catch(() => false);
        return { id, ok };
      })
    );
    const succeededIds = new Set(results.filter((r) => r.ok).map((r) => r.id));
    setServerDocuments((prev) => prev.filter((doc) => !succeededIds.has(doc.document_id)));
    setSelectedDocumentIds((prev) => {
      const next = new Set(prev);
      succeededIds.forEach((id) => next.delete(id));
      return next;
    });
    setDeletingIds((prev) => {
      const next = new Set(prev);
      documentIds.forEach((id) => next.delete(id));
      return next;
    });
  }

  function toggleDocumentSelection(documentId: string): void {
    setSelectedDocumentIds((prev) => {
      const next = new Set(prev);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
      }
      return next;
    });
  }

  function toggleSelectAll(): void {
    setSelectedDocumentIds((prev) =>
      prev.size === serverDocuments.length
        ? new Set()
        : new Set(serverDocuments.map((d) => d.document_id))
    );
  }

  async function handleRetry(jobId: string, filename: string): Promise<void> {
    setRetryingId(jobId);
    const response = await apiFetch(`/ingestion/jobs/${jobId}/retry`, { method: "POST" });
    setRetryingId(null);
    if (!response.ok) return;

    const { job_id: newJobId } = (await response.json()) as { job_id: string };
    clearInProgressLocally(jobId);
    updateInProgress({ id: newJobId, title: filename, lastUpdated: Date.now(), status: "pending" });
    ensurePolling(newJobId, filename);
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
            aria-label="Choose PDF files"
            onChange={handleFileInputChange}
            className="text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-brand file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white"
          />
        </div>
      </div>
      <p className="mb-2 text-xs text-slate-400">Maximum file size: {MAX_UPLOAD_SIZE_LABEL} per PDF.</p>

      {rejectedFiles.length > 0 && (
        <p className="mb-6 text-sm text-red-600">
          Too large (max {MAX_UPLOAD_SIZE_LABEL}), not uploaded: {rejectedFiles.join(", ")}
        </p>
      )}

      {tooManyFilesMessage && (
        <p className="mb-6 text-sm text-red-600">{tooManyFilesMessage}</p>
      )}

      {stagedFiles.length > 0 && (
        <div className="mb-6 rounded-xl border border-slate-200 p-4">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Ready to upload
          </p>
          <ul className="mb-3 flex flex-col gap-1">
            {stagedFiles.map((file, index) => (
              <li
                key={`${file.name}-${index}`}
                className="flex items-center justify-between text-sm text-slate-700"
              >
                <span className="truncate">{file.name}</span>
                <button
                  type="button"
                  className="ml-2 shrink-0 text-xs text-slate-400 hover:text-slate-700"
                  onClick={() => removeStagedFile(index)}
                  aria-label={`Remove ${file.name} from upload`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <Button onClick={() => void startStagedUpload()}>
            Upload {stagedFiles.length} file{stagedFiles.length === 1 ? "" : "s"}
          </Button>
        </div>
      )}

      {uploadsInFlight.length > 0 && (
        <ul className="mb-8 flex flex-col gap-2">
          {uploadsInFlight.map((upload) => (
            <li key={upload.id} className="rounded-xl border border-slate-200 p-4">
              <p className="mb-2 truncate text-sm font-medium text-slate-900">{upload.file.name}</p>
              {upload.status === "failed" ? (
                <div className="flex items-center justify-between">
                  <p className="text-sm text-red-600">{upload.errorMessage ?? "Upload failed."}</p>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" onClick={() => retryUpload(upload.id)}>
                      Try again
                    </Button>
                    <button
                      type="button"
                      className="shrink-0 text-xs text-slate-400 hover:text-slate-700"
                      onClick={() => removeFailedUpload(upload.id)}
                      aria-label={`Remove ${upload.file.name} from uploads`}
                    >
                      ✕
                    </button>
                  </div>
                </div>
              ) : (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                  <div
                    className="h-full rounded-full bg-slate-900 transition-all"
                    style={{ width: `${Math.round(upload.progress * 100)}%` }}
                  />
                </div>
              )}
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
                  {doc.status === "processing" &&
                    doc.startedAt !== undefined &&
                    Date.now() - doc.startedAt > PROCESSING_HINT_DELAY_MS && (
                      <p className="mt-1 text-xs text-slate-400">
                        Complex documents may need extra processing time (up to a couple of
                        minutes).
                      </p>
                    )}
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
                        variant="outline"
                        onClick={() => void handleRetry(doc.id, doc.title)}
                        disabled={retryingId === doc.id}
                      >
                        {retryingId === doc.id ? "Retrying..." : "Retry"}
                      </Button>
                      <Button variant="outline" onClick={() => void dismissInProgress(doc.id)}>
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

      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Your documents
        </p>
        {selectedDocumentIds.size > 0 && (
          <Button
            variant="danger"
            onClick={() => void deleteDocuments(Array.from(selectedDocumentIds))}
          >
            Delete selected ({selectedDocumentIds.size})
          </Button>
        )}
      </div>
      {serverDocuments.length === 0 ? (
        <p className="text-sm text-slate-400">No documents uploaded yet.</p>
      ) : (
        <>
          <label className="mb-2 flex items-center gap-2 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={selectedDocumentIds.size === serverDocuments.length}
              onChange={toggleSelectAll}
            />
            Select all
          </label>
          <ul className="flex flex-col gap-2">
            {serverDocuments.map((doc) => (
              <li
                key={doc.document_id}
                className="flex items-center justify-between rounded-xl border border-slate-200 p-4"
              >
                <label className="flex min-w-0 items-center gap-3">
                  <input
                    type="checkbox"
                    checked={selectedDocumentIds.has(doc.document_id)}
                    onChange={() => toggleDocumentSelection(doc.document_id)}
                  />
                  <span className="truncate font-medium text-slate-900">{doc.filename}</span>
                  <ConfidenceBadge confidence={doc.parsing_confidence} />
                </label>
                <Button
                  variant="danger"
                  onClick={() => void deleteDocuments([doc.document_id])}
                  disabled={deletingIds.has(doc.document_id)}
                >
                  {deletingIds.has(doc.document_id) ? "Deleting..." : "Delete"}
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

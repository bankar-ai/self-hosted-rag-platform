import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "../lib/apiClient";
import { documentsStore, type RecentDocument } from "../lib/documentsStore";
import type { JobStatusResponse } from "../lib/types";

const POLL_INTERVAL_MS = 2000;

const STATUS_STYLES: Record<RecentDocument["status"], string> = {
  pending: "bg-amber-100 text-amber-700",
  processing: "bg-amber-100 text-amber-700",
  done: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<RecentDocument[]>(() => documentsStore.list());
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function updateDocument(doc: RecentDocument): void {
    documentsStore.upsert(doc);
    setDocuments(documentsStore.list());
  }

  async function pollJob(jobId: string, filename: string): Promise<void> {
    const response = await apiFetch(`/ingestion/jobs/${jobId}`);
    const job = (await response.json()) as JobStatusResponse;

    updateDocument({
      id: jobId,
      title: filename,
      lastUpdated: Date.now(),
      status: job.status,
      error: job.error ?? undefined,
    });

    if (job.status === "pending" || job.status === "processing") {
      setTimeout(() => void pollJob(jobId, filename), POLL_INTERVAL_MS);
    }
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
    updateDocument({ id: jobId, title: file.name, lastUpdated: Date.now(), status: "pending" });
    void pollJob(jobId, file.name);

    if (fileInputRef.current) fileInputRef.current.value = "";
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
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
        Recent uploads
      </p>
      {documents.length === 0 ? (
        <p className="text-sm text-slate-400">No documents uploaded yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {documents.map((doc) => (
            <li
              key={doc.id}
              className="flex items-center justify-between rounded-xl border border-slate-200 p-4"
            >
              <div>
                <p className="font-medium text-slate-900">{doc.title}</p>
                {doc.error && <p className="mt-1 text-sm text-red-600">{doc.error}</p>}
              </div>
              <span
                className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_STYLES[doc.status]}`}
              >
                {doc.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

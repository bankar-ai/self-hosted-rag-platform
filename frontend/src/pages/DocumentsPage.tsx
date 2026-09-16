import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "../lib/apiClient";
import { documentsStore, type RecentDocument } from "../lib/documentsStore";
import type { JobStatusResponse } from "../lib/types";

const POLL_INTERVAL_MS = 2000;

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
    <div className="mx-auto max-w-2xl p-4">
      <h1 className="mb-4 text-xl font-semibold">Documents</h1>
      <div className="mb-6 flex gap-2">
        <input ref={fileInputRef} type="file" accept="application/pdf" />
        <Button onClick={() => void handleUpload()} disabled={isUploading}>
          {isUploading ? "Uploading..." : "Upload"}
        </Button>
      </div>
      <ul className="flex flex-col gap-2">
        {documents.map((doc) => (
          <li key={doc.id} className="rounded border p-3">
            <p className="font-medium">{doc.title}</p>
            <p className="text-sm text-gray-500">Status: {doc.status}</p>
            {doc.error && <p className="text-sm text-red-600">{doc.error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

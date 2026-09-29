/** ERP-116: what /auth/login, /auth/register (via login), and /auth/refresh return now --
 * the tokens themselves are httpOnly cookies, never in this body. */
export interface AuthActionResponse {
  user_id: string;
  csrf_token: string;
}

export interface UserResponse {
  id: string;
  email: string;
  role: "admin" | "user";
  is_active: boolean;
}

export interface Citation {
  /** The original [n] number this citation was cited with (ERP-098) -- match inline markers
   * against this field, never against this citation's position in a `Citation[]` array, since
   * that array only contains cited chunks and is not stable across a non-contiguous/out-of-
   * order cited subset. */
  marker: number;
  chunk_id: string;
  document_id: string;
  section_path: string[];
  page_start: number;
  page_end: number;
  source_filename: string;
  score: number;
  reranked: boolean;
}

export interface GenerationRequest {
  query: string;
  top_k?: number;
  rerank?: boolean;
  expand_sections?: boolean;
  conversation_id?: string;
  document_ids?: string[];
}

export type JobStatus = "pending" | "processing" | "done" | "failed";

export interface Chunk {
  chunk_id: string;
  document_id: string;
  chunk_index: number;
  text: string;
  section_path: string[];
  page_start: number;
  page_end: number;
  char_count: number;
  parser_used: "fast" | "quality";
  source_filename: string;
}

export interface IngestResponse {
  document_id: string;
  chunks: Chunk[];
}

export interface JobStatusResponse {
  status: JobStatus;
  result: IngestResponse | null;
  error: string | null;
}

export interface JobSummary {
  job_id: string;
  filename: string;
  status: JobStatus;
  error: string | null;
}

export interface JobListResponse {
  jobs: JobSummary[];
}

export interface DocumentSummary {
  document_id: string;
  filename: string;
  created_at: string;
  parsing_confidence: string;
}

export interface DocumentListResponse {
  documents: DocumentSummary[];
  has_more: boolean;
}

export interface ChunkDetail {
  chunk_id: string;
  document_id: string;
  text: string;
  section_path: string[];
  page_start: number;
  page_end: number;
  source_filename: string;
}

export interface ConversationSummary {
  conversation_id: string;
  created_at: string;
  preview: string | null;
  title: string | null;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  has_more: boolean;
}

export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
  feedback: "up" | "down" | null;
  citations: Citation[];
  /** The rerank/expand_sections/document_ids this assistant turn's generation call actually
   * used (ERP-107) -- `null` for a user-role message or one persisted before this field
   * existed. Not currently rendered anywhere in the UI. */
  retrieval_settings: Record<string, unknown> | null;
  /** End-to-end wall-clock time this assistant turn took to generate, in seconds (ERP-115) --
   * `null` for a user-role message or one persisted before this field existed. */
  duration_seconds: number | null;
}

export interface ConversationHistoryResponse {
  conversation_id: string;
  messages: ConversationMessage[];
}

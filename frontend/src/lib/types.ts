export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
}

export interface UserResponse {
  id: string;
  email: string;
  role: "admin" | "user";
  is_active: boolean;
}

export interface Citation {
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

export interface DocumentSummary {
  document_id: string;
  filename: string;
  created_at: string;
}

export interface DocumentListResponse {
  documents: DocumentSummary[];
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
}

export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
  feedback: "up" | "down" | null;
  citations: Citation[];
}

export interface ConversationHistoryResponse {
  conversation_id: string;
  messages: ConversationMessage[];
}

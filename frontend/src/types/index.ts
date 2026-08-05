/** Shared API types. These mirror the pydantic schemas in backend/schemas/. */

export type Department =
  | "Finance"
  | "HR"
  | "IT"
  | "Production"
  | "Purchase";

export type Role = "user" | "admin" | "super_admin";

export type AnswerSource =
  | "knowledge_base"
  | "sql_agent"
  | "refused_out_of_scope"
  | "refused_no_evidence"
  | "error";

export interface User {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
  department: Department;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string | null;
}

export interface DepartmentInfo {
  name: Department;
  description: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Citation {
  marker?: number;
  chunk_id: number;
  document_id: number;
  document: string;
  filename: string;
  page: number | null;
  heading: string | null;
  section: string | null;
  snippet: string;
  score: number;
}

/** Per-stage latency in milliseconds. */
export interface Timings {
  gate_ms?: number;
  rewrite_ms?: number;
  route_ms?: number;
  retrieve_ms?: number;
  rerank_ms?: number;
  generate_ms?: number;
  verify_ms?: number;
  sql_agent_ms?: number;
  first_token_ms?: number;
  total_ms?: number;
}

export interface Message {
  id: number;
  uuid: string;
  role: "user" | "assistant" | "system";
  content: string;
  answer_source: AnswerSource | null;
  rewritten_query: string | null;
  citations: Citation[];
  timings: Timings;
  confidence: number | null;
  model: string | null;
  total_latency_ms: number | null;
  feedback: number | null;
  created_at: string;
}

export interface ConversationSummary {
  id: number;
  uuid: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[];
}

export interface DocumentRecord {
  id: number;
  uuid: string;
  filename: string;
  original_filename: string;
  title: string | null;
  department: Department;
  uploaded_by_username: string;
  file_type: string;
  size_bytes: number;
  status: "pending" | "processing" | "completed" | "failed";
  error_message: string | null;
  page_count: number;
  chunk_count: number;
  processing_seconds: number | null;
  doc_metadata: Record<string, unknown>;
  created_at: string;
  processed_at: string | null;
}

export interface DocumentList {
  documents: DocumentRecord[];
  total: number;
  department: Department;
}

export interface IngestionSummary {
  department: Department;
  total_documents: number;
  completed: number;
  processing: number;
  failed: number;
  total_chunks: number;
  total_pages: number;
  last_ingested_at: string | null;
}

export interface Analytics {
  department: Department;
  users: { total: number; admins: number };
  documents: IngestionSummary;
  chat: {
    total_answers: number;
    avg_latency_ms: number | null;
    avg_confidence: number | null;
    refusals: number;
    refusal_rate: number;
    feedback_helpful: number;
    feedback_unhelpful: number;
  };
}

/* -------------------------------------------------------------------------
 * Streaming
 * ---------------------------------------------------------------------- */

export interface StreamStartData {
  conversation_id: string;
  message_id: string;
  request_id: string;
}

export interface StreamStageData {
  stage: string;
  label: string;
  elapsed_ms: number;
}

export interface StreamDoneData {
  message_id: string;
  conversation_id: string;
  answer_source: AnswerSource;
  confidence: number | null;
  grounded: boolean;
  rewritten_query: string | null;
  model: string | null;
  timings: Timings;
  conversation_title: string | null;
}

export interface StreamErrorData {
  message: string;
  code: string;
}

export interface StreamHandlers {
  onStart?: (data: StreamStartData) => void;
  onStage?: (data: StreamStageData) => void;
  onToken?: (text: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onDone?: (data: StreamDoneData) => void;
  onError?: (data: StreamErrorData) => void;
}

/** A chat turn as held in local UI state (may still be streaming). */
export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timings?: Timings;
  answerSource?: AnswerSource;
  confidence?: number | null;
  grounded?: boolean;
  rewrittenQuery?: string | null;
  model?: string | null;
  feedback?: number | null;
  createdAt: string;
  isStreaming?: boolean;
  stage?: string | null;
  error?: string | null;
}

export interface ApiErrorShape {
  error: string;
  message: string;
  details?: Record<string, unknown>;
}

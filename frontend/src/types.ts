export type Project = {
  id: string;
  title: string;
  research_question: string;
  article_type: string;
  target_audience: string | null;
  language: string;
  target_words: number;
  citation_style: string;
  status: string;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type Paper = {
  id: string;
  project_id: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  venue: string | null;
  abstract: string | null;
  source: string | null;
  source_url: string | null;
  pdf_url: string | null;
  oa_status: string | null;
  license: string | null;
  local_pdf_path: string | null;
  local_tei_path: string | null;
  relevance_score: number;
  selected: boolean;
  parse_status: string;
  metadata_json: Record<string, unknown>;
};

export type EvidenceCard = {
  id: string;
  project_id: string;
  paper_id: string;
  chunk_ids: string[];
  claim: string;
  supporting_text: string;
  evidence_type: string | null;
  strength: string | null;
  limitations: string | null;
  page_start: number | null;
  page_end: number | null;
  citation_key: string | null;
  used_in_draft: boolean;
};

export type Draft = {
  id: string;
  project_id: string;
  version: number;
  title: string | null;
  content_md: string;
  status: string;
  quality_score: Record<string, unknown>;
  created_at: string;
};

export type ReviewIssue = {
  id: string;
  project_id: string;
  draft_id: string;
  severity: string;
  issue_type: string;
  location: string | null;
  claim: string | null;
  description: string;
  suggestion: string | null;
  evidence_ids: string[];
  resolved: boolean;
  created_at: string;
};

export type TaskPayload = {
  task_id: string;
  status: string;
  progress: number;
  current_step: string;
  logs: string[];
  result: Record<string, unknown>;
  updated_at: string;
};

export type AutoWorkflowResult = {
  task_id: string;
  query: string;
  inserted_count: number;
  total_papers: number;
  selected_count: number;
  auto_selected_count: number;
  reused_local_pdf_count: number;
  resolved_via_fallback_count: number;
  downloaded_count: number;
  parsed_count: number;
  skipped_no_pdf_count: number;
  failed_count: number;
  evidence_count: number;
  draft_id: string;
  revised_draft_id: string;
  review_issue_count: number;
  critical_issue_count: number;
  publication_prepared?: boolean;
  quality_gate?: Record<string, unknown>;
  export_files: Record<string, string>;
};

export type LLMProviderModel = {
  id: string;
  name: string;
  context_length: number;
  supports_chinese: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
  description: string | null;
};

export type LLMProvider = {
  id: string;
  name: string;
  logo_svg: string;
  description: string;
  requires_api_key: boolean;
  supports_custom_base: boolean;
  default_base_url: string | null;
  models: LLMProviderModel[];
};

export type LLMConfig = {
  id: string;
  name: string;
  provider: string;
  model: string;
  api_key?: string | null;
  api_base: string | null;
  temperature: number;
  max_tokens: number;
  timeout: number;
  proxy_url: string | null;
  use_system_proxy: boolean;
  extra_headers: Record<string, unknown>;
  extra_body: Record<string, unknown>;
  strategy_mode: string;
  enable_reasoning: boolean;
  preferred_max_tokens: number | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type LLMPreset = {
  id: string;
  name: string;
  logo_svg: string;
  description: string;
  requires_api_key: boolean;
  supports_custom_base: boolean;
  default_base_url: string | null;
  models: LLMProviderModel[];
};

export type LLMConfigListResponse = {
  configs: LLMConfig[];
  active_id: string | null;
};

export type LLMTestResult = {
  success: boolean;
  latency_ms: number;
  message: string;
  model: string | null;
  usage: Record<string, unknown> | null;
};

export type ModalityType = 'POINT' | 'PROJECTION' | 'ESTIMATE' | 'RANGE' | string;

export interface EvidenceItem {
  doc_id: string;
  page: number;
  char_start: number;
  char_end: number;
  verbatim_quote: string;
  bbox: [number, number, number, number] | null;
  page_width: number | null;
  page_height: number | null;
  page_image_resolution: number;
  extractor: string;
  verified: boolean;
  // A8 — additive table-context fields; null when the fact's value wasn't
  // confidently attributed to exactly one table cell.
  table_id?: string | null;
  row_index?: number | null;
  column_index?: number | null;
  cell_bbox?: [number, number, number, number] | null;
  row_label?: string | null;
  column_header?: string | null;
  unit_context?: string | null;
}

export interface FactValue {
  raw: string;
  normalized: string;
  unit?: string | null;
  currency?: string | null;
  sig_figs?: number | null;
}

export interface FactQualifiers {
  period?: {
    kind?: string;
    start?: string | null;
    end?: string | null;
    label?: string | null;
  } | null;
  as_of?: string | null;
  scope?: string | null;
  basis?: string | null;
  segment?: string | null;
  geography?: string | null;
  issuer?: string | null;
  extra?: Record<string, any>;
}

export interface FactSummary {
  fact_id: string;
  subject: string;
  subject_raw: string;
  measure: string;
  measure_raw: string;
  value_kind: string;
  value: FactValue;
  qualifiers: FactQualifiers;
  modality: ModalityType;
  confidence: number;
  doc_id: string | null;
  page: number | null;
  evidence_count?: number;
  // Deterministic check that value_raw is the number the verified quote
  // actually supports — distinct from (and stronger than) span/quote
  // verification. null/undefined on facts persisted before this existed.
  value_verification?: 'verified' | 'verified_with_context' | 'unverified' | 'mismatch' | null;
  value_verification_reason?: string | null;
}

export interface FactFull extends FactSummary {
  evidence: EvidenceItem[];
}

export type RelationType =
  | 'CORROBORATES'
  | 'CONTRADICTS'
  | 'APPARENT_CONFLICT'
  | 'SUPERSEDES'
  | 'AGGREGATES_INTO';

export interface GateInfo {
  verdict: string;
  reason_code: string;
  explanation: string;
  period_relation?: string;
  cross_issuer?: boolean;
  qualifier_diff?: Record<string, any>;
}

export interface RelationSummary {
  relation_id: string;
  source_fact_id: string;
  target_fact_id: string;
  source_summary: string | null;
  target_summary: string | null;
  relation: RelationType;
  confidence: number;
  reason_code: string;
  decided_by: string;
}

export interface RelationFull extends RelationSummary {
  explanation: string;
  qualifier_diff: Record<string, any>;
  source_fact: FactFull | null;
  target_fact: FactFull | null;
  gate?: GateInfo;
}

export interface DocumentDiagnostics {
  total_pages: number;
  text_pages: number;
  image_only_pages: number;
  sparse_pages: number;
  tables_detected: number;
  pages_with_tables: number;
  repeated_header_candidates: string[];
  repeated_footer_candidates: string[];
  warnings: string[];
}

export interface DocumentInfo {
  doc_id: string;
  filename: string;
  fact_count: number;
  n_pages?: number;
  table_strategy?: string;
  diagnostics?: DocumentDiagnostics | null;
}

// Matches the real JSON shape served by GET /clusters (api.py's
// list_clusters()) verbatim — a previous version of this type declared
// `key`/`subject`/`measure`/`fact_ids`/`sample_values`/`issuers`/
// `relation_count` fields that the backend has never sent (the actual
// payload is `cluster_key`/`size`/`facts`/`relations`), so every cluster
// card silently rendered blank subject/measure labels and an undefined
// React key.
export interface ClusterInfo {
  cluster_key: string;
  size: number;
  facts: FactSummary[];
  relations: RelationSummary[];
}

export interface StatsResponse {
  documents: {
    count: number;
    filenames: string[];
  };
  facts: {
    total: number;
    by_doc: Record<string, number>;
  };
  span_verification: {
    verified: number;
    rejected: number;
    rejected_by_reason: Record<string, number>;
    pass_rate: number | null;
  };
  relations: {
    total: number;
    by_type: Record<string, number>;
  };
  coverage: {
    facts_in_any_relation: number;
    facts_in_any_relation_pct: number | null;
    facts_in_multi_fact_cluster: number;
    facts_in_multi_fact_cluster_pct: number | null;
  };
  canonical: {
    subjects: string[];
    measures: string[];
    issuers: string[];
  };
  clusters: {
    total: number;
    with_2plus_facts: number;
  };
  resolution?: any;
  llm_this_process?: Record<string, any>;
}

// Matches the real JSONL row shape written by extract.py's _append_rejected()
// and served verbatim by GET /rejected-facts — a previous version of this
// type declared top-level `raw_quote`/`page`/`subject`/`measure` fields that
// don't exist on the actual record (the real quote/subject/measure live
// nested under `raw_fact`), so every rejected-fact card silently fell back
// to a hard-coded placeholder string instead of the real quote.
export interface RejectedFact {
  raw_fact?: {
    subject_raw?: string;
    measure_raw?: string;
    value_raw?: string;
    verbatim_quote?: string;
    period_raw?: string | null;
    [key: string]: any;
  };
  page_no?: number;
  doc_id?: string;
  doc_filename?: string;
  reason?: string;
  detail?: string;
  [key: string]: any;
}

export interface IngestResponse {
  status: 'indexed' | 'already_indexed' | 'failed' | string;
  doc_id: string;
  filename: string;
  facts_extracted: number;
  relations_formed?: number;
  message?: string;
}

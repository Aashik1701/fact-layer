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
  value_verification?: 'verified' | 'unverified' | 'mismatch' | null;
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

export interface DocumentInfo {
  doc_id: string;
  filename: string;
  fact_count: number;
  n_pages?: number;
  table_strategy?: string;
}

export interface ClusterInfo {
  key: string;
  subject: string;
  measure: string;
  size: number;
  fact_ids: string[];
  sample_values?: string[];
  issuers?: string[];
  relation_count?: number;
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

export interface RejectedFact {
  raw_quote?: string;
  page?: number;
  reason?: string;
  doc_id?: string;
  filename?: string;
  subject?: string;
  measure?: string;
  raw_value?: string;
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

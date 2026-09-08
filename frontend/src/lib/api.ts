import {
  FactSummary,
  FactFull,
  RelationSummary,
  RelationFull,
  DocumentInfo,
  ClusterInfo,
  StatsResponse,
  RejectedFact,
  IngestAcceptedResponse,
  IngestJob,
  RetrievalStats,
  ComparabilityExplanation,
  GraphNeighborhood,
  GraphSearchResult,
  GraphNodeType,
  FactCandidatesResponse,
} from '@/types';

const API_BASE = '';

export async function fetchStats(): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/stats`);
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.statusText}`);
  return res.json();
}

export async function fetchDocuments(): Promise<{ documents: DocumentInfo[]; total: number }> {
  const res = await fetch(`${API_BASE}/documents`);
  if (!res.ok) throw new Error(`Failed to fetch documents: ${res.statusText}`);
  return res.json();
}

export interface FactFilterParams {
  doc_id?: string;
  subject?: string;
  measure?: string;
  min_confidence?: number;
  limit?: number;
  offset?: number;
}

export async function fetchFacts(params?: FactFilterParams): Promise<{
  facts: FactSummary[];
  total: number;
  limit: number;
  offset: number;
}> {
  const query = new URLSearchParams();
  if (params?.doc_id) query.set('doc_id', params.doc_id);
  if (params?.subject) query.set('subject', params.subject);
  if (params?.measure) query.set('measure', params.measure);
  if (params?.min_confidence !== undefined) query.set('min_confidence', params.min_confidence.toString());
  if (params?.limit !== undefined) query.set('limit', params.limit.toString());
  if (params?.offset !== undefined) query.set('offset', params.offset.toString());

  const res = await fetch(`${API_BASE}/facts?${query.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch facts: ${res.statusText}`);
  return res.json();
}

export async function fetchFact(factId: string): Promise<FactFull> {
  const res = await fetch(`${API_BASE}/facts/${encodeURIComponent(factId)}`);
  if (!res.ok) throw new Error(`Failed to fetch fact ${factId}: ${res.statusText}`);
  return res.json();
}

export interface RelationFilterParams {
  type?: string;
  min_confidence?: number;
  doc_id?: string;
  fact_id?: string;
}

export async function fetchRelations(params?: RelationFilterParams): Promise<{
  relations: RelationSummary[];
  total: number;
}> {
  const query = new URLSearchParams();
  if (params?.type) query.set('type', params.type);
  if (params?.min_confidence !== undefined) query.set('min_confidence', params.min_confidence.toString());
  if (params?.doc_id) query.set('doc_id', params.doc_id);
  if (params?.fact_id) query.set('fact_id', params.fact_id);

  const res = await fetch(`${API_BASE}/relations?${query.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch relations: ${res.statusText}`);
  return res.json();
}

export async function fetchRelation(relationId: string): Promise<RelationFull> {
  const res = await fetch(`${API_BASE}/relations/${encodeURIComponent(relationId)}`);
  if (!res.ok) throw new Error(`Failed to fetch relation ${relationId}: ${res.statusText}`);
  return res.json();
}

export async function fetchClusters(min_size: number = 1): Promise<{
  clusters: ClusterInfo[];
  total: number;
}> {
  const res = await fetch(`${API_BASE}/clusters?min_size=${min_size}`);
  if (!res.ok) throw new Error(`Failed to fetch clusters: ${res.statusText}`);
  return res.json();
}

export async function fetchRejectedFacts(limit: number = 200, reason?: string): Promise<{
  rejected_facts: RejectedFact[];
  total: number;
}> {
  const query = new URLSearchParams();
  query.set('limit', limit.toString());
  if (reason) query.set('reason', reason);

  const res = await fetch(`${API_BASE}/rejected-facts?${query.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch rejected facts: ${res.statusText}`);
  return res.json();
}

// Starts ingestion and returns immediately with a job to poll (202
// Accepted) — this never waits for the pipeline to finish. Use fetchJob()
// to follow progress; see DocumentUploadZone.tsx for the polling loop.
export async function ingestDocument(file: File): Promise<IngestAcceptedResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${API_BASE}/ingest`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const errData = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errData.detail || `Upload failed with status ${res.status}`);
  }
  return res.json();
}

export async function fetchJob(jobId: string): Promise<IngestJob> {
  const res = await fetch(`${API_BASE}/jobs/${encodeURIComponent(jobId)}`);
  if (!res.ok) {
    const errData = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errData.detail || `Failed to fetch job ${jobId}: ${res.statusText}`);
  }
  return res.json();
}

export function getPageImageUrl(docId: string, page: number): string {
  return `${API_BASE}/page-image/${encodeURIComponent(docId)}/${page}`;
}

// Retrieval + Scale layer diagnostics (fact_layer/retrieval/). Both
// endpoints are read-only investigation views — see api.py's endpoint
// docstring for why they work regardless of RETRIEVAL_ENABLED.
export async function fetchRetrievalStats(): Promise<RetrievalStats> {
  const res = await fetch(`${API_BASE}/retrieval/stats`);
  if (!res.ok) throw new Error(`Failed to fetch retrieval stats: ${res.statusText}`);
  return res.json();
}

export async function fetchFactCandidates(factId: string, topK?: number): Promise<FactCandidatesResponse> {
  const query = new URLSearchParams();
  if (topK !== undefined) query.set('top_k', topK.toString());
  const res = await fetch(`${API_BASE}/facts/${encodeURIComponent(factId)}/candidates?${query.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch candidates for fact ${factId}: ${res.statusText}`);
  return res.json();
}

// Comparability Investigator (fact_layer/investigate.py). Deterministic and
// offline: two stored facts in, a structured explanation of the existing
// gate's verdict out. No LLM, no embedding, no re-extraction.
export async function fetchComparability(
  factAId: string,
  factBId: string
): Promise<ComparabilityExplanation> {
  const res = await fetch(
    `${API_BASE}/facts/${encodeURIComponent(factAId)}/comparability/${encodeURIComponent(factBId)}`
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch comparability for ${factAId} vs ${factBId}: ${res.statusText}`);
  }
  return res.json();
}

// Knowledge graph projection (fact_layer/graph.py). Bounded server-side:
// depth, node and fan-out caps are enforced by the API, not by this client.
export async function fetchGraphNeighborhood(
  nodeType: GraphNodeType,
  nodeId: string,
  opts: { depth?: number; index?: number; maxNodes?: number; maxFanout?: number } = {}
): Promise<GraphNeighborhood> {
  const query = new URLSearchParams();
  if (opts.depth !== undefined) query.set('depth', String(opts.depth));
  if (opts.index !== undefined) query.set('index', String(opts.index));
  if (opts.maxNodes !== undefined) query.set('max_nodes', String(opts.maxNodes));
  if (opts.maxFanout !== undefined) query.set('max_fanout', String(opts.maxFanout));
  const res = await fetch(
    `${API_BASE}/graph/${nodeType}/${encodeURIComponent(nodeId)}?${query.toString()}`
  );
  if (!res.ok) throw new Error(`Failed to fetch graph for ${nodeType} ${nodeId}: ${res.statusText}`);
  return res.json();
}

export async function searchGraph(q: string, limit = 20): Promise<GraphSearchResult[]> {
  const query = new URLSearchParams({ q, limit: String(limit) });
  const res = await fetch(`${API_BASE}/graph/search?${query.toString()}`);
  if (!res.ok) throw new Error(`Graph search failed: ${res.statusText}`);
  return (await res.json()).results;
}

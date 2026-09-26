import type { EvaluationRun, Dataset, OverviewMetrics, FailureCluster } from '../types/evalx';

export const BACKEND_URL = import.meta.env.VITE_API_BASE_URL || 'https://api-production-bf18c.up.railway.app';

export interface BackendHealth {
  status: 'online' | 'degraded' | 'offline';
  latencyMs: number;
  live: boolean;
  ready: boolean;
  timestamp: string;
  workerId?: string;
  redisReachable?: boolean;
  workerHeartbeat?: string;
}

export interface ApiResponse<T> {
  data: T;
  unauthenticated: boolean;
  error?: string;
}

// Check real Railway backend health and worker telemetry
export async function checkBackendHealth(): Promise<BackendHealth> {
  const start = performance.now();
  try {
    const [liveRes, workerRes] = await Promise.all([
      fetch(`${BACKEND_URL}/api/v1/health/live`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(4000)
      }),
      fetch(`${BACKEND_URL}/api/v1/health/worker`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(4000)
      }).catch(() => null)
    ]);

    const latency = Math.round(performance.now() - start);
    let workerData: { details?: { worker_id?: string }; redis_reachable?: boolean; worker_heartbeat?: string } = {};

    if (workerRes && workerRes.ok) {
      try {
        workerData = await workerRes.json();
      } catch {
        // ignore parse error
      }
    }

    return {
      status: liveRes.ok ? 'online' : 'degraded',
      latencyMs: latency,
      live: liveRes.ok,
      ready: liveRes.ok,
      timestamp: new Date().toISOString(),
      workerId: workerData.details?.worker_id,
      redisReachable: workerData.redis_reachable,
      workerHeartbeat: workerData.worker_heartbeat
    };
  } catch {
    return {
      status: 'offline',
      latencyMs: 0,
      live: false,
      ready: false,
      timestamp: new Date().toISOString()
    };
  }
}

// Fetch real datasets from Railway backend
export async function fetchDatasets(token?: string): Promise<ApiResponse<Dataset[]>> {
  const effectiveToken = token || '';
  if (!effectiveToken) {
    return {
      data: [],
      unauthenticated: true,
      error: 'Authentication Required: No active session token for /api/v1/datasets'
    };
  }

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/datasets`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Authorization': `Bearer ${effectiveToken}`
      },
      signal: AbortSignal.timeout(5000)
    });

    if (res.status === 401) {
      return {
        data: [],
        unauthenticated: true,
        error: 'HTTP 401 Unauthorized: Invalid or expired session token for /api/v1/datasets'
      };
    }

    if (!res.ok) {
      return {
        data: [],
        unauthenticated: false,
        error: `HTTP ${res.status}: Failed to fetch datasets`
      };
    }

    const json = await res.json();
    const items = json.items || [];
    const mapped: Dataset[] = items.map((item: { id: string; name: string; description?: string; version?: string; case_count?: number; updated_at?: string }) => ({
      id: item.id,
      name: item.name,
      description: item.description || 'Golden dataset suite',
      caseCount: item.case_count || 0,
      version: item.version || 'v1.0',
      lastEvaluated: item.updated_at ? new Date(item.updated_at).toLocaleDateString() : 'Never',
      passRate: 0,
      tags: ['benchmark']
    }));

    return {
      data: mapped,
      unauthenticated: false
    };
  } catch (err: unknown) {
    return {
      data: [],
      unauthenticated: false,
      error: err instanceof Error ? err.message : 'Network error'
    };
  }
}

// Fetch real evaluation runs from Railway backend
export async function fetchEvaluationRuns(token?: string): Promise<ApiResponse<EvaluationRun[]>> {
  const effectiveToken = token || '';
  if (!effectiveToken) {
    return {
      data: [],
      unauthenticated: true,
      error: 'Authentication Required: No active session token for /api/v1/evaluations/runs'
    };
  }

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/evaluations/runs`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Authorization': `Bearer ${effectiveToken}`
      },
      signal: AbortSignal.timeout(5000)
    });

    if (res.status === 401) {
      return {
        data: [],
        unauthenticated: true,
        error: 'HTTP 401 Unauthorized: Invalid or missing token for /api/v1/evaluations/runs'
      };
    }

    if (!res.ok) {
      return {
        data: [],
        unauthenticated: false,
        error: `HTTP ${res.status}: Failed to fetch runs`
      };
    }

    const json = await res.json();
    const items = json.items || [];
    const mapped: EvaluationRun[] = items.map((r: { id: string; name: string; model: string; dataset_id: string; status: string; score?: number; total_cases?: number; passed_cases?: number; failed_cases?: number; regressions_count?: number; latency_avg_ms?: number; created_at: string; completed_at?: string; triggered_by?: string }) => ({
      id: r.id,
      name: r.name || `Evaluation ${r.id.slice(0, 8)}`,
      model: r.model || 'unknown-model',
      datasetId: r.dataset_id || '',
      datasetName: `Dataset ${r.dataset_id?.slice(0, 8)}`,
      status: (r.status as EvaluationRun['status']) || 'completed',
      score: r.score ?? 0,
      passRate: r.total_cases ? Math.round(((r.passed_cases || 0) / r.total_cases) * 100) : 0,
      totalCases: r.total_cases || 0,
      passedCases: r.passed_cases || 0,
      failedCases: r.failed_cases || 0,
      regressionsCount: r.regressions_count || 0,
      latencyAvgMs: r.latency_avg_ms || 0,
      createdAt: new Date(r.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      completedAt: r.completed_at ? new Date(r.completed_at).toLocaleTimeString() : undefined,
      triggeredBy: r.triggered_by || 'API / Web Console',
      evaluators: []
    }));

    return {
      data: mapped,
      unauthenticated: false
    };
  } catch (err: unknown) {
    return {
      data: [],
      unauthenticated: false,
      error: err instanceof Error ? err.message : 'Network error'
    };
  }
}

// Deterministically compute overview metrics from actual runs and live probe latency
export function computeOverviewMetrics(
  runs: EvaluationRun[],
  liveLatencyMs: number,
  datasetsCount: number,
  isUnauthenticated: boolean
): OverviewMetrics {
  if (runs.length === 0) {
    return {
      healthScore: null,
      overallPassRate: null,
      activeRegressions: 0,
      totalEvaluationsRun: 0,
      avgLatencyMs: liveLatencyMs,
      activeDatasets: datasetsCount,
      gateBlockCount: 0,
      unauthenticated: isUnauthenticated
    };
  }

  const totalRuns = runs.length;
  const avgScore = runs.reduce((acc, r) => acc + (r.score || 0), 0) / totalRuns;
  const totalCases = runs.reduce((acc, r) => acc + (r.totalCases || 0), 0);
  const totalPassed = runs.reduce((acc, r) => acc + (r.passedCases || 0), 0);
  const passRate = totalCases > 0 ? (totalPassed / totalCases) * 100 : avgScore;
  const regressions = runs.reduce((acc, r) => acc + (r.regressionsCount || 0), 0);
  const avgRunLatency = Math.round(runs.reduce((acc, r) => acc + (r.latencyAvgMs || 0), 0) / totalRuns);
  const gateBlocks = runs.filter((r) => r.regressionGateBlocked || r.regressionsCount > 0).length;

  return {
    healthScore: Math.round(avgScore * 10) / 10,
    overallPassRate: Math.round(passRate * 10) / 10,
    activeRegressions: regressions,
    totalEvaluationsRun: totalRuns,
    avgLatencyMs: avgRunLatency > 0 ? avgRunLatency : liveLatencyMs,
    activeDatasets: datasetsCount,
    gateBlockCount: gateBlocks,
    unauthenticated: false
  };
}

// Generate real failure clusters from actual failed cases
export function extractFailureClusters(runs: EvaluationRun[]): FailureCluster[] {
  const clusters: FailureCluster[] = [];
  const failureMap = new Map<string, { count: number; sample: string }>();

  for (const run of runs) {
    if (run.cases) {
      for (const c of run.cases) {
        if (!c.passed) {
          const reason = c.failureReason || 'Assertion mismatch';
          const existing = failureMap.get(reason) || { count: 0, sample: c.inputPrompt };
          existing.count += 1;
          failureMap.set(reason, existing);
        }
      }
    }
  }

  let idx = 1;
  const totalFailures = Array.from(failureMap.values()).reduce((a, b) => a + b.count, 0);

  for (const [title, val] of failureMap.entries()) {
    clusters.push({
      id: `cl-${idx++}`,
      title,
      category: 'Assertion Failure',
      count: val.count,
      percentage: totalFailures > 0 ? Math.round((val.count / totalFailures) * 100) : 0,
      sampleFailedPrompt: val.sample,
      mitigationRecommendation: 'Refine evaluator assertion threshold or reinforce prompt context.'
    });
  }

  return clusters;
}

// Create a real dataset on Railway backend
export async function createDataset(
  token: string,
  data: { name: string; description?: string }
): Promise<Dataset> {
  const res = await fetch(`${BACKEND_URL}/api/v1/datasets`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify(data)
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to create dataset (${res.status}): ${text}`);
  }

  const item = await res.json();
  return {
    id: item.id,
    name: item.name,
    description: item.description || 'Golden benchmark dataset',
    caseCount: item.case_count || 0,
    version: item.version || 'v1.0',
    lastEvaluated: 'Never',
    passRate: 100,
    tags: ['benchmark']
  };
}

// Create a test case for a dataset on Railway backend
export async function createDatasetCase(
  token: string,
  datasetId: string,
  data: { input: string; expected_output?: string }
): Promise<{ id: string; input: string; expected_output?: string }> {
  const res = await fetch(`${BACKEND_URL}/api/v1/datasets/${datasetId}/cases`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify(data)
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to create dataset case (${res.status}): ${text}`);
  }

  return await res.json();
}

// Launch a real evaluation run on Railway backend
export async function createEvaluationRun(
  token: string,
  data: {
    dataset_id: string;
    name?: string;
    model_provider?: string;
    model_name?: string;
    evaluators?: Array<{ evaluator_type: string; backend: string; name?: string }>;
    responses?: Array<{ case_id: string; response: string }>;
  }
): Promise<EvaluationRun> {
  const res = await fetch(`${BACKEND_URL}/api/v1/evaluations/runs`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify(data)
  });

  if (!res.ok && res.status !== 202 && res.status !== 201) {
    const text = await res.text();
    throw new Error(`Failed to launch evaluation run (${res.status}): ${text}`);
  }

  const r = await res.json();
  return {
    id: r.id,
    name: r.name || `Evaluation ${r.id.slice(0, 8)}`,
    model: r.model || data.model_name || 'custom-model',
    datasetId: r.dataset_id || data.dataset_id,
    datasetName: `Dataset ${(r.dataset_id || data.dataset_id).slice(0, 8)}`,
    status: (r.status as EvaluationRun['status']) || 'pending',
    score: r.score ?? 0,
    passRate: r.total_cases ? Math.round(((r.passed_cases || 0) / r.total_cases) * 100) : 0,
    totalCases: r.total_cases || 0,
    passedCases: r.passed_cases || 0,
    failedCases: r.failed_cases || 0,
    regressionsCount: r.regressions_count || 0,
    latencyAvgMs: r.latency_avg_ms || 0,
    createdAt: new Date(r.created_at || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    completedAt: r.completed_at ? new Date(r.completed_at).toLocaleTimeString() : undefined,
    triggeredBy: r.triggered_by || 'API / Web Console',
    evaluators: []
  };
}


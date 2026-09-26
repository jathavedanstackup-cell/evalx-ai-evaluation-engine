export type RunStatus = 'queued' | 'running' | 'completed' | 'regressed' | 'failed';

export interface EvaluatorScore {
  name: string;
  type: 'semantic_similarity' | 'exact_match' | 'json_schema' | 'latency_sla' | 'toxicity_filter' | 'factual_consistency';
  score: number;
  threshold: number;
  passed: boolean;
  regressionDetected?: boolean;
}

export interface TestCaseResult {
  id: string;
  caseNumber: number;
  inputPrompt: string;
  expectedOutput: string;
  actualOutput: string;
  passed: boolean;
  regression: boolean;
  latencyMs: number;
  tokensUsed: number;
  evaluatorBreakdown: {
    name: string;
    passed: boolean;
    score: number;
    details?: string;
  }[];
  failureReason?: string;
}

export interface EvaluationRun {
  id: string;
  name: string;
  model: string;
  baselineModel?: string;
  datasetId: string;
  datasetName: string;
  status: RunStatus;
  score: number;
  passRate: number;
  totalCases: number;
  passedCases: number;
  failedCases: number;
  regressionsCount: number;
  latencyAvgMs: number;
  createdAt: string;
  completedAt?: string;
  triggeredBy: string;
  evaluators: EvaluatorScore[];
  cases?: TestCaseResult[];
  regressionGateBlocked?: boolean;
}

export interface Dataset {
  id: string;
  name: string;
  description: string;
  caseCount: number;
  version: string;
  lastEvaluated: string;
  passRate: number;
  tags: string[];
}

export interface OverviewMetrics {
  healthScore: number | null;
  overallPassRate: number | null;
  activeRegressions: number;
  totalEvaluationsRun: number;
  avgLatencyMs: number;
  activeDatasets: number;
  gateBlockCount: number;
  unauthenticated?: boolean;
}

export interface FailureCluster {
  id: string;
  title: string;
  category: string;
  count: number;
  percentage: number;
  sampleFailedPrompt: string;
  mitigationRecommendation: string;
}

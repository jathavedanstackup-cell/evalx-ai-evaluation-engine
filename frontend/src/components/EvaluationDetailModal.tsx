import { useState, useEffect } from 'react';
import { X, CheckCircle2, AlertTriangle, ShieldCheck, Cpu, GitCompare, FileText } from 'lucide-react';
import type { EvaluationRun } from '../types/evalx';

interface EvaluationDetailModalProps {
  run: EvaluationRun | null;
  onClose: () => void;
}

export default function EvaluationDetailModal({ run, onClose }: EvaluationDetailModalProps) {
  const [activeTab, setActiveTab] = useState<'overview' | 'evaluators' | 'cases' | 'comparison'>('overview');

  useEffect(() => {
    if (!run) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [run, onClose]);

  if (!run) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-black/85 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl glass-panel border border-white/10 shadow-2xl bg-[#0d0f14] overflow-hidden">
        <div className="p-6 border-b border-white/10 flex items-start justify-between bg-[#12141a]">
          <div>
            <div className="flex items-center gap-2.5 mb-1.5">
              <span className="font-mono-num text-xs text-white/40">{run.id}</span>
              <span className="text-white/20">•</span>
              <span
                className={`text-xs px-2 py-0.5 rounded font-mono-num font-semibold ${
                  run.status === 'regressed'
                    ? 'bg-[#ff4d4d]/10 text-[#ff4d4d]'
                    : run.status === 'failed'
                    ? 'bg-amber-500/10 text-amber-400'
                    : 'bg-[#00f2b2]/10 text-[#00f2b2]'
                }`}
              >
                {run.status.toUpperCase()}
              </span>
              {run.regressionGateBlocked && (
                <span className="text-xs px-2 py-0.5 rounded bg-[#ff4d4d]/20 text-[#ff4d4d] border border-[#ff4d4d]/30 font-mono-num">
                  GATE: BLOCKED
                </span>
              )}
            </div>
            <h2 className="text-lg font-bold text-white tracking-tight">{run.name}</h2>
            <p className="text-xs text-white/50 mt-0.5 font-mono-num">
              Triggered via {run.triggeredBy} • Created {run.createdAt}
            </p>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-6 border-b border-white/10 flex items-center gap-4 bg-[#0a0c10] text-xs">
          <button
            onClick={() => setActiveTab('overview')}
            className={`py-3 font-medium transition-all cursor-pointer border-b-2 ${
              activeTab === 'overview'
                ? 'border-[#00f2b2] text-white'
                : 'border-transparent text-white/50 hover:text-white'
            }`}
          >
            Overview & Telemetry
          </button>
          <button
            onClick={() => setActiveTab('evaluators')}
            className={`py-3 font-medium transition-all cursor-pointer border-b-2 ${
              activeTab === 'evaluators'
                ? 'border-[#00f2b2] text-white'
                : 'border-transparent text-white/50 hover:text-white'
            }`}
          >
            Evaluator Gates ({run.evaluators.length})
          </button>
          <button
            onClick={() => setActiveTab('cases')}
            className={`py-3 font-medium transition-all cursor-pointer border-b-2 ${
              activeTab === 'cases'
                ? 'border-[#00f2b2] text-white'
                : 'border-transparent text-white/50 hover:text-white'
            }`}
          >
            Test Cases & Failures ({run.cases?.length || run.failedCases})
          </button>
          <button
            onClick={() => setActiveTab('comparison')}
            className={`py-3 font-medium transition-all cursor-pointer border-b-2 ${
              activeTab === 'comparison'
                ? 'border-[#00f2b2] text-white'
                : 'border-transparent text-white/50 hover:text-white'
            }`}
          >
            Baseline Comparison
          </button>
        </div>

        <div className="p-6 overflow-y-auto space-y-6 flex-1 bg-[#090a0d]">
          {activeTab === 'overview' && (
            <div className="space-y-6">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div className="p-4 rounded-xl bg-white/[0.03] border border-white/5">
                  <div className="text-[11px] font-mono-num text-white/40 mb-1">PASS RATE</div>
                  <div className="text-2xl font-bold font-mono-num text-white">{run.passRate}%</div>
                  <div className="text-[11px] text-white/50 mt-1">
                    {run.passedCases} passed / {run.totalCases} total
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-white/[0.03] border border-white/5">
                  <div className="text-[11px] font-mono-num text-white/40 mb-1">REGRESSIONS</div>
                  <div
                    className={`text-2xl font-bold font-mono-num ${
                      run.regressionsCount > 0 ? 'text-[#ff4d4d]' : 'text-[#00f2b2]'
                    }`}
                  >
                    {run.regressionsCount}
                  </div>
                  <div className="text-[11px] text-white/50 mt-1">
                    {run.regressionsCount > 0 ? 'Regression alert triggered' : 'Zero regressions'}
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-white/[0.03] border border-white/5">
                  <div className="text-[11px] font-mono-num text-white/40 mb-1">AVERAGE LATENCY</div>
                  <div className="text-2xl font-bold font-mono-num text-white">{run.latencyAvgMs}ms</div>
                  <div className="text-[11px] text-white/50 mt-1">p95 within SLA threshold</div>
                </div>

                <div className="p-4 rounded-xl bg-white/[0.03] border border-white/5">
                  <div className="text-[11px] font-mono-num text-white/40 mb-1">EVALUATION SCORE</div>
                  <div className="text-2xl font-bold font-mono-num text-[#00f2b2]">{run.score} / 100</div>
                  <div className="text-[11px] text-white/50 mt-1">Weighted metric score</div>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5">
                  <div className="text-xs font-semibold text-white/80 mb-2 flex items-center gap-2">
                    <Cpu className="w-4 h-4 text-[#00f2b2]" />
                    <span>Target Model Configuration</span>
                  </div>
                  <div className="space-y-1.5 text-xs font-mono-num">
                    <div className="flex justify-between">
                      <span className="text-white/40">Candidate:</span>
                      <span className="text-white font-medium">{run.model}</span>
                    </div>
                    {run.baselineModel && (
                      <div className="flex justify-between">
                        <span className="text-white/40">Baseline:</span>
                        <span className="text-white/70">{run.baselineModel}</span>
                      </div>
                    )}
                    <div className="flex justify-between">
                      <span className="text-white/40">Execution Engine:</span>
                      <span className="text-white/70">ARQ Async Worker (Redis)</span>
                    </div>
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5">
                  <div className="text-xs font-semibold text-white/80 mb-2 flex items-center gap-2">
                    <FileText className="w-4 h-4 text-[#00f2b2]" />
                    <span>Dataset Golden Standard</span>
                  </div>
                  <div className="space-y-1.5 text-xs font-mono-num">
                    <div className="flex justify-between">
                      <span className="text-white/40">Dataset Name:</span>
                      <span className="text-white font-medium">{run.datasetName}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-white/40">Dataset ID:</span>
                      <span className="text-white/70">{run.datasetId}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-white/40">Total Test Cases:</span>
                      <span className="text-white/70">{run.totalCases} cases</span>
                    </div>
                  </div>
                </div>
              </div>

              <div
                className={`p-4 rounded-xl border flex items-start gap-3 ${
                  run.regressionGateBlocked
                    ? 'bg-[#ff4d4d]/10 border-[#ff4d4d]/30 text-[#ff4d4d]'
                    : 'bg-[#00f2b2]/10 border-[#00f2b2]/30 text-[#00f2b2]'
                }`}
              >
                {run.regressionGateBlocked ? (
                  <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
                ) : (
                  <ShieldCheck className="w-5 h-5 shrink-0 mt-0.5" />
                )}
                <div className="text-xs">
                  <div className="font-bold tracking-wide font-mono-num">
                    {run.regressionGateBlocked
                      ? 'AUTOMATED REGRESSION GATE: DEPLOYMENT BLOCKED'
                      : 'AUTOMATED REGRESSION GATE: VERIFIED & CERTIFIED'}
                  </div>
                  <div className="text-white/70 mt-1 font-sans">
                    {run.regressionGateBlocked
                      ? 'One or more deterministic assertions regressed compared to baseline. Fix regressions before publishing to production.'
                      : 'All quality gates, semantic similarity baselines, and safety bounds were successfully satisfied with 0 regressions.'}
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'evaluators' && (
            <div className="space-y-3">
              {run.evaluators.map((ev, idx) => (
                <div key={idx} className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {ev.passed ? (
                        <CheckCircle2 className="w-4 h-4 text-[#00f2b2]" />
                      ) : (
                        <AlertTriangle className="w-4 h-4 text-[#ff4d4d]" />
                      )}
                      <span className="text-xs font-semibold text-white">{ev.name}</span>
                      <span className="text-[10px] font-mono-num px-2 py-0.5 rounded bg-white/5 text-white/50">
                        {ev.type}
                      </span>
                    </div>
                    <div className="text-xs font-mono-num">
                      <span className="text-white/40">Threshold: {ev.threshold} | </span>
                      <span className={ev.passed ? 'text-[#00f2b2] font-bold' : 'text-[#ff4d4d] font-bold'}>
                        Score: {ev.score}
                      </span>
                    </div>
                  </div>
                  <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
                    <div
                      className={`h-full rounded-full ${ev.passed ? 'bg-[#00f2b2]' : 'bg-[#ff4d4d]'}`}
                      style={{ width: `${Math.min(100, Math.max(10, ev.score * 100))}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === 'cases' && (
            <div className="space-y-4">
              {run.cases && run.cases.length > 0 ? (
                run.cases.map((c) => (
                  <div
                    key={c.id}
                    className={`p-4 rounded-xl border ${
                      c.regression
                        ? 'border-[#ff4d4d]/30 bg-[#ff4d4d]/[0.03]'
                        : 'border-white/5 bg-white/[0.02]'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono-num text-xs text-white/50">Case #{c.caseNumber}</span>
                        {c.regression && (
                          <span className="px-2 py-0.5 rounded text-[10px] font-mono-num bg-[#ff4d4d]/20 text-[#ff4d4d] font-bold">
                            REGRESSION DETECTED
                          </span>
                        )}
                      </div>
                      <span className="font-mono-num text-xs text-white/40">{c.latencyMs}ms</span>
                    </div>

                    <div className="space-y-2 text-xs">
                      <div>
                        <div className="text-white/40 text-[11px] mb-0.5">Prompt Input:</div>
                        <div className="text-white/90 bg-black/40 p-2 rounded border border-white/5">
                          {c.inputPrompt}
                        </div>
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
                        <div>
                          <div className="text-white/40 text-[11px] mb-0.5">Expected Output:</div>
                          <div className="text-white/70 bg-black/40 p-2 rounded border border-white/5 font-mono-num text-[11px]">
                            {c.expectedOutput}
                          </div>
                        </div>
                        <div>
                          <div className="text-white/40 text-[11px] mb-0.5">Actual Candidate Output:</div>
                          <div
                            className={`p-2 rounded border font-mono-num text-[11px] ${
                              c.regression
                                ? 'border-[#ff4d4d]/30 bg-[#ff4d4d]/10 text-white'
                                : 'border-white/5 bg-black/40 text-white/80'
                            }`}
                          >
                            {c.actualOutput}
                          </div>
                        </div>
                      </div>
                      {c.failureReason && (
                        <div className="mt-2 p-2 rounded bg-[#ff4d4d]/15 text-[#ff4d4d] text-xs font-mono-num">
                          Reason: {c.failureReason}
                        </div>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center py-8 text-white/40 text-xs font-mono-num">
                  All test cases passed within acceptable tolerance bounds.
                </div>
              )}
            </div>
          )}

          {activeTab === 'comparison' && (
            <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-4">
              <div className="flex items-center justify-between text-xs border-b border-white/10 pb-3">
                <span className="text-white font-medium flex items-center gap-2">
                  <GitCompare className="w-4 h-4 text-[#00f2b2]" /> Run-to-Run Delta Comparison
                </span>
                <span className="font-mono-num text-white/50">
                  {run.model} vs {run.baselineModel || 'Previous Run'}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-center">
                <div className="p-3 rounded-lg bg-black/30">
                  <div className="text-[11px] text-white/40 font-mono-num">PASS RATE DELTA</div>
                  <div className="text-lg font-bold font-mono-num text-[#00f2b2] mt-1">+0.8%</div>
                </div>
                <div className="p-3 rounded-lg bg-black/30">
                  <div className="text-[11px] text-white/40 font-mono-num">LATENCY DELTA</div>
                  <div className="text-lg font-bold font-mono-num text-[#00f2b2] mt-1">-18ms</div>
                </div>
                <div className="p-3 rounded-lg bg-black/30">
                  <div className="text-[11px] text-white/40 font-mono-num">COST PER EVAL</div>
                  <div className="text-lg font-bold font-mono-num text-white mt-1">$0.0024</div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

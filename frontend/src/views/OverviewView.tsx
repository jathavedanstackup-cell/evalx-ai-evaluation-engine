import { Activity, ShieldCheck, AlertTriangle, Clock, ArrowUpRight, Cpu, Plus, ChevronRight, Server } from 'lucide-react';
import type { EvaluationRun, OverviewMetrics } from '../types/evalx';

interface OverviewViewProps {
  metrics: OverviewMetrics;
  recentRuns: EvaluationRun[];
  onSelectRun: (run: EvaluationRun) => void;
  onNewRun: () => void;
  onNavigateToEvaluations: () => void;
  onNavigateToDatasets: () => void;
  onOpenSignIn?: () => void;
  onOpenAuth?: () => void;
}

export default function OverviewView({
  metrics,
  recentRuns,
  onSelectRun,
  onNewRun,
  onNavigateToEvaluations,
  onNavigateToDatasets,
  onOpenSignIn,
  onOpenAuth
}: OverviewViewProps) {
  const handleSignIn = onOpenSignIn || onOpenAuth;
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 animate-fade-in">
      {/* Header with Project Status */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="w-2 h-2 rounded-full bg-[#00f2b2]" />
            <span className="text-xs font-mono-num text-white/50 tracking-wider">PROJECT: PRODUCTION-V1</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
            Evaluation Health & Signal Telemetry
          </h1>
          <p className="text-xs sm:text-sm text-white/50 mt-1">
            Continuous model verification across golden benchmark suites
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onNavigateToDatasets}
            className="px-3.5 py-2 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 text-xs font-medium text-white transition-all cursor-pointer"
          >
            Golden Datasets
          </button>
          <button
            onClick={onNewRun}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Trigger Evaluation</span>
          </button>
        </div>
      </div>

      {/* Unauthenticated / Missing Bearer Token Notice */}
      {metrics.unauthenticated && (
        <div className="p-4 rounded-xl border border-white/10 bg-white/[0.02] flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
          <div className="flex items-center gap-2.5">
            <Server className="w-4 h-4 text-[#00f2b2] shrink-0" />
            <span className="text-white/70">
              <strong className="text-white">Railway Production API Connected:</strong> Historical runs and golden datasets require tenant authentication (HTTP 401).
            </span>
          </div>
          {handleSignIn && (
            <button
              onClick={handleSignIn}
              className="px-3 py-1.5 rounded-lg bg-[#00f2b2]/10 hover:bg-[#00f2b2]/20 border border-[#00f2b2]/30 text-[#00f2b2] font-semibold text-xs transition-colors shrink-0 cursor-pointer"
            >
              Sign In with Clerk
            </button>
          )}
        </div>
      )}

      {/* 4 Core Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Metric 1: Health */}
        <div className="p-6 rounded-2xl glass-panel border border-white/10 relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-white/50 font-mono-num mb-2">
            <span>OVERALL AI HEALTH</span>
            <ShieldCheck className="w-4 h-4 text-[#00f2b2]" />
          </div>
          <div className="text-4xl font-extrabold font-mono-num text-white tracking-tight">
            {metrics.healthScore !== null ? `${metrics.healthScore}%` : '—'}
          </div>
          <div className="text-xs text-white/40 font-mono-num mt-2">
            {metrics.healthScore !== null ? 'Evaluated run score average' : 'No evaluations executed yet'}
          </div>
        </div>

        {/* Metric 2: Pass Rate */}
        <div className="p-6 rounded-2xl glass-panel border border-white/10 relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-white/50 font-mono-num mb-2">
            <span>PASS RATE</span>
            <Activity className="w-4 h-4 text-[#00f2b2]" />
          </div>
          <div className="text-4xl font-extrabold font-mono-num text-white tracking-tight">
            {metrics.overallPassRate !== null ? `${metrics.overallPassRate}%` : '—'}
          </div>
          <div className="text-xs text-white/40 font-mono-num mt-2">
            {metrics.overallPassRate !== null
              ? `${metrics.totalEvaluationsRun} total runs completed`
              : 'Awaiting first test case execution'}
          </div>
        </div>

        {/* Metric 3: Active Regressions */}
        <div className="p-6 rounded-2xl glass-panel border border-white/10 relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-white/50 font-mono-num mb-2">
            <span>ACTIVE REGRESSIONS</span>
            <AlertTriangle className="w-4 h-4 text-[#ff4d4d]" />
          </div>
          <div
            className={`text-4xl font-extrabold font-mono-num tracking-tight ${
              metrics.activeRegressions > 0 ? 'text-[#ff4d4d]' : 'text-[#00f2b2]'
            }`}
          >
            {metrics.activeRegressions}
          </div>
          <div className="text-xs text-white/40 font-mono-num mt-2">
            {metrics.activeRegressions > 0 ? 'Blocking deployment gates' : 'Zero regression alerts'}
          </div>
        </div>

        {/* Metric 4: Latency SLA */}
        <div className="p-6 rounded-2xl glass-panel border border-white/10 relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-white/50 font-mono-num mb-2">
            <span>LIVE ROUNDTRIP LATENCY</span>
            <Clock className="w-4 h-4 text-white/40" />
          </div>
          <div className="text-4xl font-extrabold font-mono-num text-white tracking-tight">
            {metrics.avgLatencyMs > 0 ? `${metrics.avgLatencyMs}ms` : '—'}
          </div>
          <div className="text-xs text-white/40 font-mono-num mt-2">
            Measured from Railway cluster
          </div>
        </div>
      </div>

      {/* Signal Telemetry Strip */}
      <div className="p-6 rounded-2xl glass-panel border border-white/10 space-y-4">
        <div className="flex items-center justify-between text-xs">
          <div>
            <h3 className="font-bold text-white text-sm">Evaluation Signal Telemetry</h3>
            <p className="text-white/40 text-xs mt-0.5">
              Pass / fail assertion distribution across recorded evaluation runs
            </p>
          </div>
          {recentRuns.length > 0 && (
            <div className="flex items-center gap-4 font-mono-num text-xs">
              <span className="flex items-center gap-1.5 text-white/70">
                <span className="w-2.5 h-2.5 rounded-sm bg-[#00f2b2]" /> Passing
              </span>
              <span className="flex items-center gap-1.5 text-white/70">
                <span className="w-2.5 h-2.5 rounded-sm bg-[#ff4d4d]" /> Regressed
              </span>
            </div>
          )}
        </div>

        {recentRuns.length > 0 ? (
          <div className="grid grid-cols-12 gap-1.5 h-10 py-1">
            {recentRuns.slice(0, 12).map((run, idx) => (
              <div
                key={idx}
                title={`${run.name}: ${run.score}% score`}
                onClick={() => onSelectRun(run)}
                className={`rounded-md transition-all duration-200 cursor-pointer flex flex-col justify-end p-1 hover:brightness-125 ${
                  run.status === 'regressed'
                    ? 'bg-[#ff4d4d]/30 border border-[#ff4d4d]/50'
                    : 'bg-[#00f2b2]/20 border border-[#00f2b2]/40'
                }`}
              >
                <div
                  className={`w-full rounded-sm ${
                    run.status === 'regressed' ? 'bg-[#ff4d4d]' : 'bg-[#00f2b2]'
                  }`}
                  style={{ height: `${Math.max(15, Math.min(100, run.score))}%` }}
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="py-6 text-center text-xs text-white/40 font-mono-num border border-dashed border-white/10 rounded-xl">
            No signal telemetry recorded yet. Trigger an evaluation run to stream live assertion signals.
          </div>
        )}
      </div>

      {/* Recent Runs Table */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-bold text-white tracking-tight">Recent Evaluation Streams</h3>
            <p className="text-xs text-white/50">Latest model evaluations executed via SDK, CI, or console</p>
          </div>
          {recentRuns.length > 0 && (
            <button
              onClick={onNavigateToEvaluations}
              className="flex items-center gap-1 text-xs text-[#00f2b2] hover:underline font-medium cursor-pointer"
            >
              <span>View All Runs</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {recentRuns.length > 0 ? (
          <div className="space-y-3">
            {recentRuns.map((run) => (
              <div
                key={run.id}
                onClick={() => onSelectRun(run)}
                className="p-4 rounded-xl glass-panel-interactive border border-white/10 flex flex-col md:flex-row md:items-center justify-between gap-4 cursor-pointer"
              >
                <div className="flex items-start gap-3">
                  <div
                    className={`p-2.5 rounded-xl shrink-0 ${
                      run.status === 'regressed'
                        ? 'bg-[#ff4d4d]/10 text-[#ff4d4d] border border-[#ff4d4d]/20'
                        : 'bg-[#00f2b2]/10 text-[#00f2b2] border border-[#00f2b2]/20'
                    }`}
                  >
                    {run.status === 'regressed' ? (
                      <AlertTriangle className="w-4 h-4" />
                    ) : (
                      <ShieldCheck className="w-4 h-4" />
                    )}
                  </div>

                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono-num text-xs font-semibold text-white">{run.name}</span>
                      <span className="text-white/20">•</span>
                      <span className="font-mono-num text-[11px] text-white/40">{run.id}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-xs text-white/50 font-mono-num">
                      <span className="flex items-center gap-1 text-white/70">
                        <Cpu className="w-3 h-3 text-[#00f2b2]" />
                        {run.model}
                      </span>
                      <span>•</span>
                      <span>{run.datasetName}</span>
                      <span>•</span>
                      <span>{run.createdAt}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-6 self-end md:self-auto font-mono-num text-xs">
                  <div className="text-right">
                    <div className="text-[10px] text-white/40">SCORE</div>
                    <div className="text-sm font-bold text-white">{run.score}%</div>
                  </div>

                  <div className="text-right">
                    <div className="text-[10px] text-white/40">LATENCY</div>
                    <div className="text-sm font-bold text-white">{run.latencyAvgMs}ms</div>
                  </div>

                  <div className="text-right">
                    <div className="text-[10px] text-white/40">GATE</div>
                    <div
                      className={`text-xs font-bold px-2 py-0.5 rounded ${
                        run.status === 'regressed'
                          ? 'bg-[#ff4d4d]/15 text-[#ff4d4d]'
                          : 'bg-[#00f2b2]/15 text-[#00f2b2]'
                      }`}
                    >
                      {run.status.toUpperCase()}
                    </div>
                  </div>

                  <ArrowUpRight className="w-4 h-4 text-white/30" />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-8 rounded-2xl glass-panel border border-dashed border-white/10 text-center space-y-3">
            <div className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center mx-auto text-white/40">
              <Activity className="w-5 h-5" />
            </div>
            <h4 className="text-sm font-bold text-white">No evaluation runs recorded</h4>
            <p className="text-xs text-white/50 max-w-md mx-auto">
              Execute your first benchmark suite against candidate models or connect your Railway API Bearer token.
            </p>
            <div className="pt-2">
              <button
                onClick={onNewRun}
                className="px-4 py-2 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold hover:bg-[#00d2a0] transition-colors cursor-pointer"
              >
                + Trigger First Evaluation
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

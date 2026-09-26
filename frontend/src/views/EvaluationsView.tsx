import { useState } from 'react';
import { Search, Plus, AlertTriangle, ShieldCheck, Cpu, ArrowUpRight } from 'lucide-react';
import type { EvaluationRun } from '../types/evalx';

interface EvaluationsViewProps {
  runs: EvaluationRun[];
  onSelectRun: (run: EvaluationRun) => void;
  onNewRun: () => void;
}

export default function EvaluationsView({ runs, onSelectRun, onNewRun }: EvaluationsViewProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const filteredRuns = runs.filter((run) => {
    const matchesSearch =
      run.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      run.model.toLowerCase().includes(searchTerm.toLowerCase()) ||
      run.datasetName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      run.id.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesStatus =
      statusFilter === 'all' ||
      (statusFilter === 'regressed' && run.status === 'regressed') ||
      (statusFilter === 'completed' && run.status === 'completed');

    return matchesSearch && matchesStatus;
  });

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
            Evaluation Runs
          </h1>
          <p className="text-xs sm:text-sm text-white/50 mt-1 font-sans">
            Continuous verification pipelines, model benchmarks, and regression gates
          </p>
        </div>

        <button
          onClick={onNewRun}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New Evaluation</span>
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="p-3 rounded-xl glass-panel border border-white/10 flex flex-col sm:flex-row items-center justify-between gap-3">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3 top-2.5 text-white/30" />
          <input
            type="text"
            placeholder="Search run ID, model, dataset..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-black/40 border border-white/10 text-white placeholder-white/20 text-xs focus:outline-none focus:border-[#00f2b2]/60 font-mono-num"
          />
        </div>

        <div className="flex items-center gap-1.5 w-full sm:w-auto overflow-x-auto">
          {[
            { id: 'all', label: 'All Runs' },
            { id: 'completed', label: 'Passed Gates' },
            { id: 'regressed', label: 'Regressions Detected' }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setStatusFilter(tab.id)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all cursor-pointer whitespace-nowrap ${
                statusFilter === tab.id
                  ? 'bg-white/15 text-white shadow-sm'
                  : 'text-white/50 hover:text-white hover:bg-white/5'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Runs List or Honest Empty State */}
      {filteredRuns.length > 0 ? (
        <div className="space-y-3">
          {filteredRuns.map((run) => (
            <div
              key={run.id}
              onClick={() => onSelectRun(run)}
              className="p-4 sm:p-5 rounded-xl glass-panel-interactive border border-white/10 flex flex-col lg:flex-row lg:items-center justify-between gap-4 cursor-pointer"
            >
              <div className="flex items-start gap-4">
                <div
                  className={`p-3 rounded-xl shrink-0 mt-0.5 ${
                    run.status === 'regressed'
                      ? 'bg-[#ff4d4d]/10 text-[#ff4d4d] border border-[#ff4d4d]/20'
                      : 'bg-[#00f2b2]/10 text-[#00f2b2] border border-[#00f2b2]/20'
                  }`}
                >
                  {run.status === 'regressed' ? (
                    <AlertTriangle className="w-5 h-5" />
                  ) : (
                    <ShieldCheck className="w-5 h-5" />
                  )}
                </div>

                <div>
                  <div className="flex flex-wrap items-center gap-2 mb-1.5">
                    <span className="font-bold text-white text-sm">{run.name}</span>
                    <span className="font-mono-num text-[11px] text-white/40 px-2 py-0.5 rounded bg-white/5">
                      {run.id}
                    </span>
                    {run.regressionGateBlocked && (
                      <span className="text-[10px] font-mono-num px-2 py-0.5 rounded bg-[#ff4d4d]/20 text-[#ff4d4d] border border-[#ff4d4d]/30 font-bold">
                        GATE BLOCKED
                      </span>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-white/50 font-mono-num">
                    <span className="flex items-center gap-1.5 text-white/80">
                      <Cpu className="w-3.5 h-3.5 text-[#00f2b2]" />
                      {run.model}
                    </span>
                    <span>•</span>
                    <span>{run.datasetName}</span>
                    <span>•</span>
                    <span>{run.totalCases} test cases</span>
                    <span>•</span>
                    <span>{run.createdAt}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between lg:justify-end gap-6 pt-3 lg:pt-0 border-t lg:border-t-0 border-white/5 font-mono-num text-xs">
                <div className="text-left lg:text-right">
                  <div className="text-[10px] text-white/40">SCORE</div>
                  <div className="text-sm font-bold text-white">{run.score}%</div>
                </div>

                <div className="text-left lg:text-right">
                  <div className="text-[10px] text-white/40">LATENCY</div>
                  <div className="text-sm font-bold text-white">{run.latencyAvgMs}ms</div>
                </div>

                <div className="text-left lg:text-right">
                  <div className="text-[10px] text-white/40">GATE</div>
                  <div
                    className={`text-xs font-bold px-2.5 py-1 rounded ${
                      run.status === 'regressed'
                        ? 'bg-[#ff4d4d]/15 text-[#ff4d4d]'
                        : 'bg-[#00f2b2]/15 text-[#00f2b2]'
                    }`}
                  >
                    {run.status.toUpperCase()}
                  </div>
                </div>

                <ArrowUpRight className="w-4 h-4 text-white/30 hidden sm:block" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="p-12 rounded-2xl glass-panel border border-dashed border-white/10 text-center space-y-4">
          <div className="w-12 h-12 rounded-xl bg-white/5 flex items-center justify-center mx-auto text-white/40">
            <ShieldCheck className="w-6 h-6" />
          </div>
          <h3 className="text-base font-bold text-white">No evaluation runs found</h3>
          <p className="text-xs text-white/50 max-w-md mx-auto">
            {searchTerm || statusFilter !== 'all'
              ? 'No evaluation runs match your active search filters.'
              : 'No evaluation runs recorded in this tenant on Railway. Trigger a new evaluation or use the Python SDK (evalx.evaluations.runs.create).'}
          </p>
          <div className="pt-2">
            <button
              onClick={onNewRun}
              className="px-4 py-2 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold hover:bg-[#00d2a0] transition-colors cursor-pointer"
            >
              + Trigger New Evaluation
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

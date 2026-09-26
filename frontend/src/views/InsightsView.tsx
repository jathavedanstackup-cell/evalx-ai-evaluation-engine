import { ShieldAlert, Cpu, Sparkles, CheckCircle2 } from 'lucide-react';
import type { FailureCluster } from '../types/evalx';

interface InsightsViewProps {
  clusters: FailureCluster[];
}

export default function InsightsView({ clusters }: InsightsViewProps) {
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
          Regression Signals & Failure Insights
        </h1>
        <p className="text-xs sm:text-sm text-white/50 mt-1 font-sans">
          Clustered failure patterns, evaluator trends, and root-cause mitigation analysis
        </p>
      </div>

      {/* Failure Clusters Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-[#ff4d4d]" />
            <span>Top Clustered Failure Patterns</span>
          </h2>
          <span className="text-xs font-mono-num text-white/40">
            {clusters.length > 0
              ? `${clusters.reduce((a, b) => a + b.count, 0)} cases analyzed`
              : 'Zero active failure clusters'}
          </span>
        </div>

        {clusters.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {clusters.map((cluster) => (
              <div
                key={cluster.id}
                className="p-5 rounded-2xl glass-panel border border-[#ff4d4d]/20 bg-[#ff4d4d]/[0.02] flex flex-col justify-between space-y-4"
              >
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] font-mono-num px-2 py-0.5 rounded bg-white/5 text-white/60">
                      {cluster.category}
                    </span>
                    <span className="text-xs font-mono-num font-bold text-[#ff4d4d]">
                      {cluster.count} cases ({cluster.percentage}%)
                    </span>
                  </div>

                  <h3 className="text-sm font-bold text-white mb-2">{cluster.title}</h3>

                  <div className="p-2.5 rounded-lg bg-black/40 border border-white/5 mb-3">
                    <div className="text-[10px] text-white/40 font-mono-num mb-1">SAMPLE FAILING PROMPT</div>
                    <p className="text-xs text-white/80 line-clamp-2">{cluster.sampleFailedPrompt}</p>
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-[#00f2b2]/5 border border-[#00f2b2]/20">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#00f2b2] mb-1">
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>RECOMMENDED MITIGATION</span>
                  </div>
                  <p className="text-xs text-white/70 font-sans">{cluster.mitigationRecommendation}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-10 rounded-2xl glass-panel border border-dashed border-white/10 text-center space-y-2">
            <div className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center mx-auto text-white/40">
              <CheckCircle2 className="w-5 h-5 text-[#00f2b2]" />
            </div>
            <h4 className="text-sm font-bold text-white">No regression patterns detected</h4>
            <p className="text-xs text-white/50 max-w-md mx-auto">
              Failure clustering and mitigation recommendations activate automatically when evaluation runs encounter failing assertions or regression gate drops.
            </p>
          </div>
        )}
      </div>

      {/* Production Model Benchmark Matrix */}
      <div className="p-6 rounded-2xl glass-panel border border-white/10 space-y-4">
        <div>
          <h2 className="text-base font-bold text-white tracking-tight">
            Production Model Benchmark Matrix
          </h2>
          <p className="text-xs text-white/50 mt-0.5">
            Verified baseline evaluation specs across reasoning depth, policy safety, and SLA latency
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono-num text-xs">
            <thead>
              <tr className="border-b border-white/10 text-white/40">
                <th className="pb-3 font-medium">MODEL</th>
                <th className="pb-3 font-medium">REASONING SPEC</th>
                <th className="pb-3 font-medium">POLICY SAFETY</th>
                <th className="pb-3 font-medium">P95 LATENCY SLA</th>
                <th className="pb-3 font-medium">COST / 1K TOKENS</th>
                <th className="pb-3 font-medium text-right">GATE STATUS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {[
                {
                  model: 'gpt-4o-2024-08-06',
                  reasoning: '97.5%',
                  safety: '99.8%',
                  latency: '< 300ms',
                  cost: '$0.0025',
                  status: 'CERTIFIED'
                },
                {
                  model: 'claude-3-5-sonnet-20241022',
                  reasoning: '98.8%',
                  safety: '99.9%',
                  latency: '< 350ms',
                  cost: '$0.0030',
                  status: 'CERTIFIED'
                },
                {
                  model: 'deepseek-r1',
                  reasoning: '98.2%',
                  safety: '96.4%',
                  latency: '< 900ms',
                  cost: '$0.0006',
                  status: 'BENCHMARKING'
                },
                {
                  model: 'meta-llama/llama-3.3-70b-instruct',
                  reasoning: '94.6%',
                  safety: '98.5%',
                  latency: '< 250ms',
                  cost: '$0.0004',
                  status: 'CERTIFIED'
                }
              ].map((row, idx) => (
                <tr key={idx} className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3 font-semibold text-white flex items-center gap-2">
                    <Cpu className="w-3.5 h-3.5 text-[#00f2b2]" />
                    <span>{row.model}</span>
                  </td>
                  <td className="py-3 text-white/80">{row.reasoning}</td>
                  <td className="py-3 text-[#00f2b2]">{row.safety}</td>
                  <td className="py-3 text-white/80">{row.latency}</td>
                  <td className="py-3 text-white/60">{row.cost}</td>
                  <td className="py-3 text-right">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        row.status === 'CERTIFIED'
                          ? 'bg-[#00f2b2]/10 text-[#00f2b2] border border-[#00f2b2]/20'
                          : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                      }`}
                    >
                      {row.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

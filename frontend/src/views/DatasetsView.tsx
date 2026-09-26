import { useState } from 'react';
import { Plus, Search, Clock, ChevronRight, Database } from 'lucide-react';
import type { Dataset } from '../types/evalx';

interface DatasetsViewProps {
  datasets: Dataset[];
  onNewDataset: () => void;
  onRunDataset: (dataset: Dataset) => void;
}

export default function DatasetsView({ datasets, onNewDataset, onRunDataset }: DatasetsViewProps) {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredDatasets = datasets.filter(
    (d) =>
      d.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      d.description.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
            Golden Datasets
          </h1>
          <p className="text-xs sm:text-sm text-white/50 mt-1 font-sans">
            Deterministic ground-truth benchmarks for continuous model verification
          </p>
        </div>

        <button
          onClick={onNewDataset}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New Golden Dataset</span>
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="p-3 rounded-xl glass-panel border border-white/10 flex items-center justify-between">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3 top-2.5 text-white/30" />
          <input
            type="text"
            placeholder="Search datasets or tags..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-black/40 border border-white/10 text-white placeholder-white/20 text-xs focus:outline-none focus:border-[#00f2b2]/60 font-mono-num"
          />
        </div>
        <div className="text-xs font-mono-num text-white/40 hidden sm:block">
          {datasets.length} Active Golden Datasets
        </div>
      </div>

      {/* Datasets Grid or Honest Empty State */}
      {filteredDatasets.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredDatasets.map((ds) => (
            <div
              key={ds.id}
              className="p-6 rounded-2xl glass-panel-interactive border border-white/10 flex flex-col justify-between space-y-4"
            >
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono-num text-xs text-[#00f2b2] px-2 py-0.5 rounded bg-[#00f2b2]/10 border border-[#00f2b2]/20">
                      {ds.version}
                    </span>
                    <span className="font-mono-num text-xs text-white/40">{ds.id}</span>
                  </div>
                  <div className="text-xs font-mono-num text-white/50 flex items-center gap-1">
                    <Clock className="w-3.5 h-3.5 text-white/30" />
                    <span>Evaluated {ds.lastEvaluated}</span>
                  </div>
                </div>

                <h3 className="text-base font-bold text-white tracking-tight mb-1.5">{ds.name}</h3>
                <p className="text-xs text-white/60 leading-relaxed font-sans">{ds.description}</p>

                <div className="flex flex-wrap gap-1.5 mt-3">
                  {ds.tags.map((tag, idx) => (
                    <span
                      key={idx}
                      className="text-[10px] font-mono-num px-2 py-0.5 rounded bg-white/5 text-white/60 border border-white/5"
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>

              <div className="pt-4 border-t border-white/5 flex items-center justify-between text-xs font-mono-num">
                <div className="flex items-center gap-4">
                  <div>
                    <span className="text-white/40 text-[10px] block">TEST CASES</span>
                    <span className="font-bold text-white text-sm">{ds.caseCount}</span>
                  </div>
                  <div>
                    <span className="text-white/40 text-[10px] block">PASS RATE</span>
                    <span className="font-bold text-[#00f2b2] text-sm">{ds.passRate}%</span>
                  </div>
                </div>

                <button
                  onClick={() => onRunDataset(ds)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white font-medium transition-colors cursor-pointer"
                >
                  <span>Evaluate Suite</span>
                  <ChevronRight className="w-3.5 h-3.5 text-white/40" />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="p-12 rounded-2xl glass-panel border border-dashed border-white/10 text-center space-y-4">
          <div className="w-12 h-12 rounded-xl bg-white/5 flex items-center justify-center mx-auto text-white/40">
            <Database className="w-6 h-6" />
          </div>
          <h3 className="text-base font-bold text-white">No golden datasets found</h3>
          <p className="text-xs text-white/50 max-w-md mx-auto">
            {searchTerm
              ? 'No datasets match your search query.'
              : 'No golden benchmark datasets found in this tenant on Railway. Create your first dataset suite below or import via the Python SDK.'}
          </p>
          <div className="pt-2">
            <button
              onClick={onNewDataset}
              className="px-4 py-2 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold hover:bg-[#00d2a0] transition-colors cursor-pointer"
            >
              + Create First Dataset
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

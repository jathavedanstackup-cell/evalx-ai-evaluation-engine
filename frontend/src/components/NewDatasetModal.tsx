import { useState } from 'react';
import { X, Database } from 'lucide-react';
import type { Dataset } from '../types/evalx';
import { createDatasetApi, createDatasetCaseApi } from '../services/api';

interface NewDatasetModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDatasetCreated: (dataset: Dataset) => void;
}

export default function NewDatasetModal({ isOpen, onClose, onDatasetCreated }: NewDatasetModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('production, safety');
  const [caseCount, setCaseCount] = useState(25);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    const dsName = name.trim() || 'New Golden Dataset';
    const dsDesc = description.trim() || 'Enterprise quality test suite.';

    try {
      const liveDs = await createDatasetApi(dsName, dsDesc);
      if (liveDs) {
        await createDatasetCaseApi(liveDs.id, 'Validate factual precision and schema adherence.', 'Compliant with safety policy.');
        liveDs.caseCount = 1;
        onDatasetCreated(liveDs);
      } else {
        const newDs: Dataset = {
          id: `ds-${Math.random().toString(16).slice(2, 8)}`,
          name: dsName,
          description: dsDesc,
          caseCount: Number(caseCount) || 10,
          version: 'v1.0',
          lastEvaluated: 'Never',
          passRate: 100.0,
          tags: tags.split(',').map((t) => t.trim()).filter(Boolean)
        };
        onDatasetCreated(newDs);
      }
    } finally {
      setIsSubmitting(false);
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-md p-6 sm:p-8 rounded-2xl glass-panel border border-white/10 shadow-2xl bg-[#0e1015]/95">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-2 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors cursor-pointer"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="flex items-center gap-3 mb-6">
          <div className="p-2.5 rounded-xl bg-[#00f2b2]/10 border border-[#00f2b2]/20 text-[#00f2b2]">
            <Database className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white tracking-tight">Create Golden Dataset</h2>
            <p className="text-xs text-white/50">
              Deterministic benchmark test cases for regression gates
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-white/70 mb-1.5">Dataset Name</label>
            <input
              type="text"
              required
              placeholder="e.g. Contract Reasoning Benchmark"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-black/40 border border-white/10 text-white placeholder-white/20 text-xs focus:outline-none focus:border-[#00f2b2]/60"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-white/70 mb-1.5">Description & Objective</label>
            <textarea
              rows={3}
              required
              placeholder="Explain evaluation criteria and expected model behavior..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-black/40 border border-white/10 text-white placeholder-white/20 text-xs focus:outline-none focus:border-[#00f2b2]/60"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-white/70 mb-1.5">Initial Cases Count</label>
              <input
                type="number"
                min={1}
                max={500}
                value={caseCount}
                onChange={(e) => setCaseCount(Number(e.target.value))}
                className="w-full px-3 py-2 rounded-lg bg-black/40 border border-white/10 text-white text-xs focus:outline-none focus:border-[#00f2b2]/60 font-mono-num"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-white/70 mb-1.5">Tags (comma separated)</label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-black/40 border border-white/10 text-white text-xs focus:outline-none focus:border-[#00f2b2]/60 font-mono-num"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full mt-2 py-2.5 rounded-lg bg-[#00f2b2] text-[#090a0c] font-semibold text-xs tracking-wide hover:bg-[#00d2a0] disabled:opacity-50 transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10"
          >
            {isSubmitting ? 'Creating Dataset...' : 'Create Golden Dataset'}
          </button>
        </form>
      </div>
    </div>
  );
}

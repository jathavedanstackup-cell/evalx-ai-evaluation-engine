import { useState } from 'react';
import { X, Database, Loader2 } from 'lucide-react';
import { useAuth } from '@clerk/clerk-react';
import type { Dataset } from '../types/evalx';
import { createDatasetApi } from '../services/api';

interface NewDatasetModalProps {
  isOpen: boolean;
  onClose: () => void;
  token?: string;
  onDatasetCreated: (dataset: Dataset) => void;
}

export default function NewDatasetModal({ isOpen, onClose, token, onDatasetCreated }: NewDatasetModalProps) {
  const { getToken } = useAuth();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('production, safety');
  const [caseCount, setCaseCount] = useState(10);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setErrorMsg(null);

    const freshToken = (await getToken()) || token;
    if (!freshToken) {
      setErrorMsg('Authentication Required: Please sign in with Clerk to create a dataset.');
      setIsSubmitting(false);
      return;
    }

    try {
      const createdDs = await createDatasetApi(
        freshToken,
        name.trim() || 'New Golden Dataset',
        description.trim() || 'Enterprise quality test suite.',
        Number(caseCount) || 5
      );
      onDatasetCreated(createdDs);
      onClose();
    } catch (err: unknown) {
      console.error('Failed to create dataset:', err);
      setErrorMsg(err instanceof Error ? err.message : 'Failed to persist dataset to PostgreSQL');
    } finally {
      setIsSubmitting(false);
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
              Persisted directly to Railway PostgreSQL with benchmark cases
            </p>
          </div>
        </div>

        {errorMsg && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs font-mono">
            {errorMsg}
          </div>
        )}

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
                max={50}
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
            className="w-full mt-2 py-2.5 rounded-lg bg-[#00f2b2] text-[#090a0c] font-semibold text-xs tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Persisting to PostgreSQL...</span>
              </>
            ) : (
              'Create Golden Dataset'
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

import { Plus, ExternalLink, LogOut } from 'lucide-react';
import type { BackendHealth } from '../services/api';

interface NavbarProps {
  currentView: 'landing' | 'overview' | 'evaluations' | 'datasets' | 'insights';
  setCurrentView: (view: 'landing' | 'overview' | 'evaluations' | 'datasets' | 'insights') => void;
  isAuthenticated: boolean;
  onOpenAuth: () => void;
  onOpenNewRun: () => void;
  onLogout: () => void;
  backendHealth: BackendHealth;
}

export default function Navbar({
  currentView,
  setCurrentView,
  isAuthenticated: _isAuthenticated,
  onOpenAuth,
  onOpenNewRun,
  onLogout,
  backendHealth
}: NavbarProps) {
  const isAppMode = currentView !== 'landing';

  return (
    <header className="sticky top-0 z-50 w-full border-b border-white/[0.08] bg-[#08090b]/85 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Brand Logo */}
        <div className="flex items-center gap-6">
          <button
            onClick={() => setCurrentView('landing')}
            className="flex items-center gap-2.5 group cursor-pointer text-left focus:outline-none"
          >
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#181d26] to-[#0e1117] border border-white/15 flex items-center justify-center relative shadow-md group-hover:border-[#00f2b2]/40 transition-all">
              <span className="font-mono-num font-black text-sm tracking-tighter text-white">EX</span>
              <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-[#00f2b2] shadow-[0_0_8px_#00f2b2]" />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-sm tracking-widest text-white uppercase">EVALX</span>
                <span className="text-[10px] font-mono-num px-1.5 py-0.2 rounded bg-white/10 text-white/70">
                  v1.0
                </span>
              </div>
              <p className="text-[10px] text-white/40 tracking-wider font-mono-num -mt-0.5 hidden sm:block">
                AI EVALUATION ENGINE
              </p>
            </div>
          </button>

          {/* App Navigation Tabs */}
          {isAppMode && (
            <nav className="hidden md:flex items-center gap-1 ml-4 pl-4 border-l border-white/10">
              <button
                onClick={() => setCurrentView('overview')}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  currentView === 'overview'
                    ? 'text-white bg-white/10 shadow-sm'
                    : 'text-white/60 hover:text-white hover:bg-white/5'
                }`}
              >
                Overview
              </button>
              <button
                onClick={() => setCurrentView('evaluations')}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  currentView === 'evaluations'
                    ? 'text-white bg-white/10 shadow-sm'
                    : 'text-white/60 hover:text-white hover:bg-white/5'
                }`}
              >
                Evaluations
              </button>
              <button
                onClick={() => setCurrentView('datasets')}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  currentView === 'datasets'
                    ? 'text-white bg-white/10 shadow-sm'
                    : 'text-white/60 hover:text-white hover:bg-white/5'
                }`}
              >
                Datasets
              </button>
              <button
                onClick={() => setCurrentView('insights')}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  currentView === 'insights'
                    ? 'text-white bg-white/10 shadow-sm'
                    : 'text-white/60 hover:text-white hover:bg-white/5'
                }`}
              >
                Insights
              </button>
            </nav>
          )}

          {/* Landing Nav Links */}
          {!isAppMode && (
            <nav className="hidden md:flex items-center gap-6 ml-6 text-xs text-white/70">
              <a href="#demo" className="hover:text-white transition-colors">
                Interactive Engine
              </a>
              <a href="#features" className="hover:text-white transition-colors">
                Quality Gates
              </a>
              <a href="#architecture" className="hover:text-white transition-colors">
                Architecture
              </a>
              <a
                href="https://github.com/jathavedanstackup-cell/evalx-ai-evaluation-engine"
                target="_blank"
                rel="noreferrer"
                className="hover:text-white transition-colors flex items-center gap-1"
              >
                <span>GitHub</span>
                <ExternalLink className="w-3 h-3 text-white/40" />
              </a>
            </nav>
          )}
        </div>

        {/* Right Actions */}
        <div className="flex items-center gap-3">
          <div className="hidden lg:flex items-center gap-2 px-2.5 py-1 rounded-full bg-white/[0.04] border border-white/10 text-[11px] font-mono-num text-white/70">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00f2b2] animate-pulse" />
            <span className="text-white/40">API</span>
            <span className="text-[#00f2b2] font-semibold">{backendHealth.latencyMs}ms</span>
          </div>

          {!isAppMode ? (
            <div className="flex items-center gap-2">
              <button
                onClick={onOpenAuth}
                className="px-3.5 py-1.5 text-xs text-white/80 hover:text-white font-medium transition-colors cursor-pointer"
              >
                Sign In
              </button>
              <button
                onClick={() => setCurrentView('overview')}
                className="px-4 py-1.5 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10"
              >
                Launch Console
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2.5">
              <button
                onClick={onOpenNewRun}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#00f2b2] text-[#08090b] text-xs font-semibold tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-lg shadow-[#00f2b2]/10"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>New Evaluation</span>
              </button>

              <button
                onClick={onLogout}
                title="Exit to Landing"
                className="p-2 rounded-lg text-white/50 hover:text-white hover:bg-white/5 transition-all cursor-pointer"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

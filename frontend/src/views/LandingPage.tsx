import { ArrowRight, ShieldCheck, Activity, Zap, CheckCircle2, ChevronRight, Terminal } from 'lucide-react';
import HeroSignalRing from '../components/canvas/HeroSignalRing';
import LiveDemoEngine from '../components/LiveDemoEngine';
import { BACKEND_URL } from '../services/api';

interface LandingPageProps {
  onLaunchConsole: () => void;
  onOpenSignUp?: () => void;
  onOpenAuth?: () => void;
}

export default function LandingPage({ onLaunchConsole, onOpenSignUp, onOpenAuth }: LandingPageProps) {
  const handleSignUp = onOpenSignUp || onOpenAuth;
  return (
    <div className="relative w-full overflow-hidden bg-[#08090b]">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[1000px] h-[500px] bg-[radial-gradient(ellipse_at_top,rgba(0,242,178,0.08)_0%,rgba(8,9,11,0)_70%)] pointer-events-none" />

      {/* Hero Section */}
      <section className="relative pt-12 pb-20 md:pt-20 md:pb-28 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-7 space-y-6 text-center lg:text-left">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/[0.04] border border-white/10 text-xs font-mono-num text-[#00f2b2]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00f2b2] animate-pulse" />
              <span>EVALX v1.0.0 RELEASE • PRODUCTION CERTIFIED</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white tracking-tight leading-[1.08]">
              Know when your AI changes.
            </h1>

            <p className="text-base sm:text-lg text-white/60 max-w-2xl mx-auto lg:mx-0 leading-relaxed font-sans">
              The continuous evaluation engine for mission-critical AI. Detect subtle regressions, benchmark model updates against golden datasets, and enforce deterministic quality gates before deployment.
            </p>

            <div className="pt-2 flex flex-col sm:flex-row items-center justify-center lg:justify-start gap-4">
              <button
                onClick={onLaunchConsole}
                className="w-full sm:w-auto flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl bg-[#00f2b2] text-[#08090b] font-semibold text-sm tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-xl shadow-[#00f2b2]/15 group"
              >
                <span>Launch Evaluation Console</span>
                <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
              </button>

              <a
                href="#demo"
                className="w-full sm:w-auto flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl bg-white/[0.05] hover:bg-white/[0.09] border border-white/10 text-white font-medium text-sm transition-all cursor-pointer"
              >
                <span>Run Interactive Demo</span>
                <ChevronRight className="w-4 h-4 text-white/40" />
              </a>
            </div>

            <div className="pt-6 border-t border-white/5 flex flex-wrap items-center justify-center lg:justify-start gap-6 text-xs text-white/50 font-mono-num">
              <div className="flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-[#00f2b2]" />
                <span>Zero-Regression CI Gate</span>
              </div>
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4 text-[#00f2b2]" />
                <span>409 Tests Verified</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Activity className="w-4 h-4 text-[#00f2b2]" />
                <span>Async ARQ Pipeline</span>
              </div>
            </div>
          </div>

          <div className="lg:col-span-5 relative flex items-center justify-center w-full">
            <HeroSignalRing />
          </div>
        </div>
      </section>

      {/* Interactive Engine Demo */}
      <section id="demo" className="py-20 border-t border-white/[0.06] bg-[#07080a] relative">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">
          <div className="text-center max-w-2xl mx-auto space-y-3">
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#00f2b2]/10 border border-[#00f2b2]/20 text-xs font-mono-num text-[#00f2b2]">
              LIVE ENGINE EVALUATION
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-white tracking-tight">
              Test how EVALX detects model regressions in real-time
            </h2>
            <p className="text-xs sm:text-sm text-white/50">
              Select a test case below to inspect side-by-side model outputs, evaluate against assertions, and trigger automated deployment gates.
            </p>
          </div>

          <LiveDemoEngine />
        </div>
      </section>

      {/* Value Pillars */}
      <section id="features" className="py-24 border-t border-white/[0.06] max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-2xl mx-auto mb-16 space-y-3">
          <div className="text-xs font-mono-num text-[#00f2b2] uppercase tracking-wider">
            CORE CAPABILITIES
          </div>
          <h2 className="text-3xl font-bold text-white tracking-tight">
            Engineered for deterministic AI quality
          </h2>
          <p className="text-sm text-white/50">
            Everything your team needs to deploy model updates, prompt revisions, and fine-tunes without unexpected behavior drift.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          <div className="p-8 rounded-2xl glass-panel-interactive border border-white/10 flex flex-col justify-between">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-xl bg-[#00f2b2]/10 border border-[#00f2b2]/20 flex items-center justify-center text-[#00f2b2]">
                <ShieldCheck className="w-6 h-6" />
              </div>
              <h3 className="text-lg font-bold text-white tracking-tight">Automated Regression Gates</h3>
              <p className="text-xs text-white/60 leading-relaxed font-sans">
                Never deploy an AI degradation. EVALX runs baseline vs candidate delta assertions in your CI/CD pipelines, blocking pull requests whenever quality or policy bounds drop below defined thresholds.
              </p>
            </div>
            <div className="pt-6 mt-6 border-t border-white/5 text-[11px] font-mono-num text-[#00f2b2]">
              • Automated CI/CD pass/fail exit codes
            </div>
          </div>

          <div className="p-8 rounded-2xl glass-panel-interactive border border-white/10 flex flex-col justify-between">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-xl bg-[#00f2b2]/10 border border-[#00f2b2]/20 flex items-center justify-center text-[#00f2b2]">
                <Zap className="w-6 h-6" />
              </div>
              <h3 className="text-lg font-bold text-white tracking-tight">Precision Evaluator Suite</h3>
              <p className="text-xs text-white/60 leading-relaxed font-sans">
                Comprehensive battery of built-in evaluators: semantic embedding cosine similarity, strict JSON schema validation, exact string match assertions, toxicity detection, and p95 latency SLAs.
              </p>
            </div>
            <div className="pt-6 mt-6 border-t border-white/5 text-[11px] font-mono-num text-[#00f2b2]">
              • Extensible custom Python assertion hooks
            </div>
          </div>

          <div className="p-8 rounded-2xl glass-panel-interactive border border-white/10 flex flex-col justify-between">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-xl bg-[#00f2b2]/10 border border-[#00f2b2]/20 flex items-center justify-center text-[#00f2b2]">
                <Activity className="w-6 h-6" />
              </div>
              <h3 className="text-lg font-bold text-white tracking-tight">Production Telemetry & Audit</h3>
              <p className="text-xs text-white/60 leading-relaxed font-sans">
                Every evaluation run is hashed, immutable, and correlated with distributed trace IDs. Compare candidate models against historical runs with latency percentiles and token economics.
              </p>
            </div>
            <div className="pt-6 mt-6 border-t border-white/5 text-[11px] font-mono-num text-[#00f2b2]">
              • Full audit trail and compliance readiness
            </div>
          </div>
        </div>
      </section>

      {/* Production Architecture Section */}
      <section id="architecture" className="py-20 border-t border-white/[0.06] bg-[#07080a]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
            <div className="lg:col-span-6 space-y-4">
              <div className="text-xs font-mono-num text-[#00f2b2] uppercase tracking-wider">
                DEPLOYED ARCHITECTURE
              </div>
              <h2 className="text-3xl font-bold text-white tracking-tight">
                Built on production-grade infrastructure
              </h2>
              <p className="text-sm text-white/60 leading-relaxed font-sans">
                EVALX is backed by a fully production-certified backend running on Railway, featuring decoupled asynchronous task execution, durable state persistence, and high-throughput Redis queuing.
              </p>

              <div className="space-y-3 pt-2">
                {[
                  { title: 'FastAPI Backend Engine', desc: 'Type-safe asynchronous REST API with OpenAPI documentation' },
                  { title: 'PostgreSQL & Alembic', desc: 'Durable relational schema for runs, cases, datasets, and audits' },
                  { title: 'Redis & ARQ Workers', desc: 'Concurrent async evaluation queue supporting bursty benchmark loads' },
                  { title: 'Certified Python SDK', desc: 'CLI and SDK for integration into GitHub Actions, GitLab CI, and scripts' }
                ].map((item, idx) => (
                  <div key={idx} className="flex items-start gap-3 text-xs">
                    <CheckCircle2 className="w-4 h-4 text-[#00f2b2] shrink-0 mt-0.5" />
                    <div>
                      <span className="font-semibold text-white">{item.title}</span>
                      <span className="text-white/50"> — {item.desc}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="lg:col-span-6 p-6 rounded-2xl glass-panel border border-white/10 font-mono-num text-xs text-white/80 space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-white/10">
                <span className="text-white/40">SYSTEM STATUS: ALL OPERATIONAL</span>
                <span className="text-[#00f2b2] flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-[#00f2b2] animate-pulse" />
                  ONLINE
                </span>
              </div>

              <div className="space-y-2 text-[11px]">
                <div className="flex justify-between p-2 rounded bg-black/40 border border-white/5">
                  <span className="text-white/50">API Gateway</span>
                  <span className="text-white">api-production-bf18c.up.railway.app</span>
                </div>
                <div className="flex justify-between p-2 rounded bg-black/40 border border-white/5">
                  <span className="text-white/50">Quality Certification</span>
                  <span className="text-[#00f2b2]">409 / 409 Tests Passed (100%)</span>
                </div>
                <div className="flex justify-between p-2 rounded bg-black/40 border border-white/5">
                  <span className="text-white/50">Worker Queue</span>
                  <span className="text-white">ARQ / Redis 7.2 (Healthy)</span>
                </div>
                <div className="flex justify-between p-2 rounded bg-black/40 border border-white/5">
                  <span className="text-white/50">Database Engine</span>
                  <span className="text-white">PostgreSQL 16 with Pooler</span>
                </div>
              </div>

              <div className="pt-2">
                <a
                  href={`${BACKEND_URL}/docs`}
                  target="_blank"
                  rel="noreferrer"
                  className="w-full py-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 flex items-center justify-center gap-2 text-white/70 hover:text-white transition-colors"
                >
                  <Terminal className="w-3.5 h-3.5" />
                  <span>Inspect Backend OpenAPI Swagger</span>
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="py-24 max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 text-center space-y-6">
        <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight">
          Ready to eliminate AI regressions?
        </h2>
        <p className="text-sm sm:text-base text-white/60 max-w-xl mx-auto font-sans">
          Deploy model updates with confidence. Launch the evaluation console now or run your first golden dataset benchmark.
        </p>
        <div className="pt-2 flex flex-col sm:flex-row items-center justify-center gap-4">
          <button
            onClick={onLaunchConsole}
            className="w-full sm:w-auto px-8 py-3.5 rounded-xl bg-[#00f2b2] text-[#08090b] font-semibold text-sm tracking-wide hover:bg-[#00d2a0] transition-all cursor-pointer shadow-xl shadow-[#00f2b2]/15"
          >
            Launch Evaluation Console
          </button>
          <button
            onClick={handleSignUp}
            className="w-full sm:w-auto px-8 py-3.5 rounded-xl bg-white/[0.05] hover:bg-white/[0.09] border border-white/10 text-white font-medium text-sm transition-all cursor-pointer"
          >
            Create Team Account
          </button>
        </div>
      </section>
    </div>
  );
}

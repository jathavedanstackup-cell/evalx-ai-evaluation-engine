import { ShieldCheck, ExternalLink, GitBranch } from 'lucide-react';
import { BACKEND_URL } from '../services/api';

export default function Footer() {
  return (
    <footer className="w-full border-t border-white/[0.08] bg-[#060709] py-12 px-4 sm:px-6 lg:px-8 text-white/50 text-xs">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-white font-semibold">
            <span className="w-2 h-2 rounded-full bg-[#00f2b2]" />
            <span>EVALX</span>
            <span className="text-[10px] font-mono-num text-white/40">v1.0.0-RELEASE</span>
          </div>
          <p className="text-white/40 max-w-sm">
            Continuous regression testing, benchmark scoring, and automated CI quality gates for mission-critical AI.
          </p>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-8 font-mono-num text-[11px]">
          <div>
            <div className="text-white font-medium mb-2 font-sans text-xs">DEPLOYMENT</div>
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 text-white/70">
                <span className="w-1.5 h-1.5 rounded-full bg-[#00f2b2]" />
                <span>Railway Live API</span>
              </div>
              <div className="text-white/40 text-[10px]">Postgres • Redis • Worker</div>
            </div>
          </div>

          <div>
            <div className="text-white font-medium mb-2 font-sans text-xs">QUALITY GATES</div>
            <div className="space-y-1">
              <div className="text-white/70">409 Tests Certified</div>
              <div className="text-white/40 text-[10px]">371 Core • 38 SDK</div>
            </div>
          </div>

          <div>
            <div className="text-white font-medium mb-2 font-sans text-xs">SOURCE & DOCS</div>
            <div className="space-y-1">
              <a
                href="https://github.com/jathavedanstackup-cell/evalx-ai-evaluation-engine"
                target="_blank"
                rel="noreferrer"
                className="hover:text-white flex items-center gap-1 text-white/70 transition-colors"
              >
                <span>GitHub Repository</span>
                <ExternalLink className="w-3 h-3 text-white/40" />
              </a>
              <a
                href={`${BACKEND_URL}/docs`}
                target="_blank"
                rel="noreferrer"
                className="hover:text-white flex items-center gap-1 text-white/40 transition-colors"
              >
                <span>OpenAPI Swagger</span>
                <ExternalLink className="w-3 h-3 text-white/40" />
              </a>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto mt-8 pt-6 border-t border-white/5 flex flex-col sm:flex-row items-center justify-between gap-4 text-white/30 text-[11px]">
        <div>© 2026 EVALX Engineering. All rights reserved.</div>
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1">
            <ShieldCheck className="w-3 h-3 text-[#00f2b2]" /> Deterministic Zero-Regression Gate
          </span>
          <span className="flex items-center gap-1">
            <GitBranch className="w-3 h-3 text-white/40" /> commit 7c32878
          </span>
        </div>
      </div>
    </footer>
  );
}

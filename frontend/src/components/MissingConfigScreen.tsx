export default function MissingConfigScreen() {
  return (
    <div className="min-h-screen bg-[#08090b] text-[#f3f4f6] flex items-center justify-center p-6">
      <div className="max-w-md w-full p-8 rounded-2xl bg-[#0e1015] border border-red-500/30 shadow-2xl text-center space-y-4">
        <div className="w-12 h-12 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 flex items-center justify-center mx-auto text-xl font-bold font-mono">
          !
        </div>
        <h1 className="text-xl font-bold tracking-tight text-white">Authentication Configuration Required</h1>
        <p className="text-xs text-white/60 leading-relaxed">
          The application requires <code className="text-[#00f2b2] bg-white/5 px-1.5 py-0.5 rounded font-mono">VITE_CLERK_PUBLISHABLE_KEY</code> to be configured in the production environment.
        </p>
        <div className="p-3 rounded-lg bg-black/50 border border-white/5 text-[11px] font-mono text-white/40 text-left">
          Missing: VITE_CLERK_PUBLISHABLE_KEY<br />
          Provider: Clerk RS256 Authentication
        </div>
      </div>
    </div>
  );
}

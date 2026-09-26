import { useState } from 'react';
import { X } from 'lucide-react';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (email: string, token?: string) => void;
}

export default function AuthModal({ isOpen, onClose, onSuccess }: AuthModalProps) {
  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [apiToken, setApiToken] = useState('');
  const [showTokenInput, setShowTokenInput] = useState(false);
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      onSuccess(email || 'engineer@evalx.ai', apiToken || undefined);
      onClose();
    }, 300);
  };

  const handleGoogleSignIn = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      onSuccess('user@gmail.com');
      onClose();
    }, 300);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-sm p-6 sm:p-8 rounded-2xl bg-[#0c0d11] border border-white/10 shadow-2xl">
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-2 text-white/40 hover:text-white transition-colors cursor-pointer"
          aria-label="Close"
        >
          <X className="w-4 h-4" />
        </button>

        {/* Minimal Header */}
        <div className="mb-6">
          <div className="text-sm font-bold tracking-widest text-[#00f2b2] uppercase font-mono-num mb-1">
            EVALX
          </div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            {isSignUp ? 'Create account' : 'Welcome back'}
          </h2>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-white/60 mb-1.5">
              Email
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
              className="w-full px-3.5 py-2.5 rounded-lg bg-black/50 border border-white/10 text-white placeholder-white/25 text-sm focus:outline-none focus:border-[#00f2b2] focus:ring-1 focus:ring-[#00f2b2] transition-colors"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-white/60">
                Password
              </label>
              {!isSignUp && (
                <button
                  type="button"
                  onClick={() => alert('Password reset link sent to your email.')}
                  className="text-xs text-white/40 hover:text-white/80 transition-colors"
                >
                  Forgot password?
                </button>
              )}
            </div>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
              className="w-full px-3.5 py-2.5 rounded-lg bg-black/50 border border-white/10 text-white placeholder-white/25 text-sm focus:outline-none focus:border-[#00f2b2] focus:ring-1 focus:ring-[#00f2b2] transition-colors"
            />
          </div>

          {/* Optional Direct Railway API Token Input */}
          <div className="pt-1">
            {!showTokenInput ? (
              <button
                type="button"
                onClick={() => setShowTokenInput(true)}
                className="text-[11px] text-white/40 hover:text-[#00f2b2] transition-colors font-mono-num"
              >
                + Connect Railway API Bearer Token
              </button>
            ) : (
              <div>
                <label className="block text-[11px] font-mono-num text-[#00f2b2] mb-1">
                  Railway Bearer Token
                </label>
                <input
                  type="password"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  placeholder="Bearer token or Clerk JWT..."
                  className="w-full px-3 py-2 rounded-lg bg-black/70 border border-[#00f2b2]/30 text-white placeholder-white/20 text-xs font-mono-num focus:outline-none focus:border-[#00f2b2]"
                />
              </div>
            )}
          </div>

          {/* Primary Action Button */}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-[#00f2b2] text-[#08090b] font-semibold text-sm hover:bg-[#00d2a0] transition-colors cursor-pointer mt-2 disabled:opacity-50"
          >
            {loading ? 'Authenticating...' : isSignUp ? 'Create account' : 'Sign in'}
          </button>
        </form>

        {/* Divider */}
        <div className="relative flex items-center justify-center my-4">
          <div className="border-t border-white/10 w-full" />
          <span className="bg-[#0c0d11] px-3 text-[11px] text-white/30 uppercase tracking-wider">
            or
          </span>
        </div>

        {/* Google Sign In Button */}
        <button
          onClick={handleGoogleSignIn}
          className="w-full py-2.5 px-4 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 flex items-center justify-center gap-2.5 text-xs font-medium text-white transition-colors cursor-pointer"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24">
            <path
              fill="#EA4335"
              d="M12 5c1.6 0 3 .6 4.1 1.7l3.1-3.1C17.3 1.8 14.8 1 12 1 7.5 1 3.7 3.6 1.9 7.3l3.7 2.9C6.5 7.4 9 5 12 5z"
            />
            <path
              fill="#4285F4"
              d="M23.5 12.3c0-.8-.1-1.7-.2-2.3H12v4.5h6.5c-.3 1.5-1.1 2.8-2.4 3.7l3.7 2.9c2.2-2 3.7-5.1 3.7-8.8z"
            />
            <path
              fill="#FBBC05"
              d="M5.6 14.8c-.2-.7-.4-1.5-.4-2.3s.2-1.5.4-2.3L1.9 7.3C.7 9.7 0 12 0 14.8s.7 5.1 1.9 7.5l3.7-2.9z"
            />
            <path
              fill="#34A853"
              d="M12 23.5c3.2 0 6-1.1 8-3l-3.7-2.9c-1.1.7-2.5 1.2-4.3 1.2-3 0-5.5-2.4-6.4-5.2L1.9 16.5C3.7 20.2 7.5 23.5 12 23.5z"
            />
          </svg>
          <span>Continue with Google</span>
        </button>

        {/* Toggle Account Creation */}
        <div className="mt-5 text-center text-xs text-white/50">
          {isSignUp ? (
            <span>
              Already have an account?{' '}
              <button
                onClick={() => setIsSignUp(false)}
                className="text-[#00f2b2] hover:underline font-medium cursor-pointer"
              >
                Sign in
              </button>
            </span>
          ) : (
            <span>
              Don't have an account?{' '}
              <button
                onClick={() => setIsSignUp(true)}
                className="text-[#00f2b2] hover:underline font-medium cursor-pointer"
              >
                Create account
              </button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

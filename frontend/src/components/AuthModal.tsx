import { useState } from 'react';
import { X, ArrowRight, ShieldCheck } from 'lucide-react';
import { useSignIn, useSignUp } from '@clerk/clerk-react';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export default function AuthModal({ isOpen, onClose, onSuccess }: AuthModalProps) {
  const { isLoaded: isSignInLoaded, signIn, setActive: setSignInActive } = useSignIn();
  const { isLoaded: isSignUpLoaded, signUp, setActive: setSignUpActive } = useSignUp();

  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pendingVerification, setPendingVerification] = useState(false);
  const [verificationCode, setVerificationCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      if (isSignUp) {
        if (!isSignUpLoaded) {
          setError('Authentication service initializing. Please wait...');
          setLoading(false);
          return;
        }

        const result = await signUp.create({
          emailAddress: email.trim(),
          password: password,
        });

        if (result.status === 'complete') {
          if (result.createdSessionId) {
            await setSignUpActive({ session: result.createdSessionId });
          }
          setLoading(false);
          onSuccess();
          onClose();
        } else if (result.status === 'missing_requirements') {
          // Prepare email address verification
          await signUp.prepareEmailAddressVerification({ strategy: 'email_code' });
          setPendingVerification(true);
          setLoading(false);
        } else {
          setError(`Registration incomplete (status: ${result.status})`);
          setLoading(false);
        }
      } else {
        if (!isSignInLoaded) {
          setError('Authentication service initializing. Please wait...');
          setLoading(false);
          return;
        }

        const result = await signIn.create({
          identifier: email.trim(),
          password: password,
        });

        if (result.status === 'complete') {
          if (result.createdSessionId) {
            await setSignInActive({ session: result.createdSessionId });
          }
          setLoading(false);
          onSuccess();
          onClose();
        } else {
          setError(`Sign in incomplete (status: ${result.status})`);
          setLoading(false);
        }
      }
    } catch (err: any) {
      console.error('Clerk authentication error:', err);
      const msg = err.errors?.[0]?.message || err.errors?.[0]?.longMessage || err.message || 'Authentication failed. Please verify credentials.';
      setError(msg);
      setLoading(false);
    }
  };

  const handleVerifyCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isSignUpLoaded) return;
    setError(null);
    setLoading(true);

    try {
      const result = await signUp.attemptEmailAddressVerification({
        code: verificationCode.trim(),
      });

      if (result.status === 'complete') {
        if (result.createdSessionId) {
          await setSignUpActive({ session: result.createdSessionId });
        }
        setLoading(false);
        onSuccess();
        onClose();
      } else {
        setError(`Verification status: ${result.status}`);
        setLoading(false);
      }
    } catch (err: any) {
      console.error('Clerk verification error:', err);
      const msg = err.errors?.[0]?.message || err.message || 'Verification failed. Please check the code.';
      setError(msg);
      setLoading(false);
    }
  };

  const handleGoogleSignIn = async () => {
    if (!isSignInLoaded) return;
    setError(null);
    setLoading(true);
    try {
      await signIn.authenticateWithRedirect({
        strategy: 'oauth_google',
        redirectUrl: '/sso-callback',
        redirectUrlComplete: '/',
      });
    } catch (err: any) {
      setError(err.errors?.[0]?.message || 'Google authentication failed');
      setLoading(false);
    }
  };

  const resetForm = () => {
    setError(null);
    setPendingVerification(false);
    setVerificationCode('');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-sm p-6 sm:p-8 rounded-2xl bg-[#0c0d11] border border-white/10 shadow-2xl">
        {/* Close Button */}
        <button
          onClick={() => {
            resetForm();
            onClose();
          }}
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
            {pendingVerification
              ? 'Verify Email'
              : isSignUp
              ? 'Create account'
              : 'Welcome back'}
          </h2>
          <p className="text-xs text-white/50 mt-1">
            {pendingVerification
              ? `Enter the code sent to ${email}`
              : isSignUp
              ? 'Start running regression gates and evaluation pipelines'
              : 'Sign in to access your evaluation workspace'}
          </p>
        </div>

        {/* Error Notification */}
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs leading-relaxed">
            {error}
          </div>
        )}

        {pendingVerification ? (
          /* Email Verification Form */
          <form onSubmit={handleVerifyCode} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-white/60 mb-1.5">
                Verification Code
              </label>
              <input
                type="text"
                required
                autoFocus
                value={verificationCode}
                onChange={(e) => setVerificationCode(e.target.value)}
                placeholder="123456"
                className="w-full px-3.5 py-2.5 rounded-lg bg-black/50 border border-white/10 text-white placeholder-white/25 text-sm focus:outline-none focus:border-[#00f2b2] focus:ring-1 focus:ring-[#00f2b2] transition-colors font-mono tracking-widest text-center"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-lg bg-[#00f2b2] text-[#08090b] font-semibold text-sm hover:bg-[#00d2a0] transition-colors cursor-pointer mt-2 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <span>{loading ? 'Verifying...' : 'Complete Sign Up'}</span>
              <ShieldCheck className="w-4 h-4" />
            </button>

            <button
              type="button"
              onClick={() => setPendingVerification(false)}
              className="w-full text-xs text-white/40 hover:text-white/80 transition-colors text-center cursor-pointer mt-1"
            >
              Back to registration
            </button>
          </form>
        ) : (
          /* Standard Sign In / Sign Up Form */
          <>
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
                    <span className="text-xs text-white/40 font-mono text-[10px]">
                      Clerk RS256 Auth
                    </span>
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

              {/* Primary Action Button */}
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 rounded-lg bg-[#00f2b2] text-[#08090b] font-semibold text-sm hover:bg-[#00d2a0] transition-colors cursor-pointer mt-2 disabled:opacity-50 flex items-center justify-center gap-2"
              >
                <span>{loading ? 'Authenticating...' : isSignUp ? 'Create account' : 'Sign in'}</span>
                <ArrowRight className="w-4 h-4" />
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
              type="button"
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
                    onClick={() => {
                      resetForm();
                      setIsSignUp(false);
                    }}
                    className="text-[#00f2b2] hover:underline font-medium cursor-pointer"
                  >
                    Sign in
                  </button>
                </span>
              ) : (
                <span>
                  Don't have an account?{' '}
                  <button
                    onClick={() => {
                      resetForm();
                      setIsSignUp(true);
                    }}
                    className="text-[#00f2b2] hover:underline font-medium cursor-pointer"
                  >
                    Create account
                  </button>
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

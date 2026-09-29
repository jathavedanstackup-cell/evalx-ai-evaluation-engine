import { useState, useEffect, useMemo, useCallback } from 'react';
import { useAuth, useClerk } from '@clerk/clerk-react';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import LandingPage from './views/LandingPage';
import OverviewView from './views/OverviewView';
import EvaluationsView from './views/EvaluationsView';
import DatasetsView from './views/DatasetsView';
import InsightsView from './views/InsightsView';
import NewRunModal from './components/NewRunModal';
import NewDatasetModal from './components/NewDatasetModal';
import EvaluationDetailModal from './components/EvaluationDetailModal';
import {
  checkBackendHealth,
  fetchDatasets,
  fetchEvaluationRuns,
  computeOverviewMetrics,
  extractFailureClusters,
} from './services/api';
import type { BackendHealth } from './services/api';
import type { EvaluationRun, Dataset } from './types/evalx';

export default function App() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { openSignIn, openSignUp, signOut } = useClerk();

  const [currentView, setCurrentView] = useState<'landing' | 'overview' | 'evaluations' | 'datasets' | 'insights'>('landing');
  const [isNewRunModalOpen, setIsNewRunModalOpen] = useState<boolean>(false);
  const [isNewDatasetModalOpen, setIsNewDatasetModalOpen] = useState<boolean>(false);
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null);

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [isUnauthenticated, setIsUnauthenticated] = useState<boolean>(!isSignedIn);
  const [activeToken, setActiveToken] = useState<string | null>(null);

  const [backendHealth, setBackendHealth] = useState<BackendHealth>({
    status: 'online',
    latencyMs: 0,
    live: false,
    ready: false,
    timestamp: new Date().toISOString()
  });

  // Verify Railway live backend & load real data
  const loadBackendData = useCallback(async (token?: string) => {
    const health = await checkBackendHealth();
    setBackendHealth(health);

    const freshToken = token || (await getToken());
    if (!freshToken) {
      setIsUnauthenticated(true);
      setDatasets([]);
      setRuns([]);
      return;
    }

    const [dsRes, runsRes] = await Promise.all([
      fetchDatasets(freshToken),
      fetchEvaluationRuns(freshToken)
    ]);

    if (dsRes.unauthenticated || runsRes.unauthenticated) {
      setIsUnauthenticated(true);
    } else {
      setIsUnauthenticated(false);
      setDatasets(dsRes.data);
      setRuns(runsRes.data);
    }
  }, [getToken]);

  // Fetch token and sync with Clerk auth state
  useEffect(() => {
    let isMounted = true;
    if (!isLoaded) return;

    if (isSignedIn) {
      getToken().then((token) => {
        if (isMounted) {
          setActiveToken(token);
          if (token) {
            loadBackendData(token);
          }
        }
      }).catch((err) => {
        console.error('Error fetching Clerk session token:', err);
      });
    } else {
      Promise.resolve().then(() => {
        if (isMounted) {
          setActiveToken(null);
          setIsUnauthenticated(true);
          setDatasets([]);
          setRuns([]);
        }
      });
    }
    return () => {
      isMounted = false;
    };
  }, [isLoaded, isSignedIn, getToken, loadBackendData]);

  // Periodic health check
  useEffect(() => {
    checkBackendHealth().then((health) => {
      setBackendHealth(health);
    });

    const interval = setInterval(() => {
      checkBackendHealth().then((health) => {
        setBackendHealth(health);
      });
    }, 20000);

    return () => clearInterval(interval);
  }, []);

  // Deterministically compute overview metrics from actual runs and live probe latency
  const metrics = useMemo(() => {
    return computeOverviewMetrics(runs, backendHealth.latencyMs, datasets.length, isUnauthenticated);
  }, [runs, backendHealth.latencyMs, datasets.length, isUnauthenticated]);

  // Generate real failure clusters from actual failed cases
  const clusters = useMemo(() => {
    return extractFailureClusters(runs);
  }, [runs]);

  const handleLaunchConsole = () => {
    setCurrentView('overview');
  };

  const handleLogout = async () => {
    try {
      await signOut();
    } catch (err) {
      console.error('SignOut error:', err);
    }
    setActiveToken(null);
    setDatasets([]);
    setRuns([]);
    setIsUnauthenticated(true);
    setCurrentView('landing');
  };

  const handleRunCreated = (newRun: EvaluationRun) => {
    setRuns((prev) => [newRun, ...prev]);
    setSelectedRun(newRun);
  };

  const handleDatasetCreated = (newDs: Dataset) => {
    setDatasets((prev) => [newDs, ...prev]);
  };

  const handleRunDataset = (_dataset: Dataset) => {
    setIsNewRunModalOpen(true);
  };

  return (
    <div className="min-h-screen flex flex-col bg-[#08090b] text-[#f3f4f6]">
      <Navbar
        currentView={currentView}
        setCurrentView={setCurrentView}
        isAuthenticated={Boolean(isSignedIn)}
        onOpenSignIn={() => openSignIn()}
        onOpenSignUp={() => openSignUp()}
        onOpenNewRun={() => setIsNewRunModalOpen(true)}
        onLogout={handleLogout}
        backendHealth={backendHealth}
      />

      <main className="flex-1">
        {currentView === 'landing' && (
          <LandingPage
            onLaunchConsole={handleLaunchConsole}
            onOpenSignUp={() => openSignUp()}
          />
        )}

        {currentView === 'overview' && (
          <OverviewView
            metrics={metrics}
            recentRuns={runs}
            onSelectRun={(run) => setSelectedRun(run)}
            onNewRun={() => setIsNewRunModalOpen(true)}
            onNavigateToEvaluations={() => setCurrentView('evaluations')}
            onNavigateToDatasets={() => setCurrentView('datasets')}
            onOpenSignIn={() => openSignIn()}
          />
        )}

        {currentView === 'evaluations' && (
          <EvaluationsView
            runs={runs}
            onSelectRun={(run) => setSelectedRun(run)}
            onNewRun={() => setIsNewRunModalOpen(true)}
          />
        )}

        {currentView === 'datasets' && (
          <DatasetsView
            datasets={datasets}
            onNewDataset={() => setIsNewDatasetModalOpen(true)}
            onRunDataset={handleRunDataset}
          />
        )}

        {currentView === 'insights' && (
          <InsightsView clusters={clusters} />
        )}
      </main>

      <Footer />

      <NewRunModal
        isOpen={isNewRunModalOpen}
        onClose={() => setIsNewRunModalOpen(false)}
        datasets={datasets}
        token={activeToken || undefined}
        onRunCreated={handleRunCreated}
      />

      <NewDatasetModal
        isOpen={isNewDatasetModalOpen}
        onClose={() => setIsNewDatasetModalOpen(false)}
        token={activeToken || undefined}
        onDatasetCreated={handleDatasetCreated}
      />

      <EvaluationDetailModal
        run={selectedRun}
        onClose={() => setSelectedRun(null)}
      />
    </div>
  );
}

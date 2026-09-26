import { useState, useEffect, useMemo, useCallback } from 'react';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import LandingPage from './views/LandingPage';
import OverviewView from './views/OverviewView';
import EvaluationsView from './views/EvaluationsView';
import DatasetsView from './views/DatasetsView';
import InsightsView from './views/InsightsView';
import AuthModal from './components/AuthModal';
import NewRunModal from './components/NewRunModal';
import NewDatasetModal from './components/NewDatasetModal';
import EvaluationDetailModal from './components/EvaluationDetailModal';
import {
  checkBackendHealth,
  fetchDatasets,
  fetchEvaluationRuns,
  computeOverviewMetrics,
  extractFailureClusters,
  fetchRunResults,
} from './services/api';
import type { BackendHealth } from './services/api';
import type { EvaluationRun, Dataset } from './types/evalx';

export default function App() {
  const [currentView, setCurrentView] = useState<'landing' | 'overview' | 'evaluations' | 'datasets' | 'insights'>('landing');
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(() => {
    return Boolean(localStorage.getItem('evalx_auth_token'));
  });
  const [isAuthModalOpen, setIsAuthModalOpen] = useState<boolean>(false);
  const [isNewRunModalOpen, setIsNewRunModalOpen] = useState<boolean>(false);
  const [isNewDatasetModalOpen, setIsNewDatasetModalOpen] = useState<boolean>(false);
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null);

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [isUnauthenticated, setIsUnauthenticated] = useState<boolean>(() => {
    return !localStorage.getItem('evalx_auth_token');
  });

  const [backendHealth, setBackendHealth] = useState<BackendHealth>({
    status: 'online',
    latencyMs: 0,
    live: false,
    ready: false,
    timestamp: new Date().toISOString()
  });

  // Verify Railway live backend & load real data
  const loadBackendData = useCallback(async (token?: string) => {
    // 1. Probe public live & worker telemetry from Railway
    const health = await checkBackendHealth();
    setBackendHealth(health);

    // 2. Fetch protected datasets and evaluation runs
    const [dsRes, runsRes] = await Promise.all([
      fetchDatasets(token),
      fetchEvaluationRuns(token)
    ]);

    if (dsRes.unauthenticated || runsRes.unauthenticated) {
      setIsUnauthenticated(true);
    } else {
      setIsUnauthenticated(false);
      setDatasets(dsRes.data);
      setRuns(runsRes.data);
    }
  }, []);

  useEffect(() => {
    let active = true;

    const runSync = async () => {
      await loadBackendData();
    };
    void runSync();

    const interval = setInterval(() => {
      checkBackendHealth().then((health) => {
        if (active) setBackendHealth(health);
      });
    }, 20000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [loadBackendData]);

  // Deterministically compute overview metrics from actual runs and live probe latency
  const metrics = useMemo(() => {
    return computeOverviewMetrics(runs, backendHealth.latencyMs, datasets.length, isUnauthenticated);
  }, [runs, backendHealth.latencyMs, datasets.length, isUnauthenticated]);

  // Extract real failure clusters from actual failed cases
  const clusters = useMemo(() => {
    return extractFailureClusters(runs);
  }, [runs]);

  const handleLaunchConsole = () => {
    setCurrentView('overview');
  };

  const handleAuthSuccess = (_userEmail: string, sessionToken?: string) => {
    setIsAuthenticated(true);
    setIsUnauthenticated(false);
    if (sessionToken) {
      localStorage.setItem('evalx_auth_token', sessionToken);
      loadBackendData(sessionToken);
    } else {
      loadBackendData();
    }
    setCurrentView('overview');
  };

  const handleLogout = () => {
    setIsAuthenticated(false);
    setIsUnauthenticated(true);
    localStorage.removeItem('evalx_auth_token');
    setDatasets([]);
    setRuns([]);
    setCurrentView('landing');
  };

  const handleSelectRun = async (run: EvaluationRun) => {
    setSelectedRun(run);
    const cases = await fetchRunResults(run.id);
    if (cases.length > 0) {
      setSelectedRun((prev) => (prev && prev.id === run.id ? { ...prev, cases } : prev));
    }
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
        isAuthenticated={isAuthenticated}
        onOpenAuth={() => setIsAuthModalOpen(true)}
        onOpenNewRun={() => setIsNewRunModalOpen(true)}
        onLogout={handleLogout}
        backendHealth={backendHealth}
      />

      <main className="flex-1">
        {currentView === 'landing' && (
          <LandingPage
            onLaunchConsole={handleLaunchConsole}
            onOpenAuth={() => setIsAuthModalOpen(true)}
          />
        )}

        {currentView === 'overview' && (
          <OverviewView
            metrics={metrics}
            recentRuns={runs}
            onSelectRun={handleSelectRun}
            onNewRun={() => setIsNewRunModalOpen(true)}
            onNavigateToEvaluations={() => setCurrentView('evaluations')}
            onNavigateToDatasets={() => setCurrentView('datasets')}
            onOpenAuth={() => setIsAuthModalOpen(true)}
          />
        )}

        {currentView === 'evaluations' && (
          <EvaluationsView
            runs={runs}
            onSelectRun={handleSelectRun}
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

      {/* Global Modals */}
      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        onSuccess={handleAuthSuccess}
      />

      <NewRunModal
        isOpen={isNewRunModalOpen}
        onClose={() => setIsNewRunModalOpen(false)}
        datasets={datasets}
        onRunCreated={handleRunCreated}
      />

      <NewDatasetModal
        isOpen={isNewDatasetModalOpen}
        onClose={() => setIsNewDatasetModalOpen(false)}
        onDatasetCreated={handleDatasetCreated}
      />

      <EvaluationDetailModal
        run={selectedRun}
        onClose={() => setSelectedRun(null)}
      />
    </div>
  );
}

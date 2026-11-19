import React, { useState, useEffect, useCallback, useRef } from 'react';
import Navbar from './components/Navbar';
import Dashboard from './pages/Dashboard';
import Clips from './pages/Clips';
import Shorts from './pages/Shorts';
import LiveFeed from './pages/LiveFeed';
import Settings from './pages/Settings';
import { 
  fetchStatus, 
  fetchQuota, 
  fetchClips, 
  fetchShorts, 
  fetchHighlights, 
  fetchSettings,
  startMonitoring, 
  stopMonitoring, 
  processPendingShorts, 
  processSingleShort,
  uploadSingleShort, 
  autoUploadShorts, 
  updateSettings,
  deleteSingleClip,
  deleteAllClips,
  deleteSingleShort,
  deleteAllShorts 
} from './api';
import { AlertCircle, CheckCircle2, X } from 'lucide-react';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [status, setStatus] = useState(null);
  const [quota, setQuota] = useState(null);
  const [clips, setClips] = useState([]);
  const [shorts, setShorts] = useState([]);
  const [highlights, setHighlights] = useState([]);
  const [settings, setSettings] = useState({});
  const [liveEvents, setLiveEvents] = useState([]);
  
  // Loading & toast states
  const [isStarting, setIsStarting] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [toast, setToast] = useState(null);
  const [clipProcessingStatus, setClipProcessingStatus] = useState(null);

  const wsRef = useRef(null);

  const showToast = (message, type = 'info') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadAllData = useCallback(async () => {
    try {
      const [st, q, cl, sh, hl, sett] = await Promise.all([
        fetchStatus().catch(() => null),
        fetchQuota().catch(() => null),
        fetchClips().catch(() => []),
        fetchShorts().catch(() => []),
        fetchHighlights(30).catch(() => []),
        fetchSettings().catch(() => ({})),
      ]);

      if (st) setStatus(st);
      if (q) setQuota(q);
      if (cl) setClips(cl);
      if (sh) setShorts(sh);
      if (hl) setHighlights(hl);
      if (sett) setSettings(sett);
    } catch (err) {
      console.error('Failed to load initial data:', err);
    }
  }, []);

  // WebSocket Connection
  useEffect(() => {
    let reconnectTimeout = null;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;

    function connectWs() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('Connected to LiveClipper WebSocket');
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          // Add to live events timeline
          setLiveEvents(prev => [payload, ...prev].slice(0, 100));

          // Trigger state refreshes based on event types
          if (payload.type === 'status_update' || payload.type === 'monitoring_started' || payload.type === 'monitoring_stopped') {
            loadAllData();
          } else if (payload.type === 'clip_extracted') {
            fetchClips().then(setClips).catch(() => {});
            showToast(`New clip extracted: ${payload.data?.title || payload.data?.filename}`, 'success');
          } else if (payload.type === 'processing_started' || payload.type === 'short_processing_started') {
            setClipProcessingStatus({
              active: true,
              clip_id: payload.data?.clip_id,
              title: payload.data?.title || `Clip ${payload.data?.clip_id}`,
              stage: 'trimming',
              percentage: 15,
              status: 'processing',
              error: null,
            });
          } else if (payload.type === 'processing_progress') {
            setClipProcessingStatus(prev => ({
              ...(prev || {}),
              active: true,
              clip_id: payload.data?.clip_id || prev?.clip_id,
              stage: payload.data?.stage || prev?.stage || 'processing',
              percentage: payload.data?.percentage ?? prev?.percentage ?? 50,
              status: 'processing',
              error: null,
            }));
          } else if (payload.type === 'processing_complete' || payload.type === 'short_processing_complete' || payload.type === 'shorts_processing_complete') {
            setClipProcessingStatus(prev => ({
              ...(prev || {}),
              percentage: 100,
              status: 'completed',
              error: null,
            }));
            fetchShorts().then(setShorts).catch(() => {});
            setTimeout(() => {
              setClipProcessingStatus(null);
            }, 3000);
            showToast('Short video processing complete!', 'success');
          } else if (payload.type === 'processing_failed') {
            setClipProcessingStatus(prev => ({
              ...(prev || {}),
              status: 'failed',
              error: payload.data?.error || 'Processing failed',
            }));
          } else if (payload.type === 'upload_complete') {
            fetchShorts().then(setShorts).catch(() => {});
            fetchQuota().then(setQuota).catch(() => {});
            showToast(`Uploaded to YouTube Shorts: ${payload.data?.title}`, 'success');
          } else if (payload.type === 'clip_deleted' || payload.type === 'all_clips_deleted') {
            fetchClips().then(setClips).catch(() => {});
          } else if (payload.type === 'short_deleted' || payload.type === 'all_shorts_deleted') {
            fetchShorts().then(setShorts).catch(() => {});
          }
        } catch (e) {
          console.debug('Raw WS message:', event.data);
        }
      };

      ws.onclose = () => {
        reconnectTimeout = setTimeout(connectWs, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connectWs();
    loadAllData();

    // Periodic poll every 10s for status & disk sync
    const interval = setInterval(loadAllData, 10000);

    return () => {
      clearInterval(interval);
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (wsRef.current) wsRef.current.close();
    };
  }, [loadAllData]);

  // Actions
  const handleToggleMonitoring = async () => {
    if (status?.is_monitoring) {
      await handleStopMonitoring();
    } else {
      await handleStartMonitoring({});
    }
  };

  const handleStartMonitoring = async (payload) => {
    setIsStarting(true);
    try {
      await startMonitoring(payload);
      showToast('Live stream monitoring started!', 'success');
      await loadAllData();
    } catch (err) {
      showToast(`Could not start monitoring: ${err.message}`, 'error');
    } finally {
      setIsStarting(false);
    }
  };

  const handleStopMonitoring = async () => {
    try {
      await stopMonitoring();
      showToast('Monitoring stopped cleanly.', 'info');
      await loadAllData();
    } catch (err) {
      showToast(`Could not stop monitoring: ${err.message}`, 'error');
    }
  };

  const handleProcessSingle = async (clipId) => {
    const targetClip = clips.find(c => c.id === clipId);
    setClipProcessingStatus({
      active: true,
      clip_id: clipId,
      title: targetClip ? targetClip.title : `Clip ${clipId}`,
      stage: 'trimming',
      percentage: 15,
      status: 'processing',
      error: null,
    });
    try {
      await processSingleShort(clipId);
      showToast(`Clip ${clipId} reframed into 9:16 Short!`, 'success');
      await Promise.all([fetchShorts().then(setShorts), fetchClips().then(setClips)]);
    } catch (err) {
      setClipProcessingStatus(prev => ({
        ...(prev || { active: true, clip_id: clipId, title: `Clip ${clipId}` }),
        status: 'failed',
        error: err.message || 'Processing failed',
      }));
      showToast(`Processing failed: ${err.message}`, 'error');
    }
  };

  const handleProcessAll = async () => {
    setIsProcessing(true);
    try {
      const summary = await processPendingShorts();
      showToast(`Processed ${summary.processed} short(s) successfully!`, 'success');
      await Promise.all([fetchShorts().then(setShorts), fetchClips().then(setClips)]);
    } catch (err) {
      showToast(`Batch processing failed: ${err.message}`, 'error');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleUploadSingle = async (shortId, opts) => {
    try {
      await uploadSingleShort(shortId, opts);
      showToast(`Short ${shortId} uploaded to YouTube!`, 'success');
      await Promise.all([fetchShorts().then(setShorts), fetchQuota().then(setQuota)]);
    } catch (err) {
      showToast(`Upload failed: ${err.message}`, 'error');
    }
  };

  const handleUploadAll = async () => {
    setIsUploading(true);
    try {
      const summary = await autoUploadShorts();
      showToast(`Uploaded ${summary.uploaded} short(s) to YouTube!`, 'success');
      await Promise.all([fetchShorts().then(setShorts), fetchQuota().then(setQuota)]);
    } catch (err) {
      showToast(`Batch upload failed: ${err.message}`, 'error');
    } finally {
      setIsUploading(false);
    }
  };

  const handleSaveSettings = async (payload) => {
    setIsSaving(true);
    try {
      const updated = await updateSettings(payload);
      setSettings(updated);
      showToast('Settings saved to .env successfully!', 'success');
    } catch (err) {
      showToast(`Failed to save settings: ${err.message}`, 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteClip = async (clipId) => {
    await deleteSingleClip(clipId);
    setClips(prev => prev.filter(c => c.id !== clipId));
  };

  const handleDeleteAllClips = async () => {
    await deleteAllClips();
    setClips([]);
  };

  const handleDeleteShort = async (clipId) => {
    await deleteSingleShort(clipId);
    setShorts(prev => prev.filter(s => s.id !== clipId));
  };

  const handleDeleteAllShorts = async () => {
    await deleteAllShorts();
    setShorts([]);
  };

  return (
    <div className="min-h-screen bg-[#0a0d14] flex flex-col">
      {/* Toast Notification */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 animate-bounce">
          <div className={`flex items-center space-x-3 px-4 py-3 rounded-xl shadow-2xl border text-sm font-semibold ${
            toast.type === 'error'
              ? 'bg-red-950/90 border-red-500/50 text-red-200'
              : toast.type === 'success'
              ? 'bg-emerald-950/90 border-emerald-500/50 text-emerald-200'
              : 'bg-blue-950/90 border-blue-500/50 text-blue-200'
          }`}>
            {toast.type === 'error' ? <AlertCircle className="w-5 h-5 text-red-400" /> : <CheckCircle2 className="w-5 h-5 text-emerald-400" />}
            <span>{toast.message}</span>
            <button onClick={() => setToast(null)} className="ml-2 hover:opacity-80">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Top Navbar */}
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        status={status}
        quota={quota}
        onToggleMonitoring={handleToggleMonitoring}
        isStarting={isStarting}
      />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {activeTab === 'dashboard' && (
          <Dashboard
            status={status}
            quota={quota}
            highlights={highlights}
            clips={clips}
            shorts={shorts}
            onStartMonitoring={handleStartMonitoring}
            onStopMonitoring={handleStopMonitoring}
            onNavigate={setActiveTab}
            onRefresh={loadAllData}
          />
        )}

        {activeTab === 'clips' && (
          <Clips
            clips={clips}
            onProcessSingle={handleProcessSingle}
            onProcessAll={handleProcessAll}
            isProcessing={isProcessing}
            onRefresh={loadAllData}
            onDeleteClip={handleDeleteClip}
            onDeleteAllClips={handleDeleteAllClips}
            showToast={showToast}
            processingStatus={clipProcessingStatus}
            onDismissProcessingStatus={() => setClipProcessingStatus(null)}
          />
        )}

        {activeTab === 'shorts' && (
          <Shorts
            shorts={shorts}
            quota={quota}
            onUploadSingle={handleUploadSingle}
            onUploadAll={handleUploadAll}
            isUploading={isUploading}
            onRefresh={loadAllData}
            onDeleteShort={handleDeleteShort}
            onDeleteAllShorts={handleDeleteAllShorts}
            showToast={showToast}
          />
        )}

        {activeTab === 'live' && (
          <LiveFeed
            liveEvents={liveEvents}
            onClearEvents={() => setLiveEvents([])}
          />
        )}

        {activeTab === 'settings' && (
          <Settings
            settings={settings}
            onSave={handleSaveSettings}
            isSaving={isSaving}
          />
        )}
      </main>
    </div>
  );
}

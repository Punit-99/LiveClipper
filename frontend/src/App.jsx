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
  deleteAllShorts,
  approveFallbackStream,
  waitTargetChannels
} from './api';
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';

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
  const [toasts, setToasts] = useState([]);
  const [clipProcessingStatus, setClipProcessingStatus] = useState(null);

  const wsRef = useRef(null);

  // Auto-dismissing top-right toast notification manager
  const showToast = useCallback((message, type = 'info', action = null) => {
    const id = Date.now() + Math.random().toString();
    setToasts(prev => [...prev.slice(-3), { id, message, type, action }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4500);
  }, []);

  const dismissToast = (id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
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

          // Trigger state updates and notifications based on event types (Requirement 10)
          if (payload.type === 'status_update' || payload.type === 'monitoring_started' || payload.type === 'monitoring_stopped') {
            loadAllData();
            if (payload.type === 'monitoring_started') {
              showToast('Stream monitoring started!', 'info');
            } else if (payload.type === 'monitoring_stopped') {
              showToast('Stream monitoring stopped.', 'info');
            }
          } else if (payload.type === 'recording_started') {
            loadAllData();
            showToast(payload.data?.message || `Started recording ${payload.data?.channel}`, 'info');
          } else if (payload.type === 'chat_spike_detected') {
            showToast(payload.data?.message || `Chat spike detected on ${payload.data?.channel}!`, 'info');
          } else if (payload.type === 'gemini_analyzing') {
            showToast(payload.data?.message || `Gemini analyzing highlight on ${payload.data?.channel}...`, 'info');
          } else if (payload.type === 'gemini_verdict') {
            const isApproved = Boolean(payload.data?.approved);
            showToast(payload.data?.message || `Gemini verdict: ${payload.data?.score}/100`, isApproved ? 'success' : 'info');
          } else if (payload.type === 'clip_extracting') {
            showToast(payload.data?.message || `Extracting clip for ${payload.data?.channel}...`, 'info');
          } else if (payload.type === 'clip_extracted') {
            fetchClips().then(setClips).catch(() => {});
            showToast(
              payload.data?.message || `New clip extracted: ${payload.data?.title || payload.data?.filename}`, 
              'success', 
              { label: 'View', onClick: () => setActiveTab('clips') }
            );
          } else if (payload.type === 'clip_extraction_failed') {
            showToast(payload.data?.message || `Clip extraction failed: ${payload.data?.error}`, 'error');
          } else if (payload.type === 'stream_offline') {
            loadAllData();
            showToast(payload.data?.message || `Stream ${payload.data?.channel} went offline.`, 'info');
          } else if (payload.type === 'recording_stopped') {
            loadAllData();
            showToast(payload.data?.message || `Recording stopped for ${payload.data?.channel}.`, 'info');
          } else if (payload.type === 'fallback_streams_discovered') {
            loadAllData();
          } else if (payload.type === 'fallback_stream_approved') {
            showToast(`Fallback stream ${payload.data?.channel} approved! Starting recording...`, 'success');
            loadAllData();
          } else if (payload.type === 'waiting_target_channels') {
            showToast(payload.data?.message || 'Waiting for target channels to come online...', 'info');
            loadAllData();
          } else if (payload.type === 'target_channel_online') {
            showToast(`Target channel ${payload.data?.channel} is now LIVE! Starting recording...`, 'success');
            loadAllData();
          } else if (payload.type === 'stats_update') {
            setStatus(prev => prev ? ({ ...prev, live_stats: payload.data }) : prev);
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
  }, [loadAllData, showToast]);

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
      const res = await startMonitoring(payload);
      if (res.status === 'awaiting_fallback_approval') {
        showToast('Target channels offline. Alternative streams found.', 'info');
      } else if (res.status === 'waiting_for_targets') {
        showToast('Target channels offline. Polling targets in background.', 'info');
      } else {
        showToast('Live stream monitoring started!', 'success');
      }
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

  const handleApproveFallback = async (channelId) => {
    try {
      await approveFallbackStream(channelId);
      showToast('Fallback stream approved! Starting recording...', 'success');
      await loadAllData();
    } catch (err) {
      showToast(`Failed to approve stream: ${err.message}`, 'error');
    }
  };

  const handleWaitTarget = async () => {
    try {
      await waitTargetChannels();
      showToast('Waiting for target channels to come online.', 'info');
      await loadAllData();
    } catch (err) {
      showToast(`Failed: ${err.message}`, 'error');
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
      {/* Top-Right Auto-Dismissing Toast Notifications (Requirement 10) */}
      <div className="fixed top-6 right-6 z-50 flex flex-col space-y-2.5 pointer-events-none max-w-sm w-full">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-center justify-between space-x-3 px-4 py-3 rounded-xl shadow-2xl border text-xs font-semibold animate-in slide-in-from-top-2 duration-200 ${
              t.type === 'error'
                ? 'bg-red-950/95 border-red-500/50 text-red-200'
                : t.type === 'success'
                ? 'bg-emerald-950/95 border-emerald-500/50 text-emerald-200'
                : 'bg-[#182030]/95 border-[#24334f] text-slate-200'
            }`}
          >
            <div className="flex items-center space-x-2.5 min-w-0">
              {t.type === 'error' ? (
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              ) : t.type === 'success' ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              ) : (
                <Info className="w-4 h-4 text-blue-400 shrink-0" />
              )}
              <span className="truncate">{t.message}</span>
            </div>

            <div className="flex items-center space-x-2 shrink-0">
              {t.action && (
                <button
                  onClick={() => {
                    t.action.onClick();
                    dismissToast(t.id);
                  }}
                  className="px-2 py-0.5 rounded bg-blue-600 hover:bg-blue-500 text-white text-[11px] font-bold transition-colors"
                >
                  {t.action.label}
                </button>
              )}
              <button
                onClick={() => dismissToast(t.id)}
                className="p-1 hover:opacity-75 rounded transition-opacity"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>

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
            settings={settings}
            highlights={highlights}
            clips={clips}
            shorts={shorts}
            onStartMonitoring={handleStartMonitoring}
            onStopMonitoring={handleStopMonitoring}
            onApproveFallback={handleApproveFallback}
            onWaitTarget={handleWaitTarget}
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

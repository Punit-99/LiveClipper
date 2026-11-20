import React, { useState, useEffect, useCallback, useRef } from "react"
import { Sidebar } from "@/components/layout/Sidebar"
import { Header } from "@/components/layout/Header"
import { PageContainer } from "@/components/layout/PageContainer"
import { CommandPalette } from "@/components/layout/CommandPalette"
import { QueuePopover } from "@/components/queue/QueuePopover"
import { Toaster } from "@/components/ui/sonner"
import Dashboard from "@/pages/Dashboard"
import ImportPage from "@/pages/Import"
import Clips from "@/pages/Clips"
import Shorts from "@/pages/Shorts"
import LiveFeed from "@/pages/LiveFeed"
import Settings from "@/pages/Settings"
import {
  fetchStatus,
  fetchQuota,
  fetchClips,
  fetchShorts,
  fetchHighlights,
  fetchSettings,
  startMonitoring,
  stopMonitoring,
  processSingleShort,
  processPendingShorts,
  reconvertShort,
  uploadSingleShort,
  updateSettings,
  deleteSingleClip,
  deleteAllClips,
  deleteSingleShort,
  deleteAllShorts,
  approveFallbackStream,
  waitTargetChannels,
  fetchProcessingQueue,
  fetchUploadQueue,
  cancelProcessing,
  cancelUpload,
  fetchChannelsLiveStatus,
} from "@/api"
import { toast } from "sonner"
import { ChannelSelectionModal } from "@/components/modals/ChannelSelectionModal"

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard")
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [queueOpen, setQueueOpen] = useState(false)
  const [showChannelModal, setShowChannelModal] = useState(false)

  // Data states
  const [status, setStatus] = useState(null)
  const [quota, setQuota] = useState(null)
  const [clips, setClips] = useState([])
  const [shorts, setShorts] = useState([])
  const [highlights, setHighlights] = useState([])
  const [settings, setSettings] = useState({})
  const [liveEvents, setLiveEvents] = useState([])

  // Queue state
  const [processingQueue, setProcessingQueue] = useState({ active_count: 0, waiting_count: 0, items: [] })
  const [uploadQueue, setUploadQueue] = useState({ active_count: 0, waiting_count: 0, items: [] })
  const [uploadTasks, setUploadTasks] = useState({})

  const [isStarting, setIsStarting] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  const wsRef = useRef(null)

  const loadAllData = useCallback(async () => {
    try {
      const [st, q, cl, sh, hl, sett, pq, uq] = await Promise.all([
        fetchStatus().catch(() => null),
        fetchQuota().catch(() => null),
        fetchClips().catch(() => []),
        fetchShorts().catch(() => []),
        fetchHighlights(30).catch(() => []),
        fetchSettings().catch(() => ({})),
        fetchProcessingQueue().catch(() => ({ active_count: 0, waiting_count: 0, items: [] })),
        fetchUploadQueue().catch(() => ({ active_count: 0, waiting_count: 0, items: [] })),
      ])

      if (st) setStatus(st)
      if (q) setQuota(q)
      if (cl) setClips(cl)
      if (sh) setShorts(sh)
      if (hl) setHighlights(hl)
      if (sett) setSettings(sett)
      if (pq) setProcessingQueue(pq)
      if (uq) setUploadQueue(uq)
    } catch (err) {
      console.error("Failed to load initial data:", err)
    }
  }, [])

  useEffect(() => {
    loadAllData()
    const interval = setInterval(loadAllData, 10000)
    return () => clearInterval(interval)
  }, [loadAllData])

  // WebSocket Connection
  useEffect(() => {
    let reconnectTimeout = null
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${protocol}//${window.location.host}/ws/live`

    function connectWs() {
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        console.log("Connected to LiveClipper WebSocket")
      }

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          setLiveEvents((prev) => [payload, ...prev].slice(0, 100))

          const type = payload.type
          const data = payload.data || {}

          if (type === "status_update" || type === "monitoring_started" || type === "monitoring_stopped") {
            loadAllData()
            if (type === "monitoring_started") toast.info("Stream monitoring started!")
            if (type === "monitoring_stopped") toast.info("Stream monitoring stopped.")
          } else if (type === "recording_started") {
            loadAllData()
            toast.info(`Started recording ${data.channel || ""}`)
          } else if (type === "chat_spike_detected") {
            toast.warning(`Chat spike detected on ${data.channel || ""}!`)
          } else if (type === "clip_extracted") {
            fetchClips().then(setClips).catch(() => {})
            toast.success(`New clip extracted: ${data.title || data.filename || ""}`, {
              action: {
                label: "View",
                onClick: () => setActiveTab("clips"),
              },
            })
          } else if (type === "upload_started") {
            setUploadTasks((prev) => ({
              ...prev,
              [data.clip_id]: { status: "uploading", percentage: 0 },
            }))
            toast.info(`Uploading Short: ${data.title || data.clip_id}...`)
          } else if (type === "upload_progress") {
            setUploadTasks((prev) => ({
              ...prev,
              [data.clip_id]: { status: "uploading", percentage: data.percentage || 0 },
            }))
          } else if (type === "upload_complete") {
            setUploadTasks((prev) => ({
              ...prev,
              [data.clip_id]: { status: "completed", percentage: 100 },
            }))
            fetchShorts().then(setShorts).catch(() => {})
            fetchQuota().then(setQuota).catch(() => {})
            toast.success(`Short uploaded to YouTube!`, {
              action: data.url
                ? {
                    label: "Watch",
                    onClick: () => window.open(data.url, "_blank"),
                  }
                : undefined,
            })
          } else if (type === "upload_failed") {
            setUploadTasks((prev) => ({
              ...prev,
              [data.clip_id]: { status: "failed", error: data.error },
            }))
            toast.error(`Upload failed: ${data.error || ""}`)
          } else if (type === "processing_queue_updated") {
            setProcessingQueue(data)
          } else if (type === "upload_queue_updated") {
            setUploadQueue(data)
          } else if (type === "import_progress") {
            if (data.warning) {
              toast.warning(data.warning)
            }
          } else if (type === "import_complete") {
            toast.success(`Import complete! ${data.clips_count || 0} clips extracted.`, {
              action: {
                label: "View",
                onClick: () => setActiveTab("import"),
              },
            })
            fetchClips().then(setClips).catch(() => {})
          } else if (type === "import_failed") {
            toast.error(`Import failed: ${data.error || "Unknown error"}`)
          }
        } catch (e) {
          console.error("Failed to parse websocket message", e)
        }
      }

      ws.onclose = () => {
        wsRef.current = null
        reconnectTimeout = setTimeout(connectWs, 3000)
      }

      ws.onerror = (err) => {
        console.error("WebSocket error:", err)
        ws.close()
      }
    }

    connectWs()
    return () => {
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      if (wsRef.current) wsRef.current.close()
    }
  }, [loadAllData])

  // Handlers
  const handleRefreshQuota = useCallback(async () => {
    try {
      const q = await fetchQuota()
      if (q) setQuota(q)
      toast.success("YouTube quota updated.")
    } catch {
      toast.error("Failed to refresh quota.")
    }
  }, [])

  const handleToggleMonitoring = async () => {
    if (status?.is_monitoring) {
      try {
        await stopMonitoring()
        toast.success("Stream monitoring stopped.")
        await loadAllData()
      } catch (err) {
        console.error("Failed to stop monitoring:", err)
        toast.error(`Failed to stop monitoring: ${err.message || "Unknown error"}`)
      }
    } else {
      setShowChannelModal(true)
    }
  }

  const handleStartMonitoring = async (payload = {}) => {
    setIsStarting(true)
    try {
      const res = await startMonitoring(payload)
      if (res.status === "awaiting_fallback_approval") {
        toast.info("Target channels offline. Alternative live streams found.")
      } else if (res.status === "waiting_for_targets") {
        toast.info("Target channels offline. Polling targets in background.")
      } else {
        toast.success("Live stream monitoring started!")
      }
      setShowChannelModal(false)
      await loadAllData()
      return res
    } catch (err) {
      console.error("Failed to start monitoring:", err)
      toast.error(`Failed to start monitoring: ${err.message || "Unknown error"}`)
      throw err
    } finally {
      setIsStarting(false)
    }
  }

  const handleStopMonitoring = async () => {
    try {
      await stopMonitoring()
      toast.success("Stream monitoring stopped.")
      await loadAllData()
    } catch (err) {
      console.error("Failed to stop monitoring:", err)
      toast.error(`Failed to stop monitoring: ${err.message || "Unknown error"}`)
    }
  }

  const handleApproveFallback = async (ch) => {
    try {
      await approveFallbackStream(ch)
      toast.success(`Fallback stream ${ch} approved!`)
      await loadAllData()
    } catch (err) {
      toast.error(err.message || "Failed to approve stream.")
    }
  }

  const handleWaitTarget = async () => {
    try {
      await waitTargetChannels()
      toast.info("Waiting for target channels to come online.")
      await loadAllData()
    } catch (err) {
      toast.error(err.message || "Failed to set wait targets.")
    }
  }

  const handleProcessSingle = async (clipId) => {
    try {
      await processSingleShort(clipId)
      await loadAllData()
    } catch (err) {
      console.error("Failed to process single short:", err)
      throw err
    }
  }

  const handleReconvertSingle = async (clipId) => {
    try {
      const res = await reconvertShort(clipId)
      toast.success("Short queued for re-processing!")
      await loadAllData()
      return res
    } catch (err) {
      console.error("Failed to reconvert short:", err)
      toast.error(err.message || "Failed to reconvert short.")
      throw err
    }
  }

  const handleUploadSingle = async (clipId) => {
    try {
      await uploadSingleShort(clipId)
      await loadAllData()
    } catch (err) {
      console.error("Failed to upload single short:", err)
      throw err
    }
  }

  const handleDeleteClip = async (clipId) => {
    try {
      await deleteSingleClip(clipId)
      setClips((prev) => prev.filter((c) => (c.clip_id || c.id) !== clipId))
      await loadAllData()
    } catch (err) {
      console.error("Failed to delete clip:", err)
      throw err
    }
  }

  const handleDeleteAllClips = async () => {
    try {
      await deleteAllClips()
      setClips([])
      await loadAllData()
    } catch (err) {
      console.error("Failed to delete all clips:", err)
      throw err
    }
  }

  const handleDeleteShort = async (clipId) => {
    try {
      await deleteSingleShort(clipId)
      setShorts((prev) => prev.filter((s) => (s.clip_id || s.id) !== clipId))
      await loadAllData()
    } catch (err) {
      console.error("Failed to delete short:", err)
      throw err
    }
  }

  const handleDeleteAllShorts = async () => {
    try {
      await deleteAllShorts()
      setShorts([])
      await loadAllData()
    } catch (err) {
      console.error("Failed to delete all shorts:", err)
      throw err
    }
  }

  const handleSaveSettings = async (newSettings) => {
    setIsSaving(true)
    try {
      const updated = await updateSettings(newSettings)
      setSettings(updated)
      await loadAllData()
    } catch (err) {
      console.error("Failed to save settings:", err)
      throw err
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#0a0d14] text-slate-100 antialiased font-sans">
      {/* Collapsible Sidebar */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        collapsed={sidebarCollapsed}
        setCollapsed={setSidebarCollapsed}
        monitoringStatus={status}
        queueStatus={{ processing: processingQueue, upload: uploadQueue }}
        onOpenQueue={() => setQueueOpen(true)}
      />

      {/* Main Content Area */}
      <div className="flex flex-col flex-1 h-full overflow-hidden">
        <Header
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          monitoringStatus={status}
          quota={quota}
          onToggleMonitoring={handleToggleMonitoring}
          isStarting={isStarting}
          onOpenCommand={() => setCommandOpen(true)}
          queueStatus={{ processing: processingQueue, upload: uploadQueue }}
          onOpenQueue={() => setQueueOpen(true)}
          onRefreshQuota={handleRefreshQuota}
        />

        <PageContainer>
          {activeTab === "dashboard" && (
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
              onOpenStartModal={() => setShowChannelModal(true)}
            />
          )}

          {activeTab === "import" && (
            <ImportPage onNavigate={setActiveTab} />
          )}

          {activeTab === "clips" && (
            <Clips
              clips={clips}
              onProcessSingle={handleProcessSingle}
              onReconvertShort={handleReconvertSingle}
              onProcessAll={() => processPendingShorts()}
              onRefresh={loadAllData}
              onDeleteClip={handleDeleteClip}
              onDeleteAllClips={handleDeleteAllClips}
              processingQueue={processingQueue}
            />
          )}

          {activeTab === "shorts" && (
            <Shorts
              shorts={shorts}
              quota={quota}
              onUploadSingle={handleUploadSingle}
              onUploadAll={() => {}}
              onRefresh={loadAllData}
              onDeleteShort={handleDeleteShort}
              onDeleteAllShorts={handleDeleteAllShorts}
              uploadTasks={uploadTasks}
              uploadQueue={uploadQueue}
            />
          )}

          {activeTab === "live" && (
            <LiveFeed
              liveEvents={liveEvents}
              onClearEvents={() => setLiveEvents([])}
            />
          )}

          {activeTab === "settings" && (
            <Settings
              settings={settings}
              onSave={handleSaveSettings}
              isSaving={isSaving}
            />
          )}
        </PageContainer>
      </div>

      {/* Cmd+K Command Palette */}
      <CommandPalette
        open={commandOpen}
        onOpenChange={setCommandOpen}
        onNavigate={setActiveTab}
        onToggleMonitoring={handleToggleMonitoring}
        isMonitoring={Boolean(status?.is_monitoring)}
        onCheckLive={fetchChannelsLiveStatus}
        onOpenQueue={() => setQueueOpen(true)}
      />

      {/* Queue Popover */}
      <QueuePopover
        open={queueOpen}
        onOpenChange={setQueueOpen}
        queueStatus={{ processing: processingQueue, upload: uploadQueue }}
      />

      {/* Channel Selection Modal for Starting Monitoring */}
      <ChannelSelectionModal
        open={showChannelModal}
        onOpenChange={setShowChannelModal}
        onStartMonitoring={handleStartMonitoring}
        onWaitTarget={handleWaitTarget}
        maxConcurrent={settings?.multi_stream_enabled ? (Number(settings?.max_concurrent_streams) || 2) : 1}
        onNavigateToSettings={() => setActiveTab("settings")}
      />

      {/* Sonner Toast Notifications */}
      <Toaster position="bottom-right" richColors />
    </div>
  )
}

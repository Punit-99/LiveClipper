import React, { useState } from "react"
import {
  Play,
  Square,
  HardDrive,
  BrainCircuit,
  Youtube,
  Film,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Radio,
  ExternalLink,
  RefreshCw,
  X,
  Activity,
  Layers,
  MessageSquare,
  Loader2,
  Check,
  Scissors
} from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog"
import { Checkbox } from "@/components/ui/checkbox"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { StatCard } from "@/components/dashboard/StatCard"
import { DiskUsage } from "@/components/dashboard/DiskUsage"
import { GeminiStatus } from "@/components/dashboard/GeminiStatus"
import { StreamStatus } from "@/components/dashboard/StreamStatus"
import { formatBytes, formatDuration, fetchChannelsLiveStatus } from "@/api"
import { toast } from "sonner"

export default function Dashboard({
  status,
  quota,
  settings,
  highlights = [],
  clips = [],
  shorts = [],
  onStartMonitoring,
  onStopMonitoring,
  onApproveFallback,
  onWaitTarget,
  onNavigate,
  onRefresh,
  onOpenStartModal,
}) {
  const [liveChannelsList, setLiveChannelsList] = useState([])
  const [isCheckingLive, setIsCheckingLive] = useState(false)
  const [isBannerDismissed, setIsBannerDismissed] = useState(false)
  const [lastMonitoringState, setLastMonitoringState] = useState(status?.is_monitoring)

  // Reset dismissal if monitoring toggles
  if (status?.is_monitoring !== lastMonitoringState) {
    setLastMonitoringState(status?.is_monitoring)
    if (status?.is_monitoring) {
      setIsBannerDismissed(false)
    }
  }

  // Fallback streams state
  const fallbackStreams = Array.isArray(status?.fallback_streams) ? status.fallback_streams : []
  const waitingTargetChannels = status?.waiting_target_channels

  const isMonitoring = status?.is_monitoring
  const activeChannels = Array.isArray(status?.active_channels) ? status.active_channels : []
  const targetChannels = Array.isArray(status?.target_channels)
    ? status.target_channels
    : (typeof status?.target_channels === "string" ? status.target_channels.split(",").map((s) => s.trim()).filter(Boolean) : [])
  const uptime = status?.uptime_seconds || 0

  const liveStats = status?.live_stats || {}
  const clipsCount = clips.length
  const shortsCount = shorts.length
  const bufferMb = liveStats.buffer_mb || 0
  const maxBufferMb = settings?.max_total_buffer_mb || 1500

  const handleManualCheckLive = async () => {
    setIsCheckingLive(true)
    try {
      const data = await fetchChannelsLiveStatus()
      setLiveChannelsList(data.channels || [])
      toast.success("Channel live status updated.")
    } catch (err) {
      console.error("Failed to check live status:", err)
      toast.error(`Failed to check live status: ${err.message || "Unknown error"}`)
    } finally {
      setIsCheckingLive(false)
    }
  }

  const handleApprove = async (streamId) => {
    try {
      await onApproveFallback(streamId)
      toast.success(`Fallback stream ${streamId} approved!`)
    } catch (err) {
      console.error("Failed to approve fallback stream:", err)
      toast.error(`Failed to approve stream: ${err.message || "Unknown error"}`)
    }
  }

  const handleWait = async () => {
    try {
      await onWaitTarget()
      toast.info("Waiting for target channels to come online.")
    } catch (err) {
      console.error("Failed to set wait targets:", err)
      toast.error(`Failed to set wait target: ${err.message || "Unknown error"}`)
    }
  }

  return (
    <div className="space-y-6">
      {/* Fallback stream notification banner */}
      {fallbackStreams.length > 0 && !waitingTargetChannels && !isBannerDismissed && (
        <Alert variant="warning" className="relative border-amber-500/30 bg-amber-500/10 backdrop-blur-md pr-10">
          <button
            type="button"
            onClick={() => {
              setIsBannerDismissed(true)
              handleWait()
            }}
            className="absolute top-3 right-3 text-amber-400/80 hover:text-white transition-colors p-1 rounded-md"
            title="Dismiss and keep waiting"
            aria-label="Dismiss banner"
          >
            <X className="w-4 h-4" />
          </button>
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          <AlertTitle className="text-white font-semibold">
            Target channels offline — Fallback streams found
          </AlertTitle>
          <AlertDescription className="mt-2 space-y-3">
            <p className="text-xs text-amber-200/90">
              None of your configured target channels are currently live. Would you like to monitor an alternative live stream or keep waiting?
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 pt-1">
              {(fallbackStreams || []).map((stream) => {
                const channelName = stream.channel_name || stream.channel || stream.channel_id || "Streamer"
                const viewers = stream.viewer_count || stream.viewers || 0
                const category = stream.category || stream.game || "Gaming"
                const streamId = stream.channel || stream.channel_id || stream.id

                return (
                  <div
                    key={streamId}
                    className="p-3 rounded-lg bg-[#0d131f] border border-[#1f293d] flex items-center justify-between gap-2"
                  >
                    <div className="min-w-0 pr-1">
                      <p className="text-xs text-white truncate">
                        <strong className="font-bold text-white">{channelName}</strong>
                        <span className="text-slate-400 font-normal"> · {viewers.toLocaleString()} viewers · {category}</span>
                      </p>
                      {stream.stream_title && (
                        <p className="text-[11px] text-slate-400 truncate mt-0.5">
                          {stream.stream_title}
                        </p>
                      )}
                    </div>
                    <Button
                      size="sm"
                      onClick={() => handleApprove(streamId)}
                      className="h-7 text-xs bg-emerald-600 hover:bg-emerald-500 text-white font-medium shrink-0"
                    >
                      Approve
                    </Button>
                  </div>
                )
              })}
            </div>
            <div className="pt-1 flex items-center space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleWait}
                className="text-xs border-amber-500/40 text-amber-300 hover:bg-amber-500/20"
              >
                Keep Waiting for Target Channels
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Top Stat Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Stream Engine"
          value={isMonitoring ? `ACTIVE (${activeChannels.length})` : "IDLE"}
          subtext={isMonitoring ? `Uptime: ${formatDuration(uptime)}` : "Click Start to begin"}
          icon={Radio}
          badge={isMonitoring ? "LIVE" : "STOPPED"}
          badgeVariant={isMonitoring ? "success" : "secondary"}
          tooltip="Captures live broadcast streams into rolling buffers and monitors chat events."
        />

        <StatCard
          title="Clips Extracted"
          value={clipsCount}
          subtext="Available highlight clips"
          icon={Scissors}
          badge="Ready"
          badgeVariant="purple"
          tooltip="Total video highlight clips captured from live streams or URL imports."
        />

        <StatCard
          title="Processed Shorts"
          value={shortsCount}
          subtext="9:16 vertical videos"
          icon={Film}
          badge="Shorts"
          badgeVariant="purple"
          tooltip="Vertical videos formatted with subtitles and reframing ready for YouTube."
        />

        <StatCard
          title="Daily YouTube Quota"
          value={quota ? `${quota.uploads_today} / ${quota.daily_limit}` : "0 / 5"}
          subtext="Uploads remaining today"
          icon={Youtube}
          badge={`${quota ? Math.max(0, quota.daily_limit - quota.uploads_today) : 5} left`}
          tooltip="YouTube Data API daily upload quota limit (default: 5 uploads/day)."
        />
      </div>

      {/* Secondary Row: Video Buffer & Gemini Intelligence */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <DiskUsage diskStats={{ used_mb: bufferMb, max_mb: maxBufferMb }} />
        <GeminiStatus stats={liveStats} settings={settings} />
      </div>

      {/* Configured Channels Live Status */}
      <StreamStatus
        channels={liveChannelsList.length > 0 ? liveChannelsList : (targetChannels || []).map((ch) => ({ name: ch }))}
        isMonitoring={isMonitoring}
        onCheckLiveStatus={handleManualCheckLive}
        isCheckingLive={isCheckingLive}
        onOpenStartModal={onOpenStartModal}
      />
    </div>
  )
}

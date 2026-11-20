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
}) {
  const [showChannelModal, setShowChannelModal] = useState(false)
  const [liveChannelsList, setLiveChannelsList] = useState([])
  const [selectedChannels, setSelectedChannels] = useState([])
  const [isLoadingChannels, setIsLoadingChannels] = useState(false)
  const [isStarting, setIsStarting] = useState(false)
  const [isCheckingLive, setIsCheckingLive] = useState(false)

  // Fallback streams state
  const fallbackStreams = status?.fallback_streams || []
  const waitingTargetChannels = status?.waiting_target_channels

  const isMonitoring = status?.is_monitoring
  const activeChannels = status?.active_channels || []
  const targetChannels = status?.target_channels || []
  const uptime = status?.uptime_seconds || 0

  const liveStats = status?.live_stats || {}
  const clipsCount = clips.length
  const shortsCount = shorts.length
  const bufferMb = liveStats.buffer_mb || 0
  const maxBufferMb = settings?.max_total_buffer_mb || 1500

  const maxConcurrent = settings?.multi_stream_enabled ? (Number(settings?.max_concurrent_streams) || 2) : 1

  // Handle opening stream selection modal
  const handleOpenStartModal = async () => {
    setIsLoadingChannels(true)
    setShowChannelModal(true)
    try {
      const data = await fetchChannelsLiveStatus()
      const list = data.channels || []
      setLiveChannelsList(list)

      // Auto-select live channels up to maxConcurrent
      const liveOnes = list.filter((c) => c.is_live).map((c) => c.name || c.channel_id)
      setSelectedChannels(liveOnes.slice(0, maxConcurrent))
    } catch (err) {
      toast.error("Failed to fetch channel status: " + err.message)
      setLiveChannelsList([])
    } finally {
      setIsLoadingChannels(false)
    }
  }

  const handleConfirmStart = async () => {
    setIsStarting(true)
    try {
      await onStartMonitoring({ channels: selectedChannels })
      setShowChannelModal(false)
      toast.success("Stream monitoring started.")
    } catch (err) {
      toast.error(err.message || "Failed to start monitoring.")
    } finally {
      setIsStarting(false)
    }
  }

  const handleManualCheckLive = async () => {
    setIsCheckingLive(true)
    try {
      const data = await fetchChannelsLiveStatus()
      setLiveChannelsList(data.channels || [])
      toast.success("Channel live status updated.")
    } catch (err) {
      toast.error("Failed to check live status: " + err.message)
    } finally {
      setIsCheckingLive(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Fallback stream notification banner */}
      {fallbackStreams.length > 0 && !waitingTargetChannels && (
        <Alert variant="warning" className="border-amber-500/30 bg-amber-500/10 backdrop-blur-md">
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          <AlertTitle className="text-white font-semibold">
            Target channels offline — Fallback streams found
          </AlertTitle>
          <AlertDescription className="mt-2 space-y-3">
            <p className="text-xs text-amber-200/90">
              None of your configured target channels are currently live. Would you like to monitor an alternative live stream or keep waiting?
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 pt-1">
              {fallbackStreams.map((stream) => (
                <div
                  key={stream.channel || stream.id}
                  className="p-3 rounded-lg bg-[#0d131f] border border-[#1f293d] flex items-center justify-between"
                >
                  <div className="min-w-0 pr-2">
                    <h5 className="font-semibold text-xs text-white truncate">
                      {stream.channel || stream.title}
                    </h5>
                    <p className="text-[11px] text-slate-400">
                      {(stream.viewers || stream.viewer_count || 0).toLocaleString()} viewers · {stream.game || "Gaming"}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => onApproveFallback(stream.channel || stream.id)}
                    className="h-7 text-xs bg-emerald-600 hover:bg-emerald-500 text-white font-medium shrink-0"
                  >
                    Approve
                  </Button>
                </div>
              ))}
            </div>
            <div className="pt-1 flex items-center space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={onWaitTarget}
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
      />

      {/* Channel Selection Modal on Start Monitoring */}
      <Dialog open={showChannelModal} onOpenChange={setShowChannelModal}>
        <DialogContent className="max-w-lg bg-[#0f1422] border-[#1f293d]">
          <DialogHeader>
            <DialogTitle className="text-white text-base">Select Channels to Monitor</DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Check channels to monitor. Maximum concurrent streams: {maxConcurrent}.
            </DialogDescription>
          </DialogHeader>

          {isLoadingChannels ? (
            <div className="py-8 flex flex-col items-center justify-center space-y-2 text-xs text-slate-400">
              <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
              <span>Checking channel live statuses...</span>
            </div>
          ) : (
            <div className="max-h-64 overflow-y-auto space-y-2 pr-1">
              {liveChannelsList.map((ch) => {
                const id = ch.name || ch.channel_id
                const isLive = Boolean(ch.is_live || ch.live)
                const isChecked = selectedChannels.includes(id)

                return (
                  <div
                    key={id}
                    className={`p-3 rounded-lg border flex items-center justify-between transition-colors ${
                      isLive
                        ? "bg-[#121826] border-[#1f293d] hover:border-slate-600"
                        : "bg-[#0a0d14] border-[#161c28] opacity-60"
                    }`}
                  >
                    <div className="flex items-center space-x-3 min-w-0">
                      <Checkbox
                        checked={isChecked}
                        disabled={!isLive}
                        onCheckedChange={(checked) => {
                          if (checked) {
                            if (selectedChannels.length < maxConcurrent) {
                              setSelectedChannels([...selectedChannels, id])
                            } else {
                              toast.warning(`Maximum ${maxConcurrent} concurrent streams allowed.`)
                            }
                          } else {
                            setSelectedChannels(selectedChannels.filter((c) => c !== id))
                          }
                        }}
                      />
                      <div className="min-w-0">
                        <span className="block text-xs font-semibold text-white truncate">
                          {ch.display_name || ch.name}
                        </span>
                        <span className="text-[11px] text-slate-400">
                          {isLive ? `${(ch.viewer_count || 0).toLocaleString()} viewers` : "Offline"}
                        </span>
                      </div>
                    </div>

                    <Badge variant={isLive ? "success" : "secondary"} className="text-[10px]">
                      {isLive ? "LIVE" : "OFFLINE"}
                    </Badge>
                  </div>
                )
              })}
            </div>
          )}

          <DialogFooter className="pt-3 border-t border-[#1f293d] flex items-center justify-between sm:justify-between">
            <span className="text-xs text-slate-400">
              Selected: {selectedChannels.length} / {maxConcurrent}
            </span>
            <div className="flex items-center space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowChannelModal(false)}
                className="text-xs border-[#1f293d]"
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleConfirmStart}
                disabled={isStarting || selectedChannels.length === 0}
                className="text-xs bg-blue-600 hover:bg-blue-500 text-white font-semibold"
              >
                {isStarting ? "Starting..." : "Start Monitoring"}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

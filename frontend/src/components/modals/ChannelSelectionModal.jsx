import React, { useState, useEffect } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Loader2, Radio, Settings, AlertCircle, RefreshCw } from "lucide-react"
import { fetchChannelsLiveStatus } from "@/api"
import { toast } from "sonner"

export function ChannelSelectionModal({
  open,
  onOpenChange,
  onStartMonitoring,
  onWaitTarget,
  maxConcurrent = 2,
  onNavigateToSettings,
}) {
  const [channels, setChannels] = useState([])
  const [selectedChannels, setSelectedChannels] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [isStarting, setIsStarting] = useState(false)

  const loadChannels = async () => {
    setIsLoading(true)
    try {
      const data = await fetchChannelsLiveStatus()
      const list = Array.isArray(data?.channels) ? data.channels : []
      setChannels(list)

      if (list.length === 0) {
        toast.error("No target channels found. Please configure YouTube channel IDs in Settings first.")
        setSelectedChannels([])
      } else {
        // Auto-select live channels up to maxConcurrent, or fallback to first target channels
        const liveOnes = list.filter((c) => c.is_live).map((c) => c.name || c.id || c.channel_id)
        if (liveOnes.length > 0) {
          setSelectedChannels(liveOnes.slice(0, maxConcurrent))
        } else {
          // Preselect first channel by default so user can directly start
          const firstTarget = list[0]
          setSelectedChannels([firstTarget.name || firstTarget.id || firstTarget.channel_id])
        }
      }
    } catch (err) {
      console.error("Failed to load channel live status:", err)
      toast.error(`Failed to fetch channel status: ${err.message || "Network error"}`)
      setChannels([])
      setSelectedChannels([])
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      loadChannels()
    }
  }, [open])

  const handleToggleChannel = (id) => {
    if (selectedChannels.includes(id)) {
      setSelectedChannels(selectedChannels.filter((c) => c !== id))
    } else {
      if (selectedChannels.length < maxConcurrent) {
        setSelectedChannels([...selectedChannels, id])
      } else {
        toast.warning(`Maximum ${maxConcurrent} concurrent streams allowed.`)
      }
    }
  }

  const handleConfirmStart = async () => {
    setIsStarting(true)
    try {
      await onStartMonitoring({ channels: selectedChannels })
      onOpenChange(false)
    } catch (err) {
      // Toast already shown in parent or handle here
    } finally {
      setIsStarting(false)
    }
  }

  const handleConfirmWait = async () => {
    if (!onWaitTarget) return
    setIsStarting(true)
    try {
      await onWaitTarget()
      onOpenChange(false)
    } catch (err) {
      // handled in parent
    } finally {
      setIsStarting(false)
    }
  }

  const anyLive = channels.some((c) => c.is_live)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg bg-[#0f1422] border-[#1f293d]">
        <DialogHeader>
          <div className="flex items-center space-x-2">
            <Radio className="w-4 h-4 text-emerald-400" />
            <DialogTitle className="text-white text-base">Select Channels to Monitor</DialogTitle>
          </div>
          <DialogDescription className="text-xs text-slate-400">
            Choose configured channels to monitor live. Maximum concurrent streams: {maxConcurrent}.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="py-12 flex flex-col items-center justify-center space-y-2 text-xs text-slate-400">
            <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
            <span>Checking channel live statuses...</span>
          </div>
        ) : channels.length === 0 ? (
          <div className="py-8 text-center space-y-4">
            <div className="w-10 h-10 rounded-full bg-amber-500/10 text-amber-400 mx-auto flex items-center justify-center">
              <AlertCircle className="w-5 h-5" />
            </div>
            <div>
              <p className="text-xs font-semibold text-white">No Target Channels Configured</p>
              <p className="text-[11px] text-slate-400 max-w-xs mx-auto mt-1">
                You must add YouTube Channel IDs (e.g. UCWsDFcIhY2DBi3GB5uykGXA) in Settings before monitoring can start.
              </p>
            </div>
            {onNavigateToSettings && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  onOpenChange(false)
                  onNavigateToSettings()
                }}
                className="text-xs border-[#1f293d] space-x-1.5"
              >
                <Settings className="w-3.5 h-3.5" />
                <span>Go to Settings</span>
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {!anyLive && (
              <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-200/90 flex items-start space-x-2">
                <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  All configured target channels are currently offline. You can still start monitoring to wait for them or allow fallback stream discovery.
                </span>
              </div>
            )}

            <div className="max-h-60 overflow-y-auto space-y-2 pr-1">
              {channels.map((ch) => {
                const id = ch.name || ch.id || ch.channel_id
                const isLive = Boolean(ch.is_live)
                const isChecked = selectedChannels.includes(id)

                return (
                  <div
                    key={id}
                    onClick={() => handleToggleChannel(id)}
                    className={`p-3 rounded-lg border flex items-center justify-between cursor-pointer transition-colors ${
                      isChecked
                        ? "bg-[#151d2f] border-blue-500/40"
                        : "bg-[#0d131f] border-[#1f293d] hover:border-slate-600"
                    }`}
                  >
                    <div className="flex items-center space-x-3 min-w-0">
                      <Checkbox
                        checked={isChecked}
                        onCheckedChange={() => handleToggleChannel(id)}
                        onClick={(e) => e.stopPropagation()}
                      />
                      <div className="min-w-0">
                        <span className="block text-xs font-semibold text-white truncate">
                          {ch.display_name || ch.name || id}
                        </span>
                        <span className="text-[11px] text-slate-400">
                          {isLive ? `${(ch.viewers || ch.viewer_count || 0).toLocaleString()} viewers` : "Offline"}
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
          </div>
        )}

        <DialogFooter className="pt-3 border-t border-[#1f293d] flex items-center justify-between sm:justify-between">
          <div className="flex items-center space-x-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={loadChannels}
              disabled={isLoading}
              className="text-xs text-slate-400 hover:text-white h-7 px-2"
            >
              <RefreshCw className={`w-3 h-3 mr-1 ${isLoading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </Button>
            {channels.length > 0 && (
              <span className="text-[11px] text-slate-400">
                Selected: {selectedChannels.length}/{maxConcurrent}
              </span>
            )}
          </div>

          <div className="flex items-center space-x-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="text-xs border-[#1f293d]"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleConfirmStart}
              disabled={isStarting || isLoading || channels.length === 0}
              className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white font-semibold"
            >
              {isStarting ? "Starting..." : "Start Monitoring"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

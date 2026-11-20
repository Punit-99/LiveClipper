import React, { useState, useEffect, useRef } from "react"
import {
  Radio,
  Flame,
  BrainCircuit,
  Sparkles,
  Scissors,
  Film,
  Upload,
  AlertCircle,
  Clock,
  Trash2,
  CheckCircle2,
} from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Separator } from "@/components/ui/separator"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"

export default function LiveFeedPage({ liveEvents = [], onClearEvents }) {
  const [autoScroll, setAutoScroll] = useState(true)
  const feedEndRef = useRef(null)

  useEffect(() => {
    if (autoScroll) {
      feedEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }
  }, [liveEvents, autoScroll])

  const getEventMeta = (type) => {
    switch (type) {
      case "chat_spike":
      case "highlight_detected":
        return {
          icon: Flame,
          label: "Spike Detected",
          variant: "warning",
        }
      case "gemini_verdict":
        return {
          icon: Sparkles,
          label: "Gemini Verdict",
          variant: "purple",
        }
      case "clip_extracted":
      case "clip_extracting":
        return {
          icon: Scissors,
          label: "Clip Extracted",
          variant: "success",
        }
      case "short_processed":
        return {
          icon: Film,
          label: "Short Rendered",
          variant: "purple",
        }
      case "upload_complete":
        return {
          icon: Upload,
          label: "YouTube Uploaded",
          variant: "success",
        }
      case "error":
      case "upload_failed":
        return {
          icon: AlertCircle,
          label: "Error",
          variant: "destructive",
        }
      default:
        return {
          icon: Radio,
          label: "Live Log",
          variant: "secondary",
        }
    }
  }

  return (
    <div className="space-y-6">
      {/* Header controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center space-x-2">
            <Radio className="w-5 h-5 text-red-500 animate-pulse" />
            <span>Real-time Stream Event Feed</span>
            <Badge variant="outline" className="text-xs">
              {liveEvents.length} events
            </Badge>
          </h2>
          <p className="text-xs text-slate-400">
            Real-time feed of chat spikes, AI evaluations, clip cuts, and video uploads.
          </p>
        </div>

        <div className="flex items-center space-x-4">
          {/* Auto-scroll toggle */}
          <div className="flex items-center space-x-2 text-xs text-slate-300">
            <span>Auto-scroll</span>
            <Switch checked={autoScroll} onCheckedChange={setAutoScroll} />
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={onClearEvents}
            className="h-8 text-xs border-[#1f293d] hover:bg-[#1a2233]"
          >
            <Trash2 className="w-3.5 h-3.5 mr-1.5" />
            <span>Clear Feed</span>
          </Button>
        </div>
      </div>

      {/* Main Events Feed Card */}
      <Card className="border-[#1f293d] bg-[#121826]/80 overflow-hidden">
        <ScrollArea className="h-[600px] p-4">
          {liveEvents.length === 0 ? (
            <div className="py-24 text-center text-slate-500 text-xs">
              No live events received yet. Start stream monitoring or import a video to see real-time updates.
            </div>
          ) : (
            <div className="space-y-3">
              {liveEvents.map((evt, idx) => {
                const meta = getEventMeta(evt.type)
                const Icon = meta.icon
                const timeStr = evt.timestamp
                  ? new Date(evt.timestamp).toLocaleTimeString()
                  : new Date().toLocaleTimeString()
                const fullDate = evt.timestamp ? new Date(evt.timestamp).toLocaleString() : ""

                return (
                  <div
                    key={evt.id || idx}
                    className="p-3 rounded-lg border border-[#1f293d] bg-[#0d131f] space-y-1.5 text-xs transition-colors hover:border-slate-600"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <Badge variant={meta.variant} className="text-[10px] flex items-center space-x-1">
                          <Icon className="w-3 h-3 mr-1" />
                          <span>{meta.label}</span>
                        </Badge>
                        {evt.channel && (
                          <span className="font-semibold text-slate-200">
                            {evt.channel}
                          </span>
                        )}
                      </div>

                      <TooltipProvider delayDuration={150}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="text-[11px] font-mono text-slate-400 flex items-center space-x-1 cursor-default">
                              <Clock className="w-3 h-3 mr-0.5" />
                              <span>{timeStr}</span>
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>{fullDate}</p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>

                    <p className="text-slate-300 leading-relaxed pl-1">
                      {evt.message || evt.text || evt.title || JSON.stringify(evt.data || "")}
                    </p>
                  </div>
                )
              })}
              <div ref={feedEndRef} />
            </div>
          )}
        </ScrollArea>
      </Card>
    </div>
  )
}

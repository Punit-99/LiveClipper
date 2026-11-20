import React from "react"
import { Layers, X, Loader2, CheckCircle2, AlertCircle } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cancelProcessing, cancelUpload } from "@/api"
import { toast } from "sonner"

export function QueuePopover({ queueStatus, open, onOpenChange }) {
  const processing = queueStatus?.processing || { active_count: 0, waiting_count: 0, items: [] }
  const upload = queueStatus?.upload || { active_count: 0, waiting_count: 0, items: [] }

  const totalActive = (processing.active_count || 0) + (upload.active_count || 0)
  const totalWaiting = (processing.waiting_count || 0) + (upload.waiting_count || 0)
  const totalTasks = totalActive + totalWaiting

  const handleCancel = async (clipId, type) => {
    try {
      if (type === "processing") {
        await cancelProcessing(clipId)
        toast.info("Shorts processing cancelled.")
      } else {
        await cancelUpload(clipId)
        toast.info("Upload task cancelled.")
      }
    } catch (err) {
      toast.error(err.message || "Failed to cancel task.")
    }
  }

  const renderTaskItem = (item, type) => {
    const isRunning = item.status === "processing" || item.status === "uploading"
    const isQueued = item.status === "queued"
    const pct = item.percentage || 0

    return (
      <div
        key={`${type}-${item.clip_id}`}
        className="p-3 rounded-lg border border-[#1f293d] bg-[#0d131f] space-y-2 text-xs"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2 truncate">
            {isRunning ? (
              <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin shrink-0" />
            ) : (
              <Badge variant="secondary" className="text-[10px] px-1 py-0 shrink-0">
                #{item.position || 1}
              </Badge>
            )}
            <span className="font-medium text-slate-200 truncate">
              {item.title || item.clip_id}
            </span>
          </div>

          <Button
            variant="ghost"
            size="icon"
            onClick={() => handleCancel(item.clip_id, type)}
            className="h-5 w-5 text-slate-400 hover:text-red-400 shrink-0"
          >
            <X className="w-3 h-3" />
          </Button>
        </div>

        {isRunning && (
          <div className="space-y-1">
            <div className="flex justify-between text-[11px] text-slate-400">
              <span>{item.stage || (type === "upload" ? "Uploading" : "Rendering")}</span>
              <span>{pct}%</span>
            </div>
            <Progress value={pct} className="h-1.5" />
          </div>
        )}

        {isQueued && (
          <span className="text-[11px] text-amber-400/90 font-medium">
            Queued (Waiting for available slot...)
          </span>
        )}
      </div>
    )
  }

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-80 sm:w-96 p-4 border-[#1f293d] bg-[#0f1422] shadow-2xl space-y-4"
      >
        <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
          <div className="flex items-center space-x-2">
            <Layers className="w-4 h-4 text-blue-400" />
            <h4 className="font-semibold text-sm text-white">Background Operations</h4>
          </div>
          <Badge variant="outline" className="text-xs">
            {totalTasks} {totalTasks === 1 ? "task" : "tasks"}
          </Badge>
        </div>

        <ScrollArea className="max-h-[360px] pr-2 space-y-3">
          {/* Processing Queue */}
          {(processing.items || []).length > 0 && (
            <div className="space-y-2 mb-3">
              <div className="flex items-center justify-between text-xs font-semibold text-slate-400">
                <span>Shorts Processing</span>
                <span>{processing.items.length} active/queued</span>
              </div>
              <div className="space-y-2">
                {(processing.items || []).map((item) => renderTaskItem(item, "processing"))}
              </div>
            </div>
          )}

          {/* Upload Queue */}
          {(upload.items || []).length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold text-slate-400">
                <span>YouTube Uploads</span>
                <span>{upload.items.length} active/queued</span>
              </div>
              <div className="space-y-2">
                {(upload.items || []).map((item) => renderTaskItem(item, "upload"))}
              </div>
            </div>
          )}

          {totalTasks === 0 && (
            <div className="py-8 text-center text-slate-500 text-xs">
              No tasks currently processing or queued.
            </div>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}

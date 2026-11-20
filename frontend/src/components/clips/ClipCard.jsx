import React, { useState } from "react"
import {
  Play,
  MoreVertical,
  Film,
  Trash2,
  CheckCircle2,
  RotateCw,
  Loader2,
  Clock,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { formatRecordedDate } from "@/api"
import { toast } from "sonner"

export function ClipCard({
  clip,
  isSelected,
  onToggleSelect,
  onPreview,
  onProcess,
  onReconvert,
  onDelete,
  processingQueue,
}) {
  const [showReconvertDialog, setShowReconvertDialog] = useState(false)

  const clipId = (clip?.clip_id && clip.clip_id !== "undefined")
    ? clip.clip_id
    : (clip?.id && clip.id !== "undefined")
    ? clip.id
    : ""
  const isImported = Boolean(
    clip?.is_imported || clip?.source_type === "imported" || clipId?.startsWith("imp_")
  )
  const score = Number(clip?.score ?? clip?.highlight_score ?? 0)
  const scoreVariant = score >= 80 ? "success" : score >= 60 ? "warning" : "destructive"

  const activeTask = (processingQueue?.active || []).find((it) => it.clip_id === clipId)
  const waitingTask = (processingQueue?.waiting || []).find((it) => it.clip_id === clipId)

  const isProcessing = Boolean(activeTask)
  const isQueued = Boolean(waitingTask)
  const isProcessed = Boolean(clip?.has_short || clip?.short_state === "PROCESSED")

  const thumbnailSrc = clipId
    ? isImported
      ? `/api/import/clips/${clipId}/thumbnail`
      : `/api/clips/${clipId}/thumbnail`
    : null

  const dateStr = formatRecordedDate(clip?.created_at || clip?.timestamp || clip?.recorded_at_iso)

  return (
    <>
      <Card className="overflow-hidden border-[#1f293d] bg-[#121826]/90 hover:border-slate-600 transition-all group flex flex-col justify-between">
        <div>
          {/* Thumbnail banner with play trigger */}
          <div
            className="relative aspect-video bg-black/60 cursor-pointer overflow-hidden flex items-center justify-center"
            onClick={() => onPreview(clip)}
          >
            {thumbnailSrc ? (
              <img
                src={thumbnailSrc}
                alt={clip?.title || clip?.suggested_title}
                className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                onError={(e) => {
                  e.currentTarget.style.display = "none"
                }}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-slate-500">
                <Film className="w-8 h-8 opacity-40" />
              </div>
            )}
            <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/30" />

            {/* Selection Checkbox & Badges */}
            <div
              className="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center space-x-2">
                <Checkbox
                  checked={isSelected}
                  onCheckedChange={() => onToggleSelect(clipId)}
                  className="bg-black/60 border-slate-500"
                />
                <Badge variant={isImported ? "purple" : "secondary"} className="text-[10px] shadow">
                  {isImported ? "Imported" : "Live"}
                </Badge>
              </div>

              <div className="flex items-center space-x-1.5">
                {isProcessed && (
                  <Badge variant="success" className="text-[10px] shadow">
                    Short ✓
                  </Badge>
                )}
                <Badge variant={scoreVariant} className="text-[10px] font-bold shadow">
                  {score}/100
                </Badge>
              </div>
            </div>

            {/* Play Button Overlay */}
            <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <div className="w-10 h-10 rounded-full bg-blue-600/90 text-white flex items-center justify-center shadow-lg">
                <Play className="w-5 h-5 ml-0.5 fill-current" />
              </div>
            </div>

            {/* Duration badge */}
            <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 text-[11px] font-mono text-slate-200">
              {Math.round(clip?.duration || 0)}s
            </div>
          </div>

          {/* Content details */}
          <CardContent className="p-4 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <h4
                className="text-sm font-semibold text-white line-clamp-1 hover:text-blue-400 cursor-pointer"
                onClick={() => onPreview(clip)}
              >
                {clip?.title || clip?.suggested_title || clipId}
              </h4>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-slate-400 hover:text-white -mr-1">
                    <MoreVertical className="w-3.5 h-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-44">
                  <DropdownMenuItem onClick={() => onPreview(clip)}>
                    <Play className="w-3.5 h-3.5 mr-2" />
                    <span>Preview</span>
                  </DropdownMenuItem>
                  {isProcessed ? (
                    <DropdownMenuItem onClick={() => setShowReconvertDialog(true)}>
                      <RotateCw className="w-3.5 h-3.5 mr-2 text-blue-400" />
                      <span>Re-convert to Short</span>
                    </DropdownMenuItem>
                  ) : (
                    <DropdownMenuItem onClick={() => onProcess(clipId)}>
                      <Film className="w-3.5 h-3.5 mr-2 text-blue-400" />
                      <span>Process to Short</span>
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => onDelete(clipId)} className="text-red-400 hover:text-red-300">
                    <Trash2 className="w-3.5 h-3.5 mr-2" />
                    <span>Delete</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            <div className="flex items-center justify-between text-xs text-slate-400">
              <span className="truncate max-w-[150px] font-medium text-slate-300">
                {clip?.channel || clip?.streamer_name || "Unknown"}
              </span>
              {dateStr && <span>{dateStr}</span>}
            </div>

            {clip?.analysis_reason && (
              <p className="text-[11px] text-slate-400 line-clamp-2">
                {clip.analysis_reason}
              </p>
            )}
          </CardContent>
        </div>

        {/* Card bottom actions */}
        <div className="p-3 pt-0 border-t border-[#1f293d]/60 mt-2 flex items-center justify-between min-h-[44px]">
          {isProcessing ? (
            <Button
              variant="outline"
              size="sm"
              disabled
              className="w-full text-xs flex items-center justify-center space-x-1.5 border-[#1f293d] bg-blue-500/10 text-blue-400 cursor-not-allowed"
            >
              <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
              <span>Processing... {activeTask?.percentage || 0}%</span>
            </Button>
          ) : isQueued ? (
            <Button
              variant="outline"
              size="sm"
              disabled
              className="w-full text-xs flex items-center justify-center space-x-1.5 border-[#1f293d] bg-amber-500/10 text-amber-400 cursor-not-allowed"
            >
              <Clock className="w-3.5 h-3.5 mr-1.5" />
              <span>Queued (pos {waitingTask?.position || 1})</span>
            </Button>
          ) : isProcessed ? (
            <div className="w-full flex items-center justify-between px-1">
              <Badge variant="success" className="text-[11px] py-1 px-2 flex items-center space-x-1">
                <CheckCircle2 className="w-3 h-3 mr-1" />
                <span>Processed ✓</span>
              </Badge>
              <button
                type="button"
                onClick={() => setShowReconvertDialog(true)}
                className="text-xs text-slate-400 hover:text-blue-400 underline decoration-slate-600 hover:decoration-blue-400 transition-colors flex items-center space-x-1 cursor-pointer"
              >
                <RotateCw className="w-3 h-3 mr-1" />
                <span>Re-convert</span>
              </button>
            </div>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (isProcessing) {
                  toast.info("This clip is already being processed.")
                  return
                }
                if (isQueued) {
                  toast.info(`This clip is already in queue (position ${waitingTask?.position || 1}).`)
                  return
                }
                onProcess(clipId)
              }}
              className="w-full text-xs flex items-center justify-center space-x-1.5 border-[#1f293d] hover:bg-blue-600 hover:text-white hover:border-transparent transition-all"
            >
              <Film className="w-3.5 h-3.5" />
              <span>Process to Short</span>
            </Button>
          )}
        </div>
      </Card>

      {/* Re-convert Confirmation Dialog */}
      <AlertDialog open={showReconvertDialog} onOpenChange={setShowReconvertDialog}>
        <AlertDialogContent className="bg-[#121826] border-[#1f293d]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white flex items-center space-x-2">
              <RotateCw className="w-5 h-5 text-blue-400" />
              <span>Re-process this clip?</span>
            </AlertDialogTitle>
            <AlertDialogDescription className="text-slate-400">
              The existing Short will be replaced with a new version.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-[#1f293d] bg-transparent text-slate-300 hover:bg-slate-800">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setShowReconvertDialog(false)
                if (onReconvert) onReconvert(clipId)
              }}
              className="bg-blue-600 hover:bg-blue-700 text-white"
            >
              Re-convert
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

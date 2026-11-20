import React, { useState } from "react"
import { Play, Film, Trash2, RotateCw, Loader2, Clock, CheckCircle2 } from "lucide-react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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

export function ClipTable({
  clips,
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
  onPreview,
  onProcess,
  onReconvert,
  onDelete,
  processingQueue,
}) {
  const [reconvertClipId, setReconvertClipId] = useState(null)
  const isAllSelected = clips.length > 0 && selectedIds.length === clips.length

  return (
    <>
      <div className="rounded-xl border border-[#1f293d] bg-[#121826]/80 overflow-hidden">
        <Table>
          <TableHeader className="bg-[#0f1422]">
            <TableRow className="border-[#1f293d]">
              <TableHead className="w-12">
                <Checkbox
                  checked={isAllSelected}
                  onCheckedChange={onToggleSelectAll}
                  className="border-slate-500"
                />
              </TableHead>
              <TableHead className="w-20">Type</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Channel</TableHead>
              <TableHead>Score</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Recorded</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(clips || []).map((clip) => {
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
              const isSelected = selectedIds.includes(clipId)

              const activeTask = (processingQueue?.active || []).find((it) => it.clip_id === clipId)
              const waitingTask = (processingQueue?.waiting || []).find((it) => it.clip_id === clipId)
              const isProcessing = Boolean(activeTask)
              const isQueued = Boolean(waitingTask)
              const isProcessed = Boolean(clip?.has_short || clip?.short_state === "PROCESSED")

              return (
                <TableRow
                  key={clipId}
                  className="border-[#1f293d] hover:bg-[#1a2233]/40"
                >
                  <TableCell>
                    <Checkbox
                      checked={isSelected}
                      onCheckedChange={() => onToggleSelect(clipId)}
                      className="border-slate-500"
                    />
                  </TableCell>
                  <TableCell>
                    <Badge variant={isImported ? "purple" : "secondary"} className="text-[10px]">
                      {isImported ? "Imported" : "Live"}
                    </Badge>
                  </TableCell>
                  <TableCell
                    className="font-medium text-white max-w-xs truncate cursor-pointer hover:text-blue-400"
                    onClick={() => onPreview(clip)}
                  >
                    {clip?.title || clip?.suggested_title || clipId}
                  </TableCell>
                  <TableCell className="text-slate-300">
                    {clip?.channel || clip?.streamer_name || "Unknown"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={scoreVariant} className="text-[10px]">
                      {score}/100
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-slate-300">
                    {Math.round(clip?.duration || 0)}s
                  </TableCell>
                  <TableCell className="text-xs text-slate-400">
                    {formatRecordedDate(clip?.created_at || clip?.timestamp || clip?.recorded_at_iso)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end space-x-1.5">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => onPreview(clip)}
                        className="h-7 w-7 text-slate-400 hover:text-white"
                        title="Preview Clip"
                      >
                        <Play className="w-3.5 h-3.5" />
                      </Button>

                      {isProcessing ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled
                          className="h-7 px-2 text-[11px] text-blue-400 bg-blue-500/10 cursor-not-allowed"
                          title="Currently processing"
                        >
                          <Loader2 className="w-3 h-3 animate-spin mr-1" />
                          <span>{activeTask?.percentage || 0}%</span>
                        </Button>
                      ) : isQueued ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled
                          className="h-7 px-2 text-[11px] text-amber-400 bg-amber-500/10 cursor-not-allowed"
                          title={`Queued (pos ${waitingTask?.position || 1})`}
                        >
                          <Clock className="w-3 h-3 mr-1" />
                          <span>Pos {waitingTask?.position || 1}</span>
                        </Button>
                      ) : isProcessed ? (
                        <>
                          <Badge variant="success" className="text-[10px] py-0.5 px-1.5 mr-1">
                            Processed ✓
                          </Badge>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setReconvertClipId(clipId)}
                            className="h-7 w-7 text-slate-400 hover:text-blue-400"
                            title="Re-convert Short"
                          >
                            <RotateCw className="w-3.5 h-3.5" />
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon"
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
                          className="h-7 w-7 text-blue-400 hover:text-blue-300 hover:bg-blue-500/10"
                          title="Process to Short"
                        >
                          <Film className="w-3.5 h-3.5" />
                        </Button>
                      )}

                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => onDelete(clipId)}
                        className="h-7 w-7 text-red-400 hover:text-red-300 hover:bg-red-500/10"
                        title="Delete Clip"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      {/* Re-convert Confirmation Dialog */}
      <AlertDialog open={Boolean(reconvertClipId)} onOpenChange={(open) => !open && setReconvertClipId(null)}>
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
                const targetId = reconvertClipId
                setReconvertClipId(null)
                if (onReconvert && targetId) onReconvert(targetId)
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

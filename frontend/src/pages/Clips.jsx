import React, { useState } from "react"
import {
  Scissors,
  LayoutGrid,
  Table as TableIcon,
  Film,
  Trash2,
  RotateCw,
  Search,
  Filter,
  CheckCircle2,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { ClipCard } from "@/components/clips/ClipCard"
import { ClipTable } from "@/components/clips/ClipTable"
import { ClipPlayer } from "@/components/clips/ClipPlayer"
import { toast } from "sonner"

export default function ClipsPage({
  clips = [],
  onProcessSingle,
  onProcessAll,
  onRefresh,
  onDeleteClip,
  onDeleteAllClips,
}) {
  const [viewMode, setViewMode] = useState("grid") // "grid" | "table"
  const [searchQuery, setSearchQuery] = useState("")
  const [channelFilter, setChannelFilter] = useState("all")
  const [scoreFilter, setScoreFilter] = useState("all")
  const [typeFilter, setTypeFilter] = useState("all") // "all" | "live" | "imported"

  const [selectedIds, setSelectedIds] = useState([])
  const [previewClip, setPreviewClip] = useState(null)
  const [clipToDelete, setClipToDelete] = useState(null)
  const [showDeleteAllDialog, setShowDeleteAllDialog] = useState(false)

  // Channels list for filter dropdown
  const channels = Array.from(new Set((clips || []).map((c) => c.channel || c.streamer_name))).filter(Boolean)

  // Filter clips
  const filteredClips = (clips || []).filter((clip) => {
    const clipId = clip.clip_id || clip.id || ""
    const title = (clip.title || clip.suggested_title || clipId).toLowerCase()
    const channel = (clip.channel || clip.streamer_name || "").toLowerCase()
    const search = searchQuery.toLowerCase()

    if (search && !title.includes(search) && !channel.includes(search)) return false
    if (channelFilter !== "all" && (clip.channel || clip.streamer_name) !== channelFilter) return false

    const score = Number(clip.score ?? clip.highlight_score ?? 0)
    if (scoreFilter === "80" && score < 80) return false
    if (scoreFilter === "60" && score < 60) return false

    const isImported = Boolean(
      clip.is_imported || clip.source_type === "imported" || clipId?.startsWith("imp_")
    )
    if (typeFilter === "live" && isImported) return false
    if (typeFilter === "imported" && !isImported) return false

    return true
  })

  // Selection handlers
  const handleToggleSelect = (clipId) => {
    setSelectedIds((prev) =>
      prev.includes(clipId) ? prev.filter((id) => id !== clipId) : [...prev, clipId]
    )
  }

  const handleToggleSelectAll = () => {
    if (selectedIds.length === filteredClips.length) {
      setSelectedIds([])
    } else {
      setSelectedIds(filteredClips.map((c) => c.clip_id || c.id))
    }
  }

  // Process single clip
  const handleProcessSingle = async (processId) => {
    try {
      await onProcessSingle(processId)
      toast.success("Clip queued for Shorts processing!")
    } catch (err) {
      console.error("Failed to process clip:", err)
      toast.error(`Failed to process clip: ${err.message || "Unknown error"}`)
    }
  }

  // Batch process
  const handleBatchProcess = async () => {
    if (selectedIds.length === 0) return
    let successCount = 0
    let failCount = 0
    for (const id of selectedIds) {
      try {
        await onProcessSingle(id)
        successCount++
      } catch (err) {
        failCount++
        console.error("Failed processing clip", id, err)
      }
    }
    if (successCount > 0) {
      toast.success(`Queued ${successCount} clips for Shorts processing!`)
    }
    if (failCount > 0) {
      toast.error(`Failed to process ${failCount} clips.`)
    }
    setSelectedIds([])
  }

  // Batch delete
  const handleBatchDelete = async () => {
    if (selectedIds.length === 0) return
    let count = 0
    let failCount = 0
    for (const id of selectedIds) {
      try {
        await onDeleteClip(id)
        count++
      } catch (err) {
        failCount++
        console.error("Failed deleting clip", id, err)
      }
    }
    if (count > 0) {
      toast.success(`Deleted ${count} clips.`)
    }
    if (failCount > 0) {
      toast.error(`Failed to delete ${failCount} clips.`)
    }
    setSelectedIds([])
  }

  const handleDeleteSingle = async () => {
    if (!clipToDelete) return
    const cid = clipToDelete.clip_id || clipToDelete.id
    try {
      await onDeleteClip(cid)
      toast.success("Clip deleted successfully.")
    } catch (err) {
      console.error("Failed to delete clip:", err)
      toast.error(`Failed to delete clip: ${err.message || "Unknown error"}`)
    } finally {
      setClipToDelete(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Top Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center space-x-2">
            <span>Video Highlight Clips</span>
            <Badge variant="outline" className="text-xs">
              {filteredClips.length} {filteredClips.length === 1 ? "clip" : "clips"}
            </Badge>
          </h2>
          <p className="text-xs text-slate-400">
            Raw highlight clips extracted from live broadcast events and imported YouTube videos.
          </p>
        </div>

        {/* View Toggle & Refresh */}
        <div className="flex items-center space-x-2">
          {/* Dual view switcher */}
          <div className="flex items-center bg-[#0d131f] border border-[#1f293d] rounded-lg p-0.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setViewMode("grid")}
              className={`h-7 px-2.5 text-xs ${
                viewMode === "grid" ? "bg-primary text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setViewMode("table")}
              className={`h-7 px-2.5 text-xs ${
                viewMode === "table" ? "bg-primary text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              <TableIcon className="w-3.5 h-3.5" />
            </Button>
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            className="h-8 text-xs border-[#1f293d] hover:bg-[#1a2233]"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* Filter Bar */}
      <Card className="border-[#1f293d] bg-[#121826]/80 p-3.5">
        <div className="flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
          {/* Search text input */}
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <Input
              placeholder="Search by title or channel..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 h-8 text-xs border-[#1f293d] bg-[#0d131f]"
            />
          </div>

          {/* Filter dropdowns */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Channel filter */}
            <Select value={channelFilter} onValueChange={setChannelFilter}>
              <SelectTrigger className="h-8 w-36 text-xs border-[#1f293d] bg-[#0d131f]">
                <SelectValue placeholder="All Channels" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Channels</SelectItem>
                {channels.map((ch) => (
                  <SelectItem key={ch} value={ch}>
                    {ch}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Score filter */}
            <Select value={scoreFilter} onValueChange={setScoreFilter}>
              <SelectTrigger className="h-8 w-32 text-xs border-[#1f293d] bg-[#0d131f]">
                <SelectValue placeholder="All Scores" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Scores</SelectItem>
                <SelectItem value="80">Score ≥ 80</SelectItem>
                <SelectItem value="60">Score ≥ 60</SelectItem>
              </SelectContent>
            </Select>

            {/* Source Type Filter */}
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="h-8 w-32 text-xs border-[#1f293d] bg-[#0d131f]">
                <SelectValue placeholder="All Sources" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Sources</SelectItem>
                <SelectItem value="live">Live Stream</SelectItem>
                <SelectItem value="imported">Imported URL</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Batch action banner if items selected */}
        {selectedIds.length > 0 && (
          <div className="mt-3 pt-3 border-t border-[#1f293d] flex items-center justify-between text-xs">
            <span className="text-slate-300 font-medium">
              {selectedIds.length} {selectedIds.length === 1 ? "clip" : "clips"} selected
            </span>
            <div className="flex items-center space-x-2">
              <Button
                size="sm"
                onClick={handleBatchProcess}
                className="h-7 text-xs bg-blue-600 hover:bg-blue-500 text-white font-medium"
              >
                <Film className="w-3 h-3 mr-1.5" />
                <span>Process Selected ({selectedIds.length})</span>
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleBatchDelete}
                className="h-7 text-xs"
              >
                <Trash2 className="w-3 h-3 mr-1.5" />
                <span>Delete Selected</span>
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* Main View: Grid vs Table */}
      {filteredClips.length === 0 ? (
        <Card className="border-[#1f293d] bg-[#121826]/60 p-12 text-center space-y-3">
          <div className="w-12 h-12 rounded-xl bg-blue-500/10 text-blue-400 mx-auto flex items-center justify-center">
            <Scissors className="w-6 h-6" />
          </div>
          <h4 className="font-semibold text-sm text-white">No Clips Found</h4>
          <p className="text-xs text-slate-400 max-w-sm mx-auto">
            {searchQuery || channelFilter !== "all" || scoreFilter !== "all"
              ? "No clips match the selected search or filter criteria."
              : "No clips have been extracted yet. Start stream monitoring or import a YouTube video to generate highlight clips."}
          </p>
        </Card>
      ) : viewMode === "grid" ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredClips.map((clip) => {
            const cid = clip.clip_id || clip.id
            return (
              <ClipCard
                key={cid}
                clip={clip}
                isSelected={selectedIds.includes(cid)}
                onToggleSelect={handleToggleSelect}
                onPreview={(c) => setPreviewClip(c)}
                onProcess={handleProcessSingle}
                onDelete={() => setClipToDelete(clip)}
              />
            )
          })}
        </div>
      ) : (
        <ClipTable
          clips={filteredClips}
          selectedIds={selectedIds}
          onToggleSelect={handleToggleSelect}
          onToggleSelectAll={handleToggleSelectAll}
          onPreview={(c) => setPreviewClip(c)}
          onProcess={handleProcessSingle}
          onDelete={(deleteId) => {
            const target = clips.find((c) => (c.clip_id || c.id) === deleteId)
            if (target) setClipToDelete(target)
          }}
        />
      )}

      {/* Clip Preview Modal */}
      <ClipPlayer
        clip={previewClip}
        open={Boolean(previewClip)}
        onOpenChange={(open) => !open && setPreviewClip(null)}
      />

      {/* Delete Confirmation Alert Dialog */}
      <AlertDialog
        open={Boolean(clipToDelete)}
        onOpenChange={(open) => !open && setClipToDelete(null)}
      >
        <AlertDialogContent className="bg-[#0f1422] border-[#1f293d]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Delete Highlight Clip?</AlertDialogTitle>
            <AlertDialogDescription className="text-slate-400">
              Are you sure you want to delete this clip? The MP4 file and its JSON sidecar will be permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-[#1f293d] text-slate-300">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteSingle}
              className="bg-red-600 hover:bg-red-500 text-white"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

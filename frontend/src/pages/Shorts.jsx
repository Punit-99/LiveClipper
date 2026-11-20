import React, { useState } from "react"
import {
  Film,
  LayoutGrid,
  Table as TableIcon,
  Upload,
  ShieldCheck,
  RotateCw,
  Trash2,
  Search,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ExternalLink,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { ShortCard } from "@/components/shorts/ShortCard"
import { ShortTable } from "@/components/shorts/ShortTable"
import { ShortPlayer } from "@/components/shorts/ShortPlayer"
import { verifyShortUpload, reuploadSingleShort, verifyAllUploads } from "@/api"
import { toast } from "sonner"

export default function ShortsPage({
  shorts = [],
  quota,
  onUploadSingle,
  onUploadAll,
  onRefresh,
  onDeleteShort,
  onDeleteAllShorts,
  uploadTasks = {},
}) {
  const [viewMode, setViewMode] = useState("grid") // "grid" | "table"
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState("all") // "all" | "uploaded" | "ready" | "failed"
  const [channelFilter, setChannelFilter] = useState("all")

  const [selectedIds, setSelectedIds] = useState([])
  const [previewShort, setPreviewShort] = useState(null)
  const [shortToDelete, setShortToDelete] = useState(null)

  // Verification state tracking map: { [clipId]: { loading: bool, result: object } }
  const [verifyMap, setVerifyMap] = useState({})
  const [isVerifyingAll, setIsVerifyingAll] = useState(false)

  // Filter channels
  const channels = Array.from(new Set(shorts.map((s) => s.channel))).filter(Boolean)

  const filteredShorts = shorts.filter((short) => {
    const title = (short.title || short.clip_id || "").toLowerCase()
    const channel = (short.channel || "").toLowerCase()
    const search = searchQuery.toLowerCase()

    if (search && !title.includes(search) && !channel.includes(search)) return false
    if (channelFilter !== "all" && short.channel !== channelFilter) return false

    const isUploaded = short.state === "UPLOADED" || short.youtube_url
    const isFailed = short.state === "FAILED"

    if (statusFilter === "uploaded" && !isUploaded) return false
    if (statusFilter === "ready" && (isUploaded || isFailed)) return false
    if (statusFilter === "failed" && !isFailed) return false

    return true
  })

  // Selection handlers
  const handleToggleSelect = (clipId) => {
    setSelectedIds((prev) =>
      prev.includes(clipId) ? prev.filter((id) => id !== clipId) : [...prev, clipId]
    )
  }

  const handleToggleSelectAll = () => {
    if (selectedIds.length === filteredShorts.length) {
      setSelectedIds([])
    } else {
      setSelectedIds(filteredShorts.map((s) => s.clip_id))
    }
  }

  // Verification handler
  const handleVerify = async (clipId) => {
    setVerifyMap((prev) => ({ ...prev, [clipId]: { loading: true } }))
    try {
      const res = await verifyShortUpload(clipId)
      setVerifyMap((prev) => ({ ...prev, [clipId]: { loading: false, result: res } }))
      if (res.verified) {
        toast.success(`Verified on YouTube! (${res.status})`)
      } else {
        toast.error(`Verification failed: ${res.reason || "Video not found on YouTube"}`)
      }
    } catch (err) {
      setVerifyMap((prev) => ({
        ...prev,
        [clipId]: { loading: false, result: { verified: false, reason: err.message } },
      }))
      toast.error(err.message || "Failed to verify upload.")
    }
  }

  // Re-upload handler
  const handleReupload = async (clipId) => {
    try {
      await reuploadSingleShort(clipId)
      toast.success("Fresh YouTube upload queued!")
      if (onRefresh) onRefresh()
    } catch (err) {
      toast.error(err.message || "Failed to re-upload.")
    }
  }

  // Batch Verify All
  const handleVerifyAll = async () => {
    setIsVerifyingAll(true)
    try {
      const res = await verifyAllUploads()
      toast.success(`Verification complete: ${res.verified || 0} verified, ${res.missing || 0} missing.`)
      if (onRefresh) onRefresh()
    } catch (err) {
      toast.error(err.message || "Failed to batch verify.")
    } finally {
      setIsVerifyingAll(false)
    }
  }

  // Batch Upload
  const handleBatchUpload = async () => {
    if (selectedIds.length === 0) return
    let count = 0
    for (const id of selectedIds) {
      try {
        await onUploadSingle(id)
        count++
      } catch (err) {
        console.error("Batch upload failed for", id, err)
      }
    }
    toast.success(`Queued ${count} shorts for upload!`)
    setSelectedIds([])
  }

  const handleDeleteSingle = async () => {
    if (!shortToDelete) return
    try {
      await onDeleteShort(shortToDelete.clip_id)
      toast.success("Short deleted successfully.")
    } catch (err) {
      toast.error(err.message || "Failed to delete short.")
    } finally {
      setShortToDelete(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center space-x-2">
            <span>Processed Shorts</span>
            <Badge variant="outline" className="text-xs">
              {filteredShorts.length} {filteredShorts.length === 1 ? "video" : "videos"}
            </Badge>
          </h2>
          <p className="text-xs text-slate-400">
            Finished 9:16 vertical videos reframed with subtitles ready for YouTube upload.
          </p>
        </div>

        {/* View mode & actions */}
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
            onClick={handleVerifyAll}
            disabled={isVerifyingAll}
            className="h-8 text-xs border-[#1f293d] hover:bg-[#1a2233] space-x-1.5"
          >
            <ShieldCheck className={`w-3.5 h-3.5 text-emerald-400 ${isVerifyingAll ? "animate-spin" : ""}`} />
            <span>Verify All</span>
          </Button>

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
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <Input
              placeholder="Search by title or channel..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 h-8 text-xs border-[#1f293d] bg-[#0d131f]"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-8 w-36 text-xs border-[#1f293d] bg-[#0d131f]">
                <SelectValue placeholder="All Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Status</SelectItem>
                <SelectItem value="uploaded">Uploaded</SelectItem>
                <SelectItem value="ready">Ready to Upload</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
              </SelectContent>
            </Select>

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
          </div>
        </div>

        {/* Batch action banner */}
        {selectedIds.length > 0 && (
          <div className="mt-3 pt-3 border-t border-[#1f293d] flex items-center justify-between text-xs">
            <span className="text-slate-300 font-medium">
              {selectedIds.length} {selectedIds.length === 1 ? "short" : "shorts"} selected
            </span>
            <div className="flex items-center space-x-2">
              <Button
                size="sm"
                onClick={handleBatchUpload}
                className="h-7 text-xs bg-red-600 hover:bg-red-500 text-white font-medium"
              >
                <Upload className="w-3 h-3 mr-1.5" />
                <span>Upload Selected ({selectedIds.length})</span>
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* Main View */}
      {filteredShorts.length === 0 ? (
        <Card className="border-[#1f293d] bg-[#121826]/60 p-12 text-center space-y-3">
          <div className="w-12 h-12 rounded-xl bg-purple-500/10 text-purple-400 mx-auto flex items-center justify-center">
            <Film className="w-6 h-6" />
          </div>
          <h4 className="font-semibold text-sm text-white">No Shorts Available</h4>
          <p className="text-xs text-slate-400 max-w-sm mx-auto">
            {searchQuery || statusFilter !== "all" || channelFilter !== "all"
              ? "No shorts match your current search or filter."
              : "No shorts have been rendered yet. Go to the Clips tab and click 'Process to Short' on any clip."}
          </p>
        </Card>
      ) : viewMode === "grid" ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {filteredShorts.map((short) => (
            <ShortCard
              key={short.clip_id}
              shortItem={short}
              isSelected={selectedIds.includes(short.clip_id)}
              onToggleSelect={handleToggleSelect}
              onPreview={(s) => setPreviewShort(s)}
              onUpload={(cid) => {
                onUploadSingle(cid)
                toast.success("Short queued for YouTube upload!")
              }}
              onVerify={handleVerify}
              onReupload={handleReupload}
              onDelete={(cid) => setShortToDelete(short)}
              uploadState={uploadTasks[short.clip_id]}
              verifyState={verifyMap[short.clip_id]}
            />
          ))}
        </div>
      ) : (
        <ShortTable
          shorts={filteredShorts}
          selectedIds={selectedIds}
          onToggleSelect={handleToggleSelect}
          onToggleSelectAll={handleToggleSelectAll}
          onPreview={(s) => setPreviewShort(s)}
          onUpload={(cid) => {
            onUploadSingle(cid)
            toast.success("Short queued for YouTube upload!")
          }}
          onVerify={handleVerify}
          onReupload={handleReupload}
          onDelete={(cid) => {
            const target = shorts.find((s) => s.clip_id === cid)
            if (target) setShortToDelete(target)
          }}
        />
      )}

      {/* Preview Modal */}
      <ShortPlayer
        shortItem={previewShort}
        open={Boolean(previewShort)}
        onOpenChange={(open) => !open && setPreviewShort(null)}
      />

      {/* Delete Confirmation Alert Dialog */}
      <AlertDialog
        open={Boolean(shortToDelete)}
        onOpenChange={(open) => !open && setShortToDelete(null)}
      >
        <AlertDialogContent className="bg-[#0f1422] border-[#1f293d]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Delete Short Video?</AlertDialogTitle>
            <AlertDialogDescription className="text-slate-400">
              Are you sure you want to delete this processed Short? The vertical video file and its metadata manifest record will be permanently deleted.
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

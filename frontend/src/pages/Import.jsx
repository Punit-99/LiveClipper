import React, { useState, useEffect } from "react"
import {
  Download,
  Sparkles,
  Film,
  Trash2,
  Play,
  RotateCw,
  ExternalLink,
  Layers,
  CheckCircle2,
  Clock,
  HardDrive,
  Video,
  AlertTriangle,
  Scissors,
} from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { ImportForm } from "@/components/import/ImportForm"
import { AnalysisProgress } from "@/components/import/AnalysisProgress"
import { ClipPlayer } from "@/components/clips/ClipPlayer"
import {
  startImportAnalysis,
  downloadImportVideo,
  fetchImportLibrary,
  deleteImportLibraryVideo,
  startLibraryAnalysis,
  cancelImportJob,
  clearAllImportData,
  fetchImportJobs,
  fetchImportedClips,
  deleteImportedClip,
  processImportedClip,
  formatRecordedDate,
} from "@/api"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

export default function ImportPage({ onNavigate }) {
  const [jobs, setJobs] = useState([])
  const [activeJob, setActiveJob] = useState(null)
  const [library, setLibrary] = useState([])
  const [importedClips, setImportedClips] = useState([])
  const [loading, setLoading] = useState(true)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [isCancelling, setIsCancelling] = useState(false)

  // Preview & Delete states
  const [previewClip, setPreviewClip] = useState(null)
  const [clipToDelete, setClipToDelete] = useState(null)
  const [libraryVideoToDelete, setLibraryVideoToDelete] = useState(null)
  const [clearModalOpen, setClearModalOpen] = useState(false)
  const [isClearing, setIsClearing] = useState(false)

  const loadData = async () => {
    try {
      const [jobsData, clipsData, libData] = await Promise.all([
        fetchImportJobs().catch(() => []),
        fetchImportedClips().catch(() => []),
        fetchImportLibrary().catch(() => []),
      ])
      setJobs(jobsData)
      setImportedClips(clipsData)
      setLibrary(libData)

      // Find active job if any
      const running = jobsData.find(
        (j) =>
          j.status === "downloading" ||
          j.status === "analyzing" ||
          j.status === "extracting" ||
          j.status === "pending"
      )
      if (running) {
        setActiveJob(running)
        setIsAnalyzing(true)
      } else {
        setIsAnalyzing(false)
        if (jobsData.length > 0 && (!activeJob || activeJob.status === "completed" || activeJob.status === "failed")) {
          setActiveJob(jobsData[0])
        }
      }
    } catch (err) {
      console.error("Error loading import data", err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 4000)
    return () => clearInterval(interval)
  }, [])

  // Start Analysis (One-shot)
  const handleStartAnalysis = async (params) => {
    setIsAnalyzing(true)
    try {
      const job = await startImportAnalysis(params)
      setActiveJob(job)
      toast.success("Analysis started! Video is being processed.")
      await loadData()
    } catch (err) {
      setIsAnalyzing(false)
      toast.error(err.message || "Failed to start import analysis.")
    }
  }

  // Download Only to Library
  const handleDownloadOnly = async (params) => {
    try {
      await downloadImportVideo(params)
      toast.success("Video downloaded and saved to Library!")
      await loadData()
    } catch (err) {
      toast.error(err.message || "Failed to download video.")
    }
  }

  // Analyze a video from Library
  const handleAnalyzeLibraryVideo = async (videoId) => {
    setIsAnalyzing(true)
    try {
      const job = await startLibraryAnalysis(videoId, {
        num_clips: 5,
        clip_duration: 55,
      })
      setActiveJob(job)
      toast.success("Analysis started for library video!")
      await loadData()
    } catch (err) {
      setIsAnalyzing(false)
      toast.error(err.message || "Failed to start analysis.")
    }
  }

  // Cancel running job
  const handleCancelJob = async (jobId) => {
    setIsCancelling(true)
    try {
      await cancelImportJob(jobId)
      toast.info("Import job cancelled.")
      setIsAnalyzing(false)
      await loadData()
    } catch (err) {
      toast.error(`Failed to cancel: ${err.message || "Unknown error"}`)
    } finally {
      setIsCancelling(false)
    }
  }

  // Delete clip
  const confirmDeleteClip = async () => {
    if (!clipToDelete) return
    const cid = clipToDelete.clip_id || clipToDelete.id
    try {
      await deleteImportedClip(cid)
      setImportedClips((prev) => prev.filter((c) => (c.clip_id || c.id) !== cid))
      toast.success("Imported clip deleted.")
    } catch (err) {
      toast.error(`Failed to delete clip: ${err.message || "Unknown error"}`)
    } finally {
      setClipToDelete(null)
    }
  }

  // Delete video from library
  const confirmDeleteLibraryVideo = async () => {
    if (!libraryVideoToDelete) return
    const vid = libraryVideoToDelete.video_id
    try {
      await deleteImportLibraryVideo(vid)
      setLibrary((prev) => prev.filter((item) => item.video_id !== vid))
      toast.success("Video deleted from library.")
      await loadData()
    } catch (err) {
      toast.error(`Failed to delete library video: ${err.message || "Unknown error"}`)
    } finally {
      setLibraryVideoToDelete(null)
    }
  }

  // Process clip to short
  const handleProcessClip = async (clipId) => {
    try {
      await processImportedClip(clipId)
      toast.success("Clip queued for Shorts processing!")
    } catch (err) {
      toast.error(err.message || "Failed to queue clip.")
    }
  }

  // Clear all imports
  const handleClearAllImports = async () => {
    setIsClearing(true)
    try {
      const res = await clearAllImportData()
      toast.success(`Cleared all imports! Freed ${res.freed_mb || 0} MB of disk space.`)
      await loadData()
    } catch (err) {
      toast.error(err.message || "Failed to clear imports.")
    } finally {
      setIsClearing(false)
      setClearModalOpen(false)
    }
  }

  // Calculate total library disk size
  const totalLibrarySizeMb = library.reduce((acc, item) => acc + (item.file_size_mb || 0), 0)

  return (
    <div className="space-y-6">
      {/* Top Import Form (Controls locked when isAnalyzing === true) */}
      <ImportForm
        onStartAnalysis={handleStartAnalysis}
        onDownloadOnly={handleDownloadOnly}
        onVideoUploaded={() => loadData()}
        isAnalyzing={isAnalyzing}
      />

      {/* Active Progress Banner with Heartbeat and Stop/Cancel Button */}
      {activeJob && (
        <AnalysisProgress
          job={activeJob}
          onCancelJob={handleCancelJob}
          isCancelling={isCancelling}
        />
      )}

      {/* SECTION 1: Downloaded Videos Library */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Video className="w-4 h-4 text-blue-400" />
            <h3 className="font-semibold text-base text-white">Downloaded Videos Library</h3>
            <Badge variant="outline" className="text-xs">
              {library.length} {library.length === 1 ? "video" : "videos"}
            </Badge>
            {totalLibrarySizeMb > 0 && (
              <span className="text-xs text-slate-400 hidden sm:inline">
                ({totalLibrarySizeMb.toFixed(1)} MB total)
              </span>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={loadData}
            className="h-8 text-xs border-[#1f293d] hover:bg-[#1a2233]"
          >
            <RotateCw className="w-3.5 h-3.5 mr-1.5" />
            <span>Refresh</span>
          </Button>
        </div>

        {library.length === 0 ? (
          <Card className="border-[#1f293d] bg-[#121826]/60 p-8 text-center space-y-2">
            <Video className="w-8 h-8 text-slate-500 mx-auto" />
            <h4 className="font-medium text-xs text-slate-300">No Videos in Library</h4>
            <p className="text-[11px] text-slate-500 max-w-sm mx-auto">
              Download any YouTube URL or upload a video file above. Saved videos will appear here for easy analysis.
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {library.map((item) => {
              const isVideoAnalyzing =
                isAnalyzing && activeJob && activeJob.video_id === item.video_id

              return (
                <Card
                  key={item.video_id}
                  className="border-[#1f293d] bg-[#121826]/90 overflow-hidden flex flex-col justify-between hover:border-slate-600 transition-all group"
                >
                  <div>
                    {/* Thumbnail banner */}
                    <div className="relative aspect-video bg-black/60 overflow-hidden flex items-center justify-center">
                      <img
                        src={item.thumbnail_url}
                        alt={item.title}
                        className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                        onError={(e) => {
                          e.currentTarget.style.display = "none"
                        }}
                      />
                      <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/30" />

                      <div className="absolute top-2 left-2 flex items-center space-x-1.5">
                        <Badge
                          variant={item.source_type === "uploaded" ? "secondary" : "purple"}
                          className="text-[10px] uppercase font-bold"
                        >
                          {item.source_type}
                        </Badge>
                      </div>

                      <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 text-[11px] font-mono text-slate-200">
                        {Math.round(item.duration / 60)}m · {item.file_size_mb}MB
                      </div>
                    </div>

                    <CardContent className="p-4 space-y-1.5">
                      <h4 className="text-xs font-semibold text-white line-clamp-1">
                        {item.title}
                      </h4>
                      <p className="text-[11px] text-slate-400">
                        {item.channel} · {item.clips_count} {item.clips_count === 1 ? "clip" : "clips"} extracted
                      </p>
                    </CardContent>
                  </div>

                  {/* Actions */}
                  <div className="p-3 pt-0 border-t border-[#1f293d]/60 mt-1 flex items-center space-x-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={isAnalyzing}
                      onClick={() => handleAnalyzeLibraryVideo(item.video_id)}
                      className={cn(
                        "flex-1 text-xs border-[#1f293d] transition-all flex items-center justify-center space-x-1.5",
                        isVideoAnalyzing
                          ? "bg-blue-600/30 text-blue-300 border-blue-500/40"
                          : "hover:bg-blue-600 hover:text-white"
                      )}
                    >
                      <Sparkles className="w-3.5 h-3.5" />
                      <span>{isVideoAnalyzing ? "Analyzing..." : "Analyze"}</span>
                    </Button>

                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={isAnalyzing}
                      onClick={() => setLibraryVideoToDelete(item)}
                      className="h-8 w-8 text-slate-400 hover:text-red-400"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </Card>
              )
            })}
          </div>
        )}
      </div>

      {/* SECTION 2: Imported Highlights */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-4 h-4 text-purple-400" />
            <h3 className="font-semibold text-base text-white">Imported Highlights</h3>
            <Badge variant="outline" className="text-xs">
              {importedClips.length} {importedClips.length === 1 ? "clip" : "clips"}
            </Badge>
          </div>
        </div>

        {importedClips.length === 0 ? (
          <Card className="border-[#1f293d] bg-[#121826]/60 p-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-xl bg-purple-500/10 text-purple-400 mx-auto flex items-center justify-center">
              <Film className="w-6 h-6" />
            </div>
            <h4 className="font-semibold text-sm text-white">No Imported Clips Yet</h4>
            <p className="text-xs text-slate-400 max-w-sm mx-auto">
              Analyze a YouTube video or uploaded video above. Highlight moments will automatically be cut and displayed here.
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {(importedClips || []).map((clip) => {
              const cid = clip.clip_id || clip.id || ""
              const score = Number(clip.score ?? clip.highlight_score ?? 0)
              const scoreVariant = score >= 80 ? "success" : score >= 60 ? "warning" : "destructive"

              return (
                <Card
                  key={cid}
                  className="border-[#1f293d] bg-[#121826]/90 overflow-hidden flex flex-col justify-between hover:border-slate-600 transition-all group"
                >
                  <div>
                    {/* Thumbnail frame */}
                    <div
                      className="relative aspect-video bg-black/60 cursor-pointer overflow-hidden flex items-center justify-center"
                      onClick={() => setPreviewClip(clip)}
                    >
                      <img
                        src={`/api/import/clips/${cid}/thumbnail`}
                        alt={clip.suggested_title}
                        className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                        onError={(e) => {
                          e.currentTarget.style.display = "none"
                        }}
                      />
                      <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/30" />

                      <div className="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between">
                        <Badge variant="purple" className="text-[10px] shadow">
                          Imported
                        </Badge>
                        <Badge variant={scoreVariant} className="text-[10px] font-bold shadow">
                          {score}/100
                        </Badge>
                      </div>

                      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                        <div className="w-10 h-10 rounded-full bg-blue-600/90 text-white flex items-center justify-center shadow-lg">
                          <Play className="w-5 h-5 ml-0.5 fill-current" />
                        </div>
                      </div>

                      <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 text-[11px] font-mono text-slate-200">
                        {Math.round(clip.duration || 0)}s
                      </div>
                    </div>

                    <CardContent className="p-4 space-y-2">
                      <h4
                        className="text-sm font-semibold text-white line-clamp-1 hover:text-blue-400 cursor-pointer"
                        onClick={() => setPreviewClip(clip)}
                      >
                        {clip.suggested_title || cid}
                      </h4>

                      <div className="flex items-center justify-between text-xs text-slate-400">
                        <span className="truncate max-w-[150px] font-medium text-slate-300">
                          {clip.channel || "YouTube"}
                        </span>
                        <span>{formatRecordedDate(clip.created_at)}</span>
                      </div>

                      {clip.reason && (
                        <p className="text-[11px] text-slate-400 line-clamp-2">
                          {clip.reason}
                        </p>
                      )}
                    </CardContent>
                  </div>

                  {/* Actions footer */}
                  <div className="p-3 pt-0 border-t border-[#1f293d]/60 mt-2 flex items-center space-x-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleProcessClip(cid)}
                      className="flex-1 text-xs border-[#1f293d] hover:bg-blue-600 hover:text-white transition-all flex items-center justify-center space-x-1.5"
                    >
                      <Film className="w-3.5 h-3.5" />
                      <span>Process to Short</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setClipToDelete(clip)}
                      className="h-8 w-8 text-slate-400 hover:text-red-400"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </Card>
              )
            })}
          </div>
        )}

        {/* Clear All Imports button at the bottom */}
        {(importedClips.length > 0 || library.length > 0) && (
          <div className="pt-4 flex justify-center">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setClearModalOpen(true)}
              className="text-xs border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300 flex items-center space-x-1.5"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Clear All Imports & Free Disk Space</span>
            </Button>
          </div>
        )}
      </div>

      {/* Preview Modal */}
      <ClipPlayer
        clip={previewClip}
        open={Boolean(previewClip)}
        onOpenChange={(open) => !open && setPreviewClip(null)}
      />

      {/* Delete Single Clip Confirmation */}
      <AlertDialog
        open={Boolean(clipToDelete)}
        onOpenChange={(open) => !open && setClipToDelete(null)}
      >
        <AlertDialogContent className="bg-[#0f1422] border-[#1f293d]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Delete Imported Clip?</AlertDialogTitle>
            <AlertDialogDescription className="text-slate-400">
              Are you sure you want to delete this clip? The video file and its metadata sidecar will be permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-[#1f293d] text-slate-300">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDeleteClip}
              className="bg-red-600 hover:bg-red-500 text-white"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Library Video Confirmation */}
      <AlertDialog
        open={Boolean(libraryVideoToDelete)}
        onOpenChange={(open) => !open && setLibraryVideoToDelete(null)}
      >
        <AlertDialogContent className="bg-[#0f1422] border-[#1f293d]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Delete Video from Library?</AlertDialogTitle>
            <AlertDialogDescription className="text-slate-400">
              Are you sure you want to delete "{libraryVideoToDelete?.title}" ({libraryVideoToDelete?.file_size_mb} MB) and its extracted clips? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-[#1f293d] text-slate-300">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDeleteLibraryVideo}
              className="bg-red-600 hover:bg-red-500 text-white"
            >
              Delete Video
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Clear All Imports Confirmation */}
      <AlertDialog
        open={clearModalOpen}
        onOpenChange={setClearModalOpen}
      >
        <AlertDialogContent className="bg-[#0f1422] border-[#1f293d]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white flex items-center space-x-2">
              <AlertTriangle className="w-5 h-5 text-amber-400" />
              <span>Clear All Imported Data?</span>
            </AlertDialogTitle>
            <AlertDialogDescription className="text-slate-400 space-y-2">
              <p>
                This will permanently delete all {importedClips.length} imported clips, all {library.length} downloaded/uploaded library videos, and their cached media files.
              </p>
              <p className="text-slate-300 font-medium">
                This will free up approximately {totalLibrarySizeMb.toFixed(1)} MB of local disk storage.
              </p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-[#1f293d] text-slate-300">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleClearAllImports}
              disabled={isClearing}
              className="bg-red-600 hover:bg-red-500 text-white"
            >
              {isClearing ? "Cleaning..." : "Clear All Imports"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

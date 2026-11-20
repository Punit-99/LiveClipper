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
  Clock
} from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { ImportForm } from "@/components/import/ImportForm"
import { AnalysisProgress } from "@/components/import/AnalysisProgress"
import { ClipPlayer } from "@/components/clips/ClipPlayer"
import {
  startImportAnalysis,
  fetchImportJobs,
  fetchImportedClips,
  deleteImportedClip,
  processImportedClip,
  formatRecordedDate,
} from "@/api"
import { toast } from "sonner"

export default function ImportPage({ onNavigate }) {
  const [jobs, setJobs] = useState([])
  const [activeJob, setActiveJob] = useState(null)
  const [importedClips, setImportedClips] = useState([])
  const [loading, setLoading] = useState(true)
  const [isAnalyzing, setIsAnalyzing] = useState(false)

  // Player & Delete modals
  const [previewClip, setPreviewClip] = useState(null)
  const [clipToDelete, setClipToDelete] = useState(null)

  const loadData = async () => {
    try {
      const [jobsData, clipsData] = await Promise.all([
        fetchImportJobs().catch(() => []),
        fetchImportedClips().catch(() => []),
      ])
      setJobs(jobsData)
      setImportedClips(clipsData)

      // Find active job if any
      const running = jobsData.find((j) => j.status === "downloading" || j.status === "analyzing" || j.status === "extracting" || j.status === "pending")
      if (running) {
        setActiveJob(running)
        setIsAnalyzing(true)
      } else if (jobsData.length > 0 && !activeJob) {
        setActiveJob(jobsData[0])
      }
    } catch (err) {
      console.error("Error loading import data", err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleStartAnalysis = async (params) => {
    setIsAnalyzing(true)
    try {
      const job = await startImportAnalysis(params)
      setActiveJob(job)
      toast.success("Analysis started! Video is being downloaded and analyzed.")
      await loadData()
    } catch (err) {
      setIsAnalyzing(false)
      toast.error(err.message || "Failed to start import analysis.")
    }
  }

  const handleProcessClip = async (clipId) => {
    try {
      await processImportedClip(clipId)
      toast.success("Clip queued for Shorts processing!")
    } catch (err) {
      toast.error(err.message || "Failed to queue clip.")
    }
  }

  const confirmDelete = async () => {
    if (!clipToDelete) return
    try {
      await deleteImportedClip(clipToDelete.clip_id)
      setImportedClips((prev) => prev.filter((c) => c.clip_id !== clipToDelete.clip_id))
      toast.success("Imported clip deleted.")
    } catch (err) {
      toast.error(err.message || "Failed to delete clip.")
    } finally {
      setClipToDelete(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Top Import Form */}
      <ImportForm
        onStartAnalysis={handleStartAnalysis}
        isAnalyzing={isAnalyzing}
      />

      {/* Active Progress banner if a job is in flight */}
      {activeJob && (
        <AnalysisProgress job={activeJob} />
      )}

      {/* Extracted Clips Results Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-4 h-4 text-purple-400" />
            <h3 className="font-semibold text-base text-white">Imported Highlights</h3>
            <Badge variant="outline" className="text-xs">
              {importedClips.length} {importedClips.length === 1 ? "clip" : "clips"}
            </Badge>
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

        {importedClips.length === 0 ? (
          <Card className="border-[#1f293d] bg-[#121826]/60 p-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-xl bg-purple-500/10 text-purple-400 mx-auto flex items-center justify-center">
              <Film className="w-6 h-6" />
            </div>
            <h4 className="font-semibold text-sm text-white">No Imported Clips Yet</h4>
            <p className="text-xs text-slate-400 max-w-sm mx-auto">
              Paste any YouTube video URL above and click Analyze. We'll automatically identify viral moments and extract highlight clips for you.
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {importedClips.map((clip) => {
              const score = Number(clip.score || 0)
              const scoreVariant = score >= 80 ? "success" : score >= 60 ? "warning" : "destructive"

              return (
                <Card
                  key={clip.clip_id}
                  className="border-[#1f293d] bg-[#121826]/90 overflow-hidden flex flex-col justify-between hover:border-slate-600 transition-all group"
                >
                  <div>
                    {/* Thumbnail video frame */}
                    <div
                      className="relative aspect-video bg-black/60 cursor-pointer overflow-hidden"
                      onClick={() => setPreviewClip(clip)}
                    >
                      <img
                        src={`/api/import/clips/${clip.clip_id}/thumbnail`}
                        alt={clip.suggested_title}
                        className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                        onError={(e) => {
                          e.currentTarget.style.display = "none"
                        }}
                      />
                      <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/30" />

                      {/* Badges */}
                      <div className="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between">
                        <Badge variant="purple" className="text-[10px] shadow">
                          Imported
                        </Badge>
                        <Badge variant={scoreVariant} className="text-[10px] font-bold shadow">
                          {score}/100
                        </Badge>
                      </div>

                      {/* Play hover button */}
                      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                        <div className="w-10 h-10 rounded-full bg-blue-600/90 text-white flex items-center justify-center shadow-lg">
                          <Play className="w-5 h-5 ml-0.5 fill-current" />
                        </div>
                      </div>

                      {/* Duration */}
                      <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 text-[11px] font-mono text-slate-200">
                        {Math.round(clip.duration || 0)}s
                      </div>
                    </div>

                    <CardContent className="p-4 space-y-2">
                      <h4
                        className="text-sm font-semibold text-white line-clamp-1 hover:text-blue-400 cursor-pointer"
                        onClick={() => setPreviewClip(clip)}
                      >
                        {clip.suggested_title || clip.clip_id}
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
                      onClick={() => handleProcessClip(clip.clip_id)}
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
      </div>

      {/* Preview Modal */}
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
              onClick={confirmDelete}
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

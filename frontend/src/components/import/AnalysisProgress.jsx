import React from "react"
import {
  CheckCircle2,
  Loader2,
  Sparkles,
  AlertCircle,
  XCircle,
  Activity,
  Film,
  Scissors,
  Brain,
  Volume2,
  Tv,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const STAGES = [
  { id: 1, name: "Download", icon: Film, range: [0, 15] },
  { id: 2, name: "Transcribe", icon: Volume2, range: [15, 30] },
  { id: 3, name: "Gemini AI", icon: Brain, range: [30, 50] },
  { id: 4, name: "Audio", icon: Volume2, range: [50, 65] },
  { id: 5, name: "Visual", icon: Tv, range: [65, 75] },
  { id: 6, name: "Scoring", icon: Sparkles, range: [75, 85] },
  { id: 7, name: "Extract", icon: Scissors, range: [85, 100] },
]

export function AnalysisProgress({ job, onCancelJob, isCancelling }) {
  if (!job) return null

  const isFailed = job.status === "failed"
  const isCompleted = job.status === "completed"
  const isCancelled = job.status === "cancelled"
  const isRunning = !isFailed && !isCompleted && !isCancelled
  const progress = job.progress || 0

  const activeStage = job.stage || (
    progress < 15 ? 1 :
    progress < 30 ? 2 :
    progress < 50 ? 3 :
    progress < 65 ? 4 :
    progress < 75 ? 5 :
    progress < 85 ? 6 : 7
  )

  const isDownloading = job.status === "downloading" || progress < 20
  const cancelLabel = isDownloading ? "Cancel Download" : "Stop Analysis"

  return (
    <Card className="border-blue-500/30 bg-[#0d1424]/90 backdrop-blur-md shadow-xl overflow-hidden">
      <CardContent className="p-5 space-y-4">
        {/* Header row */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center space-x-3">
            {isFailed ? (
              <AlertCircle className="w-5 h-5 text-red-400 shrink-0" />
            ) : isCancelled ? (
              <XCircle className="w-5 h-5 text-amber-400 shrink-0" />
            ) : isCompleted ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
            ) : (
              <div className="relative">
                <Loader2 className="w-5 h-5 text-blue-400 animate-spin shrink-0" />
                <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
              </div>
            )}
            <div>
              <div className="flex items-center space-x-2">
                <h4 className="font-semibold text-sm text-white">
                  {isCompleted
                    ? "Analysis Complete!"
                    : isCancelled
                    ? "Analysis Cancelled"
                    : isFailed
                    ? "Import Analysis Failed"
                    : "Processing YouTube Video Pipeline"}
                </h4>
                {isRunning && (
                  <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-400 border-emerald-500/30 flex items-center space-x-1 py-0">
                    <Activity className="w-2.5 h-2.5 animate-pulse" />
                    <span>Heartbeat Active</span>
                  </Badge>
                )}
              </div>
              <p className="text-xs text-slate-300 mt-0.5">
                {job.current_step || "Processing media and detecting highlights..."}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2 self-end sm:self-auto">
            <Badge
              variant={
                isCompleted
                  ? "success"
                  : isCancelled
                  ? "secondary"
                  : isFailed
                  ? "destructive"
                  : "purple"
              }
              className="text-xs font-semibold px-2 py-0.5"
            >
              {isCompleted ? "100% Done" : isCancelled ? "Cancelled" : isFailed ? "Error" : `${progress}%`}
            </Badge>

            {isRunning && onCancelJob && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onCancelJob(job.job_id)}
                disabled={isCancelling}
                className="h-7 text-xs border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300 transition-colors"
              >
                {isCancelling ? "Stopping..." : cancelLabel}
              </Button>
            )}
          </div>
        </div>

        {/* Granular Progress Bar */}
        <Progress value={progress} className="h-2.5 bg-slate-800" />

        {/* 7-Stage Tracker */}
        <div className="grid grid-cols-7 gap-1 pt-1">
          {STAGES.map((s) => {
            const isStageDone = progress >= s.range[1] || isCompleted
            const isStageCurrent = isRunning && activeStage === s.id
            const Icon = s.icon

            return (
              <div
                key={s.id}
                className={cn(
                  "flex flex-col items-center justify-center p-1.5 rounded-lg border text-center transition-all",
                  isStageDone
                    ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                    : isStageCurrent
                    ? "bg-blue-500/20 border-blue-500/50 text-blue-300 ring-1 ring-blue-500/40"
                    : "bg-[#0b0f19] border-slate-800/60 text-slate-500"
                )}
              >
                <Icon className={cn("w-3.5 h-3.5 mb-0.5", isStageCurrent && "animate-bounce")} />
                <span className="text-[10px] font-medium truncate w-full">{s.name}</span>
              </div>
            )
          })}
        </div>

        {/* Error message card */}
        {isFailed && job.error && (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300 space-y-1">
            <span className="font-semibold block">Failure Details:</span>
            <p className="font-mono text-[11px] break-all">{job.error}</p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

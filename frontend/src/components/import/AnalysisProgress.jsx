import React from "react"
import { CheckCircle2, Loader2, Sparkles, AlertCircle } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"

export function AnalysisProgress({ job }) {
  if (!job) return null

  const isFailed = job.status === "failed"
  const isCompleted = job.status === "completed"
  const progress = job.progress || 0

  return (
    <Card className="border-blue-500/30 bg-blue-950/20 backdrop-blur-md">
      <CardContent className="p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2.5">
            {isFailed ? (
              <AlertCircle className="w-5 h-5 text-red-400" />
            ) : isCompleted ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400" />
            ) : (
              <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
            )}
            <div>
              <h4 className="font-semibold text-sm text-white">
                {isCompleted
                  ? "Analysis Complete!"
                  : isFailed
                  ? "Import Analysis Failed"
                  : "Analyzing YouTube Video..."}
              </h4>
              <p className="text-xs text-slate-300">
                {job.current_step || "Processing media pipeline..."}
              </p>
            </div>
          </div>
          <Badge
            variant={isCompleted ? "success" : isFailed ? "destructive" : "purple"}
            className="text-xs font-semibold"
          >
            {isCompleted ? "Done" : isFailed ? "Error" : `${progress}%`}
          </Badge>
        </div>

        <Progress value={progress} className="h-2" />

        {isFailed && job.error && (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300">
            {job.error}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

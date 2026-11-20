import React, { useState, useEffect } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { AspectRatio } from "@/components/ui/aspect-ratio"
import { Badge } from "@/components/ui/badge"
import { Film } from "lucide-react"

export function ClipPlayer({ clip, open, onOpenChange }) {
  const clipId = (clip?.clip_id && clip.clip_id !== "undefined")
    ? clip.clip_id
    : (clip?.id && clip.id !== "undefined")
    ? clip.id
    : ""
  const isImported = Boolean(clip?.is_imported || clip?.source_type === "imported" || clipId?.startsWith("imp_"))
  const initialSrc = clipId
    ? isImported
      ? `/api/import/clips/${clipId}/preview`
      : `/api/clips/${clipId}/preview`
    : null

  const thumbnailSrc = clipId
    ? isImported
      ? `/api/import/clips/${clipId}/thumbnail`
      : `/api/clips/${clipId}/thumbnail`
    : null

  const [videoSrc, setVideoSrc] = useState(initialSrc)
  const [hasError, setHasError] = useState(false)

  useEffect(() => {
    setVideoSrc(initialSrc)
    setHasError(false)
  }, [initialSrc])

  if (!clip) return null

  const handleVideoError = () => {
    if (clipId && videoSrc?.startsWith("/api/import/clips/")) {
      // Fallback to /api/clips/:id/preview
      setVideoSrc(`/api/clips/${clipId}/preview`)
    } else if (clipId && videoSrc?.startsWith("/api/clips/")) {
      // Fallback to /api/import/clips/:id/preview
      setVideoSrc(`/api/import/clips/${clipId}/preview`)
    } else {
      setHasError(true)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl p-4 sm:p-6 bg-[#0f1422] border-[#1f293d]">
        <DialogHeader className="mb-2 text-left">
          <div className="flex items-center space-x-2">
            <DialogTitle className="text-base sm:text-lg font-bold text-white truncate">
              {clip.title || clip.suggested_title || clipId}
            </DialogTitle>
            <Badge variant={isImported ? "purple" : "success"} className="text-[10px] shrink-0">
              {isImported ? "Imported" : "Live"}
            </Badge>
          </div>
          <DialogDescription className="text-xs text-slate-400">
            {clip.channel || clip.streamer_name} · Score: {clip.score ?? clip.highlight_score ?? 0}/100 · {Math.round(clip.duration || 0)}s
          </DialogDescription>
        </DialogHeader>

        {/* 16:9 AspectRatio Video Frame */}
        <div className="overflow-hidden rounded-lg bg-black border border-[#1f293d] flex items-center justify-center">
          <AspectRatio ratio={16 / 9} className="flex items-center justify-center">
            {videoSrc && !hasError ? (
              <video
                key={videoSrc}
                src={videoSrc}
                poster={thumbnailSrc || undefined}
                controls
                autoPlay
                playsInline
                preload="auto"
                onError={handleVideoError}
                className="w-full h-full object-contain"
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-slate-500 space-y-2 p-6">
                <Film className="w-10 h-10 opacity-40 animate-pulse" />
                <span className="text-xs">Video preview unavailable or still processing</span>
              </div>
            )}
          </AspectRatio>
        </div>

        {clip.analysis_reason && (
          <div className="mt-3 p-3 rounded-lg bg-[#0d131f] border border-[#1f293d] text-xs text-slate-300">
            <span className="font-semibold text-slate-400">Analysis verdict: </span>
            {clip.analysis_reason}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

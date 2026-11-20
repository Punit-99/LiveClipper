import React, { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Copy, Check, ExternalLink, Sparkles, Film, Tag } from "lucide-react"
import { toast } from "sonner"

export function ShortPlayer({ shortItem, open, onOpenChange }) {
  const [copiedTitle, setCopiedTitle] = useState("")

  if (!shortItem) return null

  const clipId = (shortItem.clip_id && shortItem.clip_id !== "undefined")
    ? shortItem.clip_id
    : (shortItem.id && shortItem.id !== "undefined")
    ? shortItem.id
    : ""

  const score = Number(shortItem.score ?? shortItem.highlight_score ?? 0)
  const videoSrc = clipId ? `/api/shorts/${clipId}/preview` : null
  const duration = Math.round(shortItem.duration || 0)

  const [hasError, setHasError] = useState(false)

  React.useEffect(() => {
    setHasError(false)
  }, [videoSrc])

  const viralTitles = Array.isArray(shortItem.viral_titles)
    ? shortItem.viral_titles.map((t) => (typeof t === "object" ? t.title : String(t)))
    : []

  const activeTitle = shortItem.selected_title || shortItem.title || viralTitles[0] || "Short Video"
  const tags = Array.isArray(shortItem.tags) ? shortItem.tags : []

  const handleCopy = (text) => {
    navigator.clipboard.writeText(text)
    setCopiedTitle(text)
    toast.success("Copied to clipboard!")
    setTimeout(() => setCopiedTitle(""), 2000)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[420px] w-full p-4 bg-[#0b101d] border-[#1e293b] max-h-[92vh] flex flex-col overflow-y-auto">
        <DialogHeader className="text-left space-y-1 mb-2">
          <div className="flex items-center justify-between pr-6">
            <Badge variant="purple" className="text-[10px] uppercase font-bold tracking-wider">
              #Shorts 9:16
            </Badge>
            <div className="flex items-center space-x-2">
              <Badge variant="success" className="text-[10px]">
                Score: {score}/100
              </Badge>
              <Badge variant="outline" className="text-[10px] text-slate-300 border-slate-700">
                {duration}s
              </Badge>
            </div>
          </div>
          <DialogTitle className="text-sm font-bold text-white line-clamp-2 pt-1">
            {activeTitle}
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-400">
            {shortItem.channel || "Streamer"}
          </DialogDescription>
        </DialogHeader>

        {/* 9:16 Vertical Video Viewport (No horizontal black letterboxing) */}
        <div className="relative mx-auto w-full max-w-[340px] aspect-[9/16] bg-black rounded-xl overflow-hidden border border-slate-800 shadow-2xl flex items-center justify-center">
          {videoSrc && !hasError ? (
            <video
              key={videoSrc}
              src={videoSrc}
              controls
              autoPlay
              playsInline
              preload="auto"
              onError={() => setHasError(true)}
              className="w-full h-full object-cover rounded-xl"
            />
          ) : (
            <div className="flex flex-col items-center justify-center text-slate-500 space-y-2 p-6">
              <Film className="w-10 h-10 opacity-40 animate-pulse" />
              <span className="text-xs">Video preview unavailable</span>
            </div>
          )}
        </div>

        {/* Metadata & Actions footer */}
        <div className="mt-3 space-y-2.5 text-left">
          {/* Active title with 1-click copy */}
          <div className="p-2.5 rounded-lg bg-[#131b2e] border border-slate-800 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block">
                Viral Title
              </span>
              <p className="text-xs text-white truncate font-medium">{activeTitle}</p>
            </div>
            <Button
              size="icon"
              variant="ghost"
              onClick={() => handleCopy(activeTitle)}
              className="h-7 w-7 text-slate-400 hover:text-white shrink-0"
              title="Copy Title"
            >
              {copiedTitle === activeTitle ? (
                <Check className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
            </Button>
          </div>

          {/* Alternate viral titles if available */}
          {viralTitles.length > 1 && (
            <div className="space-y-1.5 pt-1">
              <span className="text-[11px] font-medium text-slate-300 flex items-center gap-1">
                <Sparkles className="w-3 h-3 text-purple-400" /> Alternate Titles:
              </span>
              <div className="space-y-1 max-h-24 overflow-y-auto">
                {viralTitles.map((t, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleCopy(t)}
                    className="p-1.5 px-2 rounded bg-[#0f1626] hover:bg-[#162138] border border-slate-800/80 cursor-pointer flex items-center justify-between text-[11px] text-slate-300 hover:text-white transition-colors"
                  >
                    <span className="truncate pr-2">{t}</span>
                    {copiedTitle === t ? (
                      <Check className="w-3 h-3 text-emerald-400 shrink-0" />
                    ) : (
                      <Copy className="w-3 h-3 text-slate-500 shrink-0" />
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tags */}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {tags.slice(0, 6).map((tag, i) => (
                <Badge
                  key={i}
                  variant="secondary"
                  className="text-[10px] bg-slate-800/60 text-slate-300 border-none px-1.5 py-0.5"
                >
                  #{tag.replace(/^#/, "")}
                </Badge>
              ))}
            </div>
          )}

          {/* YouTube link if uploaded */}
          {shortItem.youtube_url && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => window.open(shortItem.youtube_url, "_blank")}
              className="w-full text-xs text-red-400 border-red-500/30 hover:bg-red-500/10 hover:text-red-300 flex items-center justify-center gap-1.5"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              <span>Watch on YouTube Shorts</span>
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

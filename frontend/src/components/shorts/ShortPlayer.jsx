import React from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { AspectRatio } from "@/components/ui/aspect-ratio"
import { Badge } from "@/components/ui/badge"

export function ShortPlayer({ shortItem, open, onOpenChange }) {
  if (!shortItem) return null

  const videoSrc = `/api/shorts/${shortItem.clip_id}/preview`

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md p-4 sm:p-6 bg-[#0f1422] border-[#1f293d]">
        <DialogHeader className="mb-2 text-left">
          <div className="flex items-center space-x-2">
            <DialogTitle className="text-base font-bold text-white truncate">
              {shortItem.title || shortItem.clip_id}
            </DialogTitle>
            <Badge variant="purple" className="text-[10px] shrink-0">
              #Shorts
            </Badge>
          </div>
          <DialogDescription className="text-xs text-slate-400">
            {shortItem.channel} · Score: {shortItem.highlight_score || 0}/100 · {Math.round(shortItem.duration || 0)}s
          </DialogDescription>
        </DialogHeader>

        {/* 9:16 AspectRatio Vertical Frame */}
        <div className="overflow-hidden rounded-lg bg-black border border-[#1f293d] max-h-[70vh] flex items-center justify-center">
          <AspectRatio ratio={9 / 16} className="h-full max-h-[70vh]">
            <video
              src={videoSrc}
              controls
              autoPlay
              playsInline
              className="w-full h-full object-contain"
            />
          </AspectRatio>
        </div>
      </DialogContent>
    </Dialog>
  )
}

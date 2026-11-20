import React from "react"
import { Play, MoreVertical, Film, Trash2, CheckCircle2 } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { formatRecordedDate } from "@/api"

export function ClipCard({
  clip,
  isSelected,
  onToggleSelect,
  onPreview,
  onProcess,
  onDelete,
}) {
  const isImported = Boolean(
    clip.is_imported || clip.source_type === "imported" || clip.clip_id?.startsWith("imp_")
  )
  const score = Number(clip.score || clip.highlight_score || 0)
  const scoreVariant = score >= 80 ? "success" : score >= 60 ? "warning" : "destructive"

  const thumbnailSrc = isImported
    ? `/api/import/clips/${clip.clip_id}/thumbnail`
    : `/api/clips/${clip.clip_id}/thumbnail`

  const dateStr = formatRecordedDate(clip.created_at || clip.timestamp)

  return (
    <Card className="overflow-hidden border-[#1f293d] bg-[#121826]/90 hover:border-slate-600 transition-all group flex flex-col justify-between">
      <div>
        {/* Thumbnail banner with play trigger */}
        <div
          className="relative aspect-video bg-black/60 cursor-pointer overflow-hidden"
          onClick={() => onPreview(clip)}
        >
          <img
            src={thumbnailSrc}
            alt={clip.title || clip.suggested_title}
            className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
            onError={(e) => {
              e.currentTarget.style.display = "none"
            }}
          />
          {/* Overlay gradient */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/30" />

          {/* Top badges: Selection Checkbox & Type */}
          <div
            className="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center space-x-2">
              <Checkbox
                checked={isSelected}
                onCheckedChange={() => onToggleSelect(clip.clip_id)}
                className="bg-black/60 border-slate-500"
              />
              <Badge variant={isImported ? "purple" : "secondary"} className="text-[10px] shadow">
                {isImported ? "Imported" : "Live"}
              </Badge>
            </div>
            <Badge variant={scoreVariant} className="text-[10px] font-bold shadow">
              {score}/100
            </Badge>
          </div>

          {/* Play button hover icon */}
          <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="w-10 h-10 rounded-full bg-blue-600/90 text-white flex items-center justify-center shadow-lg">
              <Play className="w-5 h-5 ml-0.5 fill-current" />
            </div>
          </div>

          {/* Duration badge */}
          <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 text-[11px] font-mono text-slate-200">
            {Math.round(clip.duration || 0)}s
          </div>
        </div>

        {/* Content details */}
        <CardContent className="p-4 space-y-2">
          <div className="flex items-start justify-between gap-2">
            <h4
              className="text-sm font-semibold text-white line-clamp-1 hover:text-blue-400 cursor-pointer"
              onClick={() => onPreview(clip)}
            >
              {clip.title || clip.suggested_title || clip.clip_id}
            </h4>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-7 w-7 text-slate-400 hover:text-white -mr-1">
                  <MoreVertical className="w-3.5 h-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-40">
                <DropdownMenuItem onClick={() => onPreview(clip)}>
                  <Play className="w-3.5 h-3.5 mr-2" />
                  <span>Preview</span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onProcess(clip.clip_id)}>
                  <Film className="w-3.5 h-3.5 mr-2 text-blue-400" />
                  <span>Process to Short</span>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => onDelete(clip.clip_id)} className="text-red-400 hover:text-red-300">
                  <Trash2 className="w-3.5 h-3.5 mr-2" />
                  <span>Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="truncate max-w-[150px] font-medium text-slate-300">
              {clip.channel || clip.streamer_name || "Unknown"}
            </span>
            {dateStr && <span>{dateStr}</span>}
          </div>

          {clip.analysis_reason && (
            <p className="text-[11px] text-slate-400 line-clamp-2">
              {clip.analysis_reason}
            </p>
          )}
        </CardContent>
      </div>

      {/* Card bottom actions */}
      <div className="p-3 pt-0 border-t border-[#1f293d]/60 mt-2 flex items-center justify-between">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onProcess(clip.clip_id)}
          className="w-full text-xs flex items-center justify-center space-x-1.5 border-[#1f293d] hover:bg-blue-600 hover:text-white hover:border-transparent transition-all"
        >
          <Film className="w-3.5 h-3.5" />
          <span>Process to Short</span>
        </Button>
      </div>
    </Card>
  )
}

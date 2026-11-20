import React from "react"
import { Play, Film, Trash2, ArrowUpDown } from "lucide-react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { formatRecordedDate } from "@/api"

export function ClipTable({
  clips,
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
  onPreview,
  onProcess,
  onDelete,
}) {
  const isAllSelected = clips.length > 0 && selectedIds.length === clips.length

  return (
    <div className="rounded-xl border border-[#1f293d] bg-[#121826]/80 overflow-hidden">
      <Table>
        <TableHeader className="bg-[#0f1422]">
          <TableRow className="border-[#1f293d]">
            <TableHead className="w-12">
              <Checkbox
                checked={isAllSelected}
                onCheckedChange={onToggleSelectAll}
                className="border-slate-500"
              />
            </TableHead>
            <TableHead className="w-20">Type</TableHead>
            <TableHead>Title</TableHead>
            <TableHead>Channel</TableHead>
            <TableHead>Score</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>Recorded</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {clips.map((clip) => {
            const isImported = Boolean(
              clip.is_imported || clip.source_type === "imported" || clip.clip_id?.startsWith("imp_")
            )
            const score = Number(clip.score || clip.highlight_score || 0)
            const scoreVariant = score >= 80 ? "success" : score >= 60 ? "warning" : "destructive"
            const isSelected = selectedIds.includes(clip.clip_id)

            return (
              <TableRow
                key={clip.clip_id}
                className="border-[#1f293d] hover:bg-[#1a2233]/40"
              >
                <TableCell>
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={() => onToggleSelect(clip.clip_id)}
                    className="border-slate-500"
                  />
                </TableCell>
                <TableCell>
                  <Badge variant={isImported ? "purple" : "secondary"} className="text-[10px]">
                    {isImported ? "Imported" : "Live"}
                  </Badge>
                </TableCell>
                <TableCell className="font-medium text-white max-w-xs truncate cursor-pointer hover:text-blue-400" onClick={() => onPreview(clip)}>
                  {clip.title || clip.suggested_title || clip.clip_id}
                </TableCell>
                <TableCell className="text-slate-300">
                  {clip.channel || clip.streamer_name || "Unknown"}
                </TableCell>
                <TableCell>
                  <Badge variant={scoreVariant} className="text-[10px]">
                    {score}/100
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-xs text-slate-300">
                  {Math.round(clip.duration || 0)}s
                </TableCell>
                <TableCell className="text-xs text-slate-400">
                  {formatRecordedDate(clip.created_at || clip.timestamp)}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end space-x-1.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onPreview(clip)}
                      className="h-7 w-7 text-slate-400 hover:text-white"
                      title="Preview Clip"
                    >
                      <Play className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onProcess(clip.clip_id)}
                      className="h-7 w-7 text-blue-400 hover:text-blue-300 hover:bg-blue-500/10"
                      title="Process to Short"
                    >
                      <Film className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onDelete(clip.clip_id)}
                      className="h-7 w-7 text-red-400 hover:text-red-300 hover:bg-red-500/10"
                      title="Delete Clip"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

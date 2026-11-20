import React from "react"
import { Play, Upload, ExternalLink, ShieldCheck, RotateCw, Trash2, CheckCircle2 } from "lucide-react"
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

export function ShortTable({
  shorts,
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
  onPreview,
  onUpload,
  onVerify,
  onReupload,
  onDelete,
}) {
  const isAllSelected = shorts.length > 0 && selectedIds.length === shorts.length

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
            <TableHead>Status</TableHead>
            <TableHead>Title</TableHead>
            <TableHead>Channel</TableHead>
            <TableHead>Score</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>Processed</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(shorts || []).map((short) => {
            const clipId = short.clip_id || short.id || ""
            const score = Number(short.score ?? short.highlight_score ?? 0)
            const isUploaded = short.state === "UPLOADED" || short.youtube_url
            const isFailed = short.state === "FAILED"
            const isSelected = selectedIds.includes(clipId)

            return (
              <TableRow
                key={clipId}
                className="border-[#1f293d] hover:bg-[#1a2233]/40"
              >
                <TableCell>
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={() => onToggleSelect(clipId)}
                    className="border-slate-500"
                  />
                </TableCell>
                <TableCell>
                  {isUploaded ? (
                    <Badge variant="success" className="text-[10px]">
                      Uploaded ✓
                    </Badge>
                  ) : isFailed ? (
                    <Badge variant="destructive" className="text-[10px]">
                      Failed
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="text-[10px]">
                      Ready
                    </Badge>
                  )}
                </TableCell>
                <TableCell
                  className="font-medium text-white max-w-xs truncate cursor-pointer hover:text-blue-400"
                  onClick={() => onPreview(short)}
                >
                  {short.title || short.selected_title || clipId}
                </TableCell>
                <TableCell className="text-slate-300">{short.channel}</TableCell>
                <TableCell>
                  <Badge variant="success" className="text-[10px]">
                    {score}/100
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-xs text-slate-300">
                  {Math.round(short.duration || 0)}s
                </TableCell>
                <TableCell className="text-xs text-slate-400">
                  {formatRecordedDate(short.created_at || short.timestamp || short.processed_at_iso || short.recorded_at_iso)}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end space-x-1.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onPreview(short)}
                      className="h-7 w-7 text-slate-400 hover:text-white"
                      title="Preview Short"
                    >
                      <Play className="w-3.5 h-3.5" />
                    </Button>

                    {isUploaded ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onVerify(clipId)}
                          className="h-7 w-7 text-emerald-400 hover:bg-emerald-500/10"
                          title="Verify on YouTube"
                        >
                          <ShieldCheck className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onReupload(clipId)}
                          className="h-7 w-7 text-slate-400 hover:text-white"
                          title="Re-upload"
                        >
                          <RotateCw className="w-3.5 h-3.5" />
                        </Button>
                        {short.youtube_url && (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => window.open(short.youtube_url, "_blank")}
                            className="h-7 w-7 text-red-400 hover:text-red-300"
                            title="Open in YouTube"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </Button>
                        )}
                      </>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => onUpload(clipId)}
                        className="h-7 w-7 text-red-400 hover:text-red-300 hover:bg-red-500/10"
                        title="Upload to YouTube"
                      >
                        <Upload className="w-3.5 h-3.5" />
                      </Button>
                    )}

                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onDelete(clipId)}
                      className="h-7 w-7 text-red-400 hover:text-red-300"
                      title="Delete Short"
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

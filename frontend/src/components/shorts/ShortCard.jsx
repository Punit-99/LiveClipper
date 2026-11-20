import React from "react"
import {
  Play,
  Upload,
  CheckCircle2,
  AlertCircle,
  RotateCw,
  ExternalLink,
  MoreVertical,
  Trash2,
  Loader2,
  ShieldCheck,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Progress } from "@/components/ui/progress"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { formatRecordedDate } from "@/api"

export function ShortCard({
  shortItem,
  isSelected,
  onToggleSelect,
  onPreview,
  onUpload,
  onVerify,
  onReupload,
  onDelete,
  uploadState,
  verifyState,
}) {
  const isUploaded = shortItem.state === "UPLOADED" || shortItem.youtube_url
  const isFailed = shortItem.state === "FAILED"
  const isProcessing = shortItem.state === "PROCESSING" || shortItem.state === "DISCOVERED"

  const isUploadingActive = uploadState?.status === "uploading"
  const isUploadQueued = uploadState?.status === "queued"
  const uploadPct = uploadState?.percentage || 0

  const isVerifying = verifyState?.loading
  const verificationResult = verifyState?.result

  const thumbnailSrc = `/api/shorts/${shortItem.clip_id}/thumbnail`
  const dateStr = formatRecordedDate(shortItem.created_at || shortItem.timestamp)

  return (
    <Card className="overflow-hidden border-[#1f293d] bg-[#121826]/90 hover:border-slate-600 transition-all group flex flex-col justify-between">
      <div>
        {/* Thumbnail banner with 9:16 or 16:9 vertical preview */}
        <div
          className="relative aspect-[9/14] sm:aspect-[9/13] bg-black/80 cursor-pointer overflow-hidden flex items-center justify-center"
          onClick={() => onPreview(shortItem)}
        >
          <img
            src={thumbnailSrc}
            alt={shortItem.title}
            className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
            onError={(e) => {
              e.currentTarget.style.display = "none"
            }}
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-transparent to-black/40" />

          {/* Top badges: Selection Checkbox & Upload Status */}
          <div
            className="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between"
            onClick={(e) => e.stopPropagation()}
          >
            <Checkbox
              checked={isSelected}
              onCheckedChange={() => onToggleSelect(shortItem.clip_id)}
              className="bg-black/60 border-slate-500"
            />
            {isUploaded ? (
              <Badge variant="success" className="text-[10px] flex items-center space-x-1 shadow">
                <CheckCircle2 className="w-3 h-3 mr-1" />
                <span>Uploaded</span>
              </Badge>
            ) : isUploadingActive ? (
              <Badge variant="purple" className="text-[10px] flex items-center space-x-1 shadow">
                <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                <span>{uploadPct}%</span>
              </Badge>
            ) : isUploadQueued ? (
              <Badge variant="warning" className="text-[10px] shadow">
                Queued
              </Badge>
            ) : isFailed ? (
              <Badge variant="destructive" className="text-[10px] shadow">
                Failed
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-[10px] shadow">
                Ready
              </Badge>
            )}
          </div>

          {/* Play button hover overlay */}
          <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="w-11 h-11 rounded-full bg-blue-600/90 text-white flex items-center justify-center shadow-lg">
              <Play className="w-5 h-5 ml-0.5 fill-current" />
            </div>
          </div>

          {/* Bottom duration & score tag */}
          <div className="absolute bottom-2 left-2 right-2 flex items-center justify-between text-[11px] font-mono">
            <span className="px-1.5 py-0.5 rounded bg-black/80 text-emerald-400 font-bold">
              {shortItem.highlight_score || 0}/100
            </span>
            <span className="px-1.5 py-0.5 rounded bg-black/80 text-slate-200">
              {Math.round(shortItem.duration || 0)}s
            </span>
          </div>
        </div>

        {/* Details section */}
        <CardContent className="p-3.5 space-y-2">
          <div className="flex items-start justify-between gap-1.5">
            <h4
              className="text-sm font-semibold text-white line-clamp-1 hover:text-blue-400 cursor-pointer"
              onClick={() => onPreview(shortItem)}
            >
              {shortItem.title || shortItem.clip_id}
            </h4>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-7 w-7 text-slate-400 hover:text-white -mr-1 shrink-0">
                  <MoreVertical className="w-3.5 h-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem onClick={() => onPreview(shortItem)}>
                  <Play className="w-3.5 h-3.5 mr-2" />
                  <span>Preview Short</span>
                </DropdownMenuItem>
                {shortItem.youtube_url && (
                  <DropdownMenuItem onClick={() => window.open(shortItem.youtube_url, "_blank")}>
                    <ExternalLink className="w-3.5 h-3.5 mr-2 text-red-400" />
                    <span>Watch on YouTube</span>
                  </DropdownMenuItem>
                )}
                {isUploaded && (
                  <DropdownMenuItem onClick={() => onVerify(shortItem.clip_id)}>
                    <ShieldCheck className="w-3.5 h-3.5 mr-2 text-emerald-400" />
                    <span>Verify on YouTube</span>
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => onDelete(shortItem.clip_id)} className="text-red-400">
                  <Trash2 className="w-3.5 h-3.5 mr-2" />
                  <span>Delete Short</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="truncate max-w-[140px] font-medium text-slate-300">
              {shortItem.channel}
            </span>
            {dateStr && <span className="text-[11px]">{dateStr}</span>}
          </div>

          {/* Active upload progress */}
          {isUploadingActive && (
            <div className="space-y-1 pt-1">
              <div className="flex justify-between text-[11px] text-blue-400">
                <span>Uploading to YouTube...</span>
                <span>{uploadPct}%</span>
              </div>
              <Progress value={uploadPct} className="h-1.5" />
            </div>
          )}

          {/* Verification feedback */}
          {verificationResult && (
            <div
              className={`p-2 rounded text-[11px] border flex items-center space-x-1.5 ${
                verificationResult.verified
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/20"
              }`}
            >
              {verificationResult.verified ? (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                  <span className="truncate">Verified on YouTube ({verificationResult.status})</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  <span className="truncate">Video not found on YouTube</span>
                </>
              )}
            </div>
          )}
        </CardContent>
      </div>

      {/* Card action footer */}
      <div className="p-3 pt-0 border-t border-[#1f293d]/60 mt-2 space-y-1.5">
        {isUploaded ? (
          <div className="flex items-center space-x-1.5">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onVerify(shortItem.clip_id)}
              disabled={isVerifying}
              className="flex-1 text-xs border-[#1f293d] hover:bg-[#1f293d]"
            >
              {isVerifying ? (
                <Loader2 className="w-3 h-3 animate-spin mr-1" />
              ) : (
                <ShieldCheck className="w-3 h-3 mr-1 text-emerald-400" />
              )}
              <span>Verify</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onReupload(shortItem.clip_id)}
              className="flex-1 text-xs border-[#1f293d] hover:bg-[#1f293d]"
            >
              <RotateCw className="w-3 h-3 mr-1" />
              <span>Re-upload</span>
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            onClick={() => onUpload(shortItem.clip_id)}
            disabled={isUploadingActive || isUploadQueued}
            className="w-full text-xs bg-red-600 hover:bg-red-500 text-white font-medium flex items-center justify-center space-x-1.5 shadow"
          >
            {isUploadingActive ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Uploading... {uploadPct}%</span>
              </>
            ) : isUploadQueued ? (
              <span>Queued for upload</span>
            ) : (
              <>
                <Upload className="w-3.5 h-3.5" />
                <span>Upload to YouTube</span>
              </>
            )}
          </Button>
        )}
      </div>
    </Card>
  )
}

import React, { useState, useEffect, useRef } from "react"
import {
  Youtube,
  Upload,
  Sparkles,
  Sliders,
  Clock,
  Film,
  Loader2,
  FileVideo,
  Download,
  CheckCircle2,
  AlertCircle,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Progress } from "@/components/ui/progress"
import { fetchVideoInfo, uploadImportVideoFile, downloadImportVideo } from "@/api"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

export function ImportForm({
  onStartAnalysis,
  onDownloadOnly,
  onVideoUploaded,
  isAnalyzing,
}) {
  const [activeTab, setActiveTab] = useState("url") // "url" | "upload"

  // URL state
  const [url, setUrl] = useState("")
  const [videoMeta, setVideoMeta] = useState(null)
  const [metaLoading, setMetaLoading] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)

  // Upload file state
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef(null)

  // Timing Options
  const [timingMode, setTimingMode] = useState("full") // "full" | "range"
  const [startTime, setStartTime] = useState("")
  const [endTime, setEndTime] = useState("")

  // Sliders
  const [numClips, setNumClips] = useState(5)
  const [duration, setDuration] = useState(55)
  const [skipIntroOutro, setSkipIntroOutro] = useState(false)
  const [skipIntroMinutes, setSkipIntroMinutes] = useState(1)
  const [skipOutroMinutes, setSkipOutroMinutes] = useState(1)

  // Auto fetch YouTube metadata when url changes
  useEffect(() => {
    const trimmed = url.trim()
    const isYt = /(?:youtube\.com\/(?:watch\?|shorts\/|live\/)|youtu\.be\/)/i.test(trimmed)
    if (!isYt) {
      setVideoMeta(null)
      return
    }

    // Auto-detect timestamp if present in URL
    try {
      const parsedUrl = new URL(trimmed.startsWith("http") ? trimmed : `https://${trimmed}`)
      const tParam = parsedUrl.searchParams.get("t") || parsedUrl.searchParams.get("start")
      if (tParam) {
        setTimingMode("range")
        setStartTime(tParam)
      }
    } catch {
      // ignore URL parse errors
    }

    const timer = setTimeout(async () => {
      setMetaLoading(true)
      try {
        const info = await fetchVideoInfo(trimmed)
        setVideoMeta(info)
      } catch {
        // Silently fail on invalid typing
      } finally {
        setMetaLoading(false)
      }
    }, 500)

    return () => clearTimeout(timer)
  }, [url])

  // Handle URL Download to Library
  const handleDownloadOnly = async () => {
    if (!url.trim()) {
      toast.error("Please enter a YouTube URL.")
      return
    }
    setIsDownloading(true)
    const payload = {
      url: url.trim(),
      quality: 1080,
      start_time: timingMode === "range" ? startTime.trim() : null,
      end_time: timingMode === "range" ? endTime.trim() : null,
    }
    try {
      if (onDownloadOnly) {
        await onDownloadOnly(payload)
      } else {
        await downloadImportVideo(payload)
        toast.success("Video downloaded and added to Library!")
      }
      setUrl("")
      setVideoMeta(null)
    } catch (err) {
      toast.error(err.message || "Download failed.")
    } finally {
      setIsDownloading(false)
    }
  }

  // Handle File Upload to Library
  const handleUploadFile = async () => {
    if (!uploadFile) {
      toast.error("Please select a video file first.")
      return
    }
    setIsUploading(true)
    setUploadProgress(0)
    try {
      const result = await uploadImportVideoFile(uploadFile, (pct) => {
        setUploadProgress(pct)
      })
      toast.success(`Video "${uploadFile.name}" uploaded to Library!`)
      setUploadFile(null)
      setUploadProgress(0)
      if (onVideoUploaded) onVideoUploaded(result)
    } catch (err) {
      toast.error(err.message || "Upload failed.")
    } finally {
      setIsUploading(false)
    }
  }

  // Handle Direct Submit
  const handleDirectAnalyze = (e) => {
    e.preventDefault()
    if (!url.trim()) {
      toast.error("Please enter a valid YouTube video URL.")
      return
    }

    onStartAnalysis({
      url: url.trim(),
      num_clips: numClips,
      clip_duration: duration,
      skip_intro: skipIntroOutro ? skipIntroMinutes * 60 : 0,
      skip_outro: skipIntroOutro ? skipOutroMinutes * 60 : 0,
      quality: 1080,
      start_time: timingMode === "range" ? startTime.trim() : null,
      end_time: timingMode === "range" ? endTime.trim() : null,
    })
  }

  return (
    <Card className="border-[#1f293d] bg-[#121826]/90 shadow-xl overflow-hidden">
      <CardHeader className="border-b border-[#1f293d]/60 pb-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center border border-blue-500/30">
              <Film className="w-4 h-4" />
            </div>
            <div>
              <CardTitle className="text-base text-white">Import & Analyze Video</CardTitle>
              <CardDescription className="text-xs text-slate-400">
                Download a YouTube video or upload a video file, detect highlights, and cut clips.
              </CardDescription>
            </div>
          </div>

          {/* Mode Switcher Tabs */}
          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-auto">
            <TabsList className="bg-[#0b101d] border border-[#1f293d] h-9 p-0.5">
              <TabsTrigger
                value="url"
                disabled={isAnalyzing}
                className="text-xs data-[state=active]:bg-blue-600 data-[state=active]:text-white flex items-center space-x-1.5 px-3"
              >
                <Youtube className="w-3.5 h-3.5 text-red-400" />
                <span>YouTube URL</span>
              </TabsTrigger>
              <TabsTrigger
                value="upload"
                disabled={isAnalyzing}
                className="text-xs data-[state=active]:bg-blue-600 data-[state=active]:text-white flex items-center space-x-1.5 px-3"
              >
                <Upload className="w-3.5 h-3.5 text-blue-400" />
                <span>Upload File</span>
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </CardHeader>

      <CardContent className="p-5 space-y-5">
        <div className={cn("space-y-5 transition-opacity", isAnalyzing && "opacity-50 pointer-events-none")}>
          {/* TAB 1: YouTube URL */}
          {activeTab === "url" && (
            <div className="space-y-4">
              <div className="flex flex-col sm:flex-row gap-2.5">
                <div className="relative flex-1">
                  <Youtube className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-red-500" />
                  <Input
                    type="url"
                    placeholder="Paste YouTube URL (e.g. https://www.youtube.com/watch?v=...)"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    disabled={isAnalyzing}
                    className="pl-9 h-10 border-[#1f293d] bg-[#0d131f] text-sm text-white focus-visible:ring-blue-500"
                  />
                </div>
                <div className="flex items-center space-x-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleDownloadOnly}
                    disabled={isAnalyzing || isDownloading || !url.trim()}
                    className="h-10 border-[#1f293d] bg-[#0d131f] text-xs text-slate-300 hover:text-white hover:bg-slate-800 shrink-0"
                    title="Download to Library to review before analyzing"
                  >
                    {isDownloading ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
                        <span>Downloading...</span>
                      </>
                    ) : (
                      <>
                        <Download className="w-3.5 h-3.5 mr-1.5 text-blue-400" />
                        <span>Save to Library</span>
                      </>
                    )}
                  </Button>

                  <Button
                    type="button"
                    onClick={handleDirectAnalyze}
                    disabled={isAnalyzing || isDownloading || !url.trim()}
                    className="h-10 px-5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shrink-0 flex items-center space-x-1.5"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Analyze Video</span>
                  </Button>
                </div>
              </div>

              {/* Video metadata preview banner */}
              {metaLoading && (
                <div className="p-3 rounded-lg bg-[#0d131f] border border-[#1f293d] flex items-center space-x-2 text-xs text-slate-400 animate-pulse">
                  <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
                  <span>Fetching video metadata from YouTube...</span>
                </div>
              )}

              {videoMeta && !metaLoading && (
                <div className="p-3 rounded-lg bg-[#0d131f] border border-[#1f293d] flex items-center space-x-3">
                  {videoMeta.thumbnail_url && (
                    <img
                      src={videoMeta.thumbnail_url}
                      alt={videoMeta.title}
                      className="w-24 h-14 object-cover rounded border border-slate-700 shrink-0"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <h5 className="font-semibold text-xs text-white truncate">{videoMeta.title}</h5>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      {videoMeta.channel} · {Math.round((videoMeta.duration || 0) / 60)} mins · {(videoMeta.view_count || 0).toLocaleString()} views
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: Upload Video File */}
          {activeTab === "upload" && (
            <div className="space-y-4">
              <input
                ref={fileInputRef}
                type="file"
                accept=".mp4,.mkv,.mov,.webm,.avi,video/*"
                className="hidden"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setUploadFile(e.target.files[0])
                  }
                }}
              />

              <div
                onClick={() => !isAnalyzing && !isUploading && fileInputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault()
                  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                    setUploadFile(e.dataTransfer.files[0])
                  }
                }}
                className={cn(
                  "border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all",
                  uploadFile
                    ? "border-blue-500/60 bg-blue-500/5"
                    : "border-[#1f293d] hover:border-slate-500 bg-[#0d131f]/60"
                )}
              >
                {uploadFile ? (
                  <div className="flex flex-col items-center space-y-2">
                    <FileVideo className="w-8 h-8 text-blue-400" />
                    <div>
                      <p className="text-sm font-semibold text-white">{uploadFile.name}</p>
                      <p className="text-xs text-slate-400">
                        {(uploadFile.size / (1024 * 1024)).toFixed(2)} MB · Ready to upload
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center space-y-2">
                    <Upload className="w-8 h-8 text-slate-500" />
                    <p className="text-xs text-slate-300 font-medium">
                      Drag & drop a video file here, or click to browse
                    </p>
                    <p className="text-[11px] text-slate-500">
                      Supports MP4, MKV, MOV, WebM, AVI (up to 5GB)
                    </p>
                  </div>
                )}
              </div>

              {isUploading && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs text-slate-300">
                    <span>Uploading video file...</span>
                    <span>{uploadProgress}%</span>
                  </div>
                  <Progress value={uploadProgress} className="h-2 bg-slate-800" />
                </div>
              )}

              {uploadFile && !isUploading && (
                <div className="flex items-center justify-end space-x-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setUploadFile(null)}
                    className="h-9 text-xs border-[#1f293d] text-slate-400 hover:text-white"
                  >
                    Clear
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleUploadFile}
                    className="h-9 px-5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold"
                  >
                    <Upload className="w-3.5 h-3.5 mr-1.5" />
                    <span>Upload to Library</span>
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* Analysis Timing Mode (Full Video vs Timestamp Range) */}
          <div className="pt-2 border-t border-[#1f293d]/70 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-300">Analysis Segment</span>
              <div className="flex items-center space-x-2 bg-[#0d131f] p-1 rounded-lg border border-[#1f293d]">
                <button
                  type="button"
                  onClick={() => setTimingMode("full")}
                  className={cn(
                    "text-xs px-2.5 py-1 rounded transition-colors font-medium",
                    timingMode === "full" ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white"
                  )}
                >
                  Full Video
                </button>
                <button
                  type="button"
                  onClick={() => setTimingMode("range")}
                  className={cn(
                    "text-xs px-2.5 py-1 rounded transition-colors font-medium",
                    timingMode === "range" ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white"
                  )}
                >
                  Timestamp Range
                </button>
              </div>
            </div>

            {timingMode === "range" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-3 rounded-lg bg-[#0d131f] border border-[#1f293d]">
                <div className="space-y-1">
                  <label className="text-[11px] font-medium text-slate-400">Start Time</label>
                  <Input
                    type="text"
                    placeholder="e.g. 5:30 or 01:05:30"
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    className="h-8 text-xs bg-[#121826] border-slate-700 text-white"
                  />
                  <p className="text-[10px] text-slate-500">Skip video before this timestamp</p>
                </div>

                <div className="space-y-1">
                  <label className="text-[11px] font-medium text-slate-400">End Time</label>
                  <Input
                    type="text"
                    placeholder="e.g. 12:45 or 01:30:00"
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                    className="h-8 text-xs bg-[#121826] border-slate-700 text-white"
                  />
                  <p className="text-[10px] text-slate-500">Stop analysis after this timestamp</p>
                </div>
              </div>
            )}
          </div>

          {/* Sliders Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-2 border-t border-[#1f293d]/70">
            {/* Number of Clips */}
            <div className="space-y-2">
              <div className="flex justify-between items-center text-xs">
                <span className="font-medium text-slate-300 flex items-center space-x-1.5">
                  <Sliders className="w-3.5 h-3.5 text-blue-400" />
                  <span>Clips to Extract</span>
                </span>
                <span className="font-mono text-blue-400 font-semibold">{numClips} clips</span>
              </div>
              <Slider
                min={1}
                max={10}
                step={1}
                value={[numClips]}
                onValueChange={(val) => setNumClips(val[0])}
              />
            </div>

            {/* Target Duration */}
            <div className="space-y-2">
              <div className="flex justify-between items-center text-xs">
                <span className="font-medium text-slate-300 flex items-center space-x-1.5">
                  <Clock className="w-3.5 h-3.5 text-blue-400" />
                  <span>Target Duration</span>
                </span>
                <span className="font-mono text-blue-400 font-semibold">{duration}s</span>
              </div>
              <Slider
                min={15}
                max={90}
                step={5}
                value={[duration]}
                onValueChange={(val) => setDuration(val[0])}
              />
            </div>
          </div>

          {/* Skip Intro / Outro Toggle */}
          <div className="pt-2 border-t border-[#1f293d]/70 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="text-xs font-medium text-slate-200">Skip Intro / Outro</span>
                <p className="text-[11px] text-slate-400">
                  Ignore beginning and ending minutes of the video to avoid sponsor segments.
                </p>
              </div>
              <Switch
                checked={skipIntroOutro}
                onCheckedChange={setSkipIntroOutro}
              />
            </div>

            {skipIntroOutro && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 p-3 rounded-lg bg-[#0d131f] border border-[#1f293d]">
                <div className="space-y-1.5">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-slate-400">Skip Intro</span>
                    <span className="font-mono text-blue-400">{skipIntroMinutes}m</span>
                  </div>
                  <Slider
                    min={1}
                    max={10}
                    step={1}
                    value={[skipIntroMinutes]}
                    onValueChange={(val) => setSkipIntroMinutes(val[0])}
                  />
                </div>

                <div className="space-y-1.5">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-slate-400">Skip Outro</span>
                    <span className="font-mono text-blue-400">{skipOutroMinutes}m</span>
                  </div>
                  <Slider
                    min={1}
                    max={10}
                    step={1}
                    value={[skipOutroMinutes]}
                    onValueChange={(val) => setSkipOutroMinutes(val[0])}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

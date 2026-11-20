import React, { useState, useEffect } from "react"
import { Youtube, Search, Sparkles, Sliders, Clock, Film, Loader2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { fetchVideoInfo } from "@/api"
import { toast } from "sonner"

export function ImportForm({ onStartAnalysis, isAnalyzing }) {
  const [url, setUrl] = useState("")
  const [videoMeta, setVideoMeta] = useState(null)
  const [metaLoading, setMetaLoading] = useState(false)

  // Options
  const [numClips, setNumClips] = useState(5)
  const [duration, setDuration] = useState(55)
  const [skipIntroOutro, setSkipIntroOutro] = useState(false)
  const [skipIntroMinutes, setSkipIntroMinutes] = useState(1)
  const [skipOutroMinutes, setSkipOutroMinutes] = useState(1)

  // Auto fetch video metadata when url is pasted
  useEffect(() => {
    const trimmed = url.trim()
    const isYt = /(?:youtube\.com\/(?:watch\?|shorts\/|live\/)|youtu\.be\/)/i.test(trimmed)
    if (!isYt) {
      setVideoMeta(null)
      return
    }

    const timer = setTimeout(async () => {
      setMetaLoading(true)
      try {
        const info = await fetchVideoInfo(trimmed)
        setVideoMeta(info)
      } catch (err) {
        // Silently fail on typing
      } finally {
        setMetaLoading(false)
      }
    }, 500)

    return () => clearTimeout(timer)
  }, [url])

  const handleSubmit = (e) => {
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
    })
  }

  return (
    <Card className="border-[#1f293d] bg-[#121826]/90 shadow-xl">
      <CardHeader>
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded-lg bg-red-600/20 text-red-400 flex items-center justify-center border border-red-500/30">
            <Youtube className="w-4 h-4" />
          </div>
          <div>
            <CardTitle className="text-base text-white">Import & Analyze YouTube Video</CardTitle>
            <CardDescription className="text-xs text-slate-400">
              Download any YouTube video, detect peak viral moments with AI, and extract clips.
            </CardDescription>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Main URL Input */}
          <div className="flex flex-col sm:flex-row gap-2.5">
            <div className="relative flex-1">
              <Youtube className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-red-500" />
              <Input
                type="url"
                placeholder="Paste a YouTube URL (e.g. https://www.youtube.com/watch?v=...)"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={isAnalyzing}
                className="pl-9 h-10 border-[#1f293d] bg-[#0d131f] text-sm text-white focus-visible:ring-blue-500"
              />
            </div>
            <Button
              type="submit"
              disabled={isAnalyzing || !url.trim()}
              className="h-10 px-6 bg-blue-600 hover:bg-blue-500 text-white font-semibold flex items-center justify-center space-x-2 shadow"
            >
              {isAnalyzing ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Analyzing...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  <span>Analyze Video</span>
                </>
              )}
            </Button>
          </div>

          {/* Quick Preview Card */}
          {metaLoading && (
            <div className="flex items-center space-x-2 text-xs text-slate-400 p-3 rounded-lg bg-[#0d131f] border border-[#1f293d]">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" />
              <span>Fetching YouTube video preview...</span>
            </div>
          )}

          {videoMeta && !metaLoading && (
            <div className="p-3.5 rounded-lg bg-[#0d131f] border border-[#1f293d] flex flex-col sm:flex-row gap-3.5 items-start sm:items-center">
              {videoMeta.thumbnail_url && (
                <img
                  src={videoMeta.thumbnail_url}
                  alt={videoMeta.title}
                  className="w-full sm:w-36 h-20 object-cover rounded-md border border-[#1f293d]"
                />
              )}
              <div className="flex-1 space-y-1 min-w-0">
                <h4 className="text-sm font-semibold text-white truncate">{videoMeta.title}</h4>
                <div className="flex items-center space-x-2 text-xs text-slate-400">
                  <span className="font-medium text-slate-300">{videoMeta.channel}</span>
                  <span>•</span>
                  <span>{Math.round((videoMeta.duration || 0) / 60)} mins</span>
                  {videoMeta.view_count > 0 && (
                    <>
                      <span>•</span>
                      <span>{videoMeta.view_count.toLocaleString()} views</span>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Analysis Options Accordion / Sliders */}
          <div className="pt-2 grid grid-cols-1 md:grid-cols-2 gap-5 border-t border-[#1f293d]/80">
            {/* Number of clips */}
            <div className="space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-medium text-slate-300 flex items-center space-x-1.5">
                  <Film className="w-3.5 h-3.5 text-blue-400" />
                  <span>Clips to Extract</span>
                </span>
                <span className="font-semibold text-white">{numClips} clips</span>
              </div>
              <Slider
                value={[numClips]}
                min={1}
                max={10}
                step={1}
                onValueChange={([val]) => setNumClips(val)}
                disabled={isAnalyzing}
              />
            </div>

            {/* Clip duration */}
            <div className="space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-medium text-slate-300 flex items-center space-x-1.5">
                  <Clock className="w-3.5 h-3.5 text-blue-400" />
                  <span>Target Duration</span>
                </span>
                <span className="font-semibold text-white">{duration}s</span>
              </div>
              <Slider
                value={[duration]}
                min={15}
                max={60}
                step={5}
                onValueChange={([val]) => setDuration(val)}
                disabled={isAnalyzing}
              />
            </div>

            {/* Skip intro / outro toggle */}
            <div className="md:col-span-2 pt-1 flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="text-xs font-medium text-slate-200">Skip Intro / Outro</span>
                <p className="text-[11px] text-slate-400">
                  Ignore the first and last 60 seconds of video to avoid sponsor segments.
                </p>
              </div>
              <Switch
                checked={skipIntroOutro}
                onCheckedChange={setSkipIntroOutro}
                disabled={isAnalyzing}
              />
            </div>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

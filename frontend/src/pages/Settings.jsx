import React, { useState, useEffect, useRef } from "react"
import {
  Save,
  Lock,
  Unlock,
  Plus,
  Trash2,
  Youtube,
  Film,
  HardDrive,
  BrainCircuit,
  Sliders,
  CheckCircle2,
  AlertCircle,
  Globe,
  RotateCcw,
} from "lucide-react"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"
import { resolveChannel, resetQuotaCounter } from "@/api"
import { toast } from "sonner"

const AVAILABLE_LANGUAGES = [
  { code: "en", label: "English" },
  { code: "hi", label: "Hindi" },
  { code: "es", label: "Spanish" },
  { code: "pt", label: "Portuguese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
]

export default function SettingsPage({ settings = {}, onSave, isSaving }) {
  // Lock state: settings cards locked by default to prevent accidental edits
  const [isUnlocked, setIsUnlocked] = useState(false)

  const [formData, setFormData] = useState({
    multi_stream_enabled: false,
    max_concurrent_streams: 2,
    recording_enabled: true,
    recording_quality: "1080p60,1080p,720p60,best",
    shorts_engine: "openshorts",
    openshorts_subtitle_style: "bold",
    openshorts_reframe_mode: "split",
    hook_overlay_enabled: false,
    youtube_privacy_status: "public",
    youtube_min_score: 75,
    youtube_daily_limit: 5,
    youtube_auto_upload: false,
    viral_metadata_enabled: true,
    gemini_model: "gemini-3.1-flash-lite",
    gemini_api_key: "",
    import_max_clips: 5,
    import_clip_duration: 55,
  })

  // Dynamic YouTube Channels rows: [{ id: 'UC...', name: 'CarryMinati', active: true }]
  const [ytChannels, setYtChannels] = useState([])
  const [selectedLanguages, setSelectedLanguages] = useState(["en", "hi"])
  const [isResettingQuota, setIsResettingQuota] = useState(false)
  const resolvedRef = useRef(new Set())

  const handleResetQuota = async () => {
    setIsResettingQuota(true)
    try {
      await resetQuotaCounter()
      toast.success("YouTube quota counter reset to 0.")
    } catch (err) {
      toast.error(`Failed to reset quota: ${err.message || "Unknown error"}`)
    } finally {
      setIsResettingQuota(false)
    }
  }

  useEffect(() => {
    if (settings && Object.keys(settings).length > 0) {
      setFormData((prev) => ({
        ...prev,
        ...settings,
        openshorts_subtitle_style: settings.openshorts_subtitle_style || settings.subtitle_style || prev.openshorts_subtitle_style || "bold",
        openshorts_reframe_mode: settings.openshorts_reframe_mode || settings.reframe_mode || prev.openshorts_reframe_mode || "split",
        gemini_api_key: "",
      }))

      // Parse YouTube channels defensively (string or array)
      const parseList = (val) => {
        if (Array.isArray(val)) return val.map((s) => String(s || "").trim()).filter(Boolean)
        if (typeof val === "string") return val.split(",").map((s) => s.trim()).filter(Boolean)
        return []
      }

      const activeList = parseList(settings.target_youtube_channels)
      const excludedList = parseList(settings.excluded_youtube_channels)
      const combined = [
        ...activeList.map((id) => ({ id, name: id, active: true })),
        ...excludedList.map((id) => ({ id, name: id, active: false })),
      ]
      setYtChannels(combined.length > 0 ? combined : [{ id: "", name: "", active: true }])

      if (settings.target_languages) {
        const langs = parseList(settings.target_languages)
        if (langs.length > 0) {
          setSelectedLanguages(langs)
        }
      }
    }
  }, [settings])

  // Resolve channel names asynchronously
  useEffect(() => {
    (ytChannels || []).forEach((item, idx) => {
      if (item && item.id && item.id.startsWith("UC") && item.name === item.id && !resolvedRef.current.has(item.id)) {
        resolvedRef.current.add(item.id)
        resolveChannel(item.id)
          .then((res) => {
            if (res && res.name && res.name !== item.id) {
              setYtChannels((prev) =>
                (prev || []).map((c, i) => (i === idx ? { ...c, name: res.name } : c))
              )
            }
          })
          .catch(() => {})
      }
    })
  }, [ytChannels])

  const handleAddChannel = () => {
    setYtChannels((prev) => [...(prev || []), { id: "", name: "", active: true }])
  }

  const handleRemoveChannel = (index) => {
    setYtChannels((prev) => (prev || []).filter((_, idx) => idx !== index))
  }

  const handleChannelIdChange = (index, val) => {
    setYtChannels((prev) =>
      (prev || []).map((item, idx) => (idx === index ? { ...item, id: val, name: val } : item))
    )
  }

  const handleChannelToggleActive = (index) => {
    setYtChannels((prev) =>
      (prev || []).map((item, idx) => (idx === index ? { ...item, active: !item.active } : item))
    )
  }

  const handleSave = async (e) => {
    if (e) e.preventDefault()

    const activeYt = (ytChannels || []).filter((c) => c && c.active && c.id && c.id.trim()).map((c) => c.id.trim())
    const excludedYt = (ytChannels || []).filter((c) => c && !c.active && c.id && c.id.trim()).map((c) => c.id.trim())

    const payload = {
      ...formData,
      monitor_platform: "youtube",
      target_youtube_channels: activeYt.join(","),
      excluded_youtube_channels: excludedYt.join(","),
      target_languages: (selectedLanguages || []).join(","),
      subtitle_style: formData.openshorts_subtitle_style || formData.subtitle_style || "bold",
      reframe_mode: formData.openshorts_reframe_mode || formData.reframe_mode || "split",
    }

    try {
      await onSave(payload)
      toast.success("Settings successfully updated!")
    } catch (err) {
      toast.error(err.message || "Failed to save settings.")
    }
  }

  return (
    <div className="space-y-6">
      {/* Top Header with Lock & Save */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center space-x-2">
            <span>System Configuration</span>
          </h2>
          <p className="text-xs text-slate-400">
            Tune target YouTube channels, recording pipeline, video reframing, and AI models.
          </p>
        </div>

        <div className="flex items-center space-x-3">
          {/* Lock / Unlock Toggle */}
          <div className="flex items-center space-x-2 bg-[#121826] border border-[#1f293d] px-3 py-1.5 rounded-lg text-xs">
            {isUnlocked ? (
              <Unlock className="w-3.5 h-3.5 text-amber-400" />
            ) : (
              <Lock className="w-3.5 h-3.5 text-slate-400" />
            )}
            <span className="text-slate-300 font-medium">
              {isUnlocked ? "Unlocked" : "Locked"}
            </span>
            <Switch checked={isUnlocked} onCheckedChange={setIsUnlocked} />
          </div>

          <Button
            onClick={handleSave}
            disabled={isSaving || !isUnlocked}
            className="h-9 px-5 bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs flex items-center space-x-1.5 shadow"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? "Saving..." : "Save Changes"}</span>
          </Button>
        </div>
      </div>

      {/* Settings Navigation Tabs */}
      <Tabs defaultValue="channels" className="space-y-4">
        <TabsList className="bg-[#0f1422] border border-[#1f293d] p-1 flex flex-wrap justify-start h-auto gap-1">
          <TabsTrigger value="channels" className="text-xs">
            <Youtube className="w-3.5 h-3.5 mr-1.5 text-red-500" />
            <span>Channels</span>
          </TabsTrigger>
          <TabsTrigger value="recording" className="text-xs">
            <HardDrive className="w-3.5 h-3.5 mr-1.5 text-cyan-400" />
            <span>Recording</span>
          </TabsTrigger>
          <TabsTrigger value="processing" className="text-xs">
            <Film className="w-3.5 h-3.5 mr-1.5 text-purple-400" />
            <span>Shorts & Subtitles</span>
          </TabsTrigger>
          <TabsTrigger value="youtube" className="text-xs">
            <Youtube className="w-3.5 h-3.5 mr-1.5 text-red-400" />
            <span>YouTube Upload</span>
          </TabsTrigger>
          <TabsTrigger value="advanced" className="text-xs">
            <BrainCircuit className="w-3.5 h-3.5 mr-1.5 text-indigo-400" />
            <span>AI & Advanced</span>
          </TabsTrigger>
        </TabsList>

        {/* Tab 1: Channels */}
        <TabsContent value="channels">
          <Card className="border-[#1f293d] bg-[#121826]/80">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-base text-white">Target YouTube Channels</CardTitle>
                  <CardDescription className="text-xs text-slate-400">
                    Live streams from these channel IDs will be monitored for chat spikes and highlights.
                  </CardDescription>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleAddChannel}
                  disabled={!isUnlocked}
                  className="h-8 text-xs border-[#1f293d] hover:bg-[#1f293d]"
                >
                  <Plus className="w-3.5 h-3.5 mr-1" />
                  <span>Add Channel</span>
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {(ytChannels || []).map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center space-x-2 p-2 rounded-lg bg-[#0d131f] border border-[#1f293d]"
                >
                  <Checkbox
                    checked={item.active}
                    disabled={!isUnlocked}
                    onCheckedChange={() => handleChannelToggleActive(idx)}
                  />
                  <Input
                    placeholder="Channel ID (e.g. UC7cs8q-gJRlGwj4A8OmCmXg)"
                    value={item.id}
                    disabled={!isUnlocked}
                    onChange={(e) => handleChannelIdChange(idx, e.target.value)}
                    className="h-8 text-xs font-mono border-none bg-transparent"
                  />
                  {item.name && item.name !== item.id && (
                    <Badge variant="secondary" className="text-[11px] shrink-0 font-medium">
                      {item.name}
                    </Badge>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleRemoveChannel(idx)}
                    disabled={!isUnlocked}
                    className="h-7 w-7 text-slate-400 hover:text-red-400 shrink-0"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 2: Recording */}
        <TabsContent value="recording">
          <Card className="border-[#1f293d] bg-[#121826]/80">
            <CardHeader>
              <CardTitle className="text-base text-white">Capture & Video Buffer</CardTitle>
              <CardDescription className="text-xs text-slate-400">
                Configure streamlink quality, maximum rolling memory, and segment durations.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <span className="text-xs font-medium text-slate-300">Recording Quality Hierarchy</span>
                  <Input
                    value={formData.recording_quality || "1080p60,1080p,720p60,best"}
                    disabled={!isUnlocked}
                    onChange={(e) => setFormData({ ...formData, recording_quality: e.target.value })}
                    className="h-9 text-xs"
                  />
                  <p className="text-[11px] text-slate-500">Comma-separated streamlink stream selectors.</p>
                </div>

                <div className="space-y-1.5">
                  <span className="text-xs font-medium text-slate-300">Max Concurrent Streams</span>
                  <Input
                    type="number"
                    min="1"
                    max="10"
                    value={formData.max_concurrent_streams || 2}
                    disabled={!isUnlocked}
                    onChange={(e) =>
                      setFormData({ ...formData, max_concurrent_streams: parseInt(e.target.value) || 2 })
                    }
                    className="h-9 text-xs"
                  />
                  <p className="text-[11px] text-slate-500">Max channels recorded at the same time.</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 3: Processing & Subtitles */}
        <TabsContent value="processing">
          <Card className="border-[#1f293d] bg-[#121826]/80">
            <CardHeader>
              <CardTitle className="text-base text-white">Shorts Reframing & Subtitles</CardTitle>
              <CardDescription className="text-xs text-slate-400">
                Configure vertical video layout (facecam/gameplay split) and Whisper subtitle presets.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Reframe mode */}
                <div className="space-y-1.5">
                  <span className="text-xs font-medium text-slate-300">Vertical Reframe Mode</span>
                  <Select
                    value={formData.openshorts_reframe_mode || "split"}
                    disabled={!isUnlocked}
                    onValueChange={(val) => setFormData({ ...formData, openshorts_reframe_mode: val })}
                  >
                    <SelectTrigger className="h-9 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="split">Split Mode (Facecam Top, Gameplay Bottom)</SelectItem>
                      <SelectItem value="wide">Wide Mode (Blurred Background / Letterbox)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Subtitle style */}
                <div className="space-y-1.5">
                  <span className="text-xs font-medium text-slate-300">Subtitle Style</span>
                  <Select
                    value={formData.openshorts_subtitle_style || "bold"}
                    disabled={!isUnlocked}
                    onValueChange={(val) => setFormData({ ...formData, openshorts_subtitle_style: val })}
                  >
                    <SelectTrigger className="h-9 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bold">Bold Karaoke (Word highlighted)</SelectItem>
                      <SelectItem value="hormozi">Hormozi Social (Impact font)</SelectItem>
                      <SelectItem value="clean">Clean Minimalist</SelectItem>
                      <SelectItem value="none">Disabled (No Subtitles)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Hook title overlay toggle */}
              <div className="pt-2 flex items-center justify-between border-t border-[#1f293d]">
                <div className="space-y-0.5">
                  <span className="text-xs font-medium text-slate-200">Burn Hook Title onto Video</span>
                  <p className="text-[11px] text-slate-400">
                    Burn viral hook headline onto the top of the video (Disabled by default for clean video).
                  </p>
                </div>
                <Switch
                  checked={Boolean(formData.hook_overlay_enabled)}
                  disabled={!isUnlocked}
                  onCheckedChange={(val) => setFormData({ ...formData, hook_overlay_enabled: val })}
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 4: YouTube Upload */}
        <TabsContent value="youtube">
          <Card className="border-[#1f293d] bg-[#121826]/80">
            <CardHeader>
              <CardTitle className="text-base text-white">YouTube Upload Preferences</CardTitle>
              <CardDescription className="text-xs text-slate-400">
                Configure default privacy status and upload thresholds.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <span className="text-xs font-medium text-slate-300">Default Privacy Status</span>
                  <Select
                    value={formData.youtube_privacy_status || "public"}
                    disabled={!isUnlocked}
                    onValueChange={(val) => setFormData({ ...formData, youtube_privacy_status: val })}
                  >
                    <SelectTrigger className="h-9 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="public">Public (Immediate reach)</SelectItem>
                      <SelectItem value="unlisted">Unlisted (Private link)</SelectItem>
                      <SelectItem value="private">Private</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <span className="text-xs font-medium text-slate-300">Daily Upload Quota Limit</span>
                  <Input
                    type="number"
                    min="1"
                    max="50"
                    value={formData.youtube_daily_limit || 5}
                    disabled={!isUnlocked}
                    onChange={(e) =>
                      setFormData({ ...formData, youtube_daily_limit: parseInt(e.target.value) || 5 })
                    }
                    className="h-9 text-xs"
                  />
                  <p className="text-[11px] text-slate-500">Max uploads before protecting quota.</p>
                </div>
              </div>

              <div className="pt-2 flex items-center justify-between border-t border-[#1f293d]">
                <div className="space-y-0.5">
                  <span className="text-xs font-medium text-slate-200">Auto-Upload Approved Shorts</span>
                  <p className="text-[11px] text-slate-400">
                    Automatically upload newly rendered Shorts if viral score is above 75.
                  </p>
                </div>
                <Switch
                  checked={Boolean(formData.youtube_auto_upload)}
                  disabled={!isUnlocked}
                  onCheckedChange={(val) => setFormData({ ...formData, youtube_auto_upload: val })}
                />
              </div>

              {/* Reset Quota Counter */}
              <div className="pt-2 flex items-center justify-between border-t border-[#1f293d]">
                <div className="space-y-0.5">
                  <span className="text-xs font-medium text-slate-200">Reset Quota Counter</span>
                  <p className="text-[11px] text-slate-400">
                    Reset today's recorded uploads and quota unit counter back to 0.
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!isUnlocked || isResettingQuota}
                  onClick={handleResetQuota}
                  className="h-8 text-xs border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300"
                >
                  {isResettingQuota ? "Resetting..." : "Reset Counter"}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 5: Advanced & AI */}
        <TabsContent value="advanced">
          <Card className="border-[#1f293d] bg-[#121826]/80">
            <CardHeader>
              <CardTitle className="text-base text-white">AI Highlight & Language Filter</CardTitle>
              <CardDescription className="text-xs text-slate-400">
                Configure Gemini models and language preferences.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="space-y-1.5">
                <span className="text-xs font-medium text-slate-300">Gemini Highlight Model</span>
                <Input
                  value={formData.gemini_model || "gemini-3.1-flash-lite"}
                  disabled={!isUnlocked}
                  onChange={(e) => setFormData({ ...formData, gemini_model: e.target.value })}
                  className="h-9 text-xs"
                />
              </div>

              {/* Languages */}
              <div className="space-y-2 pt-2 border-t border-[#1f293d]">
                <span className="text-xs font-medium text-slate-300 flex items-center space-x-1.5">
                  <Globe className="w-3.5 h-3.5 text-blue-400" />
                  <span>Target Stream Languages</span>
                </span>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-1">
                  {(AVAILABLE_LANGUAGES || []).map((lang) => {
                    const isChecked = (selectedLanguages || []).includes(lang.code)
                    return (
                      <label
                        key={lang.code}
                        className="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer p-2 rounded-lg bg-[#0d131f] border border-[#1f293d]"
                      >
                        <Checkbox
                          checked={isChecked}
                          disabled={!isUnlocked}
                          onCheckedChange={(chk) => {
                            if (chk) {
                              setSelectedLanguages([...(selectedLanguages || []), lang.code])
                            } else {
                              setSelectedLanguages((selectedLanguages || []).filter((c) => c !== lang.code))
                            }
                          }}
                        />
                        <span>{lang.label} ({lang.code})</span>
                      </label>
                    )
                  })}
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

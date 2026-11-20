import React, { useState } from "react"
import {
  Menu,
  Search,
  Youtube,
  Play,
  Square,
  Sparkles,
  Command as CommandIcon,
  Layers,
  LayoutDashboard,
  Download,
  Scissors,
  Film,
  Radio,
  Settings,
  RotateCw,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { cn } from "@/lib/utils"

const TAB_TITLES = {
  dashboard: "Dashboard",
  import: "Import YouTube Video",
  clips: "Extracted Clips",
  shorts: "Shorts Pipeline",
  live: "Live Event Feed",
  settings: "Settings",
}

export function Header({
  activeTab,
  setActiveTab,
  monitoringStatus,
  quota,
  onToggleMonitoring,
  isStarting,
  onOpenCommand,
  queueStatus,
  onOpenQueue,
  onRefreshQuota,
}) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const isRecording = monitoringStatus?.is_monitoring
  const activeCount = (monitoringStatus?.active_channels || []).length
  const processingCount = queueStatus?.processing?.active_count || 0
  const uploadCount = queueStatus?.upload?.active_count || 0
  const totalQueue = processingCount + uploadCount

  return (
    <header className="sticky top-0 z-30 h-16 border-b border-[#1f293d] bg-[#0d121d]/90 backdrop-blur-md px-4 sm:px-6 flex items-center justify-between">
      {/* Left: Mobile trigger & Breadcrumb */}
      <div className="flex items-center space-x-3">
        {/* Mobile Sheet Menu */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" className="md:hidden text-slate-400 hover:text-white">
              <Menu className="w-5 h-5" />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-72 bg-[#0d121d] border-r border-[#1f293d] p-4">
            <SheetHeader className="text-left mb-6">
              <div className="flex items-center space-x-2">
                <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
                  <Sparkles className="w-4 h-4 text-white" />
                </div>
                <SheetTitle className="text-white text-base">LiveClipper</SheetTitle>
              </div>
            </SheetHeader>
            <nav className="space-y-1.5">
              {[
                { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
                { id: "import", label: "Import URL", icon: Download },
                { id: "clips", label: "Clips", icon: Scissors },
                { id: "shorts", label: "Shorts", icon: Film },
                { id: "live", label: "Live Feed", icon: Radio },
                { id: "settings", label: "Settings", icon: Settings },
              ].map((item) => {
                const Icon = item.icon
                const isActive = activeTab === item.id
                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      setActiveTab(item.id)
                      setMobileOpen(false)
                    }}
                    className={cn(
                      "w-full flex items-center space-x-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-blue-600/10 text-blue-400 border border-blue-500/30 font-semibold"
                        : "text-slate-400 hover:text-slate-100 hover:bg-[#151c2c]"
                    )}
                  >
                    <Icon className="w-4 h-4" />
                    <span>{item.label}</span>
                  </button>
                )
              })}
            </nav>
          </SheetContent>
        </Sheet>

        {/* Breadcrumb navigation */}
        <Breadcrumb className="hidden sm:flex">
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink onClick={() => setActiveTab("dashboard")}>LiveClipper</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>{TAB_TITLES[activeTab] || activeTab}</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
      </div>

      {/* Right controls */}
      <div className="flex items-center space-x-3">
        {/* Command palette shortcut button */}
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenCommand}
          className="hidden lg:flex items-center space-x-2 text-xs border-[#1f293d] bg-[#121826] text-slate-400 hover:text-slate-200"
        >
          <Search className="w-3.5 h-3.5" />
          <span>Quick search...</span>
          <kbd className="pointer-events-none inline-flex h-4 select-none items-center gap-1 rounded bg-[#1a2233] px-1.5 font-mono text-[10px] font-medium text-slate-400">
            <span className="text-xs">⌘</span>K
          </kbd>
        </Button>

        {/* Active Queue pill button */}
        {totalQueue > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={onOpenQueue}
            className="flex items-center space-x-1.5 text-xs border-blue-500/30 bg-blue-500/10 text-blue-400 hover:bg-blue-500/20"
          >
            <Layers className="w-3.5 h-3.5 animate-pulse" />
            <span>Queue: {totalQueue}</span>
          </Button>
        )}

        {/* YouTube Quota badge */}
        {(() => {
          const uploads = quota?.uploads_today ?? 0
          const limit = quota?.daily_limit ?? 5
          const isExceeded = quota?.quota_exceeded || uploads >= limit
          const isWarning = !isExceeded && uploads / limit >= 0.8
          const resetsIn = quota?.resets_in_human || ""

          return (
            <div
              className={cn(
                "hidden sm:flex items-center space-x-2 border px-2.5 py-1.5 rounded-lg text-xs transition-colors",
                isExceeded
                  ? "bg-red-500/15 border-red-500/40 text-red-400"
                  : isWarning
                  ? "bg-amber-500/15 border-amber-500/40 text-amber-300"
                  : "bg-[#121826] border-[#1f293d] text-slate-300"
              )}
              title={
                isExceeded
                  ? `Daily quota limit reached (${uploads}/${limit}). ${resetsIn}`
                  : `YouTube Upload Quota: ${uploads}/${limit} used today. ${resetsIn}`
              }
            >
              <Youtube className={cn("w-3.5 h-3.5 shrink-0", isExceeded ? "text-red-400" : "text-red-500")} />
              {isExceeded ? (
                <span className="font-bold text-red-400 tracking-wide text-[11px]">QUOTA EXCEEDED</span>
              ) : (
                <>
                  <span className="text-slate-400">Quota:</span>
                  <span className={cn("font-semibold", isWarning ? "text-amber-300" : "text-slate-200")}>
                    {uploads}/{limit}
                  </span>
                </>
              )}
              {resetsIn && (
                <span className="text-[10px] text-slate-400 hidden xl:inline border-l border-slate-700/60 pl-1.5">
                  {resetsIn}
                </span>
              )}
              {onRefreshQuota && (
                <button
                  type="button"
                  onClick={onRefreshQuota}
                  className="text-slate-400 hover:text-white transition-colors ml-0.5"
                  title="Refresh Quota"
                >
                  <RotateCw className="w-3 h-3" />
                </button>
              )}
            </div>
          )
        })()}

        {/* Start / Stop Monitoring Button */}
        <Button
          onClick={onToggleMonitoring}
          disabled={isStarting}
          size="sm"
          className={cn(
            "flex items-center space-x-2 font-semibold shadow-sm transition-all",
            isRecording
              ? "bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/30"
              : "bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-500/20"
          )}
        >
          {isRecording ? (
            <>
              <Square className="w-3.5 h-3.5 fill-current" />
              <span>Stop ({activeCount})</span>
            </>
          ) : (
            <>
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{isStarting ? "Starting..." : "Start Monitor"}</span>
            </>
          )}
        </Button>
      </div>
    </header>
  )
}

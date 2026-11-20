import React from "react"
import {
  LayoutDashboard,
  Download,
  Scissors,
  Film,
  Radio,
  Settings,
  ChevronLeft,
  ChevronRight,
  Layers,
  Sparkles,
  Youtube
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "import", label: "Import URL", icon: Download, badge: "New" },
  { id: "clips", label: "Clips", icon: Scissors },
  { id: "shorts", label: "Shorts", icon: Film },
  { id: "live", label: "Live Feed", icon: Radio },
  { id: "settings", label: "Settings", icon: Settings },
]

export function Sidebar({
  activeTab,
  setActiveTab,
  collapsed,
  setCollapsed,
  monitoringStatus,
  queueStatus,
  onOpenQueue,
}) {
  const isRecording = monitoringStatus?.is_monitoring
  const activeCount = (monitoringStatus?.active_channels || []).length
  const processingCount = queueStatus?.processing?.active_count || 0
  const uploadCount = queueStatus?.upload?.active_count || 0
  const totalQueue = processingCount + uploadCount

  return (
    <aside
      className={cn(
        "hidden md:flex flex-col border-r border-[#1f293d] bg-[#0d121d] transition-all duration-300 select-none z-30 shrink-0",
        collapsed ? "w-16" : "w-64"
      )}
    >
      {/* Brand header */}
      <div className="h-16 flex items-center justify-between px-4 border-b border-[#1f293d]">
        <div
          className="flex items-center space-x-3 cursor-pointer overflow-hidden"
          onClick={() => setActiveTab("dashboard")}
        >
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 via-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-blue-500/20 shrink-0">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          {!collapsed && (
            <div className="flex flex-col min-w-0">
              <div className="flex items-center space-x-1.5">
                <span className="font-bold text-base tracking-tight text-white">LiveClipper</span>
                <span className="text-[10px] px-1.5 py-0.2 rounded font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20">
                  v2.0
                </span>
              </div>
              <span className="text-[11px] text-slate-400 truncate -mt-0.5">AI Shorts Pipeline</span>
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setCollapsed(!collapsed)}
          className="h-7 w-7 text-slate-400 hover:text-white shrink-0"
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </Button>
      </div>

      {/* Navigation links */}
      <TooltipProvider delayDuration={150}>
        <nav className="flex-1 px-2 py-4 space-y-1.5 overflow-y-auto">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const isActive = activeTab === item.id

            const navBtn = (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={cn(
                  "w-full flex items-center rounded-lg px-3 py-2 text-sm font-medium transition-colors relative group",
                  isActive
                    ? "bg-blue-600/10 text-blue-400 border border-blue-500/30 font-semibold"
                    : "text-slate-400 hover:text-slate-100 hover:bg-[#151c2c]",
                  collapsed ? "justify-center px-0" : "justify-between"
                )}
              >
                <div className="flex items-center space-x-3">
                  <Icon
                    className={cn(
                      "w-4 h-4 shrink-0 transition-transform group-hover:scale-110",
                      isActive ? "text-blue-400" : "text-slate-400"
                    )}
                  />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </div>
                {!collapsed && item.badge && (
                  <Badge variant="purple" className="text-[10px] px-1.5 py-0 uppercase">
                    {item.badge}
                  </Badge>
                )}
              </button>
            )

            if (collapsed) {
              return (
                <Tooltip key={item.id}>
                  <TooltipTrigger asChild>{navBtn}</TooltipTrigger>
                  <TooltipContent side="right">
                    <p>{item.label}</p>
                  </TooltipContent>
                </Tooltip>
              )
            }
            return navBtn
          })}
        </nav>
      </TooltipProvider>

      <Separator className="bg-[#1f293d]" />

      {/* Bottom status & queue */}
      <div className="p-3 space-y-2">
        {/* Queue quick pill */}
        {totalQueue > 0 && (
          <button
            onClick={onOpenQueue}
            className={cn(
              "w-full flex items-center rounded-lg p-2 bg-[#121826] border border-[#1f293d] hover:border-blue-500/50 transition-colors text-xs text-slate-300",
              collapsed ? "justify-center" : "justify-between"
            )}
          >
            <div className="flex items-center space-x-2">
              <Layers className="w-3.5 h-3.5 text-blue-400 animate-pulse" />
              {!collapsed && <span>Active Queue</span>}
            </div>
            {!collapsed && (
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {totalQueue}
              </Badge>
            )}
          </button>
        )}

        {/* Live status badge */}
        {!collapsed && (
          <div className="flex items-center justify-between px-2 py-1.5 rounded-lg bg-[#080b12] border border-[#1a2233] text-[11px]">
            <div className="flex items-center space-x-2">
              <span
                className={cn(
                  "w-2 h-2 rounded-full",
                  isRecording ? "bg-emerald-500 animate-pulse" : "bg-slate-500"
                )}
              />
              <span className="text-slate-400">
                {isRecording ? `RECORDING (${activeCount})` : "MONITOR IDLE"}
              </span>
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

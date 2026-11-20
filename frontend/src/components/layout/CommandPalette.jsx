import React, { useEffect } from "react"
import {
  LayoutDashboard,
  Download,
  Scissors,
  Film,
  Radio,
  Settings,
  Play,
  Square,
  Layers,
  RefreshCw,
} from "lucide-react"
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
} from "@/components/ui/command"

export function CommandPalette({
  open,
  onOpenChange,
  onNavigate,
  onToggleMonitoring,
  isMonitoring,
  onCheckLive,
  onOpenQueue,
}) {
  useEffect(() => {
    const down = (e) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        onOpenChange(!open)
      }
    }
    document.addEventListener("keydown", down)
    return () => document.removeEventListener("keydown", down)
  }, [open, onOpenChange])

  const runCommand = (action) => {
    onOpenChange(false)
    action()
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Type a command or search..." />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>

        <CommandGroup heading="Navigation">
          <CommandItem onSelect={() => runCommand(() => onNavigate("dashboard"))}>
            <LayoutDashboard className="mr-2 h-4 w-4 text-blue-400" />
            <span>Dashboard</span>
          </CommandItem>
          <CommandItem onSelect={() => runCommand(() => onNavigate("import"))}>
            <Download className="mr-2 h-4 w-4 text-indigo-400" />
            <span>Import YouTube Video</span>
          </CommandItem>
          <CommandItem onSelect={() => runCommand(() => onNavigate("clips"))}>
            <Scissors className="mr-2 h-4 w-4 text-emerald-400" />
            <span>Clips</span>
          </CommandItem>
          <CommandItem onSelect={() => runCommand(() => onNavigate("shorts"))}>
            <Film className="mr-2 h-4 w-4 text-purple-400" />
            <span>Shorts Pipeline</span>
          </CommandItem>
          <CommandItem onSelect={() => runCommand(() => onNavigate("live"))}>
            <Radio className="mr-2 h-4 w-4 text-red-400" />
            <span>Live Feed</span>
          </CommandItem>
          <CommandItem onSelect={() => runCommand(() => onNavigate("settings"))}>
            <Settings className="mr-2 h-4 w-4 text-slate-400" />
            <span>Settings</span>
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Stream & Operations">
          <CommandItem onSelect={() => runCommand(onToggleMonitoring)}>
            {isMonitoring ? (
              <>
                <Square className="mr-2 h-4 w-4 text-red-400 fill-current" />
                <span>Stop Stream Monitoring</span>
              </>
            ) : (
              <>
                <Play className="mr-2 h-4 w-4 text-emerald-400 fill-current" />
                <span>Start Stream Monitoring</span>
              </>
            )}
          </CommandItem>
          <CommandItem onSelect={() => runCommand(onCheckLive)}>
            <RefreshCw className="mr-2 h-4 w-4 text-cyan-400" />
            <span>Check Channel Live Status</span>
          </CommandItem>
          <CommandItem onSelect={() => runCommand(onOpenQueue)}>
            <Layers className="mr-2 h-4 w-4 text-blue-400" />
            <span>View Background Queue</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}

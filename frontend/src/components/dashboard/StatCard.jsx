import React from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Info } from "lucide-react"
import { cn } from "@/lib/utils"

export function StatCard({
  title,
  value,
  subtext,
  icon: Icon,
  badge,
  badgeVariant = "secondary",
  tooltip,
  className,
}) {
  return (
    <Card className={cn("border-[#1f293d] bg-[#121826]/80 relative overflow-hidden", className)}>
      <CardContent className="p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-1.5 text-xs font-medium text-slate-400">
            <span>{title}</span>
            {tooltip && (
              <TooltipProvider delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Info className="w-3.5 h-3.5 text-slate-500 hover:text-slate-300 cursor-pointer" />
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="max-w-xs text-xs">{tooltip}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
          </div>
          {Icon && (
            <div className="w-8 h-8 rounded-lg bg-blue-600/10 text-blue-400 flex items-center justify-center border border-blue-500/20">
              <Icon className="w-4 h-4" />
            </div>
          )}
        </div>

        <div className="space-y-1">
          <div className="flex items-baseline space-x-2">
            <span className="text-2xl font-bold tracking-tight text-white">{value}</span>
            {badge && (
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">
                {badge}
              </span>
            )}
          </div>
          {subtext && <p className="text-xs text-slate-400">{subtext}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

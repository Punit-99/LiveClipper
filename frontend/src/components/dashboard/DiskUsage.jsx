import React from "react"
import { HardDrive, AlertTriangle } from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"

export function DiskUsage({ diskStats }) {
  const usedMb = diskStats?.used_mb || 0
  const maxMb = diskStats?.max_mb || 1500
  const pct = Math.min(100, Math.round((usedMb / (maxMb || 1)) * 100))
  const isHigh = pct >= 80

  return (
    <Card className="border-[#1f293d] bg-[#121826]/80">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="flex items-center space-x-2">
          <HardDrive className="w-4 h-4 text-cyan-400" />
          <CardTitle className="text-sm font-semibold text-white">Rolling Video Buffer</CardTitle>
        </div>
        <span className="text-xs font-mono text-slate-300">
          {usedMb}MB / {maxMb}MB
        </span>
      </CardHeader>

      <CardContent className="space-y-2">
        <Progress
          value={pct}
          className="h-2"
          indicatorClassName={isHigh ? "bg-amber-500" : "bg-cyan-500"}
        />
        <div className="flex justify-between text-[11px] text-slate-400">
          <span>Circular FIFO buffer</span>
          <span>{pct}% capacity</span>
        </div>
      </CardContent>
    </Card>
  )
}

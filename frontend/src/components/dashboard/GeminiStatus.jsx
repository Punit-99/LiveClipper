import React from "react"
import { BrainCircuit, CheckCircle2, AlertCircle } from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

export function GeminiStatus({ stats, settings }) {
  const model = settings?.gemini_model || "gemini-3.1-flash-lite"
  const analyzedCount = stats?.gemini_analyzed || 0
  const approvedCount = stats?.clips_extracted || 0
  const rateLimitCount = stats?.gemini_rate_limits || 0

  return (
    <Card className="border-[#1f293d] bg-[#121826]/80">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="flex items-center space-x-2">
          <BrainCircuit className="w-4 h-4 text-purple-400" />
          <CardTitle className="text-sm font-semibold text-white">Gemini Intelligence</CardTitle>
        </div>
        <Badge variant="purple" className="text-[10px]">
          {model}
        </Badge>
      </CardHeader>

      <CardContent>
        <div className="grid grid-cols-3 gap-2 text-center pt-1">
          <div className="p-2 rounded-lg bg-[#0d131f] border border-[#1f293d]">
            <span className="block text-base font-bold text-white">{analyzedCount}</span>
            <span className="text-[10px] text-slate-400">Events Analyzed</span>
          </div>
          <div className="p-2 rounded-lg bg-[#0d131f] border border-[#1f293d]">
            <span className="block text-base font-bold text-emerald-400">{approvedCount}</span>
            <span className="text-[10px] text-slate-400">Approved Clips</span>
          </div>
          <div className="p-2 rounded-lg bg-[#0d131f] border border-[#1f293d]">
            <span className="block text-base font-bold text-amber-400">{rateLimitCount}</span>
            <span className="text-[10px] text-slate-400">Rate Limited</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

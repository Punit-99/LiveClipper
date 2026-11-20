import React from "react"
import { Radio, Users, Globe, ExternalLink, RefreshCw, CheckCircle2 } from "lucide-react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"

export function StreamStatus({
  channels = [],
  isMonitoring,
  onCheckLiveStatus,
  isCheckingLive,
}) {
  return (
    <Card className="border-[#1f293d] bg-[#121826]/80">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <div className="flex items-center space-x-2">
          <Radio className="w-4 h-4 text-emerald-400" />
          <CardTitle className="text-base text-white">Target Channels</CardTitle>
          <Badge variant="outline" className="text-xs">
            {channels.length} Configured
          </Badge>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onCheckLiveStatus}
          disabled={isCheckingLive}
          className="h-8 text-xs border-[#1f293d] hover:bg-[#1a2233] space-x-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isCheckingLive ? "animate-spin" : ""}`} />
          <span>Check Live Status</span>
        </Button>
      </CardHeader>

      <CardContent>
        {channels.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-500">
            No target channels configured in Settings.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {channels.map((ch) => {
              const name = ch.display_name || ch.name || ch.channel_id || "Channel"
              const isLive = Boolean(ch.is_live || ch.live)
              const viewers = ch.viewer_count || ch.viewers || 0

              return (
                <div
                  key={ch.channel_id || ch.name}
                  className="p-3 rounded-lg border border-[#1f293d] bg-[#0d131f] flex items-center justify-between"
                >
                  <div className="flex items-center space-x-3 min-w-0">
                    <Avatar className="w-8 h-8 rounded-lg">
                      <AvatarFallback className="bg-[#1a2233] text-[11px] font-bold text-slate-300">
                        {name.slice(0, 2).toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                    <div className="min-w-0">
                      <h5 className="text-xs font-semibold text-white truncate">{name}</h5>
                      <span className="text-[11px] text-slate-400">
                        {isLive ? `${viewers.toLocaleString()} viewers` : "Offline"}
                      </span>
                    </div>
                  </div>

                  <Badge variant={isLive ? "success" : "secondary"} className="text-[10px] shrink-0">
                    {isLive ? "LIVE" : "OFFLINE"}
                  </Badge>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

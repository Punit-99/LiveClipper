import React, { useState } from 'react';
import { 
  Play, 
  Square, 
  HardDrive, 
  BrainCircuit, 
  Youtube, 
  Video, 
  Film, 
  Sparkles, 
  CheckCircle2, 
  AlertTriangle,
  Clock,
  Radio,
  ExternalLink,
  RefreshCw
} from 'lucide-react';
import { formatBytes, formatDuration } from '../api';
import { formatChannelName } from '../utils/channels';

export default function Dashboard({ 
  status, 
  quota, 
  highlights = [], 
  clips = [], 
  shorts = [], 
  onStartMonitoring, 
  onStopMonitoring, 
  onNavigate,
  onRefresh 
}) {
  const [platformOverride, setPlatformOverride] = useState('');
  const [channelsInput, setChannelsInput] = useState('');

  const isMonitoring = status?.is_monitoring;
  const activeChannels = status?.active_channels || [];
  const targetChannels = status?.target_channels || [];
  const uptime = status?.uptime_seconds || 0;

  // Disk breakdown
  const disk = status?.disk || {};
  const clipsBytes = disk.clips_bytes || 0;
  const bufferBytes = disk.buffer_bytes || 0;
  const shortsBytes = disk.shorts_bytes || 0;
  const totalDiskBytes = disk.total_used_bytes || 0;
  const freeDiskBytes = disk.disk_free_bytes || 0;
  const totalSystemDisk = disk.disk_total_bytes || (totalDiskBytes + freeDiskBytes) || 1;

  const bufferPercent = Math.min(100, Math.round((bufferBytes / totalSystemDisk) * 100)) || 1;
  const clipsPercent = Math.min(100, Math.round((clipsBytes / totalSystemDisk) * 100)) || 1;
  const shortsPercent = Math.min(100, Math.round((shortsBytes / totalSystemDisk) * 100)) || 1;

  // Gemini stats
  const gemini = status?.gemini || {};

  const handleStart = () => {
    const payload = {};
    if (platformOverride) payload.platform = platformOverride;
    if (channelsInput.trim()) {
      payload.channels = channelsInput.split(',').map(c => c.trim()).filter(Boolean);
    }
    onStartMonitoring(payload);
  };

  return (
    <div className="space-y-6">
      {/* Top Banner & Quick Controls */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Monitoring Status Card */}
        <div className="lg:col-span-2 bg-[#121722] border border-[#1f293d] rounded-2xl p-6 relative overflow-hidden">
          <div className="absolute -right-12 -top-12 w-48 h-48 bg-blue-500/5 rounded-full blur-3xl pointer-events-none" />
          
          <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
            <div className="flex items-center space-x-3">
              <div className={`p-3 rounded-xl ${isMonitoring ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-400'}`}>
                <Radio className={`w-6 h-6 ${isMonitoring ? 'animate-pulse' : ''}`} />
              </div>
              <div>
                <div className="flex items-center space-x-2">
                  <h2 className="text-xl font-bold text-white">Stream Ingestion Engine</h2>
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-bold uppercase ${
                    isMonitoring 
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                      : 'bg-slate-700/50 text-slate-400 border border-slate-700'
                  }`}>
                    {isMonitoring ? 'Active' : 'Stopped'}
                  </span>
                </div>
                <p className="text-xs text-slate-400">
                  {isMonitoring ? `Running for ${formatDuration(uptime)}` : 'Multi-stream monitoring is idle'}
                </p>
              </div>
            </div>

            <div className="flex items-center space-x-2">
              <button
                onClick={onRefresh}
                className="p-2 text-slate-400 hover:text-white bg-[#1a2233] hover:bg-[#25324d] rounded-lg transition-colors border border-[#1f293d]"
                title="Refresh stats"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
              {isMonitoring ? (
                <button
                  onClick={onStopMonitoring}
                  className="flex items-center space-x-2 px-5 py-2.5 bg-red-600 hover:bg-red-500 text-white font-semibold rounded-xl text-sm transition-all shadow-lg shadow-red-500/20"
                >
                  <Square className="w-4 h-4 fill-current" />
                  <span>Stop Monitoring</span>
                </button>
              ) : (
                <button
                  onClick={handleStart}
                  className="flex items-center space-x-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold rounded-xl text-sm transition-all shadow-lg shadow-emerald-500/20"
                >
                  <Play className="w-4 h-4 fill-current" />
                  <span>Start Monitoring</span>
                </button>
              )}
            </div>
          </div>

          {/* Active channels pills */}
          <div className="space-y-3 pt-2 border-t border-[#1f293d]/60">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>Target Channels ({activeChannels.length ? activeChannels.length : targetChannels.length})</span>
              <span>Platform: <span className="font-semibold text-slate-200 capitalize">{status?.platform || 'Twitch'}</span></span>
            </div>

            <div className="flex flex-wrap gap-2">
              {(activeChannels.length > 0 ? activeChannels : targetChannels).map((ch, idx) => (
                <div
                  key={idx}
                  className="flex items-center space-x-2 px-3 py-1.5 rounded-lg bg-[#182030] border border-[#24334f] text-xs font-medium text-slate-200"
                >
                  <span className={`w-2 h-2 rounded-full ${isMonitoring ? 'bg-emerald-400' : 'bg-slate-500'}`} />
                  <span>{formatChannelName(ch)}</span>
                </div>
              ))}
              {!activeChannels.length && !targetChannels.length && (
                <span className="text-xs text-slate-500 italic">No target channels configured in .env</span>
              )}
            </div>
          </div>
        </div>

        {/* YouTube Quota & Status Card */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center space-x-2 text-red-400 font-bold">
                <Youtube className="w-5 h-5" />
                <span>YouTube Quota</span>
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-md font-semibold ${
                quota?.can_upload ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
              }`}>
                {quota?.can_upload ? 'Ready' : 'Exceeded'}
              </span>
            </div>

            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-xs text-slate-400 mb-1.5">
                  <span>Daily Uploads Limit</span>
                  <span className="font-bold text-slate-200">
                    {quota?.uploads_today || 0} / {quota?.daily_limit || 5}
                  </span>
                </div>
                <div className="w-full h-2.5 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-red-500 transition-all duration-500"
                    style={{ width: `${Math.min(100, ((quota?.uploads_today || 0) / (quota?.daily_limit || 5)) * 100)}%` }}
                  />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-xs text-slate-400 mb-1.5">
                  <span>API Units Used</span>
                  <span className="font-bold text-slate-200">
                    {quota?.units_used || 0} / {quota?.max_units || 10000}
                  </span>
                </div>
                <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-amber-500 transition-all duration-500"
                    style={{ width: `${Math.min(100, ((quota?.units_used || 0) / (quota?.max_units || 10000)) * 100)}%` }}
                  />
                </div>
              </div>
            </div>
          </div>

          <button
            onClick={() => onNavigate('shorts')}
            className="w-full mt-5 py-2.5 bg-[#1a2233] hover:bg-[#222e45] text-slate-200 text-xs font-semibold rounded-xl border border-[#24334f] transition-all flex items-center justify-center space-x-2"
          >
            <span>Review Pending Shorts</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* 4 Metrics Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-[#121722] border border-[#1f293d] rounded-xl p-5 flex items-center space-x-4">
          <div className="w-12 h-12 rounded-xl bg-purple-500/10 text-purple-400 border border-purple-500/20 flex items-center justify-center">
            <Sparkles className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-white">{highlights.length}</div>
            <div className="text-xs text-slate-400">Highlights Detected</div>
          </div>
        </div>

        <div className="bg-[#121722] border border-[#1f293d] rounded-xl p-5 flex items-center space-x-4">
          <div className="w-12 h-12 rounded-xl bg-blue-500/10 text-blue-400 border border-blue-500/20 flex items-center justify-center">
            <Video className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-white">{clips.length}</div>
            <div className="text-xs text-slate-400">Clips Extracted</div>
          </div>
        </div>

        <div className="bg-[#121722] border border-[#1f293d] rounded-xl p-5 flex items-center space-x-4">
          <div className="w-12 h-12 rounded-xl bg-red-500/10 text-red-400 border border-red-500/20 flex items-center justify-center">
            <Film className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-white">{shorts.length}</div>
            <div className="text-xs text-slate-400">Vertical Shorts Ready</div>
          </div>
        </div>

        <div className="bg-[#121722] border border-[#1f293d] rounded-xl p-5 flex items-center space-x-4">
          <div className="w-12 h-12 rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center justify-center">
            <BrainCircuit className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-white">{gemini.analyzed || 0}</div>
            <div className="text-xs text-slate-400">AI Analyses Completed</div>
          </div>
        </div>
      </div>

      {/* Disk Usage & Gemini Status */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Disk Usage Breakdown */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-2 text-white font-bold">
              <HardDrive className="w-5 h-5 text-blue-400" />
              <span>Disk Space Utilization</span>
            </div>
            <span className="text-xs text-slate-400">
              Total Used: <strong className="text-slate-200">{formatBytes(totalDiskBytes)}</strong>
            </span>
          </div>

          {/* Segmented bar */}
          <div className="w-full h-3 bg-slate-800 rounded-full overflow-hidden flex mb-4">
            <div className="bg-amber-500 transition-all" style={{ width: `${bufferPercent}%` }} title="Rolling Buffer (.buffer/)" />
            <div className="bg-blue-500 transition-all" style={{ width: `${clipsPercent}%` }} title="Source Clips (clips/)" />
            <div className="bg-red-500 transition-all" style={{ width: `${shortsPercent}%` }} title="Shorts (shorts/)" />
          </div>

          {/* Legend */}
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="bg-[#182030] p-2.5 rounded-lg border border-[#24334f]">
              <div className="flex items-center space-x-1.5 text-amber-400 font-semibold mb-1">
                <span className="w-2 h-2 rounded-full bg-amber-400" />
                <span>Rolling Buffer</span>
              </div>
              <div className="text-slate-300 font-bold">{formatBytes(bufferBytes)}</div>
            </div>

            <div className="bg-[#182030] p-2.5 rounded-lg border border-[#24334f]">
              <div className="flex items-center space-x-1.5 text-blue-400 font-semibold mb-1">
                <span className="w-2 h-2 rounded-full bg-blue-400" />
                <span>Source Clips</span>
              </div>
              <div className="text-slate-300 font-bold">{formatBytes(clipsBytes)}</div>
            </div>

            <div className="bg-[#182030] p-2.5 rounded-lg border border-[#24334f]">
              <div className="flex items-center space-x-1.5 text-red-400 font-semibold mb-1">
                <span className="w-2 h-2 rounded-full bg-red-400" />
                <span>Shorts</span>
              </div>
              <div className="text-slate-300 font-bold">{formatBytes(shortsBytes)}</div>
            </div>
          </div>
        </div>

        {/* Gemini AI Status */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-2 text-white font-bold">
              <BrainCircuit className="w-5 h-5 text-indigo-400" />
              <span>Gemini AI Worker Status</span>
            </div>
            <span className="text-xs px-2 py-0.5 rounded-md font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              {gemini.model || 'gemini-3.1-flash-lite'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs mb-4">
            <div className="bg-[#182030] p-3 rounded-lg border border-[#24334f] flex justify-between items-center">
              <span className="text-slate-400">Submitted</span>
              <span className="text-slate-200 font-bold text-sm">{gemini.submitted || 0}</span>
            </div>
            <div className="bg-[#182030] p-3 rounded-lg border border-[#24334f] flex justify-between items-center">
              <span className="text-slate-400">Analyzed</span>
              <span className="text-emerald-400 font-bold text-sm">{gemini.analyzed || 0}</span>
            </div>
            <div className="bg-[#182030] p-3 rounded-lg border border-[#24334f] flex justify-between items-center">
              <span className="text-slate-400">Failed / Retried</span>
              <span className="text-red-400 font-bold text-sm">{gemini.failed || 0}</span>
            </div>
            <div className="bg-[#182030] p-3 rounded-lg border border-[#24334f] flex justify-between items-center">
              <span className="text-slate-400">Queue Depth</span>
              <span className="text-amber-400 font-bold text-sm">{gemini.pending || 0}</span>
            </div>
          </div>

          <div className="text-xs text-slate-400 flex items-center justify-between">
            <span>Concurrency: 2 workers</span>
            <span>RPM Budget: 10 calls/min</span>
          </div>
        </div>
      </div>

      {/* Recent Highlights Feed */}
      <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-2 text-white font-bold">
            <Sparkles className="w-5 h-5 text-amber-400" />
            <span>Recent High-Scoring Highlights</span>
          </div>
          <button
            onClick={() => onNavigate('live')}
            className="text-xs text-blue-400 hover:text-blue-300 font-medium flex items-center space-x-1"
          >
            <span>View Live Feed</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="space-y-3">
          {highlights.slice(0, 5).map((hl, idx) => (
            <div
              key={hl.event_id || idx}
              className="bg-[#182030] border border-[#24334f] rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:border-slate-600 transition-colors"
            >
              <div className="space-y-1">
                <div className="flex items-center space-x-2">
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-blue-500/10 text-blue-400 border border-blue-500/20">
                    {formatChannelName(hl.streamer_name || hl.stream_id)}
                  </span>
                  <span className="text-xs text-slate-400 capitalize">{hl.category?.replace('_', ' ')}</span>
                  <span className="text-xs text-slate-500">• {formatDuration(hl.duration_seconds)}</span>
                </div>
                <h4 className="text-sm font-semibold text-slate-100">{hl.title || 'Untitled Stream Highlight'}</h4>
                {hl.key_quotes && hl.key_quotes.length > 0 && (
                  <p className="text-xs text-slate-400 italic line-clamp-1">
                    "{hl.key_quotes[0]}"
                  </p>
                )}
              </div>

              <div className="flex items-center space-x-3 self-end sm:self-center">
                <div className={`px-3 py-1 rounded-lg text-xs font-black ${
                  (hl.highlight_score || 0) >= 80 
                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                    : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                }`}>
                  {hl.highlight_score || 0}/100
                </div>
              </div>
            </div>
          ))}

          {!highlights.length && (
            <div className="text-center py-8 text-slate-500 text-sm">
              No highlights analyzed yet. Start monitoring to detect live stream moments!
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

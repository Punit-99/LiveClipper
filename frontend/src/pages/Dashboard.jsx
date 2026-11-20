import React, { useState, useEffect } from 'react';
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
  RefreshCw,
  Tv,
  Check,
  X,
  Activity,
  Layers,
  MessageSquare,
  ThumbsUp,
  ThumbsDown,
  Loader2
} from 'lucide-react';
import { formatBytes, formatDuration, fetchChannelsLiveStatus } from '../api';
import { formatChannelName } from '../utils/channels';

export default function Dashboard({ 
  status, 
  quota, 
  settings,
  highlights = [], 
  clips = [], 
  shorts = [], 
  onStartMonitoring, 
  onStopMonitoring, 
  onApproveFallback,
  onWaitTarget,
  onNavigate,
  onRefresh 
}) {
  const [showLiveStatusModal, setShowLiveStatusModal] = useState(false);
  const [liveStatusList, setLiveStatusList] = useState([]);
  const [isLoadingLiveStatus, setIsLoadingLiveStatus] = useState(false);

  // Channel selection modal for "Start Monitoring"
  const [showChannelModal, setShowChannelModal] = useState(false);
  const [selectedChannels, setSelectedChannels] = useState([]);
  const [isStartingMonitoring, setIsStartingMonitoring] = useState(false);

  // Fallback streams state
  const fallbackStreams = status?.fallback_streams || [];
  const waitingTargetChannels = status?.waiting_target_channels;

  const isMonitoring = status?.is_monitoring;
  const activeChannels = status?.active_channels || [];
  const targetChannels = status?.target_channels || [];
  const uptime = status?.uptime_seconds || 0;
  const sessions = status?.sessions || {};

  // Live Stats Panel values
  const liveStats = status?.live_stats || {};
  const segmentsCount = liveStats.segments || 0;
  const chatMessagesCount = liveStats.chat_messages || 0;
  const highlightsDetected = liveStats.highlights_detected || highlights.length;
  const highlightsApproved = liveStats.highlights_approved || 0;
  const highlightsRejected = liveStats.highlights_rejected || 0;
  const clipsExtractedCount = liveStats.clips_extracted || clips.length;
  const recordingDuration = liveStats.duration_seconds || uptime;
  const bufferMb = liveStats.buffer_mb || 0;

  // Max concurrent streams setting
  const isMultiStream = settings?.multi_stream_enabled === true;
  const maxConcurrentStreams = isMultiStream 
    ? (Number(settings?.max_concurrent_streams) || 2) 
    : 1;

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

  // Handler: Manual Check Live Status button
  const handleCheckLiveStatus = async () => {
    setIsLoadingLiveStatus(true);
    setShowLiveStatusModal(true);
    try {
      const data = await fetchChannelsLiveStatus();
      setLiveStatusList(data.channels || []);
    } catch (err) {
      console.error('Failed to fetch channel live status:', err);
    } finally {
      setIsLoadingLiveStatus(false);
    }
  };

  // Handler: Open Channel Selection Modal when user clicks "Start Monitoring"
  const handleOpenStartModal = async () => {
    setIsLoadingLiveStatus(true);
    setShowChannelModal(true);
    try {
      const data = await fetchChannelsLiveStatus();
      const channels = data.channels || [];
      setLiveStatusList(channels);
      // Auto-check live channels by default
      const liveIds = channels.filter(c => c.is_live).map(c => c.id);
      setSelectedChannels(liveIds.slice(0, maxConcurrentStreams));
    } catch (err) {
      console.error('Failed to fetch channels for modal:', err);
    } finally {
      setIsLoadingLiveStatus(false);
    }
  };

  const handleToggleChannel = (channelId) => {
    if (!isMultiStream || maxConcurrentStreams === 1) {
      setSelectedChannels(prev => (prev.includes(channelId) ? [] : [channelId]));
      return;
    }
    setSelectedChannels(prev => {
      if (prev.includes(channelId)) {
        return prev.filter(id => id !== channelId);
      } else {
        return [...prev, channelId];
      }
    });
  };

  const handleConfirmStartMonitoring = async () => {
    if (selectedChannels.length > maxConcurrentStreams) return;
    setIsStartingMonitoring(true);
    try {
      await onStartMonitoring({
        channels: selectedChannels,
        platform: 'all',
      });
      setShowChannelModal(false);
    } finally {
      setIsStartingMonitoring(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 1. Fallback Stream Approval Banner (Requirement 1) */}
      {fallbackStreams.length > 0 && !isMonitoring && (
        <div className="p-5 rounded-2xl bg-amber-950/40 border border-amber-500/50 text-amber-200 shadow-xl shadow-amber-500/5 animate-in fade-in duration-300">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
            <div className="flex items-start space-x-3">
              <div className="p-2.5 rounded-xl bg-amber-500/20 text-amber-400 border border-amber-500/30 mt-0.5 shrink-0">
                <AlertTriangle className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-bold text-amber-100">
                  None of your target channels are live.
                </h3>
                <p className="text-xs text-amber-300/90 mt-0.5">
                  Found these active category live streams instead. Select one to approve and start monitoring, or choose to wait for your target channels. Until approved, no recording starts.
                </p>
              </div>
            </div>

            <button
              onClick={onWaitTarget}
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl border border-slate-700 transition-colors shrink-0"
            >
              Wait for target channels
            </button>
          </div>

          {/* List of discovered fallback streams */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {fallbackStreams.map((stream, idx) => (
              <div
                key={stream.channel_id || idx}
                className="bg-[#182030] border border-[#24334f] rounded-xl p-3.5 flex items-center justify-between gap-3 hover:border-amber-500/40 transition-colors"
              >
                <div className="space-y-1 min-w-0">
                  <div className="flex items-center space-x-2">
                    {stream.platform === 'youtube' ? (
                      <Youtube className="w-4 h-4 text-red-400 shrink-0" />
                    ) : (
                      <Tv className="w-4 h-4 text-purple-400 shrink-0" />
                    )}
                    <span className="font-bold text-sm text-white truncate">
                      {formatChannelName(stream.channel_name || stream.channel_id)}
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 shrink-0">
                      Fallback
                    </span>
                  </div>
                  <div className="text-xs text-slate-300 line-clamp-1" title={stream.stream_title}>
                    {stream.stream_title || 'Live Stream'}
                  </div>
                  <div className="flex items-center space-x-2 text-[11px] text-slate-400">
                    <span className="text-emerald-400 font-bold">{Number(stream.viewer_count || 0).toLocaleString()} viewers</span>
                    <span>•</span>
                    <span className="uppercase font-semibold">{stream.language || 'en'}</span>
                    <span>•</span>
                    <span>{stream.category || 'Gaming'}</span>
                  </div>
                </div>

                <button
                  onClick={() => onApproveFallback(stream.channel_id)}
                  className="px-3 py-1.5 bg-amber-500 hover:bg-amber-400 text-black text-xs font-bold rounded-lg transition-all shadow-md shrink-0"
                >
                  Approve
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Waiting for target channels banner */}
      {waitingTargetChannels && !isMonitoring && fallbackStreams.length === 0 && (
        <div className="p-4 rounded-xl bg-blue-950/40 border border-blue-500/40 text-blue-200 flex items-center justify-between gap-3 text-xs">
          <div className="flex items-center space-x-2.5">
            <Radio className="w-4 h-4 text-blue-400 animate-pulse shrink-0" />
            <span>
              Target channels are currently offline. Automatically polling targets in the background and will start recording as soon as one goes live.
            </span>
          </div>
          <button
            onClick={onRefresh}
            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-semibold shrink-0"
          >
            Check Now
          </button>
        </div>
      )}

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
              {/* Check Live Status Button (Requirement 2) */}
              <button
                onClick={handleCheckLiveStatus}
                className="flex items-center space-x-1.5 px-3 py-2 text-xs font-semibold text-slate-200 bg-[#1a2233] hover:bg-[#25324d] rounded-xl transition-colors border border-[#1f293d]"
                title="Check which channels are live"
              >
                <Activity className="w-3.5 h-3.5 text-blue-400" />
                <span>Check Live Status</span>
              </button>

              <button
                onClick={onRefresh}
                className="p-2 text-slate-400 hover:text-white bg-[#1a2233] hover:bg-[#25324d] rounded-xl transition-colors border border-[#1f293d]"
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
                  onClick={handleOpenStartModal}
                  className="flex items-center space-x-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold rounded-xl text-sm transition-all shadow-lg shadow-emerald-500/20"
                >
                  <Play className="w-4 h-4 fill-current" />
                  <span>Start Monitoring</span>
                </button>
              )}
            </div>
          </div>

          {/* Monitored Streamers Cards with Yellow "Fallback" or Green "Target" Badges (Requirement 7) */}
          {isMonitoring && Object.keys(sessions).length > 0 ? (
            <div className="space-y-3 pt-3 border-t border-[#1f293d]/60">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span className="font-semibold text-slate-300">Monitored Streamers ({Object.keys(sessions).length})</span>
                <span className="text-[11px] text-slate-400">Recording live stream video & chat</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {Object.entries(sessions).map(([ch, sessData]) => {
                  const isFallback = sessData.badge === 'Fallback' || sessData.is_fallback;
                  const isYouTube = sessData.platform === 'youtube';

                  return (
                    <div
                      key={ch}
                      className="bg-[#182030] border border-[#24334f] rounded-xl p-3 flex items-center justify-between gap-3"
                    >
                      <div className="flex items-center space-x-2.5 min-w-0">
                        {isYouTube ? (
                          <Youtube className="w-5 h-5 text-red-500 shrink-0" />
                        ) : (
                          <Tv className="w-5 h-5 text-purple-400 shrink-0" />
                        )}
                        <div className="min-w-0">
                          <div className="flex items-center space-x-2">
                            <span className="font-bold text-sm text-white truncate">{ch}</span>
                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-black uppercase ${
                              isFallback
                                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                            }`}>
                              {isFallback ? 'Fallback' : 'Target'}
                            </span>
                          </div>
                          <div className="flex items-center space-x-2 text-[11px] text-slate-400 mt-0.5">
                            <span className="text-emerald-400 font-semibold">{sessData.viewers ? `${Number(sessData.viewers).toLocaleString()} viewers` : 'Live'}</span>
                            <span>•</span>
                            <span>{sessData.segments_registered || 0} segments</span>
                          </div>
                        </div>
                      </div>

                      <div className="text-right shrink-0">
                        <span className="text-xs font-mono font-bold text-slate-200">
                          {formatDuration(sessData.duration_seconds || uptime)}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            /* Target channels pills when stopped */
            <div className="space-y-3 pt-2 border-t border-[#1f293d]/60">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Configured Target Channels ({targetChannels.length})</span>
                <span>Platform: <span className="font-semibold text-slate-200 capitalize">{status?.platform || 'All'}</span></span>
              </div>

              <div className="flex flex-wrap gap-2">
                {targetChannels.map((ch, idx) => (
                  <div
                    key={idx}
                    className="flex items-center space-x-2 px-3 py-1.5 rounded-lg bg-[#182030] border border-[#24334f] text-xs font-medium text-slate-200"
                  >
                    <span className="w-2 h-2 rounded-full bg-slate-500" />
                    <span>{formatChannelName(ch)}</span>
                  </div>
                ))}
                {!targetChannels.length && (
                  <span className="text-xs text-slate-500 italic">No target channels configured in .env</span>
                )}
              </div>
            </div>
          )}
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

      {/* 5. Dashboard Live Stats Panel with Live Counters (Requirement 10) */}
      <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between border-b border-[#1f293d]/60 pb-3">
          <div className="flex items-center space-x-2 text-white font-bold">
            <Activity className="w-5 h-5 text-emerald-400" />
            <span>Live Recording & Extraction Stats</span>
          </div>
          <span className="text-xs text-slate-400">
            Active Duration: <strong className="text-emerald-400 font-mono">{formatDuration(recordingDuration)}</strong>
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {/* Segments Counter */}
          <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f]">
            <div className="flex items-center space-x-1.5 text-xs text-slate-400 mb-1">
              <Layers className="w-3.5 h-3.5 text-blue-400" />
              <span>Segments</span>
            </div>
            <div className="text-xl font-black text-white">{segmentsCount}</div>
            <div className="text-[10px] text-slate-500">Recorded chunks</div>
          </div>

          {/* Buffer Size */}
          <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f]">
            <div className="flex items-center space-x-1.5 text-xs text-slate-400 mb-1">
              <HardDrive className="w-3.5 h-3.5 text-amber-400" />
              <span>Buffer Used</span>
            </div>
            <div className="text-xl font-black text-amber-300">{bufferMb} MB</div>
            <div className="text-[10px] text-slate-500">Rolling ring buffer</div>
          </div>

          {/* Highlights Detected (Spikes) */}
          <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f]">
            <div className="flex items-center space-x-1.5 text-xs text-slate-400 mb-1">
              <Sparkles className="w-3.5 h-3.5 text-purple-400" />
              <span>Spikes</span>
            </div>
            <div className="text-xl font-black text-white">{highlightsDetected}</div>
            <div className="text-[10px] text-slate-500">Chat spikes</div>
          </div>

          {/* Highlights Approved */}
          <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f]">
            <div className="flex items-center space-x-1.5 text-xs text-slate-400 mb-1">
              <ThumbsUp className="w-3.5 h-3.5 text-emerald-400" />
              <span>Approved</span>
            </div>
            <div className="text-xl font-black text-emerald-400">{highlightsApproved}</div>
            <div className="text-[10px] text-slate-500">Gemini score &gt;= 65</div>
          </div>

          {/* Highlights Rejected */}
          <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f]">
            <div className="flex items-center space-x-1.5 text-xs text-slate-400 mb-1">
              <ThumbsDown className="w-3.5 h-3.5 text-slate-500" />
              <span>Rejected</span>
            </div>
            <div className="text-xl font-black text-slate-400">{highlightsRejected}</div>
            <div className="text-[10px] text-slate-500">Below threshold</div>
          </div>

          {/* Clips Extracted */}
          <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f]">
            <div className="flex items-center space-x-1.5 text-xs text-slate-400 mb-1">
              <Film className="w-3.5 h-3.5 text-blue-400" />
              <span>Clips</span>
            </div>
            <div className="text-xl font-black text-blue-400">{clipsExtractedCount}</div>
            <div className="text-[10px] text-slate-500">Extracted MP4s</div>
          </div>
        </div>
      </div>

      {/* Disk Space & AI Worker Status */}
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

      {/* Channel Selection Modal (Requirement 3 & 8) */}
      {showChannelModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl flex flex-col max-h-[85vh]">
            <div className="p-5 border-b border-[#1f293d] flex items-center justify-between bg-[#182030]">
              <div>
                <h3 className="text-base font-bold text-white flex items-center space-x-2">
                  <Play className="w-4 h-4 text-emerald-400" />
                  <span>Choose Channels to Monitor</span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Select up to {maxConcurrentStreams} live stream{maxConcurrentStreams === 1 ? '' : 's'} to monitor concurrently.
                </p>
              </div>
              <button
                onClick={() => setShowChannelModal(false)}
                className="p-1.5 text-slate-400 hover:text-white rounded-lg bg-slate-800 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Selection limit warning */}
            {selectedChannels.length > maxConcurrentStreams && (
              <div className="mx-5 mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-xl flex items-center space-x-2 text-xs text-red-300">
                <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                <span>
                  Limit exceeded: you selected {selectedChannels.length} streams, max is {maxConcurrentStreams}. Change MAX_CONCURRENT_STREAMS in Settings to record more.
                </span>
              </div>
            )}

            {/* Zero selected warning */}
            {selectedChannels.length === 0 && !isLoadingLiveStatus && (
              <div className="mx-5 mt-4 p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl flex items-center space-x-2 text-xs text-amber-300">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                <span>No channels selected. Choose at least one live channel.</span>
              </div>
            )}

            <div className="p-5 overflow-y-auto space-y-2 flex-1">
              {isLoadingLiveStatus ? (
                <div className="py-8 flex flex-col items-center justify-center text-slate-400 space-y-2">
                  <Loader2 className="w-6 h-6 animate-spin text-blue-400" />
                  <span className="text-xs">Checking live status of configured channels...</span>
                </div>
              ) : liveStatusList.length > 0 ? (
                liveStatusList.map((ch) => {
                  const isChecked = selectedChannels.includes(ch.id);
                  const isLive = ch.is_live;

                  return (
                    <div
                      key={ch.id}
                      onClick={() => isLive && handleToggleChannel(ch.id)}
                      className={`p-3 rounded-xl border flex items-center justify-between transition-all ${
                        !isLive
                          ? 'bg-[#141924]/50 border-[#1f293d]/50 opacity-60 cursor-not-allowed'
                          : isChecked
                          ? 'bg-blue-600/10 border-blue-500/50 cursor-pointer'
                          : 'bg-[#182030] border-[#24334f] hover:border-slate-500 cursor-pointer'
                      }`}
                    >
                      <div className="flex items-center space-x-3 min-w-0">
                        <input
                          type={(!isMultiStream || maxConcurrentStreams === 1) ? "radio" : "checkbox"}
                          name="channel-select"
                          checked={isChecked}
                          disabled={!isLive}
                          onChange={() => {}}
                          className="w-4 h-4 rounded border-slate-700 bg-slate-800 text-blue-600 focus:ring-0 focus:ring-offset-0 disabled:opacity-40"
                        />
                        <div className="min-w-0">
                          <div className="flex items-center space-x-2">
                            {ch.platform === 'youtube' ? (
                              <Youtube className="w-4 h-4 text-red-400 shrink-0" />
                            ) : (
                              <Tv className="w-4 h-4 text-purple-400 shrink-0" />
                            )}
                            <span className="font-bold text-sm text-white truncate">
                              {formatChannelName(ch.name || ch.id)}
                            </span>
                          </div>
                          {ch.title && (
                            <p className="text-[11px] text-slate-300 truncate mt-0.5" title={ch.title}>
                              {ch.title}
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center space-x-2 shrink-0">
                        {isLive ? (
                          <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold">
                            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                            <span>LIVE</span>
                            {ch.viewers > 0 && <span className="text-emerald-300">({Number(ch.viewers).toLocaleString()})</span>}
                          </div>
                        ) : (
                          <div className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 text-xs font-semibold">
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-500" />
                            <span>Offline</span>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="text-center py-6 text-slate-400 text-xs">
                  No channels configured. Add target channels in Settings or .env.
                </div>
              )}
            </div>

            <div className="p-4 border-t border-[#1f293d] bg-[#182030] flex items-center justify-between">
              <span className="text-xs text-slate-400 font-semibold">
                {selectedChannels.length} of {liveStatusList.length} streams selected (max: {maxConcurrentStreams})
              </span>

              <div className="flex items-center space-x-2">
                <button
                  onClick={() => setShowChannelModal(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold rounded-xl transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmStartMonitoring}
                  disabled={isStartingMonitoring || selectedChannels.length === 0 || selectedChannels.length > maxConcurrentStreams}
                  className="flex items-center space-x-2 px-5 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-bold rounded-xl transition-all shadow-md shadow-emerald-500/20"
                >
                  {isStartingMonitoring ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Starting...</span>
                    </>
                  ) : (
                    <>
                      <Play className="w-3.5 h-3.5 fill-current" />
                      <span>Start Monitoring Selected</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Manual Live Status Check Modal (Requirement 2) */}
      {showLiveStatusModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl flex flex-col max-h-[85vh]">
            <div className="p-5 border-b border-[#1f293d] flex items-center justify-between bg-[#182030]">
              <div className="flex items-center space-x-2 text-white font-bold">
                <Activity className="w-5 h-5 text-blue-400" />
                <span>Configured Channels Live Status</span>
              </div>
              <button
                onClick={() => setShowLiveStatusModal(false)}
                className="p-1.5 text-slate-400 hover:text-white rounded-lg bg-slate-800 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 overflow-y-auto space-y-2.5 flex-1">
              {isLoadingLiveStatus ? (
                <div className="py-8 flex flex-col items-center justify-center text-slate-400 space-y-2">
                  <Loader2 className="w-6 h-6 animate-spin text-blue-400" />
                  <span className="text-xs">Checking live status of YouTube and Twitch channels...</span>
                </div>
              ) : liveStatusList.length > 0 ? (
                liveStatusList.map((ch) => (
                  <div
                    key={ch.id}
                    className="p-3 bg-[#182030] border border-[#24334f] rounded-xl flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center space-x-2">
                        {ch.platform === 'youtube' ? (
                          <Youtube className="w-4 h-4 text-red-400 shrink-0" />
                        ) : (
                          <Tv className="w-4 h-4 text-purple-400 shrink-0" />
                        )}
                        <span className="font-bold text-sm text-white truncate">
                          {formatChannelName(ch.name || ch.id)}
                        </span>
                      </div>
                      {ch.title && (
                        <p className="text-[11px] text-slate-300 truncate mt-0.5" title={ch.title}>
                          {ch.title}
                        </p>
                      )}
                    </div>

                    <div className="flex items-center space-x-2 shrink-0">
                      {ch.is_live ? (
                        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold">
                          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                          <span>LIVE</span>
                          {ch.viewers > 0 && <span className="text-emerald-300">({Number(ch.viewers).toLocaleString()})</span>}
                        </div>
                      ) : (
                        <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 text-xs font-semibold">
                          <span className="w-1.5 h-1.5 rounded-full bg-slate-500" />
                          <span>OFFLINE</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center py-6 text-slate-400 text-xs">
                  No channels configured.
                </div>
              )}
            </div>

            <div className="p-4 border-t border-[#1f293d] bg-[#182030] flex justify-end">
              <button
                onClick={() => setShowLiveStatusModal(false)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

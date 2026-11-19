import React, { useState, useEffect, useRef } from 'react';
import { 
  Radio, 
  Sparkles, 
  BrainCircuit, 
  Video, 
  Film, 
  Upload, 
  CheckCircle2, 
  Clock, 
  Pause, 
  Play, 
  Trash2, 
  Flame,
  ArrowRight
} from 'lucide-react';
import { formatDuration } from '../api';
import { formatChannelName } from '../utils/channels';

export default function LiveFeed({ liveEvents = [], onClearEvents }) {
  const [autoScroll, setAutoScroll] = useState(true);
  const [isHovered, setIsHovered] = useState(false);
  const feedEndRef = useRef(null);

  useEffect(() => {
    if (autoScroll && !isHovered) {
      feedEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [liveEvents, autoScroll, isHovered]);

  const getEventBadge = (type) => {
    switch (type) {
      case 'chat_spike':
      case 'highlight_detected':
        return {
          icon: Flame,
          label: 'Chat Spike',
          color: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
        };
      case 'gemini_analyzing':
        return {
          icon: BrainCircuit,
          label: 'Gemini Analyzing',
          color: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
        };
      case 'gemini_verdict':
        return {
          icon: Sparkles,
          label: 'Highlight Verdict',
          color: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
        };
      case 'clip_extracted':
        return {
          icon: Video,
          label: 'Clip Extracted',
          color: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
        };
      case 'short_processed':
        return {
          icon: Film,
          label: 'Short Ready',
          color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
        };
      case 'upload_complete':
        return {
          icon: Upload,
          label: 'YouTube Uploaded',
          color: 'bg-red-500/10 text-red-400 border-red-500/20',
        };
      default:
        return {
          icon: Radio,
          label: type?.replace('_', ' ') || 'Event',
          color: 'bg-slate-700 text-slate-300 border-slate-600',
        };
    }
  };

  return (
    <div className="space-y-6">
      {/* Header Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div>
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-xl bg-blue-500/10 text-blue-400 border border-blue-500/20">
              <Radio className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white flex items-center space-x-2">
                <span>Real-Time Live Event Feed</span>
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                WebSocket event timeline for chat rate spikes, Gemini verdicts, FFmpeg extractions, and YouTube uploads.
              </p>
            </div>
          </div>
        </div>

        {/* Controls */}
        <div className="flex items-center space-x-3">
          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-xl text-xs font-semibold border transition-all ${
              autoScroll
                ? 'bg-blue-600/20 text-blue-400 border-blue-500/30'
                : 'bg-slate-800 text-slate-400 border-slate-700'
            }`}
          >
            {autoScroll ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
            <span>Auto-Scroll: {autoScroll ? 'ON' : 'OFF'}</span>
          </button>

          <button
            onClick={onClearEvents}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#182030] hover:bg-slate-800 text-slate-400 hover:text-red-400 border border-[#24334f] rounded-xl text-xs transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear Log</span>
          </button>
        </div>
      </div>

      {/* Main Feed Container */}
      <div 
        className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 min-h-[500px] max-h-[70vh] overflow-y-auto space-y-4 relative"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {isHovered && autoScroll && (
          <div className="sticky top-2 z-20 mx-auto w-fit px-3 py-1 rounded-full bg-black/80 backdrop-blur-md border border-slate-700 text-[11px] text-slate-300 font-semibold shadow-lg">
            Auto-scroll paused while hovering
          </div>
        )}

        <div className="relative pl-6 space-y-6 before:absolute before:left-3 before:top-2 before:bottom-2 before:w-0.5 before:bg-[#1f293d]">
          {liveEvents.map((evt, idx) => {
            const badge = getEventBadge(evt.type);
            const Icon = badge.icon;
            const data = evt.data || {};
            const timeStr = new Date(evt.timestamp ? evt.timestamp * 1000 : Date.now()).toLocaleTimeString();

            return (
              <div key={idx} className="relative group">
                {/* Timeline node icon */}
                <div className="absolute -left-6 top-1.5 w-6 h-6 rounded-full bg-[#121722] border-2 border-slate-700 flex items-center justify-center text-slate-400 group-hover:border-blue-500 transition-colors">
                  <Icon className="w-3 h-3" />
                </div>

                {/* Event Card Content */}
                <div className="bg-[#182030] border border-[#24334f] rounded-xl p-4 ml-3 space-y-2 group-hover:border-slate-600 transition-colors">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center space-x-2">
                      <span className={`px-2 py-0.5 rounded text-[11px] font-bold uppercase border ${badge.color}`}>
                        {badge.label}
                      </span>
                      {data.channel && (
                        <span className="text-xs font-bold text-slate-200">
                          #{formatChannelName(data.channel)}
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] font-mono text-slate-500">{timeStr}</span>
                  </div>

                  {/* Dynamic event body details */}
                  {evt.type === 'clip_extracted' && (
                    <div className="space-y-1 text-xs">
                      <div className="flex items-center space-x-2">
                        <span className="font-bold text-emerald-400">Clip [{data.clip_id}]</span>
                        <span className="text-slate-300 font-semibold">{data.title}</span>
                      </div>
                      <div className="flex items-center space-x-3 text-slate-400 text-[11px]">
                        <span>Score: <strong className="text-emerald-400">{data.highlight_score}/100</strong></span>
                        <span>Duration: {formatDuration(data.duration)}</span>
                        <span>File: {data.filename}</span>
                      </div>
                    </div>
                  )}

                  {evt.type === 'upload_complete' && (
                    <div className="space-y-1 text-xs">
                      <div className="flex items-center space-x-2 font-bold text-red-400">
                        <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                        <span>Successfully uploaded to YouTube Shorts!</span>
                      </div>
                      <p className="text-slate-300 font-semibold">{data.title}</p>
                      {data.youtube_url && (
                        <a
                          href={data.youtube_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-blue-400 hover:text-blue-300 flex items-center space-x-1 font-semibold"
                        >
                          <span>{data.youtube_url}</span>
                          <ArrowRight className="w-3 h-3" />
                        </a>
                      )}
                    </div>
                  )}

                  {evt.type === 'session_state' && (
                    <div className="text-xs text-slate-300">
                      Channel state changed from <strong className="text-slate-400 uppercase">{data.old_state}</strong> to <strong className="text-emerald-400 uppercase">{data.new_state}</strong>
                    </div>
                  )}

                  {evt.type === 'status_update' && (
                    <div className="text-xs text-slate-400">
                      System status: {data.is_monitoring ? 'Monitoring active' : 'Monitoring stopped'}
                    </div>
                  )}

                  {/* Fallback raw payload view if generic */}
                  {!['clip_extracted', 'upload_complete', 'session_state', 'status_update'].includes(evt.type) && (
                    <div className="text-xs text-slate-300">
                      {data.message || JSON.stringify(data)}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {!liveEvents.length && (
            <div className="py-20 text-center text-slate-500 text-xs">
              Waiting for incoming WebSocket events... Start monitoring or trigger processing to see live updates.
            </div>
          )}

          <div ref={feedEndRef} />
        </div>
      </div>
    </div>
  );
}

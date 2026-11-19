import React, { useState } from 'react';
import { 
  Play, 
  Film, 
  Filter, 
  Sparkles, 
  Clock, 
  CheckCircle2, 
  X, 
  Loader2, 
  ArrowUpDown,
  Youtube,
  Tv,
  Trash2,
  AlertTriangle
} from 'lucide-react';
import { formatDuration, deleteSingleClip, deleteAllClips } from '../api';
import { formatChannelName } from '../utils/channels';

export default function Clips({ 
  clips = [], 
  onProcessSingle, 
  onProcessAll, 
  isProcessing, 
  onRefresh,
  onDeleteClip,
  onDeleteAllClips,
  showToast
}) {
  const [selectedClip, setSelectedClip] = useState(null);
  const [channelFilter, setChannelFilter] = useState('');
  const [platformFilter, setPlatformFilter] = useState('');
  const [scoreFilter, setScoreFilter] = useState(0);
  const [sortBy, setSortBy] = useState('score');
  const [processingId, setProcessingId] = useState(null);
  const [clipToDelete, setClipToDelete] = useState(null);
  const [showDeleteAllModal, setShowDeleteAllModal] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [isDeletingAll, setIsDeletingAll] = useState(false);

  // Derive unique channels for filter
  const channels = Array.from(new Set(clips.map(c => c.channel))).filter(Boolean);

  // Filter & sort
  const filteredClips = clips.filter(c => {
    if (channelFilter && c.channel.toLowerCase() !== channelFilter.toLowerCase()) return false;
    if (platformFilter && c.platform.toLowerCase() !== platformFilter.toLowerCase()) return false;
    if (scoreFilter > 0 && c.score < scoreFilter) return false;
    return true;
  }).sort((a, b) => {
    if (sortBy === 'score') return b.score - a.score;
    if (sortBy === 'duration') return b.duration - a.duration;
    return (b.extracted_at || '').localeCompare(a.extracted_at || '');
  });

  const handleProcessSingle = async (clipId) => {
    setProcessingId(clipId);
    try {
      await onProcessSingle(clipId);
    } finally {
      setProcessingId(null);
    }
  };

  const handleConfirmDeleteSingle = async () => {
    if (!clipToDelete) return;
    const cid = clipToDelete.id;
    setDeletingId(cid);
    setClipToDelete(null);
    try {
      if (onDeleteClip) {
        await onDeleteClip(cid);
      } else {
        await deleteSingleClip(cid);
        if (onRefresh) onRefresh();
      }
      if (showToast) showToast('Clip deleted', 'success');
    } catch (err) {
      if (showToast) showToast(err.message || 'Failed to delete clip', 'error');
    } finally {
      setTimeout(() => setDeletingId(null), 350);
    }
  };

  const handleConfirmDeleteAll = async () => {
    setIsDeletingAll(true);
    setShowDeleteAllModal(false);
    try {
      if (onDeleteAllClips) {
        await onDeleteAllClips();
      } else {
        await deleteAllClips();
        if (onRefresh) onRefresh();
      }
      if (showToast) showToast('All clips deleted', 'success');
    } catch (err) {
      if (showToast) showToast(err.message || 'Failed to delete all clips', 'error');
    } finally {
      setIsDeletingAll(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header & Batch Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center space-x-2">
            <span>Extracted Highlight Clips</span>
            <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-blue-500/10 text-blue-400 border border-blue-500/20">
              {filteredClips.length} clips
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Raw stream highlights extracted based on live chat spikes and Gemini analysis.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3 self-start sm:self-auto">
          <button
            onClick={() => setShowDeleteAllModal(true)}
            disabled={!clips.length || isDeletingAll}
            className="flex items-center space-x-2 px-4 py-2.5 border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl text-sm font-semibold transition-all"
          >
            {isDeletingAll ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Deleting...</span>
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                <span>Delete All Clips</span>
              </>
            )}
          </button>

          <button
            onClick={onProcessAll}
            disabled={isProcessing || !clips.length}
            className="flex items-center space-x-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-xl text-sm transition-all shadow-lg shadow-blue-500/20"
          >
            {isProcessing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Processing All...</span>
              </>
            ) : (
              <>
                <Film className="w-4 h-4" />
                <span>Process All to Shorts</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Filter and Sort Toolbar */}
      <div className="bg-[#121722] border border-[#1f293d] rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center space-x-2 text-slate-400 font-semibold">
            <Filter className="w-4 h-4" />
            <span>Filter:</span>
          </div>

          {/* Channel dropdown */}
          <select
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
            className="bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-1.5 focus:outline-none focus:border-blue-500"
          >
            <option value="">All Channels</option>
            {channels.map((ch) => (
              <option key={ch} value={ch}>
                {formatChannelName(ch)}
              </option>
            ))}
          </select>

          {/* Platform filter */}
          <select
            value={platformFilter}
            onChange={(e) => setPlatformFilter(e.target.value)}
            className="bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-1.5 focus:outline-none focus:border-blue-500"
          >
            <option value="">All Platforms</option>
            <option value="twitch">Twitch</option>
            <option value="youtube">YouTube</option>
          </select>

          {/* Score filter */}
          <select
            value={scoreFilter}
            onChange={(e) => setScoreFilter(Number(e.target.value))}
            className="bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-1.5 focus:outline-none focus:border-blue-500"
          >
            <option value="0">Min Score: Any</option>
            <option value="70">Score 70+</option>
            <option value="80">Score 80+</option>
            <option value="85">Score 85+ (Viral)</option>
          </select>
        </div>

        {/* Sort */}
        <div className="flex items-center space-x-2">
          <span className="text-slate-400 font-semibold flex items-center space-x-1">
            <ArrowUpDown className="w-3.5 h-3.5" />
            <span>Sort by:</span>
          </span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-1.5 focus:outline-none focus:border-blue-500"
          >
            <option value="score">Highest Score</option>
            <option value="date">Most Recent</option>
            <option value="duration">Longest Duration</option>
          </select>
        </div>
      </div>

      {/* Grid of Clip Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredClips.map((clip) => {
          const isYouTube = clip.platform?.toLowerCase() === 'youtube';
          const isProcessingThis = processingId === clip.id;

          return (
            <div
              key={clip.id}
              className={`bg-[#121722] border border-[#1f293d] rounded-2xl overflow-hidden flex flex-col justify-between hover:border-slate-600 transition-all duration-300 group ${
                deletingId === clip.id ? 'opacity-0 scale-95 pointer-events-none' : 'opacity-100 scale-100'
              }`}
            >
              {/* Video Preview Card Header */}
              <div 
                className="relative aspect-video bg-black cursor-pointer overflow-hidden group/video"
                onClick={() => setSelectedClip(clip)}
              >
                <video
                  src={`/api/clips/${clip.id}/preview`}
                  preload="metadata"
                  className="w-full h-full object-cover group-hover/video:scale-105 transition-transform duration-300"
                />
                
                {/* Overlay Play Button */}
                <div className="absolute inset-0 bg-black/40 group-hover/video:bg-black/20 flex items-center justify-center transition-colors">
                  <div className="w-12 h-12 rounded-full bg-blue-600/90 text-white flex items-center justify-center shadow-lg group-hover/video:scale-110 transition-transform">
                    <Play className="w-5 h-5 fill-current ml-0.5" />
                  </div>
                </div>

                {/* Score badge & delete action top-right */}
                <div className="absolute top-3 right-3 flex items-center space-x-1.5 z-10">
                  <span className={`px-2.5 py-1 rounded-lg text-xs font-black shadow-md ${
                    clip.score >= 80 
                      ? 'bg-emerald-500 text-black' 
                      : 'bg-amber-500 text-black'
                  }`}>
                    {clip.score}/100
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setClipToDelete(clip);
                    }}
                    title="Delete clip"
                    className="p-1.5 rounded-lg bg-black/70 backdrop-blur-md text-slate-400 hover:text-red-400 hover:bg-red-500/20 transition-all shadow-md group/trash"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>

                {/* Platform badge top-left */}
                <div className="absolute top-3 left-3 flex items-center space-x-1.5 bg-black/70 backdrop-blur-md px-2 py-1 rounded-md text-xs font-semibold text-white">
                  {isYouTube ? <Youtube className="w-3.5 h-3.5 text-red-500" /> : <Tv className="w-3.5 h-3.5 text-purple-400" />}
                  <span>{formatChannelName(clip.channel)}</span>
                </div>

                {/* Duration bottom-right */}
                <div className="absolute bottom-2 right-2 bg-black/80 px-2 py-0.5 rounded text-[11px] font-mono text-slate-200">
                  {formatDuration(clip.duration)}
                </div>
              </div>

              {/* Card Body */}
              <div className="p-5 flex-1 flex flex-col justify-between space-y-4">
                <div className="space-y-2">
                  <div className="flex items-center space-x-2">
                    <span className="text-[11px] uppercase tracking-wider font-bold px-2 py-0.5 rounded bg-[#182030] text-slate-300 border border-[#24334f]">
                      {clip.category?.replace('_', ' ')}
                    </span>
                    {clip.has_short && (
                      <span className="flex items-center space-x-1 text-[11px] font-semibold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Short Ready</span>
                      </span>
                    )}
                  </div>

                  <h3 className="font-bold text-sm text-slate-100 line-clamp-2">
                    {clip.title}
                  </h3>

                  {clip.quotes && clip.quotes.length > 0 && (
                    <p className="text-xs text-slate-400 italic line-clamp-1">
                      "{clip.quotes[0]}"
                    </p>
                  )}
                </div>

                {/* Actions */}
                <div className="pt-2 border-t border-[#1f293d]/80 flex items-center justify-between gap-2">
                  <button
                    onClick={() => setSelectedClip(clip)}
                    className="px-3 py-2 text-xs font-semibold text-slate-300 bg-[#182030] hover:bg-[#222e45] rounded-xl border border-[#24334f] transition-colors"
                  >
                    Details & Quotes
                  </button>

                  <button
                    onClick={() => handleProcessSingle(clip.id)}
                    disabled={isProcessingThis}
                    className="flex-1 flex items-center justify-center space-x-1.5 px-3 py-2 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-xl transition-all shadow-sm shadow-blue-500/20"
                  >
                    {isProcessingThis ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        <span>Processing...</span>
                      </>
                    ) : (
                      <>
                        <Film className="w-3.5 h-3.5" />
                        <span>{clip.has_short ? 'Re-Process' : 'Process to Short'}</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        {!filteredClips.length && (
          <div className="col-span-full py-16 text-center bg-[#121722] border border-[#1f293d] rounded-2xl text-slate-400">
            <Film className="w-12 h-12 mx-auto text-slate-600 mb-3" />
            <h3 className="text-lg font-bold text-slate-300">No clips match your filters</h3>
            <p className="text-xs text-slate-500 mt-1">Try resetting the channel, platform, or score filter options.</p>
          </div>
        )}
      </div>

      {/* Modal Video Player */}
      {selectedClip && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-3xl overflow-hidden shadow-2xl flex flex-col max-h-[90vh]">
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-[#1f293d] flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-blue-500/10 text-blue-400">
                  {formatChannelName(selectedClip.channel)}
                </span>
                <span className="text-sm font-bold text-white line-clamp-1">{selectedClip.title}</span>
              </div>
              <button
                onClick={() => setSelectedClip(null)}
                className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Video Player */}
            <div className="bg-black aspect-video flex items-center justify-center">
              <video
                src={`/api/clips/${selectedClip.id}/preview`}
                controls
                autoPlay
                className="w-full h-full max-h-[50vh] object-contain"
              />
            </div>

            {/* Modal Details */}
            <div className="p-6 overflow-y-auto space-y-4 text-xs">
              <div>
                <h4 className="text-slate-400 uppercase tracking-wider font-semibold mb-1">Gemini AI Reasoning</h4>
                <p className="text-slate-200 leading-relaxed bg-[#182030] p-3 rounded-xl border border-[#24334f]">
                  {selectedClip.reasoning || 'No analysis reasoning logged.'}
                </p>
              </div>

              {selectedClip.quotes && selectedClip.quotes.length > 0 && (
                <div>
                  <h4 className="text-slate-400 uppercase tracking-wider font-semibold mb-2">Key Chat Reaction Quotes</h4>
                  <div className="flex flex-wrap gap-2">
                    {selectedClip.quotes.map((q, idx) => (
                      <span
                        key={idx}
                        className="bg-[#182030] text-slate-300 border border-[#24334f] px-3 py-1.5 rounded-lg italic"
                      >
                        "{q}"
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-3 border-t border-[#1f293d] flex justify-end space-x-3 bg-[#0d121d]">
              <button
                onClick={() => setSelectedClip(null)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold"
              >
                Close
              </button>
              <button
                onClick={() => {
                  handleProcessSingle(selectedClip.id);
                  setSelectedClip(null);
                }}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-semibold"
              >
                Process to Vertical Short
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Single Clip Confirmation Modal */}
      {clipToDelete && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6 space-y-4">
            <div className="flex items-center space-x-3 text-red-400">
              <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/20">
                <AlertTriangle className="w-5 h-5 text-red-400" />
              </div>
              <h3 className="text-base font-bold text-white">Delete Clip</h3>
            </div>
            
            <p className="text-sm text-slate-300">
              Delete this clip? This cannot be undone.
            </p>

            <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f] text-xs space-y-1">
              <div className="font-bold text-white line-clamp-1">{clipToDelete.title}</div>
              <div className="text-slate-400 flex items-center justify-between">
                <span>Channel: {formatChannelName(clipToDelete.channel)}</span>
                <span>Score: {clipToDelete.score}/100</span>
              </div>
            </div>

            <div className="flex justify-end space-x-3 pt-2">
              <button
                onClick={() => setClipToDelete(null)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDeleteSingle}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-xl text-xs font-semibold transition-colors shadow-lg shadow-red-600/20"
              >
                Delete Clip
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete All Clips Confirmation Modal */}
      {showDeleteAllModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6 space-y-4">
            <div className="flex items-center space-x-3 text-red-400">
              <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/20">
                <AlertTriangle className="w-5 h-5 text-red-400" />
              </div>
              <h3 className="text-base font-bold text-white">Delete All Clips</h3>
            </div>

            <p className="text-sm text-slate-300">
              Delete ALL clips? This will remove {clips.length} clip{clips.length === 1 ? '' : 's'} permanently.
            </p>

            <p className="text-xs text-slate-500">
              All raw MP4 highlights and JSON metadata files will be deleted from disk.
            </p>

            <div className="flex justify-end space-x-3 pt-2">
              <button
                onClick={() => setShowDeleteAllModal(false)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDeleteAll}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-xl text-xs font-semibold transition-colors shadow-lg shadow-red-600/20"
              >
                Delete All Permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

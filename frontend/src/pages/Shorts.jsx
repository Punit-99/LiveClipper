import React, { useState } from 'react';
import { 
  Play, 
  Youtube, 
  Upload, 
  ExternalLink, 
  CheckCircle2, 
  Clock, 
  Sparkles, 
  X, 
  Loader2, 
  ChevronDown, 
  ChevronUp, 
  Tag, 
  FileText,
  ThumbsUp,
  MessageSquare,
  Share2,
  Trash2,
  AlertTriangle
} from 'lucide-react';
import { formatDuration, deleteSingleShort, deleteAllShorts } from '../api';
import { formatChannelName } from '../utils/channels';

export default function Shorts({ 
  shorts = [], 
  quota, 
  onUploadSingle, 
  onUploadAll, 
  isUploading, 
  onRefresh,
  onDeleteShort,
  onDeleteAllShorts,
  showToast
}) {
  const [selectedShort, setSelectedShort] = useState(null);
  const [expandedDescId, setExpandedDescId] = useState(null);
  const [activeTitles, setActiveTitles] = useState({});
  const [uploadingId, setUploadingId] = useState(null);
  const [privacyMap, setPrivacyMap] = useState({});
  const [shortToDelete, setShortToDelete] = useState(null);
  const [showDeleteAllModal, setShowDeleteAllModal] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [isDeletingAll, setIsDeletingAll] = useState(false);

  const handleSelectTitle = (shortId, title) => {
    setActiveTitles(prev => ({ ...prev, [shortId]: title }));
  };

  const handlePrivacyChange = (shortId, privacy) => {
    setPrivacyMap(prev => ({ ...prev, [shortId]: privacy }));
  };

  const handleUploadSingle = async (short) => {
    setUploadingId(short.id);
    const chosenTitle = activeTitles[short.id] || short.selected_title;
    const privacy = privacyMap[short.id] || 'public';
    try {
      await onUploadSingle(short.id, {
        selected_title: chosenTitle,
        privacy_status: privacy,
      });
    } finally {
      setUploadingId(null);
    }
  };

  const handleConfirmDeleteSingle = async () => {
    if (!shortToDelete) return;
    const sid = shortToDelete.id;
    setDeletingId(sid);
    setShortToDelete(null);
    try {
      if (onDeleteShort) {
        await onDeleteShort(sid);
      } else {
        await deleteSingleShort(sid);
        if (onRefresh) onRefresh();
      }
      if (showToast) showToast('Short deleted', 'success');
    } catch (err) {
      if (showToast) showToast(err.message || 'Failed to delete Short', 'error');
    } finally {
      setTimeout(() => setDeletingId(null), 350);
    }
  };

  const handleConfirmDeleteAll = async () => {
    setIsDeletingAll(true);
    setShowDeleteAllModal(false);
    try {
      if (onDeleteAllShorts) {
        await onDeleteAllShorts();
      } else {
        await deleteAllShorts();
        if (onRefresh) onRefresh();
      }
      if (showToast) showToast('All Shorts deleted', 'success');
    } catch (err) {
      if (showToast) showToast(err.message || 'Failed to delete all Shorts', 'error');
    } finally {
      setIsDeletingAll(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header and Batch Upload */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center space-x-2">
            <span>Vertical YouTube Shorts</span>
            <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-red-500/10 text-red-400 border border-red-500/20">
              {shorts.length} Shorts
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            9:16 vertical reframed Shorts with animated captions and hook titles ready for publication.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3 self-start sm:self-auto">
          <button
            onClick={() => setShowDeleteAllModal(true)}
            disabled={!shorts.length || isDeletingAll}
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
                <span>Delete All Shorts</span>
              </>
            )}
          </button>

          <button
            onClick={onUploadAll}
            disabled={isUploading || !shorts.length || !quota?.can_upload}
            className="flex items-center space-x-2 px-5 py-2.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-xl text-sm transition-all shadow-lg shadow-red-500/20"
          >
            {isUploading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Uploading Shorts...</span>
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" />
                <span>Upload All Eligible ({quota ? `${quota.daily_limit - quota.uploads_today} left` : '5 left'})</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Grid of 9:16 Shorts Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {shorts.map((short) => {
          const currentTitle = activeTitles[short.id] || short.selected_title;
          const isUploaded = short.state === 'UPLOADED' || Boolean(short.youtube_url);
          const isUploadingThis = uploadingId === short.id;
          const isExpanded = expandedDescId === short.id;
          const currentPrivacy = privacyMap[short.id] || 'public';

          return (
            <div
              key={short.id}
              className={`bg-[#121722] border border-[#1f293d] rounded-2xl overflow-hidden flex flex-col justify-between hover:border-slate-600 transition-all duration-300 ${
                deletingId === short.id ? 'opacity-0 scale-95 pointer-events-none' : 'opacity-100 scale-100'
              }`}
            >
              {/* Vertical 9:16 Video Preview Header */}
              <div 
                className="relative bg-black flex items-center justify-center cursor-pointer group/vid overflow-hidden"
                style={{ aspectRatio: '9 / 12' }}
                onClick={() => setSelectedShort(short)}
              >
                <video
                  src={`/api/shorts/${short.id}/preview`}
                  preload="metadata"
                  className="w-full h-full object-cover group-hover/vid:scale-105 transition-transform duration-300"
                />

                {/* Overlay Play Action */}
                <div className="absolute inset-0 bg-black/40 group-hover/vid:bg-black/20 flex items-center justify-center transition-colors">
                  <div className="w-12 h-12 rounded-full bg-red-600/90 text-white flex items-center justify-center shadow-lg group-hover/vid:scale-110 transition-transform">
                    <Play className="w-5 h-5 fill-current ml-0.5" />
                  </div>
                </div>

                {/* Top Overlay Badges */}
                <div className="absolute top-3 left-3 right-3 flex items-center justify-between z-10">
                  <span className="bg-black/80 backdrop-blur-md px-2.5 py-1 rounded-lg text-xs font-semibold text-white">
                    {formatChannelName(short.channel)}
                  </span>
                  <div className="flex items-center space-x-1.5">
                    <span className="bg-red-600 text-white px-2.5 py-1 rounded-lg text-xs font-black shadow-md">
                      {short.score}/100
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setShortToDelete(short);
                      }}
                      title="Delete Short"
                      className="p-1.5 rounded-lg bg-black/60 backdrop-blur-md text-slate-400 hover:text-red-400 hover:bg-red-500/20 transition-all shadow-md group/trash"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Duration Badge */}
                <div className="absolute bottom-3 right-3 bg-black/80 px-2 py-0.5 rounded text-[11px] font-mono text-slate-200">
                  {formatDuration(short.duration)}
                </div>

                {/* Engine Badge */}
                <div className="absolute bottom-3 left-3 bg-black/80 px-2 py-0.5 rounded text-[10px] uppercase font-bold text-slate-300">
                  {short.processing_engine || 'openshorts'}
                </div>
              </div>

              {/* Card Body */}
              <div className="p-5 flex-1 flex flex-col justify-between space-y-4">
                {/* Viral Title Selection Section */}
                <div className="space-y-3">
                  <div>
                    <label className="text-[11px] uppercase tracking-wider font-bold text-slate-400 mb-1.5 flex items-center space-x-1">
                      <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                      <span>Select Viral Title</span>
                    </label>
                    <div className="text-sm font-bold text-white bg-[#182030] p-3 rounded-xl border border-[#24334f]">
                      {currentTitle}
                    </div>
                  </div>

                  {/* 3 Alternate Viral Title Options */}
                  {short.viral_titles && short.viral_titles.length > 0 && (
                    <div className="space-y-1.5">
                      <span className="text-[10px] text-slate-500 font-semibold uppercase">AI Suggestions:</span>
                      <div className="flex flex-col gap-1.5">
                        {short.viral_titles.slice(0, 3).map((vt, vIdx) => {
                          const tText = typeof vt === 'object' ? vt.title : vt;
                          const isSelected = tText === currentTitle;
                          return (
                            <button
                              key={vIdx}
                              onClick={() => handleSelectTitle(short.id, tText)}
                              className={`text-left text-xs p-2 rounded-lg border transition-all ${
                                isSelected
                                  ? 'bg-blue-600/20 border-blue-500 text-blue-300 font-semibold'
                                  : 'bg-[#182030]/60 hover:bg-[#182030] border-[#24334f] text-slate-400'
                              }`}
                            >
                              <div className="line-clamp-1">{tText}</div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Collapsible Description & Tags */}
                  <div>
                    <button
                      onClick={() => setExpandedDescId(isExpanded ? null : short.id)}
                      className="text-xs text-slate-400 hover:text-slate-200 flex items-center space-x-1 font-semibold"
                    >
                      <FileText className="w-3.5 h-3.5" />
                      <span>{isExpanded ? 'Hide' : 'Show'} Description & Tags</span>
                      {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                    </button>

                    {isExpanded && (
                      <div className="mt-2 p-3 bg-[#182030] rounded-xl border border-[#24334f] space-y-2 text-xs">
                        <p className="text-slate-300 whitespace-pre-line line-clamp-4">
                          {short.description || 'No description generated.'}
                        </p>
                        {short.tags && short.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 pt-1 border-t border-[#24334f]">
                            {short.tags.slice(0, 8).map((tg, tIdx) => (
                              <span key={tIdx} className="text-[10px] text-blue-400 bg-blue-500/10 px-1.5 py-0.5 rounded">
                                #{tg.replace('#', '')}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                {/* Upload & Actions Section */}
                <div className="pt-3 border-t border-[#1f293d]/80 space-y-3">
                  {isUploaded ? (
                    <div className="flex items-center justify-between p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl">
                      <div className="flex items-center space-x-2 text-xs font-bold text-emerald-400">
                        <CheckCircle2 className="w-4 h-4" />
                        <span>Uploaded to YouTube</span>
                      </div>
                      {short.youtube_url && (
                        <a
                          href={short.youtube_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center space-x-1 text-xs font-semibold text-blue-400 hover:text-blue-300"
                        >
                          <span>Watch</span>
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <select
                          value={currentPrivacy}
                          onChange={(e) => handlePrivacyChange(short.id, e.target.value)}
                          className="bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-red-500"
                        >
                          <option value="public">Public</option>
                          <option value="unlisted">Unlisted</option>
                          <option value="private">Private</option>
                        </select>

                        <button
                          onClick={() => handleUploadSingle(short)}
                          disabled={isUploadingThis || !quota?.can_upload}
                          className="flex-1 flex items-center justify-center space-x-1.5 px-3 py-2 text-xs font-semibold text-white bg-red-600 hover:bg-red-500 disabled:opacity-50 rounded-xl transition-all shadow-sm shadow-red-500/20"
                        >
                          {isUploadingThis ? (
                            <>
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              <span>Uploading...</span>
                            </>
                          ) : (
                            <>
                              <Upload className="w-3.5 h-3.5" />
                              <span>Upload to YouTube</span>
                            </>
                          )}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}

        {!shorts.length && (
          <div className="col-span-full py-16 text-center bg-[#121722] border border-[#1f293d] rounded-2xl text-slate-400">
            <Youtube className="w-12 h-12 mx-auto text-slate-600 mb-3" />
            <h3 className="text-lg font-bold text-slate-300">No vertical Shorts processed yet</h3>
            <p className="text-xs text-slate-500 mt-1">Go to the Clips tab and click "Process to Short" on any highlight clip.</p>
          </div>
        )}
      </div>

      {/* Phone-Frame Modal Player */}
      {selectedShort && (
        <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4">
          <div className="relative flex flex-col items-center">
            {/* Close button */}
            <button
              onClick={() => setSelectedShort(null)}
              className="absolute -top-12 right-0 p-2 text-slate-400 hover:text-white rounded-full bg-slate-800 transition-colors"
            >
              <X className="w-6 h-6" />
            </button>

            {/* Smartphone Mockup Frame */}
            <div 
              className="relative rounded-[40px] border-[10px] border-slate-800 bg-black shadow-2xl overflow-hidden flex flex-col"
              style={{ width: '360px', height: '640px' }}
            >
              {/* Notch */}
              <div className="absolute top-2 left-1/2 -translate-x-1/2 w-32 h-4 bg-slate-800 rounded-full z-20 pointer-events-none" />

              {/* Video Player */}
              <video
                src={`/api/shorts/${selectedShort.id}/preview`}
                controls
                autoPlay
                loop
                playsInline
                className="w-full h-full object-cover"
              />

              {/* Floating UI Elements (Shorts Style) */}
              <div className="absolute right-3 bottom-20 flex flex-col items-center space-y-4 text-white z-10 pointer-events-none">
                <div className="flex flex-col items-center">
                  <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-md flex items-center justify-center">
                    <ThumbsUp className="w-5 h-5" />
                  </div>
                  <span className="text-[10px] mt-1 font-bold">{selectedShort.score * 12}</span>
                </div>
                <div className="flex flex-col items-center">
                  <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-md flex items-center justify-center">
                    <MessageSquare className="w-5 h-5" />
                  </div>
                  <span className="text-[10px] mt-1 font-bold">142</span>
                </div>
                <div className="flex flex-col items-center">
                  <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-md flex items-center justify-center">
                    <Share2 className="w-5 h-5" />
                  </div>
                  <span className="text-[10px] mt-1 font-bold">Share</span>
                </div>
              </div>

              {/* Bottom Title Overlay */}
              <div className="absolute bottom-4 left-4 right-16 text-white z-10 pointer-events-none">
                <span className="text-xs font-bold text-slate-300 mb-1 block">@{formatChannelName(selectedShort.channel)}</span>
                <p className="text-xs font-semibold line-clamp-2 drop-shadow-md">
                  {activeTitles[selectedShort.id] || selectedShort.selected_title}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Delete Single Short Confirmation Modal */}
      {shortToDelete && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6 space-y-4">
            <div className="flex items-center space-x-3 text-red-400">
              <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/20">
                <AlertTriangle className="w-5 h-5 text-red-400" />
              </div>
              <h3 className="text-base font-bold text-white">Delete Short</h3>
            </div>

            <p className="text-sm text-slate-300">
              Delete this Short? This cannot be undone.
            </p>

            {/* Warning if already uploaded to YouTube */}
            {(shortToDelete.state === 'UPLOADED' || shortToDelete.youtube_url || shortToDelete.youtube_id) && (
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3 flex items-start space-x-2.5 text-xs text-amber-200">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  This Short was uploaded to YouTube. Deleting it locally won't remove the YouTube copy.
                </span>
              </div>
            )}

            <div className="bg-[#182030] p-3 rounded-xl border border-[#24334f] text-xs space-y-1">
              <div className="font-bold text-white line-clamp-1">
                {activeTitles[shortToDelete.id] || shortToDelete.selected_title || shortToDelete.title}
              </div>
              <div className="text-slate-400 flex items-center justify-between">
                <span>Channel: {formatChannelName(shortToDelete.channel)}</span>
                <span>Score: {shortToDelete.score}/100</span>
              </div>
            </div>

            <div className="flex justify-end space-x-3 pt-2">
              <button
                onClick={() => setShortToDelete(null)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDeleteSingle}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-xl text-xs font-semibold transition-colors shadow-lg shadow-red-600/20"
              >
                Delete Short
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete All Shorts Confirmation Modal */}
      {showDeleteAllModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-[#121722] border border-[#1f293d] rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6 space-y-4">
            <div className="flex items-center space-x-3 text-red-400">
              <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/20">
                <AlertTriangle className="w-5 h-5 text-red-400" />
              </div>
              <h3 className="text-base font-bold text-white">Delete All Shorts</h3>
            </div>

            <p className="text-sm text-slate-300">
              Delete ALL Shorts? This will remove {shorts.length} Short{shorts.length === 1 ? '' : 's'} permanently.
            </p>

            <p className="text-xs text-slate-400">
              All vertical 9:16 video files, JSON sidecars, and manifest entries will be deleted from disk. Any Shorts already uploaded to YouTube will remain unaffected on YouTube.
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

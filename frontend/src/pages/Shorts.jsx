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
  Trash2,
  AlertTriangle
} from 'lucide-react';
import { formatDuration, formatDateTime, formatRelativeTime, deleteSingleShort, deleteAllShorts } from '../api';
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
  const [uploadingId, setUploadingId] = useState(null);
  const [privacyMap, setPrivacyMap] = useState({});
  const [shortToDelete, setShortToDelete] = useState(null);
  const [showDeleteAllModal, setShowDeleteAllModal] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [isDeletingAll, setIsDeletingAll] = useState(false);

  const handlePrivacyChange = (shortId, privacy) => {
    setPrivacyMap(prev => ({ ...prev, [shortId]: privacy }));
  };

  const handleUploadSingle = async (short) => {
    setUploadingId(short.id);
    const chosenTitle = short.selected_title || short.title;
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
      {/* Header and Batch Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center space-x-2">
            <span>Vertical YouTube Shorts</span>
            <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-red-500/10 text-red-400 border border-red-500/20">
              {shorts.length} Shorts
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Vertical 9:16 reframed Shorts optimized for YouTube.
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
            disabled={isUploading || !shorts.length || (quota && quota.can_upload === false)}
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

      {/* Grid of Clean Vertical 9:16 Shorts Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
        {shorts.map((short) => {
          const isUploaded = short.state === 'UPLOADED' || Boolean(short.youtube_url) || Boolean(short.youtube_id);
          const isUploadingThis = uploadingId === short.id;
          const currentPrivacy = privacyMap[short.id] || 'public';
          const viralTitle = short.selected_title || short.title || 'Live Stream Highlight';

          return (
            <div
              key={short.id}
              className={`bg-[#121722] border border-[#1f293d] rounded-2xl overflow-hidden flex flex-col justify-between hover:border-slate-600 transition-all duration-300 ${
                deletingId === short.id ? 'opacity-0 scale-95 pointer-events-none' : 'opacity-100 scale-100'
              }`}
            >
              {/* Vertical 9:16 Video Thumbnail Container */}
              <div 
                className="relative bg-black flex items-center justify-center cursor-pointer group/vid overflow-hidden"
                style={{ aspectRatio: '9 / 16' }}
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

                {/* Top Overlay Badges: Channel Name & Score Badge */}
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
                      className="p-1.5 rounded-lg bg-black/60 backdrop-blur-md text-slate-400 hover:text-red-400 hover:bg-red-500/20 transition-all shadow-md"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Duration Badge */}
                <div className="absolute bottom-3 right-3 bg-black/80 px-2 py-0.5 rounded text-[11px] font-mono text-slate-200">
                  {formatDuration(short.duration)}
                </div>

                {/* Upload Status Overlay if already uploaded */}
                {isUploaded && (
                  <div className="absolute bottom-3 left-3 flex items-center space-x-1 bg-emerald-600/90 backdrop-blur-md px-2 py-0.5 rounded text-[11px] font-bold text-white shadow-md">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>Uploaded</span>
                  </div>
                )}
              </div>

              {/* Card Body */}
              <div className="p-4 flex-1 flex flex-col justify-between space-y-3">
                <div className="space-y-2">
                  {/* Viral Title */}
                  <h3 className="font-bold text-sm text-slate-100 line-clamp-2" title={viralTitle}>
                    {viralTitle}
                  </h3>

                  {/* Dates Recorded & Processed */}
                  <div className="space-y-1 text-[11px] text-slate-400 pt-1 border-t border-[#1f293d]/60">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Recorded:</span>
                      <span className="text-slate-300 font-medium">{formatDateTime(short.recorded_at_iso) || 'Recent stream'}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Processed:</span>
                      <span className="text-slate-300 font-medium">{formatDateTime(short.processed_at_iso) || 'Completed'}</span>
                    </div>
                  </div>
                </div>

                {/* Upload Button & Status Section */}
                <div className="pt-2 border-t border-[#1f293d]/80">
                  {isUploaded ? (
                    <div className="flex items-center justify-between p-2.5 bg-emerald-500/10 border border-emerald-500/20 rounded-xl">
                      <div className="flex items-center space-x-1.5 text-xs font-bold text-emerald-400">
                        <CheckCircle2 className="w-4 h-4 shrink-0" />
                        <span>Uploaded</span>
                      </div>
                      {(short.youtube_url || short.youtube_id) && (
                        <a
                          href={short.youtube_url || `https://youtube.com/shorts/${short.youtube_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center space-x-1 text-xs font-semibold text-blue-400 hover:text-blue-300 ml-2"
                        >
                          <span>YouTube Link</span>
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <select
                        value={currentPrivacy}
                        onChange={(e) => handlePrivacyChange(short.id, e.target.value)}
                        className="bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-red-500 shrink-0"
                      >
                        <option value="public">Public</option>
                        <option value="unlisted">Unlisted</option>
                        <option value="private">Private</option>
                      </select>

                      <button
                        onClick={() => handleUploadSingle(short)}
                        disabled={isUploadingThis || (quota && quota.can_upload === false)}
                        className="flex-1 flex items-center justify-center space-x-1 px-3 py-1.5 text-xs font-semibold text-white bg-red-600 hover:bg-red-500 disabled:opacity-50 rounded-xl transition-all shadow-sm shadow-red-500/20"
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

      {/* Simple HTML5 Video Modal Player (Clean, Standard Controls, No Fake UI) */}
      {selectedShort && (
        <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4">
          <div className="relative bg-[#121722] border border-[#1f293d] rounded-2xl overflow-hidden shadow-2xl flex flex-col max-w-lg w-full max-h-[90vh]">
            {/* Modal Header */}
            <div className="p-4 bg-[#182030] border-b border-[#24334f] flex items-center justify-between">
              <div>
                <h4 className="text-sm font-bold text-white line-clamp-1">
                  {selectedShort.selected_title || selectedShort.title}
                </h4>
                <div className="flex items-center space-x-2 text-xs text-slate-400 mt-0.5">
                  <span>{formatChannelName(selectedShort.channel)}</span>
                  <span>•</span>
                  <span>Score: {selectedShort.score}/100</span>
                  <span>•</span>
                  <span>{formatDuration(selectedShort.duration)}</span>
                  {selectedShort.recorded_at_iso && (
                    <>
                      <span>•</span>
                      <span>Recorded: {formatDateTime(selectedShort.recorded_at_iso)}</span>
                    </>
                  )}
                </div>
              </div>
              <button
                onClick={() => setSelectedShort(null)}
                className="p-1.5 text-slate-400 hover:text-white rounded-lg bg-slate-800 transition-colors ml-2"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Video Player */}
            <div className="flex-1 bg-black flex items-center justify-center p-2 overflow-hidden">
              <video
                src={`/api/shorts/${selectedShort.id}/preview`}
                controls
                autoPlay
                playsInline
                className="max-h-[70vh] rounded-xl object-contain shadow-md"
              />
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
                {shortToDelete.selected_title || shortToDelete.title}
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

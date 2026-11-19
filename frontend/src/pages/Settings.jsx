import React, { useState, useEffect } from 'react';
import { 
  Save, 
  CheckCircle2, 
  AlertCircle, 
  Sliders, 
  Tv, 
  Youtube, 
  Film, 
  BrainCircuit, 
  ShieldCheck,
  RefreshCw
} from 'lucide-react';

export default function Settings({ settings = {}, onSave, isSaving }) {
  const [formData, setFormData] = useState({
    platform: 'twitch',
    target_channels: '',
    target_youtube_channels: '',
    target_languages: 'en,hi',
    multi_stream_enabled: false,
    max_streams: 2,
    recording_enabled: true,
    recording_quality: '1080p60,1080p,720p60,best',
    shorts_engine: 'openshorts',
    subtitle_style: 'none',
    reframe_mode: 'wide',
    youtube_privacy_status: 'public',
    youtube_min_score: 78,
    youtube_daily_limit: 5,
    youtube_auto_upload: false,
    viral_metadata_enabled: true,
    gemini_model: 'gemini-3.1-flash-lite',
    gemini_api_key: '',
  });

  const [savedSuccess, setSavedSuccess] = useState(false);

  useEffect(() => {
    if (settings && Object.keys(settings).length > 0) {
      setFormData(prev => ({
        ...prev,
        ...settings,
        gemini_api_key: '', // Don't expose key directly unless user enters it
      }));
    }
  }, [settings]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
    setSavedSuccess(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const payload = { ...formData };
    if (!payload.gemini_api_key) {
      delete payload.gemini_api_key;
    }
    await onSave(payload);
    setSavedSuccess(true);
    setTimeout(() => setSavedSuccess(false), 3000);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6 max-w-5xl mx-auto">
      {/* Header & Save Action */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center space-x-2">
            <Sliders className="w-5 h-5 text-blue-400" />
            <span>Pipeline Configuration</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Edit and persist core settings directly into your local <code>backend/.env</code> configuration file.
          </p>
        </div>

        <button
          type="submit"
          disabled={isSaving}
          className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-semibold rounded-xl text-sm transition-all shadow-lg shadow-blue-500/20"
        >
          {savedSuccess ? (
            <>
              <CheckCircle2 className="w-4 h-4 text-white" />
              <span>Settings Saved!</span>
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              <span>{isSaving ? 'Saving...' : 'Save Settings'}</span>
            </>
          )}
        </button>
      </div>

      {/* Grid of Settings Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* 1. Ingestion & Platform Settings */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center space-x-2 text-white font-bold border-b border-[#1f293d] pb-3">
            <Tv className="w-4 h-4 text-purple-400" />
            <span>Stream Platforms & Channels</span>
          </div>

          <div className="space-y-3 text-xs">
            <div>
              <label className="block text-slate-400 font-semibold mb-1">Monitoring Platform</label>
              <select
                name="platform"
                value={formData.platform}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              >
                <option value="twitch">Twitch Only</option>
                <option value="youtube">YouTube Live Only</option>
                <option value="all">Both (Twitch & YouTube)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Target Twitch Channels (comma-separated)</label>
              <input
                type="text"
                name="target_channels"
                value={formData.target_channels}
                onChange={handleChange}
                placeholder="kaicenat, xqc, shroud"
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 font-mono text-xs focus:outline-none focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Target YouTube Channel IDs (comma-separated)</label>
              <input
                type="text"
                name="target_youtube_channels"
                value={formData.target_youtube_channels}
                onChange={handleChange}
                placeholder="UCWsDFcIhY2DBi3GB5uykGXA, UC0IWRLai-BAwci_e9MylNGw"
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 font-mono text-xs focus:outline-none focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Language Filter Codes</label>
              <input
                type="text"
                name="target_languages"
                value={formData.target_languages}
                onChange={handleChange}
                placeholder="en, hi"
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>

        {/* 2. Multi-Stream & Recording Quality */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center space-x-2 text-white font-bold border-b border-[#1f293d] pb-3">
            <Sliders className="w-4 h-4 text-blue-400" />
            <span>Multi-Stream & Capture Quality</span>
          </div>

          <div className="space-y-4 text-xs">
            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Multi-Stream Mode</div>
                <div className="text-[11px] text-slate-400">Record and monitor multiple streams concurrently</div>
              </div>
              <input
                type="checkbox"
                name="multi_stream_enabled"
                checked={formData.multi_stream_enabled}
                onChange={handleChange}
                className="w-5 h-5 accent-blue-600 rounded cursor-pointer"
              />
            </div>

            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Live Recording (Streamlink)</div>
                <div className="text-[11px] text-slate-400">Capture rolling TS segments for highlight extraction</div>
              </div>
              <input
                type="checkbox"
                name="recording_enabled"
                checked={formData.recording_enabled}
                onChange={handleChange}
                className="w-5 h-5 accent-blue-600 rounded cursor-pointer"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Max Concurrent Streams</label>
              <input
                type="number"
                name="max_streams"
                min="1"
                max="6"
                value={formData.max_streams}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Recording Quality Streamlink Profile</label>
              <input
                type="text"
                name="recording_quality"
                value={formData.recording_quality}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 font-mono text-xs focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>

        {/* 3. Shorts Video Processing Pipeline */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center space-x-2 text-white font-bold border-b border-[#1f293d] pb-3">
            <Film className="w-4 h-4 text-cyan-400" />
            <span>Vertical 9:16 Shorts Engine</span>
          </div>

          <div className="space-y-3 text-xs">
            <div>
              <label className="block text-slate-400 font-semibold mb-1">Shorts Processing Engine</label>
              <select
                name="shorts_engine"
                value={formData.shorts_engine}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              >
                <option value="openshorts">OpenShorts (Internal Video Pipeline)</option>
                <option value="auto">Auto (OpenShorts with FFmpeg fallback)</option>
                <option value="ffmpeg_fallback">FFmpeg Fallback (Lightweight blur-pad)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Vertical Reframing Mode</label>
              <select
                name="reframe_mode"
                value={formData.reframe_mode}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              >
                <option value="wide">Wide (Cinematic blur-backdrop padding)</option>
                <option value="split">Split (Top speaker + bottom gameplay)</option>
                <option value="track">Track (Smooth camera following)</option>
                <option value="auto">Auto (Detect layout from scene composition)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Subtitle Styling Preset</label>
              <select
                name="subtitle_style"
                value={formData.subtitle_style}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              >
                <option value="none">None (No subtitles burned)</option>
                <option value="bold">Bold (Impact font with bright yellow highlight)</option>
                <option value="pill">Pill (Clean modern rounded box)</option>
                <option value="hormozi">Hormozi (High-energy animated pop captions)</option>
                <option value="default">Default (White text with black outline)</option>
                <option value="clean">Clean (Minimal subtle subtitles)</option>
              </select>
            </div>
          </div>
        </div>

        {/* 4. YouTube Upload Settings */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center space-x-2 text-white font-bold border-b border-[#1f293d] pb-3">
            <Youtube className="w-4 h-4 text-red-500" />
            <span>YouTube Shorts Publication</span>
          </div>

          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Auto-Upload Mode</div>
                <div className="text-[11px] text-slate-400">Automatically upload shorts meeting score criteria</div>
              </div>
              <input
                type="checkbox"
                name="youtube_auto_upload"
                checked={formData.youtube_auto_upload}
                onChange={handleChange}
                className="w-5 h-5 accent-red-600 rounded cursor-pointer"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Default Privacy Status</label>
              <select
                name="youtube_privacy_status"
                value={formData.youtube_privacy_status}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
              >
                <option value="public">Public (Default - Immediately live)</option>
                <option value="unlisted">Unlisted (Hidden from search)</option>
                <option value="private">Private</option>
              </select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-slate-400 font-semibold mb-1">Min Score for Upload</label>
                <input
                  type="number"
                  name="youtube_min_score"
                  min="0"
                  max="100"
                  value={formData.youtube_min_score}
                  onChange={handleChange}
                  className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 font-semibold mb-1">Daily Upload Limit</label>
                <input
                  type="number"
                  name="youtube_daily_limit"
                  min="1"
                  max="10"
                  value={formData.youtube_daily_limit}
                  onChange={handleChange}
                  className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>
        </div>

        {/* 5. AI & Gemini Highlight Analysis */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4 md:col-span-2">
          <div className="flex items-center space-x-2 text-white font-bold border-b border-[#1f293d] pb-3">
            <BrainCircuit className="w-4 h-4 text-indigo-400" />
            <span>Gemini AI Highlight Detection & Metadata</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div>
              <label className="block text-slate-400 font-semibold mb-1">Gemini Model</label>
              <input
                type="text"
                name="gemini_model"
                value={formData.gemini_model}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500 font-mono text-xs"
              />
            </div>

            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Viral Metadata Generator</div>
                <div className="text-[11px] text-slate-400">Generate 3 curiosity-gap titles, tags, and description via Gemini</div>
              </div>
              <input
                type="checkbox"
                name="viral_metadata_enabled"
                checked={formData.viral_metadata_enabled}
                onChange={handleChange}
                className="w-5 h-5 accent-indigo-600 rounded cursor-pointer"
              />
            </div>

            <div className="md:col-span-2">
              <label className="block text-slate-400 font-semibold mb-1">
                Gemini API Key (Leave empty to keep existing key)
              </label>
              <input
                type="password"
                name="gemini_api_key"
                value={formData.gemini_api_key}
                onChange={handleChange}
                placeholder="AIzaSy... (hidden)"
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 font-mono text-xs focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>
      </div>
    </form>
  );
}

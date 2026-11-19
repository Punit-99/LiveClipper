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
  RefreshCw,
  Lock,
  Unlock,
  Plus,
  X,
  Loader2,
  Globe
} from 'lucide-react';
import { resolveChannel } from '../api';

const AVAILABLE_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'Hindi' },
  { code: 'es', label: 'Spanish' },
  { code: 'pt', label: 'Portuguese' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
];

export default function Settings({ settings = {}, onSave, isSaving }) {
  // Card Lock States (All locked by default)
  const [unlockedCards, setUnlockedCards] = useState({
    platform: false,
    twitch: false,
    youtube: false,
    multistream: false,
    shorts: false,
    youtube_pub: false,
    gemini: false,
  });

  const [formData, setFormData] = useState({
    platform: 'twitch',
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

  // Dynamic Channel Rows
  // YouTube: [{ id: 'UC...', name: 'IShowSpeed', active: true, resolving: false }]
  const [ytChannels, setYtChannels] = useState([]);
  // Twitch: [{ name: 'kaicenat', active: true }]
  const [twitchChannels, setTwitchChannels] = useState([]);
  // Language checkboxes: ['en', 'hi']
  const [selectedLanguages, setSelectedLanguages] = useState(['en', 'hi']);

  const [savedSuccess, setSavedSuccess] = useState(false);

  // Initialize state from backend settings
  useEffect(() => {
    if (settings && Object.keys(settings).length > 0) {
      setFormData(prev => ({
        ...prev,
        ...settings,
        gemini_api_key: '',
      }));

      // Parse YouTube channels (active + excluded)
      const activeYt = (settings.target_youtube_channels || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);
      const excludedYt = (settings.excluded_youtube_channels || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);

      const combinedYt = [
        ...activeYt.map(id => ({ id, name: '', active: true, resolving: false })),
        ...excludedYt.map(id => ({ id, name: '', active: false, resolving: false })),
      ];
      setYtChannels(combinedYt.length > 0 ? combinedYt : [{ id: '', name: '', active: true, resolving: false }]);

      // Parse Twitch channels (active + excluded)
      const activeTw = (settings.target_channels || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);
      const excludedTw = (settings.excluded_target_channels || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);

      const combinedTw = [
        ...activeTw.map(name => ({ name, active: true })),
        ...excludedTw.map(name => ({ name, active: false })),
      ];
      setTwitchChannels(combinedTw.length > 0 ? combinedTw : [{ name: '', active: true }]);

      // Parse Languages
      const langs = (settings.target_languages || 'en,hi')
        .split(',')
        .map(s => s.trim().toLowerCase())
        .filter(Boolean);
      setSelectedLanguages(langs.length > 0 ? langs : ['en', 'hi']);
    }
  }, [settings]);

  // Resolve YouTube channel names on mount or when new IDs appear without names
  useEffect(() => {
    ytChannels.forEach((item, index) => {
      if (item.id && item.id.trim() && !item.name && !item.resolving) {
        handleResolveYt(index, item.id);
      }
    });
  }, [ytChannels.map(c => c.id).join(',')]);

  const toggleLock = (cardKey) => {
    setUnlockedCards(prev => ({
      ...prev,
      [cardKey]: !prev[cardKey],
    }));
  };

  const isAnyCardUnlocked = Object.values(unlockedCards).some(Boolean);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
    setSavedSuccess(false);
  };

  // --- YouTube Channel Row Helpers ---
  const handleResolveYt = async (index, channelId) => {
    const cleanId = (channelId || '').trim();
    if (!cleanId) return;

    setYtChannels(prev => prev.map((item, idx) => idx === index ? { ...item, resolving: true } : item));

    try {
      const res = await resolveChannel(cleanId);
      setYtChannels(prev => prev.map((item, idx) => {
        if (idx === index) {
          return {
            ...item,
            name: res && res.name ? res.name : cleanId,
            resolving: false,
          };
        }
        return item;
      }));
    } catch {
      setYtChannels(prev => prev.map((item, idx) => idx === index ? { ...item, resolving: false } : item));
    }
  };

  const handleYtIdChange = (index, val) => {
    setYtChannels(prev => prev.map((item, idx) => {
      if (idx === index) {
        return { ...item, id: val, name: '' };
      }
      return item;
    }));
    setSavedSuccess(false);
  };

  const handleYtActiveToggle = (index) => {
    setYtChannels(prev => prev.map((item, idx) => {
      if (idx === index) {
        return { ...item, active: !item.active };
      }
      return item;
    }));
    setSavedSuccess(false);
  };

  const addYtChannel = () => {
    setYtChannels(prev => [...prev, { id: '', name: '', active: true, resolving: false }]);
  };

  const removeYtChannel = (index) => {
    setYtChannels(prev => prev.filter((_, idx) => idx !== index));
    setSavedSuccess(false);
  };

  // --- Twitch Channel Row Helpers ---
  const handleTwitchNameChange = (index, val) => {
    setTwitchChannels(prev => prev.map((item, idx) => {
      if (idx === index) {
        return { ...item, name: val };
      }
      return item;
    }));
    setSavedSuccess(false);
  };

  const handleTwitchActiveToggle = (index) => {
    setTwitchChannels(prev => prev.map((item, idx) => {
      if (idx === index) {
        return { ...item, active: !item.active };
      }
      return item;
    }));
    setSavedSuccess(false);
  };

  const addTwitchChannel = () => {
    setTwitchChannels(prev => [...prev, { name: '', active: true }]);
  };

  const removeTwitchChannel = (index) => {
    setTwitchChannels(prev => prev.filter((_, idx) => idx !== index));
    setSavedSuccess(false);
  };

  // --- Language Checkbox Helpers ---
  const toggleLanguage = (code) => {
    setSelectedLanguages(prev => {
      if (prev.includes(code)) {
        if (prev.length === 1) return prev; // Keep at least one language
        return prev.filter(c => c !== code);
      } else {
        return [...prev, code];
      }
    });
    setSavedSuccess(false);
  };

  // --- Submit Handler ---
  const handleSubmit = async (e) => {
    e.preventDefault();

    const activeYtIds = ytChannels
      .filter(c => c.active && c.id && c.id.trim())
      .map(c => c.id.trim());
    const excludedYtIds = ytChannels
      .filter(c => !c.active && c.id && c.id.trim())
      .map(c => c.id.trim());

    const activeTwitchNames = twitchChannels
      .filter(c => c.active && c.name && c.name.trim())
      .map(c => c.name.trim().toLowerCase());
    const excludedTwitchNames = twitchChannels
      .filter(c => !c.active && c.name && c.name.trim())
      .map(c => c.name.trim().toLowerCase());

    const payload = {
      ...formData,
      target_youtube_channels: activeYtIds.join(','),
      excluded_youtube_channels: excludedYtIds.join(','),
      target_channels: activeTwitchNames.join(','),
      excluded_target_channels: excludedTwitchNames.join(','),
      target_languages: selectedLanguages.join(','),
    };

    if (!payload.gemini_api_key) {
      delete payload.gemini_api_key;
    }

    await onSave(payload);
    setSavedSuccess(true);
    setTimeout(() => setSavedSuccess(false), 3000);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6 max-w-5xl mx-auto pb-12">
      {/* Header & Save Action */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#121722] border border-[#1f293d] rounded-2xl p-6">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center space-x-2">
            <Sliders className="w-5 h-5 text-blue-400" />
            <span>Pipeline Configuration</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Edit settings cards by unlocking them. Changes persist directly into your local <code>backend/.env</code> file.
          </p>
        </div>

        {isAnyCardUnlocked ? (
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
        ) : (
          <div className="flex items-center space-x-2 text-xs text-slate-400 bg-[#182030] px-4 py-2.5 rounded-xl border border-[#24334f]">
            <Lock className="w-4 h-4 text-slate-400" />
            <span>Unlock any card to enable saving</span>
          </div>
        )}
      </div>

      {/* Grid of Settings Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

        {/* 1. Monitoring Platform & Language Filter */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <Globe className="w-4 h-4 text-purple-400" />
              <span>Platform & Language Settings</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('platform')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.platform
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.platform ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.platform ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`space-y-4 text-xs transition-opacity ${unlockedCards.platform ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div>
              <label className="block text-slate-400 font-semibold mb-1">Monitoring Platform Mode</label>
              <select
                name="platform"
                disabled={!unlockedCards.platform}
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
              <label className="block text-slate-400 font-semibold mb-2">Target Stream Languages</label>
              <div className="grid grid-cols-2 gap-2">
                {AVAILABLE_LANGUAGES.map(lang => {
                  const isChecked = selectedLanguages.includes(lang.code);
                  return (
                    <label
                      key={lang.code}
                      className={`flex items-center space-x-2.5 p-2 rounded-lg border transition-all cursor-pointer ${
                        isChecked
                          ? 'bg-blue-950/30 border-blue-500/40 text-blue-200'
                          : 'bg-[#182030] border-[#24334f] text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      <input
                        type="checkbox"
                        disabled={!unlockedCards.platform}
                        checked={isChecked}
                        onChange={() => toggleLanguage(lang.code)}
                        className="w-4 h-4 accent-blue-600 rounded cursor-pointer"
                      />
                      <span className="font-medium text-xs text-white">{lang.label}</span>
                      <span className="text-[10px] text-slate-500 font-mono ml-auto">({lang.code})</span>
                    </label>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* 2. YouTube Target Channels */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <Youtube className="w-4 h-4 text-red-500" />
              <span>YouTube Live Channels</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('youtube')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.youtube
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.youtube ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.youtube ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`space-y-3 text-xs transition-opacity ${unlockedCards.youtube ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div className="flex items-center justify-between text-slate-400 text-[11px] pb-1 border-b border-[#1f293d]/50">
              <span>Active</span>
              <span className="flex-1 ml-4">Channel ID & Resolved Name</span>
              <span>Remove</span>
            </div>

            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {ytChannels.map((ch, idx) => (
                <div key={idx} className="flex items-center space-x-2 bg-[#182030] p-2 rounded-xl border border-[#24334f]">
                  <input
                    type="checkbox"
                    disabled={!unlockedCards.youtube}
                    checked={ch.active}
                    onChange={() => handleYtActiveToggle(idx)}
                    title={ch.active ? "Monitored (Active)" : "Excluded from monitoring"}
                    className="w-4 h-4 accent-red-600 rounded cursor-pointer shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <input
                      type="text"
                      disabled={!unlockedCards.youtube}
                      value={ch.id}
                      onChange={(e) => handleYtIdChange(idx, e.target.value)}
                      onBlur={() => handleResolveYt(idx, ch.id)}
                      placeholder="UCWsDFcIhY2DBi3GB5uykGXA"
                      className="w-full bg-[#121722] text-slate-200 border border-[#1f293d] rounded-lg px-2.5 py-1 font-mono text-xs focus:outline-none focus:border-red-500"
                    />
                    <div className="flex items-center mt-1 space-x-1">
                      {ch.resolving ? (
                        <div className="flex items-center space-x-1 text-[11px] text-blue-400">
                          <Loader2 className="w-3 h-3 animate-spin" />
                          <span>Resolving name...</span>
                        </div>
                      ) : ch.name ? (
                        <span className="text-[11px] font-semibold text-emerald-400">
                          {ch.name}
                        </span>
                      ) : (
                        <span className="text-[10px] text-slate-500">Unresolved channel</span>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    disabled={!unlockedCards.youtube}
                    onClick={() => removeYtChannel(idx)}
                    className="text-red-400 hover:text-red-300 p-1.5 rounded-lg hover:bg-red-950/30 shrink-0"
                    title="Remove channel"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>

            <button
              type="button"
              disabled={!unlockedCards.youtube}
              onClick={addYtChannel}
              className="flex items-center space-x-1.5 text-xs text-red-400 hover:text-red-300 font-semibold px-3 py-1.5 rounded-lg border border-red-500/20 hover:border-red-500/40 bg-red-500/10 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add YouTube Channel</span>
            </button>
          </div>
        </div>

        {/* 3. Twitch Target Channels */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <Tv className="w-4 h-4 text-purple-400" />
              <span>Twitch Streamers</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('twitch')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.twitch
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.twitch ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.twitch ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`space-y-3 text-xs transition-opacity ${unlockedCards.twitch ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div className="flex items-center justify-between text-slate-400 text-[11px] pb-1 border-b border-[#1f293d]/50">
              <span>Active</span>
              <span className="flex-1 ml-4">Channel Username</span>
              <span>Remove</span>
            </div>

            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {twitchChannels.map((ch, idx) => (
                <div key={idx} className="flex items-center space-x-2 bg-[#182030] p-2 rounded-xl border border-[#24334f]">
                  <input
                    type="checkbox"
                    disabled={!unlockedCards.twitch}
                    checked={ch.active}
                    onChange={() => handleTwitchActiveToggle(idx)}
                    title={ch.active ? "Monitored (Active)" : "Excluded from monitoring"}
                    className="w-4 h-4 accent-purple-600 rounded cursor-pointer shrink-0"
                  />
                  <input
                    type="text"
                    disabled={!unlockedCards.twitch}
                    value={ch.name}
                    onChange={(e) => handleTwitchNameChange(idx, e.target.value)}
                    placeholder="e.g. kaicenat, xqc, shroud"
                    className="flex-1 bg-[#121722] text-slate-200 border border-[#1f293d] rounded-lg px-2.5 py-1 font-mono text-xs focus:outline-none focus:border-purple-500"
                  />
                  <button
                    type="button"
                    disabled={!unlockedCards.twitch}
                    onClick={() => removeTwitchChannel(idx)}
                    className="text-red-400 hover:text-red-300 p-1.5 rounded-lg hover:bg-red-950/30 shrink-0"
                    title="Remove channel"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>

            <button
              type="button"
              disabled={!unlockedCards.twitch}
              onClick={addTwitchChannel}
              className="flex items-center space-x-1.5 text-xs text-purple-400 hover:text-purple-300 font-semibold px-3 py-1.5 rounded-lg border border-purple-500/20 hover:border-purple-500/40 bg-purple-500/10 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add Twitch Channel</span>
            </button>
          </div>
        </div>

        {/* 4. Multi-Stream & Recording Quality */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <Sliders className="w-4 h-4 text-blue-400" />
              <span>Multi-Stream & Capture Quality</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('multistream')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.multistream
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.multistream ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.multistream ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`space-y-4 text-xs transition-opacity ${unlockedCards.multistream ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Multi-Stream Mode</div>
                <div className="text-[11px] text-slate-400">Record and monitor multiple streams concurrently</div>
              </div>
              <input
                type="checkbox"
                name="multi_stream_enabled"
                disabled={!unlockedCards.multistream}
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
                disabled={!unlockedCards.multistream}
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
                disabled={!unlockedCards.multistream}
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
                disabled={!unlockedCards.multistream}
                value={formData.recording_quality}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 font-mono text-xs focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>

        {/* 5. Shorts Video Processing Pipeline */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <Film className="w-4 h-4 text-cyan-400" />
              <span>Vertical 9:16 Shorts Engine</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('shorts')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.shorts
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.shorts ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.shorts ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`space-y-3 text-xs transition-opacity ${unlockedCards.shorts ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div>
              <label className="block text-slate-400 font-semibold mb-1">Shorts Processing Engine</label>
              <select
                name="shorts_engine"
                disabled={!unlockedCards.shorts}
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
                disabled={!unlockedCards.shorts}
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
                disabled={!unlockedCards.shorts}
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

        {/* 6. YouTube Shorts Publication */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <Youtube className="w-4 h-4 text-red-500" />
              <span>YouTube Shorts Publication</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('youtube_pub')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.youtube_pub
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.youtube_pub ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.youtube_pub ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`space-y-3 text-xs transition-opacity ${unlockedCards.youtube_pub ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Auto-Upload Mode</div>
                <div className="text-[11px] text-slate-400">Automatically upload shorts meeting score criteria</div>
              </div>
              <input
                type="checkbox"
                name="youtube_auto_upload"
                disabled={!unlockedCards.youtube_pub}
                checked={formData.youtube_auto_upload}
                onChange={handleChange}
                className="w-5 h-5 accent-red-600 rounded cursor-pointer"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-semibold mb-1">Default Privacy Status</label>
              <select
                name="youtube_privacy_status"
                disabled={!unlockedCards.youtube_pub}
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
                  disabled={!unlockedCards.youtube_pub}
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
                  disabled={!unlockedCards.youtube_pub}
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

        {/* 7. AI & Gemini Highlight Analysis */}
        <div className="bg-[#121722] border border-[#1f293d] rounded-2xl p-6 space-y-4 md:col-span-2">
          <div className="flex items-center justify-between border-b border-[#1f293d] pb-3">
            <div className="flex items-center space-x-2 text-white font-bold">
              <BrainCircuit className="w-4 h-4 text-indigo-400" />
              <span>Gemini AI Highlight Detection & Metadata</span>
            </div>
            <button
              type="button"
              onClick={() => toggleLock('gemini')}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                unlockedCards.gemini
                  ? 'text-emerald-400 bg-emerald-950/40 border border-emerald-500/40'
                  : 'text-slate-400 hover:text-slate-200 bg-[#182030] border border-[#24334f]'
              }`}
              title={unlockedCards.gemini ? "Lock card" : "Unlock card to edit"}
            >
              {unlockedCards.gemini ? (
                <>
                  <Unlock className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Unlocked</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  <span>Locked</span>
                </>
              )}
            </button>
          </div>

          <div className={`grid grid-cols-1 md:grid-cols-2 gap-4 text-xs transition-opacity ${unlockedCards.gemini ? '' : 'opacity-60 pointer-events-none select-none'}`}>
            <div>
              <label className="block text-slate-400 font-semibold mb-1">Gemini Model</label>
              <input
                type="text"
                name="gemini_model"
                disabled={!unlockedCards.gemini}
                value={formData.gemini_model}
                onChange={handleChange}
                className="w-full bg-[#182030] text-slate-200 border border-[#24334f] rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500 font-mono text-xs"
              />
            </div>

            <div className="flex items-center justify-between p-3 bg-[#182030] rounded-xl border border-[#24334f]">
              <div>
                <div className="font-semibold text-slate-200">Viral Metadata Generator</div>
                <div className="text-[11px] text-slate-400">Generate curiosity-gap titles, tags, and description via Gemini</div>
              </div>
              <input
                type="checkbox"
                name="viral_metadata_enabled"
                disabled={!unlockedCards.gemini}
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
                disabled={!unlockedCards.gemini}
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

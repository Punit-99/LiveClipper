import React from 'react';
import { 
  Activity, 
  Video, 
  Film, 
  Radio, 
  Settings as SettingsIcon, 
  Play, 
  Square,
  Sparkles,
  Youtube
} from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab, status, quota, onToggleMonitoring, isStarting }) {
  const isMonitoring = status?.is_monitoring;
  const activeCount = status?.active_channels?.length || 0;

  return (
    <header className="sticky top-0 z-40 bg-[#0d121d]/90 backdrop-blur-md border-b border-[#1f293d]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Brand / Logo */}
        <div className="flex items-center space-x-3 cursor-pointer" onClick={() => setActiveTab('dashboard')}>
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 via-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="font-bold text-lg tracking-tight text-white">LiveClipper</span>
              <span className="text-xs px-2 py-0.5 rounded-full font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20">
                v2.0
              </span>
            </div>
            <p className="text-xs text-slate-400 -mt-0.5">Automated AI Shorts Pipeline</p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="hidden md:flex items-center space-x-1 bg-[#121826] p-1 rounded-xl border border-[#1f293d]">
          <button
            onClick={() => setActiveTab('dashboard')}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'dashboard'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-[#1a2233]'
            }`}
          >
            <Activity className="w-4 h-4" />
            <span>Dashboard</span>
          </button>

          <button
            onClick={() => setActiveTab('clips')}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'clips'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-[#1a2233]'
            }`}
          >
            <Video className="w-4 h-4" />
            <span>Clips</span>
          </button>

          <button
            onClick={() => setActiveTab('shorts')}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'shorts'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-[#1a2233]'
            }`}
          >
            <Film className="w-4 h-4" />
            <span>Shorts</span>
          </button>

          <button
            onClick={() => setActiveTab('live')}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'live'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-[#1a2233]'
            }`}
          >
            <Radio className="w-4 h-4" />
            <span>Live Feed</span>
          </button>

          <button
            onClick={() => setActiveTab('settings')}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'settings'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-[#1a2233]'
            }`}
          >
            <SettingsIcon className="w-4 h-4" />
            <span>Settings</span>
          </button>
        </nav>

        {/* Status Indicators & Control */}
        <div className="flex items-center space-x-3">
          {/* YouTube Quota Pill */}
          <div className="hidden sm:flex items-center space-x-2 bg-[#121826] border border-[#1f293d] px-2.5 py-1.5 rounded-lg text-xs">
            <Youtube className="w-3.5 h-3.5 text-red-500" />
            <span className="text-slate-400">Quota:</span>
            <span className="font-semibold text-slate-200">
              {quota ? `${quota.uploads_today}/${quota.daily_limit}` : '0/5'}
            </span>
          </div>

          {/* Monitoring Status Pill */}
          <div className="flex items-center space-x-2 bg-[#121826] border border-[#1f293d] px-3 py-1.5 rounded-lg text-xs font-medium">
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                isMonitoring ? 'bg-emerald-500 animate-pulse' : 'bg-slate-500'
              }`}
            />
            <span className="text-slate-300">
              {isMonitoring ? `RECORDING (${activeCount})` : 'IDLE'}
            </span>
          </div>

          {/* Quick Start / Stop Button */}
          <button
            onClick={onToggleMonitoring}
            disabled={isStarting}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-sm font-semibold transition-all ${
              isMonitoring
                ? 'bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm shadow-emerald-500/20'
            } ${isStarting ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {isMonitoring ? (
              <>
                <Square className="w-3.5 h-3.5 fill-current" />
                <span>Stop</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>{isStarting ? 'Starting...' : 'Start'}</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Mobile Navigation bar */}
      <div className="md:hidden flex items-center justify-around bg-[#121826] border-t border-[#1f293d] px-2 py-2 text-xs">
        <button
          onClick={() => setActiveTab('dashboard')}
          className={`flex flex-col items-center py-1 ${activeTab === 'dashboard' ? 'text-blue-400' : 'text-slate-400'}`}
        >
          <Activity className="w-4 h-4 mb-1" />
          Dashboard
        </button>
        <button
          onClick={() => setActiveTab('clips')}
          className={`flex flex-col items-center py-1 ${activeTab === 'clips' ? 'text-blue-400' : 'text-slate-400'}`}
        >
          <Video className="w-4 h-4 mb-1" />
          Clips
        </button>
        <button
          onClick={() => setActiveTab('shorts')}
          className={`flex flex-col items-center py-1 ${activeTab === 'shorts' ? 'text-blue-400' : 'text-slate-400'}`}
        >
          <Film className="w-4 h-4 mb-1" />
          Shorts
        </button>
        <button
          onClick={() => setActiveTab('live')}
          className={`flex flex-col items-center py-1 ${activeTab === 'live' ? 'text-blue-400' : 'text-slate-400'}`}
        >
          <Radio className="w-4 h-4 mb-1" />
          Live
        </button>
        <button
          onClick={() => setActiveTab('settings')}
          className={`flex flex-col items-center py-1 ${activeTab === 'settings' ? 'text-blue-400' : 'text-slate-400'}`}
        >
          <SettingsIcon className="w-4 h-4 mb-1" />
          Settings
        </button>
      </div>
    </header>
  );
}

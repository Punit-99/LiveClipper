import React, { useState, useRef, useEffect } from 'react';
import { Layers, X, Loader2, ChevronDown, CheckCircle2, AlertCircle } from 'lucide-react';

export default function QueueStatusIndicator({
  title = "Queue",
  queueData = { active: [], waiting: [], max_concurrent: 2 },
  onCancel,
  type = "processing", // "processing" | "upload"
}) {
  const [isOpen, setIsOpen] = useState(false);
  const panelRef = useRef(null);

  const active = queueData?.active || [];
  const waiting = queueData?.waiting || [];
  const activeCount = active.length;
  const waitingCount = waiting.length;
  const totalCount = activeCount + waitingCount;

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const hasActivity = totalCount > 0;

  return (
    <div className="relative inline-block text-left" ref={panelRef}>
      {/* Badge / Pill Trigger */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors shadow-sm ${
          hasActivity
            ? 'bg-blue-900/30 border-blue-500/50 text-blue-300 hover:bg-blue-900/50'
            : 'bg-zinc-800/80 border-zinc-700/60 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
        }`}
        title="Click to view queue details"
      >
        {hasActivity ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" />
        ) : (
          <Layers className="w-3.5 h-3.5 text-zinc-400" />
        )}
        <span>
          {title}: <strong className="text-zinc-100">{activeCount}</strong> active,{' '}
          <strong className="text-zinc-100">{waitingCount}</strong> waiting
        </span>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown Panel */}
      {isOpen && (
        <div className="absolute right-0 mt-2 w-80 sm:w-96 rounded-xl bg-zinc-900 border border-zinc-700/80 shadow-2xl p-4 z-50 text-xs backdrop-blur-md">
          <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-blue-400" />
              <h4 className="font-semibold text-zinc-100 text-sm">{title} Details</h4>
            </div>
            <span className="text-zinc-400 text-[11px]">
              Max concurrent: <strong className="text-zinc-200">{queueData.max_concurrent || 2}</strong>
            </span>
          </div>

          <div className="mt-3 space-y-4 max-h-80 overflow-y-auto pr-1 custom-scrollbar">
            {/* Active Items */}
            <div>
              <div className="text-[11px] font-semibold tracking-wider text-blue-400 uppercase mb-2 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse"></span>
                Active ({activeCount})
              </div>
              {activeCount === 0 ? (
                <div className="text-zinc-500 italic py-1.5 px-2 bg-zinc-950/40 rounded-lg">
                  No active tasks running
                </div>
              ) : (
                <div className="space-y-2">
                  {active.map((item) => (
                    <div
                      key={item.clip_id}
                      className="p-2.5 rounded-lg bg-zinc-950/60 border border-zinc-800/80 flex flex-col gap-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-zinc-200 truncate max-w-[200px]" title={item.title}>
                          {item.title || item.clip_id}
                        </span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 font-mono">
                          {type === 'processing'
                            ? (item.stage ? `${item.stage} ${item.percentage || 0}%` : 'processing...')
                            : (item.percentage ? `${item.percentage}%` : 'uploading...')}
                        </span>
                      </div>
                      {/* Mini progress bar */}
                      <div className="w-full bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                        <div
                          className="bg-blue-500 h-1.5 rounded-full transition-all duration-300"
                          style={{ width: `${item.percentage || 0}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Waiting Items */}
            <div>
              <div className="text-[11px] font-semibold tracking-wider text-amber-400 uppercase mb-2 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
                Waiting ({waitingCount})
              </div>
              {waitingCount === 0 ? (
                <div className="text-zinc-500 italic py-1.5 px-2 bg-zinc-950/40 rounded-lg">
                  Queue is empty
                </div>
              ) : (
                <div className="space-y-2">
                  {waiting.map((item, idx) => (
                    <div
                      key={item.clip_id}
                      className="p-2 rounded-lg bg-zinc-950/60 border border-zinc-800/80 flex items-center justify-between gap-2"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono text-[10px] shrink-0">
                          #{item.position || idx + 1}
                        </span>
                        <span className="font-medium text-zinc-300 truncate" title={item.title}>
                          {item.title || item.clip_id}
                        </span>
                      </div>
                      {onCancel && (
                        <button
                          type="button"
                          onClick={() => onCancel(item.clip_id)}
                          className="text-zinc-400 hover:text-red-400 hover:bg-red-950/30 p-1 rounded transition-colors shrink-0"
                          title="Cancel queued item"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * API client and WebSocket helper for LiveClipper frontend.
 */

export const API_BASE = '/api';

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/status`);
  if (!res.ok) throw new Error(`Status fetch failed: ${res.statusText}`);
  return res.json();
}

export async function startMonitoring(payload = {}) {
  const res = await fetch(`${API_BASE}/monitor/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Start failed: ${res.statusText}`);
  }
  return res.json();
}

export async function stopMonitoring() {
  const res = await fetch(`${API_BASE}/monitor/stop`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Stop failed: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchChannelsLiveStatus() {
  const res = await fetch(`${API_BASE}/channels/live-status`);
  if (!res.ok) throw new Error(`Failed to fetch channel live status: ${res.statusText}`);
  return res.json();
}

export async function approveFallbackStream(channelId) {
  const res = await fetch(`${API_BASE}/monitor/approve/${encodeURIComponent(channelId)}`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to approve fallback stream: ${res.statusText}`);
  }
  return res.json();
}

export async function waitTargetChannels() {
  const res = await fetch(`${API_BASE}/monitor/wait-target`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to set wait for targets: ${res.statusText}`);
  }
  return res.json();
}


export async function fetchClips(params = {}) {
  const query = new URLSearchParams();
  if (params.channel) query.set('channel', params.channel);
  if (params.platform) query.set('platform', params.platform);
  if (params.min_score) query.set('min_score', params.min_score);

  const res = await fetch(`${API_BASE}/clips?${query.toString()}`);
  if (!res.ok) throw new Error(`Clips fetch failed: ${res.statusText}`);
  return res.json();
}

export async function fetchShorts(params = {}) {
  const query = new URLSearchParams();
  if (params.channel) query.set('channel', params.channel);
  if (params.state) query.set('state', params.state);

  const res = await fetch(`${API_BASE}/shorts?${query.toString()}`);
  if (!res.ok) throw new Error(`Shorts fetch failed: ${res.statusText}`);
  return res.json();
}

export async function processPendingShorts(payload = {}) {
  const res = await fetch(`${API_BASE}/shorts/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Process shorts failed: ${res.statusText}`);
  }
  return res.json();
}

export async function processSingleShort(clipId, payload = {}) {
  const res = await fetch(`${API_BASE}/shorts/${clipId}/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Process clip failed: ${res.statusText}`);
  }
  return res.json();
}

export async function uploadSingleShort(clipId, payload = {}) {
  const res = await fetch(`${API_BASE}/upload/${clipId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Upload failed: ${res.statusText}`);
  }
  return res.json();
}

export async function autoUploadShorts(payload = {}) {
  const res = await fetch(`${API_BASE}/upload/auto`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Auto upload failed: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchQuota() {
  const res = await fetch(`${API_BASE}/upload/quota`);
  if (!res.ok) throw new Error(`Quota fetch failed: ${res.statusText}`);
  return res.json();
}

export async function fetchSettings() {
  const res = await fetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error(`Settings fetch failed: ${res.statusText}`);
  return res.json();
}

export async function updateSettings(payload) {
  const res = await fetch(`${API_BASE}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Settings update failed: ${res.statusText}`);
  }
  return res.json();
}

export async function resolveChannel(channelId) {
  if (!channelId || !channelId.trim()) {
    return { id: channelId, name: channelId, resolved: false };
  }
  try {
    const query = new URLSearchParams({ id: channelId.trim() });
    const res = await fetch(`${API_BASE}/channels/resolve?${query.toString()}`);
    if (!res.ok) {
      return { id: channelId, name: channelId, resolved: false };
    }
    return res.json();
  } catch (err) {
    return { id: channelId, name: channelId, resolved: false };
  }
}

export async function fetchHighlights(limit = 50, minScore = null) {
  const query = new URLSearchParams({ limit: limit.toString() });
  if (minScore !== null) query.set('min_score', minScore.toString());
  const res = await fetch(`${API_BASE}/highlights?${query.toString()}`);
  if (!res.ok) throw new Error(`Highlights fetch failed: ${res.statusText}`);
  return res.json();
}

export async function deleteSingleClip(clipId) {
  const res = await fetch(`${API_BASE}/clips/${clipId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Delete clip failed: ${res.statusText}`);
  }
  return res.json();
}

export async function deleteAllClips() {
  const res = await fetch(`${API_BASE}/clips?confirm=true`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Delete all clips failed: ${res.statusText}`);
  }
  return res.json();
}

export async function deleteSingleShort(clipId) {
  const res = await fetch(`${API_BASE}/shorts/${clipId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Delete short failed: ${res.statusText}`);
  }
  return res.json();
}

export async function deleteAllShorts() {
  const res = await fetch(`${API_BASE}/shorts?confirm=true`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Delete all shorts failed: ${res.statusText}`);
  }
  return res.json();
}

export function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '0s';
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m === 0) return `${remS}s`;
  return `${m}m ${remS}s`;
}

export function formatDateTime(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).replace(',', ' at');
  } catch (e) {
    return '';
  }
}

export function formatRelativeTime(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    if (isNaN(diffMs)) return '';
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 60) return 'just now';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch (e) {
    return '';
  }
}


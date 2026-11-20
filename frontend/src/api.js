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
  const data = await res.json();
  const list = Array.isArray(data) ? data : (data?.clips || []);
  return list.map(item => ({
    ...item,
    clip_id: item.clip_id || item.id,
    id: item.id || item.clip_id,
    highlight_score: item.highlight_score ?? item.score ?? 0,
    score: item.score ?? item.highlight_score ?? 0,
  }));
}

export async function fetchShorts(params = {}) {
  const query = new URLSearchParams();
  if (params.channel) query.set('channel', params.channel);
  if (params.state) query.set('state', params.state);

  const res = await fetch(`${API_BASE}/shorts?${query.toString()}`);
  if (!res.ok) throw new Error(`Shorts fetch failed: ${res.statusText}`);
  const data = await res.json();
  const list = Array.isArray(data) ? data : (data?.shorts || []);
  return list.map(item => ({
    ...item,
    clip_id: item.clip_id || item.id,
    id: item.id || item.clip_id,
    highlight_score: item.highlight_score ?? item.score ?? 0,
    score: item.score ?? item.highlight_score ?? 0,
  }));
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

export async function reconvertShort(clipId, payload = {}) {
  const res = await fetch(`${API_BASE}/shorts/${clipId}/reconvert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Re-convert short failed: ${res.statusText}`);
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

export async function verifyShortUpload(clipId) {
  const res = await fetch(`${API_BASE}/upload/${clipId}/verify`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Verification failed: ${res.statusText}`);
  }
  return res.json();
}

export async function reuploadSingleShort(clipId, payload = {}) {
  const res = await fetch(`${API_BASE}/upload/${clipId}/reupload`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Re-upload failed: ${res.statusText}`);
  }
  return res.json();
}

export async function verifyAllUploads() {
  const res = await fetch(`${API_BASE}/upload/verify-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Batch verification failed: ${res.statusText}`);
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

export async function fetchProcessingQueue() {
  const res = await fetch(`${API_BASE}/shorts/queue`);
  if (!res.ok) throw new Error(`Processing queue fetch failed: ${res.statusText}`);
  return res.json();
}

export async function cancelProcessing(clipId) {
  const res = await fetch(`${API_BASE}/shorts/${clipId}/cancel`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Cancel processing failed: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchUploadQueue() {
  const res = await fetch(`${API_BASE}/upload/queue`);
  if (!res.ok) throw new Error(`Upload queue fetch failed: ${res.statusText}`);
  return res.json();
}

export async function cancelUpload(clipId) {
  const res = await fetch(`${API_BASE}/upload/${clipId}/cancel`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Cancel upload failed: ${res.statusText}`);
  }
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

export function formatRecordedDate(isoString) {
  return formatDateTime(isoString);
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

// --- Import (YouTube Video Clipper) API functions ---

export async function fetchVideoInfo(url) {
  const query = new URLSearchParams({ url });
  const res = await fetch(`${API_BASE}/import/info?${query.toString()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to fetch video info: ${res.statusText}`);
  }
  return res.json();
}

export async function startImportAnalysis(payload) {
  const res = await fetch(`${API_BASE}/import/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Import analysis failed: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchImportJobs() {
  const res = await fetch(`${API_BASE}/import/jobs`);
  if (!res.ok) throw new Error(`Failed to fetch import jobs: ${res.statusText}`);
  return res.json();
}

export async function fetchImportJob(jobId) {
  const res = await fetch(`${API_BASE}/import/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Failed to fetch import job: ${res.statusText}`);
  return res.json();
}

export async function fetchImportedClips() {
  const res = await fetch(`${API_BASE}/import/clips`);
  if (!res.ok) throw new Error(`Failed to fetch imported clips: ${res.statusText}`);
  return res.json();
}

export async function deleteImportedClip(clipId) {
  const res = await fetch(`${API_BASE}/import/clips/${clipId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to delete imported clip: ${res.statusText}`);
  }
  return res.json();
}

export async function processImportedClip(clipId) {
  const res = await fetch(`${API_BASE}/import/clips/${clipId}/process`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to process imported clip: ${res.statusText}`);
  }
  return res.json();
}

export async function downloadImportVideo(payload) {
  const res = await fetch(`${API_BASE}/import/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Download failed: ${res.statusText}`);
  }
  return res.json();
}

export function uploadImportVideoFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);

    xhr.open('POST', `${API_BASE}/import/upload`);

    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          const pct = Math.round((event.loaded / event.total) * 100);
          onProgress(pct, event.loaded, event.total);
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          resolve(data);
        } catch {
          resolve({});
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText);
          reject(new Error(err.detail || `Upload failed with status ${xhr.status}`));
        } catch {
          reject(new Error(`Upload failed with status ${xhr.status}`));
        }
      }
    };

    xhr.onerror = () => reject(new Error('Network error during file upload'));
    xhr.send(formData);
  });
}

export async function fetchImportLibrary() {
  const res = await fetch(`${API_BASE}/import/library`);
  if (!res.ok) throw new Error(`Failed to fetch library: ${res.statusText}`);
  return res.json();
}

export async function deleteImportLibraryVideo(videoId) {
  const res = await fetch(`${API_BASE}/import/library/${videoId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to delete library video: ${res.statusText}`);
  }
  return res.json();
}

export async function startLibraryAnalysis(videoId, payload) {
  const res = await fetch(`${API_BASE}/import/analyze/${videoId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Analysis failed: ${res.statusText}`);
  }
  return res.json();
}

export async function cancelImportJob(jobId) {
  const res = await fetch(`${API_BASE}/import/jobs/${jobId}/cancel`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to cancel job: ${res.statusText}`);
  }
  return res.json();
}

export async function clearAllImportData() {
  const res = await fetch(`${API_BASE}/import/clips?confirm=true`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to clear imports: ${res.statusText}`);
  }
  return res.json();
}

export async function resetQuotaCounter() {
  const res = await fetch(`${API_BASE}/upload/quota/reset`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to reset quota counter: ${res.statusText}`);
  }
  return res.json();
}




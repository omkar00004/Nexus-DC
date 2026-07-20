// Single doorway to the FastAPI backend (proxied /api -> :8000).
const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}: ${detail.slice(0, 200)}`)
  }
  return res.json()
}

export const api = {
  health: () => request('/health'),
  dashboardSummary: () => request('/dashboard/summary'),
  events: (limit = 50) => request(`/events?limit=${limit}`),
  convergenceAlerts: () => request('/convergence/alerts'),
  runAllAgents: () => request('/agents/all/run', { method: 'POST' }),
  runAgent: (name) => request(`/agents/${name}/run`, { method: 'POST' }),
  oracleQuery: (query) =>
    request('/oracle/query', { method: 'POST', body: JSON.stringify({ query }) }),
  guideProcedures: () => request('/guide/procedures'),
  guideProcedure: (id) => request(`/guide/procedures/${id}`),
  guideStart: (procedure_id, operator) =>
    request('/guide/session/start', {
      method: 'POST', body: JSON.stringify({ procedure_id, operator }),
    }),
  guideSubmit: (session_id, reading) =>
    request('/guide/session/submit-step', {
      method: 'POST', body: JSON.stringify({ session_id, reading }),
    }),
  guideComplete: (session_id) =>
    request('/guide/session/complete', {
      method: 'POST', body: JSON.stringify({ session_id }),
    }),
  guideSession: (session_id) => request(`/guide/session/${session_id}`),
  resetDemo: () => request('/demo/reset', { method: 'POST' }),
  ncrs: () => request('/ncr'),
  ncrCreate: (fields) => request('/ncr', { method: 'POST', body: JSON.stringify(fields) }),
  ncrDraft: (description) =>
    request('/ncr/draft', { method: 'POST', body: JSON.stringify({ description }) }),
  ncrDisposition: (id, disposition, by, note) =>
    request(`/ncr/${id}/disposition`, {
      method: 'POST', body: JSON.stringify({ disposition, by, note }),
    }),
  ncrClose: (id, by) =>
    request(`/ncr/${id}/close`, { method: 'POST', body: JSON.stringify({ by }) }),
  ncrPdfUrl: (id) => `${BASE}/ncr/${id}/pdf`,
  documents: () => request('/documents'),
  documentUrl: (name) => `${BASE}/documents/${encodeURIComponent(name)}`,
  // FormData sets its own multipart boundary - bypass request()'s JSON header
  uploadDocument: async (file, docType, uploadedBy) => {
    const form = new FormData()
    form.append('file', file)
    form.append('doc_type', docType)
    form.append('uploaded_by', uploadedBy || '')
    const res = await fetch(`${BASE}/documents/upload`, { method: 'POST', body: form })
    if (!res.ok) {
      const detail = await res.text().catch(() => '')
      throw new Error(`${res.status} ${res.statusText}: ${detail.slice(0, 200)}`)
    }
    return res.json()
  },
  activateSubmittalVersion: (versionId, by) =>
    request(`/documents/submittal/versions/${versionId}/activate`, {
      method: 'POST', body: JSON.stringify({ by: by || '' }),
    }),
  deleteSubmittalVersion: (versionId) =>
    request(`/documents/submittal/versions/${versionId}`, { method: 'DELETE' }),
}

// Monochrome severity language - with ONE exception: alarm red is reserved
// for live risk states (CRITICAL), so on a projector the only red on screen
// is the thing that is actually wrong.
export const severityColor = {
  CRITICAL: 'bg-alarm text-paper border-alarm',
  HIGH: 'bg-paper text-ink border-ink border-2',
  MAJOR: 'bg-paper text-ink border-ink border-2',
  MEDIUM: 'bg-paper text-ink border-ink',
  MINOR: 'bg-paper text-muted-fg border-line',
  LOW: 'bg-paper text-muted-fg border-line',
  INFO: 'bg-paper text-muted-fg border-line',
  ON_TRACK: 'bg-muted text-ink border-line',
}

// Risk emphasis via weight; alarm red only past the critical threshold.
export function riskColor(score) {
  if (score >= 0.7) return 'font-bold text-alarm'
  if (score >= 0.4) return 'font-bold text-ink'
  if (score > 0.15) return 'text-ink'
  return 'text-muted-fg'
}

// Business-facing severity band for a 0-1 risk score. Business users act on
// labels, days and dollars; the raw score stays visible as the small
// "engineering view" next to each band, so nothing is hidden from a judge.
export function riskBand(score) {
  if (score >= 0.8) return 'CRITICAL'
  if (score >= 0.6) return 'HIGH'
  if (score >= 0.3) return 'MEDIUM'
  return 'LOW'
}

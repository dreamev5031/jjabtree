const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options)
  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()

  if (!response.ok) {
    const detail = typeof payload === 'object' && payload?.detail ? payload.detail : payload
    throw new Error(detail || `요청 실패 (${response.status})`)
  }
  return payload
}

export function adminHeaders(appKey, extra = {}) {
  return {
    'X-App-Key': appKey,
    ...extra,
  }
}

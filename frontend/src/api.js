const apiHost = window.location.hostname || '127.0.0.1'
export const API_ROOT = import.meta.env.VITE_API_ROOT || `http://${apiHost}:8000/api`

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data)
    throw new Error(message || `请求失败 (${response.status})`)
  }
  return data
}

export const api = {
  health: () => request('/health'),
  files: () => request('/files'),
  fileStats: () => request('/files/stats'),
  filePreview: (id, params = {}) => {
    const query = new URLSearchParams(params)
    return request(`/files/${id}/preview?${query.toString()}`)
  },
  filePreviewIndex: (id) => request(`/files/${id}/preview-index`),
  clearHistory: () => request('/history', { method: 'DELETE' }),
  scanLocalArchives: (payload) => request('/local/archives', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  pickLocalDirectory: (initialDirectory) => request('/local/pick-directory', {
    method: 'POST',
    body: JSON.stringify({ initial_directory: initialDirectory || '' }),
  }),
  tasks: () => request('/tasks'),
  task: (id) => request(`/tasks/${id}`),
  taskEvents: (id, params = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, value]) => value !== null && value !== undefined),
    )
    return request(`/tasks/${id}/events/history?${query.toString()}`)
  },
  pauseTask: (id) => request(`/tasks/${id}/pause`, { method: 'POST' }),
  resumeTask: (id) => request(`/tasks/${id}/resume`, { method: 'POST' }),
  databaseConfig: () => request('/config/database'),
  saveDatabaseConfig: (config) => request('/config/database', {
    method: 'PUT',
    body: JSON.stringify(config),
  }),
  testDatabaseConfig: (config) => request('/config/database/test', {
    method: 'POST',
    body: JSON.stringify(config),
  }),
  databaseTables: () => request('/database/tables'),
  databaseRecords: (params) => {
    const query = new URLSearchParams(
      Object.entries(params || {}).filter(([, value]) => value !== '' && value !== null && value !== undefined),
    )
    return request(`/database/records?${query.toString()}`)
  },
  databaseCoverage: (params) => {
    const query = new URLSearchParams(
      Object.entries(params || {}).filter(([, value]) => value !== '' && value !== null && value !== undefined),
    )
    return request(`/database/coverage?${query.toString()}`)
  },
  extract: (payload) => request('/tasks/extract', { method: 'POST', body: JSON.stringify(payload) }),
  validate: (payload) => request('/tasks/validate', { method: 'POST', body: JSON.stringify(payload) }),
  importData: (payload) => request('/tasks/import', { method: 'POST', body: JSON.stringify(payload) }),
  eventsUrl: (id, cursor = 0) => `${API_ROOT}/tasks/${id}/events?cursor=${cursor}`,
}

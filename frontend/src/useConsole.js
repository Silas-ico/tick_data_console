import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from './api'


const terminalStatuses = new Set(['success', 'failed'])
const EVENT_PAGE_SIZE = 500

export function useConsole() {
  const section = ref('dashboard')
  const serverOnline = ref(false)
  const files = ref([])
  const fileStats = ref({ indexed: 0, available: 0, missing: 0, zip: 0, logs: 0 })
  const tasks = ref([])
  const selectedZipIds = ref([])
  const localDirectory = ref('')
  const localRootDirectory = ref('')
  const mergeScope = ref('month')
  const localArchives = ref([])
  const selectedLocalArchiveIds = ref([])
  const monthDirectories = ref([])
  const selectedMonthDirectories = ref([])
  const scanMessage = ref('')
  const outputNameCustomized = ref(false)
  const selectedDataFileId = ref('')
  const outputName = ref('')
  const targetProduct = ref('')
  const tableName = ref('')
  const databaseTables = ref([])
  const databaseTablesError = ref('')
  const databaseRecords = ref(null)
  const databaseQuery = ref({ table_name: '', keyword: '', code: '', start_time: '', end_time: '', page: 1, page_size: 50 })
  const coverageTable = ref('')
  const coverageYear = ref(null)
  const coverageMonth = ref(null)
  const databaseCoverage = ref(null)
  const coverageError = ref('')
  const coverageSnapshots = new Map()
  const adjustDuplicates = ref(true)
  const selectedTask = ref(null)
  const taskEvents = ref([])
  const hasOlderEvents = ref(false)
  const hasNewerEvents = ref(false)
  const eventHistoryBusy = ref(false)
  const sseStatus = ref('disconnected')
  const busy = ref({ scan: false, upload: false, extract: false, validate: false, import: false, db: false, query: false, coverage: false, cleanup: false, directory: false })
  const cleanupConfirmOpen = ref(false)
  const notice = ref(null)
  const database = ref({
    host: '127.0.0.1',
    port: 3306,
    user: 'root',
    password: '',
    database: 'jq_category',
    charset: 'utf8mb4',
  })
  const sources = new Map()
  const eventIds = new Set()
  const pendingEvents = new Map()
  let eventFlushTimer = null
  let noticeTimer

  if (typeof window !== 'undefined') {
    localRootDirectory.value = window.localStorage.getItem('tick-data-console.local-root') || ''
    localDirectory.value = window.localStorage.getItem('tick-data-console.local-directory') || ''
  }

  const zipFiles = computed(() => files.value.filter((file) => file.extension === '.zip'))
  const dataFiles = computed(() => files.value.filter((file) => {
    const isLogFile = file.extension === '.txt' || file.extension === '.log'
    const isRejectedFile = /[_-]rejected(?:\.[^.]+)?$/i.test(file.name || '')
    return isLogFile && !isRejectedFile
  }))
  const runningTasks = computed(() => tasks.value.filter((task) => ['pending', 'running', 'pausing', 'paused'].includes(task.status)))
  const successfulTasks = computed(() => tasks.value.filter((task) => task.status === 'success'))
  const failedTasks = computed(() => tasks.value.filter((task) => task.status === 'failed'))
  const latestValidation = computed(() => tasks.value.find((task) => task.type === 'validate' && task.status === 'success')?.result)
  const latestImport = computed(() => tasks.value.find((task) => task.type === 'import' && task.status === 'success')?.result)
  const tableOptions = computed(() => databaseTables.value.map((table) => ({
    value: table.name,
    label: table.label,
    code: table.code,
  })))
  const selectedDataFile = computed(() => dataFiles.value.find((file) => file.id === selectedDataFileId.value))
  const selectedFileProduct = computed(() => {
    const name = selectedDataFile.value?.name || ''
    const match = /^(?:tick_data_)?([A-Za-z]{1,3})(?:[_\-.]|$)/.exec(name)
    return match?.[1]?.toUpperCase() || ''
  })
  const matchingTable = computed(() => tableOptions.value.find((table) => table.code === selectedFileProduct.value))
  const importTargetInvalid = computed(() => Boolean(selectedFileProduct.value && !matchingTable.value))
  const sourceProduct = computed(() => {
    const path = localDirectory.value.trim()
    const parts = path.split(/[\\/]/).filter(Boolean)
    const yearIndex = parts.findIndex((part) => /^\d{4}$/.test(part.trim()))
    if (yearIndex > 0 && /^[A-Za-z]{1,3}$/.test(parts[yearIndex - 1].trim())) {
      return parts[yearIndex - 1].trim().toUpperCase()
    }
    return ''
  })
  const targetProductLocked = computed(() => Boolean(
    sourceProduct.value && databaseTables.value.some((table) => table.code === sourceProduct.value),
  ))
  const tableNameLocked = computed(() => Boolean(matchingTable.value))
  const outputNamePrefix = computed(() => targetProduct.value ? `${targetProduct.value.toUpperCase()}_` : '')
  const outputNameSuffix = computed({
    get: () => outputName.value.startsWith(outputNamePrefix.value)
      ? outputName.value.slice(outputNamePrefix.value.length)
      : outputName.value,
    set: (value) => {
      if (!value.trim()) {
        outputNameCustomized.value = false
        refreshDerivedFields()
        return
      }
      outputNameCustomized.value = true
      outputName.value = `${outputNamePrefix.value}${value}`
    },
  })

  function directoryDetails() {
    const sourcePath = selectedMonthDirectories.value[0] || localDirectory.value.trim()
    const parts = sourcePath.split(/[\\/]/).filter(Boolean)
    const yearIndex = parts.findIndex((part) => /^\d{4}$/.test(part.trim()))
    const year = yearIndex >= 0 ? parts[yearIndex].trim() : ''
    const product = yearIndex > 0 && /^[A-Za-z]{1,3}$/.test(parts[yearIndex - 1].trim())
      ? parts[yearIndex - 1].trim().toUpperCase()
      : ''
    const monthMatch = /^(0?[1-9]|1[0-2])月$/.exec(parts.at(-1)?.trim() || '')
    return { product, year, month: monthMatch ? Number(monthMatch[1]) : null }
  }

  function refreshDerivedFields() {
    const details = directoryDetails()
    if (details.product) {
      targetProduct.value = databaseTables.value.some((table) => table.code === details.product)
        ? details.product
        : ''
    }
    if (outputNameCustomized.value || !details.product || !details.year) return
    const selectedMonths = selectedMonthDirectories.value
      .map((directory) => /(?:^|[\\/])(0?[1-9]|1[0-2])月$/.exec(directory)?.[1])
      .filter(Boolean)
      .map(Number)
      .sort((left, right) => left - right)
    const months = selectedMonths.length ? selectedMonths : details.month ? [details.month] : []
    outputName.value = months.length
      ? `${details.product}_${details.year}_${months.map((month) => String(month).padStart(2, '0')).join('_')}.txt`
      : mergeScope.value === 'year'
        ? `${details.product}_${details.year}.txt`
        : ''
  }

  function setOutputNameManually(event) {
    const prefix = targetProduct.value ? `${targetProduct.value.toUpperCase()}_` : ''
    let value = event.target.value
    if (prefix && !value.toUpperCase().startsWith(prefix)) {
      const separator = value.indexOf('_')
      value = prefix + (separator >= 0 ? value.slice(separator + 1) : value)
      outputName.value = value
    }
    outputNameCustomized.value = Boolean(value.trim())
    if (!outputNameCustomized.value) refreshDerivedFields()
  }

  watch([localDirectory, mergeScope], () => {
    localArchives.value = []
    selectedLocalArchiveIds.value = []
    monthDirectories.value = []
    selectedMonthDirectories.value = []
    scanMessage.value = ''
    refreshDerivedFields()
  })

  watch(localDirectory, (directory) => {
    if (typeof window !== 'undefined' && directory.trim()) {
      window.localStorage.setItem('tick-data-console.local-directory', directory.trim())
    }
  })

  watch(targetProduct, (product) => {
    const matching = tableOptions.value.find((table) => table.code === product)
    if (matching) tableName.value = matching.value
    if (product && outputNameCustomized.value) {
      const suffix = outputName.value.replace(/^[A-Za-z]{1,3}_/, '')
      outputName.value = `${product.toUpperCase()}_${suffix}`
    }
    refreshDerivedFields()
  })

  // Keep the import target in sync with the selected log file. Watching the
  // id directly avoids retaining a stale target when the file list is
  // refreshed or the selected file changes before its derived product does.
  function syncSelectedFileTarget() {
    const product = selectedFileProduct.value
    const matching = tableOptions.value.find((table) => table.code === product)
    if (matching) {
      tableName.value = matching.value
    } else if (product) {
      tableName.value = ''
    }
  }

  watch(selectedDataFileId, syncSelectedFileTarget)
  watch(databaseTables, syncSelectedFileTarget)

  function showNotice(message, tone = 'success') {
    notice.value = { message, tone }
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => { notice.value = null }, 3500)
  }

  function errorMessage(error) {
    return error instanceof Error ? error.message : String(error)
  }

  function upsertTask(incoming) {
    const index = tasks.value.findIndex((task) => task.id === incoming.id)
    if (index === -1) {
      tasks.value = [incoming, ...tasks.value]
      return incoming
    }
    tasks.value[index] = { ...tasks.value[index], ...incoming }
    tasks.value = [...tasks.value]
    return tasks.value[index]
  }

  async function loadFiles() {
    const [listedFiles, stats] = await Promise.all([api.files(), api.fileStats()])
    files.value = listedFiles
    fileStats.value = stats
    if (!selectedDataFileId.value && dataFiles.value.length) {
      selectedDataFileId.value = dataFiles.value[0].id
    }
  }

  async function loadTasks() {
    tasks.value = await api.tasks()
    tasks.value.filter((task) => !terminalStatuses.has(task.status)).forEach((task) => {
      connectTask(task, task.last_event_id || 0)
    })
  }

  async function loadDatabase() {
    database.value = await api.databaseConfig()
  }

  async function loadDatabaseTables() {
    try {
      const result = await api.databaseTables()
      databaseTables.value = result.tables || []
      databaseTablesError.value = ''
      if (!databaseQuery.value.table_name && databaseTables.value.length) databaseQuery.value.table_name = databaseTables.value[0].name
      if (targetProduct.value && !databaseTables.value.some((table) => table.code === targetProduct.value)) {
        targetProduct.value = ''
      }
      const fileTable = databaseTables.value.find((table) => table.code === selectedFileProduct.value)
      if (fileTable) {
        tableName.value = fileTable.name
      } else if (!databaseTables.value.some((table) => table.name === tableName.value)) {
        tableName.value = databaseTables.value[0]?.name || ''
      }
      refreshDerivedFields()
      if (section.value === 'records' && (!databaseRecords.value || !databaseRecords.value.table_name)) queryDatabaseRecords()
      if (section.value === 'coverage') queryDatabaseCoverage({ year: null, month: null, refresh: true })
    } catch (error) {
      databaseTables.value = []
      databaseTablesError.value = errorMessage(error)
    }
  }

  async function queryDatabaseRecords(resetPage = true) {
    if (!databaseQuery.value.table_name && databaseTables.value.length) databaseQuery.value.table_name = databaseTables.value[0].name
    if (resetPage) databaseQuery.value.page = 1
    busy.value.query = true
    try {
      databaseRecords.value = await api.databaseRecords(databaseQuery.value)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      busy.value.query = false
    }
  }

  async function clearDatabaseQuery() {
    databaseQuery.value = { table_name: databaseTables.value[0]?.name || '', keyword: '', code: '', start_time: '', end_time: '', page: 1, page_size: 50 }
    await queryDatabaseRecords(false)
  }

  async function queryDatabaseCoverage({ table = coverageTable.value, year = coverageYear.value, month = coverageMonth.value, refresh = false } = {}) {
    const targetTable = table || databaseTables.value[0]?.name || ''
    if (!targetTable) {
      coverageTable.value = ''
      databaseCoverage.value = null
      coverageError.value = ''
      return
    }
    if (!refresh && databaseCoverage.value?.table?.name === targetTable && year === coverageYear.value && month === coverageMonth.value) return
    const snapshotKey = `${targetTable}|${year ?? 'latest'}|${month ?? 'all'}`
    const cachedSnapshot = !refresh ? coverageSnapshots.get(snapshotKey) : null
    if (cachedSnapshot) {
      coverageTable.value = targetTable
      coverageYear.value = cachedSnapshot.selected_year
      coverageMonth.value = cachedSnapshot.selected_month
      databaseCoverage.value = cachedSnapshot
      coverageError.value = ''
      return
    }
    busy.value.coverage = true
    coverageError.value = ''
    try {
      const result = await api.databaseCoverage({ table_name: targetTable, year, month })
      coverageTable.value = targetTable
      coverageYear.value = result.selected_year
      coverageMonth.value = month
      const previous = databaseCoverage.value?.table?.name === targetTable ? databaseCoverage.value : null
      databaseCoverage.value = {
        ...result,
        total: result.total ?? previous?.total ?? 0,
        min_year: result.min_year ?? previous?.min_year ?? null,
        max_year: result.max_year ?? previous?.max_year ?? null,
        years: result.years ?? previous?.years ?? [],
      }
      coverageSnapshots.set(snapshotKey, databaseCoverage.value)
    } catch (error) {
      coverageError.value = errorMessage(error)
      databaseCoverage.value = null
    } finally {
      busy.value.coverage = false
    }
  }

  watch(section, (value) => {
    if (value === 'records' && !databaseRecords.value) queryDatabaseRecords()
    if (value === 'coverage' && databaseCoverage.value?.table?.name !== (coverageTable.value || databaseTables.value[0]?.name)) {
      queryDatabaseCoverage({ year: null, month: null, refresh: true })
    }
  })
  async function bootstrap() {
    const results = await Promise.allSettled([
      api.health(),
      loadFiles(),
      loadTasks(),
      loadDatabase(),
      loadDatabaseTables(),
    ])
    serverOnline.value = results[0].status === 'fulfilled'
    if (!serverOnline.value) {
      showNotice('后端服务未连接，请先启动 FastAPI', 'error')
    }
  }

  function requestClearHistory() {
    if (!busy.value.cleanup) cleanupConfirmOpen.value = true
  }

  function cancelClearHistory() {
    if (!busy.value.cleanup) cleanupConfirmOpen.value = false
  }

  async function clearHistory() {
    if (busy.value.cleanup) return
    cleanupConfirmOpen.value = false
    busy.value.cleanup = true
    try {
      const result = await api.clearHistory()
      await loadFiles()
      await loadTasks()
      localArchives.value = []
      selectedZipIds.value = []
      selectedDataFileId.value = ''
      databaseRecords.value = null
      showNotice(`已清理 ${result.removed_index_records || 0} 条托管文件索引和项目临时文件`)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      busy.value.cleanup = false
    }
  }

  async function scanLocalArchives() {
    if (!localDirectory.value.trim()) {
      showNotice('请选择或填写 ZIP 所在文件夹', 'error')
      return
    }
    // Infer the product from the selected path first to prevent cross-product merges.
    const details = directoryDetails()
    if (details.product && !databaseTables.value.some((table) => table.code === details.product)) {
      const message = `本地数据库中不存在 tick_data_${details.product}，请检查目录商品或数据库表配置`
      localArchives.value = []
      scanMessage.value = message
      showNotice(message, 'error')
      return
    }
    busy.value.scan = true
    try {
      const result = await api.scanLocalArchives({
        directory: localDirectory.value.trim(),
        scope: mergeScope.value,
        selected_month_directories: selectedMonthDirectories.value,
      })
      // A year root in month mode returns month choices first; ZIPs are listed after that scope is narrowed.
      localArchives.value = result.archives || []
      selectedLocalArchiveIds.value = localArchives.value.map((archive) => archive.id)
      if (result.month_directories?.length || !selectedMonthDirectories.value.length) {
        monthDirectories.value = result.month_directories || []
      }
      scanMessage.value = result.message || ''
      await loadFiles()
      showNotice(result.message || `已发现 ${result.count} 个 ZIP 文件`, result.count || result.month_directories?.length ? 'success' : 'error')
    } catch (error) {
      showNotice(errorMessage(error), 'error')
      localArchives.value = []
      scanMessage.value = `目录检查失败：${errorMessage(error)}，请填写正确的本地文件夹地址`
    } finally {
      busy.value.scan = false
    }
  }

  async function pickLocalDirectory() {
    busy.value.directory = true
    try {
      // The local backend opens the native Windows picker; selected ZIPs stay on disk.
      const result = await api.pickLocalDirectory(localDirectory.value.trim() || localRootDirectory.value.trim())
      if (!result.directory) return
      localDirectory.value = result.directory
      await nextTick()
      await scanLocalArchives()
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      busy.value.directory = false
    }
  }

  function applyTaskEvent(taskId, event) {
    const current = tasks.value.find((task) => task.id === taskId) || { id: taskId }
    const update = {
      ...current,
      progress: event.progress,
      status: event.status,
      message: event.message,
      last_event_id: event.id,
    }
    if (event.type === 'result') {
      update.result = event.data
      update.status = 'success'
      update.progress = 100
    }
    if (event.type === 'error') {
      update.error = event.message
      update.status = 'failed'
    }
    const updated = upsertTask(update)
    if (selectedTask.value?.id === taskId) {
      selectedTask.value = { ...selectedTask.value, ...updated }
      if (event.type === 'result' || event.type === 'error') sseStatus.value = 'disconnected'
    }
    if (terminalStatuses.has(update.status)) {
      sources.get(taskId)?.close()
      sources.delete(taskId)
      loadFiles().catch(() => {})
      loadTasks().catch(() => {})
      if (update.type === 'import') {
        if (databaseRecords.value) queryDatabaseRecords(false)
        if (section.value === 'coverage') queryDatabaseCoverage({ year: null, month: null, refresh: true })
      }
    }
  }

  function flushEvents() {
    eventFlushTimer = null
    for (const [taskId, events] of pendingEvents) {
      pendingEvents.delete(taskId)
      if (!events.length) continue
      const latest = events.at(-1)
      applyTaskEvent(taskId, latest)
      if (selectedTask.value?.id !== taskId || hasNewerEvents.value) continue
      const additions = events.filter((event) => {
        if (eventIds.has(event.id)) return false
        eventIds.add(event.id)
        return true
      })
      if (additions.length) {
        taskEvents.value = [...taskEvents.value, ...additions]
        if (taskEvents.value.length > EVENT_PAGE_SIZE) {
          taskEvents.value = taskEvents.value.slice(-EVENT_PAGE_SIZE)
          hasOlderEvents.value = true
          eventIds.clear()
          taskEvents.value.forEach((event) => eventIds.add(event.id))
        }
      }
    }
  }

  function queueEvent(taskId, event) {
    if (!event || event.id === undefined) return
    const events = pendingEvents.get(taskId) || []
    events.push(event)
    pendingEvents.set(taskId, events)
    if (eventFlushTimer === null) eventFlushTimer = setTimeout(flushEvents, 100)
  }

  function connectTask(task, cursor = 0) {
    if (!task?.id || terminalStatuses.has(task.status)) return
    if (sources.has(task.id)) {
      if (selectedTask.value?.id === task.id) {
        sseStatus.value = sources.get(task.id).readyState === EventSource.OPEN ? 'connected' : 'reconnecting'
      }
      return
    }
    const source = new EventSource(api.eventsUrl(task.id, cursor))
    const handle = (browserEvent) => {
      if (!browserEvent.data) return
      try {
        queueEvent(task.id, JSON.parse(browserEvent.data))
      } catch {
        showNotice('收到无法解析的任务事件', 'error')
      }
    }
    ;['status', 'log', 'progress', 'result', 'error'].forEach((name) => {
      source.addEventListener(name, handle)
    })
    source.onopen = () => {
      if (selectedTask.value?.id === task.id) sseStatus.value = 'connected'
    }
    source.onerror = () => {
      if (selectedTask.value?.id === task.id) sseStatus.value = 'reconnecting'
      const latest = tasks.value.find((item) => item.id === task.id)
      if (latest && terminalStatuses.has(latest.status)) {
        source.close()
        sources.delete(task.id)
        if (selectedTask.value?.id === task.id) sseStatus.value = 'disconnected'
      }
    }
    sources.set(task.id, source)
  }

  async function setTaskPaused(task, shouldPause) {
    try {
      const updated = shouldPause ? await api.pauseTask(task.id) : await api.resumeTask(task.id)
      upsertTask(updated)
      if (selectedTask.value?.id === task.id) selectedTask.value = { ...selectedTask.value, ...updated }
      connectTask(updated, taskEvents.value.at(-1)?.id || 0)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    }
  }

  async function selectTask(task) {
    try {
      const detail = await api.task(task.id)
      selectedTask.value = detail
      hasNewerEvents.value = false
      eventHistoryBusy.value = true
      sseStatus.value = terminalStatuses.has(detail.status) ? 'disconnected' : 'connecting'
      section.value = 'logs'
      const page = await api.taskEvents(task.id, { limit: EVENT_PAGE_SIZE })
      if (selectedTask.value?.id !== task.id) return
      taskEvents.value = page.events || []
      hasOlderEvents.value = page.has_more
      eventIds.clear()
      taskEvents.value.forEach((event) => eventIds.add(event.id))
      connectTask(detail, detail.last_event_id || 0)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      eventHistoryBusy.value = false
    }
  }

  function clearSelectedTask() {
    selectedTask.value = null
    taskEvents.value = []
    hasOlderEvents.value = false
    hasNewerEvents.value = false
    eventHistoryBusy.value = false
    sseStatus.value = 'disconnected'
  }

  async function loadOlderEvents() {
    if (!selectedTask.value || eventHistoryBusy.value || !hasOlderEvents.value || !taskEvents.value.length) return
    eventHistoryBusy.value = true
    try {
      const page = await api.taskEvents(selectedTask.value.id, {
        before_id: taskEvents.value[0].id,
        limit: EVENT_PAGE_SIZE,
      })
      const additions = page.events.filter((event) => !eventIds.has(event.id))
      additions.forEach((event) => eventIds.add(event.id))
      taskEvents.value = [...additions, ...taskEvents.value]
      hasOlderEvents.value = page.has_more
      hasNewerEvents.value = taskEvents.value.at(-1)?.id < (selectedTask.value.last_event_id || 0)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      eventHistoryBusy.value = false
    }
  }

  async function loadNewerEvents() {
    if (!selectedTask.value || eventHistoryBusy.value) return
    eventHistoryBusy.value = true
    try {
      const page = await api.taskEvents(
        selectedTask.value.id,
        hasNewerEvents.value && taskEvents.value.length
          ? { after_id: taskEvents.value.at(-1).id, limit: EVENT_PAGE_SIZE }
          : { limit: EVENT_PAGE_SIZE },
      )
      taskEvents.value = page.events || []
      hasNewerEvents.value = page.has_more
      hasOlderEvents.value = page.has_more || taskEvents.value[0]?.id > 1
      eventIds.clear()
      taskEvents.value.forEach((event) => eventIds.add(event.id))
      if (selectedTask.value) selectedTask.value.last_event_id = Math.max(
        selectedTask.value.last_event_id || 0,
        taskEvents.value.at(-1)?.id || 0,
      )
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      eventHistoryBusy.value = false
    }
  }

  async function createTask(kind, payload) {
    busy.value[kind] = true
    try {
      const task = kind === 'extract'
        ? await api.extract(payload)
        : kind === 'validate'
          ? await api.validate(payload)
          : await api.importData(payload)
      upsertTask(task)
      await selectTask(task)
      connectTask(task)
      showNotice('后台任务已启动')
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      busy.value[kind] = false
    }
  }

  function startExtract() {
    // Scanned local IDs preserve the user's exact ZIP selection instead of rescanning the whole folder.
    if (tasks.value.some((task) => task.type === 'extract' && ['pending', 'running', 'pausing', 'paused'].includes(task.status))) {
      showNotice('已有 ZIP 合并任务尚未结束，请先在运行日志中查看或继续该任务', 'error')
      return
    }
    const archiveIds = localArchives.value.length ? selectedLocalArchiveIds.value : selectedZipIds.value
    if (!localDirectory.value.trim() && !archiveIds.length) {
      showNotice('请先选择 ZIP 文件', 'error')
      return
    }
    if (localArchives.value.length && !archiveIds.length) {
      showNotice('请至少勾选一个 ZIP 文件', 'error')
      return
    }
    if (!targetProduct.value) {
      showNotice('请选择目标品种，避免不同品种数据混入', 'error')
      return
    }
    createTask('extract', {
      file_ids: archiveIds,
      output_name: outputName.value || null,
      target_product: targetProduct.value.trim() || null,
      source_directory: localArchives.value.length ? null : (localDirectory.value.trim() || null),
      merge_scope: mergeScope.value,
      selected_month_directories: selectedMonthDirectories.value,
    })
  }

  function startValidate() {
    if (!selectedDataFileId.value) {
      showNotice('请选择待检查的日志文件', 'error')
      return
    }
    createTask('validate', {
      file_id: selectedDataFileId.value,
      adjust_duplicates: adjustDuplicates.value,
    })
  }

  function startImport() {
    if (!selectedDataFileId.value) {
      showNotice('请选择待导入的日志文件', 'error')
      return
    }
    if (importTargetInvalid.value) {
      showNotice(`本地数据库中不存在 tick_data_${selectedFileProduct.value}，不能导入该日志文件`, 'error')
      return
    }
    if (!tableOptions.value.some((table) => table.value === tableName.value)) {
      showNotice('目标数据表不在本地数据库中，请刷新数据库表列表', 'error')
      return
    }
    if (matchingTable.value && tableName.value !== matchingTable.value.value) {
      tableName.value = matchingTable.value.value
      showNotice(`当前日志文件只能导入对应表：${matchingTable.value.value}`, 'error')
      return
    }
    createTask('import', {
      file_id: selectedDataFileId.value,
      table_name: tableName.value,
      adjust_duplicates: adjustDuplicates.value,
    })
  }

  async function testDatabase() {
    busy.value.db = true
    try {
      const result = await api.testDatabaseConfig(database.value)
      showNotice(`连接成功：MySQL ${result.version}`)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      busy.value.db = false
    }
  }

  async function saveDatabase() {
    busy.value.db = true
    try {
      database.value = await api.saveDatabaseConfig(database.value)
      await loadDatabaseTables()
      showNotice('本地数据库配置已保存')
    } catch (error) {
      showNotice(errorMessage(error), 'error')
    } finally {
      busy.value.db = false
    }
  }

  function toggleZip(fileId) {
    selectedZipIds.value = selectedZipIds.value.includes(fileId)
      ? selectedZipIds.value.filter((id) => id !== fileId)
      : [...selectedZipIds.value, fileId]
  }

  function toggleLocalArchive(fileId) {
    selectedLocalArchiveIds.value = selectedLocalArchiveIds.value.includes(fileId)
      ? selectedLocalArchiveIds.value.filter((id) => id !== fileId)
      : [...selectedLocalArchiveIds.value, fileId]
  }

  function selectAllLocalArchives() {
    selectedLocalArchiveIds.value = localArchives.value.map((archive) => archive.id)
  }

  function clearSelectedLocalArchives() {
    selectedLocalArchiveIds.value = []
  }

  function toggleMonthDirectory(directory) {
    // A changed month scope invalidates the previous ZIP list until the next scan.
    selectedMonthDirectories.value = selectedMonthDirectories.value.includes(directory)
      ? selectedMonthDirectories.value.filter((item) => item !== directory)
      : [...selectedMonthDirectories.value, directory]
    localArchives.value = []
    scanMessage.value = ''
    refreshDerivedFields()
  }

  function selectAllMonthDirectories() {
    selectedMonthDirectories.value = [...monthDirectories.value]
    localArchives.value = []
    scanMessage.value = ''
    refreshDerivedFields()
  }

  function clearSelectedMonthDirectories() {
    selectedMonthDirectories.value = []
    localArchives.value = []
    scanMessage.value = ''
    refreshDerivedFields()
  }

  onMounted(bootstrap)
  onBeforeUnmount(() => {
    sources.forEach((source) => source.close())
    clearTimeout(eventFlushTimer)
    clearTimeout(noticeTimer)
  })

  return {
    section,
    serverOnline,
    files,
    fileStats,
    tasks,
    zipFiles,
    dataFiles,
    selectedZipIds,
    localDirectory,
    mergeScope,
    localArchives,
    selectedLocalArchiveIds,
    monthDirectories,
    selectedMonthDirectories,
    scanMessage,
    selectedDataFileId,
    outputName,
    outputNamePrefix,
    outputNameSuffix,
    outputNameCustomized,
    setOutputNameManually,
    targetProduct,
    tableName,
    adjustDuplicates,
    selectedTask,
    taskEvents,
    hasOlderEvents,
    hasNewerEvents,
    eventHistoryBusy,
    sseStatus,
    loadOlderEvents,
    loadNewerEvents,
    runningTasks,
    successfulTasks,
    failedTasks,
    latestValidation,
    latestImport,
    database,
    databaseTables,
    databaseTablesError,
    databaseRecords,
    databaseQuery,
    coverageTable,
    coverageYear,
    coverageMonth,
    databaseCoverage,
    coverageError,
    tableOptions,
    selectedFileProduct,
    matchingTable,
    importTargetInvalid,
    targetProductLocked,
    tableNameLocked,
    busy,
    notice,
    cleanupConfirmOpen,
    requestClearHistory,
    cancelClearHistory,
    clearHistory,
    scanLocalArchives,
    pickLocalDirectory,
    toggleZip,
    toggleLocalArchive,
    selectAllLocalArchives,
    clearSelectedLocalArchives,
    toggleMonthDirectory,
    selectAllMonthDirectories,
    clearSelectedMonthDirectories,
    startExtract,
    setTaskPaused,
    startValidate,
    startImport,
    testDatabase,
    saveDatabase,
    selectTask,
    clearSelectedTask,
    loadFiles,
    queryDatabaseRecords,
    clearDatabaseQuery,
    queryDatabaseCoverage,
  }
}

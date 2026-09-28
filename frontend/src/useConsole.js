import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from './api'


const terminalStatuses = new Set(['success', 'failed'])

export function useConsole() {
  const section = ref('dashboard')
  const serverOnline = ref(false)
  const files = ref([])
  const fileStats = ref({ indexed: 0, available: 0, missing: 0, zip: 0, logs: 0 })
  const tasks = ref([])
  const selectedZipIds = ref([])
  const localDirectory = ref('')
  const localRootDirectory = ref('')
  const localBrowserPath = ref('')
  const localBrowserRootPath = ref('')
  const localBrowserLastPath = ref('')
  const localBrowserDirectories = ref([])
  const localBrowserOpen = ref(false)
  const localDirectoryMenuOpen = ref(false)
  const mergeScope = ref('month')
  const localArchives = ref([])
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
  const adjustDuplicates = ref(true)
  const selectedTask = ref(null)
  const taskEvents = ref([])
  const busy = ref({ scan: false, extract: false, validate: false, import: false, db: false, query: false, cleanup: false, directory: false })
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
  let noticeTimer

  if (typeof window !== 'undefined') {
    localRootDirectory.value = window.localStorage.getItem('tick-data-console.local-root') || ''
    localDirectory.value = window.localStorage.getItem('tick-data-console.local-directory') || ''
    localBrowserRootPath.value = window.localStorage.getItem('tick-data-console.local-browser-root') || ''
    localBrowserLastPath.value = window.localStorage.getItem('tick-data-console.local-browser-path') || ''
  }

  const zipFiles = computed(() => files.value.filter((file) => file.extension === '.zip'))
  const dataFiles = computed(() => files.value.filter((file) => {
    const isLogFile = file.extension === '.txt' || file.extension === '.log'
    const isRejectedFile = /[_-]rejected(?:\.[^.]+)?$/i.test(file.name || '')
    return isLogFile && !isRejectedFile
  }))
  const runningTasks = computed(() => tasks.value.filter((task) => ['pending', 'running'].includes(task.status)))
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
    tasks.value.filter((task) => !terminalStatuses.has(task.status)).forEach(connectTask)
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

  watch(section, (value) => {
    if (value === 'records' && !databaseRecords.value) queryDatabaseRecords()
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
      showNotice('请填写本地月份目录或年度目录', 'error')
      return
    }
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
      localArchives.value = result.archives || []
      monthDirectories.value = result.month_directories || []
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

  function appendEvent(event) {
    if (!event || taskEvents.value.some((item) => item.id === event.id)) return
    taskEvents.value = [...taskEvents.value, event]
  }

  function applyEvent(taskId, event) {
    const current = tasks.value.find((task) => task.id === taskId) || { id: taskId }
    const update = {
      ...current,
      progress: event.progress,
      status: event.status,
      message: event.message,
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
      appendEvent(event)
    }
    if (terminalStatuses.has(update.status)) {
      sources.get(taskId)?.close()
      sources.delete(taskId)
      loadFiles().catch(() => {})
      loadTasks().catch(() => {})
      if (update.type === 'import' && databaseRecords.value) queryDatabaseRecords(false)
    }
  }

  function connectTask(task, cursor = 0) {
    if (!task?.id || terminalStatuses.has(task.status) || sources.has(task.id)) return
    const source = new EventSource(api.eventsUrl(task.id, cursor))
    const handle = (browserEvent) => {
      if (!browserEvent.data) return
      try {
        applyEvent(task.id, JSON.parse(browserEvent.data))
      } catch {
        showNotice('收到无法解析的任务事件', 'error')
      }
    }
    ;['status', 'log', 'progress', 'result', 'error'].forEach((name) => {
      source.addEventListener(name, handle)
    })
    source.onerror = () => {
      const latest = tasks.value.find((item) => item.id === task.id)
      if (latest && terminalStatuses.has(latest.status)) {
        source.close()
        sources.delete(task.id)
      }
    }
    sources.set(task.id, source)
  }

  async function selectTask(task) {
    try {
      const detail = await api.task(task.id)
      selectedTask.value = detail
      taskEvents.value = detail.events || []
      section.value = 'logs'
      const cursor = taskEvents.value.at(-1)?.id || 0
      connectTask(detail, cursor)
    } catch (error) {
      showNotice(errorMessage(error), 'error')
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
    if (!localDirectory.value.trim() && !selectedZipIds.value.length) {
      showNotice('请先选择 ZIP 文件', 'error')
      return
    }
    if (!targetProduct.value) {
      showNotice('请选择目标品种，避免不同品种数据混入', 'error')
      return
    }
    createTask('extract', {
      file_ids: localDirectory.value.trim() ? [] : selectedZipIds.value,
      output_name: outputName.value || null,
      target_product: targetProduct.value.trim() || null,
      source_directory: localDirectory.value.trim() || null,
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

  async function openLocalDirectory(path = localRootDirectory.value.trim(), rememberRoot = true) {
    if (!path) {
      showNotice('请先填写本地根目录路径', 'error')
      return
    }
    busy.value.directory = true
    try {
      const result = await api.localDirectories(path)
      if (rememberRoot) {
        localRootDirectory.value = path
        localBrowserRootPath.value = result.directory
        if (typeof window !== 'undefined') window.localStorage.setItem('tick-data-console.local-browser-root', result.directory)
      }
      localBrowserPath.value = result.directory
      localBrowserLastPath.value = result.directory
      localBrowserDirectories.value = result.directories || []
      if (typeof window !== 'undefined') window.localStorage.setItem('tick-data-console.local-browser-path', result.directory)
      localBrowserOpen.value = true
      localDirectoryMenuOpen.value = false
      if (rememberRoot && typeof window !== 'undefined') window.localStorage.setItem('tick-data-console.local-root', path)
    } catch (error) {
      showNotice(`目录读取失败：${errorMessage(error)}`, 'error')
    } finally {
      busy.value.directory = false
    }
  }

  function sameDirectory(left, right) {
    return left.trim().replace(/[\\/]+$/, '').toLowerCase() === right.trim().replace(/[\\/]+$/, '').toLowerCase()
  }

  async function reopenLocalDirectory() {
    const configuredRoot = localRootDirectory.value.trim()
    const canResume = configuredRoot && localBrowserLastPath.value && localBrowserRootPath.value && sameDirectory(configuredRoot, localBrowserRootPath.value)
    const path = canResume ? localBrowserLastPath.value : configuredRoot
    await openLocalDirectory(path, !canResume)
  }

  async function browseLocalDirectory(path) {
    if (!path) return
    localDirectoryMenuOpen.value = false
    await openLocalDirectory(path, false)
  }

  function parentDirectory(path) {
    const normalized = path.replace(/[\\/]+$/, '')
    const separator = Math.max(normalized.lastIndexOf('\\'), normalized.lastIndexOf('/'))
    return separator > 2 ? normalized.slice(0, separator) : normalized
  }

  async function browseLocalDirectoryUp() {
    if (!localBrowserPath.value || localBrowserPath.value === localBrowserRootPath.value) return
    await browseLocalDirectory(parentDirectory(localBrowserPath.value))
  }

  function toggleLocalDirectoryMenu() {
    if (localBrowserDirectories.value.length) {
      localDirectoryMenuOpen.value = !localDirectoryMenuOpen.value
    }
  }

  function useLocalBrowserDirectory() {
    if (!localBrowserPath.value) return
    localDirectory.value = localBrowserPath.value
    localDirectoryMenuOpen.value = false
    if (typeof window !== 'undefined') window.localStorage.setItem('tick-data-console.local-directory', localDirectory.value)
    localBrowserOpen.value = false
    showNotice('已选择本地目录')
  }

  function closeLocalDirectoryBrowser() {
    localBrowserOpen.value = false
    localDirectoryMenuOpen.value = false
  }

  function toggleZip(fileId) {
    selectedZipIds.value = selectedZipIds.value.includes(fileId)
      ? selectedZipIds.value.filter((id) => id !== fileId)
      : [...selectedZipIds.value, fileId]
  }

  function toggleMonthDirectory(directory) {
    selectedMonthDirectories.value = selectedMonthDirectories.value.includes(directory)
      ? selectedMonthDirectories.value.filter((item) => item !== directory)
      : [...selectedMonthDirectories.value, directory]
    localArchives.value = []
    scanMessage.value = ''
    refreshDerivedFields()
  }

  onMounted(bootstrap)
  onBeforeUnmount(() => {
    sources.forEach((source) => source.close())
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
    localRootDirectory,
    localBrowserPath,
    localBrowserRootPath,
    localBrowserLastPath,
    localBrowserDirectories,
    localBrowserOpen,
    localDirectoryMenuOpen,
    mergeScope,
    localArchives,
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
    openLocalDirectory,
    reopenLocalDirectory,
    browseLocalDirectory,
    browseLocalDirectoryUp,
    toggleLocalDirectoryMenu,
    useLocalBrowserDirectory,
    closeLocalDirectoryBrowser,
    toggleZip,
    toggleMonthDirectory,
    startExtract,
    startValidate,
    startImport,
    testDatabase,
    saveDatabase,
    selectTask,
    loadFiles,
    queryDatabaseRecords,
    clearDatabaseQuery,
  }
}

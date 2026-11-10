<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from './api'
import { useConsole } from './useConsole'

const consoleState = useConsole()
const {
  section, serverOnline, files, fileStats, tasks, zipFiles, dataFiles, selectedZipIds,
  localDirectory, mergeScope, localArchives, selectedLocalArchiveIds, monthDirectories, selectedMonthDirectories, scanMessage, selectedDataFileId, outputNamePrefix, outputNameSuffix, targetProduct, tableName, databaseTables, databaseTablesError, selectedFileProduct, matchingTable, importTargetInvalid, targetProductLocked, tableNameLocked, tableOptions, adjustDuplicates, selectedTask,
  taskEvents, hasOlderEvents, hasNewerEvents, eventHistoryBusy, sseStatus, loadOlderEvents, loadNewerEvents,
  runningTasks, successfulTasks, failedTasks, latestValidation, setTaskPaused, selectAllMonthDirectories, clearSelectedMonthDirectories,
  latestImport, database, busy, notice, databaseRecords, databaseQuery, coverageTable, coverageYear, coverageMonth, databaseCoverage, coverageError, queryDatabaseCoverage, cleanupConfirmOpen, requestClearHistory, cancelClearHistory, clearHistory, scanLocalArchives, pickLocalDirectory, toggleMonthDirectory, setOutputNameManually, toggleZip, toggleLocalArchive, selectAllLocalArchives, clearSelectedLocalArchives, startExtract,
  startValidate, startImport, testDatabase, saveDatabase, selectTask, clearSelectedTask, queryDatabaseRecords, clearDatabaseQuery,
} = consoleState

const directoryName = (directory) => directory.split(/[\\/]/).filter(Boolean).at(-1) || directory
const terminalBody = ref(null)
const recordsPanel = ref(null)
const pageJump = ref(1)
const taskFilter = ref('all')
const calendarPicker = ref(null)
const calendarOpen = ref(false)
const calendarField = ref('start_time')
const calendarCursor = ref(new Date(new Date().getFullYear(), new Date().getMonth(), 1))
let followTerminalLogs = true
const previewFileId = ref('')
const previewKeyword = ref('')
const previewBusy = ref(false)
const previewError = ref('')
const previewResult = ref(null)
const previewStartLine = ref(1)
const previewJumpLine = ref(1)
const previewPages = ref([1])
const previewIndexStatus = ref('idle')
const previewIndexProgress = ref(0)
let previewIndexTimer = null
let previewRequestLine = 1
function previewCodeParts(line) {
  const parts = []
  const timestamp = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)/.exec(line)
  let source = line
  if (timestamp) {
    parts.push({ text: timestamp[0], tone: 'timestamp' })
    source = line.slice(timestamp[0].length)
  }
  const tokenPattern = /TICK_DATA:|\b(?:INFO|WARNING|ERROR|DEBUG)\b|"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|\b\d+(?:\.\d+)?\b/g
  let cursor = 0
  for (const match of source.matchAll(tokenPattern)) {
    if (match.index > cursor) parts.push({ text: source.slice(cursor, match.index), tone: '' })
    const token = match[0]
    let tone = 'number'
    if (token === 'TICK_DATA:') tone = 'tick-marker'
    else if (/^(INFO|WARNING|ERROR|DEBUG)$/.test(token)) tone = `level-${token.toLowerCase()}`
    else if (token.startsWith('"')) tone = /^\s*:/.test(source.slice(match.index + token.length)) ? 'json-key' : 'json-string'
    else if (/^(true|false|null)$/.test(token)) tone = 'json-literal'
    parts.push({ text: token, tone })
    cursor = match.index + token.length
  }
  if (cursor < source.length) parts.push({ text: source.slice(cursor), tone: '' })
  return parts
}

const visiblePreviewLines = computed(() => {
  const keyword = previewKeyword.value.trim().toLowerCase()
  const lines = (previewResult.value?.lines || []).map((line, index) => ({
    text: line,
    parts: previewCodeParts(line),
    number: previewStartLine.value + index,
  }))
  return keyword ? lines.filter((item) => item.text.toLowerCase().includes(keyword)) : lines
})

async function loadPreview(line = 1, resetHistory = false) {
  if (!previewFileId.value) return
  previewRequestLine = line
  previewBusy.value = true
  previewError.value = ''
  try {
    const result = await api.filePreview(previewFileId.value, { line, limit: 100 })
    previewIndexStatus.value = result.indexing ? 'indexing' : 'ready'
    previewIndexProgress.value = result.index_progress || result.progress || 0
    if (result.lines?.length) {
      previewResult.value = result
      previewStartLine.value = result.start_line || line
      previewJumpLine.value = previewStartLine.value
    } else if (!result.indexing) {
      previewResult.value = result
    }
    if (resetHistory) previewPages.value = [line]
    if (result.indexing) schedulePreviewIndexPoll()
    else if (previewIndexTimer) clearTimeout(previewIndexTimer)
  } catch (error) {
    previewError.value = error.message
  } finally {
    previewBusy.value = false
  }
}

function schedulePreviewIndexPoll() {
  if (previewIndexTimer) clearTimeout(previewIndexTimer)
  previewIndexTimer = setTimeout(async () => {
    if (!previewFileId.value) return
    try {
      const status = await api.filePreviewIndex(previewFileId.value)
      previewIndexStatus.value = status.status
      previewIndexProgress.value = status.progress || 0
      if (status.status === 'ready') loadPreview(previewRequestLine)
      else if (status.status === 'indexing') schedulePreviewIndexPoll()
      else previewError.value = '行号索引建立失败，请重新读取文件。'
    } catch (error) {
      previewError.value = error.message
    }
  }, 1200)
}

function nextPreviewPage() {
  if (!previewResult.value?.has_more || !previewResult.value?.total_lines) return
  const nextLine = previewStartLine.value + previewResult.value.lines.length
  previewPages.value.push(nextLine)
  loadPreview(nextLine)
}

function previousPreviewPage() {
  if (previewPages.value.length < 2) return
  previewPages.value.pop()
  loadPreview(previewPages.value.at(-1))
}

function jumpToPreviewLine() {
  const total = previewResult.value?.total_lines || 0
  const target = Math.min(Math.max(Number(previewJumpLine.value) || 1, 1), total)
  previewJumpLine.value = target
  previewPages.value.push(target)
  loadPreview(target)
}

watch(previewFileId, () => {
  if (previewIndexTimer) clearTimeout(previewIndexTimer)
  previewResult.value = null
  previewPages.value = [1]
  previewStartLine.value = 1
  previewJumpLine.value = 1
  previewIndexStatus.value = 'idle'
  previewIndexProgress.value = 0
  previewKeyword.value = ''
  if (previewFileId.value) loadPreview(1, true)
})
watch(() => dataFiles.value[0]?.id, (fileId) => {
  if (!previewFileId.value && fileId) previewFileId.value = fileId
}, { immediate: true })

const calendarWeekdays = ['一', '二', '三', '四', '五', '六', '日']
const calendarMonths = Array.from({ length: 12 }, (_, index) => ({ value: index, label: `${index + 1}月` }))
const lunarDays = ['', '初一', '初二', '初三', '初四', '初五', '初六', '初七', '初八', '初九', '初十', '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十', '廿一', '廿二', '廿三', '廿四', '廿五', '廿六', '廿七', '廿八', '廿九', '三十']
const solarFestivals = new Map([['01-01', '元旦'], ['05-01', '劳动节'], ['09-10', '教师节'], ['10-01', '国庆节']])
const lunarFestivals = new Map([['正月-1', '春节'], ['正月-15', '元宵节'], ['五月-5', '端午节'], ['七月-7', '七夕'], ['八月-15', '中秋节'], ['九月-9', '重阳节'], ['腊月-8', '腊八']])
let lunarFormatter = null
try {
  lunarFormatter = new Intl.DateTimeFormat('zh-CN-u-ca-chinese', { month: 'short', day: 'numeric' })
} catch {
  lunarFormatter = null
}

function handleTerminalScroll() {
  const element = terminalBody.value
  if (!element) return
  followTerminalLogs = element.scrollHeight - element.scrollTop - element.clientHeight < 48
}

async function scrollTerminalToBottom(force = false) {
  await nextTick()
  const element = terminalBody.value
  if (!element || (!force && !followTerminalLogs)) return
  element.scrollTop = element.scrollHeight
}

watch(() => taskEvents.value.at(-1)?.id, () => scrollTerminalToBottom())
watch(() => selectedTask.value?.id, () => {
  followTerminalLogs = true
  scrollTerminalToBottom(true)
})

const navigation = [
  { id: 'dashboard', icon: '◈', label: '工作台', caption: '全局运行概览' },
  { id: 'extract', icon: '◇', label: 'ZIP 合并', caption: '读取压缩包并合并日志' },
  { id: 'preview', icon: '⌕', label: '文件预览', caption: '分批查看合并日志' },
  { id: 'validate', icon: '✓', label: '质量检查', caption: '定位异常数据' },
  { id: 'import', icon: '⇣', label: '数据导入', caption: '写入本地 MySQL' },
  { id: 'coverage', icon: '▦', label: '数据覆盖', caption: '按年份与日期统计' },
  { id: 'records', icon: '≋', label: '数据查询', caption: '按表查看入库数据' },
  { id: 'database', icon: '▣', label: '数据库配置', caption: '本地连接参数' },
  { id: 'logs', icon: '⌁', label: '运行日志', caption: 'SSE 实时事件' },
]

const page = computed(() => navigation.find((item) => item.id === section.value) || navigation[0])
const activeExtractTasks = computed(() => tasks.value.filter((task) =>
  task.type === 'extract' && ['pending', 'running', 'pausing', 'paused'].includes(task.status),
))
const fileBreakdown = computed(() => {
  const zipCount = files.value.filter((file) => file.extension === '.zip').length
  const logCount = files.value.filter((file) => ['.txt', '.log'].includes(file.extension)).length
  return { zipCount, logCount }
})

const validationRate = computed(() => {
  const result = latestValidation.value
  if (!result || !result.total_lines) return 0
  return ((result.valid_lines / result.total_lines) * 100).toFixed(1)
})

function formatBytes(size = 0) {
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`
  return `${(size / 1024 ** 3).toFixed(2)} GB`
}


function formatTickTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').replace(/(\.\d{3})\d+$/, '$1')
}

function formatDateTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').replace(/\.\d+$/, '')
}

function formatYearMonth(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || ''))
  return match ? `${match[1]}年${Number(match[2])}月` : value || '—'
}

const coveragePieColors = ['#3978d4', '#16a394', '#ed9b36', '#8b72d9', '#df6474', '#54a66f', '#3ba8bf']
const coveragePieStyle = computed(() => {
  const years = databaseCoverage.value?.years || []
  const total = years.reduce((sum, item) => sum + item.total, 0)
  if (!total) return { background: '#e4e7ec' }
  let position = 0
  const slices = years.map((item, index) => {
    const start = position
    position += item.total / total * 100
    return `${coveragePieColors[index % coveragePieColors.length]} ${start}% ${position}%`
  })
  return { background: `conic-gradient(${slices.join(', ')})` }
})

function datePart(key) {
  return databaseQuery.value[key]?.slice(0, 10) || ''
}

function timePart(key) {
  return databaseQuery.value[key]?.slice(11, 16) || ''
}

function updateDateTime(key, part, value) {
  const date = part === 'date' ? value : datePart(key)
  const fallbackTime = key === 'start_time' ? '00:00' : '23:59'
  const time = part === 'time' ? value : (timePart(key) || fallbackTime)
  databaseQuery.value[key] = date ? `${date}T${time || fallbackTime}` : ''
}

function formatCalendarDate(value) {
  const pad = (number) => String(number).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

function parseCalendarDate(value) {
  const [year, month, day] = String(value || '').split('-').map(Number)
  if (!year || !month || !day) return null
  return new Date(year, month - 1, day)
}

function calendarDayMeta(value) {
  const pad = (number) => String(number).padStart(2, '0')
  const solarKey = `${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
  let festival = solarFestivals.get(solarKey) || ''
  let subtitle = ''

  if (lunarFormatter) {
    const parts = lunarFormatter.formatToParts(value)
    const lunarMonth = parts.find((part) => part.type === 'month')?.value || ''
    const lunarDay = Number(parts.find((part) => part.type === 'day')?.value || 0)
    festival ||= lunarFestivals.get(`${lunarMonth}-${lunarDay}`) || ''
    subtitle = lunarDay === 1 ? lunarMonth : (lunarDays[lunarDay] || '')
  }

  return { subtitle: festival || subtitle, festival: Boolean(festival) }
}

const calendarYears = computed(() => {
  const cursorYear = calendarCursor.value.getFullYear()
  const currentYear = new Date().getFullYear()
  const firstYear = Math.min(1990, cursorYear - 5)
  const lastYear = Math.max(currentYear + 5, cursorYear + 5)
  return Array.from({ length: lastYear - firstYear + 1 }, (_, index) => firstYear + index)
})

const calendarDays = computed(() => {
  const year = calendarCursor.value.getFullYear()
  const month = calendarCursor.value.getMonth()
  const firstDay = new Date(year, month, 1)
  const mondayOffset = (firstDay.getDay() + 6) % 7
  const today = formatCalendarDate(new Date())

  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(year, month, index - mondayOffset + 1)
    return {
      date,
      value: formatCalendarDate(date),
      day: date.getDate(),
      currentMonth: date.getMonth() === month,
      weekend: date.getDay() === 0 || date.getDay() === 6,
      today: formatCalendarDate(date) === today,
      ...calendarDayMeta(date),
    }
  })
})

const calendarRangeSummary = computed(() => {
  const start = datePart('start_time') || '未选择'
  const end = datePart('end_time') || '未选择'
  return `${start} 至 ${end}`
})

function formatDateButton(key, placeholder) {
  return datePart(key)?.replaceAll('-', '/') || placeholder
}

function openCalendar(key) {
  const selectedDate = parseCalendarDate(datePart(key))
    || parseCalendarDate(datePart(key === 'start_time' ? 'end_time' : 'start_time'))
    || new Date()
  calendarField.value = key
  calendarCursor.value = new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1)
  calendarOpen.value = true
}

function closeCalendar() {
  calendarOpen.value = false
}

function moveCalendarMonth(offset) {
  calendarCursor.value = new Date(calendarCursor.value.getFullYear(), calendarCursor.value.getMonth() + offset, 1)
}

function setCalendarYear(value) {
  calendarCursor.value = new Date(Number(value), calendarCursor.value.getMonth(), 1)
}

function setCalendarMonth(value) {
  calendarCursor.value = new Date(calendarCursor.value.getFullYear(), Number(value), 1)
}

function showCurrentMonth() {
  const today = new Date()
  calendarCursor.value = new Date(today.getFullYear(), today.getMonth(), 1)
}

function calendarDayClasses(day) {
  const start = datePart('start_time')
  const end = datePart('end_time')
  return {
    'outside-month': !day.currentMonth,
    weekend: day.weekend,
    today: day.today,
    festival: day.festival,
    'in-range': Boolean(start && end && day.value > start && day.value < end),
    'range-start': day.value === start,
    'range-end': day.value === end,
  }
}

function selectCalendarDate(day) {
  if (calendarField.value === 'start_time') {
    updateDateTime('start_time', 'date', day.value)
    if (datePart('end_time') && day.value > datePart('end_time')) databaseQuery.value.end_time = ''
    calendarField.value = 'end_time'
    return
  }

  if (datePart('start_time') && day.value < datePart('start_time')) {
    updateDateTime('start_time', 'date', day.value)
    databaseQuery.value.end_time = ''
    return
  }

  updateDateTime('end_time', 'date', day.value)
  closeCalendar()
}

function handleCalendarOutside(event) {
  if (calendarOpen.value && calendarPicker.value && !calendarPicker.value.contains(event.target)) closeCalendar()
}

onMounted(() => document.addEventListener('pointerdown', handleCalendarOutside))
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleCalendarOutside)
  if (previewIndexTimer) clearTimeout(previewIndexTimer)
})

const databasePaginationItems = computed(() => {
  const total = databaseRecords.value?.page_count || 0
  const current = databaseRecords.value?.page || 1
  if (total <= 7) return Array.from({ length: total }, (_, index) => ({ type: 'page', value: index + 1 }))

  const pages = [...new Set([1, total, current - 1, current, current + 1])]
    .filter((value) => value >= 1 && value <= total)
    .sort((left, right) => left - right)
  const items = []
  pages.forEach((value, index) => {
    if (index && value - pages[index - 1] > 1) items.push({ type: 'ellipsis', value: `ellipsis-${value}` })
    items.push({ type: 'page', value })
  })
  return items
})

watch(() => databaseRecords.value?.page, (value) => {
  if (value) pageJump.value = value
})

async function goToDatabasePage(targetPage) {
  const pageCount = databaseRecords.value?.page_count || 1
  const nextPage = Math.min(pageCount, Math.max(1, Number(targetPage) || 1))
  pageJump.value = nextPage
  if (busy.value.query || nextPage === databaseRecords.value?.page) return
  databaseQuery.value.page = nextPage
  await queryDatabaseRecords(false)
  await nextTick()
  recordsPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function jumpToDatabasePage() {
  goToDatabasePage(pageJump.value)
}

function openTaskGroup(group, status) {
  taskFilter.value = status
  section.value = 'logs'
  const task = group[0]
  if (task) {
    selectTask(task)
  } else {
    clearSelectedTask()
  }
}

const visibleLogTasks = computed(() => {
  if (taskFilter.value === 'running') {
    return tasks.value.filter((task) => ['pending', 'running', 'pausing', 'paused'].includes(task.status))
  }
  if (taskFilter.value === 'success' || taskFilter.value === 'failed') {
    return tasks.value.filter((task) => task.status === taskFilter.value)
  }
  return tasks.value
})

const taskFilterLabel = computed(() => ({
  all: '全部任务',
  running: '运行中任务',
  success: '已完成任务',
  failed: '异常任务',
}[taskFilter.value]))

function typeLabel(type) {
  return { extract: 'ZIP 合并', validate: '质量检查', import: '数据导入' }[type] || type
}

function taskSourceLabel(task) {
  if (!task) return ''
  if (task.type === 'extract' && task.payload?.output_name) return task.payload.output_name
  if (task.payload?.display_name) return task.payload.display_name
  const fileId = task.payload?.file_id
  if (fileId) return files.value.find((file) => file.id === fileId)?.name || `文件 ${fileId.slice(0, 8)}`
  const fileIds = task.payload?.file_ids || []
  if (fileIds.length) {
    const names = fileIds.map((id) => files.value.find((file) => file.id === id)?.name).filter(Boolean)
    if (names.length === 1) return names[0]
    if (names.length) return `${names[0]} 等 ${fileIds.length} 个 ZIP`
    return `${fileIds.length} 个 ZIP 文件`
  }
  return task.payload?.source_directory || '未记录来源'
}

function statusLabel(status) {
  return { pending: '等待中', running: '运行中', pausing: '正在暂停', paused: '已暂停', success: '已完成', failed: '失败' }[status] || status
}

function eventTypeLabel(type) {
  return { status: '状态', log: '说明', progress: '进度', result: '完成', error: '错误' }[type] || type
}

function eventTone(level) {
  return level === 'error' ? 'error' : level === 'warning' ? 'warning' : 'info'
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><span></span><span></span><span></span></div>
        <div>
          <strong>Tick 数据平台</strong>
          <small>本地数据作业控制台</small>
        </div>
      </div>

      <nav class="navigation">
        <button
          v-for="item in navigation"
          :key="item.id"
          class="nav-item"
          :class="{ active: section === item.id }"
          @click="section = item.id"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span><b>{{ item.label }}</b><small>{{ item.caption }}</small></span>
        </button>
      </nav>

      <div class="sidebar-foot">
        <div class="connection-card">
          <span class="status-dot" :class="{ online: serverOnline }"></span>
          <div><b>{{ serverOnline ? '服务运行正常' : '服务未连接' }}</b><small>127.0.0.1:8000</small></div>
        </div>
        <p>核心逻辑来自 joinzip / jq</p>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div>
          <p class="eyebrow">数据运维中心 / {{ page.label }}</p>
          <h1>{{ page.label }}</h1>
          <p>{{ page.caption }} · {{ new Date().toLocaleDateString('zh-CN') }}</p>
        </div>
        <div class="top-actions">
          <div class="live-pill"><i></i>任务实时更新</div>
          <button class="ghost-button" @click="section = 'logs'">查看任务 {{ tasks.length }}</button>
        </div>
      </header>

      <section v-if="section === 'dashboard'" class="page-stack">
        <div class="hero-card">
          <div class="hero-copy">
            <span class="tag">本地数据自动化</span>
            <h2>数据处理流程，<br><em>一站式完成。</em></h2>
            <p>从本地 ZIP 读取日志、筛选合并、质量检查到 MySQL 入库，全程可追踪、可回放。</p>
            <div class="hero-actions">
              <button class="primary-button" @click="section = 'extract'">开始 ZIP 合并</button>
              <button class="secondary-button" @click="section = 'database'">配置本地数据库</button>
            </div>
          </div>
          <div class="pipeline-visual">
            <div class="pipeline-node"><span>01</span><b>ZIP</b><small>读取日志</small></div>
            <i>→</i>
            <div class="pipeline-node accent"><span>02</span><b>LOG</b><small>解析校验</small></div>
            <i>→</i>
            <div class="pipeline-node"><span>03</span><b>SQL</b><small>本地入库</small></div>
          </div>
        </div>

        <div class="metric-grid">
          <button type="button" class="metric-card metric-card-action" :aria-label="`筛选运行中任务，共 ${runningTasks.length} 个`" @click="openTaskGroup(runningTasks, 'running')"><span class="metric-icon cyan">↻</span><span class="metric-copy"><small>运行中任务</small><strong>{{ runningTasks.length }}</strong><span class="metric-detail">后台持续执行 · 点击筛选</span></span></button>
          <button type="button" class="metric-card metric-card-action" :aria-label="`筛选已完成任务，共 ${successfulTasks.length} 个`" @click="openTaskGroup(successfulTasks, 'success')"><span class="metric-icon green">✓</span><span class="metric-copy"><small>已完成任务</small><strong>{{ successfulTasks.length }}</strong><span class="metric-detail">结果已持久化 · 点击筛选</span></span></button>
          <button type="button" class="metric-card metric-card-action" :aria-label="`筛选异常任务，共 ${failedTasks.length} 个`" @click="openTaskGroup(failedTasks, 'failed')"><span class="metric-icon orange">!</span><span class="metric-copy"><small>异常任务</small><strong>{{ failedTasks.length }}</strong><span class="metric-detail">可查看完整日志 · 点击筛选</span></span></button>
          <article class="metric-card clickable" role="button" tabindex="0" @click="section = 'validate'" @keydown.enter="section = 'validate'"><span class="metric-icon violet">▤</span><div><small>项目托管文件</small><strong>{{ fileStats.available }}</strong><p>{{ fileStats.logs }} 个生成日志 · 点击查看</p></div></article>
        </div>

        <article class="panel dashboard-explain">
          <div class="dashboard-explain-icon">i</div>
          <div><b>项目文件统计与数据库记录分开计算</b><p>当前托管文件 {{ fileStats.available }} 个，索引登记 {{ fileStats.indexed }} 条，缺失 {{ fileStats.missing }} 条，包含合并日志和异常数据文件。任务临时文件不计入；MySQL 中的 Tick 记录数请查看“数据查询”。</p></div>
          <div class="dashboard-explain-actions"><button class="text-button" @click="section = 'records'">查看数据库数据 →</button><div class="cleanup-action"><button class="danger-button" :disabled="busy.cleanup" @click="requestClearHistory">{{ busy.cleanup ? '正在清理…' : '清理生成文件与任务记录' }}</button><small>会删除项目生成文件、任务记录及运行日志，不影响 MySQL 数据</small></div></div>
        </article>

        <div class="two-column">
          <article class="panel">
            <div class="panel-head"><div><span class="section-kicker">任务概览</span><h3>最近任务</h3></div><button class="text-button" @click="section = 'logs'">全部日志 →</button></div>
            <div v-if="tasks.length" class="task-list compact">
              <button v-for="task in tasks.slice(0, 5)" :key="task.id" class="task-row" @click="selectTask(task)">
                <span class="task-type">{{ typeLabel(task.type).slice(0, 1) }}</span>
                <span class="task-main"><b>{{ typeLabel(task.type) }}</b><small>{{ task.message }}</small></span>
                <span class="mini-progress"><i :style="{ width: `${task.progress}%` }"></i></span>
                <span class="status-badge" :class="task.status">{{ statusLabel(task.status) }}</span>
              </button>
            </div>
            <div v-else class="empty-state"><b>还没有任务</b><p>启动 ZIP 合并、质量检查或数据导入后，任务记录会显示在这里。</p></div>
          </article>

          <article class="panel system-panel">
            <div class="panel-head"><div><span class="section-kicker">系统状态</span><h3>流程状态</h3></div><span class="pulse-label">实时</span></div>
            <div class="system-line"><span><i class="cyan-dot"></i>FastAPI 后台任务</span><b>{{ serverOnline ? '在线' : '离线' }}</b></div>
            <div class="system-line"><span><i class="green-dot"></i>SSE 事件推送</span><b>已启用</b></div>
            <div class="system-line"><span><i class="violet-dot"></i>本地 MySQL</span><b>{{ database.host }}:{{ database.port }}</b></div>
            <div class="system-line"><span><i class="orange-dot"></i>重复时间调整</span><b>{{ adjustDuplicates ? '已启用' : '已关闭' }}</b></div>
          </article>
        </div>
      </section>

      <section v-else-if="section === 'extract'" class="page-stack">
        <div class="two-column wide-left">
          <article class="panel">
            <div class="panel-head"><div><span class="section-kicker">本地 ZIP 处理</span><h3>选择并合并压缩包中的日志</h3></div><span class="tag subtle">本机直读</span></div>
            <div class="field directory-manual-field">
              <span><b>选择数据文件夹</b><em>使用 Windows 文件夹窗口，或直接填写路径</em></span>
              <div class="directory-config"><input v-model="localDirectory" placeholder="选择或输入 ZIP 所在文件夹" @keyup.enter="scanLocalArchives" /><button class="secondary-button" type="button" :disabled="busy.directory || busy.scan" @click="pickLocalDirectory">{{ busy.directory ? '正在打开…' : '浏览文件夹' }}</button></div>
            </div>
            <div class="scope-section"><div class="scope-section-head"><div><span class="field-label">处理范围</span><strong>{{ mergeScope === 'year' ? '全年合并' : '按月筛选' }}</strong></div><span class="scope-section-path">{{ localDirectory || '尚未选择处理目录' }}</span></div><div class="scope-grid"><button class="scope-card" :class="{ active: mergeScope === 'month' }" :aria-pressed="mergeScope === 'month'" @click="mergeScope = 'month'"><span class="scope-choice-mark">{{ mergeScope === 'month' ? '✓' : '' }}</span><b>按月筛选</b><small>处理当前月份目录；选择年度目录时，可勾选一个或多个指定月份</small></button><button class="scope-card" :class="{ active: mergeScope === 'year' }" :aria-pressed="mergeScope === 'year'" @click="mergeScope = 'year'"><span class="scope-choice-mark">{{ mergeScope === 'year' ? '✓' : '' }}</span><b>全年合并</b><small>按月份顺序处理所选年度目录中的全部 ZIP</small></button></div></div>
            <div v-if="monthDirectories.length" class="field month-picker-field"><div class="month-picker-heading"><span>月份范围</span><div><button type="button" :disabled="selectedMonthDirectories.length === monthDirectories.length" @click="selectAllMonthDirectories">全选</button><button type="button" :disabled="!selectedMonthDirectories.length" @click="clearSelectedMonthDirectories">清空</button></div></div><div class="month-picker"><button v-for="directory in monthDirectories" :key="directory" type="button" class="month-option" :class="{ selected: selectedMonthDirectories.includes(directory) }" :aria-pressed="selectedMonthDirectories.includes(directory)" @click="toggleMonthDirectory(directory)"><span class="month-check">{{ selectedMonthDirectories.includes(directory) ? '✓' : '' }}</span>{{ directoryName(directory) }}</button></div><div class="month-selection-summary">已选 {{ selectedMonthDirectories.length }} / {{ monthDirectories.length }} 个月{{ selectedMonthDirectories.length ? ' · 重新扫描后更新 ZIP 清单' : ' · 至少选择一个月份' }}</div></div>
            <div class="scan-action"><button class="secondary-button full" :disabled="busy.scan || busy.directory || !localDirectory.trim() || (monthDirectories.length > 0 && mergeScope === 'month' && !selectedMonthDirectories.length)" @click="scanLocalArchives">{{ busy.scan ? '正在扫描目录…' : localArchives.length ? '重新扫描 ZIP' : '扫描 ZIP' }}</button></div>
            <div v-if="localArchives.length" class="archive-selection">
              <div class="archive-selection-head"><div><b>选择处理文件</b><small>已选 {{ selectedLocalArchiveIds.length }} / {{ localArchives.length }} 个 ZIP</small></div><div><button type="button" :disabled="selectedLocalArchiveIds.length === localArchives.length" @click="selectAllLocalArchives">全选</button><button type="button" :disabled="!selectedLocalArchiveIds.length" @click="clearSelectedLocalArchives">清空</button></div></div>
              <div class="archive-list" role="group" aria-label="扫描到的 ZIP 文件">
                <label v-for="archive in localArchives" :key="archive.id" class="archive-option" :class="{ selected: selectedLocalArchiveIds.includes(archive.id) }">
                  <input type="checkbox" :checked="selectedLocalArchiveIds.includes(archive.id)" @change="toggleLocalArchive(archive.id)" />
                  <span class="archive-file-icon" aria-hidden="true">ZIP</span>
                  <span class="archive-option-name" :title="archive.name">{{ archive.name }}</span>
                  <span v-if="archive.month_directory" class="archive-month-label">{{ archive.month_directory }}</span>
                  <span v-else class="archive-month-label unknown">月份未识别</span>
                  <span class="archive-option-check">{{ selectedLocalArchiveIds.includes(archive.id) ? '已选' : '' }}</span>
                </label>
              </div>
              <small class="archive-selection-path">{{ scanMessage || `扫描目录：${localDirectory}` }}</small>
            </div>
            <div v-else-if="scanMessage" class="empty-inline local-empty" :class="{ 'scan-error': /失败|不存在|请检查|错误/.test(scanMessage) }">{{ scanMessage }}</div>
            <div v-else class="empty-inline local-empty">{{ localDirectory ? '尚未扫描此目录。扫描后会显示 ZIP 数量和处理范围。' : '先选择或填写处理目录。' }}</div>
          </article>

          <article class="panel action-panel">
            <span class="section-kicker">ZIP 读取与合并</span>
            <h3>读取 ZIP 并合并日志</h3>
            <p>读取 ZIP 内的 <code>log.txt</code>，筛选有效 Tick 数据和目标品种，生成合并日志及异常数据文件。</p>
            <label class="field"><span>输出文件名</span><div class="filename-editor"><span class="filename-prefix">{{ outputNamePrefix }}</span><input v-model="outputNameSuffix" placeholder="例如 2023_04.txt" /></div><small>商品标识由目标品种自动生成且不可修改；只能编辑后面的年份、月份或自定义说明。</small></label>
            <label class="field"><span>目标品种 <i class="field-hint">来自本地数据库</i></span><select v-model="targetProduct" :disabled="targetProductLocked"><option value="">请选择目标品种</option><option v-for="product in databaseTables" :key="product.code" :value="product.code">{{ product.label }}</option></select><small v-if="databaseTablesError" class="error-text">{{ databaseTablesError }}</small><small v-else-if="targetProductLocked" class="matched-text">✓ 已根据本地目录自动匹配 {{ targetProduct }}</small><small v-else-if="!databaseTables.length">请先配置数据库并确保存在 `tick_data_品种代码` 表。</small></label>
            <div class="filter-tip"><span>✓</span><p>按 <code>underlying_symbol</code>、<code>code</code> 和 <code>commodity_name</code> 筛选目标品种；格式错误、字段异常或品种不匹配的数据会写入异常数据文件（rejected）。</p></div>
            <div class="flow-steps"><span>读取 ZIP</span><i>→</i><span>定位 log.txt</span><i>→</i><span>筛选有效 Tick</span><i>→</i><span>写入合并日志</span></div>
            <div v-if="activeExtractTasks.length" class="extract-task-state concurrent">
              <div class="concurrent-task-summary"><b>已有 {{ activeExtractTasks.length }} 个合并任务运行中</b><small>当前页面仍可启动新的年份或月份合并，任务会独立执行</small><span v-for="task in activeExtractTasks.slice(0, 3)" :key="task.id">{{ taskSourceLabel(task) }} · {{ task.progress || 0 }}%</span><span v-if="activeExtractTasks.length > 3">还有 {{ activeExtractTasks.length - 3 }} 个任务</span></div>
              <button type="button" class="text-button" @click="section = 'logs'">查看任务</button>
            </div>
            <button class="primary-button full" :disabled="busy.extract || busy.upload || !localArchives.length" @click="startExtract">
              {{ busy.extract ? '正在创建任务…' : activeExtractTasks.length ? `并发启动${mergeScope === 'year' ? '全年' : '按月'}合并` : `开始${mergeScope === 'year' ? '全年' : '按月'}合并 ${selectedLocalArchiveIds.length} / ${localArchives.length} 个 ZIP` }}
            </button>
          </article>
        </div>
      </section>

      <section v-else-if="section === 'preview'" class="page-stack">
        <article class="panel control-strip preview-controls">
          <div><span class="section-kicker">合并结果</span><h3>日志文件预览</h3><p>选中文件后自动读取首批数据，按需继续浏览。</p></div>
          <div class="control-actions preview-actions">
            <label class="field select-field"><span>日志文件</span><select v-model="previewFileId"><option value="">请选择合并日志</option><option v-for="file in dataFiles" :key="file.id" :value="file.id">{{ file.name }}</option></select></label>
            <button class="secondary-button preview-refresh-button" :disabled="previewBusy || !previewFileId" @click="loadPreview(1, true)">{{ previewBusy ? '读取中…' : '重新读取' }}</button>
          </div>
        </article>
        <article class="panel preview-panel">
          <div class="panel-head"><div><span class="section-kicker">原始 Tick 日志</span><h3>{{ dataFiles.find((file) => file.id === previewFileId)?.name || '尚未选择文件' }}</h3><small v-if="previewResult" class="result-caption">文件大小 {{ formatBytes(previewResult.size) }} · <template v-if="previewResult.total_lines">共 {{ previewResult.total_lines.toLocaleString() }} 行 · 当前 {{ previewStartLine.toLocaleString() }}–{{ (previewStartLine + previewResult.lines.length - 1).toLocaleString() }} 行</template><template v-else>正在统计总行数 {{ previewIndexProgress }}% · 当前显示 {{ previewResult.lines.length }} 行</template></small></div><label v-if="previewResult" class="field preview-search"><span>本批搜索</span><input v-model="previewKeyword" placeholder="输入关键词" /></label></div>
          <div v-if="previewError" class="empty-inline scan-error">{{ previewError }}</div>
          <div v-else-if="previewBusy && !previewResult" class="empty-state"><b>正在读取文件…</b></div>
          <div v-else-if="!previewResult" class="empty-state"><b>选择一个合并日志开始查看</b><p>每批最多读取 100 行。</p></div>
          <template v-else>
            <div class="preview-lines"><div v-for="item in visiblePreviewLines" :key="`${previewStartLine}-${item.number}`"><span>{{ item.number.toLocaleString() }}</span><code><span v-for="(part, partIndex) in item.parts" :key="partIndex" :class="part.tone">{{ part.text }}</span></code></div><div v-if="!visiblePreviewLines.length" class="empty-inline">本批没有匹配的行</div></div>
            <div class="preview-pagination"><form class="preview-line-jump" @submit.prevent="jumpToPreviewLine"><label for="preview-line-number">跳转行</label><input id="preview-line-number" v-model.number="previewJumpLine" type="number" min="1" :max="previewResult.total_lines || undefined" :disabled="previewBusy || !previewResult.total_lines" /><span v-if="previewResult.total_lines">/ {{ previewResult.total_lines.toLocaleString() }}</span><button class="secondary-button" type="submit" :disabled="previewBusy || !previewResult.total_lines">跳转</button></form><div class="preview-page-actions"><button class="secondary-button" :disabled="previewBusy || previewPages.length < 2" @click="previousPreviewPage">上一批</button><button class="primary-button" :disabled="previewBusy || !previewResult.total_lines || !previewResult.has_more" @click="nextPreviewPage">{{ previewBusy ? '读取中…' : '下一批' }}</button></div></div>
          </template>
        </article>
      </section>

      <section v-else-if="section === 'validate'" class="page-stack">
        <article class="panel control-strip quality-toolbar">
          <div><span class="section-kicker">入库前检查</span><h3>数据质量检查</h3><p>入库前检查解析错误、缺失字段、盘口异常和重复时间戳。</p></div>
          <div class="control-actions quality-toolbar-actions">
            <label class="field select-field"><span>日志文件</span><select v-model="selectedDataFileId"><option value="">请选择</option><option v-for="file in dataFiles" :key="file.id" :value="file.id">{{ file.name }}</option></select></label>
            <label class="switch-line"><input v-model="adjustDuplicates" type="checkbox" /><span></span>模拟重复时间戳调整</label>
            <button class="primary-button" :disabled="busy.validate || !selectedDataFileId" @click="startValidate">{{ busy.validate ? '启动中…' : '运行检查' }}</button>
          </div>
        </article>

        <template v-if="latestValidation">
          <div class="metric-grid quality-grid">
            <article class="metric-card"><span class="metric-icon green">%</span><div><small>有效率</small><strong>{{ validationRate }}%</strong><p>{{ latestValidation.valid_lines }} 条有效</p></div></article>
            <article class="metric-card"><span class="metric-icon orange">!</span><div><small>无效行</small><strong>{{ latestValidation.invalid_lines }}</strong><p>解析或字段错误</p></div></article>
            <article class="metric-card"><span class="metric-icon cyan">⌁</span><div><small>时间调整</small><strong>{{ latestValidation.timestamp_adjustments }}</strong><p>重复秒级时间戳</p></div></article>
            <article class="metric-card"><span class="metric-icon violet">Σ</span><div><small>总行数</small><strong>{{ latestValidation.total_lines }}</strong><p>编码 {{ latestValidation.encoding }}</p></div></article>
          </div>


        <div class="two-column">
            <article class="panel">
              <div class="panel-head"><div><span class="section-kicker">检查结果</span><h3>问题分布</h3></div></div>
              <div v-if="Object.keys(latestValidation.issue_counts || {}).length" class="issue-list">
                <div v-for="(count, name) in latestValidation.issue_counts" :key="name"><span>{{ name }}</span><i><b :style="{ width: `${Math.min(100, count / latestValidation.total_lines * 100)}%` }"></b></i><strong>{{ count }}</strong></div>
              </div>
              <div v-else class="empty-state"><b>未发现质量问题</b><p>当前文件通过所有检查规则。</p></div>
            </article>
            <article class="panel">
              <div class="panel-head"><div><span class="section-kicker">异常明细</span><h3>异常样本</h3></div><span class="tag subtle">最多 100 条</span></div>
              <div class="sample-list">
                <div v-for="sample in (latestValidation.samples || []).slice(0, 8)" :key="`${sample.line}-${sample.message}`" class="sample-row"><span>行 {{ sample.line }}</span><div><b>{{ sample.message }}</b><code>{{ sample.content }}</code></div></div>
                <div v-if="!latestValidation.samples?.length" class="empty-inline">没有异常样本</div>
              </div>
            </article>
          </div>
        </template>
        <div v-else class="blank-canvas"><span>✓</span><h3>选择日志并运行质量检查</h3><p>检查结果将在这里展示，不会写入数据库。</p></div>
      </section>

      <section v-else-if="section === 'import'" class="page-stack">
        <div class="two-column wide-left">
          <article class="panel form-panel">
            <span class="section-kicker">数据入库</span><h3>导入聚宽 Tick 数据</h3><p>沿用 jq 脚本的解析、字段转换和重复时间戳调整逻辑，只允许写入本地数据库中已有的对应表。</p>
            <label class="field"><span>日志文件</span><select v-model="selectedDataFileId"><option value="">请选择日志文件</option><option v-for="file in dataFiles" :key="file.id" :value="file.id">{{ file.name }}</option></select><small v-if="selectedFileProduct && matchingTable" class="matched-text">✓ 文件品种 {{ selectedFileProduct }} 对应 {{ matchingTable.value }}</small><small v-else-if="importTargetInvalid" class="error-text">数据库缺少 `tick_data_{{ selectedFileProduct }}`，该文件不能导入</small></label>
            <label class="field"><span>目标表名 <i class="field-hint">来自本地数据库</i></span><select v-model="tableName" :disabled="tableNameLocked"><option value="">请选择目标表</option><option v-for="table in tableOptions" :key="table.value" :value="table.value">{{ table.label }}</option></select><small v-if="tableNameLocked" class="matched-text">✓ 已自动匹配日志文件对应表，不允许切换</small><small v-else>只显示本地数据库中已经存在的 `tick_data_%` 表，不会自动创建新表。</small></label>
            <label class="switch-line boxed"><input v-model="adjustDuplicates" type="checkbox" /><span></span><div><b>自动调整重复时间戳</b><small>同一合约同一秒内的数据将分配微秒偏移</small></div></label>
            <div class="database-target"><span class="db-symbol">DB</span><div><small>当前目标</small><b>{{ database.user }}@{{ database.host }}:{{ database.port }}/{{ database.database }}</b></div><button class="text-button" @click="section = 'database'">修改</button></div>
            <div v-if="databaseTablesError" class="empty-inline local-empty">数据库表读取失败：{{ databaseTablesError }}</div>
            <div v-else-if="!databaseTables.length" class="empty-inline local-empty">本地数据库中没有发现 `tick_data_%` 表，请先创建目标数据表。</div>
            <button class="primary-button full" :disabled="busy.import || !selectedDataFileId || !tableName || !databaseTables.length || importTargetInvalid" @click="startImport">{{ busy.import ? '正在创建任务…' : '启动后台导入任务' }}</button>
          </article>
          <article class="panel result-panel">
            <div class="panel-head"><div><span class="section-kicker">导入统计</span><h3>最近导入结果</h3></div></div>
            <template v-if="latestImport">
              <div class="result-number"><strong>{{ latestImport.inserted }}</strong><span>新增记录</span></div>
              <div class="result-grid"><div><small>重复跳过</small><b>{{ latestImport.duplicates }}</b></div><div><small>解析失败</small><b>{{ latestImport.failed_parse }}</b></div><div><small>写入失败</small><b>{{ latestImport.failed_insert }}</b></div><div><small>时间调整</small><b>{{ latestImport.timestamp_adjustments }}</b></div></div>
              <div class="result-meta"><span>数据库</span><b>{{ latestImport.database }} / {{ latestImport.table }}</b><span>本次入库</span><b>新增 {{ latestImport.inserted }} 条，重复跳过 {{ latestImport.duplicates }} 条</b><span>日期范围</span><b>{{ formatDateTime(latestImport.database_stats?.min_time) }} → {{ formatDateTime(latestImport.database_stats?.max_time) }}</b></div>
            </template>
            <div v-else class="empty-state tall"><b>暂无导入记录</b><p>导入完成后显示新增、重复和失败统计。</p></div>
          </article>
        </div>
      </section>

      <section v-else-if="section === 'coverage'" class="page-stack">
        <article class="panel coverage-toolbar">
          <div><span class="section-kicker">数据库日期档案</span><h3>数据覆盖统计</h3></div>
          <div class="coverage-controls">
            <label class="field"><span>数据表</span><select v-model="coverageTable" @change="queryDatabaseCoverage({ table: coverageTable, year: null, month: null, refresh: true })"><option v-for="table in tableOptions" :key="table.value" :value="table.value">{{ table.label }}</option></select></label>
            <button class="secondary-button" :disabled="busy.coverage || !coverageTable" @click="queryDatabaseCoverage({ year: null, month: null, refresh: true })">{{ busy.coverage ? '统计中…' : '刷新统计' }}</button>
          </div>
        </article>

        <div v-if="coverageError" class="empty-inline coverage-error">{{ coverageError }}</div>
        <article v-else-if="busy.coverage && !databaseCoverage" class="panel empty-state records-placeholder"><b>正在读取日期覆盖…</b></article>
        <template v-else-if="databaseCoverage">
          <div class="coverage-periods">
            <article v-for="period in [
              { key: 'day', label: '今天', range: '自然日' },
              { key: 'week', label: '本周', range: '周一至今天' },
              { key: 'month', label: '本月', range: '自然月' },
              { key: 'year', label: '今年', range: '自然年' },
            ]" :key="period.key" class="coverage-period">
              <small>{{ period.label }}</small><strong>{{ databaseCoverage.periods[period.key].toLocaleString() }}</strong><span>{{ period.range }} · 条</span>
            </article>
            <article class="coverage-period coverage-total"><small>表内累计</small><strong>{{ databaseCoverage.total.toLocaleString() }}</strong><span>Tick 条数 · {{ databaseCoverage.min_year }} 至 {{ databaseCoverage.max_year }}</span></article>
          </div>

          <article class="panel coverage-years-panel">
            <div class="panel-head"><div><span class="section-kicker">完整年份</span><h3>实际存在数据的年份</h3><small class="result-caption">仅列出数据库中确实有 Tick 记录的年份</small></div><span class="tag subtle">{{ databaseCoverage.years.length }} 年</span></div>
            <div class="coverage-years-layout">
              <div class="coverage-year-list">
                <button v-for="(item, index) in databaseCoverage.years" :key="item.year" type="button" class="coverage-year" :class="{ active: item.year === coverageYear }" @click="queryDatabaseCoverage({ year: item.year, month: null })"><i :style="{ background: coveragePieColors[index % coveragePieColors.length] }"></i><b>{{ item.year }}</b><span>{{ item.total.toLocaleString() }} 条</span><small>{{ (item.total / databaseCoverage.total * 100).toFixed(1) }}%</small></button>
              </div>
              <div class="coverage-pie" :style="coveragePieStyle" role="img" aria-label="各年份数据量占比"><div><strong>{{ databaseCoverage.years.length }}</strong><span>个年份</span></div></div>
            </div>
          </article>

          <div class="coverage-detail-grid">
            <article class="panel coverage-chart-panel">
              <div class="panel-head"><div><span class="section-kicker">{{ coverageYear }} 年</span><h3>每月数据量</h3></div><strong>{{ databaseCoverage.selected_year_total.toLocaleString() }} 条</strong></div>
              <div v-if="databaseCoverage.months.length" class="coverage-month-chart">
                <button v-for="item in databaseCoverage.months" :key="item.month" type="button" class="coverage-month" :class="{ active: item.month === coverageMonth }" :title="`${item.month} 月 · ${item.total.toLocaleString()} 条`" @click="queryDatabaseCoverage({ year: coverageYear, month: item.month })">
                  <span class="coverage-bar-value">{{ item.total.toLocaleString() }}</span><span class="coverage-bar-track"><i :style="{ height: `${Math.max(4, item.total / Math.max(...databaseCoverage.months.map((entry) => entry.total)) * 100)}%` }"></i></span><b>{{ item.month }}月</b>
                </button>
              </div>
              <div v-else class="empty-inline">该年份没有月份统计</div>
            </article>

            <article class="panel coverage-days-panel">
              <div class="panel-head"><div><span class="section-kicker">日期明细</span><h3>{{ coverageMonth ? `${coverageYear}年${coverageMonth}月` : `${coverageYear}年` }} · 有数据日期</h3></div><strong>{{ databaseCoverage.selected_month_total.toLocaleString() }} 条</strong></div>
              <div v-if="coverageMonth && databaseCoverage.days.length" class="coverage-day-list">
                <div v-for="item in databaseCoverage.days" :key="item.day" class="coverage-day"><time>{{ coverageYear }}-{{ String(coverageMonth).padStart(2, '0') }}-{{ String(item.day).padStart(2, '0') }}</time><b>{{ item.total.toLocaleString() }}</b><span>条</span></div>
              </div>
              <div v-else-if="coverageMonth" class="empty-inline">该月没有日期记录</div>
              <div v-else class="empty-inline">选择左侧月份，查看每天的数据条数</div>
            </article>
          </div>
        </template>
        <article v-else class="panel empty-state records-placeholder"><b>没有可统计的数据表</b><p>配置数据库并创建或导入 Tick 表后，这里会列出实际入库年份与日期。</p></article>
      </section>

      <section v-else-if="section === 'records'" class="page-stack">
        <article class="panel database-query-panel">
          <div class="panel-head query-panel-head"><div><span class="section-kicker">MySQL 数据浏览</span><h3>查询已入库 Tick 数据</h3></div><span class="query-default-note">默认显示首个表最新 50 条</span></div>
          <div class="query-grid compact-query-grid">
            <label class="field table-filter"><span>数据表</span><select v-model="databaseQuery.table_name"><option value="">全部表（按表分组）</option><option v-for="table in tableOptions" :key="table.value" :value="table.value">{{ table.label }}</option></select></label>
            <label class="field keyword-filter"><span>关键词</span><input v-model="databaseQuery.keyword" placeholder="合约、品种或交易所" @keyup.enter="queryDatabaseRecords()" /></label>
            <label class="field code-filter"><span>合约代码</span><input v-model="databaseQuery.code" placeholder="例如 A2305.XDCE" @keyup.enter="queryDatabaseRecords()" /></label>
            <div ref="calendarPicker" class="field date-range-field">
              <span>日期范围</span>
              <div class="date-range-control">
                <div class="calendar-date-time" :class="{ active: calendarOpen && calendarField === 'start_time' }">
                  <button class="calendar-date-button" type="button" @click="openCalendar('start_time')"><span>{{ formatDateButton('start_time', '开始日期') }}</span><i>⌄</i></button>
                  <input aria-label="开始时间" :disabled="!datePart('start_time')" :value="timePart('start_time')" type="time" @input="updateDateTime('start_time', 'time', $event.target.value)" />
                </div>
                <span class="range-separator">至</span>
                <div class="calendar-date-time" :class="{ active: calendarOpen && calendarField === 'end_time' }">
                  <button class="calendar-date-button" type="button" @click="openCalendar('end_time')"><span>{{ formatDateButton('end_time', '结束日期') }}</span><i>⌄</i></button>
                  <input aria-label="结束时间" :disabled="!datePart('end_time')" :value="timePart('end_time')" type="time" @input="updateDateTime('end_time', 'time', $event.target.value)" />
                </div>
              </div>
              <Transition name="calendar-popover">
                <div v-if="calendarOpen" class="calendar-popover" tabindex="-1" @keydown.esc="closeCalendar">
                  <div class="calendar-toolbar">
                    <strong>{{ calendarField === 'start_time' ? '选择开始日期' : '选择结束日期' }}</strong>
                    <div class="calendar-navigation">
                      <button type="button" aria-label="上个月" @click="moveCalendarMonth(-1)">‹</button>
                      <select :value="calendarCursor.getFullYear()" aria-label="选择年份" @change="setCalendarYear($event.target.value)"><option v-for="year in calendarYears" :key="year" :value="year">{{ year }}年</option></select>
                      <select :value="calendarCursor.getMonth()" aria-label="选择月份" @change="setCalendarMonth($event.target.value)"><option v-for="month in calendarMonths" :key="month.value" :value="month.value">{{ month.label }}</option></select>
                      <button type="button" aria-label="下个月" @click="moveCalendarMonth(1)">›</button>
                    </div>
                    <button class="calendar-today-button" type="button" @click="showCurrentMonth">今天</button>
                  </div>
                  <div class="calendar-weekdays"><span v-for="(weekday, index) in calendarWeekdays" :key="weekday" :class="{ weekend: index > 4 }">{{ weekday }}</span></div>
                  <div class="calendar-days">
                    <button v-for="day in calendarDays" :key="day.value" type="button" :class="calendarDayClasses(day)" @click="selectCalendarDate(day)">
                      <span>{{ day.day }}</span><small>{{ day.subtitle }}</small><em v-if="day.today">今</em>
                    </button>
                  </div>
                  <div class="calendar-footer"><span>{{ calendarRangeSummary }}</span><button type="button" @click="closeCalendar">完成</button></div>
                </div>
              </Transition>
            </div>
            <label class="field page-size-filter"><span>每表条数</span><select v-model.number="databaseQuery.page_size"><option :value="25">25</option><option :value="50">50</option><option :value="100">100</option><option :value="200">200</option></select></label>
            <div class="query-actions"><button class="secondary-button" :disabled="busy.query" @click="clearDatabaseQuery">重置</button><button class="primary-button" :disabled="busy.query" @click="queryDatabaseRecords()">{{ busy.query ? '查询中…' : '查询' }}</button></div>
          </div>
          <div class="query-hint">提示：点击开始或结束日期打开中文日历；选择开始日期后可继续选择结束日期。</div>
        </article>

        <article v-if="databaseRecords" ref="recordsPanel" class="panel records-panel">
          <div class="panel-head"><div><span class="section-kicker">查询结果</span><h3>共 {{ databaseRecords.total }} 条匹配记录</h3><small class="result-caption">{{ databaseRecords.groups?.length || 0 }} 个数据表分组 · 每组显示 {{ databaseRecords.page_size }} 条</small></div><span class="tag subtle">{{ databaseRecords.database }}</span></div>
          <div v-if="databaseRecords.groups?.length" class="record-groups">
            <section v-for="group in databaseRecords.groups" :key="group.name" class="record-group">
              <div class="record-group-head"><div><b>{{ group.label }}</b><small class="record-group-range"><span>{{ group.name }}</span><span class="record-time-range"><time>{{ formatDateTime(group.min_time) }}</time><i>→</i><time>{{ formatDateTime(group.max_time) }}</time></span></small></div><strong>{{ group.total }} 条</strong></div>
              <div v-if="group.rows.length" class="record-table-wrap">
                <table class="record-table">
                  <thead><tr><th>ID</th><th>合约</th><th>Tick 时间</th><th>最新价</th><th>开盘</th><th>最高</th><th>最低</th><th>成交量</th><th>成交额</th><th>持仓</th><th>卖一</th><th>买一</th><th>品种</th><th>交易所</th></tr></thead>
                  <tbody><tr v-for="row in group.rows" :key="`${group.name}-${row.id}`"><td>{{ row.id }}</td><td>{{ row.code }}</td><td>{{ formatTickTime(row.tick_time) }}</td><td>{{ row.current_price }}</td><td>{{ row.open_price }}</td><td>{{ row.high_price }}</td><td>{{ row.low_price }}</td><td>{{ row.volume }}</td><td>{{ row.money }}</td><td>{{ row.position }}</td><td>{{ row.ask1_price }} / {{ row.ask1_volume }}</td><td>{{ row.bid1_price }} / {{ row.bid1_volume }}</td><td>{{ row.underlying_symbol }}</td><td>{{ row.exchange }}</td></tr></tbody>
                </table>
              </div>
              <div v-else class="empty-inline">当前筛选条件下没有记录</div>
            </section>
          </div>
          <div v-else class="empty-state tall"><b>没有匹配数据</b><p>请调整表、合约或时间条件后重新查询。</p></div>
          <div v-if="databaseRecords.page_count > 1" class="record-pagination">
            <div class="pagination-nav">
              <button class="pagination-button pagination-edge" :disabled="busy.query || databaseRecords.page <= 1" @click="goToDatabasePage(1)">首页</button>
              <button class="pagination-button" :disabled="busy.query || databaseRecords.page <= 1" @click="goToDatabasePage(databaseRecords.page - 1)">上一页</button>
              <template v-for="item in databasePaginationItems" :key="item.value">
                <span v-if="item.type === 'ellipsis'" class="pagination-ellipsis">…</span>
                <button v-else class="pagination-button page-number" :class="{ active: item.value === databaseRecords.page }" :disabled="busy.query" @click="goToDatabasePage(item.value)">{{ item.value }}</button>
              </template>
              <button class="pagination-button" :disabled="busy.query || databaseRecords.page >= databaseRecords.page_count" @click="goToDatabasePage(databaseRecords.page + 1)">下一页</button>
              <button class="pagination-button pagination-edge" :disabled="busy.query || databaseRecords.page >= databaseRecords.page_count" @click="goToDatabasePage(databaseRecords.page_count)">末页</button>
            </div>
            <div class="pagination-summary">共 {{ databaseRecords.page_count }} 页</div>
            <form class="pagination-jump" @submit.prevent="jumpToDatabasePage"><span>跳至</span><input v-model.number="pageJump" :max="databaseRecords.page_count" min="1" type="number" aria-label="跳转页码" /><span>页</span><button :disabled="busy.query" type="submit">跳转</button></form>
          </div>
        </article>
        <article v-else class="panel empty-state records-placeholder"><b>{{ busy.query ? '正在加载默认数据…' : '暂无查询结果' }}</b><p>进入本页面会自动读取第一个 Tick 表的最新记录；如果没有显示，请先确认数据库连接和数据表配置。</p></article>
      </section>

      <section v-else-if="section === 'database'" class="page-stack">
        <div class="database-layout">
          <article class="panel form-panel">
            <span class="section-kicker">本地 MySQL</span><h3>数据库连接</h3><p>配置只保存在本机 <code>backend/data/database.json</code>，不会使用原脚本中的线上地址。</p>
            <div class="form-grid"><label class="field"><span>主机地址</span><input v-model="database.host" /></label><label class="field"><span>端口</span><input v-model.number="database.port" type="number" /></label></div>
            <div class="form-grid"><label class="field"><span>用户名</span><input v-model="database.user" /></label><label class="field"><span>密码</span><input v-model="database.password" type="password" placeholder="本地 MySQL 密码" /></label></div>
            <label class="field"><span>数据库名</span><input v-model="database.database" /><small>测试连接时若数据库不存在，将在本地自动创建。</small></label>
            <label class="field"><span>字符集</span><select v-model="database.charset"><option value="utf8mb4">utf8mb4</option><option value="utf8">utf8</option></select></label>
            <div class="button-row"><button class="secondary-button" :disabled="busy.db" @click="testDatabase">测试连接</button><button class="primary-button" :disabled="busy.db" @click="saveDatabase">保存配置</button></div>
          </article>
          <article class="database-visual">
            <div class="db-core"><span>MYSQL</span><strong>{{ database.database }}</strong><small>{{ database.host }}:{{ database.port }}</small></div>
            <div class="orbit orbit-one"><i></i></div><div class="orbit orbit-two"><i></i></div>
            <div class="security-note"><b>本地优先</b><p>数据库密码与导入文件仅保存在当前电脑。</p></div>
          </article>
        </div>
      </section>

      <section v-else class="logs-layout">
        <aside class="task-sidebar panel">
          <div class="panel-head"><div><span class="section-kicker">{{ taskFilterLabel }}</span><h3>任务记录</h3></div><div class="task-filter-actions"><span class="tag subtle">{{ visibleLogTasks.length }}</span><button v-if="taskFilter !== 'all'" class="text-button" type="button" @click="taskFilter = 'all'">显示全部</button></div></div>
          <div class="task-list">
            <button v-for="task in visibleLogTasks" :key="task.id" class="task-row log-task" :class="{ selected: selectedTask?.id === task.id }" @click="selectTask(task)">
              <span class="task-type">{{ typeLabel(task.type).slice(0, 1) }}</span><span class="task-main"><b>{{ typeLabel(task.type) }}</b><small :title="taskSourceLabel(task)">{{ taskSourceLabel(task) }} · {{ task.created_at }}</small></span><span class="status-dot" :class="task.status"></span>
            </button>
            <div v-if="!visibleLogTasks.length" class="empty-inline">{{ taskFilter === 'all' ? '尚无任务记录' : `没有${taskFilterLabel}记录` }}</div>
          </div>
        </aside>
        <article class="terminal-panel">
          <div class="terminal-head"><div class="terminal-dots"><i></i><i></i><i></i></div><div class="terminal-title"><b :title="selectedTask ? `${typeLabel(selectedTask.type)} · ${taskSourceLabel(selectedTask)}` : '实时运行日志'">{{ selectedTask ? `${typeLabel(selectedTask.type)} · ${taskSourceLabel(selectedTask)}` : '实时运行日志' }}</b><small :title="selectedTask?.message">{{ selectedTask?.message || '选择左侧任务查看事件流' }}</small></div><span v-if="selectedTask" class="status-badge" :class="selectedTask.status">{{ statusLabel(selectedTask.status) }}</span><button v-if="selectedTask?.type === 'extract' && ['pending', 'running'].includes(selectedTask.status)" class="task-control-button pause" type="button" title="处理完当前压缩包后暂停任务" aria-label="暂停任务" @click="setTaskPaused(selectedTask, true)"><i aria-hidden="true">Ⅱ</i><span>暂停任务</span></button><button v-else-if="selectedTask?.type === 'extract' && selectedTask.status === 'paused'" class="task-control-button resume" type="button" title="从下一个未完成的压缩包继续" aria-label="继续任务" @click="setTaskPaused(selectedTask, false)"><i aria-hidden="true">▶</i><span>继续任务</span></button></div>
          <div v-if="selectedTask" class="terminal-progress"><i :style="{ width: `${selectedTask.progress}%` }"></i><span>{{ selectedTask.progress }}%</span></div>
          <div class="terminal-history-controls">
            <button class="history-button older" type="button" :disabled="!hasOlderEvents || eventHistoryBusy" :title="hasOlderEvents ? '查看更早的任务事件' : '没有更早的任务事件'" @click="loadOlderEvents"><span aria-hidden="true">↑</span>{{ eventHistoryBusy ? '读取中…' : hasOlderEvents ? '加载更早日志' : '没有更早日志' }}</button>
            <span class="history-position">显示 {{ taskEvents.length }} 条 / 共 {{ selectedTask?.last_event_id || 0 }} 条</span>
            <button class="history-button latest" type="button" :disabled="eventHistoryBusy" title="重新读取任务最新日志" @click="loadNewerEvents"><span aria-hidden="true">↻</span>{{ eventHistoryBusy ? '读取中…' : '刷新最新日志' }}</button>
          </div>
          <div ref="terminalBody" class="terminal-body" @scroll="handleTerminalScroll">
            <div v-for="event in taskEvents" :key="event.id" class="terminal-line" :class="eventTone(event.level)"><time>{{ event.timestamp?.slice(11) }}</time><span>{{ eventTypeLabel(event.type) }}</span><p>{{ event.message }}</p></div>
            <div v-if="!taskEvents.length" class="terminal-placeholder"><span>⌁</span><p>等待任务事件...</p></div>
          </div>
          <div class="terminal-foot"><span><i :class="sseStatus"></i>{{ sseStatus === 'connected' ? '实时接收中' : sseStatus === 'connecting' ? '正在连接实时日志' : sseStatus === 'reconnecting' ? '连接中断，正在自动重连' : '实时日志未连接' }}</span><b>显示 {{ taskEvents.length }} / 共 {{ selectedTask?.last_event_id || 0 }} 条</b></div>
        </article>
      </section>
    </main>

    <Transition name="modal">
      <div v-if="cleanupConfirmOpen" class="modal-backdrop" @click.self="cancelClearHistory">
        <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="cleanup-title">
          <div class="confirm-modal-head"><span class="confirm-warning-icon">⚠</span><div><span class="section-kicker">危险操作确认</span><h2 id="cleanup-title">清理项目文件与任务日志</h2></div></div>
          <p class="confirm-lead">此操作不可撤销，请确认后继续。</p>
          <div class="confirm-warning-box"><b>将会删除</b><p>项目生成的合并日志与异常数据文件、任务临时文件、文件索引、历史任务，以及运行日志和事件记录。</p></div>
          <div class="confirm-safe-box"><b>不会删除</b><p><code>D:\JoinQuant\jq_log_data</code> 原始 ZIP 和 MySQL 中已经导入的数据。</p></div>
          <div class="confirm-modal-actions"><button class="secondary-button" :disabled="busy.cleanup" @click="cancelClearHistory">取消</button><button class="danger-button" :disabled="busy.cleanup" @click="clearHistory">{{ busy.cleanup ? '正在清理…' : '确定清理' }}</button></div>
        </section>
      </div>
    </Transition>
    <Transition name="notice">
      <div v-if="notice" class="notice" :class="notice.tone"><span>{{ notice.tone === 'error' ? '!' : '✓' }}</span>{{ notice.message }}</div>
    </Transition>
  </div>
</template>

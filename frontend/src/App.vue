<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useConsole } from './useConsole'

const consoleState = useConsole()
const {
  section, serverOnline, fileStats, tasks, zipFiles, dataFiles, selectedZipIds,
  localDirectory, localRootDirectory, localBrowserPath, localBrowserRootPath, localBrowserDirectories, localBrowserOpen, localDirectoryMenuOpen, mergeScope, localArchives, monthDirectories, selectedMonthDirectories, scanMessage, selectedDataFileId, outputNamePrefix, outputNameSuffix, targetProduct, tableName, databaseTables, databaseTablesError, selectedFileProduct, matchingTable, importTargetInvalid, targetProductLocked, tableNameLocked, tableOptions, adjustDuplicates, selectedTask,
  taskEvents, runningTasks, successfulTasks, failedTasks, latestValidation,
  latestImport, database, busy, notice, databaseRecords, databaseQuery, cleanupConfirmOpen, requestClearHistory, cancelClearHistory, clearHistory, scanLocalArchives, openLocalDirectory, reopenLocalDirectory, browseLocalDirectory, browseLocalDirectoryUp, toggleLocalDirectoryMenu, useLocalBrowserDirectory, closeLocalDirectoryBrowser, toggleMonthDirectory, setOutputNameManually, toggleZip, startExtract,
  startValidate, startImport, testDatabase, saveDatabase, selectTask, queryDatabaseRecords, clearDatabaseQuery,
} = consoleState

const directoryName = (directory) => directory.split(/[\\/]/).filter(Boolean).at(-1) || directory
const terminalBody = ref(null)
const recordsPanel = ref(null)
const pageJump = ref(1)
const calendarPicker = ref(null)
const calendarOpen = ref(false)
const calendarField = ref('start_time')
const calendarCursor = ref(new Date(new Date().getFullYear(), new Date().getMonth(), 1))
let followTerminalLogs = true

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

watch(taskEvents, () => scrollTerminalToBottom(), { deep: true })
watch(() => selectedTask.value?.id, () => {
  followTerminalLogs = true
  scrollTerminalToBottom(true)
})

const navigation = [
  { id: 'dashboard', icon: '◈', label: '工作台', caption: '全局运行概览' },
  { id: 'extract', icon: '◇', label: '压缩处理', caption: 'ZIP 解压与合并' },
  { id: 'validate', icon: '✓', label: '质量检查', caption: '定位异常数据' },
  { id: 'import', icon: '⇣', label: '数据导入', caption: '写入本地 MySQL' },
  { id: 'records', icon: '≋', label: '数据查询', caption: '按表查看入库数据' },
  { id: 'database', icon: '▣', label: '数据库配置', caption: '本地连接参数' },
  { id: 'logs', icon: '⌁', label: '运行日志', caption: 'SSE 实时事件' },
]

const page = computed(() => navigation.find((item) => item.id === section.value) || navigation[0])
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
onBeforeUnmount(() => document.removeEventListener('pointerdown', handleCalendarOutside))

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

function typeLabel(type) {
  return { extract: '压缩处理', validate: '质量检查', import: '数据导入' }[type] || type
}

function statusLabel(status) {
  return { pending: '等待中', running: '运行中', success: '已完成', failed: '失败' }[status] || status
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
          <div class="live-pill"><i></i>SSE 实时通道</div>
          <button class="ghost-button" @click="section = 'logs'">查看任务 {{ tasks.length }}</button>
        </div>
      </header>

      <section v-if="section === 'dashboard'" class="page-stack">
        <div class="hero-card">
          <div class="hero-copy">
            <span class="tag">本地数据自动化</span>
            <h2>数据处理流程，<br><em>一站式完成。</em></h2>
            <p>从 ZIP 解压、日志合并、质量检查到 MySQL 入库，全程可视、可追踪、可回放。</p>
            <div class="hero-actions">
              <button class="primary-button" @click="section = 'extract'">开始处理文件</button>
              <button class="secondary-button" @click="section = 'database'">配置本地数据库</button>
            </div>
          </div>
          <div class="pipeline-visual">
            <div class="pipeline-node"><span>01</span><b>ZIP</b><small>安全解压</small></div>
            <i>→</i>
            <div class="pipeline-node accent"><span>02</span><b>LOG</b><small>解析校验</small></div>
            <i>→</i>
            <div class="pipeline-node"><span>03</span><b>SQL</b><small>本地入库</small></div>
          </div>
        </div>

        <div class="metric-grid">
          <article class="metric-card"><span class="metric-icon cyan">↻</span><div><small>运行中任务</small><strong>{{ runningTasks.length }}</strong><p>后台持续执行</p></div></article>
          <article class="metric-card"><span class="metric-icon green">✓</span><div><small>已完成任务</small><strong>{{ successfulTasks.length }}</strong><p>结果已持久化</p></div></article>
          <article class="metric-card"><span class="metric-icon orange">!</span><div><small>异常任务</small><strong>{{ failedTasks.length }}</strong><p>可查看完整日志</p></div></article>
          <article class="metric-card clickable" role="button" tabindex="0" @click="section = 'validate'" @keydown.enter="section = 'validate'"><span class="metric-icon violet">▤</span><div><small>项目托管文件</small><strong>{{ fileStats.available }}</strong><p>{{ fileStats.logs }} 个生成日志 · 点击查看</p></div></article>
        </div>

        <article class="panel dashboard-explain">
          <div class="dashboard-explain-icon">i</div>
          <div><b>这里统计的是项目托管文件，不是数据库数据条数</b><p>当前托管文件 {{ fileStats.available }} 个，索引登记 {{ fileStats.indexed }} 条，缺失 {{ fileStats.missing }} 条，包含正常日志和 rejected 日志。解压临时目录不会计入；导入 MySQL 后的 Tick 行数，请进入“数据查询”查看。</p></div>
          <div class="dashboard-explain-actions"><button class="text-button" @click="section = 'records'">查看数据库数据 →</button><div class="cleanup-action"><button class="danger-button" :disabled="busy.cleanup" @click="requestClearHistory">{{ busy.cleanup ? '正在清理…' : '清理项目文件与任务日志' }}</button><small>⚠️ 会删除历史任务和运行日志，不影响 MySQL 数据</small></div></div>
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
            <div v-else class="empty-state"><b>还没有任务</b><p>扫描本地 ZIP 开始第一项作业。</p></div>
          </article>

          <article class="panel system-panel">
            <div class="panel-head"><div><span class="section-kicker">系统状态</span><h3>流程状态</h3></div><span class="pulse-label">实时</span></div>
            <div class="system-line"><span><i class="cyan-dot"></i>FastAPI 后台任务</span><b>{{ serverOnline ? '在线' : '离线' }}</b></div>
            <div class="system-line"><span><i class="green-dot"></i>SSE 事件推送</span><b>已启用</b></div>
            <div class="system-line"><span><i class="violet-dot"></i>本地 MySQL</span><b>{{ database.host }}:{{ database.port }}</b></div>
            <div class="system-line"><span><i class="orange-dot"></i>时间戳去重</span><b>{{ adjustDuplicates ? '已启用' : '已关闭' }}</b></div>
          </article>
        </div>
      </section>

      <section v-else-if="section === 'extract'" class="page-stack">
        <div class="two-column wide-left">
          <article class="panel">
            <div class="panel-head"><div><span class="section-kicker">本地目录处理</span><h3>读取文件夹中的压缩包</h3></div><span class="tag subtle">不上传文件</span></div>
            <div class="local-mode-note"><span class="local-mode-icon">⌂</span><div><b>文件始终留在本机</b><p>后端直接读取你填写的 Windows 文件夹，不会把 ZIP 上传到浏览器或复制到项目目录。</p></div></div>
            <div class="directory-setup-card">
              <div class="directory-setup-head"><div><span class="field-label">本地根目录配置</span><strong>配置一次，后续快速选择</strong></div><span class="directory-status">本机读取</span></div>
              <div class="directory-config"><input v-model="localRootDirectory" placeholder="例如 D:\JoinQuant\jq_log_data" @keyup.enter="reopenLocalDirectory()" /><button class="secondary-button" type="button" :disabled="busy.directory" @click="reopenLocalDirectory()">{{ busy.directory ? "读取中…" : (localBrowserLastPath ? "继续选择" : "打开目录") }}</button></div>
              <div class="directory-tip directory-tip-primary"><span class="directory-tip-icon">1</span><div><b>先填写数据根目录</b><p>例如 <code>D:\JoinQuant\jq_log_data</code>，点击“打开目录”后再选择品种、年份和月份。</p></div></div>
            </div>
            <div v-if="localBrowserOpen" class="directory-browser">
              <div class="directory-browser-head"><div><span class="field-label">目录选择</span><strong>按层级进入目标文件夹</strong></div><span class="directory-step">步骤 2 / 3</span></div>
              <div class="directory-current"><span>当前目录</span><code>{{ localBrowserPath }}</code></div>
              <div class="directory-picker">
                <button class="directory-picker-trigger" type="button" :disabled="busy.directory || !localBrowserDirectories.length" @click="toggleLocalDirectoryMenu"><span class="directory-folder-icon">▣</span><span>{{ localBrowserDirectories.length ? "选择下级文件夹" : "当前目录没有下级文件夹" }}</span><i :class="{ rotated: localDirectoryMenuOpen }">⌄</i></button>
                <div v-if="localDirectoryMenuOpen" class="directory-picker-menu"><button v-for="directory in localBrowserDirectories" :key="directory.path" class="directory-option" type="button" @click="browseLocalDirectory(directory.path)"><span class="directory-option-icon">▣</span><span class="directory-option-copy"><b>{{ directory.name }}</b><small>进入此文件夹</small></span><strong>›</strong></button></div>
              </div>
              <div class="directory-browser-actions"><button class="ghost-button" type="button" :disabled="localBrowserPath === localBrowserRootPath" @click="browseLocalDirectoryUp">上一级</button><button class="ghost-button" type="button" @click="useLocalBrowserDirectory">使用当前目录</button><button class="ghost-button" type="button" @click="closeLocalDirectoryBrowser">关闭</button></div>
              <div class="directory-tip directory-tip-secondary"><span class="directory-tip-icon">3</span><div><b>选好后使用当前目录</b><p>系统会自动填入下方路径，然后点击“扫描目录中的 ZIP”。</p></div></div>
            </div>
            <label class="field directory-manual-field"><span>本地目录路径 <em>也可手动填写</em></span><input v-model="localDirectory" placeholder="例如 D:\JoinQuant\jq_log_data\A\2023\10月" /><small>支持月份目录或年份目录；手动填写与下拉选择效果相同。</small></label>
            <div class="scope-grid"><button class="scope-card" :class="{ active: mergeScope === 'month' }" @click="mergeScope = 'month'"><b>单月合并</b><small>只处理一个月份的 ZIP 文件</small><span>适合：选择到 `A\2023\10月` 这样的月份目录</span></button><button class="scope-card" :class="{ active: mergeScope === 'year' }" @click="mergeScope = 'year'"><b>按年合并</b><small>自动处理该年份下的全部月份</small><span>适合：选择到 `A\\2023` 这样的年份目录</span></button></div>
            <button class="secondary-button full" :disabled="busy.scan" @click="scanLocalArchives">{{ busy.scan ? '正在扫描本地目录…' : '扫描目录中的 ZIP' }}</button>
            <div v-if="monthDirectories.length" class="field month-picker-field"><span>选择需要补充的月份</span><div class="month-picker"><button v-for="directory in monthDirectories" :key="directory" type="button" class="month-option" :class="{ selected: selectedMonthDirectories.includes(directory) }" @click="toggleMonthDirectory(directory)"><span class="month-check">{{ selectedMonthDirectories.includes(directory) ? '✓' : '' }}</span>{{ directoryName(directory) }}</button></div><small>可多选月份，选中后显示 `✓`；然后点击“扫描目录中的 ZIP”，只合并选中的月份。</small><div class="month-selection-summary">已选择 {{ selectedMonthDirectories.length }} 个月</div></div>
            <div v-if="localArchives.length" class="scan-result"><div><b>已发现 {{ localArchives.length }} 个 ZIP 文件</b><small>{{ scanMessage || (mergeScope === 'year' ? '已扫描年度目录' : '已扫描当前月份目录') }}</small></div><span class="scan-check">✓</span></div>
            <div v-else-if="scanMessage" class="empty-inline local-empty">{{ scanMessage }}</div>
            <div v-else class="empty-inline local-empty">填写目录并点击扫描，系统会显示可处理的 ZIP 数量。</div>
          </article>

          <article class="panel action-panel">
            <span class="section-kicker">压缩处理流程</span>
            <h3>解压并合并日志</h3>
            <p>直接流式读取本地 ZIP，定位内部 <code>log.txt</code>，截取有效区间并生成合并日志，不会一次性加载大文件。</p>
            <label class="field"><span>输出文件名</span><div class="filename-editor"><span class="filename-prefix">{{ outputNamePrefix }}</span><input v-model="outputNameSuffix" placeholder="例如 2023_04.txt" /></div><small>商品标识由目标品种自动生成且不可修改；只能编辑后面的年份、月份或自定义说明。</small></label>
            <label class="field"><span>目标品种 <i class="field-hint">来自本地数据库</i></span><select v-model="targetProduct" :disabled="targetProductLocked"><option value="">请选择目标品种</option><option v-for="product in databaseTables" :key="product.code" :value="product.code">{{ product.label }}</option></select><small v-if="databaseTablesError" class="error-text">{{ databaseTablesError }}</small><small v-else-if="targetProductLocked" class="matched-text">✓ 已根据本地目录自动匹配 {{ targetProduct }}</small><small v-else-if="!databaseTables.length">请先配置数据库并确保存在 `tick_data_品种代码` 表。</small></label>
            <div class="filter-tip"><span>✓</span><p>合并时会读取每行的 <code>underlying_symbol</code>、<code>code</code> 和 <code>commodity_name</code>。不属于目标品种的数据会被移到 rejected 文件，不进入干净日志。</p></div>
            <div class="flow-steps"><span>解压文件</span><i>→</i><span>识别日志</span><i>→</i><span>截取数据</span><i>→</i><span>合并输出</span></div>
            <button class="primary-button full" :disabled="busy.extract || !localArchives.length" @click="startExtract">
              {{ busy.extract ? '正在创建任务…' : `开始${mergeScope === 'year' ? '年度' : '单月'}合并 ${localArchives.length} 个压缩包` }}
            </button>
          </article>
        </div>
      </section>

      <section v-else-if="section === 'validate'" class="page-stack">
        <article class="panel control-strip">
          <div><span class="section-kicker">入库前检查</span><h3>数据质量检查</h3><p>入库前检查解析错误、缺失字段、盘口异常和重复时间戳。</p></div>
          <div class="control-actions">
            <label class="field select-field"><span>日志文件</span><select v-model="selectedDataFileId"><option value="">请选择</option><option v-for="file in dataFiles" :key="file.id" :value="file.id">{{ file.name }}</option></select></label>
            <label class="switch-line"><input v-model="adjustDuplicates" type="checkbox" /><span></span>模拟时间戳去重</label>
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
              <div class="result-meta"><span>数据库</span><b>{{ latestImport.database }} / {{ latestImport.table }}</b><span>数据范围</span><b>{{ latestImport.database_stats?.min_time || '—' }} → {{ latestImport.database_stats?.max_time || '—' }}</b></div>
            </template>
            <div v-else class="empty-state tall"><b>暂无导入记录</b><p>导入完成后显示新增、重复和失败统计。</p></div>
          </article>
        </div>
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
              <div class="record-group-head"><div><b>{{ group.label }}</b><small>{{ group.name }} · {{ group.min_time || '暂无时间范围' }} → {{ group.max_time || '暂无时间范围' }}</small></div><strong>{{ group.total }} 条</strong></div>
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
          <div class="panel-head"><div><span class="section-kicker">历史任务</span><h3>任务记录</h3></div><span class="tag subtle">{{ tasks.length }}</span></div>
          <div class="task-list">
            <button v-for="task in tasks" :key="task.id" class="task-row log-task" :class="{ selected: selectedTask?.id === task.id }" @click="selectTask(task)">
              <span class="task-type">{{ typeLabel(task.type).slice(0, 1) }}</span><span class="task-main"><b>{{ typeLabel(task.type) }}</b><small>{{ task.created_at }}</small></span><span class="status-dot" :class="task.status"></span>
            </button>
            <div v-if="!tasks.length" class="empty-inline">尚无任务记录</div>
          </div>
        </aside>
        <article class="terminal-panel">
          <div class="terminal-head"><div class="terminal-dots"><i></i><i></i><i></i></div><div><b>{{ selectedTask ? `${typeLabel(selectedTask.type)} · ${selectedTask.id.slice(0, 8)}` : '实时运行日志' }}</b><small>{{ selectedTask?.message || '选择左侧任务查看事件流' }}</small></div><span v-if="selectedTask" class="status-badge" :class="selectedTask.status">{{ statusLabel(selectedTask.status) }}</span></div>
          <div v-if="selectedTask" class="terminal-progress"><i :style="{ width: `${selectedTask.progress}%` }"></i><span>{{ selectedTask.progress }}%</span></div>
          <div ref="terminalBody" class="terminal-body" @scroll="handleTerminalScroll">
            <div v-for="event in taskEvents" :key="event.id" class="terminal-line" :class="eventTone(event.level)"><time>{{ event.timestamp?.slice(11) }}</time><span>{{ event.type.toUpperCase().padEnd(8) }}</span><p>{{ event.message }}</p></div>
            <div v-if="!taskEvents.length" class="terminal-placeholder"><span>⌁</span><p>等待任务事件...</p></div>
          </div>
          <div class="terminal-foot"><span><i></i>{{ selectedTask && ['pending', 'running'].includes(selectedTask.status) ? 'SSE 已连接，实时接收中' : '事件已持久化，可随时回放' }}</span><b>{{ taskEvents.length }} EVENTS</b></div>
        </article>
      </section>
    </main>

    <Transition name="modal">
      <div v-if="cleanupConfirmOpen" class="modal-backdrop" @click.self="cancelClearHistory">
        <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="cleanup-title">
          <div class="confirm-modal-head"><span class="confirm-warning-icon">⚠</span><div><span class="section-kicker">危险操作确认</span><h2 id="cleanup-title">清理项目文件与任务日志</h2></div></div>
          <p class="confirm-lead">此操作不可撤销，请确认后继续。</p>
          <div class="confirm-warning-box"><b>⚠️ 将会删除</b><p>项目托管文件、生成的 TXT/rejected 文件、解压临时目录、文件索引、历史任务，以及运行日志和事件记录。</p></div>
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

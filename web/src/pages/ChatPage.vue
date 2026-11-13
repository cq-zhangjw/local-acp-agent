<script setup lang="ts">
import { computed, defineAsyncComponent, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import { cleanSpeechText, getTtsConfig, playTtsStream, stopTtsStream } from '../api/ttsStream'
import {
  AddOutline,
  ArrowDownOutline,
  AttachOutline,
  BookOutline,
  ChatbubbleOutline,
  ChevronBackOutline,
  ChevronDownOutline,
  ChevronForwardOutline,
  CodeSlashOutline,
  ConstructOutline,
  CopyOutline,
  CreateOutline,
  FlashOutline,
  DesktopOutline,  DocumentOutline,
  FolderOutline,
  EllipsisHorizontalOutline,
  LanguageOutline,
  CallOutline,
  MenuOutline,
  MicOutline,
  PaperPlaneOutline,
  RefreshOutline,
  SearchOutline,
  SettingsOutline,
  SparklesOutline,
  ReaderOutline,
  GlobeOutline,
  PersonCircleOutline,
  StopOutline,
  StopCircleOutline,
  TrashOutline,
  TimeOutline,
  HelpCircleOutline,
  VolumeHighOutline,
  VolumeMuteOutline,
  SunnyOutline,
  MoonOutline
} from '@vicons/ionicons5'
import {
  NAlert,
  NAvatar,
  NButton,
  NCard,
  NCheckbox,
  NDrawer,
  NDrawerContent,
  NDropdown,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  NTabs,
  NTabPane,
  NTooltip,
  NPopover,
  NCollapse,
  NCollapseItem,
  useMessage
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { storageKey, type SupportedLocale } from '../i18n'
import { useTheme, type ThemePreference } from '../theme'
import ExecutionProcess from '../components/ExecutionProcess.vue'
import KnowledgeManageModal from '../components/KnowledgeManageModal.vue'
import ManagementCenter from '../components/management/ManagementCenter.vue'
import MarkdownMessage from '../components/MarkdownMessage.vue'
import ToolCallCard from '../components/ToolCallCard.vue'
import ThoughtCard from '../components/ThoughtCard.vue'
import QuickCommandBar from '../components/QuickCommandBar.vue'
import { fetchKnowledgeConfig, fetchKnowledgeDatabases, startKnowledgeService, stopKnowledgeService, type KnowledgeDb } from '../api/knowledge'

const StatisticsModal = defineAsyncComponent(() => import('../components/StatisticsModal.vue'))

type TaskStatus = 'pending' | 'streaming' | 'completed' | 'cancelled' | 'failed' | 'waiting_permission'
type ToolStatus = 'running' | 'completed' | 'failed' | 'cancelled' | 'waiting_permission'

interface QuickCommand {
  id: string
  title: string
  content: string
  agent: string
  model: string
  mode?: string
  autoSend: boolean
  role?: string
  webSearchOn?: boolean
  historyOn?: boolean
  jpMarkOn?: boolean
  showOnHome?: boolean
}

interface AttachmentRef {
  path?: string
  uri?: string
  name: string
  mimeType?: string
  kind?: 'file' | 'image'
  data?: string
  previewUrl?: string
  // MooFile attachment-store refs (filled after upload / present on replay)
  file_id?: string
  url?: string
  // In-memory only: the raw File queued for upload on send (never persisted)
  file?: File
}

interface AnalysisEntry {
  id: string
  text: string
}

interface PlanEntry {
  title: string
  status: string
}

interface ToolCallEntry {
  id: string
  name: string
  title?: string
  status: ToolStatus
  rawInput?: unknown
  output?: unknown
}

interface ExecutionProcess {
  startedAt: number
  completedAt?: number
  plan: PlanEntry[]
  analyses: AnalysisEntry[]
  toolCalls: ToolCallEntry[]
}

interface TextSegment { id: string; type: 'text'; text: string; html?: string }
interface ThoughtSegment { id: string; type: 'thought'; text: string; done: boolean }
interface PlanSegment { id: string; type: 'plan' }
interface ToolSegment { id: string; type: 'tool'; toolId: string }
type AssistantSegment = TextSegment | ThoughtSegment | PlanSegment | ToolSegment

interface UserMessage {
  id: string
  role: 'user'
  text: string
  attachments: AttachmentRef[]
  createdAt: number
}

interface AssistantMessage {
  id: string
  role: 'assistant'
  finalText: string
  status: TaskStatus
  process: ExecutionProcess
  segments: AssistantSegment[]
  createdAt: number
}

type ChatMessage = UserMessage | AssistantMessage

interface Conversation {
  id: string
  agentSessionId?: string
  title: string
  updatedAt: number
  messages: ChatMessage[]
  todoId?: string
  /** Legacy field retained when loading old history. Agent selection is authoritative. */
  mode?: 'chat' | 'task'
  /** Agent selected for this conversation. */
  agent?: string
  /** Model chosen for this conversation (name in model_config.json); passed to the backend via WS query param like agent. */
  model?: string
  /** Per-session chat toggles. */
  webSearchOn?: boolean
  historyOn?: boolean
  /** Per-session active role preset name. */
  roleName?: string
  /** Per-session jp auto-mark toggle. */
  jpMarkOn?: boolean
}

interface TodoItem {
  index: number
  title: string
  description?: string | null
  status: 'pending' | 'in_progress' | 'completed' | 'blocked'
  note?: string | null
  updatedAt?: number
}

interface PermissionRequest {
  id: number | string
  toolName: string
  rawInput: unknown
}

// Legacy localStorage key, kept only for a one-time migration into MooFile.
const STORE_KEY = 'deepagents-acp-ui-sessions-v1'
const message = useMessage()
const { t, locale } = useI18n()
const { preference: themePreference, resolved: resolvedTheme, setPreference: setThemePreference } = useTheme()
const markdown = new MarkdownIt()
const ws = ref<WebSocket | null>(null)
const connected = ref(false)
const initialized = ref(false)
const socketPromise = ref<Promise<void> | null>(null)
const initializedSessions = new Set<string>()
const restoringHistory = ref(false)
const requestId = ref(1)
const pendingRequests = new Map<number, { resolve: (value: any) => void; reject: (reason: Error) => void }>()
const conversations = ref<Conversation[]>([])
const activeConversationId = ref('')

/** The currently selected conversation (may be undefined while loading). */
const activeConversation = computed(() =>
  conversations.value.find((c) => c.id === activeConversationId.value)
)

const noActiveConversation = computed(() => !activeConversationId.value)
const conversationSearch = ref('')
const agentCollapsed = ref(false)
const timerCollapsed = ref(false)
const filteredConversations = computed(() => {
  const keyword = conversationSearch.value.trim().toLowerCase()
  if (!keyword) return conversations.value
  return conversations.value.filter((conversation) =>
    conversation.title.toLowerCase().includes(keyword)
  )
})
// 侧栏分组：普通会话与定时任务会话。
const timerConversations = computed(() =>
  filteredConversations.value.filter((c) => String(c.id).startsWith('timer-'))
)
const normalConversations = computed(() =>
  filteredConversations.value.filter((c) => !String(c.id).startsWith('timer-'))
)
// todo 分步清单：key = conversation.id（由 ACP toolCalls 事件重放构建）
const todoStates = ref<Record<string, TodoItem[]>>({})
/** 会话对应的定时任务（无绑定返回 undefined）。 */
function timerTaskOf(cid: string) {
  return timers.value.find((t) => t.sessionId === cid)
}
const input = ref('')
const attachments = ref<AttachmentRef[]>([])
const previewImage = ref<string | null>(null)
const isUploading = ref(false)
const dragActive = ref(false)
const permissionRequest = ref<PermissionRequest | null>(null)
const sidebarVisible = ref(false)
const errorText = ref('')
const timeline = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const localeOptions = computed(() => [
  { label: '中文', key: 'zh' },
  { label: '日本語', key: 'ja' },
  { label: 'English', key: 'en' }
])
// 语言切换触发区只显示简写（中 / 日 / EN），菜单仍显示完整语言名
const localeShort = computed(() =>
  locale.value === 'zh' ? '中' : locale.value === 'ja' ? '日' : 'EN'
)
function onLocaleSelect(key: string | number) {
  locale.value = key as SupportedLocale
}
const themeOptions = computed(() => [
  { label: t('themeLight'), key: 'light', icon: () => h(NIcon, { component: SunnyOutline }) },
  { label: t('themeDark'), key: 'dark', icon: () => h(NIcon, { component: MoonOutline }) },
  { label: t('themeSystem'), key: 'system', icon: () => h(NIcon, { component: DesktopOutline }) },
])
function onThemeSelect(key: string | number) {
  setThemePreference(key as ThemePreference)
}
const actionLabels = computed(() => ({
  retry: locale.value === 'zh' ? '重新生成' : locale.value === 'ja' ? '再生成' : 'Regenerate',
  copyText: locale.value === 'zh' ? '复制纯文本' : locale.value === 'ja' ? 'テキストをコピー' : 'Copy text',
  copyMarkdown: locale.value === 'zh' ? '复制 Markdown' : locale.value === 'ja' ? 'Markdown をコピー' : 'Copy Markdown',
  speak: locale.value === 'zh' ? '朗读' : locale.value === 'ja' ? '読み上げ' : 'Read aloud',
  stopSpeak: locale.value === 'zh' ? '停止朗读' : locale.value === 'ja' ? '読み上げ停止' : 'Stop reading',
  ttsFailed: locale.value === 'zh' ? '语音合成失败，请检查网络或代理' : locale.value === 'ja' ? '音声合成に失敗しました。ネットワークを確認してください' : 'Speech synthesis failed, check network or proxy',
  startSpeechInput: locale.value === 'zh' ? '开始语音输入' : locale.value === 'ja' ? '音声入力を開始' : 'Start voice input',
  stopSpeechInput: locale.value === 'zh' ? '停止语音输入' : locale.value === 'ja' ? '音声入力を停止' : 'Stop voice input',
  speechUnsupported: locale.value === 'zh' ? '当前浏览器不支持语音识别' : locale.value === 'ja' ? 'このブラウザは音声認識をサポートしていません' : 'Speech recognition is not supported in this browser',
  speechDenied: locale.value === 'zh' ? '麦克风权限被拒绝，请检查浏览器设置' : locale.value === 'ja' ? 'マイク権限が拒否されました。ブラウザ設定を確認してください' : 'Microphone access denied, please check browser settings',
  selectVoice: locale.value === 'zh' ? '音色' : locale.value === 'ja' ? '音声' : 'Voice',
  speakReply: locale.value === 'zh' ? 'AI 回复完成后自动朗读' : locale.value === 'ja' ? 'AI応答を自動読み上げ' : 'Read AI replies aloud',
  openVoiceCall: locale.value === 'zh' ? '语音通话' : locale.value === 'ja' ? '音声通話' : 'Voice call',
  theme: t('themeCurrent', { theme: t(`theme${themePreference.value.charAt(0).toUpperCase()}${themePreference.value.slice(1)}`) }),
  copied: locale.value === 'zh' ? '已复制' : locale.value === 'ja' ? 'コピーしました' : 'Copied',
  more: locale.value === 'zh' ? '更多操作' : locale.value === 'ja' ? 'その他の操作' : 'More actions',
  editMessage: locale.value === 'zh' ? '编辑消息' : locale.value === 'ja' ? 'メッセージを編集' : 'Edit message',
  editTitle: locale.value === 'zh' ? '编辑消息' : locale.value === 'ja' ? 'メッセージを編集' : 'Edit message',
  save: locale.value === 'zh' ? '保存' : locale.value === 'ja' ? '保存' : 'Save',
  cancel: locale.value === 'zh' ? '取消' : locale.value === 'ja' ? 'キャンセル' : 'Cancel',
  preview: locale.value === 'zh' ? '预览' : locale.value === 'ja' ? 'プレビュー' : 'Preview',
  deleteMessage: locale.value === 'zh' ? '删除该消息' : locale.value === 'ja' ? 'このメッセージを削除' : 'Delete message',
  addToQuickCommand: locale.value === 'zh' ? '添加至快捷指令' : locale.value === 'ja' ? 'クイックコマンドに追加' : 'Add to quick command',
  editLocalOnly: locale.value === 'zh' ? '已更新展示内容（未找到上下文记录，真实上下文未变）' : locale.value === 'ja' ? '表示内容を更新しました（コンテキスト記録が見つからず、実際のコンテキストは変更されていません）' : 'Display updated (no context record found; real context unchanged)',
  deleteLocalOnly: locale.value === 'zh' ? '已删除展示消息（未找到上下文记录，真实上下文未变）' : locale.value === 'ja' ? '表示メッセージを削除しました（コンテキスト記録が見つからず、実際のコンテキストは変更されていません）' : 'Display message deleted (no context record found; real context unchanged)',
  skills: locale.value === 'zh' ? '技能' : locale.value === 'ja' ? 'スキル' : 'Skills',
  tools: locale.value === 'zh' ? '工具' : locale.value === 'ja' ? 'ツール' : 'Tools',
  knowledge: locale.value === 'zh' ? '知识库' : locale.value === 'ja' ? 'ナレッジベース' : 'Knowledge',
  knowledgeManage: locale.value === 'zh' ? '管理' : locale.value === 'ja' ? '管理' : 'Manage',
  knowledgeLoading: locale.value === 'zh' ? '正在加载管理页面...' : locale.value === 'ja' ? '管理ページを読み込み中...' : 'Loading management page...',
  knowledgeUnreachable: locale.value === 'zh' ? '知识库服务未启动' : locale.value === 'ja' ? 'ナレッジベースサービスが起動していません' : 'Knowledge base service is unreachable',
  knowledgeStart: locale.value === 'zh' ? '立即启动' : locale.value === 'ja' ? '今すぐ起動' : 'Start now',
  knowledgeStarting: locale.value === 'zh' ? '启动中...' : locale.value === 'ja' ? '起動中...' : 'Starting...',
  knowledgeStop: locale.value === 'zh' ? '停止服务' : locale.value === 'ja' ? 'サービス停止' : 'Stop service',
  knowledgeStopping: locale.value === 'zh' ? '停止中...' : locale.value === 'ja' ? '停止中...' : 'Stopping...',
  knowledgeDocs: locale.value === 'zh' ? '文档' : locale.value === 'ja' ? '文書' : 'Docs',
  knowledgeRecords: locale.value === 'zh' ? '记录' : locale.value === 'ja' ? 'レコード' : 'Records',
  knowledgeVector: locale.value === 'zh' ? '向量' : locale.value === 'ja' ? 'ベクトル' : 'Vector',
  knowledgeNormal: locale.value === 'zh' ? '普通' : locale.value === 'ja' ? '通常' : 'Normal',
  chatMode: locale.value === 'zh' ? 'Chat 聊天' : locale.value === 'ja' ? 'チャット' : 'Chat',
  historyOn: locale.value === 'zh' ? '历史上下文' : locale.value === 'ja' ? '履歴コンテキスト' : 'History context',
  webSearch: locale.value === 'zh' ? '联网搜索' : locale.value === 'ja' ? 'ウェブ検索' : 'Web search',
  processSteps: (n: number) => locale.value === 'zh' ? `${n} 步推理过程（点击展开）` : locale.value === 'ja' ? `${n} ステップの推論（クリックで展開）` : `${n} reasoning steps (click to expand)`,
  historyOff: locale.value === 'zh' ? '单轮问答' : locale.value === 'ja' ? '単発Q&A' : 'Single-turn',
  taskMode: locale.value === 'zh' ? 'Agent 任务' : locale.value === 'ja' ? 'エージェントタスク' : 'Agent task',
  chatModeDesc: locale.value === 'zh' ? '轻量聊天，不加载工具与技能' : locale.value === 'ja' ? 'ツールやスキルなしの軽量チャット' : 'Lightweight chat without tools or skills',
  taskModeDesc: locale.value === 'zh' ? '完整 Agent 工作区，可调用工具与技能' : locale.value === 'ja' ? 'ツールとスキルを使える本格エージェント' : 'Full agent workspace with tools and skills',
  chatModeFeats: (locale.value === 'zh'
    ? ['豆包风格人设，耐心且简洁', '无工具 / 技能 / 子 Agent', '适合日常闲聊与问答']
    : locale.value === 'ja'
      ? ['豆包風ペルソナ、忍耐強く簡潔', 'ツール / スキル / サブエージェントなし', '日常の雑談や Q&A に最適']
      : ['Doubao-style persona, patient and concise', 'No tools / skills / subagents', 'Best for casual chat & Q&A']),
  taskModeFeats: (locale.value === 'zh'
    ? ['默认 Default Agent，可自由切换', '可调用工具 / 技能 / 知识库', '适合复杂任务与文件操作']
    : locale.value === 'ja'
      ? ['デフォルトは Default Agent、切り替え自由', 'ツール / スキル / ナレッジベース利用可', '複雑なタスクやファイル操作に最適']
      : ['Default agent by default, switchable', 'Tools / skills / knowledge base available', 'Best for complex tasks & files']),
  builtinTools: locale.value === 'zh' ? '内置工具' : locale.value === 'ja' ? '内蔵ツール' : 'Built-in tools',
  context: locale.value === 'zh' ? '上下文' : locale.value === 'ja' ? 'コンテキスト' : 'Context',
  contextTooltip: locale.value === 'zh' ? '当前会话上下文用量（本地估算）' : locale.value === 'ja' ? '現在のセッションのコンテキスト使用量（ローカル推定）' : 'Current session context usage (local estimate)',
  compactContext: locale.value === 'zh' ? '压缩上下文' : locale.value === 'ja' ? 'コンテキストを圧縮' : 'Compact context',
  viewContext: locale.value === 'zh' ? '查看上下文' : locale.value === 'ja' ? 'コンテキストを表示' : 'View context',
  thinking: locale.value === 'zh' ? '思考中' : locale.value === 'ja' ? '考え中' : 'Thinking',
  thoughtDone: locale.value === 'zh' ? '已思考' : locale.value === 'ja' ? '思考完了' : 'Thought',
  compacting: locale.value === 'zh' ? '压缩中...' : locale.value === 'ja' ? '圧縮中...' : 'Compacting...',
  compactDone: locale.value === 'zh' ? '上下文已压缩' : locale.value === 'ja' ? 'コンテキストを圧縮しました' : 'Context compacted',
  compactFailed: locale.value === 'zh' ? '压缩失败' : locale.value === 'ja' ? '圧縮失敗' : 'Compaction failed',
  comingSoon: locale.value === 'zh' ? '该功能即将支持' : locale.value === 'ja' ? 'この機能はまもなく対応予定です' : 'Coming soon',
  renameConversation: locale.value === 'zh' ? '重命名' : locale.value === 'ja' ? '名前を変更' : 'Rename',
  renameTitle: locale.value === 'zh' ? '重命名会话' : locale.value === 'ja' ? 'セッション名を変更' : 'Rename conversation',
  renamePlaceholder: locale.value === 'zh' ? '输入新名称' : locale.value === 'ja' ? '新しい名前を入力' : 'Enter a new name',
  settings: locale.value === 'zh' ? '设置' : locale.value === 'ja' ? '設定' : 'Settings',
  collapseSidebar: locale.value === 'zh' ? '收起侧边栏' : locale.value === 'ja' ? 'サイドバーを畳む' : 'Collapse sidebar',
  expandSidebar: locale.value === 'zh' ? '展开侧边栏' : locale.value === 'ja' ? 'サイドバーを展開' : 'Expand sidebar',
  panelComingSoon: locale.value === 'zh' ? '该模块即将支持，敬请期待' : locale.value === 'ja' ? 'このモジュールはまもなく対応予定です' : 'This module is coming soon',
  language: locale.value === 'zh' ? '语言' : locale.value === 'ja' ? '言語' : 'Language',
  closePanel: locale.value === 'zh' ? '关闭面板' : locale.value === 'ja' ? 'パネルを閉じる' : 'Close panel',
  scrollToBottom: locale.value === 'zh' ? '滚动到底部' : locale.value === 'ja' ? '一番下へスクロール' : 'Scroll to bottom',
  previewAttachment: locale.value === 'zh' ? '预览附件' : locale.value === 'ja' ? '添付をプレビュー' : 'Preview attachment',
  downloadAttachment: locale.value === 'zh' ? '下载附件' : locale.value === 'ja' ? '添付をダウンロード' : 'Download attachment',
  screenShare: locale.value === 'zh' ? '共享屏幕' : locale.value === 'ja' ? '画面共有' : 'Share screen',
  screenShareActive: locale.value === 'zh' ? '共享屏幕已开启，发送消息时自动截屏' : locale.value === 'ja' ? '画面共有をオン、送信時に自動でスクリーンショット' : 'Screen share on, screenshots on send',
  agent: locale.value === 'zh' ? 'Agent' : locale.value === 'ja' ? 'エージェント' : 'Agent',
  selectAgent: locale.value === 'zh' ? '选择 Agent' : locale.value === 'ja' ? 'エージェントを選択' : 'Select agent',
  skillsPanel: locale.value === 'zh' ? '打开技能面板' : locale.value === 'ja' ? 'スキルパネルを開く' : 'Open skills panel',
  agents: locale.value === 'zh' ? 'Agent 管理' : locale.value === 'ja' ? 'エージェント管理' : 'Agents',
  newSkill: locale.value === 'zh' ? '新建技能' : locale.value === 'ja' ? '新しいスキル' : 'New skill',
  newTool: locale.value === 'zh' ? '新建工具' : locale.value === 'ja' ? '新しいツール' : 'New tool',
  newAgent: locale.value === 'zh' ? '新建 Agent' : locale.value === 'ja' ? '新しいエージェント' : 'New agent',
  edit: locale.value === 'zh' ? '编辑' : locale.value === 'ja' ? '編集' : 'Edit',
  delete: locale.value === 'zh' ? '删除' : locale.value === 'ja' ? '削除' : 'Delete',
  confirmDelete: locale.value === 'zh' ? '确定删除？此操作不可恢复' : locale.value === 'ja' ? '削除しますか？元に戻せません' : 'Delete? This cannot be undone',
  enabled: locale.value === 'zh' ? '已启用' : locale.value === 'ja' ? '有効' : 'Enabled',
  disabled: locale.value === 'zh' ? '已停用' : locale.value === 'ja' ? '無効' : 'Disabled',
  linkedSkills: locale.value === 'zh' ? '关联技能' : locale.value === 'ja' ? '連携スキル' : 'Linked skills',
  linkedSubagents: locale.value === 'zh' ? '关联子 Agent' : locale.value === 'ja' ? '連携サブエージェント' : 'Linked subagents',
  linkedTools: locale.value === 'zh' ? '关联工具' : locale.value === 'ja' ? '連携ツール' : 'Linked tools',
  linkedKnowledge: locale.value === 'zh' ? '关联知识库' : locale.value === 'ja' ? '連携ナレッジベース' : 'Linked knowledge bases',
  currentAgent: locale.value === 'zh' ? '当前使用' : locale.value === 'ja' ? '現在使用中' : 'In use',
  subagents: locale.value === 'zh' ? '子 Agent' : locale.value === 'ja' ? 'サブエージェント' : 'Subagents',
  close: locale.value === 'zh' ? '关闭' : locale.value === 'ja' ? '閉じる' : 'Close',
  available: locale.value === 'zh' ? '待选' : locale.value === 'ja' ? '未選択' : 'Available',
  selected: locale.value === 'zh' ? '已选' : locale.value === 'ja' ? '選択済み' : 'Selected',
  taskDetail: locale.value === 'zh' ? '任务详情' : locale.value === 'ja' ? 'タスク詳細' : 'Task details',
  agentNameLabel: locale.value === 'zh' ? 'Agent 名' : locale.value === 'ja' ? 'エージェント名' : 'Agent name',
  agentDescriptionLabel: locale.value === 'zh' ? 'Agent 概要' : locale.value === 'ja' ? 'エージェント概要' : 'Agent summary',
  skillName: locale.value === 'zh' ? '技能名' : locale.value === 'ja' ? 'スキル名' : 'Skill name',
  skillDescription: locale.value === 'zh' ? '技能描述' : locale.value === 'ja' ? 'スキル説明' : 'Skill description',
  skillBody: locale.value === 'zh' ? '技能正文' : locale.value === 'ja' ? 'スキル本文' : 'Skill body',
  searchPlaceholder: locale.value === 'zh' ? '搜索…' : locale.value === 'ja' ? '検索…' : 'Search…',
  noMatch: locale.value === 'zh' ? '无匹配结果' : locale.value === 'ja' ? '一致する結果がありません' : 'No matches',
  mcpServers: locale.value === 'zh' ? 'MCP 服务' : locale.value === 'ja' ? 'MCP サーバー' : 'MCP Servers',
  emptyList: locale.value === 'zh' ? '暂无内容，可通过对话让 AI 生成' : locale.value === 'ja' ? 'まだありません。会話でAIに生成させることができます' : 'Empty. Ask the AI to create one via conversation'
}))

const currentConversation = computed(() =>
  conversations.value.find((conversation) => conversation.id === activeConversationId.value)
)
const currentMessages = computed(() => currentConversation.value?.messages ?? [])
// 定时任务专用会话是只读履历：隐藏输入框（composer）
const isTimerSession = computed(() =>
  !!currentConversation.value && String(currentConversation.value.id).startsWith('timer-')
)
// Large conversations render only a tail window so the full message list DOM
// stays small; the "load earlier" button widens the window step by step.
const MESSAGE_WINDOW = 60
const messageWindowSize = ref(MESSAGE_WINDOW)
const visibleMessages = computed(() => {
  const msgs = currentMessages.value
  if (msgs.length <= messageWindowSize.value) return msgs
  return msgs.slice(msgs.length - messageWindowSize.value)
})
const showLoadEarlier = computed(() => currentMessages.value.length > messageWindowSize.value)
function loadEarlierMessages() {
  const el = timeline.value
  const prevTop = el?.scrollTop ?? 0
  messageWindowSize.value += MESSAGE_WINDOW
  nextTick(() => {
    if (el) el.scrollTop = prevTop
  })
}
const lastAssistantId = computed(() => {
  const msgs = currentMessages.value
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant') return msgs[i].id
  }
  return ''
})
// Running state only watches assistant status: during streaming (including the
// tool-call phase) the stop button must stay visible; finalText is not a
// reliable "done" signal because it becomes non-empty after the first chunk.
const isRunning = computed(() => currentMessages.value.some(
  (item) => item.role === 'assistant'
    && (item.status === 'pending' || item.status === 'streaming' || item.status === 'waiting_permission')
))
// Whether a single assistant message is still running (drives the tail loader)
function isAssistantRunning(item: AssistantMessage) {
  return item.role === 'assistant'
    && (item.status === 'pending' || item.status === 'streaming' || item.status === 'waiting_permission')
}
/** Done 消息：是否包含 thought/tool/plan 步骤（需要折叠）。 */
function hasProcessSegments(item: AssistantMessage) {
  const segs = item.segments || []
  // Any thought/tool/plan, OR any text that appears before the last "final" text block.
  if (segs.some((s: any) => s.type === 'thought' || s.type === 'tool' || s.type === 'plan')) return true
  // If there are 2+ text segments, earlier ones are process narration.
  return segs.filter((s: any) => s.type === 'text').length > 1
}
function processStepCount(item: AssistantMessage) {
  const segs = item.segments || []
  const nonText = segs.filter((s: any) => s.type !== 'text').length
  const textCount = segs.filter((s: any) => s.type === 'text').length
  // Count non-text steps + earlier text narration (all but the last text).
  return nonText + Math.max(0, textCount - 1)
}
/** Final answer = trailing text blocks after the last thought/tool/plan.
 *  Earlier text blocks (process narration like "let me try another query")
 *  go into the folded process area. */
function finalTextSegments(item: AssistantMessage) {
  const segs = item.segments || []
  let lastProcessIdx = -1
  for (let i = segs.length - 1; i >= 0; i--) {
    if (segs[i].type !== 'text') { lastProcessIdx = i; break }
  }
  // Text blocks after the last process step = final answer.
  return segs.slice(lastProcessIdx + 1).filter((s: any) => s.type === 'text')
}
/** Everything that goes inside the folded process area. */
function processSegments(item: AssistantMessage) {
  const segs = item.segments || []
  let lastProcessIdx = -1
  for (let i = segs.length - 1; i >= 0; i--) {
    if (segs[i].type !== 'text') { lastProcessIdx = i; break }
  }
  return segs.slice(0, lastProcessIdx + 1)
}
/** 空会话默认标题按当前语言显示（兼容历史数据里写死的 中/英/日「新建会话」）。 */
function displayTitle(title: string): string {
  const n = (title || '').trim()
  if (!n) return t('newSessionTitle')
  if (n === '新建会话' || n === 'New conversation' || n === '新しい会話') return t('newSessionTitle')
  return n
}

const currentTitle = computed(() => {
  if (!currentConversation.value) return t('newSessionTitle')
  const t0 = currentConversation.value?.title ?? ''
  if (String(currentConversation.value?.id ?? '').startsWith('timer-')) {
    return timerTitle(t0) || t('newSessionTitle')
  }
  return displayTitle(t0)
})
const connectionLabel = computed(() => connected.value ? t('connected') : t('disconnected'))

function migrateConversations(saved: unknown): Conversation[] {
  if (!Array.isArray(saved)) return []
  return saved.map((conversation) => ({
    ...conversation,
    messages: (conversation.messages ?? []).map((item: ChatMessage) => {
      if (item.role !== 'assistant') return item
      // History is never mid-run when restored: a stale streaming/pending
      // status saved during an interrupted run would otherwise keep the stop
      // button active forever after reload.
      if (item.status === 'pending' || item.status === 'streaming' || item.status === 'waiting_permission') {
        // 定时任务会话的 pending 是真实运行中（执行期间 5s 轮询能拉到），
        // 必须保留 loading 态；普通会话的历史中断态则统一归为 completed。
        if (!String(conversation.id).startsWith('timer-')) {
          item = { ...item, status: 'completed' }
        }
      }
      const hasNewSegments = Array.isArray(item.segments)
        && item.segments.some((segment) => segment.type === 'thought' || segment.type === 'tool' || segment.type === 'plan')
      if (hasNewSegments) return item
      // Legacy migration: rebuild the interleaved segments from plan/analysis/tool-calls/final text
      const segments: AssistantSegment[] = []
      if (item.process?.plan?.length) segments.push({ id: createId('plan'), type: 'plan' })
      ;(item.process?.analyses ?? []).forEach((analysis) => {
        segments.push({ id: createId('thought'), type: 'thought', text: analysis.text })
      })
      ;(item.process?.toolCalls ?? []).forEach((tool) => {
        segments.push({ id: createId('tool'), type: 'tool', toolId: tool.id })
      })
      if (item.finalText) segments.push({ id: createId('text'), type: 'text', text: item.finalText })
      return { ...item, segments }
    })
  }))
}

async function saveConversation(conversation: Conversation) {
  try {
    await fetch(`/api/history/${encodeURIComponent(conversation.id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation }),
    })
  } catch {
    // server unavailable: keep the in-memory state, the next key event retries
  }
}

async function deleteConversationRemote(id: string) {
  try {
    await fetch(`/api/history/${encodeURIComponent(id)}`, { method: 'DELETE' })
  } catch {
    // server unavailable: nothing to do locally
  }
}

async function loadHistory() {
  let fromServer = false
  try {
    const res = await fetch('/api/history')
    if (res.ok) {
      const rows = await res.json()
      conversations.value = migrateConversations(rows)
      fromServer = true
    }
  } catch {
    // fall through to the legacy localStorage copy below
  }
  if (!fromServer) {
    // API unavailable (e.g. backend starting up): keep the previous behavior
    try {
      conversations.value = migrateConversations(JSON.parse(localStorage.getItem(STORE_KEY) ?? '[]'))
    } catch {
      conversations.value = []
    }
    return
  }
  // One-time migration: when MooFile is empty, seed it from the legacy
  // localStorage list, then drop the old key so it never re-imports.
  if (!conversations.value.length) {
    try {
      const legacy = JSON.parse(localStorage.getItem(STORE_KEY) ?? '[]')
      if (Array.isArray(legacy) && legacy.length) {
        conversations.value = migrateConversations(legacy)
        conversations.value.forEach((conversation) => void saveConversation(conversation))
      }
    } catch {
      // ignore malformed legacy data
    }
  }
  localStorage.removeItem(STORE_KEY)
  if (conversations.value.length) {
    // 默认进入新建会话：不选中任何历史会话（用户明确要求）
    activeConversationId.value = ''
  }
}

function createId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/** 新建会话：直接回到初始模式选择页（无弹窗），发送消息时按所选模式新建会话。 */
function openNewConversation() {
  activeConversationId.value = ''
  conversationSearch.value = ''
  contextOverride.value = null
  messageWindowSize.value = MESSAGE_WINDOW
  attachments.value = []
  errorText.value = ''
  managementVisible.value = false
  sidebarVisible.value = false
}

function createConversation() {
  agentCollapsed.value = false
  // NOTE: don't reset webSearch/history/jpMark — user may have toggled them
  // on the welcome page before sending; keep their current selection.
  // Pending draft attachments (e.g. an image attached on the start page) must
  // survive conversation creation: submitPrompt reads attachments.value AFTER
  // this call. selectConversation below clears the draft, so capture and
  // restore it in both branches.
  const pendingAttachments = [...attachments.value]
  const existingEmpty = conversations.value.find(
    (conversation) => conversation.messages.length === 0
      && (conversation.agent ?? (conversation.mode === 'chat' ? 'Chat' : 'default')) === currentAgentName.value
  )
  if (existingEmpty) {
    selectConversation(existingEmpty.id)
    attachments.value = pendingAttachments
    conversationSearch.value = ''
    sidebarVisible.value = false
    nextTick(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus())
    return
  }
  const conversation: Conversation = {
    id: `web-${Date.now()}`,
    title: t('newSessionTitle'),
    updatedAt: Date.now(),
    messages: [],
    agent: currentAgentName.value || 'default',
    model: currentModel.value || localStorage.getItem(MODEL_STORAGE_KEY) || undefined
  }
  conversations.value.unshift(conversation)
  activeConversationId.value = conversation.id
  conversationSearch.value = ''
  // NOTE: do NOT reset attachments.value here — submitPrompt captures the
  // pending attachments right after createConversation returns.
  input.value = ''
  errorText.value = ''
  sidebarVisible.value = false
  void saveConversation(conversation)
  nextTick(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus())
}

/** Open the standalone voice-call window (#/voice hash route) */
function openVoiceCall() {
  const url = `${window.location.origin}${window.location.pathname}#/voice`
  window.open(url, 'voice-call', 'width=440,height=720,resizable=yes')
}

/** 定时任务会话标题：去掉历史遗留的「【定时任务】」前缀再展示。 */
function timerTitle(title: string): string {
  return String(title || '').replace(/^【定时任务】/, '')
}

/** 执行记录按开始时间降序（最新在前），兼容后端新旧两种存储顺序。 */
function sortedRuns(task: { runHistory?: any[] }): any[] {
  return [...(task.runHistory || [])].sort((a, b) => (b.startedAt || 0) - (a.startedAt || 0))
}

function selectConversation(id: string) {
  activeConversationId.value = id
  const modeConv = conversations.value.find((c) => c.id === id)
  const conversationAgent = normalizeAgentName(modeConv?.agent ?? (modeConv?.mode === 'chat' ? 'Chat' : 'default'))
  if (conversationAgent !== currentAgentName.value) {
    currentAgentName.value = conversationAgent
    localStorage.setItem(AGENT_STORAGE_KEY, conversationAgent)
    resetSocketForAgentChange()
  }
  // 模型按会话持久化：进入会话时恢复其模型（老会话无记录则保持当前选择）
  const convModel = modeConv?.model
  if (convModel && convModel !== currentModel.value) {
    currentModel.value = convModel
    resetSocketForAgentChange()
  }
  // Restore per-session conversation toggles.
  let needReconnect = false
  if (modeConv?.webSearchOn !== undefined && modeConv.webSearchOn !== webSearchOn.value) {
    webSearchOn.value = modeConv.webSearchOn
    localStorage.setItem(WEB_SEARCH_STORAGE_KEY, String(modeConv.webSearchOn))
    needReconnect = true
  }
  if (modeConv?.historyOn !== undefined && modeConv.historyOn !== historyOn.value) {
    historyOn.value = modeConv.historyOn
    localStorage.setItem(HISTORY_STORAGE_KEY, String(modeConv.historyOn))
  }
  if (modeConv?.jpMarkOn !== undefined && modeConv.jpMarkOn !== jpMarkOn.value) {
    jpMarkOn.value = modeConv.jpMarkOn
    localStorage.setItem(JP_MARK_STORAGE_KEY, String(modeConv.jpMarkOn))
  }
  // Restore per-session role preset
  const convRole = modeConv?.roleName || ''
  if (convRole !== systemOverrideName.value) {
    if (convRole) {
      const r = systemCommands.value.find((x: any) => x.name === convRole)
      if (r) {
        systemOverride.value = r.content
        systemOverrideName.value = r.name
      }
    } else {
      systemOverride.value = ''
      systemOverrideName.value = ''
    }
    needReconnect = true
  }
  if (needReconnect) resetSocketForAgentChange()
  contextOverride.value = null // usage is per-session; refreshed after history load
  messageWindowSize.value = MESSAGE_WINDOW
  attachments.value = []
  sidebarVisible.value = false
  errorText.value = ''
  // 切换会话时自动展开对应分组；进入定时任务会话时只保留该任务。
  if (String(id).startsWith('timer-')) {
    timerCollapsed.value = false
    timersScopeAll.value = false
  } else {
    agentCollapsed.value = false
  }
  managementVisible.value = false
  // Keep the in-memory state of a running conversation local so history cannot
  // roll the stream back
  const local = conversations.value.find((conversation) => conversation.id === id)
  const running = Boolean(local?.messages.some(
    (item) => item.role === 'assistant'
      && (item.status === 'pending' || item.status === 'streaming' || item.status === 'waiting_permission')
  ))
  if (!running) {
    conversationLoading.value += 1
    void (async () => {
      try {
        const res = await fetch(`/api/history/${encodeURIComponent(id)}`)
        if (res.ok) {
          const rows = migrateConversations([await res.json()])
          if (rows.length) {
            const index = conversations.value.findIndex((conversation) => conversation.id === id)
            if (index >= 0) {
              // Prefer the fresher copy: a local conversation whose last write
              // is newer than the server row keeps its state (avoids the
              // server overwriting a rename/edit that the PUT has not flushed).
              const local = conversations.value[index]
              if (!(rows[0].updatedAt && (local.updatedAt ?? 0) > rows[0].updatedAt)) {
                conversations.value[index] = rows[0]
              }
            } else {
              conversations.value.unshift(rows[0])
            }
          }
        }
      } catch {
        // server unavailable: keep the local copy
      } finally {
        conversationLoading.value = Math.max(0, conversationLoading.value - 1)
        nextTick(scrollToBottom)
        void refreshContextUsage()
      }
    })()
  }
  // todo 分步清单：从消息重放同步，本地缺失则按 todoId 从后端恢复
  const target = conversations.value.find((conversation) => conversation.id === id)
  syncTodoState(target)
  if (target) void restoreTodoState(target)
  nextTick(scrollToBottom)
}

function deleteConversation(id: string) {
  conversations.value = conversations.value.filter((conversation) => conversation.id !== id)
  if (activeConversationId.value === id) {
    activeConversationId.value = conversations.value[0]?.id ?? ''
  }
  void deleteConversationRemote(id)
  // 双向联动：删除定时任务会话时，一并删除绑定的定时任务
  const linked = timers.value.find((t) => t.sessionId === id)
  if (linked) {
    void fetch(`/api/timers/${linked.id}`, { method: 'DELETE' })
      .catch(() => undefined)
      .finally(() => void loadTimers())
  }
}

/** Confirm handler for the centered delete dialog: clear the pending target
 * first so a double positive-click cannot delete twice. */
function confirmDeleteConversation() {
  const id = deleteTarget.value
  deleteTarget.value = null
  if (id) deleteConversation(id)
}

const renameMenu = ref<{ id: string; x: number; y: number } | null>(null)
const renameTarget = ref<Conversation | null>(null)
// Session to delete when the centered confirm dialog is accepted. A centered
// NModal is used instead of an anchored NPopconfirm because the popconfirm
// drifted away from its trigger inside the scrolling conversation list.
const deleteTarget = ref<string | null>(null)
const renameText = ref('')
function openRenameMenu(conversation: Conversation, event: MouseEvent) {
  renameMenu.value = { id: conversation.id, x: event.clientX, y: event.clientY }
}
function closeRenameMenu() {
  renameMenu.value = null
}
function openRenameModal() {
  const id = renameMenu.value?.id
  closeRenameMenu()
  const conversation = conversations.value.find((item) => item.id === id)
  if (!conversation) return
  renameTarget.value = conversation
  renameText.value = conversation.title
}
function saveRename() {
  const conversation = renameTarget.value
  if (conversation && renameText.value.trim()) {
    conversation.title = renameText.value.trim()
    conversation.updatedAt = Date.now()
    void saveConversation(conversation)
  }
  renameTarget.value = null
}

// ---------- todo 分步清单（从 ACP toolCalls 事件重放构建） ----------
function parseTodoRawInput(rawInput: unknown): Record<string, any> {
  if (typeof rawInput === 'string') {
    try { return JSON.parse(rawInput) } catch { return {} }
  }
  return (rawInput && typeof rawInput === 'object' ? rawInput : {}) as Record<string, any>
}

/** 按消息顺序重放 create_todo / update_todo，得到最终清单与 todoId（幂等）。 */
function scanTodoState(conversation: Conversation): { todoId: string | null; items: TodoItem[] } {
  let todoId: string | null = null
  let items: TodoItem[] = []
  for (const message of conversation.messages) {
    if (message.role !== 'assistant') continue
    for (const tool of message.process?.toolCalls ?? []) {
      if (tool.name === 'create_todo') {
        const input = parseTodoRawInput(tool.rawInput)
        const rawItems: unknown = Array.isArray(input.items) ? input.items : []
        items = rawItems
          .map((raw: any, index: number) => ({
            index,
            title: String(raw?.title ?? '').trim() || `步骤 ${index + 1}`,
            description: raw?.description ? String(raw.description) : null,
            status: 'pending' as const,
            note: null,
          }))
          .filter((item: TodoItem) => item.title)
        const output = parseTodoRawInput(tool.output)
        if (output.todo_id) todoId = String(output.todo_id)
      } else if (tool.name === 'update_todo' && items.length) {
        const input = parseTodoRawInput(tool.rawInput)
        const index = Number(input.step_index)
        const target = items[index]
        if (target && ['pending', 'in_progress', 'completed', 'blocked'].includes(input.status)) {
          target.status = input.status
          target.note = input.note ? String(input.note) : null
        }
      }
    }
  }
  return { todoId, items }
}

function syncTodoState(conversation: Conversation | null | undefined) {
  if (!conversation) return
  const { todoId, items } = scanTodoState(conversation)
  if (items.length) {
    todoStates.value = { ...todoStates.value, [conversation.id]: items }
  }
  // todoId 持久化（刷新后经 /api/todos 恢复）
  if (todoId && conversation.todoId !== todoId) {
    conversation.todoId = todoId
    void saveConversation(conversation)
  }
}

const currentTodoItems = computed(() => {
  const conversation = currentConversation.value
  if (!conversation) return null
  return todoStates.value[conversation.id] ?? null
})

/** 打开会话时恢复：本地无 todo 状态但会话记录了 todoId → 从后端拉取。 */
async function restoreTodoState(conversation: Conversation) {
  if (todoStates.value[conversation.id] || !conversation.todoId) return
  try {
    const res = await fetch(`/api/todos/${encodeURIComponent(conversation.todoId)}`)
    if (res.ok) {
      const todo = await res.json()
      if (Array.isArray(todo.items) && todo.items.length) {
        todoStates.value = { ...todoStates.value, [conversation.id]: todo.items }
      }
    }
  } catch {
    // backend unavailable: keep rendering from messages only
  }
}


// ---------- 右上角菜单：统计 / 导出会话 / 帮助 ----------
const helpVisible = ref(false)
const statisticsVisible = ref(false)
const headerMoreOptions = computed(() => [
  { label: t('statistics.title'), key: 'statistics' },
  { label: t('exportConversation'), key: 'export' },
  { label: t('help'), key: 'help' },
])
function onHeaderMoreSelect(key: string) {
  if (key === 'statistics') statisticsVisible.value = true
  else if (key === 'export') exportConversation()
  else if (key === 'help') helpVisible.value = true
}

/** 导出当前会话为 Markdown：元数据头 + 按时间顺序的完整对话记录。 */
function exportConversation() {
  const conversation = conversations.value.find((c) => c.id === activeConversationId.value)
  if (!conversation) return
  try {
    const lines: string[] = []
    const title = displayTitle(conversation.title)
    lines.push(`# ${title}`)
    lines.push('')
    lines.push(`- 会话 ID：${conversation.id}`)
    lines.push(`- 更新时间：${new Date(conversation.updatedAt).toLocaleString()}`)
    lines.push('')
    for (const message of conversation.messages) {
      const time = new Date(message.createdAt).toLocaleString()
      if (message.role === 'user') {
        lines.push(`## 用户 · ${time}`)
        lines.push('')
        lines.push(message.text || '（空消息）')
        if (message.attachments && message.attachments.length) {
          lines.push('')
          lines.push('**附件：**')
          for (const attachment of message.attachments) {
            const name = attachment.name || attachment.path || attachment.uri || ''
            lines.push(`- ${name}`)
          }
        }
        lines.push('')
      } else {
        lines.push(`## AI · ${time} · ${message.status}`)
        if (message.finalText) {
          lines.push('')
          lines.push(message.finalText)
        }
        const tools = message.process?.toolCalls ?? []
        if (tools.length) {
          lines.push('')
          lines.push(`**工具调用（${tools.length}）：**`)
          for (const tool of tools) lines.push(`- ${tool.name}（${tool.status}）`)
        }
        lines.push('')
      }
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${title.replace(/[\\/:*?"<>|]/g, '_')}.md`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    message.success(t('exportSuccess'))
  } catch {
    message.error(t('exportFailed'))
  }
}


// After a manual compaction the backend reports the real post-compaction
// usage; show that instead of the (unchanged) local message estimate until
// the next user message resets it.
const contextOverride = ref<{ tokens: number; pct: number; total: number } | null>(null)

const contextUsage = computed(() => {
  const conversation = currentConversation.value
  if (!conversation || !conversation.messages.length || !contextSize.value) return null
  if (contextOverride.value) return contextOverride.value
  let chars = 0
  for (const item of conversation.messages) {
    if (item.role === 'user') {
      chars += item.text.length
    } else {
      chars += (item.finalText ?? '').length
      chars += (item.process?.toolCalls ?? []).reduce((sum, tool) => sum + (tool.input ?? '').length + (tool.output ?? '').length, 0)
    }
  }
  const tokens = Math.max(1, Math.round(chars / 1.7))
  const pct = Math.min(100, Math.round((tokens / contextSize.value) * 100))
  return { pct, tokens, total: contextSize.value }
})

const compactingContext = ref(false)

// Fetch the real model-side context usage from the backend checkpoint.
// The local estimate is computed from the full history, which compaction
// never touches, so after an auto/manual compaction only the backend knows
// the true post-compaction numbers.
// Throttled context refresh during streaming: the backend recomputes
// tokens on each request, but hitting it on every chunk is wasteful.
let _ctxRefreshTimer: number | undefined
function scheduleContextRefresh() {
  if (_ctxRefreshTimer) return
  _ctxRefreshTimer = window.setTimeout(() => {
    _ctxRefreshTimer = undefined
    void refreshContextUsage()
  }, 2000)
}

async function refreshContextUsage() {
  const conversation = currentConversation.value
  if (!conversation) return
  try {
    const res = await fetch(
      `/api/context/usage?sessionId=${encodeURIComponent(conversation.agentSessionId ?? conversation.id)}`
      + `&agent=${encodeURIComponent(currentAgentName.value || 'default')}`
    )
    if (!res.ok) return
    const data = await res.json()
    if (data.ok && contextSize.value) {
      contextOverride.value = {
        tokens: Number(data.tokens) || 0,
        pct: Number(data.pct) ?? 0,
        total: Number(data.total) || contextSize.value,
      }
    }
  } catch {
    // backend unavailable: keep the local estimate
  }
}

async function compactContext() {
  const conversation = currentConversation.value
  if (!conversation || compactingContext.value || isRunning.value) return
  compactingContext.value = true
  try {
    const res = await fetch('/api/context/compact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sessionId: conversation.agentSessionId ?? conversation.id,
        agent: currentAgentName.value || 'default',
      }),
    })
    const data = await res.json()
    if (data.ok) {
      contextOverride.value = {
        tokens: Number(data.after_tokens) || 0,
        pct: Number(data.pct_after) ?? 0,
        total: contextSize.value,
      }
      if (data.summary) {
        ;message.success(`${actionLabels.value.compactDone}（${data.pct_before}% → ${data.pct_after}%）`)
      } else {
        ;message.success(actionLabels.value.compactDone)
      }
    } else {
      ;message.error(`${actionLabels.value.compactFailed}: ${data.error ?? ''}`)
    }
  } catch (e) {
    ;message.error(`${actionLabels.value.compactFailed}: ${String(e)}`)
  } finally {
    compactingContext.value = false
  }
}

/** Open the live context debug view (GET /api/context/current) in a popup window. */
function viewContext() {
  const conversation = currentConversation.value
  if (!conversation) return
  const sid = conversation.agentSessionId ?? conversation.id
  const roleParam = systemOverrideName.value ? `&role=${encodeURIComponent(systemOverrideName.value)}` : ''
  const url = `/api/context/current?sessionId=${encodeURIComponent(sid)}&format=html${roleParam}`
  window.open(url, '_blank', 'width=1200,height=800,noopener=true')
}

/** Model list from /api/config (model_config.json); the chosen model is passed
 *  to the backend via the WS query param (same pattern as agent). */
interface ModelEntry {
  model_name: string
  content_size: number
}

/** 模型偏好：浏览器存储，新建会话默认取最近一次使用的模型。 */
const MODEL_STORAGE_KEY = 'chat_model'

/** 历史上下文偏好：浏览器存储（true=携带完整历史；false=每次只带当前这一条消息）。 */
const HISTORY_STORAGE_KEY = 'chat_history_on'
const historyOn = ref(localStorage.getItem(HISTORY_STORAGE_KEY) !== 'false') // 默认开（现状行为）

/** 联网搜索开关（仅 Chat 模式）：开启时先搜网再回答。 */
const WEB_SEARCH_STORAGE_KEY = 'chat_web_search_on'
const webSearchOn = ref(localStorage.getItem(WEB_SEARCH_STORAGE_KEY) !== 'false') // 默认开
const JP_MARK_STORAGE_KEY = 'chat_jp_mark_on'
const jpMarkOn = ref(localStorage.getItem(JP_MARK_STORAGE_KEY) !== 'false') // 默认开

/** System-prompt override set by clicking a "system" quick command. Prepended to every prompt until cleared. */
const systemOverride = ref('')
const systemOverrideName = ref('')
const showRolePresets = ref(false)
const systemCommands = ref<any[]>([])
const roleSearch = ref('')
const filteredRoles = computed(() => {
  if (!roleSearch.value) return systemCommands.value
  const q = roleSearch.value.toLowerCase()
  return systemCommands.value.filter((c: any) => c.name.toLowerCase().includes(q) || (c.content || '').toLowerCase().includes(q))
})
function refreshSystemCommands() {
  fetch('/api/roles').then(r => r.json()).then(d => {
    if (d.ok) systemCommands.value = d.items
  }).catch(() => {})
}
// 日语自动注音常驻开启（显示层），无开关；后端 /api/jp-mark 保留

const currentModel = ref('')
const modelList = ref<ModelEntry[]>([])

/** Context window (tokens) of the currently selected model — drives the
 *  context-usage percentage in the composer. */
const contextSize = computed(() => {
  const hit = modelList.value.find((m) => m.model_name === currentModel.value)
  return hit?.content_size ?? 0
})
async function loadAppConfig() {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    modelList.value = Array.isArray(data.models) ? data.models : []
    // 恢复顺序：当前会话记录 > 浏览器上次选择 > 第一个模型
    const convModel = activeConversation.value?.model
    currentModel.value =
      convModel
      || localStorage.getItem(MODEL_STORAGE_KEY)
      || modelList.value[0]?.model_name
      || ''
  } catch {
    modelList.value = []
  }
}

function switchModel(name: string) {
  if (!name || name === currentModel.value) return
  currentModel.value = name
  localStorage.setItem(MODEL_STORAGE_KEY, name)
  const conv = activeConversation.value
  if (conv) {
    conv.model = name
    void saveConversation(conv)
  }
  contextOverride.value = null
  // 模型随 WS 查询参数传递到后台子进程（同 agent），重建连接使新模型生效
  resetSocketForAgentChange()
  message.success(t('modelSwitchSuccess'))
}

const sidebarCollapsed = ref(false)
type ManagementSection = 'timers' | 'agents' | 'skills' | 'tools' | 'knowledge' | 'roles' | 'commands'
const managementVisible = ref(false)
const managementSection = ref<ManagementSection>('agents')
const managementTitle = computed(() =>
  locale.value === 'zh' ? '设置与管理' : locale.value === 'ja' ? '設定と管理' : 'Settings & Management'
)
const managementMenuItems = computed(() => {
  const automation = locale.value === 'zh' ? '自动化' : locale.value === 'ja' ? '自動化' : 'Automation'
  const agent = 'Agent'
  const application = locale.value === 'zh' ? '应用' : locale.value === 'ja' ? 'アプリ' : 'Application'
  return [
    { key: 'timers', label: t('timers.panelTitle'), group: automation, icon: TimeOutline },
    { key: 'agents', label: actionLabels.value.agents, group: agent, icon: SparklesOutline },
    { key: 'skills', label: actionLabels.value.skills, group: agent, icon: CodeSlashOutline },
    { key: 'tools', label: actionLabels.value.tools, group: agent, icon: ConstructOutline },
    { key: 'knowledge', label: actionLabels.value.knowledge, group: agent, icon: BookOutline },
    { key: 'roles', label: t('rolePreset'), group: application, icon: PersonCircleOutline },
    { key: 'commands', label: t('quickCommands.management'), group: application, icon: FlashOutline },
  ]
})
function selectManagementSection(section: string) {
  managementSection.value = section as ManagementSection
  panelQuery.value = ''
  if (section === 'timers') {
    timersScopeAll.value = true
    void loadTimers()
  } else if (section === 'roles') {
    void refreshSystemCommands()
  } else if (section === 'commands') {
    void loadManagementQuickCommands()
  }
}
function openManagement(section: ManagementSection = managementSection.value) {
  selectManagementSection(section)
  managementVisible.value = true
}

// ---------- Manage panel (skills / tools / agents) ----------
const panelQuery = ref('')
const skillList = ref<any[]>([])
const toolList = ref<any[]>([])
const builtinTools = ref<any[]>([])
const agentList = ref<any[]>([])
const managementQuickCommands = ref<QuickCommand[]>([])
const commandSearch = ref('')
const commandModalVisible = ref(false)
const editingCommandId = ref('')
const commandForm = ref({
  title: '',
  content: '',
  agent: 'Chat',
  model: '',
  autoSend: false,
  role: '',
  webSearchOn: true,
  historyOn: true,
  jpMarkOn: true,
  showOnHome: true,
})
const filteredManagementQuickCommands = computed(() => {
  const query = commandSearch.value.trim().toLowerCase()
  if (!query) return managementQuickCommands.value
  return managementQuickCommands.value.filter((command) =>
    command.title.toLowerCase().includes(query)
    || command.content.toLowerCase().includes(query)
  )
})
const commandAgentOptions = computed(() =>
  agentList.value.map((agent) => ({
    label: displayAgentName(agent.name),
    value: agent.name,
  }))
)
const availableCommandModelOptions = computed(() =>
  modelList.value.map((model) => ({ label: model.model_name, value: model.model_name }))
)
const commandModelOptions = computed(() => [
  { label: t('quickCommands.defaultModel'), value: '' },
  ...availableCommandModelOptions.value,
])
const commandRoleOptions = computed(() => [
  { label: t('quickCommands.noRole'), value: '' },
  ...systemCommands.value.map((role) => ({ label: role.name, value: role.name })),
])

async function loadManagementQuickCommands() {
  try {
    const data = await apiManage('/api/quick-commands')
    managementQuickCommands.value = data.ok && Array.isArray(data.items) ? data.items : []
  } catch (error: any) {
    message.error(error.message)
  }
}

function openCommandEdit(command?: QuickCommand) {
  editingCommandId.value = command?.id ?? ''
  commandForm.value = command
    ? {
        title: command.title,
        content: command.content,
        agent: command.agent,
        model: command.model || '',
        autoSend: command.autoSend,
        role: command.role ?? '',
        webSearchOn: command.webSearchOn !== false,
        historyOn: command.historyOn !== false,
        jpMarkOn: command.jpMarkOn !== false,
        showOnHome: command.showOnHome !== false,
      }
    : {
        title: '',
        content: '',
        agent: 'Chat',
        model: availableCommandModelOptions.value[0]?.value ?? '',
        autoSend: false,
        role: '',
        webSearchOn: true,
        historyOn: true,
        jpMarkOn: true,
        showOnHome: true,
      }
  commandModalVisible.value = true
}

async function saveManagementQuickCommand() {
  if (!commandForm.value.title.trim()) return
  try {
    const path = editingCommandId.value
      ? `/api/quick-commands/${encodeURIComponent(editingCommandId.value)}`
      : '/api/quick-commands'
    await apiManage(path, editingCommandId.value ? 'PUT' : 'POST', {
      ...commandForm.value,
      mode: commandForm.value.agent === 'Chat' ? 'chat' : 'task',
    })
    commandModalVisible.value = false
    await loadManagementQuickCommands()
    await qcBar.value?.reload?.()
  } catch (error: any) {
    message.error(error.message)
  }
}

async function deleteManagementQuickCommand(command: QuickCommand) {
  if (!window.confirm(t('quickCommands.deleteConfirm', { title: command.title }))) return
  try {
    await apiManage(`/api/quick-commands/${encodeURIComponent(command.id)}`, 'DELETE')
    await loadManagementQuickCommands()
    await qcBar.value?.reload?.()
  } catch (error: any) {
    message.error(error.message)
  }
}

/** Knowledge base (MooFile Base) state for the current agent. */
const knowledgeList = ref<KnowledgeDb[]>([])
const knowledgeConfig = ref<{ baseUrl: string; topK: number; threshold: number | null; reachable: boolean }>({
  baseUrl: '',
  topK: 5,
  threshold: null,
  reachable: false
})

const filteredKnowledge = computed(() => {
  const q = panelQuery.value.trim().toLowerCase()
  const list = knowledgeList.value.filter((k) =>
    (!q
      || (k.name || '').toLowerCase().includes(q)
      || (k.id || '').toLowerCase().includes(q))
    && k.type === 'vector'
  )
  return [...list].sort((a, b) => Number(b.enabled || false) - Number(a.enabled || false))
})

async function loadKnowledge() {
  try {
    knowledgeConfig.value = await fetchKnowledgeConfig()
    const agent = currentAgentName.value || 'default'
    knowledgeList.value = await fetchKnowledgeDatabases(agent)
  } catch {
    knowledgeList.value = []
    knowledgeConfig.value = { ...knowledgeConfig.value, reachable: false }
  }
}

const knowledgeManageVisible = ref(false)
function openKnowledgeManager() {
  const baseUrl = knowledgeConfig.value.baseUrl
  if (!baseUrl) {
    message.warning(actionLabels.value.knowledgeUnreachable)
    return
  }
  // Open the external moofile_base admin UI inside an in-app modal (iframe)
  // so the management page feels like part of the application.
  knowledgeManageVisible.value = true
}

// ---------- Timers panel (scheduled tasks) ----------
interface TimerRun {
  id: string
  startedAt: number
  finishedAt?: number
  status: string
  outputSummary?: string
}
interface TimerTask {
  id: string
  name: string
  prompt: string
  schedule?: string | null
  intervalMinutes?: number | null
  sessionId: string
  agent?: string
  enabled: boolean
  lastRunAt?: number | null
  lastStatus?: string
  runHistory?: TimerRun[]
  createdAt: number
  updatedAt: number
  nextRunAt?: number | null
  cronValid?: boolean
}

const timers = ref<TimerTask[]>([])
const timerSearch = ref('')
const timerEditVisible = ref(false)
const timerEditTarget = ref<TimerTask | null>(null)
const timerDraft = reactive<{
  name: string
  description: string
  prompt: string
  mode: 'cron' | 'interval'
  cron: string
  interval: number
  agent: string
  enabled: boolean
}>({ name: '', description: '', prompt: '', mode: 'cron', cron: '', interval: 30, agent: 'default', enabled: true })
const timerDeleteTarget = ref<TimerTask | null>(null)
const timerBusyId = ref<string | null>(null)

/** 常用调度预设（选中的值直接写入 cron 输入框）。 */
const cronPresets = [
  { label: '每小时', value: '0 * * * *' },
  { label: '每 10 分钟', value: '*/10 * * * *' },
  { label: '每天 00:00', value: '0 0 * * *' },
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '工作日 09:00', value: '0 9 * * 1-5' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每月 1 日 09:00', value: '0 9 1 * *' },
  { label: '每月 15 日 09:00', value: '0 9 15 * *' },
]

/** 把 5 段 cron 表达式翻译成人话；无法解析时返回空串（不展示）。 */
function cronToText(expr: string): string {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return ''
  const [m, h, dom, mon, dow] = parts
  const isAll = (v: string) => v === '*'
  const everyN = (v: string) => (/^\*\/\d+$/.test(v) ? parseInt(v.slice(2), 10) : 0)
  // 时间部分
  let timeText = ''
  if (everyN(m) && isAll(h)) timeText = `每 ${everyN(m)} 分钟`
  else if (everyN(h) && m === '0') timeText = `每 ${everyN(h)} 小时`
  else if (isAll(m) && isAll(h)) timeText = '每分钟'
  else if (/^\d+$/.test(h) && /^\d+$/.test(m)) timeText = `${h.padStart(2, '0')}:${m.padStart(2, '0')}`
  else timeText = `${h === '*' ? '' : h}时${m === '*' ? '' : m}分`
  // 日期部分
  const dowNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  const parseDow = (v: string): string => {
    if (/^\d+$/.test(v)) return dowNames[parseInt(v, 10) % 7] || ''
    if (/^(\d)-(\d)$/.test(v)) {
      const [, a, b] = v.match(/^(\d)-(\d)$/)!
      return `${dowNames[parseInt(a, 10) % 7] || ''}至${dowNames[parseInt(b, 10) % 7] || ''}`
    }
    return ''
  }
  if (!isAll(dom) && !isAll(dow)) {
    const monText = isAll(mon) ? '' : `${mon} 月`
    return `每年${monText} ${dom} 日 ${timeText}`
  }
  if (!isAll(dom)) return `每月 ${dom} 日 ${timeText}`
  if (!isAll(dow)) return `每周${parseDow(dow) || dow} ${timeText}`
  if (!isAll(mon)) return `每年 ${mon} 月 ${timeText}`
  return `每天 ${timeText}`
}
const cronPreview = computed(() => (timerDraft.mode === 'cron' ? cronToText(timerDraft.cron) : ''))
// cron 输入即时校验 + AI 自然语言解释（debounce 调后端 /api/timers/explain-cron）
const cronExplain = ref('')
const cronExplainValid = ref(true)
let cronExplainTimer: number | undefined

async function refreshCronExplain(expr: string) {
  cronExplain.value = ''
  if (!expr.trim()) {
    cronExplainValid.value = true
    return
  }
  try {
    const res = await fetch('/api/timers/explain-cron', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cron: expr }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    cronExplainValid.value = !!data.valid
    cronExplain.value = data.description || ''
  } catch {
    // 后端不可用时按通过处理，保存侧仍有后端 400 拦截兜底
    cronExplainValid.value = true
    cronExplain.value = ''
  }
}

watch(() => timerDraft.cron, (val) => {
  if (cronExplainTimer !== undefined) window.clearTimeout(cronExplainTimer)
  cronExplainTimer = window.setTimeout(() => void refreshCronExplain(val), 500)
})
const timerAgentOptions = computed(() =>
  agentList.value
    .filter((a) => a.enabled !== false)
    .map((a) => ({ label: displayAgentName(a.name), value: a.name }))
)

/** 定时任务面板范围：true=显示全部（点击导航），false=只显示当前会话对应任务。 */
const timersScopeAll = ref(false)

/** 面板任务列表：点击「定时任务」显示全部；进入具体定时任务会话时只保留对应任务。 */
const panelTimers = computed(() => {
  let list = timers.value
  if (
    !timersScopeAll.value
    && activeConversationId.value
    && String(activeConversationId.value).startsWith('timer-')
  ) {
    list = list.filter((t) => t.sessionId === activeConversationId.value)
  }
  const q = timerSearch.value.trim().toLowerCase()
  if (q) {
    list = list.filter((t) =>
      (t.name || '').toLowerCase().includes(q) || (t.prompt || '').toLowerCase().includes(q)
    )
  }
  return list
})

async function loadTimers() {
  try {
    const res = await fetch('/api/timers')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    timers.value = await res.json()
  } catch (e: any) {
    message.error(e.message || String(e))
  }
}

/** 面板主动刷新：拉取任务最新状态；若正处于定时任务会话，同时静默刷新履历。 */
const timersRefreshing = ref(false)
async function refreshTimerPanel() {
  timersRefreshing.value = true
  try {
    await loadTimers()
    if (activeConversationId.value && String(activeConversationId.value).startsWith('timer-')) {
      await refreshTimerSessionQuiet(activeConversationId.value)
    }
  } finally {
    timersRefreshing.value = false
  }
}

/** 卡片刷新：刷新任务状态 + 该任务绑定会话的履历。 */
async function refreshTask(task: TimerTask) {
  timersRefreshing.value = true
  try {
    await loadTimers()
    if (task.sessionId) {
      if (activeConversationId.value === task.sessionId) {
        await selectConversation(task.sessionId)
      } else {
        await refreshTimerSessionQuiet(task.sessionId)
      }
    }
  } finally {
    timersRefreshing.value = false
  }
}

function timerStatusLabel(task: TimerTask): string {
  const s = task.lastStatus || 'never'
  switch (s) {
    case 'running': return t('timers.statusRunning')
    case 'success': return t('timers.statusSuccess')
    case 'failed': return t('timers.statusFailed')
    case 'skipped': return t('timers.statusSkipped')
    case 'cancelled': return t('timers.statusCancelled')
    default: return t('timers.statusNever')
  }
}

function timerStatusType(task: TimerTask): 'info' | 'success' | 'error' | 'warning' | 'default' {
  const s = task.lastStatus || 'never'
  if (s === 'success') return 'success'
  if (s === 'failed') return 'error'
  if (s === 'running') return 'info'
  if (s === 'skipped' || s === 'cancelled') return 'warning'
  return 'default'
}

function timerScheduleLabel(task: TimerTask): string {
  if (task.schedule) return `${t('timers.scheduleCron')}: ${task.schedule}`
  if (task.intervalMinutes) return `${t('timers.scheduleInterval')}: ${task.intervalMinutes}`
  return '--'
}

function timerAgentLabel(task: TimerTask): string {
  const name = (task.agent || 'default').trim() || 'default'
  return displayAgentName(name)
}

function formatTimerTime(ms?: number | null): string {
  if (!ms) return t('timers.never')
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 执行耗时：秒 / 分秒。 */
function formatDuration(startMs: number, endMs: number): string {
  const s = Math.max(0, Math.round((endMs - startMs) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m${s % 60 ? `${s % 60}s` : ''}`
}

async function toggleTimer(task: TimerTask) {
  const next = !task.enabled
  try {
    const res = await fetch(`/api/timers/${task.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    task.enabled = next
  } catch (e: any) {
    message.error(e.message || String(e))
  }
}

function openTimerEdit(task?: TimerTask) {
  timerEditTarget.value = task || null
  timerDraft.name = task?.name || ''
  timerDraft.description = task?.description || ''
  timerDraft.prompt = task?.prompt || ''
  timerDraft.mode = task?.schedule ? 'cron' : task?.intervalMinutes ? 'interval' : 'cron'
  timerDraft.cron = task?.schedule || ''
  timerDraft.interval = task?.intervalMinutes ?? 30
  timerDraft.agent = task?.agent || currentAgentName.value || 'default'
  timerDraft.enabled = task?.enabled ?? true
  timerEditVisible.value = true
}

async function saveTimer() {
  if (!timerDraft.name.trim() || !timerDraft.prompt.trim()) {
    message.warning(t('timers.name'))
    return
  }
  if (timerDraft.mode === 'cron') {
    if (!timerDraft.cron.trim()) {
      message.warning(t('timers.scheduleCron'))
      return
    }
    if (!cronExplainValid.value) {
      message.warning(t('timers.cronInvalid'))
      return
    }
  }
  const payload = {
    name: timerDraft.name.trim(),
    description: timerDraft.description.trim(),
    prompt: timerDraft.prompt.trim(),
    enabled: timerDraft.enabled,
    agent: timerDraft.agent || 'default',
    schedule: timerDraft.mode === 'cron' ? timerDraft.cron.trim() : null,
    intervalMinutes: timerDraft.mode === 'interval' ? timerDraft.interval : null,
  }
  try {
    const isEdit = !!timerEditTarget.value
    const res = await fetch(isEdit ? `/api/timers/${timerEditTarget.value!.id}` : '/api/timers', {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => null)
      throw new Error((data && data.detail) || `HTTP ${res.status}`)
    }
    message.success(t('timers.save'))
    timerEditVisible.value = false
    const saved = await res.json().catch(() => null)
    await loadTimers()
    // 新建任务：立即刷新会话履历并切到对应会话
    if (!isEdit && saved?.sessionId) {
      await selectConversation(saved.sessionId)
    }
  } catch (e: any) {
    message.error(e.message || String(e))
  }
}

async function confirmDeleteTimer() {
  const task = timerDeleteTarget.value
  if (!task) return
  timerDeleteTarget.value = null
  try {
    const res = await fetch(`/api/timers/${task.id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await loadTimers()
    // 双向联动：删除任务时，一并删除绑定会话
    if (task.sessionId) {
      conversations.value = conversations.value.filter((c) => c.id !== task.sessionId)
      if (activeConversationId.value === task.sessionId) {
        activeConversationId.value = conversations.value[0]?.id ?? ''
      }
      void deleteConversationRemote(task.sessionId)
    }
  } catch (e: any) {
    message.error(e.message || String(e))
  }
}

async function runTimer(task: TimerTask) {
  if (timerBusyId.value) {
    message.warning(t('timers.busy'))
    return
  }
  timerBusyId.value = task.id
  try {
    const res = await fetch(`/api/timers/${task.id}/run`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    message.success(t('timers.runDone'))
    await loadTimers()
    // 当前正显示该任务绑定会话时，重新拉取让新履历立即可见
    if (activeConversationId.value === task.sessionId) {
      selectConversation(task.sessionId)
    }
  } catch (e: any) {
    message.error(e.message || String(e))
  } finally {
    timerBusyId.value = null
  }
}

/** 执行记录点击：跳转到对应会话消息（消息 id 由后端透传，新记录才可跳）。 */
async function timerRunClicked(task: TimerTask, run: TimerRun) {
  if (!run.assistantMessageId || !task.sessionId) return
  const target = run.assistantMessageId
  if (activeConversationId.value !== task.sessionId) {
    selectConversation(task.sessionId)
    // selectConversation 内部异步拉取会话，轮询等待目标消息出现
    const deadline = Date.now() + 8000
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 300))
      const conv = conversations.value.find((c) => c.id === task.sessionId)
      if (conv?.messages.some((m) => m.id === target)) break
    }
  }
  await nextTick()
  const el = document.querySelector(`[data-message-id="${target}"]`)
  if (el) {
    ;(el as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'center' })
    ;(el as HTMLElement).classList.add('message--highlight')
    setTimeout(() => (el as HTMLElement).classList.remove('message--highlight'), 2000)
  } else {
    // 目标消息不在当前渲染窗口（load-earlier 未展开）：滚动到会话底部
    const tl = timeline.value
    if (tl) tl.scrollTo({ top: tl.scrollHeight, behavior: 'smooth' })
  }
}

/** Start the external moofile_base backend and refresh the panel. */
const knowledgeStarting = ref(false)
async function startKnowledge() {
  if (knowledgeStarting.value) return
  knowledgeStarting.value = true
  try {
    const res = await startKnowledgeService()
    if (res.started || res.already) {
      message.success(actionLabels.value.knowledgeStart)
      await loadKnowledge()
    }
  } catch (e: any) {
    message.error(e.message || String(e))
  } finally {
    knowledgeStarting.value = false
  }
}

/** Stop the external moofile_base backend and refresh the panel. */
const knowledgeStopping = ref(false)
async function stopKnowledge() {
  if (knowledgeStopping.value) return
  knowledgeStopping.value = true
  try {
    const res = await stopKnowledgeService()
    if (res.ok) {
      message.success(actionLabels.value.knowledgeStop)
      await loadKnowledge()
    }
  } catch (e: any) {
    message.error(e.message || String(e))
  } finally {
    knowledgeStopping.value = false
  }
}

/** Skills: filter by name/description, enabled first. */
const filteredSkills = computed(() => {
  const q = panelQuery.value.trim().toLowerCase()
  const list = skillList.value.filter((s) =>
    !q
      || s.name?.toLowerCase().includes(q)
      || (s.description || '').toLowerCase().includes(q)
  )
  return [...list].sort((a, b) => (b.enabled ? 1 : 0) - (a.enabled ? 1 : 0))
})

/** Agents: filter by name / description (same pattern as skills). */
const filteredAgents = computed(() => {
  const q = panelQuery.value.trim().toLowerCase()
  const list = agentList.value.filter((a) =>
    !q
      || a.name?.toLowerCase().includes(q)
      || (a.description || '').toLowerCase().includes(q)
  )
  return [...list].sort((a, b) => (b.enabled !== false ? 1 : 0) - (a.enabled !== false ? 1 : 0))
})

/** Built-in tools: filter by name. */
const filteredBuiltinTools = computed(() => {
  const q = panelQuery.value.trim().toLowerCase()
  if (!q) return builtinTools.value
  return builtinTools.value.filter((t) => (t.name || '').toLowerCase().includes(q))
})

/** MCP servers: filter by server/description/tool name (backend sorts enabled first). */
const filteredToolList = computed(() => {
  const q = panelQuery.value.trim().toLowerCase()
  if (!q) return toolList.value
  return toolList.value.filter((t) => {
    if ((t.name || '').toLowerCase().includes(q)) return true
    if ((t.description || '').toLowerCase().includes(q)) return true
    return (t.tools || []).some((x: any) => (x.name || '').toLowerCase().includes(q))
  })
})
const AGENT_STORAGE_KEY = 'currentAgentName'
function normalizeAgentName(name: string) {
  return name.trim().toLowerCase() === 'chat' ? 'Chat' : name
}
const currentAgentName = ref(normalizeAgentName(localStorage.getItem(AGENT_STORAGE_KEY) || ''))

/** Display name: capitalize the first letter for the UI (storage keys stay untouched). */
function displayAgentName(name: string) {
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : name
}

/** Drop the current ACP subprocess connection so the next send reconnects
 *  with the new agent name in the WS URL (each agent maps to a separate
 *  subprocess). */
function resetSocketForAgentChange() {
  if (ws.value) {
    ws.value.close()
    ws.value = null
  }
  socketPromise.value = null
  connected.value = false
  initialized.value = false
  initializedSessions.clear()
}

/** New conversations always start on the default agent. */
const agentDropdownOptions = computed(() =>
  agentList.value
    .filter((agent) => agent.enabled !== false)
    .map((agent) => ({
      key: agent.name,
      label: () =>
        h('div', { style: 'display:flex;align-items:center;gap:3px;font-size:13px;padding:2px 0;' }, [
          h(NIcon, { component: SparklesOutline, style: 'font-size:15px;color:var(--accent,#2563eb);margin-right:2px;' }),
          h('span', displayAgentName(agent.name))
        ])
    }))
)

/** Delete an agent (md file + agents.json entry + references). default is protected. */
async function deleteAgent(agent: any) {
  try {
    await apiManage(`/api/manage/agents/${agent.name}`, 'DELETE')
    if (currentAgentName.value === agent.name) {
      currentAgentName.value = 'default'
      localStorage.setItem(AGENT_STORAGE_KEY, 'default')
    }
    await fetchManageData()
  } catch (e: any) {
    message.error(e.message)
  }
}
function selectAgent(key: string) {
  const agentName = normalizeAgentName(key)
  currentAgentName.value = agentName
  localStorage.setItem(AGENT_STORAGE_KEY, agentName)
  const conversation = currentConversation.value
  if (conversation) {
    conversation.agent = agentName
    conversation.updatedAt = Date.now()
    void saveConversation(conversation)
  }
  fetchManageData() // tools panel state follows the current agent
  resetSocketForAgentChange() // each agent maps to a separate ACP subprocess
}

async function fetchManageData() {
  try {
    const agent = currentAgentName.value || 'default'
    const [skills, agents, builtins] = await Promise.all([
      fetch(`/api/manage/skills?agent=${encodeURIComponent(agent)}`).then((r) => r.json()),
      fetch('/api/manage/agents').then((r) => r.json()),
      fetch('/api/manage/builtin-tools').then((r) => r.json())
    ])
    skillList.value = Array.isArray(skills) ? skills : []
    agentList.value = Array.isArray(agents) ? agents : []
    builtinTools.value = Array.isArray(builtins) ? builtins : []
    // fall back to the first available agent when the stored one is gone
    if (currentAgentName.value !== 'Chat' && !agentList.value.some((a) => a.name === currentAgentName.value)) {
      const first = agentList.value.find((a) => a.enabled !== false)
      currentAgentName.value = normalizeAgentName(first?.name || '')
    }
    // tool enablement is queried per agent (agents.json tools.mcp_tools)
    const toolsAgent = currentAgentName.value || 'default'
    const tools = await fetch(`/api/manage/tools?agent=${toolsAgent}`).then((r) => r.json())
    toolList.value = Array.isArray(tools) ? tools : []
    await loadKnowledge()
  } catch {
    // keep the list empty when the backend is unavailable
  }
}

async function apiManage(path: string, method = 'GET', body?: unknown) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`)
  return data
}

/** Expand/collapse an MCP server group in the tools panel */
const expandedToolGroups = ref<string[]>([])
function toggleToolGroup(name: string) {
  const idx = expandedToolGroups.value.indexOf(name)
  if (idx >= 0) expandedToolGroups.value.splice(idx, 1)
  else expandedToolGroups.value.push(name)
}
// file preview modal (clickable absolute-path links in messages)
const filePreviewVisible = ref(false)
const filePreviewLoading = ref(false)
const filePreview = ref<{ name: string; path: string; content: string } | null>(null)
async function openFilePreview(path: string) {
  filePreviewVisible.value = true
  filePreviewLoading.value = true
  filePreview.value = null
  try {
    const r = await fetch(`/api/preview-file?path=${encodeURIComponent(path)}`)
    if (!r.ok) {
      let detail = '预览失败'
      try { detail = (await r.json()).detail || detail } catch { /* ignore */ }
      throw new Error(detail)
    }
    filePreview.value = await r.json()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    filePreviewLoading.value = false
  }
}
// new skill modal
const skillModalVisible = ref(false)
const skillEditing = ref(false)
const skillForm = ref({ name: '', description: '', content: '' })
async function submitSkill() {
  try {
    if (skillEditing.value) {
      await apiManage(`/api/manage/skills/${skillForm.value.name}`, 'PUT', skillForm.value)
    } else {
      await apiManage('/api/manage/skills', 'POST', skillForm.value)
    }
    skillModalVisible.value = false
    skillEditing.value = false
    skillForm.value = { name: '', description: '', content: '' }
    await fetchManageData()
  } catch (e: any) {
    message.error(e.message)
  }
}
function openSkillEdit(item?: any) {
  skillEditing.value = !!item
  skillForm.value = item
    ? { name: item.name, description: item.description, content: '' }
    : { name: '', description: '', content: '' }
  if (item) {
    fetch(`/api/manage/skills/${item.name}`)
      .then((r) => r.json())
      .then((data) => { skillForm.value.content = data.content || '' })
      .catch(() => {})
  }
  skillModalVisible.value = true
}

async function deleteSkill(skill: any) {
  if (!window.confirm(actionLabels.value.confirmDelete)) return
  try {
    await apiManage(`/api/manage/skills/${encodeURIComponent(skill.name)}`, 'DELETE')
    await fetchManageData()
  } catch (e: any) {
    message.error(e.message)
  }
}

// new tool modal
const toolModalVisible = ref(false)
const toolEditing = ref(false)
const toolForm = ref({ name: '', description: '', command: '', args: '', cwd: '', env: '' })
function openToolEdit(item?: any) {
  toolEditing.value = !!item
  toolForm.value = item
    ? {
        name: item.name,
        description: item.description || '',
        command: item.command || '',
        args: Array.isArray(item.args) ? item.args.join('\n') : '',
        cwd: item.cwd || '',
        env: item.env && typeof item.env === 'object'
          ? Object.entries(item.env).map(([key, value]) => `${key}=${value}`).join('\n')
          : '',
      }
    : { name: '', description: '', command: '', args: '', cwd: '', env: '' }
  toolModalVisible.value = true
}
async function submitTool() {
  try {
    const payload = {
      name: toolForm.value.name,
      description: toolForm.value.description,
      command: toolForm.value.command,
      args: toolForm.value.args.split('\n').map((s) => s.trim()).filter(Boolean),
      cwd: toolForm.value.cwd || undefined,
      env: Object.fromEntries(
        toolForm.value.env.split('\n').map((s) => s.trim()).filter(Boolean).map((line) => {
          const i = line.indexOf('=')
          return i > 0 ? [line.slice(0, i).trim(), line.slice(i + 1).trim()] : [line, '']
        })
      )
    }
    if (toolEditing.value) {
      await apiManage(`/api/manage/tools/${toolForm.value.name}`, 'PUT', payload)
    } else {
      await apiManage('/api/manage/tools', 'POST', payload)
    }
    toolModalVisible.value = false
    toolEditing.value = false
    toolForm.value = { name: '', description: '', command: '', args: '', cwd: '', env: '' }
    await fetchManageData()
  } catch (e: any) {
    message.error(e.message)
  }
}

// new agent modal
const agentModalVisible = ref(false)
const agentEditing = ref(false)
interface AgentToolConfig {
  inner_tools: string[]
  mcp_tools: Array<{ server: string; tools: string[] }>
}
interface AgentForm {
  name: string
  description: string
  body: string
  subagents: string[]
  skills: string[]
  tools: AgentToolConfig
  knowledge: string[]
}
const emptyAgentForm = (): AgentForm => ({
  name: '',
  description: '',
  body: '',
  subagents: [],
  skills: [],
  tools: { inner_tools: [], mcp_tools: [] },
  knowledge: [],
})
const agentForm = ref<AgentForm>(emptyAgentForm())
const agentKnowledgeOptions = ref<KnowledgeDb[]>([])
const expandedAgentToolNodes = ref<string[]>(['mcp_server'])
const agentSkillOptions = computed(() =>
  skillList.value.map((s) => ({ label: s.name, value: s.name }))
)
const agentKnowledgeChoices = computed(() => {
  const rows = [...agentKnowledgeOptions.value]
  for (const id of agentForm.value.knowledge) {
    if (!rows.some((item) => item.id === id)) {
      rows.push({
        id,
        name: id,
        type: 'vector',
        recordCount: 0,
        documentCount: 0,
        enabled: true,
      })
    }
  }
  return rows
})
function agentSkillNames(agent: any): string[] {
  return Array.isArray(agent.skills) ? agent.skills : []
}
function agentToolNames(agent: any): string[] {
  if (Array.isArray(agent.tools)) return agent.tools
  const innerTools = Array.isArray(agent.tools?.inner_tools) ? agent.tools.inner_tools : []
  const mcpTools = Array.isArray(agent.tools?.mcp_tools)
    ? agent.tools.mcp_tools.flatMap((server: any) =>
        (server.tools || []).map((tool: string) => `${server.server}/${tool}`)
      )
    : []
  return [...innerTools, ...mcpTools]
}
/** Sub-agent options: other enabled agents (exclude the one being edited). */
const agentSubagentOptions = computed(() =>
  agentList.value
    .filter((a) => a.enabled !== false && a.name !== agentForm.value.name)
    .map((a) => ({ label: displayAgentName(a.name), value: a.name }))
)
function setStringSelection(values: string[], value: string, checked: boolean): string[] {
  return checked
    ? [...new Set([...values, value])]
    : values.filter((item) => item !== value)
}
function setAgentSubagent(value: string, checked: boolean) {
  agentForm.value.subagents = setStringSelection(agentForm.value.subagents, value, checked)
}
function setAgentSkill(value: string, checked: boolean) {
  agentForm.value.skills = setStringSelection(agentForm.value.skills, value, checked)
}
function setAgentKnowledge(value: string, checked: boolean) {
  agentForm.value.knowledge = setStringSelection(agentForm.value.knowledge, value, checked)
}
function setAgentBuiltinTool(value: string, checked: boolean) {
  agentForm.value.tools.inner_tools = setStringSelection(
    agentForm.value.tools.inner_tools,
    value,
    checked
  )
}
function isAgentBuiltinGroupChecked(): boolean {
  return builtinTools.value.length > 0
    && builtinTools.value.every((tool) => agentForm.value.tools.inner_tools.includes(tool.name))
}
function isAgentBuiltinGroupIndeterminate(): boolean {
  const selectedCount = agentForm.value.tools.inner_tools.length
  return selectedCount > 0 && !isAgentBuiltinGroupChecked()
}
function setAgentBuiltinGroup(checked: boolean) {
  agentForm.value.tools.inner_tools = checked
    ? builtinTools.value.map((tool) => tool.name)
    : []
}
function isAgentToolNodeExpanded(key: string): boolean {
  return expandedAgentToolNodes.value.includes(key)
}
function toggleAgentToolNode(key: string) {
  expandedAgentToolNodes.value = isAgentToolNodeExpanded(key)
    ? expandedAgentToolNodes.value.filter((item) => item !== key)
    : [...expandedAgentToolNodes.value, key]
}
function selectedAgentMcpTools(server: string): string[] {
  return agentForm.value.tools.mcp_tools.find((entry) => entry.server === server)?.tools || []
}
function setAgentMcpTools(server: string, selected: string[]) {
  const entries = agentForm.value.tools.mcp_tools.filter((entry) => entry.server !== server)
  if (selected.length) entries.push({ server, tools: [...new Set(selected)] })
  agentForm.value.tools.mcp_tools = entries
}
function setAgentMcpTool(server: string, tool: string, checked: boolean) {
  setAgentMcpTools(
    server,
    setStringSelection(selectedAgentMcpTools(server), tool, checked)
  )
}
function setAgentMcpServer(server: any, checked: boolean) {
  const names = (server.tools || [])
    .map((tool: any) => tool.name)
    .filter((name: unknown): name is string => typeof name === 'string' && !!name)
  setAgentMcpTools(server.name, checked ? names : [])
}
function isAgentMcpServerChecked(server: any): boolean {
  const available = (server.tools || []).map((tool: any) => tool.name).filter(Boolean)
  const selected = selectedAgentMcpTools(server.name)
  return available.length > 0 && available.every((name: string) => selected.includes(name))
}
function isAgentMcpServerIndeterminate(server: any): boolean {
  const selectedCount = selectedAgentMcpTools(server.name).length
  return selectedCount > 0 && !isAgentMcpServerChecked(server)
}
async function loadAgentKnowledgeOptions(agentName: string) {
  try {
    agentKnowledgeOptions.value = await fetchKnowledgeDatabases(agentName || 'default')
  } catch {
    agentKnowledgeOptions.value = [...knowledgeList.value]
  }
}
async function submitAgent() {
  try {
    if (agentEditing.value) {
      await apiManage(`/api/manage/agents/${agentForm.value.name}`, 'PUT', agentForm.value)
    } else {
      await apiManage('/api/manage/agents', 'POST', agentForm.value)
    }
    agentModalVisible.value = false
    agentEditing.value = false
    agentForm.value = emptyAgentForm()
    await fetchManageData()
  } catch (e: any) {
    message.error(e.message)
  }
}
function openAgentEdit(item?: any) {
  agentEditing.value = !!item
  expandedAgentToolNodes.value = ['mcp_server']
  agentForm.value = item
    ? {
        name: item.name,
        description: item.description || '',
        body: item.body || '',
        subagents: item.subagents || [],
        skills: item.skills || [],
        tools: {
          inner_tools: [...(item.tools?.inner_tools || [])],
          mcp_tools: (item.tools?.mcp_tools || []).map((entry: any) => ({
            server: entry.server || entry.serverInfo?.name,
            tools: (entry.tools || [])
              .map((tool: any) => typeof tool === 'string' ? tool : tool.name)
              .filter(Boolean),
          })).filter((entry: any) => entry.server),
        },
        knowledge: [...(item.knowledge || [])],
      }
    : {
        ...emptyAgentForm(),
        tools: {
          inner_tools: builtinTools.value.map((tool) => tool.name),
          mcp_tools: [],
        },
      }
  void loadAgentKnowledgeOptions(item?.name || currentAgentName.value || 'default')
  agentModalVisible.value = true
}

onMounted(() => {
  fetchManageData()
  refreshSystemCommands()
})

function notifyComingSoon(_feature: string) {
  message.info(actionLabels.value.comingSoon, { duration: 3000 })
}

function conversationSessionId(conversation: Conversation): string {
  return conversation.agentSessionId ?? conversation.id
}

async function fetchAcpMessages(sessionId: string): Promise<any[] | null> {
  try {
    const resp = await fetch(`/api/context/${encodeURIComponent(sessionId)}/messages`)
    const data = await resp.json()
    return data?.ok ? (data.messages ?? []) : null
  } catch {
    return null
  }
}

// Resolve a display message to its ACP message id by role + occurrence order.
// user -> k-th HumanMessage; assistant -> k-th AIMessage that carries text
// (tool-call AIMessages are skipped, mirroring the table's message layout).
function resolveAcpMessageId(role: string, orderIndex: number, acpMessages: any[]): string | null {
  let seen = -1
  for (const m of acpMessages) {
    const kw = m?.kwargs ?? {}
    const type = String(kw.type ?? '')
    if (role === 'user') {
      if (type === 'human') {
        seen += 1
        if (seen === orderIndex) return String(kw.id ?? m.id ?? '') || null
      }
    } else if (role === 'assistant') {
      if (type === 'ai' && !kw.tool_calls && kw.content != null && String(kw.content) !== '') {
        seen += 1
        if (seen === orderIndex) return String(kw.id ?? m.id ?? '') || null
      }
    }
  }
  return null
}

function deleteMessage(item: { id: string }) {
  openMenuId.value = null
  const conversation = currentConversation.value
  if (!conversation) return
  const index = conversation.messages.findIndex((entry) => entry.id === item.id)
  if (index < 0) return
  const msg = conversation.messages[index]
  void (async () => {
    // Try to remove from the real context table (+ sqlite mirror) first.
    const sessionId = conversationSessionId(conversation)
    const acpMessages = await fetchAcpMessages(sessionId)
    const orderIndex = conversation.messages.slice(0, index).filter((m) => m.role === msg.role).length
    if (acpMessages) {
      const acpId = resolveAcpMessageId(msg.role, orderIndex, acpMessages)
      if (acpId) {
        try {
          const resp = await fetch(`/api/context/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(acpId)}`, { method: 'DELETE' })
          const data = await resp.json()
          if (data?.ok) {
            conversation.messages.splice(index, 1)
            conversation.updatedAt = Date.now()
            void saveConversation(conversation)
            return
          }
        } catch { /* fall through to local-only */ }
      }
    }
    // Fallback: legacy local-only removal + notice.
    conversation.messages.splice(index, 1)
    conversation.updatedAt = Date.now()
    void saveConversation(conversation)
    message.info(actionLabels.value.deleteLocalOnly)
  })()
}

const openMenuId = ref<string | null>(null)
function toggleMessageMenu(id: string) {
  openMenuId.value = openMenuId.value === id ? null : id
}
function closeMessageMenu() {
  openMenuId.value = null
}

// message editing (text only, does not re-run)
const editModalVisible = ref(false)
const editTargetId = ref<string | null>(null)
const editText = ref('')

function isMessageBusy(item: ChatMessage) {
  return item.role === 'assistant' &&
    (item.status === 'pending' || item.status === 'streaming' || item.status === 'waiting_permission')
}

function openEditMessage(item: ChatMessage) {
  editTargetId.value = item.id
  editText.value = item.role === 'user' ? item.text : (item.finalText ?? '')
  openMenuId.value = null
  editModalVisible.value = true
}

async function saveEditMessage() {
  const conversation = currentConversation.value
  if (!conversation || !editTargetId.value) return
  const index = conversation.messages.findIndex((entry) => entry.id === editTargetId.value)
  if (index < 0) return
  const msg = conversation.messages[index]
  const text = editText.value
  // Push the edit to the real context table (+ sqlite mirror) first.
  const sessionId = conversationSessionId(conversation)
  const acpMessages = await fetchAcpMessages(sessionId)
  const orderIndex = conversation.messages.slice(0, index).filter((m) => m.role === msg.role).length
  let synced = false
  if (acpMessages) {
    const acpId = resolveAcpMessageId(msg.role, orderIndex, acpMessages)
    if (acpId) {
      try {
        const resp = await fetch(`/api/context/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(acpId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text })
        })
        const data = await resp.json()
        synced = Boolean(data?.ok)
      } catch { /* ignore; keep local update below */ }
    }
  }
  // Local update always, so the UI reflects the edit. If sync failed, the next
  // prompt simply keeps the table content unchanged (notified below).
  if (msg.role === 'user') {
    msg.text = text
  } else {
    msg.finalText = text
    const textSegment = msg.segments.find((segment) => segment.type === 'text')
    if (textSegment) {
      textSegment.text = text
      delete textSegment.html // 编辑后回到 markdown 渲染，注音由下一次完成时重新触发
    } else {
      msg.segments.push({ id: createId('seg'), type: 'text', text })
    }
  }
  conversation.updatedAt = Date.now()
  void saveConversation(conversation)
  editModalVisible.value = false
  editTargetId.value = null
  if (!synced) message.info(actionLabels.value.editLocalOnly)
}

function moveConversationToTop(conversation: Conversation) {
  conversations.value = [conversation, ...conversations.value.filter((item) => item.id !== conversation.id)]
}

function websocketUrl() {
  if (import.meta.env.VITE_ACP_WS_URL) return import.meta.env.VITE_ACP_WS_URL
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const agent = currentAgentName.value || 'default'
  const model = currentModel.value || ''
  const modelParam = model ? `&model=${encodeURIComponent(model)}` : ''
  const wsParam = webSearchOn.value ? '&web_search=1' : ''
  const roleParam = systemOverrideName.value ? `&role=${encodeURIComponent(systemOverrideName.value)}` : ''
  const mode = agent === 'Chat' ? 'chat' : 'task'
  return `${protocol}//${window.location.host}/acp-ws?agent=${encodeURIComponent(agent)}&mode=${mode}${modelParam}${wsParam}${roleParam}`
}

function connect() {
  if (connected.value && ws.value?.readyState === WebSocket.OPEN) return Promise.resolve()
  if (socketPromise.value) return socketPromise.value

  socketPromise.value = new Promise((resolve, reject) => {
    const socket = new WebSocket(websocketUrl())
    let opened = false
    ws.value = socket
    socket.onopen = () => {
      opened = true
      if (ws.value !== socket) {
        socket.close()
        return
      }
      connected.value = true
      socketPromise.value = null
      resolve()
    }
    socket.onerror = () => {
      if (ws.value === socket) socketPromise.value = null
      reject(new Error(t('connectionFailed')))
    }
    socket.onclose = () => {
      if (!opened) reject(new Error(t('connectionFailed')))
      if (ws.value !== socket) return
      connected.value = false
      initialized.value = false
      initializedSessions.clear()
      ws.value = null
      pendingRequests.forEach(({ reject }) => reject(new Error(t('connectionClosed'))))
      pendingRequests.clear()
      socketPromise.value = null
    }
    socket.onmessage = (event) => handleAcpMessage(event.data)
  })
  return socketPromise.value
}

function callAcp(method: string, params: Record<string, unknown>) {
  return new Promise<any>((resolve, reject) => {
    const socket = ws.value
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      reject(new Error(t('connectionNotReady')))
      return
    }
    const id = requestId.value++
    pendingRequests.set(id, { resolve, reject })
    socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
  })
}

function respondAcp(id: number | string, result: Record<string, unknown>) {
  ws.value?.send(JSON.stringify({ jsonrpc: '2.0', id, result }))
}

async function initialize() {
  if (initialized.value) return
  await callAcp('initialize', {
    protocolVersion: 2,
    clientCapabilities: {},
    clientInfo: { name: 'deepagents-acp-ui', version: '1.0.0' }
  })
  initialized.value = true
}

function formatAcpError(error: any): string {
  const message = error?.message ?? t('requestFailed')
  const data = error?.data
  if (!data) return message
  if (typeof data === 'string') return `${message}: ${data}`
  if (data.details != null) return `${message}: ${data.details}`
  const rest = Object.entries(data)
    .filter(([key, value]) => value != null && key !== 'message')
    .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(', ')
  return rest ? `${message}: ${rest}` : message
}

function handleAcpMessage(raw: string) {
  let payload: any
  try {
    payload = JSON.parse(raw)
    console.log('Received raw ACP message:', raw)
  } catch {
    return
  }
  if (payload.id !== undefined && payload.method) {
    if (payload.method === 'session/request_permission') {
      const toolCall = payload.params?.toolCall ?? {}
      permissionRequest.value = {
        id: payload.id,
        toolName: toolCall.title ?? toolCall.toolName ?? toolCall.toolCallId ?? '工具调用',
        rawInput: toolCall.rawInput ?? {}
      }
      const active = activeAssistantMessage()
      if (active) active.status = 'waiting_permission'
    }
    return
  }
  if (payload.id !== undefined && pendingRequests.has(payload.id)) {
    const request = pendingRequests.get(payload.id)!
    pendingRequests.delete(payload.id)
    if (payload.error) request.reject(new Error(formatAcpError(payload.error)))
    else request.resolve(payload.result)
    return
  }
  if (payload.method === 'session/update') handleSessionUpdate(payload.params?.update ?? {})
}

function activeAssistantMessage() {
  const items = currentConversation.value?.messages ?? []
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].role === 'assistant') return items[index] as AssistantMessage
  }
  return undefined
}

function appendTextSegment(assistant: AssistantMessage, text: string) {
  if (!text) return
  assistant.finalText += text
  const last = assistant.segments.at(-1)
  if (last?.type === 'text') last.text += text
  else assistant.segments.push({ id: createId('text'), type: 'text', text })
}

function appendThoughtSegment(assistant: AssistantMessage, text: string) {
  if (!text) return
  const last = assistant.segments.at(-1)
  // Only append to the previous thought segment if it's still open (not done).
  // After a tool call the previous thought is closed; the next reasoning round
  // must start a fresh card, otherwise new reasoning text piles onto a stale,
  // collapsed card and the UI appears frozen.
  if (last?.type === 'thought' && !last.done) {
    ;(last as any)._buf = ((last as any)._buf || '') + text
    if ((last as any)._flushTimer) return
    ;(last as any)._flushTimer = window.setTimeout(() => {
      ;(last as any)._flushTimer = undefined
      const buf = (last as any)._buf
      if (buf) { last.text += buf; ;(last as any)._buf = '' }
    }, 60)
  }
  else assistant.segments.push({ id: createId('thought'), type: 'thought', text, done: false })
}

/** Mark the latest thought segment as finished (called when text/tool chunks start). */
function closeLastThought(assistant: AssistantMessage) {
  for (let i = assistant.segments.length - 1; i >= 0; i--) {
    const seg = assistant.segments[i]
    if (seg.type === 'thought') {
      // Flush any buffered reasoning text before marking done.
      const pending = (seg as any)._buf
      if (pending) { seg.text += pending; ;(seg as any)._buf = '' }
      if ((seg as any)._flushTimer) { clearTimeout((seg as any)._flushTimer); ;(seg as any)._flushTimer = undefined }
      seg.done = true
      break
    }
  }
}

function ensurePlanSegment(assistant: AssistantMessage) {
  if (!assistant.segments.some((segment) => segment.type === 'plan')) {
    assistant.segments.push({ id: createId('plan'), type: 'plan' })
  }
}

function ensureToolSegment(assistant: AssistantMessage, toolId: string) {
  if (!assistant.segments.some((segment) => segment.type === 'tool' && segment.toolId === toolId)) {
    assistant.segments.push({ id: createId('tool'), type: 'tool', toolId })
  }
}

function findTool(process: ExecutionProcess, toolId: string) {
  return process.toolCalls.find((tool) => tool.id === toolId)
}

function segmentTool(assistant: AssistantMessage, segment: ToolSegment): ToolCallEntry {
  return findTool(assistant.process, segment.toolId)
    ?? { id: segment.toolId, name: 'Run', status: 'running' }
}

function textFromContent(content: any): string {
  if (!content) return ''
  if (typeof content === 'string') return content
  if (Array.isArray(content)) return content.map(textFromContent).filter(Boolean).join('\n')
  if (typeof content.text === 'string') return content.text
  if (content.text && typeof content.text.value === 'string') return content.text.value // 兼容特殊结构
  if (content.content) return textFromContent(content.content)
  return ''
}

// ACP tool_call events only carry a title
const BUILTIN_TOOL_NAMES = new Set(['read_file', 'write_file', 'edit_file', 'glob', 'grep', 'ls', 'delete', 'task', 'execute'])
function inferToolName(update: any): string {
  const title = String(update.title ?? '').trim()
  const kind = String(update.kind ?? '')
  const m = title.match(/^(Read|Write|Edit|Execute)\s*[:\s]\s*(.*)$/s)
  if (m) return m[1] === 'Execute' ? 'Run' : m[1]
  if (BUILTIN_TOOL_NAMES.has(title)) return title
  if (kind === 'read') return 'Read'
  if (kind === 'edit') return 'Edit'
  if (kind === 'search') return 'Search'
  if (kind === 'execute') return 'Run'
  return ''
}

function toolDisplayTitle(update: any): string {
  const title = String(update.title ?? '').trim()
  const badge = inferToolName(update)
  const m = title.match(/^(Read|Write|Edit|Execute)\s*[:\s]\s*(.*)$/s)
  let rest = m ? m[2].trim() : title
  if (rest && (rest === badge || BUILTIN_TOOL_NAMES.has(rest))) rest = ''
  return rest
}

function normalizeToolStatus(status?: string): ToolStatus {
  if (status === 'failed' || status === 'error') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  if (status === 'waiting_permission') return 'waiting_permission'
  if (status === 'completed' || status === 'success') return 'completed'
  return 'running'
}

// 内部安全获取当前运行消息的 helper
function getReactiveAssistant(): AssistantMessage | undefined {
  const items = currentConversation.value?.messages ?? []
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (item.role === 'assistant') {
      // 如果是通过数组拿到的，通常已经是 Proxy 对象了
      return item as AssistantMessage
    }
  }
  return undefined
}

function handleSessionUpdate(update: any) {
  // 1. 全局状态拦截检查
  if (restoringHistory.value) {
    console.warn('[ACP Drop] 忽略更新：正在恢复历史记录中 (restoringHistory = true)')
    return
  }
  if (cancelRequested) {
    console.warn('[ACP Drop] 忽略更新：用户已取消当前请求 (cancelRequested = true)')
    return
  }
  
  // 2. 获取当前活动的 assistant 消息
  const assistant = getReactiveAssistant()
  if (!assistant) {
    console.warn('[ACP Drop] 忽略更新：未找到 activeAssistantMessage。当前消息数组长度:', currentConversation.value?.messages?.length)
    return
  }
  
  // 3. 检查消息状态，防止晚到的包污染已结束的会话
  if (assistant.status === 'cancelled' || assistant.status === 'completed' || assistant.status === 'failed') {
    // 如果是在被取消后刚刚切回来的些许残留 chunk，静默丢弃，不要刷屏 console
    return
  }

  const process = assistant.process
  switch (update.sessionUpdate) {
    case 'agent_message_chunk': {
      const text = textFromContent(update.content)
      if (text) {
        assistant.status = 'streaming'
        closeLastThought(assistant)
        appendTextSegment(assistant, text)
        scheduleContextRefresh()
      }
      break
    }
    case 'agent_thought_chunk': {
      const text = textFromContent(update.content)
      if (text) {
        assistant.status = 'streaming'
        appendThoughtSegment(assistant, text)
      }
      break
    }
    case 'plan': {
      process.plan = (update.entries ?? []).map((entry: any) => ({
        title: entry.title ?? entry.task ?? entry.content ?? '执行步骤',
        status: entry.status ?? 'pending'
      }))
      if (process.plan.length) ensurePlanSegment(assistant)
      break
    }
    case 'tool_call':
    case 'tool_call_start': {
      const id = update.toolCallId ?? createId('tool')
      assistant.status = 'streaming'
      closeLastThought(assistant)
      scheduleContextRefresh()
      if (!process.toolCalls.some((tool) => tool.id === id)) {
        process.toolCalls.push({
          id,
          name: inferToolName(update) || 'Run',
          title: toolDisplayTitle(update),
          status: 'running',
          rawInput: update.rawInput
        })
      }
      ensureToolSegment(assistant, id)
      break
    }
    case 'tool_call_update': {
      const id = update.toolCallId ?? createId('tool')
      let tool = process.toolCalls.find((item) => item.id === id)
      if (!tool) {
        tool = { id, name: inferToolName(update) || 'Run', status: 'running' }
        process.toolCalls.push(tool)
      }
      ensureToolSegment(assistant, id)
      if (update.title) tool.title = toolDisplayTitle(update)
      tool.rawInput = update.rawInput ?? tool.rawInput
      tool.output = textFromContent(update.content) || update.content || tool.output
      tool.status = normalizeToolStatus(update.status)
      break
    }
    default:
      // 处理未知类型，可选打印
      // console.log('Unhandled session update type:', update.sessionUpdate);
      break
  }
  
  // 4. 滚动到底部
  if (!scrollPausedByUser.value) {
    nextTick(scrollToBottom)
  }
}

async function ensureAgentSession(conversation: Conversation, isNewConversation: boolean) {
  if (initializedSessions.has(conversation.id)) return
  
  if (isNewConversation) {
    try {
      restoringHistory.value = true // 新建时也锁住，防止 session/new 响应和 update 乱序
      const response = await callAcp('session/new', { sessionId: conversation.id, cwd: '.', mcpServers: [] })
      if (!response?.sessionId) throw new Error(t('invalidSession'))
      conversation.agentSessionId = response.sessionId
    } finally {
      restoringHistory.value = false
    }
  } else {
    const agentSessionId = conversation.agentSessionId ?? conversation.id
    restoringHistory.value = true
    try {
      await callAcp('session/load', { sessionId: agentSessionId, cwd: '.', mcpServers: [] })
    } catch (error) {
      console.warn('[history] ACP session missing, recreating checkpoint:', conversation.id, error)
      const response = await callAcp('session/new', { sessionId: conversation.id, cwd: '.', mcpServers: [] })
      if (!response?.sessionId) throw error
      conversation.agentSessionId = response.sessionId
    } finally {
      restoringHistory.value = false // 无论如何都要解锁
    }
  }
  initializedSessions.add(conversation.id)
}

// localStorage only mirrors the display state; the authoritative agent context
// lives in the SQLite checkpoint. On load we *verify* every history session
// against the backend, but verification failure MUST NEVER delete a
// conversation: session/load can fail on transient backend issues (socket,
// checkpoint lock, server hiccup) while the stored data is perfectly valid,
// and auto-deleting user history on such a failure is irreversible data loss.
// Unverifiable sessions are kept and re-checked on the next load;
// ensureAgentSession lazily recreates a missing ACP checkpoint on send.
async function validateStoredSessions() {
  if (!conversations.value.length) return
  try {
    await connect()
    await initialize()
  } catch {
    return
  }
  
  for (const conversation of conversations.value) {
    if (!conversation.messages.length) continue
    const agentSessionId = conversation.agentSessionId ?? conversation.id
    restoringHistory.value = true
    try {
      await callAcp('session/load', { sessionId: agentSessionId, cwd: '.', mcpServers: [] })
      initializedSessions.add(conversation.id)
    } catch (error) {
      console.warn('[history] unverifiable session kept:', conversation.id, error)
    } finally {
      restoringHistory.value = false // 如果中间抛出未捕获错误，也必须解锁
    }
  }
}

async function sendAgentPrompt(
  conversation: Conversation,
  text: string,
  resources: AttachmentRef[],
  assistantMessage: AssistantMessage,
  isNewConversation: boolean,
  retry = false,
  userMessage?: UserMessage
) {
  // 重置取消标志位
  cancelRequested = false
  
  // 使用 helper 确保拿到的是 Vue 的 Proxy 对象，防止响应式丢失
  const reactiveAssistant = () => 
    (conversation.messages.find((message) => message.id === assistantMessage.id) as AssistantMessage) 
    ?? assistantMessage

  try {
    await connect()
    await initialize()
    await ensureAgentSession(conversation, isNewConversation)
    const sessionId = conversation.agentSessionId ?? conversation.id

    // 上传所有暂存的附件到 MooFile，只保留轻量级的占位符
    const uploaded: AttachmentRef[] = []
    for (const resource of resources) {
      uploaded.push(await uploadResource(resource, sessionId))
    }
    
    // 回填持久化的用户消息（只保存引用，丢弃 base64 等大文件数据）
    if (userMessage) {
      const stored = conversation.messages.find((m) => m.id === userMessage.id)
      if (stored && stored.role === 'user') {
        stored.attachments = uploaded.map(({ file_id, url, path, name, mimeType, kind }) =>
          ({ file_id, url, path, name, mimeType, kind }))
      }
    }

    // 组装 Prompt 文本和图片 Content Block
    const promptLines: string[] = []
    if (text) promptLines.push(text)
    
    const imageBlocks: Array<Record<string, unknown>> = []
    for (const resource of uploaded) {
      if (resource.file_id) {
        const pathHint = resource.path ? `（附件路径：${resource.path}）` : ''
        if (resource.kind === 'image') {
          promptLines.push(`[图片: ${resource.file_id}]${pathHint}（图片内容已随消息提供，直接分析即可，无需读取文件）`)
          const block = await imageContentBlock(resource)
          if (block) imageBlocks.push(block)
        } else {
          promptLines.push(`[文件: ${resource.file_id}]${pathHint}\n请直接使用本地文件工具读取该路径；不要转换为 Windows 路径或 HTTP 地址。`)
        }
      } else if (resource.path || resource.uri) {
        promptLines.push(`附件虚拟路径：${resource.path ?? resource.uri}\n请直接使用本地文件工具读取该路径；不要转换为 Windows 路径或 HTTP 地址。`)
      }
    }
    
    const promptText = promptLines.filter(Boolean).join('\n\n')
    const content: Array<Record<string, unknown>> = []
    if (promptText) {
      content.push({ type: 'text', text: promptText })
    }
    content.push(...imageBlocks)
    
    // 发送请求
    const meta: Record<string, unknown> = {
      history_on: historyOn.value,
      conversation_id: conversation.id,
      question: text,
    }
    if (retry) meta.retry = true
    
    await callAcp('session/prompt', { sessionId: conversation.agentSessionId ?? conversation.id, prompt: content, _meta: meta })
    
    // ==========================================
    // 增强的最终兜底逻辑
    // ==========================================
    
    // 1. 强制从数组重新获取最新引用，避免拿到的对象跟视图绑定的不是同一个
    const currentAssistant = reactiveAssistant()

    // 2. 如果此时已经被用户点击了“停止”，或者因为错误被标为失败，则直接跳过完成逻辑
    if (currentAssistant.status !== 'cancelled' && currentAssistant.status !== 'failed' && !cancelRequested) {
      currentAssistant.status = 'completed'
      currentAssistant.process.completedAt = Date.now()
      
      // 3. 详细检测最终文本是否为空
      if (!currentAssistant.finalText || !currentAssistant.finalText.trim()) {
        // 如果没有获取到任何正文内容，可能流式输出全部被丢弃了
        console.error('[ACP] 流程结束 (end_turn)，但未收到任何文本内容。请检查 handleSessionUpdate 是否生效。')
        
        // 如果你想在这种情况下隐藏“任务已完成”几个字，直接注释掉下面这行：
        appendTextSegment(currentAssistant, t('taskComplete'))
      } else {
        // 4. 只有在确实有文本内容时，才触发 TTS 朗读和日语注音逻辑
        speakReplyIfEnabled(currentAssistant.finalText)
        void autoJpMark(currentAssistant)
      }
    }
    
  } catch (error) {
    // 异常处理与自动重试逻辑
    const currentAssistant = reactiveAssistant()
    const errMsg = error instanceof Error ? error.message : ''
    const connectionErrors = [t('connectionClosed'), t('connectionFailed'), t('connectionNotReady')]
    
    if (
      !retry &&
      !cancelRequested &&
      currentAssistant.status === 'pending' &&
      connectionErrors.includes(errMsg)
    ) {
      // 冷启动断连处理：断开旧连接，重试一次
      resetSocketForAgentChange()
      return sendAgentPrompt(conversation, text, resources, assistantMessage, isNewConversation, true, userMessage)
    }
    
    if (
      currentAssistant.status !== 'cancelled' &&
      currentAssistant.status !== 'completed' &&
      currentAssistant.status !== 'failed'
    ) {
      currentAssistant.status = 'failed'
      currentAssistant.process.completedAt = Date.now()
      
      const message = error instanceof Error ? error.message : t('taskFailed')
      errorText.value = message
      
      // 将错误信息追加到聊天记录中，避免用户只看到红色的 Error 却不知道发生了什么
      appendTextSegment(currentAssistant, `\n\n[Error] ${message}`)
    }
  } finally {
    conversation.updatedAt = Date.now()
    void saveConversation(conversation)
    nextTick(scrollToBottom)
    void refreshContextUsage()
  }
}

/** Apply a quick command to the unified workspace. */
async function onQuickCommand(cmd: any) {
  if (cmd.agent && cmd.agent !== currentAgentName.value) selectAgent(cmd.agent)
  if (cmd.model && cmd.model !== currentModel.value) {
    currentModel.value = cmd.model
    localStorage.setItem(MODEL_STORAGE_KEY, cmd.model)
  }
  // Apply associated role preset if any
  if (cmd.role) {
    fetch(`/api/roles/${encodeURIComponent(cmd.role)}`).catch(() => {})
    // Find role content from list
    const r = systemCommands.value.find((x: any) => x.name === cmd.role)
    if (r) {
      systemOverride.value = r.content
      systemOverrideName.value = r.name
    }
  }
  input.value = cmd.content
  let needReconnect = false
  if (cmd.webSearchOn !== undefined && cmd.webSearchOn !== webSearchOn.value) {
    webSearchOn.value = cmd.webSearchOn
    localStorage.setItem(WEB_SEARCH_STORAGE_KEY, String(cmd.webSearchOn))
    needReconnect = true
  }
  if (cmd.historyOn !== undefined && cmd.historyOn !== historyOn.value) {
    historyOn.value = cmd.historyOn
    localStorage.setItem(HISTORY_STORAGE_KEY, String(cmd.historyOn))
  }
  if (cmd.jpMarkOn !== undefined && cmd.jpMarkOn !== jpMarkOn.value) {
    jpMarkOn.value = cmd.jpMarkOn
    localStorage.setItem(JP_MARK_STORAGE_KEY, String(cmd.jpMarkOn))
  }
  if (needReconnect) resetSocketForAgentChange()
  input.value = cmd.content
  if (cmd.autoSend) await submitPrompt()
  nextTick(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus())
}

const qcBar = ref<any>(null)

function focusComposer() {
  nextTick(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus())
}

function chooseRole(cmd: any) {
  systemOverride.value = cmd.content
  systemOverrideName.value = cmd.name
  showRolePresets.value = false
  resetSocketForAgentChange()
  message.success(t('roleEnabled', { name: cmd.name }))
}
function clearRole() {
  systemOverride.value = ''
  systemOverrideName.value = ''
  showRolePresets.value = false
  resetSocketForAgentChange()
}

// Role editor state
const showRoleEditor = ref(false)
const editingRoleName = ref('')
const roleEditorName = ref('')
const roleEditorContent = ref('')

function openRoleEditor(role?: any) {
  if (role) {
    editingRoleName.value = role.name
    roleEditorName.value = role.name
    roleEditorContent.value = role.content
  } else {
    editingRoleName.value = ''
    roleEditorName.value = ''
    roleEditorContent.value = ''
  }
  showRoleEditor.value = true
}

async function saveRole() {
  const name = roleEditorName.value.trim()
  if (!name) return
  await fetch(`/api/roles/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: roleEditorContent.value }),
  })
  showRoleEditor.value = false
  refreshSystemCommands()
  message.success(t('roleSaved', { name }))
}

async function deleteRole(name: string) {
  await fetch(`/api/roles/${encodeURIComponent(name)}`, { method: 'DELETE' })
  refreshSystemCommands()
}

async function submitPrompt() {
  const text = input.value.trim()
  if ((!text && !attachments.value.length && !screenShareActive.value) || isRunning.value || isUploading.value) return
  if (!currentConversation.value) createConversation()
  const conversation = currentConversation.value
  if (!conversation) return
  // capture the screen as an attachment before sending when share-screen is on
  if (screenShareActive.value) await captureScreenAttachment()

  const isNewConversation = conversation.messages.length === 0
  errorText.value = ''
  contextOverride.value = null // a new prompt resets to the local estimate
  const resources = [...attachments.value]
  // Persist only the lightweight view (no base64 / raw File); file_id/url/path
  // are back-filled by sendAgentPrompt once the uploads complete.
  const userMessage: UserMessage = {
    id: createId('user'),
    role: 'user',
    text,
    attachments: resources.map(({ name, mimeType, kind }) => ({ name, mimeType, kind })),
    createdAt: Date.now()
  }
  const assistantMessage: AssistantMessage = {
    id: createId('assistant'),
    role: 'assistant',
    finalText: '',
    status: 'pending',
    process: { startedAt: Date.now(), plan: [], analyses: [], toolCalls: [] },
    segments: [],
    createdAt: Date.now()
  }
  conversation.messages.push(userMessage, assistantMessage)
  conversation.title = displayTitle(conversation.title) === t('newSessionTitle') ? (text || resources[0]?.name || t('attachmentTask')).slice(0, 24) : conversation.title
  conversation.updatedAt = Date.now()
  // Persist per-session toggles
  conversation.webSearchOn = webSearchOn.value
  conversation.historyOn = historyOn.value
  conversation.roleName = systemOverrideName.value
  conversation.jpMarkOn = jpMarkOn.value
  moveConversationToTop(conversation)
  input.value = ''
  attachments.value = []
  void saveConversation(conversation)
  await nextTick(scrollToBottom)
  await sendAgentPrompt(conversation, text, resources, assistantMessage, isNewConversation, false, userMessage)
}

function assistantSourceMessage(assistant: AssistantMessage) {
  const conversation = currentConversation.value
  const index = conversation?.messages.findIndex((item) => item.id === assistant.id) ?? -1
  const source = index > 0 ? conversation?.messages[index - 1] : undefined
  return source?.role === 'user' ? source : undefined
}

async function retryAssistant(assistant: AssistantMessage) {
  const conversation = currentConversation.value
  const source = assistantSourceMessage(assistant)
  if (!conversation || !source || isRunning.value) return

  errorText.value = ''
  assistant.finalText = ''
  assistant.status = 'pending'
  assistant.process = { startedAt: Date.now(), plan: [], analyses: [], toolCalls: [] }
  assistant.segments = []
  conversation.updatedAt = Date.now()
  void saveConversation(conversation)
  await nextTick(scrollToBottom)
  await sendAgentPrompt(conversation, source.text, source.attachments, assistant, !conversation.agentSessionId, true)
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    message.success(actionLabels.value.copied, { duration: 3000 })
  } catch {
    message.error(t('requestFailed'), { duration: 3000 })
  }
}

function copyAssistantText(assistant: AssistantMessage, asMarkdown: boolean) {
  const value = asMarkdown
    ? assistant.finalText
    : new DOMParser().parseFromString(markdown.render(assistant.finalText), 'text/html').body.textContent?.trim() ?? ''
  void copyText(value)
}

function copyUserText(item: { text?: string }, asMarkdown: boolean) {
  const raw = item.text ?? ''
  const value = asMarkdown
    ? raw
    : new DOMParser().parseFromString(markdown.render(raw), 'text/html').body.textContent?.trim() ?? ''
  void copyText(value)
}

// ---------- TTS synthesis and voice bridging ----------
let speechAudio: HTMLAudioElement | null = null
let speechStreamStop: (() => void) | null = null
let cancelRequested = false
const speakingMessageId = ref<string | null>(null)
const selectedVoice = ref(localStorage.getItem('chat_voice_name') || 'auto')
const speakReplyEnabled = ref(localStorage.getItem('chat_speak_reply') === '1')

let cachedVoiceNames: string[] | null = null
/** Return a voice name valid for the active engine; stale localStorage values
 *  (e.g. 'zh') fall back to '', letting the backend pick a default by language.
 *  'auto' is always passed through so every engine reads mixed text per language. */
async function resolveVoiceParam(): Promise<string> {
  const v = selectedVoice.value
  if (!v || v === 'auto') return v || ''
  if (cachedVoiceNames === null) {
    try {
      const list = await fetch('/api/chat_voice/voices').then((r) => r.json())
      cachedVoiceNames = list.map((x: any) => x.value)
    } catch {
      cachedVoiceNames = []
    }
  }
  return cachedVoiceNames.includes(v) ? v : ''
}

function messagePlainText(item: { role: string; text?: string; finalText?: string }): string {
  return item.role === 'user' ? (item.text ?? '') : (item.finalText ?? '')
}

/** Stop the current playback (streaming or not) */
function stopSpeech() {
  if (speechStreamStop) {
    speechStreamStop()
    speechStreamStop = null
  }
  if (speechAudio) {
    speechAudio.pause()
    speechAudio = null
  }
}

/** Synthesize and play text (returns success); used for message read-aloud and
 *  AI reply broadcast. Routes by backend TTS_MODE: file -> non-stream base64,
 *  stream -> streaming playback with non-stream fallback. */
async function speakText(text: string): Promise<boolean> {
  text = cleanSpeechText(text)
  if (!text.trim()) return false
  stopSpeech()
  const voice = await resolveVoiceParam()
  const cfg = await getTtsConfig()
  if (cfg.mode !== 'stream') {
    return await playBase64Speech(text, voice)
  }
  // streaming first
  const handle = await playTtsStream(text, voice, () => {
    speechStreamStop = null
    speakingMessageId.value = null
  })
  if (handle) {
    speechStreamStop = handle.stop
    return true
  }
  // fallback: non-stream base64 (kept working version)
  return await playBase64Speech(text, voice)
}

async function playBase64Speech(text: string, voice: string): Promise<boolean> {
  try {
    const resp = await fetch('/api/chat_voice/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice })
    })
    const data = await resp.json()
    if (!data.audio) return false
    const audio = new Audio('data:audio/wav;base64,' + data.audio)
    speechAudio = audio
    audio.onended = () => {
      speechAudio = null
      speakingMessageId.value = null
    }
    audio.onerror = () => {
      speechAudio = null
      speakingMessageId.value = null
    }
    audio.play().catch(() => {
      speechAudio = null
    })
    return true
  } catch {
    return false
  }
}

// ---------- voice-call window bridge (the #/voice window calls via opener) ----------
let replyListener: ((text: string) => void) | null = null

/** After an AI reply: notify the voice window first, else read aloud in the main window per the toggle */
function speakReplyIfEnabled(text: string) {
  if (!text.trim()) return
  if (replyListener) {
    const cb = replyListener
    replyListener = null
    cb(text)
    return
  }
  if (!speakReplyEnabled.value) return
  void speakText(text)
}

async function warmUpAcp() {
  try {
    await connect()
    await initialize()
  } catch {
    // Backend not ready yet (e.g. gateway still booting): the first send will
    // connect lazily; a cold-start socket drop is handled by the auto-retry in
    // sendAgentPrompt.
  }
}

/** Exposed to the voice window via window.opener.chatBridge */
function setupChatBridge() {
  const bridge = {
    /** Voice window: live-sync recognized speech into the main input */
    updateInput: (text: string) => {
      input.value = text
    },
    /** Voice window: send the final text through the main session (normal ACP flow) */
    sendMessage: (text: string) => {
      const value = text.trim()
      if (!value || isRunning.value) return
      input.value = value
      void submitPrompt()
    },
    /** Voice window: register an AI-reply-done callback (once per send) */
    onReply: (cb: (text: string) => void) => {
      replyListener = cb
    }
  }
  ;(window as any).chatBridge = bridge
}

async function speakMessage(item: { id: string; role: string; text?: string; finalText?: string }) {
  if (speakingMessageId.value === item.id) {
    stopSpeech()
    speakingMessageId.value = null
    return
  }
  const text = messagePlainText(item)
  if (!text.trim()) return
  speakingMessageId.value = item.id
  const ok = await speakText(text)
  if (!ok) {
    speakingMessageId.value = null
    message.error(actionLabels.value.ttsFailed, { duration: 3000 })
  }
}

// ---------- voice input (STT -> text box) ----------
function toggleSpeakReply() {
  speakReplyEnabled.value = !speakReplyEnabled.value
  localStorage.setItem('chat_speak_reply', speakReplyEnabled.value ? '1' : '0')
}

// 日语自动注音（显示层）：回复完成后把日语汉字注音为 <ruby> HTML。
// 只作用于「本次完成」的回复；历史消息不批量请求。原始 finalText 不变，
// 复制/编辑/TTS 仍走原文。后端只对含假名的片段注音，非日语部分转义。
async function autoJpMark(assistant: AssistantMessage) {
  if (!jpMarkOn.value) return
  if (!assistant.finalText) return
  // 快速预筛：无假名直接跳过，避免无谓请求
  if (!/[\u3040-\u30ff]/.test(assistant.finalText)) return
  try {
    const resp = await fetch('/api/jp-mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: assistant.finalText })
    })
    if (!resp.ok) return
    const data = await resp.json()
    if (data?.has_jp && typeof data.html === 'string') {
      const textSegment = assistant.segments.find((segment) => segment.type === 'text')
      if (textSegment && textSegment.text === assistant.finalText) {
        textSegment.html = data.html
        // Persist immediately — the user might switch away before the next saveConversation.
        const conv = conversations.value.find((c) => c.messages.some((m) => m.id === assistant.id))
        if (conv) void saveConversation(conv)
      }
    }
  } catch {
    /* silent: 注音失败不影响正常展示 */
  }
}

async function cancelTask() {
  const conversation = currentConversation.value
  const assistant = activeAssistantMessage()
  if (!conversation || !assistant) return
  cancelRequested = true
  // set cancelled immediately so a prompt Promise resolving before session/cancel cannot mark done and read a partial reply
  assistant.status = 'cancelled'
  assistant.process.completedAt = Date.now()
  assistant.process.toolCalls.forEach((tool) => {
    if (tool.status === 'running' || tool.status === 'waiting_permission') tool.status = 'cancelled'
  })
  stopSpeech() // also stop any read-aloud (including in-flight TTS playback)
  // Fire-and-forget the cancel request, then close the socket immediately.
  // Awaiting the ACP response would block here while the agent subprocess is
  // busy generating (it cannot answer until the current step finishes), so the
  // socket would never close and the bridge would never terminate the process.
  // Closing the socket makes app.py's bridge kill the whole agent process tree.
  try {
    const socket = ws.value
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ jsonrpc: '2.0', id: requestId.value++, method: 'session/cancel', params: { sessionId: conversation.agentSessionId ?? conversation.id } }))
    }
  } catch {
    // Socket already closing; the bridge terminates the subprocess on WS close anyway.
  } finally {
    ws.value?.close()
    void saveConversation(conversation)
  }
}

function respondPermission(optionId: string) {
  if (!permissionRequest.value) return
  respondAcp(permissionRequest.value.id, { outcome: { outcome: 'selected', optionId } })
  const assistant = activeAssistantMessage()
  if (assistant) assistant.status = 'pending'
  permissionRequest.value = null
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(t('uploadFailed')))
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error(t('uploadFailed')))
    reader.readAsDataURL(file)
  })
}

/**
 * Upload one attachment into the MooFile attachment store (db_attachments_*)
 * and return its lightweight ref (file_id + url + absolute path). Resources
 * that already carry a file_id (replayed/retried messages) are returned
 * untouched. Legacy base64 images are converted back to a File and uploaded,
 * so replaying an old conversation migrates it onto the unified store.
 */
async function uploadResource(resource: AttachmentRef, sessionId: string): Promise<AttachmentRef> {
  if (resource.file_id) return resource
  let file = resource.file
  if (!file && resource.data && resource.kind === 'image') {
    try {
      const raw = atob(resource.data)
      const bytes = new Uint8Array(raw.length)
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
      file = new File([bytes], resource.name || `image-${Date.now()}.png`, { type: resource.mimeType ?? 'image/png' })
    } catch {
      file = undefined
    }
  }
  if (!file) return resource
  const form = new FormData()
  form.append('file', file)
  form.append('session_id', sessionId)
  form.append('kind', resource.kind ?? 'file')
  form.append('alt_text', resource.name)
  const response = await fetch('/api/attachments', { method: 'POST', body: form })
  if (!response.ok) {
    // Carry the backend detail (which keeps the full stack trace on a 500) so
    // the user sees the real reason instead of a bare "upload failed".
    let detail = ''
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (body?.detail) detail = JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    const reason = detail ? `${response.status}: ${detail}` : `HTTP ${response.status} ${response.statusText}`
    console.error('[uploadResource] upload failed:', reason)
    throw new Error(reason)
  }
  const body = await response.json()
  if (!body?.ok) {
    const reason = body?.detail ? `server: ${body.detail}` : 'server returned ok=false'
    console.error('[uploadResource] upload rejected:', reason)
    throw new Error(reason)
  }
  return {
    file_id: body.file_id,
    url: body.url,
    path: body.attachment?.path,
    name: body.attachment?.filename ?? resource.name,
    mimeType: body.attachment?.mime ?? resource.mimeType,
    kind: resource.kind ?? 'file'
  }
}

/**
 * Fetch a stored image attachment and build an ACP image content block
 * ({type:'image', data: base64, mimeType}). The conversation keeps only the
 * lightweight ref; the base64 exists in the transient prompt only. Returns
 * null when the blob cannot be fetched, so the model still has the
 * [图片: id] + path hint to fall back on (works in Agent mode).
 */
async function imageContentBlock(resource: AttachmentRef): Promise<Record<string, unknown> | null> {
  try {
    const url = resource.url ?? (resource.file_id ? `/api/attachments/${resource.file_id}` : undefined)
    if (!url) return null
    const response = await fetch(url)
    if (!response.ok) return null
    const blob = await response.blob()
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(blob)
    })
    const data = dataUrl.slice(dataUrl.indexOf(',') + 1)
    if (!data) return null
    return { type: 'image', data, mimeType: resource.mimeType ?? blob.type ?? 'image/png' }
  } catch (error) {
    console.warn('[imageContentBlock] fetch failed, falling back to path hint:', error)
    return null
  }
}

async function addFiles(files: Iterable<File>) {
  const selectedFiles = Array.from(files)
  if (!selectedFiles.length) return
  isUploading.value = true
  try {
    for (const file of selectedFiles) {
      if (file.type.startsWith('image/')) {
        const previewUrl = await fileToDataUrl(file)
        attachments.value.push({
          name: file.name || `pasted-image-${Date.now()}.png`,
          mimeType: file.type,
          kind: 'image',
          data: previewUrl.slice(previewUrl.indexOf(',') + 1),
          previewUrl,
          file
        })
        continue
      }
      // Non-image files are also queued and uploaded to the MooFile attachment
      // store on send (unified with images; the conversation only keeps a
      // lightweight placeholder, never base64 or the raw bytes).
      attachments.value.push({
        name: file.name,
        mimeType: file.type,
        kind: 'file',
        file
      })
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : t('uploadFailed'), { duration: 3000 })
  } finally {
    isUploading.value = false
  }
}

async function uploadFile(event: Event) {
  const inputElement = event.target as HTMLInputElement
  await addFiles(inputElement.files ?? [])
  inputElement.value = ''
}

function onComposerPaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.items ?? [])
    .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file))
  if (!files.length) return
  event.preventDefault()
  void addFiles(files)
}

function dropFiles(event: DragEvent) {
  dragActive.value = false
  void addFiles(event.dataTransfer?.files ?? [])
}

function removeAttachment(index: number) {
  attachments.value.splice(index, 1)
}

// ---------- share screen (auto-capture as attachment on send) ----------
const screenShareActive = ref(false)
let screenStream: MediaStream | null = null
let screenVideo: HTMLVideoElement | null = null

async function toggleScreenShare() {
  if (screenShareActive.value) {
    stopScreenShare()
    return
  }
  try {
    const stream = await (navigator.mediaDevices as any).getDisplayMedia({ video: { frameRate: 5 } })
    screenStream = stream
    const video = document.createElement('video')
    video.srcObject = stream
    video.muted = true
    video.playsInline = true
    await video.play()
    screenVideo = video
    screenShareActive.value = true
    stream.getVideoTracks()[0]?.addEventListener('ended', () => stopScreenShare())
  } catch {
    // user cancelled the picker; keep it off
  }
}

function stopScreenShare() {
  if (screenStream) {
    screenStream.getTracks().forEach((track) => track.stop())
    screenStream = null
  }
  screenVideo = null
  screenShareActive.value = false
}

/** Capture one frame of the screen and queue it as an image attachment */
async function captureScreenAttachment() {
  if (!screenShareActive.value || !screenVideo) return
  try {
    const canvas = document.createElement('canvas')
    canvas.width = screenVideo.videoWidth || 1920
    canvas.height = screenVideo.videoHeight || 1080
    canvas.getContext('2d')?.drawImage(screenVideo, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
    if (!blob) return
    const file = new File([blob], `screen_${Date.now()}.png`, { type: 'image/png' })
    const previewUrl = await fileToDataUrl(file)
    attachments.value.push({
      name: file.name,
      mimeType: file.type,
      kind: 'image',
      data: previewUrl.slice(previewUrl.indexOf(',') + 1),
      previewUrl,
      file
    })
  } catch {
    // ignore capture failures
  }
}

function statusText(status: TaskStatus | ToolStatus) {
  return t(`status.${status}`)
}

function formatTime(timestamp: number) {
  const date = new Date(timestamp)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function duration(process: ExecutionProcess) {
  const end = process.completedAt ?? Date.now()
  return Math.max(0, Math.round((end - process.startedAt) / 1000))
}

const showScrollBottom = ref(false)
// user scroll pauses auto-follow: mouse/touch scroll during streaming pauses it;
// follow resumes after 5s idle or on the scroll-to-bottom button
const scrollPausedByUser = ref(false)
let scrollPauseTimer: number | null = null
function pauseAutoScrollByUser() {
  if (!isRunning.value) return
  scrollPausedByUser.value = true
  if (scrollPauseTimer) window.clearTimeout(scrollPauseTimer)
  scrollPauseTimer = window.setTimeout(() => {
    scrollPausedByUser.value = false
    scrollPauseTimer = null
    if (isRunning.value) nextTick(() => scrollToBottom(false))
  }, 5000)
}
function onTimelineScroll() {
  const el = timeline.value
  if (!el) return
  showScrollBottom.value = el.scrollTop + el.clientHeight < el.scrollHeight - 8
}

function scrollToBottom(smooth = false) {
  scrollPausedByUser.value = false
  if (scrollPauseTimer) {
    window.clearTimeout(scrollPauseTimer)
    scrollPauseTimer = null
  }
  const el = timeline.value
  if (!el) return
  if (smooth) {
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  } else {
    // use instant positioning for stream follow-scroll to avoid jitter from smooth animation + incremental content
    const prev = el.style.scrollBehavior
    el.style.scrollBehavior = 'auto'
    el.scrollTop = el.scrollHeight
    el.style.scrollBehavior = prev
  }
  showScrollBottom.value = false
}

function openAttachment(attachment: AttachmentRef) {
  if (attachment.kind === 'image') {
    // Pasted/dragged images are stored as inline data URLs; show them in an
    // in-page overlay instead of window.open (which blocks data: URLs).
    const url = attachment.previewUrl
      ?? (attachment.data ? `data:${attachment.mimeType ?? 'image/png'};base64,${attachment.data}` : undefined)
      ?? attachment.url
      ?? attachment.path
      ?? attachment.uri
    if (url) previewImage.value = url
    return
  }
  // Files live in the MooFile attachment store (serve URL) or, for legacy
  // uploads, under the virtual /uploads path. Resolve against the current
  // origin so the download anchor always points at a real, absolute URL.
  const url = attachment.url ?? attachment.path ?? attachment.uri ?? attachment.previewUrl
  if (!url) return
  const absolute = new URL(url, window.location.origin).href
  const anchor = document.createElement('a')
  anchor.href = absolute
  anchor.download = attachment.name || ''
  anchor.target = '_blank'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

// ---------- quick actions (@ files / slash skills / hash tools) ----------
const quickAction = ref<'file' | 'skill' | 'tool' | null>(null)
const quickItems = ref<any[]>([])
const quickLoading = ref(false)
// current directory for the @ file picker (relative to the project root, '' = root)
const quickDir = ref('')// overlay loading: initial load (initialLoading) and conversation switch
// (conversationLoading, counter-based so rapid switches hide only after the last)
const initialLoading = ref(true)
const conversationLoading = ref(0)
const quickIndex = ref(0)
let quickTimer: number | undefined

function closeQuickAction() {
  quickAction.value = null
  quickItems.value = []
  quickIndex.value = 0
  quickDir.value = ''
}

async function loadQuickItems(action: 'file' | 'skill' | 'tool', q: string, dir = '') {
  quickLoading.value = true
  try {
    if (action === 'file') {
      const res = await fetch(`/api/manage/files?q=${encodeURIComponent(q)}&dir=${encodeURIComponent(dir)}`)
      const data = await res.json()
      quickItems.value = Array.isArray(data) ? data : []
    } else if (action === 'skill') {
      const res = await fetch('/api/manage/skills')
      const data = await res.json()
      const list = Array.isArray(data) ? data : []
      quickItems.value = q
        ? list.filter((s: any) => s.name.toLowerCase().includes(q.toLowerCase()))
        : list
    } else {
      // tools = built-in tools + every MCP server's tools (mcp_<server>_<tool>)
      const agent = currentAgentName.value || 'default'
      const [builtins, mcp] = await Promise.all([
        fetch('/api/manage/builtin-tools').then((r) => r.json()),
        fetch(`/api/manage/tools?agent=${encodeURIComponent(agent)}`).then((r) => r.json())
      ])
      const items: any[] = []
      for (const t of Array.isArray(builtins) ? builtins : []) {
        items.push({ name: t.name, desc: t.description ? `builtin · ${t.description}` : 'builtin tool' })
      }
      for (const s of Array.isArray(mcp) ? mcp : []) {
        for (const t of s.tools || []) {
          items.push({
            name: `mcp_${s.name}_${t.name}`,
            desc: s.name + (t.description ? ` · ${t.description}` : '')
          })
        }
      }
      quickItems.value = q
        ? items.filter((i) => i.name.toLowerCase().includes(q.toLowerCase()) || (i.desc || '').toLowerCase().includes(q.toLowerCase()))
        : items
    }
    quickIndex.value = 0
  } catch {
    quickItems.value = []
  } finally {
    quickLoading.value = false
  }
}

// drill into a folder (relative path from the project root)
function drillQuickDir(rel: string) {
  quickDir.value = rel
  quickIndex.value = 0
  void loadQuickItems('file', '', rel)
}

function parentQuickDir() {
  const parts = quickDir.value.split(/[\\/]/).filter(Boolean)
  parts.pop()
  quickDir.value = parts.join('/')
  quickIndex.value = 0
  void loadQuickItems('file', '', quickDir.value)
}

function selectQuickItem(item: any) {
  if (quickAction.value === 'file') {
    // a folder entry drills into it instead of being inserted
    if (item.type === 'dir') {
      drillQuickDir(item.rel || item.name)
      return
    }
    // backticked `@absolute-path` + space
    input.value = '`' + '@' + (item.path ?? item.absolute_path ?? item.name) + '`' + ' '
  } else if (quickAction.value === 'skill') {
    // backticked `/skill-name` + space
    input.value = '`' + '/' + item.name + '`' + ' '
  } else {
    // backticked `#tool-name` + space
    input.value = '`' + '#' + item.name + '`' + ' '
  }
  closeQuickAction()
}

watch(input, (value) => {
  if (value.length >= 1) {
    const ch = value[0]
    if (value.length === 1 && (ch === '@' || ch === '/' || ch === '#')) {
      quickAction.value = ch === '@' ? 'file' : ch === '/' ? 'skill' : 'tool'
      quickItems.value = []
      quickDir.value = ''
      quickLoading.value = true
      clearTimeout(quickTimer)
      quickTimer = window.setTimeout(() => void loadQuickItems(quickAction.value!, '', ''), 120)
      return
    }
    if (quickAction.value && (ch === '@' || ch === '/' || ch === '#')) {
      const q = value.slice(1)
      clearTimeout(quickTimer)
      quickTimer = window.setTimeout(() => void loadQuickItems(quickAction.value!, q, quickDir.value), 120)
      return
    }
  }
  closeQuickAction()
})

function onComposerBlur() {
  closeQuickAction()
}

function submitOnEnter(event: KeyboardEvent) {
  // IME 组合输入期间的回车（中文/日文候选词确认）不应触发发送
  if (event.isComposing || event.keyCode === 229) return
  if (!event.shiftKey) {
    event.preventDefault()
    if (quickAction.value) {
      if (quickItems.value.length) selectQuickItem(quickItems.value[quickIndex.value] ?? quickItems.value[0])
      else closeQuickAction()
      return
    }
    submitPrompt()
  }
}

// 上下键在 @ /skill /# 快速动作面板中循环移动选中项（回车选中由 submitOnEnter 处理）
function onComposerArrow(event: KeyboardEvent, delta: number) {
  if (!quickAction.value || !quickItems.value.length) return
  event.preventDefault()
  quickIndex.value = (quickIndex.value + delta + quickItems.value.length) % quickItems.value.length
}

watch(locale, (value) => localStorage.setItem(storageKey, value as SupportedLocale))
// 定时任务会话自动刷新：进入 timer-* 会话时每 5s 静默更新履历（不滚动、不重置窗口），切换会话立即停止
/** 静默刷新会话数据：只替换内存数据，不重置消息窗口、不滚动（避免画面跳动）。 */
async function refreshTimerSessionQuiet(id: string) {
  try {
    const res = await fetch(`/api/history/${encodeURIComponent(id)}`)
    if (!res.ok) return
    const rows = migrateConversations([await res.json()])
    if (!rows.length) return
    const index = conversations.value.findIndex((c) => c.id === id)
    if (index >= 0) conversations.value[index] = rows[0]
    else conversations.value.unshift(rows[0])
  } catch {
    // 静默失败，保持现有数据
  }
}

let timerRefreshInterval: number | undefined
/** 停止定时任务会话轮询（切换会话 / 面板关闭 / 任务删除时调用）。 */
function stopTimerPolling() {
  if (timerRefreshInterval !== undefined) {
    window.clearInterval(timerRefreshInterval)
    timerRefreshInterval = undefined
  }
}
/** 启动定时任务会话轮询：每 5s 静默更新履历 + 执行记录。 */
function startTimerPolling(id: string) {
  stopTimerPolling()
  timerRefreshInterval = window.setInterval(() => {
    if (activeConversationId.value === id) {
      void refreshTimerSessionQuiet(id)
      void loadTimers() // 执行履历 / 状态同步刷新
    } else {
      stopTimerPolling()
    }
  }, 5000)
}
watch(activeConversationId, (id) => {
  stopTimerPolling()
  if (
    id
    && String(id).startsWith('timer-')
    && managementVisible.value
    && managementSection.value === 'timers'
  ) {
    startTimerPolling(id)
  }
})
// 管理中心关闭时停止轮询；重新打开定时任务模块时恢复
watch(managementVisible, (visible) => {
  if (!visible) {
    stopTimerPolling()
    return
  }
  const id = activeConversationId.value
  if (managementSection.value === 'timers' && id && String(id).startsWith('timer-')) {
    startTimerPolling(id)
  }
})

onMounted(async () => {
  void loadAppConfig()
  setupChatBridge()
  void loadTimers()
  try {
    await loadHistory()
    void validateStoredSessions()
  } finally {
    initialLoading.value = false
  }
  // 预热 ACP 子进程：页面加载即建立连接并完成握手，避免“程序刚启动
  // 第一次发消息”撞上子进程冷启动异常而提示连接关闭（失败静默，后续
  // 发消息时 connect() 会自动重连）。
  void warmUpAcp()
  window.addEventListener('click', closeMessageMenu)
  window.addEventListener('click', closeRenameMenu)
})
watch(() => currentMessages.value.length, () => {
  syncTodoState(currentConversation.value)
  nextTick(() => {
    const el = timeline.value
    if (el) onTimelineScroll()
  })
})
// tool_call_update 等不改变消息长度但更新 toolCalls 内部状态——深监听以同步 todo 清单
watch(() => currentConversation.value?.messages, (messages) => {
  if (messages) syncTodoState(currentConversation.value)
}, { deep: true })
onBeforeUnmount(() => {
  window.removeEventListener('click', closeMessageMenu)
  window.removeEventListener('click', closeRenameMenu)
  if (timerRefreshInterval !== undefined) window.clearInterval(timerRefreshInterval)
  if (scrollPauseTimer) window.clearTimeout(scrollPauseTimer)
  stopScreenShare()
  stopSpeech()
  ws.value?.close()
})
</script>

<template>
  <main class="chat-app" :class="{ 'chat-app--collapsed': sidebarCollapsed }">
    <aside class="sidebar" :class="{ 'sidebar--mobile-open': sidebarVisible }">
      <div class="brand-row">
        <div class="brand-mark"><NIcon :component="CodeSlashOutline" /></div>
        <div>
          <strong>{{ t('systemName') }}</strong>
          <span>{{ t('brandSubline') }}</span>
        </div>
        <NButton class="sidebar-collapse-btn" quaternary circle size="tiny" :aria-label="actionLabels.collapseSidebar" @click="sidebarCollapsed = true">
          <template #icon><NIcon :component="ChevronBackOutline" /></template>
        </NButton>
      </div>

      <div class="sidebar-nav">
        <button type="button" class="sidebar-nav-item sidebar-nav-item--primary" :aria-label="t('newConversation')" @click="openNewConversation">
          <NIcon :component="AddOutline" /><span>{{ t('newConversation') }}</span>
        </button>
      </div>

      <div class="conversation-search">
        <NInput v-model:value="conversationSearch" size="small" clearable :placeholder="t('searchConversations')">
          <template #prefix><NIcon :component="SearchOutline" /></template>
        </NInput>
      </div>
      <nav class="conversation-list" :aria-label="t('recentConversations')">
        <div v-if="!filteredConversations.length && conversationSearch.trim()" class="conversation-empty">{{ t('noMatchingConversations') }}</div>
        <div class="conversation-group conversation-group--normal" :class="{ 'conversation-group--collapsed': agentCollapsed }">
          <button type="button" class="conversation-group-label" :class="{ 'conversation-group-label--collapsed': agentCollapsed }" @click="agentCollapsed = !agentCollapsed">
            <NIcon :component="agentCollapsed ? ChevronForwardOutline : ChevronDownOutline" />
            <span>{{ t('recentConversations') }}</span>
            <span class="conversation-group-count">{{ normalConversations.length }}</span>
          </button>
          <div v-if="!agentCollapsed" class="conversation-group-list">
            <div v-if="!normalConversations.length && !conversationSearch.trim()" class="conversation-group-empty">{{ t('emptyConversations') }}</div>
            <button
              v-for="conversation in normalConversations"
              :key="conversation.id"
              class="conversation-item"
              :class="{ active: conversation.id === activeConversationId }"
              @click="selectConversation(conversation.id)"
              @contextmenu.prevent="openRenameMenu(conversation, $event)"
            >
              <span class="conversation-icon"><NIcon :component="SparklesOutline" /></span>
              <span class="conversation-line">
                <span class="conversation-title">{{ displayTitle(conversation.title) }}</span>
              </span>
              <span class="conversation-time">{{ formatTime(conversation.updatedAt) }}</span>
              <span
                class="conversation-delete"
                role="button"
                tabindex="0"
                :aria-label="t('deleteSession')"
                @click.stop="deleteTarget = conversation.id"
                @keydown.enter.prevent="deleteTarget = conversation.id"
              >
                <NIcon :component="TrashOutline" />
              </span>
            </button>
          </div>
        </div>
        <div class="conversation-group conversation-group--timers" :class="{ 'conversation-group--collapsed': timerCollapsed }">
          <button type="button" class="conversation-group-label" :class="{ 'conversation-group-label--collapsed': timerCollapsed }" @click="timerCollapsed = !timerCollapsed">
            <NIcon :component="timerCollapsed ? ChevronForwardOutline : ChevronDownOutline" />
            <span>{{ t('timers.timerGroupScheduled') }}</span>
            <span class="conversation-group-count">{{ timerConversations.length }}</span>
          </button>
          <div v-if="!timerCollapsed" class="conversation-group-list">
            <div v-if="!timerConversations.length" class="conversation-group-empty">{{ t('timers.timerGroupEmpty') }}</div>
            <button
              v-for="conversation in timerConversations"
              :key="conversation.id"
              class="conversation-item"
              :class="{ active: conversation.id === activeConversationId, 'conversation-item--disabled': timerTaskOf(conversation.id) && !timerTaskOf(conversation.id)?.enabled }"
              @click="selectConversation(conversation.id)"
              @contextmenu.prevent="openRenameMenu(conversation, $event)"
            >
              <span class="conversation-icon"><NIcon :component="TimeOutline" /></span>
              <span class="conversation-title">{{ timerTitle(conversation.title) || displayTitle(conversation.title) }}</span>
              <span class="conversation-time">{{ formatTime(conversation.updatedAt) }}</span>
              <span
                class="conversation-delete"
                role="button"
                tabindex="0"
                :aria-label="t('deleteSession')"
                @click.stop="deleteTarget = conversation.id"
                @keydown.enter.prevent="deleteTarget = conversation.id"
              >
                <NIcon :component="TrashOutline" />
              </span>
            </button>
          </div>
        </div>
      </nav>
      <div class="sidebar-management">
        <button type="button" class="sidebar-nav-item" :aria-label="managementTitle" @click="openManagement()">
          <NIcon :component="SettingsOutline" /><span>{{ managementTitle }}</span>
        </button>
      </div>
    </aside>

    <div
      v-if="renameMenu"
      class="rename-menu"
      :style="{ left: renameMenu.x + 'px', top: renameMenu.y + 'px' }"
      @click.stop
    >
      <button type="button" class="message-menu-item" @click="openRenameModal">
        <NIcon :component="CreateOutline" />{{ actionLabels.renameConversation }}
      </button>
    </div>

    <section class="workspace" :class="{ 'workspace--initial': noActiveConversation }">
      <header class="workspace-header">
        <div class="header-title">
          <NTooltip>
            <template #trigger>
              <NButton class="mobile-menu" quaternary circle :aria-label="t('openConversations')" @click="sidebarVisible = true">
                <template #icon><NIcon :component="MenuOutline" /></template>
              </NButton>
            </template>
            {{ t('openConversations') }}
          </NTooltip>
          <NTooltip v-if="sidebarCollapsed">
            <template #trigger>
              <NButton class="sidebar-expand-btn" quaternary circle :aria-label="actionLabels.expandSidebar" @click="sidebarCollapsed = false">
                <template #icon><NIcon :component="MenuOutline" /></template>
              </NButton>
            </template>
            {{ actionLabels.expandSidebar }}
          </NTooltip>
          <div>
            <h1>{{ currentTitle }}</h1>
            <span>{{ isRunning ? t('taskRunning') : connectionLabel }}</span>
          </div>
        </div>
        <div class="header-actions">
          <NDropdown :options="localeOptions" trigger="click" @select="onLocaleSelect">
            <NButton quaternary size="small" :aria-label="t('language')" class="locale-trigger">
              <template #icon><NIcon :component="LanguageOutline" /></template>
              <span class="locale-short">{{ localeShort }}</span>
            </NButton>
          </NDropdown>
          <NDropdown :options="themeOptions" trigger="click" @select="onThemeSelect">
            <NButton quaternary circle :aria-label="actionLabels.theme" :title="actionLabels.theme">
              <template #icon><NIcon :component="resolvedTheme === 'dark' ? MoonOutline : SunnyOutline" /></template>
            </NButton>
          </NDropdown>
          <NButton quaternary circle :title="actionLabels.openVoiceCall" @click="openVoiceCall">
            <template #icon><NIcon :component="CallOutline" /></template>
          </NButton>
          <NTooltip>
            <template #trigger>
              <NButton quaternary circle :type="speakReplyEnabled ? 'primary' : 'default'" :aria-label="actionLabels.speakReply" @click="toggleSpeakReply">
                <template #icon><NIcon :component="speakReplyEnabled ? VolumeHighOutline : VolumeMuteOutline" /></template>
              </NButton>
            </template>
            {{ actionLabels.speakReply }}
          </NTooltip>
          <NTooltip>
            <template #trigger>
              <NButton quaternary circle :type="screenShareActive ? 'primary' : 'default'" :aria-label="actionLabels.screenShare" @click="toggleScreenShare">
                <template #icon><NIcon :component="DesktopOutline" /></template>
              </NButton>
            </template>
            {{ screenShareActive ? actionLabels.screenShareActive : actionLabels.screenShare }}
          </NTooltip>
          <NDropdown :options="headerMoreOptions" trigger="click" @select="onHeaderMoreSelect">
            <NButton quaternary circle :aria-label="t('moreActions')">
              <template #icon><NIcon :component="EllipsisHorizontalOutline" /></template>
            </NButton>
          </NDropdown>
        </div>
      </header>

      <div class="timeline-wrap">
        <section ref="timeline" class="timeline" aria-live="polite" @scroll="onTimelineScroll" @wheel.passive="pauseAutoScrollByUser" @touchmove.passive="pauseAutoScrollByUser">
          <div v-if="noActiveConversation" class="welcome welcome--mode">
            <div class="mode-tabs" role="tablist">
              <button type="button" class="mode-tab" :class="{ active: currentAgentName === 'Chat' }" role="tab" :aria-selected="currentAgentName === 'Chat'" @click="selectAgent('Chat')">
                <NIcon :component="ChatbubbleOutline" />{{ actionLabels.chatMode }}
              </button>
              <button type="button" class="mode-tab" :class="{ active: currentAgentName !== 'Chat' }" role="tab" :aria-selected="currentAgentName !== 'Chat'" @click="selectAgent('default')">
                <NIcon :component="ConstructOutline" />{{ actionLabels.taskMode }}
              </button>
            </div>
            <h2>{{ displayAgentName(currentAgentName) }}</h2>
            <p>{{ agentList.find((agent) => agent.name === currentAgentName)?.description || actionLabels.taskModeDesc }}</p>
          </div>
          <div v-else-if="!currentMessages.length" class="welcome">
          <div class="welcome-symbol"><NIcon :component="CodeSlashOutline" /></div>
          <h2>{{ t('startTask') }}</h2>
          <p>{{ t('startTaskHint') }}</p>
        </div>

        <div v-else class="message-column">
          <div v-if="showLoadEarlier" class="load-earlier" role="button" tabindex="0" @click="loadEarlierMessages">{{ t('loadEarlier') }}</div>
          <article v-for="item in visibleMessages" :key="item.id" :data-message-id="item.id" class="message" :class="`message--${item.role}`">
            <NAvatar round :size="30" :color="item.role === 'user' ? '#2563eb' : '#1d2733'">
              {{ item.role === 'user' ? t('you') : t('assistant') }}
            </NAvatar>
            <div class="message-body">
              <div class="message-meta">
                <strong>{{ item.role === 'user' ? t('you') : t('assistant') }}</strong>
                <span v-if="!isTimerSession || item.role === 'user'">{{ formatTime(item.createdAt) }}</span>
              </div>
              <div v-if="item.role === 'user'" class="message-bubble user-bubble">
                <MarkdownMessage v-if="item.text" class="user-content" variant="user" :content="item.text" @open-file="openFilePreview" />
                <div v-if="item.attachments.length" class="attachment-list">
                  <span v-for="attachment in item.attachments" :key="attachment.path ?? attachment.uri ?? attachment.name" class="attachment-chip" :class="{ 'attachment-chip--image': attachment.kind === 'image' }" role="button" :aria-label="attachment.kind === 'image' ? t('previewAttachment') : t('downloadAttachment')" @click="openAttachment(attachment)">
                    <img v-if="attachment.kind === 'image' && (attachment.previewUrl || attachment.url)" :src="attachment.previewUrl ?? attachment.url" :alt="attachment.name" class="attachment-preview" />
                    <NIcon :component="AttachOutline" /><span class="attachment-name">{{ attachment.name }}</span>
                  </span>
                </div>
              </div>
              <div v-if="item.role === 'user'" class="message-actions message-actions--user">
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.copyText" @click="copyUserText(item, false)">
                      <template #icon><NIcon :component="CopyOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.copyText }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.copyMarkdown" @click="copyUserText(item, true)">
                      <template #icon><NIcon :component="CodeSlashOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.copyMarkdown }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.speak" @click="speakMessage(item)">
                      <template #icon><NIcon :component="speakingMessageId === item.id ? StopCircleOutline : VolumeHighOutline" /></template>
                    </NButton>
                  </template>
                  {{ speakingMessageId === item.id ? actionLabels.stopSpeak : actionLabels.speak }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton class="hidden-action" quaternary circle size="tiny" :aria-label="actionLabels.editMessage" @click="openEditMessage(item)">
                      <template #icon><NIcon :component="CreateOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.editMessage }}
                </NTooltip>
                <div class="message-menu-wrap hidden-action" @click.stop>
                  <NTooltip>
                    <template #trigger>
                      <NButton quaternary circle size="tiny" :aria-label="actionLabels.more" @click.stop="toggleMessageMenu(item.id)">
                        <template #icon><NIcon :component="EllipsisHorizontalOutline" /></template>
                      </NButton>
                    </template>
                    {{ actionLabels.more }}
                  </NTooltip>
                  <div v-if="openMenuId === item.id" class="message-menu">
                    <button type="button" class="message-menu-item" @click="deleteMessage(item)">
                      <NIcon :component="TrashOutline" />{{ actionLabels.deleteMessage }}
                    </button>
                  </div>
                </div>
              </div>
              <template v-else>
                <!-- Running: live show. Done: collapse thought/tool/plan into one fold. -->
                <template v-if="isAssistantRunning(item)">
                  <template v-for="segment in item.segments" :key="segment.id">
                    <MarkdownMessage v-if="segment.type === 'text'" class="assistant-content" :content="segment.text" :html="segment.html" @open-file="openFilePreview" />
                    <ThoughtCard v-else-if="segment.type === 'thought' && segment.text.trim().length > 0" :text="segment.text" :done="segment.done" />
                    <ExecutionProcess v-else-if="segment.type === 'plan'" :process="item.process" :todo-items="currentTodoItems" />
                    <ToolCallCard v-else-if="segment.type === 'tool'" :tool="segmentTool(item, segment)" @open-file="openFilePreview" />
                  </template>
                </template>
                <template v-else>
                  <NCollapse v-if="hasProcessSegments(item)" class="process-collapse">
                    <NCollapseItem name="process">
                      <template #header>
                        <span class="process-collapse__header">🔧 {{ actionLabels.processSteps(processStepCount(item)) }}</span>
                      </template>
                      <template v-for="segment in processSegments(item)" :key="segment.id">
                        <MarkdownMessage v-if="segment.type === 'text'" class="assistant-content" :content="segment.text" :html="segment.html" @open-file="openFilePreview" />
                        <ThoughtCard v-else-if="segment.type === 'thought' && segment.text.trim().length > 0" :text="segment.text" :done="true" />
                        <ExecutionProcess v-else-if="segment.type === 'plan'" :process="item.process" :todo-items="currentTodoItems" />
                        <ToolCallCard v-else-if="segment.type === 'tool'" :tool="segmentTool(item, segment)" @open-file="openFilePreview" />
                      </template>
                    </NCollapseItem>
                  </NCollapse>
                  <MarkdownMessage v-for="segment in finalTextSegments(item)" :key="segment.id" class="assistant-content" :content="segment.text" :html="segment.html" @open-file="openFilePreview" />
                </template>
                <span v-if="isAssistantRunning(item)" class="msg-loader" aria-label="loading" />
                <div v-if="!isRunning && item.status !== 'pending'" class="message-actions">
                  <NTooltip v-if="item.id === lastAssistantId">
                    <template #trigger>
                      <NButton quaternary circle size="tiny" :aria-label="actionLabels.retry" @click="retryAssistant(item)">
                        <template #icon><NIcon :component="RefreshOutline" /></template>
                      </NButton>
                    </template>
                    {{ actionLabels.retry }}
                  </NTooltip>
                  <NTooltip>
                    <template #trigger>
                      <NButton quaternary circle size="tiny" :aria-label="actionLabels.copyText" @click="copyAssistantText(item, false)">
                        <template #icon><NIcon :component="CopyOutline" /></template>
                      </NButton>
                    </template>
                    {{ actionLabels.copyText }}
                  </NTooltip>
                  <NTooltip>
                    <template #trigger>
                      <NButton quaternary circle size="tiny" :aria-label="actionLabels.copyMarkdown" @click="copyAssistantText(item, true)">
                        <template #icon><NIcon :component="CodeSlashOutline" /></template>
                      </NButton>
                    </template>
                    {{ actionLabels.copyMarkdown }}
                  </NTooltip>
                  <NTooltip>
                    <template #trigger>
                      <NButton quaternary circle size="tiny" :aria-label="actionLabels.speak" @click="speakMessage(item)">
                        <template #icon><NIcon :component="speakingMessageId === item.id ? StopCircleOutline : VolumeHighOutline" /></template>
                      </NButton>
                    </template>
                    {{ speakingMessageId === item.id ? actionLabels.stopSpeak : actionLabels.speak }}
                  </NTooltip>
                  <NButton class="hidden-action" quaternary circle size="tiny" :aria-label="actionLabels.editMessage" :disabled="isMessageBusy(item)" @click="openEditMessage(item)">
                    <template #icon><NIcon :component="CreateOutline" /></template>
                  </NButton>
                  <div class="message-menu-wrap hidden-action" @click.stop>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.more" @click.stop="toggleMessageMenu(item.id)">
                      <template #icon><NIcon :component="EllipsisHorizontalOutline" /></template>
                    </NButton>
                    <div v-if="openMenuId === item.id" class="message-menu">
                      <button type="button" class="message-menu-item" @click="deleteMessage(item)">
                        <NIcon :component="TrashOutline" />{{ actionLabels.deleteMessage }}
                      </button>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </article>
        </div>
      </section>
      <NButton v-if="showScrollBottom" class="scroll-bottom-btn" circle type="primary" size="small" :aria-label="t('scrollToBottom')" @click.capture="scrollToBottom(true)">
        <template #icon><NIcon :component="ArrowDownOutline" /></template>
      </NButton>
      </div>

      <div v-if="!isTimerSession" class="composer-wrap">
        <div class="composer-column">
          <NAlert v-if="errorText" type="error" closable class="task-error" @close="errorText = ''">
            {{ errorText }}
          </NAlert>
          <div v-if="attachments.length" class="pending-attachments">
            <span v-for="(attachment, index) in attachments" :key="attachment.path ?? attachment.uri ?? attachment.name" class="attachment-chip" :class="{ 'attachment-chip--image': attachment.kind === 'image' }">
              <img v-if="attachment.kind === 'image' && attachment.previewUrl" :src="attachment.previewUrl" :alt="attachment.name" class="attachment-preview" />
              <NIcon :component="AttachOutline" /><span class="attachment-name">{{ attachment.name }}</span>
              <button :aria-label="t('removeAttachment')" @click="removeAttachment(index)">×</button>
            </span>
          </div>
          <div class="composer" :class="{ 'composer--dragging': dragActive }" @dragenter.prevent="dragActive = true" @dragover.prevent="dragActive = true" @dragleave.prevent="dragActive = false" @drop.prevent="dropFiles" @paste="onComposerPaste">
            <input ref="fileInput" class="hidden-input" type="file" multiple @change="uploadFile" />
            <NInput v-model:value="input" class="composer-input" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" :placeholder="t('inputPlaceholder')" @keydown.enter.exact="submitOnEnter" @keydown.up="onComposerArrow($event, -1)" @keydown.down="onComposerArrow($event, 1)" @blur="onComposerBlur" />
            <div v-if="quickAction" class="quick-action-panel" @mousedown.prevent>
              <div v-if="quickLoading" class="quick-action-empty">{{ t('loading') }}</div>
              <div v-else-if="!quickItems.length" class="quick-action-empty">{{ t('noResults') }}</div>
              <div
                v-if="quickAction === 'file' && quickDir"
                class="quick-action-item"
                @mousedown.prevent="parentQuickDir()"
              >
                <NIcon :component="FolderOutline" />
                <span class="quick-action-name">..</span>
                <span class="quick-action-desc">{{ quickDir }}</span>
              </div>
              <div
                v-for="(item, index) in quickItems"
                :key="item.path ?? item.name"
                class="quick-action-item"
                :class="{ 'quick-action-item--active': index === quickIndex }"
                @mousedown.prevent="selectQuickItem(item)"
              >
                <NIcon :component="quickAction === 'file' ? (item.type === 'dir' ? FolderOutline : DocumentOutline) : quickAction === 'skill' ? SparklesOutline : ConstructOutline" />
                <span class="quick-action-name">{{ quickAction === 'file' ? item.name : item.name }}</span>
                <span class="quick-action-desc">{{ quickAction === 'skill' ? item.description : item.path }}</span>
              </div>
            </div>
            <div class="composer-toolbar">
              <div class="composer-toolbar-left">
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="small" :loading="isUploading" :aria-label="t('upload')" @click="fileInput?.click()">
                      <template #icon><NIcon :component="AttachOutline" /></template>
                    </NButton>
                  </template>
                  {{ t('upload') }}
                </NTooltip>
                <NDropdown :options="agentDropdownOptions" trigger="click" @select="selectAgent">
                  <NButton quaternary size="small" class="toolbar-pill agent-pill" :aria-label="actionLabels.selectAgent">
                    <template #icon><NIcon :component="SparklesOutline" /></template>{{ displayAgentName(currentAgentName) || actionLabels.agent }}
                  </NButton>
                </NDropdown>
                <NTooltip>
                    <template #trigger>
                      <NButton
                        quaternary size="small"
                        class="toolbar-pill"
                        :class="{ 'toolbar-pill--on': historyOn }"
                        :aria-label="historyOn ? actionLabels.historyOn : actionLabels.historyOff"
                        @click="historyOn = !historyOn; localStorage.setItem(HISTORY_STORAGE_KEY, String(historyOn)); focusComposer()"
                      >
                        <template #icon><NIcon :component="historyOn ? ReaderOutline : TimeOutline" /></template>
                        {{ historyOn ? actionLabels.historyOn : actionLabels.historyOff }}
                      </NButton>
                    </template>
                    {{ historyOn ? actionLabels.historyOn : actionLabels.historyOff }}
                </NTooltip>
                <NTooltip>
                    <template #trigger>
                      <NButton
                        quaternary size="small"
                        class="toolbar-pill"
                        :class="{ 'toolbar-pill--on': webSearchOn }"
                        :aria-label="actionLabels.webSearch"
                        @click="webSearchOn = !webSearchOn; localStorage.setItem(WEB_SEARCH_STORAGE_KEY, String(webSearchOn)); focusComposer()"
                      >
                        <template #icon><NIcon :component="GlobeOutline" /></template>
                        {{ actionLabels.webSearch }}
                      </NButton>
                    </template>
                    {{ actionLabels.webSearch }}
                </NTooltip>
                <NTooltip>
                    <template #trigger>
                      <NButton
                        quaternary size="small"
                        class="toolbar-pill"
                        :class="{ 'toolbar-pill--on': jpMarkOn }"
                        :aria-label="t('jpMark')"
                        @click="jpMarkOn = !jpMarkOn; localStorage.setItem(JP_MARK_STORAGE_KEY, String(jpMarkOn)); focusComposer()"
                      >
                        <template #icon><NIcon :component="LanguageOutline" /></template>
                        {{ t('jpMarkShort') }}
                      </NButton>
                    </template>
                    {{ t('jpMarkTooltip') }}
                </NTooltip>
                <div style="position:relative;display:inline-block;">
                    <NButton
                      quaternary size="small"
                      class="toolbar-pill"
                      :class="{ 'toolbar-pill--on': !!systemOverrideName }"
                      :aria-label="t('rolePreset')"
                      @click="showRolePresets = true; focusComposer()"
                    >
                      <template #icon><NIcon :component="PersonCircleOutline" /></template>
                      {{ systemOverrideName || t('rolePreset') }}
                    </NButton>
                    <span v-if="systemOverrideName" class="role-clear-x" @click.stop="clearRole()" :title="t('clearRole')">✕</span>
                </div>
                <NModal preset="card" style="width:80vw;height:80vh;max-width:900px" :bordered="false" :mask-closable="false" :close-on-esc="true" :title="t('rolePreset')" :show="showRolePresets" @update:show="showRolePresets = $event">
                  <NInput v-model:value="roleSearch" size="small" :placeholder="t('searchRoles')" style="margin-bottom:12px" />
                  <div style="display:flex;flex-wrap:wrap;gap:10px;max-height:60vh;overflow-y:auto;">
                    <div v-for="c in filteredRoles" :key="c.name" class="qc-card" :class="{ 'qc-card--active': c.name === systemOverrideName }" @click="chooseRole(c)">
                      <div class="qc-title">{{ c.name }}</div>
                      <div class="qc-preview">{{ c.preview }}</div>
                    </div>
                  </div>
                </NModal>
              </div>
              <div class="composer-toolbar-right">
                <NPopover v-if="modelList.length" placement="top" :show-arrow="false" :delay="150" trigger="hover">
                  <template #trigger>
                    <span class="composer-model" role="button" tabindex="0" @keydown.enter.prevent>
                      <NIcon :component="FlashOutline" />{{ currentModel || 'model' }}
                    </span>
                  </template>
                  <div class="model-picker">
                    <div v-for="m in modelList" :key="m.model_name" class="model-picker-item" :class="{ 'model-picker-item--active': m.model_name === currentModel }" role="button" tabindex="0" @click="switchModel(m.model_name)" @keydown.enter.prevent="switchModel(m.model_name)">
                      <span class="model-picker-check">{{ m.model_name === currentModel ? '✓' : '' }}</span>{{ m.model_name }}
                    </div>
                  </div>
                </NPopover>
                <NPopover v-if="contextUsage" placement="bottom" :show-arrow="false" :delay="200" :trigger="'hover'">
                  <template #trigger>
                    <span class="composer-context" :title="`${contextUsage.tokens} / ${contextUsage.total} tokens`">{{ actionLabels.context }} {{ contextUsage.pct }}%</span>
                  </template>
                  <div class="context-tooltip">
                    <span class="ctx-tokens">{{ contextUsage.tokens }} / {{ contextUsage.total }} tokens</span>
                    <div class="ctx-actions">
                      <NButton size="tiny" @click="viewContext">{{ actionLabels.viewContext }}</NButton>
                      <NButton size="tiny" type="primary" :disabled="isRunning || compactingContext" :loading="compactingContext" @click="compactContext">
                        {{ compactingContext ? actionLabels.compacting : actionLabels.compactContext }}
                      </NButton>
                    </div>
                  </div>
                </NPopover>
                <NTooltip v-if="isRunning">
                  <template #trigger>
                    <NButton circle type="error" size="small" :aria-label="t('cancel')" @click="cancelTask">
                      <template #icon><NIcon :component="StopCircleOutline" /></template>
                    </NButton>
                  </template>
                  {{ t('cancel') }}
                </NTooltip>
                <NTooltip v-if="!isRunning">
                  <template #trigger>
                    <NButton circle type="primary" size="small" :aria-label="t('send')" :disabled="(!input.trim() && !attachments.length) || isUploading" @click="submitPrompt">
                      <template #icon><NIcon :component="PaperPlaneOutline" /></template>
                    </NButton>
                  </template>
                  {{ t('send') }}
                </NTooltip>
              </div>            </div>
          </div>
        </div>
        <div v-if="!activeConversationId" class="composer-column"><QuickCommandBar ref="qcBar" @invoke="onQuickCommand" /></div>
      </div>
    </section>

    <div v-if="previewImage" class="image-preview-overlay" @click="previewImage = null">
      <img :src="previewImage" :alt="actionLabels.previewAttachment" @click.stop />
    </div>

    <div v-if="initialLoading || conversationLoading > 0" class="chat-loading-overlay" aria-live="polite">
      <div class="chat-loading-box">
        <NSpin size="large" />
        <span>{{ t('loading') }}</span>
      </div>
    </div>

    <NDrawer v-model:show="sidebarVisible" placement="left" :width="280">
      <NDrawerContent :title="t('recentConversations')">
        <div class="drawer-list">
          <NButton type="primary" block @click="openNewConversation"><template #icon><NIcon :component="AddOutline" /></template>{{ t('newConversation') }}</NButton>
          <NButton block @click="sidebarVisible = false; openManagement()"><template #icon><NIcon :component="SettingsOutline" /></template>{{ managementTitle }}</NButton>
          <NInput v-model:value="conversationSearch" size="small" clearable :placeholder="t('searchConversations')">
            <template #prefix><NIcon :component="SearchOutline" /></template>
          </NInput>
          <NButton v-for="conversation in filteredConversations" :key="conversation.id" text class="drawer-item" @click="selectConversation(conversation.id)">
            {{ timerTitle(conversation.title) }}
          </NButton>
        </div>
      </NDrawerContent>
    </NDrawer>

    <ManagementCenter
      :show="managementVisible"
      :section="managementSection"
      :title="managementTitle"
      :items="managementMenuItems"
      @update:show="managementVisible = $event"
      @update:section="selectManagementSection"
    >
      <div class="management-page">
        <!-- 技能管理 -->
        <div v-if="managementSection === 'skills'" class="panel-section">
          <div class="panel-section-actions">
            <NInput v-model:value="panelQuery" size="small" clearable :placeholder="actionLabels.searchPlaceholder" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" @click="openSkillEdit()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ actionLabels.newSkill }}
            </NButton>
          </div>
          <div v-if="!filteredSkills.length" class="panel-placeholder"><p>{{ panelQuery ? actionLabels.noMatch : actionLabels.emptyList }}</p></div>
          <div v-else class="resource-card-list">
            <div v-for="skill in filteredSkills" :key="skill.name" class="manage-card manage-card--actionable">
              <div class="manage-card-head">
                <strong class="manage-card-title manage-title-with-icon">
                  <NIcon :component="CodeSlashOutline" />{{ skill.name }}
                </strong>
              </div>
              <p v-if="skill.description" class="manage-card-desc" :title="skill.description">{{ skill.description }}</p>
              <div class="manage-card-actions manage-card-actions--overlay">
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.edit" @click="openSkillEdit(skill)">
                      <template #icon><NIcon :component="CreateOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.edit }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" type="error" :aria-label="actionLabels.delete" @click="deleteSkill(skill)">
                      <template #icon><NIcon :component="TrashOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.delete }}
                </NTooltip>
              </div>
            </div>
          </div>
        </div>
        <!-- 工具（MCP）管理 -->
        <div v-else-if="managementSection === 'tools'" class="panel-section">
          <div class="panel-section-actions">
            <NInput v-model:value="panelQuery" size="small" clearable :placeholder="actionLabels.searchPlaceholder" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" @click="openToolEdit()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ actionLabels.newTool }}
            </NButton>
          </div>
          <template v-if="filteredBuiltinTools.length">
            <div class="panel-group-head">
              <span class="panel-group-label manage-title-with-icon">
                <NIcon :component="CodeSlashOutline" />{{ actionLabels.builtinTools }}
              </span>
              <span class="panel-group-count">{{ filteredBuiltinTools.length }}</span>
            </div>
            <div class="builtin-tool-list">
              <span
                v-for="tool in filteredBuiltinTools"
                :key="tool.name"
                class="builtin-tool-chip"
                :title="tool.description"
              >
                <code>{{ tool.name }}</code>
              </span>
            </div>
          </template>
          <template v-if="filteredToolList.length">
            <div class="panel-group-head panel-group-head--mcp">
              <span class="panel-group-label manage-title-with-icon">
                <NIcon :component="ConstructOutline" />{{ actionLabels.mcpServers }}
              </span>
              <span class="panel-group-count">{{ filteredToolList.length }}</span>
            </div>
            <div class="resource-card-list">
              <div v-for="tool in filteredToolList" :key="tool.name" class="manage-card manage-card--actionable">
                <div class="manage-card-head manage-card-head--group" @click="toggleToolGroup(tool.name)">
                  <span class="tool-group-left">
                    <span class="tool-group-caret">{{ expandedToolGroups.includes(tool.name) ? '▾' : '▸' }}</span>
                    <span class="manage-card-title">{{ tool.name }}</span>
                    <span v-if="tool.tools?.length" class="tool-count">{{ tool.tools.length }}</span>
                  </span>
                </div>
                <div class="manage-card-actions manage-card-actions--overlay">
                  <NTooltip>
                    <template #trigger>
                      <NButton quaternary circle size="tiny" :aria-label="actionLabels.edit" @click.stop="openToolEdit(tool)">
                        <template #icon><NIcon :component="CreateOutline" /></template>
                      </NButton>
                    </template>
                    {{ actionLabels.edit }}
                  </NTooltip>
                </div>
                <code v-if="tool.command" class="manage-card-cmd">{{ tool.command }}</code>
                <div v-if="expandedToolGroups.includes(tool.name)" class="tool-item-list">
                  <div v-for="t in tool.tools || []" :key="t.name" class="tool-item">
                    <div class="tool-item-head">
                      <code>{{ t.name }}</code>
                    </div>
                    <p v-if="t.description" class="tool-item-desc">{{ t.description }}</p>
                  </div>
                  <div v-if="!(tool.tools || []).length" class="panel-placeholder"><p>{{ actionLabels.emptyList }}</p></div>
                </div>
              </div>
            </div>
          </template>
          <div v-if="!filteredToolList.length && !filteredBuiltinTools.length" class="panel-placeholder">
            <p>{{ panelQuery ? actionLabels.noMatch : actionLabels.emptyList }}</p>
          </div>
        </div>
        <!-- Agent 管理 -->
        <div v-else-if="managementSection === 'agents'" class="panel-section">
          <div class="panel-section-actions">
            <NInput v-model:value="panelQuery" size="small" clearable :placeholder="actionLabels.searchPlaceholder" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" @click="openAgentEdit()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ actionLabels.newAgent }}
            </NButton>
          </div>
          <div v-if="!filteredAgents.length" class="panel-placeholder"><p>{{ panelQuery ? actionLabels.noMatch : actionLabels.emptyList }}</p></div>
          <div v-else class="resource-card-list">
            <div v-for="agent in filteredAgents" :key="agent.name" class="manage-card manage-card--actionable" :class="{ 'manage-card--disabled': !agent.enabled }">
              <div class="manage-card-head">
                <strong class="manage-title-with-icon">
                  <NIcon :component="SparklesOutline" />{{ displayAgentName(agent.name) }}
                </strong>
              </div>
              <p v-if="agent.description" class="manage-card-desc" :title="agent.description">{{ agent.description }}</p>
              <div v-if="(agent.subagents || []).length" class="manage-card-chips">
                <span class="manage-chip-label">{{ actionLabels.linkedSubagents }}</span>
                <NTag v-for="s in agent.subagents.slice(0, 4)" :key="s" size="small" type="warning">{{ displayAgentName(s) }}</NTag>
                <NTag v-if="agent.subagents.length > 4" size="small">+{{ agent.subagents.length - 4 }}</NTag>
              </div>
              <div v-if="agentSkillNames(agent).length" class="manage-card-chips">
                <NTag v-for="s in agentSkillNames(agent).slice(0, 4)" :key="s" size="small" type="info">{{ s }}</NTag>
                <NTag v-if="agentSkillNames(agent).length > 4" size="small">+{{ agentSkillNames(agent).length - 4 }}</NTag>
              </div>
              <div v-if="agentToolNames(agent).length" class="manage-card-chips">
                <NTag v-for="toolName in agentToolNames(agent).slice(0, 6)" :key="toolName" size="small" type="success">{{ toolName }}</NTag>
                <NTag v-if="agentToolNames(agent).length > 6" size="small">+{{ agentToolNames(agent).length - 6 }}</NTag>
              </div>
              <div v-if="(agent.knowledge || []).length" class="manage-card-chips">
                <span class="manage-chip-label">{{ actionLabels.linkedKnowledge }}</span>
                <NTag v-for="knowledgeId in agent.knowledge.slice(0, 3)" :key="knowledgeId" size="small" type="warning">{{ knowledgeId }}</NTag>
                <NTag v-if="agent.knowledge.length > 3" size="small">+{{ agent.knowledge.length - 3 }}</NTag>
              </div>
              <div class="manage-card-actions manage-card-actions--overlay">
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.edit" @click="openAgentEdit(agent)">
                      <template #icon><NIcon :component="CreateOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.edit }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" type="error" :aria-label="actionLabels.delete" :disabled="agent.name === 'default'" @click="deleteAgent(agent)">
                      <template #icon><NIcon :component="TrashOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.delete }}
                </NTooltip>
              </div>
            </div>
          </div>
        </div>
        <!-- 知识库管理 -->
        <div v-else-if="managementSection === 'knowledge'" class="panel-section panel-section--knowledge">
          <div class="panel-section-actions">
            <NInput v-model:value="panelQuery" size="small" clearable :placeholder="actionLabels.searchPlaceholder" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" :disabled="!knowledgeConfig.reachable" @click="openKnowledgeManager()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ actionLabels.knowledgeManage }}
            </NButton>
          </div>
          <NAlert v-if="!knowledgeConfig.reachable" type="warning" :show-icon="true" class="knowledge-alert">
            <div class="knowledge-alert-row">
              <span>{{ actionLabels.knowledgeUnreachable }}</span>
              <NButton size="tiny" type="warning" :loading="knowledgeStarting" :disabled="knowledgeStarting" @click="startKnowledge()">
                {{ knowledgeStarting ? actionLabels.knowledgeStarting : actionLabels.knowledgeStart }}
              </NButton>
            </div>
          </NAlert>
          <div v-if="!filteredKnowledge.length" class="panel-placeholder">
            <p>{{ panelQuery ? actionLabels.noMatch : actionLabels.emptyList }}</p>
          </div>
          <div v-else class="resource-card-list">
            <div v-for="kb in filteredKnowledge" :key="kb.id" class="manage-card">
              <div class="manage-card-head">
                <strong class="manage-card-title">{{ kb.name }}</strong>
                <NTag size="small" :type="kb.type === 'vector' ? 'info' : 'default'">{{ kb.type === 'vector' ? actionLabels.knowledgeVector : actionLabels.knowledgeNormal }}</NTag>
              </div>
              <p v-if="kb.documentCount !== undefined || kb.recordCount !== undefined" class="manage-card-desc">
                {{ actionLabels.knowledgeDocs }}: {{ kb.documentCount ?? 0 }} · {{ actionLabels.knowledgeRecords }}: {{ kb.recordCount ?? 0 }}
              </p>
            </div>
          </div>
          <div class="knowledge-manage-btn">
            <NButton size="small" type="error" quaternary :loading="knowledgeStopping" :disabled="!knowledgeConfig.reachable || knowledgeStopping" @click="stopKnowledge()">
              <template #icon v-if="!knowledgeStopping"><NIcon :component="StopOutline" /></template>{{ knowledgeStopping ? actionLabels.knowledgeStopping : actionLabels.knowledgeStop }}
            </NButton>
          </div>
        </div>
        <!-- 角色预设管理 -->
        <div v-else-if="managementSection === 'roles'" class="panel-section">
          <div class="panel-section-actions">
            <NInput v-model:value="roleSearch" size="small" clearable :placeholder="t('searchRoles')" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" @click="openRoleEditor()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ t('newRole') }}
            </NButton>
          </div>
          <div v-if="!filteredRoles.length" class="panel-placeholder"><p>{{ roleSearch ? actionLabels.noMatch : actionLabels.emptyList }}</p></div>
          <div v-else class="resource-card-list resource-card-list--two-columns">
            <div v-for="role in filteredRoles" :key="role.name" class="manage-card manage-card--actionable">
              <div class="manage-card-head">
                <strong class="manage-card-title manage-title-with-icon">
                  <NIcon :component="PersonCircleOutline" />{{ role.name }}
                </strong>
                <NTag v-if="role.builtin" size="small">{{ locale === 'zh' ? '内置' : locale === 'ja' ? '組み込み' : 'Built-in' }}</NTag>
              </div>
              <p class="manage-card-desc" :title="role.preview || role.content">{{ role.preview || role.content }}</p>
              <div class="manage-card-actions manage-card-actions--overlay">
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.edit" :disabled="role.builtin" @click="openRoleEditor(role)">
                      <template #icon><NIcon :component="CreateOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.edit }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" type="error" :aria-label="actionLabels.delete" :disabled="role.builtin" @click="deleteRole(role.name)">
                      <template #icon><NIcon :component="TrashOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.delete }}
                </NTooltip>
              </div>
            </div>
          </div>
        </div>
        <!-- 快捷指令管理 -->
        <div v-else-if="managementSection === 'commands'" class="panel-section">
          <div class="panel-section-actions">
            <NInput v-model:value="commandSearch" size="small" clearable :placeholder="actionLabels.searchPlaceholder" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" @click="openCommandEdit()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ t('quickCommands.new') }}
            </NButton>
          </div>
          <div v-if="!filteredManagementQuickCommands.length" class="panel-placeholder">
            <p>{{ commandSearch ? actionLabels.noMatch : actionLabels.emptyList }}</p>
          </div>
          <div v-else class="resource-card-list resource-card-list--two-columns">
            <div v-for="command in filteredManagementQuickCommands" :key="command.id" class="manage-card manage-card--actionable">
              <div class="manage-card-head">
                <strong class="manage-card-title manage-title-with-icon">
                  <NIcon :component="FlashOutline" />{{ command.title }}
                </strong>
              </div>
              <p class="manage-card-desc" :title="command.content">{{ command.content }}</p>
              <div class="manage-card-chips">
                <NTag size="small" type="success">{{ displayAgentName(command.agent) }}</NTag>
                <NTag size="small" type="info">{{ command.model || t('quickCommands.defaultModel') }}</NTag>
                <NTag v-if="command.role" size="small" type="warning">{{ command.role }}</NTag>
                <NTag v-if="command.showOnHome !== false" size="small">{{ t('quickCommands.shownOnHome') }}</NTag>
              </div>
              <div class="manage-card-actions manage-card-actions--overlay">
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" :aria-label="actionLabels.edit" @click="openCommandEdit(command)">
                      <template #icon><NIcon :component="CreateOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.edit }}
                </NTooltip>
                <NTooltip>
                  <template #trigger>
                    <NButton quaternary circle size="tiny" type="error" :aria-label="actionLabels.delete" @click="deleteManagementQuickCommand(command)">
                      <template #icon><NIcon :component="TrashOutline" /></template>
                    </NButton>
                  </template>
                  {{ actionLabels.delete }}
                </NTooltip>
              </div>
            </div>
          </div>
        </div>
        <!-- 定时任务管理 -->
        <div v-else-if="managementSection === 'timers'" class="panel-section panel-section--timers">
          <div class="panel-section-actions">
            <NInput v-model:value="timerSearch" size="small" clearable :placeholder="t('timers.search')" class="panel-search-input">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NButton size="small" type="primary" @click="openTimerEdit()">
              <template #icon><NIcon :component="AddOutline" /></template>{{ t('timers.newTask') }}
            </NButton>
          </div>
          <div v-if="!panelTimers.length" class="panel-placeholder"><p>{{ t('timers.noTasks') }}</p></div>
          <div v-for="task in panelTimers" :key="task.id" class="manage-card" :class="{ 'manage-card--disabled': !task.enabled, 'manage-card--active': activeConversationId === task.sessionId, 'manage-card--clickable': !!task.sessionId }" @click="task.sessionId && selectConversation(task.sessionId)">
            <div class="manage-card-head">
              <button type="button" class="manage-card-title-btn" :class="{ 'manage-card-title-btn--active': activeConversationId === task.sessionId }" :title="task.sessionId ? t('timers.openSession') : undefined" :disabled="!task.sessionId" @click.stop="task.sessionId && selectConversation(task.sessionId)">
                <strong class="manage-card-title">{{ task.name }}</strong>
              </button>
              <div class="manage-card-head-right">
                <span class="timer-run-badge">{{ t('timers.ranTimes', { count: (task.runHistory || []).length }) }}</span>
                <NSwitch size="small" :value="task.enabled" @click.stop @update:value="toggleTimer(task)" />
              </div>
            </div>
            <p class="manage-card-desc" :title="task.description || task.prompt">{{ task.description || task.prompt }}</p>
            <div class="manage-card-chips">
              <NTag size="small" type="warning">{{ t('timers.agent') }}: {{ timerAgentLabel(task) }}</NTag>
              <NTag size="small" type="info">{{ timerScheduleLabel(task) }}</NTag>
              <NTag size="small" :type="timerStatusType(task)">{{ timerStatusLabel(task) }}</NTag>
              <span v-if="task.schedule && task.cronValid === false" class="timer-cron-invalid">{{ t('timers.cronInvalid') }}</span>
              <span v-else-if="task.nextRunAt" class="timer-next-run">{{ t('timers.nextRun') }}: {{ formatTimerTime(task.nextRunAt) }}</span>
              <span v-if="task.lastRunAt" class="timer-last-run">{{ t('timers.lastRun') }}: {{ formatTimerTime(task.lastRunAt) }}</span>
            </div>
            <div class="manage-card-actions" @click.stop>
              <NButton quaternary size="tiny" :loading="timersRefreshing" :disabled="!!timerBusyId || timersRefreshing" @click="refreshTask(task)">{{ t('timers.refresh') }}</NButton>
              <NButton quaternary size="tiny" :loading="timerBusyId === task.id" :disabled="!!timerBusyId" @click="runTimer(task)">{{ t('timers.run') }}</NButton>
              <NButton quaternary size="tiny" @click="openTimerEdit(task)">{{ actionLabels.edit }}</NButton>
              <NButton quaternary size="tiny" type="error" @click="timerDeleteTarget = task">{{ actionLabels.delete }}</NButton>
            </div>
            <!-- 执行记录常开（最新在前）；一览模式（timersScopeAll）下隐藏 -->
            <div v-if="!timersScopeAll" class="timer-runs" @click.stop>
              <div
                v-for="run in sortedRuns(task)"
                :key="run.id"
                class="timer-run"
                :class="{ 'timer-run--clickable': !!(run.assistantMessageId && task.sessionId) }"
                :title="run.assistantMessageId && task.sessionId ? t('timers.runJump') : undefined"
                @click="timerRunClicked(task, run)"
              >
                <div class="timer-run-head">
                  <NTag size="tiny" :type="run.status === 'success' ? 'success' : run.status === 'failed' ? 'error' : run.status === 'running' ? 'info' : 'default'">{{ run.status }}</NTag>
                  <span class="timer-run-time">{{ formatTimerTime(run.startedAt) }}<template v-if="run.finishedAt">・{{ t('timers.duration') }} {{ formatDuration(run.startedAt, run.finishedAt) }}</template></span>
                </div>
                <p v-if="run.outputSummary" class="timer-run-summary">{{ run.outputSummary }}</p>
              </div>
              <div v-if="!(task.runHistory || []).length" class="panel-placeholder"><p>{{ t('timers.noRuns') }}</p></div>
            </div>
          </div>
        </div>
      </div>
    </ManagementCenter>

    <NModal preset="card" style="width:80vw;max-width:1000px;height:80vh;" :mask-closable="false" :title="t('roleEditor')" :show="showRoleEditor" @update:show="showRoleEditor = $event">
      <NInput v-model:value="roleEditorName" size="small" :placeholder="t('roleNamePlaceholder')" style="margin-bottom:10px" />
      <div class="role-editor-layout">
        <div class="role-editor-column">
          <div class="manage-form-label">{{ t('rolePrompt') }}</div>
          <textarea v-model="roleEditorContent" class="role-editor-textarea" :placeholder="t('rolePromptPlaceholder')"></textarea>
        </div>
        <div class="role-editor-column">
          <div class="manage-form-label">{{ t('preview') }}</div>
          <div class="role-editor-preview">
            <MarkdownMessage :content="roleEditorContent" />
          </div>
        </div>
      </div>
      <template #footer>
        <div class="manage-modal-footer">
          <NButton size="small" @click="showRoleEditor = false">{{ t('quickCommands.cancel') }}</NButton>
          <NButton size="small" type="primary" @click="saveRole">{{ t('quickCommands.save') }}</NButton>
        </div>
      </template>
    </NModal>

    <NModal :show="commandModalVisible" :mask-closable="false" @update:show="commandModalVisible = $event">
      <NCard :title="editingCommandId ? t('quickCommands.edit') : t('quickCommands.new')" :bordered="false" class="manage-modal" role="dialog">
        <div class="manage-form">
          <label class="manage-form-label">{{ t('quickCommands.title') }}</label>
          <NInput v-model:value="commandForm.title" :placeholder="t('quickCommands.titlePlaceholder')" maxlength="30" show-count />
          <label class="manage-form-label">{{ t('quickCommands.rolePreset') }}</label>
          <NSelect v-model:value="commandForm.role" :options="commandRoleOptions" />
          <label class="manage-form-label">{{ t('quickCommands.prefill') }}</label>
          <NInput v-model:value="commandForm.content" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" :placeholder="t('quickCommands.prefillPlaceholder')" />
          <div class="quick-command-form-grid">
            <div>
              <label class="manage-form-label">{{ t('quickCommands.agent') }}</label>
              <NSelect v-model:value="commandForm.agent" :options="commandAgentOptions" />
            </div>
            <div>
              <label class="manage-form-label">{{ t('quickCommands.model') }}</label>
              <NSelect v-model:value="commandForm.model" :options="commandModelOptions" />
            </div>
          </div>
          <div class="quick-command-form-grid">
            <div class="manage-form-row">
              <span>{{ t('quickCommands.webSearch') }}</span>
              <NSwitch v-model:value="commandForm.webSearchOn" size="small" />
            </div>
            <div class="manage-form-row">
              <span>{{ t('quickCommands.history') }}</span>
              <NSwitch v-model:value="commandForm.historyOn" size="small" />
            </div>
          </div>
          <div class="quick-command-form-grid">
            <div class="manage-form-row">
              <span>{{ t('quickCommands.jpMark') }}</span>
              <NSwitch v-model:value="commandForm.jpMarkOn" size="small" />
            </div>
            <div class="manage-form-row">
              <span>{{ t('quickCommands.autoSend') }}</span>
              <NSwitch v-model:value="commandForm.autoSend" size="small" />
            </div>
          </div>
          <div class="manage-form-row">
            <span>{{ t('quickCommands.showOnHome') }}</span>
            <NSwitch v-model:value="commandForm.showOnHome" size="small" />
          </div>
          <div class="manage-form-actions">
            <NButton @click="commandModalVisible = false">{{ t('quickCommands.cancel') }}</NButton>
            <NButton type="primary" @click="saveManagementQuickCommand">{{ t('quickCommands.save') }}</NButton>
          </div>
        </div>
      </NCard>
    </NModal>

    <!-- 定时任务新建/编辑弹窗 -->
    <NModal :show="timerEditVisible" :mask-closable="false" @after-leave="timerEditVisible = false">
      <NCard :title="timerEditTarget ? t('timers.editTask') : t('timers.createTitle')" :bordered="false" class="manage-modal" role="dialog">
        <div class="manage-form">
          <label class="manage-form-label">{{ t('timers.name') }}</label>
          <NInput v-model:value="timerDraft.name" size="small" :placeholder="t('timers.namePlaceholder')" />
          <label class="manage-form-label">{{ t('timers.description') }}</label>
          <NInput v-model:value="timerDraft.description" size="small" :placeholder="t('timers.descriptionPlaceholder')" />
          <label class="manage-form-label">{{ t('timers.agent') }}</label>
          <NSelect v-model:value="timerDraft.agent" size="small" :options="timerAgentOptions" :placeholder="t('timers.agentPlaceholder')" />
          <label class="manage-form-label">{{ t('timers.prompt') }}</label>
          <NInput v-model:value="timerDraft.prompt" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" :placeholder="t('timers.promptPlaceholder')" />
          <div class="timer-schedule-mode">
            <NRadioGroup v-model:value="timerDraft.mode" size="small">
              <NRadioButton value="cron">{{ t('timers.scheduleCron') }}</NRadioButton>
              <NRadioButton value="interval">{{ t('timers.scheduleInterval') }}</NRadioButton>
            </NRadioGroup>
          </div>
          <div v-if="timerDraft.mode === 'cron'">
            <label class="manage-form-label">{{ t('timers.cronPreset') }}</label>
            <NSelect
              size="small"
              :options="cronPresets"
              :placeholder="t('timers.cronPresetPlaceholder')"
              @update:value="(v: string) => { timerDraft.mode = 'cron'; timerDraft.cron = v }"
            />
            <label class="manage-form-label">{{ t('timers.scheduleCron') }}</label>
            <div class="cron-input-row">
              <NInput v-model:value="timerDraft.cron" size="small" :placeholder="t('timers.cronPlaceholder')" />
              <NTooltip placement="right">
                <template #trigger>
                  <NButton quaternary circle size="small" class="cron-help-btn" :aria-label="t('timers.cronHelp')">
                    <template #icon><NIcon :component="HelpCircleOutline" /></template>
                  </NButton>
                </template>
                <div class="cron-help">
                  <p class="cron-help-title">{{ t('timers.cronHelpTitle') }}</p>
                  <p class="cron-help-row"><code>*</code> {{ t('timers.cronHelpWildcard') }}</p>
                  <p class="cron-help-row"><code>*/15</code> {{ t('timers.cronHelpStep') }}</p>
                  <p class="cron-help-row"><code>1-5</code> {{ t('timers.cronHelpRange') }}</p>
                  <p class="cron-help-row"><code>1,2,3</code> {{ t('timers.cronHelpList') }}</p>
                  <p class="cron-help-title">{{ t('timers.cronHelpExamples') }}</p>
                  <p class="cron-help-row"><code>0 9 * * *</code> {{ t('timers.cronExDaily') }}</p>
                  <p class="cron-help-row"><code>0 * * * *</code> {{ t('timers.cronExHourly') }}</p>
                  <p class="cron-help-row"><code>*/10 * * * *</code> {{ t('timers.cronEx10min') }}</p>
                  <p class="cron-help-row"><code>0 9 * * 1-5</code> {{ t('timers.cronExWorkday') }}</p>
                  <p class="cron-help-row"><code>0 9 1 * *</code> {{ t('timers.cronExMonthly') }}</p>
                </div>
              </NTooltip>
            </div>
            <p v-if="timerDraft.mode === 'cron' && timerDraft.cron.trim() && !cronExplainValid" class="cron-preview cron-preview--invalid">{{ t('timers.cronInvalid') }}</p>
            <p v-else-if="cronExplain" class="cron-preview cron-preview--ok">{{ t('timers.cronExplain') }}: {{ cronExplain }}</p>
            <p v-else-if="cronPreview" class="cron-preview">{{ t('timers.cronPreview') }}: {{ cronPreview }}</p>
          </div>
          <div v-else>
            <label class="manage-form-label">{{ t('timers.scheduleInterval') }}</label>
            <NInputNumber v-model:value="timerDraft.interval" size="small" :min="1" :max="10080" style="width: 100%" />
          </div>
          <div class="manage-form-row">
            <NSwitch v-model:value="timerDraft.enabled" size="small" />
            <span>{{ t('timers.enabled') }}</span>
          </div>
        </div>
        <template #footer>
          <div class="manage-modal-footer">
            <NButton size="small" @click="timerEditVisible = false">{{ t('timers.close') }}</NButton>
            <NButton size="small" type="primary" @click="saveTimer">{{ t('timers.save') }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <!-- 定时任务删除确认 -->
    <NModal
      :show="!!timerDeleteTarget"
      :mask-closable="false"
      @update:show="(visible) => { if (!visible) timerDeleteTarget = null }"
    >
      <NCard :title="t('timers.deleteConfirmTitle')" :bordered="false" class="manage-modal" role="dialog">
        <p class="delete-confirm-body">{{ t('timers.deleteConfirmBody') }}</p>
        <template #footer>
          <div class="manage-modal-footer">
            <NButton size="small" @click="timerDeleteTarget = null">{{ t('deleteConfirmCancel') }}</NButton>
            <NButton size="small" type="error" @click="confirmDeleteTimer">{{ t('deleteConfirmOk') }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <!-- 技能新建/编辑弹窗 -->
    <NModal :show="skillModalVisible" :mask-closable="false" @after-leave="skillModalVisible = false">
      <NCard :title="skillEditing ? actionLabels.edit : actionLabels.newSkill" :bordered="false" class="manage-modal manage-modal--large" role="dialog">
        <div class="manage-form skill-edit-form">
          <label class="manage-form-label">{{ actionLabels.skillName }}</label>
          <NInput v-model:value="skillForm.name" size="small" :placeholder="'name'" :disabled="skillEditing" />
          <label class="manage-form-label">{{ actionLabels.skillDescription }}</label>
          <NInput v-model:value="skillForm.description" size="small" :placeholder="'description'" />
          <div class="skill-edit-split">
            <div class="skill-edit-col">
              <label class="skill-edit-label">{{ actionLabels.skillBody }}</label>
              <NInput v-model:value="skillForm.content" type="textarea" :placeholder="'SKILL.md content (markdown)'" class="manage-form-code skill-edit-editor" />
            </div>
            <div class="skill-edit-col skill-edit-preview">
              <label class="skill-edit-label">Preview</label>
              <div class="skill-edit-preview-body">
                <MarkdownMessage v-if="skillForm.content" :content="skillForm.content" @open-file="openFilePreview" />
                <p v-else class="skill-edit-preview-empty">No content</p>
              </div>
            </div>
          </div>
        </div>
        <template #footer>
          <div class="manage-form-actions">
            <NButton @click="skillModalVisible = false">{{ t('cancel') }}</NButton>
            <NButton type="primary" @click="submitSkill">OK</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <!-- 工具新建/编辑弹窗 -->
    <!-- 文件预览弹窗（消息中绝对路径链接） -->
    <NModal :show="filePreviewVisible" :mask-closable="true" @after-leave="filePreviewVisible = false">
      <NCard :title="filePreview?.name || ''" :bordered="false" class="file-preview-modal" role="dialog">
        <NSpin v-if="filePreviewLoading" style="display:flex;justify-content:center;padding:40px 0;" />
        <pre v-else-if="filePreview" class="file-preview-content"><code>{{ filePreview.content }}</code></pre>
        <p v-else class="file-preview-empty">{{ actionLabels.emptyList }}</p>
        <template #footer>
          <div class="manage-form-actions">
            <NButton @click="filePreviewVisible = false">{{ actionLabels.close }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <NModal :show="toolModalVisible" :mask-closable="false" @after-leave="toolModalVisible = false">
      <NCard :title="toolEditing ? actionLabels.edit : actionLabels.newTool" :bordered="false" class="manage-modal manage-modal--wide" role="dialog">
        <div class="manage-form">
          <NInput v-if="!toolForm.name" v-model:value="toolForm.name" size="small" :placeholder="'name'" />
          <NInput v-model:value="toolForm.description" size="small" :placeholder="'description'" />
          <NInput v-model:value="toolForm.command" size="small" :placeholder="'command (e.g. npx)'" />
          <NInput v-model:value="toolForm.args" size="small" :placeholder="'args (one per line)'" />
          <NInput v-model:value="toolForm.cwd" size="small" :placeholder="'cwd (optional)'" />
          <NInput v-model:value="toolForm.env" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" :placeholder="'env (KEY=VALUE, one per line)'" class="manage-form-code" />
        </div>
        <template #footer>
          <div class="manage-form-actions">
            <NButton @click="toolModalVisible = false">{{ t('cancel') }}</NButton>
            <NButton type="primary" @click="submitTool">OK</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <!-- Agent 新建/编辑弹窗 -->
    <NModal :show="agentModalVisible" :mask-closable="false" @after-leave="agentModalVisible = false">
      <NCard :title="agentEditing ? actionLabels.edit : actionLabels.newAgent" :bordered="false" class="manage-modal manage-modal--large" role="dialog">
        <div class="manage-form manage-form-split">
          <div class="manage-form-left">
            <label class="manage-form-label">{{ actionLabels.agentNameLabel }}</label>
            <NInput v-model:value="agentForm.name" size="small" :placeholder="t('agentName')" :disabled="agentEditing" />
            <label class="manage-form-label">{{ actionLabels.agentDescriptionLabel }}</label>
            <NInput v-model:value="agentForm.description" size="small" :placeholder="t('agentDescription')" />
            <label class="manage-form-label">{{ actionLabels.taskDetail }}</label>
            <NInput v-model:value="agentForm.body" type="textarea" :placeholder="actionLabels.taskDetail" />
          </div>
          <div class="manage-form-right">
            <div class="agent-association-section">
              <label class="manage-form-label">{{ actionLabels.linkedSubagents }}</label>
              <div v-if="agentSubagentOptions.length" class="agent-checkbox-list">
                <NCheckbox
                  v-for="option in agentSubagentOptions"
                  :key="option.value"
                  :checked="agentForm.subagents.includes(option.value)"
                  @update:checked="(checked: boolean) => setAgentSubagent(option.value, checked)"
                >
                  {{ option.label }}
                </NCheckbox>
              </div>
              <div v-else class="agent-association-empty">{{ actionLabels.emptyList }}</div>
            </div>
            <div class="agent-association-section">
              <label class="manage-form-label">{{ actionLabels.linkedSkills }}</label>
              <div v-if="agentSkillOptions.length" class="agent-checkbox-list">
                <NCheckbox
                  v-for="option in agentSkillOptions"
                  :key="option.value"
                  :checked="agentForm.skills.includes(option.value)"
                  @update:checked="(checked: boolean) => setAgentSkill(option.value, checked)"
                >
                  {{ option.label }}
                </NCheckbox>
              </div>
              <div v-else class="agent-association-empty">{{ actionLabels.emptyList }}</div>
            </div>
            <div class="agent-association-section agent-association-section--tools">
              <label class="manage-form-label">{{ actionLabels.linkedTools }}</label>
              <div class="agent-tool-tree-node">
                <div class="agent-tool-tree-row">
                  <button
                    type="button"
                    class="agent-tool-tree-toggle"
                    :aria-expanded="isAgentToolNodeExpanded('builtin')"
                    @click="toggleAgentToolNode('builtin')"
                  >
                    <NIcon :component="isAgentToolNodeExpanded('builtin') ? ChevronDownOutline : ChevronForwardOutline" />
                    <NIcon :component="FolderOutline" />
                    <strong>{{ actionLabels.builtinTools }}</strong>
                  </button>
                  <NCheckbox
                    :checked="isAgentBuiltinGroupChecked()"
                    :indeterminate="isAgentBuiltinGroupIndeterminate()"
                    @update:checked="setAgentBuiltinGroup"
                  />
                </div>
                <div v-if="isAgentToolNodeExpanded('builtin')" class="agent-checkbox-list agent-checkbox-list--tools agent-tool-tree-children">
                  <NCheckbox
                    v-for="tool in builtinTools"
                    :key="tool.name"
                    :checked="agentForm.tools.inner_tools.includes(tool.name)"
                    @update:checked="(checked: boolean) => setAgentBuiltinTool(tool.name, checked)"
                  >
                    {{ tool.name }}
                  </NCheckbox>
                </div>
              </div>
              <div class="agent-tool-tree-node">
                <div class="agent-tool-tree-row">
                  <button
                    type="button"
                    class="agent-tool-tree-toggle"
                    :aria-expanded="isAgentToolNodeExpanded('mcp_server')"
                    @click="toggleAgentToolNode('mcp_server')"
                  >
                    <NIcon :component="isAgentToolNodeExpanded('mcp_server') ? ChevronDownOutline : ChevronForwardOutline" />
                    <NIcon :component="FolderOutline" />
                    <strong>mcp_server</strong>
                  </button>
                  <span class="agent-tool-tree-count">{{ toolList.length }}</span>
                </div>
                <div v-if="isAgentToolNodeExpanded('mcp_server')" class="agent-mcp-server-list">
                  <div v-for="server in toolList" :key="server.name" class="agent-tool-server">
                    <div class="agent-tool-tree-row">
                      <button
                        type="button"
                        class="agent-tool-tree-toggle"
                        :aria-expanded="isAgentToolNodeExpanded(`mcp:${server.name}`)"
                        @click="toggleAgentToolNode(`mcp:${server.name}`)"
                      >
                        <NIcon :component="isAgentToolNodeExpanded(`mcp:${server.name}`) ? ChevronDownOutline : ChevronForwardOutline" />
                        <NIcon :component="FolderOutline" />
                        <strong>{{ server.name }}</strong>
                      </button>
                      <NCheckbox
                        :checked="isAgentMcpServerChecked(server)"
                        :indeterminate="isAgentMcpServerIndeterminate(server)"
                        @update:checked="(checked: boolean) => setAgentMcpServer(server, checked)"
                      />
                    </div>
                    <div v-if="isAgentToolNodeExpanded(`mcp:${server.name}`)" class="agent-tool-children">
                      <NCheckbox
                        v-for="tool in server.tools || []"
                        :key="tool.name"
                        :checked="selectedAgentMcpTools(server.name).includes(tool.name)"
                        @update:checked="(checked: boolean) => setAgentMcpTool(server.name, tool.name, checked)"
                      >
                        <span class="agent-tool-label" :title="`mcp_server/${server.name}/${tool.name}`">
                          <code>{{ tool.name }}</code>
                          <small v-if="tool.description">{{ tool.description }}</small>
                        </span>
                      </NCheckbox>
                    </div>
                  </div>
                </div>
                <div v-if="!toolList.length && isAgentToolNodeExpanded('mcp_server')" class="agent-association-empty">
                  {{ actionLabels.emptyList }}
                </div>
              </div>
            </div>
            <div class="agent-association-section">
              <label class="manage-form-label">{{ actionLabels.linkedKnowledge }}</label>
              <div v-if="agentKnowledgeChoices.length" class="agent-checkbox-list">
                <NCheckbox
                  v-for="knowledge in agentKnowledgeChoices"
                  :key="knowledge.id"
                  :checked="agentForm.knowledge.includes(knowledge.id)"
                  @update:checked="(checked: boolean) => setAgentKnowledge(knowledge.id, checked)"
                >
                  <span class="agent-knowledge-label">
                    <span>{{ knowledge.name }}</span>
                    <small>{{ knowledge.documentCount }} {{ actionLabels.knowledgeDocs }}</small>
                  </span>
                </NCheckbox>
              </div>
              <div v-else class="agent-association-empty">
                {{ knowledgeConfig.reachable ? actionLabels.emptyList : actionLabels.knowledgeUnreachable }}
              </div>
            </div>
          </div>
        </div>
        <template #footer>
          <div class="manage-form-actions">
            <NButton @click="agentModalVisible = false">{{ t('cancel') }}</NButton>
            <NButton type="primary" @click="submitAgent">OK</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <NModal :show="Boolean(permissionRequest)" :mask-closable="false">
      <NCard :title="t('permissionTitle')" :bordered="false" class="permission-card" role="dialog">
        <p>{{ t('permissionPrefix') }} <strong>{{ permissionRequest?.toolName }}</strong></p>
        <pre class="permission-code">{{ JSON.stringify(permissionRequest?.rawInput ?? {}, null, 2) }}</pre>
        <template #footer>
          <div class="permission-actions">
            <NButton @click="respondPermission('reject')">{{ t('reject') }}</NButton>
            <NButton type="primary" @click="respondPermission('approve')">{{ t('approve') }}</NButton>
            <NButton type="warning" @click="respondPermission('approve_always')">{{ t('approveAlways') }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>
    <NModal :show="editModalVisible" :mask-closable="false" @after-leave="editTargetId = null">
      <NCard :title="actionLabels.editTitle" :bordered="false" class="edit-card" role="dialog">
        <div class="edit-body">
          <NInput
            v-model:value="editText"
            type="textarea"
            :autosize="{ minRows: 10, maxRows: 30 }"
            :placeholder="'Markdown'"
            class="edit-input"
          />
          <div class="edit-preview">
            <div class="edit-preview-label">{{ actionLabels.preview }}</div>
            <div class="edit-preview-body">
              <MarkdownMessage :content="editText" @open-file="openFilePreview" />
            </div>
          </div>
        </div>
        <template #footer>
          <div class="permission-actions">
            <NButton @click="editModalVisible = false">{{ actionLabels.cancel }}</NButton>
            <NButton type="primary" @click="saveEditMessage">{{ actionLabels.save }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>
    <NModal :show="Boolean(renameTarget)" :mask-closable="false" @after-leave="renameTarget = null">
      <NCard :title="actionLabels.renameTitle" :bordered="false" class="rename-card" role="dialog">
        <NInput
          v-model:value="renameText"
          type="text"
          :placeholder="actionLabels.renamePlaceholder"
          @keydown.enter.exact="saveRename"
        />
        <template #footer>
          <div class="permission-actions">
            <NButton @click="renameTarget = null">{{ actionLabels.cancel }}</NButton>
            <NButton type="primary" @click="saveRename">{{ actionLabels.save }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>

    <!-- 帮助（内容待定） -->
    <NModal :show="helpVisible" :mask-closable="true" @after-leave="helpVisible = false">
      <NCard :title="t('help')" :bordered="false" class="rename-card" role="dialog">
        <div class="help-body">
          <p class="help-placeholder">{{ t('helpPlaceholder') }}</p>
        </div>
      </NCard>
    </NModal>

    <StatisticsModal v-model:show="statisticsVisible" />


    <!-- Centered delete-session confirmation (NPopconfirm drifted inside the
         scrolling conversation list, so this uses a centered dialog instead) -->
    <NModal
      preset="dialog"
      type="warning"
      :show="Boolean(deleteTarget)"
      :title="t('deleteConfirmTitle')"
      :content="t('deleteConfirmBody')"
      :positive-text="t('deleteConfirmOk')"
      :negative-text="t('deleteConfirmCancel')"
      :positive-button-props="{ type: 'error' }"
      @positive-click="confirmDeleteConversation"
      @negative-click="deleteTarget = null"
      @close="deleteTarget = null"
      @update:show="(visible) => { if (!visible) deleteTarget = null }"
    />

    <!-- Knowledge base management: external moofile_base admin UI in an iframe -->
    <KnowledgeManageModal
      :show="knowledgeManageVisible"
      :src="knowledgeConfig.baseUrl || ''"
      :title="actionLabels.knowledgeManage"
      :loading-text="actionLabels.knowledgeLoading"
      @close="knowledgeManageVisible = false"
    />

  </main>
</template>

<style scoped>
.process-collapse { margin: 2px 0 10px; background:transparent; border:none; border-radius:0; }
.role-pop { display:flex; flex-direction:column; gap:6px; max-height:300px; overflow-y:auto; }
.qc-card { position:relative; flex:0 0 220px; padding:10px 12px; border:1px solid var(--ui-border); border-radius:10px; background:var(--ui-surface); color:var(--ui-text); cursor:pointer; transition:all .15s; }
.qc-card:hover { border-color:var(--ui-primary); background:var(--ui-primary-soft); }
.qc-card--active { border-color:var(--ui-primary); background:var(--ui-primary-soft); }
.qc-actions { position:absolute; top:4px; right:4px; display:none; gap:2px; }
.qc-card:hover .qc-actions { display:flex; }
.qc-btn { border:0; background:transparent; cursor:pointer; font-size:11px; color:var(--ui-text-muted); padding:0 2px; }
.qc-btn-del:hover { color:var(--ui-error); }
.role-clear-x { position:absolute; top:-4px; right:-4px; width:14px; height:14px; border-radius:50%; background:#ef4444; color:#fff; font-size:10px; line-height:14px; text-align:center; cursor:pointer; display:none; }
.role-clear-x:hover { background:#dc2626; }
div:hover > .role-clear-x { display:block; }
.qc-card .qc-title { font-size:13px; font-weight:600; }
.qc-card .qc-preview { font-size:12px; color:var(--ui-text-muted); margin-top:4px; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
.qc-card.qc-new { display:flex; flex-direction:column; align-items:center; justify-content:center; border-style:dashed; color:var(--ui-text-muted); }
.qc-new-plus { font-size:20px; }
.qc-new-label { font-size:12px; margin-top:4px; }
.qc-tags { display:flex; gap:4px; flex-wrap:wrap; margin-top:6px; }
.qc-tag { font-size:10px; padding:1px 6px; border-radius:4px; background:var(--ui-surface-muted); color:var(--ui-text-muted); }
.qc-tag--info { background:var(--ui-primary-soft); color:var(--ui-primary); }
.qc-tag--green { background:#f0fdf4; color:#16a34a; }
.qc-tag--orange { background:#fff7ed; color:#ea580c; }
.role-item { padding:8px 10px; border:1px solid var(--ui-border); border-radius:8px; cursor:pointer; transition:all .15s; }
.role-item:hover { border-color:var(--ui-primary); background:var(--ui-primary-soft); }
.role-item--active { border-color:var(--ui-primary); background:var(--ui-primary-soft); }
.role-clear { padding:6px; text-align:center; font-size:12px; color:#ef4444; cursor:pointer; }
.process-collapse :deep(.n-collapse-item__header) { padding:10px !important; font-size:11.5px; color:#94a3b8; line-height:1.5; }
.process-collapse :deep(.n-collapse-item__header:hover) { color:#64748b; }
.process-collapse :deep(.n-collapse-item__header .n-collapse-item__header-arrow) { font-size:12px; margin-right:4px; }
.process-collapse :deep(.n-collapse-item__content) { padding-top:6px; }
.process-collapse :deep(.n-collapse-item__content-inner) { padding:0 10px; }
.process-collapse :deep(.n-collapse-item) { border:none; }
.process-collapse__header { font-size:11.5px; color:#94a3b8; }
.process-collapse :deep(.tool-call-collapse) { margin:6px 0 !important; }
.process-collapse :deep(.tool-call-collapse .n-collapse-item__header) { padding:10px 14px !important; }
.process-collapse :deep(.tool-call-collapse .n-collapse-item:first-child > .n-collapse-item__header) { padding:10px 14px !important; }
.process-collapse :deep(.tool-call-collapse .n-collapse-item .n-collapse-item__content-wrapper .n-collapse-item__content-inner) { padding-top:0 !important; }
.process-collapse :deep(.n-collapse .n-collapse-item) { margin-left:0 !important; }
.process-collapse :deep(.thought-card) { margin:0 !important; }

.chat-app { --canvas:var(--ui-canvas); --surface:var(--ui-surface); --muted:var(--ui-surface-muted); --border:var(--ui-border); --text:var(--ui-text); --subtle:var(--ui-text-muted); height:100vh; height:100dvh; display:flex; overflow:hidden; background:var(--canvas); color:var(--text); }
.sidebar { width:284px; flex:0 0 284px; height:100%; min-height:0; display:flex; flex-direction:column; padding:18px 14px 14px; background:var(--surface); border-right:1px solid var(--border); overflow:hidden; overflow-x:hidden; } .sidebar .brand-row,.sidebar .sidebar-nav,.sidebar .conversation-search,.sidebar .conversation-list,.sidebar .sidebar-footer { overflow-x:hidden; }
.brand-row { display:flex; gap:10px; align-items:center; padding:0 10px 14px; } .brand-row strong { display:block; font-size:15px; } .brand-row span { display:block; margin-top:2px; color:var(--subtle); font-size:11px; } .sidebar-collapse-btn { margin-left:auto; } .sidebar-expand-btn { display:none; } .chat-app--collapsed .sidebar { width:0; flex:0 0 0; padding:0; overflow:hidden; border-right:0; } .mobile-menu { display:none; }
.brand-mark,.welcome-symbol { display:grid; place-items:center; width:32px; height:32px; color:var(--ui-surface); background:var(--ui-text); border-radius:8px; font-size:18px; }
.new-conversation { justify-content:flex-start; margin-bottom:12px; } .conversation-search { padding:0 2px 6px; } .conversation-label { padding:0 10px 6px; color:var(--subtle); font-size:12px; } .conversation-empty { padding:14px 10px; color:var(--subtle); font-size:12px; text-align:center; } .conversation-group-empty { padding:8px 10px; color:var(--subtle); font-size:12px; } .delete-confirm-body { display:block; margin-top:2px; color:var(--subtle); font-size:12px; }
 .sidebar-nav { display:flex; flex-direction:column; gap:4px; margin-bottom:10px; } .sidebar-management { padding-top:10px; border-top:1px solid var(--border); } .sidebar-management .sidebar-nav-item { width:100%; } .sidebar-nav-item { display:flex; align-items:center; gap:8px; padding:9px 10px; border:0; border-radius:8px; background:transparent; color:var(--text); font-size:14px; font-weight:500; text-align:left; cursor:pointer; } .sidebar-nav-item:hover { background:var(--muted); } .sidebar-nav-item .n-icon { font-size:17px; color:var(--subtle); } .sidebar-nav-item--active { background:var(--muted); font-weight:600; } .sidebar-nav-item--active .n-icon { color:var(--ui-primary); } .sidebar-nav-item--primary { background:var(--ui-primary); color:var(--ui-surface); font-weight:600; } .sidebar-nav-item--primary:hover { background:var(--ui-primary-hover); } .sidebar-nav-item--primary .n-icon { color:var(--ui-surface); }
.conversation-list { display:flex; flex:1 1 0; min-height:0; flex-direction:column; gap:3px; overflow:hidden; overflow-y:auto; } .conversation-group { display:flex; flex-direction:column; min-height:0; } .conversation-group--normal { flex:0 0 auto; } .conversation-group--timers { flex:0 1 auto; } .conversation-group--collapsed { flex:0 0 auto; } .conversation-group-list { display:flex; flex-direction:column; gap:3px; min-height:0; } .conversation-group-list::-webkit-scrollbar { width:6px; } .conversation-group-list::-webkit-scrollbar-thumb { background:var(--ui-border-strong); border-radius:3px; } .conversation-group-list::-webkit-scrollbar-track { background:transparent; } .conversation-item { position:relative; min-height:54px; padding:9px 28px 9px 34px; overflow:hidden; color:var(--text); text-align:left; background:transparent; border:0; border-radius:7px; cursor:pointer; } .conversation-item:hover { background:var(--muted); } .conversation-item.active { background:var(--ui-primary-soft); color:var(--ui-primary); }
.conversation-icon { position:absolute; left:10px; top:50%; transform:translateY(-50%); display:grid; place-items:center; color:var(--subtle); font-size:15px; } .conversation-item.active .conversation-icon { color:var(--ui-primary); } .conversation-item--disabled { opacity:.45; } .conversation-item--disabled .conversation-icon { color:var(--subtle); } .conversation-item--disabled .conversation-title { text-decoration:line-through; }
.conversation-title,.conversation-time { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .conversation-title { font-size:13px; } .conversation-time { margin-top:4px; color:var(--subtle); font-size:11px; } .conversation-line { display:flex; align-items:center; gap:6px; min-width:0; } .conversation-line .conversation-title { flex:0 1 auto; min-width:0; } .conversation-mode-tag { flex:0 0 auto; padding:0 6px; font-size:10px; line-height:1.7; color:#2563eb; background:rgba(37,99,235,.08); border-radius:5px; } .conversation-delete { position:absolute; right:8px; top:18px; display:none; color:var(--subtle); } .conversation-item:hover .conversation-delete { display:block; }
.sidebar-footer { display:flex; gap:7px; align-items:center; padding:10px; color:var(--subtle); font-size:12px; } .connection-dot { width:7px; height:7px; border-radius:50%; background:#aab4c0; } .connection-dot.connected { background:#16805b; }
.workspace { min-width:0; flex:1; display:flex; flex-direction:column; } .workspace-header { height:64px; flex:0 0 64px; display:flex; align-items:center; justify-content:space-between; padding:0 28px; background:var(--ui-overlay); border-bottom:1px solid var(--border); } .header-title,.header-actions { display:flex; align-items:center; gap:8px; } .header-title h1 { max-width:440px; margin:0; overflow:hidden; font-size:15px; font-weight:650; text-overflow:ellipsis; white-space:nowrap; } .header-title span { display:block; margin-top:3px; color:var(--subtle); font-size:11px; } .mobile-menu { display:none; } .locale-trigger .locale-short { margin-left:2px; font-size:13px; font-weight:600; color:var(--subtle); }
.timeline-wrap { position:relative; flex:1; min-height:0; display:flex; flex-direction:column; } .timeline { flex:1; overflow:auto; scroll-behavior:smooth; } .timeline::-webkit-scrollbar { width:6px; } .timeline::-webkit-scrollbar-thumb { background:var(--ui-border-strong); border-radius:3px; } .timeline::-webkit-scrollbar-track { background:transparent; } .scroll-bottom-btn { position:absolute; right:max(28px, calc((100% - 840px) / 2 + 28px)); bottom:28px; z-index:5; box-shadow:0 2px 10px rgba(0,0,0,.2); } .message-column,.composer-column { width:min(840px, calc(100% - 64px)); margin:0 auto; } .message-column { padding:32px 0 48px; } .welcome { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:100%; padding:48px 24px 150px; text-align:center; } .welcome-symbol { width:44px; height:44px; margin-bottom:16px; font-size:23px; } .welcome h2 { margin:0 0 8px; font-size:20px; } .welcome p { max-width:360px; margin:0; color:var(--subtle); font-size:14px; line-height:1.7; } .workspace--initial .timeline-wrap { flex:0 0 auto; margin-top:auto; } .workspace--initial .composer-wrap { flex:0 0 auto; margin-bottom:auto; padding:0 0 24px; } .welcome--mode { min-height:0; padding:24px; }
.welcome--mode .mode-tabs { display:flex; gap:10px; margin-bottom:18px; } .mode-tab { display:inline-flex; align-items:center; gap:6px; padding:8px 20px; border:1px solid var(--border); border-radius:999px; background:var(--surface); color:var(--text); font-size:14px; font-weight:500; cursor:pointer; transition:all .15s; } .mode-tab:hover { border-color:var(--ui-border-strong); } .mode-tab.active { border-color:var(--ui-primary); color:var(--ui-primary); background:var(--ui-primary-soft); }
.conversation-list,
.conversation-group-list,
.timeline {
  scrollbar-width: thin;
  scrollbar-color: var(--ui-border-strong) transparent;
}
.conversation-list::-webkit-scrollbar,
.conversation-group-list::-webkit-scrollbar,
.timeline::-webkit-scrollbar {
  width: 6px;
  height: 6px;
  background: transparent;
}
.conversation-list::-webkit-scrollbar-thumb,
.conversation-group-list::-webkit-scrollbar-thumb,
.timeline::-webkit-scrollbar-thumb {
  min-height: 36px;
  background: var(--ui-border-strong);
  border: 0;
  border-radius: 999px;
}
.conversation-list::-webkit-scrollbar-track,
.conversation-group-list::-webkit-scrollbar-track,
.timeline::-webkit-scrollbar-track {
  background: transparent;
}
.conversation-list::-webkit-scrollbar-button,
.conversation-group-list::-webkit-scrollbar-button,
.timeline::-webkit-scrollbar-button {
  display: none;
  width: 0;
  height: 0;
}
.message { display:flex; gap:10px; margin-bottom:24px; } .message--user { flex-direction:row-reverse; } .message-body { min-width:0; max-width:calc(100% - 42px); } .message--user .message-body { display:flex; flex-direction:column; align-items:flex-end; width:fit-content; max-width:72%; } .message--assistant .message-body { background:var(--ui-surface); border:1px solid var(--ui-border); border-radius:8px; padding:12px 14px; } .message-meta { display:flex; gap:8px; align-items:center; margin-bottom:6px; color:var(--subtle); font-size:12px; } .message-meta strong { color:var(--text); font-size:13px; } .message-bubble { border-radius:8px; } .user-bubble { max-width:100%; padding:11px 14px; background:var(--ui-primary-soft); } .user-bubble p { margin:0; white-space:pre-wrap; word-break:break-word; }
.assistant-content { margin-bottom:12px; } .message-status { display:inline-block; margin-top:10px; color:#b76a00; font-size:12px; }
.message-actions { display:flex; gap:2px; align-items:center; margin-top:7px; } .message-actions--user { justify-content:flex-end; } .message-menu-wrap { position:relative; display:inline-flex; } .message-menu { position:absolute; right:0; bottom:calc(100% + 6px); z-index:60; display:flex; flex-direction:column; min-width:130px; padding:4px; background:var(--ui-surface-raised); border:1px solid var(--ui-border); border-radius:8px; box-shadow:0 4px 16px rgba(0,0,0,.2); } .message-menu-item { display:flex; gap:6px; align-items:center; padding:6px 10px; color:var(--ui-text); background:transparent; border:0; border-radius:6px; cursor:pointer; font-size:13px; } .message-menu-item:hover { background:var(--ui-surface-muted); }
.attachment-list,.pending-attachments { display:flex; flex-wrap:wrap; gap:6px; margin-top:9px; } .attachment-chip { display:inline-flex; gap:5px; align-items:center; max-width:220px; padding:4px 8px; overflow:hidden; color:var(--ui-primary); background:var(--ui-surface); border:1px solid var(--ui-border-strong); border-radius:6px; font-size:12px; cursor:pointer; } .attachment-chip .attachment-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .attachment-chip button { flex-shrink:0; } .attachment-chip--image { max-width:260px; } .attachment-preview { width:34px; height:34px; flex:0 0 34px; object-fit:cover; border:1px solid var(--ui-border); border-radius:4px; } .attachment-chip button { padding:0; color:inherit; background:none; border:0; cursor:pointer; font-size:15px; }
.process-collapse { margin-top:14px; background:var(--surface); border:1px solid var(--border); border-radius:8px; } .process-heading,.tool-heading { display:flex; gap:7px; align-items:center; min-width:0; } .process-heading { color:#405166; font-size:13px; } .process-heading small { color:var(--subtle); font-size:11px; } .plan-list { display:flex; flex-direction:column; gap:7px; padding:2px 0 10px; } .plan-item { display:flex; gap:8px; align-items:center; color:#405166; font-size:13px; } .plan-index { display:grid; place-items:center; width:19px; height:19px; color:#2563eb; background:#eaf1ff; border-radius:50%; font-size:11px; } .plan-item :deep(.n-tag) { margin-left:auto; } .detail-collapse { margin-top:8px; border:1px solid var(--border); border-radius:6px; } .analysis-text { padding:8px 0; color:#405166; line-height:1.65; white-space:pre-wrap; } .tool-heading span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .tool-heading :deep(.n-tag) { margin-left:auto; } .message-status { margin-top:10px; }
.composer-wrap { flex:0 0 auto; padding:12px 0 20px; background:linear-gradient(0deg, var(--canvas) 82%, transparent); } .task-error { margin-bottom:10px; } .pending-attachments { margin:0 0 8px; } .composer { position:relative; display:flex; flex-direction:column; gap:2px; padding:10px 12px 8px; background:var(--surface); border:1px solid var(--border); border-radius:20px; box-shadow:0 4px 14px rgba(0,0,0,.12); } .composer:focus-within { border-color:var(--ui-border-strong); box-shadow:0 4px 14px rgba(0,0,0,.12); } .composer--dragging { border-color:var(--ui-primary); background:var(--ui-primary-soft); box-shadow:0 0 0 3px color-mix(in srgb, var(--ui-primary) 16%, transparent); } .composer-input :deep(.n-input) { --n-border: transparent; --n-box-shadow: none; --n-box-shadow-focus: none; --n-box-shadow-hover: none; --n-color: transparent; --n-color-focus: transparent; background:transparent !important; box-shadow:none !important; } .composer-input :deep(.n-input__border), .composer-input :deep(.n-input__state-border) { border:0 !important; box-shadow:none !important; display:none; } .composer-input :deep(textarea) { padding-top:8px; padding-bottom:4px; } .composer-toolbar { display:flex; align-items:center; justify-content:space-between; margin-top:2px; } .composer-toolbar-left { display:flex; align-items:center; gap:2px; } .composer-toolbar-right { display:flex; align-items:center; gap:6px; } .toolbar-pill { border-radius:10px; padding:0 10px; } .toolbar-pill--on { border-color:var(--ui-primary); background:var(--ui-primary-soft); color:var(--ui-primary); } .composer-context { font-size:12px; color:var(--subtle); white-space:nowrap; } .composer-model { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--text); white-space:nowrap; cursor:pointer; padding:3px 10px; border:1px solid var(--border); border-radius:999px; background:var(--surface); transition:border-color .15s ease, background .15s ease; } .composer-model:hover { border-color:var(--ui-border-strong); background:var(--ui-surface-muted); } .model-picker { display:flex; flex-direction:column; gap:2px; min-width:200px; max-height:260px; overflow:auto; padding:4px 2px; } .model-picker-item { display:flex; align-items:center; gap:6px; padding:6px 8px; border-radius:6px; font-size:12px; color:var(--ui-text-secondary); cursor:pointer; } .model-picker-item:hover { background:var(--ui-surface-muted); } .model-picker-item--active { color:var(--ui-primary); font-weight:600; } .model-picker-check { width:12px; flex:0 0 12px; } .context-tooltip { display:flex; flex-direction:column; gap:8px; align-items:stretch; min-width:190px; padding:4px 2px; } .context-tooltip .ctx-tokens { font-size:12px; line-height:1.4; color:var(--ui-text-secondary); white-space:nowrap; } .context-tooltip .ctx-compact-btn { justify-content:center; width:100%; font-size:12px; } .context-tooltip .ctx-actions { display:flex; gap:6px; } .context-tooltip .ctx-actions .n-button { flex:1; font-size:12px; } .hidden-input { display:none; } .rename-menu { position:fixed; z-index:70; display:flex; flex-direction:column; min-width:130px; padding:4px; background:var(--ui-surface-raised); border:1px solid var(--ui-border); border-radius:8px; box-shadow:0 4px 16px rgba(0,0,0,.2); }
.permission-card { width:min(520px, calc(100vw - 32px)); } .permission-code { max-height:260px; margin:0; padding:10px; overflow:auto; background:var(--ui-surface-muted); border:1px solid var(--ui-border); border-radius:6px; font:12px/1.55 "Cascadia Code",Consolas,monospace; } .permission-actions { display:flex; justify-content:flex-end; gap:8px; }
.hidden-action { display:inline-flex; }
.agent-pill { max-width:130px; overflow:hidden; text-overflow:ellipsis; }
.management-page { min-height:100%; }
.panel-placeholder { display:flex; flex-direction:column; align-items:center; gap:10px; padding:44px 10px; color:var(--subtle); font-size:13px; text-align:center; } .panel-placeholder .n-icon { font-size:22px; } .panel-section { display:flex; flex-direction:column; gap:12px; padding:2px 2px 20px; } .panel-section label { color:var(--subtle); font-size:12px; }
.panel-section-actions { display:flex; width:100%; align-items:center; gap:8px; }
.panel-section-actions .panel-search-input { flex:1; }
.resource-card-list { display:grid; width:100%; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); align-items:stretch; gap:16px; }
.resource-card-list > .manage-card { min-width:0; }
.resource-card-list--two-columns { grid-template-columns:repeat(2, minmax(0, 1fr)); }
.manage-form-label { display:block; margin:8px 0 4px; color:var(--subtle); font-size:12px; }
.timer-schedule-mode { margin:12px 0 2px; }
.manage-form-row { display:flex; align-items:center; gap:8px; margin-top:14px; color:var(--subtle); font-size:13px; }
.cron-input-row { display:flex; align-items:center; gap:6px; }
.cron-input-row :deep(.n-input) { flex:1; }
.cron-help-btn { flex-shrink:0; }
.cron-preview { margin:6px 0 0; color:var(--subtle); font-size:12px; } .cron-preview--invalid { color:#d03050; font-weight:600; } .cron-preview--ok { color:#1a7f37; }
.cron-help { max-width: 280px; }
.cron-help-title { margin:6px 0 2px; font-weight:600; font-size:12px; }
.cron-help-row { margin:2px 0; font-size:12px; line-height:1.5; color:inherit; }
.cron-help-row code { background:var(--surface); border:1px solid var(--border); border-radius:4px; padding:0 4px; font-size:11px; }
.manage-modal-footer { display:flex; justify-content:flex-end; gap:8px; }
.timer-last-run { color:var(--subtle); font-size:11px; }
.timer-runs-count { margin-left:4px; color:var(--subtle); font-size:11px; }
.timer-runs { display:flex; flex-direction:column; gap:6px; margin-top:8px; padding-top:8px; border-top:1px solid var(--border); max-height:min(48vh, 440px); overflow-y:auto; }
.timer-runs::-webkit-scrollbar { width:6px; }
.timer-runs::-webkit-scrollbar-thumb { background:rgba(0,0,0,.18); border-radius:3px; }
.timer-runs::-webkit-scrollbar-track { background:transparent; }
.timer-run { padding:6px 8px; background:var(--surface); border:1px solid var(--border); border-radius:6px; }
.timer-run-head { display:flex; align-items:center; gap:6px; }
.timer-run-time { color:var(--subtle); font-size:11px; white-space:nowrap; }
.timer-run-summary { margin:4px 0 0; color:#405166; font-size:12px; line-height:1.5; word-break:break-word; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.timer-run--clickable { cursor:pointer; transition:background .15s; }
.timer-run--clickable:hover { background:#f2f6ff; }
.timer-cron-invalid { color:#d03050; font-size:11px; font-weight:600; }
.timer-next-run { color:var(--subtle); font-size:11px; }
.conversation-group-label { display:flex; flex:0 0 auto; align-items:center; gap:4px; margin:10px 2px 2px; padding:6px 6px; width:100%; text-align:left; color:var(--text); font-size:14px; font-weight:700; letter-spacing:.3px; background:transparent; border:0; border-radius:6px; cursor:pointer; user-select:none; } .conversation-group-label:hover { background:var(--muted); } .conversation-group-label .n-icon { color:var(--subtle); } .conversation-group-count { margin-left:auto; font-size:11px; font-weight:600; color:var(--subtle); background:var(--surface); border:1px solid var(--border); border-radius:99px; padding:0 6px; line-height:16px; }
.help-body { padding:4px 2px; } .help-placeholder { margin:0; color:var(--subtle); font-size:13px; }
.message--highlight { outline:2px solid #2563eb; outline-offset:2px; border-radius:10px; }
.panel-section { display:flex; flex-direction:column; gap:10px; }
.panel-section-note { font-size:12px; color:var(--subtle); padding:2px 2px 0; }
.manage-card--sub-linked { border-color:rgba(255,170,0,.5); background:rgba(255,170,0,.06); cursor:pointer; }
.panel-section > :deep(.n-button) { align-self:flex-start; }
.panel-group-head { display:flex; align-items:center; gap:6px; margin-top:2px; }
.panel-group-head--mcp { margin-top:6px; padding-top:8px; border-top:1px dashed var(--border); }
.panel-group-label { font-size:12px; font-weight:600; color:var(--text); }
.panel-group-count { font-size:10px; color:var(--subtle); background:var(--canvas); border:1px solid var(--border); border-radius:99px; padding:0 6px; line-height:16px; }
.manage-card { position:relative; border:1px solid var(--border); border-radius:10px; padding:10px 12px; background:var(--canvas); display:flex; flex-direction:column; gap:6px; transition:box-shadow .15s ease; }
.manage-card:hover { box-shadow:0 2px 8px rgba(29,39,51,.06); }
.manage-card--actionable > .manage-card-head { padding-right:52px; }
.manage-card--disabled { opacity:.55; } .manage-card--active { border-color:#2563eb; box-shadow:0 0 0 1px rgba(37,99,235,.35); background:rgba(37,99,235,.06); } .manage-card--clickable { cursor:pointer; }
.manage-card-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.manage-card-head-right { display:flex; align-items:center; gap:8px; }
.timer-run-badge { font-size:12px; color:var(--subtle); white-space:nowrap; }
.manage-card-title-btn { display:inline-flex; min-width:0; padding:0; border:0; background:transparent; color:inherit; text-align:left; cursor:pointer; } .manage-card-title-btn:hover .manage-card-title { color:#2563eb; } .manage-card-title-btn--active .manage-card-title { color:#2563eb; } .manage-card-title-btn:disabled { cursor:default; }
.manage-card-title { font-size:13px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.manage-title-with-icon { display:inline-flex; min-width:0; align-items:center; gap:6px; }
.manage-title-with-icon .n-icon { flex:0 0 auto; color:var(--ui-primary); font-size:15px; }
.manage-card-desc { font-size:12px; color:var(--subtle); margin:0; line-height:1.5; word-break:break-word; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.manage-card-cmd { display:block; font-size:11px; color:var(--subtle); background:var(--surface); border-radius:6px; padding:4px 8px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.manage-card-chips { display:flex; align-items:center; flex-wrap:wrap; gap:4px; }
.manage-chip-label { font-size:11px; color:var(--subtle); margin-right:2px; }
.manage-card-actions { display:flex; justify-content:flex-end; gap:2px; }
.manage-card-actions--overlay { position:absolute; z-index:1; top:6px; right:6px; opacity:0; pointer-events:none; transition:opacity .15s ease; }
.manage-card:hover > .manage-card-actions--overlay,
.manage-card:focus-within > .manage-card-actions--overlay { opacity:1; pointer-events:auto; }
.knowledge-alert { margin-bottom:8px; }
.knowledge-alert-row { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.panel-section--knowledge { position:relative; padding-bottom:44px; }
.knowledge-manage-btn { position:absolute; right:6px; bottom:6px; display:flex; align-items:center; gap:6px; }
.manage-card-head--group { cursor:pointer; user-select:none; }
.tool-group-left { display:flex; align-items:center; gap:6px; min-width:0; }
.tool-group-caret { font-size:12px; color:var(--subtle); width:14px; text-align:center; flex:0 0 auto; }
.tool-count { font-size:10px; color:var(--subtle); background:var(--surface); border-radius:99px; padding:0 6px; line-height:16px; flex:0 0 auto; }
.tool-item-list { display:flex; max-height:200px; flex-direction:column; gap:6px; overflow-y:auto; border-left:2px solid var(--border); margin:2px 0 2px 10px; padding:2px 6px 2px 10px; }
.tool-item { padding:6px 8px; border-radius:8px; background:var(--surface); display:flex; flex-direction:column; gap:4px; }
.tool-item--disabled { opacity:.55; }
.tool-item-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.tool-item-head code { font-size:12px; color:var(--primary, inherit); }
.tool-item-desc { font-size:11px; color:var(--subtle); margin:0; line-height:1.5; word-break:break-word; max-height:3em; overflow:hidden; }
.manage-modal { width:min(520px, calc(100vw - 32px)); }
.manage-modal--wide { width:min(80vw, 1200px); }
.manage-modal--large { width:min(85vw, 1400px); height:min(85vh, 920px); display:flex; flex-direction:column; }
.manage-modal--large :deep(.n-card__content) { flex:1; min-height:0; overflow:auto; display:flex; flex-direction:column; }
.manage-form { display:flex; flex-direction:column; gap:10px; }
.manage-form-split { display:grid; grid-template-columns:0.95fr 1.05fr; gap:20px; width:100%; height:100%; }
.manage-form-left { display:flex; flex-direction:column; gap:10px; min-width:0; }
.manage-form-left :deep(.n-input--textarea) { flex:1; }
.manage-form-left :deep(.n-input--textarea .n-input__textarea-el) { height:100% !important; }
.manage-form-right { display:flex; flex-direction:column; gap:12px; min-width:0; overflow-y:auto; padding-right:4px; }
.agent-association-section { display:flex; flex-direction:column; gap:8px; padding:12px; border:1px solid var(--border); border-radius:10px; background:var(--canvas); }
.agent-association-section .manage-form-label { margin:0; color:var(--text); font-weight:600; }
.agent-checkbox-list { display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:8px 14px; }
.agent-checkbox-list--tools { grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); }
.agent-association-empty { padding:8px 0; color:var(--subtle); font-size:12px; }
.agent-tool-tree-node { display:flex; flex-direction:column; gap:8px; border:1px solid var(--border); border-radius:8px; background:var(--surface); }
.agent-tool-tree-row { display:flex; min-height:38px; align-items:center; justify-content:space-between; gap:10px; padding:6px 10px; }
.agent-tool-tree-toggle { display:flex; min-width:0; flex:1; align-items:center; gap:7px; padding:0; color:var(--text); background:transparent; border:0; cursor:pointer; text-align:left; }
.agent-tool-tree-toggle .n-icon:first-child { color:var(--subtle); font-size:14px; }
.agent-tool-tree-toggle .n-icon:nth-child(2) { color:var(--ui-primary); font-size:16px; }
.agent-tool-tree-toggle strong { overflow:hidden; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.agent-tool-tree-count { min-width:22px; padding:0 6px; color:var(--subtle); background:var(--canvas); border:1px solid var(--border); border-radius:99px; font-size:10px; line-height:18px; text-align:center; }
.agent-tool-tree-children { padding:0 12px 12px 36px; }
.agent-mcp-server-list { display:flex; flex-direction:column; gap:8px; padding:0 10px 10px 30px; }
.agent-tool-server { display:flex; flex-direction:column; gap:8px; border-left:1px solid var(--border); }
.agent-tool-server > .agent-tool-tree-row { padding-right:0; }
.agent-tool-children { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:8px 14px; margin-left:18px; padding:0 0 10px 24px; border-left:1px solid var(--border); }
.agent-tool-label,.agent-knowledge-label { display:flex; min-width:0; flex-direction:column; gap:2px; }
.agent-tool-label code { overflow:hidden; font-size:12px; text-overflow:ellipsis; }
.agent-tool-label small,.agent-knowledge-label small { color:var(--subtle); font-size:11px; line-height:1.35; }
.agent-association-section :deep(.n-checkbox) { align-items:flex-start; }
.manage-form-code :deep(textarea) { font-family:"Cascadia Code",Consolas,monospace; font-size:12px; line-height:1.6; }
.manage-form-actions { display:flex; justify-content:flex-end; gap:8px; }
.quick-command-form-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; }
.quick-command-form-grid .manage-form-row { justify-content:space-between; }
.role-editor-layout { display:grid; height:calc(80vh - 180px); grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; }
.role-editor-column { display:flex; min-width:0; min-height:0; flex-direction:column; }
.role-editor-textarea { width:100%; min-height:0; flex:1; resize:none; padding:10px; color:var(--text); background:var(--canvas); border:1px solid var(--border); border-radius:8px; font:13px/1.6 inherit; }
.role-editor-preview { min-height:0; flex:1; overflow-y:auto; padding:10px; background:var(--canvas); border:1px solid var(--border); border-radius:8px; }
.agent-option-name { display:flex; align-items:center; gap:14px; font-size:13px; }
.agent-option-icon { font-size:15px; color:var(--accent, #2563eb); margin-right:4px; }
.quick-action-panel { position:absolute; bottom:calc(100% + 8px); left:0; right:0; z-index:30; max-height:220px; overflow-y:auto; padding:4px; background:var(--surface); border:1px solid var(--border); border-radius:12px; box-shadow:0 8px 24px rgba(29,39,51,.12); display:flex; flex-direction:column; gap:2px; }
.quick-action-item { display:flex; align-items:center; gap:8px; padding:7px 10px; border-radius:8px; font-size:13px; cursor:pointer; color:var(--text); }
.quick-action-item:hover, .quick-action-item--active { background:rgba(37,99,235,.08); }
.quick-action-name { flex:0 0 auto; max-width:45%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:600; }
.quick-action-desc { color:var(--subtle); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.quick-action-empty { padding:12px; color:var(--subtle); font-size:12px; text-align:center; }
.builtin-tool-list { display:flex; flex-wrap:wrap; gap:6px; }
.builtin-tool-chip { padding:3px 8px; border:1px solid var(--border); border-radius:8px; background:rgba(37,99,235,.05); cursor:pointer; transition:all .15s ease; }
.builtin-tool-chip:hover { border-color:rgba(37,99,235,.6); }
.builtin-tool-chip code { font:12px/1.4 "Cascadia Code",Consolas,monospace; color:var(--text); }
.builtin-tool-chip--active { background:rgba(37,99,235,.14); border-color:rgba(37,99,235,.55); }
.builtin-tool-chip--active code { color:#2563eb; font-weight:600; }
.builtin-tool-chip:not(.builtin-tool-chip--active) { opacity:.62; }
.skill-edit-form { flex:1; min-height:0; }
.skill-edit-split { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px; flex:1; min-height:0; }
.skill-edit-col { display:flex; flex-direction:column; gap:6px; min-height:0; min-width:0; }
.skill-edit-label { color:var(--subtle); font-size:12px; }
.skill-edit-editor :deep(textarea) { font:12px/1.5 "Cascadia Code",Consolas,monospace !important; }
.skill-edit-col :deep(.n-input--textarea) { flex:1; min-height:0; }
.skill-edit-col :deep(.n-input--textarea .n-input__textarea-el) { height:100% !important; overflow-y:auto; }
.skill-edit-preview-body { flex:1; min-height:0; overflow-y:auto; padding:10px 12px; border:1px solid var(--border); border-radius:8px; background:var(--canvas); }
.skill-edit-preview-empty { color:var(--subtle); font-size:12px; }
.file-preview-modal { width:min(70vw, 1000px); }
.file-preview-content { max-height:70vh; overflow:auto; margin:0; padding:12px 14px; background:#0f172a; color:#e2e8f0; border-radius:8px; font:12px/1.6 "Cascadia Code",Consolas,monospace; white-space:pre; word-break:normal; }
.file-preview-empty { color:var(--subtle); font-size:13px; }
.edit-card { width:70vw; } .rename-card { width:min(400px, calc(100vw - 32px)); } .edit-body { display:flex; gap:16px; align-items:stretch; } .edit-input { flex:1 1 50%; min-width:0; } .edit-input :deep(textarea) { font-family:"Cascadia Code",Consolas,monospace; font-size:13px; line-height:1.6; } .edit-preview { flex:1 1 50%; min-width:0; display:flex; flex-direction:column; } .edit-preview-label { margin-bottom:6px; color:var(--subtle); font-size:12px; } .edit-preview-body { flex:1; padding:12px 14px; background:var(--canvas); border:1px solid var(--border); border-radius:8px; overflow:auto; } .edit-preview-body :deep(p) { margin:0 0 8px; } .edit-preview-body :deep(p:last-child) { margin-bottom:0; }.drawer-list { display:flex; flex-direction:column; gap:8px; } .drawer-item { justify-content:flex-start; padding:10px; }
@media (min-width: 721px) { .chat-app--collapsed .sidebar-expand-btn { display:inline-flex; } }
@media (max-width: 720px) { .sidebar { display:none; } .mobile-menu { display:inline-flex; } .composer-toolbar .toolbar-pill { padding:0 6px; } .composer-toolbar .toolbar-pill :deep(.n-button__content) { display:none; } .composer-toolbar .agent-pill { max-width:none; } }
@media (max-width: 900px) { .workspace-header { padding:0 14px; } .message-column,.composer-column { width:calc(100% - 32px); } .message-column { padding-top:22px; } .message--user .message-body { max-width:86%; } }
@media (max-width: 900px) { .manage-modal--large { width:100vw; height:100vh; height:100dvh; } .manage-form-split { grid-template-columns:1fr; height:auto; } .manage-form-left :deep(.n-input--textarea) { min-height:280px; } .manage-form-right { overflow:visible; } }
@media (max-width: 560px) { .resource-card-list,.resource-card-list--two-columns { grid-template-columns:1fr; } .panel-section-actions { align-items:stretch; flex-direction:column; } .panel-section-actions .n-button { align-self:flex-end; } .quick-command-form-grid,.role-editor-layout { grid-template-columns:1fr; } }
@media (hover: none) { .manage-card-actions--overlay { opacity:1; pointer-events:auto; } }
/* animated loading dots under a running assistant message */
.msg-loader {
  --color-1: #9aa3b2;
  --color-2: #9aa3b233;
  --size: 0.4px;
  width: calc(16 * var(--size));
  height: calc(16 * var(--size));
  border-radius: 50%;
  background-color: var(--color-1);
  box-shadow:
    calc(32 * var(--size)) 0 var(--color-1),
    calc(-32 * var(--size)) 0 var(--color-1);
  position: relative;
  margin: 8px 0 2px 15px;
  display: block;
  animation: msgflash 0.5s ease-out infinite alternate;
}
@keyframes msgflash {
  0% {
    background-color: var(--color-2);
    box-shadow:
      calc(32 * var(--size)) 0 var(--color-2),
      calc(-32 * var(--size)) 0 var(--color-1);
  }
  50% {
    background-color: var(--color-1);
    box-shadow:
      calc(32 * var(--size)) 0 var(--color-2),
      calc(-32 * var(--size)) 0 var(--color-2);
  }
  100% {
    background-color: var(--color-2);
    box-shadow:
      calc(32 * var(--size)) 0 var(--color-1),
      calc(-32 * var(--size)) 0 var(--color-2);
  }
}
.image-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.82);
  cursor: zoom-out;
}
.image-preview-overlay img {
  max-width: 92vw;
  max-height: 92vh;
  border-radius: 8px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.6);
  cursor: default;
}
.chat-loading-overlay {
  position: fixed;
  inset: 0;
  z-index: 1600;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(247, 248, 250, 0.6);
}
.chat-loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 22px 34px;
  border-radius: 14px;
  background: var(--surface);
  box-shadow: 0 6px 30px rgba(29, 39, 51, 0.14);
  border: 1px solid var(--border);
  color: var(--subtle);
  font-size: 13px;
}
.load-earlier {
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 14px auto;
  padding: 8px 18px;
  width: fit-content;
  border-radius: 20px;
  background: var(--muted);
  border: 1px solid var(--border);
  color: var(--subtle);
  font-size: 12px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s, color 0.2s;
}
.load-earlier:hover {
  background: var(--border);
  color: var(--text);
}
</style>

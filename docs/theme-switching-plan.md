# 明暗主题切换功能实施方案

> 文档状态：已实施并验证  
> 适用版本：当前 `web/` Vue 3 + TypeScript + Naive UI 前端  
> 分析基线：2026-11-13  
> 目标：在不改变会话、ACP、附件、模型和后端数据行为的前提下，为 Web UI 增加可持久化、可访问、可覆盖系统偏好的明暗主题切换。

## 1. 当前项目分析

### 1.1 前端架构与入口

- 活动前端位于 `web/`，技术栈为 Vue 3、TypeScript、Vite、Naive UI。
- 应用入口为 `web/src/main.ts`，当前加载全局 `web/src/style.css`，注册路由和 i18n 后挂载 `App.vue`。
- `web/src/App.vue` 当前负责：
  - `NConfigProvider` 和 `NMessageProvider` 的全局包裹；
  - Naive UI `GlobalThemeOverrides`；
  - 全局字体设置；
  - 监听语言并更新 `document.body` 字体。
- 主工作台为 `web/src/pages/ChatPage.vue`，包含侧边栏、工作区、消息时间线、输入区、权限弹窗、右侧管理面板和大量页面级 scoped CSS。
- 独立语音窗口为 `web/src/pages/VoiceCallPage.vue`，其自身使用深色硬编码配色，不能仅依靠主窗口的 CSS 继承完成主题同步。

### 1.2 已有的主题基础

当前代码已经具备部分可复用基础，但还没有完整的主题状态系统：

1. `ChatPage.vue` 根节点 `.chat-app` 已定义一组局部 CSS 变量：
   - `--canvas`
   - `--surface`
   - `--muted`
   - `--border`
   - `--text`
   - `--subtle`
2. 部分组件已经使用变量，例如 `ToolCallCard.vue` 中的 `var(--text)`，但同一组件仍大量使用硬编码颜色。
3. `docs/UI_DESIGN.md` 已定义浅色设计令牌，如 `--ui-canvas`、`--ui-surface`、`--ui-border`、`--ui-text`、`--ui-primary` 等，可作为主题令牌命名和浅色基线。
4. `App.vue` 已使用 `NConfigProvider` 的 `theme-overrides`，但目前只设置固定的蓝色主色、圆角和字体，没有使用 Naive UI 的 `darkTheme`。
5. 项目已经在多个功能中使用 `localStorage`，例如语言、模型、历史开关、语音设置和初始模式，因此使用浏览器本地持久化符合现有模式。
6. 项目已有中/日/英三套 i18n 文件，新增主题相关文案需要同步维护：
   - `web/src/locales/zh.ts`
   - `web/src/locales/ja.ts`
   - `web/src/locales/en.ts`

### 1.3 当前主要问题

主题切换不能只在 `body` 上切换一个背景色，原因如下：

- `ChatPage.vue` 的页面 CSS 同时存在 CSS 变量和大量浅色硬编码值；
- `ExecutionProcess.vue`、`ToolCallCard.vue`、`MarkdownMessage.vue` 仍有独立的白色背景、边框、代码块和状态颜色；
- `highlight.js` 当前直接导入 `highlight.js/styles/github.css`，该样式是浅色固定主题；
- Markdown 中的代码工具栏、Mermaid 预览/源码标签、表格和链接颜色需要随主题变化；
- Naive UI 组件需要接收 `lightTheme`/`darkTheme`，否则原生组件与自定义 CSS 会出现明暗不一致；
- 语音窗口由 `window.open` 打开，是独立文档，必须单独读取主题偏好或接收同步消息；
- 首次加载时如果先显示浅色再切换深色，会产生主题闪烁（FOUC）。

## 2. 功能目标与非目标

### 2.1 功能目标

1. 支持三种用户偏好：
   - `light`：始终浅色；
   - `dark`：始终深色；
   - `system`：跟随操作系统 `prefers-color-scheme`。
2. 默认值为 `system`，首次使用时尊重系统偏好。
3. 用户选择持久化到 `localStorage`，刷新页面后保持。
4. 主窗口、Naive UI、消息内容、代码块、工具卡片、弹窗和右侧面板统一切换。
5. 独立语音窗口与主窗口保持一致，并在主窗口运行期间响应主题变化。
6. 支持键盘操作、可见焦点、明确的 aria-label，不依赖颜色单独表达状态。
7. 主题切换不影响：
   - 会话历史和消息内容；
   - ACP WebSocket 和任务执行；
   - localStorage 中已有的其他配置；
   - Markdown 消毒、文件链接、Mermaid 业务逻辑；
   - 现有响应式布局。

### 2.2 非目标

- 不修改 Python 后端、ACP 协议、SQLite 数据结构或会话 API。
- 不在第一阶段引入第三方主题管理库。
- 不把主题设置写入服务端账户；当前项目没有用户账户/跨设备偏好同步需求。
- 不重做整个视觉设计，只将现有浅色视觉令牌抽象为双主题令牌。
- 不把用户消息、模型配置等数据迁移到新的存储系统。

## 3. 推荐方案

### 3.1 主题状态模型

新增前端主题模块，例如：

```text
web/src/theme.ts
```

建议公开以下类型和 API：

```ts
export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const themeStorageKey = 'acp-ui-theme'

export function readThemePreference(): ThemePreference
export function resolveTheme(preference: ThemePreference): ResolvedTheme
export function applyTheme(preference: ThemePreference): ResolvedTheme
export function watchSystemTheme(callback: (theme: ResolvedTheme) => void): () => void
```

实施时也可以将这些逻辑放入 composable，例如 `web/src/composables/useTheme.ts`；如果没有现有 composables 目录，优先使用单一 `theme.ts`，避免为了一个功能增加过多抽象层。

状态职责：

- `preference`：用户选择的原始值，持久化为 `light`、`dark` 或 `system`；
- `resolvedTheme`：实际生效的 `light` 或 `dark`；
- `matchMedia('(prefers-color-scheme: dark)')`：仅在 `system` 模式下参与计算；
- `document.documentElement.dataset.theme`：CSS 的稳定选择器，例如 `data-theme="dark"`；
- `document.documentElement.style.colorScheme`：让原生输入框、滚动条和浏览器控件获得正确的明暗基线。

推荐不要只依赖 `.dark` class。使用 `data-theme` 可以避免与第三方库的 class 命名冲突，并且在开发者工具中更容易确认最终主题。

### 3.2 Naive UI 集成

修改 `web/src/App.vue`：

1. 引入 Naive UI 的 `darkTheme` 和 `lightTheme`。
2. 将当前固定的 `themeOverrides` 改为基于 `resolvedTheme` 的计算值：

```ts
const naiveTheme = computed(() =>
  resolvedTheme.value === 'dark' ? darkTheme : lightTheme
)

const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    // 继续保留项目当前的主色、圆角、字体；
    // 颜色应与 CSS token 的 --ui-primary 保持一致。
  }
}))
```

3. 将 `:theme="naiveTheme"` 和 `:theme-overrides="themeOverrides"` 同时传给 `NConfigProvider`。
4. 保留现有 `NMessageProvider` 层级，确保提示消息也跟随 Naive UI 主题。
5. `fontFamily` 继续由 `App.vue` 统一处理，不与主题状态耦合。

Naive UI 的颜色不应在每个页面里单独覆盖。若某些组件在暗色下对比度不足，优先在 `GlobalThemeOverrides` 的 `common`、`Button`、`Input`、`Modal`、`Popover`、`Dropdown`、`Collapse` 等主题项中集中修正。

### 3.3 初始主题闪烁控制

在 `web/index.html` 的 `<head>` 中增加极小的同步初始化脚本，必须早于 Vue bundle 执行：

1. 读取 `localStorage.acp-ui-theme`；
2. 值为 `light`/`dark` 时直接使用；
3. 值为 `system` 或不存在时读取 `matchMedia`；
4. 设置 `document.documentElement.dataset.theme` 和 `colorScheme`；
5. 不在此处引入业务逻辑或网络请求。

Vue 启动后由同一套 `theme.ts` 接管并校正状态。初始化脚本与模块中的合法值判断必须保持一致，避免出现“首屏是深色、Vue 接管后又变浅色”的跳变。

## 4. CSS 令牌设计

### 4.1 全局令牌

将 `web/src/style.css` 从固定浅色基础扩展为全局令牌。浅色值应与 `docs/UI_DESIGN.md` 对齐，深色值采用低亮度背景、足够亮度的正文和不刺眼的蓝色强调色。

建议令牌：

| 令牌 | 浅色建议 | 深色建议 | 用途 |
| --- | --- | --- | --- |
| `--ui-canvas` | `#f7f8fa` | `#11151c` | 页面画布 |
| `--ui-surface` | `#ffffff` | `#1a202b` | 侧栏、卡片、输入区 |
| `--ui-surface-raised` | `#ffffff` | `#222a38` | 弹窗、下拉、悬浮层 |
| `--ui-surface-muted` | `#f1f4f8` | `#252e3d` | 代码块、悬停、次级面板 |
| `--ui-border` | `#e4e8ee` | `#354052` | 分隔线和边框 |
| `--ui-border-strong` | `#cbd5e1` | `#4b5a70` | 强调边框、输入焦点 |
| `--ui-text` | `#1d2733` | `#e7edf5` | 正文和标题 |
| `--ui-text-secondary` | `#405166` | `#c0cad8` | 次级正文 |
| `--ui-text-muted` | `#6b7785` | `#8e9bad` | 时间、说明、占位 |
| `--ui-primary` | `#2563eb` | `#76a7ff` | 主操作、链接、焦点 |
| `--ui-primary-hover` | `#1d4ed8` | `#9ac0ff` | 主操作悬停 |
| `--ui-primary-soft` | `#eaf1ff` | `#263957` | 选中项、用户消息背景 |
| `--ui-success` | `#16805b` | `#62d39b` | 成功状态 |
| `--ui-warning` | `#b76a00` | `#f2bd61` | 等待授权、警告 |
| `--ui-error` | `#c73737` | `#ff8b8b` | 失败、删除、错误 |
| `--ui-code-text` | `#1d2733` | `#e7edf5` | 代码文字 |
| `--ui-code-keyword` | `#174cb9` | `#9ac0ff` | 行内代码和关键字 |
| `--ui-overlay` | `rgba(255,255,255,.72)` | `rgba(26,32,43,.86)` | 顶部栏半透明背景 |

实际色值实施时必须通过浏览器对比度检查，尤其是 `--ui-text-muted`、状态色和链接色。不要简单对所有颜色做反色处理。

### 4.2 变量命名迁移

`.chat-app` 当前已有 `--canvas`、`--surface`、`--muted`、`--border`、`--text`、`--subtle`。建议：

1. 在全局定义 `--ui-*` 作为唯一规范；
2. 在 `.chat-app` 中保留短别名一段过渡期：

```css
.chat-app {
  --canvas: var(--ui-canvas);
  --surface: var(--ui-surface);
  --muted: var(--ui-surface-muted);
  --border: var(--ui-border);
  --text: var(--ui-text);
  --subtle: var(--ui-text-muted);
}
```

3. 新代码直接使用 `--ui-*`；
4. 在本次主题改造涉及到的规则中逐步替换硬编码值；
5. 后续可单独做一次 CSS 清理，再移除短别名。

这样可以降低一次性修改 5000 行页面样式的风险，并保持已有布局行为不变。

## 5. 组件与页面改造清单

### 5.1 `web/src/App.vue`

- 引入并使用主题状态；
- 为 `NConfigProvider` 设置动态 `theme`；
- 保留现有 `themeOverrides`，但将颜色改为与 token 一致的值；
- 监听主题变化并更新 `document.documentElement.dataset.theme`；
- 在卸载时移除 `matchMedia` 监听；
- 不把主题切换逻辑复制到 `ChatPage.vue`。

### 5.2 `web/src/style.css`

- 增加 `:root` 浅色 token；
- 增加 `:root[data-theme='dark']` 深色 token；
- `body` 的 `color`、`background` 改为 token；
- `color-scheme` 由主题模块设置，CSS 中可提供 `:root[data-theme='dark'] { color-scheme: dark; }` 作为兜底；
- `ruby rt` 的颜色改为 `--ui-text-muted`；
- 保持全屏布局、禁止文档滚动和控件字体行为不变。

### 5.3 `web/src/pages/ChatPage.vue`

建议在现有顶部工作区操作区加入主题菜单，位置放在语言菜单附近：

- 图标：`SunnyOutline` / `MoonOutline`，或使用一个固定的主题图标并通过菜单表达当前选择；
- 交互：`NDropdown` 三项 `浅色 / 深色 / 跟随系统`；
- 当前项显示勾选或 active 状态；
- tooltip/aria-label 显示当前模式，不只显示图标；
- 切换时不关闭正在进行的 ACP 任务，不清空输入框，不重置滚动位置。

建议不要使用单次点击直接在浅色/深色之间循环，因为项目需要支持 `system`，菜单比二态按钮更清晰。若后续产品明确只需要两态，再将 UI 简化为按钮。

优先改造以下硬编码区域：

- `.brand-mark`、欢迎页图标；
- 侧边栏主按钮、会话 active/hover 状态；
- 顶部工作区半透明背景；
- 用户消息气泡、AI 消息卡片；
- 消息操作菜单、重命名菜单；
- 附件 chip、模型选择器、上下文提示；
- Composer 拖拽态、权限代码块；
- 右侧面板、管理表单、弹窗辅助文字；
- 滚动条 thumb；
- 任务状态和错误提示。

规则：布局、尺寸、间距、圆角和动画不因主题改变；只替换颜色、阴影透明度和必要的边框层级。

### 5.4 `web/src/components/ExecutionProcess.vue`

- 卡片背景、边框、标题、步骤文字改为 token；
- 待处理/进行中/完成/阻塞颜色使用语义 token；
- `todo-note`、步骤序号背景和 Naive UI `NTag` 保持可读；
- 不修改任务步骤计算、折叠行为和状态映射。

### 5.5 `web/src/components/ToolCallCard.vue`

- 工具卡片、参数区、返回值区、虚线分隔线改为 token；
- 状态色保留成功/失败/警告/信息语义，但使用双主题可读色；
- 工具 badge 的背景和边框不使用固定透明黑/蓝值，改用主题 token 或 `color-mix`（如项目目标浏览器允许）；
- 不修改工具状态推断、失败模式匹配和文件打开事件。

### 5.6 `web/src/components/MarkdownMessage.vue`

这是主题一致性的重点组件：

1. 将 `.markdown-message` 正文、标题、表格、图片边框、链接、行内代码、代码块、工具栏和 Mermaid 容器全部改为 token。
2. 不再固定导入 `highlight.js/styles/github.css` 作为唯一主题。
3. 采用以下两种方案之一：
   - 推荐：使用 `highlight.js/lib` 的 API，仅保留语法高亮 class，自行用 token 定义 `.hljs-*` 颜色；
   - 备选：分别加载 GitHub 浅色和 GitHub Dark 样式，并根据主题动态切换，但需要处理 CSS 动态加载和构建产物。
4. Mermaid 需要根据 `resolvedTheme` 设置 `theme: 'default'` 或 `theme: 'dark'`，并在主题切换后对已显示图表重新渲染；否则 Mermaid SVG 会继续保留旧主题颜色。
5. Mermaid 缓存必须从单纯的 `source -> svg` 改为包含主题的 key，例如：

```ts
const cacheKey = `${resolvedTheme}:${source}`
```

6. 主题切换时清理或区分旧缓存，避免浅色 SVG 被深色页面复用。
7. 代码复制、文件链接、DOMPurify 配置和 Markdown 解析参数不变。

为了让 `MarkdownMessage.vue` 获取主题，优先复用 `theme.ts` 的 `resolvedTheme` 响应式状态；不要通过读取 DOM 颜色来反推主题。

### 5.7 `web/src/pages/VoiceCallPage.vue`

语音窗口需要独立适配：

- 将当前 `#0f1115`、`#171a21` 等硬编码颜色抽成该页面自己的 token；
- 主题初始化时读取 `localStorage.acp-ui-theme`；
- `system` 模式通过 `matchMedia` 解析；
- 主窗口切换时通过 `BroadcastChannel`（推荐）发送 `{ type: 'theme-changed', preference, resolvedTheme }`；
- 兼容性回退：同时监听 `window.storage`，以支持不支持 `BroadcastChannel` 的 WebView；
- 语音窗口收到消息后更新 `data-theme`；
- 主窗口关闭/重新打开语音窗口时，以当前 localStorage 值为准，不依赖历史消息；
- 保持语音识别、TTS、桥接和窗口关闭逻辑不变。

建议频道名：

```text
acp-ui-theme
```

消息只包含主题枚举和解析结果，不包含会话内容、模型信息或其他敏感数据。

## 6. 交互与无障碍设计

### 6.1 主题菜单

菜单文案：

| key | 中文 | 日本語 | English |
| --- | --- | --- | --- |
| `theme` | 主题 | テーマ | Theme |
| `themeLight` | 浅色 | ライト | Light |
| `themeDark` | 深色 | ダーク | Dark |
| `themeSystem` | 跟随系统 | システムに合わせる | System |
| `themeCurrent` | 当前主题：{theme} | 現在のテーマ：{theme} | Current theme: {theme} |

要求：

- 主题控制必须有可读的 `aria-label`；
- 菜单项具有当前选择状态；
- 键盘可通过 Tab 聚焦、Enter/Space 打开或选择；
- 不用颜色作为唯一状态提示，状态色同时保留文字；
- 深色模式下焦点环仍需明显可见；
- 主题切换不使用过长动画，建议 `150ms` 以内；系统偏好变化时可以即时切换。

### 6.2 系统偏好变化

当用户选择 `system` 时：

- 操作系统从浅色改为深色，页面自动切换；
- `localStorage` 仍保存 `system`，不改写成 `light`/`dark`；
- 切换到手动模式后，系统变化不再影响页面；
- 语音窗口与主窗口使用同一解析规则。

## 7. 持久化与兼容性

### 7.1 Storage 契约

```text
key: acp-ui-theme
values: light | dark | system
default: system
```

- 非法值按缺失处理并回退到 `system`；
- 不迁移、不覆盖现有的 `acp-ui-locale`、`chat_model`、`chat_voice_name` 等 key；
- `localStorage` 不可用时，内存中使用 `system`，并通过标准错误处理记录初始化失败；不能让主题初始化异常阻断应用启动。

### 7.2 多窗口同步

- `BroadcastChannel` 用于即时同步；
- `storage` 事件用于兼容回退；
- 同一窗口内直接通过响应式状态更新，不依赖 `storage` 事件（该事件不会在触发写入的窗口自身触发）。

## 8. 实施步骤

### 阶段一：主题基础设施

1. 新增 `web/src/theme.ts`；
2. 在 `web/index.html` 增加无闪烁初始化脚本；
3. 在 `web/src/style.css` 增加全局 token 和 dark token；
4. 修改 `App.vue` 接入动态 Naive UI theme；
5. 保持现有浅色视觉不变，先通过构建验证。

### 阶段二：主工作台入口

1. 在 `ChatPage.vue` 顶部加入主题菜单；
2. 添加三套 i18n 文案；
3. 替换 ChatPage 核心布局和交互区域的硬编码颜色；
4. 验证切换时输入框、任务、弹窗和右侧面板状态不丢失。

### 阶段三：内容组件

1. 改造 `ExecutionProcess.vue`；
2. 改造 `ToolCallCard.vue`；
3. 改造 `MarkdownMessage.vue` 的代码、表格、链接和 Mermaid；
4. 为 Mermaid 缓存增加主题维度；
5. 重点检查代码高亮可读性和 SVG 重渲染。

### 阶段四：语音窗口与多窗口同步

1. 将 `VoiceCallPage.vue` 的颜色抽成 token；
2. 接入主题初始化；
3. 接入 `BroadcastChannel` 和 `storage` 回退；
4. 从主窗口打开语音窗口，验证首次主题和运行时同步。

### 阶段五：清理与文档

1. 搜索 `web/src` 中剩余的浅色硬编码颜色；
2. 对与主题相关的规则逐项替换，保留语义必要的品牌色或状态色；
3. 更新 `docs/UI_DESIGN.md` 的主题章节，说明 token 和三态偏好；
4. 在 README 的功能总览中补充主题能力（如果产品希望对外声明）。

## 9. 测试与验收标准

### 9.1 自动化检查

在 `web/` 目录执行：

```powershell
npm run build
```

如果后续新增前端测试框架，补充以下纯逻辑用例：

- `readThemePreference()` 对缺失值、合法值、非法值的处理；
- `resolveTheme('light')`、`resolveTheme('dark')` 和系统偏好的结果；
- `applyTheme()` 是否设置正确的 `data-theme` 和 `color-scheme`；
- 主题切换消息是否包含正确的 preference/resolvedTheme。

### 9.2 手工验收矩阵

| 场景 | 验收标准 |
| --- | --- |
| 首次打开、系统浅色 | 页面直接以浅色显示，无明显闪烁 |
| 首次打开、系统深色 | 页面直接以深色显示，无明显闪烁 |
| 选择浅色 | 刷新后仍为浅色，系统变深不影响 |
| 选择深色 | 刷新后仍为深色，系统变浅不影响 |
| 选择跟随系统 | 系统切换后主窗口自动切换 |
| Chat 页面 | 侧栏、工作区、消息、输入区、右侧面板、弹窗全部一致 |
| Markdown | 标题、链接、表格、行内代码、代码块、复制栏均可读 |
| Mermaid | 浅色/深色 SVG 与背景匹配，切换后不会复用旧主题 SVG |
| 工具/任务卡片 | 成功、失败、等待授权、运行中状态均可辨识 |
| 语音窗口 | 新开窗口使用当前主题，运行中可同步切换 |
| 任务执行中 | 切换主题不关闭 WebSocket、不丢输入、不重置任务 |
| 多语言 | 中/日/英主题菜单均有完整文案 |
| 窄屏 | 主题控制不挤压顶部操作区，抽屉和弹窗仍可用 |
| 无障碍 | 键盘可操作，焦点清晰，aria-label 有意义 |

### 9.3 对比度建议

- 普通正文至少达到 WCAG AA 的 4.5:1；
- 大标题至少达到 3:1；
- 边框和非文本装饰不能承担唯一信息；
- 代码高亮中关键字、字符串、注释在深色背景下分别检查；
- 不以“看起来不刺眼”替代实际对比度检查。

## 10. 风险、回退与发布策略

### 10.1 主要风险

1. **硬编码颜色遗漏**：页面局部仍显示白底或浅色文字。
   - 处理：按组件清单扫描 `#fff`、`#f1f4f8`、`#1d2733` 等颜色；逐页截图检查。
2. **Naive UI 与自定义 CSS 不一致**：输入框、弹窗、下拉菜单颜色脱节。
   - 处理：使用 `darkTheme`，再用集中式 `themeOverrides` 修正，不在页面内散落覆盖。
3. **高亮样式覆盖 token**：highlight.js 的 CSS 权重覆盖自定义代码色。
   - 处理：去掉固定 GitHub CSS 或为主题样式提供明确加载顺序。
4. **Mermaid 缓存错误**：切换后图表仍是旧主题。
   - 处理：缓存 key 加入 resolved theme，并在主题变化后触发增强渲染。
5. **桌面 WebView 对 BroadcastChannel 支持不完整**。
   - 处理：保留 `storage` 事件回退，并在新开窗口时重新读取 localStorage。
6. **首屏主题闪烁**：
   - 处理：`web/index.html` 同步预初始化，Vue 接管后只做状态确认。

### 10.2 回退策略

主题功能应保持低耦合，若发布后需要回退：

1. 隐藏或移除 `ChatPage.vue` 的主题入口；
2. 将 `App.vue` 的 `NConfigProvider` 恢复为固定 `lightTheme`；
3. 保留 token，不影响浅色样式；
4. 忽略已有的 `acp-ui-theme` 值，下一次恢复功能时仍可继续使用；
5. 不需要修改后端和历史数据。

### 10.3 建议发布顺序

1. 先发布主题基础设施和浅色等价迁移；
2. 再发布深色 token 与主窗口入口；
3. 最后发布 Markdown/Mermaid 和语音窗口适配；
4. 每阶段均运行 `npm run build`，并保留一轮手工回归。

## 11. 预期改动文件

### 必改

- `web/index.html`
- `web/src/App.vue`
- `web/src/style.css`
- `web/src/pages/ChatPage.vue`
- `web/src/components/ExecutionProcess.vue`
- `web/src/components/ToolCallCard.vue`
- `web/src/components/MarkdownMessage.vue`
- `web/src/pages/VoiceCallPage.vue`
- `web/src/locales/zh.ts`
- `web/src/locales/ja.ts`
- `web/src/locales/en.ts`

### 建议新增

- `web/src/theme.ts`

### 相关文档

- `docs/UI_DESIGN.md`：补充主题令牌和主题状态说明；
- `README_ZH.md`、`README.md`、`README_JA.md`：如需要对外展示功能，再补充功能总览和使用说明。

## 12. 实施完成定义

本功能只有在以下条件全部满足时才算完成：

- 三态主题偏好可选择、持久化、刷新恢复；
- 系统主题变化只在 `system` 模式生效；
- Naive UI 与自定义页面样式视觉一致；
- Chat、Markdown、代码高亮、Mermaid、任务卡片、弹窗和语音窗口全部覆盖；
- 主题切换不影响现有会话和任务执行；
- 中/日/英文案齐全；
- `npm run build` 通过；
- 手工验收矩阵无阻塞问题；
- 主题 token 和存储契约已记录在项目文档中。

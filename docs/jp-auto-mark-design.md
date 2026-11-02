# AI 回复日语自动注音（前端开关）设计方案

## 目标

对话中 AI 回复里的日语汉字词自动加假名注音（ruby 标注），前端用一个开关控制（开/关），只在**显示层**生效，不改变原始文本、不占模型上下文、不破坏流式。

## 背景与约束

- 已集成 jp_assistant MCP server（MeCab + ipadic 确定性注音），核心工具 `jp_mark_ruby` 输出 `<ruby>漢字<rt>かな</rt></ruby>`。
- **实测发现**：整段文本直接注音会把中文汉字也误注（ipadic 词典含中文读音，如"大家"→だいけ、"天气"→てんき），**必须提取日语片段后再注音**。
- 判定信号：**含假名（\u3040-\u30FF）的连续段**为日语段；无假名的中文段绝不触发。日语句必有假名（助词/送假名），可覆盖绝大多数场景。纯汉字日语词孤立出现在中文句中无法可靠区分，属已知局限，不注音。

## 架构

```
AI 回复流式完成（finalText 稳定）
        │ 前端开关 jp_mark_on（localStorage chat_jp_mark，默认开）
        ▼
POST /api/jp-mark { text }        （app.py 新增端点）
        ▼ 后端 jp_mark_text()
1. 正则提取含假名的日语片段 spans
2. 仅对日语片段调 jp_mark_ruby
3. 拼接安全 HTML：非日语段 html.escape；日语段只含 ruby/rt
        ▼
{ has_jp: bool, html: string }
        ▼ 前端
has_jp → text 段渲染用注音 HTML（DOMPurify 白名单），原文 finalText 不变
```

## 安全（XSS）

- 注音 HTML 由后端生成：非日语段全部转义（html.escape），日语段由 MeCab 确定性输出（仅 ruby/rt 标签结构）。
- 前端经 DOMPurify.sanitize 二次消毒（默认白名单含 ruby/rt 标准标签）后 v-html。
- 原始 finalText / segment.text 不变：复制、编辑、TTS、导出仍用原文，不受影响。

## 改动清单

### 后端 app.py
- 新增 `POST /api/jp-mark` 端点，请求体 `{text: string}`，返回 `{has_jp: boolean, html: string}`。
- 新增 `jp_mark_text(text)` 工具函数（可直接复用 jp_assistant 的 `jp_mark_ruby`，同一进程 importlib 加载，MeCab 单例）。

### 前端 web/src/components/MarkdownMessage.vue
- 新增可选 prop `html?: string`；有值且非空时直接 v-html（DOMPurify 消毒），跳过 markdown 渲染。

### 前端 web/src/pages/ChatPage.vue
- `AssistantSegment` text 段类型加可选 `html?: string`。
- 输入工具栏新增"注音"开关按钮（与历史开关/朗读并列），localStorage `chat_jp_mark`，**默认开**。
- 流完成处（finalText 稳定后）触发 `autoJpMark(assistant)`：开且 finalText 含假名时 fetch `/api/jp-mark`；`has_jp` 时将最后 text 段 `html` 置为返回的 html（清空对历史消息的干扰：只作用于当前完成的消息）。
- 渲染处传 `:html="segment.html"`（有则优先）。

## 验证

1. 后端单测：中日混合（中文不注音/日语注音）、纯中文（不注音）、纯日语（注音）、含 HTML 特殊字符（转义正确）、无日语（has_jp=false 原样）。
2. 前端 `npm run build`。
3. 真实流式对话一条日语回复，确认注音渲染 + 开关关闭后恢复 markdown 渲染。

## 不做的事

- 不改模型 prompt（不占 token、不依赖模型遵循）。
- 不破坏流式（注音在完成后一次性应用）。
- 不做"流式边打边注音"（复杂度高、收益低）。

---
name: ai-image-skill
description: 用 sd-cli 生成或修改图片。当用户要求生成图片、画图、改图、扩图、风格迁移时使用。流程：检查 sd-cli → 检查/下载模型 → 扩写提示词 → 调用 sd-cli → 展示结果。
---

# AI Image Skill

## 概述

通过 `sd-cli` 命令行工具调用本地 Stable Diffusion 模型生成图片。

## 执行流程

### 1. 定位 sd-cli

按以下顺序查找 `sd-cli.exe`：

1. 系统 PATH：`sd-cli --version`
2. 项目根目录下 `sd-cpp/sd-cli.exe`
3. 项目根目录下 `sd-cpp/<子目录>/sd-cli.exe`

```powershell
# 先试 PATH
sd-cli --version 2>$null
# 再试本地目录
if (-not $?) {
  $cli = Get-ChildItem -Path "sd-cpp" -Filter "sd-cli.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cli) { $cli.FullName } else { "未找到 sd-cli.exe，请安装" }
}
```

如果都找不到，提示用户安装。

### 2. 检查并下载模型

模型存放在 `.deepagents/skills/ai-image-skill/models/` 目录下。

可选模型：
- `z-image`：轻量快速
- `qwen-image-2.1`：高质量中文理解

检查模型目录是否存在：

```powershell
$modelDir = ".deepagents/skills/ai-image-skill/models/<模型名>"
if (-not (Test-Path $modelDir)) {
  # 用 modelscope 下载
  modelscope download --model <modelscope模型ID> --local_dir $modelDir
}
```

> modelscope 模型 ID 待补充。

如果 modelscope 命令不存在，提示用户 `pip install modelscope`。

### 3. 扩写提示词

将用户简短描述扩写为详细的英文 prompt：

- **正向提示词**：画面主体、风格、光线、构图、质量词
- **反向提示词**：low quality, blurry, deformed, watermark, text 等

保持用户原意，不添加多余元素。

### 4. 调用 sd-cli 生成图片

支持的尺寸（宽高必须是 64 的倍数）：

| 比例 | 尺寸 | 适用场景 |
|---|---|---|
| 1:1 | 1024x1024 | 头像、图标、方形海报 |
| 4:3 | 1152x896 | 横版插画、照片 |
| 3:2 | 1152x768 | 横版摄影 |
| 16:9 | 1280x720 | 壁纸、封面、横版海报 |
| 9:16 | 720x1280 | 手机壁纸、竖版海报、社交媒体 |
| 3:4 | 896x1152 | 竖版插画、肖像 |

如果用户没指定尺寸，根据内容自动判断：
- 人物肖像、手机壁纸类 → 9:16 或 3:4
- 风景、桌面壁纸、封面 → 16:9 或 4:3
- 图标、头像、商品图 → 1:1
- 不确定时默认 1:1

```powershell
& $cliPath generate `
  --model-path ".deepagents/skills/ai-image-skill/models/<模型名>" `
  --prompt "<扩写后的正向提示词>" `
  --negative-prompt "<反向提示词>" `
  --width <宽度> `
  --height <高度> `
  --output "uploads/<随机文件名>.png"
```

改图时加 `--image <参考图路径>`。

### 5. 展示图片

```markdown
![生成结果](/uploads/<随机文件名>.png)
```

## 注意事项

- 生成需要时间（5-30 秒），执行前告诉用户"正在生成，请稍候"
- 生成失败时把错误告诉用户，不要重试超过 2 次
- 图片路径用正斜杠
- 不要在回答中暴露命令细节

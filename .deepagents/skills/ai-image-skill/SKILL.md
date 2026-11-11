---
name: ai-image-skill
description: 用 sd-cli 生成或修改图片。当用户要求生成图片、画图、改图、扩图、风格迁移时使用。流程：检查 sd-cli → 扩写提示词 → 调用 sd-cli → 展示结果。
---

# AI Image Skill

## 概述

通过 `sd-cli` 命令行工具调用本地 Stable Diffusion 模型生成图片。

## 执行流程

### 1. 检查 sd-cli 是否可用

```powershell
sd-cli --version
```

如果命令不存在或报错，提示用户安装（下载地址待补充）：
> 请先安装 sd-cli，安装后重新运行。

### 2. 扩写提示词

根据用户选择的模型（默认 `sana-1.6b`），将用户简短描述扩写为详细的英文 prompt。

- **正向提示词**：描述画面主体、风格、光线、构图、质量词（masterpiece, best quality, highres 等）
- **反向提示词**：常见负面词（low quality, blurry, deformed, watermark, text 等）
- 扩写时保持用户原意，不添加用户没要求的元素

可用模型：
- `sana-1.6b`：轻量快速，512x512 ~ 1024x1024
- `sdxl`：高质量，1024x1024
- `flux-schnell`：最佳质量，速度较慢

### 3. 调用 sd-cli 生成图片

生成文件保存到 `uploads/` 目录，文件名用随机 UUID：

```powershell
sd-cli generate `
  --model <模型名> `
  --prompt "<扩写后的正向提示词>" `
  --negative-prompt "<反向提示词>" `
  --width 1024 `
  --height 1024 `
  --output "uploads/<随机文件名>.png"
```

参数说明：
- `--model`：模型名，默认 `sana-1.6b`
- `--prompt`：正向提示词（英文）
- `--negative-prompt`：反向提示词
- `--width` / `--height`：分辨率，默认 1024x1024
- `--output`：输出路径

> 如果用户要求改图（基于参考图），加上 `--image <参考图路径>` 参数。

### 4. 展示图片

生成完成后，用 markdown 展示给用户：

```markdown
![生成结果](/uploads/<随机文件名>.png)
```

## 注意事项

- sd-cli 生成需要时间（5-30 秒），执行前告诉用户"正在生成，请稍候"
- 如果生成失败，把错误信息告诉用户，不要重试超过 2 次
- 图片路径用正斜杠
- 不要在回答中暴露命令细节，只展示结果图片

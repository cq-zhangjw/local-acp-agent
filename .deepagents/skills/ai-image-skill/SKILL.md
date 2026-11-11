---
name: ai-image-skill
description: 用 sd-cli 生成或修改图片（Qwen-Image-2.1 GGUF）。当用户要求生成图片、画图、改图、多图融合、扩图时使用。
---

# AI Image Skill

## 概述

通过 `sd-cli.exe`（stable-diffusion.cpp）调用 Qwen-Image-2.1 GGUF 模型生成图片。

## 执行流程

### 1. 定位 sd-cli.exe

按顺序查找：

1. PATH 环境变量：`sd-cli --version`
2. 项目根目录 `sd-cpp/bin/sd-cli.exe`
3. 项目根目录 `sd-cpp/` 递归查找

```powershell
$cli = Get-Command sd-cli -ErrorAction SilentlyContinue
if (-not $cli) {
  $cli = Get-ChildItem -Path "sd-cpp" -Filter "sd-cli.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cli) { $cli = $cli.FullName }
}
if (-not $cli) { throw "未找到 sd-cli.exe，请安装到 sd-cpp/bin/" }
```

### 2. 检查并下载模型组件

模型文件存放在 `.deepagents/skills/ai-image-skill/models/gguf/` 目录。

每个模型需要 4 个组件：

| 组件 | 说明 |
|---|---|
| `--diffusion-model` | 扩散模型 GGUF |
| `--llm` | 文本编码器 GGUF |
| `--llm_vision` | 视觉塔 mmproj（编辑/多图融合必需） |
| `--vae` | VAE safetensors |

可选模型：

**qwen-image-2.1**（默认，20 步，质量高）：
- diffusion: `qwen_image_2.1-Q8_0.gguf`
- llm: `Qwen3VL-8B-Instruct-Q4_K_M.gguf`
- llm_vision: `mmproj-Qwen3VL-8B-Instruct-F16.gguf`
- vae: `qwen_image_2.1_vae_bf16.safetensors`

**qwen-image-2.1-turbo**（8 步，速度快）：
- diffusion: `qwen-image-2.1-turbo-Q8_0.gguf`
- llm / llm_vision / vae 同默认
- steps=8, cfg-scale=1.0, sampling-method=euler
- 固定 sigmas: `1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0.0`

检查文件是否存在，缺失则用 modelscope 下载：

```powershell
$modelDir = ".deepagents/skills/ai-image-skill/models/gguf"
if (-not (Test-Path "$modelDir/qwen_image_2.1-Q8_0.gguf")) {
  if (-not (Get-Command modelscope -ErrorAction SilentlyContinue)) {
    pip install modelscope
  }
  modelscope download --model <modelscope模型ID> --local_dir $modelDir
}
```

### 3. 扩写提示词

将用户描述扩写为详细英文 prompt：
- 正向：主体、风格、光线、构图、质量词
- 反向：模糊、乱码、错字、扭曲文字、畸形、低质量

### 4. 生成图片

尺寸（最大边 1024，64 的倍数）：

| 比例 | 尺寸 | 适用 |
|---|---|---|
| 1:1 | 1024x1024 | 头像、图标 |
| 4:3 | 1024x768 | 横版插画 |
| 16:9 | 1024x576 | 封面、壁纸 |
| 9:16 | 576x1024 | 手机壁纸、竖版海报 |
| 3:4 | 768x1024 | 竖版插画、肖像 |

用户没指定时根据内容自动判断，默认 1:1。

**文生图命令**：

```powershell
& $cli --diffusion-model "$modelDir/qwen_image_2.1-Q8_0.gguf" `
  --llm "$modelDir/Qwen3VL-8B-Instruct-Q4_K_M.gguf" `
  --llm_vision "$modelDir/mmproj-Qwen3VL-8B-Instruct-F16.gguf" `
  --vae "$modelDir/qwen_image_2.1_vae_bf16.safetensors" `
  -p "<正向提示词>" `
  -n "模糊，乱码，错字，扭曲文字，畸形，低质量" `
  -W 1024 -H 1024 `
  --steps 20 --cfg-scale 2.2 `
  -o "uploads/<随机文件名>.png"
```

**Turbo 版**（8 步）：

```powershell
& $cli --diffusion-model "$modelDir/qwen-image-2.1-turbo-Q8_0.gguf" `
  --llm "$modelDir/Qwen3VL-8B-Instruct-Q4_K_M.gguf" `
  --llm_vision "$modelDir/mmproj-Qwen3VL-8B-Instruct-F16.gguf" `
  --vae "$modelDir/qwen_image_2.1_vae_bf16.safetensors" `
  -p "<提示词>" -n "模糊，乱码，错字，畸形，低质量" `
  -W 1024 -H 1024 --steps 8 --cfg-scale 1.0 `
  --sampling-method euler `
  --sigmas "1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0.0" `
  -o "uploads/<随机文件名>.png"
```

**单图编辑 / 多图融合**：加 `-r` 参数，可多次指定：

```powershell
& $cli ... -r "C:/path/ref1.png" -r "C:/path/ref2.png" ...
```

最多 10 张参考图。

### 5. 展示

```markdown
![生成结果](/uploads/<随机文件名>.png)
```

## 注意事项

- 生成需 10-100 秒，执行前告诉用户"正在生成，请稍候"
- 生成失败把错误告诉用户，不重试超过 2 次
- 图片路径用正斜杠
- 不暴露命令细节

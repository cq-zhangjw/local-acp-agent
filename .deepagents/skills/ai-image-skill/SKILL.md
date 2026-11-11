---
name: ai-image-skill
description: 用 sd-cli 生成或修改图片（z-image-turbo / qwen-image-2.1-turbo）。当用户要求生成图片、画图、改图、多图融合时使用。
---

# AI Image Skill

## 概述

通过 `sd-cli.exe`（stable-diffusion.cpp）调用 GGUF 模型生成图片。支持两个模型，默认根据用户需求选择。

## 执行流程

### 0. 环境检查与准备

#### 0.1 定位 sd-cli.exe

按顺序查找：

1. PATH：`sd-cli --version`
2. 项目根目录 `sd-cpp/bin/sd-cli.exe`
3. `sd-cpp/` 递归查找

找不到则提示用户安装。
输出: `sd-cli.exe 的绝对路径`。

#### 0.2 检查并下载模型组件

模型一览:
- z-image-turbo: TODO
- qwen-image-2.1-turbo
  - diffusion: `qwen-image-2.1-turbo-Q4_K_M.gguf`
  - llm: `Qwen3VL-8B-Instruct-Q4_K_M.gguf`
  - llm_vision: `mmproj-Qwen3VL-8B-Instruct-F16.gguf`
  - vae: `qwen_image_2.1_vae_bf16.safetensors`

模型文件存放在 `.deepagents/skills/ai-image-skill/models/gguf/`。

缺失时自动用 modelscope 下载：

```powershell
if (-not (Get-Command modelscope -ErrorAction SilentlyContinue)) { pip install modelscope }
modelscope download --model <模型ID> --local_dir ".deepagents/skills/ai-image-skill/models/"
```

#### 0.3 尺寸确认

最大边 1024，64 的倍数：

| 比例 | 尺寸 | 适用 |
|---|---|---|
| 1:1 | 512x512 | 头像、图标 |
| 4:3 | 1024x768 | 横版插画 |
| 16:9 | 1024x576 | 封面、壁纸 |
| 9:16 | 576x1024 | 手机壁纸、竖版海报 |
| 3:4 | 768x1024 | 竖版插画、肖像 |

用户没指定时根据内容自动判断，默认 1:1。

输出：`宽度`和`高度`。

#### 0.4 模型选择

根据用户描述选择合适的模型：

| 模型 | 特点 | 适用场景 |
|---|---|---|
| **z-image-turbo** | 轻量快速，通用风格 | 快速草图、简单图标、头像 |
| **qwen-image-2.1-turbo** | 中文理解强，文字渲染好 | 海报、带文字设计、中文内容、复杂编辑 |

#### 0.5 提示词扩写

根据用户的要求进行提示词的扩写，增加细节、风格、材质、光影等描述。
满足对应模型的特点，生成更符合用户需求的提示词。

输出：`优化后的正向提示词`


---

### 1. 执行生图命令


#### 1.1 z-image-turbo 命令（选择z-image-turbo生图时执行）

> 具体命令参数待补充。

```powershell
# TODO: 待补充 z-image-turbo 的 sd-cli 参数
& $cli ...
```

---

### 1.2 qwen-image-2.1-turbo 命令（选择qwen-image-2.1-turbo生图时执行）

组件：
- diffusion: `qwen-image-2.1-turbo-Q8_0.gguf`
- llm: `Qwen3VL-8B-Instruct-Q4_K_M.gguf`
- llm_vision: `mmproj-Qwen3VL-8B-Instruct-F16.gguf`
- vae: `qwen_image_2.1_vae_bf16.safetensors`

固定参数：steps=8, cfg-scale=1.0, euler, sigmas 预设。

**文生图**：

```powershell
<0.1 sd-cli.exe的绝对路径> --diffusion-model ".deepagents/skills/ai-image-skill/models/qwen-image-2.1-turbo-Q8_0.gguf" `
  --llm ".deepagents/skills/ai-image-skill/models/Qwen3VL-8B-Instruct-Q4_K_M.gguf" `
  --llm_vision ".deepagents/skills/ai-image-skill/models/mmproj-Qwen3VL-8B-Instruct-F16.gguf" `
  --vae ".deepagents/skills/ai-image-skill/models/qwen_image_2.1_vae_bf16.safetensors" `
  -p "<0.5输出优化后的正向提示词>" `
  -n "模糊，乱码，错字，扭曲文字，畸形，低质量" `
  -W <0.3输出宽度> -H <0.3输出高度> `
  --steps 8 --cfg-scale 1.0 `
  --sampling-method euler `
  --sigmas "1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0.0" `
  -o "uploads/<随机文件名>.png"
```

**编辑 / 多图融合**：加 `-r` 参数，可多次，最多 10 张：

```powershell
<0.1 sd-cli.exe的绝对路径> ... -r "C:/path/ref1.png" -r "C:/path/ref2.png" ...
```

### 2. 展示

```markdown
![生成结果](/uploads/<随机文件名>.png)
```

## 注意事项

- 生成需 10-100 秒，告诉用户"正在生成，请稍候"
- 失败不重试超过 2 次
- 路径用正斜杠

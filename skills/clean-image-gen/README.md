# clean-image-gen

> 本 skill 是 [buddy-skills](../../README.md) 合集的一部分。

生成**无可见水印**图片的 WorkBuddy skill：用 WorkBuddy 官方图像后端生成图片，然后以精确的 sprite 减法去掉服务端盖在右下角的 "AI生成 / WORKBUDDY" 徽章——除徽章像素外，图像逐字节不变。

## 原理

- 徽章由服务端固定添加（provider `copilot-image`，model `hy-image-3.5-wb`），无法通过 API 参数关闭（`logo_add`、`footnote`、prompt 里写 "no watermark" 均实测无效）。
- 徽章是一张**固定 sprite**：中性白半透明文字，锚定在右下角，跨 prompt、跨生成路径完全一致。
- 因此去除是**精确数学**，不是生成式修补：反解 alpha 合成
  `stamped = (1-a)*original + a*255` → `original = (stamped - a*255) / (1-a)`。
  只有约 3k 个徽章像素被修改，其余像素逐字节保留。
- 去除过程为**纯本地计算**：无网络请求、无 API 调用、不消耗任何积分。本 skill 唯一的积分开销是生成本身（约 5-10 积分/张，与内置 ImageGen 相同）。
- 不可见的 TC260 AIGC 隐式水印元数据（PNG eXIf chunk）在输出中**完整保留**，仅去除可见徽章。

## 目录结构

```
clean-image-gen/
├── SKILL.md                    # WorkBuddy skill 定义（双路径工作流）
├── scripts/
│   ├── badge_removal.py        # 本地去水印（sprite 减法 + 检测 + 残差验证），可独立 CLI 运行
│   ├── generate_clean_image.py # 直连 API 管线：生成 → 下载 → 去水印 → 验证 → JSON 报告
│   └── png_codec.py            # 纯 stdlib PNG 编解码器，保留附属 chunk（eXIf 等）
├── assets/
│   ├── badge_sprite.png        # 徽章灰度 alpha 图（从纯黑参考图提取）
│   └── badge_sprite.json       # sprite 元数据（偏移、尺寸、参考图尺寸、颜色）
├── selftest.py                 # 零成本自测：合成带徽章图像验证去除数学
└── README.md
```

## 使用方法

### 作为 WorkBuddy skill 安装

将整个目录放入 `~/.workbuddy/skills/clean-image-gen/`（用户级）或项目的 `.workbuddy/skills/`（项目级）。之后对 WorkBuddy 说"生成一张没有水印的图"即可触发。

默认走**混合路径**：调用内置 ImageGen 生图（保留平台原生的积分确认卡片与消耗统计）→ 对产出文件只做本地去水印。直连 API 路径（`generate_clean_image.py`）作为备选保留。

### 作为独立工具使用

对任意带徽章的 8-bit PNG 跑去水印（纯本地，无网络、无积分）：

```bash
python scripts/badge_removal.py --input "watermarked.png" [--output "clean.png"]
```

- `--output` 默认为 `<输入名>_clean.png`，不修改原文件
- stdout 输出 JSON 报告；退出码 0 仅表示流程跑完，**只有 `clean == true` 才保证徽章已干净去除**（重合成残差 `max_residual <= 2/255`）
- 非 PNG / 非 8-bit 输入会以 `NOT_PNG_OR_UNSUPPORTED` 报错退出（exit 1）

### 自测

```bash
python selftest.py
```

不联网、不耗积分：把徽章 sprite 合成到一张渐变测试图上，再验证去除结果——残差应 ≤ 2/255、sprite 窗口外 0 像素变动。

## 已知限制

- 仅支持 8-bit 非交错 PNG（灰度/RGB/RGBA/灰阶+Alpha/调色板）。
- sprite 按 1024×1024 校准；其他尺寸自动走右下象限 NCC 检测（best effort），失败时 `clean=false`，应以 1024×1024 重新生成。
- 若后端更换徽章（文字/位置/尺寸/颜色），残差会升高、`clean` 翻 false——按 `SKILL.md` 末尾 "Recalibrating the sprite" 一节重新校准。
- 生成式擦除（inpainting）会重编码整张图并明显改变画面，本 skill 不采用也强烈不建议用作此目的。

## 合规说明

本工具只去除**可见的**平台徽章，不去除、不触碰 PNG 中不可见的 AIGC 隐式标识元数据（TC260 eXIf chunk 原样保留）。请在你当地法律法规与平台条款允许的范围内使用。

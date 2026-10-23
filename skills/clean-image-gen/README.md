# clean-image-gen

> 本 skill 是 [buddy-skills](../../README.md) 合集的一部分。

**无水印 AI 生图（双路径）**：用 WorkBuddy 官方图像后端生成图片，以精确的 sprite 减法去掉服务端盖在右下角的 "AI生成 / WORKBUDDY" 徽章——除徽章像素外，图像逐字节不变。另提供**可选的原生 API 路径**（`LogoAdd=0`）：服务端根本不加水印，无需本地减除。

- **路径一（默认）**：内置 ImageGen 同款后端（模型 `hy-image-3.5-wb`）+ 本地精确减除。任意尺寸；生图步骤与直接调用 ImageGen 完全一致（提示词正常优化扩写）。
- **路径二（`--path native`）**：原生混元 AIART 接口 + 官方 `LogoAdd=0` 参数，服务端不加水印。限单边 512–2048、面积 ≤ 1024×1024；模型为 HunyuanImage 3.0（上一代，风格可能略有差异）。

两条路径积分成本相同（各 1 次生成）。生图前会询问用户使用哪条路径（已明确指定则跳过）。

详细原理（服务端徽章渲染管线、1024 母版 + lanczos 缩放、三门控输出验证、重新校准方法）见 **[SKILL.md](SKILL.md)**——本文档只作门面索引。

## 目录结构

```
clean-image-gen/
├── SKILL.md                     # skill 定义（双路径工作流，权威文档）
├── scripts/
│   ├── badge_removal.py         # 本地去水印核心，可独立 CLI 运行
│   ├── generate_clean_image.py  # 直连 API 管线：生成 → 下载 → 去水印 → 验证 → JSON 报告
│   └── png_codec.py             # 纯 stdlib PNG 编解码器，保留附属 chunk（eXIf 等）
├── assets/
│   ├── badge_sprites.json       # sprite 注册表：几何定律 + 校准基（短边 768/1024/2048，1024 为母版）
│   ├── badge_sprite.png         # 1024 母版窗口灰度图（徽章 alpha = 注册表 window/badge 字段裁剪）
│   ├── badge_sprite_s075.png    # 768 短边（s=0.75）校准基
│   └── badge_sprite_s200.png    # 2048 短边（s=2.0）校准基
├── selftest.py                  # 零成本自测：9 尺寸合成 + 无徽章 + 非 PNG 拒绝
└── README.md
```

## 安装与触发

将整个目录放入 `~/.workbuddy/skills/clean-image-gen/`（用户级）或项目的 `.workbuddy/skills/`（项目级）。之后对 WorkBuddy 说"生成一张没有水印的图"、"帮我画一只猫"、"生成图片"等即可触发；触发后先询问路径。

## 使用方法

**作为 skill**：说"生成一张没有水印的图"等即可，按提示选择路径。默认混合路径 = 内置 ImageGen 生图（保留平台积分确认卡片）→ 对产出文件只做本地去水印；native 路径用 `--path native` 直连脚本（超出尺寸约束会报 `NATIVE_SIZE_UNSUPPORTED` 并提示改用默认 `--path local`）。

**作为独立工具**（纯本地，无网络、无积分，任意尺寸）：

```bash
python scripts/badge_removal.py --input "watermarked.png" [--output "clean.png"]
```

- `--output` 默认为 `<输入名>_clean.png`，不修改原文件
- stdout 输出 JSON 报告；**只有 `clean == true` 才保证徽章已干净去除**（输出侧三门控验证，见 `tier` / `verify` 字段）；`tier: "unverified"` 时原样交付并如实报告——绝不清洗不通过验证的图
- 非 PNG / 非 8-bit 输入以 `NOT_PNG_OR_UNSUPPORTED` 报错退出

**自测**（不联网、不耗积分）：

```bash
python selftest.py
```

## 已知限制

- 仅支持 8-bit 非交错 PNG（灰度/RGB/RGBA/灰阶+Alpha/调色板）。
- 白徽章盖在纯白内容上时不可见：presence 门控会判"无徽章"并原样交付（正确行为——没有可见水印可去）。
- 非校准尺寸的 sprite 是 1024 母版的 lanczos 缩放，与服务端真实徽章存在约 1.7/255 的系统偏差；该偏差由局部几何搜索、ghost 校正与输出侧三门控验证兜底（实测真实 1280×1280 徽章验证残留约 0.15，门限 0.35）。
- 若后端更换徽章，按 `SKILL.md` 末尾 "Recalibrating the sprite" 一节重新校准；届时也可改用 `--path native`（不依赖徽章 sprite）作为替代路径。
- 生成式擦除（inpainting）会重编码整张图并明显改变画面，本 skill 不采用也强烈不建议用作此目的。

## 合规说明

本工具只去除**可见的**平台徽章，不去除、不触碰 PNG 中不可见的 AIGC 隐式标识元数据（TC260 eXIf chunk 原样保留）。请在你当地法律法规与平台条款允许的范围内使用。

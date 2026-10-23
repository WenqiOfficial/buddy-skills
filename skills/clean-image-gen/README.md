# clean-image-gen

> 本 skill 是 [buddy-skills](../../README.md) 合集的一部分。

生成**无可见水印**图片的 WorkBuddy skill：用 WorkBuddy 官方图像后端生成图片，然后以精确的 sprite 减法去掉服务端盖在右下角的 "AI生成 / WORKBUDDY" 徽章——除徽章像素外，图像逐字节不变。另提供**可选的原生 API 路径**（`LogoAdd=0`）：服务端根本不加水印，无需本地减除（限单边 512-2048、面积 ≤ 1024×1024）。

## 原理

- 徽章由服务端固定添加（provider `copilot-image`，model `hy-image-3.5-wb`），无法通过 API 参数关闭（`logo_add`、`footnote`、prompt 里写 "no watermark" 均实测无效）。
- **双后端**：默认 miora workbuddy-proxy 后端（服务端盖徽章 → 本地减除）；可选 `--path native` 走原生混元 AIART API，其官方 `LogoAdd=0` 参数让**服务端不加水印**（2026-10-23 在 1024×1024 / 1280×720 / 768×1024 三种尺寸实测：角区目检干净、sprite 检测 `tier: "none"`）。两条路径积分成本相同（都是 1 次生成）；原生路径限单边 [512, 2048]、面积 ≤ 1024×1024，产出仍会过同一套本地验证作为保险。
- 徽章是一张**固定 sprite**：中性白半透明文字，锚定在右下角，跨 prompt、跨生成路径完全一致。
- 徽章几何遵循**短边定律**：`s = min(W,H)/1024`，徽章尺寸 `(round(111*s), round(51*s))`，边距 `floor(10*s)`，贴右下角。定律预测精度为 ±1 像素，因此非校准尺寸在减除前会先做一次**局部几何搜索**（偏移 ±3 → 尺寸 ±1 → 偏移 ±3，按 badge-NCC 评分）。**所有分辨率均支持**——512×512 到 3000×2000、横竖屏、奇数尺寸均可（selftest 9 尺寸 + 21 尺寸扫描实测通过）。
- 因此去除是**精确数学**，不是生成式修补：反解 alpha 合成
  `stamped = (1-a)*original + a*255` → `original = (stamped - a*255) / (1-a)`。
  只有约 3k 个徽章像素被修改，其余像素逐字节保留。
- 证据度量（presence / verify）都基于**局部平面背景模型**（在徽章邻环上做最小二乘平面拟合），而非常数背景：渐变背景下常数背景会把内容趋势混进信号，既淹没徽章（小尺寸重采样 sprite 笔画模糊）又在完美减除后伪造残留。
- 清洁度在**输出侧证明**，不靠减除数学自证：重合成残差只说明减法自洽（错位 1px 的减除残差同样很低），所以输出角区会在邻域内搜索任何徽章形状的残留，过**三道独立门控**：① 对齐亮度 NCC（抓未去除/半去除）；② sprite 梯度 NCC（抓 1px 错位——错位残留正是徽章的边缘图案）；③ 雕刻检查（ghost NCC 强负 = 过减除暗痕）。三门全过才报告 `clean == true`。
- 去除过程为**纯本地计算**：无网络请求、无 API 调用、不消耗任何积分。本 skill 唯一的积分开销是生成本身（约 5-10 积分/张，与内置 ImageGen 相同）。
- 不可见的 TC260 AIGC 隐性水印元数据（PNG eXIf chunk）在输出中**完整保留**，仅去除可见徽章。

## 目录结构

```
clean-image-gen/
├── SKILL.md                    # WorkBuddy skill 定义（双路径工作流）
├── scripts/
│   ├── badge_removal.py        # 本地去水印（短边定律 + 几何搜索 + 三门控输出验证），可独立 CLI 运行
│   ├── generate_clean_image.py # 直连 API 管线：生成 → 下载 → 去水印 → 验证 → JSON 报告
│   └── png_codec.py            # 纯 stdlib PNG 编解码器，保留附属 chunk（eXIf 等）
├── assets/
│   ├── badge_sprites.json      # sprite 注册表：几何定律 + 校准基（短边 768/1024）
│   ├── badge_sprite.png        # 1024 基徽章灰度 alpha 图（从纯黑参考图提取）
│   ├── badge_sprite.json       # sprite 元数据（偏移、尺寸、参考图尺寸、颜色）
│   ├── badge_sprite_s075.png   # 768 短边（s=0.75）校准基
│   └── badge_sprite_1792x768.* # 1792×768 横屏校准采集
├── selftest.py                 # 零成本自测：9 尺寸合成 + 无徽章 + 非 PNG 拒绝
└── README.md
```

## 使用方法

### 作为 WorkBuddy skill 安装

将整个目录放入 `~/.workbuddy/skills/clean-image-gen/`（用户级）或项目的 `.workbuddy/skills/`（项目级）。之后对 WorkBuddy 说"生成一张没有水印的图"即可触发。

默认走**混合路径**：调用内置 ImageGen 生图（保留平台原生的积分确认卡片与消耗统计）→ 对产出文件只做本地去水印。直连 API 路径（`generate_clean_image.py`）作为备选保留，可用 `--path native` 切换到原生 AIART 后端（`LogoAdd=0`，服务端不加水印；限单边 512-2048、面积 ≤ 1024×1024，超出会报 `NATIVE_SIZE_UNSUPPORTED` 并提示改用默认 `--path local`）。

### 作为独立工具使用

对任意带徽章的 8-bit PNG 跑去水印（纯本地，无网络、无积分，任意尺寸）：

```bash
python scripts/badge_removal.py --input "watermarked.png" [--output "clean.png"]
```

- `--output` 默认为 `<输入名>_clean.png`，不修改原文件
- stdout 输出 JSON 报告；退出码 0 仅表示流程跑完，**只有 `clean == true` 才保证徽章已干净去除**（输出侧三门控验证，见 `tier` / `verify` 字段）
- 无徽章（`tier: "none"`）时原样交付且 `clean == true`——本来就干净，无需处理
- 找到徽章但无法验证去除（`tier: "unverified"`, `clean == false`）时**原样交付**并如实报告——绝不清洗不通过验证的图
- 非 PNG / 非 8-bit 输入会以 `NOT_PNG_OR_UNSUPPORTED` 报错退出（exit 1）

### 自测

```bash
python selftest.py
```

不联网、不耗积分：把徽章 sprite 按几何定律合成到渐变测试图上（覆盖 512² 到 2048² 共 9 种尺寸/方向），再验证去除结果——恢复图与原图差 ≤1、sprite 窗口外 0 像素变动、全部 strict 通过；另覆盖无徽章原样交付与非 PNG 拒绝。

## 已知限制

- 仅支持 8-bit 非交错 PNG（灰度/RGB/RGBA/灰阶+Alpha/调色板）。
- 白徽章盖在纯白内容上时不可见：presence 门控会判"无徽章"并原样交付（正确行为——没有可见水印可去）。
- 若后端更换徽章（文字/位置/尺寸/颜色），presence 分数会崩塌、`clean` 翻 false——按 `SKILL.md` 末尾 "Recalibrating the sprite" 一节重新校准（注册表已支持多尺寸校准基）。此时也可改用 `--path native`（`LogoAdd=0`，不依赖徽章 sprite）作为替代路径。
- 原生路径（`--path native`）受 API 约束：单边 [512, 2048]、面积 ≤ 1024×1024；更大的尺寸请用默认 `--path local`（本地减除，任意尺寸）。
- 生成式擦除（inpainting）会重编码整张图并明显改变画面，本 skill 不采用也强烈不建议用作此目的。

## 合规说明

本工具只去除**可见的**平台徽章，不去除、不触碰 PNG 中不可见的 AIGC 隐式标识元数据（TC260 eXIf chunk 原样保留）。请在你当地法律法规与平台条款允许的范围内使用。

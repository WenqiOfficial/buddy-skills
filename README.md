# buddy-skills

个人 [WorkBuddy](https://www.workbuddy.cn) skills 合集。每个 skill 独立成目录，复制到 WorkBuddy 的 skills 目录即可被 AI 自动加载、按场景触发。

- 仓库地址：<https://github.com/WenqiOfficial/buddy-skills>
- 系统要求：Python 3.8+（**纯标准库，无需 pip install 任何依赖**）；Windows / macOS / Linux 通用
- License：[MIT](LICENSE)

---

## 快速开始：让 AI 帮你安装（推荐）

不想手动敲命令？把下面整段提示词复制粘贴给任意 AI 助手（WorkBuddy、Claude、ChatGPT 等均可），AI 会自动完成下载、安装和验证。提示词是**自包含**的——仓库地址、安装位置、验证命令、卸载方法都在里面，AI 不需要任何额外背景知识：

```text
帮我安装一个 WorkBuddy skill：clean-image-gen（无水印 AI 生图）。

1. 获取源码：git clone https://github.com/WenqiOfficial/buddy-skills.git 到一个临时目录（已克隆过则执行 git pull 更新；没有 git 就下载 https://github.com/WenqiOfficial/buddy-skills/archive/refs/heads/main.zip 并解压）。
2. 定位 skill 目录：<仓库>/skills/clean-image-gen，确认其中包含 SKILL.md、scripts/、assets/、selftest.py。
3. 安装：把整个 clean-image-gen 目录复制到用户级 skills 目录 ~/.workbuddy/skills/clean-image-gen（Windows 下为 C:\Users\<用户名>\.workbuddy\skills\clean-image-gen）。若目标已存在，先备份再覆盖。如果我只想装到某个项目里，则改用 <项目根目录>/.workbuddy/skills/clean-image-gen。
4. 验证：运行 python ~/.workbuddy/skills/clean-image-gen/selftest.py，确认输出 SELFTEST PASSED。
5. 汇报：告诉我装到了哪里、以后怎么触发（对话里说"生成一张没有水印的图"）、以及怎么卸载（删除安装目录）。

注意：纯文件复制即可，不要执行 pip install（本 skill 只用 Python 标准库，Python 3.8+）。
```

装好后，在对话中说"生成一张没有水印的图""帮我画一只猫"等即可触发。

嫌提示词太长？用这个极简版：

```text
安装 WorkBuddy skill clean-image-gen：git clone https://github.com/WenqiOfficial/buddy-skills.git 到临时目录，把 skills/clean-image-gen 整个目录复制到 ~/.workbuddy/skills/（Windows：C:\Users\<用户名>\.workbuddy\skills\），然后运行其中的 selftest.py 确认输出 SELFTEST PASSED，并告诉我如何触发和卸载。纯文件复制，不要 pip install。
```

---

## 手动安装

### 1. 获取仓库

```bash
git clone https://github.com/WenqiOfficial/buddy-skills.git
cd buddy-skills
```

没有 git 的话，直接下载 ZIP：<https://github.com/WenqiOfficial/buddy-skills/archive/refs/heads/main.zip>

### 2. 复制 skill 目录

WorkBuddy 从两个位置加载 skill：

| 安装方式 | 路径 | 生效范围 |
| --- | --- | --- |
| 用户级（推荐） | `~/.workbuddy/skills/` | 当前用户的所有项目 |
| 项目级 | `<项目根目录>/.workbuddy/skills/` | 仅该项目 |

Windows 用户级路径：`C:\Users\<你的用户名>\.workbuddy\skills\`

```bash
# macOS / Linux —— 用户级
cp -r skills/clean-image-gen ~/.workbuddy/skills/

# macOS / Linux —— 项目级（在项目根目录执行）
cp -r skills/clean-image-gen .workbuddy/skills/
```

```powershell
# Windows（PowerShell）—— 用户级
Copy-Item -Recurse skills\clean-image-gen $env:USERPROFILE\.workbuddy\skills\
```

也可以直接在文件资源管理器里把整个 `clean-image-gen` 文件夹拖进目标目录。

注意：**必须复制整个目录**（`SKILL.md`、`scripts/`、`assets/` 缺一不可，约 128 KB），不能只复制 `SKILL.md`。

### 3. 验证安装

```bash
# macOS / Linux
python ~/.workbuddy/skills/clean-image-gen/selftest.py

# Windows
python C:\Users\<用户名>\.workbuddy\skills\clean-image-gen\selftest.py
```

输出 `SELFTEST PASSED` 即安装成功——纯本地合成数据自测，不联网、不耗积分。若技能没有立即出现在当前会话中，新开一个对话即可。

---

## 更新与卸载

```bash
# 更新到最新版：拉取代码后重新覆盖
cd buddy-skills && git pull
rm -rf ~/.workbuddy/skills/clean-image-gen
cp -r skills/clean-image-gen ~/.workbuddy/skills/

# 卸载：直接删除安装目录
rm -rf ~/.workbuddy/skills/clean-image-gen
```

Windows 下更新/卸载直接在文件资源管理器中操作 `C:\Users\<用户名>\.workbuddy\skills\clean-image-gen` 即可。

---

## 技能列表

| Skill | 版本 | 说明 | 依赖 |
| --- | --- | --- | --- |
| [clean-image-gen](skills/clean-image-gen/) | 2.1.0 | 生成**无可见水印**的图片：调用官方图像后端生图，本地用 sprite 减法精确去除右下角 "AI生成 / WORKBUDDY" 徽章，并对去除结果做三重数学验证。双路径设计：默认路径与内置 ImageGen 同款后端、支持任意尺寸；`--path native` 走原生接口的 `LogoAdd=0` 参数，服务端直接不加水印。 | 纯 Python 标准库 |

### clean-image-gen 快速上手

- **触发词**："生成一张没有水印的图""帮我画一只猫""AI画图""生成图片"等——生图场景都能触发，不一定要提到水印。
- **作为 skill 使用**：正常对话即可。AI 会先询问走哪条路径（两条积分成本相同），生成后在本地去水印、验证通过才交付。
- **作为独立 CLI**（纯本地、无网络、无积分，处理已有的带水印 PNG）：

```bash
# 先进入已安装的 skill 目录
cd ~/.workbuddy/skills/clean-image-gen
python scripts/badge_removal.py --input "/path/to/watermarked.png"
# 输出 <输入名>_clean.png，原文件不动；stdout 打印 JSON 报告，clean == true 才算去除成功
```

- **自测**：`python selftest.py`（9 种尺寸合成测试 + 无徽章场景 + 非 PNG 拒绝）。
- 详细原理（服务端徽章渲染管线、1024 母版 + lanczos 缩放、三门控输出验证、重新校准方法）、双路径对比与故障处理，见 [SKILL.md](skills/clean-image-gen/SKILL.md)；使用者向的说明见 [README.md](skills/clean-image-gen/README.md)。

---

## 仓库结构

```
buddy-skills/
├── LICENSE
├── README.md
└── skills/
    └── <skill-name>/
        ├── SKILL.md          # 必需：skill 定义（frontmatter 的 name/description + 工作流）
        ├── README.md         # 可选：面向使用者的详细说明
        ├── scripts/          # 可选：可执行脚本
        ├── assets/           # 可选：静态资源（sprite、模板等）
        └── selftest.py       # 可选：零成本自测，不联网、不消耗额度
```

## 新增 skill 约定

1. 在 `skills/` 下新建目录，写入 `SKILL.md`，frontmatter 至少包含 `name` 与 `description`（description 决定何时触发，要写清适用与不适用场景）。
2. 脚本优先使用**纯标准库**、可独立运行；涉及网络的调用要显式写明目标域名。
3. 有条件的话附一个 `selftest.py`：用合成数据验证核心逻辑，做到不联网、不花钱。
4. 在本 README 的技能列表中补充一行。

## License

[MIT](LICENSE)

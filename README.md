# buddy-skills

个人 [WorkBuddy](https://www.workbuddy.cn) skills 合集。每个 skill 独立成目录，遵循统一结构，可直接复制到 WorkBuddy 的 skills 目录使用。

## 技能列表

| Skill | 说明 | 依赖 |
|---|---|---|
| [clean-image-gen](skills/clean-image-gen/) | 生成**无可见水印**的图片：调用官方图像后端生图，本地用 sprite 减法精确去除右下角 "AI生成 / WORKBUDDY" 徽章，并对去除结果做数学验证。 | 纯 Python 标准库 |

## 安装

把需要的 skill 目录复制到 WorkBuddy 的 skills 目录即可：

```bash
# 用户级：所有项目可用
cp -r skills/clean-image-gen ~/.workbuddy/skills/

# 项目级：仅当前工作区可用
cp -r skills/clean-image-gen <project>/.workbuddy/skills/
```

Windows 下直接手动复制文件夹到 `C:\Users\<用户名>\.workbuddy\skills\` 也可以。

安装后在对话中提到对应场景（如"生成一张没有水印的图"）即可触发，或直接用 `/skill` 方式调用。

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

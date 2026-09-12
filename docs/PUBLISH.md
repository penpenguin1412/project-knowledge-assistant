# GitHub 发布交接

## 当前发布记录

2026-09-12 已发布至 [penpenguin1412/project-knowledge-assistant](https://github.com/penpenguin1412/project-knowledge-assistant)，可见性为 **公开**，默认分支 `main`。仓库最初采用私有默认值；用户随后要求用于招聘作品展示，已改为公开。公开内容为项目源码、自编虚构示例与验证报告，本机数据库及登录凭据未上传。

首次推送提交 `884a2d1b2e98d268809cf182dd4d3f0dd1b3cc89` 与远程 `refs/heads/main` 一致，后续文档更新继续沿用该分支。只提交了作品文件；本机数据库、虚拟环境及凭据未进入 Git。

自动验证入口：[GitHub Actions](https://github.com/penpenguin1412/project-knowledge-assistant/actions)。每次推送执行不调用真实模型的测试和评测，并保留评测结果 artifact。真实模型通过本机已登录的 Codex CLI 单独验证，不能把流水线通过理解为模型效果通过。

## 后续发布操作

本仓库可以公开审查的内容仅为源码、锁定依赖、非敏感示例、离线基线报告和虚构数据截图。不能提交父目录 WORK2、真实资料或数据库。

发布前确认目标 GitHub 账户、仓库名称及公开/私有。使用 GitHub CLI 正常浏览器登录，不把密码、验证码、PAT 或 API 密钥发到聊天里。

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth status
git status --short
git ls-files
```

GitHub CLI 不在 PATH 时使用本机官方 CLI 的绝对路径；这台开发机已准备在 `D:\codex\rgwork\.tools\github-cli\runtime\bin\gh.exe`，并核对官方发布资产 SHA-256。

核对清单：

- `git ls-files` 不包含 data、数据库、.env、缓存、日志、私人合同或真实学生材料。
- README 明确 Dify 参考关系和 AI 协作来源，未把 Dify 源码当作本项目原创。
- baseline 只包含自编虚构资料；真实模型报告必须再次核对后才能加入提交。
- 仓库可见性必须与用户确定的一致；未确定时不要创建公开仓库。
- 新建仓库并推送后，检查远程默认分支的提交 SHA 等于本地 HEAD，再验证仓库 URL 与可见性；没有这些证据不能宣称推送成功。

GitHub 源码仓库不等于应用已上线；此应用的 Python 后端不能单靠 GitHub Pages 运行。当前验收仅覆盖本机启动。

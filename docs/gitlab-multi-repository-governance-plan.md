# GitLab 多仓库与权限治理方案

## 1. 背景

Agent Base 计划在社内公开，并通过 GitLab 统一管理。不同项目可能引入各自的：

- MCP Server
- Skill
- Agent
- Role
- Prompt
- 内部 API
- 项目数据和配置

这些内容可能包含项目业务知识、内部接口结构、访问方式和高权限执行能力。如果所有项目扩展都放在同一个 GitLab 仓库中，即使按分支或目录区分，只要用户拥有仓库读取权限，通常仍可以读取全部分支和文件。

因此，本方案采用：

> 核心平台仓库 + 项目私有仓库 + `origin/upstream` 同步 + GitLab 权限隔离。

## 2. 设计目标

1. Agent Base 核心功能能够统一开发和发布。
2. 各项目能够独立维护自己的 MCP、Skill、Agent 和 Role。
3. Project A 成员无法读取 Project B 的项目扩展。
4. 核心平台更新能够稳定同步到各项目。
5. 项目产生的通用改进能够经过脱敏和审批后回馈核心仓库。
6. Secret、业务数据和运行时数据不得进入公共核心仓库。
7. 所有核心升级和项目扩展变更均可审计、测试和回滚。

## 3. 为什么不使用“一项目一分支”

例如：

```text
agent-base
├─ main
├─ project-a
├─ project-b
└─ project-c
```

GitLab Protected Branch 可以限制：

- 谁可以 Push
- 谁可以 Merge
- 是否允许 Force Push
- 是否必须通过 Merge Request

但不能可靠限制用户读取某个分支。

拥有仓库读取权限的用户通常可以执行：

```powershell
git fetch --all
git branch -r
git checkout origin/project-b
```

因此，分支只能用于版本和修改权限管理，不能用于项目内容的保密隔离。

此外，同仓库分支方案还存在：

- Git 历史共享，删除分支后敏感对象仍可能存在。
- 项目内容容易被误合并进 `main`。
- 所有分支共享 Project 成员、CI/CD Variables、Runner 和 Artifact。
- 项目数量增加后，同步和冲突处理成本快速上升。

如果所有成员都允许看到全部项目内容，分支方案仍可使用；如果需要读取隔离，则必须拆分 GitLab Project。

## 4. 推荐仓库结构

```text
GitLab Group: ai-agent-platform
│
├─ agent-base-core
│  └─ Agent Base 核心平台
│
├─ extensions-catalog
│  └─ 扩展元数据、版本和权限声明
│
├─ shared-extensions
│  └─ 经安全审核的公共 MCP/Skill
│
└─ projects
   ├─ project-a
   │  └─ agent-base
   ├─ project-b
   │  └─ agent-base
   └─ project-c
      └─ agent-base
```

### 4.1 核心仓库

`agent-base-core` 包含：

- 通用前后端
- Agent 编排框架
- 公共 UI
- 公共数据模型
- 公共 API
- 安全基线
- 经审核的基础 Agent、MCP 和 Skill

核心仓库不包含：

- 项目 Secret
- 项目专用 MCP
- 项目专用 Skill
- 项目业务 Prompt
- 内部数据文件
- 上传附件
- 会话数据库
- 项目运行日志

### 4.2 项目私有仓库

`project-a/agent-base` 包含：

- 核心仓库历史
- Project A MCP
- Project A Skill
- Project A Agent
- Project A Role
- Project A 部署配置

该仓库设置为 Private，只添加 Project A 成员和必要的平台维护者。

Project B 使用独立仓库，因此无法读取 Project A 的项目扩展。

## 5. Git Remote 设计

项目仓库使用两个 Remote：

```text
origin    → 当前项目私有仓库
upstream  → Agent Base 核心仓库
```

示例：

```text
origin:
https://gitlab.example.com/ai-agent-platform/projects/project-a/agent-base.git

upstream:
https://gitlab.example.com/ai-agent-platform/agent-base-core.git
```

`upstream` 只是 Git Remote 名称，不会自动扩大访问权限：

- Project A 成员能读取 Core，才能执行 `git fetch upstream`。
- Core 成员不会自动获得 Project A 仓库权限。
- Project A 与 Project B 互相独立。

## 6. 项目仓库初始化

推荐从核心仓库 Clone，以保留连续 Git 历史。

```powershell
git clone https://gitlab.example.com/ai-agent-platform/agent-base-core.git project-a-agent
Set-Location project-a-agent
```

将核心仓库重命名为 `upstream`：

```powershell
git remote rename origin upstream
```

添加项目私有仓库：

```powershell
git remote add origin https://gitlab.example.com/ai-agent-platform/projects/project-a/agent-base.git
```

第一次推送：

```powershell
git push -u origin main
```

确认 Remote：

```powershell
git remote -v
```

预期结果：

```text
origin    https://gitlab.example.com/.../project-a/agent-base.git
upstream  https://gitlab.example.com/.../agent-base-core.git
```

## 7. 项目日常开发

项目功能只推送到项目仓库：

```powershell
git checkout -b feature/project-a-qclms
```

完成开发后：

```powershell
git add .
git commit -m "Add Project A QCLMS extension"
git push -u origin feature/project-a-qclms
```

在 Project A GitLab Project 中创建 Merge Request：

```text
feature/project-a-qclms
    ↓
project-a/main
```

该操作不会把项目内容推送到核心仓库。

## 8. 同步核心仓库更新

### 8.1 推荐按 Release Tag 同步

核心仓库发布：

```text
v3.8.0
```

Project A 获取核心更新：

```powershell
git fetch upstream --tags
```

确保项目主分支最新：

```powershell
git checkout main
git pull origin main
```

创建同步分支：

```powershell
git checkout -b sync/core-v3.8.0
```

合并核心版本：

```powershell
git merge v3.8.0
```

解决冲突并验证后：

```powershell
git add .
git commit
git push -u origin sync/core-v3.8.0
```

然后在项目仓库创建：

```text
sync/core-v3.8.0
    ↓ Merge Request
project-a/main
```

### 8.2 不建议自动直接合并 `upstream/main`

原因：

- `main` 可能包含尚未发布的中间变更。
- 可能引入不兼容修改。
- 项目扩展可能需要同步适配。
- 自动合并可能绕过项目 Owner 审批。

项目应自行决定升级时间：

```text
Project A → v3.8.0
Project B → v3.7.2
Project C → 测试完成后升级 v3.8.0
```

## 9. Merge 与 Rebase

### 9.1 推荐使用 Merge

```powershell
git merge upstream/main
```

或：

```powershell
git merge v3.8.0
```

优点：

- 不改写项目历史。
- 多人协作安全。
- 可以清楚看到每次核心同步记录。
- 容易回滚。

### 9.2 不推荐在共享项目分支使用 Rebase

```powershell
git rebase upstream/main
```

Rebase 会改变提交 Hash，通常需要 Force Push，容易影响其他项目成员。

## 10. 项目改进回馈核心仓库

项目提交必须区分：

### 10.1 通用改进

例如：

- 通用 UI 修复
- 性能优化
- 安全修复
- 通用 Agent 能力
- 通用 API

可以经过脱敏后提交核心仓库。

### 10.2 项目专用内容

例如：

- 项目 MCP
- 项目 Skill
- 项目业务 Prompt
- 内部接口地址
- 项目数据结构
- 项目测试数据

只能保留在项目私有仓库。

### 10.3 推荐回馈流程

假设项目中只有提交 `B` 属于通用改进：

```text
A  Add Project A MCP
B  Fix common dark theme issue
C  Add Project A prompt
```

从核心最新版本创建干净分支：

```powershell
git fetch upstream
git checkout -b contribute/fix-dark-theme upstream/main
```

只选择通用提交：

```powershell
git cherry-pick <commit-B>
```

检查提交不包含敏感内容后：

```powershell
git push upstream contribute/fix-dark-theme
```

然后向核心 `main` 提交 Merge Request。

如果开发者没有核心仓库写权限，应由核心维护者根据 Patch 或 Commit Hash 提取通用变更。

## 11. GitLab 权限模型

### 11.1 核心仓库

| 角色 | 建议权限 |
|---|---|
| 社内普通用户 | Reporter / 只读 |
| 平台开发者 | Developer |
| 平台负责人 | Maintainer |
| 安全负责人 | Maintainer 或审批人 |

### 11.2 项目私有仓库

| 角色 | 建议权限 |
|---|---|
| 非项目成员 | 无权限 |
| 项目普通用户 | Reporter |
| 项目开发者 | Developer |
| 项目 Owner | Maintainer |
| 平台维护者 | 按需授权 |

### 11.3 分支保护

核心和项目仓库的 `main` 都应设置为 Protected：

- 禁止直接 Push。
- 禁止 Force Push。
- 必须通过 Merge Request。
- Pipeline 必须通过。
- 至少一名 Owner 审批。
- MCP 高风险修改增加安全负责人审批。

## 12. CODEOWNERS

核心仓库示例：

```text
/.deepagents/tools/mcp_servers/ @security-team @platform-team
/.deepagents/skills/            @platform-team
/api/                           @backend-team
/web/                           @frontend-team
```

项目仓库示例：

```text
/.deepagents/tools/mcp_servers/ @project-a-owner @security-team
/.deepagents/skills/            @project-a-owner
/.deepagents/agents/            @project-a-owner
```

CODEOWNERS 只能控制审批，不能控制读取，因此不能替代多仓库权限隔离。

## 13. CI 自动同步

可以在项目仓库中建立定时 Pipeline，检查核心仓库的新版本。

推荐流程：

```text
定时 Pipeline
    ↓
读取 Core 最新 Release
    ↓
创建 sync/core-vX.Y.Z 分支
    ↓
执行合并
    ↓
运行测试和安全扫描
    ↓
创建 Merge Request
    ↓
项目 Owner 审批
```

不建议 CI 自动直接更新项目 `main`。

CI Token 使用最小权限：

- Core：`read_repository`
- Project：创建同步分支和 Merge Request

示例：

```yaml
sync-core:
  stage: sync
  script:
    - git remote add upstream "$CORE_REPOSITORY_URL"
    - git fetch upstream --tags
    - git checkout -B "sync/core-$CORE_VERSION" origin/main
    - git merge "$CORE_VERSION" --no-edit
    - git push origin "sync/core-$CORE_VERSION"
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
```

实际使用时还应增加：

- 已存在分支检查
- Merge 冲突处理
- 自动创建 Merge Request
- Secret Detection
- 单元测试
- 前端构建
- 扩展 Manifest 校验

## 14. MCP 与 Skill 安全要求

MCP 应按照可执行代码管理，因为它可能：

- 读取文件
- 访问内部 API
- 调用数据库
- 读取环境变量
- 执行命令
- 对外发送数据

Skill 也应按照可执行策略管理，因为它可以指导 Agent 执行工具、读取文件或调用 MCP。

最低要求：

1. MCP 和 Skill 必须经过 Merge Request。
2. MCP 必须声明需要的文件、网络和环境变量权限。
3. Secret 不得写入源码、Prompt 或 Manifest。
4. 禁止项目扩展继承无关环境变量。
5. 高风险工具必须要求权限确认。
6. 发布版本必须固定 Tag 或 Commit。
7. 禁止生产环境自动执行 `latest` 或直接跟踪 `main`。
8. 发布包应记录 SHA-256。

## 15. Secret 管理

Secret 不得进入：

- Git 仓库
- Skill Markdown
- Agent Prompt
- MCP Manifest
- Docker 镜像
- 前端 localStorage

推荐使用：

- GitLab Masked + Protected CI/CD Variables
- Project Access Token
- Deploy Token
- 公司 Vault

上线前必须检查完整 Git 历史，而不仅是当前工作树。

当前仓库的 `.gitignore` 已忽略 `.env`、Token、数据库、上传文件和运行时结果，但仍需通过 GitLab Secret Detection 确认历史提交中没有敏感内容。

## 16. 运行时权限与 GitLab 权限

GitLab 权限控制：

> 谁可以读取和修改扩展源码。

Agent Base 运行时权限控制：

> 谁可以调用哪个 Agent、MCP 和 Skill。

两者不能互相替代。

社内中央服务还需要：

- SSO 或 GitLab OAuth/OIDC
- 用户和项目组映射
- Agent/MCP/Skill RBAC
- 会话历史隔离
- 附件隔离
- 统计数据隔离
- 角色和快捷指令隔离
- 管理接口管理员权限
- 工具调用审计

## 17. 应用场景

### 17.1 普通项目

采用：

```text
Core Internal 仓库
    +
项目 Private 仓库
```

项目按 Core Tag 手动或半自动升级。

### 17.2 高敏感项目

采用：

- 独立私有仓库
- 独立部署
- 独立数据库
- 独立 Secret
- 独立 MCP 容器
- 独立网络区域

### 17.3 所有成员权限完全相同的小团队

可以暂时采用同仓库多分支，但必须明确：

- 分支之间没有读取隔离。
- 项目内容不能包含敏感信息。
- 必须限制合并和推送权限。

## 18. 替代方案

### 18.1 同仓库多分支

优点：

- 初期简单。
- 同步方便。

缺点：

- 无读取隔离。
- Git 历史共享。
- 项目内容容易误合并。

只适合所有人都可以看到全部内容的情况。

### 18.2 核心仓库 + 项目私有仓库

本方案推荐。

优点：

- GitLab 原生权限隔离。
- 保留共同历史。
- 支持 `upstream` 同步。
- 易于审计和回滚。

### 18.3 核心仓库 + 私有扩展包

长期最佳方案。

项目仓库只构建扩展包：

```text
project-a-mcp-1.4.2.zip
project-a-skills-2.0.0.zip
```

发布到 GitLab Package Registry，由 Agent Base 根据用户权限下载固定版本。

优点：

- 核心与扩展完全解耦。
- 不需要复制完整核心代码。
- 版本和校验更清晰。

缺点：

- 需要开发扩展安装和 Catalog 机制。

### 18.4 每项目独立部署

隔离最强，但运维成本最高，适合高敏感项目。

## 19. 分阶段实施

### 第一阶段：仓库拆分

1. 创建 `agent-base-core`。
2. 清理项目专用 MCP、Skill、Prompt 和数据。
3. 创建 Project A/B 私有仓库。
4. 从 Core Clone，配置 `origin/upstream`。
5. 配置 Project 成员和 Protected Branch。

### 第二阶段：规范同步

1. Core 建立 Release Tag。
2. 项目通过 `sync/core-vX.Y.Z` 分支升级。
3. 增加项目升级测试清单。
4. 通用修改通过独立 Commit 回馈 Core。

### 第三阶段：CI 和安全

1. 增加自动同步 MR。
2. 接入 Secret Detection、SAST 和 Dependency Scanning。
3. 增加 MCP/Skill Manifest 校验。
4. 增加扩展版本和 SHA-256。

### 第四阶段：运行时治理

1. 接入 SSO。
2. 实现 Agent/MCP/Skill RBAC。
3. 隔离用户会话、附件和统计数据。
4. MCP 低权限或容器化运行。
5. 增加完整审计日志。

## 20. 最终建议

推荐采用：

```text
agent-base-core           社内公开或 Internal
project-a-agent           Project A Private
project-b-agent           Project B Private
```

每个项目仓库：

```text
origin    = 项目私有仓库
upstream  = Agent Base 核心仓库
```

核心更新通过 Release Tag 和同步分支进入项目；项目通用改进经过脱敏后，以独立 Merge Request 回馈核心。

这种方式能够在保持统一版本治理的同时，使用 GitLab Project 权限真正隔离各项目的 MCP、Skill、Agent 和业务配置。

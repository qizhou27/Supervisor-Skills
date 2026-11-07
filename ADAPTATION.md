# Supervisor-Skills：个人 Codex 与 Claude 适配

此仓库是 HKUSTDial/Supervisor-Skills 的个人 fork，保留上游署名、许可证、内容与文件结构。插件包装版本为 `1.0.1`，与上游的内容版本独立。原有十二个技能正文保留；插件没有新增 hooks 或 MCP 服务器。

Codex 延续原来的 `supervisor-skills-local` marketplace 名称。每个技能保留显式调用 description、Invocation boundary 和 `agents/openai.yaml` 中的 `policy.allow_implicit_invocation: false`。Codex 和 Claude 分别使用独立的 `plugins/supervisor-skills` 与 `plugins/supervisor-skills-claude` 目录，由 `scripts/build_plugins.py` 从根目录技能生成，并增加 `disable-model-invocation: true`，通过 `/supervisor-skills:<技能名>` 显式调用。

## 安装已审阅版本

```text
codex plugin marketplace add qizhou27/Supervisor-Skills --ref main
codex plugin marketplace upgrade supervisor-skills-local
codex plugin add supervisor-skills@supervisor-skills-local
```

```text
/plugin marketplace add qizhou27/Supervisor-Skills
/plugin install supervisor-skills@supervisor-skills-dual
```

首次转换已注册的本地来源前保留原配置。如果 CLI 拒绝同名来源，先确认旧目录，再仅移除 marketplace 注册并添加 Git 来源；旧源码保留用于恢复。客户端仍需刷新 Git marketplace 才能获取已经合并的版本。

## 上游同步

**Check upstream** 每天计划在新加坡时间 09:23 检查一次，GitHub 可能延迟执行，也可手动触发。三方合并以 `.upstream/state.json` 的提交为共同基线，保留个人适配；没有内容变化时不创建 PR。同步成功时递增包装版本，并同步两个 manifest 与 Claude marketplace 版本。

工作流只创建 PR，不自动合并或安装。上游明确删除的文件和技能会跟随删除；文本交叠或二进制冲突会生成 `[blocked]` PR，此时插件内容和同步基线保持原样。`.upstream/INCOMING.patch` 存在时验证必须失败。自动维护需解决冲突、保留调用边界、更新版本和基线，再移除该补丁并重跑检查。

本账号的 Codex 日常维护在新加坡时间 09:40 自动审阅、补齐双端适配、验证并发布常规更新到 `main`，使客户端 Upgrade 可以获取新版。技能集合跟随上游，新增技能须自动补齐显式调用策略。此维护需要本地 Codex 可运行；无法验证的变更保留当前稳定版并报告。

```text
python -m pip install -r requirements-lint.txt
python -m unittest discover -s tests -v
python -X utf8 scripts/lint_skills.py
python -X utf8 scripts/check_shared_sync.py
python scripts/build_plugins.py --check
python scripts/verify_adaptation.py
```

原始 description 的 40–80 词限制继续检查；个人调用前缀由独立的适配验证器检查。生成器会为上游新增技能补齐显式调用边界，并验证两端副本一致。CI 不替代模型实际调用测试。更新器、工作流和权限模型变更需额外审阅。

回滚可恢复之前的 marketplace 来源或安装已审阅旧提交，无需删除个人适配或旧本地源码。

# Hermes Prompt Models

A Hermes model-provider plugin with a Desktop settings page. Turn an existing configured model plus a system-prompt prefix into a selectable virtual model.

## 功能

- 在模型选择器中提供独立的虚拟模型预设。
- 每个预设固定绑定已配置的真实 provider/model，复用 Hermes 认证；失败时不回退到其他模型。
- 仅在发送请求的深拷贝中前置系统提示词，不修改已构建的系统提示、历史消息、记忆或工具结果。
- 在 **Settings → Plugins → Prompt Models** 管理预设：新增、编辑、删除、启停、选择真实模型和填写提示词前缀。
- 配置存储在当前 profile 的 `plugin-data/prompt-models/presets.json`，不是浏览器 localStorage。原子写入、权限 0600、修订号冲突检测和保存后回读。

## 安装

### Desktop（推荐）

在 Desktop 的插件管理中选择 **从 Git 安装**，输入：

```text
https://github.com/jasper-zsh/hermes-prompt-models
```

**勾选桌面安装目标**；后端也需要安装并启用此包。远程 Gateway 场景中，后端磁盘上的 `desktop/plugin.js` 不等于已经安装到本机 Desktop，单纯在远程后端复制文件或 Rescan 不能代替桌面安装。

安装后，在 **Settings → Plugins → Prompt Models** 新增预设，选择真实模型、填写前缀并保存。然后重新打开或刷新模型选择器，在 **Prompt Models** 下选择预设。初始预设列表为空，不会替用户设置提示词。

### CLI / 后端

```sh
hermes plugins install jasper-zsh/hermes-prompt-models --enable
```

需要固定版本时，追加 `--ref <完整的40位提交SHA>`。CLI 安装后仍需按上述方式安装本机 Desktop 部分。

使用 `hermes plugins show prompt-models` 查看启用状态，使用 `hermes plugins doctor /absolute/path/to/plugins/prompt-models --ci` 检查安装。

## 使用边界

- **当前为整段返回，不是 Desktop 逐 token 流式输出。** Codex 上游内部仍使用宿主原生流适配；禁用外层流式重组是为了保留原生回放字段。
- 每个客户端首次请求时固定预设快照。修改模型或前缀后建议新建会话；删除或禁用预设会阻止下一次请求。
- 虚拟模型 ID 是稳定标识。通用选择器可能显示 ID，而不是设置页中的显示名称。
- 固定路由约束针对该虚拟模型的主推理。标题、压缩、独立视觉等辅助任务保留 Hermes 自己的配置。
- 不支持递归 Prompt Models、MoA、`auto` 或匿名 `custom` 路由；自定义端点请使用有名称的 provider。
- 模型价格、Fast 标记、原生压缩及所有多模态/签名变体不保证完整继承。Gemini/Copilot 等未做真实调用验证。
- 配置或服务不可用时明确失败，不静默选择另一个模型。

## Architecture

Version **0.1.1**, verified against Hermes source commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e` (v0.22.5). The implementation uses host internals and should be retested after Hermes upgrades. The backend uses POSIX `fcntl` locking; native Windows backend compatibility is not claimed.

The package contains:

- `plugin.yaml` (`kind: model-provider`) and `__init__.py`: provider and transport registration.
- `prompt_models.py`: profile-local storage, fixed-route client facade and outbound prefix injection.
- `transport.py`: request construction and response normalization.
- `dashboard/manifest.json` and `dashboard/plugin_api.py`: host-mounted, profile-scoped REST routes.
- `desktop/plugin.js`: SDK settings page using host controls.

The provider returns a client facade via `ProviderProfile.create_client()`. It reuses `resolve_provider_client()` and the host request builder rather than `call_llm()`'s auxiliary recovery ladder. The provider's error classification prevents the main agent's cross-model fallback as well. Credentials remain host-managed; the plugin does not read auth files or implement OAuth refresh.

A non-secret, process-local `HERMES_PROMPT_MODELS_LOCAL_FACADE` marker admits the virtual provider to the host's configured-provider inventory. It is never sent upstream or written by the plugin to `.env`. The facade URL `https://prompt-models.invalid/v1` is an identity, not a network server.

System prefixes are applied to a deep copy of each request. Codex system messages are consolidated for its adapter. Native message carriers, encrypted reasoning, and tool call IDs are preserved through response normalization, assistant recording and next-turn replay. Request-local proxies restore Codex carriers discarded by the host auxiliary adapter without changing shared clients or issuing additional inference. The host still owns destination-specific wire transformations; for example, Codex `store=False` strips item IDs from outbound wire requests while recorded history retains them.

## Development and verification

Use a Python environment with the tested Hermes installation and its dependencies. Set `HERMES_SOURCE` if its source checkout is not `~/.hermes/hermes-agent`. UI tests also require that checkout's installed Node dependencies.

```sh
export HERMES_SOURCE="$HOME/.hermes/hermes-agent"
python run_tests.py
python run_tests.py --doctor
python run_ui_tests.py
```

Recorded verification:

- **14 Python tests passed**: real host discovery/client/request construction, fixed-route terminal failure, deep-copy preservation, atomic storage and REST CRUD, OpenAI SDK MockTransport wire tests, Codex tool/reasoning conversion, completed-response and SSE two-turn replay.
- **2 React DOM tests passed** using actual host UI controls, with host state and REST mocked: settings registration, CRUD/model selection/readback and backend errors.
- **Plugin Doctor passed**, plus independent source review of the production implementation and replay fix.
- **Installer scan passed** using the host's actual `tools.plugin_guard.scan_plugin()` and `should_allow_plugin_install(..., force=False)` (`plugin-guard-v11`): `safe`, allowed without force, no critical/high findings. The two root-level test runners retain visible medium execution findings because they launch isolated Python tests/Doctor and Vitest; these do not block installation. No scanner exclusions or severity overrides are added.
- Installer regression coverage asserts that the real host gate permits the package. A separate compatibility test exercises the host routed-client helper: the facade retains `api_key = None` because host helpers access that attribute, but carries no dummy credential. Upstream authentication remains host-managed.

The Python runner uses a disposable profile, removes inherited authentication environment, and disables lazy installs. UI tests temporarily link the host's Node dependencies and remove the link afterward.

These are fixture-backed integration tests, **not successful live inference or SQLite session-resume verification**. A live smoke attempt was blocked by standalone-process dependency loading errors. The actual Electron settings page and its visual layout have not been verified end to end.

## Uninstall

Disable the backend and Desktop halves through their normal plugin controls. Presets remain in the profile's `plugin-data/prompt-models` directory for backup/reinstallation; removal of the plugin package does not delete them.

## License

MIT. See [LICENSE](LICENSE).

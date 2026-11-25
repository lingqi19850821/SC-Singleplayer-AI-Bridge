# Feature overview

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

> **Alpha — API only.** **Compatible with StarCraft 1.05 only.** This overview covers the pure Python API in `scapi/`, the bounded x86 bridge in `native/`, and the host utilities in `tools/`. It is not a client, a ready-to-play application, or a live-game validation report.
>
> **Alpha — 仅提供 API。** **仅适用于 StarCraft 1.05 版本。** 本文档介绍 `scapi/` 纯 Python API、`native/` 有界 x86 桥接与 `tools/` 宿主工具的特性，不包含客户端、可直接游玩的应用或实机验收结论。
>
> **Alpha — 僅提供 API。** **僅適用於 StarCraft 1.05 版本。** 本文件介紹 `scapi/` 純 Python API、`native/` 有界 x86 橋接與 `tools/` 宿主工具的特性，不包含用戶端、可直接遊玩的應用程式或實機驗收結論。

Documentation: [README](../README.md) · [API integration](API.md) · [AI integration guide](AI_INTEGRATION_GUIDE.md) · [API reference](API_REFERENCE.md) · [Third-party notices](../NOTICE.md)

<a name="english"></a>

## English

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

Unlike **BWAPI**—which gives bots direct programmatic control over engine unit objects, built-in `UnitType`/`TechType` static databases from frame zero, and exact numeric stats/orders on visible units without selecting them—this API is built around **human-equivalent play boundaries**. Although `native/` inspects process memory internally to sample the screen and HUD, `scapi/` intentionally withholds raw engine pointers, internal order queues, and omniscient tables, exposing only human-legal perception, progressive HUD learning, real 1–12 unit UI selection/command-card actions, and provenance-bearing memory.

### 1. Strict, human-legal perception and epistemic honesty

- **Viewport, not omniscience.** `observe_view` returns the current playable viewport and excludes HUD controls. Fog of war, terrain occlusion, cloaking and burrow detection decide what is visible; hidden state is not reconstructed.
- **Selection-gated exact values.** Exact live HP and shields, plus energy for owned units, appear only after a real selection through `current_selection` / `selected_panel`. Enemy energy is never published.
- **Fog has a timestamp.** Fogged buildings and resources carry explicit observation times and are never presented as current truth.
- **Unknown is a value.** Unknown fields remain `None`, `unknown` or `?`; they are not coerced to `0` or `false`.
- **Lawful own overview.** `own_units` lists current own entities independent of the camera. Coarse `stationary_s` is derived only from fresh observations while unpaused and unloaded, quantized to `15`, `30`, `60`, `120`, `180`, `300` or `600` seconds. It is not an engine order read or a wall-clock claim.

### 2. No preloaded encyclopedia; progressive two-second HUD learning

- **Nothing is preloaded.** There is no bundled unit, tech or ability encyclopedia. Names, visible stats, weapons, upgrade grades, skills and build options are learned from the live HUD.
- **One selection, two seconds.** A single real selection held visible for the two-second HUD dwell (`details_wait_ms`) learns the visible details. Moving the camera alone does not restart the dwell; a changed selection or visible rule set does.
- **Completed actors only.** For completed owned actors, the same dwell can learn visible command-card skill labels and mission-permitted `build_options`. Discovery reads labels; it never opens or mutates the real menu.
- **Groups share only what is shared.** A stable, homogeneous own-unit selection of 2–12 units that completes the dwell learns shared visible command-card labels. It does not reveal single-unit stats, hidden cargo or production.
- **Detector is exact text.** Only the exact HUD status text `"Detector"` persists `detector: True`; absent evidence stays unknown rather than `False`.
- **Two knowledge modes.** `official_standard` may attach an explicitly supplied, verified shared static library across official maps, while upgrade grades and mission `build_options` remain episode-isolated. `isolated_scope` keeps all learning inside the episode database and attaches no shared library.

### 3. Ticket-bound real actions, native cadence and spatial memory

- **Actions use the real UI.** Selection, command-card activation, targeting, production and cargo operations go through actual selection and the current native card. The shared action budget is at least 334 ms, up to three operations per second; automatic information does not consume game actions.
- **Tickets bind identity.** `SelectionTicket`, `ButtonTicket`, `TargetTicket`, `ProductionTicket` and `CargoTicket` bind `pid`, the 64-bit `process_creation` FILETIME, `epoch`, and the relevant generation or serial.
- **JSON stays lossless.** At the JSON boundary `scapi.wire_identity` encodes `process_creation` as an exact decimal string. UI redraw suppression preserves serials, so an unchanged card does not manufacture new permission.
- **Unknown means stop.** Unknown or unconfirmed outcomes fail closed with `outcome_unknown` and `automatic_retry: False`; the bridge never automatically resubmits. `enqueued` proves submission, not completion.
- **Preview before the click.** `placement_preview` observes a real pending placement and returns the chosen anchor, Addon footprint, power requirement/state and `engine_click_point`. `build_sites` reports current legal starts while the game is in active play. Neither call issues a command.
- **Memory keeps provenance.** Fog-aware `BuildingMemory` stores last-observed structures, and persistent `TerrainReference` stores explored terrain as `cells` or a compact 16x16px `grid` of at most 1,024 tiles. Missing cells and old sightings stay unknown or historical.

### 4. Automatic incremental information and a durable three-stage outbox

- **Push, not polling.** `AutomaticInformation` is a host-driven stream of snapshots and RFC 6901 field-pointer patches, separated from the player-facing `ReadyRuntime.handle`. Direct polling of `INFORMATION_OPS` is rejected as `information_is_automatic`.
- **Compact spatial context.** Live `screen_terrain` and `screen_terrain_creep` projections use one character per 16x16px cell, with current and last-observed creep distinguished. They are screen views, not a complete map.
- **Durable events.** `EventLog` reads a durable `Journal` of narrative, completion, nuclear and minimap events with explicit sequence and cursor semantics; overflow and gaps are surfaced, not hidden.
- **Threats versus contacts.** Off-screen minimap enemy threats are reported after a five-second contact dwell. Non-threat `offscreen_non_enemy_contact` records identify `allied`, `rescuable` or `neutral` contacts, grant no action permission and are never presented as enemy attacks.
- **Gated Tips.** `TipsRuntime` unlocks original-game Tips only from lawfully observed public names and context, and records delivery separately from the offer.
- **Three-stage delivery.** `DeliveryOutbox` moves each record through durable `enqueue` -> `receive` -> `processed` states. Effects are idempotent across crashes; a queued or sent message is never treated as player consumption.

### 5. Role-isolated, dual-scope experience notebooks

- **Claims are not facts.** Player-authored notes are revisable and marked `player_authored_not_api_verified`; API observations and authored claims never share an epistemic status.
- **Physical role isolation.** `ExperienceStore` and `execute_role_experience` maintain separate notebooks for `interaction`, `tactics` and `operator`, each with its own database and profile identity.
- **Two explicit scopes.** `episode` and `global` remain separate stores. `promote` moves a verified episode note into the configured global notebook and deletes the episode copy, while direct global records stay in that scope; `isolated_scope` disables global experience entirely.
- **Revisable operations.** The role boundary supports `record`, `revise`, `refute`, `promote`, `delete` and `list`, with revision checks and bounded pages.
- **No blind preload.** Startup imports no global memory. Recall is explicit through `list(scope='global')` (with `author_role`) or a selected read; delivery follows observed names or authored IDs, not hidden history.
- **Offline maintenance.** `tools/role_experience_cli.py` accepts one JSON request on standard input and can maintain global notes without attaching to a game; episode writes require a connected episode.

### 6. Bounded x86 bridge, lifecycle permits and verified non-destructive withdrawal

- **One target, one ABI.** The adapter targets StarCraft 1.05 x86 at `D:/Starcraft/starcraft.exe`, default **v53 / ABI 27**, with a 676-byte `ScapiState`. Changing a path string does not port the binary layout.
- **Offline, bounded build.** `tools/build_native.py` builds with an existing 32-bit x86 TinyCC, refuses to overwrite existing outputs, holds a build-directory lock and verifies the exact UTF-16LE DLL path allowlist embedded in each loader.
- **Preparation and artifacts.** `prepare_loader_runtime` performs identity checks and a suspended-process token preflight on an approved loader copy. `build_episode_candidate` creates immutable per-episode images and exact-path loaders without attaching to the game.
- **Host-gated lifecycle.** `Lifecycle` permits require explicit quiescence, bind the verified session, rate-limit menu inputs at 334 ms and apply save cooldowns. Existing human pauses and `menu_open` state remain human-owned.
- **Verified withdrawal.** `NativeCleanupAttempt` / `verify_native_cleanup` check 73 original code spans, 5 minimap-ping ABI spans, IAT restoration and zeroed native flags. Success requires `fully_withdrawn=True`; process exit or `Stage5.close()` is not a substitute.
- **Cursor recovery and portability.** A single-owner waiter watches the exact game process and attempts one `SPI_SETCURSORS` reset after game exit. The Python side uses only the standard library on 64-bit Python 3.12, with no model or UI lock-in.

### Scope and verification

- **Alpha, API-only and StarCraft 1.05-only.** This repository ships API, native bridge source and host utilities, not a complete client or playable application; the supported target is StarCraft 1.05 x86.
- **Offline checks only.** This checkout verifies source dependencies, imports and offline compilation. Live attach, real game actions, complete stop and cross-mission continuation are not established here.
- **Default and diagnostic.** Only **v53 / ABI 27** is the default. The optional **v50 / ABI 28** visible-phase diagnostic is not a drop-in replacement and does not establish general victory detection.
- **Licensing.** Project-authored code is AGPL-3.0-only; third-party material keeps its original terms. See [NOTICE.md](../NOTICE.md). No StarCraft game, asset or trademark rights are granted.
- **Start here.** [README](../README.md) · [API integration](API.md) · [AI integration guide](AI_INTEGRATION_GUIDE.md) · [API reference](API_REFERENCE.md)

<a name="simplified-chinese"></a>

## 简体中文

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

与 **BWAPI** 不同——BWAPI 面向脚本/竞赛 Bot，直接提供引擎级单位对象（可绕过 UI 选中与 12 单位上限直接下达指令）、开局内置的完整单位/科技静态百科，以及无需选中即可读取的精确数值与内部指令状态——本 API 的核心设计目标是**严格对齐人类玩家的感知与操作边界**。尽管底层 `native/` 桥接需要读取进程内存来采样画面与 HUD，上层公开的 `scapi/` 接口会有意屏蔽引擎内部指针、指令队列与全知数据表，仅开放人类合法感知、渐进式 HUD 面板学习、受真实 UI 选中与命令卡约束的操作，以及带出处的记忆。

### 1. 严格的人类合法感知与认知诚实

- **只看视野，不做全知。** `observe_view` 只返回当前可游玩视野并排除 HUD 控件；战争迷雾、地形遮挡、隐形与潜地检测决定可见内容，隐藏状态不会被重建。
- **精确值必须来自真实选择。** 精确的实时生命与护盾，以及己方单位的能量，只在通过 `current_selection` / `selected_panel` 完成真实选择后出现；敌方能量永远不会公开。
- **迷雾信息带时间戳。** 迷雾中的建筑与资源带有明确的观察时间，不会被当作当前事实。
- **未知就是未知。** 未知字段保持 `None`、`unknown` 或 `?`，不会强制变成 `0` 或 `false`。
- **合法的己方总览。** `own_units` 与镜头无关地列出现有己方实体；粗略的 `stationary_s` 仅由未暂停、未装载状态下的新鲜观察推导，量化为 `15`、`30`、`60`、`120`、`180`、`300` 或 `600` 秒。它不是引擎命令读取，也不代表按实际时间累计的结论。

### 2. 不预载百科：两秒 HUD 渐进学习

- **不预载任何百科。** 仓库不附带单位、科技或技能数据库；名称、可见属性、武器、升级等级、技能与建造选项都从实时 HUD 学习。
- **一次选择，两秒停留。** 单个真实选择连续可见达到两秒 HUD 停留（`details_wait_ms`）后学习可见细节。只移动镜头不会重新计时；选择或可见规则集变化才会。
- **仅限已完成的自有单位。** 对已完成的自有单位，同一停留还可学习可见命令卡技能标签和本任务允许的 `build_options`。发现过程只读取标签，不会打开或修改真实菜单。
- **编组只共享可见的共享信息。** 2–12 个同类型己方单位组成的稳定选择完成停留后，只学习共享的可见命令卡标签；不会泄露单兵属性、隐藏货物或生产信息。
- **Detector 必须精确匹配。** 只有 HUD 状态文本精确为 `"Detector"` 时才持久化为 `detector: True`；没有证据时保持未知，而不是 `False`。
- **两种知识模式。** `official_standard` 可挂载调用方显式提供、已核验的共享静态库，用于跨官方地图复用，但升级等级和任务 `build_options` 仍按对局隔离；`isolated_scope` 把所有学习限制在对局数据库内，不挂载共享库。

### 3. 票据绑定的真实操作、原生节奏与空间记忆

- **操作走真实 UI。** 选择、命令卡激活、指定目标、生产与货物操作都经过真实选择和当前原生命令卡。共享操作预算至少为 334 ms，每秒最多三次；自动信息不消耗游戏操作。
- **票据绑定身份。** `SelectionTicket`、`ButtonTicket`、`TargetTicket`、`ProductionTicket` 和 `CargoTicket` 绑定 `pid`、64 位 `process_creation` FILETIME、`epoch`，以及对应的 generation 或 serial。
- **JSON 保持无损。** 在 JSON 边界，`scapi.wire_identity` 把 `process_creation` 编码为精确十进制字符串。UI 重绘抑制期间保留 serial，未变化的命令卡不会凭空产生新权限。
- **未知即停止。** 未知或未确认的结果会失败即拒绝，返回 `outcome_unknown` 与 `automatic_retry: False`；桥接不会自动重发。`enqueued` 只证明已提交，不证明已完成。
- **先预览，再点击。** `placement_preview` 观察真实的待放置状态，返回所选锚点、Addon 占地、供电需求/状态和 `engine_click_point`；`build_sites` 在游戏处于活动游玩状态时报告当前合法建造起点。两者都不会发出命令。
- **记忆保留出处。** 感知迷雾的 `BuildingMemory` 保存最后观察到的建筑；持久化的 `TerrainReference` 以 `cells` 或最多 1,024 格的紧凑 16x16px `grid` 保存已探索地形。缺失格子和旧观察保持未知/历史状态。

### 4. 自动增量信息流与持久三段式发件箱

- **推送而非轮询。** `AutomaticInformation` 是由宿主驱动的快照与 RFC 6901 字段指针补丁流，与面向玩家的 `ReadyRuntime.handle` 分离；直接轮询 `INFORMATION_OPS` 会以 `information_is_automatic` 拒绝。
- **紧凑空间上下文。** 实时 `screen_terrain` 与 `screen_terrain_creep` 投影按每个 16x16px 单元一个字符输出，并区分当前菌毯与最后观察到的菌毯；它是屏幕视图，不是完整地图。
- **持久事件。** `EventLog` 读取持久化的 `Journal`，其中记录叙事、完成、核弹与小地图事件，并保留明确的序列和游标语义；溢出与缺口会显式暴露，不会隐藏。
- **威胁与接触分开。** 屏幕外小地图敌方威胁在五秒接触停留后报告。非威胁的 `offscreen_non_enemy_contact` 记录 `allied`、`rescuable` 或 `neutral` 接触，不授予操作权限，也绝不会被表述为敌方攻击。
- **受控 Tips。** `TipsRuntime` 只根据合法观察到的公开名称与上下文解锁原版 Tips，并把“提供”与“已交付”分开记录。
- **三段式交付。** `DeliveryOutbox` 让每条记录依次经过持久化的 `enqueue` -> `receive` -> `processed` 三态；崩溃后效果可幂等重放，已排队或已发送的消息绝不等于玩家已消费。

### 5. 角色隔离的双作用域经验笔记

- **主张不是事实。** 玩家自述可修订，并标记为 `player_authored_not_api_verified`；API 观察与玩家主张不会混用同一认知状态。
- **物理角色隔离。** `ExperienceStore` 与 `execute_role_experience` 为 `interaction`、`tactics` 和 `operator` 维护彼此独立的笔记，各自拥有数据库和配置身份。
- **两个显式作用域。** `episode` 与 `global` 保持为独立存储。`promote` 把已验证的对局笔记移入已配置的全局笔记并删除对局副本，直接写入的全局记录仍保留在该作用域；`isolated_scope` 完全禁用全局经验。
- **可修订操作。** 角色边界支持 `record`、`revise`、`refute`、`promote`、`delete` 与 `list`，并带修订检查和有界分页。
- **不盲目预载。** 启动时不导入任何全局记忆。召回必须通过显式的 `list(scope='global')`（带 `author_role`）或选定的读取；交付依据观察到的名称或作者提供的 ID，而不是隐藏历史。
- **离线维护。** `tools/role_experience_cli.py` 从标准输入接收一个 JSON 请求，可在不连接游戏的情况下维护全局笔记；对局写入需要已连接的对局。

### 6. 有界 x86 桥接、生命周期许可与经核验的非破坏性撤离

- **单一目标，单一 ABI。** 适配器面向 `D:/Starcraft/starcraft.exe` 的 StarCraft 1.05 x86，默认 **v53 / ABI 27**，`ScapiState` 为 676 字节。只改路径字符串不能移植二进制布局。
- **离线、有界构建。** `tools/build_native.py` 使用现有的 32 位 x86 TinyCC 构建，拒绝覆盖已有输出，持有构建目录锁，并校验每个 loader 中嵌入的精确 UTF-16LE DLL 路径白名单。
- **准备与产物。** `prepare_loader_runtime` 对已批准的 loader 副本执行身份检查与挂起进程令牌预检。`build_episode_candidate` 生成不可变的逐对局镜像与精确路径 loader，全程不连接游戏。
- **宿主门控的生命周期。** `Lifecycle` 许可要求显式的静默证明、绑定已核验会话、把菜单输入限制在 334 ms 节奏并执行存档冷却。既有人类暂停与 `menu_open` 状态始终归人类所有。
- **经核验的撤离。** `NativeCleanupAttempt` / `verify_native_cleanup` 核验 73 段原始代码、5 段小地图 ping ABI、IAT 还原与原生标志清零；成功必须满足 `fully_withdrawn=True`。进程退出或 `Stage5.close()` 不能替代这些检查。
- **光标恢复与可移植性。** 单一所有者等待器监视精确的游戏进程，并在游戏退出后尝试一次 `SPI_SETCURSORS` 重置。Python 侧仅使用 64 位 Python 3.12 标准库，不绑定特定模型或 UI。

### 范围与验证

- **Alpha，且仅提供 API、仅适用于 StarCraft 1.05。** 本仓库提供 API、原生桥接源码与宿主工具，不包含完整客户端或可游玩应用；受支持目标是 StarCraft 1.05 x86。
- **仅离线检查。** 当前检出仅验证源码依赖、导入与离线编译；实机连接、真实游戏操作、完整停止与跨关续接尚未在此建立。
- **默认与诊断。** 只有 **v53 / ABI 27** 是默认版本。可选的 **v50 / ABI 28** 可见阶段诊断不是可直接替换的集成方案，也不代表已建立通用胜利检测。
- **许可。** 项目自有代码采用 AGPL-3.0-only；第三方材料保留其原始条款，见 [NOTICE.md](../NOTICE.md)。本许可不授予 StarCraft 游戏、素材或商标权利。
- **从这里开始。** [README](../README.md) · [API 集成](API.md) · [AI 接入指南](AI_INTEGRATION_GUIDE.md) · [API 参考](API_REFERENCE.md)

<a name="traditional-chinese"></a>

## 繁體中文

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

與 **BWAPI** 不同——BWAPI 面向腳本/競賽 Bot，直接提供引擎級單位物件（可繞過 UI 選取與 12 單位上限直接下達指令）、開局內建的完整單位/科技靜態百科，以及無需選取即可讀取的精確數值與內部指令狀態——本 API 的核心設計目標是**嚴格對齊人類玩家的感知與操作邊界**。儘管底層 `native/` 橋接需要讀取處理程序記憶體來取樣畫面與 HUD，上層公開的 `scapi/` 介面會有意屏蔽引擎內部指標、指令佇列與全知資料表，僅開放人類合法感知、漸進式 HUD 面板學習、受真實 UI 選取與命令卡約束的操作，以及帶出處的記憶。

### 1. 嚴格的人類合法感知與認知誠實

- **只看視野，不做全知。** `observe_view` 只傳回目前可遊玩視野並排除 HUD 控制項；戰爭迷霧、地形遮擋、隱形與遁地偵測決定可見內容，隱藏狀態不會被重建。
- **精確值必須來自真實選擇。** 精確的即時生命與護盾，以及己方單位的能量，只在透過 `current_selection` / `selected_panel` 完成真實選擇後出現；敵方能量永遠不會公開。
- **迷霧資訊附時間戳記。** 迷霧中的建築與資源帶有明確的觀察時間，不會被當成目前事實。
- **未知就是未知。** 未知欄位維持 `None`、`unknown` 或 `?`，不會強制變成 `0` 或 `false`。
- **合法的己方總覽。** `own_units` 與鏡頭無關地列出現有己方實體；粗略的 `stationary_s` 僅由未暫停、未裝載狀態下的新鮮觀察推導，量化為 `15`、`30`、`60`、`120`、`180`、`300` 或 `600` 秒。它不是引擎命令讀取，也不代表按實際時間累計的結論。

### 2. 不預載百科：兩秒 HUD 漸進學習

- **不預載任何百科。** 儲存庫不附帶單位、科技或技能資料庫；名稱、可見屬性、武器、升級等級、技能與建造選項都從即時 HUD 學習。
- **一次選擇，兩秒停留。** 單一真實選擇連續可見達到兩秒 HUD 停留（`details_wait_ms`）後學習可見細節。只移動鏡頭不會重新計時；選擇或可見規則集改變才會。
- **僅限已完成的自有單位。** 對已完成的自有單位，同一停留還可學習可見命令卡技能標籤與本任務允許的 `build_options`。發現過程只讀取標籤，不會開啟或修改真實選單。
- **編組只共享可見的共享資訊。** 2–12 個同類型己方單位組成的穩定選擇完成停留後，只學習共享的可見命令卡標籤；不會洩漏單兵屬性、隱藏貨物或生產資訊。
- **Detector 必須精確符合。** 只有 HUD 狀態文字精確為 `"Detector"` 時才持久化為 `detector: True`；沒有證據時保持未知，而不是 `False`。
- **兩種知識模式。** `official_standard` 可掛載呼叫端明確提供、已核驗的共享靜態庫，用於跨官方地圖重用，但升級等級和任務 `build_options` 仍按對局隔離；`isolated_scope` 把所有學習限制在對局資料庫內，不掛載共享庫。

### 3. 票據綁定的真實操作、原生節奏與空間記憶

- **操作走真實 UI。** 選擇、命令卡啟用、指定目標、生產與貨物操作都經過真實選擇和目前原生命令卡。共享操作預算至少為 334 ms，每秒最多三次；自動資訊不消耗遊戲操作。
- **票據綁定身分。** `SelectionTicket`、`ButtonTicket`、`TargetTicket`、`ProductionTicket` 和 `CargoTicket` 綁定 `pid`、64 位元 `process_creation` FILETIME、`epoch`，以及對應的 generation 或 serial。
- **JSON 保持無損。** 在 JSON 邊界，`scapi.wire_identity` 把 `process_creation` 編碼為精確十進位字串。UI 重繪抑制期間保留 serial，未變化的命令卡不會憑空產生新權限。
- **未知即停止。** 未知或未確認的結果會失敗即拒絕，傳回 `outcome_unknown` 與 `automatic_retry: False`；橋接不會自動重送。`enqueued` 只證明已提交，不證明已完成。
- **先預覽，再點擊。** `placement_preview` 觀察真實的待放置狀態，傳回所選錨點、Addon 佔地、供電需求/狀態和 `engine_click_point`；`build_sites` 在遊戲處於活動遊玩狀態時報告目前合法建造起點。兩者都不會發出命令。
- **記憶保留出處。** 感知迷霧的 `BuildingMemory` 儲存最後觀察到的建築；持久化的 `TerrainReference` 以 `cells` 或最多 1,024 格的緊湊 16x16px `grid` 儲存已探索地形。缺失格子和舊觀察保持未知/歷史狀態。

### 4. 自動增量資訊流與持久三段式寄件匣

- **推送而非輪詢。** `AutomaticInformation` 是由宿主驅動的快照與 RFC 6901 欄位指標補丁流，與面向玩家的 `ReadyRuntime.handle` 分離；直接輪詢 `INFORMATION_OPS` 會以 `information_is_automatic` 拒絕。
- **緊湊空間脈絡。** 即時 `screen_terrain` 與 `screen_terrain_creep` 投影按每個 16x16px 單元一個字元輸出，並區分目前菌毯與最後觀察到的菌毯；它是螢幕視圖，不是完整地圖。
- **持久事件。** `EventLog` 讀取持久化的 `Journal`，其中記錄敘事、完成、核彈與小地圖事件，並保留明確的序列和游標語意；溢位與缺口會明確暴露，不會隱藏。
- **威脅與接觸分開。** 螢幕外小地圖敵方威脅在五秒接觸停留後報告。非威脅的 `offscreen_non_enemy_contact` 記錄 `allied`、`rescuable` 或 `neutral` 接觸，不授予操作權限，也絕不會被表述為敵方攻擊。
- **受控 Tips。** `TipsRuntime` 只根據合法觀察到的公開名稱與脈絡解鎖原版 Tips，並把「提供」與「已交付」分開記錄。
- **三段式交付。** `DeliveryOutbox` 讓每條記錄依次經過持久化的 `enqueue` -> `receive` -> `processed` 三態；崩潰後效果可冪等重放，已排隊或已傳送的訊息絕不等於玩家已消費。

### 5. 角色隔離的雙作用域經驗筆記

- **主張不是事實。** 玩家自述可修訂，並標記為 `player_authored_not_api_verified`；API 觀察與玩家主張不會混用同一認知狀態。
- **實體角色隔離。** `ExperienceStore` 與 `execute_role_experience` 為 `interaction`、`tactics` 和 `operator` 維護彼此獨立的筆記，各自擁有資料庫和設定身分。
- **兩個明確作用域。** `episode` 與 `global` 保持為獨立儲存。`promote` 把已驗證的對局筆記移入已設定的全域筆記並刪除對局副本，直接寫入的全域記錄仍保留在該作用域；`isolated_scope` 完全停用全域經驗。
- **可修訂操作。** 角色邊界支援 `record`、`revise`、`refute`、`promote`、`delete` 與 `list`，並帶修訂檢查和有界分頁。
- **不盲目預載。** 啟動時不匯入任何全域記憶。召回必須透過明確的 `list(scope='global')`（帶 `author_role`）或選定的讀取；交付依據觀察到的名稱或作者提供的 ID，而不是隱藏歷史。
- **離線維護。** `tools/role_experience_cli.py` 從標準輸入接收一個 JSON 請求，可在不連線遊戲的情況下維護全域筆記；對局寫入需要已連線的對局。

### 6. 有界 x86 橋接、生命週期許可與經核驗的非破壞性撤離

- **單一目標，單一 ABI。** 適配器面向 `D:/Starcraft/starcraft.exe` 的 StarCraft 1.05 x86，預設 **v53 / ABI 27**，`ScapiState` 為 676 位元組。只改路徑字串不能移植二進位配置。
- **離線、有界建置。** `tools/build_native.py` 使用現有的 32 位元 x86 TinyCC 建置，拒絕覆寫既有輸出，持有建置目錄鎖，並驗證每個 loader 中嵌入的精確 UTF-16LE DLL 路徑白名單。
- **準備與產物。** `prepare_loader_runtime` 對已批准的 loader 副本執行身分檢查與暫停處理程序權杖預檢。`build_episode_candidate` 產生不可變的逐對局映像與精確路徑 loader，全程不連線遊戲。
- **宿主閘控的生命週期。** `Lifecycle` 許可要求明確的靜止證明、綁定已核驗工作階段、把選單輸入限制在 334 ms 節奏並執行存檔冷卻。既有人類暫停與 `menu_open` 狀態始終歸人類所有。
- **經核驗的撤離。** `NativeCleanupAttempt` / `verify_native_cleanup` 核驗 73 段原始程式碼、5 段小地圖 ping ABI、IAT 還原與原生旗標歸零；成功必須滿足 `fully_withdrawn=True`。處理程序結束或 `Stage5.close()` 不能替代這些檢查。
- **游標恢復與可攜性。** 單一擁有者等待器監視精確的遊戲處理程序，並在遊戲結束後嘗試一次 `SPI_SETCURSORS` 重設。Python 側僅使用 64 位元 Python 3.12 標準函式庫，不綁定特定模型或 UI。

### 範圍與驗證

- **Alpha，且僅提供 API、僅適用於 StarCraft 1.05。** 本儲存庫提供 API、原生橋接原始碼與宿主工具，不包含完整用戶端或可遊玩應用程式；支援目標是 StarCraft 1.05 x86。
- **僅離線檢查。** 目前檢出僅驗證原始碼相依項目、匯入與離線編譯；實機連線、真實遊戲操作、完整停止與跨關續接尚未在此建立。
- **預設與診斷。** 只有 **v53 / ABI 27** 是預設版本。可選的 **v50 / ABI 28** 可見階段診斷不是可直接替換的串接方案，也不代表已建立通用勝利偵測。
- **授權。** 專案自有程式碼採用 AGPL-3.0-only；第三方材料保留其原始條款，見 [NOTICE.md](../NOTICE.md)。本授權不授予 StarCraft 遊戲、素材或商標權利。
- **從這裡開始。** [README](../README.md) · [API 串接](API.md) · [AI 串接指南](AI_INTEGRATION_GUIDE.md) · [API 參考](API_REFERENCE.md)

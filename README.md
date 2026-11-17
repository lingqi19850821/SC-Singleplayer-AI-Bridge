# SC Singleplayer AI Bridge

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

> **Alpha — API only.** **Compatible with StarCraft 1.05 only.** This API is under active development. Its interfaces and behavior may change, and it may contain bugs. This repository provides the API and native bridge source only; no client or complete application is included. You must provide your own integration and host application.
>
> **Alpha — 仅提供 API。** **仅适用于 StarCraft 1.05 版本。** 本 API 仍处于开发阶段，接口和行为可能变动，也可能存在错误。本仓库仅提供 API 与原生桥接源码，不包含客户端或完整应用；你需要自行完成接口接入并提供宿主应用。
>
> **Alpha — 僅提供 API。** **僅適用於 StarCraft 1.05 版本。** 本 API 仍處於開發階段，介面與行為可能變動，也可能存在錯誤。本儲存庫僅提供 API 與原生橋接原始碼，不包含用戶端或完整應用程式；你需要自行完成介面串接並提供宿主應用程式。

<a name="english"></a>

## English

A restricted Python API and native x86 bridge for StarCraft single-player games. The API provides observations, selection, commands, construction, production, events, knowledge storage, automatic information delivery and lifecycle interfaces, with native build and cleanup helpers. The default native version is **v49 / ABI 27**.

Desktop UI, voice, model sessions, role orchestration and a complete application host are not included. The repository does not bundle the game, saves, models, a compiler, account configuration, runtime records or test files.

### Let an AI help you integrate it

If you are unfamiliar with setup, ask your AI assistant to read **[AI_INTEGRATION_GUIDE.md](docs/AI_INTEGRATION_GUIDE.md)** first. This English-language guide explains environment preparation, building, host integration, API calls, automatic information delivery and shutdown. It identifies the host functionality the AI must help you implement or connect; compiling a DLL alone does not create a playable application.

### Requirements and build

- Windows with **64-bit Python 3.12**. The Python API uses the standard library; Qt, Node.js, the Codex SDK and Capstone are not required.
- An existing **32-bit x86 TinyCC**, including its accompanying headers and libraries. This repository does not install or distribute the compiler.
- The current adapter targets a specific **StarCraft 1.05 x86** layout, with the game path allowlisted as `D:/Starcraft/starcraft.exe`. Other patches or arbitrary installation paths are not supported by changing a path string alone.

Run from the repository root. Replace `<ABSOLUTE_PATH_TO_32_BIT_TCC>` with your compiler's actual absolute path:

```powershell
python tools/build_native.py --tcc "<ABSOLUTE_PATH_TO_32_BIT_TCC>"
```

The outputs are `build/stage5/scapi_stage5_v49_minimap_ping.dll` and `build/stage5/stage5_loader_v49_minimap_ping.exe`. The build does not run the loader, inspect game processes or inject into the game. Existing outputs are not overwritten. Binaries and runtime directories are excluded from Git.

`--visible-phase-diagnostic` builds the optional **v50 / ABI 28** diagnostic candidate; it does not change the default v49 or establish that end-of-game detection has been verified in a live game. The current native cleanup verifier supports v49, so v50 is not a drop-in integration replacement. `--output-dir` supports isolated compilation; the default host path checks still expect the standard output locations above.

### API entrypoints

```python
from scapi import Stage5, SelectionTicket, ButtonTicket, TargetTicket
from scapi.events import EventLog
from scapi.lifecycle import Lifecycle
from scapi.player_runtime import ReadyRuntime
from scapi.automatic_information import AutomaticInformation, DeliveryOutbox
```

Importing modules does not connect to the game. Constructing `Stage5(pid, ...)` connects to a bridge already installed and verified by a trusted host, may create local knowledge databases, and starts the existing waiter that resets the system cursor after the game exits. The visible cursor recovery has not been verified from this checkout. Object construction is not a side-effect-free check.

- [Host integration and responsibilities](docs/API.md)
- [Python signatures and field references](docs/API_REFERENCE.md)
- [Third-party sources and licenses](NOTICE.md)

Current validation covers source dependencies, imports and offline compilation. It does not establish live game takeover, actions, complete stop or cross-mission continuation from this standalone repository.

### License

Project-authored code uses [GNU AGPL-3.0-only](LICENSE). Third-party material retains its copyright, original license and applicable conditions; see [NOTICE.md](NOTICE.md). This license grants no rights to the StarCraft game, assets or trademarks.

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

---

<a name="simplified-chinese"></a>

## 简体中文

用于 StarCraft 单人游戏的受限 Python API 与原生 x86 桥接源码。API 提供观察、选择、命令、建造、生产、事件、知识存储、自动信息交付和生命周期接口，以及原生构建与安全收尾辅助。当前默认原生版本为 **v49 / ABI 27**。

本仓库不包含桌面界面、语音、模型会话、角色编排或完整应用宿主，也不打包游戏、存档、模型、编译器、账户配置、运行记录或测试文件。

### 让 AI 帮你接入

如果你不熟悉配置和开发，可以让 AI 助手先阅读 **[AI_INTEGRATION_GUIDE.md](docs/AI_INTEGRATION_GUIDE.md)**。这份英文指南说明了环境准备、构建、宿主接入、API 调用、自动信息交付和停止收尾流程，并明确哪些宿主能力需要 AI 帮你实现或接入；仅编译出 DLL 并不等于已有可游玩的应用。

### 环境与构建

- Windows 与 **64 位 Python 3.12**。Python API 使用标准库，不需要 Qt、Node.js、Codex SDK 或 Capstone。
- 现有的 **32 位 x86 TinyCC**，保留它自身配套的头文件和库。仓库不安装或分发编译器。
- 当前适配特定 **StarCraft 1.05 x86** 布局，游戏路径白名单为 `D:/Starcraft/starcraft.exe`。只修改路径字符串，并不能支持其他补丁或任意安装路径。

在仓库根目录运行以下命令，将 `<ABSOLUTE_PATH_TO_32_BIT_TCC>` 替换为编译器的实际绝对路径：

```powershell
python tools/build_native.py --tcc "<ABSOLUTE_PATH_TO_32_BIT_TCC>"
```

输出为 `build/stage5/scapi_stage5_v49_minimap_ping.dll` 与 `build/stage5/stage5_loader_v49_minimap_ping.exe`。构建不会运行 loader、检查游戏进程或注入游戏，也不会覆盖已有同名输出。二进制和运行目录已从 Git 中排除。

`--visible-phase-diagnostic` 仅构建可选的 **v50 / ABI 28** 诊断候选；它不改变默认 v49，也不代表终局检测已获真实游戏验收。当前原生清理验证器支持 v49，因此 v50 不能直接替换完整接入流程。`--output-dir` 可用于隔离编译；宿主的默认路径校验仍绑定上述标准输出位置。

### API 入口

```python
from scapi import Stage5, SelectionTicket, ButtonTicket, TargetTicket
from scapi.events import EventLog
from scapi.lifecycle import Lifecycle
from scapi.player_runtime import ReadyRuntime
from scapi.automatic_information import AutomaticInformation, DeliveryOutbox
```

导入模块不会连接游戏。构造 `Stage5(pid, ...)` 会连接已经由可信宿主安装并核验的桥接，可能建立本地知识数据库，并启动既有的游戏退出后系统鼠标指针恢复等待器；恢复的可见效果尚未经本仓库现场验证。不能把对象构造当成无副作用检查。

- [接入方式和宿主责任](docs/API.md)
- [Python 接口签名与字段入口](docs/API_REFERENCE.md)
- [第三方来源与许可](NOTICE.md)

当前检查范围为源码依赖、导入和离线编译。这不证明此独立仓库中的实际游戏接管、操作、完整停止或跨关流程已经通过验证。

### 许可

本项目自有代码采用 [GNU AGPL-3.0-only](LICENSE)。第三方材料保留其版权、原许可和适用条件，见 [NOTICE.md](NOTICE.md)。本许可证不授予 StarCraft 游戏、素材或商标的权利。

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

---

<a name="traditional-chinese"></a>

## 繁體中文

用於 StarCraft 單人遊戲的受限 Python API 與原生 x86 橋接原始碼。API 提供觀察、選取、命令、建造、生產、事件、知識儲存、自動資訊交付與生命週期介面，以及原生建置與安全清理輔助。當前預設原生版本為 **v49 / ABI 27**。

本儲存庫不包含桌面介面、語音、模型工作階段、角色協調或完整應用程式宿主，也不隨附遊戲、存檔、模型、編譯器、帳戶設定、執行紀錄或測試檔案。

### 讓 AI 幫你串接

如果你不熟悉設定與開發，可以請 AI 助手先閱讀 **[AI_INTEGRATION_GUIDE.md](docs/AI_INTEGRATION_GUIDE.md)**。這份英文指南說明了環境準備、建置、宿主串接、API 呼叫、自動資訊交付與停止清理流程，並明確指出哪些宿主能力需要 AI 幫你實作或串接；僅編譯出 DLL 並不等於已有可遊玩的應用程式。

### 環境與建置

- Windows 與 **64 位元 Python 3.12**。Python API 使用標準函式庫，不需要 Qt、Node.js、Codex SDK 或 Capstone。
- 現有的 **32 位元 x86 TinyCC**，保留其配套的標頭檔與函式庫。本儲存庫不安裝或散布編譯器。
- 目前適配特定 **StarCraft 1.05 x86** 記憶體配置，遊戲路徑白名單為 `D:/Starcraft/starcraft.exe`。僅修改路徑字串，並不能支援其他更新版本或任意安裝路徑。

在儲存庫根目錄執行以下命令，將 `<ABSOLUTE_PATH_TO_32_BIT_TCC>` 替換為編譯器的實際絕對路徑：

```powershell
python tools/build_native.py --tcc "<ABSOLUTE_PATH_TO_32_BIT_TCC>"
```

輸出為 `build/stage5/scapi_stage5_v49_minimap_ping.dll` 與 `build/stage5/stage5_loader_v49_minimap_ping.exe`。建置不會執行 loader、檢查遊戲處理程序或注入遊戲，也不會覆寫既有的同名輸出。二進位檔案與執行目錄已從 Git 中排除。

`--visible-phase-diagnostic` 僅建置可選的 **v50 / ABI 28** 診斷候選；它不改變預設 v49，也不代表終局偵測已通過實際遊戲驗收。現有原生清理驗證器支援 v49，因此 v50 不能直接替換完整串接流程。`--output-dir` 可用於隔離編譯；宿主的預設路徑驗證仍綁定上述標準輸出位置。

### API 入口

```python
from scapi import Stage5, SelectionTicket, ButtonTicket, TargetTicket
from scapi.events import EventLog
from scapi.lifecycle import Lifecycle
from scapi.player_runtime import ReadyRuntime
from scapi.automatic_information import AutomaticInformation, DeliveryOutbox
```

匯入模組不會連線至遊戲。建構 `Stage5(pid, ...)` 會連線至已由可信宿主安裝並驗證的橋接，可能建立本機知識資料庫，並啟動既有的遊戲結束後系統滑鼠游標還原等待器；還原的可見效果尚未經本儲存庫實際驗證。不能將物件建構視為沒有副作用的檢查。

- [串接方式與宿主責任](docs/API.md)
- [Python 介面簽章與欄位參考](docs/API_REFERENCE.md)
- [第三方來源與授權](NOTICE.md)

目前檢查範圍為原始碼相依項目、匯入與離線編譯。這不代表此獨立儲存庫中的實際遊戲接管、操作、完整停止或跨關流程已通過驗證。

### 授權

本專案自行撰寫的程式碼採用 [GNU AGPL-3.0-only](LICENSE)。第三方材料保留其著作權、原授權與適用條件，見 [NOTICE.md](NOTICE.md)。本授權不授予 StarCraft 遊戲、素材或商標的權利。

[English](#english) · [简体中文](#simplified-chinese) · [繁體中文](#traditional-chinese)

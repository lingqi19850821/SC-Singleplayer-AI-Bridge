# SC Singleplayer AI Bridge

用于 StarCraft 单人游戏的受限 Python API 与原生 x86 桥接源码。

本仓库提供观察、选择、命令、建造、生产、事件、知识存储、自动信息和生命周期接口，以及原生构建与安全收尾辅助。当前默认原生版本为 v49 / ABI 27。

这是供开发者嵌入的 API 源码库。桌面界面、语音、模型会话、角色编排和完整应用宿主不在本仓库内；调用方需要实现可信宿主。没有打包游戏、存档、模型、编译器、账户配置、运行记录或测试文件。

## 环境与构建

- Windows；Python 3.12。Python API 使用标准库，不需要 Qt、Node.js、Codex SDK 或 Capstone。
- 现有的 **32 位 x86 TinyCC**，保留它自身配套的头文件和库。仓库不安装或分发编译器。
- 当前适配的是特定 StarCraft 1.05 x86 布局，游戏路径白名单为 `D:/Starcraft/starcraft.exe`。不能将此源码视为对其他补丁或任意安装路径的支持。

在仓库根目录运行以下构建命令。`<32位TCC的绝对路径>` 是需要替换的参数：

```powershell
python tools/build_native.py --tcc "<32位TCC的绝对路径>"
```

输出为 `build/stage5/scapi_stage5_v49_minimap_ping.dll` 与 `build/stage5/stage5_loader_v49_minimap_ping.exe`。构建不会运行 loader、检查游戏进程或注入游戏；已有同名输出会被拒绝覆盖。二进制和运行目录已从 Git 中排除。

可选参数 `--visible-phase-diagnostic` 仅构建已有 v50 / ABI 28 诊断候选；它不改变默认 v49，不代表终局检测已获真实游戏验收。`--output-dir` 可用于隔离编译；可信宿主的默认路径校验仍绑定上述标准输出位置。

## 接口

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

目前交付的是源码提取候选，检查范围为依赖、导入和离线编译。没有据此验证新仓库中的实际游戏接管、操作、停止或跨关流程。

## 许可

本项目自有代码采用 [GNU AGPL-3.0-only](LICENSE)。第三方材料保留其版权、原许可和适用条件，见 [NOTICE.md](NOTICE.md)。本项目许可证不授予 StarCraft 游戏、素材或商标的权利。

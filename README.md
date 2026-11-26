# KW45 CAN ISP 与 Secure RAM 下载工具

这个仓库用于把已链接到 Secure SRAM 的 KW45 应用下载到 RAM 并启动，也可扫描 CAN 适配器、监听 ROM ISP 的 MBOOT 报文。示例应用是 `kw45b41zevk_freertos_hello`，由 MCUXpresso for VS Code 生成 BIN 和 ELF。

## 快速开始

在本仓库根目录打开 Windows PowerShell，将示例路径换成当前构建产物的位置。

```powershell
python .\parse-bin.py "D:/NXP/work-prj/kw45b41zevk_freertos_hello/freertos_hello.bin"
```

- **ROM ISP**：让目标进入 ISP，按接线选用脚本输出的 BusPal CAN、PCAN 或 UART 命令。先执行 `write-memory`，成功后执行 `execute`。
- **J-Link**：连接 SWD，执行脚本打印的命令。上面的示例会在当前目录生成 `freertos_hello.jlink`。

```powershell
JLink.exe .\freertos_hello.jlink
```

每次重新构建后都用新 BIN 生成命令。初始 MSP 和入口来自 BIN 向量表，加载地址由链接布局决定。

## 脚本与文件

| 文件 | 用途和用法 |
| :--- | :--- |
| [parse-bin.py](parse-bin.py) | 输入应用 BIN，解析 MSP 和入口，打印三组 ISP 命令，为本工程的 Secure RAM 镜像生成同名 `.jlink` 并打印运行命令。可选 `--addr` 用于明确指定加载地址，`--uart-port` 用于选择 UART COM 口和波特率。 |
| [scan-can.py](scan-can.py) | 扫描 CAN 适配器和通道，提供 PCAN 通道的诊断信息。用 `-i pcan` 只扫 PCAN，用 `--all` 扩展到全部后端。 |
| [can-isp.py](can-isp.py) | 通过 PCAN 接收 Classic CAN 报文，重组 MBOOT 包并用 SPSDK 解码。通道、位速率和过滤 ID 在脚本顶部配置，用 Ctrl+C 停止。 |
| [run_ram.jlink](run_ram.jlink) | J-Link Commander 的手写示例，使用固定的镜像路径和启动地址。新镜像可直接用 `parse-bin.py` 生成命令文件。 |
| [kw45_ram_jlink.py](kw45_ram_jlink.py) | 供 `parse-bin.py` 和 `prepare-ram-downloads.py` 调用的 J-Link 命令文件生成模块。 |
| [prepare-ram-downloads.py](prepare-ram-downloads.py) | 可指定输出目录生成 J-Link 命令文件。提供签名私钥、证书块和 SB3KDK 文件路径后，还会生成 SB3.1 的 RAM 下载配置，供 `nxpimage` 导出容器。 |
| `freertos_hello.jlink` | 由当前 BIN 生成的命令文件，包含该镜像的路径和向量值。重新构建后用新 BIN 再生成。 |
| [task.md](task.md) | 项目任务记录。 |
| [.gitignore](.gitignore) | Git 忽略规则。 |

## CAN 诊断

```powershell
python .\scan-can.py -i pcan
python .\can-isp.py
```

监听时从另一个终端执行 ISP 命令。原生 CAN 和普通 UART 使用 SPSDK 3.1.0 的 `blhost`，BusPal CAN 经 COM 口桥接使用 C++ `blhost.exe` 2.6.7。CAN 适配器需要相应驱动，Python 环境需要 `python-can`。 [CAN 设备与 SPSDK 使用手册](CAN_Scanner_and_SPSDK_Devices_Guide.md)

## 详细文档

| 要做的事 | 文档 |
| :--- | :--- |
| 将 Flash 应用改成 Secure RAM 应用，并在 MCUXpresso for VS Code 构建 | [KW45_FreeRTOS_RAM_Build_Tutorial.md](KW45_FreeRTOS_RAM_Build_Tutorial.md) |
| 下载、启动、设置 GDB 断点或排查启动故障 | [KW45_ISP_RAM_Boot_Guide.md](KW45_ISP_RAM_Boot_Guide.md) |
| 扫描 CAN 通道、选择 `blhost` 接口或抓取 MBOOT 日志 | [CAN_Scanner_and_SPSDK_Devices_Guide.md](CAN_Scanner_and_SPSDK_Devices_Guide.md) |

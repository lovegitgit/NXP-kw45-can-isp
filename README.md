# KW45 CAN ISP 与 Secure RAM 下载工具

这个仓库用于把已链接到 Secure SRAM 的 KW45 应用下载到 RAM 并启动，也可扫描 CAN 适配器、监听 ROM ISP 的 MBOOT 报文。示例应用是 `kw45b41zevk_freertos_hello`，由 MCUXpresso for VS Code 生成 BIN 和 ELF。

## 快速开始

在本仓库根目录打开 Windows PowerShell，将示例路径换成当前构建产物的位置。

```powershell
# 1. 原始 RAM BIN 模式
python .\make-ram-boot.py .\freertos_hello.bin

# 2. 签名 BIN + SB3.1 安全容器模式（使用 keys/ 同时生成签名 *_signed.bin、*_signed.jlink、*.sb3 与 *_sb3_run.bat）
python .\make-ram-boot.py .\freertos_hello.bin --sb3
```

- **ROM ISP（`write-memory` + `execute`）**：让目标进入 ISP，按接线选用 `make-ram-boot.py` 输出的 BusPal CAN、PCAN 或 UART 命令（或直接运行生成的 `*_run.bat`）。先执行 `write-memory`，成功后执行 `execute`。
- **ROM ISP（SB3.1 一步加载并执行）**：运行 `make-ram-boot.py --sb3` 生成 `<stem>.sb3` 与 `*_sb3_run.bat`，通过 `blhost -cb "pcan,PCAN_USBBUS1" receive-sb-file` 一步完成验签、写入 RAM 与跳转执行。
- **J-Link**：连接 SWD，执行脚本打印的命令。上面的示例会在当前目录生成 `freertos_hello.jlink`。

```powershell
JLink.exe .\freertos_hello.jlink
```

每次重新构建后都用新 BIN 生成命令。初始 MSP 和入口来自 BIN 向量表，加载地址由链接布局决定。

## 脚本与文件

| 文件 | 用途和用法 |
| :--- | :--- |
| [make-ram-boot.py](make-ram-boot.py) | 统一 RAM 启动工具：输入 RAM `.bin`，默认输出原始 BIN 下载指令、`.jlink` 与 `*_run.bat`；加 `--sb3` 使用 [keys/](keys/README.md) 密钥**同时生成签名 BIN（`*_signed.bin` + `*_signed.jlink`）与签名 SB3.1 容器（`<stem>.sb3` + `*_sb3_run.bat`）**。 |
| [keys/](keys/README.md) | 存放 KW45 原厂测试密钥（`ROT1_p384.pem`、`ROT1_cert_block.bin`、`sb3kdk.txt` 等）；如需生成客户自用密钥并替换，可使用 NXP 原版 [dm-kw45-secure-boot-scripts](https://github.com/nxp-appcodehub/dm-kw45-secure-boot-scripts) 生成。 |
| [scan-can.py](scan-can.py) | 扫描 CAN 适配器和通道，提供 PCAN 通道的诊断信息。用 `-i pcan` 只扫 PCAN，用 `--all` 扩展到全部后端。 |
| [can-isp-listener.py](can-isp-listener.py) | 通过 PCAN 接收 Classic CAN 报文，重组 MBOOT 包并用 SPSDK 解码。通道、位速率和过滤 ID 在脚本顶部配置，用 Ctrl+C 停止。 |
| [freertos_hello.bin](freertos_hello.bin) | 已打补丁并链接至 Secure SRAM (`0x30005000`) 的 FreeRTOS 示例固件（tested on SDK 26.06）。 |
| [freertos_hello.elf](freertos_hello.elf) | 与 `freertos_hello.bin` 配套的 ELF 调试文件（用于 GDB 源码级断点调试）。 |
| [0001-link-to-ram-run.patch](0001-link-to-ram-run.patch) | 将官方 `kw45b41zevk_freertos_hello` 改造为 Secure SRAM 运行的完整源码补丁。 |
| [.gitignore](.gitignore) | Git 忽略规则。 |

## CAN 诊断

```powershell
python .\scan-can.py -i pcan
python .\can-isp-listener.py
```

监听时从另一个终端执行 ISP 命令。原生 CAN 和普通 UART 使用 SPSDK 3.1.0 的 `blhost`，BusPal CAN 经 COM 口桥接使用 C++ `blhost.exe` 2.6.7。CAN 适配器需要相应驱动，Python 环境需要 `python-can`。 [CAN 设备与 SPSDK 使用手册](CAN_Scanner_and_SPSDK_Devices_Guide.md)

## 详细文档

| 要做的事 | 文档 |
| :--- | :--- |
| 将 Flash 应用改成 Secure RAM 应用，并在 MCUXpresso for VS Code 构建 | [KW45_FreeRTOS_RAM_Build_Tutorial.md](KW45_FreeRTOS_RAM_Build_Tutorial.md) |
| 下载、启动、设置 GDB 断点或排查启动故障 | [KW45_ISP_RAM_Boot_Guide.md](KW45_ISP_RAM_Boot_Guide.md) |
| 扫描 CAN 通道、选择 `blhost` 接口或抓取 MBOOT 日志 | [CAN_Scanner_and_SPSDK_Devices_Guide.md](CAN_Scanner_and_SPSDK_Devices_Guide.md) |

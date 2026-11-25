# CAN 设备扫描工具与 SPSDK CAN 接口支持手册

## 1. 概述

本工程包含通用 CAN 硬件接口与通道扫描脚本（`scan-can.py`），用于自动发现当前系统已连接的各品牌 CAN 收发器硬件通道，并与 NXP **SPSDK (`blhost` / `MbootCANInterface`)** 及自定义监听脚本（`can-isp.py`）无缝对接。

---

## 2. 扫描脚本（`scan-can.py`）功能与使用说明

### 2.1 核心作用
1. **跨品牌通用自动发现**：
   - 基于 `python-can` 的 `can.detect_available_configs()` 接口，自动轮询当前 Windows 系统上已安装驱动的硬件 CAN 后端。
   - 默认过滤掉总是存在的 `virtual` 虚拟测试总线以及会枚举所有普通串口的 `serial` / `slcan` 后端，仅展示真实连接的 CAN 硬件。
2. **双层诊断输出**：
   - **第一层（通用硬件总表）**：统一输出所有品牌通道的 `Interface`（接口后端名）、`Channel`（通道名）、`CAN-FD` 支持能力及品牌特定硬件元数据（如设备序列号、硬件通道号、控制器索引等）。
   - **第二层（PCAN 专属深度诊断表）**：当检测结果中包含 `pcan` 设备时，自动调用 `PCANBasic.dll` 原生接口，额外读取底层句柄（如 `0x51`）、固件版本 (`FW Ver`)、驱动版本 (`Channel Driver Version`)、API 版本及通道占用状态（区分 `AVAILABLE` 空闲、`PCAN-VIEW` 共享可连接、`OCCUPIED` 被其他进程独占）。
3. **连接代码一键生成**：
   - 在扫描结果末尾自动输出可直接粘贴到 Python 脚本中的 `can.interface.Bus(...)` 初始化语句与 `blhost -cb` 命令。

### 2.2 命令行用法

```powershell
# 1. 默认模式：扫描所有主流硬件 CAN 品牌（PCAN、Vector、Kvaser、IXXAT、CANable、创芯科技等）
python scan-can.py

# 2. 指定品牌扫描（仅扫描指定的一个或多个后端）
python scan-can.py -i pcan
python scan-can.py -i pcan vector kvaser

# 3. 全量扫描模式（包含 slcan/serial 等串口 CAN 以及 virtual 虚拟通道）
python scan-can.py --all
```

---

## 3. SPSDK 官方文档 / 源码对 CAN 硬件的底层支持机制

### 3.1 NXP 官方文档与 SPSDK 依赖声明依据
根据 **NXP SPSDK 官方文档 (ReadTheDocs / NXP GitHub Wiki)** 以及本地 `spsdk-3.1.0.dist-info/METADATA`、`common_cli_options.py` 的官方定义：

1. **NXP 官方明确声明完全委托给 `python-can`**：
   - 在 SPSDK 官方 CLI 帮助与文档（`spsdk/apps/utils/common_cli_options.py` 第 284~289 行）中，NXP 对 `-cb / --can` 接口的支持范围给出了明确说明：
     ```text
     -cb, --can interface[,channel,bitrate,rxid,txid]
     CAN Bus settings
     - interface ... CAN interface name (refer to python-can library)
     - channel   ... CAN channel number
     - bitrate   ... CAN bitrate (default=1000000)
     - rxid      ... default arbitration ID for RX (default=0x123)
     - txid      ... default arbitration ID for TX (default=0x321)
     ```
   - 在 `spsdk-3.1.0.dist-info/METADATA` 第 69 行及官方文档安装章节中，NXP 规定启用 CAN 支持的方式为安装 `can` 扩展依赖：
     ```bash
     pip install spsdk[can]
     # 对应官方依赖约束: Requires-Dist: python-can<4.5,>=4.4; extra == "can"
     ```
   - 官方参考链接：
     - **NXP SPSDK ReadTheDocs (`blhost` CLI 文档)**：`https://spsdk.readthedocs.io/en/latest/apps/blhost.html`
     - **python-can 官方支持硬件接口列表 (BitTiming & Interfaces)**：`https://python-can.readthedocs.io/en/stable/interfaces.html`

### 3.2 源码架构分析
在本地 SPSDK 源码中，MBOOT 的 CAN 通信由以下三个模块实现：
- `spsdk/mboot/interfaces/can_interface.py` (`MbootCANInterface`)：继承自 `MbootSerialProtocol`，复用 MBOOT 串行帧协议（`0x5A` 帧头、CRC16-XMODEM 校验、Ping/ACK 握手机制）。
- `spsdk/utils/interfaces/device/can_device.py` (`CANDevice`)：负责底层 CAN 报文收发与拆包/组包。
- `spsdk/apps/utils/common_cli_options.py` (`can_option`)：为 `blhost`、`nxpdevscan` 等 CLI 工具提供 `-cb` / `--can` 参数解析。

在 `CANDevice.open()`（`can_device.py` 第 79~84 行）中，SPSDK 直接调用了 `python-can` 的 `can.Bus` 与 `can.Notifier`：
```python
self.device = Bus(channel=self.channel, interface=self.interface, bitrate=self.bitrate)
self.listener = BytesBufferedReader(self.timeout)
Notifier(self.device, [self.listener])
self.device.set_filters([{"can_id": self.rxid, "can_mask": 0x7FF}])
```

`CANDevice` 的默认协议参数（`can_device.py` 第 22~26 行）与 KW45 ROM Bootloader 默认配置严格对齐：
- **默认速率 (`DEFAULT_BITRATE`)**：`1_000_000` (1 Mbps，标准 Classic CAN，每帧最大 `MAX_MESSAGE_SIZE = 8` 字节)
- **默认主机发送 ID (`DEFAULT_TX_ARBITRATION_ID`)**：`0x321`（11-bit 标准帧）
- **默认主机接收 ID (`DEFAULT_RX_ARBITRATION_ID`)**：`0x123`（11-bit 标准帧，掩码 `0x7FF`）

### 3.2 SPSDK 支持的 CAN 品牌与设备清单

由于 SPSDK 将 `interface` 和 `channel` 直接透传给 `python-can`，所有受 `python-can` 支持的 24 种 CAN 后端均可直接在 SPSDK 中使用：

| 品牌 / 硬件系列 | SPSDK `interface` 名称 | `channel` 典型取值示例 | 驱动 / 依赖库要求 | 扫描脚本默认覆盖 |
| :--- | :--- | :--- | :--- | :--- |
| **PEAK-System (PCAN)**<br>(PCAN-USB / PCAN-USB FD / Pro FD / PCI / LAN) | `pcan` | `PCAN_USBBUS1`<br>`PCAN_PCIBUS1` | `PCANBasic.dll` (PEAK 驱动) | 是（含深度诊断） |
| **Vector**<br>(VN1610 / VN1630A / VN1640A / VN8900 等) | `vector` | `0`, `1` | `vxlapi64.dll` (Vector XL Driver) | 是 |
| **Kvaser**<br>(Leaf Light / Memorator / USBcan 等) | `kvaser` | `0`, `1` | `canlib32.dll` (Kvaser CANLIB) | 是 |
| **HMS IXXAT**<br>(USB-to-CAN V2 / CAN-IB 等) | `ixxat` | `0`, `1` | IXXAT VCI V4 驱动 | 是 |
| **CandleLight / CANable / 开源 gs_usb**<br>(刷写 candleLight 固件的 USB-CAN) | `gs_usb` | `0` | `pyusb` + `libusb` / WinUSB 驱动 | 是 |
| **创芯科技 / 周立功兼容卡**<br>(USBCAN-II / Canalyst-II) | `canalystii` | `0`, `1` | `canalystii` Python 包 / `ControlCAN.dll` | 是 |
| **Intrepid Control Systems**<br>(ValueCAN 3/4, neoVI FIRE/ION) | `neovi` | `1` | `python-ics` + `icsneo40.dll` | 是 |
| **National Instruments (NI)**<br>(NI-XNET / NI-CAN 接口卡) | `nixnet`<br>`nican` | `CAN1`<br>`CAN0` | `nixnet` (`nixnet.dll`)<br>NI-CAN 驱动 | 是 |
| **SYSTEC electronic**<br>(sysWORXX USB-CANmodul 系列) | `systec` | `0`, `1` | `usbcan64.dll` | 是 |
| **ETAS**<br>(ES581 / ES582 / ES584 等) | `etas` | `ETAS://ETH/...` 或枚举名 | ETAS BOA SDK 驱动 | 是 |
| **8devices**<br>(USB2CAN 转换器) | `usb2can` | 设备序列号字符串 (如 `ED000200`) | USB2CAN Canal DLL | 是 |
| **LinkLayer Labs / 开源 CANTact** | `cantact` | `0` | `cantact` Python 库 | 是 |
| **ESD Electronics / Neousys** | `iscan` / `neousys` | `0` | 对应厂商底层 C DLL | 是 |
| **串口转 CAN (SLCAN / Lawicel)**<br>(CANable SLCAN 固件 / USBtin 等) | `slcan` | `COM3`, `COM4@115200` | `pyserial` (标准虚拟串口) | 需加 `--all` |
| **Seeed Studio / Robotell 串口 CAN** | `seeedstudio`<br>`robotell` | `COM3` | `pyserial` | 需加 `--all` |
| **Linux SocketCAN** (仅 Linux 环境) | `socketcan` | `can0`, `vcan0` | Linux 内核 SocketCAN | Linux 下支持 |

---

## 4. 在 SPSDK (`blhost`) 中使用各品牌 CAN 设备的命令指南

### 4.1 传统 BusPal 桥接方式 vs SPSDK 原生 CAN 方式

| 对比维度 | 传统 C++ `blhost.exe` (BusPal 桥接) | SPSDK `blhost` 原生 CAN 接口 |
| :--- | :--- | :--- |
| **硬件链路** | PC $\xrightarrow{\text{USB UART (COMx)}}$ NXP 开发板 (BusPal) $\xrightarrow{\text{CAN}}$ 目标 MCU | PC $\xrightarrow{\text{USB}}$ **任意品牌 CAN 分析仪 (如 PCAN)** $\xrightarrow{\text{CAN}}$ 目标 MCU |
| **命令行选项** | `-b can -p COM3` | `-cb "interface[,channel,bitrate,rxid,txid]"` |
| **局限性** | 只能使用刷了 BusPal 固件的特定 NXP 桥接板 | 支持上表所有 `python-can` 硬件设备 |

### 4.2 SPSDK `blhost -cb` 参数语法与实战示例

参数格式：
```text
-cb "interface[,channel,bitrate,rxid,txid]"
```
- `interface`：必填，上表中的 `SPSDK interface 名称`（即扫描脚本输出的 `Interface` 列）。
- `channel`：可选（建议必填），扫描脚本输出的 `Channel` 列。
- `bitrate`：可选，默认 `1000000` (1 Mbps)。
- `rxid`：可选，主机接收 CAN ID（即 MCU 发送 ID），默认 `0x123`。
- `txid`：可选，主机发送 CAN ID（即 MCU 接收 ID），默认 `0x321`。

#### 示例 1：使用 PEAK-System PCAN (`pcan`)
```powershell
# 使用默认 1M 速率、rxid=0x123、txid=0x321 查询 KW45 Bootloader 版本
blhost -cb "pcan,PCAN_USBBUS1" get-property 1

# 显式指定速率与收发 CAN ID
blhost -cb "pcan,PCAN_USBBUS1,1000000,0x123,0x321" get-property 1
```

#### 示例 2：使用 Vector CAN 卡 (`vector`)
```powershell
blhost -cb "vector,0,1000000,0x123,0x321" get-property 1
```

#### 示例 3：使用 Kvaser CAN 卡 (`kvaser`)
```powershell
blhost -cb "kvaser,0,1000000,0x123,0x321" get-property 1
```

#### 示例 4：使用 CANable / CandleLight (`gs_usb`) 或 创芯科技 (`canalystii`)
```powershell
# gs_usb (CandleLight 固件)
blhost -cb "gs_usb,0,1000000,0x123,0x321" get-property 1

# 创芯科技 USBCAN-II (Canalyst-II)
blhost -cb "canalystii,0,1000000,0x123,0x321" get-property 1
```

### 4.3 配合 `can-isp.py` 进行旁路抓包与 SPSDK 协议实时翻译
- **场景 A（单 CAN 卡抓包）**：当使用另一套主机或 `blhost.exe -b can -p COM3` 与 KW45 通信时，将 PCAN 挂在同一 CAN 总线上运行 `python can-isp.py`，即可旁路监听 `0x321`（主机请求）与 `0x123`（KW45 响应）并实时翻译出 MBOOT 命令与返回值。
- **场景 B（PCAN 共享模式同时通信与抓包）**：如果先打开 `PCAN-View` 或在支持共享句柄的驱动下，通过 `scan-can.py` 可看到通道状态为 `PCAN-VIEW (In use by PCAN-View, still connectable)`，此时仍可连接该通道进行报文收发或监听。

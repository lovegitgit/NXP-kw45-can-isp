# KW45 Secure RAM 下载与调试

本手册适用于 `kw45b41zevk_freertos_hello` 的 Secure RAM 镜像。用 MCUXpresso for VS Code 构建后，应用工程根目录会生成 `freertos_hello.bin` 和 `freertos_hello.elf`。链接布局、ECC 和启动代码的修改见编译教程。 [KW45 FreeRTOS RAM 编译教程](KW45_FreeRTOS_RAM_Build_Tutorial.md)

## 1. 从当前 BIN 生成命令

在 `can-isp` 仓库根目录打开 Windows PowerShell。每次重新构建后，用新的 BIN 重新生成。

```powershell
python .\parse-bin.py "D:/NXP/work-prj/kw45b41zevk_freertos_hello/freertos_hello.bin"
```

脚本会输出三组 ISP 下载与启动命令，为本工程的 Secure RAM 镜像在当前目录生成 `freertos_hello.jlink`，并打印 J-Link 运行命令。

| 启动参数 | 来源 |
| :--- | :--- |
| 初始 MSP | BIN 向量表 Word 0 |
| `Reset_Handler` | BIN 向量表 Word 1。ISP `execute` 使用原值，J-Link 清除最低位后设为 PC，并单独设置 `xPSR.T` |
| 加载地址、向量表基址 | 由 linker script 的 `m_interrupts` 决定。当前脚本按 `Reset_Handler` 所在的 32 KiB 边界推算，布局改变时用 `--addr` 指定实际地址 |

## 2. 选择下载方式

### ROM ISP

KW45 EVK 按住 SW4（PTA4）复位进入 ISP。选择和接线匹配的命令组，先执行 `write-memory`，收到成功状态后再执行 `execute`。

| 连接方式 | 脚本输出的命令组 |
| :--- | :--- |
| PCAN-USB 直连 CAN | **Direct PCAN**，SPSDK 3.1.0 `blhost -cb`，示例通道 `PCAN_USBBUS1` |
| COM 口经 BusPal CAN 桥接板 | **BusPal CAN via COM3**，C++ `blhost.exe` 2.6.7 |
| COM 口直连目标的 ROM ISP UART | **Direct UART**，SPSDK 3.1.0 `blhost -p`。默认 `COM3`、57600 baud。按需用 `--uart-port` 指定端口和波特率 |

KW45 这里使用的 `blhost execute` 参数依次是向量表中的入口、镜像基址和初始 MSP。连接和版本说明见 CAN 手册。 [CAN 与 BusPal 手册](CAN_Scanner_and_SPSDK_Devices_Guide.md#41-传统-buspal-桥接方式-vs-spsdk-原生-can-方式) · [SPSDK 3.1.0 UART 参数](https://spsdk.readthedocs.io/en/v3.1.0/apps/blhost.html#cmdoption-blhost-p)

### J-Link Commander

连接 SWD，确认目标板供电和 VTref。在生成脚本的同一目录执行输出的命令。

```powershell
JLink.exe ".\freertos_hello.jlink"
```

脚本暂停 CPU 后下载并回读校验，设置 VTOR、MSP、PC 和 Thumb 状态，再运行。两种下载方式启动后，均可在 Debug UART（115200 baud，8N1）观察约每秒一行 `Hello world.`。 [SEGGER J-Link Commander](https://kb.segger.com/J-Link_Commander)

## 3. 用 GDB 在启动阶段设断点

使用同次构建的 ELF，先确认其有文件内容的加载段均位于 Secure SRAM。关闭占用同一个 J-Link 的会话，在两个 PowerShell 窗口分别启动 GDB Server 和 GDB。

```powershell
JLinkGDBServerCL.exe -device KW45B41Z83 -if SWD -speed 4000 -port 2331 -localhostonly 1
```

```powershell
arm-none-eabi-gdb "D:/NXP/work-prj/kw45b41zevk_freertos_hello/freertos_hello.elf"
```

在 GDB 中下载并回读 ELF。用 ELF 的 `__isr_vector` 定位向量表。

```gdb
target remote localhost:2331
monitor reset
load
compare-sections
set $vector_base = (unsigned int)&__isr_vector
x/2wx $vector_base
```

确认加载段校验通过、向量值与当前 BIN 一致后，设置启动寄存器。下面的 `0xE000ED08` 是 VTOR 的寄存器地址，写入的值来自 ELF。

```gdb
monitor reg CONTROL = 0x00000000
set {unsigned int}0xE000ED08 = $vector_base
eval "monitor reg MSP = (0x%x)", $vector_base
set $pc = (*(unsigned int *)($vector_base + 4)) & ~1
set $xpsr = 0x01000000
x/i $pc
hbreak SystemInit
continue
```

停在 `SystemInit()` 时，`Reset_Handler` 已完成数据和栈区的 ECC 初始化，此时可查看栈。继续调试应用时可用 `hbreak main` 或 `hbreak hello_task`。 [SEGGER J-Link GDB Server](https://kb.segger.com/J-Link_GDB_Server) · [GNU GDB `eval`](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Output.html)

## 4. 常见问题

| 现象 | 优先检查 |
| :--- | :--- |
| ISP `execute` 返回 `10006` | 用当前 BIN 重新生成命令，确认入口和 MSP 来自该镜像，第二个参数等于镜像基址。 |
| ISP 写入返回 `MemoryRangeInvalid` | 核对加载基址、BIN 长度和 ROM 保留区。 |
| 进入 `SystemInit()` 前触发 `BusFault` | 读取 CFSR 和 BFAR，检查出错地址是否在尚未初始化 ECC 的数据或栈区。 |
| 跳转后进入 `DefaultISR` | 检查 `SystemInit()` 是否在解除中断屏蔽前清理 ROM 遗留的 SysTick 和 NVIC 状态。 |
| J-Link 连接或校验失败 | 核对器件名、Secure 调试权限、SWD 和 VTref。若错误发生在镜像尾部，检查 J-Link 默认 Work RAM 的 ECC 初始化范围。 [SEGGER KW45 SRAM 说明](https://kb.segger.com/NXP_KW45) |
| 无 UART 输出 | 确认 CPU 已运行到任务，串口终端使用 Debug UART 的 COM 口和 115200 baud。 |

如果要附加到正在 RAM 中运行的镜像，连接前需确认 J-Link 的 Work RAM 初始化已禁用，防止覆盖应用。 [SEGGER ECC](https://kb.segger.com/ECC)

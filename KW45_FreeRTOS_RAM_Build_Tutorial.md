# KW45 FreeRTOS Hello 从 Flash 迁移到 RAM 的编译教程

以 KW45B41Z-EVK 上的 `kw45b41zevk_freertos_hello` 为例。工程位于 Windows 的 `D:\NXP\work-prj\kw45b41zevk_freertos_hello`，对应 WSL 的 `/mnt/d/NXP/work-prj/kw45b41zevk_freertos_hello`。编译得到的 `freertos_hello.bin` 可由 CAN ISP 或 J-Link 下载到 Secure SRAM，从向量表的 `Reset_Handler` 启动。调试时使用同次构建的 `freertos_hello.elf`。

## 1. 确认原 Flash 工程需要调整的四处

| 文件 | Flash 工程的行为 | RAM 工程的修改 |
| :--- | :--- | :--- |
| `devices/Wireless/KW/KW45B41Z83/gcc/KW45B41Z83xxxA_ram.ld` | 原 Flash 脚本将向量表放在 `0x00000000`，代码从 `0x00000400` 开始，`.data` 从 Flash 装载到 RAM | 新增 RAM linker script，将向量表、代码和初始化数据链接到 Secure SRAM |
| `flags.cmake` | Debug 链接参数选择 Flash linker script | 将 Debug 的 `-T` 切换到 RAM linker script |
| `devices/Wireless/KW/KW45B41Z83/gcc/startup_KW45B41Z83.S` | 原 startup 的 RAM 路径跳过 ECC 初始化，依赖调试器准备 RAM | 在首次用栈前初始化 `m_data1`，避开已经下载的镜像 |
| `devices/Wireless/KW/KW45B41Z83/system_KW45B41Z83.c` | `SystemInit()` 直接进行原有系统配置 | 在入口关闭 SysTick，清理 NVIC 中 ROM ISP 可能遗留的使能位和挂起位 |

Flash 镜像包含按 Flash 地址链接的向量表和符号。从 ISP 或 J-Link 跳转前，需要重新链接镜像并安排启动时的 RAM 初始化。FreeRTOS 任务和 UART 应用代码沿用原示例。从原 Flash 工程迁移时按第 2 到第 5 节修改，使用已经迁移的工程可直接从第 6 节开始。

## 2. 从 Flash linker script 创建 RAM linker script

进入应用工程根目录。仅在 RAM linker script 尚未创建时复制原脚本，以保留原有的段定义和链接符号。

```powershell
Set-Location 'D:/NXP/work-prj/kw45b41zevk_freertos_hello'
$linkerDir = 'devices/Wireless/KW/KW45B41Z83/gcc'
$ramLinker = Join-Path $linkerDir 'KW45B41Z83xxxA_ram.ld'
if (-not (Test-Path $ramLinker)) {
    Copy-Item (Join-Path $linkerDir 'KW45B41Z83xxxA_flash.ld') $ramLinker
}
```

### 2.1 定义 Secure SRAM 的区域边界

在新脚本中保留 `ENTRY(Reset_Handler)`，用以下内容替换 `MEMORY`，去掉 Flash 脚本中的 `m_data0` 区域。

```ld
/* Keep the ROM work area and communication buffer below the image. */
MEMORY
{
  m_interrupts          (RX)  : ORIGIN = 0x30008000, LENGTH = 0x00000400
  m_text                (RX)  : ORIGIN = 0x30008400, LENGTH = 0x00007C00
  m_data1               (RW)  : ORIGIN = 0x30010000, LENGTH = 0x00008000
}
```

| Secure SRAM 范围（末地址不含） | 容量 | 用途 |
| :--- | :--- | :--- |
| `[0x30000000, 0x30004000)` | 16 KiB | ROM Bootloader 工作区 |
| `[0x30004000, 0x30008000)` | 16 KiB | 避开 ROM 通信缓冲的预留区 |
| `[0x30008000, 0x30008400)` | 1 KiB | `m_interrupts`，向量表基址按 1 KiB 对齐 |
| `[0x30008400, 0x30010000)` | 31 KiB | `m_text`，代码、只读数据和初始化数据 |
| `[0x30010000, 0x30018000)` | 32 KiB | `m_data1`，`.bss`、heap 和实际 MSP 栈 |

本例沿用 ROM ISP 跳转时的 Secure 地址视图。配置下载地址时以 `MEMORY` 和 ELF 的实际地址为准，`0x20008000` 是同一物理 SRAM 的 Non-Secure 别名。

### 2.2 让初始化数据随 BIN 原地加载

保留原脚本的 `.interrupts > m_interrupts` 和 `KEEP(*(.isr_vector))`，以及放在 `m_text` 中的 `.text`、异常索引和构造函数相关段。删除原来位于 `.data` 外部的 `__etext`、`__DATA_ROM` 赋值，将原来的 `.data : AT(__DATA_ROM) ... > m_data0` 及其后的 `__DATA_END` 改成下面的形式。

```ld
  .data :
  {
    . = ALIGN(4);
    /* The loader writes initialized data directly to its runtime address. */
    __etext = .;
    __DATA_ROM = .;
    __DATA_RAM = .;
    __data_start__ = .;
    *(.ramfunc*)
    *(.data)
    *(.data*)
    KEEP(*(.jcr*))
    . = ALIGN(4);
    __data_end__ = .;
  } > m_text

  __DATA_END = __data_end__;
  text_end = ORIGIN(m_text) + LENGTH(m_text);
  ASSERT(__DATA_END <= text_end, "region m_text overflowed with text and data")
```

`.data` 的 LMA（装载地址）和 VMA（运行地址）相同。BIN 从向量表排到 `.data` 结束，远处的 `.bss`、heap 和 stack 只占运行时内存。原 startup 的 `.data` copy loop 仍会执行，此时源和目标相同。

保留 `.bss`、`.heap`、`.stack` 到 `m_data1` 的映射，以及原有的 stack 和 heap 符号及容量检查。当前默认 `STACK_SIZE` 和 `HEAP_SIZE` 各 2 KiB，实际 MSP 栈边界由 `__StackLimit = 0x30017800` 和 `__StackTop = 0x30018000` 确定。map 里的 `.stack` 段地址可能低于实际栈区，核对 MSP 时看这两个链接符号和向量表 Word 0。

## 3. 将 Debug 构建切换到 RAM linker script

从原 Flash 工程手动迁移时，可将 `flags.cmake` 的 `CMAKE_EXE_LINKER_FLAGS_DEBUG` 中引用 Flash linker script 的 `-T` 行换成以下内容，保留原来的 Cortex-M33、Thumb、FPU 和其他链接选项。

```cmake
    -T\"${ProjDirPath}/devices/Wireless/KW/KW45B41Z83/gcc/KW45B41Z83xxxA_ram.ld\" -static \
```

应用工程通过 `flags.cmake` 的这条 `-T` 将现有 `debug` 配置链接到 Secure SRAM。已有此配置的工程可以直接按第 6 节构建。C 编译配置开启了 `-Werror`，构建时沿用项目的 Arm GNU Toolchain 和现有警告选项。

## 4. 在首次用栈前初始化 RAM ECC

打开 `devices/Wireless/KW/KW45B41Z83/gcc/startup_KW45B41Z83.S`。在 `Reset_Handler` 中保留原有的 `cpsid i` 和 VTOR、MSP、MSPLIM 设置。在 `#if !defined(BYPASS_ECC_RAM_INIT)` 内，用以下逻辑替换原先直接跳过 RAM ECC 初始化的分支，接在原 `.ram_init_ctcm01` 标签之前。

```asm
    /* Keep the original flash-path test, with the Secure alias bit removed. */
    ldr     r0, =0x04000000
    ldr     r1, =.ram_init_ctcm01
    bics.w  r1, #0x10000000
    cmp     r0, r1
    bcs.n   .ram_init_ctcm01

.ram_target_init_data1:         /* Initialize m_data1 ECC for RAM target. */
    ldr     r0, =0x30010000
    ldr     r1, =0x30018000
    ldr     r2, =0
    ldr     r3, =0
    ldr     r4, =0
    ldr     r5, =0
.loop_ram_data1:
    stmia   r0!, {r2 - r5}
    cmp     r0, r1
    bcc.n   .loop_ram_data1
    b.n     .ram_init_done
```

Flash 路径继续使用原有的 CTCM/STCM 初始化。RAM 路径在进入 `SystemInit()` 前，以 16 字节为步长初始化 `[0x30010000, 0x30018000)`，避开 `0x30008000–0x3000FFFF` 的镜像区。32 KiB 范围需要循环 2,048 次，开销发生在启动阶段。RAM 构建应启用 ECC 分支，亦即不定义 `BYPASS_ECC_RAM_INIT`。调整 `m_data1` 时要同步更新汇编中的起止地址，并保持 16 字节对齐。

## 5. 在解除中断屏蔽前清理 ROM ISP 状态

打开 `devices/Wireless/KW/KW45B41Z83/system_KW45B41Z83.c`，在 `SystemInit()` 入口添加以下代码，后面接原来的系统初始化逻辑。

```c
  /* Clear SysTick and NVIC state left by the ROM ISP bootloader. */
  SysTick->CTRL = 0U;
  SysTick->VAL  = 0U;
  for (uint32_t i = 0U; i < 8U; i++)
  {
      NVIC->ICER[i] = 0xFFFFFFFFUL;
      NVIC->ICPR[i] = 0xFFFFFFFFUL;
  }
```

ISP 的 `execute` 是热跳转。若 ROM 留着 CAN 等中断使能或挂起位，startup 运行到 `cpsie i` 后可能进入 `DefaultISR`。这段代码在 PRIMASK 仍屏蔽中断时执行，之后由应用和 FreeRTOS 按各自的初始化流程配置所需中断。

## 6. 在 MCUXpresso for VS Code 中构建 Secure RAM 镜像

1. 在 VS Code 打开应用工程 `D:/NXP/work-prj/kw45b41zevk_freertos_hello`。
2. 在工程根目录的 `flags.cmake` 中确认 `CMAKE_EXE_LINKER_FLAGS_DEBUG` 的 `-T` 参数指向 `KW45B41Z83xxxA_ram.ld`。
3. 在 **MCUXpresso for VS Code → Projects** 中右键工程，对原有的 `debug` 配置执行 **Rebuild**（有些版本显示为 **Clean Build**）。 [NXP VS Code 重新构建](https://docs.mcuxpresso.nxp.com/mcux-vscode/latest/Training-Getting-Started-RT1060.html#build-project)

构建完成后，应用工程根目录包含 `freertos_hello.elf` 和 `freertos_hello.bin`，链接 map 在 `debug/output.map`。按第 7 节核对向量表链接到 `0x30008000`。

## 7. 下载前检查 ELF、BIN 和向量表

在应用工程根目录运行 Arm GNU 工具。

```powershell
arm-none-eabi-objdump -h .\freertos_hello.elf
arm-none-eabi-readelf -lW .\freertos_hello.elf
arm-none-eabi-nm -n .\freertos_hello.elf |
    Select-String '__isr_vector|__StackTop|__StackLimit|__etext|__data_start__|Reset_Handler'
```

在 `can-isp` 仓库根目录解析应用工程的 BIN。

```powershell
python .\parse-bin.py "D:/NXP/work-prj/kw45b41zevk_freertos_hello/freertos_hello.bin" --addr 0x30008000
```

| 检查项 | 合格条件 | 当前示例产物 |
| :--- | :--- | :--- |
| ELF 向量表 | `.interrupts` 的 VMA 和 LMA 均等于镜像基址 | `0x30008000` |
| ELF 装载范围 | 有文件内容的 `LOAD` 段均位于 `[0x30008000, 0x30010000)`，包含初始化数据 | `[0x30008000, 0x3000D8C8)` |
| ELF 运行时数据 | `.data` 的 LMA 等于 VMA，`m_data1` 对应的 `LOAD` 段 `FileSiz = 0` | `__etext = __data_start__ = 0x3000D8C0` |
| BIN 起始 Word 0 | 等于 `__StackTop` | `0x30018000` |
| BIN 起始 Word 1 | Bit 0 为 1，清除该位后的地址指向 `Reset_Handler` | 向量表值 `0x3000847D`，指令地址 `0x3000847C` |
| BIN 文件大小 | 不超过 32 KiB，对应镜像基址到最后一个有文件内容的段末尾 | 22,728 字节 |

每次重新构建都应读取实际产物里的 Word 0 和 Word 1。代码布局改动可能改变 `Reset_Handler` 地址。

## 8. 从 CAN ISP 或 J-Link 启动

### CAN ISP

按住 SW4（PTA4）复位进入 ROM ISP。执行 `write-memory` 后检查成功状态，再执行 `execute`。示例使用 PCAN 通道 `PCAN_USBBUS1` 和第 7 节的实际向量值。

```powershell
blhost -cb "pcan,PCAN_USBBUS1" write-memory 0x30008000 "D:/NXP/work-prj/kw45b41zevk_freertos_hello/freertos_hello.bin"
blhost -cb "pcan,PCAN_USBBUS1" execute 0x3000847D 0x30008000 0x30018000
```

KW45 ROM 的三个参数依次为带 Thumb Bit 的入口、向量表基址、初始 MSP。

### J-Link

连接 SWD 并为目标板供电。在 `can-isp` 仓库根目录检查 `run_ram.jlink` 的器件名、镜像路径、下载地址、VTOR、MSP 和 PC。下载地址及 VTOR 用向量表基址，MSP 用 Word 0，PC 用清除 Thumb Bit 后的 Word 1。脚本单独设置 `xPSR.T`。

```powershell
JLink.exe .\run_ram.jlink
```

脚本应输出 `Verify successful.`，随后从 RAM 启动。两种方式均可在 Debug UART（115200 baud、8N1）观察约每秒一行 `Hello world.`。需要 ELF 源码断点或查看 Fault 寄存器时，使用下载与调试手册中的 GDB 流程。 [KW45 RAM 下载与调试手册](KW45_ISP_RAM_Boot_Guide.md)

| 现象 | 优先检查 |
| :--- | :--- |
| 构建产物的向量表仍在 `0x00000000` | 查看详细链接命令的 `-T` 和 map 文件，确认取的是刚生成的 RAM ELF/BIN |
| 链接提示 `m_text` 或 `m_data1` 溢出 | 在 map 中核对代码、初始化数据及 `.bss` 的大小，再核对 heap 和 MSP 栈预算。调整区域时同步更新 ECC 初始化范围 |
| ISP `execute` 返回 `10006` | 用当前 BIN 的 Word 1、镜像基址、Word 0 重新核对参数顺序和数值 |
| 跳转后出现 `BusFault` 或 `DefaultISR` | 前者优先检查 ECC 循环和故障地址，后者检查 `SystemInit()` 是否在 `cpsie i` 前清理了 ROM 中断状态 |

## 9. 为 Secure RAM 镜像生成 J-Link 命令或 SB3.1 配置

### 从 BIN 自动生成 J-Link 命令文件

在 `can-isp` 仓库根目录打开 Windows PowerShell。传入 BIN 后，`parse-bin.py` 会输出 BusPal、CAN 和 UART 的 ISP 命令，在当前目录生成 `freertos_hello.jlink`，并打印 J-Link 运行命令。

```powershell
$app = 'D:/NXP/work-prj/kw45b41zevk_freertos_hello'
$image = "$app/freertos_hello.bin"
$out = "$app/debug"
python .\parse-bin.py $image
if ($LASTEXITCODE -ne 0) { throw 'J-Link command file generation failed' }
JLink.exe .\freertos_hello.jlink
```

### 通过 SB3.1 认证并写入 RAM

沿用上面的 `$image` 和 `$out`，将示例路径替换成目标板已配套的签名私钥、证书块和 SB3KDK 文件。生成器只保存路径，`nxpimage` 在导出时读取对应文件。示例采用已核对的 SPSDK 2.1.1 和 `kw45xx` family。使用其他 SPSDK 版本时，按该版本的 family 名称调整 `--sb31-family`。 [NXP SPSDK KW45 SB3.1 示例](https://spsdk.readthedocs.io/en/latest/examples/sb/kw45xx_k32w1xx/kw45xx_k32w1xx_load_NBU_image.html)

```powershell
python .\prepare-ram-downloads.py $image --output-dir $out `
    --sb31-sign-key 'D:/keys/kw45-sign.pem' `
    --sb31-cert-block 'D:/keys/kw45-cert-block.yaml' `
    --sb31-kdk-file 'D:/keys/kw45-sb3kdk.txt'
if ($LASTEXITCODE -ne 0) { throw 'SB3.1 configuration failed' }
nxpimage sb31 export -c "$out/freertos_hello.sb31.json"
if ($LASTEXITCODE -ne 0) { throw 'SB3.1 export failed' }
blhost -cb 'pcan,PCAN_USBBUS1' receive-sb-file "$out/freertos_hello.sb3"
if ($LASTEXITCODE -ne 0) { throw 'SB3.1 receive failed' }
python .\parse-bin.py $image --addr 0x30008000
```

容器包含向 `0x30008000` 写入镜像的 RAM `load` 命令。收到成功状态后，执行解析脚本输出的 `blhost execute`。该命令向 KW45 ROM 提供入口、向量表和初始 MSP，因此需要目标板当前的生命周期允许独立的 ISP `execute`。完成启动后，通过 UART 输出验证任务运行。 [NXP KW45 生命周期及 ISP 命令](https://www.nxp.com/docs/en/reference-manual/KW45SRM.pdf)

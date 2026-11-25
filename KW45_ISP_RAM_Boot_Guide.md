# KW45 ISP Mode 下载程序到 RAM 运行操作手册

## 1. 核心结论与技术要点

**KW45 支持在 ISP Mode (MCU Bootloader / MBOOT) 下通过 `blhost write-memory` + `blhost execute` 直接将裸机或 FreeRTOS 应用程序下载至片上 SRAM 并跳转运行（无需打包 SB3.1 容器）**。要使程序成功跳转且不触发 `10006 Unsupported Command` 或 `BusFault`，必须严格满足以下 **4 个硬件与 ROM 校验条件**：

1. **`blhost execute <ADDRESS> <ARG> <STACKPOINTER>` 的 `<ARG>` 参数必须填镜像起始基地址**：
   - 根据《blhost User's Guide》4.2.11 节及 KW45 ROM 实测，KW45 ROM 的 `execute` 命令用于跳转到 Boot Image：
     - `<ADDRESS>`：镜像向量表第 2 个 Word（偏移 `+0x04` 处的 `Reset_Handler` 地址，Bit 0 为 `1`，如 `0x3000847D`）。
     - **`<ARG>`**：**必须填写镜像在 RAM 中的起始加载地址（如 `0x30008000`），严禁填 `0`**！KW45 ROM 会读取 `<ARG>` 指向的向量表首地址，并校验其 `Word 0 == <STACKPOINTER>`、`Word 1 == <ADDRESS>`。如果 `<ARG>` 填 `0`，ROM 会直接拒绝并返回 `10006 (0x2716) Unsupported Command`。
     - `<STACKPOINTER>`：镜像向量表第 1 个 Word（偏移 `+0x00` 处的 `Initial_SP`，如 `0x30018000`）。
2. **链接地址必须使用 Secure SRAM 别名 (`0x3000_xxxx`) 并避开 ROM 保留区**：
   - KW45 ROM Bootloader 运行在 Cortex-M33 **Secure 状态**，且通过 `get-property 12` 可知 ROM 自身占用了前 16 KiB SRAM（`0x2000_0000 - 0x2000_3FFF` / `0x3000_0000 - 0x3000_3FFF`）。
   - 应用程序必须链接到保留区之后的 **Secure SRAM 地址（如 `0x3000_8000`）**，防止 Secure 态下向 Non-Secure 地址 (`0x2000_xxxx`) 取指触发 `SecureFault`。
3. **必须在 `Reset_Handler` 中初始化数据/栈区 (`m_data1`) 的 SRAM 硬件 ECC**：
   - KW45 的片上 SRAM 带有硬件 ECC 校验。上电后，未被 128-bit 对齐写入 (`stmia r0!, {r2 - r5}`) 初始化过 ECC 的高地址 SRAM 区域，一旦被 CPU 当作栈 (`MSP`) 或变量访问，会立即触发精确总线错误 **`BusFault` (`CFSR = 0x8200`)**。
   - NXP 原厂 `startup_KW45B41Z83.S` 默认在 RAM Target 下跳过了全部 ECC RAM 初始化（假设由 J-Link 脚本初始化）。通过 CAN ISP 下载时，必须在 `Reset_Handler` 中对数据/堆栈段 (`m_data1`) 执行 `stmia` 循环清零以初始化硬件 ECC。
4. **必须在 `SystemInit()` 入口清理 ROM ISP 残留的 `SysTick` 与 `NVIC` 中断**：
   - 从 ROM ISP 热跳转到 RAM 并未经过硬件 Reset，ROM 在 CAN ISP 期间开启的 `CAN0` 等外设中断仍处于使能状态。必须在 `SystemInit()` 开头关闭 `SysTick` 并清空 `NVIC->ICER` / `NVIC->ICPR`，避免启动代码执行 `cpsie i` 时误跳入 `DefaultISR`。

---

## 2. KW45 SRAM 内存映射与规划表

| 内存区域 | Non-Secure 地址范围 | Secure 地址范围 (ISP RAM 运行使用) | 容量 | 规划用途 |
| :--- | :--- | :--- | :--- | :--- |
| **ROM Reserved** | `0x2000_0000 – 0x2000_3FFF` | `0x3000_0000 – 0x3000_3FFF` | 16 KiB | ROM Bootloader (MBOOT) 独占保留区 |
| **缓冲隔离区** | `0x2000_4000 – 0x2000_7FFF` | `0x3000_4000 – 0x3000_7FFF` | 16 KiB | 预留空闲（避开 `0x3000_4000` 通信缓冲） |
| **`m_interrupts`** | `0x2000_8000 – 0x2000_83FF` | **`0x3000_8000 – 0x3000_83FF`** | 1 KiB | 应用中断向量表 (`.isr_vector`，1KB 对齐) |
| **`m_text`** | `0x2000_8400 – 0x2000_FFFF` | **`0x3000_8400 – 0x3000_FFFF`** | 31 KiB | 代码段 `.text`、只读常量 `.rodata` 及 `.data` |
| **`m_data1`** | `0x2001_0000 – 0x2001_7FFF` | **`0x3001_0000 – 0x3001_7FFF`** | 32 KiB | `.bss`、`.heap`、`.stack` (`__StackTop = 0x30018000`) |

---

## 3. 工程代码改造步骤（以 `kw45b41zevk_freertos_hello` 为例）

### 3.1 RAM 链接脚本 (`KW45B41Z83xxxA_ram.ld`)

将 `.interrupts`、`.text`、`.rodata` 和 `.data` 全部连续放置在 `0x30008000` 开始的区间（避免 `objcopy -Obinary` 跨段产生巨型填充文件），并将 `.bss` / `.heap` / `.stack` 放在 `0x30010000 ~ 0x30018000`：

```ld
MEMORY
{
  m_interrupts          (RX)  : ORIGIN = 0x30008000, LENGTH = 0x00000400
  m_text                (RX)  : ORIGIN = 0x30008400, LENGTH = 0x00007C00
  m_data1               (RW)  : ORIGIN = 0x30010000, LENGTH = 0x00008000
}
```
并在 `.data` 段中令 `__etext = .; __DATA_ROM = .; __data_start__ = .;`，使其归属于 `> m_text` 原地就位。

### 3.2 启动文件 (`startup_KW45B41Z83.S`) 初始化 `m_data1` 硬件 ECC

在 `Reset_Handler` 中，当检测到当前运行在 RAM Target 时，对 `m_data1 (0x30010000 - 0x30018000)` 执行 128-bit `stmia` ECC 初始化（保留 `0x30008000` 代码区不被覆盖）：

```asm
#if !defined(BYPASS_ECC_RAM_INIT)
    ldr     r0, =0x04000000
    ldr     r1, =.ram_init_ctcm01
    bics.w  r1, #0x10000000
    cmp     r0, r1
    bcs.n   .ram_init_ctcm01

.ram_target_init_data1:         /* Initialize m_data1 (0x30010000 - 0x30018000) ECC for RAM target */
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

### 3.3 系统初始化 (`system_KW45B41Z83.c`) 清理 ROM 残留中断

在 `SystemInit()` 开头添加：

```c
  /* Disable SysTick and clear all enabled/pending NVIC interrupts left by ROM ISP bootloader */
  SysTick->CTRL = 0U;
  SysTick->VAL  = 0U;
  for (uint32_t i = 0U; i < 8U; i++)
  {
      NVIC->ICER[i] = 0xFFFFFFFFUL;
      NVIC->ICPR[i] = 0xFFFFFFFFUL;
  }
```

---

## 4. 解析 `.bin` 与 ISP 下载到 RAM 运行命令

### 步骤 1：使用 `parse-bin.py` 解析 `.bin` 向量表并生成命令

```powershell
python parse-bin.py D:\NXP\work-prj\kw45b41zevk_freertos_hello\freertos_hello.bin
```

输出示例：
```text
=== ARM Cortex-M Binary Vector Table Info ===
Binary File             : D:\NXP\work-prj\kw45b41zevk_freertos_hello\freertos_hello.bin
File Size               : 22728 bytes (22.20 KiB)
Initial SP (Word 0)     : 0x30018000
Reset_Handler (Word 1)  : 0x3000847D (Thumb bit = 1)
Target Load Address     : 0x30008000
```

### 步骤 2：通过 `blhost` 写入 RAM 并跳转执行

让 KW45 进入 ISP 模式（按住 SW4 / PTA4 复位），依次执行以下两条命令：

```powershell
# 1. 将编译好的 RAM 镜像写入 Secure SRAM 0x30008000
blhost -cb "pcan,PCAN_USBBUS1" write-memory 0x30008000 "D:\NXP\work-prj\kw45b41zevk_freertos_hello\freertos_hello.bin"

# 2. 跳转执行 (注意第 2 个参数 <arg> 必须为镜像起始地址 0x30008000)
blhost -cb "pcan,PCAN_USBBUS1" execute 0x3000847D 0x30008000 0x30018000
```

---

## 5. 常见问题排障表

| 现象 / 报错信息 | 根因分析 | 解决办法 |
| :--- | :--- | :--- |
| `execute` 返回 `10006 (0x2716) Unsupported Command` | `blhost execute <ADDRESS> <ARG> <SP>` 的第 2 个参数 `<ARG>` 填了 `0`，或 `<ADDRESS>` / `<SP>` 与 `<ARG>` 起始处的向量表头两个 Word 不一致 | `<ARG>` 必须填入镜像加载首地址（如 `0x30008000`），`<ADDRESS>` 填 `Word 1 (0x3000847D)`，`<SP>` 填 `Word 0 (0x30018000)` |
| `write-memory` 返回 `10200 (0x27d8) MemoryRangeInvalid` | 写入地址落在了 ROM 占用的保留区 (`0x20000000 - 0x20003FFF` / `0x30000000 - 0x30003FFF`) | 将链接脚本起始地址设在保留区之后（如 `0x30008000`） |
| `execute` 返回 `0 (0x0) Success` 但进入 `BusFault` (`CFSR = 0x8200`) | 访问了未初始化硬件 ECC 的 SRAM 区域（如高地址 `STCM` 堆栈区） | 在 `startup_KW45B41Z83.S` 的 `Reset_Handler` 中先用 `stmia r0!, {r2 - r5}` 对 `m_data1` 区域进行 128-bit 对齐清零以初始化 ECC |
| 跳转后立即卡死在 `DefaultISR` | ROM Bootloader 在 ISP 期间开启的外设中断（如 `CAN0`）未关闭，启动代码执行 `cpsie i` 后立即触发未知中断 | 在 `SystemInit()` 入口清零 `SysTick->CTRL` 并向 `NVIC->ICER[0..7]` 和 `NVIC->ICPR[0..7]` 写 `0xFFFFFFFF` |

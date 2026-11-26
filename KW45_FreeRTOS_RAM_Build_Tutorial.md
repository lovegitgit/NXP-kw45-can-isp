# KW45 FreeRTOS Secure RAM 编译与启动指南

本指南说明如何将官方 NXP SDK 示例 `kw45b41zevk_freertos_hello`（tested on **MCUXpresso SDK 26.06**）打补丁链接到 Secure SRAM (`0x30005000`)，并生成下载与启动脚本。

---

## 1. 获取或应用 RAM 补丁

仓库根目录已提供完整补丁与预编译产物，任选其一即可：
- **直接体验**：直接使用仓库根目录的预编译固件 [freertos_hello.bin](freertos_hello.bin)。
- **源码打补丁**：需先在 **MCUXpresso for VS Code** 中导入 **Standalone 模式** 的 `kw45b41zevk_freertos_hello` 示例工程（tested on **MCUXpresso SDK 26.06**，确保 `devices/` 与 `flags.cmake` 等均在工程目录内），随后在工程根目录执行：
  ```powershell
  git apply --check .\0001-link-to-ram-run.patch
  git apply .\0001-link-to-ram-run.patch
  ```

### 补丁核心原理

- 将代码段重定位至 Secure SRAM (`0x30005000`，避开 ROM 保留区与 `0x30004000` `comm_buffer`)，数据与堆栈段置于 `0x30010000 ~ 0x3001C000`；
- 在 RAM 启动分支补全数据段 128-bit ECC 初始化，避免压栈触发 `BusFault`；
- 在 `SystemInit()` 入口清理 ROM ISP 残留的 `SysTick` 与 `NVIC` 中断状态。

> **说明（非 link-to-RAM 核心补丁）**：为了便于通过串口直观验证任务在 RAM 中持续调度运行，示例中的 `freertos_hello.c` 已将主循环任务改写如下（先延时等待 UART 稳定再打印，避免首行 `count 0` 乱码）：
> ```c
> static void hello_task(void *pvParameters)
> {
>     static uint32_t count = 0;
>     for (;;)
>     {
>         vTaskDelay(pdMS_TO_TICKS(1000));
>         PRINTF("Hello world count %d.\r\n", count++);
>     }
> }
> ```

---

## 2. 生成下载命令与 SB3.1 容器

在 `can-isp` 仓库根目录打开 Windows PowerShell，直接引用当前目录的 `.\freertos_hello.bin`（或替换为你新编译出的路径）：

### 方式一：原始 RAM BIN / 签名 MBI 下载（ISP / J-Link / 一键 `.bat`）
```powershell
# 原始 RAM BIN 模式（生成 freertos_hello.jlink 和 freertos_hello_run.bat）
python .\make-ram-boot.py .\freertos_hello.bin

# 签名 MBI 模式（默认使用 keys/ 签名，生成 freertos_hello_mbi.bin 与 freertos_hello_mbi.jlink，适用于 Secure Boot 下 J-Link RAM 运行）
python .\make-ram-boot.py .\freertos_hello.bin --mbi
```
脚本将输出 BusPal CAN、Direct PCAN、UART 的 `write-memory` + `execute` 指令，并在当前目录生成 `.jlink` 与一键运行脚本 `*_run.bat`。

### 方式二：一步生成 SB3.1 安全容器并执行
```powershell
python .\make-ram-boot.py .\freertos_hello.bin --sb3
```
脚本自动使用 `keys/` 下的原厂密钥生成 `freertos_hello.sb3` 与 `freertos_hello_sb3_run.bat`。容器内已包含 `load 0x30005000` 和 `execute 0x30005000`，执行 `blhost -cb "pcan,PCAN_USBBUS1" receive-sb-file .\freertos_hello.sb3` 即可一步完成验签、加载并运行。

> **密钥生成说明**：`keys/` 目录默认为 NXP 原厂测试密钥。若客户已开启 Secure Boot，可通过 NXP 原版 [dm-kw45-secure-boot-scripts](https://github.com/nxp-appcodehub/dm-kw45-secure-boot-scripts) 生成自有密钥并替换到 `keys/` 目录（或用 `-k` 指定目录）。

---

## 3. 验证与常见问题

启动后在 Debug UART（115200 baud，8N1）可观察到每秒递增输出 `Hello world count 0.`、`Hello world count 1.` ...。若需 GDB 调试或排查启动故障，参见 [KW45_ISP_RAM_Boot_Guide.md](KW45_ISP_RAM_Boot_Guide.md)。

| 现象 | 优先检查 |
| :--- | :--- |
| 构建产物的向量表仍在 `0x00000000` | 确认补丁已生效，检查构建输出中的 `-T` 是否指向 `KW45B41Z83xxxA_ram.ld`。 |
| ISP `execute` 返回 `10006` | 确认第二个参数 `<arg>` 等于镜像基地址 `0x30005000`（而非 `0`）。 |
| 跳转后出现 `BusFault` 或 `DefaultISR` | 检查 `startup_KW45B41Z83.S` 的 ECC 初始化范围与 `SystemInit()` 清理逻辑是否完整。 |

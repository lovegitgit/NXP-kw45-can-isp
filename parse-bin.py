#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""Parse Cortex-M vectors and generate blhost commands and a KW45 J-Link command file."""

import argparse
import os
from pathlib import Path
import struct
import sys

from kw45_ram_jlink import write_ram_command_file


def parse_bin_vector(bin_path: str, load_addr: str, uart_port: str = "COM3") -> int:
    if not os.path.isfile(bin_path):
        print(f"[ERROR] File not found: {bin_path}", file=sys.stderr)
        return 1

    file_size = os.path.getsize(bin_path)
    if file_size < 8:
        print(f"[ERROR] File too small ({file_size} bytes), need at least 8 bytes.", file=sys.stderr)
        return 1

    with open(bin_path, "rb") as f:
        header = f.read(8)

    sp, pc = struct.unpack("<II", header)
    thumb_bit = pc & 1
    vector_base = pc & 0xFFFF8000

    # Auto-infer load address from PC if not explicitly provided
    if load_addr:
        target_addr = int(load_addr, 0)
    else:
        target_addr = vector_base

    print("=== ARM Cortex-M Binary Vector Table Info ===")
    print(f"Binary File             : {bin_path}")
    print(f"File Size               : {file_size} bytes ({file_size / 1024:.2f} KiB)")
    print(f"Initial SP (Word 0)     : 0x{sp:08X}")
    print(f"Reset_Handler (Word 1)  : 0x{pc:08X} (Thumb bit = {thumb_bit})")
    print(f"Target Load Address     : 0x{target_addr:08X}")

    if thumb_bit == 0:
        print("\n[WARNING] Reset_Handler LSB (Thumb bit) is 0! Cortex-M requires bit 0 = 1.")

    if target_addr < 0x04000000:
        print("\n[NOTE] This binary appears to be linked for Flash (0x00000000), not RAM!")
    else:
        print("\n[OK] This binary is linked for RAM execution.")

    print("\n--- C++ blhost.exe 2.6.7 Commands (BusPal CAN via COM3) ---")
    print(f".\\blhost.exe -b can -p COM3 write-memory 0x{target_addr:08X} \"{bin_path}\"")
    print(f".\\blhost.exe -b can -p COM3 execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}")
    print("[NOTE] SPSDK 3.1.0 supports BusPal SPI/I2C. For CAN, use the native CAN commands below.")

    print("\n--- Ready-to-use SPSDK blhost Commands (Direct PCAN) ---")
    print(f"blhost -cb \"pcan,PCAN_USBBUS1\" write-memory 0x{target_addr:08X} \"{bin_path}\"")
    print(f"blhost -cb \"pcan,PCAN_USBBUS1\" execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}")

    print("\n--- Ready-to-use SPSDK blhost Commands (Direct UART) ---")
    print(f"blhost -p \"{uart_port}\" write-memory 0x{target_addr:08X} \"{bin_path}\"")
    print(f"blhost -p \"{uart_port}\" execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}")

    if not 0x30008000 <= target_addr < 0x30010000:
        print("\n[NOTE] J-Link command files are generated for KW45 Secure RAM images.")
        return 0

    image_end = target_addr + file_size
    if image_end > 0x30010000 or target_addr % 0x400:
        print("[ERROR] J-Link requires an aligned image within the KW45 Secure RAM image area.", file=sys.stderr)
        return 1
    if not thumb_bit or not target_addr <= (pc & ~1) < image_end:
        print("[ERROR] Reset_Handler must be a Thumb entry in the image.", file=sys.stderr)
        return 1
    if sp % 8 or not 0x30010000 < sp <= 0x30018000:
        print("[ERROR] Initial MSP must be aligned and in the KW45 Secure RAM stack area.", file=sys.stderr)
        return 1
    image = Path(bin_path)
    jlink = Path(image.with_suffix(".jlink").name)
    try:
        write_ram_command_file(jlink, image, target_addr, sp, pc)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] Could not write J-Link command file: {exc}", file=sys.stderr)
        return 1

    print("\n--- Ready-to-use J-Link Commander Command ---")
    print(f'JLink.exe ".\\{jlink.name}"')
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse ARM Cortex-M .bin image header (Initial SP and Reset_Handler PC)."
    )
    parser.add_argument(
        "bin_file",
        help="Path to the .bin file (e.g. freertos_hello.bin)",
    )
    parser.add_argument(
        "-a",
        "--addr",
        default=None,
        help="Optional RAM load address (e.g. 0x20008000). Auto-detected from PC if omitted.",
    )
    parser.add_argument(
        "--uart-port",
        default="COM3",
        help="Direct UART port and optional baud rate (e.g. COM7,115200). Default: COM3.",
    )
    args = parser.parse_args()
    sys.exit(parse_bin_vector(args.bin_file, args.addr, args.uart_port))


if __name__ == "__main__":
    main()

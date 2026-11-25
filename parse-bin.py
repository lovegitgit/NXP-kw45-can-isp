#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""Parse ARM Cortex-M binary image vector table (SP & Reset_Handler PC) and generate blhost commands."""

import argparse
import os
import struct
import sys


def parse_bin_vector(bin_path: str, load_addr: str) -> int:
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

    print("\n--- Ready-to-use blhost Commands (BusPal COM3) ---")
    print(f".\\blhost.exe -b can -p COM3 write-memory 0x{target_addr:08X} \"{bin_path}\"")
    print(f".\\blhost.exe -b can -p COM3 execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}")

    print("\n--- Ready-to-use SPSDK blhost Commands (Direct PCAN) ---")
    print(f"blhost -cb \"pcan,PCAN_USBBUS1\" write-memory 0x{target_addr:08X} \"{bin_path}\"")
    print(f"blhost -cb \"pcan,PCAN_USBBUS1\" execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}")
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
    args = parser.parse_args()
    sys.exit(parse_bin_vector(args.bin_file, args.addr))


if __name__ == "__main__":
    main()

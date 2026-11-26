#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""Unified KW45 RAM boot tool: raw RAM BIN, signed/plain MBI (--mbi), and SB3.1 (--sb3).

Parses Cortex-M vector tables from a RAM-linked .bin image and generates
blhost (BusPal CAN / Direct PCAN / Direct UART), J-Link, and one-click
Windows batch scripts for:
  1. Raw RAM binary execution (default)
  2. NXP Master Boot Image packaging (--mbi, default signed for Secure Boot J-Link)
  3. NXP Secure Binary v3.1 packaging with keys/ (--sb3)
"""

import argparse
import os
from pathlib import Path
import struct
import sys

from spsdk.image.mbi.mbi import MasterBootImage
from spsdk.sbfile.sb31.images import SecureBinary31
from spsdk.utils.config import Config


DEFAULT_KEYS_DIR = Path(__file__).resolve().parent / "keys"


def generate_mbi_ram(
    input_bin: Path,
    output_mbi: Path,
    base_addr: int,
    keys_dir: Path,
    family: str = "kw45b41z8",
    auth_type: str = "signed",
) -> bytes:
    """Package a RAM-linked binary into NXP Master Boot Image (MBI) format."""
    mbi_config = {
        "family": family,
        "outputImageExecutionTarget": "xip",
        "outputImageAuthenticationType": auth_type,
        "outputImageExecutionAddress": hex(base_addr),
        "inputImageFile": input_bin.resolve().as_posix(),
        "masterBootOutputFile": output_mbi.resolve().as_posix(),
        "outputImageSubtype": "main",
        "firmwareVersion": 0,
    }

    search_paths = [str(input_bin.resolve().parent)]

    if auth_type == "signed":
        rot_pem = (keys_dir / "ROT1_p384.pem").resolve()
        rot_cert_block = (keys_dir / "ROT1_cert_block.bin").resolve()
        for key_file in (rot_pem, rot_cert_block):
            if not key_file.is_file():
                raise FileNotFoundError(f"Required KW45 key file missing: {key_file}")
        mbi_config["certBlock"] = rot_cert_block.as_posix()
        mbi_config["signer"] = f"type=file;file_path={rot_pem.as_posix()}"
        search_paths.append(str(keys_dir.resolve()))
    else:
        mbi_config["enableTrustZone"] = False

    cfg = Config(mbi_config)
    cfg.search_paths = search_paths

    mbi_obj = MasterBootImage.load_from_config(cfg)
    mbi_data = mbi_obj.export()

    output_mbi.write_bytes(mbi_data)
    return mbi_data


def generate_sb3_ram(
    input_bin: Path,
    output_sb3: Path,
    load_addr: int,
    keys_dir: Path,
    family: str = "kw45b41z8",
    fw_version: int = 0,
    kdk_access_rights: int = 3,
    description: str = "KW45B41Z8 SB3",
) -> bytes:
    """Package a RAM-linked binary into signed/encrypted SB3.1 container."""
    rot_pem = (keys_dir / "ROT1_p384.pem").resolve()
    rot_cert_block = (keys_dir / "ROT1_cert_block.bin").resolve()
    sb3kdk = (keys_dir / "sb3kdk.txt").resolve()

    for key_file in (rot_pem, rot_cert_block, sb3kdk):
        if not key_file.is_file():
            raise FileNotFoundError(f"Required KW45 key file missing: {key_file}")

    sb3_config = {
        "family": family,
        "firmwareVersion": fw_version,
        "containerOutputFile": output_sb3.resolve().as_posix(),
        "signer": f"type=file;file_path={rot_pem.as_posix()}",
        "certBlock": rot_cert_block.as_posix(),
        "containerKeyBlobEncryptionKey": sb3kdk.as_posix(),
        "kdkAccessRights": kdk_access_rights,
        "description": description,
        "commands": [
            {
                "checkFwVersion": {
                    "value": fw_version,
                    "counterId": "secure",
                }
            },
            {
                "load": {
                    "address": load_addr,
                    "file": input_bin.resolve().as_posix(),
                }
            },
            {
                "execute": {
                    "address": load_addr,
                }
            },
        ],
    }

    cfg = Config(sb3_config)
    cfg.search_paths = [str(keys_dir.resolve()), str(input_bin.resolve().parent)]

    sb3 = SecureBinary31.load_from_config(cfg)
    sb3_data = sb3.export()
    output_sb3.write_bytes(sb3_data)
    return sb3_data


def write_ram_command_file(output: Path, image: Path, base: int, sp: int, reset: int) -> None:
    """Write a KW45 Secure SRAM J-Link Commander script."""
    image = image.resolve()
    if output.resolve() == image or (output.exists() and output.samefile(image)):
        raise ValueError("Output command file must differ from the input BIN")
    image_path = image.as_posix()
    if any(character in image_path for character in '"\r\n'):
        raise ValueError("BIN path cannot contain a double quote or line break")
    output.write_text(
        f"""// KW45 Secure SRAM application download and execution.
ExitOnError 1
device KW45B41Z83
si SWD
speed 4000
connect
r
h
LoadFile "{image_path}" 0x{base:08X} noreset
VerifyBin "{image_path}" 0x{base:08X}
Mem32 0x{base:08X} 2
WReg CONTROL 0x00000000
W4 0xE000ED08 0x{base:08X}
WReg MSP 0x{sp:08X}
SetPC 0x{reset & ~1:08X}
WReg XPSR 0x01000000
Regs
g
Exit
""",
        encoding="utf-8",
    )


def write_flash_bat(
    output_bat: Path,
    target_bin_arg: str,
    base_addr: int,
    sp: int,
    pc: int,
    pcan_channel: str = "PCAN_USBBUS1",
    mode: str = "raw",
) -> None:
    """Write a Windows batch script for one-click RAM download and execution via blhost."""
    if mode == "sb3":
        content = f"""@echo off
rem ==============================================================================
rem One-Click SB3.1 Upload & Execute Script for KW45 via blhost (PCAN)
rem Generated automatically by make-ram-boot.py
rem ==============================================================================

echo Uploading and executing {target_bin_arg}...
blhost -cb "pcan,{pcan_channel}" receive-sb-file "{target_bin_arg}"
if %errorlevel% neq 0 (
    echo [ERROR] receive-sb-file failed!
    exit /b %errorlevel%
)

echo [SUCCESS] SB3 image loaded and executed in RAM!
pause
"""
    else:
        mode_label = "MBI RAM Image" if mode == "mbi" else "Raw RAM Image"
        content = f"""@echo off
rem ==============================================================================
rem One-Click {mode_label} Run Script for KW45 via blhost (PCAN)
rem Generated automatically by make-ram-boot.py
rem ==============================================================================

echo [1/2] Downloading image to RAM (0x{base_addr:08X})...
blhost -cb "pcan,{pcan_channel}" write-memory 0x{base_addr:08X} "{target_bin_arg}"
if %errorlevel% neq 0 (
    echo [ERROR] write-memory failed!
    exit /b %errorlevel%
)

echo [2/2] Executing image from RAM (PC=0x{pc:08X}, Base=0x{base_addr:08X}, SP=0x{sp:08X})...
blhost -cb "pcan,{pcan_channel}" execute 0x{pc:08X} 0x{base_addr:08X} 0x{sp:08X}
if %errorlevel% neq 0 (
    echo [ERROR] execute command failed!
    exit /b %errorlevel%
)

echo [SUCCESS] Application is now running in RAM!
pause
"""
    output_bat.write_text(content, encoding="utf-8")


def process_ram_image(
    bin_path: str,
    load_addr: str = None,
    use_mbi: bool = False,
    use_sb3: bool = False,
    family: str = "kw45b41z8",
    auth_type: str = "signed",
    keys_dir: str = str(DEFAULT_KEYS_DIR),
    pcan_channel: str = "PCAN_USBBUS1",
    uart_port: str = "COM3",
) -> int:
    if not os.path.isfile(bin_path):
        print(f"[ERROR] File not found: {bin_path}", file=sys.stderr)
        return 1

    input_path = Path(bin_path)
    file_size = input_path.stat().st_size
    if file_size < 8:
        print(f"[ERROR] File too small ({file_size} bytes), need at least 8 bytes.", file=sys.stderr)
        return 1

    header = input_path.read_bytes()[:8]
    sp, pc = struct.unpack("<II", header)
    thumb_bit = pc & 1
    vector_base = pc & 0xFFFFF000
    target_addr = int(load_addr, 0) if load_addr else vector_base

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

    cwd = Path.cwd()
    stem = input_path.stem
    keys_path = Path(keys_dir)

    if use_sb3:
        if not keys_path.is_dir():
            print(f"[ERROR] Keys directory not found: {keys_path}", file=sys.stderr)
            return 1

        output_sb3 = cwd / f"{stem}.sb3"
        sb3_data = generate_sb3_ram(
            input_bin=input_path,
            output_sb3=output_sb3,
            load_addr=target_addr,
            keys_dir=keys_path,
            family=family,
        )
        bat_path = cwd / f"{stem}_sb3_run.bat"
        print(f"\n[OK] SB3.1 Mode Enabled ({family})")
        print(f"[OK] Keys Directory     : {keys_path.resolve()}")
        print(f"[OK] Generated SB3 File : {output_sb3.name} ({len(sb3_data)} bytes)")

        print("\n--- C++ blhost.exe 2.6.7 Command (BusPal CAN via COM3) ---")
        print(f'.\\blhost.exe -b can -p COM3 receive-sb-file "{output_sb3.name}"')

        print("\n--- Ready-to-use SPSDK blhost Command (Direct PCAN) ---")
        print(f'blhost -cb "pcan,{pcan_channel}" receive-sb-file "{output_sb3.name}"')

        print("\n--- Ready-to-use SPSDK blhost Command (Direct UART) ---")
        print(f'blhost -p "{uart_port}" receive-sb-file "{output_sb3.name}"')

        try:
            write_flash_bat(bat_path, output_sb3.name, target_addr, sp, pc, pcan_channel, mode="sb3")
            print("\n--- One-Click Windows Batch Script ---")
            print(f".\\{bat_path.name}")
        except OSError as exc:
            print(f"[ERROR] Could not write batch script: {exc}", file=sys.stderr)
            return 1
        return 0

    if use_mbi:
        if auth_type == "signed" and not keys_path.is_dir():
            print(f"[ERROR] Keys directory not found: {keys_path}", file=sys.stderr)
            return 1
        output_mbi = cwd / f"{stem}_mbi.bin"
        mbi_data = generate_mbi_ram(
            input_bin=input_path,
            output_mbi=output_mbi,
            base_addr=target_addr,
            keys_dir=keys_path,
            family=family,
            auth_type=auth_type,
        )
        target_image = output_mbi
        target_bin_arg = output_mbi.name
        file_size = len(mbi_data)
        jlink_path = cwd / f"{stem}_mbi.jlink"
        bat_path = cwd / f"{stem}_mbi_run.bat"
        mode = "mbi"
        print(f"\n[OK] MBI Mode Enabled ({family}, auth={auth_type})")
        if auth_type == "signed":
            print(f"[OK] Keys Directory     : {keys_path.resolve()}")
        print(f"[OK] Generated MBI File : {output_mbi.name} ({file_size} bytes)")
    else:
        target_image = input_path
        target_bin_arg = bin_path
        jlink_path = cwd / f"{stem}.jlink"
        bat_path = cwd / f"{stem}_run.bat"
        mode = "raw"

    print("\n--- C++ blhost.exe 2.6.7 Commands (BusPal CAN via COM3) ---")
    print(f'.\\blhost.exe -b can -p COM3 write-memory 0x{target_addr:08X} "{target_bin_arg}"')
    print(f'.\\blhost.exe -b can -p COM3 execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}')
    print("[NOTE] SPSDK 3.1.0 supports BusPal SPI/I2C. For CAN, use the native CAN commands below.")

    print("\n--- Ready-to-use SPSDK blhost Commands (Direct PCAN) ---")
    print(f'blhost -cb "pcan,{pcan_channel}" write-memory 0x{target_addr:08X} "{target_bin_arg}"')
    print(f'blhost -cb "pcan,{pcan_channel}" execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}')

    print("\n--- Ready-to-use SPSDK blhost Commands (Direct UART) ---")
    print(f'blhost -p "{uart_port}" write-memory 0x{target_addr:08X} "{target_bin_arg}"')
    print(f'blhost -p "{uart_port}" execute 0x{pc:08X} 0x{target_addr:08X} 0x{sp:08X}')

    try:
        write_flash_bat(bat_path, target_bin_arg, target_addr, sp, pc, pcan_channel, mode=mode)
        print("\n--- One-Click Windows Batch Script ---")
        print(f".\\{bat_path.name}")
    except OSError as exc:
        print(f"[ERROR] Could not write batch script: {exc}", file=sys.stderr)
        return 1

    if not 0x30005000 <= target_addr < 0x30010000:
        print("\n[NOTE] J-Link command files are generated for KW45 Secure RAM images.")
        return 0

    image_end = target_addr + file_size
    if image_end > 0x30010000 or target_addr % 0x400:
        print("[ERROR] J-Link requires an aligned image within the KW45 Secure RAM image area.", file=sys.stderr)
        return 1
    if not thumb_bit or not target_addr <= (pc & ~1) < image_end:
        print("[ERROR] Reset_Handler must be a Thumb entry in the image.", file=sys.stderr)
        return 1
    if sp % 8 or not 0x30010000 < sp <= 0x3001C000:
        print("[ERROR] Initial MSP must be aligned and in the KW45 Secure RAM stack area.", file=sys.stderr)
        return 1

    try:
        write_ram_command_file(jlink_path, target_image, target_addr, sp, pc)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] Could not write J-Link command file: {exc}", file=sys.stderr)
        return 1

    print("\n--- Ready-to-use J-Link Commander Command ---")
    print(f'JLink.exe ".\\{jlink_path.name}"')
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified KW45 RAM boot tool: raw BIN (default), MBI (--mbi), or SB3.1 (--sb3)."
    )
    parser.add_argument(
        "bin_file",
        help="Path to the RAM-linked .bin file (e.g. freertos_hello.bin)",
    )
    parser.add_argument(
        "-a",
        "--addr",
        default=None,
        help="Optional RAM load address (e.g. 0x30008000). Auto-detected from PC if omitted.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mbi",
        action="store_true",
        help="Package the input RAM binary into NXP MBI format (*_mbi.bin, default signed for Secure Boot J-Link).",
    )
    mode_group.add_argument(
        "--sb3",
        action="store_true",
        help="Package the input RAM binary into signed/encrypted SB3.1 (*.sb3) using keys/.",
    )
    parser.add_argument(
        "-f",
        "--family",
        default="kw45b41z8",
        help="MCU family for --mbi / --sb3 (default: kw45b41z8).",
    )
    parser.add_argument(
        "--auth",
        choices=["signed", "plain", "crc"],
        default="signed",
        help="MBI authentication type when --mbi is set (default: signed).",
    )
    parser.add_argument(
        "-k",
        "--keys-dir",
        default=str(DEFAULT_KEYS_DIR),
        help=f"Directory containing KW45 keys for --mbi/--sb3 (default: {DEFAULT_KEYS_DIR}).",
    )
    parser.add_argument(
        "-c",
        "--channel",
        default="PCAN_USBBUS1",
        help="PCAN channel name for blhost (default: PCAN_USBBUS1).",
    )
    parser.add_argument(
        "--uart-port",
        default="COM3",
        help="Direct UART port and optional baud rate (e.g. COM7,115200). Default: COM3.",
    )
    args = parser.parse_args()

    sys.exit(
        process_ram_image(
            bin_path=args.bin_file,
            load_addr=args.addr,
            use_mbi=args.mbi,
            use_sb3=args.sb3,
            family=args.family,
            auth_type=args.auth,
            keys_dir=args.keys_dir,
            pcan_channel=args.channel,
            uart_port=args.uart_port,
        )
    )


if __name__ == "__main__":
    main()

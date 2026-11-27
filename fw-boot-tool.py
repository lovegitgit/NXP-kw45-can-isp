#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""Generic ARM Cortex-M firmware (.elf / .bin) analyzer and boot command generator.

Pure Python standard library implementation (no external tools or packages required).
Supports both .elf and .bin inputs:
  1. Parses Cortex-M vector table (Initial SP, Reset_Handler, Load Address)
  2. Computes exact Flash & RAM usage (.text/.data/.bss/.heap/.stack) from ELF
  3. Generates ISP (blhost BusPal CAN / Direct PCAN / Direct UART) commands & .bat
  4. Generates J-Link Commander script (.jlink) with automatic PC/MSP/VTOR setup
"""

import argparse
from pathlib import Path
import struct
import sys


ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1
PT_LOAD = 1
SHT_NULL = 0
SHT_NOBITS = 8
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4


def format_size(num_bytes: int) -> str:
    return f"{num_bytes:8d} B ({num_bytes / 1024:6.2f} KiB)"


def is_elf_file(path: Path) -> bool:
    """Detect ELF-format firmware (.elf, .axf, .out) by magic header."""
    with open(path, "rb") as f:
        return f.read(4) == ELF_MAGIC


def is_ram_address(addr: int) -> bool:
    """Check if address lies in Cortex-M SRAM / TCM regions."""
    if 0x20000000 <= addr < 0x40000000:
        return True
    if 0x04000000 <= addr < 0x04040000 or 0x14000000 <= addr < 0x14040000:
        return True
    return False


def parse_bin_info(bin_path: Path, addr_override: int = None) -> dict:
    raw = bin_path.read_bytes()
    file_size = len(raw)
    if file_size < 8:
        raise ValueError(f"File too small ({file_size} bytes), need at least 8 bytes.")

    sp, pc = struct.unpack("<II", raw[:8])
    thumb_bit = pc & 1
    inferred_base = (pc & ~1) & 0xFFFFF000

    mbi_info = None
    if file_size >= 0x38:
        mbi_len, mbi_flags, mbi_crc_or_cert = struct.unpack("<III", raw[0x20:0x2C])
        mbi_load_addr = struct.unpack("<I", raw[0x34:0x38])[0]
        if mbi_len > 0 and mbi_len <= file_size + 0x1000 and (mbi_load_addr == 0 or (mbi_load_addr & 0x3FF) == 0):
            mbi_info = {
                "length": mbi_len,
                "flags": mbi_flags,
                "crc_or_cert": mbi_crc_or_cert,
                "load_addr": mbi_load_addr,
            }
            if mbi_load_addr != 0:
                inferred_base = mbi_load_addr

    load_addr = addr_override if addr_override is not None else inferred_base
    return {
        "load_addr": load_addr,
        "bin_image_size": file_size,
        "sp": sp,
        "pc": pc,
        "entry": pc,
        "thumb_bit": thumb_bit,
        "is_ram_image": is_ram_address(load_addr),
        "mbi": mbi_info,
    }


def parse_elf32(elf_path: Path) -> dict:
    """Parse ELF32 little-endian headers, vector table, loadable bytes, and Flash/RAM usage."""
    data = elf_path.read_bytes()
    if len(data) < 52 or data[:4] != ELF_MAGIC:
        raise ValueError("Not a valid ELF file.")
    if data[4] != ELFCLASS32 or data[5] != ELFDATA2LSB:
        raise ValueError("Only 32-bit little-endian ARM Cortex-M ELF files are supported.")

    e_entry, e_phoff, e_shoff = struct.unpack_from("<III", data, 24)
    e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHHHH", data, 42)

    if e_shoff == 0 or e_shnum == 0 or e_shstrndx >= e_shnum:
        raise ValueError("ELF file has no valid section header table.")

    load_segments = []
    pt_load_flash = 0
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack_from("<8I", data, off)
        if p_type == PT_LOAD and p_filesz > 0:
            load_segments.append({
                "offset": p_offset,
                "vaddr": p_vaddr,
                "paddr": p_paddr,
                "filesz": p_filesz,
                "memsz": p_memsz,
            })
            if not is_ram_address(p_paddr):
                pt_load_flash += p_filesz

    if not load_segments:
        raise ValueError("ELF file contains no loadable PT_LOAD segments.")

    load_segments.sort(key=lambda seg: seg["paddr"])
    first_seg = load_segments[0]
    min_lma = first_seg["paddr"]
    max_lma = max(seg["paddr"] + seg["filesz"] for seg in load_segments)
    bin_image_size = max_lma - min_lma

    vec_off = first_seg["offset"]
    if vec_off + 8 > len(data):
        raise ValueError("First PT_LOAD segment is truncated (< 8 bytes).")
    sp, pc = struct.unpack_from("<II", data, vec_off)
    thumb_bit = pc & 1

    mbi_info = None
    if first_seg["filesz"] >= 0x38 and vec_off + 0x38 <= len(data):
        mbi_len, mbi_flags, mbi_crc_or_cert = struct.unpack_from("<III", data, vec_off + 0x20)
        mbi_load_addr, = struct.unpack_from("<I", data, vec_off + 0x34)
        if mbi_len > 0 and mbi_len <= bin_image_size + 0x1000 and (mbi_load_addr == 0 or (mbi_load_addr & 0x3FF) == 0):
            mbi_info = {
                "length": mbi_len,
                "flags": mbi_flags,
                "crc_or_cert": mbi_crc_or_cert,
                "load_addr": mbi_load_addr,
            }

    shstr_off = e_shoff + e_shstrndx * e_shentsize
    _, _, _, _, strtab_offset, strtab_size, _, _, _, _ = struct.unpack_from("<10I", data, shstr_off)
    strtab = data[strtab_offset : strtab_offset + strtab_size]

    def get_name(offset: int) -> str:
        end = strtab.find(b"\x00", offset)
        if end == -1:
            end = len(strtab)
        return strtab[offset:end].decode("utf-8", errors="replace")

    sections = []
    flash_sec_total = 0
    ram_code_bytes = 0
    ram_data_bytes = 0
    ram_bss_bytes = 0
    ram_heap_bytes = 0
    ram_stack_bytes = 0

    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, _, _, _, _ = struct.unpack_from("<10I", data, off)
        if sh_type == SHT_NULL or not (sh_flags & SHF_ALLOC) or sh_size == 0:
            continue

        name = get_name(sh_name)
        is_nobits = (sh_type == SHT_NOBITS)
        in_ram = is_ram_address(sh_addr)

        lma = sh_addr
        if not is_nobits:
            for seg in load_segments:
                if seg["vaddr"] <= sh_addr < seg["vaddr"] + seg["filesz"]:
                    lma = seg["paddr"] + (sh_addr - seg["vaddr"])
                    break

        sections.append({
            "name": name,
            "vaddr": sh_addr,
            "lma": lma,
            "size": sh_size,
            "nobits": is_nobits,
            "in_ram": in_ram,
        })

        lower_name = name.lower()
        if is_nobits:
            if "heap" in lower_name:
                ram_heap_bytes += sh_size
            elif "stack" in lower_name:
                ram_stack_bytes += sh_size
            else:
                ram_bss_bytes += sh_size
        else:
            if in_ram:
                if (sh_flags & SHF_EXECINSTR) or "interrupt" in lower_name or "text" in lower_name or "rodata" in lower_name:
                    ram_code_bytes += sh_size
                else:
                    ram_data_bytes += sh_size
            else:
                flash_sec_total += sh_size

    ram_static_bytes = ram_code_bytes + ram_data_bytes + ram_bss_bytes
    ram_total_bytes = ram_static_bytes + ram_heap_bytes + ram_stack_bytes

    return {
        "raw_elf": data,
        "load_segments": load_segments,
        "entry": e_entry,
        "load_addr": min_lma,
        "bin_image_size": bin_image_size,
        "sp": sp,
        "pc": pc,
        "thumb_bit": thumb_bit,
        "is_ram_image": is_ram_address(min_lma),
        "mbi": mbi_info,
        "sections": sections,
        "flash_bytes": pt_load_flash if pt_load_flash > 0 else flash_sec_total,
        "ram_code_bytes": ram_code_bytes,
        "ram_data_bytes": ram_data_bytes,
        "ram_bss_bytes": ram_bss_bytes,
        "ram_heap_bytes": ram_heap_bytes,
        "ram_stack_bytes": ram_stack_bytes,
        "ram_static_bytes": ram_static_bytes,
        "ram_total_bytes": ram_total_bytes,
    }


def extract_bin_from_elf(elf_info: dict, out_bin: Path) -> None:
    """Extract contiguous binary image from ELF PT_LOAD segments."""
    min_lma = elf_info["load_addr"]
    size = elf_info["bin_image_size"]
    buf = bytearray(size)
    raw = elf_info["raw_elf"]
    for seg in elf_info["load_segments"]:
        dst = seg["paddr"] - min_lma
        src = seg["offset"]
        fsz = seg["filesz"]
        buf[dst : dst + fsz] = raw[src : src + fsz]
    out_bin.write_bytes(buf)


def build_jlink_commands(
    image: Path,
    base: int,
    sp: int,
    pc: int,
    is_ram: bool,
) -> str:
    """Build generic J-Link Commander internal commands (after manual connect)."""
    image_resolved = image.resolve()
    image_path = image_resolved.as_posix()
    is_elf = is_elf_file(image_resolved)

    if is_elf:
        # J-Link syntax: loadfile <filename> [, <Addr>] [, <noreset | reset>]
        # For ELF/AXF, passing noreset as the 2nd arg fails <Addr> parsing; use single-arg LoadFile.
        load_lines = f'LoadFile "{image_path}"\nh\n'
    elif is_ram:
        load_lines = (
            f'LoadFile "{image_path}" 0x{base:08X} noreset\n'
            f'VerifyBin "{image_path}" 0x{base:08X}\n'
        )
    else:
        load_lines = f'LoadFile "{image_path}" 0x{base:08X} noreset\n'

    return (
        f"r\n"
        f"h\n"
        f"{load_lines}"
        f"Mem32 0x{base:08X} 2\n"
        f"WReg CONTROL 0x00000000\n"
        f"W4 0xE000ED08 0x{base:08X}\n"
        f"WReg MSPLIM 0x00000000\n"
        f"WReg MSP 0x{sp:08X}\n"
        f"SetPC 0x{pc & ~1:08X}\n"
        f"WReg XPSR 0x01000000\n"
        f"Regs\n"
        f"g\n"
    )


def write_isp_bat(
    output_bat: Path,
    bin_arg: str,
    base_addr: int,
    image_size: int,
    sp: int,
    pc: int,
    is_ram: bool,
    pcan_channel: str,
) -> None:
    """Write Windows batch script for one-click blhost download & run."""
    if is_ram:
        content = f"""@echo off
rem ==============================================================================
rem One-Click RAM Image Download & Execute Script via blhost (PCAN)
rem Generated automatically by fw-boot-tool.py
rem ==============================================================================

echo [1/2] Downloading image to RAM (0x{base_addr:08X})...
blhost -cb "pcan,{pcan_channel}" write-memory 0x{base_addr:08X} "{bin_arg}"
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
    else:
        erase_size = (image_size + 0x1FFF) & ~0x1FFF
        content = f"""@echo off
rem ==============================================================================
rem One-Click Flash Image Programming & Reset Script via blhost (PCAN)
rem Generated automatically by fw-boot-tool.py
rem ==============================================================================

echo [1/3] Erasing Flash (0x{base_addr:08X}, size=0x{erase_size:X})...
blhost -cb "pcan,{pcan_channel}" flash-erase-region 0x{base_addr:08X} 0x{erase_size:X}
if %errorlevel% neq 0 (
    echo [ERROR] flash-erase-region failed!
    exit /b %errorlevel%
)

echo [2/3] Programming Flash (0x{base_addr:08X})...
blhost -cb "pcan,{pcan_channel}" write-memory 0x{base_addr:08X} "{bin_arg}"
if %errorlevel% neq 0 (
    echo [ERROR] write-memory failed!
    exit /b %errorlevel%
)

echo [3/3] Executing image (PC=0x{pc:08X}, Base=0x{base_addr:08X}, SP=0x{sp:08X})...
blhost -cb "pcan,{pcan_channel}" execute 0x{pc:08X} 0x{base_addr:08X} 0x{sp:08X}
if %errorlevel% neq 0 (
    echo [ERROR] execute command failed!
    exit /b %errorlevel%
)

echo [SUCCESS] Application is now running!
pause
"""
    output_bat.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generic ARM Cortex-M (.elf / .axf / .bin) info calculator and ISP / J-Link boot script generator."
    )
    parser.add_argument("firmware", help="Path to firmware .elf, .axf, .out, or .bin file")
    parser.add_argument(
        "-a",
        "--addr",
        default=None,
        help="Optional load address override for .bin (auto-detected from ELF or vector table if omitted).",
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
        help="Direct UART port for blhost (default: COM3).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show per-section VMA/LMA/size breakdown when ELF is available.",
    )
    args = parser.parse_args()

    input_path = Path(args.firmware)
    if not input_path.is_file():
        print(f"[ERROR] File not found: {input_path}", file=sys.stderr)
        return 1

    addr_override = int(args.addr, 0) if args.addr else None
    cwd = Path.cwd()
    stem = input_path.stem

    elf_path = None
    bin_path = None
    elf_info = None

    input_is_elf = is_elf_file(input_path)
    if input_is_elf:
        elf_path = input_path
        elf_info = parse_elf32(elf_path)
        info = elf_info
        if addr_override is not None:
            info["load_addr"] = addr_override
            info["is_ram_image"] = is_ram_address(addr_override)
        sibling_bin = elf_path.with_suffix(".bin")
        if sibling_bin.is_file():
            bin_path = sibling_bin
        else:
            bin_path = cwd / f"{stem}.bin"
            extract_bin_from_elf(elf_info, bin_path)
            print(f"[INFO] Extracted {bin_path.name} from ELF for blhost write-memory.")
    else:
        bin_path = input_path
        info = parse_bin_info(bin_path, addr_override=addr_override)
        for ext in (".elf", ".axf", ".out"):
            sibling_elf = bin_path.with_suffix(ext)
            if sibling_elf.is_file() and is_elf_file(sibling_elf):
                elf_path = sibling_elf
                elf_info = parse_elf32(elf_path)
                break

    load_addr = info["load_addr"]
    sp = info["sp"]
    pc = info["pc"]
    thumb_bit = info["thumb_bit"]
    image_size = info["bin_image_size"]
    is_ram = info["is_ram_image"]

    print("=== ARM Cortex-M Firmware Vector Table Info ===")
    print(f"Input File              : {input_path}")
    print(f"Load Image Size (.bin)  : {image_size} bytes ({image_size / 1024:.2f} KiB)")
    print(f"Initial SP (Word 0)     : 0x{sp:08X}")
    print(f"Reset_Handler (Word 1)  : 0x{pc:08X} (Thumb bit = {thumb_bit})")
    print(f"Target Load Address     : 0x{load_addr:08X}")
    print(f"Execution Target        : {'RAM (SRAM / TCM)' if is_ram else 'Flash (XIP)'}")

    if info["mbi"]:
        mbi = info["mbi"]
        print(
            f"NXP MBI Header          : Detected (Length={mbi['length']} B, "
            f"Flags=0x{mbi['flags']:08X}, LoadAddr=0x{mbi['load_addr']:08X})"
        )

    if thumb_bit == 0:
        print("[WARNING] Reset_Handler Thumb bit (LSB) is 0!")

    if elf_info is not None:
        print("\n=== Exact Flash / RAM Usage (from ELF) ===")
        print(f"ELF File                : {elf_path}")
        print(f"Flash Usage (Load)      : {format_size(elf_info['flash_bytes'])}")
        print(f"RAM Code/RO (if RAM)    : {format_size(elf_info['ram_code_bytes'])}")
        print(f"RAM Data (.data)        : {format_size(elf_info['ram_data_bytes'])}")
        print(f"RAM BSS  (.bss)         : {format_size(elf_info['ram_bss_bytes'])}")
        print(f"RAM Heap (.heap)        : {format_size(elf_info['ram_heap_bytes'])}")
        print(f"RAM Stack (.stack)      : {format_size(elf_info['ram_stack_bytes'])}")
        print("-" * 52)
        print(f"Total Static RAM        : {format_size(elf_info['ram_static_bytes'])}  (Code/RO + Data + BSS)")
        print(f"Total Reserved RAM      : {format_size(elf_info['ram_total_bytes'])}  (Static + Heap + Stack)")

        if args.verbose:
            print("\n--- Section Breakdown ---")
            print(f"{'Section':<20} {'VMA':<12} {'LMA':<12} {'Size':>10}  {'Region':<6} {'Type'}")
            for sec in elf_info["sections"]:
                region = "RAM" if sec["in_ram"] else "Flash"
                stype = "NOBITS" if sec["nobits"] else "PROGBITS"
                print(
                    f"{sec['name']:<20} 0x{sec['vaddr']:08X}   0x{sec['lma']:08X}   "
                    f"{sec['size']:>8d} B  {region:<6} {stype}"
                )
    else:
        print("\n=== Estimated Footprint (BIN only) ===")
        if is_ram:
            print(f"Flash Usage             : {format_size(0)}  (RAM-linked image)")
            print(f"RAM Image Footprint     : {format_size(image_size)}  (.interrupts + .text + .rodata + .data)")
        else:
            print(f"Flash Usage             : {format_size(image_size)}  (.interrupts + .text + .rodata + .data)")
        print("[NOTE] Pass .elf (or keep companion .elf in same directory) for exact .bss/heap/stack usage.")

    bin_arg = str(bin_path)
    if is_ram:
        print("\n--- C++ blhost.exe 2.6.7 Commands (BusPal CAN via COM3) ---")
        print(f'.\\blhost.exe -b can -p COM3 write-memory 0x{load_addr:08X} "{bin_arg}"')
        print(f'.\\blhost.exe -b can -p COM3 execute 0x{pc:08X} 0x{load_addr:08X} 0x{sp:08X}')

        print("\n--- Ready-to-use SPSDK blhost Commands (Direct PCAN) ---")
        print(f'blhost -cb "pcan,{args.channel}" write-memory 0x{load_addr:08X} "{bin_arg}"')
        print(f'blhost -cb "pcan,{args.channel}" execute 0x{pc:08X} 0x{load_addr:08X} 0x{sp:08X}')

        print("\n--- Ready-to-use SPSDK blhost Commands (Direct UART) ---")
        print(f'blhost -p "{args.uart_port}" write-memory 0x{load_addr:08X} "{bin_arg}"')
        print(f'blhost -p "{args.uart_port}" execute 0x{pc:08X} 0x{load_addr:08X} 0x{sp:08X}')
    else:
        erase_size = (image_size + 0x1FFF) & ~0x1FFF
        print("\n--- C++ blhost.exe 2.6.7 Flash Commands (BusPal CAN via COM3) ---")
        print(f'.\\blhost.exe -b can -p COM3 flash-erase-region 0x{load_addr:08X} 0x{erase_size:X}')
        print(f'.\\blhost.exe -b can -p COM3 write-memory 0x{load_addr:08X} "{bin_arg}"')
        print(f'.\\blhost.exe -b can -p COM3 execute 0x{pc:08X} 0x{load_addr:08X} 0x{sp:08X}')

        print("\n--- Ready-to-use SPSDK blhost Flash Commands (Direct PCAN) ---")
        print(f'blhost -cb "pcan,{args.channel}" flash-erase-region 0x{load_addr:08X} 0x{erase_size:X}')
        print(f'blhost -cb "pcan,{args.channel}" write-memory 0x{load_addr:08X} "{bin_arg}"')
        print(f'blhost -cb "pcan,{args.channel}" execute 0x{pc:08X} 0x{load_addr:08X} 0x{sp:08X}')

        print("\n--- Ready-to-use SPSDK blhost Flash Commands (Direct UART) ---")
        print(f'blhost -p "{args.uart_port}" flash-erase-region 0x{load_addr:08X} 0x{erase_size:X}')
        print(f'blhost -p "{args.uart_port}" write-memory 0x{load_addr:08X} "{bin_arg}"')
        print(f'blhost -p "{args.uart_port}" execute 0x{pc:08X} 0x{load_addr:08X} 0x{sp:08X}')

    bat_path = cwd / f"{stem}_run.bat"
    write_isp_bat(bat_path, bin_arg, load_addr, image_size, sp, pc, is_ram, args.channel)
    print("\n--- One-Click Windows Batch Script ---")
    print(f".\\{bat_path.name}")

    jlink_target = elf_path if input_is_elf else bin_path
    jlink_cmds = build_jlink_commands(jlink_target, load_addr, sp, pc, is_ram)
    jlink_path = cwd / f"{stem}.jlink"
    jlink_path.write_text(jlink_cmds, encoding="utf-8")
    print(f"\n--- J-Link Internal Commands (after manual connect, saved to .\\{jlink_path.name}) ---")
    print(jlink_cmds, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())

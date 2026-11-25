#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""Universal CAN Interface & Channel Scanner (with PCAN deep diagnostics)."""

import argparse
from typing import Any, Dict, List

import can
from can.interfaces import VALID_INTERFACES

# Dedicated hardware CAN backends scanned by default
# (Excludes virtual/network test buses and raw COM-port enumerators unless --all is used)
DEFAULT_HW_INTERFACES = [
    "pcan",
    "vector",
    "kvaser",
    "ixxat",
    "gs_usb",
    "canalystii",
    "neovi",
    "nixnet",
    "systec",
    "etas",
    "cantact",
    "usb2can",
    "nican",
    "iscan",
    "neousys",
]

# Optional PCANBasic import for deep PCAN diagnostics
try:
    from can.interfaces.pcan.basic import (
        FEATURE_DELAY_CAPABLE,
        FEATURE_FD_CAPABLE,
        FEATURE_IO_CAPABLE,
        PCAN_API_VERSION,
        PCAN_CHANNEL_AVAILABLE,
        PCAN_CHANNEL_OCCUPIED,
        PCAN_CHANNEL_PCANVIEW,
        PCAN_CHANNEL_UNAVAILABLE,
        PCAN_CHANNEL_VERSION,
        PCAN_ERROR_OK,
        PCAN_FIRMWARE_VERSION,
        PCAN_NONEBUS,
        PCANBasic,
        TPCANHandle,
    )
    from can.interfaces.pcan.pcan import PCAN_CHANNEL_NAMES

    HAS_PCAN_BASIC = True
except Exception:
    HAS_PCAN_BASIC = False

if HAS_PCAN_BASIC:
    PCAN_CONDITION_LABELS = {
        PCAN_CHANNEL_UNAVAILABLE: "UNAVAILABLE (Not connected)",
        PCAN_CHANNEL_AVAILABLE: "AVAILABLE (Ready to connect)",
        PCAN_CHANNEL_OCCUPIED: "OCCUPIED (In use by another app)",
        PCAN_CHANNEL_PCANVIEW: "PCAN-VIEW (In use by PCAN-View, still connectable)",
    }


def format_pcan_features(feature_mask: int) -> str:
    features = []
    if feature_mask & FEATURE_FD_CAPABLE:
        features.append("CAN-FD")
    else:
        features.append("Classic-CAN")
    if feature_mask & FEATURE_DELAY_CAPABLE:
        features.append("Interframe-Delay")
    if feature_mask & FEATURE_IO_CAPABLE:
        features.append("Digital-IO")
    return ", ".join(features)


def get_pcan_string_param(pcan_basic: Any, handle: Any, param: Any) -> str:
    res, val = pcan_basic.GetValue(handle, param)
    if res == PCAN_ERROR_OK and isinstance(val, (bytes, bytearray)):
        return val.decode("latin-1", errors="ignore").strip("\x00").strip()
    return "N/A"


def format_generic_details(cfg: Dict[str, Any]) -> str:
    """Format extra metadata fields returned by non-PCAN interfaces (Vector, Kvaser, gs_usb, etc.)."""
    skip_keys = {"interface", "channel", "supports_fd"}
    items = []
    for k, v in cfg.items():
        if k in skip_keys:
            continue
        if isinstance(v, int) and ("id" in k.lower() or "serial" in k.lower() or "hw" in k.lower()):
            items.append(f"{k}={v} (0x{v:X})")
        else:
            items.append(f"{k}={v}")
    return ", ".join(items) if items else "-"


def print_pcan_detail_table(pcan_configs: List[Dict[str, Any]]) -> None:
    if not HAS_PCAN_BASIC or not pcan_configs:
        return

    print("\n=== PCAN Hardware Detailed Diagnostics ===")
    try:
        pcan_basic = PCANBasic()
    except Exception as e:
        print(f"Could not initialize PCANBasic DLL for extended info: {e}")
        return

    api_ver = get_pcan_string_param(pcan_basic, PCAN_NONEBUS, PCAN_API_VERSION)
    print(f"PCAN-Basic API Version : {api_ver}")

    header = (
        f"{'Channel Name':<16} | {'Handle':<6} | {'Device Name':<18} | "
        f"{'Dev ID':<10} | {'Ctrl':<4} | {'Features':<24} | {'FW Ver':<10} | {'Condition'}"
    )
    print(header)
    print("-" * len(header))

    for cfg in pcan_configs:
        ch_name = str(cfg.get("channel", "UNKNOWN"))
        handle = PCAN_CHANNEL_NAMES.get(ch_name, PCAN_NONEBUS)
        handle_hex = f"0x{handle.value:02X}" if handle != PCAN_NONEBUS else "N/A"
        dev_name = str(cfg.get("device_name", "Unknown"))
        dev_id = int(cfg.get("device_id", 0))
        ctrl_num = int(cfg.get("controller_number", 0))
        feat_mask = int(cfg.get("device_features", 0))
        cond_code = int(cfg.get("channel_condition", PCAN_CHANNEL_UNAVAILABLE))

        cond_str = PCAN_CONDITION_LABELS.get(cond_code, f"UNKNOWN (0x{cond_code:02X})")
        feat_str = format_pcan_features(feat_mask)
        fw_ver = (
            get_pcan_string_param(pcan_basic, handle, PCAN_FIRMWARE_VERSION)
            if handle != PCAN_NONEBUS
            else "N/A"
        )

        print(
            f"{ch_name:<16} | {handle_hex:<6} | {dev_name:<18} | "
            f"0x{dev_id:08X} | {ctrl_num:<4} | {feat_str:<24} | {fw_ver:<10} | {cond_str}"
        )

    first_handle = PCAN_CHANNEL_NAMES.get(str(pcan_configs[0].get("channel", "")), PCAN_NONEBUS)
    if first_handle != PCAN_NONEBUS:
        drv_ver = get_pcan_string_param(pcan_basic, first_handle, PCAN_CHANNEL_VERSION)
        print(f"Channel Driver Version : {drv_ver}")


def scan_can_channels(target_interfaces: List[str]) -> None:
    print("=== Universal CAN Interface & Channel Scanner ===")
    print(f"Scanning backends: {', '.join(target_interfaces)}\n")

    configs = can.detect_available_configs(interfaces=target_interfaces)
    if not configs:
        print("No CAN hardware channels detected.")
        print("Tip: Run with '--all' to also include serial/COM ports and virtual interfaces.")
        return

    print(f"Total Detected Channels: {len(configs)}\n")
    header = f"{'Interface':<12} | {'Channel':<22} | {'CAN-FD':<8} | {'Hardware Details'}"
    print(header)
    print("-" * max(len(header) + 30, 80))

    pcan_configs = []

    for cfg in configs:
        iface = str(cfg.get("interface", "unknown"))
        channel = str(cfg.get("channel", "N/A"))
        supports_fd = cfg.get("supports_fd")
        fd_str = "Yes" if supports_fd is True else ("No" if supports_fd is False else "Unknown")
        details = format_generic_details(cfg)

        print(f"{iface:<12} | {channel:<22} | {fd_str:<8} | {details}")

        if iface == "pcan":
            pcan_configs.append(cfg)

    if pcan_configs:
        print_pcan_detail_table(pcan_configs)

    print("\n--- Quick Copy for python-can / SPSDK blhost ---")
    for cfg in configs:
        iface = cfg.get("interface")
        channel = cfg.get("channel")
        print(f"python-can : can.interface.Bus(interface={iface!r}, channel={channel!r}, bitrate=1000000)")
        print(f"SPSDK CLI  : blhost -cb \"{iface},{channel},1000000,0x123,0x321\" get-property 1")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan available CAN interfaces and channels across all supported brands."
    )
    parser.add_argument(
        "-i",
        "--interface",
        nargs="+",
        help="Specific python-can interface(s) to scan (e.g. pcan vector kvaser gs_usb canalystii)",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Scan all python-can backends including serial/slcan COM ports and virtual buses",
    )
    args = parser.parse_args()

    if args.interface:
        targets = args.interface
    elif args.all:
        targets = sorted(VALID_INTERFACES)
    else:
        targets = [i for i in DEFAULT_HW_INTERFACES if i in VALID_INTERFACES]

    scan_can_channels(targets)


if __name__ == "__main__":
    main()

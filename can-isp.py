#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""Monitor KW45 CAN ISP traffic on PCAN and decode MBOOT with SPSDK 3.1.0.

Run with python can-isp.py and press Ctrl+C to stop. Board and host CAN
settings are defined below. See CAN_Scanner_and_SPSDK_Devices_Guide.md
for setup, example traffic, and troubleshooting.
"""

import struct
import can

try:
    from spsdk.mboot.commands import (
        CmdHeader,
        CommandTag,
        GenericResponse,
        GetPropertyResponse,
        ResponseTag,
        parse_cmd_response,
    )
    from spsdk.mboot.error_codes import stringify_status_code
    from spsdk.mboot.properties import (
        PropertyTag,
        Version,
        parse_property_value,
    )
    from spsdk.mboot.protocol.serial_protocol import PingResponse
    from spsdk.utils.family import FamilyRevision
    HAS_SPSDK = True
except ImportError:
    HAS_SPSDK = False

# --- Configuration ---
PCAN_CHANNEL = 'PCAN_USBBUS1'
BITRATE = 1000000               # 1000 kbit/s (1 Mbit/s)
TARGET_FAMILY = 'kw45xx'        # Target MCU family for SPSDK property override

# PCAN-View Dataset Entry: 1 Mbit/s, Sample Point 75.0%, Prescaler 10
# F_clock = 80 MHz, Prescaler (nom_brp) = 10 -> Total TQ = 80 MHz / (10 * 1 Mbit/s) = 8 TQ
# Sample Point = (1 + TSEG1) / 8 = 75% -> TSEG1 = 5, TSEG2 = 2, SJW = 1
F_CLOCK = 80000000
NOM_BRP = 10
NOM_TSEG1 = 5
NOM_TSEG2 = 2
NOM_SJW = 1

# Filter configuration: set empty list [] to listen to all CAN IDs
# can_mask: 0x7FF for 11-bit Standard ID, 0x1FFFFFFF for 29-bit Extended ID
CAN_FILTERS = [
    {"can_id": 0x123, "can_mask": 0x7FF, "extended": False},
    {"can_id": 0x321, "can_mask": 0x7FF, "extended": False},
]

# --- Main Thread: CAN Bus Initialization and Data Reception ---
bus = None

# --- Initialize the PCAN Standard CAN Bus ---
try:
    bus = can.interface.Bus(
        interface='pcan',
        channel=PCAN_CHANNEL,
        bitrate=BITRATE,
        f_clock=F_CLOCK,
        nom_brp=NOM_BRP,
        nom_tseg1=NOM_TSEG1,
        nom_tseg2=NOM_TSEG2,
        nom_sjw=NOM_SJW,
        fd=False,                       # 经典 CAN (不带 FD)
        can_filters=CAN_FILTERS if CAN_FILTERS else None,
    )
    print(f"Successfully connected to PCAN channel: {PCAN_CHANNEL} (Bitrate: {BITRATE} bps)")
    print("Bus is in Standard CAN mode.")
except Exception as e:
    print(f"Error connecting to PCAN bus: {e}")
    print("Please double-check:")
    print("1. Your PCAN hardware is connected.")
    print("2. You have the latest PCAN drivers installed.")
    print("3. The 'PCAN_CHANNEL' name is correct.")
    print("4. All timing parameters are accurate for your network and hardware.")
    exit()

# --- KW45 MCU Bootloader (MBOOT) Framing Packet Reassembly ---
MBOOT_START_BYTE = 0x5A
MBOOT_PACKET_TYPES = {
    0xA1: "ACK",
    0xA2: "NAK",
    0xA3: "ACK_ABORT",
    0xA4: "COMMAND",
    0xA5: "DATA",
    0xA6: "PING",
    0xA7: "PING_RESPONSE",
}

class MbootReassembler:
    def __init__(self):
        self.buf = bytearray()
        self.expected_len = 0
        self.timestamp = 0.0

    def reset(self):
        self.buf.clear()
        self.expected_len = 0

    def feed(self, can_id, timestamp, data):
        out = []
        # If idle and frame does not start with 0x5A, print raw frame directly
        if not self.buf and (not data or data[0] != MBOOT_START_BYTE):
            out.append((timestamp, can_id, "RAW", bytes(data)))
            return out

        if not self.buf:
            self.timestamp = timestamp

        self.buf.extend(data)

        while self.buf:
            if self.buf[0] != MBOOT_START_BYTE:
                out.append((self.timestamp, can_id, "RAW", bytes(self.buf)))
                self.reset()
                break

            if len(self.buf) < 2:
                break

            ptype = self.buf[1]
            if ptype in (0xA1, 0xA2, 0xA3, 0xA6):
                self.expected_len = 2
            elif ptype == 0xA7:
                self.expected_len = 10
            elif ptype in (0xA4, 0xA5):
                if len(self.buf) < 4:
                    break
                payload_len = self.buf[2] | (self.buf[3] << 8)
                self.expected_len = 6 + payload_len
            else:
                out.append((self.timestamp, can_id, "RAW", bytes(self.buf)))
                self.reset()
                break

            if len(self.buf) >= self.expected_len:
                pkt = bytes(self.buf[:self.expected_len])
                pkt_name = MBOOT_PACKET_TYPES.get(ptype, f"0x{ptype:02X}")
                out.append((self.timestamp, can_id, pkt_name, pkt))
                del self.buf[:self.expected_len]
                self.expected_len = 0
                self.timestamp = timestamp
            else:
                break

        return out

class MbootDecoder:
    """Decode reassembled MBOOT packets using SPSDK."""

    def __init__(self, family_name=TARGET_FAMILY):
        self.family = FamilyRevision(family_name) if HAS_SPSDK else None
        self.last_property_tag = None
        self.last_ext_mem_id = None

    def _get_property_name(self, prop_tag):
        if not HAS_SPSDK:
            return f"0x{prop_tag:02X}"
        try:
            tag_enum = PropertyTag.from_index(prop_tag, self.family)
            return f"'{tag_enum.friendly_name}' ({tag_enum.description})"
        except Exception:
            return f"Unknown(0x{prop_tag:02X})"

    def decode(self, pkt_type, pkt_bytes):
        if not HAS_SPSDK or len(pkt_bytes) < 2:
            return []

        ptype = pkt_bytes[1]
        lines = []

        if ptype == 0xA6:
            lines.append("Send Ping")
        elif ptype == 0xA7 and len(pkt_bytes) == 10:
            ping_resp = PingResponse.parse(pkt_bytes[2:])
            ver_str = str(Version(ping_resp.version))
            lines.append(
                f"Ping responded: Protocol Version = {ver_str} "
                f"(0x{ping_resp.version:08x}), Options = 0x{ping_resp.options:04x}"
            )
        elif ptype == 0xA4 and len(pkt_bytes) >= 10:
            payload = pkt_bytes[6:]
            header = CmdHeader.parse(payload)
            # Check if packet is a Response (tag >= 0xA0) or a Command request
            if header.tag in ResponseTag.tags():
                resp = parse_cmd_response(payload)
                lines.append(f"Response status = {stringify_status_code(resp.status)}")
                if isinstance(resp, GetPropertyResponse):
                    for i, word in enumerate(resp.values, start=1):
                        lines.append(f"Response word {i} = {word} ({word:#x})")
                    if self.last_property_tag is not None and resp.values:
                        prop_val = parse_property_value(
                            self.last_property_tag,
                            resp.values,
                            self.last_ext_mem_id,
                            self.family,
                        )
                        if prop_val is not None:
                            lines.append(str(prop_val))
                elif isinstance(resp, GenericResponse):
                    cmd_name = (
                        CommandTag.get_label(resp.cmd_tag)
                        if resp.cmd_tag in CommandTag.tags()
                        else f"0x{resp.cmd_tag:02X}"
                    )
                    lines.append(f"Command Tag = {cmd_name} (0x{resp.cmd_tag:02x})")
                else:
                    # Other response types (ReadMemoryResponse, FlashReadOnceResponse, etc.)
                    param_bytes = payload[CmdHeader.SIZE + 4 :]
                    word_count = len(param_bytes) // 4
                    if word_count > 0:
                        words = struct.unpack_from(f"<{word_count}I", param_bytes)
                        for i, word in enumerate(words, start=1):
                            lines.append(f"Response word {i} = {word} ({word:#x})")
            else:
                param_bytes = payload[CmdHeader.SIZE :]
                word_count = min(header.params_count, len(param_bytes) // 4)
                params = (
                    list(struct.unpack_from(f"<{word_count}I", param_bytes))
                    if word_count > 0
                    else []
                )
                cmd_label = (
                    CommandTag.get_label(header.tag)
                    if header.tag in CommandTag.tags()
                    else f"0x{header.tag:02X}"
                )
                cmd_desc = (
                    CommandTag.get_description(header.tag, cmd_label)
                    if header.tag in CommandTag.tags()
                    else cmd_label
                )
                if header.tag == CommandTag.GET_PROPERTY.tag and params:
                    self.last_property_tag = params[0]
                    self.last_ext_mem_id = params[1] if len(params) > 1 else 0
                    prop_info = self._get_property_name(self.last_property_tag)
                    lines.append(
                        f"Inject command 'get-property': Tag = {self.last_property_tag} "
                        f"({prop_info}), MemoryIndex = {self.last_ext_mem_id}"
                    )
                elif header.tag == CommandTag.SET_PROPERTY.tag and len(params) >= 2:
                    prop_info = self._get_property_name(params[0])
                    lines.append(
                        f"Inject command 'set-property': Tag = {params[0]} "
                        f"({prop_info}), Value = {params[1]} ({params[1]:#x})"
                    )
                else:
                    params_str = ", ".join(f"P[{i}]={p:#x}" for i, p in enumerate(params))
                    lines.append(
                        f"Inject command '{cmd_label}' ({cmd_desc})"
                        + (f": {params_str}" if params_str else "")
                    )

        return lines


reassemblers = {}
decoder = MbootDecoder(family_name=TARGET_FAMILY)

# --- Read Standard CAN Messages ---
print("\nListening for CAN messages... Press Ctrl+C to stop.")
try:
    while True:
        message = bus.recv(timeout=1.0)
        if message is not None:
            arb_id = message.arbitration_id
            if arb_id not in reassemblers:
                reassemblers[arb_id] = MbootReassembler()
            packets = reassemblers[arb_id].feed(arb_id, message.timestamp, message.data)
            for ts, cid, pkt_type, pkt_bytes in packets:
                data_hex = ' '.join(f'{b:02X}' for b in pkt_bytes)
                print(f"Timestamp: {ts:.3f} | "
                      f"ID: 0x{cid:03X} | "
                      f"Type: {pkt_type:<13} | "
                      f"Len: {len(pkt_bytes):2d} | "
                      f"Data: [{data_hex}]")
                for decoded_line in decoder.decode(pkt_type, pkt_bytes):
                    print(f"    -> {decoded_line}")
except KeyboardInterrupt:
    print("\nStopping message reception.")
except can.CanError as e:
    print(f"A CAN error occurred during message reception: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
finally:
    # --- Clean up ---
    if bus is not None:
        bus.shutdown()
        print("PCAN bus shut down.")

#!/usr/bin/env python3
"""Replay Beetle WonderSwan RetroArch states directly through libretro.

Diagnostic for the ending-cinematic middle-band report.  This loads the exact
mednafen_wswan_libretro core, unserializes a user-provided RetroArch RZIP state,
runs deterministic frames with no input, and records the WSRAM object/map state.

It does not modify the ROM, SaveRAM, or the user's RetroArch states.
"""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORE = Path(r"C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll")
DEFAULT_ORIG_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_MAIN_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_STATE_DIR = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan")
DEFAULT_ORIG_STATE = DEFAULT_STATE_DIR / "SD Gundam G Generation Mono-Eye Gundams.state"
DEFAULT_MAIN_STATE = DEFAULT_STATE_DIR / "monoeye_ko_expanded.state31"

RETRO_MEMORY_SYSTEM_RAM = 2

# Environment commands used by common Mednafen libretro cores.  Unknown
# commands are intentionally rejected so the core falls back to defaults.
RETRO_ENVIRONMENT_GET_CAN_DUPE = 3
RETRO_ENVIRONMENT_SET_MESSAGE = 6
RETRO_ENVIRONMENT_SHUTDOWN = 7
RETRO_ENVIRONMENT_SET_PERFORMANCE_LEVEL = 8
RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY = 9
RETRO_ENVIRONMENT_SET_PIXEL_FORMAT = 10
RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS = 11
RETRO_ENVIRONMENT_SET_KEYBOARD_CALLBACK = 12
RETRO_ENVIRONMENT_SET_DISK_CONTROL_INTERFACE = 13
RETRO_ENVIRONMENT_SET_HW_RENDER = 14
RETRO_ENVIRONMENT_GET_VARIABLE = 15
RETRO_ENVIRONMENT_SET_VARIABLES = 16
RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE = 17
RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME = 18
RETRO_ENVIRONMENT_GET_LIBRETRO_PATH = 19
RETRO_ENVIRONMENT_SET_FRAME_TIME_CALLBACK = 21
RETRO_ENVIRONMENT_SET_AUDIO_CALLBACK = 22
RETRO_ENVIRONMENT_GET_RUMBLE_INTERFACE = 23
RETRO_ENVIRONMENT_GET_INPUT_DEVICE_CAPABILITIES = 24
RETRO_ENVIRONMENT_GET_SENSOR_INTERFACE = 25
RETRO_ENVIRONMENT_GET_CAMERA_INTERFACE = 26
RETRO_ENVIRONMENT_GET_LOG_INTERFACE = 27
RETRO_ENVIRONMENT_GET_PERF_INTERFACE = 28
RETRO_ENVIRONMENT_GET_LOCATION_INTERFACE = 29
RETRO_ENVIRONMENT_GET_CONTENT_DIRECTORY = 30
RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY = 31
RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO = 32
RETRO_ENVIRONMENT_SET_PROC_ADDRESS_CALLBACK = 33
RETRO_ENVIRONMENT_SET_SUBSYSTEM_INFO = 34
RETRO_ENVIRONMENT_SET_CONTROLLER_INFO = 35
RETRO_ENVIRONMENT_SET_MEMORY_MAPS = 36
RETRO_ENVIRONMENT_SET_GEOMETRY = 37
RETRO_ENVIRONMENT_GET_USERNAME = 38
RETRO_ENVIRONMENT_GET_LANGUAGE = 39
RETRO_ENVIRONMENT_GET_CURRENT_SOFTWARE_FRAMEBUFFER = 40
RETRO_ENVIRONMENT_GET_HW_RENDER_INTERFACE = 41
RETRO_ENVIRONMENT_SET_SUPPORT_ACHIEVEMENTS = 42
RETRO_ENVIRONMENT_SET_HW_RENDER_CONTEXT_NEGOTIATION_INTERFACE = 43
RETRO_ENVIRONMENT_SET_SERIALIZATION_QUIRKS = 44
RETRO_ENVIRONMENT_SET_HW_SHARED_CONTEXT = 44
RETRO_ENVIRONMENT_GET_VFS_INTERFACE = 45
RETRO_ENVIRONMENT_GET_LED_INTERFACE = 46
RETRO_ENVIRONMENT_GET_AUDIO_VIDEO_ENABLE = 47
RETRO_ENVIRONMENT_GET_MIDI_INTERFACE = 48
RETRO_ENVIRONMENT_GET_FASTFORWARDING = 49
RETRO_ENVIRONMENT_GET_TARGET_REFRESH_RATE = 50
RETRO_ENVIRONMENT_GET_INPUT_BITMASKS = 51
RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION = 52
RETRO_ENVIRONMENT_SET_CORE_OPTIONS = 53
RETRO_ENVIRONMENT_SET_CORE_OPTIONS_INTL = 54
RETRO_ENVIRONMENT_SET_CORE_OPTIONS_DISPLAY = 55
RETRO_ENVIRONMENT_GET_PREFERRED_HW_RENDER = 56
RETRO_ENVIRONMENT_GET_DISK_CONTROL_INTERFACE_VERSION = 57
RETRO_ENVIRONMENT_SET_DISK_CONTROL_EXT_INTERFACE = 58
RETRO_ENVIRONMENT_GET_MESSAGE_INTERFACE_VERSION = 59
RETRO_ENVIRONMENT_SET_MESSAGE_EXT = 60
RETRO_ENVIRONMENT_GET_INPUT_MAX_USERS = 61
RETRO_ENVIRONMENT_SET_AUDIO_BUFFER_STATUS_CALLBACK = 62
RETRO_ENVIRONMENT_SET_MINIMUM_AUDIO_LATENCY = 63
RETRO_ENVIRONMENT_SET_FASTFORWARDING_OVERRIDE = 64
RETRO_ENVIRONMENT_SET_CONTENT_INFO_OVERRIDE = 65
RETRO_ENVIRONMENT_GET_GAME_INFO_EXT = 66
RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2 = 67
RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2_INTL = 68
RETRO_ENVIRONMENT_SET_CORE_OPTIONS_UPDATE_DISPLAY_CALLBACK = 69
RETRO_ENVIRONMENT_SET_VARIABLE = 70
RETRO_ENVIRONMENT_GET_THROTTLE_STATE = 71
RETRO_ENVIRONMENT_GET_SAVESTATE_CONTEXT = 72
RETRO_ENVIRONMENT_GET_HW_RENDER_CONTEXT_NEGOTIATION_INTERFACE_SUPPORT = 73
RETRO_ENVIRONMENT_GET_JIT_CAPABLE = 74
RETRO_ENVIRONMENT_GET_MICROPHONE_INTERFACE = 75
RETRO_ENVIRONMENT_SET_NETPACKET_INTERFACE = 76
RETRO_ENVIRONMENT_GET_DEVICE_POWER = 77

ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
INPUT_POLL_CB = C.CFUNCTYPE(None)
INPUT_STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class RetroGameInfo(C.Structure):
    _fields_ = [
        ("path", C.c_char_p),
        ("data", C.c_void_p),
        ("size", C.c_size_t),
        ("meta", C.c_char_p),
    ]


class RetroSystemInfo(C.Structure):
    _fields_ = [
        ("library_name", C.c_char_p),
        ("library_version", C.c_char_p),
        ("valid_extensions", C.c_char_p),
        ("need_fullpath", C.c_bool),
        ("block_extract", C.c_bool),
    ]


class RetroVariable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class Harness:
    def __init__(self, core_path: Path, system_dir: Path, save_dir: Path):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(core_path.parent))
            os.add_dll_directory(str(core_path.parent.parent))
        self.lib = C.CDLL(str(core_path))
        self.system_dir_b = os.fsencode(system_dir)
        self.save_dir_b = os.fsencode(save_dir)
        self.content_dir_b = os.fsencode(ROOT)
        self.libretro_path_b = os.fsencode(core_path)
        self.rom_buffer = None
        self.video_frames = 0
        self.last_video = None
        self.input_pressed: set[int] = set()
        self._wire()

    def _wire(self) -> None:
        l = self.lib
        l.retro_set_environment.argtypes = [ENV_CB]
        l.retro_set_video_refresh.argtypes = [VIDEO_CB]
        l.retro_set_audio_sample.argtypes = [AUDIO_CB]
        l.retro_set_audio_sample_batch.argtypes = [AUDIO_BATCH_CB]
        l.retro_set_input_poll.argtypes = [INPUT_POLL_CB]
        l.retro_set_input_state.argtypes = [INPUT_STATE_CB]
        l.retro_get_system_info.argtypes = [C.POINTER(RetroSystemInfo)]
        l.retro_load_game.argtypes = [C.POINTER(RetroGameInfo)]
        l.retro_load_game.restype = C.c_bool
        l.retro_set_controller_port_device.argtypes = [C.c_uint, C.c_uint]
        l.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        l.retro_unserialize.restype = C.c_bool
        l.retro_serialize_size.restype = C.c_size_t
        l.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        l.retro_serialize.restype = C.c_bool
        l.retro_get_memory_data.argtypes = [C.c_uint]
        l.retro_get_memory_data.restype = C.c_void_p
        l.retro_get_memory_size.argtypes = [C.c_uint]
        l.retro_get_memory_size.restype = C.c_size_t

        @ENV_CB
        def env(cmd: int, data: int) -> bool:
            if cmd == RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
                return True
            if cmd == RETRO_ENVIRONMENT_GET_CAN_DUPE:
                C.cast(data, C.POINTER(C.c_bool))[0] = True
                return True
            if cmd in (RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY,
                       RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY,
                       RETRO_ENVIRONMENT_GET_CONTENT_DIRECTORY,
                       RETRO_ENVIRONMENT_GET_LIBRETRO_PATH):
                value = {
                    RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY: self.system_dir_b,
                    RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY: self.save_dir_b,
                    RETRO_ENVIRONMENT_GET_CONTENT_DIRECTORY: self.content_dir_b,
                    RETRO_ENVIRONMENT_GET_LIBRETRO_PATH: self.libretro_path_b,
                }[cmd]
                C.cast(data, C.POINTER(C.c_char_p))[0] = value
                return True
            if cmd == RETRO_ENVIRONMENT_GET_VARIABLE:
                var = C.cast(data, C.POINTER(RetroVariable)).contents
                var.value = None
                return False
            if cmd == RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE:
                C.cast(data, C.POINTER(C.c_bool))[0] = False
                return True
            if cmd == RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION:
                C.cast(data, C.POINTER(C.c_uint))[0] = 0
                return True
            if cmd == RETRO_ENVIRONMENT_GET_INPUT_BITMASKS:
                C.cast(data, C.POINTER(C.c_bool))[0] = True
                return True
            if cmd == RETRO_ENVIRONMENT_GET_LANGUAGE:
                C.cast(data, C.POINTER(C.c_uint))[0] = 0  # English
                return True
            if cmd == RETRO_ENVIRONMENT_GET_INPUT_MAX_USERS:
                C.cast(data, C.POINTER(C.c_uint))[0] = 1
                return True
            if cmd in (
                RETRO_ENVIRONMENT_SET_MESSAGE,
                RETRO_ENVIRONMENT_SET_PERFORMANCE_LEVEL,
                RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS,
                RETRO_ENVIRONMENT_SET_VARIABLES,
                RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME,
                RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO,
                RETRO_ENVIRONMENT_SET_SUBSYSTEM_INFO,
                RETRO_ENVIRONMENT_SET_CONTROLLER_INFO,
                RETRO_ENVIRONMENT_SET_MEMORY_MAPS,
                RETRO_ENVIRONMENT_SET_GEOMETRY,
                RETRO_ENVIRONMENT_SET_SUPPORT_ACHIEVEMENTS,
                RETRO_ENVIRONMENT_SET_SERIALIZATION_QUIRKS,
            ):
                return True
            return False

        @VIDEO_CB
        def video(data, width, height, pitch):
            self.video_frames += 1
            self.last_video = (int(width), int(height), int(pitch), bool(data))

        @AUDIO_CB
        def audio(left, right):
            return None

        @AUDIO_BATCH_CB
        def audio_batch(data, frames):
            return frames

        @INPUT_POLL_CB
        def input_poll():
            return None

        @INPUT_STATE_CB
        def input_state(port, device, index, ident):
            # Standard libretro joypad IDs.  The diagnostic can change this set
            # between retro_run() calls to replay button-driven transitions.
            return 1 if int(ident) in self.input_pressed else 0

        self._callbacks = (env, video, audio, audio_batch, input_poll, input_state)
        l.retro_set_environment(env)
        l.retro_set_video_refresh(video)
        l.retro_set_audio_sample(audio)
        l.retro_set_audio_sample_batch(audio_batch)
        l.retro_set_input_poll(input_poll)
        l.retro_set_input_state(input_state)
        l.retro_init()

    def load_game(self, rom_path: Path) -> dict:
        info = RetroSystemInfo()
        self.lib.retro_get_system_info(C.byref(info))
        raw = rom_path.read_bytes()
        path_b = os.fsencode(rom_path.resolve())
        if info.need_fullpath:
            game = RetroGameInfo(path_b, None, 0, None)
        else:
            self.rom_buffer = C.create_string_buffer(raw)
            game = RetroGameInfo(path_b, C.cast(self.rom_buffer, C.c_void_p), len(raw), None)
        if not self.lib.retro_load_game(C.byref(game)):
            raise RuntimeError(f"retro_load_game failed: {rom_path}")
        self.lib.retro_set_controller_port_device(0, 1)  # RETRO_DEVICE_JOYPAD
        return {
            "library_name": info.library_name.decode(errors="replace") if info.library_name else None,
            "library_version": info.library_version.decode(errors="replace") if info.library_version else None,
            "need_fullpath": bool(info.need_fullpath),
        }

    def unserialize(self, payload: bytes) -> None:
        buf = C.create_string_buffer(payload)
        if not self.lib.retro_unserialize(buf, len(payload)):
            raise RuntimeError(f"retro_unserialize failed ({len(payload)} bytes)")

    def ram(self) -> bytes:
        size = int(self.lib.retro_get_memory_size(RETRO_MEMORY_SYSTEM_RAM))
        ptr = self.lib.retro_get_memory_data(RETRO_MEMORY_SYSTEM_RAM)
        if not ptr or size <= 0:
            raise RuntimeError(f"SYSTEM_RAM unavailable ptr={ptr!r} size={size}")
        return C.string_at(ptr, size)

    def set_pressed(self, *idents: int) -> None:
        self.input_pressed = {int(x) for x in idents}

    def run(self) -> None:
        self.lib.retro_run()

    def serialized_sha256(self) -> str:
        size = int(self.lib.retro_serialize_size())
        if size <= 0:
            raise RuntimeError(f"retro_serialize_size returned {size}")
        buf = C.create_string_buffer(size)
        if not self.lib.retro_serialize(buf, size):
            raise RuntimeError("retro_serialize failed")
        return hashlib.sha256(buf.raw[:size]).hexdigest()

    def close(self) -> None:
        try:
            self.lib.retro_unload_game()
        finally:
            self.lib.retro_deinit()


def retroarch_state_payload(path: Path) -> bytes:
    raw = path.read_bytes()
    if not raw.startswith(b"#RZIPv"):
        raise ValueError(f"not RetroArch RZIP: {path}")
    blob = zlib.decompress(raw[24:])
    if blob[:8] != b"RASTATE\x01":
        raise ValueError("RASTATE header missing")
    p = 8
    while p + 8 <= len(blob):
        tag = blob[p : p + 4]
        size = struct.unpack_from("<I", blob, p + 4)[0]
        p += 8
        data = blob[p : p + size]
        if len(data) != size:
            raise ValueError("truncated RASTATE chunk")
        if tag == b"MEM ":
            return data
        p += size
    raise ValueError("RASTATE MEM chunk missing")


def u16(ram: bytes, off: int) -> int:
    return struct.unpack_from("<H", ram, off)[0]


def ending_snapshot(ram: bytes, frame: int) -> dict:
    # Object 0x78 is the only active object whose position matches the reported
    # band start (x=160,y=72) and differs between the supplied states.
    obj_off = 0x846 + 0x78 * 0x20
    obj = struct.unpack_from("<16H", ram, obj_off)
    # Flatten the exact 80-cell band used in the previous state comparison:
    # row9 c4-27 + row10 c0-27 + row11 c0-27.
    band = [u16(ram, 0x3000 + 2 * (9 * 32 + c)) for c in range(4, 28)]
    band += [u16(ram, 0x3000 + 2 * (10 * 32 + c)) for c in range(28)]
    band += [u16(ram, 0x3000 + 2 * (11 * 32 + c)) for c in range(28)]
    return {
        "frame": frame,
        "obj78_flags": f"{obj[0]:04X}",
        "obj78_x": obj[3],
        "obj78_y": obj[4],
        "obj78_plus0A": f"{obj[5]:04X}",
        "obj78_plus0C": f"{obj[6]:04X}",
        "obj78_plus0E": f"{obj[7]:04X}",
        "band": [f"{x:04X}" for x in band],
        "ram_sha256": hashlib.sha256(ram).hexdigest(),
    }


def run_case(core: Path, rom: Path, state: Path, frames: int) -> dict:
    payload = retroarch_state_payload(state)
    h = Harness(core, Path(r"C:\RetroArch-Win64\system"), state.parent)
    try:
        system = h.load_game(rom)
        h.unserialize(payload)
        rows = [ending_snapshot(h.ram(), 0)]
        rows[0]["serialized_sha256"] = h.serialized_sha256()
        for frame in range(1, frames + 1):
            h.run()
            row = ending_snapshot(h.ram(), frame)
            row["serialized_sha256"] = h.serialized_sha256()
            rows.append(row)
        return {
            "rom": str(rom),
            "state": str(state),
            "serialized_bytes": len(payload),
            "system": system,
            "system_ram_bytes": len(h.ram()),
            "video_frames": h.video_frames,
            "last_video": h.last_video,
            "frames": rows,
        }
    finally:
        h.close()


def compare(orig: dict, main: dict) -> list[dict]:
    out = []
    for o, m in zip(orig["frames"], main["frames"]):
        ob = [int(x, 16) for x in o["band"]]
        mb = [int(x, 16) for x in m["band"]]
        direct = sum(a == b for a, b in zip(ob, mb))
        next_match = sum(mb[i] == ob[i + 1] for i in range(min(len(mb), len(ob) - 1)))
        prev_match = sum(mb[i + 1] == ob[i] for i in range(min(len(ob), len(mb) - 1)))
        out.append({
            "frame": o["frame"],
            "orig_obj78_plus0A": o["obj78_plus0A"],
            "main_obj78_plus0A": m["obj78_plus0A"],
            "orig_obj78_plus0E": o["obj78_plus0E"],
            "main_obj78_plus0E": m["obj78_plus0E"],
            "band_direct_matches": direct,
            "band_main_eq_orig_next": next_match,
            "band_main_next_eq_orig": prev_match,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", type=Path, default=DEFAULT_CORE)
    ap.add_argument("--orig-rom", type=Path, default=DEFAULT_ORIG_ROM)
    ap.add_argument("--main-rom", type=Path, default=DEFAULT_MAIN_ROM)
    ap.add_argument("--orig-state", type=Path, default=DEFAULT_ORIG_STATE)
    ap.add_argument("--main-state", type=Path, default=DEFAULT_MAIN_STATE)
    ap.add_argument("--frames", type=int, default=32)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    orig = run_case(args.core, args.orig_rom, args.orig_state, args.frames)
    main_case = run_case(args.core, args.main_rom, args.main_state, args.frames)
    result = {
        "ok": True,
        "orig": orig,
        "main": main_case,
        "comparison": compare(orig, main_case),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

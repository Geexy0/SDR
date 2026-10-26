#!/usr/bin/env python3
"""Mayhem USB Serial controller for PortaPack H4M / HackRF Pro (receive-safe)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Optional

try:
    import serial
except ImportError:
    print("pyserial required: pip install pyserial", file=sys.stderr)
    sys.exit(1)

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 2.0
PROMPT = b"ch> "


class MayhemSerial:
    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None

    def __enter__(self) -> "MayhemSerial":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        self.ser = serial.Serial(
            self.port,
            self.baud,
            timeout=self.timeout,
            write_timeout=15,
        )
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        self.ser.write(b"\r\n")
        self.ser.flush()
        time.sleep(0.2)
        self._drain(0.3)

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def _drain(self, seconds: float = 0.2) -> bytes:
        assert self.ser
        end = time.time() + seconds
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(4096)
            if chunk:
                buf += chunk
            else:
                time.sleep(0.02)
        return buf

    def command(self, cmd: str, wait: float = 2.0, expect_prompt: bool = True) -> str:
        assert self.ser
        self.ser.reset_input_buffer()
        payload = (cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="replace")
        self.ser.write(payload)
        self.ser.flush()
        buf = b""
        deadline = time.time() + wait
        while time.time() < deadline:
            chunk = self.ser.read(8192)
            if chunk:
                buf += chunk
                if expect_prompt and PROMPT in buf:
                    # Allow trailing bytes after prompt
                    time.sleep(0.05)
                    extra = self.ser.read(self.ser.in_waiting or 1)
                    if extra:
                        buf += extra
                        continue
                    break
            else:
                time.sleep(0.02)
        text = buf.decode("utf-8", errors="replace")
        return self._strip_echo(text, cmd)

    @staticmethod
    def _strip_echo(text: str, cmd: str) -> str:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        # Drop echoed command line and trailing prompt
        cleaned = []
        for i, line in enumerate(lines):
            if i == 0 and line.strip() == cmd.strip():
                continue
            if line.strip() == "ch>":
                continue
            if line.endswith("ch>") and line.strip() == "ch>":
                continue
            cleaned.append(line)
        # Remove trailing empty / prompt fragments
        while cleaned and cleaned[-1].strip() in ("", "ch>"):
            cleaned.pop()
        if cleaned and cleaned[-1].rstrip().endswith("ch>"):
            cleaned[-1] = cleaned[-1].rsplit("ch>", 1)[0].rstrip()
        return "\n".join(cleaned).strip()

    def _ensure_closed(self) -> None:
        """Best-effort fclose if a prior transfer left a file open."""
        r = self.command("fclose", wait=1.5)
        if "no open file" not in r.lower() and r:
            pass

    def upload_bytes(
        self,
        remote_path: str,
        data: bytes,
        chunk_hex: int = 8,
        use_fwb: bool = False,
    ) -> None:
        """Upload via fopen / fseek 0 / fwrite hex / ftruncate / fsync / fclose.

        Default is tiny hex fwrite (8 bytes → 16 hex digits). ChibiOS shell
        MAX_LINE is ~64 chars; fwb binary bulk has wedged this CDC stack, so
        it is opt-in only (use_fwb=True).
        """
        assert self.ser
        remote_path = remote_path.replace("\\", "/")
        parent = str(Path(remote_path).parent).replace("\\", "/").rstrip("/") or "/"
        # Only mkdir if parent is not an existing top-level Mayhem folder
        if parent not in (".", "/", "/FREQMAN", "FREQMAN"):
            self.command(f"mkdir {parent}", wait=2.0)

        self._ensure_closed()
        # Replace existing file cleanly
        self.command(f"unlink {remote_path}", wait=2.0)

        resp = self.command(f"fopen {remote_path}", wait=3.0)
        if "already open" in resp.lower():
            self._ensure_closed()
            resp = self.command(f"fopen {remote_path}", wait=3.0)
        if "error" in resp.lower():
            raise RuntimeError(f"fopen failed: {resp}")

        seek = self.command("fseek 0", wait=2.0)
        if "error" in seek.lower() or "no open file" in seek.lower():
            raise RuntimeError(f"fseek failed: {seek}")

        if use_fwb and data:
            self._upload_fwb(data)
        else:
            total = len(data)
            for i in range(0, total, chunk_hex):
                chunk = data[i : i + chunk_hex]
                hexstr = chunk.hex().upper()
                r = self.command(f"fwrite {hexstr}", wait=4.0)
                if (
                    "usage:" in r.lower()
                    or "no open file" in r.lower()
                    or "error" in r.lower()
                ):
                    raise RuntimeError(f"fwrite failed at offset {i}: {r}")
                if (i // chunk_hex) % 20 == 0:
                    print(f"  … {min(i + chunk_hex, total)}/{total} bytes", flush=True)
                time.sleep(0.02)

        self.command("ftruncate", wait=2.0)
        self.command("fsync", wait=3.0)
        self.command("fclose", wait=2.0)

    def _upload_fwb(self, data: bytes) -> None:
        """Opt-in binary write; can wedge some CDC sessions — prefer fwrite."""
        assert self.ser
        self.ser.reset_input_buffer()
        self.ser.write(f"fwb {len(data)}\r\n".encode("ascii"))
        self.ser.flush()
        buf = b""
        deadline = time.time() + 5.0
        while time.time() < deadline:
            chunk = self.ser.read(1024)
            if chunk:
                buf += chunk
                if b"send" in buf.lower() or b"error" in buf.lower():
                    break
            else:
                time.sleep(0.02)
        if b"error" in buf.lower() or b"no open file" in buf.lower():
            raise RuntimeError(f"fwb rejected: {buf.decode('utf-8', errors='replace')}")
        # Write in USB-friendly pieces
        for i in range(0, len(data), 64):
            self.ser.write(data[i : i + 64])
            self.ser.flush()
            time.sleep(0.01)
        deadline = time.time() + max(8.0, len(data) / 500.0 + 3.0)
        while time.time() < deadline:
            chunk = self.ser.read(1024)
            if chunk:
                buf += chunk
                if PROMPT in buf and b"ok" in buf.lower():
                    break
            else:
                time.sleep(0.02)
        text = buf.decode("utf-8", errors="replace")
        if "ok" not in text.lower():
            raise RuntimeError(f"fwb write incomplete: {text[-200:]}")

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        data = local_path.read_bytes()
        # Ensure LF endings for text FREQMAN
        if local_path.suffix.upper() == ".TXT":
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        self.upload_bytes(remote_path, data)


def parse_info(text: str) -> dict:
    info = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def cmd_status(args: argparse.Namespace) -> int:
    with MayhemSerial(args.port, args.baud, args.timeout) as m:
        info_raw = m.command("info", wait=3.0)
        help_raw = m.command("help", wait=3.0)
        try:
            ls_freq = m.command("ls /FREQMAN", wait=5.0)
        except Exception as e:
            ls_freq = f"(ls failed: {e})"

    info = parse_info(info_raw)
    print("=== Mayhem status ===")
    print(f"Port:              {args.port}")
    print(f"Mayhem Version:    {info.get('Mayhem Version', 'unknown')}")
    print(f"Board:             {info.get('Board', 'unknown')}")
    print(f"Device:            {info.get('Device', 'unknown')}")
    print(f"HackRF Board Rev:  {info.get('HackRF Board Rev', 'unknown')}")
    print(f"Flash Current:     {info.get('Flash Current', 'unknown')}")
    print()
    print("--- info ---")
    print(info_raw)
    print()
    print("--- help (commands) ---")
    print(help_raw)
    print()
    print("--- /FREQMAN ---")
    print(ls_freq)
    sd_ok = "FREQMAN" in ls_freq or ".TXT" in ls_freq.upper() or "/" in ls_freq
    print()
    print(f"SD / FREQMAN reachable: {'yes' if sd_ok or ls_freq else 'unknown'}")
    return 0


def _png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal RGB8 PNG encoder (stdlib only)."""
    if len(rgb) != width * height * 3:
        raise ValueError(f"RGB size {len(rgb)} != {width}*{height}*3")

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + tag
            + data
            + zlib.crc32(tag + data).to_bytes(4, "big")
        )

    raw = b"".join(b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height))
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, 2, 0, 0, 0])  # 8-bit RGB
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def capture_screenframe(m: MayhemSerial, wait: float = 45.0) -> tuple[int, int, bytes]:
    """Return (width, height, rgb_bytes) from screenframe."""
    assert m.ser
    m.ser.reset_input_buffer()
    m.ser.write(b"screenframe\r\n")
    m.ser.flush()

    buf = b""
    deadline = time.time() + wait
    while time.time() < deadline:
        chunk = m.ser.read(16384)
        if chunk:
            buf += chunk
            if b"ok\r\n" in buf or buf.rstrip().endswith(b"ok"):
                # wait for prompt
                time.sleep(0.1)
                buf += m.ser.read(m.ser.in_waiting or 256)
                if PROMPT in buf or time.time() > deadline - 0.5:
                    break
        else:
            time.sleep(0.02)

    text = buf.decode("ascii", errors="ignore")
    # Collect hex rows (ignore echo/prompt/ok)
    rows: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s or s in ("screenframe", "ok", "ch>"):
            continue
        if s.endswith("ch>"):
            s = s[: -3].strip()
        if re.fullmatch(r"[0-9A-Fa-f]+", s) and len(s) >= 60 and len(s) % 6 == 0:
            rows.append(s.upper())

    if not rows:
        raise RuntimeError(
            "No screenframe hex rows received. "
            f"Got {len(buf)} bytes. Device may be busy."
        )

    width = len(rows[0]) // 6
    height = len(rows)
    # Pad/truncate inconsistent rows
    rgb = bytearray()
    for row in rows:
        if len(row) < width * 6:
            row = row + ("00" * (width * 6 - len(row)))
        elif len(row) > width * 6:
            row = row[: width * 6]
        rgb.extend(bytes.fromhex(row))

    return width, height, bytes(rgb)


def cmd_screenshot(args: argparse.Namespace) -> int:
    out = Path(args.screenshot)
    out.parent.mkdir(parents=True, exist_ok=True)
    with MayhemSerial(args.port, args.baud, args.timeout) as m:
        width, height, rgb = capture_screenframe(m)
    png = _png_rgb(width, height, rgb)
    out.write_bytes(png)
    print(f"Saved {out} ({width}x{height}, {len(png)} bytes PNG)")
    return 0


def wait_hackrf(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["hackrf_info"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode == 0 and "No HackRF boards found" not in out:
                print(out.strip())
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"hackrf_info wait: {e}")
        time.sleep(1.0)
        print("… polling for HackRF Native Mode (1d50:6089)")
    return False


def cmd_switch_to_hackrf(args: argparse.Namespace) -> int:
    print("Sending 'hackrf' — PortaPack will leave Mayhem Serial Mode.")
    print("AMP stays under your control in Linux tools (default -a 0).")
    with MayhemSerial(args.port, args.baud, args.timeout) as m:
        # Serial often drops immediately; don't require prompt
        assert m.ser
        m.ser.write(b"hackrf\r\n")
        m.ser.flush()
        time.sleep(0.5)
    # Port disappears; wait for native USB
    time.sleep(1.5)
    if wait_hackrf(timeout=args.hackrf_timeout):
        print("HackRF Native Mode ready.")
        return 0
    print("Timed out waiting for hackrf_info.", file=sys.stderr)
    return 1


def cmd_raw(args: argparse.Namespace) -> int:
    with MayhemSerial(args.port, args.baud, args.timeout) as m:
        resp = m.command(args.cmd, wait=args.wait)
    print(resp)
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    local = Path(args.upload)
    if not local.is_file():
        print(f"Missing file: {local}", file=sys.stderr)
        return 1
    remote = args.remote or f"/FREQMAN/{local.name.upper()}"
    with MayhemSerial(args.port, args.baud, args.timeout) as m:
        print(f"Uploading {local} -> {remote} …")
        m.upload_file(local, remote)
        size = m.command(f"filesize {remote}", wait=3.0)
        print(f"filesize: {size}")
        listing = m.command("ls /FREQMAN", wait=5.0)
        print(listing)
    print("Upload complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PortaPack Mayhem USB Serial CLI helper (RX-safe defaults)."
    )
    p.add_argument("--port", default=os.environ.get("MAYHEM_PORT", DEFAULT_PORT))
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--wait", type=float, default=5.0, help="Response wait for --cmd")
    p.add_argument(
        "--hackrf-timeout",
        type=float,
        default=30.0,
        help="Seconds to poll hackrf_info after --switch-to-hackrf",
    )

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="info + help + FREQMAN listing")
    g.add_argument("--screenshot", metavar="OUT.png", help="Capture screenframe to PNG")
    g.add_argument(
        "--switch-to-hackrf",
        action="store_true",
        help="Enter HackRF Native USB Mode (ends serial console)",
    )
    g.add_argument("--cmd", metavar="RAW", help="Send raw Mayhem serial command")
    g.add_argument("--upload", metavar="LOCAL", help="Upload file via fopen/fwrite")
    p.add_argument(
        "--remote",
        help="Remote path for --upload (default /FREQMAN/<NAME>)",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.status:
            return cmd_status(args)
        if args.screenshot:
            return cmd_screenshot(args)
        if args.switch_to_hackrf:
            return cmd_switch_to_hackrf(args)
        if args.cmd is not None:
            return cmd_raw(args)
        if args.upload:
            return cmd_upload(args)
    except serial.SerialException as e:
        print(f"Serial error on {args.port}: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Physical-layer RF emitter fingerprinting — CFO, rise, phase noise, spoof detect."""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pp_dashboard.config import settings
from pp_dashboard.subghz.analyze import load_cs8
from pp_dashboard.subghz.workbench import am_envelope, estimate_baud_fft

log = logging.getLogger(__name__)

FEATURE_KEYS = (
    "cfo_ppm",
    "clock_skew_ppm",
    "rise_time_us",
    "overshoot_db",
    "phase_noise_rad_rms",
)


def _as_complex64(
    cs8_path_or_iq: Path | str | bytes | bytearray | np.ndarray,
) -> np.ndarray:
    """Load IQ and normalize to roughly unit-fullscale complex64."""
    if isinstance(cs8_path_or_iq, (str, Path)):
        z = np.asarray(load_cs8(Path(cs8_path_or_iq)), dtype=np.complex128).ravel()
        # load_cs8 keeps int8 numeric range — normalize like CS8 → float
        return (z / 128.0).astype(np.complex64)
    if isinstance(cs8_path_or_iq, np.ndarray):
        arr = cs8_path_or_iq
        if np.iscomplexobj(arr):
            z = np.asarray(arr, dtype=np.complex128).ravel()
            peak = float(np.max(np.abs(z))) + 1e-12
            if peak > 2.0:
                z = z / 128.0
            return z.astype(np.complex64)
        iq8 = np.asarray(arr, dtype=np.int8).ravel()
    else:
        iq8 = np.frombuffer(bytes(cs8_path_or_iq), dtype=np.int8)
    if iq8.size % 2:
        iq8 = iq8[:-1]
    i = iq8[0::2].astype(np.float64) / 128.0
    q = iq8[1::2].astype(np.float64) / 128.0
    return (i + 1j * q).astype(np.complex64)


def _burst_mask(z: np.ndarray) -> np.ndarray:
    """Active samples above median envelope + 8 dB."""
    env = np.abs(z).astype(np.float64)
    if env.size < 16:
        return np.ones(env.size, dtype=bool)
    med = float(np.median(env) + 1e-12)
    med_db = 20.0 * math.log10(med)
    thr = 10.0 ** ((med_db + 8.0) / 20.0)
    mask = env >= thr
    if int(mask.sum()) < 32:
        # Fall back to top 25% magnitude samples
        thr2 = float(np.percentile(env, 75))
        mask = env >= thr2
    return mask


def _cfo_hz(z: np.ndarray, sample_rate: int) -> float:
    """Phase-difference autocorrelation CFO estimator (float64 accumulator)."""
    if z.size < 4:
        return 0.0
    z64 = np.asarray(z, dtype=np.complex128)
    prod = z64[1:] * np.conj(z64[:-1])
    s = np.sum(prod, dtype=np.complex128)
    ang = math.atan2(float(np.imag(s)), float(np.real(s)))
    return float(sample_rate) * ang / (2.0 * math.pi)


def _rise_and_overshoot(env: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """10%→90% rise time (µs) with sub-sample lerp + overshoot dB vs settled mean."""
    if env.size < 8:
        return 0.0, 0.0
    peak = float(np.max(env))
    if peak <= 1e-12:
        return 0.0, 0.0
    # Settled level: mean of last 40% of burst (above 50% peak)
    hi = env >= 0.5 * peak
    if not np.any(hi):
        return 0.0, 0.0
    first = int(np.argmax(hi))
    tail = env[max(first, int(0.6 * env.size)) :]
    settled = float(np.mean(tail)) if tail.size else peak
    settled = max(settled, 1e-12)
    lo_lvl = 0.1 * settled
    hi_lvl = 0.9 * settled

    def _cross(level: float) -> float:
        for i in range(1, env.size):
            a, b = float(env[i - 1]), float(env[i])
            if a < level <= b:
                frac = 0.0 if abs(b - a) < 1e-15 else (level - a) / (b - a)
                return (i - 1) + frac
        return float(np.argmax(env))

    t10 = _cross(lo_lvl)
    t90 = _cross(hi_lvl)
    if t90 < t10:
        t10, t90 = t90, t10
    rise_us = (t90 - t10) * 1e6 / float(sample_rate)
    overshoot_db = 20.0 * math.log10(max(peak, 1e-12) / settled)
    return float(max(0.0, rise_us)), float(overshoot_db)


def _phase_noise_rms(z: np.ndarray, sample_rate: int, cfo_hz: float) -> float:
    if z.size < 8:
        return 0.0
    n = np.arange(z.size, dtype=np.float64)
    deramped = z * np.exp(-2j * math.pi * cfo_hz * n / float(sample_rate))
    phase = np.unwrap(np.angle(deramped))
    # Remove residual linear trend
    if phase.size >= 2:
        t = np.arange(phase.size, dtype=np.float64)
        coef = np.polyfit(t, phase, 1)
        resid = phase - np.polyval(coef, t)
    else:
        resid = phase - np.mean(phase)
    return float(np.sqrt(np.mean(resid * resid)))


def _clock_skew_ppm(env: np.ndarray, sample_rate: int) -> float:
    """Fractional baud offset: fine zero-cross rate vs workbench FFT baud."""
    if env.size < 64:
        return 0.0
    baud_fft = float(estimate_baud_fft(env.astype(np.float32), sample_rate=sample_rate))
    if baud_fft <= 1.0:
        return 0.0
    # Zero-crossing rate on centered envelope as alternate baud estimate
    x = env.astype(np.float64) - float(np.mean(env))
    signs = np.sign(x)
    signs[signs == 0] = 1
    zc = int(np.sum(signs[1:] * signs[:-1] < 0))
    baud_zc = 0.5 * zc * float(sample_rate) / max(env.size - 1, 1)
    if baud_zc <= 1.0:
        return 0.0
    return float((baud_zc - baud_fft) / baud_fft * 1e6)


def extract_rf_fingerprint(
    cs8_path_or_iq: Path | str | bytes | bytearray | np.ndarray,
    sample_rate: int,
    nominal_freq_hz: float,
) -> dict[str, float]:
    """Deterministic hardware signature from a CS8 burst or IQ array."""
    z_all = _as_complex64(cs8_path_or_iq)
    if z_all.size < 16:
        raise ValueError("IQ too short for RF fingerprint")
    # Promote magnitude math on float32 components (via complex64 storage)
    z_all = z_all.astype(np.complex64)
    env_all = np.abs(z_all).astype(np.float64)
    # Rise / overshoot need the turn-on transient — compute on full capture
    rise_us, over_db = _rise_and_overshoot(env_all, int(sample_rate))
    mask = _burst_mask(z_all)
    # Keep a contiguous span covering the active burst (mask holes break CFO).
    if np.any(mask):
        idx = np.flatnonzero(mask)
        i0, i1 = int(idx[0]), int(idx[-1]) + 1
        # Prefer interior high-SNR slice once rise settles
        span = i1 - i0
        if span > 64:
            pad = max(0, span // 10)
            i0 = min(i0 + pad, i1 - 32)
        z = z_all[i0:i1]
    else:
        z = z_all
    if z.size < 16:
        z = z_all
    # Weight CFO by high-envelope samples only (contiguous)
    env_z = np.abs(z).astype(np.float64)
    thr = float(np.median(env_z) + 1e-12)
    thr = 10.0 ** ((20.0 * math.log10(thr) + 8.0) / 20.0)
    hi = env_z >= thr
    z_cfo = z[hi] if int(hi.sum()) >= 32 else z
    # Still contiguous? hi may punch holes — use phase diffs only where both samples high
    z64 = np.asarray(z, dtype=np.complex128)
    env64 = env_z
    thr_c = thr
    both = (env64[1:] >= thr_c) & (env64[:-1] >= thr_c)
    if int(both.sum()) >= 16:
        prod = z64[1:][both] * np.conj(z64[:-1][both])
        s = np.sum(prod, dtype=np.complex128)
        ang = math.atan2(float(np.imag(s)), float(np.real(s)))
        cfo = float(sample_rate) * ang / (2.0 * math.pi)
    else:
        cfo = _cfo_hz(z_cfo if np.asarray(z_cfo).size >= 16 else z, int(sample_rate))
    f0 = max(float(nominal_freq_hz), 1.0)
    cfo_ppm = cfo / f0 * 1e6
    env = am_envelope(z)
    pn = _phase_noise_rms(z, int(sample_rate), cfo)
    skew = _clock_skew_ppm(env.astype(np.float64), int(sample_rate))
    return {
        "cfo_hz": float(cfo),
        "cfo_ppm": float(cfo_ppm),
        "clock_skew_ppm": float(skew),
        "rise_time_us": float(rise_us),
        "overshoot_db": float(over_db),
        "phase_noise_rad_rms": float(pn),
    }


def _feature_vec(fp: dict[str, float]) -> np.ndarray:
    return np.array([float(fp[k]) for k in FEATURE_KEYS], dtype=np.float64)


@dataclass
class EmitterFingerprintStore:
    """SQLite store of physical emitter clusters + digital-ID spoof tracking."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rf_emitters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    physical_emitter_id TEXT NOT NULL,
                    digital_device_id TEXT,
                    bits_hex TEXT,
                    cfo_hz REAL,
                    cfo_ppm REAL,
                    clock_skew_ppm REAL,
                    rise_time_us REAL,
                    overshoot_db REAL,
                    phase_noise_rad_rms REAL,
                    frequency_hz REAL,
                    ts REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rf_emitters_phy ON rf_emitters(physical_emitter_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rf_emitters_dig ON rf_emitters(digital_device_id)"
            )
            conn.commit()

    def list_all(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rf_emitters ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def next_phy_id(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT physical_emitter_id FROM rf_emitters ORDER BY id DESC LIMIT 200"
            ).fetchall()
        nums: list[int] = []
        for r in row:
            pid = str(r["physical_emitter_id"] or "")
            if pid.startswith("PHY-"):
                try:
                    nums.append(int(pid.split("-", 1)[1]))
                except ValueError:
                    pass
        n = (max(nums) + 1) if nums else 1
        return f"PHY-{n:03d}"

    def insert(
        self,
        *,
        physical_emitter_id: str,
        digital_device_id: str | None,
        bits_hex: str | None,
        fingerprint: dict[str, float],
        frequency_hz: float | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO rf_emitters (
                    physical_emitter_id, digital_device_id, bits_hex,
                    cfo_hz, cfo_ppm, clock_skew_ppm, rise_time_us, overshoot_db,
                    phase_noise_rad_rms, frequency_hz, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    physical_emitter_id,
                    digital_device_id,
                    bits_hex,
                    float(fingerprint["cfo_hz"]),
                    float(fingerprint["cfo_ppm"]),
                    float(fingerprint["clock_skew_ppm"]),
                    float(fingerprint["rise_time_us"]),
                    float(fingerprint["overshoot_db"]),
                    float(fingerprint["phase_noise_rad_rms"]),
                    frequency_hz,
                    time.time(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def rows_for_digital(self, digital_device_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rf_emitters WHERE digital_device_id=? ORDER BY id",
                (digital_device_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_phy_means(self) -> dict[str, tuple[np.ndarray, np.ndarray, int]]:
        """physical_id → (mean_vec, std_vec, count)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM rf_emitters").fetchall()
        buckets: dict[str, list[np.ndarray]] = {}
        for r in rows:
            pid = str(r["physical_emitter_id"])
            vec = np.array([float(r[k]) for k in FEATURE_KEYS], dtype=np.float64)
            buckets.setdefault(pid, []).append(vec)
        out: dict[str, tuple[np.ndarray, np.ndarray, int]] = {}
        for pid, vecs in buckets.items():
            m = np.mean(np.stack(vecs, axis=0), axis=0)
            s = np.std(np.stack(vecs, axis=0), axis=0)
            s = np.maximum(s, 1e-6)
            out[pid] = (m, s, len(vecs))
        return out


def _mahalanobis(x: np.ndarray, mean: np.ndarray, std: np.ndarray) -> float:
    return float(np.sqrt(np.sum(((x - mean) / std) ** 2)))


def analyze_and_cluster(
    store: EmitterFingerprintStore,
    cs8_path_or_iq: Path | str | bytes | bytearray | np.ndarray,
    *,
    sample_rate: int,
    nominal_freq_hz: float,
    digital_device_id: str | None = None,
    bits_hex: str | None = None,
    match_threshold: float = 3.0,
) -> dict[str, Any]:
    """Extract fingerprint, assign PHY-ID, and raise SPOOF/CLONE when digital ID mismatches PHY."""
    fp = extract_rf_fingerprint(cs8_path_or_iq, sample_rate, nominal_freq_hz)
    x = _feature_vec(fp)
    clone_alert = False
    alert_detail = ""

    # Spoof: same digital address, >4σ from that address's historical PHY cluster(s)
    if digital_device_id:
        prior = store.rows_for_digital(digital_device_id)
        if prior:
            # Stats over prior samples for this digital ID (use first PHY's cloud)
            phy_ids = {str(r["physical_emitter_id"]) for r in prior}
            vecs = np.stack(
                [
                    np.array([float(r[k]) for k in FEATURE_KEYS], dtype=np.float64)
                    for r in prior
                ],
                axis=0,
            )
            mean = np.mean(vecs, axis=0)
            std = np.maximum(np.std(vecs, axis=0), 1e-6)
            # Soft floor so a single prior sample still yields a finite σ for CFO/rise
            for i, key in enumerate(FEATURE_KEYS):
                if key == "cfo_ppm":
                    std[i] = max(std[i], 0.5)
                elif key == "rise_time_us":
                    std[i] = max(std[i], 0.5)
            dist = _mahalanobis(x, mean, std)
            # Also flag large single-feature z-scores on cfo_ppm / rise_time_us
            z_cfo = abs(x[0] - mean[0]) / std[0]
            z_rise = abs(x[2] - mean[2]) / std[2]
            if z_cfo > 4.0 or z_rise > 4.0 or dist > 4.0:
                clone_alert = True
                alert_detail = (
                    f"SPOOF / CLONE ALERT: digital={digital_device_id} "
                    f"z_cfo={z_cfo:.2f} z_rise={z_rise:.2f} mahal={dist:.2f} "
                    f"prior_phy={sorted(phy_ids)}"
                )

    # Cluster assign among known physical emitters
    phy_means = store.all_phy_means()
    best_id: str | None = None
    best_d = float("inf")
    for pid, (mean, std, _n) in phy_means.items():
        d = _mahalanobis(x, mean, np.maximum(std, 1e-6))
        if d < best_d:
            best_d = d
            best_id = pid

    if clone_alert or best_id is None or best_d > match_threshold:
        phy_id = store.next_phy_id()
    else:
        phy_id = best_id

    row_id = store.insert(
        physical_emitter_id=phy_id,
        digital_device_id=digital_device_id,
        bits_hex=bits_hex,
        fingerprint=fp,
        frequency_hz=nominal_freq_hz,
    )
    return {
        "ok": True,
        "id": row_id,
        "physical_emitter_id": phy_id,
        "digital_device_id": digital_device_id,
        "bits_hex": bits_hex,
        "fingerprint": fp,
        "clone_alert": clone_alert,
        "alert": alert_detail or None,
        "cluster_distance": None if best_id is None else best_d,
    }


def default_emitter_store() -> EmitterFingerprintStore:
    return EmitterFingerprintStore(settings.captures_dir / "rf_emitters.sqlite3")

"""Pre-render masked audio variants. Run offline, never during the live demo.

For every source WAV, writes one masked file per effect to audio/masked/
as <asset>__<effect>.wav.
"""
import wave
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = BASE_DIR / "audio" / "source"
MASKED_DIR = BASE_DIR / "audio" / "masked"

SAMPLE_RATE = 48000
NOISE_SEED = 0


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        frames = f.readframes(f.getnframes())
        return np.frombuffer(frames, dtype=np.int16).copy()


def write_wav(path: Path, samples: np.ndarray):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(samples.astype(np.int16).tobytes())


def white_noise(rng: np.random.Generator, n_samples: int, amplitude: int) -> np.ndarray:
    return (rng.uniform(-1.0, 1.0, n_samples) * amplitude).astype(np.int16)


def apply_static(samples: np.ndarray, rng: np.random.Generator, start_s: float, duration_s: float, amplitude: int) -> np.ndarray:
    out = samples.copy()
    start = min(int(start_s * SAMPLE_RATE), len(out))
    end = min(start + int(duration_s * SAMPLE_RATE), len(out))
    out[start:end] = white_noise(rng, end - start, amplitude)
    return out


def apply_dropout(samples: np.ndarray, start_s: float, duration_s: float) -> np.ndarray:
    out = samples.copy()
    start = min(int(start_s * SAMPLE_RATE), len(out))
    end = min(start + int(duration_s * SAMPLE_RATE), len(out))
    out[start:end] = 0
    return out


def apply_effect(samples: np.ndarray, effect: str) -> np.ndarray:
    rng = np.random.default_rng(NOISE_SEED)
    if effect == "STATIC_SHORT":
        return apply_static(samples, rng, start_s=0.3, duration_s=0.2, amplitude=8000)
    if effect == "STATIC_LONG":
        return apply_static(samples, rng, start_s=0.2, duration_s=0.6, amplitude=8000)
    if effect == "NOISE_BURST":
        return apply_static(samples, rng, start_s=0.1, duration_s=0.08, amplitude=28000)
    if effect == "DROPOUT_SHORT":
        return apply_dropout(samples, start_s=0.3, duration_s=0.25)
    raise ValueError(f"unknown effect {effect}")


EFFECTS = ["STATIC_SHORT", "STATIC_LONG", "NOISE_BURST", "DROPOUT_SHORT"]


def main():
    MASKED_DIR.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(SOURCE_DIR.glob("*.wav")):
        asset = source_path.stem
        samples = load_wav(source_path)
        for effect in EFFECTS:
            masked = apply_effect(samples, effect)
            out_path = MASKED_DIR / f"{asset}__{effect}.wav"
            write_wav(out_path, masked)
            print(f"wrote {out_path.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()

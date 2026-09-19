#!/usr/bin/env python3
"""Deterministically synthesize GameOpti Couch Mode audio assets.

Only mathematical oscillators and fixed numeric parameters are used. No sample,
recording, melody, random noise, or third-party asset is an input to this script.
"""

from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
from typing import Iterable, Iterator
import wave


EFFECT_SAMPLE_RATE = 22_050
MUSIC_SAMPLE_RATE = 44_100
MUSIC_SYNTH_RATE = 4_410
MUSIC_DURATION_SECONDS = 120.0
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "src/game_optimization_linux/assets/audio"
_TAU = 2.0 * math.pi
_PCM_MAX = 32_767


def _pcm_bytes(values: Iterable[float]) -> bytes:
    pcm = array(
        "h",
        (max(-_PCM_MAX, min(_PCM_MAX, round(value * _PCM_MAX))) for value in values),
    )
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def _write_mono(name: str, samples: Iterable[float]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT_DIR / name), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(EFFECT_SAMPLE_RATE)
        output.writeframes(_pcm_bytes(samples))


def _write_stereo(name: str, frames: Iterable[tuple[float, float]]) -> None:
    """Stream interleaved stereo PCM so the long music asset stays cheap to build."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT_DIR / name), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(MUSIC_SAMPLE_RATE)
        block: list[float] = []
        for left, right in frames:
            block.extend((left, right))
            if len(block) >= 32_768:
                output.writeframesraw(_pcm_bytes(block))
                block.clear()
        if block:
            output.writeframesraw(_pcm_bytes(block))
        output.writeframes(b"")


def _effect(
    name: str,
    duration: float,
    start_frequency: float,
    end_frequency: float,
    *,
    overtone: float = 1.5,
    level: float = 0.18,
) -> None:
    count = round(duration * EFFECT_SAMPLE_RATE)
    phase = 0.0
    values: list[float] = []
    for index in range(count):
        progress = index / max(1, count - 1)
        frequency = start_frequency + (end_frequency - start_frequency) * progress
        phase += _TAU * frequency / EFFECT_SAMPLE_RATE
        attack = min(1.0, progress / 0.10)
        release = min(1.0, (1.0 - progress) / 0.32)
        envelope = attack * release * (1.0 - 0.22 * progress)
        tone = math.sin(phase) + 0.24 * math.sin(phase * overtone)
        values.append(level * envelope * tone / 1.24)
    _write_mono(name, values)


def _midi_frequency(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def _smoothed(value: float) -> float:
    normalized = max(0.0, min(1.0, value))
    return normalized * normalized * (3.0 - 2.0 * normalized)


def _note_envelope(
    local_time: float,
    duration: float,
    attack: float,
    release: float,
) -> float:
    if local_time < 0.0 or local_time >= duration:
        return 0.0
    return _smoothed(local_time / attack) * _smoothed((duration - local_time) / release)


def _music_source_frames() -> Iterator[tuple[float, float]]:
    """Compose a seamless 48-bar, three-section instrumental menu loop.

    At 96 BPM each four-beat bar is 2.5 seconds, so 48 bars are exactly
    120 seconds. Every pad, bass, pulse, and motif voice has a local release
    before its event boundary; no oscillator tail crosses the loop seam.
    """

    bpm = 96.0
    beat_duration = 60.0 / bpm
    bar_duration = beat_duration * 4.0
    total_frames = round(MUSIC_DURATION_SECONDS * MUSIC_SYNTH_RATE)

    # D-minor-centred, original procedural progression. The final A-major bar
    # resolves naturally to the opening D minor when playback loops.
    chord_midi = {
        "dm": (50, 53, 57),
        "bb": (46, 50, 53),
        "f": (53, 57, 60),
        "c": (48, 52, 55),
        "gm": (55, 58, 62),
        "a": (57, 61, 64),
    }
    bass_midi = {"dm": 38, "bb": 34, "f": 41, "c": 36, "gm": 31, "a": 33}
    progression = (
        "dm", "bb", "f", "c",
        "dm", "gm", "bb", "a",
        "dm", "bb", "f", "c",
        "gm", "bb", "a", "a",
    )
    section_levels = (
        # pad, bass, pulse, motif
        (0.050, 0.062, 0.018, 0.032),
        (0.056, 0.076, 0.034, 0.046),
        (0.062, 0.068, 0.026, 0.040),
    )
    bass_steps = (
        (0, 7, 12, 7),
        (0, 12, 7, 10),
        (0, 7, 10, 12),
    )

    # Sparse original motif fragments. Events are (beat, MIDI note, duration,
    # pan), and every final-bar event ends well before the 120-second seam.
    motif_bars: list[tuple[tuple[float, int, float, float], ...]] = []
    motif_notes = (62, 65, 67, 69, 72, 69, 67, 65)
    for bar in range(48):
        section = bar // 16
        local_bar = bar % 16
        events: tuple[tuple[float, int, float, float], ...] = ()
        if section == 0 and local_bar in {3, 7, 11, 15}:
            start = (local_bar // 4) * 2
            events = (
                (1.0, motif_notes[start % len(motif_notes)], 0.46, 0.30),
                (2.5, motif_notes[(start + 1) % len(motif_notes)], 0.52, 0.70),
            )
        elif section == 1 and local_bar % 4 in {1, 3}:
            start = (local_bar * 2) % len(motif_notes)
            events = (
                (0.5, motif_notes[start], 0.40, 0.25),
                (1.5, motif_notes[(start + 1) % len(motif_notes)], 0.40, 0.52),
                (3.0, motif_notes[(start + 2) % len(motif_notes)], 0.48, 0.76),
            )
        elif section == 2 and local_bar % 4 in {0, 2}:
            start = (local_bar + 3) % len(motif_notes)
            events = (
                (0.75, motif_notes[start], 0.54, 0.68),
                (2.25, motif_notes[(start + 2) % len(motif_notes)], 0.58, 0.32),
            )
        motif_bars.append(events)

    chord_frequencies = {
        name: tuple(_midi_frequency(note) for note in notes)
        for name, notes in chord_midi.items()
    }
    bass_frequencies = {
        name: _midi_frequency(note) for name, note in bass_midi.items()
    }

    for frame_index in range(total_frames):
        time_value = frame_index / MUSIC_SYNTH_RATE
        bar_index = min(47, int(time_value / bar_duration))
        bar_time = time_value - bar_index * bar_duration
        section = bar_index // 16
        chord_name = progression[bar_index % len(progression)]
        pad_level, bass_level, pulse_level, motif_level = section_levels[section]

        # Wide, slowly breathing pad. Tiny opposite detunes create stereo width
        # without random noise or phase-discontinuous modulation.
        pad_envelope = _note_envelope(
            bar_time, bar_duration, attack=0.34, release=0.52
        )
        breath = 0.90 + 0.10 * math.sin(_TAU * bar_time / bar_duration)
        left_pad = 0.0
        right_pad = 0.0
        for voice_index, frequency in enumerate(chord_frequencies[chord_name]):
            phase_offset = (voice_index + 1) * 0.73
            left_phase = _TAU * frequency * 0.9985 * time_value + phase_offset
            right_phase = _TAU * frequency * 1.0015 * time_value - phase_offset
            left_pad += math.sin(left_phase) + 0.13 * math.sin(2.0 * left_phase + 0.2)
            right_pad += math.sin(right_phase) + 0.13 * math.sin(2.0 * right_phase - 0.2)
        pad_scale = pad_level * pad_envelope * breath / (3.0 * 1.13)
        left = left_pad * pad_scale
        right = right_pad * pad_scale

        # One articulated bass note per beat provides harmonic movement while
        # keeping every note locally faded at beat boundaries.
        beat_index = min(3, int(bar_time / beat_duration))
        beat_time = bar_time - beat_index * beat_duration
        bass_frequency = bass_frequencies[chord_name] * (
            2.0 ** (bass_steps[section][beat_index] / 12.0)
        )
        bass_envelope = _note_envelope(
            beat_time, beat_duration * 0.94, attack=0.025, release=0.14
        )
        bass_phase = _TAU * bass_frequency * beat_time
        bass = bass_level * bass_envelope * (
            math.sin(bass_phase) + 0.20 * math.sin(2.0 * bass_phase)
        ) / 1.20
        left += bass * 0.96
        right += bass

        # A soft eighth-note pulse grows in the middle section and thins again
        # in the final section. Alternating pan keeps it clear of the bass.
        pulse_duration = beat_duration / 2.0
        pulse_index = min(7, int(bar_time / pulse_duration))
        pulse_time = bar_time - pulse_index * pulse_duration
        pulse_enabled = (
            pulse_index in {0, 3, 4, 7}
            if section == 0
            else pulse_index % 2 == 1 if section == 1
            else pulse_index in {0, 2, 5, 7}
        )
        if pulse_enabled:
            pulse_note = chord_midi[chord_name][pulse_index % 3] + 12
            pulse_frequency = _midi_frequency(pulse_note)
            pulse_envelope = _note_envelope(
                pulse_time, pulse_duration * 0.82, attack=0.010, release=0.085
            )
            pulse = pulse_level * pulse_envelope * math.sin(
                _TAU * pulse_frequency * pulse_time
            )
            if pulse_index % 2:
                left += pulse * 0.38
                right += pulse
            else:
                left += pulse
                right += pulse * 0.38

        # Short motif notes appear only in selected bars and use fixed panning.
        for event_beat, note, event_duration, pan in motif_bars[bar_index]:
            event_time = bar_time - event_beat * beat_duration
            motif_envelope = _note_envelope(
                event_time, event_duration, attack=0.035, release=0.16
            )
            if motif_envelope <= 0.0:
                continue
            motif_phase = _TAU * _midi_frequency(note) * event_time
            motif = motif_level * motif_envelope * (
                math.sin(motif_phase) + 0.16 * math.sin(2.0 * motif_phase + 0.4)
            ) / 1.16
            left += motif * (1.0 - 0.55 * pan)
            right += motif * (0.45 + 0.55 * pan)

        # Conservative master gain leaves ample headroom for summed voices.
        yield left * 0.92, right * 0.92


def _music_frames() -> Iterator[tuple[float, float]]:
    """Upsample the deterministic band-limited composition to 44.1 kHz."""

    ratio = MUSIC_SAMPLE_RATE // MUSIC_SYNTH_RATE
    if ratio < 1 or MUSIC_SAMPLE_RATE % MUSIC_SYNTH_RATE:
        raise ValueError("music sample rates must have an integral ratio")
    source = iter(_music_source_frames())
    current = next(source)
    for following in source:
        for step in range(ratio):
            fraction = step / ratio
            yield (
                current[0] + (following[0] - current[0]) * fraction,
                current[1] + (following[1] - current[1]) * fraction,
            )
        current = following
    for step in range(ratio):
        fraction = step / ratio
        yield current[0] * (1.0 - fraction), current[1] * (1.0 - fraction)


def _ambient_loop() -> None:
    _write_stereo("menu-ambient.wav", _music_frames())


def main() -> None:
    _effect("navigate.wav", 0.045, 510.0, 570.0, level=0.13)
    _effect("confirm.wav", 0.095, 650.0, 840.0, level=0.17)
    _effect("back.wav", 0.085, 470.0, 350.0, level=0.15)
    _effect("open.wav", 0.125, 420.0, 720.0, level=0.14)
    _effect("close.wav", 0.110, 690.0, 390.0, level=0.13)
    _effect("adjust.wav", 0.055, 590.0, 620.0, level=0.11)
    _effect("error.wav", 0.155, 250.0, 205.0, overtone=1.41421356237, level=0.16)
    _effect("launch.wav", 0.240, 330.0, 660.0, overtone=2.0, level=0.18)
    _ambient_loop()


if __name__ == "__main__":
    main()

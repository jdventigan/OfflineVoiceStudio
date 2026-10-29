"""Offline text-to-speech helpers for Offline Voice Studio.

The implementation uses the Windows built-in Speech API (SAPI 5) through
``System.Speech``.  No network access, model downloads, or extra Python
packages are required.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import sounddevice as sd
import soundfile as sf


class TtsEngine:
    """Small adapter around Windows SAPI 5."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None

    def list_voices(self) -> list[str]:
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip() or "Could not list voices."
            )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: int,
        volume: int,
        output_path: Path | str,
        stop_event: Any = None,
    ) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass

        input_fd, input_path = tempfile.mkstemp(
            suffix=".txt", prefix="offline_voice_studio_tts_"
        )
        input_path = Path(input_path)
        try:
            with os.fdopen(input_fd, "w", encoding="utf-8") as handle:
                handle.write(text)

            input_escaped = str(input_path).replace("'", "''")
            output_escaped = str(output_path).replace("'", "''")
            voice_escaped = str(voice or "").replace("'", "''")
            voice_block = ""
            if voice_escaped:
                voice_block = f"if ('{voice_escaped}' -ne '') {{ $s.SelectVoice('{voice_escaped}') }}"

            script = (
                "$ErrorActionPreference = 'Stop'; "
                "Add-Type -AssemblyName System.Speech; "
                f"$text = [System.IO.File]::ReadAllText('{input_escaped}', [System.Text.Encoding]::UTF8); "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"{voice_block}; "
                f"$s.Rate = {int(rate)}; "
                f"$s.Volume = {int(volume)}; "
                f"$s.SetOutputToWaveFile('{output_escaped}'); "
                "$s.Speak($text); "
                "$s.Dispose()"
            )

            self._process = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            while self._process.poll() is None:
                if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                    self._process.terminate()
                    try:
                        self._process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.communicate()
                    self._process = None
                    raise RuntimeError("Speech synthesis cancelled.")
                time.sleep(0.1)

            stdout, stderr = self._process.communicate()
            return_code = self._process.returncode
            self._process = None
            if return_code != 0:
                raise RuntimeError(
                    (stderr or "").strip()
                    or (stdout or "").strip()
                    or "Speech synthesis failed."
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError("Speech synthesis did not create an audio file.")
        finally:
            try:
                input_path.unlink()
            except OSError:
                pass

    def play_wav(self, path: Path | str, stop_event: Any = None) -> None:
        data, samplerate = sf.read(str(path), dtype="float32")
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)

        sd.play(data, samplerate)
        try:
            stream = sd.get_stream()
        except RuntimeError:
            stream = None

        duration = 0.0
        try:
            duration = len(data) / float(samplerate)
        except (TypeError, ValueError, ZeroDivisionError):
            duration = 0.0
        deadline = time.monotonic() + duration + 5.0

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                sd.stop()
                return
            if stream is None or not getattr(stream, "active", False):
                break
            if time.monotonic() >= deadline:
                sd.stop()
                return
            time.sleep(0.05)

        if stop_event is None or not getattr(stop_event, "is_set", lambda: False)():
            sd.wait()

    def stop(self) -> None:
        process = self._process
        if process is not None:
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass
        try:
            sd.stop()
        except Exception:
            pass

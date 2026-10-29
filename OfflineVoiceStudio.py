from __future__ import annotations

import ctypes
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

import soundcard as sc
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel

from tts import TtsEngine


APP_TITLE = "Offline Voice Studio"
RECORDINGS_DIR = Path.home() / "Documents" / "Offline Voice Studio" / "Recordings"
IS_FROZEN = getattr(sys, "frozen", False)
PROJECT_DIRECTORY = (
    Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if IS_FROZEN
    else Path(__file__).resolve().parent
)
MEETING_TRANSCRIBER_DIRECTORY = PROJECT_DIRECTORY.parent / "MeetingTranscriber"


@dataclass(frozen=True)
class AudioInput:
    kind: str
    identifier: Any
    name: str
    sample_rate: int
    channels: int

    @property
    def label(self) -> str:
        source = "System audio" if self.kind == "system" else "Microphone input"
        return f"{source}: {self.name}"


class OfflineVoiceStudio:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x760")
        self.root.minsize(720, 650)
        self.root.configure(bg="#f1f4f1")

        self.device_options: list[AudioInput] = []
        self.audio_queue: queue.Queue[Any] = queue.Queue(maxsize=256)
        self.waveform_queue: queue.Queue[tuple[float, float]] = queue.Queue(maxsize=256)
        self.waveform_levels: deque[tuple[float, float]] = deque(maxlen=180)
        self.ui_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.Queue()
        self.queue_overflow = threading.Event()
        self.input_warning = threading.Event()
        self.stop_requested = threading.Event()
        self.stream: sd.InputStream | None = None
        self.capture_thread: threading.Thread | None = None
        self.writer_thread: threading.Thread | None = None
        self.recording_path: Path | None = None
        self.recording = False
        self.transcribing = False
        self.closing = False
        self.recording_started = 0.0
        self.stream_stop_error: str | None = None
        self.capture_error: str | None = None
        self.save_progress_value = tk.DoubleVar(value=0)
        self.save_progress_text = tk.StringVar(value="")
        self.save_progress_mode: str | None = None
        self.saving_recording = False
        self.frames_lock = threading.Lock()
        self.frames_captured = 0
        self.frames_written = 0

        self.status = tk.StringVar(value="Choose an audio source to begin.")
        self.timer = tk.StringVar(value="00:00")
        self.recording_name = tk.StringVar(value="No recording yet")
        self.music_mode = tk.BooleanVar(value=False)
        self.theme = tk.StringVar(value="System")
        self.tts_engine = TtsEngine()
        self.tts_voices = []
        self.tts_voice = tk.StringVar()
        self.tts_rate = tk.IntVar(value=0)
        self.tts_volume = tk.IntVar(value=100)
        self.speaking = False
        self.speak_stop_requested = threading.Event()
        self.speak_thread = None
        self.highlight_spoken_var = tk.BooleanVar(value=True)
        local_model_directory = PROJECT_DIRECTORY / "models" / "faster-whisper-small"
        model_directory = local_model_directory
        if not IS_FROZEN:
            sibling_model_directory = (
                MEETING_TRANSCRIBER_DIRECTORY / "models"
                / "faster-whisper-small"
            )
            if not self._missing_model_files(sibling_model_directory):
                model_directory = sibling_model_directory
        self.model_path = tk.StringVar(value=str(model_directory))
        self.device_name = tk.StringVar()

        self._configure_styles()
        self._build_interface()
        self.refresh_devices()
        self.refresh_tts_voices()
        self.apply_theme()
        self._poll_system_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._poll_ui_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f1f4f1")
        style.configure("TLabel", background="#f1f4f1", foreground="#253633", font=("Segoe UI", 10))
        style.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"), foreground="#193d36")
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("Accent.TButton", background="#167c68", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("disabled", "#9bb4ac"), ("active", "#116552")])
        style.configure("Record.TButton", background="#b64e3b", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Record.TButton", background=[("active", "#963b2d")])
        style.configure("TCombobox", padding=6)

    def _build_interface(self) -> None:
        self.header = tk.Frame(self.root, bg="#193d36", padx=20, pady=4)
        self.header.pack(fill="x")
        header_row = tk.Frame(self.header, bg=self.header.cget("bg"))
        header_row.pack(fill="x")
        tk.Label(
            header_row,
            text="OFFLINE VOICE STUDIO",
            bg="#193d36",
            fg="#f4faf6",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left", anchor="center")

        self.body_container = ttk.Frame(self.root)
        body_container = self.body_container
        body_container.pack(fill="both", expand=True)
        self.body_canvas = tk.Canvas(
            body_container, bg="#f1f4f1", highlightthickness=0
        )
        body_canvas = self.body_canvas
        body_scrollbar = ttk.Scrollbar(
            body_container, orient="vertical", command=body_canvas.yview
        )
        body_canvas.configure(yscrollcommand=body_scrollbar.set)
        body_scrollbar.pack(side="right", fill="y")
        body_canvas.pack(side="left", fill="both", expand=True)
        theme_row = tk.Frame(header_row, bg=self.header.cget("bg"))
        theme_row.pack(side="right", anchor="center", padx=0, pady=0)
        tk.Label(theme_row, text="Theme", bg=self.header.cget("bg"), fg="#ffffff", font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.theme_combo = ttk.Combobox(theme_row, textvariable=self.theme, values=["Light", "Dark", "System", "Solarized", "Nord", "High Contrast"], state="readonly", width=14)
        self.theme_combo.pack(side="left")
        self.theme_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_theme())

        body = ttk.Frame(body_canvas, padding=(12, 8))
        body_window = body_canvas.create_window(
            (0, 0), window=body, anchor="nw"
        )

        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        stt_tab = ttk.Frame(notebook, padding=(0, 4))
        tts_tab = ttk.Frame(notebook, padding=(0, 4))
        notebook.add(stt_tab, text="Speech to Text")
        notebook.add(tts_tab, text="Text to Speech")
        body = stt_tab

        def _update_body_scrollregion(_event=None):
            body_canvas.configure(scrollregion=body_canvas.bbox("all"))

        def _update_body_width(event):
            body_canvas.itemconfig(body_window, width=event.width)

        body.bind("<Configure>", _update_body_scrollregion)
        body_canvas.bind("<Configure>", _update_body_width)

        def _on_body_mousewheel(event):
            body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        body_canvas.bind_all("<MouseWheel>", _on_body_mousewheel)

        device_row = ttk.Frame(body)
        device_row.pack(fill="x", pady=(0, 14))
        ttk.Label(device_row, text="AUDIO SOURCE", style="Heading.TLabel").pack(side="left", padx=(0, 14))
        self.device_combo = ttk.Combobox(device_row, textvariable=self.device_name, state="readonly")
        self.device_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(device_row, text="Refresh", command=self.refresh_devices).pack(side="left", padx=(8, 0))

        self.record_panel = tk.Frame(body, bg="#ffffff", padx=14, pady=10, highlightthickness=1, highlightbackground="#dce5df")
        record_panel = self.record_panel
        record_panel = self.record_panel
        record_panel.pack(fill="x", pady=(0, 16))
        record_controls = tk.Frame(record_panel, bg="#ffffff")
        record_controls.pack(fill="x")
        self.record_button = ttk.Button(
            record_controls,
            text="Start Recording",
            style="Record.TButton",
            command=self.start_recording,
        )
        self.record_button.pack(side="left")
        self.open_file_button = ttk.Button(
            record_controls,
            text="Open Audio/Video",
            command=self.open_audio_file,
        )
        self.open_file_button.pack(side="left", padx=(8, 0))
        tk.Label(
            record_controls,
            textvariable=self.timer,
            bg="#ffffff",
            fg="#193d36",
            font=("Segoe UI", 19, "bold"),
        ).pack(side="left", padx=18)
        tk.Label(
            record_panel,
            textvariable=self.recording_name,
            bg="#ffffff",
            fg="#54645e",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(10, 0))
        tk.Label(
            record_panel,
            text="LIVE INPUT",
            bg="#ffffff",
            fg="#54645e",
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(10, 4))
        self.waveform_canvas = tk.Canvas(
            record_panel,
            height=56,
            bg="#193d36",
            highlightthickness=0,
        )
        self.waveform_canvas.pack(fill="x")
        self.waveform_canvas.bind("<Configure>", self._draw_waveform)
        self._draw_waveform()
        self.save_progress_frame = ttk.Frame(record_panel)
        ttk.Label(
            self.save_progress_frame,
            textvariable=self.save_progress_text,
            style="TLabel",
        ).pack(fill="x", pady=(8, 4))
        self.save_progress_bar = ttk.Progressbar(
            self.save_progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            variable=self.save_progress_value,
        )
        self.save_progress_bar.pack(fill="x")

        transcript_heading = ttk.Frame(body)
        transcript_heading.pack(fill="x", pady=(0, 6))
        ttk.Label(transcript_heading, text="TRANSCRIPT", style="Heading.TLabel").pack(side="left")
        self.transcribe_button = ttk.Button(
            transcript_heading,
            text="Transcribe Recording",
            style="Accent.TButton",
            command=self.transcribe_recording,
        )
        self.transcribe_button.pack(side="right")
        self.music_mode_button = ttk.Checkbutton(
            transcript_heading,
            text="Music / lyrics",
            variable=self.music_mode,
        )
        self.music_mode_button.pack(side="right", padx=(0, 10))
        ttk.Button(
            transcript_heading,
            text="Save Transcript",
            command=self.save_transcript,
        ).pack(side="right", padx=(0, 8))

        self.transcript = scrolledtext.ScrolledText(
            body,
            wrap=tk.WORD,
            height=8,
            font=("Segoe UI", 10),
            bg="#ffffff",
            fg="#253633",
            insertbackground="#167c68",
            relief="flat",
            padx=12,
            pady=10,
        )
        self.transcript.pack(fill="both", expand=True)
        self.transcript.tag_configure("speaking", background="#fff3cd", foreground="#193d36")

        ttk.Label(tts_tab, text="TEXT TO SPEAK", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        self.tts_input = scrolledtext.ScrolledText(
            tts_tab,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            bg="#ffffff",
            fg="#253633",
            insertbackground="#167c68",
            relief="flat",
            padx=12,
            pady=10,
            height=4,
        )
        self.tts_input.pack(side="top", fill="both", expand=True, pady=(0, 6))
        self.tts_input.tag_configure(
            "speaking", background="#fff3cd", foreground="#193d36"
        )
        tts_toolbar = ttk.Frame(tts_tab)
        tts_toolbar.pack(side="top", fill="x", pady=(0, 6))
        ttk.Button(
            tts_toolbar, text="Append Transcript", command=self.append_transcript_into_tts
        ).pack(side="left")
        ttk.Button(
            tts_toolbar, text="Open Text File...", command=self.open_text_file_for_tts
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            tts_toolbar, text="Clear Text",
            command=lambda: self.tts_input.delete("1.0", tk.END),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            tts_toolbar, text="Install Voices...", command=self.open_voice_settings
        ).pack(side="left", padx=(8, 0))
        self._build_tts_panel(tts_tab)

        self.status_bar = tk.Frame(self.root, bg="#dfe8e2", padx=22, pady=9)
        status_bar = self.status_bar
        status_bar.pack(fill="x", side="bottom")
        tk.Label(
            status_bar,
            textvariable=self.status,
            bg="#dfe8e2",
            fg="#36544a",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            status_bar,
            text="Jerome Ventigan | Powered by Visual Studio Code",
            bg="#dfe8e2",
            fg="#54645e",
            anchor="e",
            font=("Segoe UI", 8),
        ).pack(side="right", padx=(12, 0))

    def refresh_devices(self) -> None:
        try:
            self.device_options = []
            default_speaker_id = None
            try:
                default_speaker_id = sc.default_speaker().id
                for microphone in sc.all_microphones(include_loopback=True):
                    if microphone.isloopback:
                        self.device_options.append(
                            AudioInput(
                                "system",
                                microphone.id,
                                microphone.name,
                                48000,
                                int(microphone.channels),
                            )
                        )
            except Exception:
                pass

            for index, device in enumerate(sd.query_devices()):
                input_channels = int(device.get("max_input_channels", 0))
                if input_channels > 0:
                    self.device_options.append(
                        AudioInput(
                            "microphone",
                            index,
                            str(device["name"]),
                            int(device.get("default_samplerate", 16000)),
                            input_channels,
                        )
                    )

            labels = [device.label for device in self.device_options]
            self.device_combo["values"] = labels
            if labels:
                default_index = next(
                    (
                        index
                        for index, device in enumerate(self.device_options)
                        if device.kind == "system" and device.identifier == default_speaker_id
                    ),
                    next((index for index, device in enumerate(self.device_options) if device.kind == "system"), 0),
                )
                self.device_combo.current(default_index)
                selected = self.device_options[default_index]
                if selected.kind == "system":
                    self.status.set(f"Ready to capture playback from {selected.name}.")
                else:
                    self.status.set(f"{len(labels)} audio source(s) ready.")
            else:
                self.device_name.set("")
                self.status.set("No audio input or system loopback source was found.")
        except Exception as exc:
            self.status.set("Could not read audio devices.")
            messagebox.showerror("Audio device error", str(exc), parent=self.root)

    def start_recording(self) -> None:
        if not self.device_options:
            messagebox.showwarning("No microphone", "Connect or enable a microphone, then refresh the device list.", parent=self.root)
            return

        device_index = self.device_combo.current()
        if device_index < 0:
            messagebox.showwarning("Choose a microphone", "Select an input device before recording.", parent=self.root)
            return

        selected_device = self.device_options[device_index]
        sample_rate = selected_device.sample_rate
        if not self._is_local_path(RECORDINGS_DIR):
            messagebox.showerror(
                "Local recording folder required",
                "The Documents recording folder is on a network drive. Choose a local Windows account folder to keep recordings offline.",
                parent=self.root,
            )
            return
        try:
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Recording folder error", str(exc), parent=self.root)
            return

        self.recording_path = RECORDINGS_DIR / f"recording_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
        audio_queue: queue.Queue[Any] = queue.Queue(maxsize=256)
        waveform_queue: queue.Queue[tuple[float, float]] = queue.Queue(maxsize=256)
        self.audio_queue = audio_queue
        self.waveform_queue = waveform_queue
        self.waveform_levels.clear()
        self._draw_waveform()
        self.queue_overflow = threading.Event()
        self.input_warning = threading.Event()
        self.stop_requested = threading.Event()
        self.stream_stop_error = None
        self.capture_error = None
        self.capture_thread = None
        self.saving_recording = False
        self.frames_captured = 0
        self.frames_written = 0
        self.save_progress_frame.pack(fill="x", pady=(8, 0))
        self.save_progress_value.set(0)
        self.save_progress_text.set("Saving WAV as you record...")
        self.recording = True
        self.recording_started = time.monotonic()
        self.record_button.configure(text="Stop Recording", command=self.stop_recording)
        self.open_file_button.configure(state="disabled")
        self.transcribe_button.configure(state="disabled")
        self.status.set(f"Recording {selected_device.label}...")
        self.recording_name.set(self.recording_path.name)

        try:
            queue_overflow = self.queue_overflow
            input_warning = self.input_warning
            self.writer_thread = threading.Thread(
                target=self._write_recording,
                args=(
                    self.recording_path,
                    sample_rate,
                    audio_queue,
                    self.stop_requested,
                    queue_overflow,
                    input_warning,
                ),
                daemon=True,
            )
            self.writer_thread.start()
            if selected_device.kind == "system":
                self.capture_thread = threading.Thread(
                    target=self._capture_system_audio,
                    args=(
                        selected_device,
                        audio_queue,
                        waveform_queue,
                        self.stop_requested,
                        queue_overflow,
                        input_warning,
                    ),
                    daemon=True,
                )
                self.capture_thread.start()
            else:
                self.stream = sd.InputStream(
                    device=selected_device.identifier,
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=lambda audio, frames, timing, status: self._capture_audio(
                        audio,
                        status,
                        audio_queue,
                        waveform_queue,
                        queue_overflow,
                        input_warning,
                    ),
                )
                self.stream.start()
            self._update_timer()
            self.root.after(100, self._update_write_progress)
        except Exception as exc:
            self.recording = False
            self._stop_input_stream()
            self.stop_requested.set()
            self.record_button.configure(text="Start Recording", command=self.start_recording)
            self.open_file_button.configure(state="normal")
            self.transcribe_button.configure(state="normal")
            self.status.set("Recording could not start.")
            messagebox.showerror("Recording error", str(exc), parent=self.root)

    def _capture_audio(
        self,
        audio: Any,
        status: Any,
        audio_queue: queue.Queue[Any],
        waveform_queue: queue.Queue[tuple[float, float]],
        queue_overflow: threading.Event,
        input_warning: threading.Event,
    ) -> None:
        if status:
            input_warning.set()
        try:
            block = audio.copy()
            audio_queue.put_nowait(block)
        except queue.Full:
            queue_overflow.set()
        else:
            with self.frames_lock:
                self.frames_captured += block.shape[0]
        self._enqueue_waveform(audio, waveform_queue)

    @staticmethod
    def _enqueue_waveform(audio: Any, waveform_queue: queue.Queue[tuple[float, float]]) -> None:
        samples = audio[:, 0] if getattr(audio, "ndim", 1) > 1 else audio
        envelope = (float(samples.min()), float(samples.max()))
        try:
            waveform_queue.put_nowait(envelope)
        except queue.Full:
            try:
                waveform_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                waveform_queue.put_nowait(envelope)
            except queue.Full:
                pass

    def _capture_system_audio(
        self,
        device: AudioInput,
        audio_queue: queue.Queue[Any],
        waveform_queue: queue.Queue[tuple[float, float]],
        stop_requested: threading.Event,
        queue_overflow: threading.Event,
        input_warning: threading.Event,
    ) -> None:
        try:
            if device.channels < 2:
                raise RuntimeError("System audio loopback requires a stereo output device.")
            loopback = sc.get_microphone(device.identifier, include_loopback=True)
            with loopback.recorder(
                samplerate=device.sample_rate,
                channels=[0, 1],
                blocksize=4096,
            ) as recorder:
                while not stop_requested.is_set():
                    audio = recorder.record(numframes=2048)
                    if audio.size == 0:
                        continue
                    mono_audio = audio.mean(axis=1, keepdims=True).astype("float32", copy=False)
                    try:
                        audio_queue.put_nowait(mono_audio)
                    except queue.Full:
                        queue_overflow.set()
                    else:
                        with self.frames_lock:
                            self.frames_captured += mono_audio.shape[0]
                    self._enqueue_waveform(mono_audio, waveform_queue)
        except Exception as exc:
            self.capture_error = str(exc)
            stop_requested.set()

    def _write_recording(
        self,
        path: Path,
        sample_rate: int,
        audio_queue: queue.Queue[Any],
        stop_requested: threading.Event,
        queue_overflow: threading.Event,
        input_warning: threading.Event,
    ) -> None:
        error: Exception | None = None
        try:
            with sf.SoundFile(path, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16") as output:
                while True:
                    try:
                        block = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        if stop_requested.is_set():
                            break
                        continue
                    if block is None:
                        break
                    output.write(block)
                    with self.frames_lock:
                        self.frames_written += block.shape[0]
        except Exception as exc:
            error = exc
        self._schedule_ui(
            self._recording_saved,
            path,
            error,
            queue_overflow.is_set(),
            input_warning.is_set(),
            self.stream_stop_error,
            self.capture_error,
        )

    def _schedule_ui(self, callback: Callable[..., Any], *args: Any) -> None:
        if self.closing:
            return
        self.ui_queue.put((callback, args))

    def _poll_ui_queue(self) -> None:
        if self.closing:
            return
        while True:
            try:
                callback, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            callback(*args)
        waveform_changed = False
        while True:
            try:
                self.waveform_levels.append(self.waveform_queue.get_nowait())
                waveform_changed = True
            except queue.Empty:
                break
        if waveform_changed:
            self._draw_waveform()
        self.root.after(50, self._poll_ui_queue)

    def _draw_waveform(self, event: Any = None) -> None:
        if not hasattr(self, "waveform_canvas"):
            return
        canvas = self.waveform_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 2)
        height = max(canvas.winfo_height(), 2)
        center = height / 2
        amplitude = height * 0.44
        canvas.create_line(0, center, width, center, fill="#52746b", width=1)

        samples = list(self.waveform_levels)
        if not samples:
            canvas.create_text(
                width / 2,
                center,
                text="Waiting for microphone input",
                fill="#c2d7ce",
                font=("Segoe UI", 9),
            )
            return

        spacing = width / len(samples)
        line_width = max(1, min(3, int(spacing * 0.6)))
        for index, (minimum, maximum) in enumerate(samples):
            x = (index + 0.5) * spacing
            top = center - maximum * amplitude
            bottom = center - minimum * amplitude
            if bottom - top < 2:
                top -= 1
                bottom += 1
            canvas.create_line(x, top, x, bottom, fill="#71d9a8", width=line_width)

    def _stop_input_stream(self) -> str | None:
        stream = self.stream
        self.stream = None
        if stream is None:
            return None

        error = None
        try:
            stream.stop()
        except Exception as exc:
            error = str(exc)
        try:
            stream.close()
        except Exception as exc:
            error = error or str(exc)
        return error

    def stop_recording(self) -> None:
        if not self.recording:
            return

        self.recording = False
        self.stream_stop_error = self._stop_input_stream()
        self.stop_requested.set()
        self.saving_recording = True
        self.save_progress_value.set(0)
        self.save_progress_text.set("Finishing audio capture...")
        self.save_progress_frame.pack(fill="x", pady=(8, 0))
        self._set_save_progress_mode("indeterminate")
        self.root.after(100, self._update_save_progress)
        self.open_file_button.configure(state="disabled")
        self.record_button.configure(text="Saving Recording...", state="disabled")
        self.status.set("Finishing and saving the WAV recording...")

    def _set_save_progress_mode(self, mode: str) -> None:
        if self.save_progress_mode == mode:
            return
        if self.save_progress_mode == "indeterminate":
            self.save_progress_bar.stop()
        self.save_progress_bar.configure(mode=mode)
        self.save_progress_mode = mode
        if mode == "indeterminate":
            self.save_progress_bar.start(12)

    def _update_save_progress(self) -> None:
        if not self.saving_recording:
            return

        capture_running = self.capture_thread is not None and self.capture_thread.is_alive()
        writer_running = self.writer_thread is not None and self.writer_thread.is_alive()
        if capture_running:
            self._set_save_progress_mode("indeterminate")
            self.save_progress_text.set("Finishing audio capture...")
        else:
            with self.frames_lock:
                frames_captured = self.frames_captured
                frames_written = self.frames_written

            if frames_captured:
                self._set_save_progress_mode("determinate")
                percent = min(99, int(frames_written * 100 / frames_captured))
                self.save_progress_value.set(percent)
                self.save_progress_text.set(f"Saving recording: {percent}%")
            else:
                self._set_save_progress_mode("indeterminate")
                self.save_progress_text.set("Finalizing recording...")

        if capture_running or writer_running:
            self.root.after(100, self._update_save_progress)

    def _finish_save_progress(self, saved: bool) -> None:
        self.saving_recording = False
        self._set_save_progress_mode("determinate")
        if not saved:
            self.save_progress_frame.pack_forget()
            self.save_progress_value.set(0)
            return
        self.save_progress_value.set(100)
        self.save_progress_text.set("Recording saved")

    def _recording_saved(
        self,
        path: Path,
        error: Exception | None,
        queue_overflow: bool,
        input_warning: bool,
        stream_stop_error: str | None,
        capture_error: str | None,
    ) -> None:
        self.recording = False
        self.record_button.configure(text="Start Recording", state="normal", command=self.start_recording)
        self.open_file_button.configure(state="normal")
        self.transcribe_button.configure(state="normal")
        if error:
            self._finish_save_progress(saved=False)
            self.status.set("Recording could not be saved.")
            messagebox.showerror("Save error", str(error), parent=self.root)
            return
        try:
            if sf.info(path).frames == 0:
                self._finish_save_progress(saved=False)
                self.recording_path = None
                self.recording_name.set(f"No audio captured: {path.name}")
                if capture_error:
                    self.status.set("System audio capture failed.")
                    messagebox.showerror("System audio error", capture_error, parent=self.root)
                else:
                    self.status.set("No audio samples were captured. Check the selected audio source and try again.")
                return
        except Exception as exc:
            self._finish_save_progress(saved=False)
            self.recording_path = None
            self.status.set("The recording could not be verified.")
            messagebox.showerror("Recording error", str(exc), parent=self.root)
            return
        self.recording_path = path
        self._finish_save_progress(saved=True)
        self.recording_name.set(f"Saved: {path}")
        warnings = []
        if queue_overflow:
            warnings.append("capture fell behind; some audio may be missing")
        if input_warning:
            warnings.append("the input device reported an audio warning")
        if stream_stop_error:
            warnings.append("the audio device reported a stop error")
        if capture_error:
            warnings.append(f"audio capture stopped unexpectedly: {capture_error}")
        if warnings:
            self.status.set("Recording saved with warning: " + "; ".join(warnings) + ".")
        else:
            self.status.set("Recording saved. Ready to transcribe offline.")

    def _update_timer(self) -> None:
        if not self.recording:
            return
        elapsed = int(time.monotonic() - self.recording_started)
        self.timer.set(f"{elapsed // 60:02}:{elapsed % 60:02}")
        if self.queue_overflow.is_set():
            self.status.set("Recording continues; audio writing is falling behind.")
        elif self.input_warning.is_set():
            self.status.set("Recording continues; the input device reported an audio warning.")
        self.root.after(250, self._update_timer)

    def _update_write_progress(self) -> None:
        if not self.recording:
            return
        with self.frames_lock:
            frames_captured = self.frames_captured
            frames_written = self.frames_written
        if frames_captured:
            percent = min(100, int(frames_written * 100 / frames_captured))
            self.save_progress_value.set(percent)
            self.save_progress_text.set(f"Writing WAV: {percent}%")
        else:
            self.save_progress_text.set("Waiting for audio to write...")
        self.root.after(250, self._update_write_progress)

    def open_audio_file(self) -> None:
        if self.recording or self.transcribing:
            return
        selected = filedialog.askopenfilename(
            title="Open audio or video for transcription",
            filetypes=[
                (
                    "Audio and video files",
                    "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.opus "
                    "*.mp4 *.mkv *.mov *.avi *.webm *.wmv *.mpeg *.mpg *.m4v *.3gp",
                ),
                ("All files", "*.*"),
            ],
            parent=self.root,
        )
        if not selected:
            return

        path = Path(selected)
        if not self._is_local_path(path):
            messagebox.showerror(
                "Local file required",
                "Choose an audio or video file stored on a local drive. Network paths are blocked to keep processing offline.",
                parent=self.root,
            )
            return
        if not path.is_file():
            messagebox.showerror("Audio file not found", "The selected file is no longer available.", parent=self.root)
            return
        if path.stat().st_size == 0:
            messagebox.showerror("Empty audio file", "The selected audio file is empty.", parent=self.root)
            return

        self.recording_path = path
        self.recording_name.set(f"Selected file: {path.name}")
        self.timer.set("--:--")
        self.transcript.delete("1.0", tk.END)
        self.status.set("Audio file selected. Ready to transcribe locally.")

    @staticmethod
    def _missing_model_files(model_directory: Path) -> list[str]:
        required_files = ("config.json", "model.bin")
        return [name for name in required_files if not (model_directory / name).is_file()]

    @staticmethod
    def _is_local_path(path: Path) -> bool:
        path_text = str(path)
        if path_text.startswith(("\\\\", "//")):
            return False
        if os.name != "nt":
            return True

        drive, _ = os.path.splitdrive(os.path.abspath(path_text))
        if not drive:
            return False
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        return get_drive_type(drive + "\\") in (2, 3, 5, 6)

    def transcribe_recording(self) -> None:
        if self.recording:
            messagebox.showwarning("Recording in progress", "Stop and save the recording before transcribing it.", parent=self.root)
            return
        if self.transcribing:
            return
        if not self.recording_path or not self.recording_path.is_file():
            messagebox.showwarning("No recording", "Record audio before starting transcription.", parent=self.root)
            return

        model_directory = Path(self.model_path.get().strip())
        if not self._is_local_path(model_directory):
            messagebox.showerror(
                "Local model required",
                "The transcription model must be stored on a local drive. Network paths are blocked.",
                parent=self.root,
            )
            return
        if not model_directory.is_dir():
            messagebox.showerror(
                "Local model not found",
                "The selected model folder does not exist:\n"
                f"{model_directory}\n\n"
                "Choose a local Faster-Whisper model folder. This app does not download models.",
                parent=self.root,
            )
            return
        missing_files = self._missing_model_files(model_directory)
        if missing_files:
            messagebox.showerror(
                "Invalid local model",
                "The selected folder is missing required Faster-Whisper files: "
                + ", ".join(missing_files),
                parent=self.root,
            )
            return

        music_mode = self.music_mode.get()
        self.transcribing = True
        self.record_button.configure(state="disabled")
        self.open_file_button.configure(state="disabled")
        self.transcribe_button.configure(state="disabled")
        self.music_mode_button.configure(state="disabled")
        self.transcript.delete("1.0", tk.END)
        task = "lyrics" if music_mode else "speech"
        self.save_progress_frame.pack(fill="x", pady=(8, 0))
        self.save_progress_value.set(0)
        self.save_progress_text.set("Preparing transcription...")
        self._set_save_progress_mode("indeterminate")
        self.status.set(f"Loading the local model and transcribing {task} in {self.recording_path.name}...")
        threading.Thread(
            target=self._transcribe_worker,
            args=(self.recording_path, model_directory, music_mode),
            daemon=True,
        ).start()

    def _transcribe_worker(
        self,
        audio_path: Path,
        model_directory: Path,
        music_mode: bool,
    ) -> None:
        try:
            speaker_intervals = []
            if not music_mode:
                self._schedule_ui(self.status.set, "Detecting speakers with the local model...")
                self._schedule_ui(self._set_transcription_progress_phase, "Detecting speakers...")
                speaker_intervals = self._run_diarization(audio_path)

            self._schedule_ui(self.status.set, "Loading the local transcription model...")
            self._schedule_ui(self._set_transcription_progress_phase, "Loading local model...")
            model = WhisperModel(
                str(model_directory),
                device="cpu",
                compute_type="int8",
                local_files_only=True,
            )
            segments, info = model.transcribe(
                str(audio_path),
                beam_size=5,
                vad_filter=not music_mode,
            )
            duration = float(getattr(info, "duration", 0) or 0)
            self._schedule_ui(self._begin_transcription_progress, duration)
            found_text = False
            for segment in segments:
                if duration > 0:
                    percent = min(99, int(segment.end * 100 / duration))
                    self._schedule_ui(self._update_transcription_progress, percent)
                text = segment.text.strip()
                if not text:
                    continue
                found_text = True
                if music_mode:
                    speaker = "Music / vocals"
                else:
                    speaker = self._identify_speaker(
                        segment.start,
                        segment.end,
                        speaker_intervals,
                    )
                line = (
                    f"[{self._format_time(segment.start)} - "
                    f"{self._format_time(segment.end)}] {speaker}: {text}"
                )
                self._schedule_ui(self._append_transcript, line)
            if not found_text:
                self._schedule_ui(self._append_transcript, "No speech was detected in this recording.")
            self._schedule_ui(
                self._transcription_finished,
                info.language,
                music_mode,
                bool(speaker_intervals),
            )
        except Exception as exc:
            self._schedule_ui(self._transcription_failed, str(exc))

    def _speaker_resources(self) -> tuple[Path, Path, Path, Path]:
        bundled_root = PROJECT_DIRECTORY
        sibling_root = MEETING_TRANSCRIBER_DIRECTORY
        candidates = [
            (
                bundled_root / "speaker_diarization" / "speaker_diarization.exe",
                bundled_root / "ffmpeg" / "ffmpeg.exe",
                bundled_root / "speaker_models" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx",
                bundled_root / "speaker_models" / "speaker_embedding.onnx",
            )
        ]
        if not IS_FROZEN:
            candidates.append(
                (
                sibling_root / "packaging" / "app_dist" / "MeetingTranscriber" / "speaker_diarization" / "speaker_diarization.exe",
                sibling_root / "packaging" / "app_dist" / "MeetingTranscriber" / "ffmpeg" / "ffmpeg.exe",
                sibling_root / "speaker_models" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx",
                sibling_root / "speaker_models" / "speaker_embedding.onnx",
                )
            )
        for resources in candidates:
            if all(self._is_local_path(path) and path.is_file() for path in resources):
                return resources
        raise FileNotFoundError(
            "Local speaker-detection files were not found. Place the speaker "
            "runtime, FFmpeg, and ONNX models in this project or the adjacent "
            "MeetingTranscriber project. No downloads are performed."
        )

    def _run_diarization(self, audio_path: Path) -> list[tuple[float, float, int]]:
        diarization_exe, ffmpeg_exe, segmentation_model, embedding_model = self._speaker_resources()
        with tempfile.TemporaryDirectory(prefix="offline_voice_speakers_") as temp_directory:
            wav_path = Path(temp_directory) / "diarization_input.wav"
            conversion = subprocess.run(
                [
                    str(ffmpeg_exe),
                    "-y",
                    "-i", str(audio_path),
                    "-vn",
                    "-af", "apad=pad_dur=10",
                    "-ac", "1",
                    "-ar", "16000",
                    "-c:a", "pcm_s16le",
                    str(wav_path),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if conversion.returncode != 0:
                raise RuntimeError(
                    "Could not prepare audio for speaker detection:\n"
                    + conversion.stderr[-2000:]
                )

            result = subprocess.run(
                [
                    str(diarization_exe),
                    "--seg-model", str(segmentation_model),
                    "--speaker-embedding-model", str(embedding_model),
                    "--wav", str(wav_path),
                ],
                cwd=str(diarization_exe.parent),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "Local speaker detection failed:\n"
                    + (result.stderr[-2000:] or result.stdout[-2000:])
                )

        pattern = re.compile(
            r"^\s*(\d+):(\d{2}):(\d{2}\.\d+)\s+-->\s+"
            r"(\d+):(\d{2}):(\d{2}\.\d+)\s+speaker_(\d+)\s*$"
        )
        intervals = []
        for line in result.stdout.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            h1, m1, s1, h2, m2, s2, speaker = match.groups()
            start = int(h1) * 3600 + int(m1) * 60 + float(s1)
            end = int(h2) * 3600 + int(m2) * 60 + float(s2)
            intervals.append((start, end, int(speaker)))

        intervals.sort(key=lambda item: item[0])
        if not intervals:
            return []
        return intervals

    @staticmethod
    def _identify_speaker(
        start: float,
        end: float,
        intervals: list[tuple[float, float, int]],
    ) -> str:
        best_speaker = None
        best_overlap = 0.0
        for speaker_start, speaker_end, speaker_id in intervals:
            overlap = max(0.0, min(end, speaker_end) - max(start, speaker_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker_id
        if best_speaker is None:
            return "Speaker unknown"
        return f"Speaker {best_speaker + 1}"

    def _append_transcript(self, line: str) -> None:
        if self.transcript.get("1.0", tk.END).strip():
            self.transcript.insert(tk.END, "\n\n")
        self.transcript.insert(tk.END, line)
        self.transcript.see(tk.END)

    def _set_transcription_progress_phase(self, phase: str) -> None:
        self._set_save_progress_mode("indeterminate")
        self.save_progress_text.set(phase)

    def _begin_transcription_progress(self, duration: float) -> None:
        if duration <= 0:
            self._set_transcription_progress_phase("Transcribing...")
            return
        self._set_save_progress_mode("determinate")
        self.save_progress_value.set(0)
        self.save_progress_text.set("Transcribing: 0%")

    def _update_transcription_progress(self, percent: int) -> None:
        current = int(self.save_progress_value.get())
        percent = max(current, min(99, max(0, percent)))
        self.save_progress_value.set(percent)
        self.save_progress_text.set(f"Transcribing: {percent}%")

    def _transcription_finished(
        self,
        language: str,
        music_mode: bool,
        speaker_intervals_found: bool,
    ) -> None:
        self.transcribing = False
        self.record_button.configure(state="normal")
        self.open_file_button.configure(state="normal")
        self.transcribe_button.configure(state="normal")
        self.music_mode_button.configure(state="normal")
        self._set_save_progress_mode("determinate")
        self.save_progress_value.set(100)
        if music_mode:
            self.save_progress_text.set("Lyrics transcription complete")
            self.status.set(f"Lyrics transcription complete. Detected language: {language}.")
        elif speaker_intervals_found:
            self.save_progress_text.set("Transcription complete")
            self.status.set(f"Transcription complete. Detected language: {language}.")
        else:
            self.save_progress_text.set("Transcription complete")
            self.status.set(
                "Transcription complete; no speakers were detected, so labels are unknown. "
                f"Detected language: {language}."
            )

    def _transcription_failed(self, error: str) -> None:
        self.transcribing = False
        self.record_button.configure(state="normal")
        self.open_file_button.configure(state="normal")
        self.transcribe_button.configure(state="normal")
        self.music_mode_button.configure(state="normal")
        self.save_progress_text.set("Transcription failed")
        self.status.set("Transcription failed.")
        messagebox.showerror("Transcription error", error, parent=self.root)

    def save_transcript(self) -> None:
        text = self.transcript.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("No transcript", "Transcribe a recording before saving the transcript.", parent=self.root)
            return

        destination = filedialog.asksaveasfilename(
            title="Save transcript",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
            initialfile="transcript.txt",
            parent=self.root,
        )
        if not destination:
            return

        destination_path = Path(destination)
        if not self._is_local_path(destination_path):
            messagebox.showerror(
                "Local destination required",
                "Choose a destination on a local drive. Network paths are blocked to keep transcript export offline.",
                parent=self.root,
            )
            return

        try:
            destination_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc), parent=self.root)
            return
        self.status.set(f"Transcript saved to {destination}")

    @staticmethod
    def _format_time(seconds: float) -> str:
        total_seconds = int(seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02}:{minutes:02}:{seconds:02}"
        return f"{minutes:02}:{seconds:02}"

    def _build_tts_panel(self, body: Any) -> None:
        tts_panel = tk.Frame(
            body, bg="#ffffff", padx=18, pady=14,
            highlightthickness=1, highlightbackground="#dce5df",
        )
        tts_panel.pack(side="bottom", fill="x", pady=(6, 0))
        self.tts_panel = tts_panel

        ttk.Label(tts_panel, text="VOICE OUTPUT", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 8)
        )

        voice_row = ttk.Frame(tts_panel)
        voice_row.pack(fill="x", pady=(0, 4))
        ttk.Label(voice_row, text="Voice").pack(side="left", padx=(0, 8))
        self.tts_voice_combo = ttk.Combobox(
            voice_row, textvariable=self.tts_voice, state="readonly"
        )
        self.tts_voice_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(voice_row, text="Refresh", command=self.refresh_tts_voices).pack(
            side="left", padx=(8, 0)
        )

        controls_row = ttk.Frame(tts_panel)
        controls_row.pack(fill="x", pady=(0, 4))
        ttk.Label(controls_row, text="Rate").pack(side="left")
        ttk.Scale(
            controls_row, from_=-10, to=10, orient="horizontal",
            variable=self.tts_rate, length=90,
        ).pack(side="left", padx=(4, 12))
        ttk.Label(controls_row, text="Volume").pack(side="left")
        ttk.Scale(
            controls_row, from_=0, to=100, orient="horizontal",
            variable=self.tts_volume, length=90,
        ).pack(side="left", padx=(4, 12))

        button_row = ttk.Frame(tts_panel)
        button_row.pack(fill="x")
        self.speak_button = ttk.Button(
            button_row, text="Speak Transcript", style="Accent.TButton",
            command=self.speak_transcript,
        )
        self.speak_button.pack(side="left")
        self.speak_selection_button = ttk.Button(
            button_row, text="Speak Selection", command=self.speak_selection
        )
        self.speak_selection_button.pack(side="left", padx=(8, 0))
        self.stop_speak_button = ttk.Button(
            button_row, text="Stop Speaking", command=self.stop_speaking, state="disabled"
        )
        self.stop_speak_button.pack(side="left", padx=(8, 0))
        self.save_speech_button = ttk.Button(
            button_row, text="Save Spoken Audio...", command=self.save_spoken_audio
        )
        self.save_speech_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            button_row, text="Highlight spoken text",
            variable=self.highlight_spoken_var,
        ).pack(side="right")

        self.tts_status = tk.StringVar(value="Choose a voice to enable speech output.")
        ttk.Label(tts_panel, textvariable=self.tts_status, style="TLabel").pack(
            fill="x", pady=(4, 0)
        )

    def refresh_tts_voices(self) -> None:
        try:
            voices = self.tts_engine.list_voices()
        except Exception as exc:
            messagebox.showerror("Voice error", str(exc), parent=self.root)
            return
        self.tts_voices = voices
        self.tts_voice_combo["values"] = voices
        if voices and not self.tts_voice.get():
            self.tts_voice.set(voices[0])
        if voices:
            self.tts_status.set(f"{len(voices)} voice(s) available for speech.")
        else:
            self.tts_status.set("No Windows voices were found.")

    def _strip_transcript_label(self, text: str) -> str:
        return re.sub(
            r"^\[[^\]]+\]\s*(?:Speaker \d+|Music / vocals|Speaker unknown):\s*",
            "",
            text,
        ).strip()

    def _clean_speech_text(self, text: str) -> str:
        cleaned_lines = []
        for line in text.splitlines():
            cleaned_lines.append(self._strip_transcript_label(line.strip()))
        return " ".join(part for part in cleaned_lines if part)

    def _collect_speak_chunks(self, selection_only: bool = False) -> list[dict[str, str]]:
        if selection_only:
            try:
                selected = self.tts_input.get("sel.first", "sel.last")
            except tk.TclError:
                selected = ""
            selected = selected.strip()
            if not selected:
                return []
            spoken = self._clean_speech_text(selected)
            if not spoken:
                return []
            return [{"spoken": spoken, "start": "sel.first", "end": "sel.last"}]

        content = self.tts_input.get("1.0", "end-1c")
        chunks: list[dict[str, str]] = []
        line_number = 1
        for raw_line in content.split("\n"):
            stripped = raw_line.strip()
            if stripped:
                spoken = self._clean_speech_text(stripped)
                if spoken:
                    chunks.append(
                        {"spoken": spoken, "start": f"{line_number}.0", "end": f"{line_number}.end"}
                    )
            line_number += 1
        return chunks

    def speak_transcript(self) -> None:
        if self.recording or self.transcribing or self.speaking:
            return
        chunks = self._collect_speak_chunks(False)
        if not chunks:
            messagebox.showwarning(
                "No transcript",
                "Transcribe or enter text before speaking.",
                parent=self.root,
            )
            return
        self._start_speaking(chunks)

    def speak_selection(self) -> None:
        if self.recording or self.transcribing or self.speaking:
            return
        chunks = self._collect_speak_chunks(True)
        if not chunks:
            messagebox.showwarning(
                "No selection",
                "Select transcript text before speaking.",
                parent=self.root,
            )
            return
        self._start_speaking(chunks)

    def _start_speaking(self, chunks: list[dict[str, str]]) -> None:
        self.speaking = True
        self.speak_stop_requested.clear()
        self._update_tts_controls(True)
        self.record_button.configure(state="disabled")
        self.open_file_button.configure(state="disabled")
        self.transcribe_button.configure(state="disabled")
        self.music_mode_button.configure(state="disabled")
        self.tts_status.set(f"Speaking {len(chunks)} chunk(s)...")
        self.speak_thread = threading.Thread(
            target=self._speak_chunks_worker, args=(chunks,), daemon=True
        )
        self.speak_thread.start()

    def _update_tts_controls(self, speaking: bool) -> None:
        if speaking:
            self.speak_button.configure(state="disabled")
            self.speak_selection_button.configure(state="disabled")
            self.save_speech_button.configure(state="disabled")
            self.stop_speak_button.configure(state="normal")
        else:
            self.speak_button.configure(state="normal")
            self.speak_selection_button.configure(state="normal")
            self.save_speech_button.configure(state="normal")
            self.stop_speak_button.configure(state="disabled")

    def stop_speaking(self) -> None:
        if not self.speaking:
            return
        self.speak_stop_requested.set()
        self.tts_engine.stop()
        try:
            sd.stop()
        except Exception:
            pass
        self.tts_status.set("Stopping speech...")

    def _speak_chunks_worker(self, chunks: list[dict[str, str]]) -> None:
        temp_files: list[str] = []
        failed: str | None = None
        cancelled = False
        try:
            for chunk in chunks:
                if self.speak_stop_requested.is_set():
                    cancelled = True
                    break
                if self.highlight_spoken_var.get():
                    self._schedule_ui(
                        self._highlight_speak_chunk, chunk["start"], chunk["end"]
                    )
                fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="ovs_tts_")
                os.close(fd)
                wav_path = str(wav_path)
                try:
                    Path(wav_path).unlink()
                except OSError:
                    pass
                temp_files.append(wav_path)
                try:
                    self.tts_engine.synthesize(
                        chunk["spoken"],
                        self.tts_voice.get(),
                        int(self.tts_rate.get()),
                        int(self.tts_volume.get()),
                        Path(wav_path),
                        self.speak_stop_requested,
                    )
                except Exception as exc:
                    if self.speak_stop_requested.is_set():
                        cancelled = True
                    else:
                        failed = str(exc)
                    break
                if self.speak_stop_requested.is_set():
                    cancelled = True
                    break
                try:
                    self.tts_engine.play_wav(Path(wav_path), self.speak_stop_requested)
                except Exception as exc:
                    if self.speak_stop_requested.is_set():
                        cancelled = True
                    else:
                        failed = str(exc)
                    break
                if self.speak_stop_requested.is_set():
                    cancelled = True
                    break
        finally:
            for path in temp_files:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        if failed:
            self._schedule_ui(self._speak_failed, failed)
        elif cancelled:
            self._schedule_ui(self._speak_cancelled)
        else:
            self._schedule_ui(self._speak_finished)

    def _highlight_speak_chunk(self, start: str, end: str) -> None:
        self.tts_input.tag_remove("speaking", "1.0", tk.END)
        if not self.highlight_spoken_var.get():
            return
        try:
            self.tts_input.tag_add("speaking", start, end)
            self.tts_input.see(start)
        except tk.TclError:
            pass

    def _speak_finished(self) -> None:
        self.speaking = False
        self.tts_input.tag_remove("speaking", "1.0", tk.END)
        self._update_tts_controls(False)
        self.record_button.configure(state="normal")
        self.open_file_button.configure(state="normal")
        self.transcribe_button.configure(state="normal")
        self.music_mode_button.configure(state="normal")
        self.tts_status.set("Speech finished.")

    def _speak_cancelled(self) -> None:
        self.speaking = False
        self.tts_input.tag_remove("speaking", "1.0", tk.END)
        self._update_tts_controls(False)
        self.record_button.configure(state="normal")
        self.open_file_button.configure(state="normal")
        self.transcribe_button.configure(state="normal")
        self.music_mode_button.configure(state="normal")
        self.tts_status.set("Speech stopped.")

    def _speak_failed(self, error: str) -> None:
        self.speaking = False
        self.tts_input.tag_remove("speaking", "1.0", tk.END)
        self._update_tts_controls(False)
        self.record_button.configure(state="normal")
        self.open_file_button.configure(state="normal")
        self.transcribe_button.configure(state="normal")
        self.music_mode_button.configure(state="normal")
        self.tts_status.set("Speech failed.")
        messagebox.showerror("Speech error", error, parent=self.root)

    def save_spoken_audio(self) -> None:
        if self.recording or self.transcribing or self.speaking:
            return
        chunks = self._collect_speak_chunks(False)
        if not chunks:
            messagebox.showwarning(
                "No transcript", "Transcribe or enter text before saving speech.",
                parent=self.root,
            )
            return
        destination = filedialog.asksaveasfilename(
            title="Save spoken audio",
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")],
            initialfile="speech.wav",
            parent=self.root,
        )
        if not destination:
            return
        destination_path = Path(destination)
        if not self._is_local_path(destination_path):
            messagebox.showerror(
                "Local destination required",
                "Choose a destination on a local drive.",
                parent=self.root,
            )
            return
        text = " ".join(chunk["spoken"] for chunk in chunks)
        if not text.strip():
            messagebox.showwarning("No speech text", "There is no speakable text.", parent=self.root)
            return

        self.speaking = True
        self.speak_stop_requested.clear()
        self._update_tts_controls(True)
        self.record_button.configure(state="disabled")
        self.open_file_button.configure(state="disabled")
        self.transcribe_button.configure(state="disabled")
        self.music_mode_button.configure(state="disabled")
        self.tts_status.set("Saving spoken audio...")
        threading.Thread(
            target=self._save_spoken_audio_worker,
            args=(destination_path, text),
            daemon=True,
        ).start()

    def _save_spoken_audio_worker(self, destination_path: Path, text: str) -> None:
        try:
            self.tts_engine.synthesize(
                text,
                self.tts_voice.get(),
                int(self.tts_rate.get()),
                int(self.tts_volume.get()),
                destination_path,
                None,
            )
            self._schedule_ui(self._save_spoken_audio_finished, str(destination_path))
        except Exception as exc:
            self._schedule_ui(self._speak_failed, str(exc))

    def _save_spoken_audio_finished(self, destination: str) -> None:
        self._speak_finished()
        self.tts_status.set(f"Spoken audio saved to {destination}")
        self.status.set(f"Spoken audio saved to {destination}")


    def append_transcript_into_tts(self) -> None:
        transcript_text = self.transcript.get("1.0", "end-1c").strip()
        if not transcript_text:
            messagebox.showinfo(
                "No transcript", "Transcribe audio before appending it.", parent=self.root
            )
            return
        existing = self.tts_input.get("1.0", "end-1c").strip()
        if existing:
            self.tts_input.insert(tk.END, "\n\n" + transcript_text)
        else:
            self.tts_input.insert("1.0", transcript_text)
        self.tts_input.see(tk.END)

    def open_text_file_for_tts(self) -> None:
        selected = filedialog.askopenfilename(
            title="Open text for speech",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            parent=self.root,
        )
        if not selected:
            return
        path = Path(selected)
        if not self._is_local_path(path):
            messagebox.showerror(
                "Local file required",
                "Choose a text file stored on a local drive.",
                parent=self.root,
            )
            return
        if not path.is_file():
            messagebox.showerror("File not found", "The selected file is no longer available.", parent=self.root)
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Open failed", str(exc), parent=self.root)
            return
        existing = self.tts_input.get("1.0", "end-1c").strip()
        if existing:
            replace = messagebox.askyesno(
                "Replace text?",
                "Replace the current TTS text with this file?\nChoose No to append instead.",
                parent=self.root,
            )
            if replace:
                self.tts_input.delete("1.0", tk.END)
                self.tts_input.insert("1.0", text)
            else:
                self.tts_input.insert(tk.END, "\n\n" + text)
        else:
            self.tts_input.insert("1.0", text)
        self.tts_input.see("1.0")

    def get_windows_theme(self) -> str:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "Light" if value == 1 else "Dark"
        except OSError:
            return "Light"

    def apply_theme(self) -> None:
        selected = self.theme.get()
        if selected == "System":
            theme = self.get_windows_theme()
        else:
            theme = selected
        colors = self._theme_colors(theme)
        self.colors = colors

        self.root.configure(bg=colors["bg"])
        self.header.configure(bg=colors["header_bg"])
        def _theme_header(widget):
            if isinstance(widget, tk.Frame):
                widget.configure(bg=colors["header_bg"])
            elif isinstance(widget, tk.Label):
                widget.configure(bg=colors["header_bg"], fg=colors["header_fg"])
            for child in widget.winfo_children():
                _theme_header(child)
        _theme_header(self.header)

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["text"], font=("Segoe UI", 10))
        style.configure("Heading.TLabel", background=colors["bg"], foreground=colors["accent"], font=("Segoe UI", 12, "bold"))
        style.configure("TButton", background=colors["panel"], foreground=colors["text"], bordercolor=colors["border"], font=("Segoe UI", 10), padding=(12, 8))
        style.map("TButton", background=[("active", colors["accent_light"])] )
        style.configure("Accent.TButton", background=colors["accent"], foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", colors["accent_hover"]), ("disabled", colors["muted"])] )
        style.configure("Record.TButton", background=colors["danger"], foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        style.configure("TCombobox", fieldbackground=colors["panel"], background=colors["panel"], foreground=colors["text"], bordercolor=colors["border"])
        style.configure("TScale", background=colors["bg"], troughcolor=colors["border"])
        style.configure("TScrollbar", background=colors["panel"], troughcolor=colors["bg"], bordercolor=colors["border"])
        style.configure("TNotebook", background=colors["bg"], bordercolor=colors["border"])
        style.configure("TNotebook.Tab", background=colors["panel"], foreground=colors["text"], padding=(12, 8))
        style.map("TNotebook.Tab", background=[("selected", colors["accent"])], foreground=[("selected", "#ffffff")])

        self.body_canvas.configure(bg=colors["bg"])
        self.waveform_canvas.configure(bg=colors["header_bg"])

        for panel in (getattr(self, "record_panel", None), getattr(self, "tts_panel", None)):
            if panel is not None:
                panel.configure(bg=colors["panel"], highlightbackground=colors["border"])

        for widget in (self.transcript, self.tts_input):
            widget.configure(bg=colors["panel"], fg=colors["text"], insertbackground=colors["accent"])

        self.status_bar.configure(bg=colors["status_bg"])
        for child in self.status_bar.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(bg=colors["status_bg"], fg=colors["muted"])

        # refresh waveform canvas colors
        self._draw_waveform()


    def open_voice_settings(self) -> None:
        try:
            os.startfile("ms-settings:speech")
        except OSError:
            try:
                subprocess.Popen(["control", "/name", "Microsoft.SpeechRecognition"])
            except OSError as exc:
                messagebox.showerror("Voice settings", str(exc), parent=self.root)

    def _poll_system_theme(self) -> None:
        if self.theme.get() == "System":
            self.apply_theme()
        self.root.after(5000, self._poll_system_theme)

    def _theme_colors(self, theme: str) -> dict[str, str]:
        if theme == "Dark":
            return {
                "bg": "#151a19", "panel": "#232a28", "text": "#e8f0ec",
                "muted": "#9fb0a9", "accent": "#2aa88f", "accent_hover": "#27887a",
                "accent_light": "#2f3a38", "danger": "#e0605a", "header_bg": "#2aa88f",
                "header_fg": "#ffffff", "status_bg": "#1b2220", "border": "#33403c",
            }
        if theme == "Solarized":
            return {
                "bg": "#fdf6e3", "panel": "#eee8d5", "text": "#586e75",
                "muted": "#93a1a1", "accent": "#268bd2", "accent_hover": "#2176b8",
                "accent_light": "#e0f0fa", "danger": "#dc322f", "header_bg": "#268bd2",
                "header_fg": "#ffffff", "status_bg": "#e8e0cc", "border": "#d3cab3",
            }
        if theme == "Nord":
            return {
                "bg": "#2e3440", "panel": "#3b4252", "text": "#eceff4",
                "muted": "#d8dee9", "accent": "#88c0d0", "accent_hover": "#81a1c1",
                "accent_light": "#434c5e", "danger": "#bf616a", "header_bg": "#88c0d0",
                "header_fg": "#2e3440", "status_bg": "#3b4252", "border": "#4c566a",
            }
        if theme == "High Contrast":
            return {
                "bg": "#000000", "panel": "#000000", "text": "#ffffff",
                "muted": "#ffffff", "accent": "#ffff00", "accent_hover": "#ffff00",
                "accent_light": "#333333", "danger": "#ff0000", "header_bg": "#ffff00",
                "header_fg": "#000000", "status_bg": "#000000", "border": "#ffffff",
            }
        return {
            "bg": "#f3f6f4", "panel": "#ffffff", "text": "#1f2d2a",
            "muted": "#5f6f6a", "accent": "#167c68", "accent_hover": "#116552",
            "accent_light": "#e7f2ee", "danger": "#d94841", "header_bg": "#167c68",
            "header_fg": "#ffffff", "status_bg": "#e6ece8", "border": "#d0d9d4",
        }

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        if self.recording:
            self.recording = False
            self.stream_stop_error = self._stop_input_stream()
            self.stop_requested.set()
        self._finish_close()

    def _finish_close(self) -> None:
        capture_running = self.capture_thread is not None and self.capture_thread.is_alive()
        writer_running = self.writer_thread is not None and self.writer_thread.is_alive()
        if capture_running or writer_running:
            self.root.after(100, self._finish_close)
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    OfflineVoiceStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()

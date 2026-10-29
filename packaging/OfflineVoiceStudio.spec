# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


PROJECT_DIR = Path(SPECPATH).resolve().parent
MEETING_TRANSCRIBER_DIR = PROJECT_DIR.parent / "MeetingTranscriber"
MODEL_DIR = MEETING_TRANSCRIBER_DIR / "models" / "faster-whisper-small"
SPEAKER_MODELS_DIR = MEETING_TRANSCRIBER_DIR / "speaker_models"
SPEAKER_RUNTIME_DIR = (
    MEETING_TRANSCRIBER_DIR
    / "packaging"
    / "app_dist"
    / "MeetingTranscriber"
    / "speaker_diarization"
)
FFMPEG_DIR = (
    MEETING_TRANSCRIBER_DIR
    / "packaging"
    / "app_dist"
    / "MeetingTranscriber"
    / "ffmpeg"
)

required_files = (
    MODEL_DIR / "model.bin",
    MODEL_DIR / "config.json",
    SPEAKER_MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx",
    SPEAKER_MODELS_DIR / "speaker_embedding.onnx",
    SPEAKER_RUNTIME_DIR / "speaker_diarization.exe",
    FFMPEG_DIR / "ffmpeg.exe",
)
missing_files = [str(path) for path in required_files if not path.is_file()]
if missing_files:
    raise FileNotFoundError("Installer assets are missing: " + ", ".join(missing_files))


def collect_tree(source_directory, destination_directory):
    source_directory = Path(source_directory)
    destination_directory = Path(destination_directory)
    return [
        (
            str(path),
            (destination_directory / path.relative_to(source_directory).parent).as_posix(),
        )
        for path in source_directory.rglob("*")
        if path.is_file()
    ]


datas = []
binaries = []
hiddenimports = []
for package in (
    "faster_whisper",
    "ctranslate2",
    "tokenizers",
    "onnxruntime",
    "av",
    "sounddevice",
    "soundfile",
    "soundcard",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += collect_tree(MODEL_DIR, "models/faster-whisper-small")
datas += collect_tree(
    SPEAKER_MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0",
    "speaker_models/sherpa-onnx-pyannote-segmentation-3-0",
)
datas += [(str(SPEAKER_MODELS_DIR / "speaker_embedding.onnx"), "speaker_models")]
datas += collect_tree(SPEAKER_RUNTIME_DIR, "speaker_diarization")
datas += collect_tree(FFMPEG_DIR, "ffmpeg")

analysis = Analysis(
    [str(PROJECT_DIR / "OfflineVoiceStudio.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

app = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="OfflineVoiceStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

COLLECT(
    app,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OfflineVoiceStudio",
)

# Offline Voice Studio

A Windows desktop app for recording microphone or system playback audio and transcribing local audio or video files. For video, it transcribes the audio track only; it does not perform OCR on on-screen text. Created by Jerome Ventigan. The app does not download models or send audio to a service.

## Requirements

- Windows 10 or 11
- Python 3.10 or later
- A microphone for voice input, or a Windows playback device for system audio capture
- A local Faster-Whisper model folder, such as `faster-whisper-small`

## Setup

Open PowerShell in this project folder and create an isolated environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks environment activation, run the environment's interpreter directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Model setup

The app uses a local Faster-Whisper model and does not fetch models. The model location is managed internally and is not part of the normal user workflow. A source run uses `models/faster-whisper-small` or the adjacent MeetingTranscriber model folder. The Windows installer bundles the model with the app.

## Speaker labels

Normal speech transcription uses local speaker diarization and adds labels such as `Speaker 1`. The app looks for the diarization executable, FFmpeg, and ONNX speaker models in this project or in the adjacent MeetingTranscriber project. These assets must already be present; the app does not download them. Music / lyrics mode skips speaker detection and labels segments as `Music / vocals`.

## Run

```powershell
.\.venv\Scripts\python.exe .\OfflineVoiceStudio.py
```

Choose **System audio: [speaker]** to capture sound playing through the selected Windows output, or choose a microphone input for live voice recording. Select **Start Recording**, then **Stop Recording**. WAV files are saved under `Documents\Offline Voice Studio\Recordings`. Use **Open Audio/Video** to select an existing audio file or a video with an audio track. Select **Transcribe Recording** to load the selected local model and transcribe the selected file. Use **Save Transcript** to export the text.

For ordinary speech, leave **Music / lyrics** unchecked. Enable it for songs to disable voice-activity filtering, which may otherwise discard sung vocals. This mode transcribes lyrics only; it does not identify instruments or music.

## Notes

- System audio uses Windows WASAPI loopback; it captures the selected speaker output, including other sounds played through that output.
- The microphone recorder uses the selected input's default sample rate. Both capture modes write mono PCM WAV audio.
- Transcription, audio conversion, and speaker detection use local model and executable files only; no audio is uploaded and no runtime downloads are performed.
- Recording uses a bounded audio buffer; if storage falls behind, the app warns that some audio may be missing.
- A progress indicator tracks frames written versus captured during recording, then shows finalization and remains at 100% after the WAV is closed.
- Transcription progress is indeterminate during speaker detection/model loading, then estimated from processed audio timestamps and reaches 100% when transcription completes.
- Transcription runs on the CPU using int8 inference.
- Windows microphone privacy settings must allow desktop apps to access the microphone.
- The transcription model is not included in this source project; provide a compatible local model folder before use.

## Text to voice

After transcribing, use the **Voice Output** panel to read the transcript aloud with an installed Windows voice. You can choose the voice, rate, and volume, speak the full transcript or only the selected text, stop speech at any time, and save the spoken audio as a WAV file. Speech output stays local and uses the Windows built-in speech voices; no model download or network access is required.

## Build the Windows installer

The installer bundles the local Faster-Whisper model, speaker-detection runtime, ONNX speaker models, FFmpeg, and this user guide. The source files are taken from the adjacent `MeetingTranscriber` project when building.

Install the build dependency in the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-build.txt
```

From the project root, build the one-folder application and compile the installer:

```powershell
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --distpath .\packaging\app_dist --workpath .\packaging\app_build .\packaging\OfflineVoiceStudio.spec
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" .\packaging\OfflineVoiceStudio.iss
```

The setup file is written to `packaging\installer_output\OfflineVoiceStudio-Setup.exe`. The bundled runtime assets total about 1.5 GB before installer compression. The installer is per-user, requires no administrator rights, and does not download anything at install or runtime.

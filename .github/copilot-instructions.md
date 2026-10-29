# Offline Voice Studio Instructions

- Keep recording and transcription fully local. Do not add cloud services, network uploads, or automatic model downloads.
- Keep Tkinter widget updates on the main thread. Audio capture callbacks should enqueue blocks; file writing and model inference belong on worker threads.
- Add Python dependencies to `requirements.txt` and keep setup instructions in `README.md` current.
- Save recordings under the user's Documents folder, not inside the installed application directory.
- Use the project's `.venv` for development and validation.

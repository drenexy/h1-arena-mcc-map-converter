# Build H1ArenaMapConverter.exe (one file, no console window)
# Requires: pip install pyinstaller
$ErrorActionPreference = "Stop"
pyinstaller --onefile --windowed --name H1ArenaMapConverter `
    --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.filedialog `
    companion.py
Write-Host "done -> dist\H1ArenaMapConverter.exe"

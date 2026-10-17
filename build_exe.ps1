# Build H1MapCompanion.exe (one file, no console window)
# Requires: pip install pyinstaller
$ErrorActionPreference = "Stop"
pyinstaller --onefile --windowed --name H1MapCompanion `
    --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.filedialog `
    companion.py
Write-Host "done -> dist\H1MapCompanion.exe"

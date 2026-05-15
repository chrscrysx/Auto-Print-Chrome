# Chromium Auto-Printer

A lightweight, background system tray application built in Python that automatically detects Chromium-based print dialogs (Google Chrome, Microsoft Edge, Brave, etc.) and instantly executes the print job. 

Designed for high-efficiency workflows, it operates without hijacking the physical mouse cursor and features targeted window scanning to prevent accidental prints on unrelated web pages.

## Features
* **Universal Chromium Support:** Works across Chrome, Edge, and other Chromium browsers by hooking into the native Windows UIAutomation accessibility tree.
* **Targeted Execution:** Configure the app to only trigger on specific window titles (e.g., "SIMC") or use the `*` wildcard to trigger on all browser windows.
* **Persistent Configuration:** Automatically generates and reads a `settings.txt` file so preferences survive computer reboots.
* **Zero Mouse Interference:** Uses direct API keystrokes (`Enter`) to trigger the print button, keeping the user's physical mouse cursor completely free.
* **Native OS Integration:** Resides cleanly in the Windows System Tray with native Windows dialog prompts for configuration.

## Prerequisites
* Python 3.x installed (Make sure Python is added to your Windows PATH).
* A Windows Operating System.

## Installation for Development
1. Clone or download this repository.
2. Open a terminal in the project directory.
3. Install the required Python dependencies:
   ```bash
   pip install -r requirements.txt

## compiler
python -m PyInstaller --onefile --noconsole tray_auto_printer.py
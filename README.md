# Auto-Printer for Chromium Browsers

A background Windows system-tray application that automatically detects print dialogs in Chromium-based browsers (Google Chrome, Microsoft Edge) and triggers print jobs with pre-configured paper sizes and print settings.

## Features

- **Auto-Print Detection**: Hooks into the Windows UIAutomation accessibility tree to detect print dialogs without interfering with the mouse.
- **Targeted Window Matching**: Trigger only on specific window titles (e.g., `SIMC`) or use `*` to target all Chromium windows.
- **Per-Printer Paper Size Mapping**: Map any installed printer to a specific paper size via a Windows Forms dialog. The app stores exact dimensions (width/height in microns) and vendor IDs.
- **Browser Preferences Injection & Relaunch**: Writes directly to Chrome/Edge `Preferences` files (`print_preview_sticky_settings.appState`) to enforce paper size, layout, color, scaling, margins, headers/footers, and background graphics. Restores running browser sessions automatically after updating.
- **Registry Policy Enforcement**: Uses the standard `PrintingPaperSizeDefault` policy in `Software\Policies\Google\Chrome` and `Software\Policies\Microsoft\Edge` to enforce paper size defaults dynamically using standard IPP/PWG names (e.g. `iso_a4_210x297mm`) or custom dimensions.
- **Opt-in File Logging**: Logging to `error_log.log` is disabled by default. Toggle it from the system tray menu or set `"logging_enabled": true` in `printer_config.json`.
- **Printer Capability Scanning**: Scans all installed printers and their supported paper sizes into the config file for accurate mapping.
- **Single Instance**: Prevents multiple copies from running simultaneously.
- **Legacy Migration**: Automatically migrates old `settings.txt` files to the new JSON format.

## Prerequisites

- Windows 10/11
- Python 3.x (if running from source)
- Google Chrome and/or Microsoft Edge

## Installation

### From Source

```bash
pip install -r requirements.txt
python tray_auto_printer.py
```

## Compiler

```bash
pip install pyinstaller
python -m PyInstaller --onefile --noconsole tray_auto_printer.py
```

The compiled executable will be located in the `dist/` folder.

## Configuration

All settings are stored in `printer_config.json` in the application directory.

### Example `printer_config.json`

```json
{
  "target_title": "SIMC",
  "browser_defaults": {
    "layout": "portrait",
    "color": "color",
    "scale": "fit to printable area",
    "margin": "default",
    "headers": "false",
    "backgrounds": "false"
  },
  "mappings": {
    "Microsoft Print to PDF": {
      "paper_name": "Legal",
      "vendor_id": "5",
      "width_microns": 215900,
      "height_microns": 355600
    }
  },
  "printer_capabilities": {
    "Microsoft Print to PDF": [
      {
        "paper_name": "Letter",
        "vendor_id": "1",
        "width_microns": 215900,
        "height_microns": 279400
      }
    ]
  },
  "logging_enabled": false
}
```

### Fields

| Field | Description |
|-------|-------------|
| `target_title` | Window title substring to match. Use `"*"` for all Chromium windows. |
| `browser_defaults` | Default print settings applied to every print job. |
| `mappings` | Per-printer paper size mapping. Each entry stores `paper_name`, `vendor_id`, and exact dimensions. |
| `printer_capabilities` | Auto-generated list of every printer's supported paper sizes. |
| `logging_enabled` | If `true`, writes logs to `error_log.log` with rotation (1 MB max). |

### Browser Defaults

| Setting | Accepted Values |
|---------|-----------------|
| `layout` | `portrait`, `landscape` |
| `color` | `color`, `black and white` |
| `scale` | `fit to printable area`, `fit to page`, `actual size`, `default`, or a custom percentage like `150%` |
| `margin` | `default`, `none`, `minimum`, `custom` |
| `headers` | `true`, `false` |
| `backgrounds` | `true`, `false` |

## System Tray Menu

Right-click the tray icon to access:

- **Status**: Shows the current target window title.
- **Change Target Title...**: Opens a dialog to change the target window title.
- **Map Printer Paper Size...**: Opens a dialog to map a printer to a paper size. Requires closing Chrome/Edge to unlock preference files.
- **Refresh Printer List**: Re-scans installed printers and their paper sizes.
- **Logging to file: On/Off**: Toggles file logging.
- **Open Settings File**: Opens `printer_config.json` in the default editor.
- **Quit Auto-Printer**: Shuts down the application.

## How It Works

1. **Monitoring Loop**: The app polls the active foreground window every 350 ms when a Chromium window matches target title criteria.
2. **Print Dialog Detection**: Uses `uiautomation` to locate print buttons and destination dropdowns in the window accessibility tree.
3. **Paper Size Matching**:
   - Reads the selected destination printer.
   - If there is a mapping configured in `printer_config.json`, it converts the target paper size into its standardized IPP/PWG name format.
   - It checks the registry to see if `PrintingPaperSizeDefault` matches the target size. If there is a mismatch, it updates the registry value in the background for subsequent printing.
   - If there is no custom mapping for the printer, it clears the registry policy dynamically to restore default browser options.
4. **Trigger Print**: Clicks "Print" (or sends `{Space}`) directly.
5. **Cleanup**: The registry policy is registered to clear via `atexit` when the application quits.

## Important Notes

- **Registry Policy**: The app temporarily writes to `HKEY_CURRENT_USER\Software\Policies\Google\Chrome` and `...\Microsoft\Edge`. These are cleaned up on exit, but if the app crashes, a stale policy may remain. Run the app again or manually delete `PrintingPaperSizeDefault` from those keys.
- **Browser Restart**: When mapping a new printer/paper size via the UI dialog, the app warns you and force-closes all Chrome/Edge windows to unlock the `Preferences` files. Unsaved work will be lost.
- **Scaling Enum**: Chromium uses `scalingType` (HTML) and `scalingTypePdf` (PDF). The enum values are `0=DEFAULT`, `1=FIT_TO_PAGE`, `2=FIT_TO_PAPER`, `3=CUSTOM`.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| App not detecting print dialog | Ensure the target window title matches. Try `"*"` to target all windows. |
| Paper size not applying | Check that the printer name in `mappings` exactly matches the name shown in the print dialog. Use **Refresh Printer List** to update capabilities. |
| Fit to printable area not sticking | Verify `browser_defaults.scale` is set to `"fit to printable area"`. The app maps this to `scalingType: 1` and `scalingTypePdf: 1`. |
| Registry policy left behind | Restart the app; it cleans stale policies on startup. Or manually delete `PrintingPaperSizeDefault` from the Chrome/Edge policy registry keys. |
| Log file not appearing | Set `"logging_enabled": true` in `printer_config.json` or toggle it from the tray menu. |

## Dependencies

- `uiautomation`
- `pystray`
- `Pillow`

See `requirements.txt` for versions.

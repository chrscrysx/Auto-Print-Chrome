import time
import threading
import subprocess
import os
import sys
import socket
import traceback
import logging
import json
import winreg
import atexit
from logging.handlers import RotatingFileHandler
import uiautomation as auto
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

# --- PATH & LOGGING CONFIGURATION ---
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

OLD_CONFIG_FILE = os.path.join(APP_DIR, "settings.txt")
CONFIG_FILE = os.path.join(APP_DIR, "printer_config.json")
ERROR_LOG = os.path.join(APP_DIR, "error_log.log")

# Setup logger — file handler is added later based on config
logger = logging.getLogger("AutoPrinter")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', '%Y-%m-%d %H:%M:%S')

# Always add console output if available
try:
    if sys.stdout is not None:
        _console_handler = logging.StreamHandler(sys.stdout)
        _console_handler.setFormatter(_formatter)
        logger.addHandler(_console_handler)
except Exception:
    pass

def configure_logger(enabled):
    """Add or remove the rotating file handler based on the config setting."""
    # Remove any existing file handlers
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler):
            logger.removeHandler(h)
            h.close()
    if enabled:
        try:
            fh = RotatingFileHandler(ERROR_LOG, maxBytes=1024 * 1024, backupCount=1, encoding='utf-8')
            fh.setFormatter(_formatter)
            logger.addHandler(fh)
        except Exception:
            pass

# --- CONFIGURATION FILE HANDLING (TARGET TITLE & PRINTER MAPPINGS) ---
def scan_printer_capabilities():
    """
    Runs a fast PowerShell script to list all installed printers and their supported paper sizes,
    updating the 'printer_capabilities' dictionary in the JSON config file.
    """
    logger.info("Scanning printer capabilities...")
    ps_cmd = (
        "Add-Type -AssemblyName System.Drawing; "
        "[System.Drawing.Printing.PrinterSettings]::InstalledPrinters | ForEach-Object { "
        "  $p = $_; "
        "  $ps = New-Object System.Drawing.Printing.PrinterSettings; "
        "  $ps.PrinterName = $p; "
        "  $sizes = @(); "
        "  try { "
        "    foreach ($size in $ps.PaperSizes) { "
        "      $sizes += @{ "
        "        paper_name = $size.PaperName; "
        "        vendor_id = $size.RawKind; "
        "        width_microns = [int]($size.Width * 254); "
        "        height_microns = [int]($size.Height * 254) "
        "      } "
        "    } "
        "  } catch {}; "
        "  [PSCustomObject]@{ printer_name = $p; paper_sizes = $sizes } "
        "} | ConvertTo-Json -Depth 5"
    )
    
    try:
        CREATE_NO_WINDOW = 0x08000000
        process = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )
        
        if process.returncode == 0 and process.stdout.strip():
            try:
                data = json.loads(process.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                
                capabilities = {}
                for entry in data:
                    printer_name = entry.get("printer_name")
                    paper_sizes = entry.get("paper_sizes", [])
                    if printer_name:
                        sizes_list = []
                        if isinstance(paper_sizes, dict):
                            paper_sizes = [paper_sizes]
                        for size in paper_sizes:
                            if isinstance(size, dict):
                                sizes_list.append({
                                    "paper_name": size.get("paper_name", ""),
                                    "vendor_id": str(size.get("vendor_id", "")),
                                    "width_microns": size.get("width_microns", 0),
                                    "height_microns": size.get("height_microns", 0)
                                })
                        capabilities[printer_name] = sizes_list
                return capabilities
            except Exception as e:
                logger.error(f"Failed to parse scanned capabilities output: {e}")
        else:
            logger.error(f"PowerShell scan exited with error: {process.stderr}")
    except Exception as e:
        logger.error(f"Exception scanning capabilities: {e}")
    return {}

def migrate_old_settings():
    """
    Migrates configuration from old settings.txt to printer_config.json if it exists.
    """
    target_title = "SIMC"
    mappings = {
        "Microsoft Print to PDF": "A4",
        "Adobe PDF": "8.5 x 13"
    }
    prefs = {
        "layout": "portrait",
        "color": "color",
        "scale": "fit to printable area",
        "margin": "default",
        "headers": "false",
        "backgrounds": "false"
    }
    
    KNOWN_SETTINGS = {"target title", "layout", "color", "scale", "margin", "headers", "backgrounds"}
    
    if os.path.exists(OLD_CONFIG_FILE):
        logger.info("Migrating legacy settings.txt configuration...")
        try:
            current_mappings = {}
            with open(OLD_CONFIG_FILE, "r", encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        k_lower = k.lower()
                        if k_lower in KNOWN_SETTINGS:
                            if k_lower == "target title":
                                target_title = v
                            else:
                                prefs[k_lower] = v
                        else:
                            current_mappings[k] = v
            if current_mappings:
                mappings = current_mappings
            
            # Back up settings.txt
            bak_file = OLD_CONFIG_FILE + ".bak"
            if os.path.exists(bak_file):
                os.remove(bak_file)
            os.rename(OLD_CONFIG_FILE, bak_file)
            logger.info(f"Backed up settings.txt to {bak_file}")
        except Exception as e:
            logger.error(f"Failed to migrate old settings: {e}")
            
    return target_title, mappings, prefs

def save_settings(target_title, mappings, prefs=None, capabilities=None, logging_enabled=None):
    if prefs is None:
        prefs = {
            "layout": "portrait",
            "color": "color",
            "scale": "fit to printable area",
            "margin": "default",
            "headers": "false",
            "backgrounds": "false"
        }

    if capabilities is None:
        capabilities = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding='utf-8') as f:
                    old_data = json.load(f)
                    capabilities = old_data.get("printer_capabilities", {})
            except Exception:
                pass

    config_data = {
        "target_title": target_title,
        "browser_defaults": prefs,
        "mappings": mappings,
        "printer_capabilities": capabilities
    }

    if logging_enabled is not None:
        config_data["logging_enabled"] = logging_enabled
    elif os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding='utf-8') as f:
                old_data = json.load(f)
                if "logging_enabled" in old_data:
                    config_data["logging_enabled"] = old_data["logging_enabled"]
        except Exception:
            pass

    try:
        with open(CONFIG_FILE, "w", encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save settings to JSON: {e}")

def load_settings():
    if os.path.exists(OLD_CONFIG_FILE):
        target_title, mappings, prefs = migrate_old_settings()
        capabilities = scan_printer_capabilities()
        converted_mappings = {}
        for prn, paper_val in mappings.items():
            paper_name = paper_val
            vendor_id = ""
            if "," in paper_val:
                parts = paper_val.split(",", 1)
                paper_name = parts[0].strip()
                vendor_id = parts[1].strip()

            matched = False
            for cap_prn, cap_sizes in capabilities.items():
                if cap_prn == prn:
                    for size in cap_sizes:
                        if size["paper_name"] == paper_name:
                            converted_mappings[prn] = {
                                "paper_name": paper_name,
                                "vendor_id": size["vendor_id"],
                                "width_microns": size["width_microns"],
                                "height_microns": size["height_microns"]
                            }
                            matched = True
                            break
            if not matched:
                converted_mappings[prn] = {
                    "paper_name": paper_name,
                    "vendor_id": vendor_id,
                    "width_microns": 0,
                    "height_microns": 0
                }
        save_settings(target_title, converted_mappings, prefs, capabilities, False)
        return target_title, converted_mappings, prefs, False

    target_title = "SIMC"
    mappings = {}
    prefs = {
        "layout": "portrait",
        "color": "color",
        "scale": "fit to printable area",
        "margin": "default",
        "headers": "false",
        "backgrounds": "false"
    }
    logging_enabled = False

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
            target_title = data.get("target_title", target_title)
            prefs = data.get("browser_defaults", prefs)
            mappings = data.get("mappings", mappings)
            capabilities = data.get("printer_capabilities", {})
            logging_enabled = data.get("logging_enabled", False)

            if not capabilities:
                capabilities = scan_printer_capabilities()
                save_settings(target_title, mappings, prefs, capabilities, logging_enabled)
        except Exception as e:
            logger.error(f"Failed to read settings from JSON: {e}")
    else:
        capabilities = scan_printer_capabilities()
        save_settings(target_title, mappings, prefs, capabilities, logging_enabled)

    return target_title, mappings, prefs, logging_enabled

def get_browser_prefs_from_config(prefs):
    app_state_changes = {}
    
    # 1. Layout
    layout = prefs.get("layout", "portrait").lower()
    app_state_changes["isLandscapeEnabled"] = (layout == "landscape")
    
    # 2. Color
    color = prefs.get("color", "color").lower()
    app_state_changes["isColorEnabled"] = (color in ["color", "true", "enabled"])
    
    # 3. Scale
    # Chromium ScalingType enum: 0=DEFAULT, 1=FIT_TO_PAGE, 2=FIT_TO_PAPER, 3=CUSTOM
    # Keep scaling="100" for non-custom modes so the control stays enabled.
    scale = prefs.get("scale", "fit to printable area").lower()
    if scale in ["fit to printable area", "fit to page"]:
        app_state_changes["scalingType"] = 1
        app_state_changes["scalingTypePdf"] = 1
        app_state_changes["scaling"] = "100"
    elif scale in ["actual size", "actual"]:
        app_state_changes["scalingType"] = 3
        app_state_changes["scalingTypePdf"] = 3
        app_state_changes["scaling"] = "100"
    elif scale in ["default"]:
        app_state_changes["scalingType"] = 0
        app_state_changes["scalingTypePdf"] = 0
        app_state_changes["scaling"] = "100"
    else:
        try:
            val = scale.replace("%", "").strip()
            int(val)
            app_state_changes["scalingType"] = 3
            app_state_changes["scalingTypePdf"] = 3
            app_state_changes["scaling"] = val
        except ValueError:
            app_state_changes["scalingType"] = 1
            app_state_changes["scalingTypePdf"] = 1
            app_state_changes["scaling"] = "100"
            
    # 4. Margin
    margin = prefs.get("margin", "default").lower()
    if margin == "none":
        app_state_changes["marginsType"] = 1
    elif margin == "minimum":
        app_state_changes["marginsType"] = 2
    elif margin == "custom":
        app_state_changes["marginsType"] = 3
    else:
        app_state_changes["marginsType"] = 0
        
    # 5. Headers & Footers
    headers = prefs.get("headers", "false").lower()
    app_state_changes["isHeaderFooterEnabled"] = (headers in ["true", "checked", "enabled"])
    
    # 6. Background Graphics
    backgrounds = prefs.get("backgrounds", "false").lower()
    app_state_changes["isCssBackgroundEnabled"] = (backgrounds in ["true", "checked", "enabled"])
    
    return app_state_changes

STANDARD_DIMENSIONS = {
    "ISO_A0": (841000, 1189000),
    "ISO_A1": (594000, 841000),
    "ISO_A2": (420000, 594000),
    "ISO_A3": (297000, 420000),
    "ISO_A4": (210000, 297000),
    "ISO_A5": (148000, 210000),
    "ISO_A6": (105000, 148000),
    "ISO_B0": (1000000, 1414000),
    "ISO_B1": (707000, 1000000),
    "ISO_B2": (500000, 707000),
    "ISO_B3": (353000, 500000),
    "ISO_B4": (250000, 353000),
    "ISO_B5": (176000, 250000),
    "ISO_B6": (125000, 176000),
    "ISO_C0": (917000, 1297000),
    "ISO_C1": (648000, 917000),
    "ISO_C2": (458000, 648000),
    "ISO_C3": (324000, 458000),
    "ISO_C4": (229000, 324000),
    "ISO_C5": (162000, 229000),
    "ISO_C6": (114000, 162000),
    "ISO_DL": (110000, 220000),
    "NA_LETTER": (215900, 279400),
    "NA_LEGAL": (215900, 355600),
    "NA_EXECUTIVE": (184150, 266700),
    "NA_LEDGER": (279400, 431800),
    "NA_TABLOID": (279400, 431800),
    "NA_MONARCH": (98425, 190500),
    "NA_NUMBER_9": (98425, 225425),
    "NA_NUMBER_10": (104775, 241300),
    "JPN_CHOU3": (120000, 235000),
    "JPN_CHOU4": (90000, 205000),
    "JPN_YOU4": (105000, 235000),
    "JPN_POSTCARD": (100000, 148000),
}

def get_chromium_paper_name(paper_name):
    # Normalize name: uppercase, strip spaces, underscores, dashes, and hashes
    name_clean = paper_name.upper().replace(" ", "").replace("_", "").replace("-", "").replace("#", "")
    
    # Map normalized keys to standard Chromium identifiers
    norm_map = {
        "A0": "ISO_A0", "A1": "ISO_A1", "A2": "ISO_A2", "A3": "ISO_A3", "A4": "ISO_A4", "A5": "ISO_A5", "A6": "ISO_A6",
        "B0": "ISO_B0", "B1": "ISO_B1", "B2": "ISO_B2", "B3": "ISO_B3", "B4": "ISO_B4", "B5": "ISO_B5", "B6": "ISO_B6",
        "C0": "ISO_C0", "C1": "ISO_C1", "C2": "ISO_C2", "C3": "ISO_C3", "C4": "ISO_C4", "C5": "ISO_C5", "C6": "ISO_C6",
        "ISOB0": "ISO_B0", "ISOB1": "ISO_B1", "ISOB2": "ISO_B2", "ISOB3": "ISO_B3", "ISOB4": "ISO_B4", "ISOB5": "ISO_B5",
        "ISOC0": "ISO_C0", "ISOC1": "ISO_C1", "ISOC2": "ISO_C2", "ISOC3": "ISO_C3", "ISOC4": "ISO_C4", "ISOC5": "ISO_C5",
        "JISB0": "JIS_B0", "JISB1": "JIS_B1", "JISB2": "JIS_B2", "JISB3": "JIS_B3", "JISB4": "JIS_B4", "JISB5": "JIS_B5",
        "LETTER": "NA_LETTER", "NALETTER": "NA_LETTER",
        "LEGAL": "NA_LEGAL", "NALEGAL": "NA_LEGAL",
        "EXECUTIVE": "NA_EXECUTIVE", "NAEXECUTIVE": "NA_EXECUTIVE",
        "LEDGER": "NA_LEDGER", "NALEDGER": "NA_LEDGER",
        "TABLOID": "NA_TABLOID", "NATABLOID": "NA_TABLOID",
        "ENVELOPECHOU3": "JPN_CHOU3", "JAPANESEENVELOPECHOU3": "JPN_CHOU3", "CHOU3": "JPN_CHOU3",
        "ENVELOPECHOU4": "JPN_CHOU4", "JAPANESEENVELOPECHOU4": "JPN_CHOU4", "CHOU4": "JPN_CHOU4",
        "ENVELOPEYOU4": "JPN_YOU4", "JAPANENVELOPEYOU4": "JPN_YOU4", "YOU4": "JPN_YOU4",
        "JAPANESEPOSTCARD": "JPN_POSTCARD",
        "ENVELOPEDL": "ISO_DL", "DL": "ISO_DL",
        "ENVELOPEC5": "ISO_C5",
        "ENVELOPEC4": "ISO_C4",
        "ENVELOPEB5": "ISO_B5",
        "ENVELOPEMONARCH": "NA_MONARCH", "MONARCH": "NA_MONARCH",
        "ENVELOPE9": "NA_NUMBER_9", "NUMBER9": "NA_NUMBER_9",
        "ENVELOPE10": "NA_NUMBER_10", "NUMBER10": "NA_NUMBER_10",
    }
    
    if name_clean in norm_map:
        return norm_map[name_clean]

    # Fallback to custom display name format
    clean_display = paper_name.upper().replace(" ", "_").replace(".", "_").replace("-", "_").replace("#", "_")
    return f"CUSTOM_{clean_display}"

# --- REGISTRY POLICY FOR PAPER SIZE ---
# Chrome and Edge read PrintingPaperSizeDefault from registry each time the
# print dialog opens. Writing here OVERRIDES in-memory sticky settings.
_REGISTRY_POLICY_PATHS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Policies\Microsoft\Edge"),
    (winreg.HKEY_CURRENT_USER, r"Software\Policies\Google\Chrome"),
]
_REGISTRY_POLICY_VALUE = "PrintingPaperSizeDefault"

def set_paper_registry_policy(paper_name, width_microns=0, height_microns=0):
    """
    Write PrintingPaperSizeDefault to the Chrome/Edge registry policy paths.
    Uses the "custom" size path with exact width/height in microns so it works
    for every paper size — standard ISO, North American, Japanese, and
    proprietary printer-specific sizes.
    Returns True if at least one registry path was written successfully.
    """
    w = int(width_microns) if width_microns else 0
    h = int(height_microns) if height_microns else 0
    if not w or not h:
        # Fallback to A4 so we never write zero dimensions
        w, h = STANDARD_DIMENSIONS["ISO_A4"]
    policy_value = json.dumps({"name": "custom", "custom_size": {"width": w, "height": h}})

    written = False
    for hive, path in _REGISTRY_POLICY_PATHS:
        try:
            with winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, _REGISTRY_POLICY_VALUE, 0, winreg.REG_SZ, policy_value)
            logger.info(f"[Registry] Set {path}\\{_REGISTRY_POLICY_VALUE} = {policy_value}")
            written = True
        except Exception as e:
            logger.warning(f"[Registry] Could not write to {path}: {e}")
    return written

def clear_paper_registry_policy():
    """
    Remove PrintingPaperSizeDefault from registry so the user regains
    full control of paper size after printing.
    """
    for hive, path in _REGISTRY_POLICY_PATHS:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, _REGISTRY_POLICY_VALUE)
                    logger.info(f"[Registry] Cleared {path}\\{_REGISTRY_POLICY_VALUE}")
                except FileNotFoundError:
                    pass  # Already absent
        except Exception:
            pass

# Guarantee cleanup on normal exit or crash
atexit.register(clear_paper_registry_policy)

def find_matching_paper_dict_from_prefs(target_printer, target_paper):
    """
    Scans the browser Preferences files to find if the user previously selected
    this paper size for this printer, and returns the complete mediaSize dict if found.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    user_data_dirs = {
        "Google Chrome": os.path.join(local_app_data, r"Google\Chrome\User Data"),
        "Microsoft Edge": os.path.join(local_app_data, r"Microsoft\Edge\User Data")
    }
    
    for browser_name, user_data_dir in user_data_dirs.items():
        if os.path.exists(user_data_dir):
            for folder in os.listdir(user_data_dir):
                filepath = os.path.join(user_data_dir, folder, "Preferences")
                if os.path.exists(filepath):
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            prefs = json.load(f)
                        sticky = prefs.get("printing", {}).get("print_preview_sticky_settings", {})
                        if "appState" in sticky:
                            app_state = json.loads(sticky["appState"])
                            media_size = app_state.get("mediaSize", {})
                            selected_dest = app_state.get("selectedDestinationId", "")
                            
                            dest_match = (selected_dest == target_printer)
                            if not dest_match:
                                recent = app_state.get("recentDestinations", [])
                                if recent and isinstance(recent, list) and len(recent) > 0 and isinstance(recent[0], dict):
                                    if recent[0].get("id") == target_printer:
                                        dest_match = True
                                        
                            if dest_match and media_size:
                                custom_name = media_size.get("custom_display_name", "").lower()
                                name = media_size.get("name", "").lower()
                                target_lower = target_paper.lower()
                                
                                def clean(s):
                                    return s.replace(" ", "").replace("_", "").replace("-", "").replace("#", "").lower()
                                
                                if clean(custom_name) == clean(target_lower) or clean(name) == clean(target_lower) or clean(name.replace("iso_", "").replace("na_", "")) == clean(target_lower):
                                    logger.info(f"Found matching mediaSize dictionary in {browser_name} ({folder}) preferences: {media_size}")
                                    return media_size
                    except Exception as e:
                        pass
    return None

# Initial load of configurations
TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED = load_settings()
configure_logger(LOGGING_ENABLED)
running = True

# Clean any stale registry policy left from a previous crash
clear_paper_registry_policy()

# Thread-safety lock for shared configuration globals
_config_lock = threading.Lock()
_last_config_mtime = 0

# --- SINGLE INSTANCE LOCK ---
try:
    instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    instance_socket.bind(("127.0.0.1", 44556)) 
except socket.error:
    logger.error("⚠️ Auto-Printer is already running. Exiting to prevent duplicates.")
    os._exit(0)

def create_icon_image():
    image = Image.new('RGB', (64, 64), color=(255, 255, 255))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(0, 120, 215)) 
    dc.rectangle((24, 8, 40, 16), fill=(150, 150, 150)) 
    dc.rectangle((20, 48, 44, 60), fill=(200, 200, 200)) 
    return image

# --- PRINTER & PAPER SIZE MAPPING UI STRINGS ---
# Localized names for multi-language support (includes "Printer" for Edge)
DESTINATION_NAMES = [
    "Destination", "Destino", "Ziel", "Destinazione", "目标", "送信先", "대상", "Назначение",
    "Printer", "Impresora", "Drucken", "Imprimante", "Stampare", "打印机", "印表機", "プリンター", "プリンタ", "프린터", "Принтер"
]
MORE_SETTINGS_NAMES = ["More settings", "Más opciones", "Weitere Einstellungen", "Plus de paramètres", "Altre settings", "更多设置", "详细设置", "설정 더보기", "Дополнительные настройки"]
PAPER_SIZE_NAMES = ["Paper size", "Tamaño del papel", "Papierformat", "Taille du papier", "Formato carta", "纸张尺寸", "用紙サイズ", "용지 크기", "Размер бумаги"]

PRINT_NAMES = ["Print", "Imprimir", "Drucken", "Imprimer", "Stampare", "打印", "印刷", "Drukuj", "Печать", "프린트", "인쇄"]
CANCEL_NAMES = ["Cancel", "Cancelar", "Abbrechen", "Annuler", "Annulla", "取消", "キャンセル", "Anuluj", "Отмена", "취소"]
BLACKLIST_CLASSES = {
    'BrowserCaptionButtonContainer', 'WinCaptionButtonContainer',
    'LocationBarView', 'OmniboxViewViews', 'BookmarkBarView',
    'EdgeTabStrip', 'EdgeTabContainerImpl', 'TabStrip',
    'EdgeExtensionsToolbarContainer', 'PinnedToolbarActionsContainer',
    'ToolbarHubIconContainerView', 'EdgeVerticalTabContainerView'
}

def get_control_value(control):
    """
    Helper to extract the current selected text value from a combobox or button dropdown.
    """
    if not control:
        return ""
    try:
        # Check ValuePattern (ComboBoxControl)
        pattern = control.GetValuePattern()
        if pattern:
            val = pattern.Value
            if val:
                return val
    except Exception:
        pass
        
    try:
        # If it's a ButtonControl, look for child TextControl
        for child in control.GetChildren():
            if child.ControlTypeName == "TextControl":
                return child.Name
    except Exception:
        pass
        
    # Fallback: strip matched prefixes from the button name
    name = control.Name
    for prefix in DESTINATION_NAMES + PAPER_SIZE_NAMES:
        if name.startswith(prefix + " "):
            return name[len(prefix)+1:].strip()
        elif name == prefix:
            return ""
            
    return name

def walk_and_find_print_controls(control, max_depth=16, depth=1):
    controls = {
        "print_btn": None,
        "cancel_btn": None,
        "more_settings_btn": None,
        "destination_combo": None,
        "paper_size_combo": None
    }
    
    if depth > max_depth:
        return controls
        
    try:
        children = control.GetChildren()
    except Exception:
        return controls
        
    try:
        for child in children:
            # Wrap everything in try-except in case the child element is destroyed during traversal
            try:
                class_name = ""
                control_type = ""
                try:
                    class_name = child.ClassName
                except Exception:
                    pass
                try:
                    control_type = child.ControlTypeName
                except Exception:
                    continue  # Can't determine type; skip

                if class_name in BLACKLIST_CLASSES:
                    continue

                # Handle ButtonControl
                if control_type == "ButtonControl":
                    try:
                        name = child.Name
                    except Exception:
                        name = ""
                    if name in PRINT_NAMES:
                        controls["print_btn"] = child
                    elif name in CANCEL_NAMES:
                        controls["cancel_btn"] = child
                    elif name in MORE_SETTINGS_NAMES or name == "Fewer settings":
                        controls["more_settings_btn"] = child
                    else:
                        # Check for ButtonControl serving as Destination or Paper size dropdown
                        is_dest = False
                        for dest_prefix in DESTINATION_NAMES:
                            if name.startswith(dest_prefix + " ") or name == dest_prefix:
                                controls["destination_combo"] = child
                                is_dest = True
                                break
                        if not is_dest:
                            for paper_prefix in PAPER_SIZE_NAMES:
                                if name.startswith(paper_prefix + " ") or name == paper_prefix:
                                    controls["paper_size_combo"] = child
                                    break

                # Handle ComboBoxControl
                elif control_type == "ComboBoxControl":
                    try:
                        name = child.Name
                    except Exception:
                        name = ""
                    if name in DESTINATION_NAMES:
                        controls["destination_combo"] = child
                    elif name in PAPER_SIZE_NAMES:
                        controls["paper_size_combo"] = child

            except Exception:
                pass

            # Recurse
            try:
                sub_res = walk_and_find_print_controls(child, max_depth, depth + 1)
                for k, v in sub_res.items():
                    if v is not None:
                        controls[k] = v
            except Exception:
                pass
    except Exception:
        pass

    return controls

def _write_paper_size_to_prefs(selected_printer, target_paper_config):
    """
    Directly writes the configured paper size into all browser Preference files.
    This is a reliable fallback when UI automation cannot interact with the print dialog.
    Returns True if at least one Preferences file was updated.
    """
    if isinstance(target_paper_config, dict):
        paper_name = target_paper_config.get("paper_name", "")
        vendor_id = str(target_paper_config.get("vendor_id", ""))
        width_microns = target_paper_config.get("width_microns", 0)
        height_microns = target_paper_config.get("height_microns", 0)
    else:
        paper_name = target_paper_config
        vendor_id = ""
        width_microns = 0
        height_microns = 0

    if not paper_name:
        return False

    # Try to find an exact mediaSize dict from prefs (most accurate)
    matching_size_dict = find_matching_paper_dict_from_prefs(selected_printer, paper_name)

    if matching_size_dict:
        target_size = dict(matching_size_dict)
        if vendor_id:
            target_size["vendor_id"] = vendor_id
    else:
        resolved_name = get_chromium_paper_name(paper_name)
        width_val = int(width_microns) if width_microns else 0
        height_val = int(height_microns) if height_microns else 0
        if resolved_name in STANDARD_DIMENSIONS:
            width_val, height_val = STANDARD_DIMENSIONS[resolved_name]
        target_size = {
            "name": resolved_name,
            "width_microns": width_val,
            "height_microns": height_val,
            "custom_display_name": paper_name,
            "imageable_area_bottom_microns": 0,
            "imageable_area_left_microns": 0,
            "imageable_area_right_microns": width_val,
            "imageable_area_top_microns": height_val,
            "vendor_id": vendor_id
        }

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    user_data_dirs = {
        "Google Chrome": os.path.join(local_app_data, r"Google\Chrome\User Data"),
        "Microsoft Edge": os.path.join(local_app_data, r"Microsoft\Edge\User Data")
    }

    updated = False
    for browser_name, user_data_dir in user_data_dirs.items():
        if not os.path.exists(user_data_dir):
            continue
        for folder in os.listdir(user_data_dir):
            filepath = os.path.join(user_data_dir, folder, "Preferences")
            if not os.path.exists(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
                if "printing" not in prefs:
                    prefs["printing"] = {}
                if "print_preview_sticky_settings" not in prefs["printing"]:
                    prefs["printing"]["print_preview_sticky_settings"] = {}
                sticky = prefs["printing"]["print_preview_sticky_settings"]
                app_state = {}
                if "appState" in sticky:
                    try:
                        app_state = json.loads(sticky["appState"])
                    except Exception:
                        pass
                app_state["mediaSize"] = target_size
                app_state["version"] = 2
                app_state["selectedDestinationId"] = selected_printer
                app_state["recentDestinations"] = [
                    {"id": selected_printer, "origin": "local", "displayName": selected_printer}
                ]
                with _config_lock:
                    changes = get_browser_prefs_from_config(BROWSER_PRINT_PREFS)
                for key, val in changes.items():
                    app_state[key] = val
                sticky["appState"] = json.dumps(app_state)
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, indent=2, ensure_ascii=False)
                updated = True
            except Exception as ex:
                logger.warning(f"Prefs fallback: could not update {filepath}: {ex}")
    if updated:
        logger.info(f"[Prefs fallback] Wrote paper size '{paper_name}' for printer '{selected_printer}' to browser preferences.")
    return updated


def auto_configure_paper_size(active_win, controls, printer_mapping=None):
    if printer_mapping is None:
        printer_mapping = PRINTER_PAPER_MAPPING
    try:
        # Temporarily allow a small search timeout for expanding menus
        auto.SetGlobalSearchTimeout(0.3)

        # 1. Locate the "Destination/Printer" dropdown
        destination_combo = controls["destination_combo"]
        if not destination_combo:
            logger.info("Destination/Printer dropdown not found.")
            return False, False

        # Get the selected printer name
        selected_printer = get_control_value(destination_combo)
        logger.info(f"Selected printer detected: '{selected_printer}'")

        target_paper_config = printer_mapping.get(selected_printer)
        if not target_paper_config:
            logger.info(f"No custom paper size configured for '{selected_printer}'.")
            return True, False
            
        if isinstance(target_paper_config, dict):
            target_paper_name = target_paper_config.get("paper_name", "")
        else:
            target_paper_name = target_paper_config
            if "," in target_paper_config:
                target_paper_name = target_paper_config.split(",", 1)[0].strip()
            
        logger.info(f"Target paper size for '{selected_printer}' is '{target_paper_name}'.")
        
        # 2. Locate the "Paper size" dropdown
        paper_size_combo = controls["paper_size_combo"]
        
        # If hidden, try clicking "More settings" to expand the settings panel
        if not paper_size_combo:
            more_settings_btn = controls["more_settings_btn"]
            if more_settings_btn:
                logger.info("Paper size dropdown is hidden. Expanding 'More settings'...")
                try:
                    more_settings_btn.GetInvokePattern().Invoke()
                except Exception:
                    try:
                        more_settings_btn.Click()
                    except Exception:
                        try:
                            more_settings_btn.SetFocus()
                            more_settings_btn.SendKeys('{Space}')
                        except Exception:
                            pass
                
                # Wait with retries for expansion animation to finish and expose dropdown
                for attempt in range(15):
                    time.sleep(0.2)
                    new_controls = walk_and_find_print_controls(active_win, 16)
                    paper_size_combo = new_controls["paper_size_combo"]
                    if paper_size_combo:
                        logger.info(f"Paper size dropdown appeared after {attempt+1} retry(s).")
                        break
                    # Log all button names found on first and last attempt for diagnostics
                    if attempt == 0 or attempt == 14:
                        try:
                            btn_names = []
                            def _collect_btns(ctrl, d=0):
                                if d > 12:
                                    return
                                try:
                                    for c in ctrl.GetChildren():
                                        try:
                                            if c.ControlTypeName in ("ButtonControl", "ComboBoxControl"):
                                                btn_names.append(repr(c.Name))
                                        except Exception:
                                            pass
                                        _collect_btns(c, d+1)
                                except Exception:
                                    pass
                            _collect_btns(active_win)
                            logger.info(f"[Diag attempt {attempt}] Buttons/combos in dialog: {btn_names[:40]}")
                        except Exception:
                            pass
                        
        if not paper_size_combo:
            logger.warning("Could not find 'Paper size' dropdown via UI — using registry policy fallback.")
            # PRIMARY FALLBACK: write to Windows Registry policy
            # Chrome/Edge reads PrintingPaperSizeDefault on every print dialog open.
            # This overrides the in-memory sticky settings without needing a browser restart.
            w = target_paper_config.get("width_microns", 0) if isinstance(target_paper_config, dict) else 0
            h = target_paper_config.get("height_microns", 0) if isinstance(target_paper_config, dict) else 0
            reg_ok = set_paper_registry_policy(target_paper_name, w, h)
            return reg_ok, reg_ok  # reopen needed if registry write succeeded
            
        # 3. Read and compare current paper size
        current_paper = get_control_value(paper_size_combo)
        if current_paper == target_paper_name:
            logger.info(f"Paper size is already set to '{target_paper_name}' for printer '{selected_printer}'.")
            return True, False
            
        # 4. Open the paper size dropdown and select target paper size
        logger.info(f"Changing paper size from '{current_paper}' to '{target_paper_name}'...")
        try:
            # Try combobox expand pattern
            paper_size_combo.GetExpandCollapsePattern().Expand()
        except Exception:
            try:
                # Try button invoke pattern
                paper_size_combo.GetInvokePattern().Invoke()
            except Exception:
                try:
                    paper_size_combo.Click()
                except Exception as ex:
                    logger.error(f"Failed to open/click paper size dropdown: {ex}")
                    w = target_paper_config.get("width_microns", 0) if isinstance(target_paper_config, dict) else 0
                    h = target_paper_config.get("height_microns", 0) if isinstance(target_paper_config, dict) else 0
                    reg_ok = set_paper_registry_policy(target_paper_name, w, h)
                    return reg_ok, reg_ok
                    
        time.sleep(0.5) # Wait for dropdown menu to draw
        
        # Robust scanning for dropdown items
        def clean_str(s):
            return s.lower().replace(" ", "").replace("_", "").replace("-", "").replace("#", "")
            
        target_clean = clean_str(target_paper_name)
        target_item = None
        dropdown_items = []
        try:
            dropdown_items = paper_size_combo.GetChildren()
        except Exception:
            pass
            
        def scan_container_for_item(container, target_clean_val):
            try:
                children = container.GetChildren()
            except Exception:
                return None
            for child in children:
                try:
                    name_clean = clean_str(child.Name)
                    control_type = child.ControlTypeName
                    if control_type in ["ListItemControl", "MenuItemControl", "ButtonControl", "TextControl"]:
                        if target_clean_val in name_clean or name_clean in target_clean_val:
                            return child
                except Exception:
                    pass
                try:
                    res = scan_container_for_item(child, target_clean_val)
                    if res:
                        return res
                except Exception:
                    pass
            return None
            
        if not dropdown_items:
            try:
                desktop = auto.GetRootControl()
                for child in desktop.GetChildren():
                    if child.ClassName in ["ComboLBox", "DropDownForm", "NetWindow", "Menu", "RootView"]:
                        dropdown_items = child.GetChildren()
                        if dropdown_items:
                            break
            except Exception:
                pass
                
        for item_ctrl in dropdown_items:
            try:
                item_name = item_ctrl.Name
                if target_clean in clean_str(item_name) or clean_str(item_name) in target_clean:
                    target_item = item_ctrl
                    break
            except Exception:
                pass
                
        if not target_item:
            target_item = scan_container_for_item(active_win, target_clean)
            
        if not target_item:
            target_item = scan_container_for_item(auto.GetRootControl(), target_clean)
            
        if not target_item:
            target_item = paper_size_combo.ListItemControl(Name=target_paper_name)
            
        if target_item and target_item.Exists(0.5, 0):
            logger.info(f"Target paper size item '{target_item.Name}' found. Selecting/clicking it...")
            try:
                target_item.GetSelectionItemPattern().Select()
            except Exception:
                try:
                    target_item.GetInvokePattern().Invoke()
                except Exception:
                    target_item.Click()
            time.sleep(0.6)
            logger.info("Paper size successfully updated via UI!")
            return True, False
        else:
            logger.warning(f"Target paper size option '{target_paper_name}' not found in dropdown — using registry policy fallback.")
            w = target_paper_config.get("width_microns", 0) if isinstance(target_paper_config, dict) else 0
            h = target_paper_config.get("height_microns", 0) if isinstance(target_paper_config, dict) else 0
            reg_ok = set_paper_registry_policy(target_paper_name, w, h)
            return reg_ok, True
            
    except Exception as e:
        logger.error(f"Error configuring paper size: {e}")
        return False, False
    finally:
        auto.SetGlobalSearchTimeout(0)

def monitor_print_dialog():
    global running, TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS
    
    logger.info(f"🚀 Auto-Printer started! Monitoring for: '{TARGET_WINDOW_TITLE}'")
    
    with auto.UIAutomationInitializerInThread():
        auto.SetGlobalSearchTimeout(0)
        
        while running:
            try:
                # Only reload settings when the config file actually changes
                global _last_config_mtime
                try:
                    current_mtime = os.path.getmtime(CONFIG_FILE)
                except Exception:
                    current_mtime = 0
                if current_mtime != _last_config_mtime:
                    with _config_lock:
                        _last_config_mtime = current_mtime
                        TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED = load_settings()
                        configure_logger(LOGGING_ENABLED)

                active_win = None
                try:
                    active_win = auto.GetForegroundControl()
                except Exception:
                    pass

                sleep_time = 1.0

                if active_win:
                    win_name = ""
                    try:
                        win_name = active_win.Name.lower()
                    except Exception:
                        pass

                    with _config_lock:
                        target_title = TARGET_WINDOW_TITLE

                    is_target_match = (target_title == "*") or (target_title.lower() in win_name)

                    try:
                        is_chrome = (active_win.ClassName == "Chrome_WidgetWin_1")
                    except Exception:
                        is_chrome = False

                    if is_chrome and is_target_match:
                        # Chromium window matches; poll slightly faster
                        sleep_time = 0.35 
                        
                        # Find the Print dialog container (ClassName="RootView") to query its nested DocumentControl
                        print_win = active_win.WindowControl(searchDepth=8, ClassName="RootView")
                        if print_win.Exists(0, 0):
                            # Force build the accessibility tree on the print WebUI document
                            doc = print_win.DocumentControl(searchDepth=16)
                            if doc.Exists(0, 0):
                                try:
                                    doc.GetLegacyIAccessiblePattern()
                                except Exception:
                                    pass
                        
                        # Find controls in the active window
                        controls = walk_and_find_print_controls(active_win, 16)
                        print_button = controls["print_btn"]
                        cancel_button = controls["cancel_btn"]
                        
                        if print_button and cancel_button:
                            try:
                                # Wait up to 1.6s for destination_combo to appear in UIA tree
                                retries = 0
                                while retries < 8 and not controls["destination_combo"]:
                                    time.sleep(0.2)
                                    controls = walk_and_find_print_controls(active_win, 16)
                                    print_button = controls["print_btn"]
                                    cancel_button = controls["cancel_btn"]
                                    retries += 1

                                # --- Determine if paper size needs to be changed ---
                                selected_printer = get_control_value(controls.get("destination_combo", None))
                                with _config_lock:
                                    printer_mapping = PRINTER_PAPER_MAPPING
                                target_config = printer_mapping.get(selected_printer, {}) if selected_printer else {}
                                target_paper_name = ""
                                if isinstance(target_config, dict):
                                    target_paper_name = target_config.get("paper_name", "")
                                elif isinstance(target_config, str):
                                    target_paper_name = target_config.split(",", 1)[0].strip()

                                # Check if paper size is already correct via UI
                                paper_already_correct = False
                                if target_paper_name:
                                    paper_combo = controls.get("paper_size_combo")
                                    if paper_combo:
                                        current_paper = get_control_value(paper_combo)
                                        def _clean(s):
                                            return s.lower().replace(" ", "").replace("_", "").replace("-", "").replace("#", "")
                                        if _clean(current_paper) == _clean(target_paper_name):
                                            paper_already_correct = True

                                if paper_already_correct:
                                    # Paper size already matches — just print
                                    logger.info(f"Paper size already correct ('{target_paper_name}'). Printing...")
                                    config_success = True
                                    used_prefs_fallback = False
                                else:
                                    # Attempt to configure paper size via UI or prefs fallback
                                    config_success, used_prefs_fallback = auto_configure_paper_size(active_win, controls, printer_mapping)

                                # --- Decide action ---
                                should_print = False
                                needs_reopen = False

                                if config_success:
                                    if used_prefs_fallback:
                                        # Prefs were written while dialog is open — browser won't re-read them
                                        # Must cancel, then reopen dialog with Ctrl+P
                                        needs_reopen = True
                                    else:
                                        should_print = True
                                else:
                                    if selected_printer and not printer_mapping.get(selected_printer):
                                        should_print = True
                                        logger.info(f"No configuration mapped for '{selected_printer}'. Printing with defaults.")

                                # --- Cancel & reopen flow ---
                                if needs_reopen:
                                    logger.info("📄 Prefs updated. Cancelling dialog to reopen with correct paper size...")
                                    try:
                                        cancel_button.GetInvokePattern().Invoke()
                                    except Exception:
                                        try:
                                            cancel_button.Click()
                                        except Exception:
                                            pass
                                    # Wait for dialog to fully close and Chrome to finish cleanup
                                    time.sleep(2.0)
                                    # Write prefs AFTER dialog is closed so Chrome doesn't overwrite them
                                    _write_paper_size_to_prefs(selected_printer, target_config)
                                    # Re-trigger Ctrl+P to reopen print dialog
                                    try:
                                        active_win.SendKeys("{Ctrl}p")
                                    except Exception:
                                        pass
                                    # Wait for new dialog to open and render
                                    time.sleep(3.0)
                                    # Wake up accessibility tree for the new dialog
                                    new_print_win = active_win.WindowControl(searchDepth=8, ClassName="RootView")
                                    if new_print_win.Exists(0, 0):
                                        new_doc = new_print_win.DocumentControl(searchDepth=16)
                                        if new_doc.Exists(0, 0):
                                            try:
                                                new_doc.GetLegacyIAccessiblePattern()
                                            except Exception:
                                                pass
                                    # Find print/cancel buttons in the new dialog
                                    new_controls = walk_and_find_print_controls(active_win, 16)
                                    new_print_btn = new_controls.get("print_btn")
                                    new_cancel_btn = new_controls.get("cancel_btn")
                                    if new_print_btn and new_cancel_btn:
                                        logger.info("🖨️ Re-opened dialog ready. Triggering print with corrected paper size...")
                                        cancel_name = "Cancel"
                                        try:
                                            cancel_name = new_cancel_btn.Name
                                        except Exception:
                                            pass
                                        try:
                                            new_print_btn.GetInvokePattern().Invoke()
                                        except Exception:
                                            try:
                                                new_print_btn.SetFocus()
                                                new_print_btn.SendKeys('{Space}')
                                            except Exception:
                                                pass
                                        logger.info("✅ Print triggered. Waiting for dialog to close...")
                                        while running:
                                            time.sleep(0.5)
                                            try:
                                                if not active_win.ButtonControl(searchDepth=16, Name=cancel_name).Exists(0, 0):
                                                    break
                                            except Exception:
                                                break
                                        logger.info("🔓 Dialog closed. System re-armed and ready for next print job!")
                                    else:
                                        logger.warning("⚠️ Could not find print button in re-opened dialog.")

                                # --- Direct print flow ---
                                elif should_print:
                                    logger.info("🖨️ Print dialog detected and verified! Triggering print...")
                                    cancel_name = "Cancel"
                                    try:
                                        cancel_name = cancel_button.Name
                                    except Exception:
                                        pass
                                    try:
                                        print_button.GetInvokePattern().Invoke()
                                    except Exception:
                                        try:
                                            print_button.SetFocus()
                                            print_button.SendKeys('{Space}')
                                        except Exception:
                                            pass
                                    logger.info("✅ Print triggered. Waiting for dialog to close...")
                                    while running:
                                        time.sleep(0.5)
                                        try:
                                            if not active_win.ButtonControl(searchDepth=16, Name=cancel_name).Exists(0, 0):
                                                break
                                        except Exception:
                                            break
                                    logger.info("🔓 Dialog closed. System re-armed and ready for next print job!")
                                else:
                                    logger.warning("⚠️ Paper size configuration failed. Retrying next cycle...")
                            finally:
                                clear_paper_registry_policy()

            except Exception:
                logger.error(traceback.format_exc())
                
            time.sleep(sleep_time)

def change_title(icon, item):
    global TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED
    with _config_lock:
        TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED = load_settings()

    ps_script = """
    Add-Type -AssemblyName Microsoft.VisualBasic
    $defaultVal = $env:TARGET_WINDOW_TITLE
    $result = [Microsoft.VisualBasic.Interaction]::InputBox('Enter the window title to target.`n`n(Type * to target ALL Chromium browsers):', 'Change Auto-Printer Target', $defaultVal)
    Write-Output "DIALOG_RESULT:$result"
    """

    try:
        CREATE_NO_WINDOW = 0x08000000
        env = os.environ.copy()
        with _config_lock:
            env["TARGET_WINDOW_TITLE"] = TARGET_WINDOW_TITLE

        process = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            env=env
        )

        output = process.stdout.strip()
        for line in output.splitlines():
            if line.startswith("DIALOG_RESULT:"):
                new_title = line.split("DIALOG_RESULT:", 1)[1].strip()

                if new_title:
                    logger.info(f"⚙️ Target changed to: '{new_title}'")
                    with _config_lock:
                        TARGET_WINDOW_TITLE = new_title
                        save_settings(new_title, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, None, LOGGING_ENABLED)
                    icon.update_menu()
                    break
    except Exception as e:
        logger.error(f"Dialog Error: {str(e)}")

def map_printer_size(icon, item):
    global TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED

    # Reload settings first to get the latest state
    with _config_lock:
        TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED = load_settings()

    ps_script = r"""
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Map Printer Paper Size"
    $form.Size = New-Object System.Drawing.Size(360, 240)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    
    # Font
    $font = New-Object System.Drawing.Font("Segoe UI", 9)
    $form.Font = $font
    
    # Label for Printer
    $lblPrinter = New-Object System.Windows.Forms.Label
    $lblPrinter.Text = "Select Installed Printer:"
    $lblPrinter.Location = New-Object System.Drawing.Point(20, 15)
    $lblPrinter.Size = New-Object System.Drawing.Size(300, 20)
    $form.Controls.Add($lblPrinter)
    
    # ComboBox for Printer
    $cbPrinter = New-Object System.Windows.Forms.ComboBox
    $cbPrinter.Location = New-Object System.Drawing.Point(20, 35)
    $cbPrinter.Size = New-Object System.Drawing.Size(300, 25)
    $cbPrinter.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
    $form.Controls.Add($cbPrinter)
    
    # Label for Paper Size
    $lblPaper = New-Object System.Windows.Forms.Label
    $lblPaper.Text = "Select Default Paper Size:"
    $lblPaper.Location = New-Object System.Drawing.Point(20, 75)
    $lblPaper.Size = New-Object System.Drawing.Size(300, 20)
    $form.Controls.Add($lblPaper)
    
    # ComboBox for Paper Size
    $cbPaper = New-Object System.Windows.Forms.ComboBox
    $cbPaper.Location = New-Object System.Drawing.Point(20, 95)
    $cbPaper.Size = New-Object System.Drawing.Size(300, 25)
    $cbPaper.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
    $form.Controls.Add($cbPaper)
    
    # Load config file
    $configPath = $env:CONFIG_FILE_PATH
    $config = $null
    if (Test-Path $configPath) {
        $config = Get-Content -Raw $configPath | ConvertFrom-Json
    }
    
    # SelectedIndexChanged Event for Printer Dropdown (Dynamic Paper Loading)
    $cbPrinter.add_SelectedIndexChanged({
        $cbPaper.Items.Clear()
        $selectedPrinter = $cbPrinter.SelectedItem
        if ($selectedPrinter) {
            $caps = $null
            if ($config -and $config.printer_capabilities -and $config.printer_capabilities.$selectedPrinter) {
                $caps = $config.printer_capabilities.$selectedPrinter
            }
            
            if ($caps) {
                foreach ($size in $caps) {
                    $itemText = "$($size.paper_name) (ID: $($size.vendor_id))"
                    if ($cbPaper.Items.Contains($itemText) -eq $false) {
                        $cbPaper.Items.Add($itemText)
                    }
                }
            } else {
                # Fallback standard sizes
                $cbPaper.Items.Add("A4 (ID: 9)")
                $cbPaper.Items.Add("Letter (ID: 1)")
                $cbPaper.Items.Add("8.5 x 13 (ID: 14)")
            }
            
            # Select mapped paper size if it exists
            $mapped = $null
            if ($config -and $config.mappings -and $config.mappings.$selectedPrinter) {
                $mapped = $config.mappings.$selectedPrinter
            }
            
            $foundIndex = -1
            if ($mapped) {
                $mappedPaperName = $mapped.paper_name
                for ($i = 0; $i -lt $cbPaper.Items.Count; $i++) {
                    $item = $cbPaper.Items[$i]
                    if ($item -eq $mappedPaperName -or $item -like "$mappedPaperName (ID: *") {
                        $foundIndex = $i
                        break
                    }
                }
            }
            
            if ($foundIndex -ne -1) {
                $cbPaper.SelectedIndex = $foundIndex
            } elseif ($cbPaper.Items.Count -gt 0) {
                $cbPaper.SelectedIndex = 0
            }
        }
    })
    
    # Populate Printers
    $printers = @()
    if ($config -and $config.printer_capabilities) {
        foreach ($p in $config.printer_capabilities.psobject.properties.Name) {
            $printers += $p
        }
    }
    if ($printers.Count -eq 0) {
        $printers = [System.Drawing.Printing.PrinterSettings]::InstalledPrinters
    }
    
    foreach ($p in $printers) {
        $cbPrinter.Items.Add($p)
    }
    
    $defaultPrinter = (New-Object System.Drawing.Printing.PrinterSettings).PrinterName
    $cbPrinter.SelectedItem = $defaultPrinter
    if ($cbPrinter.SelectedIndex -eq -1 -and $cbPrinter.Items.Count -gt 0) {
        $cbPrinter.SelectedIndex = 0
    }
    
    # Save Button
    $btnSave = New-Object System.Windows.Forms.Button
    $btnSave.Text = "Save Mapping"
    $btnSave.Location = New-Object System.Drawing.Point(30, 145)
    $btnSave.Size = New-Object System.Drawing.Size(130, 30)
    $btnSave.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Controls.Add($btnSave)
    
    # Cancel Button
    $btnCancel = New-Object System.Windows.Forms.Button
    $btnCancel.Text = "Cancel"
    $btnCancel.Location = New-Object System.Drawing.Point(180, 145)
    $btnCancel.Size = New-Object System.Drawing.Size(130, 30)
    $btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Controls.Add($btnCancel)
    
    $form.AcceptButton = $btnSave
    $form.CancelButton = $btnCancel
    
    $result = $form.ShowDialog()
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
        $selectedPrinter = $cbPrinter.SelectedItem
        $selectedItem = $cbPaper.SelectedItem
        if ($selectedItem) {
            $paperName = $selectedItem
            $vendorId = ""
            if ($selectedItem -match "^(.*) \(ID: (\d+)\)$") {
                $paperName = $Matches[1].Trim()
                $vendorId = $Matches[2].Trim()
            }
            
            $widthMicrons = 0
            $heightMicrons = 0
            if ($config -and $config.printer_capabilities -and $config.printer_capabilities.$selectedPrinter) {
                $sizeObj = $config.printer_capabilities.$selectedPrinter | Where-Object { $_.paper_name -eq $paperName } | Select-Object -First 1
                if ($sizeObj) {
                    $widthMicrons = $sizeObj.width_microns
                    $heightMicrons = $sizeObj.height_microns
                }
            }
            
            if ($widthMicrons -eq 0 -or $heightMicrons -eq 0) {
                Add-Type -AssemblyName System.Drawing
                $ps = New-Object System.Drawing.Printing.PrinterSettings
                $ps.PrinterName = $selectedPrinter
                $selectedSize = $ps.PaperSizes | Where-Object { $_.PaperName -eq $paperName } | Select-Object -First 1
                if ($selectedSize) {
                    $widthMicrons = [int]($selectedSize.Width * 254)
                    $heightMicrons = [int]($selectedSize.Height * 254)
                    $vendorId = $selectedSize.RawKind
                }
            }
            
            if ($widthMicrons -ne 0) {
                Write-Output "RESULT_PRINTER:$selectedPrinter"
                Write-Output "RESULT_PAPER:$paperName"
                Write-Output "RESULT_WIDTH_MICRONS:$widthMicrons"
                Write-Output "RESULT_HEIGHT_MICRONS:$heightMicrons"
                Write-Output "RESULT_VENDOR_ID:$vendorId"
            }
        }
    }
    """
    
    try:
        CREATE_NO_WINDOW = 0x08000000
        env = os.environ.copy()
        env["CONFIG_FILE_PATH"] = CONFIG_FILE
        
        process = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            env=env
        )
        
        output = process.stdout.strip()
        printer = None
        paper = None
        width_microns = None
        height_microns = None
        vendor_id = None
        
        for line in output.splitlines():
            if line.startswith("RESULT_PRINTER:"):
                printer = line.split("RESULT_PRINTER:", 1)[1].strip()
            elif line.startswith("RESULT_PAPER:"):
                paper = line.split("RESULT_PAPER:", 1)[1].strip()
            elif line.startswith("RESULT_WIDTH_MICRONS:"):
                width_microns = line.split("RESULT_WIDTH_MICRONS:", 1)[1].strip()
            elif line.startswith("RESULT_HEIGHT_MICRONS:"):
                height_microns = line.split("RESULT_HEIGHT_MICRONS:", 1)[1].strip()
            elif line.startswith("RESULT_VENDOR_ID:"):
                vendor_id = line.split("RESULT_VENDOR_ID:", 1)[1].strip()
                
        if printer and paper and width_microns and height_microns and vendor_id:
            logger.info(f"⚙️ Setting mapping: '{printer}' = '{paper}' (Width: {width_microns}, Height: {height_microns}, VendorId: {vendor_id})")
            
            # Look up previously selected paper size details in Preferences file
            matching_size_dict = find_matching_paper_dict_from_prefs(printer, paper)
            
            # 1. Warn user and close browsers to unlock preference files
            confirm_msg = "Auto-Printer needs to close all Chrome and Edge windows to apply paper size settings.\n\nUnsaved work in browsers will be lost. Continue?"
            escaped_confirm = confirm_msg.replace("'", "''")
            ps_confirm = f"Add-Type -AssemblyName System.Windows.Forms; $r = [System.Windows.Forms.MessageBox]::Show('{escaped_confirm}', 'Auto-Printer Warning', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning); Write-Output \"CONFIRM:$r\""
            confirm_proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_confirm],
                capture_output=True,
                text=True,
                creationflags=0x08000000
            )
            if "CONFIRM:No" in confirm_proc.stdout:
                logger.info("User cancelled browser closure. Aborting mapping.")
                return

            logger.info("Closing Chrome and Edge to unlock preference files...")

            def kill_browsers():
                subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
                subprocess.run(["taskkill", "/f", "/im", "msedge.exe"], creationflags=0x08000000)

            kill_browsers()
            time.sleep(0.5)  # Wait for initial file release
            
            # 2. Formulate target paper size config
            if matching_size_dict:
                target_size = matching_size_dict
                # Ensure vendor_id is updated to what we received from driver (just in case)
                target_size["vendor_id"] = str(vendor_id)
            else:
                resolved_name = get_chromium_paper_name(paper)
                width_val = int(width_microns)
                height_val = int(height_microns)
                if resolved_name in STANDARD_DIMENSIONS:
                    width_val, height_val = STANDARD_DIMENSIONS[resolved_name]
                    
                target_size = {
                    "name": resolved_name,
                    "width_microns": width_val,
                    "height_microns": height_val,
                    "custom_display_name": paper,
                    "imageable_area_bottom_microns": 0,
                    "imageable_area_left_microns": 0,
                    "imageable_area_right_microns": width_val,
                    "imageable_area_top_microns": height_val,
                    "vendor_id": str(vendor_id)
                }
            
            # 3. Apply settings to all profile preference files
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            user_data_dirs = {
                "Google Chrome": os.path.join(local_app_data, r"Google\Chrome\User Data"),
                "Microsoft Edge": os.path.join(local_app_data, r"Microsoft\Edge\User Data")
            }
            
            # Helper to write preferences
            def update_preferences_file(filepath, target_size_dict, printer_name):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        prefs = json.load(f)
                    if "printing" not in prefs:
                        prefs["printing"] = {}
                    if "print_preview_sticky_settings" not in prefs["printing"]:
                        prefs["printing"]["print_preview_sticky_settings"] = {}
                    sticky = prefs["printing"]["print_preview_sticky_settings"]
                    
                    app_state = {}
                    if "appState" in sticky:
                        try:
                            app_state = json.loads(sticky["appState"])
                        except Exception:
                            pass
                            
                    # Inject paper size
                    app_state["mediaSize"] = target_size_dict
                    app_state["version"] = 2
                    
                    # Inject printer selection
                    app_state["selectedDestinationId"] = printer_name
                    app_state["recentDestinations"] = [
                        {
                            "id": printer_name,
                            "origin": "local",
                            "displayName": printer_name
                        }
                    ]
                    
                    # Retrieve defaults from config
                    with _config_lock:
                        changes = get_browser_prefs_from_config(BROWSER_PRINT_PREFS)
                    for key, val in changes.items():
                        app_state[key] = val
                    
                    sticky["appState"] = json.dumps(app_state)
                    
                    # Disable Startup Boost in Edge preferences to prevent background processes from locking/overwriting the file
                    if "microsoft" in filepath.lower() or "edge" in filepath.lower():
                        if "startup_boost" not in prefs:
                            prefs["startup_boost"] = {}
                        prefs["startup_boost"]["enabled"] = False
                        
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(prefs, f, indent=2, ensure_ascii=False)
                    return True
                except Exception as ex:
                    logger.error(f"Failed to update profile {filepath}: {ex}")
                    return False
            
            def apply_preferences():
                for browser_name, user_data_dir in user_data_dirs.items():
                    if os.path.exists(user_data_dir):
                        for folder in os.listdir(user_data_dir):
                            filepath = os.path.join(user_data_dir, folder, "Preferences")
                            if os.path.exists(filepath):
                                update_preferences_file(filepath, target_size, printer)
                                logger.info(f"Updated preferences for {browser_name} ({folder})")
                                
            # Write preferences initially
            apply_preferences()
            
            # Kill again in case Startup Boost or other threads restarted browser background processes during write
            time.sleep(0.3)
            kill_browsers()
            time.sleep(0.3)
            
            # Write preferences a second time to ensure absolute persistence
            apply_preferences()
            
            # 4. Save mapping in printer_config.json
            with _config_lock:
                PRINTER_PAPER_MAPPING[printer] = {
                    "paper_name": paper,
                    "vendor_id": str(vendor_id),
                    "width_microns": int(width_microns),
                    "height_microns": int(height_microns)
                }
                save_settings(TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, None, LOGGING_ENABLED)
            icon.update_menu()
            # Also write the registry policy immediately so the next print dialog uses the correct paper size
            set_paper_registry_policy(paper, int(width_microns), int(height_microns))
            logger.info("⚙️ Settings successfully applied to browsers, registry, and saved to printer_config.json.")
            
            # Show system tray balloon notification
            try:
                icon.notify(f"Settings successfully applied for {printer} ({paper})!", "Auto-Printer")
            except Exception:
                pass
                
            # Show a standard message box popup
            try:
                msg = f"Browser print preferences have been successfully configured and saved!\n\nPrinter: {printer}\nPaper Size: {paper}"
                # Format string to escape quotes for PowerShell double quotes
                escaped_msg = msg.replace('"', '`"')
                ps_msg_cmd = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('{escaped_msg}', 'Auto-Printer', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)"
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_msg_cmd],
                    creationflags=CREATE_NO_WINDOW
                )
            except Exception:
                pass
            
    except Exception as e:
        logger.error(f"Mapping Dialog Error: {str(e)}")

def open_settings_file(icon, item):
    try:
        logger.info(f"Opening config file: {CONFIG_FILE}")
        os.startfile(CONFIG_FILE)
    except Exception as e:
        logger.error(f"Failed to open settings file: {e}")

def quit_app(icon, item):
    global running
    logger.info("🛑 Shutting down Auto-Printer...")
    running = False
    icon.stop()
    try:
        instance_socket.close()
    except Exception:
        pass

def setup(icon):
    icon.visible = True
    monitor_thread = threading.Thread(target=monitor_print_dialog, daemon=True)
    monitor_thread.start()

def get_status_text(item):
    if TARGET_WINDOW_TITLE.strip() == "*":
        return 'Status: Targeting ALL Windows'
    return f'Status: Targeting "{TARGET_WINDOW_TITLE}"'

def toggle_logging(icon, item):
    global LOGGING_ENABLED
    with _config_lock:
        LOGGING_ENABLED = not LOGGING_ENABLED
        save_settings(TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, None, LOGGING_ENABLED)
        configure_logger(LOGGING_ENABLED)
    status = "enabled" if LOGGING_ENABLED else "disabled"
    logger.info(f"Logging {status}.")
    try:
        icon.notify(f"File logging {status}.", "Auto-Printer")
    except Exception:
        pass
    icon.update_menu()

def get_logging_status_text(item):
    return f'Logging to file: {"On" if LOGGING_ENABLED else "Off"}'

def refresh_printer_list(icon, item):
    global TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, LOGGING_ENABLED
    try:
        capabilities = scan_printer_capabilities()
        with _config_lock:
            save_settings(TARGET_WINDOW_TITLE, PRINTER_PAPER_MAPPING, BROWSER_PRINT_PREFS, capabilities, LOGGING_ENABLED)
        logger.info("Printer capabilities successfully re-scanned and updated.")
        try:
            icon.notify("Printer capabilities list updated successfully!", "Auto-Printer")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to refresh printer list: {e}")

if __name__ == '__main__':
    menu = pystray.Menu(
        item(get_status_text, lambda: None),
        pystray.Menu.SEPARATOR,
        item('Change Target Title...', change_title),
        item('Map Printer Paper Size...', map_printer_size),
        item('Refresh Printer List', refresh_printer_list),
        item(get_logging_status_text, toggle_logging),
        item('Open Settings File', open_settings_file),
        pystray.Menu.SEPARATOR,
        item('Quit Auto-Printer', quit_app)
    )

    tray_icon = pystray.Icon("ChromiumAutoPrint", create_icon_image(), "Auto-Printer Active", menu)
    tray_icon.run(setup=setup)
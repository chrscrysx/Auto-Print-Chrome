import time
import threading
import subprocess
import os
import socket
import traceback
from datetime import datetime
import uiautomation as auto
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

# --- CONFIGURATION & FILE HANDLING ---
CONFIG_FILE = "settings.txt"
ERROR_LOG = "error_log.txt"

def log_error(err_msg):
    """Writes background errors to a text file for developer debugging."""
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {err_msg}\n")
    except Exception:
        pass

def load_target_title():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            content = f.read().strip()
            return content if content else "SIMC"
    else:
        with open(CONFIG_FILE, "w") as f:
            f.write("SIMC")
        return "SIMC"

def save_target_title(title):
    with open(CONFIG_FILE, "w") as f:
        f.write(title)

TARGET_WINDOW_TITLE = load_target_title()
running = True

# --- 1. SINGLE INSTANCE LOCK ---
try:
    instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    instance_socket.bind(("127.0.0.1", 44556)) 
except socket.error:
    print("⚠️ Auto-Printer is already running. Exiting to prevent duplicates.")
    os._exit(0)

def create_icon_image():
    image = Image.new('RGB', (64, 64), color=(255, 255, 255))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(0, 120, 215)) 
    dc.rectangle((24, 8, 40, 16), fill=(150, 150, 150)) 
    dc.rectangle((20, 48, 44, 60), fill=(200, 200, 200)) 
    return image

def monitor_print_dialog():
    global running
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 Auto-Printer started! Monitoring for: '{TARGET_WINDOW_TITLE}'")
    
    with auto.UIAutomationInitializerInThread():
        auto.SetGlobalSearchTimeout(0)
        
        while running:
            try:
                active_win = auto.GetForegroundControl()
                sleep_time = 1.0 
                
                if active_win:
                    win_name = ""
                    try:
                        win_name = active_win.Name.lower()
                    except Exception:
                        pass 
                        
                    is_target_match = (TARGET_WINDOW_TITLE == "*") or (TARGET_WINDOW_TITLE.lower() in win_name)
                    
                    try:
                        is_chrome = (active_win.ClassName == "Chrome_WidgetWin_1")
                    except Exception:
                        is_chrome = False

                    # If we are inside the target browser
                    if is_chrome and is_target_match:
                        sleep_time = 0.15 
                        
                        # FIX: Require BOTH Print and Cancel buttons to exist to avoid clicking the PDF toolbar
                        print_button = active_win.ButtonControl(searchDepth=15, Name="Print")
                        cancel_button = active_win.ButtonControl(searchDepth=15, Name="Cancel")
                        
                        # SCENARIO: The actual Print dialog appears on screen
                        if print_button.Exists(0, 0) and cancel_button.Exists(0, 0):
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🖨️ Print dialog detected! Triggering print...")
                            
                            try:
                                print_button.GetInvokePattern().Invoke()
                            except Exception:
                                try:
                                    print_button.SetFocus()
                                    print_button.SendKeys('{Space}') 
                                except Exception:
                                    pass
                                    
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Print triggered. Waiting for dialog to close...")
                            
                            # LOCKDOWN MODE: Track the ORIGINAL window. Break lock only when Cancel vanishes.
                            while running:
                                time.sleep(0.5)
                                try:
                                    if not active_win.ButtonControl(searchDepth=15, Name="Cancel").Exists(0, 0):
                                        break
                                except Exception:
                                    break # Window closed
                                
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔓 Dialog closed. System re-armed and ready for next print job!")
                            
            except Exception as e:
                log_error(traceback.format_exc())
                
            time.sleep(sleep_time)

def change_title(icon, item):
    global TARGET_WINDOW_TITLE
    
    ps_script = f"""
    Add-Type -AssemblyName Microsoft.VisualBasic
    $result = [Microsoft.VisualBasic.Interaction]::InputBox('Enter the window title to target.`n`n(Type * to target ALL Chromium browsers):', 'Change Auto-Printer Target', '{TARGET_WINDOW_TITLE}')
    Write-Output "DIALOG_RESULT:$result"
    """
    
    try:
        CREATE_NO_WINDOW = 0x08000000
        process = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )
        
        output = process.stdout.strip()
        for line in output.splitlines():
            if line.startswith("DIALOG_RESULT:"):
                new_title = line.split("DIALOG_RESULT:", 1)[1].strip()
                
                if new_title:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙️ Target changed to: '{new_title}'")
                    TARGET_WINDOW_TITLE = new_title
                    save_target_title(new_title) 
                    icon.update_menu()
                    break
    except Exception as e:
        log_error(f"Dialog Error: {str(e)}")

def quit_app(icon, item):
    global running
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Shutting down Auto-Printer...")
    running = False
    icon.stop()

def setup(icon):
    icon.visible = True
    monitor_thread = threading.Thread(target=monitor_print_dialog, daemon=True)
    monitor_thread.start()

def get_status_text(item):
    if TARGET_WINDOW_TITLE.strip() == "*":
        return 'Status: Targeting ALL Windows'
    return f'Status: Targeting "{TARGET_WINDOW_TITLE}"'

if __name__ == '__main__':
    menu = pystray.Menu(
        item(get_status_text, lambda: None),
        pystray.Menu.SEPARATOR,
        item('Change Target Title...', change_title),
        item('Quit Auto-Printer', quit_app)
    )

    tray_icon = pystray.Icon("ChromiumAutoPrint", create_icon_image(), "Auto-Printer Active", menu)
    tray_icon.run(setup=setup)
This operational guide outlines the deployment of an Akıllı Tahta Güvenlik ve Takip Sistemi (Smart Board Security & Tracking System) demo/MVP.
--------------------------------------------------------------------------------
1. Windows OS Hardening & Shell Replacement
Standard kiosk tricks (like maximizing windows) are easily bypassed on interactive touchscreens. Achieving true lock-down requires targeting the Windows Registry and the Winlogon sub-system.
Best Practices for Deep Lock-Down
Avoid Modifying Global Keys Directly: Do not alter keys affecting HKEY_LOCAL_MACHINE unless the smart board uses a dedicated local kiosk account. Instead, apply restrictions exclusively under HKEY_CURRENT_USER (HKCU) for the target standard user account.
The Shell Replacement Trick: By default, Windows runs explorer.exe to manage the taskbar, desktop icons, and Start Menu hotkeys. If you change the registry value HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\Shell to point directly to your compiled Python executable ("C:\path\to\your_app.exe"), Windows will boot directly into your program without loading the desktop shell environment. This disables basic shortcuts like Alt+F4 or the Windows Key automatically because the handler (explorer.exe) isn't running.
Taming Ctrl+Alt+Del: This sequence is intercepted directly by the Secure Attention Sequence (SAS) inside the Windows kernel (winlogon.exe). User-space software hooks (like keyboard or pynput in Python) cannot block it. To mitigate this without breaking administrative access:
Disable the Task Manager button inside the Ctrl+Alt+Del screen via the registry.
Turn on Fast User Switching Suppression so students cannot switch users to bypass the interface.
The Registry Hardening Payload
Save the following configuration as kiosk_setup.reg or execute it natively using an administrative terminal or Python's winreg library:
Windows Registry Editor Version 5.00

; 1. Disable Task Manager (Removes the option from Ctrl+Alt+Del screen)
[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Policies\System]
"DisableTaskMgr"=dword:00000001

; 2. Remove "Lock", "Change Password", and "Sign out" options from Ctrl+Alt+Del
[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer]
"NoLockScreen"=dword:00000001
"NoClose"=dword:00000001
"NoLogoff"=dword:00000001

; 3. Suppress Fast User Switching
[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System]
"HideFastUserSwitching"=dword:00000001

; 4. Custom Shell Replacement (Optional: Replace with your actual compiled path)
; [HKEY_CURRENT_USER\Software\Microsoft\Windows NT\CurrentVersion\Winlogon]
; "Shell"="C:\\AkilliTahta\\security_system.exe"

⚠️ Rollback Plan: Always keep an administrative account on the machine untouched by these modifications. If your application crashes while acting as the shell, you will be met with a black screen. To recover, press Ctrl+Alt+Del (if Task Manager wasn't disabled for the admin account), select File -> Run New Task, type regedit.exe, and revert the Shell string back to explorer.exe.
--------------------------------------------------------------------------------
2. PyInstaller Packaging & Code Signing ($200 Budget)
Code Signing with a $200 Budget
Traditional Extended Validation (EV) certificates cost upwards of 400–600 per year and require physical hardware tokens. With a $200 budget, you have two viable options to prevent Windows Defender SmartScreen blocks:
Azure Trusted Signing: Microsoft’s cloud-based signing service. It charges roughly $10/month for a basic developer certificate, fitting perfectly within your budget. It requires no physical USB keys and integrates directly with GitHub Actions or local command-line tools.
Standard Individual/OV Certificate via Resellers: Standard certificates (like those from Sectigo bought through resellers like SSL.com or CheapSSLShop) sometimes hover near $200/year, but they often require hardware token shipping or cloud HSM hosting, which can add unexpected hidden validation costs.
💡 Recommendation: Use Azure Trusted Signing. It is the most economical and modern path for indie developers and small teams to pass SmartScreen reputation checks without buying physical HSM hardware.
PyInstaller Production Execution
Do not rely on the simple pyinstaller --onefile main.py command for a production environment. Build errors can occur when dealing with complex, multi-dependency scientific libraries like face_recognition, dlib, OpenCV, or numpy.
Run the following structured build sequence to manage assets, bypass console flash artifacts, and inject metadata:
# 1. Install required production libraries
pip install pyinstaller opencv-python face_recognition

# 2. Run PyInstaller with explicitly isolated data flags
pyinstaller --clean \
            --onefile \
            --noconsole \
            --name="AkilliTahtaCore" \
            --add-data "models/*;models/" \
            main.py

The Ultimate Production .spec File Config
PyInstaller generates a .spec configuration file. Modify this file directly to enforce administrative execution rights (UAC Execution Level), ensuring your app has the necessary privileges to manage security features:
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('models/*', 'models')], # Include face landmarks / models explicitly
    hiddenimports=['cv2', 'face_recognition'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'sqlite3.test'], # Strip unneeded bulk
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AkilliTahtaCore',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True, # Compresses the binary to save footprint size
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # True for debugging, False to completely hide the cmd window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True, # CRITICAL: Prompts for Admin privileges automatically on launch
)

--------------------------------------------------------------------------------
3. Core Architecture Skeleton (Two-Window State Machine)
This single-file Python implementation handles the state transition between a locked kiosk screen and an administrative dashboard using Python's native thread-safe layout.
import tkinter as tk
from tkinter import ttk
import threading
import time

class KioskStateMachineApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw() # Hide root window to manage sub-stages cleanly
        self.current_window = None
        self.is_authenticated = False
        
        # Start directly in the Locked State
        self.transition_to_lock_screen()

    def clear_current_window(self):
        if self.current_window:
            self.current_window.destroy()
            self.current_window = None

    def transition_to_lock_screen(self):
        self.clear_current_window()
        self.is_authenticated = False
        
        # Create Lock Screen Toplevel Window
        self.current_window = tk.Toplevel()
        self.current_window.title("SİSTEM KİLİTLİ")
        
        # Apply strict kiosk window protocols
        self.apply_kiosk_protocols(self.current_window)
        
        # UI Layout
        frame = ttk.Frame(self.current_window, padding="30")
        frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        lbl = ttk.Label(frame, text="Akıllı Tahta Koruma Sistemi", font=("Helvetica", 24, "bold"), foreground="red")
        lbl.pack(pady=10)
        
        sub_lbl = ttk.Label(frame, text="Lütfen Kimlik Doğrulaması Yapın (Yüz / Kart)", font=("Helvetica", 14))
        sub_lbl.pack(pady=10)
        
        # Demo Bypass Button (Simulating a successful face match match event)
        btn_bypass = ttk.Button(frame, text="Simüle Et: Yüz Eşleşti (Sistemi Aç)", command=self.transition_to_dashboard)
        btn_bypass.pack(pady=20)

    def transition_to_dashboard(self):
        self.clear_current_window()
        self.is_authenticated = True
        
        # Create Dashboard Toplevel Window
        self.current_window = tk.Toplevel()
        self.current_window.title("Yönetim Paneli")
        
        # Restore basic desktop control parameters but keep full-screen if desired
        self.current_window.state('zoomed') # Maximized state
        self.current_window.protocol("WM_DELETE_WINDOW", self.transition_to_lock_screen) # Closing signs out
        
        frame = ttk.Frame(self.current_window, padding="30")
        frame.pack(fill=tk.BOTH, expand=True)
        
        lbl = ttk.Label(frame, text="Eğitmen Kontrol Paneli", font=("Helvetica", 20, "bold"), foreground="green")
        lbl.pack(pady=10)
        
        btn_lock = ttk.Button(frame, text="Oturumu Kapat ve Tahtayı Kilitle", command=self.transition_to_lock_screen)
        btn_lock.pack(pady=10)

    def apply_kiosk_protocols(self, window):
        """Implements Kiosk Security Checklist items to block common exit paths."""
        window.attributes("-fullscreen", True)
        window.attributes("-topmost", True)
        
        # Intercept window manager close commands
        window.protocol("WM_DELETE_WINDOW", lambda: None)
        
        # Catch and kill key escape combinations locally
        window.bind("<Alt-F4>", lambda e: "break")
        window.bind("<Control-Escape>", lambda e: "break")
        window.bind("<TkF10>", lambda e: "break")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = KioskStateMachineApp()
    app.run()

--------------------------------------------------------------------------------
4. Face Matrix Logic (Dynamic In-Memory Enrollment)
This module handles face identification. It matches webcam frames against an active in-memory cache and appends new student/teacher profiles dynamically.
import cv2
import face_recognition
import numpy as np

class FaceMatrixSystem:
    def __init__(self):
        # Known profiles store arrays mapping structural indices to clear metadata names
        self.known_face_encodings = []
        self.known_face_names = []

    def enroll_new_face(self, image_path, person_name):
        """Loads an external image asset, calculates its vector matrix, and appends to data pool."""
        try:
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            
            if len(encodings) > 0:
                target_encoding = encodings[0]
                self.known_face_encodings.append(target_encoding)
                self.known_face_names.append(person_name)
                print(f"Başarılı Kayıt: {person_name}")
                return True
            else:
                print(f"Hata: {person_name} için yüz algılanamadı.")
                return False
        except Exception as e:
            print(f"Kayıt Hatası: {str(e)}")
            return False

    def identify_frame(self, frame, tolerance=0.5):
        """Scans a raw target camera frame against the compiled matrix list.
        Lower tolerance values prevent false positives on twin similarities.
        """
        # Convert the image from BGR color (OpenCV default) to RGB color (face_recognition default)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Locate positions and build math models
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        
        found_identities = []
        
        for face_encoding in face_encodings:
            if not self.known_face_encodings:
                found_identities.append("Bilinmeyen Kullanıcı")
                continue
                
            # Check for matches across our signature index array
            matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=tolerance)
            name = "Bilinmeyen Kullanıcı"
            
            # Use the known face with the smallest distance to the new face
            face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
            best_match_index = np.argmin(face_distances)
            
            if matches[best_match_index]:
                name = self.known_face_names[best_match_index]
                
            found_identities.append(name)
            
        return face_locations, found_identities

--------------------------------------------------------------------------------
5. Kiosk Security Checklist (Tkinter Level)
To catch escape commands before they bubble up to the OS, enforce these rules on your Tkinter frames:
Vulnerability Path
Target Hotkey / Protocol
Tkinter Countermeasure Method
Window Frame Close
X Button click
window.protocol("WM_DELETE_WINDOW", lambda: None)
Default Close Signal
Alt + F4
window.bind("<Alt-F4>", lambda e: "break")
Start Menu / Overlay
Ctrl + Esc or Win Key
window.bind("<Control-Escape>", lambda e: "break")
Context Menu Blur
AppKey / Right Click
window.bind("<Button-3>", lambda e: "break")
Focus Loss Escape
Mouse click outside bounds
window.attributes("-fullscreen", True) + window.attributes("-topmost", True)
Note: In Tkinter binding, returning "break" stops the event chain immediately, preventing the parent window system or native OS wrapper from processing the keystroke locally.
--------------------------------------------------------------------------------
6. Active Window Tracker Script
This background monitor runs as an independent concurrent thread. It tracks active processes and feeds them into the user panel interface seamlessly without freezing the application UI.
import tkinter as tk
from tkinter import ttk
import threading
import time
import win32process
import win32gui
import psutil

class ActiveWindowTracker(threading.Thread):
    def __init__(self, callback_func):
        super().__init__()
        self.callback_func = callback_func
        self.daemon = True # Thread terminates automatically when the main loop closes
        self.is_running = True

    def get_active_window_info(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            process_name = process.name()
            window_title = win32gui.GetWindowText(hwnd)
            return process_name, window_title
        except Exception:
            return "Bilinmeyen", "Bilinmeyen Pencere"

    def run(self):
        last_app = None
        while self.is_running:
            app_name, title = self.get_active_window_info()
            if app_name and app_name != last_app and title != "":
                # Send the detected window name back to the UI thread via our callback function
                self.callback_func(app_name, title)
                last_app = app_name
            time.sleep(1.0) # Check every 1 second to minimize CPU usage

class DashboardWithTrackerView:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Sistem İzleme Modülü")
        self.root.geometry("600x400")
        
        lbl = ttk.Label(self.root, text="Aktif Uygulama Geçmişi (Canlı)", font=("Helvetica", 14, "bold"))
        lbl.pack(pady=10)
        
        # Build Table View Component
        self.tree = ttk.Treeview(self.root, columns=("Uygulama", "Pencere Başlığı"), show='headings')
        self.tree.heading("Uygulama", text="Çalışan Uygulama (.exe)")
        self.tree.heading("Pencere Başlığı", text="Pencere Başlığı")
        self.tree.column("Uygulama", width=150)
        self.tree.column("Pencere Başlığı", width=400)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Initialize background thread and link it to our UI updater function
        self.tracker_thread = ActiveWindowTracker(self.update_table_callback)
        self.tracker_thread.start()

    def update_table_callback(self, app_name, title):
        """Thread-safe injection of tracking telemetry into the Treeview frame."""
        # Use .after() to safely modify UI elements from a background thread
        self.root.after(0, lambda: self.tree.insert("", 0, values=(app_name, title)))

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    view = DashboardWithTrackerView()
    view.run()

--------------------------------------------------------------------------------
7. Step-by-Step Vibe-Coding Implementation Guide
If you are using tools like Cursor or Claude, use this structured sequence to build your MVP without running into dependency or environment conflicts.
Step 1: Environment Setup
Create a dedicated folder and set up a clean virtual environment to prevent package version mismatches:
mkdir AkilliTahtaMVP && cd AkilliTahtaMVP
python -m venv venv
venv\Scripts\activate
pip install opencv-python face_recognition pywin32 psutil

Step 2: Assemble Components with AI Assistance
Create your file structure:
AkilliTahtaMVP/
├── main.py              <-- Merge Section 3 & Section 6 here
├── vision.py            <-- Paste Section 4 here
└── models/              <-- Put your anchor face reference photos here (.jpg/.png)

When prompting your AI assistant (Cursor/Claude), use clear, isolated tasks to prevent code regression:
Prompt for AI: "Review main.py. I have integrated the KioskStateMachineApp and the ActiveWindowTracker. Modify the transition logic in transition_to_dashboard so that the window tracking thread launches automatically only after a user successfully logs in, and closes properly if the system re-locks."
Step 3: Local Dry-Run Debugging
Test the code locally before packaging or modifying any registry settings. Comment out window.attributes("-fullscreen", True) during initial development so you can easily close the application window if your escape hooks work too well and lock you out of your desktop.
Step 4: Compiling to an Executable
Once your code runs cleanly in Python, generate the production spec configuration file and compile it:
pyinstaller --clean --noconsole main.py

Open the generated main.spec file and verify that uac_admin=True and console=False are configured correctly under the EXE block, then run the final production build:
pyinstaller main.spec

--------------------------------------------------------------------------------
8. Source References & Access Verification
Microsoft Winlogon Architecture & Shell Customization: Documenting standard infrastructure patterns for custom workstation deployments.
URL: Microsoft Learn - Winlogon Shell Configuration (Accessed: June 2026)
Preventing Unauthorized System Access: Implementing registry modifications to disable Task Manager and manage user access privileges on shared workstations.
URL: Microsoft Q&A - Security Policies for Kiosk Deployments (Accessed: June 2026)
Azure Trusted Signing Pricing and Overview: Documentation on Microsoft's cloud-based code signing service for independent developers.
URL: Microsoft Learn - Trusted Signing Documentation (Accessed: June 2026)
PyInstaller Runtime & Advanced Customization Specifications: Managing multi-dependency packaging configurations, resource embedding, and administrative permission elevation.
URL: PyInstaller Official Feature Documentation (Accessed: June 2026)
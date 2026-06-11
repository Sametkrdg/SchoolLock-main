"""
TahtaKilit — Akıllı Tahta Güvenlik Uygulaması  |  Faz 1 + 2: Kiosk GUI + IPC İstemcisi
========================================================================================

Tasarım felsefesi: "Ciddi, Güvenli, Basit" (Serious, Secure, Simple)

İki durumlu state-machine:
  • LockScreen      — Koyu/Crimson, kamera çerçevesi, kimlik doğrulama bekleniyor
  • DashboardScreen — Emerald, "Oturumu Kapat" butonu, öğretim modu

Faz bağlantı noktaları (arama: "HOOK"):
  • IPC_HOOK        (Faz 2) — IPCClient watchdog pipe'ına bağlanır ✓ (bu dosyada)
  • BIOMETRIC_HOOK  (Faz 3) — biometric.py kamera karesi ve AUTH sinyalini buraya bağlar
"""

import threading
import time
import tkinter as tk
from tkinter import font as tkfont

# pywin32 yalnızca Windows'ta zorunlu; kurulu değilse IPC sessizce devre dışı kalır.
try:
    import pywintypes
    import win32con
    import win32file
    _IPC_AVAILABLE = True
except ImportError:
    _IPC_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
#  GELİŞTİRME MODU
#  Üretim binary'si derlemeden önce False yapın.
#
#  DEV_MODE = True iken:
#    • Tam ekran (fullscreen) devre dışı — masaüstünüzde kalmaz.
#    • Odak döngüsü (maintain_focus) devre dışı — pencere odak çalmaz.
#    • Shift+Escape ile programdan çıkabilirsiniz.
# ─────────────────────────────────────────────────────────────────────────────
DEV_MODE = True


# ─────────────────────────────────────────────────────────────────────────────
#  TASARIM TOKENLARI  —  Renkler, yazı tipleri, ölçüler tek yerde toplanır.
#  Hiçbir zaman bu sınıfı atlatarak ham değer kullanmayın.
# ─────────────────────────────────────────────────────────────────────────────
class Theme:
    # Arka planlar (koyu, yüksek kontrast)
    BG_DARK   = "#0C0C0F"   # Ana arka plan — neredeyse siyah
    BG_PANEL  = "#16161A"   # İç paneller / kart yüzeyleri
    BG_CAMERA = "#000000"   # Kamera çerçevesi içi (saf siyah)

    # Metin
    FG_PRIMARY = "#F4F4F5"  # Birincil — kırık beyaz
    FG_MUTED   = "#8A8A93"  # İkincil / yardımcı — gri

    # Kilit durumu vurgusu (Crimson)
    CRIMSON      = "#DC143C"
    CRIMSON_DEEP = "#8E0E27"

    # Doğrulama durumu vurgusu (Emerald)
    EMERALD      = "#0F9D58"
    EMERALD_DEEP = "#0A6E3D"

    # Yazı tipi
    FAMILY      = "Arial"
    SIZE_HERO   = 52   # Saat / ana başlık
    SIZE_TITLE  = 34   # Bölüm başlığı
    SIZE_STATUS = 28   # Durum mesajı
    SIZE_BODY   = 24   # Gövde (akıllı tahta için minimum erişilebilir boyut)
    SIZE_BUTTON = 30   # Buton etiketi

    # Boşluk / dolgu ölçeği
    PAD_XL = 48
    PAD_LG = 32
    PAD_MD = 20
    PAD_SM = 12


# ─────────────────────────────────────────────────────────────────────────────
#  ANA UYGULAMA KONTROLCÜSÜ
# ─────────────────────────────────────────────────────────────────────────────
class TahtaKilitApp(tk.Tk):
    """
    İki durumlu (kilit / panel) ana uygulama penceresi.

    Dış modüller yalnızca şu iki yöntemi çağırır:
        authenticate_success()  — biometric.py eşleşme onayladığında
        lock_system()           — öğretmen "Kilitle" butonuna bastığında
    """

    def __init__(self):
        super().__init__()

        self.title("TahtaKilit")
        self.configure(bg=Theme.BG_DARK)

        # ── TAM EKRAN ────────────────────────────────────────────────────────
        # Üretimde: self.attributes("-fullscreen", True)
        # Geliştirme sırasında pencere masaüstünüzü kilitlememesi için
        # aşağıdaki satır DEV_MODE kontrolüyle koşullandırılmıştır.
        if not DEV_MODE:
            self.attributes("-fullscreen", True)

        # ── KİOSK GÜVENLİK ÖNLEMLER ──────────────────────────────────────────
        self._apply_kiosk_security()

        # ── YAZITIPLERI (tüm ekranlarda paylaşılır) ───────────────────────────
        self._build_fonts()

        # ── EKRAN YERLEŞİMİ ──────────────────────────────────────────────────
        self.container = tk.Frame(self, bg=Theme.BG_DARK)
        self.container.pack(fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames: dict[str, tk.Frame] = {}
        for ScreenClass in (LockScreen, DashboardScreen):
            screen = ScreenClass(parent=self.container, controller=self)
            self.frames[ScreenClass.__name__] = screen
            screen.grid(row=0, column=0, sticky="nsew")

        # Her zaman KİLİTLİ durumda başla.
        self.show_screen("LockScreen")

        # ── IPC HOOK (Faz 2) ─────────────────────────────────────────────────
        # IPCClient arka planda bağlanır; bağlantı yoksa (watchdog çalışmıyor
        # ya da pywin32 kurulu değil) uygulama sessizce devam eder.
        self._ipc = IPCClient()
        self._ipc.start()
        # ─────────────────────────────────────────────────────────────────────

    # ── GÜVENLİK ÖNLEMLER ────────────────────────────────────────────────────
    def _apply_kiosk_security(self):
        """
        Tüm Tkinter bypass engellerini tek yerde toplar.
        Üretim kiosk modunu oluşturan güvenlik katmanı.
        """
        # Pencere her zaman en üstte kalır (görev çubuğu dahil).
        self.attributes("-topmost", True)

        # Pencere kapatma olayını (X butonu) engelle.
        self.protocol("WM_DELETE_WINDOW", self._block)

        # Klavye bypass vektörlerini engelle — tüm alt widget'lara uygulanır.
        self.bind_all("<Alt-F4>",          self._block)  # Varsayılan kapatma
        self.bind_all("<Control-Escape>",  self._block)  # Başlat menüsü
        self.bind_all("<Button-3>",        self._block)  # Sağ tık bağlam menüsü

        # Geliştirici çıkış kapısı — yalnızca DEV_MODE'da aktif.
        if DEV_MODE:
            self.bind_all("<Shift-Escape>", self._dev_exit)

        # Odak döngüsü: pencereyi her 100 ms'de bir öne getirir.
        # DEV_MODE'da devre dışı — diğer pencerelerde çalışmanızı engellemez.
        if not DEV_MODE:
            self._maintain_focus()

    @staticmethod
    def _block(event=None):
        """Tcl olay zincirini kırar; çıkış / bağlam menüsü tetiklenmez."""
        return "break"

    def _maintain_focus(self):
        """Odağı ve topmost önceliğini her 100 ms'de bir yeniden uygular."""
        self.focus_force()
        self.lift()
        self.after(100, self._maintain_focus)

    def _dev_exit(self, _event=None):
        """Geliştirici çıkışı (Shift+Escape) — yalnızca DEV_MODE=True iken aktif."""
        self.destroy()

    # ── YAZITIPLERI ───────────────────────────────────────────────────────────
    def _build_fonts(self):
        self.font_hero   = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_HERO,   weight="bold")
        self.font_title  = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_TITLE,  weight="bold")
        self.font_status = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_STATUS, weight="bold")
        self.font_body   = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_BODY,   weight="normal")
        self.font_button = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_BUTTON, weight="bold")

    # ── DURUM YÖNETİMİ ────────────────────────────────────────────────────────
    def show_screen(self, name: str):
        """İstenen ekranı öne getirir (kilit ↔ panel geçişi)."""
        self.frames[name].tkraise()

    def authenticate_success(self):
        """
        Başarılı kimlik doğrulama → Öğretim Kontrol Paneline geç.
        Faz 3'te biometric.py bu yöntemi çağırır.
        """
        self._ipc.send("AUTH_SUCCESS")
        self.show_screen("DashboardScreen")

    def lock_system(self):
        """Oturumu kapat → sistemi güvenli şekilde tekrar kilitle."""
        self._ipc.send("LOCK_COMMAND")
        self.show_screen("LockScreen")


# ─────────────────────────────────────────────────────────────────────────────
#  DURUM 1 — KİLİT EKRANI
# ─────────────────────────────────────────────────────────────────────────────
class LockScreen(tk.Frame):
    """
    Koyu, yüksek kontrastlı kilit ekranı.
    Crimson vurgular sistemik güvenliği belirtir.

    Dışarıya açık özellik:
        self.camera_label — biometric.py buraya ImageTk.PhotoImage yazar.
    """

    STATUS_TEXT = "SİSTEM KİLİTLİ - Lütfen Kimlik Doğrulaması Yapın"

    def __init__(self, parent, controller: TahtaKilitApp):
        super().__init__(parent, bg=Theme.BG_DARK)
        self.controller = controller

        # Üstte ince Crimson güvenlik şeridi.
        tk.Frame(self, bg=Theme.CRIMSON, height=8).pack(fill="x", side="top")

        # Tüm içeriği dikeyde ortalayan gövde.
        body = tk.Frame(self, bg=Theme.BG_DARK)
        body.pack(fill="both", expand=True, padx=Theme.PAD_XL, pady=Theme.PAD_XL)

        # Kilit simgesi
        tk.Label(
            body,
            text="🔒",
            font=tkfont.Font(family=Theme.FAMILY, size=64),
            bg=Theme.BG_DARK,
            fg=Theme.CRIMSON,
        ).pack(pady=(Theme.PAD_LG, Theme.PAD_SM))

        # Durum mesajı — tek, net, büyük
        tk.Label(
            body,
            text=self.STATUS_TEXT,
            font=controller.font_status,
            bg=Theme.BG_DARK,
            fg=Theme.FG_PRIMARY,
            wraplength=1100,
            justify="center",
        ).pack(pady=(0, Theme.PAD_LG))

        # ── KAMERA ÇERÇEVESİ ─────────────────────────────────────────────────
        # Crimson kenarlı, içi saf siyah, sabit 720×480.
        # BIOMETRIC HOOK (Faz 3): biometric.py bu çerçeveye kamera karesi yazar
        # ve eşleşme onaylandığında controller.authenticate_success() çağırır.
        # TEMP-AUTH-HOOK click binding'i o aşamada kaldırılacak.
        camera_outer = tk.Frame(body, bg=Theme.CRIMSON, highlightthickness=0, bd=0)
        camera_outer.pack(pady=Theme.PAD_MD)

        # camera_label: biometric.py tarafından her karede güncellenen widget.
        self.camera_label = tk.Label(
            camera_outer,
            bg=Theme.BG_CAMERA,
            width=720,
            height=480,
            cursor="hand2",
        )
        self.camera_label.pack(padx=4, pady=4)

        # Gerçek akış gelince kaldırılacak yer tutucu metin.
        placeholder = tk.Label(
            self.camera_label,
            text="KAMERA GÖRÜNTÜSÜ\n\n[ OpenCV akışı buraya yerleştirilecek ]",
            font=controller.font_body,
            bg=Theme.BG_CAMERA,
            fg=Theme.FG_MUTED,
            justify="center",
        )
        placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # TEMP-AUTH-HOOK: kamera alanına tıklamak doğrulamayı simüle eder.
        # Faz 3'te biometric.py bağlantısıyla bu satırlar kaldırılacak.
        for widget in (self.camera_label, placeholder):
            widget.bind("<Button-1>", lambda _e: controller.authenticate_success())

        # Alt yardımcı metin
        tk.Label(
            body,
            text="Yüzünüzü kameraya hizalayın",
            font=controller.font_body,
            bg=Theme.BG_DARK,
            fg=Theme.FG_MUTED,
        ).pack(pady=(Theme.PAD_MD, 0))


# ─────────────────────────────────────────────────────────────────────────────
#  DURUM 2 — ÖĞRETİM KONTROL PANELİ (DASHBOARD)
# ─────────────────────────────────────────────────────────────────────────────
class DashboardScreen(tk.Frame):
    """
    Doğrulama başarılı olduğunda gösterilen panel.
    Emerald vurgular güvenli/aktif durumu belirtir.
    """

    def __init__(self, parent, controller: TahtaKilitApp):
        super().__init__(parent, bg=Theme.BG_DARK)
        self.controller = controller

        # Üstte ince Emerald güvenli-durum şeridi.
        tk.Frame(self, bg=Theme.EMERALD, height=8).pack(fill="x", side="top")

        body = tk.Frame(self, bg=Theme.BG_DARK)
        body.pack(fill="both", expand=True, padx=Theme.PAD_XL, pady=Theme.PAD_XL)

        # Güvenli durum simgesi
        tk.Label(
            body,
            text="✓",
            font=tkfont.Font(family=Theme.FAMILY, size=72, weight="bold"),
            bg=Theme.BG_DARK,
            fg=Theme.EMERALD,
        ).pack(pady=(Theme.PAD_LG, Theme.PAD_SM))

        # Başlık
        tk.Label(
            body,
            text="SİSTEM AÇIK — Oturum Aktif",
            font=controller.font_title,
            bg=Theme.BG_DARK,
            fg=Theme.FG_PRIMARY,
        ).pack(pady=(0, Theme.PAD_SM))

        tk.Label(
            body,
            text="Kimlik doğrulaması başarılı. Tahta kullanıma hazır.",
            font=controller.font_body,
            bg=Theme.BG_DARK,
            fg=Theme.FG_MUTED,
        ).pack(pady=(0, Theme.PAD_XL))

        # Butonu sayfanın altına iten esnek boşluk.
        tk.Frame(body, bg=Theme.BG_DARK).pack(fill="both", expand=True)

        # "Oturumu Kapat" — büyük dokunma hedefi, Crimson renk (tehlike sinyali).
        tk.Button(
            body,
            text="Oturumu Kapat ve Tahtayı Kilitle",
            font=controller.font_button,
            bg=Theme.CRIMSON,
            fg=Theme.FG_PRIMARY,
            activebackground=Theme.CRIMSON_DEEP,
            activeforeground=Theme.FG_PRIMARY,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=Theme.PAD_XL,
            pady=Theme.PAD_LG,
            command=controller.lock_system,
        ).pack(pady=(0, Theme.PAD_LG), ipadx=Theme.PAD_LG, ipady=Theme.PAD_MD)


# ─────────────────────────────────────────────────────────────────────────────
#  IPC İSTEMCİSİ  —  Watchdog Named Pipe'ına bağlanır
# ─────────────────────────────────────────────────────────────────────────────

class IPCClient:
    """
    Watchdog Named Pipe sunucusuna bağlanan hafif istemci.

    Tasarım kararları:
      • Tüm ağ/pipe işlemleri daemon thread'de çalışır — Tkinter ana döngüsünü
        hiçbir zaman bloklamaz.
      • Watchdog çalışmıyorsa (geliştirme ortamı, DEV_MODE) bağlantı sessizce
        başarısız olur; uygulama tek başına çalışmaya devam eder.
      • send() thread-safe; authenticate_success() ve lock_system() doğrudan çağırır.
    """

    PIPE_NAME          = r"\\.\pipe\TahtaKilitIPC"
    HEARTBEAT_INTERVAL = 2.0    # saniye
    CONNECT_RETRIES    = 5
    RETRY_DELAY_BASE   = 0.5    # üstel geri çekilme için taban (saniye)

    def __init__(self) -> None:
        self._handle   = None
        self._lock     = threading.Lock()
        self._running  = True

    def start(self) -> None:
        """Bağlantı + kalp atışı döngüsünü arka planda başlatır."""
        if not _IPC_AVAILABLE:
            # pywin32 kurulu değil — IPC devre dışı, uygulama yine çalışır.
            return
        t = threading.Thread(
            target=self._connect_and_heartbeat,
            daemon=True,
            name="IPCClient",
        )
        t.start()

    def send(self, message: str) -> bool:
        """
        Watchdog'a bir mesaj gönderir.
        Bağlı değilse ya da gönderim başarısız olursa False döner —
        çağıran taraf bu durumu ele almak zorunda değildir.
        """
        if not _IPC_AVAILABLE:
            return False
        with self._lock:
            if self._handle is None:
                return False
            try:
                win32file.WriteFile(self._handle, message.encode("utf-8"))
                return True
            except pywintypes.error:
                # Pipe koptu — bağlantıyı temizle; _connect_and_heartbeat yeniden dener.
                self._close_handle()
                return False

    # ── İÇ YARDIMCILAR ────────────────────────────────────────────────────────

    def _connect_and_heartbeat(self) -> None:
        """
        Pipe'a bağlanmayı dener (üstel geri çekilmeyle) ve sonra
        kalp atışı döngüsüne girer. Pipe koparsa yeniden bağlanmayı dener.
        """
        while self._running:
            if self._handle is None:
                if not self._try_connect():
                    # Tüm denemeler tükendi — watchdog çalışmıyor olabilir.
                    # Bir süre bekle, sonra tekrar dene (watchdog sonradan başlayabilir).
                    time.sleep(self.RETRY_DELAY_BASE * self.CONNECT_RETRIES)
                    continue

            # Pipe bağlı: kalp atışı gönder.
            if not self.send("HEARTBEAT"):
                # send() bağlantıyı kapattı; döngünün başına dön ve yeniden bağlan.
                continue

            time.sleep(self.HEARTBEAT_INTERVAL)

    def _try_connect(self) -> bool:
        """
        Pipe'a bağlanmayı CONNECT_RETRIES kez dener.
        Başarılıysa True, tüm denemeler tükendiyse False döner.
        """
        for attempt in range(self.CONNECT_RETRIES):
            try:
                handle = win32file.CreateFile(
                    self.PIPE_NAME,
                    win32con.GENERIC_WRITE,   # Yalnızca GUI→Watchdog yönünde
                    0,                        # Paylaşım yok
                    None,                     # Güvenlik tanımlayıcısı
                    win32con.OPEN_EXISTING,
                    0,
                    None,
                )
                with self._lock:
                    self._handle = handle
                return True

            except pywintypes.error as exc:
                # 2   = ERROR_FILE_NOT_FOUND  (pipe henüz oluşturulmadı)
                # 231 = ERROR_PIPE_BUSY       (önceki istemci hâlâ bağlı)
                delay = self.RETRY_DELAY_BASE * (attempt + 1)
                if exc.winerror in (2, 231):
                    time.sleep(delay)
                else:
                    # Beklenmedik hata — daha uzun bekle.
                    time.sleep(delay * 2)

        return False

    def _close_handle(self) -> None:
        """Pipe handle'ını güvenli şekilde kapatır (kilit altında)."""
        if self._handle is not None:
            try:
                win32file.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


if __name__ == "__main__":
    app = TahtaKilitApp()
    app.mainloop()

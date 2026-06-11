"""
TahtaKilit — Akıllı Tahta Güvenlik Uygulaması  |  Faz 1: Kiosk Arayüzü
=======================================================================

Tasarım felsefesi: "Ciddi, Güvenli, Basit" (Serious, Secure, Simple)

İki durumlu state-machine:
  • LockScreen      — Koyu/Crimson, kamera çerçevesi, kimlik doğrulama bekleniyor
  • DashboardScreen — Emerald, "Oturumu Kapat" butonu, öğretim modu

Gelecek fazlara bağlantı noktaları (arama: "HOOK"):
  • BIOMETRIC_HOOK  (Faz 3) — biometric.py, kamera karesi ve AUTH sinyalini buraya bağlar
  • IPC_HOOK        (Faz 2) — watchdog.py Named Pipe istemcisi buraya eklenir
"""

import tkinter as tk
from tkinter import font as tkfont


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

        # ── IPC HOOK (Faz 2) ──────────────────────────────────────────────────
        # Watchdog Named Pipe istemcisini buraya ekleyin.
        # Bağlantı kurulduktan sonra aşağıdaki after() döngüsünü başlatın:
        #
        #   self.after(5000, self._send_heartbeat)
        #
        # def _send_heartbeat(self):
        #     ipc.send("HEARTBEAT")
        #     self.after(5000, self._send_heartbeat)
        #
        # authenticate_success() içinden ipc.send("AUTH_SUCCESS") gönderin.
        # lock_system() içinden    ipc.send("LOCK_COMMAND")    gönderin.
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
        Faz 2'de buraya ipc.send("AUTH_SUCCESS") eklenecek.
        """
        self.show_screen("DashboardScreen")

    def lock_system(self):
        """
        Oturumu kapat → sistemi güvenli şekilde tekrar kilitle.

        Faz 2'de buraya ipc.send("LOCK_COMMAND") eklenecek.
        """
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


if __name__ == "__main__":
    app = TahtaKilitApp()
    app.mainloop()

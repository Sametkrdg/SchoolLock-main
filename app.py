"""
TahtaKilit — Akıllı Tahta Güvenlik Uygulaması
==============================================

Tasarım Felsefesi: "Ciddi, Güvenli, Basit" (Serious, Secure, Simple)

Bu modül, akıllı tahta (interaktif dokunmatik cam panel) için iki durumlu
güvenlik arayüzünü uygular:

  • Durum 1 — KİLİT EKRANI (Lock Screen)
        Koyu, yüksek kontrastlı arka plan + Crimson (koyu kırmızı) vurgular.
        Ortada OpenCV kamera akışının yerleştirileceği büyük boş çerçeve.

  • Durum 2 — ÖĞRETİM KONTROL PANELİ (Dashboard)
        Başarılı doğrulamayı ve güvenliği belirten Deep Emerald (zümrüt yeşili)
        vurgular + büyük "Oturumu Kapat ve Tahtayı Kilitle" butonu.

Dokunmatik erişilebilirlik için tüm kontroller büyük (geniş dokunma hedefleri),
büyük yazı tipleri (Arial 24+) ve bol iç boşluk (padding) ile tasarlanmıştır.

NOT: Biyometrik kimlik doğrulama (OpenCV) henüz uygulanmadığından, kilit
ekranındaki kamera çerçevesine dokunmak GEÇİCİ olarak başarılı doğrulamayı
simüle eder ve panele geçer. Bu kanca (hook) Faz 2'de gerçek doğrulama ile
değiştirilecektir — ilgili satır `# TEMP-AUTH-HOOK` etiketi ile işaretlidir.
"""

import tkinter as tk
from tkinter import font as tkfont


# ---------------------------------------------------------------------------
# TASARIM TOKENLARI  —  Renkler, yazı tipleri, ölçüler tek yerde toplanır.
# ---------------------------------------------------------------------------
class Theme:
    # --- Arka planlar (koyu, yüksek kontrast) ---
    BG_DARK        = "#0C0C0F"   # Ana arka plan — neredeyse siyah
    BG_PANEL       = "#16161A"   # İç paneller / kart yüzeyleri
    BG_CAMERA      = "#000000"   # Kamera çerçevesi içi (saf siyah)

    # --- Metin ---
    FG_PRIMARY     = "#F4F4F5"   # Birincil metin — kırık beyaz
    FG_MUTED       = "#8A8A93"   # İkincil / yardımcı metin — gri

    # --- Vurgu renkleri ---
    CRIMSON        = "#DC143C"   # Kilit / güvenlik vurgusu (kırmızı)
    CRIMSON_DEEP   = "#8E0E27"   # Koyu kırmızı (gölge / pasif kenar)
    EMERALD        = "#0F9D58"   # Başarılı doğrulama / güvenli durum (yeşil)
    EMERALD_DEEP   = "#0A6E3D"   # Koyu zümrüt (buton basılı / kenar)

    # --- Yazı tipi aileleri ---
    FAMILY         = "Arial"

    # --- Yazı tipi ölçüleri (akıllı tahta için büyük) ---
    SIZE_HERO      = 52          # Çok büyük başlık (saat / durum)
    SIZE_TITLE     = 34          # Bölüm başlığı
    SIZE_STATUS    = 28          # Durum mesajı
    SIZE_BODY      = 24          # Gövde metni (minimum erişilebilir boyut)
    SIZE_BUTTON    = 30          # Buton etiketi

    # --- Boşluk / dolgu ölçeği ---
    PAD_XL         = 48
    PAD_LG         = 32
    PAD_MD         = 20
    PAD_SM         = 12


class TahtaKilitApp(tk.Tk):
    """İki durumlu (kilit / panel) ana uygulama penceresi."""

    def __init__(self):
        super().__init__()

        self.title("TahtaKilit")
        self.configure(bg=Theme.BG_DARK)

        # --- Tam ekran kiosk modu (akıllı tahta için) ---
        self.attributes("-fullscreen", True)
        # Geliştirme kolaylığı: ESC tuşu tam ekrandan çıkar.
        self.bind("<Escape>", self._exit_fullscreen)

        # Yazı tiplerini bir kez oluştur (tüm ekranlarda paylaşılır).
        self._build_fonts()

        # Ekranların yerleşeceği kök kapsayıcı.
        self.container = tk.Frame(self, bg=Theme.BG_DARK)
        self.container.pack(fill="both", expand=True)

        # İki durumu da hazırla, üst üste yerleştir, gerekeni öne getir.
        self.frames = {}
        for ScreenClass in (LockScreen, DashboardScreen):
            screen = ScreenClass(parent=self.container, controller=self)
            self.frames[ScreenClass.__name__] = screen
            screen.grid(row=0, column=0, sticky="nsew")

        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        # Uygulama her zaman KİLİTLİ durumda başlar.
        self.show_screen("LockScreen")

    # ------------------------------------------------------------------ fonts
    def _build_fonts(self):
        self.font_hero   = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_HERO,   weight="bold")
        self.font_title  = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_TITLE,  weight="bold")
        self.font_status = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_STATUS, weight="bold")
        self.font_body   = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_BODY,   weight="normal")
        self.font_button = tkfont.Font(family=Theme.FAMILY, size=Theme.SIZE_BUTTON, weight="bold")

    # ---------------------------------------------------------------- state nav
    def show_screen(self, name: str):
        """İstenen durumu öne getirir (kilit <-> panel geçişi)."""
        self.frames[name].tkraise()

    def authenticate_success(self):
        """Başarılı kimlik doğrulama → Öğretim Kontrol Paneline geç."""
        self.show_screen("DashboardScreen")

    def lock_system(self):
        """Oturumu kapat → sistemi güvenli şekilde tekrar kilitle."""
        self.show_screen("LockScreen")

    def _exit_fullscreen(self, _event=None):
        self.attributes("-fullscreen", False)


# ===========================================================================
#  DURUM 1 — KİLİT EKRANI
# ===========================================================================
class LockScreen(tk.Frame):
    """
    Koyu, yüksek kontrastlı kilit ekranı.
    Crimson (kırmızı) vurgular sistemik güvenliği belirtir.
    Ortada OpenCV kamera akışı için büyük boş çerçeve bulunur.
    """

    STATUS_TEXT = "SİSTEM KİLİTLİ - Lütfen Kimlik Doğrulaması Yapın"

    def __init__(self, parent, controller: TahtaKilitApp):
        super().__init__(parent, bg=Theme.BG_DARK)
        self.controller = controller

        # Üstte ince crimson güvenlik şeridi — sistemik kilit göstergesi.
        accent_bar = tk.Frame(self, bg=Theme.CRIMSON, height=8)
        accent_bar.pack(fill="x", side="top")

        # Tüm içeriği dikeyde ortalayan iç gövde.
        body = tk.Frame(self, bg=Theme.BG_DARK)
        body.pack(fill="both", expand=True, padx=Theme.PAD_XL, pady=Theme.PAD_XL)

        # --- Kilit simgesi (basit, metin tabanlı — özel SVG yok) ---
        lock_glyph = tk.Label(
            body,
            text="🔒",
            font=tkfont.Font(family=Theme.FAMILY, size=64),
            bg=Theme.BG_DARK,
            fg=Theme.CRIMSON,
        )
        lock_glyph.pack(pady=(Theme.PAD_LG, Theme.PAD_SM))

        # --- Büyük, net durum mesajı ---
        status = tk.Label(
            body,
            text=self.STATUS_TEXT,
            font=controller.font_status,
            bg=Theme.BG_DARK,
            fg=Theme.FG_PRIMARY,
            wraplength=1100,
            justify="center",
        )
        status.pack(pady=(0, Theme.PAD_LG))

        # --- KAMERA ÇERÇEVESİ (OpenCV yer tutucusu) ---
        # Crimson kenarlı, içi saf siyah, açıkça tanımlanmış büyük alan.
        camera_outer = tk.Frame(
            body,
            bg=Theme.CRIMSON,            # kenar rengi
            highlightthickness=0,
            bd=0,
        )
        camera_outer.pack(pady=Theme.PAD_MD)

        # 4 px crimson kenar etkisi için iç çerçeveyi padding ile yerleştir.
        self.camera_frame = tk.Frame(
            camera_outer,
            bg=Theme.BG_CAMERA,
            width=720,
            height=480,
            cursor="hand2",
        )
        self.camera_frame.pack(padx=4, pady=4)
        self.camera_frame.pack_propagate(False)  # sabit boyutu koru

        # Çerçeve içi yer tutucu metin (gerçek akış gelince kaldırılacak).
        cam_hint = tk.Label(
            self.camera_frame,
            text="KAMERA GÖRÜNTÜSÜ\n\n[ OpenCV akışı buraya yerleştirilecek ]",
            font=controller.font_body,
            bg=Theme.BG_CAMERA,
            fg=Theme.FG_MUTED,
            justify="center",
        )
        cam_hint.place(relx=0.5, rely=0.5, anchor="center")

        # GEÇİCİ: kamera alanına dokunmak başarılı doğrulamayı simüle eder.
        # Faz 2'de gerçek biyometrik sonuç ile değiştirilecek. # TEMP-AUTH-HOOK
        for widget in (self.camera_frame, cam_hint):
            widget.bind("<Button-1>", lambda _e: controller.authenticate_success())

        # --- Alt yardımcı metin ---
        hint = tk.Label(
            body,
            text="Yüzünüzü kameraya hizalayın",
            font=controller.font_body,
            bg=Theme.BG_DARK,
            fg=Theme.FG_MUTED,
        )
        hint.pack(pady=(Theme.PAD_MD, 0))


# ===========================================================================
#  DURUM 2 — ÖĞRETİM KONTROL PANELİ (DASHBOARD)
# ===========================================================================
class DashboardScreen(tk.Frame):
    """
    Doğrulama başarılı olduğunda gösterilen panel.
    Deep Emerald (zümrüt yeşili) vurgular güvenli/aktif durumu belirtir.
    Büyük "Oturumu Kapat ve Tahtayı Kilitle" butonu kilit durumuna döner.
    """

    def __init__(self, parent, controller: TahtaKilitApp):
        super().__init__(parent, bg=Theme.BG_DARK)
        self.controller = controller

        # Üstte ince emerald güvenli-durum şeridi.
        accent_bar = tk.Frame(self, bg=Theme.EMERALD, height=8)
        accent_bar.pack(fill="x", side="top")

        body = tk.Frame(self, bg=Theme.BG_DARK)
        body.pack(fill="both", expand=True, padx=Theme.PAD_XL, pady=Theme.PAD_XL)

        # --- Güvenli durum simgesi ---
        check_glyph = tk.Label(
            body,
            text="✓",
            font=tkfont.Font(family=Theme.FAMILY, size=72, weight="bold"),
            bg=Theme.BG_DARK,
            fg=Theme.EMERALD,
        )
        check_glyph.pack(pady=(Theme.PAD_LG, Theme.PAD_SM))

        # --- Aktif oturum başlığı ---
        title = tk.Label(
            body,
            text="SİSTEM AÇIK — Oturum Aktif",
            font=controller.font_title,
            bg=Theme.BG_DARK,
            fg=Theme.FG_PRIMARY,
        )
        title.pack(pady=(0, Theme.PAD_SM))

        subtitle = tk.Label(
            body,
            text="Kimlik doğrulaması başarılı. Tahta kullanıma hazır.",
            font=controller.font_body,
            bg=Theme.BG_DARK,
            fg=Theme.FG_MUTED,
        )
        subtitle.pack(pady=(0, Theme.PAD_XL))

        # Butonu dikeyde aşağı itip ortalamak için esnek boşluk.
        spacer = tk.Frame(body, bg=Theme.BG_DARK)
        spacer.pack(fill="both", expand=True)

        # --- BÜYÜK: Oturumu Kapat ve Tahtayı Kilitle ---
        lock_button = tk.Button(
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
        )
        # ipadx/ipady ile dokunma hedefini iyice büyüt.
        lock_button.pack(pady=(0, Theme.PAD_LG), ipadx=Theme.PAD_LG, ipady=Theme.PAD_MD)


if __name__ == "__main__":
    app = TahtaKilitApp()
    app.mainloop()

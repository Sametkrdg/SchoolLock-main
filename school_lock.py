# -*- coding: utf-8 -*-
"""
SchoolLock - Akıllı Tahta Kilit Ekranı (MVP / Demo)
---------------------------------------------------
Senaryo:
  1. Uygulama açılınca tam ekran (kiosk) kilit ekranı gelir.
  2. Kamera canlı görüntü verir, öğretmenin yüzünü "ogretmen.jpg" ile karşılaştırır.
  3. Yüz tanınınca ekran yeşile döner, 2 sn sonra kilit kapanır -> Windows masaüstü.
  4. Arka planda her 5 saniyede aktif pencere başlığı "kullanim_log.txt" dosyasına işlenir.

Güvenlik notu (DEMO): Takılı kalmamak için gizli çıkış kısayolu => CTRL + SHIFT + Q
"""

import os
import time
import threading
import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk

# Windows aktif pencere başlığı için
import win32gui


# ============================================================
#  AYARLAR
# ============================================================
OGRETMEN_RESMI = "ogretmen.jpg"      # Öğretmenin referans fotoğrafı (script ile aynı klasörde)
LOG_DOSYASI = "kullanim_log.txt"     # Uygulama takip log dosyası
KAMERA_INDEX = 0                     # Varsayılan kamera (0). Harici kamera için 1 deneyin.
TANIMA_ESIGI = 70                   # LBPH güven değeri (DÜŞÜK = daha benzer). 70-90 arası ayarlayın.
GEREKLI_ESLESME = 5                  # Üst üste kaç karede tanınırsa giriş onaylanır (yanlış pozitifi azaltır)
LOG_ARALIGI_SN = 5                   # Kaç saniyede bir aktif pencere kaydedilsin


# ============================================================
#  ARKA PLAN: UYGULAMA TAKİP (LOGGING)
# ============================================================
def aktif_pencere_basligi():
    """O an Windows'ta önde (foreground) olan pencerenin başlığını döndürür."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd).strip()
    except Exception:
        return ""


def log_dongusu():
    """
    Her LOG_ARALIGI_SN saniyede bir aktif pencereyi kontrol eder,
    her uygulamanın toplam kaç saniye açık/önplanda kaldığını biriktirir
    ve kullanim_log.txt dosyasına yazar. Bu fonksiyon arka plan thread'inde sonsuz döner.
    """
    kullanim = {}  # {pencere_basligi: toplam_saniye}

    while True:
        baslik = aktif_pencere_basligi()
        if baslik:  # boş başlıkları atla
            kullanim[baslik] = kullanim.get(baslik, 0) + LOG_ARALIGI_SN

            # Log dosyasını her döngüde güncel haliyle yeniden yaz
            try:
                with open(LOG_DOSYASI, "w", encoding="utf-8") as f:
                    f.write("=== SCHOOLLOCK UYGULAMA KULLANIM RAPORU ===\n")
                    f.write("Son guncelleme: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    f.write("-" * 45 + "\n")
                    # En çok kullanılandan aza doğru sırala
                    for ad, sn in sorted(kullanim.items(), key=lambda x: x[1], reverse=True):
                        dakika, saniye = divmod(sn, 60)
                        f.write(f"{ad}  ->  {dakika} dk {saniye} sn\n")
            except Exception as e:
                print("Log yazma hatasi:", e)

        time.sleep(LOG_ARALIGI_SN)


def logging_baslat():
    """Takip döngüsünü daemon thread olarak başlatır (program kapanınca otomatik biter)."""
    t = threading.Thread(target=log_dongusu, daemon=True)
    t.start()


# ============================================================
#  KİLİT EKRANI + YÜZ TANIMA
# ============================================================
class KilitEkrani:
    def __init__(self, root):
        self.root = root
        self.eslesme_sayaci = 0
        self.giris_yapildi = False

        # ---- Tam ekran / Kiosk ayarları ----
        self.root.title("SchoolLock")
        self.root.attributes("-fullscreen", True)   # Tam ekran (görev çubuğunu da kaplar)
        self.root.attributes("-topmost", True)       # Her zaman en üstte
        self.root.configure(bg="#1c1c2b")

        # ---- Çıkış / kapatma engelleri ----
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)  # Pencere X butonunu engelle
        self.root.bind("<Escape>", lambda e: "break")          # ESC engellendi
        self.root.bind("<Alt-F4>", lambda e: "break")          # Alt+F4 engellendi
        # Gizli güvenlik çıkışı (demo'da takılı kalmamak için):
        self.root.bind("<Control-Shift-Q>", lambda e: self.guvenli_cikis())

        # ---- Arayüz ----
        self.baslik = tk.Label(
            root, text="🔒 SİSTEM KİLİTLİ",
            font=("Segoe UI", 48, "bold"), fg="white", bg="#1c1c2b"
        )
        self.baslik.pack(pady=(80, 10))

        self.alt_yazi = tk.Label(
            root, text="Öğretmen Yüz Taraması Bekleniyor...",
            font=("Segoe UI", 22), fg="#a0a0c0", bg="#1c1c2b"
        )
        self.alt_yazi.pack(pady=10)

        # Kamera görüntüsü için küçük pencere
        self.kamera_label = tk.Label(root, bg="black")
        self.kamera_label.pack(pady=30)

        self.durum = tk.Label(
            root, text="Kamera başlatılıyor...",
            font=("Segoe UI", 16), fg="#a0a0c0", bg="#1c1c2b"
        )
        self.durum.pack(pady=10)

        # ---- Yüz tanıma motorunu hazırla ----
        self.yuz_dedektoru = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self.taniyici = cv2.face.LBPHFaceRecognizer_create()
        self.model_hazir = self.modeli_egit()

        # ---- Kamerayı aç ----
        self.cap = cv2.VideoCapture(KAMERA_INDEX, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.durum.config(text="HATA: Kamera açılamadı!", fg="#ff5555")
        else:
            self.kareyi_guncelle()  # Canlı döngüyü başlat

    def modeli_egit(self):
        """ogretmen.jpg üzerinden yüz tanıma modelini eğitir (tek örnekle)."""
        if not os.path.exists(OGRETMEN_RESMI):
            self.durum.config(text=f"HATA: '{OGRETMEN_RESMI}' bulunamadı!", fg="#ff5555")
            return False

        gri = cv2.imread(OGRETMEN_RESMI, cv2.IMREAD_GRAYSCALE)
        if gri is None:
            self.durum.config(text="HATA: Referans resmi okunamadı!", fg="#ff5555")
            return False

        yuzler = self.yuz_dedektoru.detectMultiScale(gri, 1.1, 5)
        if len(yuzler) == 0:
            self.durum.config(text="HATA: Referans resimde yüz bulunamadı!", fg="#ff5555")
            return False

        x, y, w, h = yuzler[0]
        yuz = gri[y:y + h, x:x + w]
        # Tek etiket (0) ile eğit
        self.taniyici.train([yuz], np.array([0]))
        return True

    def kareyi_guncelle(self):
        """Her karede: kamerayı oku, ekrana bas, yüzü tanımaya çalış."""
        if self.giris_yapildi:
            return

        ret, frame = self.cap.read()
        if ret:
            frame = cv2.flip(frame, 1)  # Ayna görüntüsü (daha doğal)
            gri = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            yuzler = self.yuz_dedektoru.detectMultiScale(gri, 1.1, 5)

            tanindi = False
            for (x, y, w, h) in yuzler:
                renk = (0, 0, 255)  # kırmızı (tanınmadı)
                if self.model_hazir:
                    etiket, guven = self.taniyici.predict(gri[y:y + h, x:x + w])
                    if etiket == 0 and guven < TANIMA_ESIGI:
                        tanindi = True
                        renk = (0, 255, 0)  # yeşil (tanındı)
                cv2.rectangle(frame, (x, y), (x + w, y + h), renk, 2)

            # Üst üste eşleşme say
            if tanindi:
                self.eslesme_sayaci += 1
                self.durum.config(text=f"Yüz analiz ediliyor... ({self.eslesme_sayaci}/{GEREKLI_ESLESME})",
                                  fg="#55ff55")
            else:
                self.eslesme_sayaci = 0
                if self.model_hazir:
                    self.durum.config(text="Lütfen kameraya bakın", fg="#a0a0c0")

            # Görüntüyü Tkinter'a aktar (küçültülmüş)
            kucuk = cv2.resize(frame, (400, 300))
            img = Image.fromarray(cv2.cvtColor(kucuk, cv2.COLOR_BGR2RGB))
            self.foto = ImageTk.PhotoImage(image=img)
            self.kamera_label.config(image=self.foto)

            # Yeterli eşleşme olduysa giriş başarılı
            if self.eslesme_sayaci >= GEREKLI_ESLESME:
                self.giris_basarili()
                return

        self.root.after(30, self.kareyi_guncelle)  # ~30 fps

    def giris_basarili(self):
        """Ekranı yeşile boyar, hoş geldin mesajı gösterir, 2 sn sonra kilidi kaldırır."""
        self.giris_yapildi = True
        if self.cap.isOpened():
            self.cap.release()

        # Tüm arayüzü temizleyip yeşil 'başarılı' ekranı göster
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg="#1f8b4c")
        tk.Label(self.root, text="✓ GİRİŞ BAŞARILI", font=("Segoe UI", 60, "bold"),
                 fg="white", bg="#1f8b4c").pack(pady=(200, 10))
        tk.Label(self.root, text="Hoş geldiniz, Öğretmenim", font=("Segoe UI", 28),
                 fg="white", bg="#1f8b4c").pack()

        # 2 saniye sonra kilit ekranını tamamen kapat
        self.root.after(2000, self.kilidi_kaldir)

    def kilidi_kaldir(self):
        """Kilit ekranını yok eder; arka plan logging thread'i çalışmaya devam eder."""
        self.root.destroy()

    def guvenli_cikis(self):
        """Demo güvenlik kapısı: CTRL+SHIFT+Q ile programı tamamen kapatır."""
        if self.cap.isOpened():
            self.cap.release()
        self.root.destroy()
        os._exit(0)


# ============================================================
#  ANA AKIŞ
# ============================================================
def main():
    # 1) Arka plan uygulama takibini hemen başlat
    logging_baslat()

    # 2) Kilit ekranını aç (yüz tanınınca kapanır, program arka planda devam eder)
    root = tk.Tk()
    KilitEkrani(root)
    root.mainloop()

    # 3) Kilit kapandıktan sonra: masaüstü açık, takip arka planda sürüyor.
    print("Kilit kaldırıldı. Uygulama takibi arka planda devam ediyor...")
    print(f"Loglar '{LOG_DOSYASI}' dosyasına yazılıyor. Kapatmak için bu pencereyi kapatın.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Program sonlandırıldı.")


if __name__ == "__main__":
    main()

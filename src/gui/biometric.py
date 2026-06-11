"""
TahtaKilit — Biyometrik Motor  |  Faz 3: Yüz Tanıma
=====================================================

Sorumluluklar:
  1. VectorStore  — 128D yüz vektörlerini makineye özgü XOR şifresiyle diske kaydeder/yükler.
                    Ham görüntüler enroll() sonrasında silinir; yalnızca vektörler tutulur.

  2. BiometricEngine — Daemon thread'de çalışır:
       • OpenCV ile webcam karesi alır
       • Kareyi 1/4'e küçülterek face_recognition (dlib ResNet-34) ile kodlar
       • Laplacian varyansı ile canlılık testi yapar (baskı fotoğrafı bypass önlemi)
       • CONSECUTIVE_MATCHES üst üste eşleşme sonrasında on_auth() çağırır
       • Kamera hataları için güvenli kurtarma ve UI bildirim mekanizması içerir

Enrollment (kayıt) CLI:
    python src/gui/biometric.py enroll "Ahmet Öğretmen" ogretmen.jpg

app.py entegrasyon noktası:
    engine = BiometricEngine(
        camera_label = lock_screen.camera_label,
        on_auth      = controller.authenticate_success,
        on_error     = lock_screen.update_status,
        tk_root      = controller,
    )
    engine.start()
"""

import hashlib
import logging
import pickle
import platform
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk

try:
    import face_recognition
    _FR_AVAILABLE = True
except ImportError:
    _FR_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
#  YAPILANDIRMA SABİTLERİ
# ─────────────────────────────────────────────────────────────────────────────

# Proje kökü: src/gui/biometric.py → ../../..
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

PROFILES_DIR = _PROJECT_ROOT / "profiles"
VECTORS_PATH = PROFILES_DIR / "vectors.dat"

# Kamera
CAMERA_INDEX      = 0      # 0 = varsayılan, 1 = harici
DISPLAY_W         = 640    # UI'da gösterilecek kare genişliği (piksel)
DISPLAY_H         = 480    # UI'da gösterilecek kare yüksekliği (piksel)
FRAME_SCALE       = 0.25   # face_recognition için küçültme oranı (1/4)

# Doğrulama
MATCH_TOLERANCE      = 0.45   # Varsayılan 0.6; düşük → katı. Fotoğraf bypass riskini azaltır.
CONSECUTIVE_MATCHES  = 5      # Üst üste kaç eşleşme gerektiği (yanlış pozitifi azaltır)
LIVENESS_VAR_MIN     = 80.0   # Laplacian varyansı bu eşiğin altında → baskı fotoğrafı şüphesi

# Kamera yeniden bağlantı
RECONNECT_ATTEMPTS  = 5
RECONNECT_DELAY_S   = 2.0
RECONNECT_LONG_S    = 10.0   # Tüm denemeler tükenince beklenecek süre

# Kilitleme sonrası cooldown — öğretmen kilitledikten hemen sonra yeniden girişi önler
LOCK_COOLDOWN_S = 2.0


# ─────────────────────────────────────────────────────────────────────────────
#  ŞİFRELEME YARDIMCILARI
# ─────────────────────────────────────────────────────────────────────────────

def _machine_key() -> bytes:
    """
    MAC adresi + ana bilgisayar adından makineye özgü 32-byte SHA-256 anahtar türetir.
    vectors.dat başka bir makineye kopyalandığında anahtar eşleşmez, dosya okunamaz.
    """
    material = f"{uuid.getnode()}:{platform.node()}"
    return hashlib.sha256(material.encode("utf-8")).digest()


def _xor_cipher(data: bytes, key: bytes) -> bytes:
    """Döngüsel XOR şifre/çözme — aynı işlev hem şifreler hem çözer."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


# ─────────────────────────────────────────────────────────────────────────────
#  VEKTÖR DEPOSU
# ─────────────────────────────────────────────────────────────────────────────

class VectorStore:
    """
    128D yüz vektörlerini şifrelenmiş pickle olarak profiles/vectors.dat'a kaydeder.

    Güvenlik notu: XOR şifresi kriptografik açıdan güçlü değildir; amacı
    vektörlerin düz metin olarak erişilebilir olmasını engellemektir.
    Fiziksel saldırılar için disk şifrelemesi (BitLocker) kullanılmalıdır.
    """

    def __init__(self) -> None:
        self._vectors: dict[str, np.ndarray] = {}
        self._log = logging.getLogger("VectorStore")
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

    # ── Genel API ─────────────────────────────────────────────────────────────

    def add(self, name: str, vector: np.ndarray) -> None:
        self._vectors[name] = vector

    def remove(self, name: str) -> None:
        self._vectors.pop(name, None)

    def get_all(self) -> dict[str, np.ndarray]:
        return dict(self._vectors)

    def __len__(self) -> int:
        return len(self._vectors)

    # ── Kalıcılık ─────────────────────────────────────────────────────────────

    def save(self) -> None:
        raw       = pickle.dumps(self._vectors)
        encrypted = _xor_cipher(raw, _machine_key())
        VECTORS_PATH.write_bytes(encrypted)
        self._log.info(f"{len(self._vectors)} vektör kaydedildi → {VECTORS_PATH}")

    def _load(self) -> None:
        if not VECTORS_PATH.exists():
            self._log.warning("vectors.dat bulunamadı — boş depo ile başlatıldı.")
            return
        try:
            encrypted        = VECTORS_PATH.read_bytes()
            raw              = _xor_cipher(encrypted, _machine_key())
            self._vectors    = pickle.loads(raw)
            names            = list(self._vectors.keys())
            self._log.info(f"{len(names)} profil yüklendi: {names}")
        except Exception as exc:
            # Bozuk dosya ya da farklı makineden kopyalanmış — sıfırdan başla.
            self._log.error(
                f"vectors.dat okunamadı (farklı makinede üretilmiş olabilir): {exc}"
            )
            self._vectors = {}


# ─────────────────────────────────────────────────────────────────────────────
#  BİYOMETRİK MOTOR
# ─────────────────────────────────────────────────────────────────────────────

class BiometricEngine:
    """
    Daemon thread'de çalışarak gerçek zamanlı yüz tanıma yapar ve
    eşleşme onaylandığında Tkinter state-machine'i tetikler.

    Thread güvenliği:
      Tüm Tkinter çağrıları tk_root.after(0, ...) ile ana thread'e iletilir.
      Bu sınıfın herhangi bir metodu doğrudan Tkinter widget metodunu çağırmaz.
    """

    def __init__(
        self,
        camera_label,           # tk.Label — her kare buraya yazılır (image=)
        on_auth,                # callable() — eşleşme onaylandığında çağrılır
        on_error,               # callable(msg: str, is_error: bool) — durum güncellemesi
        tk_root,                # tk.Tk — after() için
        camera_index: int = CAMERA_INDEX,
    ) -> None:
        self._camera_label  = camera_label
        self._on_auth       = on_auth
        self._on_error      = on_error
        self._tk            = tk_root
        self._camera_index  = camera_index

        self._cap: cv2.VideoCapture | None = None
        self._store         = VectorStore()
        self._running       = False
        self._consecutive   = 0
        self._cooldown_until = 0.0
        self._current_photo = None   # ImageTk.PhotoImage referansını GC'den korur

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [BIO] %(levelname)s — %(message)s",
            datefmt="%H:%M:%S",
        )
        self._log = logging.getLogger("BiometricEngine")

    # ── Genel API (app.py çağırır) ────────────────────────────────────────────

    def start(self) -> None:
        """Yüz tanıma döngüsünü daemon thread olarak başlatır."""
        if not _FR_AVAILABLE:
            self._ui_error(
                "face_recognition modülü kurulu değil. "
                "'pip install face_recognition' çalıştırın."
            )
            return

        if not self._store.get_all():
            self._ui_error(
                "Kayıtlı öğretmen profili yok. "
                "'python src/gui/biometric.py enroll' ile kayıt yapın."
            )
            return

        self._running = True
        threading.Thread(
            target=self._run, daemon=True, name="BiometricEngine"
        ).start()
        self._log.info("BiometricEngine başlatıldı.")

    def stop(self) -> None:
        """Tanıma döngüsünü durdurur; kamerayı bir sonraki iterasyonda serbest bırakır."""
        self._running = False

    def reset_on_lock(self) -> None:
        """
        Manuel kilitleme sonrası çağrılır.
        Eşleşme sayacını sıfırlar ve LOCK_COOLDOWN_S süre doğrulamayı askıya alır.
        Öğretmenin kilitleme hareketinin hemen ardından kamerayla yeniden giriş
        yapmasını önler.
        """
        self._consecutive    = 0
        self._cooldown_until = time.monotonic() + LOCK_COOLDOWN_S

    # ── Enrollment (Yönetici Aracı) ───────────────────────────────────────────

    @staticmethod
    def enroll(name: str, image_path: str) -> bool:
        """
        Referans fotoğrafından 128D vektör çıkarır, şifrelenmiş olarak kaydeder
        ve ham görüntü dosyasını siler (PRD zorunluluğu: raw image depolanmaz).

        Döndürür: True başarılı, False başarısız.
        """
        if not _FR_AVAILABLE:
            print("HATA: face_recognition modülü kurulu değil.")
            return False

        img_path = Path(image_path)
        if not img_path.exists():
            print(f"HATA: Dosya bulunamadı → {image_path}")
            return False

        try:
            img       = face_recognition.load_image_file(str(img_path))
            encodings = face_recognition.face_encodings(img)
        except Exception as exc:
            print(f"HATA: Görüntü işlenemedi → {exc}")
            return False

        if not encodings:
            print("HATA: Görüntüde yüz tespit edilemedi. Daha net bir fotoğraf kullanın.")
            return False

        store = VectorStore()
        store.add(name, encodings[0])
        store.save()

        # Ham görüntüyü kayıt sonrasında sil
        try:
            img_path.unlink()
            print(f"Ham görüntü silindi: {img_path}")
        except OSError as exc:
            print(f"UYARI: Ham görüntü silinemedi (manuel silin): {exc}")

        print(f"Kayıt başarılı → '{name}'  ({VECTORS_PATH})")
        return True

    # ── Ana Döngü (Thread) ────────────────────────────────────────────────────

    def _run(self) -> None:
        """
        Üst düzey thread fonksiyonu.
        Kamerayı açar → işleme döngüsüne girer → kapanırsa yeniden dener.
        """
        while self._running:
            if not self._open_camera():
                # Tüm yeniden bağlantı denemeleri tükendi — uzun bekle.
                time.sleep(RECONNECT_LONG_S)
                continue

            self._log.info("Kamera hazır. Tanıma döngüsü başladı.")
            try:
                self._process_loop()
            finally:
                if self._cap:
                    self._cap.release()
                    self._cap = None

    def _open_camera(self) -> bool:
        """
        Kamerayı RECONNECT_ATTEMPTS kez açmayı dener.
        Her başarısız denemede UI'a durum mesajı gönderir.
        """
        for attempt in range(1, RECONNECT_ATTEMPTS + 1):
            self._cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
            if self._cap.isOpened():
                return True

            self._cap.release()
            self._cap = None
            msg = f"Kamera açılamadı (deneme {attempt}/{RECONNECT_ATTEMPTS})..."
            self._log.warning(msg)
            self._ui_error(msg)
            time.sleep(RECONNECT_DELAY_S)

        self._ui_error("Kamera bağlanamadı. USB/dahili kamerayı kontrol edin.")
        return False

    def _process_loop(self) -> None:
        """
        Kamera açıkken her kareyi analiz eder.
        cap.read() başarısız olursa döngüden çıkar; _run() yeniden bağlanır.
        """
        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                self._log.warning("Kare alınamadı — kamera bağlantısı kopmuş olabilir.")
                self._ui_error("Kamera bağlantısı kesildi. Yeniden bağlanılıyor...")
                break

            frame = cv2.flip(frame, 1)   # Ayna görüntüsü — daha doğal hissiyat
            pil_img, matched, count = self._analyse(frame)

            # Kareyi her durumda UI'da göster
            if pil_img is not None:
                self._update_camera_label(pil_img)

            # Cooldown aktifse doğrulama işlemini atla
            if time.monotonic() < self._cooldown_until:
                continue

            if matched:
                msg = f"Doğrulanıyor... ({count}/{CONSECUTIVE_MATCHES})"
                self._schedule_ui(lambda m=msg: self._on_error(m, False))
                if count >= CONSECUTIVE_MATCHES:
                    self._consecutive = 0
                    self._log.info("Kimlik doğrulama başarılı — UI'a AUTH_SUCCESS gönderiliyor.")
                    self._schedule_ui(self._on_auth)
            elif count == 0:
                # Sayaç yeni sıfırlandı — bekleme mesajını geri getir
                self._schedule_ui(
                    lambda: self._on_error("Yüzünüzü kameraya hizalayın", False)
                )

    # ── Analiz ────────────────────────────────────────────────────────────────

    def _analyse(self, frame: np.ndarray) -> tuple:
        """
        Tek bir kareyi işler.

        Döndürür:
            pil_img  : PIL.Image veya None (hata durumunda)
            matched  : bool — bu karede en az bir yüz eşleşti mi
            count    : int  — güncel art arda eşleşme sayısı
        """
        # face_recognition için 1/4 boyuta küçült (işlem yükünü ~16× azaltır)
        small     = cv2.resize(frame, (0, 0), fx=FRAME_SCALE, fy=FRAME_SCALE)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        try:
            locations = face_recognition.face_locations(rgb_small, model="hog")
            encodings = face_recognition.face_encodings(rgb_small, locations)
        except Exception as exc:
            self._log.error(f"face_recognition işlem hatası: {exc}")
            return self._to_pil(frame), False, 0

        known_vecs   = list(self._store.get_all().values())
        scale        = int(1 / FRAME_SCALE)   # küçük koordinatları orijinale geri ölçekle
        frame_matched = False

        for (top, right, bottom, left), encoding in zip(locations, encodings):
            # ── Canlılık testi ─────────────────────────────────────────────
            # Kağıt çıktısı veya ekran fotoğrafı düşük Laplacian varyansı üretir.
            t, r, b, l = top*scale, right*scale, bottom*scale, left*scale
            face_gray   = cv2.cvtColor(frame[t:b, l:r], cv2.COLOR_BGR2GRAY)

            if not self._is_live(face_gray):
                # Gri kutu: yüz algılandı ama canlılık başarısız
                cv2.rectangle(frame, (l, t), (r, b), (128, 128, 128), 2)
                cv2.putText(
                    frame, "CANLILIK HATASI", (l, t - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 1,
                )
                self._consecutive = 0
                continue

            # ── Vektör karşılaştırması ────────────────────────────────────
            if known_vecs:
                distances = face_recognition.face_distance(known_vecs, encoding)
                best_dist = float(np.min(distances))
                is_match  = best_dist <= MATCH_TOLERANCE
            else:
                is_match  = False

            color = (0, 200, 60) if is_match else (0, 60, 220)   # yeşil / kırmızı (BGR)
            cv2.rectangle(frame, (l, t), (r, b), color, 2)

            if is_match:
                frame_matched = True

        if frame_matched:
            self._consecutive += 1
        else:
            self._consecutive = 0

        return self._to_pil(frame), frame_matched, self._consecutive

    # ── Yardımcılar ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_live(face_gray: np.ndarray) -> bool:
        """
        Yüz bölgesinin Laplacian varyansını hesaplar.
        Düşük varyans → düz, az detay → muhtemelen baskı fotoğrafı veya ekran.
        Eşik değeri (LIVENESS_VAR_MIN) gerçek ortamda kalibre edilmelidir.
        """
        if face_gray.size == 0:
            return False
        return float(cv2.Laplacian(face_gray, cv2.CV_64F).var()) >= LIVENESS_VAR_MIN

    @staticmethod
    def _to_pil(frame: np.ndarray) -> Image.Image:
        """OpenCV BGR kareyi yeniden boyutlandırılmış PIL RGB görüntüsüne dönüştürür."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb).resize((DISPLAY_W, DISPLAY_H), Image.BILINEAR)

    def _update_camera_label(self, pil_img: Image.Image) -> None:
        """
        PIL görüntüyü Tkinter ana thread'inde PhotoImage'a dönüştürüp camera_label'a yazar.

        ImageTk.PhotoImage ana thread'de oluşturulmalıdır; bu nedenle dönüşüm
        after() callback'inin içinde yapılır.
        """
        def _apply(img: Image.Image = pil_img) -> None:
            photo               = ImageTk.PhotoImage(image=img)
            self._current_photo = photo  # GC referansını koru
            self._camera_label.config(image=photo)

        self._schedule_ui(_apply)

    def _schedule_ui(self, callback) -> None:
        """Tkinter ana thread'inde callback'i güvenle zamanlar."""
        try:
            self._tk.after(0, callback)
        except Exception:
            pass  # Uygulama kapanıyorsa tk referansı artık geçersiz

    def _ui_error(self, msg: str) -> None:
        """Hata mesajını Tkinter durum etiketine gönderir."""
        self._schedule_ui(lambda m=msg: self._on_error(m, True))


# ─────────────────────────────────────────────────────────────────────────────
#  KAYIT CLI  —  python src/gui/biometric.py enroll "Ad Soyad" foto.jpg
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    if len(sys.argv) == 4 and sys.argv[1] == "enroll":
        _, _, teacher_name, photo_path = sys.argv
        success = BiometricEngine.enroll(teacher_name, photo_path)
        sys.exit(0 if success else 1)

    print(
        "Kullanım: python src/gui/biometric.py enroll \"Ad Soyad\" foto.jpg\n"
        "\n"
        "  Ad Soyad : Öğretmenin görüntülenecek adı\n"
        "  foto.jpg : Referans fotoğraf (işlem sonrasında silinir)\n"
        "\n"
        "Örnek: python src/gui/biometric.py enroll \"Ahmet Öğretmen\" ogretmen.jpg\n"
    )
    sys.exit(1)

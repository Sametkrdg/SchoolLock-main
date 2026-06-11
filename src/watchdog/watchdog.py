"""
TahtaKilit — Watchdog Süreci  |  Faz 2: IPC + Süreç Koruma
============================================================

Mimari:
  • Bu süreç Windows kabuğu olarak çalışır (explorer.exe yerine).
  • GUI alt sürecini (KilitArayuzu / app.py) başlatır ve izler.
  • GUI öldürülürse ya da kalp atışı kesilirse 200ms altında yeniden başlatır.
  • Named Pipe sunucusu üzerinden GUI durum sinyallerini alır:
        "HEARTBEAT"    → GUI hayatta, kilit durumu korunuyor
        "AUTH_SUCCESS" → Öğretmen doğrulandı; Görev Yöneticisi kilidi kaldırılabilir
        "LOCK_COMMAND" → Öğretmen kilitledi; tüm sertleştirme katmanları yeniden etkin

Üretim çalıştırma:
    python src/watchdog/watchdog.py

Geliştirme çalıştırma:
    DEV_MODE=True olarak aynı komut (app.py'yi python ile başlatır)

Faz 3 bağlantı noktası:
    REGISTRY_HOOK satırlarını registry_ops.RegistryEngine çağrılarıyla doldurun.
"""

import logging
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path

# pywin32 — Windows Named Pipe için zorunlu
try:
    import pywintypes
    import win32file
    import win32pipe
except ImportError as exc:
    sys.exit(
        f"[WATCHDOG] pywin32 bulunamadı. Lütfen 'pip install pywin32' komutunu çalıştırın.\n{exc}"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  YAPILANDIRMA
# ─────────────────────────────────────────────────────────────────────────────

# Üretimde False yapın.
DEV_MODE = True

PIPE_NAME = r"\\.\pipe\TahtaKilitIPC"

# GUI kalp atışı bu kadar saniye içinde gelmezse süreç ölü sayılır.
# (Süreç izleme bunu zaten yakalar; bu yalnızca donmuş/kilitlenmiş GUI içindir.)
HEARTBEAT_TIMEOUT_S = 5

# GUI ölümünden yeniden başlatmaya kadar geçen minimum süre (50ms << 200ms hedef).
RELAUNCH_COOLDOWN_S = 0.05

# Bu kadar hızlı art arda yeniden başlatma sonrasında uzun beklemeye geç.
MAX_QUICK_RELAUNCHES = 5
QUICK_RELAUNCH_WINDOW_S = 30
EXTENDED_COOLDOWN_S = 10

# Pipe okuma tamponu (mesajlar çok küçük; 4 KB fazlasıyla yeter).
PIPE_BUF = 4096

# Proje kök dizini (src/watchdog/watchdog.py → ../../..)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Geliştirme: python ile app.py başlat.
# Üretim:     derlenmiş GUI exe'yi doğrudan başlat.
GUI_COMMAND_DEV  = [sys.executable, str(_PROJECT_ROOT / "app.py")]
GUI_COMMAND_PROD = [str(_PROJECT_ROOT / "KilitArayuzu.exe")]


# ─────────────────────────────────────────────────────────────────────────────
#  DURUM MAKİNESİ
# ─────────────────────────────────────────────────────────────────────────────

class KioskState(Enum):
    LOCKED   = "LOCKED"
    UNLOCKED = "UNLOCKED"


# ─────────────────────────────────────────────────────────────────────────────
#  WATCHDOG SUNUCU
# ─────────────────────────────────────────────────────────────────────────────

class WatchdogServer:
    """
    GUI sürecini başlatır, izler ve gerektiğinde yeniden başlatır.

    Güvenlik garantisi:
        GUI herhangi bir şekilde sonlandırılırsa (kill, kilitlenme, çökme)
        watchdog 200ms altında yeni bir örnek başlatır; bu sırada masaüstü
        görünmez çünkü watchdog zaten Windows kabuğudur.
    """

    def __init__(self) -> None:
        self.state            = KioskState.LOCKED
        self.last_heartbeat   = time.monotonic()
        self._gui_process: subprocess.Popen | None = None
        self._relaunch_times: list[float] = []
        self._pipe_handle     = None
        self._state_lock      = threading.Lock()

        logging.basicConfig(
            level=logging.DEBUG if DEV_MODE else logging.INFO,
            format="%(asctime)s [WATCHDOG] %(levelname)s — %(message)s",
            datefmt="%H:%M:%S",
        )
        self.log = logging.getLogger("watchdog")
        self.log.info(f"DEV_MODE={'etkin' if DEV_MODE else 'devre dışı'}")

    # ── ANA DÖNGÜ ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        Asla dönmez.
        Pipe sunucusunu arka planda başlatır, sonra GUI izleme döngüsüne girer.
        """
        pipe_thread = threading.Thread(
            target=self._pipe_server_loop,
            daemon=True,
            name="PipeServer",
        )
        pipe_thread.start()

        # Kalp atışı zaman aşımı denetçisi
        timeout_thread = threading.Thread(
            target=self._heartbeat_watchdog,
            daemon=True,
            name="HeartbeatWatchdog",
        )
        timeout_thread.start()

        self.log.info("Watchdog başlatıldı. GUI izleme döngüsüne giriliyor.")
        while True:
            self._launch_gui()

            # Bu satır GUI ölene kadar bloklar — anlık algılama, sıfır polling yükü.
            exit_code = self._gui_process.wait()
            self.log.warning(
                f"GUI süreci sonlandı (çıkış kodu: {exit_code}, "
                f"son kalp atışı: {time.monotonic() - self.last_heartbeat:.1f}s önce)."
            )
            self._throttled_cooldown()

    # ── GUI BAŞLATMA ──────────────────────────────────────────────────────────

    def _launch_gui(self) -> None:
        cmd = GUI_COMMAND_DEV if DEV_MODE else GUI_COMMAND_PROD
        try:
            self._gui_process = subprocess.Popen(cmd)
            self.last_heartbeat = time.monotonic()
            self.log.info(f"GUI başlatıldı — PID: {self._gui_process.pid}  komut: {cmd}")
        except FileNotFoundError:
            self.log.critical(
                f"GUI çalıştırılabilir dosyası bulunamadı: {cmd[0]}\n"
                "Derlenmiş exe eksikse DEV_MODE=True yapın."
            )
            # Yeniden denemeyi fırtına senaryolarında engellemek için uzun bekle.
            time.sleep(EXTENDED_COOLDOWN_S)

    # ── YENİDEN BAŞLATMA HIZI DENETÇISI ──────────────────────────────────────

    def _throttled_cooldown(self) -> None:
        """
        Hızlı art arda çökmeler tespit edilirse kısa bekleme yerine
        uzun beklemeye geçer; sonsuz hızlı döngüyü önler.
        """
        now = time.monotonic()
        # Pencere dışına düşen kayıtları temizle
        self._relaunch_times = [t for t in self._relaunch_times if now - t < QUICK_RELAUNCH_WINDOW_S]
        self._relaunch_times.append(now)

        if len(self._relaunch_times) >= MAX_QUICK_RELAUNCHES:
            self.log.error(
                f"GUI {MAX_QUICK_RELAUNCHES} kez {QUICK_RELAUNCH_WINDOW_S}s içinde çöktü. "
                f"{EXTENDED_COOLDOWN_S}s bekleniyor — donanım/yazılım sorununu kontrol edin."
            )
            time.sleep(EXTENDED_COOLDOWN_S)
        else:
            time.sleep(RELAUNCH_COOLDOWN_S)

    # ── KALP ATIŞI ZAMAN AŞIMI ────────────────────────────────────────────────

    def _heartbeat_watchdog(self) -> None:
        """
        GUI'nin donup kalmadığını denetler.
        process.wait() yalnızca öldürülen süreçleri yakalar;
        bu thread donmuş (ama hayatta) süreçleri de yakalar.
        """
        while True:
            time.sleep(HEARTBEAT_TIMEOUT_S)
            if self._gui_process is None:
                continue
            elapsed = time.monotonic() - self.last_heartbeat
            if elapsed > HEARTBEAT_TIMEOUT_S and self._gui_process.poll() is None:
                self.log.warning(
                    f"Kalp atışı {elapsed:.1f}s'dir alınamıyor — GUI donmuş olabilir. "
                    "Süreç sonlandırılıyor."
                )
                self._gui_process.terminate()
                # Ana döngü process.wait() dönüşünü algılar ve yeniden başlatır.

    # ── NAMED PIPE SUNUCU ─────────────────────────────────────────────────────

    def _pipe_server_loop(self) -> None:
        """
        Named Pipe sunucusunu sürekli açık tutar.
        Her GUI bağlantısı için: bağlan → oku → bağlantıyı kes → tekrarla.
        """
        pipe = win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_INBOUND,          # Yalnızca GUI→Watchdog yönünde
            win32pipe.PIPE_TYPE_MESSAGE              # Her WriteFile ayrı bir mesajdır
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT,
            1,          # Maksimum eşzamanlı örnek
            0,          # Çıkış tamponu (gelen-yalnız pipe'ta kullanılmaz)
            PIPE_BUF,   # Giriş tamponu
            0,          # Varsayılan zaman aşımı (ms)
            None,       # Güvenlik tanımlayıcısı (varsayılan)
        )
        self.log.info(f"Pipe sunucusu hazır → {PIPE_NAME}")

        while True:
            try:
                # İstemci bağlanana kadar bloklar (CPU tüketmez).
                win32pipe.ConnectNamedPipe(pipe, None)
                self.log.info("GUI istemcisi pipe'a bağlandı.")
                self._read_loop(pipe)
            except pywintypes.error as exc:
                self.log.error(f"Pipe sunucu hatası: {exc}")
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(pipe)
                except Exception:
                    pass
            # Kısa bekle: yeni GUI başlayana kadar pipe'ı "dinleme" moduna al.
            time.sleep(0.01)

    def _read_loop(self, pipe) -> None:
        """
        Bağlı istemciden gelen mesajları pipe kopana kadar okur.
        Her hata net bir log satırıyla kaydedilir; sessizce yutulmaz.
        """
        while True:
            try:
                _, raw = win32file.ReadFile(pipe, PIPE_BUF)
                msg = raw.decode("utf-8").strip()
                self.log.debug(f"← {msg!r}")
                self._handle_message(msg)

            except pywintypes.error as exc:
                # 109 = ERROR_BROKEN_PIPE  |  232 = ERROR_NO_DATA
                if exc.winerror in (109, 232):
                    self.log.info("Pipe bağlantısı kapandı (GUI sonlandı veya yeniden başlatılıyor).")
                else:
                    self.log.error(f"Beklenmedik pipe okuma hatası [{exc.winerror}]: {exc}")
                break

            except Exception as exc:
                self.log.error(f"Read loop istisnası: {exc}")
                break

    # ── MESAJ İŞLEYİCİ ───────────────────────────────────────────────────────

    def _handle_message(self, msg: str) -> None:
        """GUI'den gelen durum sinyallerini işler ve iç durumu günceller."""
        self.last_heartbeat = time.monotonic()

        with self._state_lock:
            if msg == "HEARTBEAT":
                # Sessizce işle — last_heartbeat zaten güncellendi.
                pass

            elif msg == "AUTH_SUCCESS":
                self.state = KioskState.UNLOCKED
                self.log.info("Durum: UNLOCKED — öğretmen doğrulandı.")
                # REGISTRY_HOOK (Faz 1 tamamlandığında açın):
                # from src.utils.registry_ops import RegistryEngine
                # RegistryEngine.set_task_manager_disabled(False)

            elif msg == "LOCK_COMMAND":
                self.state = KioskState.LOCKED
                self.log.info("Durum: LOCKED — tüm sertleştirme katmanları yeniden etkin.")
                # REGISTRY_HOOK (Faz 1 tamamlandığında açın):
                # from src.utils.registry_ops import RegistryEngine
                # RegistryEngine.set_task_manager_disabled(True)

            else:
                self.log.warning(f"Tanınmayan mesaj: {msg!r} — görmezden geliniyor.")


# ─────────────────────────────────────────────────────────────────────────────
#  GİRİŞ NOKTASI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    WatchdogServer().run()

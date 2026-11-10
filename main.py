import os
from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from supabase import create_client, Client as SupabaseClient

# Muat variabel environment dari file .env
load_dotenv()

# ================= KONFIGURASI SUPABASE =================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("[ERROR] SUPABASE_URL atau SUPABASE_KEY belum disetel di .env")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================= KONFIGURASI PYROGRAM =================
try:
    API_ID = int(os.getenv("API_ID", 0))
    API_HASH = os.getenv("API_HASH")
    GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", 0))
    ESP32_BOT_USERNAME = os.getenv("ESP32_BOT_USERNAME")
except ValueError:
    raise ValueError("[ERROR] Pastikan API_ID dan GROUP_CHAT_ID berupa angka di file .env")

if not all([API_ID, API_HASH, GROUP_CHAT_ID, ESP32_BOT_USERNAME]):
    raise ValueError("[ERROR] Konfigurasi Telegram di .env belum lengkap.")

# Inisialisasi Userbot
app = Client("smartcity_session", api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.chat(GROUP_CHAT_ID) & filters.user(ESP32_BOT_USERNAME))
async def sync_to_supabase(client, message):
    text = message.text
    if not text:
        return

    updates = {}

    # -- Parsing Status Perangkat Servo --
    if "Pintu DIBUKA" in text: updates['pintu'] = True
    elif "Pintu DITUTUP" in text: updates['pintu'] = False

    if "Jemuran DIKELUARKAN" in text: updates['jemuran'] = True
    elif "Jemuran DITARIK" in text: updates['jemuran'] = False
        
    if "Pagar DIBUKA" in text: updates['pagar'] = True
    elif "Pagar DITUTUP" in text: updates['pagar'] = False
        
    if "Jendela DIBUKA" in text: updates['jendela'] = True
    elif "Jendela DITUTUP" in text: updates['jendela'] = False

    # -- Parsing Status Lampu --
    if "Lampu Rumah MENYALA" in text: updates['lampu_rumah'] = True
    elif "Lampu Rumah MATI" in text: updates['lampu_rumah'] = False

    # -- Parsing Sensor LDR (Cahaya & Lampu Jalan) --
    if "Lampu Jalan MENYALA" in text or "Malam Tiba" in text: 
        updates['lampu_jalan'] = True
        if "Malam Tiba" in text: updates['is_dark'] = True
            
    elif "Lampu Jalan MATI" in text or "Pagi Tiba" in text: 
        updates['lampu_jalan'] = False
        if "Pagi Tiba" in text: updates['is_dark'] = False

    # -- Parsing Sensor Hujan --
    if "Hujan turun" in text: updates['is_raining'] = True
    elif "Hujan telah reda" in text: updates['is_raining'] = False
    
    # Kondisi khusus jemuran ditarik karena hujan
    if "HUJAN! Jemuran ditarik otomatis" in text:
        updates['is_raining'] = True
        updates['jemuran'] = False

    # -- Eksekusi Update ke Supabase --
    if updates:
        try:
            # Update baris dengan id = 1 sesuai inisialisasi tabel di database
            response = supabase.table('smart_city_state').update(updates).eq('id', 1).execute()
            print(f"[OK] Supabase synced: {updates}")
        except Exception as e:
            print(f"[ERROR] Gagal sinkronisasi ke Supabase: {e}")

# ... (kode filter & supabase Anda tetap sama) ...

async def main():
    await app.start()
    print("🔄 Memperbarui cache database Telegram lokal...")
    
    try:
        # Mengambil daftar riwayat chat agar Pyrogram mengenali ID dan Access Hash grup
        async for dialog in app.get_dialogs():
            pass
        print("✅ Sinkronisasi grup berhasil!")
    except Exception as e:
        print(f"[WARN] Sinkronisasi sebagian gagal, tapi akan dilanjutkan: {e}")

    print("🚀 Background listener Smart City siap dan berjalan!")
    
    # idle() akan menahan skrip agar terus berjalan di background tanpa memblokir sistem
    await idle()
    
    await app.stop()

if __name__ == "__main__":
    # Menjalankan fungsi main() sebagai asinkron
    app.run(main())

import os
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from supabase import create_client, Client as SupabaseClient

load_dotenv()

# ================= SUPABASE =================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL dan SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY wajib di .env")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================= TELEGRAM =================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
ESP32_BOT_USERNAME = os.getenv("ESP32_BOT_USERNAME")

if not all([API_ID, API_HASH, GROUP_CHAT_ID, ESP32_BOT_USERNAME]):
    raise ValueError("Konfigurasi Telegram di .env belum lengkap.")

app = Client(
    "smartcity_session",
    api_id=API_ID,
    api_hash=API_HASH
)

# Dashboard hanya boleh mengontrol perangkat yang memang punya command di ESP32.
COMMANDS = {
    "pintu": "/pintu",
    "jemuran": "/jemuran",
    "lampu_rumah": "/LampuRumah",
    "lampu_jalan": "/LampuJalan",
}

# ================= TELEGRAM -> SUPABASE =================
def parse_message(text: str):
    updates = {}

    if "Pintu DIBUKA" in text:
        updates["pintu"] = True
    elif "Pintu DITUTUP" in text:
        updates["pintu"] = False

    if "Jemuran DIKELUARKAN" in text:
        updates["jemuran"] = True
    elif "Jemuran DITARIK" in text:
        updates["jemuran"] = False

    if "Lampu Rumah MENYALA" in text:
        updates["lampu_rumah"] = True
    elif "Lampu Rumah MATI" in text:
        updates["lampu_rumah"] = False

    if "Lampu Jalan MENYALA" in text or "Malam Tiba" in text:
        updates["lampu_jalan"] = True
        if "Malam Tiba" in text:
            updates["is_dark"] = True
    elif "Lampu Jalan MATI" in text or "Pagi Tiba" in text:
        updates["lampu_jalan"] = False
        if "Pagi Tiba" in text:
            updates["is_dark"] = False

    if "Hujan turun" in text:
        updates["is_raining"] = True
    elif "Hujan telah reda" in text:
        updates["is_raining"] = False

    if "HUJAN! Jemuran ditarik otomatis" in text:
        updates["is_raining"] = True
        updates["jemuran"] = False

    return updates


@app.on_message(filters.chat(GROUP_CHAT_ID) & filters.user(ESP32_BOT_USERNAME))
async def sync_telegram_to_supabase(client, message):
    text = message.text or message.caption or ""
    if not text:
        return

    updates = parse_message(text)
    if not updates:
        return

    try:
        supabase.table("smart_city_state").update(updates).eq("id", 1).execute()

        # Jika ada command dashboard yang sudah dikirim dan status aktual
        # sudah sama dengan target, tandai sebagai completed.
        for device, state in updates.items():
            if device in COMMANDS:
                supabase.table("device_commands") \
                    .update({"status": "completed"}) \
                    .eq("device", device) \
                    .eq("desired_state", state) \
                    .in_("status", ["pending", "sent"]) \
                    .execute()

        print(f"[OK] Telegram -> Supabase: {updates}")
    except Exception as e:
        print(f"[ERROR] Gagal sync Telegram -> Supabase: {e}")


# ================= SUPABASE -> TELEGRAM =================
async def process_dashboard_commands():
    """
    Dashboard tidak mengirim Telegram langsung.
    Dashboard memasukkan row ke device_commands.
    Python Userbot membaca queue ini lalu mengirim command ke grup.

    Karena command ESP32 bersifat toggle, kita hanya mengirim command
    jika desired_state berbeda dari state aktual di smart_city_state.
    """
    while True:
        try:
            result = (
                supabase.table("device_commands")
                .select("*")
                .eq("status", "pending")
                .order("created_at")
                .limit(10)
                .execute()
            )

            commands = result.data or []

            if commands:
                state_result = (
                    supabase.table("smart_city_state")
                    .select("*")
                    .eq("id", 1)
                    .single()
                    .execute()
                )
                current = state_result.data or {}

                for command in commands:
                    command_id = command["id"]
                    device = command["device"]
                    desired = bool(command["desired_state"])
                    current_state = bool(current.get(device, False))
                    telegram_command = COMMANDS.get(device)

                    if not telegram_command:
                        supabase.table("device_commands").update({
                            "status": "failed",
                            "error_message": f"Device '{device}' tidak didukung ESP32"
                        }).eq("id", command_id).execute()
                        continue

                    # Kalau ternyata sudah sesuai, tidak perlu toggle.
                    if current_state == desired:
                        supabase.table("device_commands").update({
                            "status": "completed",
                            "sent_at": datetime.now(timezone.utc).isoformat()
                        }).eq("id", command_id).execute()
                        continue

                    # Kirim command ke grup yang didengarkan ESP32.
                    sent = await app.send_message(GROUP_CHAT_ID, telegram_command)

                    supabase.table("device_commands").update({
                        "status": "sent",
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                        "telegram_message_id": sent.id
                    }).eq("id", command_id).execute()

                    print(
                        f"[OK] Dashboard -> Telegram: "
                        f"{device} => {desired} ({telegram_command})"
                    )

        except Exception as e:
            print(f"[ERROR] Queue dashboard: {e}")

        await asyncio.sleep(0.8)


async def main():
    await app.start()

    print("🔄 Memuat dialog Telegram...")
    try:
        async for _ in app.get_dialogs():
            pass
        print("✅ Dialog Telegram siap.")
    except Exception as e:
        print(f"[WARN] get_dialogs: {e}")

    print("🚀 Smart City Userbot ONLINE")
    print("   Telegram -> Supabase : aktif")
    print("   Dashboard -> Telegram: aktif")

    queue_task = asyncio.create_task(process_dashboard_commands())

    try:
        await idle()
    finally:
        queue_task.cancel()
        await app.stop()


if __name__ == "__main__":
    app.run(main())

import os
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from supabase import create_client, Client as SupabaseClient

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
ESP32_BOT_USERNAME = os.getenv("ESP32_BOT_USERNAME", "").lstrip("@").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL dan SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY wajib di .env")
if not all([API_ID, API_HASH, GROUP_CHAT_ID, ESP32_BOT_USERNAME]):
    raise ValueError("API_ID, API_HASH, GROUP_CHAT_ID, ESP32_BOT_USERNAME wajib di .env")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Client("smartcity_session", api_id=API_ID, api_hash=API_HASH)

# HARUS sama dengan command yang benar-benar diproses ESP32.
COMMANDS = {
    "pintu": "/pintu",
    "jemuran": "/jemuran",
    "lampu_rumah": "/LampuRumah",
    "lampu_jalan": "/LampuJalan",
}


def parse_esp32_message(text: str):
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

    if "Lampu Jalan MENYALA" in text:
        updates["lampu_jalan"] = True
    elif "Lampu Jalan MATI" in text:
        updates["lampu_jalan"] = False

    if "Malam Tiba" in text:
        updates["is_dark"] = True
    elif "Pagi Tiba" in text:
        updates["is_dark"] = False

    if "Hujan turun" in text:
        updates["is_raining"] = True
    elif "Hujan telah reda" in text:
        updates["is_raining"] = False

    if "HUJAN! Jemuran ditarik otomatis" in text:
        updates["is_raining"] = True
        updates["jemuran"] = False

    return updates


@app.on_message(filters.chat(GROUP_CHAT_ID))
async def telegram_to_supabase(client, message):
    """Semua pesan grup diperiksa, tetapi hanya pesan yang terlihat seperti
    konfirmasi ESP32 yang boleh mengubah state. Ini lebih aman daripada
    bergantung pada filters.user yang kadang tidak cocok dengan username bot.
    """
    text = message.text or message.caption or ""
    if not text:
        return

    updates = parse_esp32_message(text)
    if not updates:
        return

    # Pastikan pesan berasal dari bot ESP32.
    sender = message.from_user
    sender_username = (sender.username or "").lstrip("@").lower() if sender else ""
    if sender_username != ESP32_BOT_USERNAME.lower():
        return

    try:
        supabase.table("smart_city_state").update(updates).eq("id", 1).execute()
        for device, actual_state in updates.items():
            if device in COMMANDS:
                supabase.table("device_commands").update({
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("device", device).eq("desired_state", actual_state).in_(
                    "status", ["pending", "sending", "sent"]
                ).execute()
        print(f"[OK] ESP32 -> Supabase: {updates}")
    except Exception as e:
        print(f"[ERROR] Telegram -> Supabase: {e}")


async def process_dashboard_commands():
    """Bridge: Dashboard -> Supabase queue -> Telegram -> ESP32."""
    while True:
        try:
            result = (
                supabase.table("device_commands")
                .select("*")
                .eq("status", "pending")
                .order("created_at")
                .limit(5)
                .execute()
            )

            for command in result.data or []:
                command_id = command["id"]
                device = command["device"]
                desired = bool(command["desired_state"])
                telegram_command = COMMANDS.get(device)

                if not telegram_command:
                    supabase.table("device_commands").update({
                        "status": "failed",
                        "error_message": f"Device {device} tidak didukung ESP32"
                    }).eq("id", command_id).execute()
                    continue

                # Ambil status AKTUAL, bukan status dashboard.
                state_result = (
                    supabase.table("smart_city_state")
                    .select(device)
                    .eq("id", 1)
                    .single()
                    .execute()
                )
                actual = bool((state_result.data or {}).get(device, False))

                # ESP32 command bersifat TOGGLE. Kalau sudah sesuai, jangan toggle lagi.
                if actual == desired:
                    supabase.table("device_commands").update({
                        "status": "completed",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", command_id).execute()
                    continue

                # Tandai sedang dikirim agar dashboard bisa menunjukkan progres.
                supabase.table("device_commands").update({
                    "status": "sending"
                }).eq("id", command_id).eq("status", "pending").execute()

                try:
                    sent = await app.send_message(GROUP_CHAT_ID, telegram_command)
                    supabase.table("device_commands").update({
                        "status": "sent",
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                        "telegram_message_id": sent.id,
                    }).eq("id", command_id).execute()
                    print(f"[OK] Dashboard -> Telegram: {device} => {desired} via {telegram_command}")
                except Exception as send_error:
                    supabase.table("device_commands").update({
                        "status": "failed",
                        "error_message": str(send_error),
                    }).eq("id", command_id).execute()
                    print(f"[ERROR] Telegram command {telegram_command}: {send_error}")

        except Exception as e:
            print(f"[ERROR] Queue dashboard: {e}")

        await asyncio.sleep(0.5)


async def main():
    await app.start()
    print("========================================")
    print(" SMART CITY USERBOT ONLINE")
    print(" Dashboard -> Supabase -> Telegram -> ESP32")
    print(" ESP32 -> Telegram -> Supabase -> Dashboard")
    print("========================================")

    try:
        async for _ in app.get_dialogs():
            pass
    except Exception as e:
        print(f"[WARN] get_dialogs: {e}")

    queue_task = asyncio.create_task(process_dashboard_commands())
    try:
        await idle()
    finally:
        queue_task.cancel()
        await app.stop()


if __name__ == "__main__":
    app.run(main())

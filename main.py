import os
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from supabase import create_client, Client as SupabaseClient


# ============================================================
# LOAD ENV
# ============================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))

ESP32_BOT_USERNAME = (
    os.getenv("ESP32_BOT_USERNAME", "")
    .lstrip("@")
    .strip()
)


# ============================================================
# VALIDASI ENV
# ============================================================

if not SUPABASE_URL:
    raise ValueError(
        "SUPABASE_URL wajib diisi di file .env"
    )

if not SUPABASE_KEY:
    raise ValueError(
        "SUPABASE_SERVICE_ROLE_KEY atau SUPABASE_KEY wajib diisi di .env"
    )

if not API_ID:
    raise ValueError(
        "API_ID wajib diisi di .env"
    )

if not API_HASH:
    raise ValueError(
        "API_HASH wajib diisi di .env"
    )

if not GROUP_CHAT_ID:
    raise ValueError(
        "GROUP_CHAT_ID wajib diisi di .env"
    )

if not ESP32_BOT_USERNAME:
    raise ValueError(
        "ESP32_BOT_USERNAME wajib diisi di .env"
    )


# ============================================================
# SUPABASE
# ============================================================

supabase: SupabaseClient = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ============================================================
# PYROGRAM
# ============================================================

app = Client(
    "smartcity_session",
    api_id=API_ID,
    api_hash=API_HASH
)


# ============================================================
# COMMAND ESP32
#
# HARUS SAMA DENGAN COMMAND YANG DIDUKUNG ESP32
# ============================================================

COMMANDS = {
    "pintu": "/pintu",
    "jemuran": "/jemuran",
    "lampu_rumah": "/LampuRumah",
    "lampu_jalan": "/LampuJalan",
}


# ============================================================
# HELPER
# ============================================================

def now_utc():
    """
    Menghasilkan waktu UTC dalam format ISO.
    """
    return datetime.now(timezone.utc).isoformat()


def get_current_state(device):
    """
    Mengambil status aktual device dari smart_city_state.

    Tidak menggunakan .single() supaya tidak muncul error:
    'Cannot coerce the result to a single JSON object'
    """

    try:
        result = (
            supabase
            .table("smart_city_state")
            .select(device)
            .eq("id", 1)
            .limit(1)
            .execute()
        )

        if not result.data:
            print(
                f"[WARN] smart_city_state dengan id=1 tidak ditemukan."
            )
            return None

        return bool(
            result.data[0].get(device, False)
        )

    except Exception as e:
        print(
            f"[ERROR] Gagal membaca state {device}: {e}"
        )
        return None


def update_device_command_completed(
    device,
    desired_state
):
    """
    Menandai command sebagai completed
    setelah ESP32 benar-benar mengirim status.
    """

    try:
        result = (
            supabase
            .table("device_commands")
            .update({
                "status": "completed",
                "completed_at": now_utc(),
            })
            .eq("device", device)
            .eq("desired_state", desired_state)
            .in_(
                "status",
                ["pending", "sending", "sent"]
            )
            .execute()
        )

        return result

    except Exception as e:
        print(
            f"[ERROR] Gagal update command {device}: {e}"
        )
        return None


# ============================================================
# PARSE PESAN ESP32
# ============================================================

def parse_esp32_message(text: str):
    """
    Membaca pesan status dari ESP32.

    Contoh:
        Pintu DIBUKA
        Pintu DITUTUP
        Jemuran DIKELUARKAN
        Jemuran DITARIK
        Lampu Rumah MENYALA
        Lampu Rumah MATI
        Lampu Jalan MENYALA
        Lampu Jalan MATI
        Malam Tiba
        Pagi Tiba
        Hujan turun
        Hujan telah reda
        HUJAN! Jemuran ditarik otomatis
    """

    updates = {}

    text = text.strip()

    # --------------------------------------------------------
    # PINTU
    # --------------------------------------------------------

    if "Pintu DIBUKA" in text:
        updates["pintu"] = True

    elif "Pintu DITUTUP" in text:
        updates["pintu"] = False


    # --------------------------------------------------------
    # JEMURAN
    # --------------------------------------------------------

    if "Jemuran DIKELUARKAN" in text:
        updates["jemuran"] = True

    elif "Jemuran DITARIK" in text:
        updates["jemuran"] = False


    # --------------------------------------------------------
    # LAMPU RUMAH
    # --------------------------------------------------------

    if "Lampu Rumah MENYALA" in text:
        updates["lampu_rumah"] = True

    elif "Lampu Rumah MATI" in text:
        updates["lampu_rumah"] = False


    # --------------------------------------------------------
    # LAMPU JALAN
    # --------------------------------------------------------

    if "Lampu Jalan MENYALA" in text:
        updates["lampu_jalan"] = True

    elif "Lampu Jalan MATI" in text:
        updates["lampu_jalan"] = False


    # --------------------------------------------------------
    # SIANG / MALAM
    # --------------------------------------------------------

    if "Malam Tiba" in text:
        updates["is_dark"] = True

    elif "Pagi Tiba" in text:
        updates["is_dark"] = False


    # --------------------------------------------------------
    # HUJAN
    # --------------------------------------------------------

    if "Hujan turun" in text:
        updates["is_raining"] = True

    elif "Hujan telah reda" in text:
        updates["is_raining"] = False


    # --------------------------------------------------------
    # HUJAN -> JEMURAN OTOMATIS
    # --------------------------------------------------------

    if "HUJAN! Jemuran ditarik otomatis" in text:
        updates["is_raining"] = True
        updates["jemuran"] = False


    return updates


# ============================================================
# TELEGRAM -> SUPABASE
# ============================================================

@app.on_message(filters.chat(GROUP_CHAT_ID))
async def telegram_to_supabase(client, message):

    """
    Membaca pesan Telegram dari group.

    Hanya pesan dari ESP32_BOT_USERNAME
    yang boleh mengubah smart_city_state.
    """

    text = message.text or message.caption or ""

    if not text:
        return


    # --------------------------------------------------------
    # PARSE PESAN
    # --------------------------------------------------------

    updates = parse_esp32_message(text)

    if not updates:
        return


    # --------------------------------------------------------
    # CEK PENGIRIM
    # --------------------------------------------------------

    sender = message.from_user

    if not sender:
        print(
            "[WARN] Pesan memiliki sender kosong."
        )
        return

    sender_username = (
        sender.username or ""
    ).lstrip("@").lower()


    expected_username = (
        ESP32_BOT_USERNAME
        .lstrip("@")
        .lower()
    )


    if sender_username != expected_username:

        print(
            f"[INFO] Status diabaikan. "
            f"Sender @{sender_username}, "
            f"bukan @{expected_username}"
        )

        return


    # --------------------------------------------------------
    # UPDATE SUPABASE
    # --------------------------------------------------------

    try:

        # Update state aktual
        (
            supabase
            .table("smart_city_state")
            .update(updates)
            .eq("id", 1)
            .execute()
        )


        # ----------------------------------------------------
        # COMPLETE COMMAND
        # ----------------------------------------------------

        for device, actual_state in updates.items():

            if device not in COMMANDS:
                continue

            update_device_command_completed(
                device,
                actual_state
            )


        print(
            f"[OK] ESP32 -> Supabase: {updates}"
        )


    except Exception as e:

        print(
            f"[ERROR] Telegram -> Supabase: {e}"
        )


# ============================================================
# DASHBOARD -> TELEGRAM
# ============================================================

async def process_dashboard_commands():

    """
    Bridge:

    Dashboard
        ↓
    device_commands
        ↓
    Python Userbot
        ↓
    Telegram
        ↓
    ESP32
    """

    while True:

        try:

            # ------------------------------------------------
            # AMBIL COMMAND PENDING
            # ------------------------------------------------

            result = (
                supabase
                .table("device_commands")
                .select("*")
                .eq("status", "pending")
                .order("created_at")
                .limit(5)
                .execute()
            )


            commands = result.data or []


            # ------------------------------------------------
            # TIDAK ADA COMMAND
            # ------------------------------------------------

            if not commands:
                await asyncio.sleep(0.5)
                continue


            # ------------------------------------------------
            # PROSES COMMAND
            # ------------------------------------------------

            for command in commands:

                command_id = command["id"]

                device = command["device"]

                desired = bool(
                    command["desired_state"]
                )


                print(
                    f"[QUEUE] Command #{command_id} "
                    f"{device} -> {desired}"
                )


                # ------------------------------------------------
                # CEK APAKAH DEVICE DIDUKUNG
                # ------------------------------------------------

                telegram_command = COMMANDS.get(device)


                if not telegram_command:

                    (
                        supabase
                        .table("device_commands")
                        .update({
                            "status": "failed",
                            "error_message": (
                                f"Device '{device}' "
                                "tidak didukung ESP32."
                            )
                        })
                        .eq("id", command_id)
                        .execute()
                    )

                    print(
                        f"[ERROR] Device tidak didukung: {device}"
                    )

                    continue


                # ------------------------------------------------
                # AMBIL STATUS AKTUAL
                # ------------------------------------------------

                actual = get_current_state(device)


                # Kalau database tidak bisa dibaca,
                # jangan kirim command ke ESP32.
                if actual is None:

                    (
                        supabase
                        .table("device_commands")
                        .update({
                            "status": "failed",
                            "error_message": (
                                "Gagal membaca "
                                "smart_city_state."
                            )
                        })
                        .eq("id", command_id)
                        .execute()
                    )

                    continue


                print(
                    f"[STATE] {device}: "
                    f"actual={actual}, "
                    f"desired={desired}"
                )


                # ------------------------------------------------
                # SUDAH SESUAI
                # ------------------------------------------------

                if actual == desired:

                    (
                        supabase
                        .table("device_commands")
                        .update({
                            "status": "completed",
                            "completed_at": now_utc(),
                        })
                        .eq("id", command_id)
                        .execute()
                    )

                    print(
                        f"[SKIP] {device} sudah "
                        f"sesuai dengan desired state."
                    )

                    continue


                # ------------------------------------------------
                # STATUS SENDING
                # ------------------------------------------------

                sending_result = (
                    supabase
                    .table("device_commands")
                    .update({
                        "status": "sending"
                    })
                    .eq("id", command_id)
                    .eq("status", "pending")
                    .execute()
                )


                # ------------------------------------------------
                # KIRIM COMMAND KE TELEGRAM
                # ------------------------------------------------

                try:

                    sent = await app.send_message(
                        GROUP_CHAT_ID,
                        telegram_command
                    )


                    # --------------------------------------------
                    # SIMPAN STATUS SENT
                    # --------------------------------------------

                    (
                        supabase
                        .table("device_commands")
                        .update({
                            "status": "sent",
                            "sent_at": now_utc(),
                            "telegram_message_id": sent.id,
                        })
                        .eq("id", command_id)
                        .execute()
                    )


                    print(
                        f"[OK] Dashboard -> Telegram: "
                        f"{device} => {desired} "
                        f"via {telegram_command}"
                    )


                except Exception as send_error:

                    (
                        supabase
                        .table("device_commands")
                        .update({
                            "status": "failed",
                            "error_message": str(send_error),
                        })
                        .eq("id", command_id)
                        .execute()
                    )


                    print(
                        f"[ERROR] Telegram command "
                        f"{telegram_command}: "
                        f"{send_error}"
                    )


        except Exception as e:

            print(
                f"[ERROR] Queue dashboard: {e}"
            )


        # ----------------------------------------------------
        # LOOP DELAY
        # ----------------------------------------------------

        await asyncio.sleep(0.5)


# ============================================================
# START USERBOT
# ============================================================

async def main():

    await app.start()


    print()
    print("==============================================")
    print("       SMART CITY USERBOT ONLINE")
    print("==============================================")
    print()
    print(" Dashboard -> Supabase -> Telegram -> ESP32")
    print(" ESP32 -> Telegram -> Supabase -> Dashboard")
    print()
    print(f" Group ID       : {GROUP_CHAT_ID}")
    print(f" ESP32 Bot      : @{ESP32_BOT_USERNAME}")
    print()
    print(" Supported Commands:")
    print("   /pintu")
    print("   /jemuran")
    print("   /LampuRumah")
    print("   /LampuJalan")
    print()
    print("==============================================")
    print()


    # ========================================================
    # LOAD DIALOGS
    # ========================================================

    try:

        async for _ in app.get_dialogs():
            pass

        print("[OK] Telegram dialogs loaded.")

    except Exception as e:

        print(
            f"[WARN] get_dialogs: {e}"
        )


    # ========================================================
    # START QUEUE PROCESSOR
    # ========================================================

    queue_task = asyncio.create_task(
        process_dashboard_commands()
    )


    try:

        await idle()

    finally:

        print(
            "[INFO] Menghentikan queue processor..."
        )

        queue_task.cancel()

        try:
            await queue_task
        except asyncio.CancelledError:
            pass


        print(
            "[INFO] Menghentikan Telegram client..."
        )

        await app.stop()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(main())

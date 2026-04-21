import sqlite3
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "8731086007:AAHy-RvPoekNc12hyELGXtPakszfoNSHD4w"
DB_FILE = "licenses.db"


def get_conn():
    return sqlite3.connect(DB_FILE)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'available',
        current_user_id INTEGER,
        current_username TEXT,
        expected_end_time TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_id INTEGER NOT NULL,
        telegram_user_id INTEGER NOT NULL,
        telegram_username TEXT,
        start_time TEXT NOT NULL,
        expected_end_time TEXT NOT NULL,
        actual_end_time TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        FOREIGN KEY (license_id) REFERENCES licenses(id)
    )
    """)

    for name in ["Tekla-1", "Tekla-2", "Tekla-3"]:
        cur.execute("INSERT OR IGNORE INTO licenses (name) VALUES (?)", (name,))

    conn.commit()
    conn.close()


def now_utc():
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Tekla License Bot is ready.\n\n"
        "Commands:\n"
        "/status - show all licenses\n"
        "/reserve <license_name> <minutes>\n"
        "Example: /reserve Tekla-1 60\n"
        "/release <license_name>\n"
        "Example: /release Tekla-1\n"
        "/mylicense - show your active license"
    )
    await update.message.reply_text(text)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT name, status, current_username, expected_end_time
        FROM licenses
        ORDER BY name
    """)
    rows = cur.fetchall()
    conn.close()

    lines = ["Current license status:"]
    for name, stat, user, end_time in rows:
        if stat == "available":
            lines.append(f"• {name}: Available")
        else:
            lines.append(f"• {name}: In use by {user or 'unknown'} until {end_time}")

    await update.message.reply_text("\n".join(lines))


async def reserve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /reserve <license_name> <minutes>\n"
            "Example: /reserve Tekla-1 60"
        )
        return

    user = update.effective_user
    username = user.username or user.full_name

    license_name = " ".join(context.args[:-1])

    try:
        minutes = int(context.args[-1])
    except ValueError:
        await update.message.reply_text("Minutes must be a number.")
        return

    if minutes <= 0:
        await update.message.reply_text("Minutes must be greater than 0.")
        return

    start_time = now_utc()
    end_time = start_time + timedelta(minutes=minutes)

    conn = get_conn()
    cur = conn.cursor()

    # Check whether user already has an active license
    cur.execute("""
        SELECT name
        FROM licenses
        WHERE current_user_id = ? AND status = 'in_use'
    """, (user.id,))
    existing = cur.fetchone()

    if existing:
        conn.close()
        await update.message.reply_text(
            f"You already hold {existing[0]}. Release it first."
        )
        return

    # Check requested license
    cur.execute("""
        SELECT id, status
        FROM licenses
        WHERE name = ?
    """, (license_name,))
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("License not found.")
        return

    license_id, lic_status = row

    if lic_status != "available":
        conn.close()
        await update.message.reply_text(f"{license_name} is not available.")
        return

    # Reserve it
    cur.execute("""
        UPDATE licenses
        SET status = 'in_use',
            current_user_id = ?,
            current_username = ?,
            expected_end_time = ?
        WHERE id = ?
    """, (user.id, username, to_iso(end_time), license_id))

    cur.execute("""
        INSERT INTO sessions (
            license_id,
            telegram_user_id,
            telegram_username,
            start_time,
            expected_end_time,
            actual_end_time,
            status
        ) VALUES (?, ?, ?, ?, ?, NULL, 'active')
    """, (
        license_id,
        user.id,
        username,
        to_iso(start_time),
        to_iso(end_time)
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{license_name} reserved by {username} until {to_iso(end_time)} UTC."
    )


async def release(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage: /release <license_name>\n"
            "Example: /release Tekla-1"
        )
        return

    user = update.effective_user
    license_name = " ".join(context.args)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, current_user_id
        FROM licenses
        WHERE name = ?
    """, (license_name,))
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("License not found.")
        return

    license_id, current_user_id = row

    if current_user_id != user.id:
        conn.close()
        await update.message.reply_text(
            "You are not the current holder of this license."
        )
        return

    cur.execute("""
        UPDATE licenses
        SET status = 'available',
            current_user_id = NULL,
            current_username = NULL,
            expected_end_time = NULL
        WHERE id = ?
    """, (license_id,))

    cur.execute("""
        UPDATE sessions
        SET actual_end_time = ?, status = 'released'
        WHERE license_id = ? AND telegram_user_id = ? AND status = 'active'
    """, (to_iso(now_utc()), license_id, user.id))

    conn.commit()
    conn.close()

    await update.message.reply_text(f"{license_name} released.")


async def mylicense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT name, expected_end_time
        FROM licenses
        WHERE current_user_id = ? AND status = 'in_use'
    """, (user.id,))
    row = cur.fetchone()

    conn.close()

    if row:
        name, end_time = row
        await update.message.reply_text(
            f"You are using {name} until {end_time} UTC."
        )
    else:
        await update.message.reply_text("You do not currently hold any license.")


def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reserve", reserve))
    app.add_handler(CommandHandler("release", release))
    app.add_handler(CommandHandler("mylicense", mylicense))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
import os
import sqlite3
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "reminders.db"

load_dotenv(BASE_DIR / ".env")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

scheduler = BackgroundScheduler()


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def create_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                email TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def send_reminder(reminder_id):
    with get_connection() as connection:
        reminder = connection.execute(
            """
            SELECT id, task, email, remind_at, status
            FROM reminders
            WHERE id = ?
            """,
            (reminder_id,),
        ).fetchone()

        if reminder is None or reminder["status"] != "pending":
            return

        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            error = "EMAIL_ADDRESS or EMAIL_PASSWORD is missing."
            connection.execute(
                """
                UPDATE reminders
                SET status = 'failed', error_message = ?
                WHERE id = ?
                """,
                (error, reminder_id),
            )
            print(error)
            return

        message = EmailMessage()
        message["Subject"] = "⏰ Reminder"
        message["From"] = EMAIL_ADDRESS
        message["To"] = reminder["email"]
        message.set_content(
            f"""Hello!

This is your reminder:

{reminder["task"]}

Scheduled for: {reminder["remind_at"]}

Have a great day!
"""
        )

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                smtp.send_message(message)

            connection.execute(
                """
                UPDATE reminders
                SET status = 'sent', error_message = NULL
                WHERE id = ?
                """,
                (reminder_id,),
            )
            print(f"Reminder {reminder_id} sent.")

        except Exception as exc:
            connection.execute(
                """
                UPDATE reminders
                SET status = 'failed', error_message = ?
                WHERE id = ?
                """,
                (str(exc), reminder_id),
            )
            print(f"Reminder {reminder_id} failed: {exc}")


def schedule_reminder(reminder_id, remind_at):
    run_time = datetime.fromisoformat(remind_at)

    scheduler.add_job(
        send_reminder,
        trigger="date",
        run_date=run_time,
        args=[reminder_id],
        id=f"reminder_{reminder_id}",
        replace_existing=True,
    )


def load_pending_reminders():
    now = datetime.now()

    with get_connection() as connection:
        pending = connection.execute(
            """
            SELECT id, remind_at
            FROM reminders
            WHERE status = 'pending'
            ORDER BY remind_at
            """
        ).fetchall()

        for reminder in pending:
            run_time = datetime.fromisoformat(reminder["remind_at"])

            if run_time > now:
                schedule_reminder(reminder["id"], reminder["remind_at"])
            else:
                connection.execute(
                    """
                    UPDATE reminders
                    SET status = 'missed',
                        error_message = 'Reminder time passed while the app was offline.'
                    WHERE id = ?
                    """,
                    (reminder["id"],),
                )


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        task = request.form.get("task", "").strip()
        email = request.form.get("email", "").strip()
        remind_at = request.form.get("remind_at", "").strip()

        if not task or not email or not remind_at:
            flash("Please fill in every field.", "error")
            return redirect(url_for("home"))

        try:
            reminder_time = datetime.fromisoformat(remind_at)
        except ValueError:
            flash("That date/time is not valid.", "error")
            return redirect(url_for("home"))

        if reminder_time <= datetime.now():
            flash("Choose a time in the future.", "error")
            return redirect(url_for("home"))

        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders (task, email, remind_at)
                VALUES (?, ?, ?)
                """,
                (task, email, remind_at),
            )
            reminder_id = cursor.lastrowid

        schedule_reminder(reminder_id, remind_at)

        flash("Reminder created successfully.", "success")
        return redirect(url_for("home"))

    with get_connection() as connection:
        reminders = connection.execute(
            """
            SELECT id, task, email, remind_at, status, error_message, created_at
            FROM reminders
            ORDER BY id DESC
            """
        ).fetchall()

    return render_template("index.html", reminders=reminders)


@app.post("/delete/<int:reminder_id>")
def delete_reminder(reminder_id):
    job_id = f"reminder_{reminder_id}"

    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM reminders WHERE id = ?",
            (reminder_id,),
        )

    flash("Reminder deleted.", "success")
    return redirect(url_for("home"))


if __name__ == "__main__":
    create_database()

    if not scheduler.running:
        scheduler.start()

    load_pending_reminders()

    app.run(debug=True, use_reloader=False)

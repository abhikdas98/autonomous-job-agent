import smtplib
from email.message import EmailMessage
from app.config import settings
from app.db import connection, metrics_snapshot

def build_report():
    with connection() as c:
        rows = c.execute("SELECT title,company,location,url,score,status FROM jobs ORDER BY created_at DESC LIMIT 15").fetchall()
        pending = c.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
        submitted = c.execute("SELECT COUNT(*) FROM applications WHERE status='submitted'").fetchone()[0]
    lines = ["Autonomous Job Agent report",f"Jobs in database: {metrics_snapshot()['jobs_total']}",
             f"Pending approvals: {pending}",f"Applications recorded as submitted: {submitted}","","Recent jobs:"]
    for r in rows:
        lines.append(f"- [{r['score']:.0f}/100] {r['title']} — {r['company']} | {r['location']} | {r['status']}\n  {r['url']}")
    return "\n".join(lines)

def send_report():
    if not all([settings.smtp_host,settings.email_from,settings.email_to]):
        return {"sent":False,"reason":"SMTP_HOST, EMAIL_FROM, and EMAIL_TO must be configured."}
    msg = EmailMessage()
    msg["Subject"] = "Autonomous Job Agent — Job Search Report"
    msg["From"], msg["To"] = settings.email_from, settings.email_to
    msg.set_content(build_report())
    with smtplib.SMTP(settings.smtp_host,settings.smtp_port,timeout=20) as server:
        if settings.smtp_starttls: server.starttls()
        if settings.smtp_username: server.login(settings.smtp_username,settings.smtp_password)
        server.send_message(msg)
    return {"sent":True,"to":settings.email_to}

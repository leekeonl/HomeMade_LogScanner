"""
email_report.py  —  Send scan_count.py results via email
=========================================================

Standalone side script — does NOT modify scan_count.py.

How it works:
  1. Finds the latest HTML report and CSV files in the current folder
  2. Reads recipient list from email_list.txt
  3. Sends email with:
       - HTML report embedded in body (inline preview)
       - HTML + CSV files as attachments
       - Keyword summary in the email body

Setup (one-time):
  1. Fill in SMTP settings below (Gmail example provided)
  2. Create email_list.txt with one recipient per line
  3. Run:  python email_report.py

Gmail setup:
  - Use an App Password (not your main password)
  - Enable 2FA → Google Account → Security → App Passwords
  - Generate one for "Mail" → paste into SENDER_PASSWORD below

Outlook / Office 365:
  - SMTP_HOST = "smtp.office365.com"
  - SMTP_PORT = 587

Usage:
  # Send latest report automatically (finds most recent files)
  python email_report.py

  # Send a specific report file
  python email_report.py --report analysis_report_260514-082011.html

  # Preview without sending (dry run)
  python email_report.py --dry-run
"""

import os
import sys
import glob
import argparse
import smtplib
import csv
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email                import encoders

from colorama import init, Fore, Style
init(autoreset=True)

# ──────────────────────────────────────────────────────────────────────────────
# SMTP CONFIGURATION — fill these in
# ──────────────────────────────────────────────────────────────────────────────
SMTP_HOST     = "smtp.gmail.com"   # Gmail: smtp.gmail.com
                                   # Outlook: smtp.office365.com
                                   # Custom: your mail server
SMTP_PORT     = 587                # 587 = TLS (recommended), 465 = SSL
SENDER_EMAIL  = ""                 # your email address
SENDER_PASSWORD = ""               # Gmail: App Password (16 chars, no spaces)
                                   # Outlook: your password

EMAIL_LIST_FILE = "email_list.txt" # one recipient email per line

# ──────────────────────────────────────────────────────────────────────────────
# EMAIL CONTENT SETTINGS
# ──────────────────────────────────────────────────────────────────────────────
EMAIL_SUBJECT_PREFIX = "[Log Analysis]"   # prepended to subject
ATTACH_CSV           = True               # attach CSV files
ATTACH_HTML          = True               # attach HTML report file
EMBED_SUMMARY        = True               # include issue summary table in body


# ══════════════════════════════════════════════════════════════════════════════
# FILE DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════
def find_latest_html() -> str | None:
    """Return the most recently created analysis_report_*.html file."""
    reports = sorted(
        glob.glob("analysis_report_*.html"),
        key=os.path.getmtime,
        reverse=True
    )
    return reports[0] if reports else None


def find_latest_csvs() -> list:
    """Return all CSV files created in the last 24 hours that look like outputs."""
    patterns = ["Debuglog_output_*.csv", "Eventlog_output_*.csv",
                "LocalScan_output_*.csv"]
    found = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    # Sort by modification time, newest first
    found.sort(key=os.path.getmtime, reverse=True)
    return found


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL LIST
# ══════════════════════════════════════════════════════════════════════════════
def load_email_list() -> list:
    if not os.path.exists(EMAIL_LIST_FILE):
        print(f"{Fore.RED}Error: {EMAIL_LIST_FILE} not found.")
        print(f"{Fore.YELLOW}Create {EMAIL_LIST_FILE} with one email address per line.")
        print(f"Example:\n  engineer1@company.com\n  engineer2@company.com")
        return []
    emails = []
    with open(EMAIL_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "@" in line:
                emails.append(line)
    return emails


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY EXTRACTION  (reads CSV to build email body table)
# ══════════════════════════════════════════════════════════════════════════════
def extract_summary_from_csvs(csv_files: list) -> dict:
    """
    Returns:
        {
            "total_issues": int,
            "error_count":  int,
            "warn_count":   int,
            "top_issues":   [(error_text, count, filename), ...]  top 10
            "computers":    set of computer names
        }
    """
    all_rows    = []
    computers   = set()

    for csv_path in csv_files:
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    all_rows.append(row)
                    if row.get("ComputerName") or row.get("FileName"):
                        computers.add(row.get("ComputerName") or row.get("FileName",""))
        except Exception as e:
            print(f"{Fore.YELLOW}Warning: could not read {csv_path}: {e}")

    def sev(err):
        e = err.lower()
        if any(k in e for k in ["message not understood","exception","hard tolerance",
                                  "jit","wacskipped","abort"]): return "error"
        if any(k in e for k in ["alarm","warning","tolerance","low","below",
                                  "pulse sync"]): return "warning"
        return "info"

    error_count = sum(1 for r in all_rows if sev(r.get("Error","")) == "error")
    warn_count  = sum(1 for r in all_rows if sev(r.get("Error","")) == "warning")

    # Sort by count descending, take top 10
    try:
        sorted_rows = sorted(all_rows, key=lambda r: int(r.get("Count",0)), reverse=True)
    except Exception:
        sorted_rows = all_rows

    top_issues = []
    for r in sorted_rows[:10]:
        err   = r.get("Error","")
        count = r.get("Count","?")
        fname = r.get("FileNames","") or r.get("FileName","")
        top_issues.append((err, count, fname))

    return {
        "total_issues": len(all_rows),
        "error_count":  error_count,
        "warn_count":   warn_count,
        "top_issues":   top_issues,
        "computers":    computers,
    }


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL BODY BUILDER
# ══════════════════════════════════════════════════════════════════════════════
def build_email_body(summary: dict, report_file: str, csv_files: list,
                     scan_time: str) -> tuple:
    """Returns (plain_text, html_body)."""

    computers_str = ", ".join(sorted(summary["computers"])) if summary["computers"] else "N/A"
    report_name   = os.path.basename(report_file) if report_file else "N/A"

    # ── Plain text version ────────────────────────────────────────────────────
    plain = f"""Log Analysis Report
Generated: {scan_time}
Computers: {computers_str}

SUMMARY
  Total unique issues : {summary['total_issues']}
  Errors              : {summary['error_count']}
  Warnings            : {summary['warn_count']}

TOP ISSUES (by occurrence count)
"""
    for i, (err, count, fname) in enumerate(summary["top_issues"], 1):
        err_short = err[:100] + ("..." if len(err) > 100 else "")
        plain += f"  {i:>2}. ×{count:<5} {err_short}\n"

    plain += f"""
Report file : {report_name}
CSV files   : {', '.join(os.path.basename(c) for c in csv_files)}

Open the attached HTML file for the full interactive report.
"""

    # ── HTML version ──────────────────────────────────────────────────────────
    sev_color = {
        "error":   ("#A32D2D", "#FCEBEB"),
        "warning": ("#854F0B", "#FAEEDA"),
        "info":    ("#185FA5", "#E6F1FB"),
    }

    def sev_cls(err):
        e = err.lower()
        if any(k in e for k in ["message not understood","exception","hard tolerance",
                                  "jit","wacskipped","abort"]): return "error"
        if any(k in e for k in ["alarm","warning","tolerance","low","below",
                                  "pulse sync"]): return "warning"
        return "info"

    rows_html = ""
    for i, (err, count, fname) in enumerate(summary["top_issues"], 1):
        sc        = sev_cls(err)
        txt_color, bg_color = sev_color[sc]
        err_short = err[:120] + ("..." if len(err) > 120 else "")
        rows_html += f"""
        <tr style="background:{'#fafafa' if i%2==0 else '#ffffff'}">
          <td style="padding:8px 12px;color:#6b6a65;font-size:12px">{i}</td>
          <td style="padding:8px 12px">
            <span style="display:inline-block;background:{bg_color};color:{txt_color};
                  font-size:10px;padding:1px 7px;border-radius:4px;font-weight:600;
                  margin-right:6px">{sc.upper()}</span>
            <span style="font-size:12px;color:#1a1a18">{err_short}</span>
          </td>
          <td style="padding:8px 12px;text-align:center;font-weight:600;
               font-size:13px;color:#378ADD">×{count}</td>
        </tr>"""

    csv_badges = "".join(
        f'<span style="display:inline-block;background:#f5f4f0;border:1px solid #ddd;'
        f'border-radius:4px;padding:2px 8px;font-size:11px;margin:2px;color:#6b6a65">'
        f'📄 {os.path.basename(c)}</span>'
        for c in csv_files
    )

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f4f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:680px;margin:24px auto;background:#fff;border-radius:12px;
     overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08)">

  <!-- Header -->
  <div style="background:#1a1a18;padding:24px 28px">
    <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;
         color:rgba(255,255,255,.5);margin-bottom:4px">Log Analysis Report</div>
    <div style="font-size:20px;font-weight:600;color:#ffffff">Scan Results</div>
    <div style="font-size:12px;color:rgba(255,255,255,.6);margin-top:4px">{scan_time}</div>
  </div>

  <!-- Stats strip -->
  <div style="display:flex;background:#26251f">
    <div style="flex:1;padding:14px 20px;text-align:center;border-right:1px solid rgba(255,255,255,.06)">
      <div style="font-size:24px;font-weight:600;color:#e8e6de">{summary['total_issues']}</div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#8c8a82;margin-top:2px">Issues</div>
    </div>
    <div style="flex:1;padding:14px 20px;text-align:center;border-right:1px solid rgba(255,255,255,.06)">
      <div style="font-size:24px;font-weight:600;color:#E24B4A">{summary['error_count']}</div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#8c8a82;margin-top:2px">Errors</div>
    </div>
    <div style="flex:1;padding:14px 20px;text-align:center;border-right:1px solid rgba(255,255,255,.06)">
      <div style="font-size:24px;font-weight:600;color:#EF9F27">{summary['warn_count']}</div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#8c8a82;margin-top:2px">Warnings</div>
    </div>
    <div style="flex:1;padding:14px 20px;text-align:center">
      <div style="font-size:24px;font-weight:600;color:#e8e6de">{len(summary['computers'])}</div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#8c8a82;margin-top:2px">Computers</div>
    </div>
  </div>

  <div style="padding:24px 28px">

    <!-- Computers -->
    <div style="margin-bottom:20px">
      <div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
           color:#6b6a65;margin-bottom:6px">Computers Scanned</div>
      <div style="font-size:13px;color:#1a1a18">{computers_str}</div>
    </div>

    <!-- Top issues table -->
    <div style="margin-bottom:24px">
      <div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
           color:#6b6a65;margin-bottom:10px">Top Issues (by occurrence)</div>
      <table style="width:100%;border-collapse:collapse;border:1px solid #ebebeb;border-radius:8px;overflow:hidden">
        <thead>
          <tr style="background:#f5f4f0">
            <th style="padding:8px 12px;text-align:left;font-size:10px;font-weight:600;
                 color:#6b6a65;text-transform:uppercase;letter-spacing:.05em;width:32px">#</th>
            <th style="padding:8px 12px;text-align:left;font-size:10px;font-weight:600;
                 color:#6b6a65;text-transform:uppercase;letter-spacing:.05em">Issue</th>
            <th style="padding:8px 12px;text-align:center;font-size:10px;font-weight:600;
                 color:#6b6a65;text-transform:uppercase;letter-spacing:.05em;width:60px">Count</th>
          </tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>

    <!-- Attachments note -->
    <div style="background:#f5f4f0;border-radius:8px;padding:14px 16px;margin-bottom:20px">
      <div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
           color:#6b6a65;margin-bottom:8px">📎 Attachments</div>
      <div style="margin-bottom:4px">
        <span style="display:inline-block;background:#e8e6de;border-radius:4px;padding:2px 8px;
              font-size:11px;margin:2px;color:#1a1a18">🌐 {os.path.basename(report_file) if report_file else 'N/A'}</span>
        <span style="font-size:11px;color:#6b6a65;margin-left:4px">— Interactive HTML report (open in browser)</span>
      </div>
      <div>{csv_badges}</div>
    </div>

    <!-- Footer -->
    <div style="font-size:11px;color:#8c8a82;border-top:1px solid #ebebeb;padding-top:16px">
      Generated by scan_count.py · {scan_time}
    </div>
  </div>
</div>
</body>
</html>"""

    return plain, html


# ══════════════════════════════════════════════════════════════════════════════
# SEND EMAIL
# ══════════════════════════════════════════════════════════════════════════════
def send_email(recipients: list, subject: str,
               plain_body: str, html_body: str,
               attachments: list, dry_run: bool = False):
    """
    Send email with HTML body and file attachments.
    attachments: list of file paths
    """
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"{Fore.RED}Error: SENDER_EMAIL or SENDER_PASSWORD not set.")
        print(f"{Fore.YELLOW}Fill in the SMTP settings at the top of email_report.py")
        return False

    print(f"\n{Fore.CYAN}{'='*55}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  EMAIL REPORT")
    print(f"{'='*55}")
    print(f"{Fore.WHITE}From    : {SENDER_EMAIL}")
    print(f"{Fore.WHITE}To      : {', '.join(recipients)}")
    print(f"{Fore.WHITE}Subject : {subject}")
    print(f"{Fore.WHITE}Attach  : {len(attachments)} file(s)")
    for a in attachments:
        sz = os.path.getsize(a) / 1024
        print(f"          {os.path.basename(a)}  ({sz:.1f} KB)")

    if dry_run:
        print(f"\n{Fore.YELLOW}[DRY RUN] Email NOT sent — remove --dry-run to send")
        return True

    print(f"\n{Fore.CYAN}Connecting to {SMTP_HOST}:{SMTP_PORT} ...", end="", flush=True)

    try:
        msg = MIMEMultipart("mixed")
        msg["From"]    = SENDER_EMAIL
        msg["To"]      = ", ".join(recipients)
        msg["Subject"] = subject

        # Attach plain + HTML alternative body
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain_body, "plain", "utf-8"))
        alt.attach(MIMEText(html_body,  "html",  "utf-8"))
        msg.attach(alt)

        # Attach files
        for fpath in attachments:
            if not os.path.exists(fpath):
                print(f"\n{Fore.YELLOW}Warning: attachment not found: {fpath}")
                continue
            with open(fpath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{os.path.basename(fpath)}"'
            )
            msg.attach(part)

        # Connect and send
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipients, msg.as_string())

        print(f" {Fore.GREEN}OK")
        print(f"{Fore.GREEN}{Style.BRIGHT}✓ Email sent to {len(recipients)} recipient(s)")
        return True

    except smtplib.SMTPAuthenticationError:
        print(f"\n{Fore.RED}Authentication failed.")
        print(f"{Fore.YELLOW}Gmail: use an App Password, not your main password.")
        print(f"{Fore.YELLOW}Go to: Google Account → Security → 2FA → App Passwords")
        return False
    except smtplib.SMTPException as e:
        print(f"\n{Fore.RED}SMTP error: {e}")
        return False
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Send scan_count.py results via email"
    )
    parser.add_argument(
        "--report",
        help="Specific HTML report file to send (default: latest)",
        default=None
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview email without actually sending"
    )
    args = parser.parse_args()

    # ── Find report file ──────────────────────────────────────────────────────
    report_file = args.report or find_latest_html()
    if not report_file:
        print(f"{Fore.RED}No HTML report found.")
        print(f"{Fore.YELLOW}Run scan_count.py first, then run this script.")
        sys.exit(1)
    if not os.path.exists(report_file):
        print(f"{Fore.RED}Report file not found: {report_file}")
        sys.exit(1)

    # ── Find CSV files ────────────────────────────────────────────────────────
    csv_files = find_latest_csvs()

    # ── Load recipients ───────────────────────────────────────────────────────
    recipients = load_email_list()
    if not recipients:
        sys.exit(1)

    # ── Extract summary from CSVs ─────────────────────────────────────────────
    summary    = extract_summary_from_csvs(csv_files)
    scan_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Build email content ───────────────────────────────────────────────────
    scan_date = datetime.now().strftime("%Y-%m-%d")
    computers = ", ".join(sorted(summary["computers"])) if summary["computers"] else "Local Scan"
    subject   = (f"{EMAIL_SUBJECT_PREFIX} {scan_date} | "
                 f"{summary['total_issues']} issues | "
                 f"{summary['error_count']} errors | "
                 f"{computers}")

    plain_body, html_body = build_email_body(
        summary     = summary,
        report_file = report_file,
        csv_files   = csv_files,
        scan_time   = scan_time,
    )

    # ── Build attachment list ──────────────────────────────────────────────────
    attachments = []
    if ATTACH_HTML and report_file:
        attachments.append(report_file)
    if ATTACH_CSV:
        attachments.extend(csv_files)

    # ── Print what we found ───────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}Report  : {report_file}")
    print(f"{Fore.CYAN}CSVs    : {len(csv_files)} file(s) found")
    print(f"{Fore.CYAN}Issues  : {summary['total_issues']} "
          f"({summary['error_count']} errors, {summary['warn_count']} warnings)")

    # ── Send ──────────────────────────────────────────────────────────────────
    ok = send_email(
        recipients  = recipients,
        subject     = subject,
        plain_body  = plain_body,
        html_body   = html_body,
        attachments = attachments,
        dry_run     = args.dry_run,
    )

    if not ok and not args.dry_run:
        sys.exit(1)

    input(f"\n{Fore.CYAN}Press Enter to exit...")


if __name__ == "__main__":
    main()

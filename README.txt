================================================================================
  LOG ANALYSIS TOOL — README
  Lam Research Equipment Log Scanner & AI Analyzer
================================================================================

Last updated : May 2026
Python       : 3.10+
Dependency   : colorama  (pip install -r requirements.txt)


────────────────────────────────────────────────────────────────────────────────
  FILE OVERVIEW
────────────────────────────────────────────────────────────────────────────────

  FILE                  PURPOSE
  ──────────────────────────────────────────────────────────────────────────────
  scan_count.py         Production: FTP log collection + AI analysis
  scan_count_test.py    Testing: same as above but dummy AI (no API key needed)
  scan_local.py         Local: analyse log files already on your machine
  email_report.py       Email: send latest report to a recipient list
  cases.json            Knowledge base: past analysed cases (auto-managed)

  SUPPORT FILES (you create/edit these)
  ──────────────────────────────────────────────────────────────────────────────
  input.txt             Hostnames / IPs of equipment (scan_count.py only)
  strings.txt           Keywords to search for in logs
  email_list.txt        Email addresses to receive reports
  requirements.txt      Python package list (pip install -r requirements.txt)


────────────────────────────────────────────────────────────────────────────────
  QUICK START
────────────────────────────────────────────────────────────────────────────────

  1. Install dependency
     -------------------------------------------------
     pip install -r requirements.txt

  2. Create strings.txt  (keywords to search, one per line)
     -------------------------------------------------
     alarm
     error
     message not understood
     hard tolerance
     JIT

  3a. FTP scan  →  create input.txt  (one equipment IP per line)
     -------------------------------------------------
     192.168.1.10
     192.168.1.11

     Edit scan_count.py:
       ANTHROPIC_API_KEY = "sk-ant-..."   ← your API key

     Run:
       python scan_count.py

  3b. Local scan  →  put log files in current folder
     -------------------------------------------------
     python scan_local.py

  4. Send results by email
     -------------------------------------------------
     Edit email_report.py:
       SENDER_EMAIL    = "you@gmail.com"
       SENDER_PASSWORD = "xxxx xxxx xxxx xxxx"   ← Gmail App Password

     Create email_list.txt:
       engineer1@company.com
       manager@company.com

     Run:
       python email_report.py


================================================================================
  1. scan_count.py  —  FTP Collection + AI Analysis
================================================================================

PURPOSE
  Connects to equipment hosts via FTP, downloads yesterday's and today's log
  files, searches them for keywords, groups similar findings, and generates
  interactive HTML reports with optional Claude AI analysis.

WHEN TO USE
  - Daily routine scan of live equipment
  - Logs are on the equipment, not on your PC
  - You want to scan multiple machines in parallel

SETUP — FILES NEEDED
  ┌─────────────────┬────────────────────────────────────────────────────────┐
  │ input.txt       │ One equipment hostname or IP address per line          │
  │                 │ Example:                                               │
  │                 │   192.168.1.10                                         │
  │                 │   192.168.1.11                                         │
  │                 │   lam-tool-03.local                                    │
  ├─────────────────┼────────────────────────────────────────────────────────┤
  │ strings.txt     │ One search keyword per line (case-insensitive)         │
  │                 │ Example:                                               │
  │                 │   alarm                                                │
  │                 │   message not understood                               │
  │                 │   hard tolerance                                       │
  │                 │   JIT                                                  │
  ├─────────────────┼────────────────────────────────────────────────────────┤
  │ cases.json      │ Auto-created. Stores past analysed cases.              │
  │                 │ Do not delete — it improves AI analysis over time.     │
  └─────────────────┴────────────────────────────────────────────────────────┘

CONFIGURATION  (top of scan_count.py)
  ANTHROPIC_API_KEY = "sk-ant-..."
    Get yours at: https://console.anthropic.com/ → API Keys
    Leave blank: all other functions still work, AI analysis returns an error.

  MAX_FTP_WORKERS = 8
    How many equipment hosts are scanned in parallel.
    Increase if you have many machines and a fast network.
    Decrease if FTP connections are being refused.

  FTP credentials are hardcoded:
    Login : lam
    Pass  : 123
    Change these in _scan_host() if your equipment uses different credentials.

  CATEGORIES (log sources — edit if your paths differ):
    DebugLog  → /D/Lam/data/DebugLogs/System/{date}/   filter: .log files
    EventLog  → /D/Lam/data/EventLogs/General/         filter: yesterday's date

TIME RANGE
  Scans from: yesterday 00:00:00
  Scans to  : current run time
  Both yesterday's AND today's folders are checked.
  Today's folder is silently skipped if it doesn't exist yet.

HOW IT WORKS  (step by step)
  1. Read input.txt → list of equipment hosts
  2. Read strings.txt → list of search keywords
  3. For each host (in parallel, up to MAX_FTP_WORKERS):
       a. FTP connect → navigate to log folder
       b. List files → filter by date and extension
       c. Download new files (skip if already cached locally)
       d. Scan each file line-by-line for keywords
  4. Group similar lines (≥90% similarity) → deduplicate noise
  5. Write CSV with columns: ComputerName, Date, FileNames, Error, Count, AI_Analysis
  6. Show keyword summary table in console
  7. Ask: "Start AI analysis? (y/n)"
       y → interactive menu: pick issues to analyse
       n → generate HTML report immediately (no AI analysis)

INTERACTIVE ANALYSIS MENU
  After typing "y", a numbered list of issues appears.
  Type a number or command:

    3          → analyse issue #3
    1,5,12     → analyse issues #1, #5, and #12
    all        → analyse every issue (can take a long time with many issues)
    report     → save HTML + update CSVs right now (keeps session open)
    q          → quit (auto-saves report if any analyses were done)

  After each analysis:
    - Result is printed to console
    - You are asked: "Save as reusable case? (y/n)"
      If yes → the case is added to cases.json for future reference

OUTPUT FILES
  Debuglog_output_YYMMDD-HHMMSS.csv    ← DebugLog results
  Eventlog_output_YYMMDD-HHMMSS.csv    ← EventLog results
  analysis_report_YYMMDD-HHMMSS.html   ← Interactive HTML report
  cases.json                            ← Updated case knowledge base

  Local cache of downloaded logs:
  {hostname}/{category}/{date}/         ← Reused on next run (not re-downloaded)

HTML REPORT FEATURES
  Three tabs in the sidebar:
    Issues   → click any issue to see detail, AI analysis, related cases
    Keywords → per-keyword hit count and bar chart, click to see related issues
    Cases    → past case knowledge base, click to see details and matching issues

  Issue detail panel:
    - Severity badge (ERROR / WARNING / INFO)
    - Occurrence bar vs top issue
    - Raw log line
    - AI analysis (Root Cause / Event Sequence / Affected Components / Actions)
    - "📂 Save as Case" button (downloads updated cases.json)
    - Purple banner: related past cases from knowledge base

  Search and filter:
    - Text search across all issues
    - Filter by severity (All / Errors / Warnings / Info)
    - Keyword search in Keywords tab
    - Case search in Cases tab

RUN COMMAND
  python scan_count.py


================================================================================
  2. scan_count_test.py  —  Function Testing (No API Key Needed)
================================================================================

PURPOSE
  Identical to scan_count.py in every way except:
    - When ANTHROPIC_API_KEY is blank → returns pre-written dummy analysis
    - You can test FTP, scanning, grouping, CSV, HTML, case KB without paying
      for API calls

WHEN TO USE
  - First-time setup verification
  - Testing on a new machine or network
  - Debugging FTP or file-grabbing issues
  - Demonstrating the tool to others

HOW TO USE
  Leave ANTHROPIC_API_KEY = ""  (blank)
  Run:  python scan_count_test.py

  Everything behaves the same as scan_count.py.
  AI analysis will return a sensible dummy response instead of calling Claude.

SWITCHING TO LIVE
  When ready for real AI analysis:
    1. Fill in ANTHROPIC_API_KEY in scan_count.py (not scan_count_test.py)
    2. Use scan_count.py from that point on


================================================================================
  3. scan_local.py  —  Analyse Logs Already on Your Machine
================================================================================

PURPOSE
  Reads log files directly from a local folder — no FTP, no time range filter.
  Every file in the folder is scanned (subject to FILE_FILTER).

WHEN TO USE
  - Logs have already been copied to your PC
  - You want to review a specific set of log files
  - Investigating a past incident (not yesterday's logs)
  - Working offline / no network access to equipment

SETUP — FILES NEEDED
  ┌─────────────────┬────────────────────────────────────────────────────────┐
  │ strings.txt     │ Same format as scan_count.py                           │
  ├─────────────────┼────────────────────────────────────────────────────────┤
  │ cases.json      │ Same shared knowledge base                             │
  ├─────────────────┼────────────────────────────────────────────────────────┤
  │ log files       │ Put them in the current folder, or set LOG_FOLDER      │
  └─────────────────┴────────────────────────────────────────────────────────┘

  NO input.txt needed — there are no hosts to connect to.

CONFIGURATION  (top of scan_local.py)

  ANTHROPIC_API_KEY = "sk-ant-..."
    Same as scan_count.py. Leave blank and dummy analysis is returned.

  LOG_FOLDER = "."
    Default: current directory (wherever you run the script from).
    Change to point at a different folder:
      LOG_FOLDER = r"C:\Logs\PM03"              ← Windows path
      LOG_FOLDER = "/home/user/logs/260421"     ← Linux/Mac path

  FILE_FILTER = []
    Empty = scan ALL files in LOG_FOLDER (excluding script/output files).
    Set to limit which files are scanned:
      FILE_FILTER = [".log"]              → only files ending in .log
      FILE_FILTER = ["260513", "260514"]  → only files with these dates in name
      FILE_FILTER = ["PM03", "PM05"]      → only files with PM03 or PM05 in name
      FILE_FILTER = ["EventLog"]          → only files with EventLog in name
    A file is included if its name contains ANY item in the list.

  MAX_WORKERS = 4
    How many files are scanned in parallel.

WHAT GETS SKIPPED AUTOMATICALLY
  scan_local.py, scan_count.py, scan_count_test.py, email_report.py
  strings.txt, cases.json, requirements.txt
  Files with extensions: .py, .csv, .html, .json, .xlsx, .docx, .pdf

HOW IT WORKS
  1. Read strings.txt → keywords
  2. List all files in LOG_FOLDER that pass FILE_FILTER
  3. Scan each file in parallel for keyword matches
  4. Group similar lines (≥90% similarity)
  5. Write LocalScan_output_YYMMDD-HHMMSS.csv
  6. Show keyword summary → interactive analysis menu (same as scan_count.py)

OUTPUT FILES
  LocalScan_output_YYMMDD-HHMMSS.csv     ← all issues
  analysis_report_YYMMDD-HHMMSS.html     ← interactive HTML report

  The HTML header shows the folder path instead of a date range.
  The "Computer" column shows the filename (since there are no host names).

DIFFERENCE FROM scan_count.py
  ┌──────────────────────┬─────────────────────────┬─────────────────────────┐
  │                      │ scan_count.py            │ scan_local.py           │
  ├──────────────────────┼─────────────────────────┼─────────────────────────┤
  │ Log source           │ FTP from equipment       │ Local folder            │
  │ Time range           │ Yesterday → now          │ None (all files)        │
  │ Host list            │ input.txt                │ Not needed              │
  │ File filter          │ Date + extension         │ FILE_FILTER setting     │
  │ Parallelism          │ Per host (FTP)           │ Per file                │
  │ "Computer" column    │ Hostname / IP            │ Filename                │
  └──────────────────────┴─────────────────────────┴─────────────────────────┘

RUN COMMAND
  python scan_local.py


================================================================================
  4. email_report.py  —  Send Results by Email
================================================================================

PURPOSE
  Sends the latest HTML report and CSV files to a list of recipients.
  Runs AFTER scan_count.py or scan_local.py — reads their output files.

WHEN TO USE
  - Automatically distribute daily scan results to the team
  - Share a report with someone who doesn't have the tool installed
  - Archive results to an email inbox

SETUP

  Step 1 — Fill in SMTP settings at the top of email_report.py:

    SMTP_HOST       = "smtp.gmail.com"      ← Gmail
    SMTP_PORT       = 587                   ← TLS (recommended)
    SENDER_EMAIL    = "you@gmail.com"
    SENDER_PASSWORD = "xxxx xxxx xxxx xxxx" ← App Password (see below)

    Other providers:
      Outlook / Office 365 : smtp.office365.com  port 587
      Yahoo                : smtp.mail.yahoo.com port 587

  Step 2 — Gmail App Password (required if using Gmail):
    Google Account → Security → 2-Step Verification → App Passwords
    Create one for "Mail" → copy the 16-character password (no spaces)
    Paste into SENDER_PASSWORD above.

    Why App Password? Google blocks "less secure" sign-ins.
    An App Password is a separate password just for this script.

  Step 3 — Create email_list.txt:
    One email address per line.
    Lines starting with # are comments (ignored).

    Example:
      # Team
      engineer1@company.com
      engineer2@company.com
      # Manager
      manager@company.com

HOW IT WORKS
  1. Finds the most recently created analysis_report_*.html file
  2. Finds all Debuglog_output_*.csv and Eventlog_output_*.csv files
  3. Reads email_list.txt → recipient list
  4. Parses CSV files to build a summary (issue count, top 10 issues)
  5. Builds a styled HTML email body with stats and issue table
  6. Sends email with HTML body + report and CSV as attachments

EMAIL CONTENT
  Subject:  [Log Analysis] 2026-05-14 | 23 issues | 5 errors | Q1_Platform
  Body:
    - Dark header with scan date
    - Stats strip: total issues / errors / warnings / computers
    - Top 10 issues table with severity colour coding
    - Attachment list
  Attachments:
    - analysis_report_*.html  (open in browser for interactive view)
    - All CSV output files

SETTINGS  (top of email_report.py)
  ATTACH_CSV   = True     ← include CSV files as attachments
  ATTACH_HTML  = True     ← include HTML report as attachment
  EMAIL_SUBJECT_PREFIX = "[Log Analysis]"  ← change to your team's prefix

USAGE

  # Send using latest report (most common)
  python email_report.py

  # Send a specific report file
  python email_report.py --report analysis_report_260514-082011.html

  # Preview without actually sending (test settings)
  python email_report.py --dry-run

WORKFLOW EXAMPLE
  python scan_count.py          ← run scan (generates HTML + CSV)
  python email_report.py        ← send results to team


================================================================================
  5. cases.json  —  Case Knowledge Base
================================================================================

PURPOSE
  Stores past analysed issues with their root cause, reproduction steps,
  and key log patterns. Used by both scan_count.py and scan_local.py to:
    - Show "[N case ref]" hints next to matching issues in the console
    - Automatically inject relevant past cases into the Claude AI prompt
    - Display related cases in the HTML report with click-through links
    - Allow searching, editing, and deleting cases from the HTML report UI

FORMAT
  JSON array. Each case has these fields:

  {
    "id":           "a1b2c3d4",         ← auto-generated 8-char ID
    "title":        "L1 PM5 Shearing Pin Issue",
    "tags":         ["PR-214602", "PM5", "shearing pin", "singlePlanMotion"],
    "reproduction": "1. Configure PM5 as Single Plan\n2. Launch TM and PM3\n...",
    "log_patterns": ["MPMPositionChange", "RobotCommandSent GOTO N 3", "WaferLifter"],
    "analysis":     "singlePlanMotion change requires BOTH TM and PM to reboot...",
    "created_at":   "2026-03-31T11:32:00",
    "updated_at":   "2026-03-31T11:32:00"
  }

HOW CASES ARE MATCHED TO ISSUES
  Scoring system (higher = more relevant):
    log_patterns match in issue text  →  +10 points per match
    tags match in issue text          →   +5 points per match
    title words match in issue text   →   +2 points per word

  Top 3 matching cases are injected into the Claude AI prompt.
  Cases with 0 score are not shown.

HOW TO ADD A CASE

  Option A — From the console (after AI analysis):
    After each analysis, the script asks:
      "Save as reusable case? (y/n)"
    Type "y" and fill in:
      - Case title
      - Tags (comma-separated)
      - Reproduction steps
      - Key log patterns (comma-separated)
    The case is automatically saved to cases.json.

  Option B — From the HTML report:
    1. Click an analysed issue
    2. Click "📂 Save as Case" button in the AI Analysis panel
    3. Fill in the modal form → click Save
    4. cases.json is auto-downloaded with the new case included
    5. Replace the old cases.json with the downloaded file

  Option C — Edit cases.json directly:
    Open in any text editor.
    Copy an existing case block, change the fields, save.
    Make sure the JSON stays valid (no trailing commas, quotes balanced).

HOW TO EDIT OR DELETE A CASE

  From the HTML report:
    1. Click the "Cases" tab in the sidebar
    2. Click the case to open its detail view
    3. Click "✏ Edit" button → modify fields → Save
       OR click "Delete case" button → Confirm
    4. Download the new cases.json and replace the old one

  From cases.json directly:
    Edit or remove the case object in the JSON array.

TIPS
  - The more specific your log_patterns, the fewer false matches
  - Include PR numbers in tags so cases are searchable by PR
  - cases.json is shared between scan_count.py and scan_local.py
    (put the same file in both working directories, or symlink it)
  - Back up cases.json regularly — it accumulates institutional knowledge
  - Do not delete cases.json between runs; it grows more useful over time


================================================================================
  FULL FOLDER STRUCTURE
================================================================================

  Your working directory should look like this:

  📁 your-folder/
  ├── scan_count.py           ← main production script
  ├── scan_count_test.py      ← test/demo script
  ├── scan_local.py           ← local log analysis script
  ├── email_report.py         ← email sender
  ├── requirements.txt        ← pip dependencies
  │
  ├── input.txt               ← equipment IPs (scan_count only)
  ├── strings.txt             ← keywords to search
  ├── cases.json              ← case knowledge base (grows over time)
  ├── email_list.txt          ← recipient email addresses
  │
  └── 📁 outputs (auto-created)
      ├── Debuglog_output_YYMMDD-HHMMSS.csv
      ├── Eventlog_output_YYMMDD-HHMMSS.csv
      ├── LocalScan_output_YYMMDD-HHMMSS.csv
      ├── analysis_report_YYMMDD-HHMMSS.html
      │
      └── 📁 {hostname}/          ← FTP download cache (scan_count only)
          └── 📁 {category}/
              └── 📁 {date}/
                  └── log files...


================================================================================
  TROUBLESHOOTING
================================================================================

  "strings.txt not found"
    → Create strings.txt with at least one keyword per line.

  "input.txt not found"  (scan_count.py only)
    → Create input.txt with one equipment hostname or IP per line.

  FTP connection error / timeout
    → Check that the equipment is reachable (ping {hostname})
    → Check FTP credentials in _scan_host() (default: lam / 123)
    → Try reducing MAX_FTP_WORKERS if many connections are refused

  "No matches found"
    → The keywords in strings.txt did not appear in any log line
    → Try broader keywords (e.g. "alarm" instead of "AlarmSet")
    → Check that the correct date folder exists on the equipment

  "[ERROR] ANTHROPIC_API_KEY is not set"
    → Paste your API key into ANTHROPIC_API_KEY at the top of the script
    → Use scan_count_test.py to test without an API key

  Gmail SMTP Authentication Error
    → Do NOT use your main Gmail password
    → Create an App Password: Google Account → Security → 2FA → App Passwords
    → Make sure 2-Step Verification is enabled on your Google account

  HTML report opens blank in browser
    → Open directly in Chrome, Firefox, or Edge (not Internet Explorer)
    → The file is self-contained — no internet connection needed to view it

  cases.json parse error after manual edit
    → Common issues: trailing comma before ], missing comma between items
    → Validate with: python -c "import json; json.load(open('cases.json'))"


================================================================================
  REQUIREMENTS
================================================================================

  Python   3.10 or newer
  Package  colorama

  Install:
    pip install -r requirements.txt

  Contents of requirements.txt:
    colorama

  All other libraries used (os, ftplib, csv, json, smtplib, threading, etc.)
  are part of Python's standard library — no additional installation needed.


================================================================================
  WORKFLOW SUMMARY
================================================================================

  DAILY ROUTINE  (FTP scan)
  ─────────────────────────
  1. python scan_count.py
       → Scans all equipment in input.txt
       → Shows issues in console
       → Type numbers to analyse, or "all", or "report" to skip
  2. python email_report.py
       → Sends HTML + CSV to everyone in email_list.txt

  AD-HOC LOCAL ANALYSIS
  ─────────────────────
  1. Copy log files into a folder
  2. cd into that folder
  3. python scan_local.py
       → Scans all log files in the folder
       → Same interactive analysis menu

  FIRST-TIME SETUP / TESTING
  ──────────────────────────
  1. python scan_count_test.py  (no API key needed)
       → Verify FTP connections work
       → Verify file grabbing works
       → Verify HTML report generates correctly

================================================================================

================================================================================
                            LOG ANALYSIS TOOL
================================================================================

A Python tool that automates the manual log review workflow I used to do
by hand at work for semiconductor equipment. Built to solve a real,
daily pain point - pulling logs from multiple equipment hosts, searching
for known error patterns, and producing analysis reports - and to explore
design patterns around stdlib-only API clients, weighted scoring for case
retrieval, and offline-capable interactive HTML reports.


--------------------------------------------------------------------------------
PROBLEM
--------------------------------------------------------------------------------

Every morning at work, troubleshooting equipment issues required manually:

    1. SSH or FTP into each equipment host individually
    2. Browse through DebugLogs/ and EventLogs/ directories looking for
       yesterday's files
    3. Open each file in a text editor and grep for keywords (alarm, error,
       "Message not understood", etc.)
    4. Mentally deduplicate near-identical alarm lines that fire dozens of
       times
    5. Cross-reference with memory: "haven't we seen this PR-186313 issue
       before?"
    6. Write up findings in an email, copy-paste relevant log lines

Each machine took ~30 minutes to an hour. With 5-10 machines to check daily,
that meant ~5 hours just on log triage, before any actual analysis began.
And institutional knowledge lived in engineers' heads - when someone left,
their hard-won pattern recognition left with them.


--------------------------------------------------------------------------------
SOLUTION
--------------------------------------------------------------------------------

                                       Before              After
    --------------------------------- ------------------- ----------------------
    Time per machine                  ~30-60 min          ~3 min
    Daily triage (5-10 machines)      ~5 hours            ~10 min
    Duplicate alarm noise             Manual dedup        90% similarity grouped
    Past case recall                  Memory-dependent    Auto-matched from KB
    Report delivery                   Hand-written email  Auto-generated + sent


--------------------------------------------------------------------------------
KEY FEATURES
--------------------------------------------------------------------------------

- Multi-host FTP collection
    Scans up to 8 equipment hosts in parallel using ThreadPoolExecutor.
    Downloads only new files; cached files from previous runs are reused.

- 90% similarity grouping
    The same IntensityOutOfThresholdLimitSet alarm firing 78 times
    collapses into one entry with Count: 78. Uses Python's SequenceMatcher
    to compare line content within each computer.

- Claude AI analysis with case-aware prompting
    For each issue group, the top 3 most relevant past cases are pulled
    from the knowledge base and injected into the system prompt. AI output
    references prior cases by ID and PR number.

- Interactive standalone HTML report
    Three-tab sidebar (Issues / Keywords / Cases), severity filtering,
    search, click-through navigation between issues and matching past
    cases. Single file, no internet required to view.

- Case Knowledge Base (cases.json)
    Analyzed issues can be saved as reusable cases with reproduction
    steps, tags (PR numbers), and key log patterns. Future scans
    auto-match against this KB.

- Email reporting
    Separate email_report.py finds the latest scan output and sends a
    styled HTML summary + CSV attachments to an email_list.txt recipient
    list.

- API-free test mode
    scan_count_test.py returns hand-written dummy analyses when no API
    key is set, so FTP, grouping, HTML, and KB logic can be verified
    without spending tokens.


--------------------------------------------------------------------------------
DESIGN DECISIONS
--------------------------------------------------------------------------------

A few choices worth calling out:

- Pure stdlib Anthropic client (urllib only, no SDK)
    The equipment PCs at work are partially air-gapped and often can't
    install the anthropic Python package. Writing the API client against
    urllib.request means one less thing that can go wrong on deployment,
    and the only required dependency is colorama for terminal colors.

- Weighted keyword scoring for case retrieval, not embeddings
    The knowledge base is ~50 cases; semantic embeddings would be overkill
    and add a vector database dependency. Instead a simple scoring rule
    (log_pattern match = +10, tag match = +5, title word = +2) retrieves
    the top 3 candidates fast and offline. Surprisingly effective because
    engineers naturally write specific patterns in tags.

- Two-stage pipeline (scan -> optional analyze)
    Scanning produces CSV and finds issues; AI analysis is a separate
    interactive step the user opts into per-issue. This keeps the fast
    path fast - even with no API key, scanning + HTML report works
    completely.

- Standalone single-file HTML report (no web server)
    The HTML embeds all issue and case data as JSON literals in a <script>
    block, with vanilla JS for rendering. No build step, no CDN, no
    server. Engineers can email the HTML file and the recipient opens it
    directly.

- Case KB as plain JSON (not SQLite)
    Cases are human-readable, git-trackable, and editable in any text
    editor. The HTML report can even download an updated cases.json via
    a Blob URL when you add/edit/delete cases from the UI - no backend
    needed.

- Separate test variant rather than a feature flag
    scan_count_test.py is its own file with dummy analysis baked in.
    Keeps production scan_count.py clean - no if TEST_MODE: branches -
    and lets newcomers run the test version on day one without an API
    key.


--------------------------------------------------------------------------------
SCREENSHOTS
--------------------------------------------------------------------------------

(Add screenshots here. The HTML report's Issues tab and the Cases tab
with a matched case are the two views worth showing.)


--------------------------------------------------------------------------------
REQUIREMENTS
--------------------------------------------------------------------------------

- Python 3.10 or newer
- colorama

    pip install -r requirements.txt

All other libraries (urllib, ftplib, csv, json, smtplib, threading,
concurrent.futures) are part of Python's standard library.


--------------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------------

Daily routine (FTP scan):

    python scan_count.py        # collects from equipment, generates report
    python email_report.py      # sends report to team


Ad-hoc local analysis:

    # Copy log files into a folder, cd there, then:
    python scan_local.py


First-time setup / testing without API key:

    python scan_count_test.py   # dummy AI analysis, full pipeline otherwise


Interactive analysis menu (after scanning, the script lists issues 1-N):

    Input       Action
    ---------   ------------------------------------------------------------
    3           Analyze issue #3 only
    1,5,12      Analyze issues #1, #5, and #12
    all         Analyze every issue
    report      Save HTML report and CSV right now (keep session open)
    q           Quit (auto-saves report if any analyses were done)


--------------------------------------------------------------------------------
FILE STRUCTURE
--------------------------------------------------------------------------------

    LogAnalysisTool/
      scan_count.py           - Production: FTP collection + AI analysis
      scan_count_test.py      - Test variant: dummy analysis, no API key needed
      scan_local.py           - Local: analyze log files already on your machine
      email_report.py         - Standalone email sender for scan outputs
      cases.json              - Case knowledge base (auto-grows over time)
      input.txt               - Equipment hostnames/IPs (scan_count only)
      strings.txt             - Keywords to search for
      email_list.txt          - Email recipients
      requirements.txt
      README.md


Two-stage pipeline architecture:

    scan_logs()                                # FTP/local -> CSV + issue list
        |
        v
    [Interactive menu: pick issues to analyze]
        |
        v
    analyze_issue(entry)                       # Calls Claude with case KB
        -> find_similar_cases(error_text)      # Weighted score top-3
        -> call_claude_api(prompt + cases)     # urllib -> Anthropic API
        |
        v
    generate_html_report() + update_csv()      # Standalone HTML + CSV


--------------------------------------------------------------------------------
KEY ALGORITHMS
--------------------------------------------------------------------------------

1. Similarity grouping (is_similar)

    Near-identical log lines get collapsed using SequenceMatcher:

        SequenceMatcher(None, line_a, line_b).ratio() >= 0.9

    Two lines that differ only by a timestamp or wafer ID - but are
    otherwise the same alarm - get grouped into one entry with an
    incrementing count. This is what turns 78 raw
    IntensityOutOfThresholdLimit hits into a single issue group.


2. Case retrieval (find_similar_cases)

    For each new issue, scan the KB and score every case:

        score  += +10  for each log_pattern that appears in the issue text
        score  +=  +5  for each tag that appears in the issue text
        score  +=  +2  for each title word (>3 chars) in the issue text

    Top 3 cases by score are injected into the AI prompt. Cases with
    score 0 are not shown. The weights reflect what matters most: a
    log_pattern match is a strong signal (engineers write these from
    real error strings), a tag match is medium (PR numbers, component
    names), a title word is weak (may be incidental).


3. Severity classification

    A small keyword-based heuristic for color-coding in the report:

        error:    "message not understood", "exception", "hard tolerance"
        warning:  "alarm", "warning", "warningset", "tolerance",
                  "low", "below", "pulse sync", "idex"
        info:     everything else

    Intentionally simple - not trying to be smarter than the engineer
    reading the report. The severity is a hint, not a verdict.


--------------------------------------------------------------------------------
KNOWLEDGE BASE FORMAT
--------------------------------------------------------------------------------

cases.json is a list of case objects:

    [
      {
        "id":           "h1b1p1r1",
        "title":        "JIT Stuck due to WAC Disabled Configuration",
        "tags":         ["PR-186313", "Scheduler", "WAC", "JIT"],
        "reproduction": "1. Set flow with WAC\n2. Select WAC as disabled...",
        "log_patterns": ["receiveDisablingTags", "#WAC", "JIT misjudgment"],
        "analysis":     "Flow with WAC disabled caused JIT misjudgment...",
        "created_at":   "2025-04-16T10:00:00",
        "updated_at":   "2025-04-16T10:00:00"
      }
    ]


Three ways to add a case:

    1. From the console - after each AI analysis, the script asks "Save as
       reusable case? (y/n)" and prompts for fields interactively.

    2. From the HTML report - click "Save as Case" on an analyzed issue,
       fill the modal, save. The report downloads an updated cases.json
       you replace on disk.

    3. Direct edit - open cases.json in any text editor. Just keep the
       JSON valid (no trailing commas).


--------------------------------------------------------------------------------
ROADMAP
--------------------------------------------------------------------------------

- Auto-scheduled daily runs (cron / Windows Task Scheduler guide)
- [ACTION NEEDED] email subject when scan matches known KB cases
- Cross-tool comparison view (issues appearing on 2+ machines)
- Time-axis visualization (when did alarms cluster during the day)
- Slack / Teams webhook posting (alongside email)
- Multi-day trend analysis (recurring issues over a week)


--------------------------------------------------------------------------------
AUTHOR
--------------------------------------------------------------------------------

Written by Matthew Lee.

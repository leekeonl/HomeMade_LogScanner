import os
import ftplib
import csv
import json
import uuid
import urllib.request
import urllib.error
import html as html_mod
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from colorama import init, Fore, Style

init(autoreset=True)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────
INPUT_FILE   = "input.txt"       # one hostname/IP per line
STRINGS_FILE = "strings.txt"     # one keyword per line
CASES_FILE   = "cases.json"

# Anthropic API Key — get yours at https://console.anthropic.com/ → API Keys
ANTHROPIC_API_KEY = ""  # Leave blank for test mode (dummy analysis)
ANTHROPIC_MODEL   = "claude-sonnet-4-20250514"

MAX_FTP_WORKERS   = 8
MAX_SIMILAR_CASES = 3

# ──────────────────────────────────────────────────────────────────────────────
# TIME RANGE  →  Yesterday 00:00:00  ~  Right now
# ──────────────────────────────────────────────────────────────────────────────
now       = datetime.now()
yesterday = now - timedelta(days=1)

yesterday_str       = yesterday.strftime("%y%m%d")       # 260420
yesterday_date_long = yesterday.strftime("%Y%m%d")       # 20260420
today_str           = now.strftime("%y%m%d")
current_time        = now.strftime("%y%m%d-%H%M%S")

# ──────────────────────────────────────────────────────────────────────────────
# CATEGORIES
# ──────────────────────────────────────────────────────────────────────────────
categories = [
    {
        "name": "DebugLog",
        "remote_path": f"/D/Lam/data/DebugLogs/System/{yesterday_str}",
        "output_file": f"Debuglog_output_{current_time}.csv",
        "filter": ".log"
    },
    {
        "name": "EventLog",
        "remote_path": "/D/Lam/data/EventLogs/General",
        "output_file": f"Eventlog_output_{current_time}.csv",
        "filter": yesterday_date_long
    }
]

local_file_cache = {}
_cache_lock      = threading.Lock()
_print_lock      = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# CASE KNOWLEDGE BASE
# ══════════════════════════════════════════════════════════════════════════════
def load_cases() -> list:
    if not os.path.exists(CASES_FILE):
        return []
    try:
        with open(CASES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_cases(cases: list):
    with open(CASES_FILE, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)

def add_case(title, tags, reproduction, log_patterns, analysis) -> dict:
    cases = load_cases()
    now_s = datetime.now().isoformat(timespec="seconds")
    case  = {
        "id": str(uuid.uuid4())[:8], "title": title, "tags": tags,
        "reproduction": reproduction, "log_patterns": log_patterns,
        "analysis": analysis, "created_at": now_s, "updated_at": now_s,
    }
    cases.append(case)
    save_cases(cases)
    return case

def find_similar_cases(issue_text, top_n=MAX_SIMILAR_CASES) -> list:
    cases = load_cases()
    if not cases: return []
    il = issue_text.lower()
    scored = []
    for c in cases:
        score = 0
        for pat in c.get("log_patterns", []):
            if pat.lower() in il: score += 10
        for tag in c.get("tags", []):
            if tag.lower() in il: score += 5
        for word in c.get("title", "").lower().split():
            if len(word) > 3 and word in il: score += 2
        if score > 0: scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_n]]

def format_cases_for_prompt(cases) -> str:
    if not cases: return ""
    lines = ["[RELEVANT PAST CASES]"]
    for i, c in enumerate(cases, 1):
        lines.append(f"\n--- Case #{i}: {c['title']} ---")
        if c.get("reproduction"): lines.append(f"Reproduction:\n{c['reproduction']}")
        if c.get("log_patterns"): lines.append(f"Key patterns: {', '.join(c['log_patterns'])}")
        if c.get("analysis"):     lines.append(f"Analysis:\n{c['analysis']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def is_similar(a: str, b: str, threshold: float = 0.9) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= threshold

def tprint(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# KEYWORD SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
def print_keyword_summary(keyword_hits: dict, keyword_computers: dict):
    print(f"\n{'═'*65}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  KEYWORD SUMMARY")
    print(f"{'═'*65}")
    print(f"  {'Keyword':<35} {'Hits':>6}  {'PCs':>4}  Bar")
    print(f"  {'─'*35}  {'─'*6}  {'─'*4}  {'─'*15}")
    if not keyword_hits:
        print(f"  {Fore.MAGENTA}No hits."); print(f"{'═'*65}"); return
    max_h = max(keyword_hits.values()) or 1
    for kw, count in sorted(keyword_hits.items(), key=lambda x: -x[1]):
        bar   = "█" * max(1, int(count / max_h * 15)) if count else ""
        pcs   = len(keyword_computers.get(kw, set()))
        color = Fore.RED if count > max_h * 0.6 else \
                Fore.YELLOW if count > max_h * 0.2 else Fore.GREEN
        kd    = (kw[:33] + "..") if len(kw) > 35 else kw
        print(f"  {Fore.WHITE}{kd:<35} {color}{count:>6}  "
              f"{Fore.CYAN}{pcs:>4}  {color}{bar}{Style.RESET_ALL}")
    print(f"{'═'*65}\n")


# ══════════════════════════════════════════════════════════════════════════════
# ANTHROPIC API  (TEST VERSION — dummy analysis when API key is blank)
# ══════════════════════════════════════════════════════════════════════════════
def _dummy_analysis(user_message: str) -> str:
    """
    Returns a pre-written dummy analysis so all other functions
    (FTP, scan, grouping, CSV, HTML, case KB) can be tested without an API key.
    Keyed on keywords found in the issue/log text.
    """
    lower = user_message.lower()

    if "shearing pin" in lower or "singleplanmotion" in lower:
        return (
            "## Root Cause\n"
            "singlePlanMotion option change requires BOTH TM and PM to reboot. "
            "Log shows only PM was rebooted, not TM, causing robot misalignment.\n\n"
            "## Event Sequence\n"
            "- singlePlanMotion changed → PM rebooted only\n"
            "- Robot GOTO N 3 command sent to misaligned position\n"
            "- WaferLifter position mismatch → shearing pin fault triggered\n\n"
            "## Affected Components\n"
            "- TM (not rebooted — root cause)\n"
            "- PM WaferLifter\n"
            "- Robot arm alignment\n\n"
            "## Recommended Actions\n"
            "- Reboot both TM and PM after singlePlanMotion config change\n"
            "- Add validation check that enforces dual reboot requirement\n\n"
            "## Related Past Cases\n"
            "Matches Case [a1b2c3d4] L1 PM5 Shearing Pin Issue — identical root cause."
        )

    if "jit" in lower or "wac" in lower or "jitstuck" in lower:
        return (
            "## Root Cause\n"
            "JIT scheduler misjudgment caused by WAC disabled configuration "
            "or abort recovery not handled at Step 0.\n\n"
            "## Event Sequence\n"
            "- WAC disabled or abort triggered at Step 0\n"
            "- JIT rule failed to check receiveDisablingTags #WAC\n"
            "- Next wafer advancement blocked\n\n"
            "## Affected Components\n"
            "- JIT scheduler (JITCanSendNextWafer logic)\n"
            "- WAC cycle management\n\n"
            "## Recommended Actions\n"
            "- Update JIT rule to check receiveDisablingTags #WAC\n"
            "- Handle abort recovery cases at Step 0\n\n"
            "## Related Past Cases\n"
            "Matches Cases [h1b1p1r1][h1b1p1r2][h1b1p1r3] — JIT/WAC family."
        )

    if "aei" in lower or "baseline" in lower or "asnumber" in lower:
        return (
            "## Root Cause\n"
            "HydraBaselineAEIFilename left empty in recipe. "
            "AEI map computation calls #asNumber on nil baseline reference.\n\n"
            "## Event Sequence\n"
            "- Recipe loaded without baseline AEI file\n"
            "- PreEtch Step 31 triggers AEI comparison\n"
            "- nil.asNumber → Message not understood: #asNumber\n\n"
            "## Affected Components\n"
            "- HydraController AEI module\n"
            "- HydraPredictedDifferenceAEIMap\n\n"
            "## Recommended Actions\n"
            "- Register baseline AEI file in recipe config\n"
            "- Or disable HydraAutoCalEnable if AEI not needed\n\n"
            "## Related Past Cases\n"
            "Matches Case [e5f6g7h8] L1 PM3 AEI Baseline File Missing."
        )

    if "vc job" in lower or "preconditionwac" in lower or "pick and place" in lower:
        return (
            "## Root Cause\n"
            "VC job wafer started Pick and Place before PreConditionWAC tag was set, "
            "causing scheduler abort when slot door timeout prevention kicked in.\n\n"
            "## Event Sequence\n"
            "- AEW started manually (PreWac = 60s)\n"
            "- VC job Pick/Place started before #PreConditionWAC\n"
            "- Scheduler aborted operations to prevent slot door timeout\n\n"
            "## Affected Components\n"
            "- JIT scheduler (JITValueForPickVC)\n"
            "- PreConditionWAC tag timing\n\n"
            "## Recommended Actions\n"
            "- Ensure #PreConditionWAC is set before Pick/Place starts\n\n"
            "## Related Past Cases\n"
            "Matches Case [f1c1p1r1] VC Job Wafer Premature Pick and Place."
        )

    # Generic fallback — still exercising the full pipeline
    words = [w for w in user_message.split() if len(w) > 6][:4]
    return (
        f"## Root Cause\n"
        f"[DUMMY] Issue keywords detected: {' '.join(words)}\n\n"
        f"## Event Sequence\n"
        f"- Log pattern matched → issue surfaced in output\n\n"
        f"## Affected Components\n"
        f"- See raw log line above for component details\n\n"
        f"## Recommended Actions\n"
        f"- Set ANTHROPIC_API_KEY in scan_count.py to get real AI analysis\n"
        f"- Use this file (scan_count_test.py) for function testing only"
    )


def call_claude_api(system_prompt: str, user_message: str) -> str:
    # ── TEST MODE: no API key needed ──────────────────────────────────────────
    if not ANTHROPIC_API_KEY:
        print(f"  {Fore.YELLOW}[TEST MODE] Dummy analysis returned (no API key)")
        return _dummy_analysis(user_message)

    # ── LIVE MODE: real Anthropic API call (identical to scan_count.py) ───────
    url     = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type":      "application/json",
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model":      ANTHROPIC_MODEL,
        "max_tokens": 2048,
        "system":     system_prompt,
        "messages":   [{"role": "user", "content": user_message}]
    }
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        return f"[API HTTPError {e.code}] {e.read().decode('utf-8','ignore')}"
    except Exception as e:
        return f"[API Error] {e}"


# ══════════════════════════════════════════════════════════════════════════════
# AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
def analyze_issue(entry: dict, cat_name: str) -> str:
    computer   = entry["ComputerName"]
    issue_text = entry["Error"]
    file_names = entry["FileNames"]

    parts = []
    for fname in file_names:
        lp = local_file_cache.get((computer, cat_name, fname))
        if lp and os.path.exists(lp):
            with open(lp, "rb") as f:
                parts.append(f"=== FILE: {fname} ===\n"
                              + f.read().decode("latin-1", errors="ignore"))
        else:
            parts.append(f"=== FILE: {fname} ===\n[not found]")

    combined = "\n\n".join(parts)
    if len(combined) > 100_000:
        half     = 50_000
        combined = combined[:half] + f"\n\n...[{len(combined)-100_000} chars omitted]...\n\n" + combined[-half:]

    similar   = find_similar_cases(issue_text)
    cases_sec = format_cases_for_prompt(similar)
    if similar:
        print(f"  {Fore.CYAN}→ Injecting {len(similar)} past case(s): "
              + ", ".join(f'"{c["title"]}"' for c in similar))

    system_prompt = (
        "You are an expert log analysis engineer for Lam Research semiconductor equipment. "
        "Analyse the log and explain WHY the error occurred. "
        "Use headers: ## Root Cause / ## Event Sequence / ## Affected Components / ## Recommended Actions"
        + ("\n## Related Past Cases" if similar else "")
    )
    user_message = (
        f"[Issue]\n{issue_text}\n\n[Computer]\n{computer}\n\n"
        + (cases_sec + "\n\n" if cases_sec else "")
        + f"[Log]\n{combined}"
    )
    return call_claude_api(system_prompt, user_message)


# ══════════════════════════════════════════════════════════════════════════════
# SAVE CASE
# ══════════════════════════════════════════════════════════════════════════════
def prompt_save_case(entry: dict, analysis_text: str):
    print(f"\n{Fore.CYAN}{'─'*60}")
    ans = input(f"{Fore.CYAN}Save as reusable case? (y/n): {Style.RESET_ALL}").strip().lower()
    if ans != "y": return
    title = input(f"{Fore.WHITE}Case title: ").strip()
    if not title: print(f"{Fore.YELLOW}Skipped."); return
    tags    = [t.strip() for t in input(f"{Fore.WHITE}Tags (comma): ").strip().split(",") if t.strip()]
    print(f"{Fore.WHITE}Reproduction steps (blank line ×2 to finish):")
    lines = []
    while True:
        l = input()
        if l == "" and lines and lines[-1] == "": break
        lines.append(l)
    patterns = [p.strip() for p in input(f"{Fore.WHITE}Log patterns (comma): ").strip().split(",") if p.strip()]
    patterns = list(dict.fromkeys(patterns + [w for w in entry["Error"].split() if len(w) > 6][:3]))
    case = add_case(title, tags, "\n".join(lines).strip(), patterns, analysis_text)
    print(f"\n{Fore.GREEN}✓ Saved — ID: {case['id']}")


# ══════════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ══════════════════════════════════════════════════════════════════════════════
def _e(s): return html_mod.escape(str(s))

def generate_html_report(analyses, report_path, scan_range_from, scan_range_to,
                         total_lines, keyword_hits=None, keyword_computers=None):

    def sev(a):
        e = a["Error"].lower()
        if any(k in e for k in ["message not understood","exception","hard tolerance"]): return "error"
        if any(k in e for k in ["alarm","warning","warningset","tolerance","low",
                                  "below","pulse sync","idex"]): return "warning"
        return "info"

    for a in analyses: a["_sev"] = sev(a)
    total_issues = len(analyses)
    err_count    = sum(1 for a in analyses if a["_sev"] == "error")
    warn_count   = sum(1 for a in analyses if a["_sev"] == "warning")

    kw_hits = keyword_hits or {}
    kw_pcs  = {k: len(v) for k, v in (keyword_computers or {}).items()}
    kw_max  = max(kw_hits.values(), default=1) or 1
    kw_js   = json.dumps([
        {"kw": k, "hits": v, "computers": kw_pcs.get(k, 0), "pct": round(v/kw_max*100)}
        for k, v in sorted(kw_hits.items(), key=lambda x: -x[1])
    ], ensure_ascii=False)

    cases    = load_cases()
    js_cases = json.dumps(cases, ensure_ascii=False)
    js_issues= json.dumps([
        {"id": i, "computer": a["ComputerName"], "category": a["Category"],
         "date": a["Date"], "files": a["FileNames"], "error": a["Error"],
         "count": a["Count"], "sev": a["_sev"], "analysis": a.get("Analysis",""),
         "keywords": [k for k in kw_hits if k.lower() in a["Error"].lower()]}
        for i, a in enumerate(analyses)
    ], ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Log Analysis — {_e(scan_range_from[:10])}</title>
<style>
:root{{--bg:#f5f4f0;--surface:#fff;--border:rgba(0,0,0,.10);--text:#1a1a18;--muted:#6b6a65;
  --accent:#1a1a18;--err:#E24B4A;--err-bg:#FCEBEB;--err-txt:#A32D2D;
  --warn:#EF9F27;--warn-bg:#FAEEDA;--warn-txt:#854F0B;
  --info:#378ADD;--info-bg:#E6F1FB;--info-txt:#185FA5;
  --green:#1a7a4a;--green-bg:#eaf3de;--purple:#7c3aed;
  --purple-bg:#ede9fe;--purple-txt:#5b21b6;--mono:'Consolas',monospace;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1c1b18;--surface:#26251f;--border:rgba(255,255,255,.08);
  --text:#e8e6de;--muted:#8c8a82;--accent:#e8e6de;--err-bg:#2d1515;--err-txt:#f09595;
  --warn-bg:#2d2010;--warn-txt:#FAC775;--info-bg:#0d1e30;--info-txt:#85B7EB;
  --green-bg:#0d2018;--purple-bg:#1e1535;--purple-txt:#c4b5fd;}}}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);font-size:14px;line-height:1.6;}}
.shell{{display:grid;grid-template-columns:320px 1fr;min-height:100vh;}}
.sidebar{{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow:hidden;}}
.main{{padding:30px 34px;min-height:100vh;}}
.header{{padding:16px 16px 10px;border-bottom:1px solid var(--border);}}
.header h1{{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);}}
.header .range{{font-size:11px;color:var(--muted);}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);border-bottom:1px solid var(--border);}}
.stat{{background:var(--surface);padding:9px 10px;}}
.stat .sv{{font-size:17px;font-weight:600;}} .stat .sl{{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}}
.tab-bar{{display:flex;border-bottom:1px solid var(--border);}}
.tab{{flex:1;padding:8px 0;font-size:11px;font-weight:500;text-align:center;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;transition:all .15s;}}
.tab:hover{{color:var(--text);}} .tab.active{{color:var(--text);border-bottom-color:var(--accent);}}
.search-wrap{{padding:8px 12px;border-bottom:1px solid var(--border);}}
.search-wrap input{{width:100%;padding:6px 10px;font-size:12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);outline:none;}}
.filters{{display:flex;gap:5px;padding:7px 12px;border-bottom:1px solid var(--border);flex-wrap:wrap;}}
.pill{{font-size:10px;padding:2px 8px;border-radius:20px;cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--muted);transition:all .15s;}}
.pill:hover{{background:var(--bg);}} .pill.active{{color:var(--text);border-color:var(--accent);background:var(--bg);font-weight:500;}}
.panel{{flex:1;overflow-y:auto;}}
.panel::-webkit-scrollbar{{width:4px;}} .panel::-webkit-scrollbar-thumb{{background:var(--border);border-radius:2px;}}
.list-item{{padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);transition:background .1s;position:relative;}}
.list-item:hover{{background:var(--bg);}} .list-item.active{{background:var(--bg);}}
.list-item.active::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent);}}
.item-dot{{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;vertical-align:middle;margin-top:-1px;}}
.item-title{{font-size:12px;font-weight:500;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.item-meta{{font-size:10px;color:var(--muted);margin-top:2px;display:flex;gap:7px;align-items:center;flex-wrap:wrap;}}
.badge{{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;font-weight:500;}}
.badge.error{{background:var(--err-bg);color:var(--err-txt);}} .badge.warning{{background:var(--warn-bg);color:var(--warn-txt);}}
.badge.info{{background:var(--info-bg);color:var(--info-txt);}} .badge.kw{{background:var(--green-bg);color:var(--green);}}
.badge.case{{background:var(--purple-bg);color:var(--purple-txt);}}
.count-chip{{font-size:10px;background:var(--bg);border:1px solid var(--border);padding:1px 5px;border-radius:4px;color:var(--muted);}}
.detail-header{{margin-bottom:18px;}} .detail-header h2{{font-size:15px;font-weight:600;line-height:1.4;margin-bottom:8px;}}
.meta-row{{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:6px;align-items:center;}}
.meta-chip{{font-size:11px;padding:3px 9px;border-radius:5px;border:1px solid var(--border);color:var(--muted);background:var(--surface);}}
.bar-wrap{{margin:16px 0;}} .bar-wrap h3{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:8px;}}
.bar-row{{display:flex;align-items:center;gap:8px;margin-bottom:5px;}}
.bar-label{{font-size:11px;color:var(--muted);width:175px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.bar-track{{flex:1;height:10px;background:var(--bg);border-radius:3px;overflow:hidden;}}
.bar-fill{{height:100%;border-radius:3px;}} .bar-count{{font-size:11px;font-weight:500;width:30px;text-align:right;}}
.log-box{{background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:10px 13px;font-family:var(--mono);font-size:11px;color:var(--muted);word-break:break-all;line-height:1.6;max-height:90px;overflow-y:auto;margin:12px 0;}}
.card{{border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-top:18px;}}
.card-header{{padding:9px 15px;background:var(--bg);border-bottom:1px solid var(--border);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);display:flex;align-items:center;justify-content:space-between;}}
.card-body{{padding:15px;}}
.section-title{{font-size:11px;font-weight:700;color:var(--text);margin:14px 0 4px;text-transform:uppercase;letter-spacing:.04em;border-top:1px solid var(--border);padding-top:12px;}}
.section-title:first-child{{border-top:none;padding-top:0;margin-top:0;}}
.bullet{{font-size:13px;padding:2px 0 2px 10px;color:var(--text);}}
.body-line{{font-size:13px;color:var(--text);padding:1px 0;}}
.spacer{{height:5px;}} .no-analysis{{font-size:13px;color:var(--muted);font-style:italic;padding:8px 0;}}
.save-btn{{font-size:11px;padding:4px 10px;border-radius:5px;cursor:pointer;border:1px solid var(--purple-txt);background:var(--purple-bg);color:var(--purple-txt);font-weight:500;}}
.kw-item{{padding:9px 14px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s;}}
.kw-item:hover{{background:var(--bg);}} .kw-item.active{{background:var(--bg);}}
.kw-bar-track{{width:100%;height:5px;background:var(--bg);border-radius:3px;overflow:hidden;margin:3px 0;}}
.kw-bar-fill{{height:100%;border-radius:3px;background:var(--info);}}
.kw-meta{{display:flex;gap:10px;font-size:10px;color:var(--muted);}}
.case-tag{{display:inline-block;font-size:9px;padding:1px 5px;border-radius:3px;background:var(--purple-bg);color:var(--purple-txt);font-weight:500;}}
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:100;align-items:center;justify-content:center;}}
.modal-overlay.open{{display:flex;}}
.modal{{background:var(--surface);border-radius:10px;padding:24px;width:540px;max-width:95vw;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,.25);}}
.modal h2{{font-size:15px;font-weight:600;margin-bottom:16px;}}
.form-row{{margin-bottom:13px;}}
.form-row label{{display:block;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:4px;}}
.form-row input,.form-row textarea{{width:100%;padding:8px 10px;font-size:13px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);outline:none;font-family:inherit;resize:vertical;}}
.modal-actions{{display:flex;gap:8px;justify-content:flex-end;margin-top:18px;}}
.btn{{padding:7px 16px;border-radius:6px;font-size:12px;font-weight:500;cursor:pointer;border:1px solid var(--border);}}
.btn.primary{{background:var(--accent);color:var(--surface);border-color:var(--accent);}}
.btn.danger{{background:var(--err-bg);color:var(--err-txt);border-color:var(--err-txt);}}
.empty{{display:flex;flex-direction:column;align-items:center;justify-content:center;height:60vh;color:var(--muted);gap:8px;}}
@media(max-width:700px){{.shell{{grid-template-columns:1fr;}}.sidebar{{position:static;height:auto;max-height:55vh;}}.main{{padding:18px;}}}}
</style>
</head>
<body>
<div class="shell">
<aside class="sidebar">
  <div class="header">
    <h1>Log Analysis Report</h1>
    <div class="range">{_e(scan_range_from)} → {_e(scan_range_to)}</div>
  </div>
  <div class="stats">
    <div class="stat"><div class="sv">{total_issues}</div><div class="sl">Issues</div></div>
    <div class="stat"><div class="sv" style="color:var(--err)">{err_count}</div><div class="sl">Errors</div></div>
    <div class="stat"><div class="sv" style="color:var(--warn)">{warn_count}</div><div class="sl">Warns</div></div>
    <div class="stat"><div class="sv">{total_lines:,}</div><div class="sl">Lines</div></div>
  </div>
  <div class="tab-bar">
    <div class="tab active" id="tab-issues"   onclick="switchTab('issues')">Issues</div>
    <div class="tab"        id="tab-keywords" onclick="switchTab('keywords')">Keywords</div>
    <div class="tab"        id="tab-cases"    onclick="switchTab('cases')">Cases</div>
  </div>
  <div id="panel-issues" style="display:flex;flex-direction:column;flex:1;overflow:hidden;">
    <div class="search-wrap"><input type="text" id="search" placeholder="Search issues…" oninput="applyFilters()"></div>
    <div class="filters">
      <button class="pill active" data-sev="all"     onclick="setSev(this)">All</button>
      <button class="pill"        data-sev="error"   onclick="setSev(this)">Errors</button>
      <button class="pill"        data-sev="warning" onclick="setSev(this)">Warnings</button>
      <button class="pill"        data-sev="info"    onclick="setSev(this)">Info</button>
    </div>
    <div class="panel" id="issue-list"></div>
  </div>
  <div id="panel-keywords" style="display:none;flex-direction:column;flex:1;overflow:hidden;">
    <div class="search-wrap"><input type="text" id="kw-search" placeholder="Search keywords…" oninput="applyKwFilters()"></div>
    <div class="panel" id="kw-list"></div>
  </div>
  <div id="panel-cases" style="display:none;flex-direction:column;flex:1;overflow:hidden;">
    <div class="search-wrap" style="display:flex;gap:6px;">
      <input type="text" id="case-search" placeholder="Search cases…" oninput="applyCaseFilters()" style="flex:1;">
      <button class="btn primary" style="font-size:11px;padding:5px 10px;white-space:nowrap;" onclick="openNewCaseModal()">+ New</button>
    </div>
    <div class="panel" id="case-list"></div>
  </div>
</aside>
<main class="main" id="main-panel">
  <div class="empty">
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
      <path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 0 1 0 10h-2"/>
      <line x1="8" y1="12" x2="16" y2="12"/>
    </svg>
    <p>Select an issue, keyword, or case from the sidebar</p>
  </div>
</main>
</div>
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h2 id="modal-title">Save as Case</h2>
    <input type="hidden" id="modal-case-id">
    <div class="form-row"><label>Case Title</label><input type="text" id="f-title"></div>
    <div class="form-row"><label>Tags (comma-separated)</label><input type="text" id="f-tags"></div>
    <div class="form-row"><label>Reproduction Steps</label><textarea id="f-repro" rows="4"></textarea></div>
    <div class="form-row"><label>Key Log Patterns (comma-separated)</label><input type="text" id="f-patterns"></div>
    <div class="form-row"><label>Analysis / Known Fix</label><textarea id="f-analysis" rows="5"></textarea></div>
    <div class="modal-actions">
      <button class="btn danger" id="modal-delete-btn" style="display:none;margin-right:auto" onclick="deleteCurrentCase()">Delete</button>
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn primary" onclick="saveModal()">Save</button>
    </div>
  </div>
</div>
<script>
const ISSUES={js_issues};
const KEYWORDS={kw_js};
let CASES={js_cases};
const MAX_COUNT=Math.max(...ISSUES.map(i=>i.count),1);
let currentSev='all',editingId=null;
function switchTab(tab){{
  ['issues','keywords','cases'].forEach(t=>{{
    document.getElementById('tab-'+t).classList.toggle('active',t===tab);
    document.getElementById('panel-'+t).style.display=t===tab?'flex':'none';
  }});
  if(tab==='cases') renderCaseList(CASES);
}}
function renderList(items){{
  const el=document.getElementById('issue-list');
  const clr={{error:'#E24B4A',warning:'#EF9F27',info:'#378ADD'}};
  el.innerHTML=items.length?items.map(is=>{{
    const short=is.error.length>68?is.error.slice(0,68)+'…':is.error;
    const hasAI=is.analysis&&is.analysis.length>10;
    const kTag=is.keywords.length?`<span class="badge kw">${{escH(is.keywords[0])}}${{is.keywords.length>1?' +'+(is.keywords.length-1):''}}</span>`:'';
    const mC=CASES.filter(c=>(c.log_patterns||[]).some(p=>is.error.toLowerCase().includes(p.toLowerCase()))||(c.tags||[]).some(t=>is.error.toLowerCase().includes(t.toLowerCase())));
    const cTag=mC.length?`<span class="badge case">📂${{mC.length}}</span>`:'';
    return`<div class="list-item" id="row-${{is.id}}" onclick="selectIssue(${{is.id}})">
      <div class="item-title"><span class="item-dot" style="background:${{clr[is.sev]}}"></span>${{escH(short)}}</div>
      <div class="item-meta"><span class="count-chip">×${{is.count}}</span><span class="badge ${{is.sev}}">${{is.sev}}</span>${{kTag}}${{cTag}}${{hasAI?'<span style="color:var(--green);font-size:10px">✦ analysed</span>':''}}</div>
    </div>`;
  }}).join(''):'<div style="padding:20px;text-align:center;color:var(--muted);font-size:12px;">No matches</div>';
}}
function applyFilters(){{const q=document.getElementById('search').value.toLowerCase();renderList(ISSUES.filter(is=>(currentSev==='all'||is.sev===currentSev)&&(!q||is.error.toLowerCase().includes(q)||is.computer.toLowerCase().includes(q))));}}
function setSev(btn){{document.querySelectorAll('.pill').forEach(p=>p.classList.remove('active'));btn.classList.add('active');currentSev=btn.dataset.sev;applyFilters();}}
function selectIssue(id){{
  document.querySelectorAll('.list-item').forEach(r=>r.classList.remove('active'));
  const row=document.getElementById('row-'+id);if(row)row.classList.add('active');
  const is=ISSUES.find(x=>x.id===id);if(!is)return;
  const clr={{error:'#E24B4A',warning:'#EF9F27',info:'#378ADD'}};
  const barPct=is.count/MAX_COUNT*100;
  const hasAI=is.analysis&&is.analysis.length>10;
  const aiHtml=hasAI?renderAnalysis(is.analysis):'<p class="no-analysis">No AI analysis yet.</p>';
  const relC=CASES.filter(c=>(c.log_patterns||[]).some(p=>is.error.toLowerCase().includes(p.toLowerCase()))||(c.tags||[]).some(t=>is.error.toLowerCase().includes(t.toLowerCase())));
  const cBanner=relC.length?`<div style="margin:12px 0;padding:10px 13px;background:var(--purple-bg);border-radius:7px;border:1px solid rgba(124,58,237,.15);">
    <div style="font-size:11px;font-weight:600;color:var(--purple-txt);margin-bottom:6px;">📂 ${{relC.length}} related past case${{relC.length>1?'s':''}}</div>
    ${{relC.map(c=>`<div style="font-size:12px;cursor:pointer;" onclick="switchTab('cases');setTimeout(()=>selectCase('${{c.id}}'),50)">→ ${{escH(c.title)}}</div>`).join('')}}
  </div>`:'';
  document.getElementById('main-panel').innerHTML=`
    <div class="detail-header">
      <h2>${{escH(is.error.length>130?is.error.slice(0,130)+'…':is.error)}}</h2>
      <div class="meta-row"><span class="badge ${{is.sev}}">${{is.sev.toUpperCase()}}</span>
        <span class="meta-chip">🖥 ${{escH(is.computer)}}</span>
        <span class="meta-chip">📁 ${{escH(is.category)}}</span>
        <span class="meta-chip">📅 ${{escH(is.date)}}</span>
        <span class="meta-chip">🔁 ${{is.count}}×</span></div>
      <div class="meta-row"><span class="meta-chip" style="font-family:monospace;font-size:10px">${{escH(is.files)}}</span></div>
    </div>
    ${{cBanner}}
    <div class="bar-wrap"><h3>Occurrence vs top issue</h3>
      <div class="bar-row"><div class="bar-label">This issue</div>
        <div class="bar-track"><div class="bar-fill" style="width:${{barPct.toFixed(1)}}%;background:${{clr[is.sev]}}"></div></div>
        <div class="bar-count" style="color:${{clr[is.sev]}}">${{is.count}}</div></div>
      <div class="bar-row"><div class="bar-label" style="color:var(--muted)">Top issue</div>
        <div class="bar-track"><div class="bar-fill" style="width:100%;background:var(--border)"></div></div>
        <div class="bar-count" style="color:var(--muted)">${{MAX_COUNT}}</div></div>
    </div>
    <div class="log-box">${{escH(is.error)}}</div>
    <div class="card">
      <div class="card-header"><span>AI Analysis</span>
        ${{hasAI?`<button class="save-btn" onclick="openSaveCaseModal(${{is.id}})">📂 Save as Case</button>`:''}}
      </div>
      <div class="card-body">${{aiHtml}}</div>
    </div>`;
}}
function renderKwList(items){{document.getElementById('kw-list').innerHTML=items.map((kw,i)=>`
  <div class="kw-item" id="kwrow-${{i}}" onclick="selectKeyword(${{i}})">
    <div style="font-size:12px;font-weight:500">${{escH(kw.kw)}}</div>
    <div class="kw-bar-track"><div class="kw-bar-fill" style="width:${{kw.pct}}%"></div></div>
    <div class="kw-meta"><span>Hits:<strong>${{kw.hits}}</strong></span><span>PCs:<strong>${{kw.computers}}</strong></span></div>
  </div>`).join('');
}}
function applyKwFilters(){{const q=document.getElementById('kw-search').value.toLowerCase();renderKwList(KEYWORDS.filter(k=>!q||k.kw.toLowerCase().includes(q)));}}
function selectKeyword(idx){{
  document.querySelectorAll('.kw-item').forEach(r=>r.classList.remove('active'));
  const el=document.getElementById('kwrow-'+idx);if(el)el.classList.add('active');
  const q=(document.getElementById('kw-search').value||'').toLowerCase();
  const vis=KEYWORDS.filter(k=>!q||k.kw.toLowerCase().includes(q));
  const kw=vis[idx];if(!kw)return;
  const related=ISSUES.filter(is=>is.keywords.some(k=>k.toLowerCase()===kw.kw.toLowerCase()));
  const clr={{error:'#E24B4A',warning:'#EF9F27',info:'#378ADD'}};
  document.getElementById('main-panel').innerHTML=`
    <div class="detail-header"><h2 style="font-family:monospace">"${{escH(kw.kw)}}"</h2>
      <div class="meta-row"><span class="badge kw">keyword</span>
        <span class="meta-chip">🔍 ${{kw.hits}} hits</span>
        <span class="meta-chip">🖥 ${{kw.computers}} PC${{kw.computers!==1?'s':''}}</span></div></div>
    <div style="margin-top:20px;">
      <h3 style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Related issues (${{related.length}})</h3>
      ${{related.map(is=>{{const s=is.error.length>100?is.error.slice(0,100)+'…':is.error;
        return`<div class="list-item" style="border-radius:7px;border:1px solid var(--border);margin-bottom:6px;"
               onclick="switchTab('issues');setTimeout(()=>selectIssue(${{is.id}}),30)">
          <div class="item-title"><span class="item-dot" style="background:${{clr[is.sev]}}"></span>${{escH(s)}}</div>
          <div class="item-meta"><span class="count-chip">×${{is.count}}</span><span class="badge ${{is.sev}}">${{is.sev}}</span><span>${{escH(is.computer)}}</span></div>
        </div>`;}}).join('')}}
    </div>`;
}}
function renderCaseList(items){{document.getElementById('case-list').innerHTML=items.length?items.map(c=>{{
  const tags=(c.tags||[]).map(t=>`<span class="case-tag">${{escH(t)}}</span>`).join(' ');
  return`<div class="list-item" id="caserow-${{c.id}}" onclick="selectCase('${{c.id}}')">
    <div class="item-title">${{escH(c.title)}}</div>
    <div class="item-meta">${{tags}}<span style="color:var(--muted)">${{(c.updated_at||'').slice(0,10)}}</span></div>
  </div>`;
}}).join(''):'<div style="padding:20px;text-align:center;color:var(--muted);font-size:12px;">No cases yet.</div>';
}}
function applyCaseFilters(){{const q=document.getElementById('case-search').value.toLowerCase();renderCaseList(CASES.filter(c=>!q||c.title.toLowerCase().includes(q)||(c.tags||[]).some(t=>t.toLowerCase().includes(q))));}}
function selectCase(id){{
  document.querySelectorAll('.list-item').forEach(r=>r.classList.remove('active'));
  const row=document.getElementById('caserow-'+id);if(row)row.classList.add('active');
  const c=CASES.find(x=>x.id===id);if(!c)return;
  const tags=(c.tags||[]).map(t=>`<span class="case-tag">${{escH(t)}}</span>`).join(' ');
  const pats=(c.log_patterns||[]).map(p=>`<span class="case-tag" style="background:var(--green-bg);color:var(--green)">${{escH(p)}}</span>`).join(' ');
  const mi=ISSUES.filter(is=>(c.log_patterns||[]).some(p=>is.error.toLowerCase().includes(p.toLowerCase()))||(c.tags||[]).some(t=>is.error.toLowerCase().includes(t.toLowerCase())));
  const clr={{error:'#E24B4A',warning:'#EF9F27',info:'#378ADD'}};
  document.getElementById('main-panel').innerHTML=`
    <div class="detail-header"><h2>${{escH(c.title)}}</h2>
      <div class="meta-row"><span class="badge case">📂 case</span><span class="meta-chip">ID: ${{escH(c.id)}}</span>
        <span class="meta-chip">Updated: ${{escH((c.updated_at||'').slice(0,10))}}</span>
        <button class="save-btn" onclick="openEditCaseModal('${{c.id}}')">✏ Edit</button></div>
      <div class="meta-row">${{tags}}</div>
    </div>
    ${{c.reproduction?`<div style="margin-top:16px;"><h3 style="font-size:10px;font-weight:600;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Reproduction Steps</h3><div style="font-size:13px;white-space:pre-wrap;background:var(--bg);border-radius:6px;padding:8px 11px;border:1px solid var(--border)">${{escH(c.reproduction)}}</div></div>`:''}}
    ${{(c.log_patterns||[]).length?`<div style="margin-top:12px;"><h3 style="font-size:10px;font-weight:600;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Key Patterns</h3><div style="display:flex;flex-wrap:wrap;gap:5px">${{pats}}</div></div>`:''}}
    ${{c.analysis?`<div style="margin-top:12px;"><h3 style="font-size:10px;font-weight:600;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Known Analysis</h3><div style="font-size:13px;white-space:pre-wrap;background:var(--bg);border-radius:6px;padding:8px 11px;border:1px solid var(--border)">${{escH(c.analysis)}}</div></div>`:''}}
    ${{mi.length?`<div style="margin-top:16px;"><h3 style="font-size:10px;font-weight:600;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Matching Issues (${{mi.length}})</h3>
      ${{mi.map(is=>{{const s=is.error.length>100?is.error.slice(0,100)+'…':is.error;
        return`<div class="list-item" style="border-radius:7px;border:1px solid var(--border);margin-bottom:6px;"
               onclick="switchTab('issues');setTimeout(()=>selectIssue(${{is.id}}),30)">
          <div class="item-title"><span class="item-dot" style="background:${{clr[is.sev]}}"></span>${{escH(s)}}</div>
          <div class="item-meta"><span class="count-chip">×${{is.count}}</span><span class="badge ${{is.sev}}">${{is.sev}}</span></div>
        </div>`;
      }}).join('')}}</div>`:''}}`;
}}
function openSaveCaseModal(issueId){{
  const is=ISSUES.find(x=>x.id===issueId);editingId=null;
  document.getElementById('modal-title').textContent='Save as Case';
  document.getElementById('f-title').value='';document.getElementById('f-tags').value=(is?.keywords||[]).join(', ');
  document.getElementById('f-repro').value='';document.getElementById('f-patterns').value='';
  document.getElementById('f-analysis').value=is?.analysis||'';
  document.getElementById('modal-delete-btn').style.display='none';
  document.getElementById('modal').classList.add('open');
}}
function openNewCaseModal(){{
  editingId=null;document.getElementById('modal-title').textContent='New Case';
  ['f-title','f-tags','f-repro','f-patterns','f-analysis'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('modal-delete-btn').style.display='none';
  document.getElementById('modal').classList.add('open');
}}
function openEditCaseModal(id){{
  const c=CASES.find(x=>x.id===id);if(!c)return;editingId=id;
  document.getElementById('modal-title').textContent='Edit Case';
  document.getElementById('modal-case-id').value=c.id;
  document.getElementById('f-title').value=c.title||'';document.getElementById('f-tags').value=(c.tags||[]).join(', ');
  document.getElementById('f-repro').value=c.reproduction||'';document.getElementById('f-patterns').value=(c.log_patterns||[]).join(', ');
  document.getElementById('f-analysis').value=c.analysis||'';
  document.getElementById('modal-delete-btn').style.display='inline-block';
  document.getElementById('modal').classList.add('open');
}}
function closeModal(){{document.getElementById('modal').classList.remove('open');editingId=null;}}
function saveModal(){{
  const title=document.getElementById('f-title').value.trim();if(!title){{alert('Enter a title.');return;}}
  const tags=document.getElementById('f-tags').value.split(',').map(s=>s.trim()).filter(Boolean);
  const repro=document.getElementById('f-repro').value.trim();
  const patterns=document.getElementById('f-patterns').value.split(',').map(s=>s.trim()).filter(Boolean);
  const analysis=document.getElementById('f-analysis').value.trim();
  const now=new Date().toISOString().slice(0,19);
  if(editingId){{const c=CASES.find(x=>x.id===editingId);if(c){{c.title=title;c.tags=tags;c.reproduction=repro;c.log_patterns=patterns;c.analysis=analysis;c.updated_at=now;}}}}
  else{{CASES.push({{id:Math.random().toString(36).slice(2,10),title,tags,reproduction:repro,log_patterns:patterns,analysis,created_at:now,updated_at:now}});}}
  closeModal();switchTab('cases');renderCaseList(CASES);
  const blob=new Blob([JSON.stringify(CASES,null,2)],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cases.json';a.click();
}}
function deleteCurrentCase(){{
  if(!editingId)return;if(!confirm('Delete this case?'))return;
  CASES=CASES.filter(c=>c.id!==editingId);closeModal();renderCaseList(CASES);
  const blob=new Blob([JSON.stringify(CASES,null,2)],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cases.json';a.click();
}}
function renderAnalysis(text){{
  return text.split('\\n').map(line=>{{
    if(line.startsWith('## '))return`<div class="section-title">${{escH(line.slice(3))}}</div>`;
    if(line.startsWith('# '))return`<strong>${{escH(line.slice(2))}}</strong>`;
    if(line.startsWith('- ')||line.startsWith('* '))return`<div class="bullet">• ${{escH(line.slice(2))}}</div>`;
    if(line.trim()==='')return'<div class="spacer"></div>';
    return`<div class="body-line">${{escH(line)}}</div>`;
  }}).join('\\n');
}}
function escH(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
renderList(ISSUES);renderKwList(KEYWORDS);renderCaseList(CASES);
setTimeout(()=>ISSUES.length&&selectIssue(0),200);
document.getElementById('modal').addEventListener('click',e=>{{if(e.target===document.getElementById('modal'))closeModal();}});
</script>
</body>
</html>"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n{Fore.GREEN}{Style.BRIGHT}HTML report saved: {report_path}")


# ══════════════════════════════════════════════════════════════════════════════
# CSV UPDATE
# ══════════════════════════════════════════════════════════════════════════════
def update_csv_with_analysis(csv_path, analyses):
    if not os.path.exists(csv_path): return
    amap = {(a["ComputerName"], a["Error"]): a["Analysis"] for a in analyses}
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fns = list(reader.fieldnames or [])
        if "AI_Analysis" not in fns: fns.append("AI_Analysis")
        for row in reader:
            key = (row.get("ComputerName",""), row.get("Error",""))
            row["AI_Analysis"] = amap.get(key, row.get("AI_Analysis",""))
            rows.append(row)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fns)
        writer.writeheader(); writer.writerows(rows)
    print(f"{Fore.GREEN}CSV updated: {csv_path}")


# ══════════════════════════════════════════════════════════════════════════════
# INTERACTIVE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
def interactive_analysis(all_summaries, keyword_hits, keyword_computers):
    if not all_summaries:
        print(f"\n{Fore.MAGENTA}No issues to analyse."); return

    all_analyses    = []
    csv_analysis_map = {}

    def print_issue_list():
        cases = load_cases()
        print(f"\n{'='*70}")
        print(f"{Fore.CYAN}{Style.BRIGHT}  [ AI ISSUE ANALYSIS MODE ]")
        print(f"{Fore.WHITE}  Scan: {yesterday.strftime('%Y-%m-%d 00:00:00')} → {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{Fore.WHITE}  KB  : {Fore.YELLOW}{len(cases)} case(s)")
        print(f"{'='*70}\n")
        for i, item in enumerate(all_summaries, 1):
            e    = item["entry"]
            prev = e["Error"][:68] + ("..." if len(e["Error"]) > 68 else "")
            done = f"{Fore.GREEN}✓ " if any(a["Error"]==e["Error"] for a in all_analyses) else "  "
            sim  = find_similar_cases(e["Error"])
            hint = f" {Fore.CYAN}[{len(sim)} case ref]" if sim else ""
            print(f"  {Fore.YELLOW}[{i:>3}] {done}{Fore.WHITE}"
                  f"{e['ComputerName']:<18} {Fore.CYAN}×{e['Count']:<5}"
                  f"{Fore.WHITE}{prev}{hint}{Style.RESET_ALL}")
        print(f"\n{Fore.WHITE}number(s) / {Fore.YELLOW}all{Fore.WHITE} / {Fore.YELLOW}report{Fore.WHITE} / {Fore.YELLOW}q")

    print_issue_list()

    while True:
        raw = input(f"\n{Fore.GREEN}>> {Style.RESET_ALL}").strip().lower()
        if raw == "q": break
        if raw == "report":
            _flush_report(all_summaries, all_analyses, csv_analysis_map,
                          keyword_hits, keyword_computers); continue
        if raw == "all":
            selected_indices = list(range(len(all_summaries)))
        else:
            try:
                selected_indices = [int(x.strip())-1 for x in raw.split(",")]
                for idx in selected_indices:
                    if idx < 0 or idx >= len(all_summaries): raise ValueError(f"Out of range: {idx+1}")
            except ValueError as e:
                print(f"{Fore.RED}Invalid ({e})."); continue

        for idx in selected_indices:
            item  = all_summaries[idx]
            entry = item["entry"]
            prev  = entry["Error"][:60] + ("..." if len(entry["Error"]) > 60 else "")
            print(f"\n{Fore.CYAN}[{idx+1}] Analysing: {prev}")
            txt = analyze_issue(entry, item["cat_name"])
            result = {
                "ComputerName": entry["ComputerName"], "Category": item["cat_name"],
                "Date": entry["Date"], "FileNames": ", ".join(entry["FileNames"]),
                "Error": entry["Error"], "Count": entry["Count"], "Analysis": txt,
            }
            ex = next((a for a in all_analyses if a["Error"]==entry["Error"] and a["ComputerName"]==entry["ComputerName"]), None)
            if ex: ex["Analysis"] = txt
            else:  all_analyses.append(result)
            csv_analysis_map.setdefault(item["csv_path"], [])
            ex2 = next((a for a in csv_analysis_map[item["csv_path"]] if a["Error"]==entry["Error"]), None)
            if ex2: ex2["Analysis"] = txt
            else:   csv_analysis_map[item["csv_path"]].append(result)
            print(f"\n{Fore.GREEN}--- Analysis ---\n{txt}")
            print(f"{Fore.GREEN}{'─'*60}")
            prompt_save_case(entry, txt)

        print(f"\n{Fore.WHITE}More / {Fore.YELLOW}report{Fore.WHITE} / {Fore.YELLOW}q")

    if all_analyses:
        _flush_report(all_summaries, all_analyses, csv_analysis_map,
                      keyword_hits, keyword_computers)


def _flush_report(all_summaries, all_analyses, csv_analysis_map,
                  keyword_hits, keyword_computers):
    amap = {(a["ComputerName"], a["Error"]): a for a in all_analyses}
    full = [amap.get((item["entry"]["ComputerName"], item["entry"]["Error"]), {
        "ComputerName": item["entry"]["ComputerName"], "Category": item["cat_name"],
        "Date": item["entry"]["Date"], "FileNames": ", ".join(item["entry"]["FileNames"]),
        "Error": item["entry"]["Error"], "Count": item["entry"]["Count"], "Analysis": "",
    }) for item in all_summaries]
    total = sum(open(p,"rb").read().count(b"\n")
                for p in set(local_file_cache.values()) if os.path.exists(p))
    rp = f"analysis_report_{datetime.now().strftime('%y%m%d-%H%M%S')}.html"
    generate_html_report(full, rp,
                         yesterday.strftime("%Y-%m-%d 00:00:00"),
                         now.strftime("%Y-%m-%d %H:%M:%S"),
                         total, keyword_hits, keyword_computers)
    for cp, ca in csv_analysis_map.items():
        update_csv_with_analysis(cp, ca)


# ══════════════════════════════════════════════════════════════════════════════
# FTP WORKER  — reads from real FTP, filter = cat["filter"] in fname
# ══════════════════════════════════════════════════════════════════════════════
def _scan_host(host: str, cat: dict, search_queries: list) -> list:
    raw        = []
    remote_path = cat["remote_path"]
    local_base  = os.path.join(os.getcwd(), host, cat["name"], yesterday_str)
    os.makedirs(local_base, exist_ok=True)

    try:
        ftp = ftplib.FTP(host, timeout=10)
        ftp.login("lam", "123")

        try:
            ftp.cwd(remote_path)
        except ftplib.error_perm:
            ftp.quit()
            return raw

        filenames = []
        ftp.retrlines("NLST", filenames.append)

        for fname in filenames:
            # ── ORIGINAL filter logic ─────────────────────────────────────
            if cat["filter"] not in fname:
                continue

            local_path = os.path.join(local_base, fname)
            if not os.path.exists(local_path):
                tprint(f"  {Fore.WHITE}[{host}] Downloading: {fname}")
                with open(local_path, "wb") as lf:
                    ftp.retrbinary(f"RETR {fname}", lf.write)
            else:
                tprint(f"  {Fore.BLUE}[{host}] Cached: {fname}")

            with _cache_lock:
                local_file_cache[(host, cat["name"], fname)] = local_path

            with open(local_path, "rb") as lf:
                content = lf.read().decode("latin-1", errors="ignore")

            for line in content.splitlines():
                clean = line.replace("\x00", "").strip()
                if not clean: continue
                for query in search_queries:
                    if query in clean.lower():
                        raw.append({"Computer": host, "File": fname,
                                    "Content": clean, "MatchedKeyword": query})
                        break

        ftp.quit()

    except Exception as e:
        tprint(f"  {Fore.RED}Error on {host}: {e}")

    return raw


# ══════════════════════════════════════════════════════════════════════════════
# MAIN SCAN
# ══════════════════════════════════════════════════════════════════════════════
def scan_logs():
    if not os.path.exists(STRINGS_FILE):
        print(Fore.RED + f"Error: {STRINGS_FILE} not found!"); return [], {}, {}
    with open(STRINGS_FILE, "r", encoding="utf-8") as f:
        search_queries = [line.strip().lower() for line in f if line.strip()]

    if not os.path.exists(INPUT_FILE):
        print(Fore.RED + f"Error: {INPUT_FILE} not found!"); return [], {}, {}
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        computers = [line.strip() for line in f if line.strip()]

    cases = load_cases()
    print(f"\n{Fore.CYAN}Scan range : {yesterday.strftime('%Y-%m-%d 00:00:00')} → {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{Fore.CYAN}Hosts      : {len(computers)}  |  Keywords: {len(search_queries)}  |  Workers: {min(MAX_FTP_WORKERS,len(computers))}")
    print(f"{Fore.CYAN}KB cases   : {Fore.YELLOW}{len(cases)}{Fore.WHITE} loaded\n")

    keyword_hits      = {q: 0 for q in search_queries}
    keyword_computers = {q: set() for q in search_queries}
    all_summaries     = []

    for cat in categories:
        print(f"\n{Fore.CYAN}{Style.BRIGHT}--- Category: {cat['name']}  (filter: '{cat['filter']}') ---")
        all_raw = []

        with ThreadPoolExecutor(max_workers=min(MAX_FTP_WORKERS, len(computers))) as pool:
            futures = {pool.submit(_scan_host, h, cat, search_queries): h for h in computers}
            for future in as_completed(futures):
                host = futures[future]
                try:
                    host_raw = future.result()
                    all_raw.extend(host_raw)
                    tprint(f"  {Fore.GREEN}[{host}] Done — {len(host_raw)} hits")
                except Exception as e:
                    tprint(f"  {Fore.RED}[{host}] Error: {e}")

        for res in all_raw:
            kw = res.get("MatchedKeyword","")
            if kw in keyword_hits:
                keyword_hits[kw]      += 1
                keyword_computers[kw].add(res["Computer"])

        final_summary = []
        for res in all_raw:
            matched = False
            for entry in final_summary:
                if res["Computer"]==entry["ComputerName"] and is_similar(res["Content"],entry["Error"]):
                    entry["Count"] += 1
                    if res["File"] not in entry["FileNames"]: entry["FileNames"].append(res["File"])
                    matched = True; break
            if not matched:
                final_summary.append({
                    "ComputerName": res["Computer"],
                    "Date": yesterday_str,
                    "FileNames": [res["File"]],
                    "Error": res["Content"], "Count": 1,
                })

        if final_summary:
            with open(cat["output_file"], "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["ComputerName","Date","FileNames","Error","Count","AI_Analysis"])
                for s in final_summary:
                    writer.writerow([s["ComputerName"],s["Date"],", ".join(s["FileNames"]),s["Error"],s["Count"],""])
            print(f"\n{Fore.GREEN}{Style.BRIGHT}Saved: {cat['output_file']}")
            for entry in final_summary:
                all_summaries.append({"entry":entry,"cat_name":cat["name"],"csv_path":cat["output_file"]})
        else:
            print(f"\n{Fore.MAGENTA}No matches found in {cat['name']}.")

    return all_summaries, keyword_hits, keyword_computers


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    all_summaries, keyword_hits, keyword_computers = scan_logs()
    print_keyword_summary(keyword_hits, keyword_computers)
    print(f"{Fore.CYAN}{Style.BRIGHT}All scan tasks complete.")

    if all_summaries:
        print(f"\n{Fore.WHITE}Total unique issues: {Fore.YELLOW}{len(all_summaries)}")
        go = input(f"\n{Fore.GREEN}Start AI analysis? (y/n): {Style.RESET_ALL}").strip().lower()
        if go == "y":
            interactive_analysis(all_summaries, keyword_hits, keyword_computers)
        else:
            _flush_report(all_summaries, [], {}, keyword_hits, keyword_computers)
    else:
        print(f"{Fore.MAGENTA}No issues found.")

    input(f"\n{Fore.CYAN}Press Enter to exit...")

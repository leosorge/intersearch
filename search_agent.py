"""
search_agent.py  -  YouTube Interview Monitor
Cerca interviste/dichiarazioni nelle ultime 24h e genera output.html
"""

import os
import re
import sys
import yaml
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

CONFIG_PATH = Path("config/topics.yaml")
OUTPUT_PATH = Path("output.html")

TRANSLATE_PATTERNS = [
    re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]"),  # Cinese
    re.compile(r"[\u3040-\u309f\u30a0-\u30ff]"),   # Giapponese
    re.compile(r"[\uac00-\ud7af]"),                 # Coreano
    re.compile(r"[\u0900-\u097f]"),                 # Sanscrito
    re.compile(r"[\u0600-\u06ff]"),                 # Arabo
    re.compile(r"[\u0400-\u04ff]"),                 # Russo/Cirillico
]

CSS = (
    ":root{--bg:#0d0d0d;--surface:#161616;--border:#2a2a2a;--accent:#e63946;"
    "--text:#e0e0e0;--muted:#666;--link:#58a6ff;--link-hover:#79b8ff}"
    "*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}"
    "body{background:var(--bg);color:var(--text);"
    "font-family:'Segoe UI',system-ui,sans-serif;font-size:15px;line-height:1.6}"
    "header{background:var(--surface);border-bottom:1px solid var(--border);"
    "padding:20px 32px;display:flex;align-items:baseline;gap:16px}"
    "header h1{font-size:1.4rem;color:var(--accent)}"
    "header .stats{font-size:.82rem;color:var(--muted)}"
    "main{max-width:900px;margin:0 auto;padding:32px 24px}"
    "section{margin-bottom:40px}"
    "h2{font-size:1rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;"
    "color:var(--accent);border-bottom:1px solid var(--border);"
    "padding-bottom:8px;margin-bottom:14px}"
    ".count{color:var(--muted);font-weight:400;font-size:.85em}"
    ".empty{color:var(--muted);font-style:italic;font-size:.88rem}"
    "ol{list-style:none;padding-left:0}"
    "li{border-bottom:1px solid #1a1a1a}"
    "li:last-child{border-bottom:none}"
    "a.video-link{display:flex;align-items:center;gap:12px;padding:10px 4px;"
    "text-decoration:none;transition:background .15s}"
    "a.video-link:hover{background:#111}"
    "a.video-link img{width:100px;min-width:100px;aspect-ratio:16/9;"
    "object-fit:cover;border-radius:4px;border:1px solid #2a2a2a;flex-shrink:0}"
    ".video-info{display:flex;flex-direction:column;gap:3px}"
    ".meta{font-size:.72rem;color:var(--muted)}"
    ".title{color:var(--link);font-size:.9rem;line-height:1.4}"
    "a.video-link:hover .title{color:var(--link-hover)}"
    "footer{text-align:center;padding:24px;font-size:.78rem;"
    "color:var(--muted);border-top:1px solid var(--border)}"
)


# ── Traduzione ────────────────────────────────────────────────

def translate_title(title):
    if not any(p.search(title) for p in TRANSLATE_PATTERNS):
        return title
    log.warning("[TRANSLATE] rilevato: %s", title[:80])
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source="auto", target="it").translate(title)
        log.warning("[TRANSLATE] tradotto: %s", (result or "")[:80])
        return result if result else title
    except Exception as exc:
        log.warning("[TRANSLATE] fallita: %s", exc)
        return title


# ── YouTube search ────────────────────────────────────────────

def search_topic(youtube, topic, max_results, order):
    terms = "interview OR keynote OR speech OR declaration OR testimony OR conference"
    query = '"' + topic + '" (' + terms + ')'
    after = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("Ricerca: %s", query)
    try:
        resp = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=max_results,
            order=order,
            videoDuration="medium",
            relevanceLanguage="en",
            publishedAfter=after,
        ).execute()
    except HttpError as exc:
        log.error("Errore API: %s", exc)
        return []

    videos = []
    for item in resp.get("items", []):
        s   = item["snippet"]
        vid = item["id"]["videoId"]
        raw = s.get("title", "")
        log.info("  %s", raw[:70])
        videos.append({
            "video_id":  vid,
            "title":     translate_title(raw),
            "channel":   s.get("channelTitle", ""),
            "published": s.get("publishedAt", "")[:10],
            "url":       "https://www.youtube.com/watch?v=" + vid,
        })
    return videos


def run_search(config):
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        log.error("YOUTUBE_API_KEY mancante")
        sys.exit(1)

    yt       = build("youtube", "v3", developerKey=api_key)
    settings = config.get("settings", {})
    max_res  = settings.get("max_results_per_topic", 8)
    order    = settings.get("order", "relevance")
    seen     = set()
    results  = []

    for t in config.get("topics", []):
        name = t if isinstance(t, str) else t.get("name", "")
        raw  = search_topic(yt, name, max_res, order)
        vids = []
        for v in raw:
            if v["video_id"] not in seen:
                seen.add(v["video_id"])
                vids.append(v)
        log.info("  -> %d video unici per '%s'", len(vids), name)
        results.append({"topic": name, "videos": vids})
    return results


# ── HTML ──────────────────────────────────────────────────────

def esc(s):
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def make_item(v):
    thumb = "https://img.youtube.com/vi/" + esc(v["video_id"]) + "/mqdefault.jpg"
    url   = esc(v["url"])
    return (
        "<li>"
        + '<a href="' + url + '" target="_blank" rel="noopener" class="video-link">'
        + '<img src="' + thumb + '" width="100" alt="" loading="lazy"/>'
        + '<span class="video-info">'
        + '<span class="meta">' + esc(v["published"]) + " &mdash; " + esc(v["channel"]) + "</span>"
        + '<span class="title">' + esc(v["title"]) + "</span>"
        + "</span></a></li>"
    )


def make_section(r):
    topic  = esc(r["topic"])
    videos = r["videos"]
    if not videos:
        return ("<section><h2>" + topic + "</h2>"
                + "<p class='empty'>Nessun video nelle ultime 24 ore.</p></section>")
    items = "\n".join(make_item(v) for v in videos)
    count = str(len(videos))
    return ("<section><h2>" + topic + " <span class='count'>(" + count + ")</span></h2>"
            + "<ol>\n" + items + "\n</ol></section>")


def generate_html(results, generated_at):
    total    = sum(len(r["videos"]) for r in results)
    sections = "\n".join(make_section(r) for r in results)
    stats    = ("Aggiornato: " + esc(generated_at)
                + " &nbsp;&middot;&nbsp; " + str(total) + " video trovati")
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="it">',
        "<head>",
        '<meta charset="UTF-8"/>',
        '<meta name="viewport" content="width=device-width,initial-scale=1.0"/>',
        "<title>YouTube Interview Monitor</title>",
        "<style>" + CSS + "</style>",
        "</head><body>",
        "<header>",
        "<h1>&#9654; YouTube Interview Monitor</h1>",
        '<span class="stats">' + stats + "</span>",
        "</header>",
        "<main>" + sections + "</main>",
        "<footer>Generato automaticamente &mdash; YouTube Data API v3</footer>",
        "</body></html>",
    ])


# ── Main ──────────────────────────────────────────────────────

def main():
    if not CONFIG_PATH.exists():
        log.error("Config non trovata: %s", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    results = run_search(config)
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = generate_html(results, generated_at=now)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    total = sum(len(r["videos"]) for r in results)
    log.info("OK -- %d video in %d topic -> output.html", total, len(results))


if __name__ == "__main__":
    main()

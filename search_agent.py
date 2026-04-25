"""
search_agent.py
---------------
Cerca su YouTube interviste e dichiarazioni sui topic configurati
e genera output.html con la lista dei video trovati, linkati.

Uso:
    python search_agent.py

Variabili d'ambiente richieste:
    YOUTUBE_API_KEY   (oppure nel file .env)
"""

import os
import sys
import yaml
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path("config/topics.yaml")
OUTPUT_PATH = Path("output.html")


# ══════════════════════════════════════════════════════════════
# YouTube search
# ══════════════════════════════════════════════════════════════

def build_query(topic: str) -> str:
    """
    Costruisce una query YouTube che favorisce interviste e dichiarazioni,
    escludendo contenuti non rilevanti (commenti, reazioni, compilazioni).
    """
    interview_terms = "interview OR keynote OR speech OR declaration OR testimony OR conference"
    return f'"{topic}" ({interview_terms})'


def search_topic(youtube, topic: str, max_results: int, order: str) -> list[dict]:
    """Cerca video per un singolo topic. Ritorna lista di dict video."""
    from datetime import timedelta
    query = build_query(topic)
    log.info(f"Ricerca: {query!r}")

    # Calcola 24 ore fa in formato RFC 3339 (richiesto dall'API)
    published_after = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=max_results,
            order=order,
            videoDuration="medium",
            relevanceLanguage="en",
            publishedAfter=published_after,
        ).execute()
    except HttpError as e:
        log.error(f"Errore API YouTube per topic '{topic}': {e}")
        return []

    videos = []
    for item in resp.get("items", []):
        s = item["snippet"]
        vid_id = item["id"]["videoId"]
        videos.append({
            "video_id":   vid_id,
            "title":      s.get("title", ""),
            "channel":    s.get("channelTitle", ""),
            "published":  s.get("publishedAt", "")[:10],
            "url":        f"https://www.youtube.com/watch?v={vid_id}",
            "thumb":      (s.get("thumbnails", {}).get("medium") or {}).get("url", ""),
        })
    return videos

def run_search(config: dict) -> list[dict]:
    """
    Esegue la ricerca per tutti i topic.
    Ritorna lista di dict { topic, videos[] } deduplicata per video_id.
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        log.error("YOUTUBE_API_KEY non trovata. Imposta la variabile d'ambiente o il file .env")
        sys.exit(1)

    youtube = build("youtube", "v3", developerKey=api_key)

    topics   = config.get("topics", [])
    settings = config.get("settings", {})
    max_res  = settings.get("max_results_per_topic", 8)
    order    = settings.get("order", "relevance")

    seen_ids = set()
    results  = []

    for topic_cfg in topics:
        topic_name = topic_cfg if isinstance(topic_cfg, str) else topic_cfg.get("name", "")
        videos_raw = search_topic(youtube, topic_name, max_res, order)

        # Deduplicazione globale
        videos = []
        for v in videos_raw:
            if v["video_id"] not in seen_ids:
                seen_ids.add(v["video_id"])
                videos.append(v)

        log.info(f"  → {len(videos)} video unici per '{topic_name}'")
        results.append({"topic": topic_name, "videos": videos})

    return results


# ══════════════════════════════════════════════════════════════
# HTML generator
# ══════════════════════════════════════════════════════════════

def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

# Script che richiedono traduzione (rilevati tramite range Unicode)
_TRANSLATE_PATTERNS = [
    re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]'),   # Cinese (CJK)
    re.compile(r'[\u3040-\u309f\u30a0-\u30ff]'),    # Giapponese (Hiragana/Katakana)
    re.compile(r'[\uac00-\ud7af]'),                  # Coreano (Hangul)
    re.compile(r'[\u0900-\u097f]'),                  # Sanscrito/Hindi (Devanagari)
    re.compile(r'[\u0600-\u06ff]'),                  # Arabo
    re.compile(r'[\u0400-\u04ff]'),                  # Russo/Cirillico
]

def translate_title(title: str) -> str:
    if not any(p.search(title) for p in _TRANSLATE_PATTERNS):
        return title
    log.info(f"[TRANSLATE] rilevato script non-latino: {title[:60]}")
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target="it").translate(title)
        log.info(f"[TRANSLATE] risultato: {translated[:60]}")
        return translated if translated else title
    except Exception as e:
        log.warning(f"[TRANSLATE] fallita: {e}")
        return title

def generate_html(results: list[dict], generated_at: str) -> str:
    total = sum(len(r["videos"]) for r in results)

    sections = []
    for r in results:
        topic = _esc(r["topic"])
        videos = r["videos"]
        if not videos:
            sections.append(f"""
    <section>
      <h2>{topic}</h2>
      <p class="empty">Nessun video trovato.</p>
    </section>""")
            continue

        items = "\n".join(
            f'      <li>'
            f'<a href="{_esc(v["url"])}" target="_blank" rel="noopener" class="video-link">'
            f'<img src="https://img.youtube.com/vi/{_esc(v["video_id"])}/mqdefault.jpg" '
            f'     width="100" alt="" loading="lazy"/>'
            f'<span class="video-info">'
            f'<span class="meta">{_esc(v["published"])} &mdash; {_esc(v["channel"])}</span>'
            f'<span class="title">{_esc(v["title"])}</span>'
            f'</span>'
            f'</a>'
            f'</li>'
            for v in videos
        )
        sections.append(f"""
    <section>
      <h2>{topic} <span class="count">({len(videos)})</span></h2>
      <ol>
{items}
      </ol>
    </section>""")

    sections_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>YouTube Interview Monitor</title>
  <style>
    :root {{
      --bg: #0d0d0d; --surface: #161616; --border: #2a2a2a;
      --accent: #e63946; --text: #e0e0e0; --muted: #666;
      --link: #58a6ff; --link-hover: #79b8ff;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: 'Segoe UI', system-ui, sans-serif;
      font-size: 15px; line-height: 1.6;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 20px 32px;
      display: flex; align-items: baseline; gap: 16px;
    }}
    header h1 {{ font-size: 1.4rem; color: var(--accent); }}
    header .stats {{ font-size: 0.82rem; color: var(--muted); }}
    main {{ max-width: 900px; margin: 0 auto; padding: 32px 24px; }}
    section {{ margin-bottom: 40px; }}
    h2 {{
      font-size: 1rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .08em; color: var(--accent);
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px; margin-bottom: 14px;
    }}
    .count {{ color: var(--muted); font-weight: 400; font-size: 0.85em; }}
    .empty {{ color: var(--muted); font-style: italic; font-size: 0.88rem; }}
    footer {{
      text-align: center; padding: 24px;
      font-size: 0.78rem; color: var(--muted);
      border-top: 1px solid var(--border);
    }}
    ol {{ list-style: none; padding-left: 0; }}
    li {{ border-bottom: 1px solid #1a1a1a; }}
    li:last-child {{ border-bottom: none; }}
    a.video-link {{
      display: flex; align-items: center; gap: 12px;
      padding: 10px 4px; text-decoration: none;
      transition: background 0.15s;
    }}
    a.video-link:hover {{ background: #111; }}
    a.video-link img {{
      width: 100px; min-width: 100px;
      aspect-ratio: 16/9; object-fit: cover;
      border-radius: 4px; border: 1px solid #2a2a2a;
    }}
    .video-info {{ display: flex; flex-direction: column; gap: 3px; }}
    .title {{ color: var(--link); font-size: 0.9rem; line-height: 1.4; }}
    a.video-link:hover .title {{ color: var(--link-hover); }}
  </style>
</head>
<body>
  <header>
    <h1>▶ YouTube Interview Monitor</h1>
    <span class="stats">
      Aggiornato: {_esc(generated_at)} &nbsp;·&nbsp; {total} video trovati
    </span>
  </header>
  <main>
{sections_html}
  </main>
  <footer>
    Generato automaticamente da YouTube Interview Monitor &mdash;
    Dati: YouTube Data API v3
  </footer>
</body>
</html>"""
# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

def main():
    # Carica config
    if not CONFIG_PATH.exists():
        log.error(f"Config non trovata: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Esegui ricerca
    results = run_search(config)

    # Genera HTML
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = generate_html(results, generated_at=now)

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    total = sum(len(r["videos"]) for r in results)
    log.info(f"✅ output.html generato — {total} video in {len(results)} topic")


if __name__ == "__main__":
    main()

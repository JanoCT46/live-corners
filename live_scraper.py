"""
Segue su diretta.it corner, punteggio e minuto delle partite indicate e li invia al bot ogni 2 minuti.
Gira su GitHub Actions, lanciato dal bot al primo calcio d'inizio.

Variabili d'ambiente:
  LIVE_TOKEN     identificativo del monitoraggio (lo dà il bot)
  LIVE_CALLBACK  indirizzo del bot (nel workflow arriva da un segreto, quindi resta mascherato nei log)
  LIVE_SECRET    segreto condiviso col bot
  LIVE_MATCHES   JSON: [{"id","home","away","league","kickoff"}]
Segue ogni partita fino al fischio finale. Si ferma da solo quando sono tutte finite,
oppure se il bot risponde che il monitoraggio non è più attivo.
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from playwright.async_api import async_playwright

from team_names import teams_match

POLL_SECONDS = 120
MAX_RUN_SECONDS = 5 * 3600 + 45 * 60   # GitHub ferma ogni job a 6 ore
OVER_LINE = 9

LEAGUE_BASES = {
    "Serie A": "https://www.diretta.it/calcio/italia/serie-a/",
    "Serie B": "https://www.diretta.it/calcio/italia/serie-b/",
    "Premier League": "https://www.diretta.it/calcio/inghilterra/premier-league/",
    "La Liga": "https://www.diretta.it/calcio/spagna/laliga/",
    "Bundesliga": "https://www.diretta.it/calcio/germania/bundesliga/",
    "Ligue 1": "https://www.diretta.it/calcio/francia/ligue-1/",
}
# Pagine in cui cercare la partita: prima il calendario, poi la pagina della lega, poi i risultati
LEAGUE_SUBPAGES = ["calendario/", "", "risultati/"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def parse_status(text):
    """Testo di stato di diretta.it -> (codice stile API-Sports, minuto)."""
    t = (text or "").strip().lower()
    if not t:
        return "NS", None
    if "rinviat" in t:
        return "PST", None
    if "annullat" in t or "sospes" in t or "abbandonat" in t:
        return "CANC", None
    if "intervallo" in t:
        return "HT", 45
    if "finale" in t or "terminat" in t or "dopo i supplementari" in t or "dopo i rigori" in t:
        return "FT", 90
    t = " ".join(t.split())
    # Formato attuale di diretta.it: "2 tempo 60:02" / "1 tempo 12:34" (minuti:secondi)
    phase = re.search(r"(\d)\s*[°º]?\s*tempo|primo tempo|secondo tempo", t)
    ph = None
    if phase:
        ph = phase.group(1) or ("1" if "primo" in phase.group(0) else "2")
    mmss = re.search(r"(\d{1,3})\s*:\s*\d{2}", t)
    if "supplement" in t and not mmss:
        return "ET", None
    # Il formato MM:SS vale solo se c'è "1/2 tempo": senza, potrebbe essere l'ORARIO di inizio
    # (es. "13:30" = 15:30 italiane, perché il runner di GitHub è in UTC) e non un minuto di gioco.
    if mmss and ph:
        minute = int(mmss.group(1))
        if minute > 130:
            return "NS", None
        return ("1H" if ph == "1" else "2H"), minute
    if ph:  # tempo noto ma minuto non leggibile
        return ("1H" if ph == "1" else "2H"), None
    m = re.search(r"(\d+)(?:\s*\+\s*(\d+))?\s*['′’]?", t)
    if m and ("'" in t or "′" in t or "’" in t or t.replace("+", "").strip().isdigit()):
        minute = int(m.group(1)) + (int(m.group(2)) if m.group(2) else 0)
        return ("1H" if minute <= 45 else "2H"), minute
    return "NS", None


def parse_score(text):
    nums = re.findall(r"\d+", text or "")
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return None, None


async def accept_cookies(page):
    try:
        btn = await page.wait_for_selector("#onetrust-accept-btn-handler", timeout=3000)
        if btn:
            await btn.click()
    except Exception:
        pass


async def find_match_url(page, match):
    """Cerca la partita nelle pagine della lega confrontando i nomi delle squadre."""
    base = LEAGUE_BASES.get(match.get("league") or "")
    if not base:
        print(f"   ⚠️ Lega sconosciuta per {match['home']} - {match['away']}: {match.get('league')}")
        return None
    for sub in LEAGUE_SUBPAGES:
        try:
            await page.goto(base + sub, timeout=60000)
            await accept_cookies(page)
            await page.wait_for_selector(".event__match", timeout=10000)
        except Exception:
            continue
        rows = page.locator(".event__match")
        for i in range(await rows.count()):
            row = rows.nth(i)
            try:
                h = await row.locator(".event__homeParticipant").inner_text()
                a = await row.locator(".event__awayParticipant").inner_text()
                if teams_match(match["home"], h.strip()) and teams_match(match["away"], a.strip()):
                    href = await row.locator(".eventRowLink").get_attribute("href")
                    if href:
                        return href if href.startswith("http") else f"https://www.diretta.it{href}"
            except Exception:
                continue
    return None


def parse_corner_row(text):
    """
    Riga di statistica "5 / Calci d'angolo / 3" -> 8. Scarta i contenitori che racchiudono
    molte righe (il loro testo contiene "angolo" ma anche tutti gli altri numeri).
    """
    lines = [l.strip() for l in (text or "").replace("\r", "").split("\n") if l.strip()]
    if not 3 <= len(lines) <= 5:
        return None
    for i, line in enumerate(lines):
        low = line.lower()
        if ("angol" in low or "corner" in low) and "falli" not in low and 1 <= i < len(lines) - 1:
            h, a = lines[i - 1], lines[i + 1]
            if h.isdigit() and a.isdigit():
                return int(h) + int(a)
    return None


async def _first_text(page, selector):
    try:
        el = page.locator(selector)
        if await el.count():
            return (await el.first.inner_text()).strip()
    except Exception:
        pass
    return ""


async def scan_corners(page):
    """Cerca nella pagina la riga dei corner. Ritorna (corner totali o None, righe viste, righe corner)."""
    corners, corner_rows = None, []
    rows = page.locator('div[class*="statistics"] div, .stat__row, div[class*="row"]')
    n_rows = await rows.count()
    for r in range(n_rows):
        row = rows.nth(r)
        try:
            txt = await row.inner_text()
        except Exception:
            continue
        low = (txt or "").lower()
        if ("angol" in low or "corner" in low) and "falli" not in low:
            if len(corner_rows) < 3:
                corner_rows.append(" ".join(txt.split())[:60])
            val = parse_corner_row(txt)
            if val is not None:
                corners = val
                break
    return corners, n_rows, corner_rows


async def read_match(page, url, kickoff_ts=0):
    """Apre la partita e legge stato, punteggio e corner. Stampa cosa vede, per la diagnosi."""
    await page.goto(url, timeout=30000)
    await page.wait_for_timeout(1500)

    status_text = await _first_text(page, ".detailScore__status, .fixedHeaderDuel__detailStatus")
    score_text = await _first_text(page, ".detailScore__wrapper")
    header_text = await _first_text(page, ".duelParticipant, .duelParticipant__container")
    status, minute = parse_status(status_text)
    if status == "NS" and header_text:  # il minuto può stare altrove nell'intestazione
        status, minute = parse_status(" ".join(header_text.split()))
    hg, ag = parse_score(score_text)

    # I corner si leggono sempre: una partita già iniziata li ha anche se lo stato non si capisce
    corners, n_rows, corner_rows = None, 0, []
    tab = page.locator("a", has_text="Statistiche")
    tab_found = await tab.count()
    if tab_found:
        await tab.first.click()
        try:
            await page.wait_for_selector('div[class*="statistics"], .stat__row', timeout=6000)
        except Exception:
            pass
        await page.wait_for_timeout(800)
    corners, n_rows, corner_rows = await scan_corners(page)
    if corners is None and tab_found:
        # Le statistiche a volte non sono ancora caricate: secondo tentativo dopo una breve attesa
        await page.wait_for_timeout(3000)
        corners, n_rows, corner_rows = await scan_corners(page)

    # Stato non riconosciuto ma la partita ha già dati: è iniziata, stima il minuto dall'orario
    if status == "NS" and kickoff_ts and time.time() > kickoff_ts and (corners is not None or hg is not None):
        # Niente stima dall'orario (include l'intervallo e il recupero): meglio "in corso" che un minuto sbagliato
        status, minute = "LIVE", None

    print(f"      [diagnosi] stato={status_text!r} punteggio={score_text!r} "
          f"intestazione={' '.join(header_text.split())[:80]!r} tab_statistiche={tab_found} "
          f"righe={n_rows} righe_corner={corner_rows[:2]}")
    return {"status": status, "elapsed": minute, "hg": hg, "ag": ag, "corners": corners}


def post_update(callback, secret, token, matches):
    """Invia i dati al bot. Ritorna il codice HTTP (0 se irraggiungibile)."""
    for attempt in range(3):
        try:
            r = requests.post(
                f"{callback}/live-update",
                json={"token": token, "matches": matches},
                headers={"X-Live-Secret": secret},
                timeout=60,  # il servizio può impiegare un po' a svegliarsi
            )
            return r.status_code
        except requests.RequestException as e:
            print(f"   ⚠️ Invio al bot fallito (tentativo {attempt + 1}): {e}")
            time.sleep(5)
    return 0


def is_over(m):
    return m.get("corners") is not None and m["corners"] >= OVER_LINE


async def main():
    token = os.environ.get("LIVE_TOKEN", "")
    callback = os.environ.get("LIVE_CALLBACK", "").rstrip("/")
    secret = os.environ.get("LIVE_SECRET", "")
    matches = json.loads(os.environ.get("LIVE_MATCHES", "[]"))
    if not (token and callback and secret and matches):
        print("❌ Mancano LIVE_TOKEN / LIVE_CALLBACK / LIVE_SECRET / LIVE_MATCHES.")
        return 1

    t0 = time.time()
    urls = {}                 # id -> url della partita su diretta.it
    state = {}                # id -> ultimo dato letto
    finished = set()          # id delle partite finite (non si leggono più)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(user_agent=UA)
        page = await ctx.new_page()

        while time.time() - t0 < MAX_RUN_SECONDS:
            cycle_start = time.time()
            for m in matches:
                mid = str(m["id"])
                if mid in finished:
                    continue
                try:
                    kick = datetime.fromisoformat(m["kickoff"]).timestamp()
                except (ValueError, TypeError):
                    kick = 0
                if time.time() < kick - 120:
                    continue  # non ancora iniziata
                try:
                    if mid not in urls:
                        url = await find_match_url(page, m)
                        if not url:
                            print(f"   ⚠️ {m['home']} - {m['away']}: non trovata su diretta.it (riprovo)")
                            continue
                        urls[mid] = url
                    data = await read_match(page, urls[mid], kick)
                    prev = state.get(mid)
                    if data["corners"] is None and prev and prev.get("corners") is not None:
                        data["corners"] = prev["corners"]  # lettura fallita: tengo l'ultimo valore noto
                    state[mid] = data
                    print(f"   {m['home']} - {m['away']}: {data}")
                    if data["status"] in ("FT", "AET", "PEN", "PST", "CANC"):  # si segue fino alla fine, anche oltre i 9 corner
                        finished.add(mid)
                except Exception as e:
                    print(f"   ⚠️ Errore su {m['home']} - {m['away']}: {e}")

            if state:
                code = await asyncio.to_thread(post_update, callback, secret, token, state)
                print(f"📤 Inviato al bot: HTTP {code}")
                if code in (403, 410):
                    print("ℹ️ Il bot non segue più questo monitoraggio (o il segreto non coincide): mi fermo.")
                    break

            if len(finished) == len(matches):
                print("🏁 Tutte le partite concluse.")
                break
            await asyncio.sleep(max(5, POLL_SECONDS - (time.time() - cycle_start)))

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


"""
Corrispondenza tra i nomi delle squadre di API-Sports (usati dal bot) e quelli
di Diretta.it (usati dallo scraper nel database). Usato sia da scraper.py sia
da analyzer.py, così la logica è una sola.
"""

import unicodedata

EXPLICIT_TEAM_FIXES = {
    "Virtus Entella": "Entella", "Sudtirol": "Südtirol", "Hellas Verona": "Verona",
    "Vicenza Virtus": "L.R. Vicenza", "Manchester United": "Manchester Utd",
    "Nottingham Forest": "Nottingham", "Hull City": "Hull", "Atletico Madrid": "Atl. Madrid",
    "Deportivo La Coruna": "A Coruna", "Athletic Club": "Ath. Bilbao", "Barcelona": "Barcellona",
    "Real Betis": "Betis", "Sevilla": "Siviglia", "Stade Brestois 29": "Brest", "Nice": "Nizza",
    "Paris Saint Germain": "PSG", "Lyon": "Lione", "Toulouse": "Tolosa", "Strasbourg": "Strasburgo",
    "Marseille": "Marsiglia", "Estac Troyes": "Troyes", "Lille": "Lilla",
    "Borussia Dortmund": "Dortmund", "Werder Bremen": "Brema", "FSV Mainz 05": "Magonza",
    "Hamburger SV": "Amburgo", "FC Augsburg": "Augusta", "Bayern München": "Bayern",
    "VfB Stuttgart": "Stoccarda", "Eintracht Frankfurt": "Francoforte", "1. FC Köln": "Colonia",
    "Bayer Leverkusen": "Leverkusen", "1899 Hoffenheim": "Hoffenheim", "Union Berlin": "Union Berlino",
    "RB Leipzig": "RB Lipsia", "Borussia Mönchengladbach": "Monchengladbach",
    "SC Freiburg": "Friburgo", "FC Schalke 04": "Schalke", "SC Paderborn 07": "Paderborn",
}

_PREFIXES = ["1. FC ", "FSV ", "SV ", "SC ", "VfB ", "RB ", "AS ", "AC ", "FC ", "SpVgg ", "TSG "]


def _strip_accents(text):
    """Toglie gli accenti (Südtirol -> Sudtirol, Atlético -> Atletico)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def clean_team_name(name):
    if not name:
        return ""
    cleaned = name
    for p in _PREFIXES:
        if cleaned.startswith(p):
            cleaned = cleaned[len(p):]
    return _strip_accents(cleaned).strip().lower()


def teams_match(api_name, diretta_name):
    if not api_name or not diretta_name:
        return False
    fixed_api = EXPLICIT_TEAM_FIXES.get(api_name, api_name)
    c_api = clean_team_name(fixed_api)
    c_dir = clean_team_name(diretta_name)
    return c_api == c_dir or c_api in c_dir or c_dir in c_api


def find_in_index(api_name, index, apply_fixes=True, allow_contains=True):
    """
    Cerca una squadra in un dizionario {nome_pulito: dati}.
    Prima corrispondenza esatta (dopo le correzioni esplicite), poi per
    contenimento, ma solo se il candidato è uno solo (niente falsi positivi).
    """
    if not api_name:
        return None
    # apply_fixes=True: l'indice usa i nomi di Diretta (db cross);
    # apply_fixes=False: l'indice usa già i nomi di API-Sports (db corner).
    base = EXPLICIT_TEAM_FIXES.get(api_name, api_name) if apply_fixes else api_name
    c_api = clean_team_name(base)
    if c_api in index:
        return index[c_api]
    if not allow_contains or len(c_api) < 4:
        return None
    candidates = [k for k in index if len(k) >= 4 and (c_api in k or k in c_api)]
    if len(candidates) == 1:
        return index[candidates[0]]
    return None

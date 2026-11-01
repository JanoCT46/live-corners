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
    # Eredivisie (nomi API-Sports -> Diretta). Da verificare con check_nomi.py dopo il primo aggiornamento del db.
    "AZ Alkmaar": "Alkmaar", "NAC Breda": "Breda", "Go Ahead Eagles": "G.A. Eagles", "FC Groningen": "Groningen",
    "SC Heerenveen": "Heerenveen", "NEC Nijmegen": "Nijmegen", "PSV Eindhoven": "PSV", "Fortuna Sittard": "Sittard",
    "FC Twente": "Twente", "FC Utrecht": "Utrecht", "PEC Zwolle": "Zwolle", "RKC Waalwijk": "Waalwijk",
    # Bundesliga austriaca
    "Rheindorf Altach": "Altach", "FK Austria Wien": "Austria Vienna", "Grazer AK": "Grazer", "TSV Hartberg": "Hartberg",
    "LASK Linz": "LASK", "SV Ried": "Ried", "SK Rapid Wien": "SK Rapid", "RB Salzburg": "Salzburg",
    "Red Bull Salzburg": "Salzburg", "WSG Tirol": "Tirol", "WSG Swarovski Tirol": "Tirol", "Wolfsberger AC": "Wolfsberger",
    "Austria Klagenfurt": "A. Klagenfurt", "SK Sturm Graz": "Sturm Graz", "Blau-Weiß Linz": "BW Linz",
    # Serie A brasiliana
    "RB Bragantino": "Bragantino", "Fortaleza EC": "Fortaleza", "Vasco DA Gama": "Vasco", "Atletico Goianiense": "Atletico GO",
    "Botafogo": "Botafogo RJ", "Atletico Mineiro": "Atletico-MG", "Athletico Paranaense": "Athletico-PR",
    # MLS
    "Atlanta United FC": "Atlanta Utd", "Atlanta United": "Atlanta Utd", "FC Cincinnati": "Cincinnati", "D.C. United": "DC United",
    "Minnesota United FC": "Minnesota", "Minnesota United": "Minnesota", "New York City FC": "New York City",
    "Orlando City SC": "Orlando City", "San Diego": "San Diego FC", "Charlotte FC": "Charlotte", "Austin": "Austin FC",
    "Atletico Paranaense": "Athletico-PR", "Austria Lustenau": "A. Lustenau", "Rapid Vienna": "SK Rapid",
    "WSG Wattens": "Tirol", "GO Ahead Eagles": "G.A. Eagles",
    # Serie C (nomi API-Sports -> Diretta)
    "Atalanta II": "Atalanta U23", "Ostia Mare": "Ostiamare", "Desenzano Calvina": "Desenzano",
    "LA Galaxy": "Los Angeles Galaxy", "Vancouver Whitecaps FC": "Vancouver Whitecaps", "Seattle Sounders FC": "Seattle Sounders",
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
    c_dir = clean_team_name(diretta_name)
    # il secondo nome può essere di Diretta oppure di API-Sports (matches_stats): si prova sia con la correzione
    # esplicita (nome API -> Diretta) sia con il nome così com'è (altrimenti "GO Ahead Eagles" -> "G.A. Eagles"
    # non trova più "GO Ahead Eagles" di API-Sports)
    for cand in {EXPLICIT_TEAM_FIXES.get(api_name, api_name), api_name}:
        c_api = clean_team_name(cand)
        if c_api == c_dir or c_api in c_dir or c_dir in c_api:
            return True
    return False


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

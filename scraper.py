#!/usr/bin/env python3
"""
Garmin Value Lab — Moteur d'automatisation Backend (Veille Prix UE + Actualités RSS)
Exécuté toutes les 12h via GitHub Actions pour alimenter data.json.
"""

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
import urllib.request

# ==============================================================================
# CONFIGURATION DE LA WATCHLIST (Modèles suivis)
# ==============================================================================
WATCHLIST = [
    {
        "id": "tactix-8-amoled-47",
        "name": "Tactix 8 AMOLED 47mm",
        "queries": ["Garmin Tactix 8 AMOLED 47", "Garmin Tactix 8 47mm"],
        "required_tokens": ["tactix", "8"],
        "forbidden_tokens": ["7", "delta", "charlie", "pro ballistic"],
        "min_valid_price": 650,
        "max_valid_price": 1550
    },
    {
        "id": "tactix-7-amoled",
        "name": "Tactix 7 AMOLED",
        "queries": ["Garmin Tactix 7 AMOLED"],
        "required_tokens": ["tactix", "7", "amoled"],
        "forbidden_tokens": ["8", "standard", "pro"],
        "min_valid_price": 450,
        "max_valid_price": 1400
    },
    {
        "id": "fenix-8-amoled-47",
        "name": "Fenix 8 AMOLED 47mm",
        "queries": ["Garmin Fenix 8 AMOLED 47mm", "Garmin Fenix 8 47"],
        "required_tokens": ["fenix", "8"],
        "forbidden_tokens": ["7", "7x", "7s", "6", "e", "solar", "51mm", "43mm"],
        "min_valid_price": 450,
        "max_valid_price": 1200
    },
    {
        "id": "enduro-3",
        "name": "Enduro 3 Solar 51mm",
        "queries": ["Garmin Enduro 3"],
        "required_tokens": ["enduro", "3"],
        "forbidden_tokens": ["2", "1"],
        "min_valid_price": 420,
        "max_valid_price": 1000
    }
]

# ==============================================================================
# FILTRES ANTI-ACCESSOIRES & FAUX POSITIFS (Multilingue FR / DE / ES / IT / EN)
# ==============================================================================
GLOBAL_NEGATIVE_KEYWORDS = [
    "bracelet", "strap", "band", "armband", "correa", "cinturino",
    "coque", "case", "hülle", "funda", "custodia", "bumper", "protège",
    "verre", "film", "screen protector", "schutzfolie", "cristal templado",
    "chargeur", "cable", "charger", "ladekabel", "cargador", "dock", "socle",
    "boite vide", "empty box", "leere verpackung", "caja vacia", "manuel",
    "for parts", "hs", "cassé", "defekt", "écran cassé", "pièces"
]

# Flux RSS eBay Europe (recherche publique structurée)
EBAY_REGIONS = [
    {"country": "FR", "domain": "www.ebay.fr"},
    {"country": "DE", "domain": "www.ebay.de"},
    {"country": "ES", "domain": "www.ebay.es"},
    {"country": "IT", "domain": "www.ebay.it"}
]

# Flux RSS optionnels (Leboncoin / Kleinanzeigen via pont RSS type PolitePol / RSSHub)
CUSTOM_CLASSIFIEDS_RSS = [
    # Exemple : "https://politepol.com/fd/votre-flux-leboncoin-garmin.xml"
]

# Flux Actualités Garmin spécialisés
NEWS_FEEDS = [
    {"source": "Garmin Newsroom", "url": "https://www.garmin.com/en-US/newsroom/feed/"},
    {"source": "DC Rainmaker", "url": "https://www.dcrainmaker.com/feed"},
    {"source": "the5krunner", "url": "https://the5krunner.com/feed/"},
    {"source": "Gadgets & Wearables", "url": "https://gadgetsandwearables.com/feed/"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
}


def fetch_xml(url: str, timeout: int = 12) -> Optional[ET.Element]:
    """Télécharge et parse un flux XML/RSS de manière sécurisée."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            return ET.fromstring(content)
    except Exception as exc:
        print(f"[WARN] Échec lecture flux {url}: {exc}")
        return None


def extract_price_eur(text: str) -> Optional[float]:
    """Extrait un montant en euros depuis un titre ou une description RSS."""
    if not text:
        return None
    # Recherche patterns type 899,00 EUR / € 899.00 / 850€
    patterns = [
        r"(\d{1,2}[\s\.]?\d{3}(?:[,\.]\d{1,2})?)\s*(?:€|EUR)",
        r"(?:€|EUR)\s*(\d{1,2}[\s\.]?\d{3}(?:[,\.]\d{1,2})?)",
        r"\b([4-9]\d{2}|1\d{3})(?:[,\.]\d{2})?\s*(?:€|EUR)"
    ]
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            raw = match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def is_valid_listing(title: str, price: Optional[float], watch_cfg: Dict) -> bool:
    """Valide qu'une annonce correspond exactement au modèle et exclut les accessoires."""
    if price is None:
        return False
    if not (watch_cfg["min_valid_price"] <= price <= watch_cfg["max_valid_price"]):
        return False

    t_lower = title.lower()

    # 1. Exclusion des accessoires
    if any(neg in t_lower for neg in GLOBAL_NEGATIVE_KEYWORDS):
        return False

    # 2. Vérification des tokens obligatoires
    if not all(req in t_lower for req in watch_cfg["required_tokens"]):
        return False

    # 3. Exclusion des générations / tailles incompatibles
    for forb in watch_cfg["forbidden_tokens"]:
        # Match mot entier pour éviter d'exclure "47mm" si "7" est interdit
        if re.search(rf"\b{re.escape(forb)}\b", t_lower):
            return False

    return True


def detect_condition(text: str) -> str:
    """Détermine si l'annonce concerne un produit Neuf ou Occasion."""
    t_lower = text.lower()
    new_markers = ["neuf", "neu", "nuevo", "nuovo", "new", "scellé", "sealed", "ovp", "garantie 24"]
    if any(m in t_lower for m in new_markers):
        return "Neuf"
    return "Occasion"


def scrape_watch_prices(watch_cfg: Dict) -> List[Dict]:
    """Interroge les flux européens eBay et flux personnalisés pour un modèle donné."""
    candidates = []
    seen_urls = set()

    for query in watch_cfg["queries"]:
        encoded_q = urllib.parse.quote_plus(query)
        for region in EBAY_REGIONS:
            # Flux RSS natif eBay (_rss=1, LH_BIN=1 pour Achat Immédiat)
            rss_url = f"https://{region['domain']}/sch/i.html?_nkw={encoded_q}&LH_BIN=1&_sop=15&_rss=1"
            root = fetch_xml(rss_url)
            if root is None:
                continue

            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()

                if not link or link in seen_urls:
                    continue

                price = extract_price_eur(f"{title} {desc}")
                if is_valid_listing(title, price, watch_cfg):
                    seen_urls.add(link)
                    candidates.append({
                        "title": title[:95],
                        "price": round(price, 2),
                        "condition": detect_condition(f"{title} {desc}"),
                        "country": region["country"],
                        "source": "eBay",
                        "url": link,
                        "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    })
            time.sleep(0.6)

    # Lecture des flux annonces personnalisés (Leboncoin / Kleinanzeigen)
    for custom_url in CUSTOM_CLASSIFIEDS_RSS:
        root = fetch_xml(custom_url)
        if root is None:
            continue
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if not link or link in seen_urls:
                continue
            price = extract_price_eur(f"{title} {desc}")
            if is_valid_listing(title, price, watch_cfg):
                seen_urls.add(link)
                candidates.append({
                    "title": title[:95],
                    "price": round(price, 2),
                    "condition": detect_condition(f"{title} {desc}"),
                    "country": "UE",
                    "source": "Petites Annonces",
                    "url": link,
                    "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d")
                })

    # Tri par prix croissant et sélection des 3 meilleures opportunités
    candidates.sort(key=lambda x: x["price"])
    return candidates[:3]


def clean_html(raw_html: str) -> str:
    """Supprime les balises HTML d'un résumé RSS."""
    clean = re.sub(r"<[^>]+>", " ", raw_html or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return (clean[:175] + "…") if len(clean) > 175 else clean


def scrape_garmin_news() -> List[Dict]:
    """Agrège les 6 derniers articles pertinents sur l'écosystème Garmin."""
    articles = []
    garmin_keywords = ["garmin", "tactix", "fenix", "enduro", "forerunner", "instinct", "epix", "connect"]

    for feed in NEWS_FEEDS:
        root = fetch_xml(feed["url"])
        if root is None:
            continue

        for item in root.findall(".//item")[:10]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            desc = clean_html(item.findtext("description") or "")

            full_text = f"{title} {desc}".lower()
            if any(kw in full_text for kw in garmin_keywords):
                articles.append({
                    "title": title,
                    "source": feed["source"],
                    "date": pub_date[:16] if pub_date else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "summary": desc or "Consultez l'analyse complète sur le site source.",
                    "url": link
                })

    # Déduplication par URL et conservation des 6 plus récents
    unique_articles = []
    seen = set()
    for art in articles:
        if art["url"] not in seen:
            seen.add(art["url"])
            unique_articles.append(art)
    return unique_articles[:6]


def main():
    print("=== Démarrage veille Garmin Value Lab ===")
    prices_payload = {}

    for watch in WATCHLIST:
        print(f"Analyse marché UE : {watch['name']}...")
        best_offers = scrape_watch_prices(watch)
        prices_payload[watch["id"]] = {
            "name": watch["name"],
            "offers": best_offers
        }
        print(f"  -> {len(best_offers)} offres valides retenues.")

    print("Agrégation des flux d'actualités Garmin...")
    news_payload = scrape_garmin_news()
    print(f"  -> {len(news_payload)} articles retenus.")

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "prices": prices_payload,
        "news": news_payload
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("=== Fichier data.json généré avec succès ===")


if __name__ == "__main__":
    main()
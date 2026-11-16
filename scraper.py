#!/usr/bin/env python3
"""
Garmin Value Lab — Moteur de veille UE haute précision (Prix Planchers + News)
"""

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
import urllib.request

WATCHLIST = [
    {
        "id": "tactix-8-amoled-47",
        "name": "Tactix 8 AMOLED 47mm",
        "queries": ["Garmin Tactix 8", "Tactix 8 AMOLED"],
        "required_tokens": ["tactix", "8"],
        "forbidden_tokens": ["7", "delta", "charlie", "51mm", "solar"],
        "min_valid_price": 580,
        "max_valid_price": 1500
    },
    {
        "id": "tactix-7-amoled",
        "name": "Tactix 7 AMOLED",
        "queries": ["Garmin Tactix 7 AMOLED", "Tactix 7 AMOLED"],
        "required_tokens": ["tactix", "7", "amoled"],
        "forbidden_tokens": ["8", "delta", "charlie"],
        "min_valid_price": 420,
        "max_valid_price": 1350
    },
    {
        "id": "fenix-8-amoled-47",
        "name": "Fenix 8 AMOLED 47mm",
        "queries": ["Garmin Fenix 8 47", "Fenix 8 AMOLED 47mm"],
        "required_tokens": ["fenix", "8"],
        "forbidden_tokens": ["7", "7x", "7s", "6", "e", "solar", "51mm", "43mm"],
        "min_valid_price": 450,
        "max_valid_price": 1150
    },
    {
        "id": "enduro-3",
        "name": "Enduro 3 Solar 51mm",
        "queries": ["Garmin Enduro 3", "Enduro 3 Garmin"],
        "required_tokens": ["enduro", "3"],
        "forbidden_tokens": ["2", "1"],
        "min_valid_price": 420,
        "max_valid_price": 980
    }
]

GLOBAL_NEGATIVE_KEYWORDS = [
    "bracelet", "strap", "band", "armband", "correa", "cinturino", "quickfit",
    "coque", "case", "hülle", "funda", "custodia", "bumper", "protège", "bezel",
    "verre", "film", "screen protector", "schutzfolie", "cristal templado", "tempered",
    "chargeur", "cable", "charger", "ladekabel", "cargador", "dock", "socle", "adapter",
    "boite vide", "empty box", "leere verpackung", "caja vacia", "manuel", "notice",
    "for parts", "hs", "cassé", "defekt", "écran cassé", "pièces", "dummy", "factice"
]

# Marchés européens (incluant DE, FR, ES, IT, NL, AT)
EBAY_REGIONS = [
    {"country": "FR", "domain": "www.ebay.fr"},
    {"country": "DE", "domain": "www.ebay.de"},
    {"country": "ES", "domain": "www.ebay.es"},
    {"country": "IT", "domain": "www.ebay.it"},
    {"country": "NL", "domain": "www.ebay.nl"}
]

CUSTOM_CLASSIFIEDS_RSS = [
    # Ajoutez ici vos flux RSS Leboncoin / Kleinanzeigen / Dealabs (ex: via PolitePol ou RSS.app)
]

NEWS_FEEDS = [
    {"source": "Garmin Newsroom", "url": "https://www.garmin.com/en-US/newsroom/feed/"},
    {"source": "DC Rainmaker", "url": "https://www.dcrainmaker.com/feed"},
    {"source": "the5krunner", "url": "https://the5krunner.com/feed/"},
    {"source": "Gadgets & Wearables", "url": "https://gadgetsandwearables.com/feed/"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
}


def fetch_xml(url: str, timeout: int = 12) -> Optional[ET.Element]:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return ET.fromstring(resp.read())
    except Exception as exc:
        print(f"[WARN] Flux inaccessible ({url}): {exc}")
        return None


def extract_price_eur(text: str) -> Optional[float]:
    if not text:
        return None
    # Nettoyage des balises HTML dans la description eBay pour lire le vrai bloc prix
    clean_text = re.sub(r"<[^>]+>", " ", text)
    clean_text = re.sub(r"\s+", " ", clean_text)

    # Priorité aux montants explicitement libellés en EUR / €
    eur_patterns = [
        r"(?:EUR|€)\s*(\d{1,3}(?:[\s\.,]\d{3})*(?:[,\.]\d{2})?)",
        r"(\d{1,3}(?:[\s\.,]\d{3})*(?:[,\.]\d{2})?)\s*(?:EUR|€)"
    ]
    for pat in eur_patterns:
        matches = re.findall(pat, clean_text, re.IGNORECASE)
        for m in matches:
            # Normalisation européenne : "1.199,00" -> "1199.00" ou "850,00" -> "850.00"
            normalized = m.replace(" ", "")
            if "," in normalized and "." in normalized:
                normalized = normalized.replace(".", "").replace(",", ".")
            elif "," in normalized:
                normalized = normalized.replace(",", ".")
            try:
                val = float(normalized)
                if 200 <= val <= 3500:
                    return val
            except ValueError:
                continue
    return None


def is_valid_listing(title: str, price: Optional[float], watch_cfg: Dict) -> bool:
    if price is None:
        return False
    if not (watch_cfg["min_valid_price"] <= price <= watch_cfg["max_valid_price"]):
        return False

    t_lower = title.lower()

    if any(neg in t_lower for neg in GLOBAL_NEGATIVE_KEYWORDS):
        return False

    if not all(req in t_lower for req in watch_cfg["required_tokens"]):
        return False

    for forb in watch_cfg["forbidden_tokens"]:
        if re.search(rf"\b{re.escape(forb)}\b", t_lower):
            return False

    return True


def detect_condition(text: str) -> str:
    t_lower = text.lower()
    new_markers = ["neuf", "neu", "nuevo", "nuovo", "brand new", "scellé", "sealed", "ovp", "garantie 24"]
    if any(m in t_lower for m in new_markers):
        return "Neuf"
    return "Occasion"


def scrape_watch_prices(watch_cfg: Dict) -> List[Dict]:
    candidates = []
    seen_urls = set()

    for query in watch_cfg["queries"]:
        encoded_q = urllib.parse.quote_plus(query)
        for region in EBAY_REGIONS:
            # On interroge Achat Immédiat ET Enchères triés par prix croissant (_sop=15)
            rss_url = (
                f"https://{region['domain']}/sch/i.html?_nkw={encoded_q}"
                f"&_udlo={watch_cfg['min_valid_price']}&_udhi={watch_cfg['max_valid_price']}"
                f"&_sop=15&_rss=1"
            )
            root = fetch_xml(rss_url)
            if root is None:
                continue

            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip().split("?")[0]
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
                        "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                    })
            time.sleep(0.4)

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
                    "source": "Annonce",
                    "url": link,
                    "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                })

    candidates.sort(key=lambda x: x["price"])
    return candidates[:3]


def clean_html(raw_html: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", raw_html or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return (clean[:175] + "…") if len(clean) > 175 else clean


def scrape_garmin_news() -> List[Dict]:
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

    news_payload = scrape_garmin_news()

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "prices": prices_payload,
        "news": news_payload
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("=== Fichier data.json généré ===")


if __name__ == "__main__":
    main()

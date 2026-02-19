import gspread
import os
import json
import time
import re
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW_TZ = ZoneInfo("Europe/Warsaw")


# --- KONFIGURACJA ---
ARKUSZ_ID = '1JdrNZr4eeX8Vc1w-V7XgB2ysdnxAQg_KqCE3b6RHCtc'
NAZWA_ZAKLADKI = 'Pabianice_Najem_OTO'
URL_OTODOM = 'https://www.otodom.pl/pl/wyniki/wynajem/lokal/lodzkie/pabianicki/gmina-miejska--pabianice/pabianice?by=DEFAULT&direction=DESC'

def extract_numbers(text):
    if not text:
        return '0'
    text = text.replace('\xa0', '').replace(' ', '').replace(',', '.')
    match = re.search(r'(\d+\.?\d*)', text)
    return match.group(1) if match else '0'

def clean_and_convert_to_number(value):
    try:
        return float(value)
    except:
        return 0

def authorize_google_sheets():
    print("Autoryzacja do Google Sheets...")
    try:
        creds_json = os.environ.get('G_SHEETS_JSON')
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(ARKUSZ_ID).worksheet(NAZWA_ZAKLADKI)
    except Exception as e:
        print(f"BŁĄD autoryzacji: {e}")
        return None

def setup_selenium_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    )

    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def _to_amount(pl_amount_text):
    if not pl_amount_text:
        return 0.0
    t = pl_amount_text.replace('\xa0', ' ').strip()
    t = t.replace(' ', '').replace(',', '.')
    m = re.search(r'(\d+(?:\.\d+)?)', t)
    return float(m.group(1)) if m else 0.0

def parse_rent_and_fees(card_text):
    """
    Fallback na regex z tekstu (gdy DOM nie da się złapać).
    Zwraca: (koszt_najmu, czynsz)
    """
    if not card_text:
        return 0, 0

    txt = card_text.replace('\xa0', ' ')
    txt_low = txt.lower()

    matches = list(re.finditer(r'(\d[\d\s,\.]*)\s*zł', txt, flags=re.IGNORECASE))

    rent_candidates = []
    fee_candidates = []

    for m in matches:
        amount_str = m.group(1)
        amount = _to_amount(amount_str)
        if amount <= 0:
            continue

        start, end = m.start(), m.end()
        ctx_start = max(0, start - 35)
        ctx_end = min(len(txt), end + 35)
        ctx = txt_low[ctx_start:ctx_end]

        if ('kaucj' in ctx) or ('depozyt' in ctx):
            continue

        if ('czynsz' in ctx) or ('opłat' in ctx) or ('administr' in ctx) or ('eksploat' in ctx):
            fee_candidates.append(amount)
        else:
            rent_candidates.append(amount)

    koszt_najmu = max(rent_candidates) if rent_candidates else 0
    czynsz = max(fee_candidates) if fee_candidates else 0

    if czynsz == 0 and ('czynsz' in txt_low):
        all_amounts = []
        for m in matches:
            amount = _to_amount(m.group(1))
            if amount > 0:
                start, end = m.start(), m.end()
                ctx = txt_low[max(0, start - 35):min(len(txt), end + 35)]
                if ('kaucj' in ctx) or ('depozyt' in ctx):
                    continue
                all_amounts.append(amount)
        all_amounts = sorted(all_amounts, reverse=True)
        if len(all_amounts) >= 2:
            koszt_najmu = all_amounts[0]
            czynsz = all_amounts[1]

    return koszt_najmu, czynsz

def extract_prices_from_card_dom(card):
    """
    Zwraca: (koszt_najmu, cena_za_m2, czynsz)
    - koszt najmu: <span data-sentry-element="MainPrice">396 zł</span>
    - cena za m2:  <span>22 zł/m²</span> (szukamy po treści 'zł/m²' albo 'zł/m2')
    - czynsz:      "+ czynsz: 600 zł/miesiąc" (wyciągamy TYLKO kwotę po słowie czynsz)
    """
    koszt_najmu = 0.0
    cena_za_m2 = 0.0
    czynsz = 0.0

    # --- 1) KOSZT NAJMU (MainPrice) z DOM ---
    main_price_selectors = [
        'span[data-sentry-element="MainPrice"]',
        '[data-sentry-element="MainPrice"]',
    ]

    for sel in main_price_selectors:
        try:
            els = card.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                t = (el.text or "").strip()
                if not t:
                    try:
                        t = (el.get_attribute("textContent") or "").strip()
                    except:
                        t = ""
                if t and 'zł' in t.lower():
                    val = _to_amount(t)
                    if val > koszt_najmu:
                        koszt_najmu = val
        except:
            pass

    # --- 2) Cena za m2 z DOM (nie po klasie, tylko po treści) ---
    # bierzemy wszystkie spany i szukamy takiego, który zawiera "zł/m²" lub "zł/m2"
    try:
        spans = card.find_elements(By.TAG_NAME, "span")
        for sp in spans:
            t = (sp.text or "").strip()
            if not t:
                try:
                    t = (sp.get_attribute("textContent") or "").strip()
                except:
                    t = ""
            if not t:
                continue

            t_norm = t.replace('\xa0', ' ').strip().lower()
            # typowe: "22 zł/m²"
            if ("zł/m²" in t_norm) or ("zł/m2" in t_norm):
                val = _to_amount(t_norm)
                if val > 0:
                    cena_za_m2 = val
                    break
    except:
        pass

    # --- 3) innerHTML fallback ---
    inner_html = ""
    try:
        inner_html = card.get_attribute("innerHTML") or ""
    except:
        inner_html = ""

    if inner_html:
        ih = inner_html.replace("&nbsp;", " ").replace("\xa0", " ")

        if koszt_najmu == 0:
            m = re.search(
                r'data-sentry-element="MainPrice"[^>]*>\s*([\d\s,\.]+)\s*zł',
                ih,
                flags=re.IGNORECASE
            )
            if m:
                koszt_najmu = _to_amount(m.group(1))

        if cena_za_m2 == 0:
            # łapie "22 zł/m²" albo "22 zł/m2"
            m2 = re.search(r'([\d\s,\.]+)\s*zł\s*/\s*(?:m²|m2)', ih, flags=re.IGNORECASE)
            if m2:
                cena_za_m2 = _to_amount(m2.group(1))

        # czynsz: tylko kwota po słowie czynsz
        m_fee = re.search(r'czynsz[^0-9]*([\d\s,\.]+)\s*zł', ih, flags=re.IGNORECASE)
        if m_fee:
            czynsz = _to_amount(m_fee.group(1))

    # --- 4) Asekuracja: czynsz z innerText ---
    if czynsz == 0:
        try:
            inner_text = (card.get_attribute("innerText") or "").replace('\xa0', ' ')
            m_fee2 = re.search(r'czynsz[^0-9]*([\d\s,\.]+)\s*zł', inner_text, flags=re.IGNORECASE)
            if m_fee2:
                czynsz = _to_amount(m_fee2.group(1))
        except:
            pass

    # --- 5) Asekuracja: cena/m2 z innerText ---
    if cena_za_m2 == 0:
        try:
            inner_text = (card.get_attribute("innerText") or "").replace('\xa0', ' ')
            m2t = re.search(r'([\d\s,\.]+)\s*zł\s*/\s*(?:m²|m2)', inner_text, flags=re.IGNORECASE)
            if m2t:
                cena_za_m2 = _to_amount(m2t.group(1))
        except:
            pass

    return koszt_najmu, cena_za_m2, czynsz

def process_page(driver):
    rows_to_append = []
    print("Przeszukuję stronę w poszukiwaniu ofert...")

    for i in range(3):
        driver.execute_script(f"window.scrollTo(0, {(i + 1) * 800});")
        time.sleep(2)

    listing_cards = driver.find_elements(By.CSS_SELECTOR, 'article[data-cy="listing-item"]')
    if not listing_cards:
        listing_cards = driver.find_elements(By.CSS_SELECTOR, 'div[data-cy="search.listing.organic"] article')
    if not listing_cards:
        listing_cards = driver.find_elements(By.TAG_NAME, 'article')

    print(f"Wykryto {len(listing_cards)} potencjalnych ogłoszeń.")

    for card in listing_cards:
        try:
            link_el = card.find_element(By.TAG_NAME, 'a')
            link = link_el.get_attribute('href')
            if not link or 'pl/oferta/' not in link:
                continue

            data_now = datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")
            card_text = card.text
            lines = [line.strip() for line in card_text.split('\n') if line.strip()]

            # --- TYTUŁ ---
            tytul = "Brak tytułu"
            for line in lines:
                if '/' in line and len(line) < 10:
                    continue
                if 'zł' in line.lower():
                    continue
                if 'tomaszów' in line.lower() and len(line) < 25:
                    continue
                tytul = line
                break

            # --- ADRES ---
            adres = "Tomaszów Mazowiecki"
            for line in lines:
                if 'tomaszów' in line.lower() or 'mazowiecki' in line.lower():
                    if 'zł' not in line.lower():
                        adres = line
                        break

            # --- CENY: E = cena_najmu, F = cena_za_m2 ---
            cena_najmu_dom, cena_m2_dom, czynsz_dom = extract_prices_from_card_dom(card)
            cena_najmu_txt, czynsz_txt = parse_rent_and_fees(card_text)

            # preferuj DOM; fallback do tekstu
            cena_najmu = cena_najmu_dom if cena_najmu_dom > 0 else cena_najmu_txt
            czynsz = czynsz_dom if czynsz_dom > 0 else czynsz_txt

            # cena za m2: jak DOM nie dał, policz z cena_najmu/metraz (tylko gdy metraż > 0)
            # (ale najpierw spróbujemy wprost z DOM)
            cena_za_m2 = cena_m2_dom

            # --- PARAMETRY (Pokoje, m2, Piętro) ---
            pokoje, metraz, pietro = "0", "0", "0"
            for line in lines:
                l_low = line.lower()
                if 'poko' in l_low:
                    pokoje = extract_numbers(line)
                if 'm²' in l_low or 'm2' in l_low:
                    metraz = extract_numbers(line)
                if 'piętr' in l_low:
                    pietro = extract_numbers(line)

            # jeśli dalej brak cena/m2, a mamy metraż i cenę najmu -> wylicz
            if (cena_za_m2 == 0 or cena_za_m2 is None) and metraz:
                try:
                    mval = float(str(metraz).replace(',', '.'))
                    if mval > 0 and cena_najmu > 0:
                        cena_za_m2 = round(cena_najmu / mval, 2)
                except:
                    pass

            # --- OFERENT ---
            typ = "Oferta prywatna"
            wystawca = "Osoba prywatna"
            if "biuro" in card_text.lower() or "agency" in card_text.lower():
                typ = "Biuro nieruchomości"
                wystawca = lines[-1] if len(lines) > 0 else "Biuro"

            # E = cena_najmu, F = cena_za_m2 (TAKO MA BYĆ)
            # DODATKOWO: czynsz dopisany na końcu jako extra kolumna (żeby nie zgubić danych)
            rows_to_append.append([
                data_now, link, tytul, adres,
                cena_najmu, cena_za_m2,
                pokoje, metraz, pietro, typ, wystawca,
                "Brak Danych (Poza Kartą)", "Brak opisu (Poza Kartą)",
                czynsz  # <- jeśli nie chcesz tej kolumny, usuń tę wartość
            ])
        except:
            continue

    return rows_to_append

def main():
    zakladka = authorize_google_sheets()
    driver = setup_selenium_driver()
    if not zakladka or not driver:
        return

    try:
        print(f"Otwieram: {URL_OTODOM}")
        driver.get(URL_OTODOM)
        time.sleep(12)

        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            ).click()
            print("Cookies OK.")
        except:
            pass

        data = process_page(driver)
        if data:
            print(f"Zapisuję {len(data)} ofert do Google Sheets...")
            zakladka.append_rows(data)
            print("ZAPIS ZAKOŃCZONY SUKCESEM!")
        else:
            print(f"Nie znaleziono ofert. Tytuł strony: {driver.title}")

    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()

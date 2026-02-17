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

# --- KONFIGURACJA ---
ARKUSZ_ID = '1JdrNZr4eeX8Vc1w-V7XgB2ysdnxAQg_KqCE3b6RHCtc'
NAZWA_ZAKLADKI = 'TOM_OTO'
URL_OTODOM = 'https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/lodzkie/tomaszowski/gmina-miejska--tomaszow-mazowiecki/tomaszow-mazowiecki?ownerTypeSingleSelect=ALL'

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
    # przyjmuje np. "1 200", "1 200,50", "1200" i zwraca float
    if not pl_amount_text:
        return 0.0
    t = pl_amount_text.replace('\xa0', ' ').strip()
    t = t.replace(' ', '').replace(',', '.')
    m = re.search(r'(\d+(?:\.\d+)?)', t)
    return float(m.group(1)) if m else 0.0

def parse_rent_and_fees(card_text):
    """
    Zwraca:
      (koszt_najmu, czynsz)

    Logika:
    - wyciąga wszystkie wystąpienia: "<kwota> zł"
    - patrzy na kontekst (okno znaków przed/po)
    - jeśli w kontekście jest 'kaucja' -> ignoruj
    - jeśli w kontekście jest 'czynsz'/'opłaty'/'administr' -> traktuj jako czynsz
    - pozostałe kwoty -> kandydaci na koszt najmu
    - koszt najmu: największa z kandydatów
    - czynsz: największa z czynszowych (jeśli brak -> 0)
    """
    if not card_text:
        return 0, 0

    txt = card_text.replace('\xa0', ' ')
    txt_low = txt.lower()

    # Znajdź wszystkie kwoty z "zł" wraz ze span
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

        # Odfiltruj kaucje / depozyty
        if ('kaucj' in ctx) or ('depozyt' in ctx):
            continue

        # Czynsz / opłaty administracyjne / eksploatacyjne
        if ('czynsz' in ctx) or ('opłat' in ctx) or ('administr' in ctx) or ('eksploat' in ctx):
            fee_candidates.append(amount)
        else:
            rent_candidates.append(amount)

    koszt_najmu = max(rent_candidates) if rent_candidates else 0
    czynsz = max(fee_candidates) if fee_candidates else 0

    # Dodatkowa asekuracja:
    # jeśli nie wykryto czynszu po kontekście, ale są 2+ sensowne kwoty,
    # a w całym tekście jest słowo "czynsz", to spróbuj wziąć "mniejszą" jako czynsz.
    if czynsz == 0 and ('czynsz' in txt_low):
        # weź wszystkie kwoty bez kaucji i spróbuj odgadnąć
        all_amounts = []
        for m in matches:
            amount = _to_amount(m.group(1))
            if amount > 0:
                start, end = m.start(), m.end()
                ctx = txt_low[max(0, start - 35):min(len(txt), end + 35)]
                if ('kaucj' in ctx) or ('depozyt' in ctx):
                    continue
                all_amounts.append(amount)
        # heurystyka: największa to najem, druga największa to czynsz
        all_amounts = sorted(all_amounts, reverse=True)
        if len(all_amounts) >= 2:
            koszt_najmu = all_amounts[0]
            czynsz = all_amounts[1]

    return koszt_najmu, czynsz

def process_page(driver):
    rows_to_append = []
    print("Przeszukuję stronę w poszukiwaniu ofert...")

    # Przewijanie - bardzo ważne dla doładowania ofert
    for i in range(3):
        driver.execute_script(f"window.scrollTo(0, {(i + 1) * 800});")
        time.sleep(2)

    # Próbujemy znaleźć karty ogłoszeń różnymi metodami
    listing_cards = driver.find_elements(By.CSS_SELECTOR, 'article[data-cy="listing-item"]')
    if not listing_cards:
        listing_cards = driver.find_elements(By.CSS_SELECTOR, 'div[data-cy="search.listing.organic"] article')
    if not listing_cards:
        listing_cards = driver.find_elements(By.TAG_NAME, 'article')

    print(f"Wykryto {len(listing_cards)} potencjalnych ogłoszeń.")

    for card in listing_cards:
        try:
            # 1. Pobieramy link - jeśli go nie ma, to nie jest ogłoszenie
            link_el = card.find_element(By.TAG_NAME, 'a')
            link = link_el.get_attribute('href')
            if not link or 'pl/oferta/' not in link:
                continue

            data_now = time.strftime("%Y-%m-%d %H:%M:%S")
            card_text = card.text
            lines = [line.strip() for line in card_text.split('\n') if line.strip()]

            # --- INTELIGENTNY TYTUŁ ---
            tytul = "Brak tytułu"
            for line in lines:
                # Pomijamy licznik zdjęć (np. 1 / 10)
                if '/' in line and len(line) < 10:
                    continue
                # Pomijamy ceny (wszystko co ma zł)
                if 'zł' in line.lower():
                    continue
                # Pomijamy adresy (miejscowości) w pierwszej linii
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

            # --- CENY: KOSZT NAJMU (E) + CZYNSZ (F) ---
            cena_najmu, czynsz = parse_rent_and_fees(card_text)

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

            # --- OFERENT ---
            typ = "Oferta prywatna"
            wystawca = "Osoba prywatna"
            if "biuro" in card_text.lower() or "agency" in card_text.lower():
                typ = "Biuro nieruchomości"
                wystawca = lines[-1] if len(lines) > 0 else "Biuro"

            # KOLEJNOŚĆ KOLUMN:
            # A Data Scrapping
            # B URL Ogłoszenia
            # C Tytuł Ogłoszenia
            # D Adres
            # E Koszt Najmu
            # F Czynsz
            rows_to_append.append([
                data_now, link, tytul, adres,
                cena_najmu, czynsz,
                pokoje, metraz, pietro, typ, wystawca,
                "Brak Danych (Poza Kartą)", "Brak opisu (Poza Kartą)"
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

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
    if not text: return '0'
    processed_text = text.replace(' ', '').replace(',', '.')
    match = re.search(r'(\d+\.?\d*)', processed_text)
    return match.group(1) if match else '0'

def clean_and_convert_to_number(value):
    try: return float(value)
    except: return 0

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
    # Kluczowe: udawanie prawdziwej przeglądarki
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
    
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    # Ukrywanie faktu bycia botem przed skryptami JS
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def process_page(driver):
    rows_to_append = []
    # Przewijanie dla załadowania elementów
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(1)
    
    # Szukamy konkretnych kart ogłoszeń
    listing_cards = driver.find_elements(By.CSS_SELECTOR, 'article[data-cy="listing-item"]')
    print(f"Znaleziono {len(listing_cards)} ogłoszeń.")

    for card in listing_cards:
        try:
            # 1. Podstawowe dane (Tytuł, Link, Adres)
            link_el = card.find_element(By.CSS_SELECTOR, 'a[data-cy="listing-item-link"]')
            link = link_el.get_attribute('href')
            
            # Pobieramy konkretnie tytuł, pomijając licznik zdjęć
            tytul = card.find_element(By.CSS_SELECTOR, 'p[data-cy="listing-item-title"]').text.strip()
            
            # Pobieramy konkretnie adres
            try:
                adres = card.find_element(By.CSS_SELECTOR, 'p[data-cy="listing-item-address"]').text.strip()
            except:
                adres = "Brak adresu"

            # 2. Cena i Czynsz
            full_card_text = card.text.lower()
            try:
                cena_raw = card.find_element(By.CSS_SELECTOR, 'span[data-cy="listing-item-price"]').text
                cena = clean_and_convert_to_number(extract_numbers(cena_raw))
            except:
                cena = 0

            # Szukanie czynszu w tekście karty
            czynsz = 0
            if "+ czynsz" in full_card_text:
                match_cz = re.search(r'czynsz[:\s]*([\d\s,]+)', full_card_text)
                if match_cz:
                    czynsz = clean_and_convert_to_number(extract_numbers(match_cz.group(1)))

            # 3. Parametry (Pokoje, m2, Piętro) - szukamy wewnątrz listy <dl>
            pokoje, metraz, pietro = "0", "0", "0"
            specs = card.find_elements(By.CSS_SELECTOR, 'dl div')
            for spec in specs:
                t = spec.text.lower()
                if 'poko' in t: pokoje = extract_numbers(t)
                elif 'm²' in t: metraz = extract_numbers(t)
                elif 'piętro' in t: pietro = extract_numbers(t)

            # 4. Typ Oferenta i Wystawca
            typ_oferenta = "Oferta prywatna"
            wystawca = "Osoba prywatna"
            
            # Jeśli w karcie jest wzmianka o biurze/agencji
            if "biuro" in full_card_text or "nieruchomości" in full_card_text:
                typ_oferenta = "Biuro nieruchomości"
                # Próba wyciągnięcia nazwy biura (zazwyczaj ostatnia linia tekstu)
                lines = card.text.split('\n')
                wystawca = lines[-1] if len(lines) > 0 else "Biuro"

            row = [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                link,
                tytul,
                adres,
                cena,
                czynsz,
                pokoje,
                metraz,
                pietro,
                typ_oferenta,
                wystawca,
                "Brak Danych (Poza Kartą)",
                "Brak opisu (Poza Kartą)"
            ]
            rows_to_append.append(row)
            
        except Exception as e:
            continue
            
    return rows_to_append
def main():
    zakladka = authorize_google_sheets()
    driver = setup_selenium_driver()
    if not zakladka or not driver: return

    try:
        print(f"Otwieram: {URL_OTODOM}")
        driver.get(URL_OTODOM)
        time.sleep(10) # Czekamy aż wszystko (reklamy, skrypty) się załaduje
        
        # Akceptacja cookies
        try:
            WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))).click()
            print("Cookies OK.")
        except: pass
        
        data = process_page(driver)
        
        if data:
            print(f"Zapisuję {len(data)} ofert...")
            zakladka.append_rows(data)
            print("SUKCES!")
        else:
            print("Nadal nie widzę ofert. Prawdopodobna blokada bota (Captcha).")
            # Log dla Ciebie: co widzi przeglądarka?
            print(f"Tytuł strony: {driver.title}")
            
    finally:
        driver.quit()

if __name__ == "__main__":
    main()


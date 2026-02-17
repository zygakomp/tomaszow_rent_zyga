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
    # Przewijamy stronę w dół, żeby wymusić załadowanie ogłoszeń (Lazy Load)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(2)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)

    # Szukamy ogłoszeń po dowolnym tagu 'article' (najbardziej odporne na zmiany nazw)
    listing_cards = driver.find_elements(By.TAG_NAME, 'article')
    print(f"Wykryto obiektów typu article: {len(listing_cards)}")

    for card in listing_cards:
        # Sprawdzamy czy to faktycznie ogłoszenie (czy ma link)
        try:
            link_el = card.find_element(By.TAG_NAME, 'a')
            link = link_el.get_attribute('href')
            if 'oferta/' not in link: continue
            
            data_now = time.strftime("%Y-%m-%d %H:%M:%S")
            full_text = card.text # Pobieramy cały tekst karty naraz - szybciej i pewniej
            
            # Analiza tekstu karty
            lines = full_text.split('\n')
            tytul = lines[0] if len(lines) > 0 else "Brak"
            
            # Cena - zazwyczaj pierwsza linia z "zł"
            cena = 0
            for line in lines:
                if 'zł' in line and 'czynsz' not in line.lower():
                    cena = clean_and_convert_to_number(extract_numbers(line))
                    break
            
            # Czynsz
            czynsz = 0
            match_czynsz = re.search(r'\+\s*czynsz[:\s]*([\d\s]+)', full_text.lower())
            if match_czynsz:
                czynsz = clean_and_convert_to_number(extract_numbers(match_czynsz.group(1)))

            # Parametry (Pokoje, m2)
            pokoje, metraz, pietro = 0, 0, 0
            for line in lines:
                if 'poko' in line.lower(): pokoje = extract_numbers(line)
                if 'm²' in line: metraz = extract_numbers(line)
                if 'piętro' in line.lower(): pietro = extract_numbers(line)

            # Adres - zazwyczaj linia po tytule
            adres = lines[1] if len(lines) > 1 else "Brak"

            row = [data_now, link, tytul, adres, cena, czynsz, pokoje, metraz, pietro, "Biuro/Prywatne", "Wystawca", "Brak", "Brak"]
            rows_to_append.append(row)
        except:
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

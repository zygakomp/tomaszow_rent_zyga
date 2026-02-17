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
    text = text.replace('\xa0', '').replace(' ', '').replace(',', '.')
    match = re.search(r'(\d+\.?\d*)', text)
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
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
    
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def process_page(driver):
    rows_to_append = []
    print("Przeszukuję stronę w poszukiwaniu ofert...")
    
    # Przewijanie - bardzo ważne dla doładowania ofert
    for i in range(3):
        driver.execute_script(f"window.scrollTo(0, { (i+1)*800 });")
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
            if not link or 'pl/oferta/' not in link: continue

            data_now = time.strftime("%Y-%m-%d %H:%M:%S")
            card_text = card.text
            lines = [line.strip() for line in card_text.split('\n') if line.strip()]

            # --- INTELIGENTNY TYTUŁ ---
            tytul = "Brak tytułu"
            for line in lines:
                # Pomijamy licznik zdjęć (np. 1 / 10)
                if '/' in line and len(line) < 10: continue
                # Pomijamy ceny (wszystko co ma zł)
                if 'zł' in line.lower(): continue
                # Pomijamy adresy (miejscowości) w pierwszej linii
                if 'tomaszów' in line.lower() and len(line) < 25: continue
                tytul = line
                break

            # --- ADRES ---
            adres = "Tomaszów Mazowiecki"
            for line in lines:
                if 'tomaszów' in line.lower() or 'mazowiecki' in line.lower():
                    if 'zł' not in line.lower():
                        adres = line
                        break

            # --- CENY (Największa to koszt najmu, mniejsza to czynsz) ---
            # Wyciągamy wszystkie kwoty z symbolem zł
            found_prices = re.findall(r'([\d\s,]+)\s*zł', card_text.replace('\xa0', ' '))
            clean_prices = sorted([float(extract_numbers(p)) for p in found_prices], reverse=True)
            
            cena_najmu = clean_prices[0] if len(clean_prices) > 0 else 0
            # Czynsz - bierzemy drugą co do wielkości kwotę, jeśli słowo 'czynsz' jest w tekście
            czynsz = clean_prices[1] if len(clean_prices) > 1 and 'czynsz' in card_text.lower() else 0

            # --- PARAMETRY (Pokoje, m2, Piętro) ---
            pokoje, metraz, pietro = "0", "0", "0"
            for line in lines:
                l_low = line.lower()
                if 'poko' in l_low: pokoje = extract_numbers(line)
                if 'm²' in l_low: metraz = extract_numbers(line)
                if 'piętr' in l_low: pietro = extract_numbers(line)

            # --- OFERENT ---
            typ = "Oferta prywatna"
            wystawca = "Osoba prywatna"
            if "biuro" in card_text.lower() or "agency" in card_text.lower():
                typ = "Biuro nieruchomości"
                wystawca = lines[-1] if len(lines) > 0 else "Biuro"

            rows_to_append.append([
                data_now, link, tytul, adres, cena_najmu, czynsz, 
                pokoje, metraz, pietro, typ, wystawca, 
                "Brak Danych (Poza Kartą)", "Brak opisu (Poza Kartą)"
            ])
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
        time.sleep(12) 
        
        try:
            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))).click()
            print("Cookies OK.")
        except: pass
        
        data = process_page(driver)
        if data:
            print(f"Zapisuję {len(data)} ofert do Google Sheets...")
            zakladka.append_rows(data)
            print("ZAPIS ZAKOŃCZONY SUKCESEM!")
        else:
            print(f"Nie znaleziono ofert. Tytuł strony: {driver.title}")
            
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    main()

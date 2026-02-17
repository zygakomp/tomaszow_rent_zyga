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
    print("Przewijanie strony dla załadowania ofert...")
    for _ in range(4): # Przewijamy 4 razy
        driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(1.5)

    # Szukamy kart ogłoszeń wieloma metodami dla pewności
    listing_cards = driver.find_elements(By.CSS_SELECTOR, 'article[data-cy="listing-item"]')
    if not listing_cards:
        listing_cards = driver.find_elements(By.XPATH, "//article")

    print(f"Wykryto {len(listing_cards)} potencjalnych ogłoszeń.")

    for card in listing_cards:
        try:
            # Sprawdzenie czy to ogłoszenie (musi mieć link)
            link_el = card.find_element(By.TAG_NAME, 'a')
            link = link_el.get_attribute('href')
            if not link or 'oferta/' not in link: continue

            data_now = time.strftime("%Y-%m-%d %H:%M:%S")
            
            # --- TYTUŁ (Precyzyjne celowanie, by uniknąć "1 / 8") ---
            try:
                tytul = card.find_element(By.CSS_SELECTOR, '[data-cy="listing-item-title"]').text.strip()
            except:
                # Fallback: bierzemy tekst i czyścimy z liczników zdjęć
                lines = card.text.split('\n')
                tytul = lines[1] if len(lines) > 1 and '/' in lines[0] else lines[0]

            # --- ADRES ---
            try:
                adres = card.find_element(By.CSS_SELECTOR, '[data-cy="listing-item-address"]').text.strip()
            except:
                adres = "Tomaszów Mazowiecki"

            # --- CENA I CZYNSZ ---
            try:
                cena_raw = card.find_element(By.CSS_SELECTOR, '[data-cy="listing-item-price"]').text
                cena = clean_and_convert_to_number(extract_numbers(cena_raw))
            except: cena = 0

            full_text = card.text.lower()
            czynsz = 0
            match_cz = re.search(r'czynsz[:\s]*([\d\s,]+)', full_text)
            if match_cz:
                czynsz = clean_and_convert_to_number(extract_numbers(match_cz.group(1)))

            # --- PARAMETRY (Pokoje, m2, Piętro) ---
            pokoje, metraz, pietro = "0", "0", "0"
            # Szukamy w spanach, które zawierają jednostki lub słowa kluczowe
            for s in card.find_elements(By.TAG_NAME, 'span'):
                t = s.text.lower()
                if 'poko' in t: pokoje = extract_numbers(t)
                elif 'm²' in t: metraz = extract_numbers(t)
                elif 'piętro' in t: pietro = extract_numbers(t)

            # --- OFERENT ---
            typ = "Oferta prywatna"
            wystawca = "Osoba prywatna"
            if "biuro" in full_text or "nieruchomości" in full_text:
                typ = "Biuro nieruchomości"
                wystawca = card.text.split('\n')[-1] # Zazwyczaj nazwa biura jest na dole

            rows_to_append.append([
                data_now, link, tytul, adres, cena, czynsz, 
                pokoje, metraz, pietro, typ, wystawca, 
                "Brak Danych (Poza Kartą)", "Brak opisu (Poza Kartą)"
            ])
        except: continue
            
    return rows_to_append

def main():
    zakladka = authorize_google_sheets()
    driver = setup_selenium_driver()
    if not zakladka or not driver: return

    try:
        print(f"Otwieram: {URL_OTODOM}")
        driver.get(URL_OTODOM)
        time.sleep(15) # Więcej czasu na załadowanie (ważne w GitHub Actions)
        
        try:
            btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
            btn.click()
            print("Cookies OK.")
        except: pass
        
        data = process_page(driver)
        if data:
            print(f"Zapisuję {len(data)} ofert...")
            zakladka.append_rows(data)
            print("ZAPIS ZAKOŃCZONY!")
        else:
            print(f"Nie znaleziono ofert. Tytuł strony: {driver.title}")
            
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    main()

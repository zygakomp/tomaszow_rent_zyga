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
    
    # Przewijanie, aby dociągnąć leniwe ładowanie (lazy loading)
    driver.execute_script("window.scrollBy(0, 1000);")
    time.sleep(2)

    # Szukamy wszystkich artykułów (ogłoszeń)
    listing_cards = driver.find_elements(By.CSS_SELECTOR, 'article[data-cy="listing-item"]')
    if not listing_cards:
        listing_cards = driver.find_elements(By.TAG_NAME, 'article')

    print(f"Wykryto {len(listing_cards)} ogłoszeń.")

    for card in listing_cards:
        try:
            full_text = card.text
            lines = full_text.split('\n')
            
            # 1. Link i Data
            link = card.find_element(By.TAG_NAME, 'a').get_attribute('href')
            if 'oferta/' not in link: continue
            data_now = time.strftime("%Y-%m-%d %H:%M:%S")

            # 2. Tytuł - inteligentne omijanie licznika zdjęć (np. "1 / 12")
            tytul = lines[0]
            if "/" in tytul and len(tytul) < 7: # Jeśli to np. "1 / 10", bierzemy następną linię
                tytul = lines[1] if len(lines) > 1 else "Brak tytułu"

            # 3. Adres - szukamy linii, która nie jest ceną ani tytułem
            adres = "Tomaszów Mazowiecki"
            for line in lines[1:4]:
                if "zł" not in line and "/" not in line and len(line) > 5:
                    adres = line
                    break

            # 4. CENA (Koszt najmu) - szukamy największej kwoty z "zł"
            ceny_w_tekscie = re.findall(r'(\d[\d\s]*)\s*zł', full_text.replace('\xa0', ' '))
            liczby_cen = sorted([int(extract_numbers(c)) for c in ceny_w_tekscie], reverse=True)
            
            # Najwyższa kwota to zazwyczaj cena najmu, mniejsza to czynsz
            cena = liczby_cen[0] if len(liczby_cen) > 0 else 0
            czynsz = liczby_cen[1] if len(liczby_cen) > 1 else 0
            
            # Jeśli czynsz w tekście jest wyraźnie opisany "+ czynsz"
            match_czynsz = re.search(r'czynsz[:\s]*([\d\s]+)', full_text.lower())
            if match_czynsz:
                czynsz = clean_and_convert_to_number(extract_numbers(match_czynsz.group(1)))

            # 5. Parametry (Pokoje, Powierzchnia, Piętro)
            pokoje, metraz, pietro = 0, 0, 0
            for line in lines:
                l_low = line.lower()
                if 'poko' in l_low: pokoje = extract_numbers(line)
                if 'm²' in l_low: metraz = extract_numbers(line)
                if 'piętro' in l_low: pietro = extract_numbers(line)

            # 6. Oferent
            typ = "Oferta prywatna"
            wystawca = "Osoba prywatna"
            if "biuro" in full_text.lower() or "nieruchomości" in full_text.lower():
                typ = "Biuro nieruchomości"
                wystawca = lines[-1] # Nazwa biura jest zazwyczaj na samym dole karty

            row = [
                data_now, link, tytul, adres, cena, czynsz, 
                pokoje, metraz, pietro, typ, wystawca, 
                "Brak Danych (Poza Kartą)", "Brak opisu (Poza Kartą)"
            ]
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


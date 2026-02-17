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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.chrome.options import Options

# --- KONFIGURACJA ---
ARKUSZ_ID = '1JdrNZr4eeX8Vc1w-V7XgB2ysdnxAQg_KqCE3b6RHCtc'
NAZWA_ZAKLADKI = 'TOM_OTO'
URL_OTODOM = 'https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/lodzkie/tomaszowski/gmina-miejska--tomaszow-mazowiecki/tomaszow-mazowiecki?ownerTypeSingleSelect=ALL'

def extract_numbers(text):
    if not text: return 'Brak Danych'
    processed_text = text.replace(' ', '').replace(',', '.')
    match = re.search(r'(\d+\.?\d*)', processed_text)
    return match.group(1) if match else 'Brak Danych'

def clean_and_convert_to_number(value):
    if value == 'Brak Danych': return value
    try: return float(value)
    except ValueError: return value

def authorize_google_sheets():
    print("Autoryzacja do Google Sheets...")
    try:
        creds_json = os.environ.get('G_SHEETS_JSON')
        if not creds_json:
            raise Exception("Brak sekretu G_SHEETS_JSON!")
            
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        arkusz = client.open_by_key(ARKUSZ_ID)
        
        try:
            zakladka = arkusz.worksheet(NAZWA_ZAKLADKI)
        except gspread.WorksheetNotFound:
            zakladka = arkusz.add_worksheet(title=NAZWA_ZAKLADKI, rows="100", cols="20")
        return zakladka
    except Exception as e:
        print(f"BŁĄD autoryzacji: {e}")
        return None

def setup_selenium_driver():
    print("Inicjalizacja ustawień Chrome...")
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        print("Przeglądarka uruchomiona pomyślnie.")
        return driver
    except Exception as e:
        print(f"BŁĄD startu Chrome: {e}")
        return None

def handle_cookies(driver):
    """Funkcja, której brakowało poprzednio."""
    try:
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
        print("Ciasteczka zaakceptowane.")
    except:
        print("Komunikat o ciasteczkach nie pojawił się.")

def process_page(driver):
    rows_to_append = []
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-sentry-component='AdvertCard']"))
        )
    except:
        return rows_to_append

    listing_cards = driver.find_elements(By.XPATH, "//article[@data-sentry-component='AdvertCard'] | //article[@data-sentry-component='VipAdvertCard']")

    for card in listing_cards:
        data_scrapingu = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            link = card.find_element(By.XPATH, ".//a[@data-cy='listing-item-link']").get_attribute('href')
            tytul = card.find_element(By.XPATH, ".//p[contains(@class, 'title')]").text.strip()
            
            # Pobieranie ceny głównej
            try:
                cena_raw = card.find_element(By.XPATH, ".//span[@data-sentry-element='MainPrice']").text
                cena = clean_and_convert_to_number(extract_numbers(cena_raw))
            except:
                cena = "Brak Danych"
                
            rows_to_append.append([data_scrapingu, link, tytul, "", cena, "", "", "", "", "", "", "", ""])
        except:
            continue
    return rows_to_append

def main():
    zakladka = authorize_google_sheets()
    if not zakladka: return
        
    driver = setup_selenium_driver()
    if not driver: return

    try:
        print(f"Wchodzę na stronę: {URL_OTODOM}")
        driver.get(URL_OTODOM)
        time.sleep(8) 
        
        handle_cookies(driver)
        
        print("Rozpoczynam zbieranie danych...")
        all_data = process_page(driver)
        
        if all_data:
            print(f"Znaleziono {len(all_data)} ofert. Zapisuję...")
            zakladka.append_rows(all_data)
            print("ZAPIS ZAKOŃCZONY SUKCESEM!")
        else:
            print("Nie znaleziono ofert. Sprawdź selektory.")
            
    except Exception as e:
        print(f"Wystąpił błąd: {e}")
    finally:
        if 'driver' in locals():
            driver.quit()
            print("Przeglądarka zamknięta.")

if __name__ == "__main__":
    main()

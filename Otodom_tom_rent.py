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
        # POBIERANIE KLUCZA Z SEKRETÓW GITHUB
        creds_json = os.environ.get('G_SHEETS_JSON')
        if not creds_json:
            raise Exception("Brak sekretu G_SHEETS_JSON w ustawieniach GitHub!")
            
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
    options.add_argument('--headless=new') # Nowszy tryb headless
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    try:
        print("Instalacja/sprawdzanie sterownika Chrome...")
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        print("Przeglądarka uruchomiona pomyślnie.")
        return driver
    except Exception as e:
        print(f"BŁĄD krytyczny przy starcie Chrome: {e}")
        return None

def main():
    zakladka = authorize_google_sheets()
    if not zakladka:
        print("Zamykam skrypt: Błąd autoryzacji arkusza.")
        return
        
    driver = setup_selenium_driver()
    if not driver:
        print("Zamykam skrypt: Nie udało się uruchomić przeglądarki.")
        return

    try:
        print(f"Wchodzę na stronę: {URL_OTODOM}")
        driver.get(URL_OTODOM)
        
        # Zwiększamy czas czekania na załadowanie (Otodom bywa wolny)
        time.sleep(10) 
        
        print("Próba akceptacji ciasteczek...")
        handle_cookies(driver)
        
        print("Rozpoczynam zbieranie danych...")
        all_data = process_page(driver)
        
        if all_data:
            print(f"Znaleziono {len(all_data)} ofert. Zapisuję do Google Sheets...")
            zakladka.append_rows(all_data)
            print("ZAPIS ZAKOŃCZONY SUKCESEM!")
        else:
            print("UWAGA: Nie znaleziono żadnych ofert na stronie. Sprawdź czy selektory XPATH są aktualne.")
            
    except Exception as e:
        print(f"Wystąpił błąd podczas pracy: {e}")
    finally:
        if driver:
            driver.quit()
            print("Przeglądarka zamknięta.")
if __name__ == "__main__":

    main()

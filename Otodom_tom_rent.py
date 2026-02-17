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
    print("Uruchamianie Chrome w trybie headless...")
    options = Options()
    options.add_argument('--headless') # Tryb bez okna - wymagany na serwerze
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    
    service = ChromeService(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def handle_cookies(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
    except:
        pass

def process_page(driver):
    rows_to_append = []
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-sentry-component='AdvertCard']"))
        )
    except:
        return rows_to_append

    listing_cards = driver.find_elements(By.XPATH, "//article[@data-sentry-component='AdvertCard'] | //article[@data-sentry-component='VipAdvertCard']")

    for card in listing_cards:
        data_scrapingu = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            link = card.find_element(By.XPATH, ".//a[@data-cy='listing-item-link']").get_attribute('href')
            tytul = card.find_element(By.XPATH, ".//p[contains(@data-cy, 'title')]").text.strip()
            
            # Pobieranie ceny i reszty parametrów (uproszczone dla stabilności)
            cena_raw = card.find_element(By.XPATH, ".//span[@data-sentry-element='MainPrice']").text
            cena = clean_and_convert_to_number(extract_numbers(cena_raw))
            
            rows_to_append.append([data_scrapingu, link, tytul, "", cena, "", "", "", "", "", "", "", ""])
        except:
            continue
    return rows_to_append

def main():
    zakladka = authorize_google_sheets()
    driver = setup_selenium_driver()
    if not zakladka or not driver: return

    try:
        driver.get(URL_OTODOM)
        time.sleep(5)
        handle_cookies(driver)
        
        all_data = process_page(driver)
        
        if all_data:
            zakladka.append_rows(all_data)
            print(f"Zapisano {len(all_data)} wierszy.")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
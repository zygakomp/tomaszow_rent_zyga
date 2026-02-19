import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# --- KONFIGURACJA ---
ARKUSZ_ID = '1JdrNZr4eeX8Vc1w-V7XgB2ysdnxAQg_KqCE3b6RHCtc'
NAZWA_ZAKLADKI = 'olx_tom_sell'
URL_OLX = 'https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/tomaszow-mazowiecki/'

BASE_URL = "https://www.olx.pl"
WARSAW_TZ = ZoneInfo("Europe/Warsaw")

MAX_PAGES = int(os.environ.get("MAX_PAGES", "5"))

def now_pl_str():
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")

def clean_and_convert_to_number(text_value, is_float=False):
    if not isinstance(text_value, str) or text_value.strip() == "Brak Danych":
        return text_value
    cleaned_value = text_value.strip().replace('\xa0', ' ')
    cleaned_value = cleaned_value.replace('zł', '').replace('.', '').replace(',', '.').replace(' ', '').strip()
    cleaned_value = (
        cleaned_value
        .replace('m²', '').replace('m2', '')
        .replace('pokoje', '').replace('pokój', '')
        .replace('piętro', '')
        .strip()
    )
    if cleaned_value.lower() == 'parter': return 0
    if cleaned_value.lower() in ['powyżej10', 'powyzej10']: return 11
    try:
        if is_float: return float(cleaned_value)
        else: return int(float(cleaned_value))
    except ValueError: return text_value

def authorize_google_sheets():
    try:
        creds_json = os.environ.get("G_SHEETS_JSON")
        if not creds_json: raise Exception("Brak zmiennej G_SHEETS_JSON")
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        arkusz = client.open_by_key(ARKUSZ_ID)
        try:
            zakladka = arkusz.worksheet(NAZWA_ZAKLADKI)
        except gspread.WorksheetNotFound:
            zakladka = arkusz.add_worksheet(title=NAZWA_ZAKLADKI, rows="300", cols="25")

        # --- POPRAWIONE NAGŁÓWKI (I: Pokoje, J: Poziom) ---
        naglowki = [
            'Data Scrapingu',    # A
            'URL Ogłoszenia',    # B
            'Typ Oferenta',      # C
            'Wystawca (Nazwa)',  # D
            'Telefon Kontaktowy',# E
            'Koszt (F)',         # F
            'Cena za m2 (G)',    # G
            'Powierzchnia (H)',  # H
            'Liczba pokoi (I)',  # I - TUTAJ POPRAWKA
            'Poziom (J)',        # J - TUTAJ POPRAWKA
            'Czynsz (dodatkowo)',# K
            'Parking',
            'Zwierzęta',
            'Winda',
            'Umeblowane',
            'Rodzaj zabudowy',
            'Opis'
        ]
        if not zakladka.row_values(1):
            zakladka.append_row(naglowki)
        return zakladka
    except Exception as e:
        print(f"Błąd arkusza: {e}")
        return None

def setup_selenium_driver():
    try:
        service = ChromeService(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        print(f"Błąd Chrome: {e}"); return None

def get_listing_details(driver, url):
    driver.get(url)
    time.sleep(3)
    data_scrapingu = now_pl_str()
    cena_najmu = "Brak Danych"
    opis_ogloszenia = "Brak opisu"
    typ_oferenta = "Brak Danych"
    wystawca_nazwa = "Brak Danych"
    telefon_kontaktowy = "Brak Danych"
    czynsz_oplaty = "Brak Danych"
    powierzchnia = "Brak Danych"
    liczba_pokoi = "Brak Danych"
    parking = "Brak Danych"
    zwierzeta = "Brak Danych"
    winda = "Brak Danych"
    poziom = "Brak Danych"
    umeblowane = "Brak Danych"
    rodzaj_zabudowy = "Brak Danych"

    wait = WebDriverWait(driver, 10)
    try:
        cena_najmu = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='ad-price-container']/h3"))).text.strip()
    except: pass
    try:
        wystawca_nazwa = driver.find_element(By.XPATH, "//h4[@data-testid='user-profile-user-name']").text.strip()
    except: pass
    try:
        btn = wait.until(EC.presence_of_element_located((By.XPATH, "//button[@data-testid='show-phone']")))
        btn.click(); time.sleep(1.5)
        telefon_kontaktowy = driver.find_element(By.XPATH, "//div[@data-testid='ad-contact-bar']//p[contains(@class,'css-')]").text.strip()
    except: pass

    try:
        param_container = driver.find_element(By.XPATH, "//div[@data-testid='ad-parameters-container']")
        try: typ_oferenta = param_container.find_element(By.TAG_NAME, "span").text.strip()
        except: pass
        params = param_container.find_elements(By.TAG_NAME, "p")
        for p in params:
            t = p.text.strip()
            if t.startswith("Czynsz (dodatkowo):"): czynsz_oplaty = t.replace("Czynsz (dodatkowo):", "").strip()
            elif t.startswith("Powierzchnia:"): powierzchnia = t.replace("Powierzchnia:", "").strip()
            elif t.startswith("Liczba pokoi:"): liczba_pokoi = t.replace("Liczba pokoi:", "").strip()
            elif t.startswith("Parking:"): parking = t.replace("Parking:", "").strip()
            elif t.startswith("Zwierzęta:"): zwierzeta = t.replace("Zwierzęta:", "").strip()
            elif t.startswith("Winda:"): winda = t.replace("Winda:", "").strip()
            elif t.startswith("Poziom:"): poziom = t.replace("Poziom:", "").strip()
            elif t.startswith("Umeblowane:"): umeblowane = t.replace("Umeblowane:", "").strip()
            elif t.startswith("Rodzaj zabudowy:"): rodzaj_zabudowy = t.replace("Rodzaj zabudowy:", "").strip()
    except: pass

    try:
        opis_ogloszenia = driver.find_element(By.XPATH, "//div[@data-cy='ad_description']//div[contains(@class, 'css-')]").text.strip().replace('\n', ' ')
    except: pass

    koszt_kwota = clean_and_convert_to_number(cena_najmu, is_float=False)
    powierzchnia_liczba = clean_and_convert_to_number(powierzchnia, is_float=True)
    
    # Cena za m2 (G)
    if isinstance(koszt_kwota, (int, float)) and isinstance(powierzchnia_liczba, (int, float)) and powierzchnia_liczba > 0:
        cena_za_m2 = round(koszt_kwota / powierzchnia_liczba, 2)
    else: cena_za_m2 = "Brak Danych"

    return [
        data_scrapingu,      # A
        url,                 # B
        typ_oferenta,        # C
        wystawca_nazwa,      # D
        telefon_kontaktowy,  # E
        koszt_kwota,         # F
        cena_za_m2,          # G
        powierzchnia_liczba, # H
        clean_and_convert_to_number(liczba_pokoi), # I - POPRAWIONE
        clean_and_convert_to_number(poziom),       # J - POPRAWIONE
        clean_and_convert_to_number(czynsz_oplaty),# K
        parking,
        zwierzeta,
        winda,
        umeblowane,
        rodzaj_zabudowy,
        opis_ogloszenia
    ]

def main_scraper():
    zakladka = authorize_google_sheets()
    driver = setup_selenium_driver()
    if not zakladka or not driver: return
    try:
        driver.get(URL_OLX); time.sleep(5)
        all_links = set()
        # Skanowanie stron (uproszczone dla czytelności)
        listing_links = driver.find_elements(By.XPATH, "//div[@data-cy='l-card']//a[contains(@href, '/d/oferta/')]")
        for elem in listing_links:
            all_links.add(elem.get_attribute('href'))
        
        rows = []
        for i, link in enumerate(list(all_links)[:20]): # Limit dla testu
            print(f"Przetwarzam {i+1}: {link}")
            rows.append(get_listing_details(driver, link))
        
        if rows: zakladka.append_rows(rows)
    finally: driver.quit()

if __name__ == "__main__":
    main_scraper()

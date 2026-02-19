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

# limit stron (domyślnie 5) — ustaw w workflow env MAX_PAGES
MAX_PAGES = int(os.environ.get("MAX_PAGES", "5"))


def now_pl_str():
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")


# --- UNIWERSALNA FUNKCJA DO CZYSZCZENIA I KONWERSJI NA LICZBY ---
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

    if cleaned_value.lower() == 'parter':
        return 0
    if cleaned_value.lower() in ['powyżej10', 'powyzej10']:
        return 11

    try:
        if is_float:
            return float(cleaned_value)
        else:
            return int(float(cleaned_value))
    except ValueError:
        return text_value


# --- GOOGLE SHEETS ---
def authorize_google_sheets():
    print("Autoryzacja do Google Sheets (ENV: G_SHEETS_JSON)...")
    try:
        creds_json = os.environ.get("G_SHEETS_JSON")
        if not creds_json:
            raise Exception("Brak zmiennej środowiskowej G_SHEETS_JSON.")

        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        arkusz = client.open_by_key(ARKUSZ_ID)

        try:
            zakladka = arkusz.worksheet(NAZWA_ZAKLADKI)
        except gspread.WorksheetNotFound:
            print(f"Tworzę nową zakładkę: {NAZWA_ZAKLADKI}")
            zakladka = arkusz.add_worksheet(title=NAZWA_ZAKLADKI, rows="300", cols="25")

        # ZMIENIONO: Nagłówki (G to teraz Cena za m2)
        naglowki = [
            'Data Scrapingu',    # A
            'URL Ogłoszenia',    # B
            'Typ Oferenta',      # C
            'Wystawca (Nazwa)',  # D
            'Telefon Kontaktowy',# E
            'Koszt (F)',         # F
            'Cena za m2 (G)',    # G (Wyliczenie F/H)
            'Powierzchnia (H)',  # H
            'Czynsz (dodatkowo)',# I
            'Liczba pokoi',      # J
            'Parking',
            'Zwierzęta',
            'Winda',
            'Poziom',
            'Umeblowane',
            'Rodzaj zabudowy',
            'Opis'
        ]

        if zakladka.row_values(1) != naglowki:
            if not zakladka.row_values(1):
                zakladka.append_row(naglowki)
        
        return zakladka
    except Exception as e:
        print(f"BŁĄD autoryzacji: {e}")
        return None


def setup_selenium_driver():
    print("Uruchamianie Chrome...")
    try:
        service = ChromeService(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--incognito')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')

        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except Exception as e:
        print(f"BŁĄD Chrome: {e}")
        return None


def handle_cookies(driver):
    try:
        btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
        btn.click()
    except:
        pass


def get_max_page_number(driver):
    try:
        pagination_links = driver.find_elements(By.XPATH, "//li[@data-testid='pagination-list-item']/a")
        max_page = 1
        for link in pagination_links:
            try:
                page_num = int(link.text.strip())
                if page_num > max_page: max_page = page_num
            except: continue
        return max_page
    except:
        return 1


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
        cena_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='ad-price-container']/h3")))
        cena_najmu = cena_element.text.strip()
    except: pass

    try:
        wystawca_element = driver.find_element(By.XPATH, "//h4[@data-testid='user-profile-user-name']")
        wystawca_nazwa = wystawca_element.text.strip()
    except: pass

    try:
        show_phone_button = wait.until(EC.presence_of_element_located((By.XPATH, "//button[@data-testid='show-phone']")))
        show_phone_button.click()
        time.sleep(1.5)
        numer_element = driver.find_element(By.XPATH, "//div[@data-testid='ad-contact-bar']//p[contains(@class,'css-')]")
        telefon_kontaktowy = numer_element.text.strip()
    except: pass

    try:
        param_container = driver.find_element(By.XPATH, "//div[@data-testid='ad-parameters-container']")
        try:
            typ_oferenta = param_container.find_element(By.TAG_NAME, "span").text.strip()
        except: pass

        param_elements = param_container.find_elements(By.TAG_NAME, "p")
        for p in param_elements:
            text = p.text.strip()
            if text.startswith("Czynsz (dodatkowo):"): czynsz_oplaty = text.replace("Czynsz (dodatkowo):", "").strip()
            elif text.startswith("Powierzchnia:"): powierzchnia = text.replace("Powierzchnia:", "").strip()
            elif text.startswith("Liczba pokoi:"): liczba_pokoi = text.replace("Liczba pokoi:", "").strip()
            elif text.startswith("Parking:"): parking = text.replace("Parking:", "").strip()
            elif text.startswith("Zwierzęta:"): zwierzeta = text.replace("Zwierzęta:", "").strip()
            elif text.startswith("Winda:"): winda = text.replace("Winda:", "").strip()
            elif text.startswith("Poziom:"): poziom = text.replace("Poziom:", "").strip()
            elif text.startswith("Umeblowane:"): umeblowane = text.replace("Umeblowane:", "").strip()
            elif text.startswith("Rodzaj zabudowy:"): rodzaj_zabudowy = text.replace("Rodzaj zabudowy:", "").strip()
    except: pass

    try:
        opis_element = driver.find_element(By.XPATH, "//div[@data-cy='ad_description']//div[contains(@class, 'css-')]")
        opis_ogloszenia = opis_element.text.strip().replace('\n', ' ')
    except: pass

    # Konwersje i obliczenia
    koszt_kwota = clean_and_convert_to_number(cena_najmu, is_float=False)
    powierzchnia_liczba = clean_and_convert_to_number(powierzchnia, is_float=True)
    
    # --- OBLICZANIE CENY ZA M2 (KOLUMNA G: F/H) ---
    if isinstance(koszt_kwota, (int, float)) and isinstance(powierzchnia_liczba, (int, float)) and powierzchnia_liczba > 0:
        cena_za_m2 = round(koszt_kwota / powierzchnia_liczba, 2)
    else:
        cena_za_m2 = "Brak Danych"

    print(f"  -> Koszt: {koszt_kwota}, Pow: {powierzchnia_liczba}, Cena/m2: {cena_za_m2}")

    return [
        data_scrapingu,      # A
        url,                 # B
        typ_oferenta,        # C
        wystawca_nazwa,      # D
        telefon_kontaktowy,  # E
        koszt_kwota,         # F
        cena_za_m2,          # G (WYLIczone F/H)
        powierzchnia_liczba, # H
        clean_and_convert_to_number(czynsz_oplaty), # I
        clean_and_convert_to_number(liczba_pokoi),  # J
        parking,
        zwierzeta,
        winda,
        clean_and_convert_to_number(poziom),
        umeblowane,
        rodzaj_zabudowy,
        opis_ogloszenia
    ]


def main_scraper():
    zakladka = authorize_google_sheets()
    if not zakladka: return
    driver = setup_selenium_driver()
    if not driver: return

    try:
        driver.get(URL_OLX)
        time.sleep(6)
        handle_cookies(driver)

        detected_max = get_max_page_number(driver)
        max_page = min(detected_max, MAX_PAGES)
        
        all_links = set()
        for page_num in range(1, max_page + 1):
            page_url = URL_OLX if page_num == 1 else f"{URL_OLX}?page={page_num}"
            driver.get(page_url)
            time.sleep(4)
            listing_links = driver.find_elements(By.XPATH, "//div[@data-cy='l-card']//a[contains(@href, '/d/oferta/')]")
            for elem in listing_links:
                href = elem.get_attribute('href')
                if href: all_links.add(href)

        rows_to_append = []
        links_list = list(all_links)
        for i, link in enumerate(links_list):
            print(f"[{i + 1}/{len(links_list)}] Przetwarzam: {link}")
            details = get_listing_details(driver, link)
            rows_to_append.append(details)
            time.sleep(1)

        if rows_to_append:
            zakladka.append_rows(rows_to_append)
            print("Zapisano dane.")

    except Exception as e:
        print(f"Błąd: {e}")
    finally:
        if driver: driver.quit()


if __name__ == "__main__":
    main_scraper()

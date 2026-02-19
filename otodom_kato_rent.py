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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo

# 🏠 --- KONFIGURACJA SKRYPTU ---
ARKUSZ_ID = '1JdrNZr4eeX8Vc1w-V7XgB2ysdnxAQg_KqCE3b6RHCtc'
NAZWA_ZAKLADKI = 'otodom_kato_rent'
URL_OTODOM = 'https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/slaskie/katowice/katowice/katowice?ownerTypeSingleSelect=ALL'
BASE_URL = 'https://www.otodom.pl'

# --- LIMIT STRON (DOMYŚLNIE 5) ---
MAX_STRON = 5  # <- ustaw ile stron chcesz max

# --- STREFA CZASOWA (PL) ---
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


# --- FUNKCJE ---

def now_pl_str():
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def clean_and_convert_to_number(text_value):
    """
    Usuwa z tekstu jednostki i formatowanie, zwracając czystą liczbę (float) lub None.
    Obsługuje formatowanie typu "3 500 zł" -> 3500.0, "55 m2" -> 55.0.
    """
    if not text_value or text_value in ["Brak Danych", "Brak info o czynszu"]:
        return None

    cleaned_text = re.sub(r'[^\d,\.]', '', text_value).replace(',', '.')
    cleaned_text = cleaned_text.replace(' ', '')

    try:
        return float(cleaned_text)
    except ValueError:
        return None


def authorize_google_sheets():
    """Autoryzacja i inicjalizacja połączenia z Arkuszem Google (GitHub: ENV G_SHEETS_JSON)."""
    print("Autoryzacja do Google Sheets (ENV: G_SHEETS_JSON)...")
    try:
        creds_json = os.environ.get("G_SHEETS_JSON")
        if not creds_json:
            raise Exception("Brak zmiennej środowiskowej G_SHEETS_JSON. Dodaj secret w GitHub.")

        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        arkusz = client.open_by_key(ARKUSZ_ID)

        try:
            zakladka = arkusz.worksheet(NAZWA_ZAKLADKI)
        except gspread.WorksheetNotFound:
            print(f"Tworzę nową zakładkę: {NAZWA_ZAKLADKI}")
            zakladka = arkusz.add_worksheet(title=NAZWA_ZAKLADKI, rows="100", cols="20")

        print(f"Pomyślnie połączono z arkuszem: {arkusz.title}, zakładka: {zakladka.title}")

        naglowki = [
            'Data Scrapingu',
            'URL Ogłoszenia',
            'Tytuł Ogłoszenia',
            'Adres',
            'Koszt Najmu',
            'Czynsz (dodatkowo)',
            'Liczba pokoi',
            'Powierzchnia',
            'Piętro',
            'Typ Oferenta',
            'Wystawca (Nazwa)',
            'Telefon Kontaktowy',
            'Opis'
        ]

        if zakladka.row_values(1) != naglowki:
            if not zakladka.row_values(1):
                zakladka.append_row(naglowki)
            else:
                print("Nagłówki już istnieją lub pierwszy wiersz nie jest pusty. Pomijam dodawanie nagłówków.")

        return zakladka

    except Exception as e:
        print(f"BŁĄD autoryzacji Google Sheets: {e}")
        return None


def setup_selenium_driver():
    """Konfiguracja i uruchomienie sterownika Chrome pod GitHub Actions (headless Linux)."""
    print("Uruchamianie Chrome (headless, GitHub Actions)...")
    try:
        service = ChromeService(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()

        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--incognito')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        )

        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print("Chrome uruchomiony.")
        return driver
    except Exception as e:
        print(f"BŁĄD uruchomienia Chrome: {e}")
        return None


def handle_cookies(driver):
    """Akceptacja komunikatu o ciasteczkach Otodom."""
    print("Próba akceptacji ciasteczek...")
    try:
        WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
        print("Ciasteczka zaakceptowane.")
        time.sleep(1)
    except TimeoutException:
        print("Komunikat o ciasteczkach nie pojawił się lub został pominięty.")
    except Exception as e:
        print(f"Nie udało się zaakceptować ciasteczek: {e}")


def get_max_pages(driver):
    """
    Wykrywa maksymalny numer strony z paginacji (na Otodom jest to zwykle <a> z cyfrą).
    """
    max_page = 1
    try:
        # przykładowy HTML: <ul data-cy="nexus-pagination-component"> ... <a>2</a> ...
        page_links = driver.find_elements(
            By.XPATH,
            "//ul[@data-cy='nexus-pagination-component']//a"
        )

        numeric_pages = []
        for el in page_links:
            t = (el.text or "").strip()
            if t.isdigit():
                numeric_pages.append(int(t))

        if numeric_pages:
            max_page = max(numeric_pages)
    except Exception:
        pass
    return max_page


def build_page_url(base_url, page_num):
    """
    Buduje URL dla strony page=N, zachowując istniejące parametry.
    Usuwa stare page=...
    """
    url = re.sub(r'([?&])page=\d+', r'\1', base_url)
    url = re.sub(r'[?&]$', '', url)

    if page_num == 1:
        return url

    if '?' in url:
        return url + f"&page={page_num}"
    return url + f"?page={page_num}"


def main_scraper():
    """Główna funkcja skanująca listę ogłoszeń i zapisująca dane (zapis po każdej stronie)."""
    zakladka = authorize_google_sheets()
    if not zakladka:
        return

    driver = setup_selenium_driver()
    if not driver:
        return

    try:
        driver.get(URL_OTODOM)
        time.sleep(6)
        handle_cookies(driver)

        detected_max = get_max_pages(driver)
        real_max = min(MAX_STRON, detected_max) if detected_max and detected_max > 0 else MAX_STRON
        if real_max < 1:
            real_max = 1

        print(f"Wykryto stron: {detected_max} | Limit: MAX_STRON={MAX_STRON} | Przetwarzam: {real_max}")

        for page_num in range(1, real_max + 1):
            current_url = build_page_url(URL_OTODOM, page_num)

            print("\n" + "=" * 50)
            print(f"--- SCRAPOWANIE STRONY {page_num} Z {real_max} ---")
            print(f"URL: {current_url}")
            print("=" * 50)

            if page_num > 1:
                driver.get(current_url)
                time.sleep(5)

            listing_cards = driver.find_elements(By.XPATH, "//article[@data-sentry-component='AdvertCard']")

            if not listing_cards:
                print(f"OSTRZEŻENIE: Nie znaleziono ogłoszeń na stronie {page_num}. Przerywam pętlę.")
                break

            print(f"Znaleziono {len(listing_cards)} ogłoszeń do przetworzenia na stronie {page_num}.")

            rows_to_append_page = []

            for i, card in enumerate(listing_cards):
                data_scrapingu = now_pl_str()

                link, tytul, adres, cena_najmu_tekst = "Brak linku", "Brak tytułu", "Brak adresu", None
                czynsz_oplaty_tekst, liczba_pokoi_tekst, powierzchnia_tekst, pietro_tekst = None, None, None, None
                typ_oferenta, wystawca_nazwa = None, None
                telefon_kontaktowy = "Brak Danych (Poza Kartą)"
                opis_ogloszenia = "Brak opisu (Poza Kartą)"

                cena_najmu_num, czynsz_oplaty_num, liczba_pokoi_num, powierzchnia_num, pietro_num = None, None, None, None, None

                # Link, Tytuł, Adres
                try:
                    link_element = card.find_element(By.XPATH, ".//a[@data-cy='listing-item-link']")
                    link = link_element.get_attribute('href')
                    tytul = card.find_element(By.XPATH, ".//p[@data-cy='listing-item-title']").text.strip()
                    adres = card.find_element(By.XPATH, ".//p[contains(@class, 'e1cuc5p50')]").text.strip()
                except NoSuchElementException:
                    pass

                # Cena Najmu + czynsz (czynsz tylko jeśli tekst zawiera słowo "czynsz")
                try:
                    cena_najmu_tekst = card.find_element(By.XPATH, ".//span[@data-sentry-element='MainPrice']").text.strip()

                    try:
                        czynsz_el = card.find_element(By.XPATH, ".//span[contains(@class, 'eanmlll2')]")
                        tmp = (czynsz_el.text or "").strip()
                        czynsz_oplaty_tekst = tmp if "czynsz" in tmp.lower() else None
                    except NoSuchElementException:
                        czynsz_oplaty_tekst = None

                except NoSuchElementException:
                    pass

                # Parametry
                try:
                    specs = card.find_elements(By.XPATH, ".//dl/dt | .//dl/dd/span")
                    for j in range(0, len(specs), 2):
                        if j + 1 < len(specs):
                            key = specs[j].text.strip()
                            value = specs[j + 1].text.strip()

                            if 'Liczba pokoi' in key:
                                liczba_pokoi_tekst = value
                            elif 'Cena za metr kwadratowy' in key:
                                powierzchnia_tekst = value
                            elif 'Piętro' in key:
                                pietro_tekst = value
                except NoSuchElementException:
                    pass

                # Konwersje
                cena_najmu_num = clean_and_convert_to_number(cena_najmu_tekst)
                czynsz_oplaty_num = clean_and_convert_to_number(czynsz_oplaty_tekst)
                liczba_pokoi_num = clean_and_convert_to_number(liczba_pokoi_tekst)
                powierzchnia_num = clean_and_convert_to_number(powierzchnia_tekst)

                if pietro_tekst:
                    if 'parter' in pietro_tekst.lower():
                        pietro_num = 0
                    elif '+' in pietro_tekst:
                        pietro_num = clean_and_convert_to_number(pietro_tekst.replace('+', ''))
                    else:
                        pietro_num = clean_and_convert_to_number(pietro_tekst)

                # Typ Oferenta / Wystawca
                try:
                    typ_oferenta_element = card.find_element(By.XPATH, ".//span[contains(@class, 'e11ruw5v4')]")
                    typ_oferenta = typ_oferenta_element.text.strip()

                    if 'Prywatna' not in typ_oferenta:
                        try:
                            wystawca_element = card.find_element(
                                By.XPATH,
                                ".//div[@data-testid='seller-info-text-component']//div[contains(@class, 'css-')]"
                            )
                            wystawca_nazwa = wystawca_element.text.strip()
                        except NoSuchElementException:
                            wystawca_nazwa = None
                    else:
                        wystawca_nazwa = typ_oferenta

                except NoSuchElementException:
                    typ_oferenta = None
                    wystawca_nazwa = None

                print(f"Strona {page_num}: [{i + 1}/{len(listing_cards)}] {tytul} | Najem={cena_najmu_num or 'Brak'} | Czynsz={czynsz_oplaty_num or 'Brak'}")

                row_to_save = [
                    data_scrapingu,
                    link,
                    tytul,
                    adres,
                    cena_najmu_num,
                    czynsz_oplaty_num,
                    liczba_pokoi_num,
                    powierzchnia_num,
                    pietro_num,
                    typ_oferenta,
                    wystawca_nazwa,
                    telefon_kontaktowy,
                    opis_ogloszenia
                ]
                rows_to_append_page.append(row_to_save)

                if (i + 1) % 10 == 0:
                    time.sleep(1)

            if rows_to_append_page:
                print(f"Zapisuję {len(rows_to_append_page)} wierszy ze strony {page_num} do Arkusza Google...")
                try:
                    zakladka.append_rows(rows_to_append_page)
                    print(f"Pomyślnie zapisano ogłoszenia ze strony {page_num}.")
                except Exception as e:
                    print(f"BŁĄD zapisu do Arkusza dla strony {page_num}: {e}")
            else:
                print(f"Brak ogłoszeń do zapisania na stronie {page_num}.")

    except Exception as e:
        print(f"Wystąpił nieoczekiwany błąd w trakcie działania: {e}")

    finally:
        if 'driver' in locals() and driver:
            driver.quit()
            print("--- PRZEGLĄDARKA ZAMKNIĘTA ---")


# --- START (GitHub Actions: uruchom raz i zakończ) ---
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("--- START SCRAPOWANIA OTODOM (GITHUB ACTIONS) ---")
    print(f"Start o (PL): {now_pl_str()}")
    print("=" * 50)
    main_scraper()

import os
import sys
import json
import argparse
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
NAZWA_ZAKLADKI = 'Pabianice_Najem_OLX'
URL_OLX = 'https://www.olx.pl/nieruchomosci/biura-lokale/pabianice/'

BASE_URL = "https://www.olx.pl"
WARSAW_TZ = ZoneInfo("Europe/Warsaw")

BRAK = "brak danych"


def now_pl_str():
    return datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def safe_strip(x):
    if not isinstance(x, str):
        return x
    return x.strip()


def clean_and_convert_to_number(text_value, is_float=False):
    """
    Czyści tekst z symboli waluty, jednostek (m², pokoje, piętra) i spacji,
    a następnie konwertuje na int lub float.
    Jeśli konwersja się nie powiedzie, zwraca oryginalny tekst.
    """
    if not isinstance(text_value, str):
        return text_value

    tv = text_value.strip()
    if not tv or tv.lower() in ["brak danych", "brak", "—", "-"]:
        return BRAK

    cleaned_value = tv.replace('\xa0', ' ')

    # usuń waluty i separatory
    cleaned_value = (
        cleaned_value
        .replace('zł', '')
        .replace('PLN', '')
        .replace('pln', '')
        .replace('.', '')
        .replace(',', '.')
        .replace(' ', '')
        .strip()
    )

    # usuń jednostki/teksty
    cleaned_value = (
        cleaned_value
        .replace('m²', '')
        .replace('m2', '')
        .replace('pokoje', '')
        .replace('pokój', '')
        .replace('pietro', '')
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


def compute_price_per_m2(koszt_najmu_kwota, powierzchnia_liczba):
    """
    G = kwota za m2 = F / H (koszt najmu / powierzchnia)
    Zwraca float (zaokrąglony do 2) albo 'brak danych'
    """
    try:
        if isinstance(koszt_najmu_kwota, (int, float)) and isinstance(powierzchnia_liczba, (int, float)):
            if powierzchnia_liczba and powierzchnia_liczba > 0:
                return round(float(koszt_najmu_kwota) / float(powierzchnia_liczba), 2)
    except Exception:
        pass
    return BRAK


def parse_pages_cli_env_prompt(default_env="5"):
    """
    Priorytet:
    1) CLI: --pages / --max-pages
    2) ENV: MAX_PAGES
    3) Prompt lokalny (tylko gdy stdin to TTY)
    4) Fallback: 5
    """
    parser = argparse.ArgumentParser(description="OLX scraper (liczba stron konfigurowalna).")
    parser.add_argument("--pages", "--max-pages", dest="max_pages", type=int, default=None,
                        help="Ile stron OLX przeskanować (nadpisuje ENV MAX_PAGES).")
    args, _ = parser.parse_known_args()

    if args.max_pages is not None and args.max_pages > 0:
        return int(args.max_pages)

    env_val = os.environ.get("MAX_PAGES", default_env).strip()
    try:
        env_pages = int(env_val)
        if env_pages > 0:
            return env_pages
    except Exception:
        pass

    # prompt tylko lokalnie
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            raw = input("Podaj ilość stron do przeskanowania (np. 5): ").strip()
            if raw:
                v = int(raw)
                if v > 0:
                    return v
        except Exception:
            pass

    return 5


# --- GOOGLE SHEETS (GITHUB: ENV G_SHEETS_JSON) ---
def authorize_google_sheets():
    """Autoryzacja i inicjalizacja połączenia z Arkuszem Google (ENV: G_SHEETS_JSON)."""
    print("Autoryzacja do Google Sheets (ENV: G_SHEETS_JSON)...")
    try:
        creds_json = os.environ.get("G_SHEETS_JSON")
        if not creds_json:
            raise Exception("Brak zmiennej środowiskowej G_SHEETS_JSON (dodaj secret w GitHub).")

        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        arkusz = client.open_by_key(ARKUSZ_ID)

        # otwórz lub utwórz zakładkę
        try:
            zakladka = arkusz.worksheet(NAZWA_ZAKLADKI)
        except gspread.WorksheetNotFound:
            print(f"Tworzę nową zakładkę: {NAZWA_ZAKLADKI}")
            zakladka = arkusz.add_worksheet(title=NAZWA_ZAKLADKI, rows="300", cols="30")

        print(f"Pomyślnie połączono z arkuszem: {arkusz.title}, zakładka: {zakladka.title}")

        # WYMAGANIE: F=koszt najmu, G=kwota za m2, H=powierzchnia
        # Czynsz przeniesiony do I (żeby nadal był w arkuszu)
        naglowki = [
            'Data Scrapingu',          # A
            'URL Ogłoszenia',          # B
            'Typ Oferenta',            # C
            'Wystawca (Nazwa)',        # D
            'Telefon Kontaktowy',      # E
            'Koszt Najmu',             # F
            'Kwota za m²',             # G  (F/H)
            'Powierzchnia',            # H
            'Czynsz (dodatkowo)',      # I
            'Liczba pokoi',            # J
            'Parking',                 # K
            'Zwierzęta',               # L
            'Winda',                   # M
            'Poziom',                  # N
            'Umeblowane',              # O
            'Rodzaj zabudowy',         # P
            'Opis'                     # Q
        ]

        existing_first_row = zakladka.row_values(1)
        if existing_first_row != naglowki:
            if not existing_first_row:
                zakladka.append_row(naglowki)
            else:
                print("Nagłówki już istnieją lub 1. wiersz nie jest pusty — pomijam ustawianie nagłówków.")

        return zakladka

    except Exception as e:
        print(f"BŁĄD autoryzacji Google Sheets: {e}")
        return None


# --- SELENIUM (GITHUB ACTIONS) ---
def setup_selenium_driver():
    """Chrome headless pod GitHub Actions."""
    print("Uruchamianie Chrome (headless, GitHub Actions)...")
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
    """Akceptacja cookies (OLX / Onetrust)."""
    print("Próba akceptacji ciasteczek...")
    try:
        candidates = [
            (By.ID, "onetrust-accept-btn-handler"),
            (By.XPATH, "//button[contains(.,'Akceptuj')]"),
            (By.XPATH, "//button[contains(.,'Zgadzam')]"),
            (By.XPATH, "//button[contains(.,'Accept')]"),
        ]

        for by, sel in candidates:
            try:
                btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, sel)))
                btn.click()
                print("Cookies zaakceptowane.")
                time.sleep(1)
                return
            except Exception:
                continue

        print("Cookies nie pojawiły się / już zaakceptowane.")
    except Exception as e:
        print(f"Nie udało się zaakceptować ciasteczek: {e}")


# --- FUNKCJE POBIERANIA DANYCH ---
def get_max_page_number(driver):
    """Próbuje znaleźć maksymalny numer strony z elementów paginacji."""
    try:
        pagination_links = driver.find_elements(By.XPATH, "//li[@data-testid='pagination-list-item']/a")
        max_page = 1
        for link in pagination_links:
            try:
                page_num = int(link.text.strip())
                if page_num > max_page:
                    max_page = page_num
            except ValueError:
                continue

        print(f"Paginacja: Maksymalny znaleziony numer strony to: {max_page}")
        return max_page

    except Exception as e:
        print(f"BŁĄD: Nie udało się określić maksymalnego numeru strony: {e}. Zwracam 1.")
        return 1


def get_listing_details(driver, url):
    """Przechodzi na stronę ogłoszenia i pobiera wszystkie szczegóły (w tym telefon)."""
    driver.get(url)
    time.sleep(3)

    data_scrapingu = now_pl_str()

    cena_najmu = BRAK
    opis_ogloszenia = BRAK
    typ_oferenta = BRAK
    wystawca_nazwa = BRAK
    telefon_kontaktowy = BRAK
    czynsz_oplaty = BRAK
    powierzchnia = BRAK
    liczba_pokoi = BRAK
    parking = BRAK
    zwierzeta = BRAK
    winda = BRAK
    poziom = BRAK
    umeblowane = BRAK
    rodzaj_zabudowy = BRAK

    wait = WebDriverWait(driver, 10)

    # 1) KOSZT NAJMU
    try:
        cena_element = wait.until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-testid='ad-price-container']/h3"))
        )
        cena_najmu = safe_strip(cena_element.text) or BRAK
    except TimeoutException:
        pass

    # 2) WYSTAWCA
    try:
        wystawca_element = driver.find_element(By.XPATH, "//h4[@data-testid='user-profile-user-name']")
        wystawca_nazwa = safe_strip(wystawca_element.text) or BRAK
    except NoSuchElementException:
        pass

    # 3) TELEFON (klik "Pokaż")
    try:
        show_phone_button = wait.until(
            EC.presence_of_element_located((By.XPATH, "//button[@data-testid='show-phone']"))
        )
        if show_phone_button.is_displayed() and show_phone_button.is_enabled():
            try:
                show_phone_button.click()
                time.sleep(1.5)
            except ElementClickInterceptedException:
                pass

            # próba 1: element numeru
            try:
                numer_element = driver.find_element(
                    By.XPATH,
                    "//div[@data-testid='ad-contact-bar']//p[contains(@class,'css-')]"
                )
                telefon_kontaktowy = safe_strip(numer_element.text) or BRAK
            except NoSuchElementException:
                # próba 2: cały kontener
                try:
                    phone_container = driver.find_element(By.XPATH, "//div[@data-testid='ad-contact-bar']")
                    telefon_kontaktowy = (
                        phone_container.text
                        .replace("Zadzwoń", "")
                        .replace("Pokaż", "")
                        .replace("Wyślij wiadomość", "")
                        .strip()
                    ) or BRAK
                except NoSuchElementException:
                    pass

    except TimeoutException:
        # fallback: czasem numer jest bez przycisku
        try:
            numer_element = driver.find_element(
                By.XPATH,
                "//div[@data-testid='ad-contact-bar']//p[contains(@class,'css-')]"
            )
            telefon_kontaktowy = safe_strip(numer_element.text) or BRAK
        except NoSuchElementException:
            pass
    except Exception:
        pass

    # 4) PARAMETRY
    try:
        param_container = driver.find_element(By.XPATH, "//div[@data-testid='ad-parameters-container']")

        # typ oferenta (pierwszy <span> w parametrach)
        try:
            typ_element = param_container.find_element(By.TAG_NAME, "span")
            typ_oferenta = safe_strip(typ_element.text) or BRAK
        except NoSuchElementException:
            pass

        # wszystkie wiersze <p>
        param_elements = param_container.find_elements(By.TAG_NAME, "p")
        for p in param_elements:
            text = safe_strip(p.text) or ""
            if not text:
                continue

            if text.startswith("Czynsz (dodatkowo):"):
                czynsz_oplaty = text.replace("Czynsz (dodatkowo):", "").strip() or BRAK
            elif text.startswith("Powierzchnia:"):
                powierzchnia = text.replace("Powierzchnia:", "").strip() or BRAK
            elif text.startswith("Liczba pokoi:"):
                liczba_pokoi = text.replace("Liczba pokoi:", "").strip() or BRAK
            elif text.startswith("Parking:"):
                parking = text.replace("Parking:", "").strip() or BRAK
            elif text.startswith("Zwierzęta:"):
                zwierzeta = text.replace("Zwierzęta:", "").strip() or BRAK
            elif text.startswith("Winda:"):
                winda = text.replace("Winda:", "").strip() or BRAK
            elif text.startswith("Poziom:"):
                poziom = text.replace("Poziom:", "").strip() or BRAK
            elif text.startswith("Umeblowane:"):
                umeblowane = text.replace("Umeblowane:", "").strip() or BRAK
            elif text.startswith("Rodzaj zabudowy:"):
                rodzaj_zabudowy = text.replace("Rodzaj zabudowy:", "").strip() or BRAK

    except NoSuchElementException:
        pass

    # 5) OPIS
    try:
        opis_element = driver.find_element(
            By.XPATH,
            "//div[@data-cy='ad_description']//div[contains(@class, 'css-')]"
        )
        opis_ogloszenia = opis_element.text.strip().replace('\n', ' ') or BRAK
    except NoSuchElementException:
        pass

    # --- KONWERSJE LICZB ---
    koszt_najmu_kwota = clean_and_convert_to_number(cena_najmu, is_float=False)       # F
    powierzchnia_liczba = clean_and_convert_to_number(powierzchnia, is_float=True)   # H
    czynsz_oplaty_kwota = clean_and_convert_to_number(czynsz_oplaty, is_float=False) # I
    liczba_pokoi_liczba = clean_and_convert_to_number(liczba_pokoi, is_float=False)  # J
    poziom_liczba = clean_and_convert_to_number(poziom, is_float=False)              # N

    powierzchnia = powierzchnia_liczba
    liczba_pokoi = liczba_pokoi_liczba
    poziom = poziom_liczba

    # --- G: kwota za m2 (F/H) ---
    kwota_za_m2 = compute_price_per_m2(koszt_najmu_kwota, powierzchnia_liczba)

    print(
        f"  -> Zeskanowano: Najem(F): {koszt_najmu_kwota}, m2(G): {kwota_za_m2}, "
        f"Pow(H): {powierzchnia}, Czynsz(I): {czynsz_oplaty_kwota}, "
        f"Pokoje(J): {liczba_pokoi}, Poziom(N): {poziom}"
    )

    # KOLEJNOŚĆ KOLUMN (A-Q) zgodnie z nagłówkami:
    return [
        data_scrapingu,          # A
        url,                     # B
        typ_oferenta,            # C
        wystawca_nazwa,          # D
        telefon_kontaktowy,      # E
        koszt_najmu_kwota,       # F
        kwota_za_m2,             # G
        powierzchnia,            # H
        czynsz_oplaty_kwota,     # I
        liczba_pokoi,            # J
        parking,                 # K
        zwierzeta,               # L
        winda,                   # M
        poziom,                  # N
        umeblowane,              # O
        rodzaj_zabudowy,         # P
        opis_ogloszenia          # Q
    ]


def main_scraper(max_pages_user: int):
    """Jedno uruchomienie: zbierz linki z max max_pages_user stron, wejdź w ogłoszenia, zapisz hurtowo do GSheets."""
    zakladka = authorize_google_sheets()
    if not zakladka:
        return

    driver = setup_selenium_driver()
    if not driver:
        return

    try:
        print(f"Otwieram: {URL_OLX}")
        driver.get(URL_OLX)
        time.sleep(6)
        handle_cookies(driver)

        detected_max = get_max_page_number(driver)
        max_page = min(detected_max, max_pages_user)
        print(f"Limit stron (Twoje): {max_pages_user}. Wykryto={detected_max}. Przetworzę={max_page}.")

        all_links = set()

        # 1) zbierz linki
        for page_num in range(1, max_page + 1):
            if page_num == 1:
                page_url = URL_OLX
            else:
                joiner = "&" if "?" in URL_OLX else "?"
                page_url = f"{URL_OLX}{joiner}page={page_num}"

            print(f"\n--- Skanuję stronę {page_num}/{max_page} ({page_url}) ---")
            driver.get(page_url)
            time.sleep(4)

            listing_links_elements = driver.find_elements(
                By.XPATH,
                "//div[@data-cy='l-card']//a[contains(@href, '/d/oferta/')]"
            )

            current_links = set()
            for elem in listing_links_elements:
                href = elem.get_attribute('href')
                if href:
                    current_links.add(href)

            if not current_links:
                print(f"  -> Nie znaleziono linków na stronie {page_num}.")
                continue

            print(f"  -> Znaleziono {len(current_links)} linków na stronie {page_num}.")
            all_links.update(current_links)

        links_list = list(all_links)
        print(f"\nZnaleziono łącznie {len(links_list)} unikalnych ogłoszeń do przetworzenia.")

        # 2) przetwórz szczegóły i zapisz hurtowo
        rows_to_append = []

        for i, link in enumerate(links_list):
            print(f"[{i + 1}/{len(links_list)}] Przetwarzam: {link}")

            full_link = link
            if isinstance(link, str) and link.startswith('/d/oferta/'):
                full_link = BASE_URL + link

            details = get_listing_details(driver, full_link)
            rows_to_append.append(details)

            time.sleep(1)

        if rows_to_append:
            print(f"\nZapisuję {len(rows_to_append)} wierszy do Arkusza Google (hurtowo)...")
            try:
                zakladka.append_rows(rows_to_append)
                print("  -> Zapisano do Arkusza Google.")
            except Exception as e:
                print(f"BŁĄD zapisu do Arkusza: {e}")
        else:
            print("Brak danych do zapisania.")

    except Exception as e:
        print(f"Wystąpił nieoczekiwany błąd w trakcie działania: {e}")

    finally:
        if driver:
            driver.quit()
            print("\n--- PRZEGLĄDARKA ZAMKNIĘTA, PRZETWARZANIE ZAKOŃCZONE ---")


if __name__ == "__main__":
    max_pages = parse_pages_cli_env_prompt()

    print("\n" + "=" * 60)
    print("--- START OLX SCRAPER (GITHUB ACTIONS / LOCAL) ---")
    print(f"Start o (PL): {now_pl_str()}")
    print(f"MAX_PAGES (wybrane): {max_pages}")
    print("Kolumny: F=Koszt Najmu, G=Kwota za m², H=Powierzchnia")
    print("=" * 60)

    main_scraper(max_pages)

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
import re

# --- KONFIGURACJA SKRYPTU ---
ARKUSZ_ID = '1JdrNZr4eeX8Vc1w-V7XgB2ysdnxAQg_KqCE3b6RHCtc'
NAZWA_ZAKLADKI = 'TOM_OTO_Sprzedaz'
URL_OTODOM = 'https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/lodzkie/tomaszowski/gmina-miejska--tomaszow-mazowiecki/tomaszow-mazowiecki'


# --- FUNKCJE POMOCNICZE ---

def extract_numbers(text):
    """
    Funkcja do ekstrakcji czystego ciągu cyfr (z kropką, jeśli to separator dziesiętny)
    lub wartości 'Brak Danych' z tekstu.
    """
    if not text:
        return 'Brak Danych'

    processed_text = text.replace('\xa0', ' ').replace(' ', '').replace(',', '.')
    match = re.search(r'(\d+\.?\d*)', processed_text)

    if match:
        return match.group(1)

    return 'Brak Danych'


def clean_and_convert_to_number(value):
    """
    Konwertuje string z liczbą na float (żeby Sheets rozpoznał typ liczbowy).
    Jeśli 'Brak Danych' - zwraca tekst.
    """
    if value == 'Brak Danych':
        return value
    try:
        return float(value)
    except ValueError:
        return value


def authorize_google_sheets():
    """Autoryzacja i inicjalizacja połączenia z Arkuszem Google (GitHub Actions: ENV secret)."""
    print("Autoryzacja do Google Sheets (ENV: G_SHEETS_JSON)...")
    try:
        creds_json = os.environ.get("G_SHEETS_JSON")
        if not creds_json:
            raise Exception("Brak zmiennej środowiskowej G_SHEETS_JSON (dodaj secret w GitHub).")

        creds_dict = json.loads(creds_json)
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        arkusz = client.open_by_key(ARKUSZ_ID)

        # Próba otwarcia lub stworzenia zakładki
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
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        )

        service = ChromeService(ChromeDriverManager().install())
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
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
        print("Ciasteczka zaakceptowane.")
        time.sleep(1)
    except TimeoutException:
        print("Komunikat o ciasteczkach nie pojawił się lub został pominięty (lub już akceptowano).")
    except Exception as e:
        print(f"Nie udało się zaakceptować ciasteczek: {e}")


def process_page(driver):
    """Pobiera dane z aktualnej strony z listą ogłoszeń."""
    rows_to_append = []

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-sentry-component='AdvertCard']"))
        )
    except TimeoutException:
        print("BŁĄD: Nie znaleziono żadnych kart ogłoszeń na stronie po 15s.")
        return rows_to_append

    listing_cards = driver.find_elements(
        By.XPATH,
        "//article[@data-sentry-component='AdvertCard'] | //article[@data-sentry-component='VipAdvertCard']"
    )

    if not listing_cards:
        print("OSTRZEŻENIE: Nie znaleziono żadnych kart ogłoszeń na bieżącej stronie.")
        return rows_to_append

    print(f"Znaleziono {len(listing_cards)} ogłoszeń do przetworzenia.")

    for i, card in enumerate(listing_cards):
        data_scrapingu = time.strftime("%Y-%m-%d %H:%M:%S")
        link, tytul, adres = "Brak linku", "Brak tytułu", "Brak adresu"

        cena_najmu_text = "Brak Danych"
        czynsz_oplaty_text = "Brak Danych"
        liczba_pokoi_text = "Brak Danych"
        powierzchnia_text = "Brak Danych"
        pietro_text = "Brak Danych"

        typ_oferenta, wystawca_nazwa = "Brak Danych", "Brak Danych"
        telefon_kontaktowy = "Brak Danych (Poza Kartą)"
        opis_ogloszenia = "Brak opisu (Poza Kartą)"

        # 1) Link, tytuł, adres
        try:
            link_element = card.find_element(By.XPATH, ".//a[@data-cy='listing-item-link']")
            link = link_element.get_attribute('href')

            try:
                tytul = card.find_element(By.XPATH, ".//p[@data-cy='listing-item-title']").text.strip()
            except NoSuchElementException:
                try:
                    tytul = card.find_element(By.XPATH, ".//p[@data-sentry-element='Title']").text.strip()
                except NoSuchElementException:
                    pass

            try:
                adres = card.find_element(By.XPATH, ".//p[contains(@class, 'e1cuc5p50')]").text.strip()
            except NoSuchElementException:
                try:
                    adres = card.find_element(By.XPATH, ".//div[@data-sentry-element='AddressWrapper']//p").text.strip()
                except NoSuchElementException:
                    pass

        except NoSuchElementException:
            pass

        # 2) Cena i czynsz (tak jak w Twoim HTML)
        try:
            # koszt najmu: <span data-sentry-element="MainPrice">1550&nbsp;zł</span>
            cena_najmu_element = card.find_element(By.XPATH, ".//span[@data-sentry-element='MainPrice']")
            cena_najmu_text = (cena_najmu_element.text or "").strip()

            # czynsz: "+ czynsz: 600 zł/miesiąc"
            try:
                czynsz_oplaty_element = card.find_element(By.XPATH, ".//span[contains(@class, 'eanmlll2')]")
                czynsz_oplaty_text = (czynsz_oplaty_element.text or "").strip()
                # nie usuwamy agresywnie, bo format bywa "+ czynsz: 600 zł/miesiąc"
                # wyciągniemy liczby niżej przez extract_numbers()
            except NoSuchElementException:
                czynsz_oplaty_text = "Brak info o czynszu"
        except NoSuchElementException:
            pass

        # 3) Parametry (pokoje / powierzchnia / piętro)
        try:
            specs = card.find_elements(By.XPATH, ".//dl/dt | .//dl/dd/span")
            for j in range(0, len(specs), 2):
                if j + 1 < len(specs):
                    key = specs[j].text.strip()
                    value = specs[j + 1].text.strip()

                    if 'Liczba pokoi' in key:
                        liczba_pokoi_text = value
                    elif 'Cena za metr kwadratowy' in key or 'Powierzchnia' in key:
                        powierzchnia_text = value
                    elif 'Piętro' in key:
                        pietro_text = value
        except NoSuchElementException:
            pass

        # 4) Typ oferenta / wystawca
        try:
            typ_oferenta_element = card.find_element(By.XPATH, ".//span[contains(@class, 'e11ruw5v4')]")
            typ_oferenta = typ_oferenta_element.text.strip()

            if 'Prywatna' not in typ_oferenta:
                try:
                    wystawca_element = card.find_element(By.XPATH, ".//span[contains(@class, 'css-g6wttb')]")
                    wystawca_nazwa = wystawca_element.text.strip()
                except NoSuchElementException:
                    try:
                        wystawca_nazwa = card.find_element(By.XPATH, ".//span[@data-sentry-element='OwnerName']").text.strip()
                    except NoSuchElementException:
                        wystawca_nazwa = typ_oferenta
            else:
                wystawca_nazwa = typ_oferenta
        except Exception:
            pass

        # --- KONWERSJE DO LICZB ---
        raw_cena_najmu = extract_numbers(cena_najmu_text)
        raw_czynsz_oplaty = extract_numbers(czynsz_oplaty_text)
        raw_liczba_pokoi = extract_numbers(liczba_pokoi_text)
        raw_powierzchnia = extract_numbers(powierzchnia_text)
        raw_pietro = extract_numbers(pietro_text)

        cena_najmu = clean_and_convert_to_number(raw_cena_najmu)
        czynsz_oplaty = clean_and_convert_to_number(raw_czynsz_oplaty)
        liczba_pokoi = clean_and_convert_to_number(raw_liczba_pokoi)
        powierzchnia = clean_and_convert_to_number(raw_powierzchnia)
        pietro = clean_and_convert_to_number(raw_pietro)

        print(f"[{i + 1}/{len(listing_cards)}] Tytuł: {tytul} | Najem: {cena_najmu} | Czynsz: {czynsz_oplaty}")

        row_to_save = [
            data_scrapingu,
            link,
            tytul,
            adres,
            cena_najmu,       # kolumna E
            czynsz_oplaty,    # kolumna F
            liczba_pokoi,
            powierzchnia,
            pietro,
            typ_oferenta,
            wystawca_nazwa,
            telefon_kontaktowy,
            opis_ogloszenia
        ]
        rows_to_append.append(row_to_save)

        if (i + 1) % 10 == 0:
            time.sleep(1)

    return rows_to_append


def main_scraper():
    """Główna funkcja skanująca listę ogłoszeń i zapisująca dane do Sheets."""
    zakladka = authorize_google_sheets()
    if not zakladka:
        return

    driver = setup_selenium_driver()
    if not driver:
        return

    all_rows_to_append = []
    current_page = 1

    try:
        print(f"Otwieram: {URL_OTODOM}")
        driver.get(URL_OTODOM)
        time.sleep(8)

        handle_cookies(driver)

        while True:
            print(f"\n--- PRZETWARZANIE STRONY {current_page} ---")

            try:
                rows_from_page = process_page(driver)
                all_rows_to_append.extend(rows_from_page)
            except Exception as e:
                print(f"BŁĄD przetwarzania strony {current_page}: {e}")

            next_page_button_xpath = "//button[@title='Go to next Page']"

            try:
                next_button = WebDriverWait(driver, 6).until(
                    EC.element_to_be_clickable((By.XPATH, next_page_button_xpath))
                )

                if next_button.get_attribute('disabled') == 'true' or 'disabled' in (next_button.get_attribute('class') or ''):
                    print("Przycisk 'Następna strona' jest nieaktywny. Koniec ogłoszeń.")
                    break

                try:
                    next_button.click()
                except ElementClickInterceptedException:
                    print("Przycisk 'Następna strona' przesłonięty -> klik JS.")
                    driver.execute_script("arguments[0].click();", next_button)

                current_page += 1
                time.sleep(6)

            except TimeoutException:
                print("Nie znaleziono przycisku 'Następna strona' (Timeout). Koniec paginacji.")
                break

        if all_rows_to_append:
            print(f"\nZapisuję {len(all_rows_to_append)} wierszy do Arkusza Google...")
            try:
                zakladka.append_rows(all_rows_to_append)
                print("Pomyślnie zapisano wszystkie ogłoszenia.")
            except Exception as e:
                print(f"BŁĄD zapisu do Arkusza: {e}")
        else:
            print("Nie było nic do zapisania.")

    except Exception as e:
        print(f"Wystąpił nieoczekiwany błąd: {e}")

    finally:
        if driver:
            driver.quit()
            print("--- PRZEGLĄDARKA ZAMKNIĘTA ---")


# --- START (GitHub Actions: uruchom raz i zakończ) ---
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("--- START SCRAPOWANIA OTODOM (GITHUB ACTIONS) ---")
    print(f"Start o: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    main_scraper()

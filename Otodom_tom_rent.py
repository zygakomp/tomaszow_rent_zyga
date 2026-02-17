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
    print("Szukam kart ogłoszeń...")
    
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-cy="listing-item"]'))
        )
    except:
        print("Nie znaleziono ofert. Sprawdzam czy strona się załadowała...")
        return rows_to_append

    listing_cards = driver.find_elements(By.CSS_SELECTOR, 'article[data-cy="listing-item"]')
    print(f"Znaleziono {len(listing_cards)} ogłoszeń.")

    for card in listing_cards:
        data_scrapingu = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # Domyślne wartości
        link, tytul, adres = "Brak", "Brak", "Brak"
        cena, czynsz, pokoje, powierzchnia, pietro = "Brak", "Brak", "Brak", "Brak", "Brak"
        typ_oferenta, wystawca = "Brak", "Brak"

        try:
            # 1. Link i Tytuł
            link_el = card.find_element(By.CSS_SELECTOR, 'a[data-cy="listing-item-link"]')
            link = link_el.get_attribute('href')
            tytul = card.find_element(By.CSS_SELECTOR, 'p[data-cy="listing-item-title"]').text.strip()
            
            # 2. Adres
            try:
                adres = card.find_element(By.CSS_SELECTOR, 'p[data-cy="listing-item-address"]').text.strip()
            except:
                pass

            # 3. Cena główna
            try:
                cena_raw = card.find_element(By.CSS_SELECTOR, 'span[data-cy="listing-item-price"]').text
                cena = clean_and_convert_to_number(extract_numbers(cena_raw))
            except:
                pass

            # 4. Czynsz (dodatkowy)
            try:
                # Szukamy tekstu zawierającego "+ czynsz" w okolicy ceny
                extra_info = card.text
                if "+ czynsz" in extra_info:
                    # Wyciągamy kwotę czynszu po słowie "czynsz:"
                    match = re.search(r'czynsz:\s*([\d\s,]+)', extra_info)
                    if match:
                        czynsz = clean_and_convert_to_number(extract_numbers(match.group(1)))
            except:
                pass

            # 5. Parametry (Pokoje, Powierzchnia, Piętro)
            # Szukamy w liście definicji <dl> wewnątrz karty
            try:
                specs = card.find_elements(By.CSS_SELECTOR, 'dl > div')
                for spec in specs:
                    text = spec.text.lower()
                    val = spec.find_element(By.TAG_NAME, 'dd').text
                    
                    if 'poko' in text:
                        pokoje = clean_and_convert_to_number(extract_numbers(val))
                    elif 'm²' in text or 'powierzchnia' in text:
                        powierzchnia = clean_and_convert_to_number(extract_numbers(val))
                    elif 'piętro' in text:
                        pietro = clean_and_convert_to_number(extract_numbers(val))
            except:
                pass

            # 6. Typ oferenta
            try:
                # Zazwyczaj ikona lub tekst na dole karty
                info_text = card.text
                if "Biuro" in info_text:
                    typ_oferenta = "Biuro nieruchomości"
                    wystawca = "Biuro"
                else:
                    typ_oferenta = "Prywatna"
                    wystawca = "Osoba prywatna"
            except:
                pass

            # Tworzymy pełny wiersz zgodnie z Twoimi nagłówkami
            row = [
                data_scrapingu,
                link,
                tytul,
                adres,
                cena,
                czynsz,
                pokoje,
                powierzchnia,
                pietro,
                typ_oferenta,
                wystawca,
                "Brak Danych (Poza Kartą)",
                "Brak opisu (Poza Kartą)"
            ]
            rows_to_append.append(row)

        except Exception as e:
            print(f"Błąd przy przetwarzaniu karty: {e}")
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



from bs4 import BeautifulSoup
import requests
from src.data_scraping.scraper import extract_card_id

def main():
    BASE_URL = "https://www.futbin.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/115.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    url = f"{BASE_URL}/27/players?page={1}"
    print(f"[Page {1}] Fetching {url}")

    response = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.find_all("tr", class_="player-row")

    for row in rows:
        name_tag = row.find("a", class_="table-player-name")
        if name_tag and "href" in name_tag.attrs:
            href = name_tag["href"]
            card_id = extract_card_id(href)
            print(card_id)

if __name__=="__main__":
    main()
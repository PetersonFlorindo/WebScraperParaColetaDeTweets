import csv
import json
import os
import re
import time
import random
import pyautogui
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


DETAILS_PATH = "details_valid.csv"
TOTAL_TARGET = 2000
MAX_PAGES = 10
PROGRESS_FILE = "progress_movies.json"
STOPWORDS = {
    "a", "an", "the",
    "and", "or", "but",
    "of", "in", "on", "at", "to", "for", "from", "by", "with", "without",
    "as", "is", "are",
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos",
    "e", "ou", "mas",
    "em", "no", "na", "nos", "nas",
    "por", "para", "com", "sem",
}


def ensure_header(path: str, cols: list[str]):
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            first = f.readline().strip()
        if first == ",".join(cols):
            return
    except FileNotFoundError:
        pass

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.DictWriter(f, fieldnames=cols).writeheader()


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text or "movie"


def normalize_query(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_queries(title: str) -> list[str]:
    base = normalize_query(title)
    if not base:
        return []
    tokens = [tok for tok in base.lower().split() if tok and tok not in STOPWORDS]
    title_no_stopwords_count = len(tokens)

    queries = []
    if title_no_stopwords_count <= 1:
        queries.append(f"{base} trailer")

    queries.extend([
        base,
        f"{base} movie",
        f"{base} theater",
        f"{base} film",
        f"{base} cinema",
    ])

    # Remove duplicatas preservando ordem.
    return list(dict.fromkeys(queries))


def parse_release_date(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def read_movies(path: str):
    movies = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(1000)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        reader = csv.DictReader(f, delimiter=dialect.delimiter)
        for row in reader:
            tmdb_id = str(row.get("tmdb_id", "")).strip()
            title = str(row.get("title", "")).strip()
            release_raw = str(row.get("release_date", "")).strip()
            budget_raw = str(row.get("budget_usd", "")).strip()
            release_dt = parse_release_date(release_raw)
            try:
                budget = int(float(budget_raw)) if budget_raw else 0
            except ValueError:
                budget = 0
            if tmdb_id and title and release_dt:
                movies.append({
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "release_dt": release_dt,
                    "budget_usd": budget,
                })
    return movies


def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {"completed": [], "partial": {}, "last_attempted": ""}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            completed = data.get("completed", [])
            partial = data.get("partial", {})
            last_attempted = data.get("last_attempted", "")
            return {"completed": completed, "partial": partial, "last_attempted": last_attempted}
    except Exception:
        return {"completed": [], "partial": {}, "last_attempted": ""}


def save_progress(completed, partial, last_attempted):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "completed": completed,
                "partial": partial,
                "last_attempted": last_attempted,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def build_search_url_for_day(day: datetime, query: str):
    q = quote_plus(query)
    since = day.strftime("%Y-%m-%d")
    until = (day + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"https://nitter.net/search?f=tweets&q={q}&since={since}&until={until}"


def extract_tweets_from_page(driver, cols, seen, total, query, output_csv):
    tweet_cards = driver.find_elements(By.CSS_SELECTOR, "div.timeline-item")
    added = 0
    with open(output_csv, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        for card in tweet_cards:
            if total >= TOTAL_TARGET:
                break
            try:
                tweet_link = card.find_element(By.CSS_SELECTOR, "a.tweet-link")
                url = tweet_link.get_attribute("href")
            except Exception:
                url = None

            if not url or url in seen:
                continue
            seen.add(url)

            try:
                user = card.find_element(By.CSS_SELECTOR, "a.username").text.strip()
            except Exception:
                user = ""
            try:
                name = card.find_element(By.CSS_SELECTOR, "a.fullname").text.strip()
            except Exception:
                name = ""
            try:
                text = card.find_element(By.CSS_SELECTOR, "div.tweet-content").text
                text = " ".join(text.split())
            except Exception:
                text = ""
            try:
                date = card.find_element(By.CSS_SELECTOR, "span.tweet-date a").text.strip()
            except Exception:
                date = ""

            w.writerow({
                "query": query,
                "tweet_date": date,
                "username": user,
                "name": name,
                "text": text,
                "url": url,
            })
            total += 1
            added += 1
    return total, added


def collect_for_query_window(driver, wait, cols, seen, total, query, output_csv, start_day, end_day):
    day = start_day
    while total < TOTAL_TARGET and day <= end_day:
        driver.get(build_search_url_for_day(day, query))
        time.sleep(3)
        try:
            driver.execute_script("document.body.click();")
        except Exception:
            pass

        pages = 0
        last_total = total
        while total < TOTAL_TARGET and pages < MAX_PAGES:
            if "429" in driver.page_source or "Too Many Requests" in driver.page_source:
                driver.refresh()
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.timeline-item, div.show-more, div.error")))
                except Exception:
                    pass

            total, _ = extract_tweets_from_page(driver, cols, seen, total, query, output_csv)
            if total >= TOTAL_TARGET:
                break

            if total == last_total:
                try:
                    driver.find_element(By.CSS_SELECTOR, "div.show-more a")
                except Exception:
                    break
            last_total = total

            try:
                for _ in range(2):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                load_more = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.show-more a")))
                rect = driver.execute_script(
                    """
                    const el = arguments[0];
                    const r = el.getBoundingClientRect();
                    return {
                      left: r.left,
                      top: r.top,
                      width: r.width,
                      height: r.height,
                      screenX: window.screenX,
                      screenY: window.screenY,
                      outerHeight: window.outerHeight,
                      innerHeight: window.innerHeight,
                      dpr: window.devicePixelRatio || 1
                    };
                    """,
                    load_more,
                )
                chrome_top = rect["outerHeight"] - rect["innerHeight"]
                center_x = rect["screenX"] + rect["left"] + rect["width"] / 2
                center_y = rect["screenY"] + chrome_top + rect["top"] + rect["height"] / 2
                center_x *= rect["dpr"]
                center_y *= rect["dpr"]
                pyautogui.moveTo(center_x + random.uniform(-3, 3), center_y + random.uniform(-3, 3), duration=0.2)
                pyautogui.click()
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.timeline-item")))
            except Exception:
                break

            pages += 1

        day += timedelta(days=1)

    return total


def main():
    cols = [
        "query",
        "tweet_date",
        "username",
        "name",
        "text",
        "url",
    ]

    movies = read_movies(DETAILS_PATH)
    if not movies:
        print("[err] Nenhum filme encontrado.")
        return

    try:
        n_to_collect = int(input("Quantos títulos deseja coletar nesta execução? ").strip())
    except Exception:
        print("[err] Valor inválido.")
        return
    if n_to_collect <= 0:
        print("[err] Digite um número > 0.")
        return

    progress = load_progress()
    completed = set(progress.get("completed", []))
    partial = dict(progress.get("partial", {}))
    last_attempted = str(progress.get("last_attempted", "")).strip()

    movies.sort(key=lambda m: m.get("budget_usd", 0), reverse=True)
    start_after_key = last_attempted
    if not start_after_key and completed:
        for movie in movies:
            key = f"{movie['tmdb_id']}:{movie['title']}"
            if key in completed:
                start_after_key = key

    if start_after_key:
        found = any(f"{m['tmdb_id']}:{m['title']}" == start_after_key for m in movies)
        if not found:
            start_after_key = ""

    driver = webdriver.Chrome()
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    collected_now = 0

    try:
        started = not start_after_key
        for movie in movies:
            if collected_now >= n_to_collect:
                break
            tmdb_id = movie["tmdb_id"]
            title = movie["title"]
            key = f"{tmdb_id}:{title}"
            if not started:
                if key == start_after_key:
                    started = True
                continue
            if key in completed:
                continue

            query = normalize_query(title)
            if not query:
                continue
            last_attempted = key
            save_progress(sorted(completed), partial, last_attempted)

            output_csv = f"tweets_{tmdb_id}_{slugify(title)}.csv"
            ensure_header(output_csv, cols)

            seen = set()
            total = 0

            release_dt = movie["release_dt"]
            start_day = release_dt - timedelta(days=30)
            end_day = release_dt - timedelta(days=1)

            attempted_queries = set()
            for query in build_queries(title):
                if total >= TOTAL_TARGET:
                    break
                attempted_queries.add(query)
                total = collect_for_query_window(
                    driver, wait, cols, seen, total, query, output_csv, start_day, end_day
                )

            trailer_query = f"{normalize_query(title)} trailer"
            if total < TOTAL_TARGET and trailer_query and trailer_query not in attempted_queries:
                total = collect_for_query_window(
                    driver, wait, cols, seen, total, trailer_query, output_csv, start_day, end_day
                )

            if total >= TOTAL_TARGET:
                completed.add(key)
                partial.pop(key, None)
                save_progress(sorted(completed), partial, last_attempted)
                collected_now += 1
                print(f"[ok] {title} ({tmdb_id}) -> {total} tweets em {output_csv}")
            else:
                partial[key] = total
                save_progress(sorted(completed), partial, last_attempted)
                print(f"[warn] {title} ({tmdb_id}) -> {total} tweets (incompleto)")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()

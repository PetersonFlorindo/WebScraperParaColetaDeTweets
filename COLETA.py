# -*- coding: utf-8 -*-
"""
✅ 1 CSV por filme: ./out_movies/tweets_<tmdb_id>_<slug>.csv
✅ Pergunta quantos filmes coletar por execução
✅ Continua do último filme (checkpoint progress_nitter.json)
✅ Começa pelos mais recentes (release_date DESC)
✅ Coleta até 200 tweets únicos por filme
✅ Janela: 14 dias antes do release (inclusive) até o release (inclusive)
✅ Query com operadores: since:/until:/-filter:replies/-filter:nativeretweets (mais estável)
✅ AUDIÊNCIA GLOBAL: LANG=None (não usa lang: na query)
✅ FLUSH a cada 50 tweets novos no CSV do filme
✅ Sleep dinâmico (auto-pacer)

Requisitos no arquivo details_valid(a).*:
- tmdb_id
- title
- release_date
"""

from ntscraper import Nitter
from datetime import datetime, timedelta
import pandas as pd
import os, time, csv, re, hashlib, random, json


# =========================
# CONFIG
# =========================
DETAILS_BASENAME = "details_valida"       # também acha details_valid*
OUT_DIR = "out_movies"
PROGRESS_FILE = "progress_nitter.json"

TWEETS_PER_MOVIE = 200
FLUSH_EVERY = 50

# ✅ Global: NÃO filtra idioma
LANG = None   # ex.: "pt" para português; None = global (não adiciona lang: na query)

# Per call: instâncias públicas costumam ser mais estáveis com <= 100
PER_CALL = 100

NITTER_INSTANCES = [
    "https://twitt.re",
    "https://nitter.privacydev.net",
    "https://xcancel.com",
    "https://nitter.dashy.a3x.dn.nyx.im",
    "http://46.250.231.226:8889",
    "http://198.46.203.183:8089",
    "http://5.78.115.92:8081",
    "http://153.127.64.199:8081",
    # extras (melhora resiliência)
    "https://nitter.poast.org",
    "https://nitter.space",
    "https://nitter.fdn.fr",
    "https://nitter.cz",
    "https://ntrqq.com",
    "https://nitter.lacontrevoie.fr",
]


# =========================
# PACER (sleep dinâmico)
# =========================
class DynamicPacer:
    def __init__(
        self,
        init_sleep: float = 0.45,
        min_sleep: float = 0.12,
        max_sleep: float = 4.00,
        dec_factor: float = 0.90,
        inc_empty: float = 1.18,
        inc_error: float = 1.45,
        jitter: float = 0.10,
        verbose: bool = True,
    ):
        self.sleep = init_sleep
        self.min_sleep = min_sleep
        self.max_sleep = max_sleep
        self.dec_factor = dec_factor
        self.inc_empty = inc_empty
        self.inc_error = inc_error
        self.jitter = jitter
        self.verbose = verbose
        self.empty_streak = 0
        self.error_streak = 0

    def _clip(self):
        self.sleep = max(self.min_sleep, min(self.sleep, self.max_sleep))

    def wait(self):
        j = 1.0 + random.uniform(-self.jitter, self.jitter)
        time.sleep(self.sleep * j)

    def on_success(self, n: int):
        self.empty_streak = 0
        self.error_streak = 0
        self.sleep *= self.dec_factor
        self._clip()
        if self.verbose:
            print(f"        [PACER] success n={n} | sleep={self.sleep:.3f}s")

    def on_empty(self):
        self.empty_streak += 1
        mult = self.inc_empty * (1.0 + 0.04 * min(self.empty_streak, 10))
        self.sleep *= mult
        self._clip()
        if self.verbose:
            print(f"        [PACER] empty x{self.empty_streak} | sleep={self.sleep:.3f}s")

    def on_error(self, msg: str = ""):
        self.error_streak += 1
        self.empty_streak = 0
        mult = self.inc_error * (1.0 + 0.06 * min(self.error_streak, 12))
        self.sleep *= mult
        self._clip()
        if self.verbose:
            short = (msg[:90] + "...") if msg and len(msg) > 90 else (msg or "")
            print(f"        [PACER] error x{self.error_streak} | sleep={self.sleep:.3f}s | {short}")


# =========================
# Helpers
# =========================
def slugify(s: str, max_len: int = 80) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "_", s).strip("_")
    return s[:max_len] if len(s) > max_len else s

def norm_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())

def parse_release_date(s) -> datetime | None:
    if pd.isna(s):
        return None
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return pd.to_datetime(s, dayfirst=True).to_pydatetime()
    except Exception:
        return None

def find_details_file(basename: str) -> str:
    if os.path.exists(basename) and os.path.isfile(basename):
        return basename

    exts = [".csv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl"]
    for ext in exts:
        p = basename + ext
        if os.path.exists(p) and os.path.isfile(p):
            return p

    for fn in os.listdir("."):
        if fn.startswith(basename) and os.path.isfile(fn):
            return fn

    if basename == "details_valida":
        for fn in os.listdir("."):
            if fn.startswith("details_valid") and os.path.isfile(fn):
                return fn

    raise FileNotFoundError(f"Não encontrei '{basename}' nem variações na pasta atual.")

def read_details(path: str) -> pd.DataFrame:
    low = path.lower()
    if low.endswith(".xlsx") or low.endswith(".xls"):
        df = pd.read_excel(path)
    elif low.endswith(".parquet"):
        df = pd.read_parquet(path)
    elif low.endswith(".jsonl"):
        df = pd.read_json(path, lines=True)
    elif low.endswith(".json"):
        df = pd.read_json(path)
    else:
        # CSV robusto
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            sample = f.read(64_000)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
            sep = dialect.delimiter
        except Exception:
            sep = ";"

        df = None
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                df = pd.read_csv(path, sep=sep, engine="python", encoding=enc, quotechar='"', escapechar="\\")
                print(f"[info] CSV lido com sep='{sep}' encoding='{enc}' | shape={df.shape}")
                break
            except Exception as e:
                last_err = e
                df = None

        if df is None:
            print(f"[warn] Falha lendo CSV. Tentando on_bad_lines='skip' (sep='{sep}')...")
            df = pd.read_csv(
                path, sep=sep, engine="python", encoding="utf-8-sig",
                on_bad_lines="skip", quotechar='"', escapechar="\\"
            )
            print(f"[info] CSV lido com on_bad_lines='skip' | shape={df.shape}")
            if last_err:
                print(f"[warn] erro original: {last_err}")

    required = {"tmdb_id", "title", "release_date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes: {missing}\nEncontradas: {list(df.columns)}")
    return df

def movie_out_path(tmdb_id: str, title: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, f"tweets_{tmdb_id}_{slugify(title)}.csv")

def ensure_csv_header(path: str, cols: list[str]):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=cols).writeheader()

def tweet_unique_key_from_fields(url: str | None, username: str | None, date: str | None, text: str | None) -> str:
    if url and str(url).strip():
        return "url:" + str(url).strip()
    raw = f"{(username or '').strip()}|{(date or '').strip()}|{norm_spaces(text or '')}"
    return "hash:" + hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()

def load_movie_state(movie_csv: str):
    seen, count = set(), 0
    if os.path.exists(movie_csv) and os.path.getsize(movie_csv) > 0:
        with open(movie_csv, "r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                k = tweet_unique_key_from_fields(row.get("url"), row.get("username"), row.get("tweet_date"), row.get("text"))
                seen.add(k)
                count += 1
    return seen, count

def read_progress():
    if not os.path.exists(PROGRESS_FILE) or os.path.getsize(PROGRESS_FILE) == 0:
        return {"next_index": 0}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {"next_index": 0}
    except Exception:
        return {"next_index": 0}

def write_progress(next_index: int):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"next_index": next_index}, f, ensure_ascii=False, indent=2)

def ask_how_many_movies() -> int:
    while True:
        raw = input("Quantos filmes você quer coletar nesta execução? ").strip()
        try:
            n = int(raw)
            if n <= 0:
                print("Digite um número inteiro > 0.")
                continue
            return n
        except ValueError:
            print("Valor inválido. Digite um número inteiro (ex.: 10).")


# =========================
# Query builder (do jeito que “funciona”)
# =========================
def build_query(base: str, since_day: str, until_day: str) -> str:
    parts = [base]
    if LANG:
        parts.append(f"lang:{LANG}")
    parts += [
        f"since:{since_day}",
        f"until:{until_day}",
        "-filter:replies",
        "-filter:nativeretweets",
    ]
    return " ".join(parts)

def build_query_variants(title: str, year: int | None):
    base = norm_spaces(title)
    hashtag = re.sub(r"[^A-Za-z0-9]", "", base)
    hashtag = f"#{hashtag}" if hashtag else None

    variants = [
        f"\"{base}\"",
        base,
        f"\"{base}\" movie",
        f"{base} movie",
        f"\"{base}\" film",
        f"{base} film",
    ]
    if hashtag:
        variants.append(hashtag)
    if year:
        variants.append(f"\"{base}\" {year}")
        variants.append(f"{base} {year}")

    out, seen = [], set()
    for q in variants:
        qn = q.strip()
        if qn and qn not in seen:
            seen.add(qn)
            out.append(qn)
    return out

def fetch(instance: str, full_query: str, per_call: int):
    # clamp recomendado
    n = max(10, min(int(per_call), 1000))
    scraper = Nitter(log_level=1, skip_instance_check=False)
    return scraper.get_tweets(
        full_query,
        mode="term",
        number=n,
        instance=instance,
        max_retries=3,
        # NÃO passa since/until/exclude/language aqui (já está na query)
    )

def date_iter(start_dt: datetime, end_dt: datetime):
    # itera por dia, inclusive
    cur = start_dt.date()
    end = end_dt.date()
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


# =========================
# Coleta por filme (diária + flush 50)
# =========================
def collect_for_one_movie(tmdb_id: str, title: str, release_str: str, release_dt: datetime):
    # janela inclusive: release-14 .. release
    start_dt = release_dt - timedelta(days=14)
    end_dt = release_dt

    out_csv = movie_out_path(tmdb_id, title)

    cols = [
        "tmdb_id", "title", "release_date",
        "since", "until",
        "tweet_date", "username", "name",
        "text", "url", "retweets", "replies", "quotes", "likes", "language"
    ]

    ensure_csv_header(out_csv, cols)

    seen_keys, already = load_movie_state(out_csv)
    remaining = max(0, TWEETS_PER_MOVIE - already)

    print(
        f"\n[movie] {title} (tmdb_id={tmdb_id}) | release={release_str} | "
        f"janela={start_dt.strftime('%Y-%m-%d')}..{end_dt.strftime('%Y-%m-%d')} | "
        f"já_salvos={already} | faltam={remaining}"
    )

    if remaining <= 0:
        print("  [skip] Já está completo (>=200).")
        return True

    pacer = DynamicPacer(verbose=True)
    print(f"    [PACER] init sleep: {pacer.sleep:.3f}s")

    queries = build_query_variants(title, release_dt.year)
    insts_base = NITTER_INSTANCES[:]

    # Vamos fazer append sempre (header já garantido)
    buffer_rows = []
    added_this_movie = 0

    def flush_buffer():
        nonlocal buffer_rows, added_this_movie
        if not buffer_rows:
            return
        with open(out_csv, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            for r in buffer_rows:
                w.writerow(r)
            f.flush()
        added_this_movie += len(buffer_rows)
        print(f"    [FLUSH] +{len(buffer_rows)} | total_no_csv={already + added_this_movie}")
        buffer_rows = []

    # coleta dia a dia (muito mais estável que tentar a janela inteira)
    for day in date_iter(start_dt, end_dt):
        if remaining <= 0:
            break

        since_day = day.strftime("%Y-%m-%d")
        until_day = (day + timedelta(days=1)).strftime("%Y-%m-%d")  # exclusivo, como no seu script

        print(f"\n    === Dia {since_day} ===")
        insts = insts_base[:]
        random.shuffle(insts)

        for qbase in queries:
            if remaining <= 0:
                break

            full_q = build_query(qbase, since_day, until_day)
            print(f"    [query] {full_q}")

            got_any_for_query = False

            for inst in insts:
                if remaining <= 0:
                    break

                pacer.wait()
                try:
                    print(f"      [inst] {inst} | per_call={PER_CALL}")
                    res = fetch(inst, full_q, PER_CALL)
                    tweets = res.get("tweets", []) or []
                    print(f"      [raw] {len(tweets)}")

                    if tweets:
                        pacer.on_success(len(tweets))
                    else:
                        pacer.on_empty()

                    added = 0
                    for t in tweets:
                        if remaining <= 0:
                            break

                        user = t.get("user") or {}
                        url = t.get("link")
                        k = tweet_unique_key_from_fields(url, user.get("username"), t.get("date"), t.get("text"))

                        if k in seen_keys:
                            continue
                        seen_keys.add(k)

                        buffer_rows.append({
                            "tmdb_id": tmdb_id,
                            "title": title,
                            "release_date": release_str,
                            "since": since_day,
                            "until": until_day,
                            "tweet_date": t.get("date"),
                            "username": user.get("username"),
                            "name": user.get("name"),
                            "text": norm_spaces(t.get("text") or ""),
                            "url": url,
                            "retweets": t.get("retweets"),
                            "replies": t.get("replies"),
                            "quotes": t.get("quotes"),
                            "likes": t.get("likes"),
                            "language": t.get("language"),
                        })

                        remaining -= 1
                        added += 1

                        if len(buffer_rows) >= FLUSH_EVERY:
                            flush_buffer()

                    print(f"      [ok] {inst}: novos={added} | faltam={remaining}")

                    # Se veio algo, não martela outras instâncias na mesma query (igual ao seu)
                    if added > 0:
                        got_any_for_query = True
                        break

                except Exception as e:
                    msg = str(e)
                    print(f"      [warn] {inst}: {msg}")
                    pacer.on_error(msg)
                    pacer.wait()

            # pausa leve entre variantes
            time.sleep(0.25 if got_any_for_query else 0.10)

        # flush no final do dia também (mesmo se < 50)
        flush_buffer()
        time.sleep(0.35)

    # flush final
    flush_buffer()

    final_count = already + added_this_movie
    print(f"\n  [done] total_no_csv={final_count} (meta=200)")
    return final_count >= TWEETS_PER_MOVIE


# =========================
# MAIN
# =========================
def main():
    n_movies = ask_how_many_movies()

    details_path = find_details_file(DETAILS_BASENAME)
    print(f"[info] Lendo: {details_path}")

    df = read_details(details_path).copy()
    df["tmdb_id"] = df["tmdb_id"].astype(str).str.strip()
    df["title"] = df["title"].astype(str)

    df["_release_dt"] = df["release_date"].apply(parse_release_date)
    df = df[~df["_release_dt"].isna()].copy()

    # ✅ Mais recentes primeiro
    df = df.sort_values(by=["_release_dt", "tmdb_id"], ascending=[False, True]).reset_index(drop=True)

    prog = read_progress()
    start_idx = int(prog.get("next_index", 0))

    if start_idx >= len(df):
        print(f"[info] Fim da lista (next_index={start_idx}, total_filmes={len(df)}).")
        return

    end_idx = min(len(df), start_idx + n_movies)
    print(f"[info] Continuando do índice {start_idx} até {end_idx - 1} (total={end_idx - start_idx})")
    print(f"[info] Checkpoint: {PROGRESS_FILE}")
    os.makedirs(OUT_DIR, exist_ok=True)

    idx = start_idx
    while idx < end_idx:
        row = df.iloc[idx]
        tmdb_id = row["tmdb_id"]
        title = row["title"]
        release_dt = row["_release_dt"]
        release_str = str(row["release_date"])

        collect_for_one_movie(tmdb_id, title, release_str, release_dt)

        idx += 1
        write_progress(idx)
        print(f"[checkpoint] next_index={idx}")

    print(f"\n[ok] Execução finalizada. Próximo start será next_index={idx}.")


if __name__ == "__main__":
    main()

"""Private portfolio configuration comes from EARNINGS_CONFIG_JSON, never the repo."""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from email.mime.text import MIMEText
from base64 import urlsafe_b64encode
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://www.nasdaq.com"}
TIMES = {"time-pre-market": "ABD seansı açılmadan önce", "time-after-hours": "ABD seansı kapandıktan sonra", "time-not-supplied": "Saat açıklanmamış"}
MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
TG_LABEL = "Bilanco Radari/Telegram teslim edildi"


def request(method, url, **kwargs):
    """Retry read requests only: never blindly retry a message send."""
    attempts = 3 if method == "GET" else 1
    for n in range(attempts):
        try:
            response = requests.request(method, url, timeout=30, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if n + 1 == attempts:
                # Do not log URLs: a Telegram URL contains a bot credential.
                raise RuntimeError("Uzak servis isteği başarısız") from None
            time.sleep(n + 1)


def portfolio(rows):
    result = {}
    header_seen = False
    for row in rows:
        symbol = str(row[0]).strip().upper() if row else ""
        if symbol in ("SEMBOL", "SYMBOL", "SEMBOLÜ"):
            header_seen = True
            continue
        if header_seen and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol) and len(row) > 2 and str(row[2]).strip():
            result[symbol] = str(row[2]).strip()
    if not result:
        raise RuntimeError("Portföy sayfasından şirket listesi okunamadı")
    return result


class Google:
    def __init__(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_info({
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "token_uri": "https://oauth2.googleapis.com/token",
        })
        creds.refresh(Request())
        self.headers = {"Authorization": "Bearer " + creds.token}

    def call(self, path, method="GET", **kwargs):
        return request(method, "https://gmail.googleapis.com/gmail/v1/users/me/" + path, headers=self.headers, **kwargs).json()

    def holdings(self, config):
        title = config["sheet_name"].replace("'", "''")
        bounded_range = quote("'" + title + "'!A1:D1000", safe="")
        url = "https://sheets.googleapis.com/v4/spreadsheets/" + config["spreadsheet_id"] + "/values/" + bounded_range
        response = request("GET", url, headers=self.headers, params={"valueRenderOption": "FORMATTED_VALUE"}).json()
        return portfolio(response.get("values", []))

    def telegram_label(self):
        labels = self.call("labels").get("labels", [])
        found = next((v["id"] for v in labels if v["name"] == TG_LABEL), None)
        if found:
            return found
        return self.call("labels", "POST", json={"name": TG_LABEL, "labelListVisibility": "labelHide", "messageListVisibility": "hide"})["id"]

    def find_sent(self, subject):
        messages = self.call("messages", params={"q": 'in:sent subject:"' + subject + '"', "maxResults": 20}).get("messages", [])
        for msg in messages:
            data = self.call("messages/" + msg["id"], params={"format": "metadata", "metadataHeaders": "Subject"})
            actual = next((h["value"] for h in data.get("payload", {}).get("headers", []) if h["name"].lower() == "subject"), "")
            if actual == subject:
                return data
        return None

    def send(self, recipient, subject, body):
        message = MIMEText(body, "plain", "utf-8")
        message["To"] = recipient
        message["Subject"] = subject
        raw = urlsafe_b64encode(message.as_bytes()).decode()
        return self.call("messages/send", "POST", json={"raw": raw})

    def mark_telegram(self, message_id, label):
        self.call("messages/" + message_id + "/modify", "POST", json={"addLabelIds": [label]})


class Telegram:
    def __init__(self):
        self.token = os.environ["TELEGRAM_BOT_TOKEN"]
        self.chat = os.environ["TELEGRAM_CHAT_ID"]

    def call(self, operation, **data):
        # Telegram credentials are used only with Telegram's own HTTPS endpoint.
        payload = request("POST", "https://api.telegram.org/bot" + self.token + "/" + operation, json={"chat_id": self.chat, **data}).json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram gönderimi reddedildi")
        return payload["result"]

    def verify(self):
        if self.call("getChat").get("type") != "private":
            raise RuntimeError("Telegram hedefi kişisel sohbet değil; gönderim durduruldu")

    def send(self, body):
        for chunk in chunks(body):
            self.call("sendMessage", text=chunk, disable_web_page_preview=True)


def chunks(body, limit=3800):
    if len(body) <= limit:
        return [body]
    result, current = [], ""
    for line in body.splitlines(True):
        if len(current) + len(line) > limit and current:
            result.append(current)
            current = ""
        while len(line) > limit:
            result.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        result.append(current)
    return result


def calendar_day(day, symbols):
    url = "https://api.nasdaq.com/api/calendar/earnings"
    payload = request("GET", url, headers=HEADERS, params={"date": day.isoformat()}).json()
    if (payload.get("status") or {}).get("rCode") != 200:
        raise RuntimeError("Takvim isteği reddedildi")
    data = payload.get("data")
    if data is None:
        raise RuntimeError("Bu günün takvimi mevcut değil")
    rows = data.get("rows")
    if rows is not None and not isinstance(rows, list):
        raise RuntimeError("Takvim biçimi değişti")
    return [dict(symbol=r["symbol"], name=r["name"].strip(), date=day.isoformat(), time=TIMES.get(r.get("time"), "Saat açıklanmamış"), status="Takvim tarihi; şirket teyidi yapılmadı", source="https://www.nasdaq.com/market-activity/earnings?date=" + day.isoformat()) for r in (rows or []) if r.get("symbol") in symbols]


def secondary(symbol, today):
    url = "https://stockanalysis.com/" + ("quote/otc/" + symbol if symbol == "IFNNY" else "stocks/" + symbol.lower()) + "/"
    text = BeautifulSoup(request("GET", url).text, "html.parser").get_text(" ", strip=True)
    match = re.search(r"Earnings Date\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s+(20\d{2})", text)
    if not match:
        return None
    month, day, year = match.groups()
    date = dt.date(int(year), MONTHS[month], int(day))
    if date < today:
        return None
    return dict(symbol=symbol, name=symbol, date=date.isoformat(), time="Saat açıklanmamış", status="Tahmini tarih; şirket teyidi yapılmadı", source=url)


def collect(symbols, today, horizon=49):
    days = [today + dt.timedelta(days=i) for i in range(horizon) if (today + dt.timedelta(days=i)).weekday() < 5]
    found, failures = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(calendar_day, d, symbols) for d in days]
        for day, future in zip(days, futures):
            try:
                for event in future.result():
                    if event["symbol"] not in found:
                        found[event["symbol"]] = event
            except Exception:
                failures.append(day.isoformat())
    missing = sorted(set(symbols) - set(found))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for symbol, future in zip(missing, [pool.submit(secondary, s, today) for s in missing]):
            try:
                event = future.result()
                if event:
                    event["name"] = symbols[symbol]
                    found[symbol] = event
            except Exception:
                failures.append("şirket takvimi " + symbol)
    return sorted(found.values(), key=lambda e: (e["date"], e["symbol"])), failures


def event_line(event):
    date = dt.date.fromisoformat(event["date"]).strftime("%d.%m.%Y")
    return f"{event['symbol']} — {date}\n{event['time']}\n{event['status']}\nKaynak: {event['source']}"


def make_plans(events, symbols, today, days_before=3, initial=False):
    plans = []
    for e in events:
        left = (dt.date.fromisoformat(e["date"]) - today).days
        if 0 <= left <= days_before:
            key = f"reminder:{e['symbol']}:{e['date']}"
            body = "🔔 Bilanço hatırlatması\n\n" + event_line(e) + f"\n\nBilançoya {left} gün kaldı. Tarih, şirketin bulunduğu piyasadaki takvim günüdür."
            plans.append((key, "Bilanço hatırlatması", body))
    if today.weekday() == 0 or initial:
        end = today + dt.timedelta(days=6 - today.weekday())
        weekly = [e for e in events if today.isoformat() <= e["date"] <= end.isoformat()]
        body = f"📅 Bu haftanın bilançoları ({today:%d.%m}–{end:%d.%m.%Y})\n\n"
        body += "\n\n".join(event_line(e) for e in weekly) if weekly else "Takvimde bu haftaya ait bilanço bulunamadı."
        next_events = [e for e in events if e["date"] > end.isoformat()][:8]
        if next_events:
            body += "\n\nSıradaki bilançolar:\n" + "\n".join(f"{e['symbol']}: {dt.date.fromisoformat(e['date']):%d.%m.%Y} ({e['status']})" for e in next_events)
        unknown = sorted(set(symbols) - {e["symbol"] for e in events})
        body += f"\n\nİzlenen şirket: {len(symbols)}\n" + ", ".join(sorted(symbols))
        if unknown:
            body += "\n\nYaklaşan tarihi henüz bulunamayanlar: " + ", ".join(unknown) + ". Bu şirketler takipte; tarih bulunduğunda hatırlatma kapsamına girer."
        body += "\n\nŞirket teyidi yapılmayan takvim tarihleri değişebilir. ABD seans ifadesi, ABD piyasa saatini anlatır. Kontrol: Türkiye saatiyle her gün 09.00."
        week = today - dt.timedelta(days=today.weekday())
        plans.append((f"weekly:{week.isoformat()}", "Bu haftanın bilançoları", body))
    return plans


def deliver(google, telegram, recipient, label, plan):
    key, title, body = plan
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    subject = f"{title} [ER-{digest}]"
    existing = google.find_sent(subject)
    message = existing or google.send(recipient, subject, body)
    if label not in message.get("labelIds", []):
        telegram.send(body)
        google.mark_telegram(message["id"], label)
    return existing is None


def run(config, google, telegram, today, initial=False, dry_run=False):
    holdings = google.holdings(config)
    symbols = {**holdings, **{s: s for s in config["extra_symbols"]}}
    if not dry_run:
        profile = google.call("profile")
        if profile["emailAddress"].lower() != config["email"].lower():
            raise RuntimeError("E-posta hedefi bağlı hesabın kendisi değil")
        telegram.verify()
    events, failures = collect(symbols, today)
    plans = make_plans(events, symbols, today, config.get("days_before", 3), initial)
    if failures:
        warning = "⚠️ Bilanço takvimi kısmen okunamadı. Okunamayan günlerdeki/şirketlerdeki bilançolar doğrulanamadı. Kontrol yeniden denenecek.\n" + "\n".join(failures)
        plans.append(("coverage:" + today.isoformat(), "Bilanço takvimi kontrol hatası", warning))
    sent = 0
    if not dry_run:
        label = google.telegram_label()
        for plan in plans:
            sent += deliver(google, telegram, config["email"], label, plan)
    # Public Actions logs contain only counters, never holdings or recipients.
    summary = {"monitored": len(symbols), "dates_found": len(events), "plans": len(plans), "new_emails": sent, "coverage_failures": len(failures), "dry_run": dry_run}
    print(json.dumps(summary))
    with open("earnings-run-summary.md", "w", encoding="utf-8") as out:
        out.write("Bilanço takvimi kontrolü\n\n" + "\n".join(f"- {k}: {v}" for k, v in summary.items()))
    if failures:
        raise RuntimeError("Takvim kapsamı eksik; durum bildirildi")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    telegram = None
    try:
        config = json.loads(os.environ["EARNINGS_CONFIG_JSON"])
        today = dt.datetime.now(ZoneInfo("Europe/Istanbul")).date()
        telegram = Telegram()
        google = Google()
        run(config, google, telegram, today, args.initial, args.dry_run)
    except Exception:
        # No raw exception or request body is written to public logs.
        print("Bilanço radarı çalışması tamamlanamadı. Özel bildirimleri ve bağlantı ayarlarını kontrol edin.", file=sys.stderr)
        if not args.dry_run and telegram is not None:
            try:
                telegram.verify()
                telegram.send("⚠️ Bilanço radarı kontrolünde hata oluştu. Bugünkü hatırlatmaların tamamı doğrulanamadı. GitHub Actions çalışma durumunu kontrol edin.")
            except Exception:
                pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()

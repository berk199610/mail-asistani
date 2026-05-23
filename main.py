import os
import base64
import json
import urllib.request
import urllib.parse
import urllib.error
import time
import re
import io
import random
import pandas as pd
import yfinance as yf
import gspread
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime

# ================= AYARLAR =================

SECILEN_MODEL = "models/gemini-2.5-flash"
YEDEK_MODEL = "models/gemini-2.5-flash-lite"

HER_MESAJDAKI_MAIL_SAYISI = 8
BEKLEME_SURESI_SANIYE = 35

MAX_RETRY = 5
ILK_BEKLEME = 20

ACIL_UYARI_ESIK = 7

# Yasaklı gönderenler — bunlar AI'ya hiç gönderilmez
YASAKLI_KELIMELER = [
    "Yapı Kredi", "Garanti", "İş Bankası", "Akbank", "Midas", "Google Flights",
    "Unsubscribe", "Üyelikten ayrıl", "View in browser", "Tarayıcıda görüntüle"
]
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

GMAIL_QUERY = 'newer_than:1d -category:promotions -category:social -in:spam'

# ================= ETİKET TANIMLARI =================
# Bu listede tanımlananlar Gmail'de yoksa otomatik oluşturulur.
# AI bu kategorilere göre karar verecek.

KONU_ETIKETLERI = {
    "01-AI-Altyapi": {"renk": "#4986e7", "ad": "Berkonomi/01-AI-Altyapı"},      # mavi
    "02-AI-Donanim": {"renk": "#3dc789", "ad": "Berkonomi/02-AI-Donanım"},      # yeşil
    "03-AI-Enerji":  {"renk": "#ffad47", "ad": "Berkonomi/03-AI-Enerji"},       # turuncu
    "04-Savunma-Uzay": {"renk": "#cc3a21", "ad": "Berkonomi/04-Savunma-Uzay"},  # kırmızı
    "05-Makro-Diger": {"renk": "#8e63ce", "ad": "Berkonomi/05-Makro-Diğer"},    # mor
    "06-Sirket-Earnings": {"renk": "#16a766", "ad": "Berkonomi/06-Şirket-Earnings"},  # koyu yeşil
    "07-Genel-Piyasa": {"renk": "#fad165", "ad": "Berkonomi/07-Genel-Piyasa"},  # sarı
}

AKSIYON_ETIKETLERI = {
    "ANALIZ":         {"renk": "#16a766", "ad": "Aksiyon/✓Analiz-Edildi"},
    "YUKSEK_ONEM":    {"renk": "#cc3a21", "ad": "Aksiyon/⭐Yüksek-Önem"},
    "ELE_KONUSUZ":    {"renk": "#666666", "ad": "Aksiyon/⊘Konusuz"},
    "ELE_REKLAM":     {"renk": "#666666", "ad": "Aksiyon/⊘Reklam"},
    "ELE_BANKA":      {"renk": "#666666", "ad": "Aksiyon/⊘Bankacılık"},
    "ELE_DUSUK":      {"renk": "#999999", "ad": "Aksiyon/⊘Düşük-Değer"},
    "HATA":           {"renk": "#ff7537", "ad": "Aksiyon/✗Hata"},
}

# Etiket isim → Gmail label_id eşleştirme cache (program başında doldurulur)
LABEL_CACHE = {}


# ================= PROMPT =================

PROMPT_KURALLARI = """
ROL:
Sen bir "Kıdemli Fon Yöneticisi"sin. 15+ yıl ABD borsalarında deneyimin var.
Berkonomi adlı bir yatırım analizi topluluğuna içerik üretiyorsun.

GÖREV:
Aşağıda numaralandırılmış e-postalar var (MAIL_1, MAIL_2, ...).
HER mail için şu kararı vereceksin:
1. Konu kategorisi nedir?
2. Analiz mi edeyim, eleyeyim mi?
3. Eliyorsam sebebi ne?
4. Analiz ediyorsam tam analiz yaz.

BERK'İN İLGİ ALANLARI:
- 01-AI-Altyapi: NBIS, hyperscaler CapEx, data center, network
- 02-AI-Donanim: NVDA, MU, HBM, semiconductor (ALAB, CRDO, MRVL, SNDK, LITE)
- 03-AI-Enerji: CEG, TLN, VST, doğal gaz, midstream (WMB, TRP, DTM, ENB, KMI)
- 04-Savunma-Uzay: Defense, savunma sensörü, RF GaN, EO/IR, uzay sektörü
- 05-Makro-Diger: Fed faiz, ekonomik veri, sektör makro trendi
- 06-Sirket-Earnings: Tek hisse earnings, M&A, kontrat, partnership
- 07-Genel-Piyasa: Genel piyasa yorumu, indeks haberleri, market sentiment

ELE_KONUSUZ: Sağlık, biotech, pharma, healthcare, drug trial → Berk'in tema dışı
ELE_REKLAM: Pure reklam, webinar daveti, indirim, promosyon
ELE_BANKA: TR banka haberleri, Midas reklamı, broker promosyonu
ELE_DUSUK: Berk'in temasında olsa bile yüzeysel, derinlik yok, sadece headline

KALİTE KURALLARI (analiz edilenler için):
- Yüzeysel özet YAPMA, rakam olmalı (revenue, EBITDA, margin %)
- Hype dilinden kaçın, objektif ol
- Türkçe yaz, ticker'ları $ ile yaz ($NBIS, $MU)
- Risk de söyle, sadece pozitif yön değil

ÇIKTI FORMATI:
HER mail için sadece JSON döndür (HTML YOK, sadece JSON).
Tüm cevabı şu yapıda VER:

```json
[
  {
    "mail_no": 1,
    "konu_kategorisi": "02-AI-Donanim",
    "aksiyon": "analiz",
    "ele_sebebi": null,
    "ticker": "$NVDA",
    "sirket": "NVIDIA",
    "konu": "Q4 earnings beat, data center revenue +78% YoY",
    "ozet": "NVIDIA Q4'te 22.1B$ revenue ile %15 beklentiyi aştı. Data center segmenti 18.4B$ ile toplamın %83'ü.",
    "yorum": "Hyperscaler CapEx döngüsü 2026'da hızlanıyor. Berk'in AI hardware tezi konfirme oluyor, ancak gross margin 75%'ten 73%'e gerilemesi izlenmeli.",
    "risk": "Çin ihracat kısıtları H2'de daralma yaratabilir, Blackwell yield sorunu.",
    "etki": "Pozitif",
    "onem_skoru": 9
  },
  {
    "mail_no": 2,
    "konu_kategorisi": "01-AI-Altyapi",
    "aksiyon": "ele",
    "ele_sebebi": "ELE_DUSUK",
    "konu_basligi": "Hyperscaler X büyüyor",
    "ele_aciklama": "Headline haber, somut rakam yok, derinlik yetersiz"
  },
  ...
]
```

ÖNEM SKORU (sadece analiz edilenlerde):
- 1-3: Düşük
- 4-6: Orta
- 7-10: Yüksek (portföy etkileyebilir)

ÖNEMLİ KURALLAR:
1. SADECE JSON döndür, başka hiçbir metin yazma (HTML, açıklama, yorum YASAK)
2. Her mail için MUTLAKA bir entry olsun (analiz ya da ele)
3. mail_no sırası ile uyumlu olsun (1, 2, 3, ...)
4. ele_sebebi sadece şunlardan biri olabilir: ELE_KONUSUZ, ELE_REKLAM, ELE_DUSUK
   (ELE_BANKA otomatik atanır, sen kullanma)
5. Analiz edilenlerde TÜM alanlar dolu olmalı.
"""


# ================= TELEGRAM =================

def telegram_gonder(mesaj, acil=False, parse_mode="HTML"):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if len(mesaj) > 4000:
        mesaj = mesaj[:3950] + "\n\n... (kesildi)"

    data = {
        "chat_id": chat_id,
        "text": mesaj,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
        "disable_notification": "false" if acil else "true"
    }
    try:
        encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
        return True
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")
        return False


# ================= GMAIL ETİKETLEME =================

def etiketleri_olustur_veya_getir(service):
    """
    Sistem etiketlerini Gmail'de oluşturur (yoksa) ve label_id'lerini cache'ler.
    """
    global LABEL_CACHE

    try:
        mevcut = service.users().labels().list(userId='me').execute().get('labels', [])
        mevcut_map = {lbl['name']: lbl['id'] for lbl in mevcut}

        tum_etiketler = list(KONU_ETIKETLERI.values()) + list(AKSIYON_ETIKETLERI.values())

        for etiket in tum_etiketler:
            ad = etiket['ad']
            if ad in mevcut_map:
                LABEL_CACHE[ad] = mevcut_map[ad]
            else:
                # Yeni etiket oluştur
                try:
                    yeni = service.users().labels().create(
                        userId='me',
                        body={
                            "name": ad,
                            "labelListVisibility": "labelShow",
                            "messageListVisibility": "show"
                        }
                    ).execute()
                    LABEL_CACHE[ad] = yeni['id']
                    print(f"✓ Etiket oluşturuldu: {ad}")
                except Exception as e:
                    print(f"❌ Etiket oluşturulamadı '{ad}': {e}")

        print(f"Toplam {len(LABEL_CACHE)} etiket hazır.")
        return True
    except Exception as e:
        print(f"Etiket sistemi hatası: {e}")
        return False


def maile_etiket_ata(service, message_id, etiket_anahtarlari):
    """
    Bir maile bir veya daha fazla etiket ekler.
    etiket_anahtarlari: ["02-AI-Donanim", "ANALIZ", "YUKSEK_ONEM"] gibi.
    """
    label_ids = []
    for anahtar in etiket_anahtarlari:
        if anahtar in KONU_ETIKETLERI:
            ad = KONU_ETIKETLERI[anahtar]['ad']
        elif anahtar in AKSIYON_ETIKETLERI:
            ad = AKSIYON_ETIKETLERI[anahtar]['ad']
        else:
            continue
        if ad in LABEL_CACHE:
            label_ids.append(LABEL_CACHE[ad])

    if not label_ids:
        return False

    try:
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={"addLabelIds": label_ids}
        ).execute()
        return True
    except Exception as e:
        print(f"Etiket atama hatası ({message_id}): {e}")
        return False


# ================= GMAIL & SHEETS =================

def giris_yap_ve_sheets():
    try:
        info = {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        SCOPES = [
            'https://www.googleapis.com/auth/gmail.modify',
            'https://www.googleapis.com/auth/spreadsheets'
        ]
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        gmail_service = build('gmail', 'v1', credentials=creds)

        sheet = None
        try:
            if "SHEET_ID" in os.environ:
                gc = gspread.authorize(creds)
                sheet = gc.open_by_key(os.environ["SHEET_ID"]).sheet1
        except Exception as e:
            print(f"Sheets bağlantı hatası: {e}")

        return gmail_service, sheet
    except Exception as e:
        print(f"Genel Giriş Hatası: {e}")
        return None, None


def html_temizle(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for s in soup(["script", "style", "head", "title", "meta", "footer"]):
            s.extract()
        text = soup.get_text(separator=' ')
        lines = (line.strip() for line in text.splitlines())
        text = '\n'.join(chunk for chunk in lines if chunk)
        return text[:15000]
    except Exception:
        return html_content[:5000]


def yasakli_mi(gonderen):
    if HARIC_TUTULACAK_MAIL in gonderen:
        return True
    for kelime in YASAKLI_KELIMELER:
        if kelime.lower() in gonderen.lower():
            return True
    return False


def mailleri_getir(service):
    """
    Gmail'den mailleri çeker. Yasaklı olanları AYIRARAK döndürür.
    Her mail kaydı bir dict: {message_id, gonderen, konu, icerik}
    """
    print(f"Mail sorgusu: {GMAIL_QUERY}")
    results = service.users().messages().list(userId='me', q=GMAIL_QUERY, maxResults=150).execute()
    messages = results.get('messages', [])

    analiz_listesi = []  # AI'ya gidecek
    banka_listesi = []   # ELE_BANKA olarak etiketlenecek

    if not messages:
        return analiz_listesi, banka_listesi

    print(f"Bulunan ham mail sayısı: {len(messages)}")

    for msg in messages:
        try:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            headers = txt['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "Konu Yok")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Bilinmiyor")
            msg_id = msg['id']

            # Yasaklı kontrolü
            if yasakli_mi(sender):
                banka_listesi.append({
                    "message_id": msg_id,
                    "gonderen": sender,
                    "konu": subject
                })
                continue

            # İçeriği çıkar
            body = ""
            if 'parts' in txt['payload']:
                for part in txt['payload']['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data')
                        if data:
                            body = base64.urlsafe_b64decode(data).decode(errors='ignore')
                    elif part['mimeType'] == 'text/html':
                        data = part['body'].get('data')
                        if data:
                            html_raw = base64.urlsafe_b64decode(data).decode(errors='ignore')
                            body = html_temizle(html_raw)
            elif 'body' in txt['payload']:
                data = txt['payload']['body'].get('data')
                if data:
                    body = base64.urlsafe_b64decode(data).decode(errors='ignore')

            if not body:
                body = txt.get('snippet', '')
            if "<" in body and ">" in body:
                temiz_body = html_temizle(body)
            else:
                temiz_body = body

            final_text = temiz_body.replace("\r", "").replace("\n", " ")[:12000]

            analiz_listesi.append({
                "message_id": msg_id,
                "gonderen": sender,
                "konu": subject,
                "icerik": final_text
            })
        except Exception as e:
            print(f"Mail okuma hatası (atlanıyor): {e}")
            continue

    print(f"AI'ya gidecek: {len(analiz_listesi)} | Banka/yasaklı: {len(banka_listesi)}")
    return analiz_listesi, banka_listesi


# ================= AI ANALİZ =================

def _gemini_istek_yap(model_adi, prompt, api_key, timeout=180):
    clean_name = model_adi.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.95,
            "topK": 40,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json"  # JSON modu
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    req = urllib.request.Request(
        url, data=json.dumps(data).encode('utf-8'),
        headers=headers, method='POST'
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode('utf-8')
        parsed = json.loads(raw)

    if 'candidates' not in parsed or not parsed['candidates']:
        finish = parsed.get('promptFeedback', {}).get('blockReason', 'BILINMEYEN')
        raise ValueError(f"AI yanıt vermedi (blockReason={finish})")

    cand = parsed['candidates'][0]
    if 'content' not in cand or 'parts' not in cand['content']:
        finish = cand.get('finishReason', 'BILINMEYEN')
        raise ValueError(f"AI içerik üretmedi (finishReason={finish})")

    return cand['content']['parts'][0]['text']


def ai_ile_analiz_et(mail_paketi):
    """
    Mail paketini AI'ya gönderir, JSON listesi döndürür.
    mail_paketi: [{message_id, gonderen, konu, icerik}, ...]
    return: [{mail_no, konu_kategorisi, aksiyon, ...}, ...] (her mail için 1 entry)
    """
    # Maillere numara ver
    mail_metinleri = []
    for i, m in enumerate(mail_paketi, 1):
        mail_metinleri.append(
            f"=== MAIL_{i} ===\n"
            f"GÖNDEREN: {m['gonderen']}\n"
            f"KONU: {m['konu']}\n"
            f"İÇERİK: {m['icerik']}"
        )

    prompt = f"{PROMPT_KURALLARI}\n\n{chr(10).join(mail_metinleri)}"
    api_key = os.environ["GEMINI_API_KEY"]

    aktif_model = SECILEN_MODEL
    son_hata = None
    telegram_uyari = False

    for deneme in range(MAX_RETRY):
        try:
            ham_cevap = _gemini_istek_yap(aktif_model, prompt, api_key)

            # JSON parse et
            # Bazen ```json ... ``` ile gelir, temizle
            temiz = re.sub(r'^```json\s*|```\s*$', '', ham_cevap.strip(), flags=re.MULTILINE)
            temiz = temiz.strip()

            kararlar = json.loads(temiz)
            if not isinstance(kararlar, list):
                raise ValueError(f"AI list dönmedi, tip: {type(kararlar)}")

            return kararlar

        except urllib.error.HTTPError as e:
            son_hata = e
            if e.code in (429, 500, 502, 503, 504):
                bekleme = ILK_BEKLEME * (2 ** deneme) + random.uniform(0, 5)
                print(f"⚠️  HTTP {e.code} ({aktif_model}). {bekleme:.0f}sn... ({deneme+1}/{MAX_RETRY})")
                if deneme == 2 and not telegram_uyari:
                    telegram_gonder(
                        f"⚠️ <b>API Problemi</b>\nGemini HTTP {e.code} dönüyor, yedek modele geçiliyor.",
                        acil=True
                    )
                    telegram_uyari = True
                if deneme == 2 and aktif_model == SECILEN_MODEL:
                    aktif_model = YEDEK_MODEL
                    print(f"🔄 Yedek modele geçiliyor: {aktif_model}")
                time.sleep(bekleme)
                continue
            elif e.code == 404:
                if aktif_model == SECILEN_MODEL:
                    aktif_model = YEDEK_MODEL
                    continue
                return None
            else:
                try:
                    err_body = e.read().decode('utf-8', errors='ignore')[:500]
                except Exception:
                    err_body = ""
                print(f"Kalıcı hata HTTP {e.code}: {err_body}")
                return None

        except urllib.error.URLError as e:
            son_hata = e
            bekleme = ILK_BEKLEME * (2 ** deneme) + random.uniform(0, 5)
            print(f"⚠️  Ağ hatası: {e}. {bekleme:.0f}sn...")
            time.sleep(bekleme)
            continue

        except (json.JSONDecodeError, ValueError) as e:
            son_hata = e
            bekleme = ILK_BEKLEME * (2 ** deneme)
            print(f"⚠️  Parse/format hatası: {e}. {bekleme:.0f}sn... ({deneme+1}/{MAX_RETRY})")
            time.sleep(bekleme)
            continue

        except Exception as e:
            son_hata = e
            print(f"⚠️  Beklenmeyen: {type(e).__name__}: {e}")
            time.sleep(ILK_BEKLEME)
            continue

    telegram_gonder(
        f"⛔ <b>Paket Analiz Edilemedi</b>\n{MAX_RETRY} deneme sonrası başarısız.\nHata: <code>{son_hata}</code>",
        acil=True
    )
    return None


# ================= HTML RAPOR =================

def html_analiz_karti(karar, ticker_grafik_var):
    """Bir analiz kararı için HTML kartı üretir."""
    konu_etiket = karar.get('konu_kategorisi', '?')
    ticker = karar.get('ticker', '')
    sirket = karar.get('sirket', '')
    konu = karar.get('konu', '')
    ozet = karar.get('ozet', '')
    yorum = karar.get('yorum', '')
    risk = karar.get('risk', '')
    etki = karar.get('etki', 'Nötr')
    onem = karar.get('onem_skoru', 0)

    etki_renk = "#27ae60" if etki.lower() == "pozitif" else (
                "#c0392b" if etki.lower() == "negatif" else "#7f8c8d")

    yuksek_onem_badge = ""
    if isinstance(onem, (int, float)) and onem >= ACIL_UYARI_ESIK:
        yuksek_onem_badge = (
            f'<span style="background:#c0392b;color:white;padding:3px 8px;'
            f'border-radius:4px;font-size:11px;margin-left:8px;">⭐ YÜKSEK ÖNEM</span>'
        )

    grafik_info = '<span style="color:#27ae60;font-size:11px;">📈 Grafik aşağıda</span>' if ticker_grafik_var else ''

    return f"""
    <div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 20px;
                border-radius: 8px; background-color: #fcfcfc;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3 style="color: #2c3e50; margin: 0;">{konu}{yuksek_onem_badge}</h3>
            <span style="background:#ecf0f1;padding:3px 8px;border-radius:4px;font-size:11px;">
                {konu_etiket}
            </span>
        </div>
        <p style="margin: 8px 0;">
            <strong style="color:{etki_renk};">{ticker}</strong> — {sirket}
            &nbsp; <span style="color:#7f8c8d;font-size:12px;">Etki: {etki} | Önem: {onem}/10</span>
            &nbsp; {grafik_info}
        </p>
        <p><strong>📝 Özet:</strong> {ozet}</p>
        <div style="background-color: #e8f8f5; padding: 10px; border-radius: 4px;
                    color: #117a65; margin-top:10px;">
            <strong>💡 Yorum:</strong> {yorum}
        </div>
        <p style="font-size: 12px; color: #c0392b; margin-top:8px;">
            <strong>⚠️ Risk:</strong> {risk}
        </p>
    </div>
    """


def html_elenen_satiri(karar):
    """Bir elenen mail için tek satır HTML."""
    konu = karar.get('konu_basligi', karar.get('konu', '—'))
    sebep = karar.get('ele_sebebi', '?').replace('ELE_', '')
    aciklama = karar.get('ele_aciklama', '')
    kategori = karar.get('konu_kategorisi', '?')

    return (
        f'<tr>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:12px;">{konu}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:11px;color:#7f8c8d;">{kategori}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:11px;color:#c0392b;">{sebep}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:11px;color:#666;">{aciklama}</td>'
        f'</tr>'
    )


def html_elenen_banka_satiri(mail):
    """Yasaklı gönderenden gelen mail için tek satır."""
    return (
        f'<tr>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:12px;">{mail["konu"]}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:11px;color:#7f8c8d;">{mail["gonderen"][:50]}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:11px;color:#c0392b;">BANKA</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;font-size:11px;color:#666;">Yasaklı gönderen listesi</td>'
        f'</tr>'
    )


# ================= GRAFİK & MAIL =================

def grafik_ciz(ticker):
    try:
        clean_ticker = ticker.replace("$", "").strip()
        stock = yf.Ticker(clean_ticker)
        hist = stock.history(period="1mo")
        if hist.empty:
            return None
        plt.figure(figsize=(10, 4))
        plt.plot(hist.index, hist['Close'], label=clean_ticker, color='#2980b9', linewidth=2)
        plt.title(f'{clean_ticker} - Son 30 Günlük Trend', fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.legend()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close()
        return buf
    except Exception:
        return None


def mail_gonder_resimli(service, kime, konu, html_content, grafik_buffers):
    try:
        msg = MIMEMultipart()
        msg['To'] = kime
        msg['From'] = "me"
        msg['Subject'] = konu

        if grafik_buffers:
            html_content += "<hr><h3>📈 Hisse Grafikleri (Son 1 Ay)</h3>"
            for i, (ticker, buf) in enumerate(grafik_buffers):
                cid = f"chart_{i}"
                html_content += (
                    f"<p><b>{ticker}</b></p>"
                    f"<img src='cid:{cid}' style='width:100%; max-width:800px; "
                    f"border:1px solid #ddd;'><br>"
                )
                img = MIMEImage(buf.read())
                img.add_header('Content-ID', f'<{cid}>')
                msg.attach(img)

        msg.attach(MIMEText(html_content, 'html'))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Mail gönderildi: {konu}")
    except Exception as e:
        print(f"Mail hatası: {e}")


def listeyi_bol(liste, parca_boyutu):
    for i in range(0, len(liste), parca_boyutu):
        yield liste[i:i + parca_boyutu]


# ================= MAIN =================

if __name__ == '__main__':
    print("Program V5.0 Başlıyor (Etiketleme + Telegram)...")

    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("✓ Telegram bot token mevcut")
    else:
        print("ℹ️  Telegram bot token yok, bildirimler atlanacak")

    service, sheet = giris_yap_ve_sheets()
    hedef_mail = os.environ["HEDEF_MAIL"]

    if not service:
        telegram_gonder("⛔ Gmail bağlantısı kurulamadı.", acil=True)
        exit(1)

    # 1. Etiketleri hazırla
    print("\n=== Etiket Sistemi Hazırlanıyor ===")
    etiketleri_olustur_veya_getir(service)

    # 2. Mailleri çek (ayrı listeler: analiz edilecek + banka/yasaklı)
    print("\n=== Mailler Çekiliyor ===")
    analiz_mailleri, banka_mailleri = mailleri_getir(service)

    # 3. Banka maillerini hemen etiketle (AI'ya gönderilmiyor)
    print(f"\n=== {len(banka_mailleri)} Banka/Yasaklı Mail Etiketleniyor ===")
    for m in banka_mailleri:
        maile_etiket_ata(service, m['message_id'], ["ELE_BANKA"])

    # 4. Analiz edilecek mail yoksa erken çık
    if len(analiz_mailleri) == 0:
        print("AI'ya gidecek mail yok.")
        telegram_gonder(
            f"ℹ️ Bugün analiz edilecek mail yok.\n"
            f"({len(banka_mailleri)} banka/yasaklı mail etiketlendi.)",
            acil=False
        )
        exit(0)

    # 5. Paketlere böl
    paketler = list(listeyi_bol(analiz_mailleri, HER_MESAJDAKI_MAIL_SAYISI))
    toplam_paket = len(paketler)
    toplam_mail = len(analiz_mailleri)

    # Başlangıç bildirimi
    telegram_gonder(
        f"🚀 <b>Mail Asistanı Başladı (V5.0)</b>\n\n"
        f"📧 AI'ya gidecek: {toplam_mail} mail / {toplam_paket} paket\n"
        f"⊘ Banka/yasaklı: {len(banka_mailleri)} mail (etiketlendi)\n"
        f"⏱ Tahmini süre: ~{toplam_paket * BEKLEME_SURESI_SANIYE // 60} dk",
        acil=False
    )

    # 6. Paketleri sırayla işle ve sonuçları biriktir
    tum_analiz_kararlari = []      # Analiz edilenler (HTML için)
    tum_elenen_kararlari = []      # AI tarafından elenenler (HTML için)
    basarisiz_paket = 0
    sheets_satirlari = []

    for index, paket in enumerate(paketler, 1):
        print(f"\n=== Paket {index}/{toplam_paket} ===")
        kararlar = ai_ile_analiz_et(paket)

        if kararlar is None:
            # Paket çöktü, tüm mailleri hata olarak etiketle
            basarisiz_paket += 1
            for m in paket:
                maile_etiket_ata(service, m['message_id'], ["HATA"])
            continue

        # Her karar için işlem
        for karar in kararlar:
            try:
                mail_no = karar.get('mail_no', 0)
                # mail_no 1-indexed, paket içindeki index'i bul
                if not (1 <= mail_no <= len(paket)):
                    print(f"⚠️ Geçersiz mail_no: {mail_no}")
                    continue

                ilgili_mail = paket[mail_no - 1]
                msg_id = ilgili_mail['message_id']
                konu_kat = karar.get('konu_kategorisi', '07-Genel-Piyasa')
                aksiyon = karar.get('aksiyon', 'ele')

                # Konu kategorisi geçerli mi?
                if konu_kat not in KONU_ETIKETLERI:
                    konu_kat = '07-Genel-Piyasa'

                if aksiyon == 'analiz':
                    # Analiz edildi → konu kategorisi + ANALIZ + (varsa) YUKSEK_ONEM
                    onem = karar.get('onem_skoru', 0)
                    etiketler = [konu_kat, "ANALIZ"]
                    if isinstance(onem, (int, float)) and onem >= ACIL_UYARI_ESIK:
                        etiketler.append("YUKSEK_ONEM")
                    maile_etiket_ata(service, msg_id, etiketler)
                    tum_analiz_kararlari.append(karar)

                    # Sheets satırı
                    sheets_satirlari.append([
                        datetime.now().strftime('%Y-%m-%d'),
                        karar.get('ticker', ''),
                        karar.get('sirket', ''),
                        konu_kat,
                        karar.get('konu', ''),
                        karar.get('etki', ''),
                        onem
                    ])

                else:
                    # Elendi → konu kategorisi + ele sebebi
                    sebep_raw = karar.get('ele_sebebi', 'ELE_DUSUK')
                    if sebep_raw not in AKSIYON_ETIKETLERI:
                        sebep_raw = 'ELE_DUSUK'
                    maile_etiket_ata(service, msg_id, [konu_kat, sebep_raw])
                    tum_elenen_kararlari.append(karar)

            except Exception as e:
                print(f"Karar işleme hatası: {e}")
                continue

        print(f"Paket {index} bitti. Analiz: {len([k for k in kararlar if k.get('aksiyon') == 'analiz'])}, "
              f"Elenen: {len([k for k in kararlar if k.get('aksiyon') == 'ele'])}")

        if index < toplam_paket:
            print(f"Mola: {BEKLEME_SURESI_SANIYE}sn")
            time.sleep(BEKLEME_SURESI_SANIYE)

    # 7. Sheets'e tek seferde yaz
    if sheet and sheets_satirlari:
        try:
            sheet.append_rows(sheets_satirlari)
            print(f"\n✓ Sheets: {len(sheets_satirlari)} satır eklendi")
        except Exception as e:
            print(f"Sheets kayıt hatası: {e}")

    # 8. Grafikleri çiz (sadece yüksek önemli olanlar için - hız + temizlik)
    print("\n=== Grafikler Çiziliyor ===")
    grafikler = []
    yuksek_onem_kararlari = [k for k in tum_analiz_kararlari
                              if isinstance(k.get('onem_skoru'), (int, float))
                              and k['onem_skoru'] >= ACIL_UYARI_ESIK]
    for karar in yuksek_onem_kararlari:
        ticker = karar.get('ticker', '')
        if ticker and "$" in ticker:
            buf = grafik_ciz(ticker)
            if buf:
                grafikler.append((ticker, buf))
                karar['_grafik_var'] = True

    grafikli_tickerlar = {t for t, _ in grafikler}

    # 9. 3 BÖLÜMLÜ MAİL RAPORU
    print("\n=== Mail Raporu Hazırlanıyor ===")

    bugun = datetime.now().strftime('%Y-%m-%d')
    yuksek_sayi = len(yuksek_onem_kararlari)
    analiz_sayi = len(tum_analiz_kararlari)
    elenen_sayi = len(tum_elenen_kararlari)
    banka_sayi = len(banka_mailleri)

    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 900px;">
        <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:8px;">
            📊 Berkonomi Mail Raporu - {bugun}
        </h2>
        <div style="background:#ecf0f1;padding:12px;border-radius:6px;margin-bottom:20px;">
            <strong>Özet:</strong>
            ⭐ {yuksek_sayi} yüksek önemli &nbsp;|&nbsp;
            ✓ {analiz_sayi} analiz &nbsp;|&nbsp;
            ⊘ {elenen_sayi} elenen &nbsp;|&nbsp;
            🏦 {banka_sayi} banka/yasaklı &nbsp;|&nbsp;
            {('❌ ' + str(basarisiz_paket) + ' paket başarısız') if basarisiz_paket else '✓ Tüm paketler başarılı'}
        </div>
    """

    # BÖLÜM 1: Yüksek önemli (skor ≥ 7)
    if yuksek_onem_kararlari:
        html += '<h2 style="color:#c0392b;">⭐ Bölüm 1: Yüksek Önemli Haberler</h2>'
        for karar in sorted(yuksek_onem_kararlari, key=lambda x: x.get('onem_skoru', 0), reverse=True):
            ticker = karar.get('ticker', '')
            html += html_analiz_karti(karar, ticker in grafikli_tickerlar)

    # BÖLÜM 2: Standart analizler — kategoriye göre gruplanmış
    standart_analizler = [k for k in tum_analiz_kararlari
                           if not (isinstance(k.get('onem_skoru'), (int, float))
                                   and k['onem_skoru'] >= ACIL_UYARI_ESIK)]
    if standart_analizler:
        html += '<hr><h2 style="color:#2980b9;">📊 Bölüm 2: Standart Analizler</h2>'

        # Kategoriye göre grupla
        kategoriler = {}
        for karar in standart_analizler:
            kat = karar.get('konu_kategorisi', '07-Genel-Piyasa')
            kategoriler.setdefault(kat, []).append(karar)

        for kat in sorted(kategoriler.keys()):
            html += f'<h3 style="color:#34495e;margin-top:20px;">📂 {kat}</h3>'
            for karar in kategoriler[kat]:
                html += html_analiz_karti(karar, False)

    # BÖLÜM 3: Elenen mailler özeti
    if tum_elenen_kararlari or banka_mailleri:
        html += '<hr><h2 style="color:#7f8c8d;">⊘ Bölüm 3: Elenen Mailler Özeti</h2>'
        html += '<p style="font-size:12px;color:#7f8c8d;">Aşağıdaki mailler AI tarafından elendi. Gmail\'de ilgili etikete bakıp yanlış elenenleri kontrol edebilirsin.</p>'
        html += '''
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead>
              <tr style="background:#ecf0f1;">
                <th style="padding:8px;text-align:left;">Konu</th>
                <th style="padding:8px;text-align:left;">Kategori/Gönderen</th>
                <th style="padding:8px;text-align:left;">Sebep</th>
                <th style="padding:8px;text-align:left;">Açıklama</th>
              </tr>
            </thead>
            <tbody>
        '''
        # Önce AI elenenler
        for karar in tum_elenen_kararlari:
            html += html_elenen_satiri(karar)
        # Sonra banka mailleri
        for m in banka_mailleri:
            html += html_elenen_banka_satiri(m)
        html += '</tbody></table>'

    html += '</div>'

    # Mail gönder
    baslik = f"📊 Berkonomi {bugun} - {analiz_sayi} analiz, {yuksek_sayi} yüksek önem"
    mail_gonder_resimli(service, hedef_mail, baslik, html, grafikler)

    # 10. Telegram özeti
    telegram_gonder(
        f"✅ <b>Analiz Bitti</b>\n\n"
        f"⭐ Yüksek önemli: {yuksek_sayi}\n"
        f"✓ Toplam analiz: {analiz_sayi}\n"
        f"⊘ Elenen: {elenen_sayi}\n"
        f"🏦 Banka/yasaklı: {banka_sayi}\n"
        f"❌ Başarısız paket: {basarisiz_paket}\n\n"
        f"Detaylı rapor mail kutunda.",
        acil=False
    )

    # 11. Yüksek önemli haberleri Telegram'a tek tek bildir
    for karar in yuksek_onem_kararlari:
        ticker = karar.get('ticker', '')
        sirket = karar.get('sirket', '')
        konu = karar.get('konu', '')
        skor = karar.get('onem_skoru', 0)
        etki = karar.get('etki', '')
        etki_emoji = "🟢" if etki.lower() == "pozitif" else ("🔴" if etki.lower() == "negatif" else "⚪")

        telegram_gonder(
            f"⭐ <b>YÜKSEK ÖNEM ({skor}/10)</b>\n\n"
            f"{etki_emoji} <b>{ticker}</b> — {sirket}\n"
            f"📰 {konu}\n\n"
            f"📊 Detaylar mailde.",
            acil=True
        )
        time.sleep(0.5)

    print("\n✓ V5.0 Bitti.")
ILK_BEKLEME = 20  # exponential backoff başlangıcı

# Önem skoru ≥ bu değer → 🚨 ACIL uyarı olarak Telegram'a gönder
ACIL_UYARI_ESIK = 7

# Filtreler
YASAKLI_KELIMELER = [
    "Yapı Kredi", "Garanti", "İş Bankası", "Akbank", "Midas", "Google Flights",
    "Unsubscribe", "Üyelikten ayrıl", "View in browser", "Tarayıcıda görüntüle"
]
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

# Gmail sorgusu: promotions/social/spam dışla → kalite ↑
GMAIL_QUERY = 'newer_than:1d -category:promotions -category:social -in:spam'

# --- DETAYLI PROMPT (AI'ya verilen emirler) ---
PROMPT_KURALLARI = """
ROL:
Sen bir "Kıdemli Fon Yöneticisi"sin. 15+ yıl ABD borsalarında deneyimin var.
Berkonomi adlı bir yatırım analizi topluluğuna içerik üretiyorsun.

GÖREV:
Aşağıdaki e-postaları analiz et. Berk'in ilgi alanları:
- AI Altyapı (NBIS, hyperscaler CapEx)
- AI Donanım (HBM, NVDA, MU, SNDK, ALAB, CRDO, MRVL)
- AI Enerji (CEG, TLN, VST, doğal gaz/midstream)
- Savunma sanayi (sensör, RF/EO/IR)
- Uzay sektörü
- Yarı iletken tedarik zinciri (foundry, OSAT, WFE)

FİLTRELER (RAPORA DAHİL ETME):
- Sağlık sektörü (Healthcare, Biotech, Pharma, Drug trials) → YOK SAY
- Sadece reklam, indirim, webinar → YOK SAY
- Anlamsız spam / promosyon → YOK SAY

ÖNCELİK SIRASI:
1. M&A, stratejik ortaklıklar, büyük sözleşmeler
2. Earnings beat/miss + guidance değişiklikleri
3. Sektör trendleri ve makro
4. Tek tek hisse haberleri

KALİTE KURALLARI:
- Yüzeysel özet YAPMA. "Şirket X iyi performans gösterdi" gibi boş cümleler yasak.
- Her analizde RAKAM olmalı (revenue %, EBITDA, margin, vs.)
- Yorumun objektif ve verilere dayalı olsun. Hype dilinden kaçın.
- Türkçe yaz, ticker'ları $ ile yaz ($NBIS, $MU).
- Risk varsa risk de söyle, sadece pozitif yönü değil.

ÇIKTI FORMATI:
Her haber için iki tür çıktı üret:
1. Okunabilir HTML özeti.
2. Veritabanı için JSON formatı.

HER E-POSTA İÇİN ŞABLON:

[HTML KISMI]
<div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 8px; background-color: #fcfcfc;">
    <h3 style="color: #2c3e50; margin-top: 0;">[HABER BAŞLIĞI]</h3>
    <p style="font-size: 12px; color: #7f8c8d;">Kaynak: [Gönderen]</p>
    <p><strong>📝 Özet:</strong> [2-3 cümle, rakamlarla]</p>
    <p><strong>🏢 Sektör & Ticker:</strong> [Sektör] / [$TICKER]</p>
    <div style="background-color: #e8f8f5; padding: 10px; border-radius: 4px; color: #117a65;">
        <strong>💡 Fon Yöneticisi Yorumu:</strong> [Berk'in tezleri ile bağlantısı, bull/bear case]
    </div>
    <p style="font-size: 11px; color: #c0392b;"><strong>⚠️ Risk:</strong> [Bilinen riskler]</p>
</div>

[JSON KISMI]
```json
{
  "ticker": "$AAPL",
  "sirket": "Apple",
  "sektor": "Teknoloji",
  "konu": "Apple Car projesi iptal edildi",
  "etki": "Negatif",
  "onem_skoru": 7
}
```

ÖNEM SKORU:
- 1-3: Düşük (rutin haber)
- 4-6: Orta (sektör için anlamlı)
- 7-10: Yüksek (portföy etkileyebilecek haber)

ÖNEMLİ: Filtreye takılan mailler için HİÇBİR ŞEY YAZMA, atla.
"""


# ================= TELEGRAM =================

def telegram_gonder(mesaj, acil=False, parse_mode="HTML"):
    """
    Telegram'a bildirim gönderir. Token yoksa sessizce atlar.
    Acil mesajlarda bildirim sesi açık, normal mesajlarda sessiz.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        # Token ayarlanmamışsa hiç deneme — opsiyonel feature
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram mesaj limiti: 4096 karakter
    if len(mesaj) > 4000:
        mesaj = mesaj[:3950] + "\n\n... (kesildi)"

    data = {
        "chat_id": chat_id,
        "text": mesaj,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
        "disable_notification": "false" if acil else "true"
    }

    try:
        encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
        return True
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")
        return False


def telegram_paket_ozeti(index, toplam, haberler):
    """
    Bir paketteki haberleri Telegram'a kısa özet olarak gönderir.
    Önem skoru ≥ ACIL_UYARI_ESIK olanlar ayrı 🚨 mesaj olarak gönderilir.
    """
    if not haberler:
        return

    # Önemli haberleri ayır
    acil_haberler = [h for h in haberler if h.get('onem_skoru', 0) >= ACIL_UYARI_ESIK]
    normal_haberler = [h for h in haberler if h.get('onem_skoru', 0) < ACIL_UYARI_ESIK]

    # Acil haberler için tek tek bildirim
    for h in acil_haberler:
        ticker = h.get('ticker', '')
        sirket = h.get('sirket', '')
        konu = h.get('konu', '')
        etki = h.get('etki', '')
        skor = h.get('onem_skoru', 0)

        etki_emoji = "🟢" if etki.lower() == "pozitif" else ("🔴" if etki.lower() == "negatif" else "⚪")

        mesaj = (
            f"🚨 <b>YÜKSEK ÖNEM ({skor}/10)</b>\n\n"
            f"{etki_emoji} <b>{ticker}</b> — {sirket}\n"
            f"📰 {konu}\n\n"
            f"📊 Detaylar mailde."
        )
        telegram_gonder(mesaj, acil=True)
        time.sleep(0.5)  # rate limit önlemi

    # Normal haberleri tek mesajda topla
    if normal_haberler:
        satirlar = [f"📦 <b>Paket {index}/{toplam}</b> — {len(normal_haberler)} haber\n"]
        for h in normal_haberler:
            ticker = h.get('ticker', '—')
            konu = h.get('konu', '')[:80]
            skor = h.get('onem_skoru', 0)
            satirlar.append(f"• {ticker} ({skor}/10): {konu}")

        mesaj = "\n".join(satirlar)
        telegram_gonder(mesaj, acil=False)


# ================= GMAIL & SHEETS =================

def giris_yap_ve_sheets():
    try:
        info = {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        SCOPES = [
            'https://www.googleapis.com/auth/gmail.modify',
            'https://www.googleapis.com/auth/spreadsheets'
        ]
        creds = Credentials.from_authorized_user_info(info, SCOPES)

        gmail_service = build('gmail', 'v1', credentials=creds)

        sheet = None
        try:
            if "SHEET_ID" in os.environ:
                gc = gspread.authorize(creds)
                sheet = gc.open_by_key(os.environ["SHEET_ID"]).sheet1
        except Exception as e:
            print(f"Sheets bağlantı hatası: {e}")

        return gmail_service, sheet
    except Exception as e:
        print(f"Genel Giriş Hatası: {e}")
        return None, None


def html_temizle(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for script in soup(["script", "style", "head", "title", "meta", "footer"]):
            script.extract()
        text = soup.get_text(separator=' ')
        lines = (line.strip() for line in text.splitlines())
        text = '\n'.join(chunk for chunk in lines if chunk)
        return text[:15000]
    except Exception:
        return html_content[:5000]


def filtre_kontrol(gonderen):
    if HARIC_TUTULACAK_MAIL in gonderen:
        return False
    for kelime in YASAKLI_KELIMELER:
        if kelime.lower() in gonderen.lower():
            return False
    return True


def mailleri_getir(service):
    print(f"Mail sorgusu: {GMAIL_QUERY}")
    results = service.users().messages().list(userId='me', q=GMAIL_QUERY, maxResults=150).execute()
    messages = results.get('messages', [])

    mail_listesi = []

    if not messages:
        return []
    print(f"Bulunan ham mail sayısı: {len(messages)}")

    for msg in messages:
        try:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            headers = txt['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "Konu Yok")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Bilinmiyor")

            if not filtre_kontrol(sender):
                continue

            body = ""
            if 'parts' in txt['payload']:
                for part in txt['payload']['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data')
                        if data:
                            body = base64.urlsafe_b64decode(data).decode(errors='ignore')
                    elif part['mimeType'] == 'text/html':
                        data = part['body'].get('data')
                        if data:
                            html_raw = base64.urlsafe_b64decode(data).decode(errors='ignore')
                            body = html_temizle(html_raw)
            elif 'body' in txt['payload']:
                data = txt['payload']['body'].get('data')
                if data:
                    body = base64.urlsafe_b64decode(data).decode(errors='ignore')

            if not body:
                body = txt.get('snippet', '')
            if "<" in body and ">" in body:
                temiz_body = html_temizle(body)
            else:
                temiz_body = body

            final_text = temiz_body.replace("\r", "").replace("\n", " ")[:15000]
            mail_listesi.append(f"GÖNDEREN: {sender}\nKONU: {subject}\nİÇERİK: {final_text}")
        except Exception as e:
            print(f"Mail okuma hatası (atlanıyor): {e}")
            continue

    print(f"Filtre sonrası mail sayısı: {len(mail_listesi)}")
    return mail_listesi


# ================= AI ANALİZ =================

def _gemini_istek_yap(model_adi, prompt, api_key, timeout=120):
    """
    Tek bir Gemini API çağrısı yapar. Hata fırlatır, retry burada YOK.
    """
    clean_name = model_adi.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.95,
            "topK": 40,
            "maxOutputTokens": 8192,
            "responseMimeType": "text/plain"
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode('utf-8')
        parsed = json.loads(raw)

    if 'candidates' not in parsed or not parsed['candidates']:
        finish = parsed.get('promptFeedback', {}).get('blockReason', 'BILINMEYEN')
        raise ValueError(f"AI yanıt vermedi (blockReason={finish})")

    cand = parsed['candidates'][0]
    if 'content' not in cand or 'parts' not in cand['content']:
        finish = cand.get('finishReason', 'BILINMEYEN')
        raise ValueError(f"AI içerik üretmedi (finishReason={finish})")

    return cand['content']['parts'][0]['text']


def ai_ile_analiz_et(mail_chunk):
    """
    Gemini ile analiz. Exponential backoff + jitter + yedek model fallback.
    """
    prompt = f"{PROMPT_KURALLARI}\n\n=== E-POSTALAR ===\n" + "\n---\n".join(mail_chunk)
    api_key = os.environ["GEMINI_API_KEY"]

    aktif_model = SECILEN_MODEL
    son_hata = None
    telegram_uyari_gonderildi = False

    for deneme in range(MAX_RETRY):
        try:
            return _gemini_istek_yap(aktif_model, prompt, api_key)

        except urllib.error.HTTPError as e:
            son_hata = e
            if e.code in (429, 500, 502, 503, 504):
                bekleme = ILK_BEKLEME * (2 ** deneme) + random.uniform(0, 5)
                print(f"⚠️  HTTP {e.code} ({aktif_model}). "
                      f"{bekleme:.0f}sn bekleniyor... (Deneme {deneme+1}/{MAX_RETRY})")

                # 2. denemede hâlâ başarısızsa Telegram'a haber ver
                if deneme == 2 and not telegram_uyari_gonderildi:
                    telegram_gonder(
                        f"⚠️ <b>API Problemi</b>\n\n"
                        f"Gemini sürekli HTTP {e.code} dönüyor. "
                        f"Yedek modele geçiliyor...",
                        acil=True
                    )
                    telegram_uyari_gonderildi = True

                # 3. denemede hâlâ başarısızsa yedek modele geç
                if deneme == 2 and aktif_model == SECILEN_MODEL:
                    aktif_model = YEDEK_MODEL
                    print(f"🔄 Yedek modele geçiliyor: {aktif_model}")

                time.sleep(bekleme)
                continue

            elif e.code == 404:
                if aktif_model == SECILEN_MODEL:
                    aktif_model = YEDEK_MODEL
                    print(f"❌ Model bulunamadı, yedek deneniyor: {aktif_model}")
                    continue
                return f"<p style='color:red'>HATA: Hiçbir model bulunamadı.</p>"

            else:
                try:
                    err_body = e.read().decode('utf-8', errors='ignore')[:500]
                except Exception:
                    err_body = ""
                return f"<p style='color:red'>AI Analiz Hatası (HTTP {e.code}): {e.reason}<br><small>{err_body}</small></p>"

        except urllib.error.URLError as e:
            son_hata = e
            bekleme = ILK_BEKLEME * (2 ** deneme) + random.uniform(0, 5)
            print(f"⚠️  Ağ hatası: {e}. {bekleme:.0f}sn bekleniyor... (Deneme {deneme+1}/{MAX_RETRY})")
            time.sleep(bekleme)
            continue

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            son_hata = e
            bekleme = ILK_BEKLEME * (2 ** deneme)
            print(f"⚠️  Yanıt parse hatası: {e}. {bekleme:.0f}sn bekleniyor... (Deneme {deneme+1}/{MAX_RETRY})")
            time.sleep(bekleme)
            continue

        except Exception as e:
            son_hata = e
            print(f"⚠️  Beklenmeyen hata: {type(e).__name__}: {e}")
            time.sleep(ILK_BEKLEME)
            continue

    # Tüm denemeler başarısız → Telegram'a final uyarı
    telegram_gonder(
        f"⛔ <b>Analiz Başarısız</b>\n\n"
        f"{MAX_RETRY} deneme sonrası bir paket işlenemedi.\n"
        f"Son hata: <code>{son_hata}</code>",
        acil=True
    )
    return (f"<p style='color:red'>"
            f"⛔ Analiz {MAX_RETRY} denemede de başarısız oldu. Son hata: {son_hata}"
            f"</p>")


# ================= GRAFİK & MAIL =================

def grafik_ciz(ticker):
    try:
        clean_ticker = ticker.replace("$", "").strip()
        stock = yf.Ticker(clean_ticker)
        hist = stock.history(period="1mo")
        if hist.empty:
            return None
        plt.figure(figsize=(10, 4))
        plt.plot(hist.index, hist['Close'], label=f'{clean_ticker}', color='#2980b9', linewidth=2)
        plt.title(f'{clean_ticker} - Son 30 Günlük Trend', fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.legend()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close()
        return buf
    except Exception:
        return None


def mail_gonder_resimli(service, kime, konu, html_content, grafik_buffers):
    try:
        msg = MIMEMultipart()
        msg['To'] = kime
        msg['From'] = "me"
        msg['Subject'] = konu

        if grafik_buffers:
            html_content += "<hr><h3>📈 Hisse Grafikleri (Son 1 Ay)</h3>"
            for i, (ticker, buf) in enumerate(grafik_buffers):
                cid = f"chart_{i}"
                html_content += (f"<p><b>{ticker}</b></p>"
                                 f"<img src='cid:{cid}' style='width:100%; max-width:800px; "
                                 f"border:1px solid #ddd;'><br>")
                img = MIMEImage(buf.read())
                img.add_header('Content-ID', f'<{cid}>')
                msg.attach(img)

        msg.attach(MIMEText(html_content, 'html'))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Mail gönderildi: {konu}")
    except Exception as e:
        print(f"Mail hatası: {e}")


def listeyi_bol(liste, parca_boyutu):
    for i in range(0, len(liste), parca_boyutu):
        yield liste[i:i + parca_boyutu]


# ================= MAIN =================

if __name__ == '__main__':
    print("Program V4.1 Başlıyor (Telegram entegre)...")

    # Telegram bağlantısı test
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("✓ Telegram bot token mevcut")
    else:
        print("ℹ️  Telegram bot token yok, bildirimler atlanacak")

    service, sheet = giris_yap_ve_sheets()
    hedef_mail = os.environ["HEDEF_MAIL"]

    if not service:
        telegram_gonder("⛔ <b>Mail Asistanı çöktü</b>\nGmail bağlantısı kurulamadı.", acil=True)
        exit(1)

    tum_mailler = mailleri_getir(service)

    if len(tum_mailler) == 0:
        print("Mail bulunamadı.")
        telegram_gonder("ℹ️ Bugün analiz edilecek mail bulunamadı.", acil=False)
        exit(0)

    paketler = list(listeyi_bol(tum_mailler, HER_MESAJDAKI_MAIL_SAYISI))
    toplam_paket = len(paketler)

    # Başlangıç mesajı (mail + telegram)
    bilgi = (f"<h3>🚀 Analiz Başladı (V4.1)</h3>"
             f"Toplanan Mail: <b>{len(tum_mailler)}</b><br>"
             f"Paket Sayısı: <b>{toplam_paket}</b><br>"
             f"Model: <b>{SECILEN_MODEL}</b> (yedek: {YEDEK_MODEL})")
    mail_gonder_resimli(service, hedef_mail, "Analiz Başlatılıyor...", bilgi, [])

    telegram_gonder(
        f"🚀 <b>Mail Asistanı Başladı</b>\n\n"
        f"📧 {len(tum_mailler)} mail / {toplam_paket} paket\n"
        f"⏱ Tahmini süre: ~{toplam_paket * BEKLEME_SURESI_SANIYE // 60} dk\n\n"
        f"Önemli haberleri ({ACIL_UYARI_ESIK}/10+) anlık bildirimle alacaksın.",
        acil=False
    )

    basarisiz_paket = 0
    toplam_acil_haber = 0
    toplam_haber = 0

    for index, paket in enumerate(paketler, 1):
        print(f"\n=== Paket {index}/{toplam_paket} işleniyor ===")

        ai_output = ai_ile_analiz_et(paket)

        if "Analiz" in ai_output and "başarısız" in ai_output:
            basarisiz_paket += 1

        veriler_sheets = []
        grafikler = []
        haberler_paket = []  # Telegram özet için

        # Regex ile JSON ayıkla
        json_matches = re.findall(r'```json\s*(\{.*?\})\s*```', ai_output, re.DOTALL)
        for m in json_matches:
            try:
                data = json.loads(m)
                ticker = data.get('ticker', '')
                onem = data.get('onem_skoru', 0)

                # Sheets satırı
                veriler_sheets.append([
                    datetime.now().strftime('%Y-%m-%d'),
                    ticker,
                    data.get('sirket', ''),
                    data.get('sektor', ''),
                    data.get('konu', ''),
                    data.get('etki', ''),
                    onem
                ])

                # Telegram için topla
                haberler_paket.append(data)
                toplam_haber += 1
                if isinstance(onem, (int, float)) and onem >= ACIL_UYARI_ESIK:
                    toplam_acil_haber += 1

                # Grafik
                if ticker and "$" in ticker:
                    buf = grafik_ciz(ticker)
                    if buf:
                        grafikler.append((ticker, buf))
            except Exception as e:
                print(f"JSON parse hatası: {e}")
                continue

        # Sheets Kaydı
        if sheet and veriler_sheets:
            try:
                sheet.append_rows(veriler_sheets)
                print(f"Sheets: {len(veriler_sheets)} satır eklendi")
            except Exception as e:
                print(f"Sheets kayıt hatası: {e}")

        # Mail Gönderimi
        temiz_html = re.sub(r'```json.*?```', '', ai_output, flags=re.DOTALL)
        baslik = f"📊 Rapor {index}/{toplam_paket} - Grafikli Analiz"
        mail_gonder_resimli(service, hedef_mail, baslik, temiz_html, grafikler)

        # Telegram özet (acil olanlar ayrı, normaller toplu)
        telegram_paket_ozeti(index, toplam_paket, haberler_paket)

        if index < len(paketler):
            print(f"Mola: {BEKLEME_SURESI_SANIYE}sn")
            time.sleep(BEKLEME_SURESI_SANIYE)

    # ===== FİNAL ÖZET =====
    basari_orani = ((toplam_paket - basarisiz_paket) / toplam_paket * 100) if toplam_paket else 0

    # Mail özeti
    ozet_html = (f"<h3>✅ Analiz Bitti</h3>"
                 f"Toplam paket: {toplam_paket}<br>"
                 f"Başarısız: {basarisiz_paket}<br>"
                 f"Başarı oranı: {basari_orani:.0f}%<br>"
                 f"Toplam haber: {toplam_haber}<br>"
                 f"🚨 Yüksek önemli: {toplam_acil_haber}")
    mail_gonder_resimli(service, hedef_mail, "Analiz Tamamlandı ✓", ozet_html, [])

    # Telegram özeti
    telegram_gonder(
        f"✅ <b>Analiz Bitti</b>\n\n"
        f"📦 Paket: {toplam_paket} (başarı: {basari_orani:.0f}%)\n"
        f"📰 Toplam haber: {toplam_haber}\n"
        f"🚨 Yüksek önemli: {toplam_acil_haber}\n\n"
        f"Detaylar mail kutunda.",
        acil=False
    )

    print("\n✓ Bitti.")

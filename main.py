import os
import base64
import json
import urllib.request
import urllib.error
import time
import re
import io
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

# Model Seçimi (2.5 Flash, 30sn bekleme ile limit yemez)
SECILEN_MODEL = "models/gemini-2.5-flash"

# Limit & Bekleme
HER_MESAJDAKI_MAIL_SAYISI = 8  # Grafik ve Sheet işlemi olduğu için az ve öz gönderiyoruz
BEKLEME_SURESI_SANIYE = 35     # Her işlem arası mola

# Filtreler
YASAKLI_KELIMELER = [
    "Yapı Kredi", "Garanti", "İş Bankası", "Akbank", "Midas", "Google Flights", 
    "Unsubscribe", "Üyelikten ayrıl", "View in browser"
]
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

# --- DETAYLI PROMPT (AI'ya verilen emirler) ---
PROMPT_KURALLARI = """
GÖREV:
Aşağıdaki e-postaları "Kıdemli Fon Yöneticisi" gözüyle analiz et.

FİLTRELER (BUNLARI RAPORA VE VERİTABANINA DAHİL ETME):
- Sağlık sektörü (Healthcare, Biotech, Pharma, Drug trials) haberlerini YOK SAY.
- Sadece "reklam", "indirim", "webinar" içeren mailleri YOK SAY.

ÖNCELİK:
- Teknoloji (AI, Yarı İletkenler), Enerji (Yenilenebilir, Petrol), Savunma, Finans.
- Şirketlerin yeni yatırımları, birleşmeleri (M&A) ve stratejik ortaklıkları.

ÇIKTI FORMATI:
Her haber için iki tür çıktı üret:
1. Okunabilir HTML özeti.
2. Veritabanı için JSON formatı.

HER E-POSTA İÇİN ŞABLON:

[HTML KISMI]
<div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 8px; background-color: #fcfcfc;">
    <h3 style="color: #2c3e50; margin-top: 0;">[HABER BAŞLIĞI]</h3>
    <p style="font-size: 12px; color: #7f8c8d;">Kaynak: [Gönderen]</p>
    <p><strong>Özet:</strong> ...</p>
    <p><strong>Sektör & Ticker:</strong> ...</p>
    <div style="background-color: #e8f8f5; padding: 10px; border-radius: 4px; color: #117a65;">
        <strong>💡 Yorum:</strong> ...
    </div>
</div>

[JSON KISMI]
```json
{
  "ticker": "$AAPL",
  "sirket": "Apple",
  "sektor": "Teknoloji",
  "konu": "Apple Car projesi iptal edildi",
  "etki": "Negatif"
}
```
"""


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
        
        # Gmail servisi
        gmail_service = build('gmail', 'v1', credentials=creds)
        
        # Sheets servisi
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
        for script in soup(["script", "style", "head", "title", "meta"]):
            script.extract()
        text = soup.get_text(separator=' ')
        lines = (line.strip() for line in text.splitlines())
        text = '\n'.join(chunk for chunk in lines if chunk)
        return text[:15000]
    except:
        return html_content[:5000]


def filtre_kontrol(gonderen):
    if HARIC_TUTULACAK_MAIL in gonderen:
        return False
    for kelime in YASAKLI_KELIMELER:
        if kelime.lower() in gonderen.lower():
            return False
    return True


def mailleri_getir(service):
    print("Son 24 saatlik mailler taranıyor...")
    # 150 maile kadar kontrol eder
    results = service.users().messages().list(userId='me', q='newer_than:1d', maxResults=150).execute()
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
                            body = base64.urlsafe_b64decode(data).decode()
                    elif part['mimeType'] == 'text/html':
                        data = part['body'].get('data')
                        if data:
                            html_raw = base64.urlsafe_b64decode(data).decode()
                            body = html_temizle(html_raw)
            elif 'body' in txt['payload']:
                data = txt['payload']['body'].get('data')
                if data:
                    body = base64.urlsafe_b64decode(data).decode()
            
            if not body:
                body = txt.get('snippet', '')
            if "<" in body and ">" in body:
                temiz_body = html_temizle(body)
            else:
                temiz_body = body
            
            final_text = temiz_body.replace("\r", "").replace("\n", " ")[:15000]
            mail_listesi.append(f"GÖNDEREN: {sender}\nKONU: {subject}\nİÇERİK: {final_text}")
        except:
            continue
            
    return mail_listesi


def ai_ile_analiz_et(mail_chunk):
    prompt = f"{PROMPT_KURALLARI}\n\n=== E-POSTALAR ===\n" + "\n---\n".join(mail_chunk)

    api_key = os.environ["GEMINI_API_KEY"]
    clean_name = SECILEN_MODEL.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    for _ in range(3):
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode('utf-8'))['candidates'][0]['content']['parts'][0]['text']
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(40)
            else:
                return f"HATA: {e}"
        except:
            time.sleep(5)
    return "Analiz Yapılamadı."


def grafik_ciz(ticker):
    try:
        clean_ticker = ticker.replace("$", "").strip()
        stock = yf.Ticker(clean_ticker)
        hist = stock.history(period="1mo")  # Son 1 aylık veri
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
    except:
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
                html_content += f"<p><b>{ticker}</b></p><img src='cid:{cid}' style='width:100%; max-width:800px; border:1px solid #ddd;'><br>"
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


if __name__ == '__main__':
    print("Program V3.0 Başlıyor...")
    service, sheet = giris_yap_ve_sheets()
    hedef_mail = os.environ["HEDEF_MAIL"]
    
    if service:
        tum_mailler = mailleri_getir(service)
        if len(tum_mailler) > 0:
            paketler = list(listeyi_bol(tum_mailler, HER_MESAJDAKI_MAIL_SAYISI))
            toplam_paket = len(paketler)
            
            bilgi = f"<h3>🚀 Analiz Başladı</h3>Toplanan Mail: <b>{len(tum_mailler)}</b><br>Paket Sayısı: <b>{toplam_paket}</b>"
            mail_gonder_resimli(service, hedef_mail, "Analiz Başlatılıyor...", bilgi, [])

            for index, paket in enumerate(paketler, 1):
                print(f"Paket {index}/{toplam_paket} işleniyor...")
                
                ai_output = ai_ile_analiz_et(paket)
                
                veriler_sheets = []
                grafikler = []
                
                # Regex ile JSON ayıkla
                json_matches = re.findall(r'```json\s*(\{.*?\})\s*```', ai_output, re.DOTALL)
                for m in json_matches:
                    try:
                        data = json.loads(m)
                        ticker = data.get('ticker', '')
                        veriler_sheets.append([
                            datetime.now().strftime('%Y-%m-%d'),
                            ticker,
                            data.get('sirket', ''),
                            data.get('sektor', ''),
                            data.get('konu', ''),
                            data.get('etki', '')
                        ])
                        if ticker and "$" in ticker:
                            buf = grafik_ciz(ticker)
                            if buf:
                                grafikler.append((ticker, buf))
                    except:
                        continue

                # Sheets Kaydı
                if sheet and veriler_sheets:
                    try:
                        sheet.append_rows(veriler_sheets)
                    except:
                        pass

                # Mail Gönderimi (JSON'u gizle)
                temiz_html = re.sub(r'```json.*?```', '', ai_output, flags=re.DOTALL)
                baslik = f"📊 Rapor {index}/{toplam_paket} - Grafikli Analiz"
                mail_gonder_resimli(service, hedef_mail, baslik, temiz_html, grafikler)
                
                if index < len(paketler):
                    time.sleep(BEKLEME_SURESI_SANIYE)
            print("Bitti.")
        else:
            print("Mail bulunamadı.")

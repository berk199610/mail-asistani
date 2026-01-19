import os
import base64
import json
import urllib.request
import time
import math
import re
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime

# ================= AYARLAR =================

SECILEN_MODEL = "models/gemini-2.0-flash-exp" 
# Hata alırsan: "models/gemini-1.5-flash" yapabilirsin.

# Limit Ayarları
HER_MESAJDAKI_MAIL_SAYISI = 5
DAKIKALIK_ISTEK_LIMITI = 5
BEKLEME_SURESI_SANIYE = 305

# Filtreler
YASAKLI_KELIMELER = [
    "Yapı Kredi", "Garanti", "İş Bankası", "Akbank", "Midas", "Google Flights", 
    "Unsubscribe", "Üyelikten ayrıl", "View in browser"
]
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

# PROMPT (HTML Çıktı İçin Optimize Edildi)
PROMPT_KURALLARI = """
GÖREV:
Aşağıdaki e-postaları "Kıdemli Finansal Analist" gözüyle incele.

ÖNCELİK:
- ABD borsaları, makroekonomi, teknoloji hisseleri, kripto paralar.
- Substack ve Seeking Alpha kaynaklı önemli analizler.

FORMAT KURALLARI:
1. Çıktı HTML formatında olmalı ama <html> tagleri kullanma, sadece <div> ve <p> vb. kullan.
2. Her analiz bir "kart" gibi görünmeli.
3. Ticker sembollerini mutlaka $ sembolü ile yaz (Örn: $NVDA, $SPY, $BTC).

HER E-POSTA İÇİN ŞABLON:
<div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 8px; background-color: #f9f9f9;">
    <h3 style="color: #2c3e50; margin-top: 0;">[E-POSTA KONUSU]</h3>
    <p style="font-size: 12px; color: #7f8c8d;">Kimden: [Gönderen]</p>
    
    <p><strong>📝 Özet:</strong> E-postanın ana mesajını buraya yaz.</p>
    
    <p><strong>📊 Analiz:</strong></p>
    <ul>
        <li><strong>Şirketler:</strong> $AAPL, $TSLA vb.</li>
        <li><strong>Fırsat/Risk:</strong> Buraya maddeler halinde yaz.</li>
    </ul>

    <div style="background-color: #e8f6f3; padding: 10px; border-radius: 5px; color: #138d75;">
        <strong>💡 Yatırımcı Yorumu:</strong> Buraya yorumunu yaz.
    </div>
</div>
"""

# ===========================================

def giris_yap():
    try:
        info = {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        print(f"Giriş hatası: {e}")
        return None

def html_temizle(html_content):
    """HTML içeriğini temizleyip sadece metni alır."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for script in soup(["script", "style", "head", "title", "meta", "footer"]):
            script.extract()
        text = soup.get_text(separator=' ')
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        return text[:10000] 
    except:
        return html_content[:5000]

def filtre_kontrol(gonderen):
    if HARIC_TUTULACAK_MAIL in gonderen: return False
    for kelime in YASAKLI_KELIMELER:
        if kelime.lower() in gonderen.lower(): return False
    return True

def piyasa_verisi_getir(metin):
    """Metin içindeki $TICKER sembollerini bulup fiyatlarını getirir."""
    tickers = set(re.findall(r'\$([A-Z]{2,5})', metin))
    if not tickers:
        return ""
    
    html_output = "<div style='margin-top:20px; padding:10px; border:1px solid #333; background:#fff;'>"
    html_output += "<h4>📈 Piyasa Kontrolü (Canlı)</h4><table style='width:100%; text-align:left; border-collapse: collapse;'>"
    html_output += "<tr style='border-bottom:1px solid #ddd;'><th>Hisse</th><th>Fiyat</th><th>Değişim</th></tr>"
    
    veri_bulundu = False
    for ticker in list(tickers)[:10]: 
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                fiyat = hist['Close'].iloc[-1]
                onceki = hist['Open'].iloc[-1]
                degisim = ((fiyat - onceki) / onceki) * 100
                renk = "green" if degisim >= 0 else "red"
                
                html_output += f"<tr><td><b>{ticker}</b></td><td>${fiyat:.2f}</td><td style='color:{renk};'>%{degisim:.2f}</td></tr>"
                veri_bulundu = True
        except:
            continue
            
    html_output += "</table></div>"
    return html_output if veri_bulundu else ""

def mail_gonder(service, kime, konu, icerik_html, ek_dosya_yolu=None):
    try:
        msg = MIMEMultipart()
        msg['To'] = kime
        msg['From'] = "me"
        msg['Subject'] = konu

        msg.attach(MIMEText(icerik_html, 'html'))

        if ek_dosya_yolu and os.path.exists(ek_dosya_yolu):
            part = MIMEBase('application', "octet-stream")
            with open(ek_dosya_yolu, 'rb') as file:
                part.set_payload(file.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(ek_dosya_yolu)}"')
            msg.attach(part)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Mail gönderildi: {konu}")
    except Exception as e:
        print(f"Mail gönderme hatası: {e}")

def mailleri_getir(service):
    print("Son 24 saatlik mailler taranıyor...")
    results = service.users().messages().list(userId='me', q='newer_than:1d', maxResults=150).execute()
    messages = results.get('messages', [])
    
    mail_listesi = []
    ham_veri = [] 

    if not messages: return [], []

    print(f"Ham mail sayısı: {len(messages)}. Filtreleniyor...")
    
    for msg in messages:
        try:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            headers = txt['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "Konu Yok")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Bilinmiyor")
            
            if not filtre_kontrol(sender): continue

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
            
            if not body: body = txt.get('snippet', '')
            
            if "<" in body and ">" in body: 
                temiz_body = html_temizle(body)
            else:
                temiz_body = body
            
            final_text = temiz_body.replace("\r", "").replace("\n", " ")[:8000]
            
            mail_listesi.append(f"GÖNDEREN: {sender}\nKONU: {subject}\nİÇERİK: {final_text}")
            
            ham_veri.append({
                "Tarih": datetime.now().strftime('%Y-%m-%d'),
                "Gonderen": sender,
                "Konu": subject,
                "Icerik_Ozet": final_text[:200]
            })
            
        except Exception as e:
            print(f"Mail okuma hatası: {e}")
            continue
            
    return mail_listesi, ham_veri

def ai_ile_analiz_et(mail_chunk):
    prompt = f"{PROMPT_KURALLARI}\n\n=== İNCELENECEK E-POSTALAR ===\n" + "\n\n----------------\n\n".join(mail_chunk)
    
    api_key = os.environ["GEMINI_API_KEY"]
    clean_name = SECILEN_MODEL.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    data = { "contents": [{"parts": [{"text": prompt}]}] }
    
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"<p style='color:red'>AI Analiz Hatası: {e}</p>"

def listeyi_bol(liste, parca_boyutu):
    for i in range(0, len(liste), parca_boyutu):
        yield liste[i:i + parca_boyutu]

if __name__ == '__main__':
    print("Program Başlıyor...")
    service = giris_yap()
    hedef_mail = os.environ["HEDEF_MAIL"]
    
    if service:
        tum_mailler, arsiv_verisi = mailleri_getir(service)
        toplam_mail = len(tum_mailler)
        
        if toplam_mail > 0:
            # --- 1. CSV ARŞİV OLUŞTURMA ---
            csv_dosya_adi = f"gunluk_arsiv_{datetime.now().strftime('%Y%m%d')}.csv"
            try:
                df = pd.DataFrame(arsiv_verisi)
                df.to_csv(csv_dosya_adi, index=False)
                print("CSV Arşivi oluşturuldu.")
            except:
                csv_dosya_adi = None

            # --- 2. BİLGİLENDİRME MAİLİ ---
            tarih = datetime.now().strftime('%d.%m.%Y')
            paket_sayisi = math.ceil(toplam_mail / HER_MESAJDAKI_MAIL_SAYISI)
            
            bilgi_mesaji = (
                f"<h3>🚀 Mail Asistanı V2.0 Devrede</h3>"
                f"<p>Bugün analiz edilecek toplam <b>{toplam_mail}</b> adet önemli mail bulundu.</p>"
                f"<p>Bu mailler <b>{paket_sayisi}</b> parça halinde analiz edilip gönderilecek.</p>"
                f"<p><i>Sistem: HTML Temizliği ✅ | Canlı Borsa Verisi ✅ | CSV Arşivi ✅</i></p>"
            )
            mail_gonder(service, hedef_mail, f"Analiz Başlıyor ({tarih})", bilgi_mesaji)
            
            # --- 3. ANALİZ SÜRECİ ---
            mail_paketleri = list(listeyi_bol(tum_mailler, HER_MESAJDAKI_MAIL_SAYISI))
            anlik_istek_sayisi = 0
            
            for index, paket in enumerate(mail_paketleri, 1):
                print(f"Paket {index}/{paket_sayisi} işleniyor...")
                
                # AI Analizi
                analiz_sonucu = ai_ile_analiz_et(paket)
                
                # yfinance ile Piyasa Verisi Ekleme
                piyasa_html = piyasa_verisi_getir(analiz_sonucu)
                
                # Final HTML
                final_icerik = f"""
                <html>
                <body style="font-family: Arial, sans-serif;">
                    <div style="background-color:#2c3e50; color:white; padding:10px; text-align:center;">
                        <h2>Günlük Özet Raporu ({index}/{paket_sayisi})</h2>
                    </div>
                    <div style="padding:20px;">
                        {analiz_sonucu}
                    </div>
                    {piyasa_html}
                    <hr>
                    <p style="font-size:10px; color:#999;">Bu rapor AI tarafından oluşturulmuştur. Yatırım tavsiyesi değildir.</p>
                </body>
                </html>
                """
                
                konu_basligi = f"📊 Günlük Mail Özeti - {index} / {paket_sayisi}"
                
                # Sadece son mailde CSV dosyasını ekle
                ek_dosya = csv_dosya_adi if index == len(mail_paketleri) else None
                
                mail_gonder(service, hedef_mail, konu_basligi, final_icerik, ek_dosya_yolu=ek_dosya)
                
                anlik_istek_sayisi += 1
                
                if index < len(mail_paketleri):
                    if anlik_istek_sayisi >= DAKIKALIK_ISTEK_LIMITI:
                        print(f"Limit doldu. {BEKLEME_SURESI_SANIYE} saniye bekleniyor...")
                        time.sleep(BEKLEME_SURESI_SANIYE)
                        anlik_istek_sayisi = 0
                    else:
                        time.sleep(5) 
            
            print("TÜM İŞLEMLER BİTTİ.")
        else:
            print("Analiz edilecek mail yok.")

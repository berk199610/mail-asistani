import os
import base64
import json
import urllib.request
import time
import math
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime

# ================= AYARLAR =================

SECILEN_MODEL = "models/gemini-3-flash-preview"

# Limit Ayarları
HER_MESAJDAKI_MAIL_SAYISI = 5
DAKIKALIK_ISTEK_LIMITI = 5
BEKLEME_SURESI_SANIYE = 305

# Filtreler
YASAKLI_KELIMELER = [
    "Yapı Kredi", "Garanti", "İş Bankası", "Akbank", "Midas", "Google Flights"
]
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

# YENİ PROMPT (SENİN İSTEDİĞİN SADE FORMAT)
PROMPT_KURALLARI = """
GÖREV:
Aşağıdaki 5 adet e-postayı "Finansal Analist" gözüyle incele.

ÖNCELİK:
- ABD borsaları, finans, makroekonomi, teknoloji ve büyüme şirketleri.
- Substack ve Seeking Alpha kaynaklı mailler.

FORMAT KURALLARI:
1. Çıktı sade ve okunabilir olmalı.
2. Gereksiz süslemeler (#, --- vb.) yapma, sadece kalın yazı (**bold**) kullan.
3. Her e-postayı birbirinden net bir boşlukla ayır.

HER E-POSTA İÇİN ŞU ŞABLONU KULLAN:

[E-POSTA KONUSU]
(Kimden: [Gönderen])

1) Özet:
E-postanın ana mesajını detaylı ama net açıkla.

2) Detaylı Analiz:
- Şirketler & Tickerlar: Bahsedilen şirketleri ve Ticker sembollerini (örn: $SPY, NVDA) kalın yaz.
- Sektörler: Hangi sektörleri etkiliyor?
- Riskler ve Fırsatlar: Maddeler halinde yaz.

3) Yorum:
Yatırımcı açısından ne anlama geliyor? (Uzun Vade / Kısa Vade / Spekülatif).

---
(Diğer e-postaya geç)
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

def filtre_kontrol(gonderen):
    if HARIC_TUTULACAK_MAIL in gonderen: return False
    for kelime in YASAKLI_KELIMELER:
        if kelime.lower() in gonderen.lower(): return False
    return True

def mail_gonder(service, kime, konu, icerik):
    try:
        message = MIMEText(icerik)
        message['to'] = kime
        message['from'] = "me"
        message['subject'] = konu
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Mail gönderildi: {konu}")
    except Exception as e:
        print(f"Mail gönderme hatası: {e}")

def mailleri_getir(service):
    print("Son 24 saatlik mailler taranıyor...")
    results = service.users().messages().list(userId='me', q='newer_than:1d', maxResults=150).execute()
    messages = results.get('messages', [])
    
    mail_listesi = []
    if not messages: return []

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
                            break
            elif 'body' in txt['payload']:
                data = txt['payload']['body'].get('data')
                if data:
                    body = base64.urlsafe_b64decode(data).decode()
            
            if not body: body = txt.get('snippet', '')
            temiz_body = body[:6000].replace("\r", "").replace("\n", " ")
            
            mail_listesi.append(f"GÖNDEREN: {sender}\nKONU: {subject}\nİÇERİK: {temiz_body}")
        except:
            continue
            
    return mail_listesi

def ai_ile_analiz_et(mail_chunk):
    prompt = f"{PROMPT_KURALLARI}\n\n=== İNCELENECEK 5 E-POSTA ===\n" + "\n\n----------------\n\n".join(mail_chunk)
    
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
        return f"HATA OLUŞTU: {e}"

def listeyi_bol(liste, parca_boyutu):
    for i in range(0, len(liste), parca_boyutu):
        yield liste[i:i + parca_boyutu]

if __name__ == '__main__':
    service = giris_yap()
    hedef_mail = os.environ["HEDEF_MAIL"]
    
    if service:
        tum_mailler = mailleri_getir(service)
        toplam_mail = len(tum_mailler)
        
        if toplam_mail > 0:
            # --- 1. BİLGİLENDİRME MAİLİ ---
            tarih = datetime.now().strftime('%d.%m.%Y')
            paket_sayisi = math.ceil(toplam_mail / HER_MESAJDAKI_MAIL_SAYISI)
            
            bilgi_mesaji = (
                f"Merhaba,\n\n"
                f"Bugün analiz edilecek toplam **{toplam_mail}** adet önemli mail bulundu.\n"
                f"Bu mailler **{paket_sayisi}** parça halinde (Rapor 1, Rapor 2...) birazdan gönderilmeye başlanacak.\n\n"
                f"İyi okumalar."
            )
            mail_gonder(service, hedef_mail, f"Analiz Başlıyor ({tarih})", bilgi_mesaji)
            
            # --- 2. ANALİZ SÜRECİ ---
            mail_paketleri = list(listeyi_bol(tum_mailler, HER_MESAJDAKI_MAIL_SAYISI))
            anlik_istek_sayisi = 0
            
            for index, paket in enumerate(mail_paketleri, 1):
                print(f"Paket {index} işleniyor...")
                analiz_sonucu = ai_ile_analiz_et(paket)
                
                # Mail başlığı: Günlük Mail Özeti - 1 / 5
                konu_basligi = f"Günlük Mail Özeti - {index} / {paket_sayisi}"
                mail_gonder(service, hedef_mail, konu_basligi, analiz_sonucu)
                
                anlik_istek_sayisi += 1
                
                if index < len(mail_paketleri):
                    if anlik_istek_sayisi >= DAKIKALIK_ISTEK_LIMITI:
                        print(f"Limit doldu. {BEKLEME_SURESI_SANIYE} saniye bekleniyor...")
                        time.sleep(BEKLEME_SURESI_SANIYE)
                        anlik_istek_sayisi = 0
                    else:
                        time.sleep(2) # Güvenlik payı
            
            print("TÜM İŞLEMLER BİTTİ.")
        else:
            print("Analiz edilecek mail yok.")

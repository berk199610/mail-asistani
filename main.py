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

# Model
SECILEN_MODEL = "models/gemini-3-flash-preview"

# Limit Ayarları
HER_MESAJDAKI_MAIL_SAYISI = 5   # Her raporda kaç mail analiz edilecek
DAKIKALIK_ISTEK_LIMITI = 5      # API'nin dakikalık limiti
BEKLEME_SURESI_SANIYE = 305     # Limit dolunca kaç saniye beklenecek (5 dk + 5 sn güvenlik payı)

# Filtreler (Bunlar AI'ya gönderilmez)
YASAKLI_KELIMELER = [
    "Yapı Kredi", "Garanti", "İş Bankası", "Akbank", "Midas", "Google Flights"
]
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

# Prompt
PROMPT_KURALLARI = """
GÖREV:
Aşağıdaki 5 adet e-postayı "Finansal Analist" gözüyle incele.

ÖNCELİK:
- ABD borsaları, finans, makroekonomi, teknoloji ve büyüme şirketleri.
- Substack ve Seeking Alpha kaynaklı mailler.

FORMAT (HER E-POSTA İÇİN):
---
### [E-POSTA KONUSU]
**(Kimden: [Gönderen])**

**1) Özet:**
Detaylı ama net özet.

**2) Detaylı Analiz:**
Şirketler (Ticker), sektörler, riskler ve fırsatlar. ABD borsası şirketlerini kalın yaz.

**3) Yorum:**
Yatırımcı için ne anlama geliyor? (Uzun/Kısa vade, Spekülatif vb.)

DİL: Tamamen Türkçe, profesyonel finans dili.
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

def mailleri_getir(service):
    print("Son 24 saatlik mailler taranıyor...")
    results = service.users().messages().list(userId='me', q='newer_than:1d', maxResults=150).execute()
    messages = results.get('messages', [])
    
    mail_listesi = []
    
    if not messages: return []

    print(f"Toplam {len(messages)} ham mail bulundu. Filtreleniyor...")
    
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
    """5'li mail grubunu AI'ya sorar"""
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

def raporu_gonder(service, rapor_metni, rapor_numarasi):
    if not rapor_metni: return
    try:
        hedef = os.environ["HEDEF_MAIL"]
        tarih = datetime.now().strftime('%d.%m.%Y')
        konu = f"Günlük Mail Özeti - {rapor_numarasi} ({tarih})"
        
        message = MIMEText(rapor_metni)
        message['to'] = hedef
        message['from'] = "me"
        message['subject'] = konu
        
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Rapor {rapor_numarasi} gönderildi.")
    except Exception as e:
        print(f"Mail gönderme hatası: {e}")

def listeyi_bol(liste, parca_boyutu):
    """Listeyi 5'erli parçalara böler"""
    for i in range(0, len(liste), parca_boyutu):
        yield liste[i:i + parca_boyutu]

if __name__ == '__main__':
    service = giris_yap()
    if service:
        tum_mailler = mailleri_getir(service)
        toplam_mail = len(tum_mailler)
        
        if toplam_mail > 0:
            print(f"Filtreleme sonrası {toplam_mail} mail kaldı. İşlem başlıyor...")
            
            # Listeyi 5'erli paketlere böl
            mail_paketleri = list(listeyi_bol(tum_mailler, HER_MESAJDAKI_MAIL_SAYISI))
            toplam_paket = len(mail_paketleri)
            
            print(f"Toplam {toplam_paket} adet rapor oluşturulacak.")
            
            anlik_istek_sayisi = 0
            
            for index, paket in enumerate(mail_paketleri, 1):
                print(f"--- Paket {index}/{toplam_paket} işleniyor ---")
                
                # 1. Analiz Et
                analiz_sonucu = ai_ile_analiz_et(paket)
                
                # 2. Mail Gönder
                raporu_gonder(service, analiz_sonucu, index)
                
                # 3. Sayaçları Güncelle
                anlik_istek_sayisi += 1
                
                # 4. Limit Kontrolü (Son paket değilse kontrol et)
                if index < toplam_paket:
                    if anlik_istek_sayisi >= DAKIKALIK_ISTEK_LIMITI:
                        print(f"⚠️ Limit (Dakikada 5 istek) doldu. {BEKLEME_SURESI_SANIYE} saniye bekleniyor...")
                        time.sleep(BEKLEME_SURESI_SANIYE)
                        anlik_istek_sayisi = 0 # Sayacı sıfırla ve devam et
                        print("✅ Bekleme bitti, devam ediliyor...")
                    else:
                        # Paketler arası kısa bir nefes alma (2 saniye) - Opsiyonel güvenlik
                        time.sleep(2)
            
            print("TÜM İŞLEMLER TAMAMLANDI.")
        else:
            print("Analiz edilecek mail bulunamadı.")

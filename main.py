import os
import base64
import json
import urllib.request
import time
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime

# ================= AYARLAR =================

# Kullanılacak Model (Sadece 3.0 Pro)
SECILEN_MODEL = "models/gemini-3-pro-preview"

# Filtrelenecek Mail Adresi (Bu adresten gelen mailler rapora dahil edilmez)
HARIC_TUTULACAK_MAIL = "berkucmaz20@gmail.com"

# MAİLLERİ NASIL ÖZETLEYECEĞİNİ BURAYA YAZ (Prompt)
# Burayı dilediğin gibi değiştirebilirsin.
AI_SISTEM_MESAJI = """
Sen benim profesyonel asistanımsın.
Aşağıda son 24 saatte gelen maillerim var.
Bunları analiz et ve bana Türkçe bir rapor hazırla.

İSTEKLERİM:
1. Mailleri önem sırasına göre diz.
2. Eğer "Fatura", "İş Teklifi" veya "Acil" bir durum varsa en başa 🚨 emojisiyle yaz.
3. Gereksiz bültenleri, reklamları "Diğer" başlığı altında tek cümleyle geçiştir.
4. Benim dilimle, samimi ama net konuş.
5. Her mailin kimden geldiğini parantez içinde belirt.
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

def mailleri_getir(service):
    print("Son 24 saatlik mailler taranıyor...")
    # 'newer_than:1d' = Son 24 saat
    results = service.users().messages().list(userId='me', q='newer_than:1d', maxResults=50).execute()
    messages = results.get('messages', [])
    
    mail_icerikleri = []
    
    if not messages:
        return []

    print(f"{len(messages)} adet mail bulundu. Filtreleniyor ve işleniyor...")
    
    for msg in messages:
        try:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            
            headers = txt['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "Konu Yok")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Bilinmiyor")
            
            # FİLTRELEME: Eğer gönderen senin mailinse bu maili atla
            if HARIC_TUTULACAK_MAIL in sender:
                print(f"Atlandı (Kendinden gelen mail): {subject}")
                continue

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
            
            temiz_body = body[:2500].replace("\r", "").replace("\n", " ") # Karakter limiti biraz artırıldı
            mail_icerikleri.append(f"KİMDEN: {sender}\nKONU: {subject}\nİÇERİK: {temiz_body}")
        except:
            continue

    return mail_icerikleri

def ai_ile_yorumla(mail_listesi):
    print(f"Analiz başlıyor. Model: {SECILEN_MODEL}")
    prompt = f"{AI_SISTEM_MESAJI}\n\n=== İŞTE GELEN MAİLLER ===\n" + "\n----------------\n".join(mail_listesi)
    
    api_key = os.environ["GEMINI_API_KEY"]
    clean_name = SECILEN_MODEL.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    data = { "contents": [{"parts": [{"text": prompt}]}] }
    
    # --- YENİ EKLENEN KISIM: RETRY (TEKRAR DENEME) MEKANİZMASI ---
    max_deneme = 3
    for deneme in range(1, max_deneme + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode('utf-8'))
                text_cevap = result['candidates'][0]['content']['parts'][0]['text']
                return text_cevap
        except Exception as e:
            hata_mesaji = str(e)
            print(f"Deneme {deneme}/{max_deneme} BAŞARISIZ OLDU. Hata: {hata_mesaji}")
            
            # Eğer 429 hatasıysa (Too Many Requests) bekle ve tekrar dene
            if "429" in hata_mesaji or "503" in hata_mesaji:
                if deneme < max_deneme:
                    print("Sunucu yoğun. 15 saniye bekleniyor...")
                    time.sleep(15) 
                    continue
            
            # Son denemeyse hatayı döndür
            if deneme == max_deneme:
                return f"HATA: Yapay zeka servisine ulaşılamadı. (Hata Kodu: {hata_mesaji})"
    
    return "Bilinmeyen hata."

def raporu_gonder(service, rapor_metni):
    if not rapor_metni: return
    try:
        hedef = os.environ["HEDEF_MAIL"]
        # Mailin içinde hata mesajı varsa başlığa DİKKAT yazalım
        baslik_on_ek = ""
        if "HATA:" in rapor_metni:
            baslik_on_ek = "⚠️ HATA VAR - "

        message = MIMEText(rapor_metni)
        message['to'] = hedef
        message['from'] = "me"
        message['subject'] = f"{baslik_on_ek}Günlük Mail Özeti - {datetime.now().strftime('%d.%m.%Y')}"
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'raw': raw_message}
        
        service.users().messages().send(userId="me", body=create_message).execute()
        print("Rapor gönderildi.")
    except Exception as e:
        print(f"Mail gönderme hatası: {e}")

if __name__ == '__main__':
    service = giris_yap()
    if service:
        mailler = mailleri_getir(service)
        if mailler:
            ozet = ai_ile_yorumla(mailler)
            raporu_gonder(service, ozet)
        else:
            print("Yeni mail yok.")
            # İstersen boş mail gelmesin diye burayı kapattım.
            # raporu_gonder(service, "Son 24 saatte yeni mail gelmedi.")

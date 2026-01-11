import os
import base64
import json
import urllib.request
import time
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime

# ================= SADECE BU MODEL KULLANILACAK =================

# Senin isteğin üzerine tek bir model ayarlandı.
# DİKKAT: Google bu modelin ismini değiştirirse veya erişimin yoksa hata verebilir.
SECILEN_MODEL = "models/gemini-3-pro-preview" 

# ================================================================

AI_SISTEM_MESAJI = """
Sen benim kişisel asistanımsın. Aşağıda son 24 saatte gelen maillerim var.
Bunları benim için analiz et ve Türkçe olarak özetle.
Önemli iş fırsatlarını, kritik uyarıları maddeler halinde yaz.
Gereksiz reklamları 'Diğerleri' diye tek cümlede geç.
"""

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
    # newer_than:1d -> Son 1 gün
    results = service.users().messages().list(userId='me', q='newer_than:1d', maxResults=50).execute()
    messages = results.get('messages', [])
    
    mail_icerikleri = []
    
    if not messages:
        print("Yeni mail yok.")
        return []

    print(f"{len(messages)} adet mail bulundu...")
    
    for msg in messages:
        try:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            headers = txt['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "Konu Yok")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Bilinmiyor")
            
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
            
            temiz_body = body[:2000].replace("\r", "").replace("\n", " ")
            mail_icerikleri.append(f"KİMDEN: {sender}\nKONU: {subject}\nİÇERİK: {temiz_body}")
        except:
            continue

    return mail_icerikleri

def ai_ile_yorumla(mail_listesi):
    print(f"Analiz başlıyor. Kullanılan Model: {SECILEN_MODEL}")
    
    prompt = f"{AI_SISTEM_MESAJI}\n\nMAİLLER:\n" + "\n".join(mail_listesi)
    
    api_key = os.environ["GEMINI_API_KEY"]
    
    # URL Hazırlığı
    clean_name = SECILEN_MODEL.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"HATA OLUŞTU! Model çalışmadı.\nHata detayı: {e}\n(Lütfen API Key'i veya model ismini kontrol et)."

def raporu_gonder(service, rapor_metni):
    if not rapor_metni: return
    try:
        hedef = os.environ["HEDEF_MAIL"]
        message = MIMEText(rapor_metni)
        message['to'] = hedef
        message['from'] = "me"
        message['subject'] = f"Günlük Mail Özeti - {datetime.now().strftime('%d.%m.%Y')}"
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'raw': raw_message}
        
        service.users().messages().send(userId="me", body=create_message).execute()
        print("Rapor başarıyla gönderildi.")
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
            print("Mail yok, işlem yapılmadı.")

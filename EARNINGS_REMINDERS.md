# Bilanço hatırlatmaları

Bu görev her gün Türkiye saatiyle 09.00 civarında Google E-Tablo'nun ayarlarda
belirtilen portföy sayfasını okur. Ek sembolleri birleştirir ve mükerrerleri kaldırır.
Eski portföy veya satılanlar sayfası okunmaz. Portföy sayfasındaki ekleme ve
çıkarmalar sonraki çalışmada otomatik yansır; ek semboller ayarlardan yönetilir.

Nasdaq bilanço takviminde gelecek 49 gün taranır. Bu pencerede bulunmayan
şirketlerde Stock Analysis'ın yaklaşan bilanço tarihi kontrol edilir. Geçmiş
tarihler kullanılmaz. Takvim ve tahmin bilgisi şirket tarafından teyit edilmiş
tarih gibi gösterilmez. Tarihi henüz bulunmayan şirketler haftalık özette belirtilir.

Bilançodan 3 gün önce e-posta ve Telegram gönderilir. Tarih geç duyurulursa
2, 1 veya 0 gün kala yakalanır. Pazartesi günü haftanın bilanço özeti gönderilir.
İlk kurulumda `initial` modu haftalık özeti o gün de gönderir.

Tekrar kontrolünde Gmail'in Gönderilenler klasörü kontrol edilir. Telegram
teslimi gizli bir Gmail etiketiyle işaretlenir. E-posta teslim olup Telegram
başarısız olursa sonraki çalışmada yalnız Telegram yeniden denenir. Bir gönderim
başarıyla teslim edildiği halde sunucunun yanıtı veya teslim etiketi kaybolursa
Telegram tekrarına karşı mutlak garanti yoktur.

## Özel ayarlar

Mevcut gönderim sırları kullanılır: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`,
`GMAIL_REFRESH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Ek ayar `EARNINGS_CONFIG_JSON` GitHub Actions secret olarak kaydedilir:

```json
{
  "spreadsheet_id": "ozel-dosya-kimligi",
  "sheet_name": "guncel-portfoy-sayfasi",
  "extra_symbols": ["ORNEK"],
  "email": "kendi-eposta-adresiniz",
  "days_before": 3
}
```

Ayar içeriği, şirket listesi, portföy bağlantısı ve alıcı bilgisi herkese açık
depoya veya çalışma günlüklerine yazılmaz. İşlem yalnız A:D sütunlarını okur;
maliyet, hisse adedi veya portföy değeri gönderilmez. Takip listesi yalnız özel
Telegram sohbetine ve Gmail hesabının kendi adresine gönderilir.

Görev hata aldığında başarılı çalışmış gibi görünmez; kişisel Telegram'a hata
bildirimi gönderilmeye çalışılır. GitHub çalışma özeti yalnız sayımları gösterir.
Google veya Telegram tamamen erişilemezse o kanaldan hata bildirimi de ulaşamaz.

## Doğrulama

```sh
python -m unittest test_earnings_reminders.py -v
python earnings_reminders.py --dry-run
python earnings_reminders.py --initial
```

Canlı kurulumda önce `dry-run`, ardından `initial` çalıştırılmalı; her iki
kanalın teslimi ve takvim görevinin `active` durumu ayrı ayrı kontrol edilmelidir.
GitHub zamanlama işleri gecikebilir. Mevcut mail-asistanı bakım görevi depoyu aktif
tutar; bu bakım kaldırılırsa GitHub'ın pasif depolardaki zamanlaması da izlenmelidir.

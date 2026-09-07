# Splash Init

نصب یکپارچهٔ **3x-ui + تنظیمات Splash + ProxyFleet XUI Sync** روی Ubuntu یا Debian.
برای نصب Sync دیگر به کلون‌کردن یا اجرای نصب‌کنندهٔ مخزن دیگری نیاز نیست.

## نصب روی سرور تازه

با کاربر `root` اجرا کنید:

```bash
curl --fail --location --retry 5 --retry-all-errors \
  --connect-timeout 15 --max-time 120 \
  https://raw.githubusercontent.com/exirhub/splash-init/main/install.sh \
  --output /root/splash-init.sh && bash /root/splash-init.sh
```

اگر `curl` نصب نیست، ابتدا اجرا کنید:

```bash
apt-get update
apt-get install -y ca-certificates curl
```

نصب‌کننده این مراحل را انجام می‌دهد:

1. بررسی root، Ubuntu/Debian و systemd و جلوگیری از نصب هم‌زمان.
2. نصب پیش‌نیازها و اعمال روال قبلی DNS، Swap یک‌گیگابایتی و تنظیمات TCP.
3. نصب غیرتعاملی 3x-ui در صورت نبودن آن.
4. اعتبارسنجی `x-ui-ads.db` و ورود آن **فقط وقتی پیش از نصب دیتابیسی وجود نداشته باشد**.
5. نصب نسخهٔ همراه ProxyFleet Sync، تنظیمات و سرویس systemd.
6. اجرای اولین Sync و فعال‌کردن تایمر **۱۰دقیقه‌ای** با حداکثر ۳۰ ثانیه تأخیر تصادفی.
7. بررسی مجموعهٔ `TH-*`، Selector مربوط به `ADMOB-BALANCER` و تنظیمات runtime.
8. نصب دستور دائمی `splash-init-update`.

اجرای عادی، مانند نصب‌کنندهٔ قبلی، UFW را در صورت نصب‌بودن غیرفعال و DNS و
تنظیمات شبکه را اعمال می‌کند. برای به‌روزرسانی از دستور مخصوص زیر استفاده کنید.
Node.js، PM2 و دریافت‌کنندهٔ فایل `server.js` نصب نمی‌شوند.

## به‌روزرسانی

```bash
sudo splash-init-update
```

یا از یک نصب‌کنندهٔ دریافت‌شده:

```bash
sudo bash /root/splash-init.sh --update-only
```

حالت `--update-only` کد یکپارچه و Sync را به‌روزرسانی می‌کند؛ دیتابیس اولیه را
دوباره وارد نمی‌کند، 3x-ui را ارتقا نمی‌دهد و DNS، UFW، Swap یا TCP را تغییر
نمی‌دهد. Sync طبق قواعد معمول خودش Outboundهای مدیریت‌شده را همگام می‌کند.
تنظیمات، توکن، Inboundها، قواعد مسیریابی و state محدودیت ری‌استارت حفظ می‌شوند.

اجرای `install.sh` از یک کلون کامل، همان نسخهٔ محلی را نصب می‌کند؛ برای آپدیت
کلون ابتدا `git pull --ff-only` کنید. اگر فقط نصب‌کننده دریافت شده باشد، فایل‌های
لازم از یک commit مشخص در **همین مخزن** دریافت می‌شوند. `SPLASH_REF` می‌تواند
commit، tag یا branch باشد؛ مقدار پیش‌فرض `main` است.

## دیتابیس و مسیریابی

قالب پیش‌فرض **`x-ui-ads.db`** است و از قبل قاعدهٔ مسیریابی به `ADMOB-BALANCER`
را دارد. این قالب در شروع هیچ Outbound مدیریت‌شدهٔ `TH-*` ندارد؛ بنابراین
اولین فهرست معتبر و Ready از ProxyFleet فوراً وارد می‌شود.

فایل قدیمی `x-ui.db` در مخزن حفظ شده، اما قاعدهٔ لازم برای این Balancer را
ندارد و قالب مناسبی برای نصب یکپارچه نیست. اعتبارسنجی آن را پیش از تغییر
دیتابیس رد می‌کند. نصب‌کننده قواعد دلخواه مسیریابی ایجاد یا حدس نمی‌زند.

اگر `/etc/x-ui/x-ui.db` از قبل وجود داشته باشد، نصب‌کننده آن را حفظ می‌کند؛
این دیتابیس باید خودش تنظیمات سازگار با Balancer را داشته باشد. قالب اولیه
هیچ‌وقت برای بازنویسی کاربران یا تنظیمات موجود استفاده نمی‌شود.

نصب‌کننده سرویس استاندارد `x-ui` و مسیر `/etc/x-ui/x-ui.db` را مدیریت می‌کند.
اگر تنظیمات Sync موجود به سرویس یا دیتابیس دیگری اشاره کند، پیش از تغییرات
میزبان متوقف می‌شود تا دیتابیس نامرتبطی ایجاد یا تغییر نکند.

در نصب تازه، پیش از جایگزینی دیتابیس ساخته‌شده توسط نصب‌کنندهٔ رسمی، بک‌آپ
سازگار با SQLite/WAL در `/var/lib/splash-init/backups/` تهیه می‌شود. اگر سرویس
پس از ورود قالب بالا نیاید، بازگرداندن بک‌آپ و راه‌اندازی مجدد بررسی می‌شود؛
خطا به‌عنوان موفقیت نصب اعلام نمی‌شود و بک‌آپ برای بازیابی باقی می‌ماند.

## تنظیمات ProxyFleet

فایل تنظیمات با دسترسی `0600` ساخته می‌شود:

```text
/etc/proxyfleet-xui-sync.env
```

آدرس پیش‌فرض همان آدرس پروژهٔ Sync است:

```text
http://85.237.211.23:8788/outbounds
```

در **اولین نصب**، متغیرهای `PROXYFLEET_OUTBOUNDS_URL` و
`PROXYFLEET_OUTBOUNDS_TOKEN` مقادیر اولیه را تعیین می‌کنند. برای تغییر نصب
موجود، فایل تنظیمات را ویرایش کنید؛ اجرای مجدد مقادیر موجود را با پیش‌فرض‌ها
یا متغیرهای پوسته جایگزین نمی‌کند.

```bash
sudoedit /etc/proxyfleet-xui-sync.env
sudo systemctl start proxyfleet-xui-sync.service
```

نصب تازه مسیر Xray را برای `amd64` یا `arm64` شناسایی می‌کند. مقدار سفارشی
`XRAY_BINARY` در نصب موجود حفظ می‌شود. تنظیمات کامل در
[راهنمای Sync همراه](vendor/proxyfleet-xui-sync/README.md) آمده است.

## بررسی نتیجه

```bash
systemctl status x-ui --no-pager
systemctl status proxyfleet-xui-sync.timer --no-pager
systemctl list-timers proxyfleet-xui-sync.timer
journalctl -u proxyfleet-xui-sync.service -n 100 --no-pager -o cat
```

| نتیجه | معنی |
| --- | --- |
| کد خروج `0` | نصب انجام شده و مجموعهٔ مدیریت‌شده و تنظیمات runtime سازگارند. |
| کد خروج `2` و `SYNC PENDING` | اجزا نصب‌اند، ولی مجموعهٔ اولیه، runtime یا تغییر توپولوژی هنوز آماده نیست؛ تایمر دوباره بررسی می‌کند. |
| کد خروج دیگر | خطایی رخ داده است؛ نصب کامل اعلام نمی‌شود. |

موفق‌بودن `systemctl start` به‌تنهایی دریافت مجموعهٔ جدید را ثابت نمی‌کند؛
Sync ممکن است به دلیل Readyنبودن فهرست یا قواعد پایداری، اجرا را عقب بیندازد.
بررسی پایان نصب، سازگاری تنظیمات است؛ تست اتصال از ایران یا تضمین تازه‌بودن
پاسخ ProxyFleet نیست. جزئیات اجرای جاری در لاگ سرویس ثبت می‌شود.

پس از رفع مشکل، برای تلاش دوباره `sudo splash-init-update` را اجرا کنید.

## Cloud-Init

در بخش User Data سرور Ubuntu/Debian قرار دهید:

```yaml
#cloud-config
package_update: true
packages:
  - ca-certificates
  - curl
runcmd:
  - [bash, -c, 'set -e; curl --fail --location --retry 5 --retry-all-errors --connect-timeout 15 --max-time 120 https://raw.githubusercontent.com/exirhub/splash-init/main/install.sh --output /root/splash-init.sh; bash /root/splash-init.sh > /var/log/splash-init.log 2>&1']
```

## OVH Post-Installation Script و AWS

برای P-I-S، اسکریپت Bash زیر را قرار دهید؛ `#cloud-config` اضافه نکنید:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y ca-certificates curl
curl --fail --location --retry 5 --retry-all-errors \
  --connect-timeout 15 --max-time 120 \
  https://raw.githubusercontent.com/exirhub/splash-init/main/install.sh \
  --output /root/splash-init.sh
bash /root/splash-init.sh > /var/log/splash-init.log 2>&1
```

فایل `aws.sh` نیز ورودی همان نصب‌کننده است و منطق نصب یا دیتابیس جداگانه ندارد.

## نسخهٔ همراه و تست‌ها

Sync همراه، بدون تغییر رفتار runtime، از این commit است:

```text
exirhub/proxyfleet-xui-sync
cba805f382da7ec104ffb73959e82ea8ba84ad9e
```

[فایل منشأ و checksumها](vendor/proxyfleet-xui-sync.source.json) این نسخه را
مشخص می‌کند. آپدیتر نسخهٔ منتشرشده در **splash-init** را نصب می‌کند؛ تغییرات
آیندهٔ مخزن مستقل Sync باید ابتدا همراه با تست‌ها به این مخزن وارد شوند.

```bash
bash -n install.sh
bash -n aws.sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 vendor/proxyfleet-xui-sync/tests/smoke_test.py
```

تست‌ها از فایل‌ها و دیتابیس‌های موقت و سرویس‌های شبیه‌سازی‌شده استفاده می‌کنند.
CI سلامت فایل‌های دیتابیس واقعی مخزن را نیز به‌صورت فقط‌خواندنی بررسی می‌کند؛
هیچ نصب یا ری‌استارتی روی سرور عملیاتی انجام نمی‌شود.

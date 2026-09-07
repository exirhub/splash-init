# Splash Init

نصب یکپارچهٔ **3x-ui + تنظیمات Splash + ProxyFleet XUI Sync** روی Ubuntu یا Debian.
برای نصب Sync دیگر به کلون‌کردن یا اجرای نصب‌کنندهٔ مخزن دیگری نیاز نیست.

## نصب مخزن خصوصی؛ بدون تنظیم Git یا SSH

این مخزن خصوصی است؛ لینک خام بدون احراز هویت ممکن است `404` بدهد.
برای نصب به Git، GitHub CLI، کلون‌کردن مخزن یا ساخت کلید SSH روی سرور نیاز نیست.
یک توکن GitHub کافی است؛ کلید SSH برای دستورهای HTTP زیر کاربرد ندارد.

از [ساخت Fine-grained token](https://github.com/settings/personal-access-tokens/new)
توکنی با این دسترسی بسازید، یا از توکن موجود با همین دسترسی استفاده کنید:

- **Resource owner:** `exirhub`
- **Repository access → Only select repositories:** `splash-init`
- **Repository permissions → Contents:** `Read-only`

برای این نصب دسترسی نوشتن یا دسترسی به مخزن دیگری لازم نیست.
این مجوز در [مستندات GitHub Contents API](https://docs.github.com/en/rest/repos/contents#get-repository-content)
توضیح داده شده است.

کل بلوک زیر را روی Ubuntu یا Debian اجرا کنید. پیش‌نیاز دانلود خودکار نصب
می‌شود؛ وقتی `GitHub token:` ظاهر شد، توکن را بچسبانید و Enter بزنید.
هنگام واردکردن توکن هیچ کاراکتری نمایش داده نمی‌شود.

```bash
sudo bash <<'SPLASH_INSTALL'
set +x
set -Eeuo pipefail
umask 077
if ! command -v curl >/dev/null || [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
fi
read -r -s -p 'GitHub token: ' SPLASH_GITHUB_TOKEN </dev/tty
printf '\n' >/dev/tty
[[ "$SPLASH_GITHUB_TOKEN" =~ ^[A-Za-z0-9._~+/-]+=*$ ]] || { echo 'Invalid GitHub token format.' >&2; exit 1; }
export -n SPLASH_GITHUB_TOKEN
work="$(mktemp -d /tmp/splash-bootstrap.XXXXXX)"
trap 'unset SPLASH_GITHUB_TOKEN; rm -rf -- "$work"' EXIT
status="$(printf 'header = "Authorization: Bearer %s"\n' "$SPLASH_GITHUB_TOKEN" |
  curl -q --config - --fail --silent --show-error --proto '=https' --tlsv1.2 \
    --retry 5 --retry-all-errors --connect-timeout 15 --max-time 120 \
    --header 'Accept: application/vnd.github.raw+json' \
    --output "$work/install.sh" --write-out '%{http_code}' \
    'https://api.github.com/repos/exirhub/splash-init/contents/install.sh?ref=main')"
[[ "$status" == 200 && -s "$work/install.sh" ]] || { echo 'GitHub download failed.' >&2; exit 1; }
bash -n "$work/install.sh"
SPLASH_GITHUB_TOKEN="$SPLASH_GITHUB_TOKEN" bash "$work/install.sh"
SPLASH_INSTALL
```

توکن فقط در همین اجرا استفاده می‌شود؛ در فایل تنظیمات یا دستور آپدیتر ذخیره
نمی‌شود و جزئی از URL یا آرگومان‌های `curl` نیست. تمام فایل‌های خصوصی، از جمله
دیتابیس و کد Sync همراه، با همان توکن دریافت می‌شوند. دانلود عمومی 3x-ui توکن
GitHub شما را دریافت نمی‌کند. این توکن با `PROXYFLEET_OUTBOUNDS_TOKEN` متفاوت است.

نصب‌کننده این مراحل را انجام می‌دهد:

1. بررسی root، Ubuntu/Debian و systemd و جلوگیری از نصب هم‌زمان.
2. نصب پیش‌نیازها و اعمال روال قبلی DNS، Swap یک‌گیگابایتی و تنظیمات TCP.
3. نصب غیرتعاملی 3x-ui در صورت نبودن آن.
4. اعتبارسنجی `x-ui.db` و ورود آن **فقط وقتی پیش از نصب دیتابیسی وجود نداشته باشد**.
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

آپدیتر توکن را دوباره به‌صورت مخفی می‌پرسد؛ هیچ تنظیم اولیهٔ Git یا SSH لازم
نیست. نصب‌های قبلی که آپدیترشان هنوز احراز هویت ندارد، یک بار بلوک نصب بالا
را اجرا کنند و آخرین دستور داخل آن را به این خط تغییر دهند:

```bash
SPLASH_GITHUB_TOKEN="$SPLASH_GITHUB_TOKEN" bash "$work/install.sh" --update-only
```

در اجرای غیرتعاملی، متغیر `SPLASH_GITHUB_TOKEN` باید از پیش در محیط فرایند
موجود باشد؛ بدون توکن و ترمینال، برنامه با خطای روشن متوقف می‌شود.

حالت `--update-only` کد یکپارچه و Sync را به‌روزرسانی می‌کند؛ دیتابیس اولیه را
دوباره وارد نمی‌کند، 3x-ui را ارتقا نمی‌دهد و DNS، UFW، Swap یا TCP را تغییر
نمی‌دهد. Sync طبق قواعد معمول خودش Outboundهای مدیریت‌شده را همگام می‌کند.
تنظیمات، توکن، Inboundها، قواعد مسیریابی و state محدودیت ری‌استارت حفظ می‌شوند.

اجرای `install.sh` از یک کلون کامل، همان نسخهٔ محلی را نصب می‌کند؛ برای آپدیت
کلون ابتدا `git pull --ff-only` کنید. اگر فقط نصب‌کننده دریافت شده باشد، فایل‌های
لازم از یک commit مشخص در **همین مخزن** دریافت می‌شوند. `SPLASH_REF` می‌تواند
commit، tag یا branch باشد؛ مقدار پیش‌فرض `main` است.

## دیتابیس و مسیریابی

تنها قالب نصب **`x-ui.db`** همین مخزن است. سلامت SQLite و ساختار تنظیمات آن
قبل از ورود بررسی می‌شود. قواعد مسیریابی داخل همین فایل حفظ می‌شوند؛ داشتن
قاعدهٔ تبلیغ یا ارجاع به `ADMOB-BALANCER` شرط نصب نیست.

Sync طبق رفتار خود Outboundهای `TH-*` و Selector مربوط به `ADMOB-BALANCER`
را همگام می‌کند و به قواعد مسیریابی دست نمی‌زند. اگر قالب اولیه Outbound
مدیریت‌شده نداشته باشد، اولین فهرست معتبر و Ready فوراً وارد می‌شود.

اگر `/etc/x-ui/x-ui.db` از قبل وجود داشته باشد، نصب‌کننده آن را حفظ می‌کند؛
قالب اولیه هیچ‌وقت برای بازنویسی کاربران یا تنظیمات موجود استفاده نمی‌شود.

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
فیلد `balancer_routing` فقط نشان می‌دهد قواعد موجود به Balancer ارجاع دارند
یا نه؛ مقدار `false` جلوی نصب یا همگام‌سازی را نمی‌گیرد و قاعده‌ای تحمیل نمی‌کند.

پس از رفع مشکل، برای تلاش دوباره `sudo splash-init-update` را اجرا کنید.

## Cloud-Init، OVH Post-Installation Script و AWS

برای نصب بدون حضور کاربر، اسکریپت زیر را در بخش **Shell script / User Data**
یا **P-I-S** قرار دهید و مقدار `REPLACE_WITH_GITHUB_TOKEN` را جایگزین کنید.
Cloud-Init ورودی با سربرگ `#!/usr/bin/env bash` را نیز اجرا می‌کند؛ به این
اسکریپت `#cloud-config` اضافه نکنید. در این روش توکن در User Data ارائه‌دهنده
قرار می‌گیرد؛ دسترسی آن را فقط به خواندن همین مخزن محدود کنید.

```bash
#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077
SPLASH_GITHUB_TOKEN='REPLACE_WITH_GITHUB_TOKEN'
export -n SPLASH_GITHUB_TOKEN
[[ "$SPLASH_GITHUB_TOKEN" =~ ^[A-Za-z0-9._~+/-]+=*$ ]] || { echo 'Invalid GitHub token format.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y ca-certificates curl
work="$(mktemp -d /tmp/splash-bootstrap.XXXXXX)"
trap 'unset SPLASH_GITHUB_TOKEN; rm -rf -- "$work"' EXIT
status="$(printf 'header = "Authorization: Bearer %s"\n' "$SPLASH_GITHUB_TOKEN" |
  curl -q --config - --fail --silent --show-error --proto '=https' --tlsv1.2 \
    --retry 5 --retry-all-errors --connect-timeout 15 --max-time 120 \
    --header 'Accept: application/vnd.github.raw+json' \
    --output "$work/install.sh" --write-out '%{http_code}' \
    'https://api.github.com/repos/exirhub/splash-init/contents/install.sh?ref=main')"
[[ "$status" == 200 && -s "$work/install.sh" ]] || { echo 'GitHub download failed.' >&2; exit 1; }
bash -n "$work/install.sh"
SPLASH_GITHUB_TOKEN="$SPLASH_GITHUB_TOKEN" bash "$work/install.sh" > /var/log/splash-init.log 2>&1
```

فایل `aws.sh` نیز ورودی همان نصب‌کننده است و منطق نصب یا دیتابیس جداگانه ندارد؛
با `SPLASH_GITHUB_TOKEN` یا دریافت مخفی توکن از ترمینال کار می‌کند.

## خطای دسترسی به GitHub

- **401:** توکن اشتباه، منقضی یا لغو شده است.
- **403:** مجوز یا تأیید سازمان، سیاست دسترسی یا محدودیت درخواست‌ها را بررسی کنید.
- **404:** نام مخزن/نسخه یا دسترسی توکن به `exirhub/splash-init` را بررسی کنید؛
  مخزن خصوصی بدون مجوز هم می‌تواند `404` بدهد.

توکن GitHub را در لاگ یا پیام خطا وارد نکنید؛ برای عیب‌یابی همان کد خطا کافی است.

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

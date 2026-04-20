# GitHub'a Yükleme Rehberi — Adım Adım

Bu rehber PolyPaper Bot projesini **private** olarak GitHub'a yüklemek için yazıldı. GitHub'ı hiç kullanmamış birisi için adım adım.

> **Not:** Git operasyonları Windows PC'nizde çalışacak. İlk önce temel araçları kurmanız gerek.

---

## 0. Ön Hazırlık (5–10 dakika)

### Git kurulumu

1. https://git-scm.com/download/win adresine git
2. "64-bit Git for Windows Setup" indir ve çift tıkla
3. Kurulumda **hepsini default** bırak, sadece "Git from the command line" seçeneğinin işaretli olduğundan emin ol
4. Kurulum bittikten sonra PowerShell veya cmd aç:
   ```
   git --version
   ```
   `git version 2.x.x.windows.1` gibi bir çıktı görmelisin.

### GitHub CLI (`gh`) kurulumu

1. https://cli.github.com/ adresine git
2. "Download for Windows" tıkla → `gh_*_windows_amd64.msi` dosyasını kur
3. Yeni bir cmd penceresi aç:
   ```
   gh --version
   ```
   `gh version 2.x.x` çıktısı görmelisin.

### GitHub hesabı

Zaten var (Gmail: vfurkanv@gmail.com). Eğer iki-faktörlü auth (2FA) açık değilse önce onu aç:
- github.com → Profile → Settings → Password and authentication → 2FA aç

---

## 1. GitHub'a Giriş (`gh auth login`)

Cmd veya PowerShell aç, proje klasörüne git:

```cmd
cd C:\yol\yaklasik\boyle\Polyscout31
```

Sonra:

```cmd
gh auth login
```

Sırasıyla soracak:
- **Where do you use GitHub?** → `GitHub.com`
- **What is your preferred protocol?** → `HTTPS`
- **Authenticate Git with your GitHub credentials?** → `Y` (yes)
- **How would you like to authenticate?** → `Login with a web browser`

Sonra bir **one-time code** gösterecek (örn: `A1B2-C3D4`). Kopyala.

Tarayıcıda açılan sayfada bu code'u yapıştır → Authorize.

Cmd'e dönünce "Logged in as <kullanici>" yazmalı. Kontrol:

```cmd
gh auth status
```

---

## 2. Adım 1 — Git Init + İlk Commit

Proje klasöründe:

```cmd
scripts\setup_github_step1_init.bat
```

Bu script:
1. Eğer bozuk `.git/` varsa sana sorar, siler
2. `git init -b main` yapar
3. Config ayarlarını yazar (email, hooks path, autocrlf)
4. **Sadece onaylı dosyaları** stage eder:
   - Kök: `README.md`, `LICENSE`, `SECURITY.md`, `CHANGELOG.md`, `.gitignore`, `.env.example`, `requirements.txt`, `main.py`, bat dosyaları
   - Klasörler: `core/`, `backtest/`, `telegram_bot/`, `indicators/`, `skills/`, `data_feeds/`, `calibration/`, `config/`, `db/`, `utils/`, `scripts/`, `tests/`, `worker/`, `tools/`, `docs/`, `.githooks/`, `.github/`
5. `.env` ve `*.db` dosyalarının **staged olmadığını** doğrular
6. `git commit` ile initial commit oluşturur
7. **Pre-commit hook** burada otomatik çalışır ve API key pattern'i varsa commit'i reddeder

### Sorun çıkarsa

- **"git bulunamadi"** → Git kurulumu eksik, adım 0'a dön
- **Pre-commit reject** → Bir yerde API key kalmış. Hook hangi dosyada olduğunu gösterir. O dosyayı düzenle, tekrar dene
- **.git silinmiyor** → Manuel: `rmdir /s /q .git`

---

## 3. Adım 2 — GitHub'a Push

```cmd
scripts\setup_github_step2_push.bat
```

Bu script:
1. `gh` kurulu mu, giriş yapılmış mı kontrol eder
2. Repo ismi sorar (default: `polypaper-bot`)
3. `PRIVATE` olarak onay ister
4. `gh repo create <isim> --private --source=. --push` komutuyla repo yaratır ve push eder

Başarılı olunca sana repo URL'sini verecek. Tarayıcıda görmek için:

```cmd
gh repo view polypaper-bot --web
```

---

## 4. Günlük Kullanım (Commit akışı)

İlk yükleme bittikten sonra kod değiştirdiğinde:

```cmd
git status                    # Ne değişmiş gör
git add .                     # Tüm değişiklikleri stage et
git commit -m "mesaj"         # Commit (pre-commit hook yine çalışır)
git push                      # GitHub'a gönder
```

**Commit mesajı örnekleri:**
- `feat: yeni classic strategy plugin eklendi`
- `fix: shadow report JobQueue race condition`
- `docs: README güncellendi`
- `chore: requirements.txt bump`

---

## 5. Güvenlik Kontrolü (İlk push sonrası)

Tarayıcıda repo'ya git ve mutlaka kontrol et:

1. **Repo privacy** → Settings → sağ üstte 🔒 PRIVATE yazmalı
2. **.env orada YOK olmalı** → kök dizinde `.env.example` var ama `.env` yok
3. **Hiçbir .db dosyası YOK olmalı** → `polypaper.db`, `shadow_trades.db` görünmemeli
4. **data_store/ YOK olmalı** → 195GB veri buraya gitmemeli
5. **logs/ YOK olmalı**

Bir şey varsa — **DERHAL**:
```cmd
git rm --cached <yanlis-dosya>
git commit -m "fix: remove sensitive file"
git push
```

> **KRİTİK:** Git history'den bir dosyayı tamamen silmek zor. Eğer `.env` kazara push edilirse **tüm API key'leri rotate etmek zorundasın** (SECURITY.md'deki prosedür).

---

## 6. Collaborator Eklemek (ileride)

Şu an sadece sen görüyorsun. Birini eklemek istersen:

```cmd
gh repo edit polypaper-bot --add-collaborator KULLANICI_ADI
```

Veya tarayıcıda: Settings → Collaborators → Add people.

---

## 7. Yedekleme & Clone

GitHub'a push ettikten sonra kodun bir yedeği orada. Başka bir cihaza/klasöre çekmek için:

```cmd
gh repo clone polypaper-bot
```

> **NOT:** Clone edilen klasörde `.env` YOKTUR. Yeni ortama kuruyorsan `.env.example`'ı kopyala `.env` yap ve kendi key'lerini gir.

---

## 8. Sık Sorulan Sorular

### Repo'yu silmek istersem?

```cmd
gh repo delete polypaper-bot --yes
```

(Dikkat — geri alınamaz.)

### Yanlış dosya commit ettim, geri almak istiyorum?

Son commit henüz push edilmediyse:
```cmd
git reset --soft HEAD~1     # Commit'i geri al, stage'i tut
git reset HEAD <dosya>       # Dosyayı unstage et
```

Push edildiyse force push gerekir — tehlikeli, yardım iste.

### GitHub'ı başka bilgisayardan nasıl kullanırım?

Yeni makinede git + gh kur → `gh auth login` → `gh repo clone polypaper-bot` → `.env` dosyasını kendi key'lerinle oluştur → `python main.py`.

---

## Özet

```
Windows PC (Cmd):
  cd Polyscout31
  gh auth login                                  (ilk defa)
  scripts\setup_github_step1_init.bat           (init + commit)
  scripts\setup_github_step2_push.bat           (push to GitHub)
  
Sonraki değişikliklerde:
  git add .
  git commit -m "mesaj"
  git push
```

Bu kadar. Herhangi bir adımda takılırsan hata mesajını aynen kopyala bana at.

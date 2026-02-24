# Subs.ro -- Kodi Subtitle Addon & Repository

Addon Kodi pentru subtitrari de pe [subs.ro](https://subs.ro) -- cea mai mare baza de date de subtitrari romanesti.

**Current version: 1.0.3**

## Instalare in Kodi

### Pasul 1 -- Adauga source-ul in Kodi

**Settings > File Manager > Add Source > `<None>`**

```
https://triffalin.github.io/repository.subsro/
```

Numeste-l: `Subs.ro`

### Pasul 2 -- Instaleaza repository addon-ul

**Addons > Install from zip file > Subs.ro >** `repository.subsro-1.0.0.zip`

### Pasul 3 -- Instaleaza addon-ul de subtitrari

**Addons > Install from repository > Subs.ro Repository > Subtitle providers > Subs.ro > Install**

### Pasul 4 -- Configureaza API Key

Setarile addon-ului > introdu API key-ul de pe [subs.ro](https://subs.ro) (Profil > API)

---

## GitHub Pages (Kodi Source URL)

**[https://triffalin.github.io/repository.subsro/](https://triffalin.github.io/repository.subsro/)**

---

## Structura Repo

```
repository.subsro/
├── index.html                  # GitHub Pages landing page
├── repository.subsro-1.0.0.zip # Quick-install zip for the repository addon
├── repository.subsro/          # Repository addon source
│   └── addon.xml
├── service.subtitles.subsro/   # Subtitle addon source
│   ├── addon.xml
│   ├── service.py
│   ├── changelog.txt
│   └── resources/
│       ├── settings.xml
│       ├── media/
│       ├── language/
│       └── lib/
│           ├── subtitle_downloader.py
│           ├── archive_utils.py
│           ├── data_collector.py
│           ├── cache.py
│           ├── file_operations.py
│           ├── utilities.py
│           ├── exceptions.py
│           └── subsro/
│               └── provider.py
├── zips/                       # Kodi repository index + zip files
│   ├── addons.xml
│   ├── addons.xml.md5
│   ├── index.html
│   ├── repository.subsro/
│   │   ├── repository.subsro-1.0.0.zip
│   │   └── index.html
│   └── service.subtitles.subsro/
│       ├── service.subtitles.subsro-1.0.3.zip
│       └── index.html
├── _repo_generator.py          # Regenerate zips/ after changes
├── LICENSE
└── README.md
```

## Kodi Installation Flow

1. User adds `https://triffalin.github.io/repository.subsro/` as a file source
2. User installs `repository.subsro-1.0.0.zip` from that source
3. The repository addon points Kodi to `zips/addons.xml` for available addons
4. Kodi finds `service.subtitles.subsro` v1.0.3 in the index
5. Kodi downloads `zips/service.subtitles.subsro/service.subtitles.subsro-1.0.3.zip`
6. User configures their subs.ro API key in addon settings

## Update versiune

```bash
# Edit source files in service.subtitles.subsro/
# Bump version in service.subtitles.subsro/addon.xml
# Then regenerate:
python _repo_generator.py
git add -A && git commit -m "chore: v1.x.x" && git push
```

## Changelog

### v1.0.3 (2026-02-23)
- Fix: unsupported language codes (e.g. Russian) no longer cause HTTP 400 / Unknown error
- Unsupported languages are silently skipped; search continues for supported ones

### v1.0.2 (2026-02-23)
- Fix: Unknown error caused by double endOfDirectory calls and uncaught exceptions

### v1.0.1 (2026-02-23)
- Fix: Configure button and GitHub Pages URL setup

### v1.0.0 (2026-02-23)
- Initial release
- Search subtitles by IMDB ID, TMDB ID, title, release name
- Support: Romanian, English, Italian, French, German, Hungarian, Greek, Portuguese, Spanish
- ZIP and RAR archive extraction with encoding detection (UTF-8, CP1250, ISO-8859-2)
- TV show season/episode smart filtering
- Preferred language setting in addon preferences
- Debug logging toggle

## Licenta

GPL-3.0

# Subs.ro — Kodi Subtitle Addon & Repository

Addon Kodi pentru subtitrari de pe [subs.ro](https://subs.ro) — cea mai mare baza de date de subtitrari romanesti.

## Instalare in Kodi

### Pasul 1 — Adauga source-ul

**Settings → File Manager → Add Source:**
```
https://raw.githubusercontent.com/triffalin/repository.subsro/main/zips/
```
Numeste-l: `Subs.ro Repo`

### Pasul 2 — Instaleaza repository addon-ul

**Addons → Install from zip file → Subs.ro Repo:**
- Selecteaza `repository.subsro-1.0.0.zip`

### Pasul 3 — Instaleaza addon-ul de subtitrari

**Addons → Install from repository → Subs.ro Repository → Subtitle providers → Subs.ro**

### Pasul 4 — Configureaza API Key

Dupa instalare, seteaza API key-ul de pe [subs.ro](https://subs.ro) (Profil → API).

---

## Structura Repo

```
repository.subsro/
├── repository.subsro/          # Repository addon source
│   └── addon.xml
├── service.subtitles.subsro/   # Subtitle addon source
│   ├── addon.xml
│   ├── default.py
│   └── resources/
├── zips/                       # Kodi repository index
│   ├── addons.xml
│   ├── addons.xml.md5
│   ├── repository.subsro/
│   │   └── repository.subsro-1.0.0.zip
│   └── service.subtitles.subsro/
│       └── service.subtitles.subsro-1.0.0.zip
└── _repo_generator.py          # Generator script
```

## Update versiune

```bash
python _repo_generator.py
git add -A && git commit -m "chore: bump to vX.Y.Z"
git push
```

## Licenta

GPL-3.0

# Subs.ro — Kodi Subtitle Addon & Repository

Addon Kodi pentru subtitrari de pe [subs.ro](https://subs.ro) — cea mai mare baza de date de subtitrari romanesti.

## Instalare in Kodi

### Pasul 1 — Adauga source-ul in Kodi

**Settings → File Manager → Add Source → `<None>`**

```
https://triffalin.github.io/repository.subsro/
```

Numeste-l: `Subs.ro`

### Pasul 2 — Instaleaza repository addon-ul

**Addons → Install from zip file → Subs.ro →** `repository.subsro-1.0.0.zip`

### Pasul 3 — Instaleaza addon-ul de subtitrari

**Addons → Install from repository → Subs.ro Repository → Subtitle providers → Subs.ro → Install**

### Pasul 4 — Configureaza API Key

Setarile addon-ului → introdu API key-ul de pe [subs.ro](https://subs.ro) (Profil → API)

---

## GitHub Pages (Kodi Source URL)

**[https://triffalin.github.io/repository.subsro/](https://triffalin.github.io/repository.subsro/)**

---

## Structura Repo

```
repository.subsro/
├── index.html                  # GitHub Pages — Kodi source URL
├── repository.subsro/          # Repository addon source
│   └── addon.xml
├── service.subtitles.subsro/   # Subtitle addon source
│   ├── addon.xml
│   ├── default.py
│   └── resources/
├── zips/                       # Kodi repository index + zip files
│   ├── addons.xml
│   ├── addons.xml.md5
│   ├── repository.subsro/
│   │   └── repository.subsro-1.0.0.zip
│   └── service.subtitles.subsro/
│       └── service.subtitles.subsro-1.0.0.zip
└── _repo_generator.py
```

## Update versiune

```bash
python _repo_generator.py
git add -A && git commit -m "chore: v1.x.x" && git push
```

## Licenta

GPL-3.0

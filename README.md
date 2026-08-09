# Cian archive

A small local Python server that saves a Cian flat listing into `archive/<id>/listing.json` and downloads available public images to `archive/<id>/images/`.

```sh
python app.py
```

Open http://127.0.0.1:8787 and paste a Cian flat URL. The UI lets you inspect the JSON, open the original listing, and delete saved archives.

The collector tries Cian's structured offer endpoint first (saving its full response in `raw_offer`), then falls back to public HTML metadata/JSON-LD. It does not automate CAPTCHA solving or attempt to evade Cian's access controls. If Cian blocks both normal public routes from a network, no archive is created.

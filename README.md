# Shelf Scanner proof of concept

A small web app that follows automation plan v4. You upload shelf photos and pick the shelf category, and it returns the books worth pulling. Each pull comes with a numbered box on the photo, a crop of its spine, where to find it, why it's flagged, what to check in hand, matching sales from the price file, and Terapeak, eBay and AbeBooks lookup links.

## How it works
1. **Shelf finding:** one Sonnet 5.5 call on a 1568 px overview of the photo returns the shelf bands.
2. **Reading:** each shelf is cut into overlapping full-resolution crops, with red lines marking the part each crop owns and a percentage ruler along the top. Sonnet lists every spine in TSV: position, literal text, author, title, imprint, format, jacket, look, appearance and marks. Books listed by two neighbouring crops are removed in code.
3. **Close-ups:** up to three sheets per photo of the spines read least well, each enlarged about three times, are read again (about 2–3¢). This is how the faint *Odyssey* on the fine-bindings shelf gets read.
4. **Second read (optional):** on rare, extremely rare and signed shelves, Opus 5.5 looks only for flat, stacked and hidden books. Only books the first read missed are added. Books listed twice (boxes on top of each other with similar titles, as happens with flat books and at the photo's edge) are dropped, before and after the close-ups.
5. **Rules:** `shelfscan/rules.py` applies plan v4 as fixed code, with tuning from testing:
   - a year printed on the spine (1935 or MCMXXXV) overrides the model's guess at the book's age;
   - retold and abridged editions don't borrow the original's sales;
   - a modern copy on a thrift or cheap shelf needs at least two matching sales;
   - an old copy that finds no sales by its author or full title may match on two distinctive title words ("Arabian Nights", "The Secret Garden"), using only listings not dated 1970 or later, because editors and translators get read as authors and old spines often name no author;
   - on rare and extremely rare shelves, antique books, and old ones whose spine shows illustrations, a translation or a small press, are listed as **lesser-known leads** (blue) when the price file has nothing on them;
   - several copies of one title share a single entry and number, the most promising copy first;
   - **extremely rare and signed shelves** are priced high already, so they have no minimum value, and age alone (an antique look) doesn't list a book there. Every book with a sign of value is listed in one **check order**, likeliest value first, with a short table at the top of the photo giving each book's place, likely value and the first thing to check. The likely value comes from the best evidence there is: signed copies of this title, then signed copies by the author (for a signed copy), then sales of this title, then the lower quartile of the author's other sales (a rough guide; an author with three or more sales also counts as a sign of value on these shelves). Books with no price evidence come last, strongest signs first. On these shelves the reading prompt also asks for **signed stickers** on spines and jackets; a sticker makes the signature the first check and uses signed sales for the value.
   - **Paperback gate:** paperbacks are skipped unless they're on the whitelist or by King, Bachman, Tolkien or Philip K. Dick.
   - **Exclusions:** clear book-club copies and modern reprints are left out.
   - **Minimum value:** a book is flagged only if its matching sales clear the shelf's minimum.
   - **Category rules:** each shelf category has its own rule.

   The reference data is in `data/`: the price file, `rules.json` (whitelist, publisher lists and minimums), the frequent authors and the watch list.

   **Spine feet:** after a first pass of the rules, the feet of the pulls' and leads' spines are read again from enlarged close-ups (up to 36 per photo, about 1–2¢), where publishers, reprint series and book clubs put their names and logos. A book-club logo there (such as Book Club Associates' boxed "bca") or a reprint imprint drops the book, a collectible imprint adds it, and the rules run again. Books lying flat aren't checked.
6. **Notes:** one Opus call per photo, text only (about 9¢ and 40 seconds for 15 books), writes bibliographic notes for each pull and lead from Claude's own knowledge: the first edition (year, publisher and city, UK and US), the points that identify the first printing or valuable issue, and the lookalikes worth less, with a confidence level. Generic advice is left out: the reader is an expert. Web search improved the notes on local and small-press titles in a test, but cost about 10¢ a book, so it isn't used yet.
7. **Output:** the results page, plus `results.json` in the job folder. The photo has numbered red boxes for pulls, blue for leads, and amber "?" boxes for spines where neither a title nor an author could be read; the page counts the spines read and unreadable, per photo and per shelf. The JSON includes every spine as read, so `tests/replay.py` can re-run new rules on old scans for free.
8. **Feedback:** each pull has "Worth it" and "Not worth it" buttons, and each book in the "not pulled" list has "Should pull". The verdicts are saved with the scan, and `/feedback.json` lists them all, for tuning the rules.

Photos in one scan are read in parallel. If the app restarts in the middle of a scan, the scan resumes, and the crops already read cost nothing the second time.

Saved responses are cached by a hash of the image and prompt, so running the same photo again costs nothing.

## Cost
- **Claude**, billed to the personal Anthropic key:
  - about 13–22¢ per photo on Sonnet;
  - the second read adds about 20¢ on a shelf like the children's sample.
  - Each scan has a $2 cap, and the app stops for the day at $10. Both are app settings.
  - **Credit left:** the scan page and each scan's page show about how much credit is left on the whole Anthropic account. Anthropic has no API for the balance, so you type it in once from the Console's Billing page ("Update the credit balance" on the scan page), and the app subtracts everything the account spends from then on. It reads the spending with an Admin API key from the usage report, priced with the app's price table, which matched the Cost API to the cent on a settled day; the Cost API itself leaves out the current day and lags by hours. Type the balance in again after adding credits or an auto-reload, since purchases aren't in the API. The usage report ran 20 minutes and more behind when tested, so it is read only up to an hour ago, and the app's own spending since then comes from its ledger of paid calls (`spend.jsonl`): the app's scans show at once, and other use of the account shows after an hour. The figure is re-read at most every 2 minutes. A model the app has no price for is named next to the figure rather than counted.
- **Azure:** App Service on the Free F1 tier ($0), in Central US, because this subscription has no F1 quota in East US or East US 2. Files stay on the app's own disk, with no database, registry or storage account.
  - F1 limits: 60 CPU minutes a day, 1 GB of disk, and the app sleeps after about 20 idle minutes, so the first page after a sleep takes 30–60 seconds.
  - Scans older than 30 days are deleted.

## Run locally
```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # from ../anthropic.md; never print it
SHELFSCAN_PASSCODE=pick-one SHELFSCAN_INSECURE_COOKIE=1 .venv/bin/gunicorn --bind 127.0.0.1:8765 --workers 1 --threads 8 app:app
# or one-off from the command line, writing a static report.html:
.venv/bin/python cli.py photo.jpg --category rare --out /tmp/scan
```
`tests/replay.py results.json` re-runs the current rules on a finished scan's readings without any API calls. `tests/dry_rules.py` does the same for the TSV readings from the earlier API test.

## Deploy
`AZURE_CONFIG_DIR=<isolated az profile> ./deploy.sh` signs in with the service principal in `../azure.md` and refuses the ParcelShield tenant. It then:
- creates the F1 plan and the web app in resource group `norris-bookshelf` if they're missing;
- sets the app settings from `../anthropic.md` and `../shelfscan.md`;
- deploys the code.

`../shelfscan.md` is created on the first run. It holds the app name, the sign-in passcode and the session secret.

- **After rotating the Anthropic key:** update `../anthropic.md`, then run `./deploy.sh --settings-only`.
- **Credit left:** put an Admin API key (`sk-ant-admin01-...`, from the Console's Admin keys page) in `../admin-claude-key.md`, then run `./deploy.sh --settings-only`. The app only reads the account's spending with it, but the key itself has full admin rights over the Anthropic organization, so revoke it in the Console if the app is ever compromised.
- **To use a new price file:** put it in `data/` with the same column names, or list it in `SHELFSCAN_PRICE_FILES`, then redeploy. Each result records which file version priced it.

## Known limits
- **Settings apply to the whole scan:** all photos in one scan share the category settings. Use "Different shelves in the photo" (for example `3=signed`) for mixed photos.
- **Box placement:** boxes come from the model's position estimates, and the ruler made them much better. A box can still sit a spine off, so the spine crop on each card is the surer guide.
- **Overlapping photos:** books seen in two overlapping photos aren't merged yet.
- **Terapeak link:** it needs an eBay seller sign-in, and its search format hasn't been checked.
- **Price file:** it has no sales under $50, and its newest sale is from January 2025.
- **Signed shelves:** every readable book is pulled, but few authors have signed sales in the price file, so most are unranked. A table of signature values would fix that.
- **Screenshots:** phone screenshots read much worse than the original photos. Photo B, a screenshot, had 28 of 75 spines unreadable.

# thai-egp

[ภาษาไทย](README.md) · Browse fiscal 2568 by district: [ภาษีบ้านฉันไปไหน](https://nuttakulsv.github.io/thai-local-spend/) · From the maker of [data-go-th](https://github.com/nuttakulsv/data-go-th) (search and clean any other data.go.th dataset) and [awesome-thai-api](https://github.com/nuttakulsv/awesome-thai-api)

Thailand publishes every public procurement contract from its e-GP system, fiscal year 2558 (2015) onwards, as open data on data.go.th. It's the same data behind the government's own [ภาษีไปไหน ("where did the tax go")](https://govspending.data.go.th) site: who bought what, from whom, by which procedure, for how much.

The raw files are hard to use. Each year is 2 to 6 GB of CSV split into ten-odd parts, the column layout changes between years, and the 2568 files have headers that don't match their rows. Read them naively with pandas and the winner's name lands in the latitude column.

thai-egp downloads the data from data.go.th, fixes those problems, and loads it into SQLite in one command. Then you can search it, rank it, and run a set of red-flag checks as a starting point for your own reporting. Pure Python standard library, nothing else to install.

```
$ egp search ถนน --province เชียงใหม่ --year 2567
...
67059412082  59,800,000 บาท  ประกวดราคาอิเล็กทรอนิกส์ (e-bidding)  2024-07-12
  ประกวดราคาจ้างก่อสร้างงานปรับปรุงทางเพื่อความปลอดภัย ถนนสาย ชม.3029 ถนนสมโภชเชียงใหม่ ๗๐๐ ปี ...
  แขวงทางหลวงชนบทเชียงใหม่, เชียงใหม่ -> บริษัท โฟร์เอ็ม อินเตอร์เทรด จำกัด
```

## Install

Python 3.9 or newer.

```
pip install git+https://github.com/nuttakulsv/thai-egp
```

Or grab the single file: `curl -O https://raw.githubusercontent.com/nuttakulsv/thai-egp/main/thai_egp.py` and run `python3 thai_egp.py` instead of `egp`.

## Use

Fiscal years are Buddhist era (2568 = Oct 2024 to Sep 2025).

```
egp years                     # what's on data.go.th and how big it is
egp load 2568                 # stream a whole year into egp.sqlite, no raw files kept
egp load 2566 2567            # add more years to the same database

egp search ซ่อมถนน --dept "เทศบาลตำบลแม่ริม"     # words in the project name, all must match
egp search --winner ก่อสร้าง --min 10000000 --year 2568
egp top winner --year 2568                      # who received the most
egp top dept --province ขอนแก่น                  # which units spent the most
egp top method --year 2568                      # share of each procurement method
egp flags --province ลำปาง                       # red flags worth a closer look
```

`load` streams each file from data.go.th straight into the database. If the connection drops, that file is rolled back, and running the command again resumes from it. Expect about 1.6 KB of disk per row. To keep the raw files, `egp download 2568`, then `egp load egp-data/2568/*.csv`.

Every command takes `--csv`. The database is plain SQLite (`sqlite3 egp.sqlite`), main table `contracts`.

## Who wins the most, nationwide

```
egp winners
```

Streams every year (about 50 GB) and writes one row per juristic winner to `winners.csv` without building a database, so it works on a laptop with little disk left. It takes hours, depending on your connection, and skips finished files if you restart it.

Columns include total baht, contract count, count and baht of direct-procurement (เฉพาะเจาะจง) contracts, plus the pre-2017 equivalent (ตกลงราคา), first and last year, the top three buying units, the 13-digit registration number where the data has it, and a link to the company's page on the Department of Business Development's DataWarehouse, where its directors are listed.

People (sole traders) are left out. Contracts worth more than twice their budget, almost always a typo, are not counted and are listed in `winners-summary.json` for checking.

A full run on 2 Nov 2026 (fiscal 2558-2568) came to 45.3 million contracts worth 11.76 trillion baht, won by about 235,000 juristic persons; the top 100 took 17.8% of the money.

| Fiscal year | Contracts (m) | Value (trillion baht) | Direct (เฉพาะเจาะจง / ตกลงราคา) |
|---|---|---|---|
| 2558 | 3.50 | 1.03 | 11% |
| 2559 | 1.87 | 0.68 | 17% |
| 2560 | 2.03 | 0.71 | 16% |
| 2561 | 3.63 | 0.91 | 31% |
| 2562 | 3.78 | 1.10 | 30% |
| 2563 | 4.95 | 1.24 | 33% |
| 2564 | 5.01 | 1.30 | 32% |
| 2565 | 4.76 | 1.16 | 32% |
| 2566 | 5.59 | 1.18 | 38% |
| 2567 | 5.46 | 1.17 | 38% |
| 2568 | 4.69 | 1.28 | 31% |

The direct share jumps from 2561 (fiscal 2018), partly because the 2017 Procurement Act allows direct purchases up to 500,000 baht, where the old ตกลงราคา rule stopped at 100,000. Why 2559-2560 have so few contracts I don't know yet. The companies with the most direct contracts are almost all medicine and medical-supply distributors, because hospitals routinely buy drugs this way, so the count alone says nothing about wrongdoing.

## Red flags

`egp flags` looks for patterns that procurement watchdogs start from. Every one is a signal, not evidence. Many have ordinary explanations, like buying fuel from the only station in the district or medicine from the Government Pharmaceutical Organization.

| flag | what it finds |
|---|---|
| `split` | One unit buys from the same winner, same type of work, by direct procurement, three or more times within 30 days. Each contract is under the cap for that method (500,000 baht, or 100,000 for the pre-2017 ตกลงราคา), but together they're over it. This is what contract splitting looks like. |
| `near_reference` | Competitive tenders won within 1% of the reference price (ราคากลาง), i.e. barely any price competition. |
| `concentration` | One winner takes more than half of a unit's contract value in a fiscal year. |
| `direct_over_cap` | Direct procurement above 500,000 baht. Allowed only for the reasons in section 56 of the 2017 Procurement Act (urgency, a sole supplier, ...), so the stated reason is worth reading. |

By default units and projects under 5 million baht are skipped (`--min-baht`), and only juristic winners are considered. Flags never name individuals.

The 500,000 baht cap comes from the Public Procurement and Supplies Administration Act B.E. 2560, section 56 paragraph 1 (2)(b), and the ministerial regulation of 23 August 2017.

## Traps in the official files

Worth knowing even if you don't use this tool.

- **Dataset names change between years:** `cgd-contract-2558`, `cdg-contract-2564` (letters swapped), `egp-contact-2568` (contact, not contract).
- **Column counts differ:** 2558 to 2567 have 31 columns, some files with 1 to 6 extra empty ones. 2568 has 28 columns, but its headers list 26 or 31 names depending on the file. Read by position, never by header.
- **Swapped columns:** in 2568 the procurement method and method group trade places.
- **Registration numbers mangled by Excel:** before 2568 the 13-digit numbers became `1.05522E+11`, keeping only six digits. Individuals' numbers are masked on purpose (`352050022xxxx`). 2568 has full numbers. thai-egp keeps the five digits that survive in `winner_prefix` (registrar office, entity type, registration decade), which is enough to tell apart same-named companies from different provinces. Some agencies' files for 2559-2564 kept only three digits (`1.06E+11`), the last one rounded; those rows get no `winner_prefix`, and `winners` joins them to the same-named company that holds at least 90% of that name's contracts.
- **Contract numbers turned into dates by Excel:** `6/58` became `มิ.ย.-58`.
- **Two date formats,** `29-ก.ค.-58` and `29 พ.ย. 67`, both with two-digit Buddhist years. thai-egp converts them to ISO dates.
- **Big contracts first, roughly:** the first file of a year holds the large contracts and the last ones hold 1,000-baht purchases, but the order is loose. Never summarise a year from one file.
- **One row per contract,** so a project can have several rows. Count projects with `COUNT(DISTINCT project_id)`.

## pandas, DuckDB, Python

```
egp clean 2568 > egp-2568.csv        # cleaned CSV with English column names
```

```python
import thai_egp
for row in thai_egp.rows(2568):      # streams the whole year from data.go.th
    ...
```

Main columns: `project_id`, `project_name`, `dept`, `subdept`, `method`, `method_kind` (`ebidding`, `specific`, `selection`, `emarket`, `price_agreement`, `price_quotation`, `special`, `open`), `budget`, `reference_price`, `agreed_price`, `amount` (contract value), `fiscal_year`, `province`, `winner_id`, `winner_prefix`, `winner_juristic`, `winner_name`, `contract_date`.

## Company directors

e-GP data has no directors or shareholders. Those live with the Department of Business Development. `egp winners` links each company with a full registration number to its [DBD DataWarehouse](https://datawarehouse.dbd.go.th) page, one at a time.

thai-egp doesn't collect directors automatically. DBD doesn't offer that: the site is behind bot protection, its official API is for government agencies, and the department has said it doesn't support compiling who sits on which board.

## Limits

- Only contracts in e-GP, from fiscal year 2558 to the latest year on data.go.th. New years are found by dataset title.
- One company can be spelled several ways (`บจก.`, `บริษัท ... จำกัด`, with or without `(มหาชน)`). `winners` merges them; `--winner` is a substring match, so try variants.
- Renamed companies are counted separately.
- Some projects list the same contract twice, same winner and amount but a new contract number (probably a re-signed contract), so they sum above the budget. Measured on a sample, that moves totals by about 0.01%, and by no more than 0.05% for the largest winners. thai-egp counts what the data says.
- The numbers are what agencies typed in. Typos in, typos out.

## Data source and licence

Procurement data from the Comptroller General's Department e-GP system, published by the Digital Government Development Agency on [data.go.th](https://data.go.th) under Creative Commons Attribution. Credit them if you publish results.

Code: MIT.

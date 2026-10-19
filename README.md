# wmw — Wild Microbiome Watch

wmw finds shotgun-metagenome studies of wild animals in public archives (ENA, or GSA
for China), curates them in an Airtable base, and runs them through the
[Drakkar](https://github.com/alberdilab/drakkar) genome-resolved metagenomics pipeline
on the Mjolnir HPC. Results flow back into Airtable (stats, genomes, attachments) and
the large files go to the ERDA archive.

This document is written for **agents** (and people) who need to read or change the
database, or to operate wmw on the HPC. Command-line reference is in `docs/cli.md`,
column-level schema in `docs/schema.md`, internals in `docs/architecture.md`, and
filter semantics in `docs/filters.md`.

## 1. The system in one picture

```
ENA / GSA ──wmw scan──► Airtable Studies   status new
                              │  human review in Airtable
                              ▼  status approved
ENA / GSA ──wmw fetch─► Airtable Samples   status pending → human sets use / ignore
                              │            study status → indexed
                              ▼  human (or agent) sets study status ready
        wmw process (HPC) ── writes <code>.tsv + <code>.sh ── screen session
                              │
                              ▼  preprocessing → cataloging → amr → profiling → annotating
                           Drakkar (Snakemake, Slurm jobs)
                              │
                              ├─► Airtable: study status, Samples stats, Genomes rows, attachments
                              └─► ERDA (SFTP): assemblies, bins, AMR tables, gene calls
```

Two human gates exist on purpose: **`new` → `approved`** (is this study worth fetching?)
and **sample `status` = `use`** (which runs go into the pipeline). Agents should not
flip these without being asked.

## 2. Access and secrets

- **Token:** `AIRTABLE_WMW_TOKEN` (a Codeome secret, set both locally and on the HPC).
  wmw reads it automatically; the old `AIRTABLE_TOKEN` and `--airtable-token` still
  work. Never print, log, or write it to a file or script.
- **On the HPC** the secrets file is `~/.codeome/secrets/wmw-3f90c10d.env`; run
  `source` on it in any script that needs the token. `sbatch` jobs inherit it.
- **Base and tables** are fixed in `src/wmw/data/config.yaml` (the installed copy is
  what runs; `wmw config --view` shows it):

| Table | ID |
|---|---|
| base | `appHz4Vc5NpVdfU3K` |
| Studies | `tblBzrwZn1IVKUlfQ` |
| Samples | `tblXdKvsFaLUgLtU5` |
| Genomes | `tbl0CBUAmHek5zLOe` |
| Species | `tblXZZUqf7tSr0Pxy` |

- **Field names vs IDs.** The config maps every column to an Airtable *field ID*
  (`STUDIES_COL_*`, `SAMPLES_COL_*`, `GENOMES_COL_*`) and wmw reads and writes by ID.
  The columns' **display names in the base are Title-case** (`Status`, `Code`,
  `Run accession`…), not the snake_case names used in code and `docs/schema.md`. When
  querying Airtable directly (pyairtable, the API), use the display names or the
  field IDs from the config, never the snake_case names.
- `wmw status` summarises Samples by `Status` (live: `use` 311, `ignore` 288) and
  accepts `--batch <Code>` to restrict to one study.

## 3. The database

Four linked tables. The `Code` columns are the join keys everything downstream uses.

### Studies — one row per archive project (≈363 rows)

| Column | Meaning |
|---|---|
| `Code` | `ST00001`… The **batch code**: HPC work dir name, screen session name, Drakkar output folder. Assigned by Airtable (autonumber). |
| `Study accession` / `Secondary study accession` | `PRJEB…`/`PRJNA…`, `ERP…`; for GSA `CRA…` / `PRJCA…`. Dedup key. |
| `Source` | `ENA` or `GSA` |
| `Status` | The study lifecycle (below). Drives `wmw process`. |
| `Study title`, `Description`, `Scientific names(s)`, `Center`, `Data publication date`, `Detected runs`, `Detected host taxa` | Archive metadata |
| `Pubmed ID`, `Publication DOI/URL/title/Year/Journal/Authors` | Resolved from PubMed / CrossRef; `pub_pdf` from Unpaywall |
| `Type` | Multi-select: `Wild`, `Captivity`, `Domestic` (human-curated) |
| `Priority`, `Time boost`, `Memory boost` | Scheduling hints. `Priority = Low` makes wmw add `--slurm-partition lazyqueue --slurm-qos lazy`. `Time boost` / `Memory boost` are multipliers passed to Drakkar. |
| `Preprocessing file`, `Cataloging file`, `Counts file`, `Bases file`, `Quantifying file` | Attachments of Drakkar's study-level tables |
| AMR attachments (`hits`, `loci`, `drug_classes`, `mobility`, `regions`, `amr_manifest`) | Output of `drakkar amr` |
| `Samples`, `Species` | Links to the Samples and Species tables |

**Status values seen in the base:** `new` (316), `approved` (14), `discarded` (9),
`indexed`, `resume`, `locked`, `preprocessed`, `cataloged`, `Done` (11).
Full lifecycle:

```
new → approved → indexed → ready → preprocessing → preprocessed → cataloging → cataloged
    → amring → amred → quantifying → quantified → annotating → Done
side states:  discarded (human), error, stopped, locked
triggers a human or agent can set:  ready, rerun, resume, unlock
```

- `ready` / `rerun`: build the input TSV and start from the first stage.
- `resume`: restart after the latest stage that left outputs, and replay any missed
  Airtable/ERDA finalisation. wmw also parks a study in `resume` itself when an
  Airtable write fails mid-run.
- `unlock`: a `resume` that first runs `drakkar unlock` (use only after confirming
  nothing is running; `locked` means a stale Snakemake lock was found).
- `stopped` is written by `wmw stop`; `error` by a failed stage.

### Samples — one row per sequencing run (599 rows)

| Column | Meaning |
|---|---|
| `Code` | `SA000001`… The **sample code**: used as the Drakkar sample name, assembly folder name, and Slurm job tag. |
| `Study` | Link to the Studies row (and `Study accession` as text) |
| `Run accession`, `Sample accession`, `Experiment accession` | Archive IDs. `Run accession` is the dedup key. |
| `Status` | `pending` (new), **`use`** (goes to Drakkar), **`ignore`** (excluded). Human-curated. |
| `Forward URL` / `Reverse URL` | Download URLs for R1/R2 (`fastq_url_1/2`) |
| `Unspilt URL` | `fastq_url_unsplit`: a paired run served as one flat FASTQ with both mates; Drakkar splits it (`rawreads_unsplit`) |
| `Fastq FTP`, `Fastq MD5` | Raw archive file list / checksums |
| `Instrument platform/model`, `Library strategy/source/layout`, `Base count`, `Read count` | Sequencing metadata. Platform picks Drakkar's `--platform illumina\|bgi`. |
| `Host`, `Host scientific name`, `Host taxid`, `Country`, `Collection date`, `Latitude`, `Longitude`, `Biome`, `Environment` | Host and BioSample/MIxS metadata. Blank means unknown, not excluded. |
| `reference_name`, `reference_path` | Per-sample host reference genome (Drakkar maps and removes host reads). |
| `coverage` | Optional co-assembly/coverage hint passed to Drakkar |
| Preprocessing stats (`Reads pre/post fastp`, `Bases pre/post fastp`, `Host reads/bases`, `Metagenomic reads/bases`, `Adapter trimmed …`, `Singlem fraction`, `C`, `LR`) | Written from `preprocessing.tsv` |
| Assembly stats (`Assembly - Length/N50/L50/GC%/Number of contigs/Largest contig/Mapping rate …`), `Number of bins`, `Average completeness/contamination`, `Genomes` (link), `MAGs - Mapping rate` | Written from cataloging outputs |
| `contig_to_bin`, `faa_erda`, `ffn_erda` | Attachment / ERDA pointers |
| AMR counts | Opt-in; the config keys are still blank, so not written yet |

### Genomes — one row per MAG that passes quality (completeness > 50, contamination < 10)

`Name`, `Code`, `Sample` and `Study` links, `Completeness`, `Contamination`, `Length`,
`N50`, `Number of contigs`, taxonomy (`Division`, `Phylum`, `Class`, `Order`,
`Family`, `Genus`, `Species`, plus `fastani_ani`, `closest_placement_ani/af`),
annotation counts (`Number of genes`, `annotated genes`, `KO`, `EC`, `CAZY`, `PFAM`,
`VF`, `AMR`, `DEFENCE`, `ANTIDEFENCE`, `SIGNALP`, `Annotation rate`), and the attachments
`Genome file` (FASTA) and `Annotation file`.

### Species — host species (taxid, `Name`, Linnaean ranks, `Reference genomes`)

Studies link to their host species. wmw never fills a sample's `reference_name` /
`reference_path`: they are curated by hand in Airtable and passed through unchanged
into the Drakkar input TSV.

### Rules that keep the base safe

1. **Dedup by accession.** `scan` and `fetch` can be rerun over overlapping ranges.
2. **Never overwrite curated cells.** `wmw fetch --fill-missing` writes only into
   empty cells. `--refresh-metadata` *does* overwrite BioSample fields, so use it
   only when the archive is the source of truth.
3. **Opt-in columns:** a blank `*_COL_*` config key means "don't write this column",
   because one unknown field name makes Airtable reject the whole batch.
4. **A blank field is never grounds for exclusion** by any filter.
5. **Select fields reject unknown options.** Writing a status label the base does not
   offer fails; check the option exists first.

## 4. wmw on the HPC, with Drakkar

### Where things live

| Thing | Location |
|---|---|
| Host | `jpl786@mjolnirgate.unicph.domain` (use the Codeome control socket; see your agent instructions) |
| wmw project output root (`DRAKKAR_OUTPUT_DIR`) | `/projects/alberdilab/scratch/jpl786/wmw` |
| One study's work dir | `<DRAKKAR_OUTPUT_DIR>/<Code>/`, e.g. `…/wmw/ST00060/` |
| Drakkar conda env (`DRAKKAR_CONDA_ENV`) | `/projects/alberdilab/data/environments/conda/drakkar` (called directly as `<env>/bin/drakkar`) |
| Conda | not on PATH; `bash -ic "useconda; conda activate <env>; …"` |
| ERDA archive | SFTP `io.erda.dk`, base folder `/WMW/<Code>/{assemblies,bins,amr,genes}` |

### The `wmw process` flow

`wmw process --batch ST00060` (omit `--batch` for every study in `ready`, `resume`,
`unlock` or `rerun`):

1. Looks up the study by `Code`.
2. For `ready`/`rerun`: pulls the study's samples with `Status = use` and writes
   `<work_dir>/<Code>.tsv`, the Drakkar sample-detail file with columns `sample`
   (= sample `Code`), `rawreads1`, `rawreads2` (or `rawreads_unsplit`),
   `reference_name`, `reference_path`, and `assembly`/`coverage` when set.
3. Derives the sequencing platform (majority of the `use` rows; ties and unknowns fall
   back to illumina) and writes `<work_dir>/<Code>.sh`.
4. Starts that script in a detached **`screen` session named `<Code>`**. Logs are
   `<work_dir>/<Code>.out` and `<Code>.err`.
5. The script chains every stage from the starting one to the end:

```
drakkar preprocessing → cataloging → amr → profiling → annotating
```

   `--workflow X` picks the start stage; `--only` runs just that stage; `--slurm`
   makes Drakkar submit its jobs through Slurm instead of running locally.
6. Around each stage the script calls `wmw set-status` (Airtable study status), installs
   an `EXIT` trap that reports `error`/`stopped`/`locked`, and — before marking a stage
   done — checks that its **final output files exist**, because Drakkar exits 0 when it
   refuses to start. Required outputs: `<Code>_preprocessing.tsv`;
   `cataloging/final/all_bin_{paths.txt,metadata.csv}`; `amr/amr_qc.tsv`;
   `profiling_genomes/final/{counts,bases}.tsv`; annotation tables.
7. After a stage completes, wmw **finalises** it:
   - preprocessing → read stats into Samples; `Preprocessing file` on the study
   - cataloging → assembly/bin stats into Samples, qualifying MAGs into Genomes
     (FASTA attached via a `<Code>-genome-upload` screen session), per-assembly
     `contig_to_bin` attachments, assemblies + bins to ERDA via `<Code>-erda-upload`
   - amr → `amr_qc.tsv` counts, aggregate tables attached to the study and sent to ERDA
     inline; prodigal gene calls to ERDA via `<Code>-erda-genes`
   - profiling / annotating → counts/bases tables, taxonomy and gene annotation counts
     into Genomes
8. Study `Status` ends at `Done` (annotation complete).

Stage ↔ Airtable status labels: `preprocessing/preprocessed`, `cataloging/cataloged`,
`amring/amred`, `quantifying/quantified`, `annotating/Done`.

### Work directory layout (Drakkar output)

```
<Code>/
  <Code>.tsv  <Code>.sh  <Code>.out  <Code>.err      wmw-written
  <Code>_upload_erda.*  <Code>_upload_genomes.*       scripts/logs of the detached uploads
  .wmw-stop                                           written by `wmw stop`
  .snakemake/locks                                    stale lock => status `locked`
  data/  logging/  drakkar_report.html                Drakkar-written
  <Code>_preprocessing.tsv  <Code>_cataloging.tsv     per-stage tables at the root
  dereplicating.tsv  profiling_genomes.tsv
  preprocessing/
  cataloging/megahit/<sample>/<sample>.fna            assemblies, named by sample Code
  cataloging/binette/<sample>/final_contig_to_bin.tsv
  cataloging/final/all_bin_paths.txt, all_bin_metadata.csv, all_contig_to_bin.csv (newer runs)
  amr/amr_qc.tsv, *.tsv.xz aggregates, manifest.yaml, raw/prodigal/<sample>.{faa,ffn}
  profiling_genomes/final/counts.tsv, bases.tsv
  annotating/genome_taxonomy.tsv, annotation_qc.tsv, final/<genome>_genes.tsv
```

### Operating it (what an agent should do)

- **Check state:** read the Studies row's `Status`, then `<Code>.out`/`<Code>.err`,
  then `screen -ls` for a live `<Code>` session. A study in a `…ing` status with no
  screen session and no Slurm jobs is dead; investigate before resuming.
- **Start:** set the study `ready` (samples already marked `use`), then
  `wmw process --batch <Code> [--slurm]` on the HPC.
- **Recover:** after a genuine failure fix the cause, set `resume`, rerun
  `wmw process --batch <Code>`. For `locked`: confirm nothing is running, set
  `unlock`, rerun.
- **Stop:** `wmw stop --batch <Code>` (sets `stopped`, writes `.wmw-stop`, kills the
  screens, best-effort `scancel`).
- **Backfill:** `wmw upload-contig-to-bin`, `wmw upload-amr`, `wmw upload-erda
  [--what cataloging|amr|genes|all]` for batches that ran before a payload existed.
- **Environment:** `wmw` runs where it is installed (HPC conda env). Run `source
  ~/.codeome/secrets/wmw-3f90c10d.env` first so `AIRTABLE_WMW_TOKEN` is set. Do not
  copy secrets under `/projects`.
- **Leave intermediate files alone** unless the user approves deletion; the work dir is
  also the record of what has been run.

### Things that bite

- A mixed-platform study is preprocessed as its majority platform (a warning is shown).
- A table over Airtable's attachment size limit is reported and kept on ERDA only.
- ERDA transfers skip files already present; `--replace-files` (with `--study`)
  forces a resend.
- `Priority = Low` studies run on the `lazyqueue`/`lazy` partition and QOS.
- GSA studies have no base/read counts, so `MIN_BASES` can't filter them.

## 5. Discovery commands (any machine with the token)

```
wmw scan  [--source ena|gsa] [--from DATE] [--to DATE] [--study ACC] [--keyword TEXT] [--dry-run]
wmw fetch [--status approved] [--study ACC] [--fill-missing | --refresh-metadata] [--dry-run]
```

`scan` only ever writes `new` studies; `fetch` only reads `approved` ones and then
marks them `indexed`. Use `--dry-run` first.

## 6. Development

```
pip install -e . pytest      # then:  pytest tests/   (548 tests)
```

Every code change is logged under `[Unreleased]` in `CHANGELOG.md`. Releases are cut
only on request with `python scripts/release.py X.Y.Z` (`--dry-run` first). Commits are
authored by the user alone, with no AI co-author trailer.

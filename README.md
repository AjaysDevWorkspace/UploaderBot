# Newton School MCQ Upload Bot

Reads MCQs from a Word file, checks them, uploads them through the Newton School Django admin
("Add multiple choice question" form), and writes an Excel report with the Django link and ID of
every question.

MCQs only. Coding questions and assessment creation are not part of this version.

---

## 1. One-time setup (Windows)

**Easy way:** install Python 3.10+ from python.org (tick "Add python.exe to PATH"), then
double-click **`setup.bat`**. Afterwards, double-click **`run.bat`** for a menu:
check, preview, upload one, upload all, report.

**Manual way** (same result):

```powershell
cd newton_mcq_bot
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

If PowerShell blocks the activate script, run this once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Every later session only needs `cd newton_mcq_bot` and `.venv\Scripts\Activate.ps1`.

Check `config\settings.json` once:

| Setting | Meaning |
|---|---|
| `assessment_types` | Values ticked in "Question for assessment type". Set to `["Normal Assessment", "Competitive Assessment"]`; checked again after every save |
| `default_difficulty` | `null` = every question must have `Difficulty:`. Or e.g. `"Medium"` as fallback |
| `default_bloom_taxonomy` | `null` = keep the portal default unless the question has `Bloom:` |
| `shuffle_options` | Ticks "Shuffle options" |
| `min_options` | Minimum options per question (portal maximum is 5) |
| `mcq_add_url` | Leave `null`; the bot finds the form by clicking through the admin |

---

## 2. Upload flow (every time)

| Step | What you do | Command |
|---|---|---|
| 1 | Write questions using `MCQ_Template.docx` (format below). Save as `.docx` in `input\` | |
| 2 | **Check** the file. No browser, nothing uploaded | `python main.py check input\week3.docx` |
| 3 | Fix every `ERR` line in Word, save, run step 2 again until all are `OK` | |
| 4 | **Preview**: the bot logs in, fills the first question, does NOT save. Compare the form with your Word file | `python main.py upload input\week3.docx` |
| 5 | **Upload one** as a final test, then open its Django link and check it | `python main.py upload input\week3.docx --save --limit 1` |
| 6 | **Upload the rest**. Questions already done are skipped automatically | `python main.py upload input\week3.docx --save` |
| 7 | Open `output\upload_report.xlsx`: Status, Django Link, Django ID for every question | |

Notes:

- The first time, a Chrome window opens on the admin login page. Log in yourself, including OTP if
  asked, then press Enter in the terminal. The login is remembered in `.browser_profile\`.
- `--save` always asks you to type `SAVE` before anything is created.
- If the run stops (internet, Ctrl+C, closed window), run the same command again. It continues
  from where it stopped, and never re-creates questions marked Done.
- If some questions are invalid, the upload refuses to start. Fix them, or add `--skip-invalid`
  to upload only the valid ones. The invalid ones appear as "Not done" in the report.

### Report statuses

| Status | Meaning | What to do |
|---|---|---|
| Done | Saved on the portal and verified by reading it back | Nothing |
| Not done | Invalid in Word, or rejected by the portal. The reason is in "Error / Note" | Fix and run upload again |
| Needs check | The run stopped right after Save was clicked, so the result is unknown | Search the MCQ list for the question. If it is **not** there, rerun with `--force-retry` |

---

## 3. Question format

Start from `MCQ_Template.docx`. Its instructions sit above the first question and are ignored by
the bot. Its three example questions start with `[EXAMPLE]` and are rejected on purpose, so delete
them.

```
Q1. In a normal HTTP exchange, who starts the communication?
A) The server, by pushing data to the client
B) The client, by sending a request first
C) The database, by firing a change event
D) The load balancer, by polling clients
Answer: B
Explanation: HTTP is request-driven. The client always sends a request first.
Difficulty: Easy
Bloom: Understand
Topic: HTTP Basics
```

| Line | Required | Accepted forms | Rules |
|---|---|---|---|
| Question | Yes | `Q1.` `Q1)` `Q1:` `Question 1:` `Question 10:` | Text may continue over several paragraphs. Bold, italic, underline are kept. Text in **Consolas** (or another monospace font) becomes code |
| Options | Yes | `A)` `A.` `(A)` `A:` `Option A:` | Type the letters yourself, in order A, B, C, D, E. Minimum 4, maximum 5. Plain text only |
| Answer | Yes | `Answer: B`, `Correct Answer: B`, `Ans: B`, `Answer: B) The client...`, or the exact option text | Exactly **one** correct option. If letter and text are both given, they must agree |
| Explanation | No | `Explanation:` or `Solution:` | Can continue over several paragraphs |
| Difficulty | Yes* | `Difficulty: Easy` | Beginner, Easy, Medium, Hard, Challenge. (*unless `default_difficulty` is set) |
| Bloom | No | `Bloom: Understand` | One of: Remember, Understand, Apply, Analyze, Evaluate, Create ("Analyse" is accepted too) |
| Topic | No | `Topic: Arrays` | Shown in the Topic column of both reports; not uploaded to the portal yet |
| Marks | No | `Marks: 1` | Recorded in the reports; marks belong to the assessment stage |

**MCQ No.** in the reports is the number written in the document (`Question 10:` → 10). If any
question has no number, or a number is repeated, the position in the file (1, 2, 3...) is used.

Also allowed:

- **Two-column tables**: left column `Q3`, `A`, `B`, `C`, `D`, `Answer`, `Difficulty`; right column
  the values. See example Q3 in the template.
- **Headings** between questions (e.g. "Section 2: Arrays"). These are ignored.
- **A blank line inside a question**, which is kept as spacing.

Not allowed (the question is marked invalid, never uploaded wrongly):

- Word's **automatic numbering or bullets** for questions or options. Word-generated letters are
  invisible to the bot, so type `A)` by hand.
- **Images** or **Word equations**. Write formulas as text, e.g. `O(n log n)`.
- More than one correct answer, e.g. `Answer: A, C`.
- Any text between questions that is not one of the lines above. Notes belong after `Explanation:`.
- The same question text twice in one file.

---

## 4. Commands

```powershell
python main.py check  input\FILE.docx                    # validate only
python main.py upload input\FILE.docx                    # preview one question, no save
python main.py upload input\FILE.docx --save             # upload all valid questions
python main.py upload input\FILE.docx --save --limit 5   # upload the next 5 only
python main.py upload input\FILE.docx --save --skip-invalid
python main.py upload input\FILE.docx --save --force-retry   # only after checking "Needs check" items
python main.py report                                    # rebuild the Excel report from the database
python upload_one.py input\test_question.json [--save]   # single question from JSON (testing)
python inspect_portal.py                                 # read-only page inspector (for new phases)
```

## 5. Output files

| File | Contents |
|---|---|
| `output\parsed_questions.json` | Exactly what the bot read from the Word file |
| `output\validation_report.xlsx` | Valid / Invalid per question, with errors and warnings |
| `output\upload_report.xlsx` | MCQ No., Status, Django Link, Django ID, question, topic, answer, difficulty, hash, time, errors. Summary sheet with totals |
| `database\upload_tracking.db` | Upload history used for skipping duplicates and resuming. Don't delete it |
| `logs\bot.log` and screenshots | Run details for troubleshooting |

If `upload_report.xlsx` is open in Excel during a run, the bot saves a timestamped copy instead.

## 6. Troubleshooting

| Message | Fix |
|---|---|
| `Field "..." not found on the page` | The portal form changed. Run `python inspect_portal.py` on the add form and share the output |
| `"X" is not an available option` | The value (e.g. Bloom level) is not in the portal dropdown. The message lists the allowed options |
| `3 failures in a row` | The bot stopped to avoid more errors. Read `logs\bot.log` |
| Keeps asking you to log in | Delete the `.browser_profile` folder and log in again |

## 7. Security

- No passwords are stored. You log in yourself; the session lives in `.browser_profile\`.
  Treat that folder like a password and never share or commit it (it is in `.gitignore`).
- The bot only uses the normal admin form, with your own permissions.
